#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Serve the sprite-gen curation webview for a single run directory.

Standalone, dependency-free (Python standard library + the PIL already used by
the pipeline). Launch it against any sprite-gen run folder and open the printed
URL in a browser to compare frames per state, select/reject frames, and apply a
non-destructive per-frame transform (rotate/scale/move). All edits are persisted
to `curation.json` in the run directory; the original frame PNGs are never
touched. The compose scripts read that sidecar and bake the result.

    python3 serve_curation.py --run-dir <run-folder>

This is intentionally a standalone skill tool (not a Studio panel) so it works
from Claude Code Desktop, the Codex app, or any environment where the skill is
installed.

API:
    GET  /                    -> curator SPA
    GET  /api/run             -> run state (cell, states, frames, current curation)
    GET  /frames/<state>/<f>  -> a frame PNG
    GET  /run/<relpath>       -> a file inside the run dir (atlas/qa previews)
    POST /api/curation        -> atomically write curation.json (request body)
    POST /api/compose         -> re-run compose_sprite_atlas.py, return its result
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile

try:
    from PIL import Image
except ImportError:  # pragma: no cover — 파이프라인 필수 의존성이지만 서버는 살아있게
    Image = None
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from curation import (CURATION_FILENAME, SCHEMA_VERSION, backup_stale_curation, empty_curation,
                      imported_ref_role, load_curation_report, run_revision, stamp_curation)
from extract import heal_run, load_consistent_frames_manifest
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sprite_gen.layout import frames_dir_rel, raw_rel, row_frame_rel, row_orig_rel, state_frame_total
from runio import publish_guard, read_guard

SCRIPTS_DIR = Path(__file__).resolve().parent
CURATOR_DIR = SCRIPTS_DIR / "curator"


def _url(*parts) -> str:
    """/-rooted URL with every path segment percent-encoded. base/ref/state/frame names
    can contain `#`, `%`, space, quotes, non-ASCII — unencoded they break the URL (a `#`
    becomes a fragment → 404) or leak into HTML attributes. do_GET unquote()s on serving,
    so this round-trips; unreserved names (e.g. down_walk, frame-0.png) are unchanged."""
    return "/" + "/".join(quote(str(p), safe="") for p in parts)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}



# ── 픽셀 격자 자동 측정 ──────────────────────────────────────────────
# fit.pixel_perfect 계약이 없는 런(예: --pngs-dir 임포트)에서도 격자 오버레이를 켠다:
# 인접픽셀 색경계 위치가 한 간격의 배수에 몰려 있으면 그 간격이 블록 피치다.
# 축별 브루트포스(경계 질량 ≥80% 인 최대 간격). 측정 실패한 줄은 격자를 그리지 않는다
# (가짜 격자 금지). 결과는 (경로, mtime) 키로 캐시.
_PITCH_CACHE: dict = {}
_BASE_GRID_CACHE: dict = {}  # (base path, mtime_ns) -> /api/base-grid 응답 (검출은 수 초짜리)


def _find_base_path(run_dir: Path):
    for candidate in sorted(run_dir.glob("base-source.*")):
        if candidate.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return candidate
    return None


def _base_grid_response(run_dir: Path, base_path: Path) -> dict:
    """베이스 검출 격자 응답 (mtime 키 캐시). GET /api/base-grid 와 base-edit 의
    논리→raw 확장이 같은 절단선을 쓴다 (SSoT — 클라와 서버가 다른 격자를 보면 안 됨)."""
    cache_key = (str(base_path), base_path.stat().st_mtime_ns)
    cached = _BASE_GRID_CACHE.get(cache_key)
    if cached is not None:
        return cached
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    chroma = tuple(request.get("chroma_key", {}).get("rgb") or (255, 0, 255))
    from extract import (_grid_edges, detect_pixel_grid,
                         remove_chroma_background_ycbcr, solid_alpha_bbox)
    with Image.open(base_path) as opened:
        cleaned = remove_chroma_background_ycbcr(opened.convert("RGBA"), chroma)
    box = solid_alpha_bbox(cleaned) or cleaned.getbbox()
    if not box:
        result = {"grid": None, "note": "base has no content to detect a grid on"}
    else:
        tight = cleaned.crop(box)
        (pitch_x, pitch_y), (phase_x, phase_y) = detect_pixel_grid(tight)
        if min(pitch_x, pitch_y) < 2.0:
            result = {"grid": None, "note": "no confident pixel grid detected"}
        else:
            x_edges = [box[0] + e for e in _grid_edges(tight.width, pitch_x, phase_x)]
            y_edges = [box[1] + e for e in _grid_edges(tight.height, pitch_y, phase_y)]
            result = {"grid": {"xEdges": x_edges, "yEdges": y_edges,
                               "pitch": [round(pitch_x, 2), round(pitch_y, 2)]}}
    _BASE_GRID_CACHE.clear()  # 베이스는 런당 1개 — 이전 세대 항목만 치움
    _BASE_GRID_CACHE[cache_key] = result
    return result


def _uniform_blocks(px, w, h, k):
    """이미지가 k×k 단색 블록으로만 이루어졌는가 (정확 판정)."""
    for by in range(0, h, k):
        for bx in range(0, w, k):
            first = px[bx, by]
            for y in range(by, by + k):
                for x in range(bx, bx + k):
                    if px[x, y] != first:
                        return False
    return True


def detect_pixel_pitch(path):
    """이 프레임에서 논리 픽셀 1개가 차지하는 셀 픽셀 수 — **항상 ≥1, 실패 없음**.

    정확 판정이다(추정 아님): 이미지가 k×k 단색 블록으로만 이루어져 있으면 그건
    k 배 확대본이고, 조건을 만족하는 **최대 k** 가 답이다. k=1 은 1×1 블록이
    자명히 단색이라 언제나 참이므로 이 측정은 **실패할 수 없다** — 즉 "격자를
    모른다" 라는 상태 자체가 존재하지 않고, 그래서 격자 컨트롤을 숨길 근거도 없다.

    왜 추정을 버렸나 (수홍 2026-07-24, "10번 넘게 말한" 조건부 숨김 버그의 뿌리):
    옛 구현은 엣지 자기상관으로 피치를 **추정**하고 2 미만이면 None 으로 접었다.
    그 결과 (a) 이미 논리 해상도인 1:1 픽셀아트가 전부 "검출 실패"로 접혀
    임포트 런에 격자 버튼이 아예 뜨지 않았고, (b) 실측상 실제 스프라이트
    프레임에서도 거의 항상 None 이라 임포트 런은 사실상 격자를 가진 적이 없다.
    엔진의 주기성 검출기(`detect_pixel_grid`)도 대안이 못 된다 — 1:1 프레임에
    4.99 를 냈다(캐릭터 디자인의 반복 무늬를 격자로 오인).
    블록 단색성은 그런 오인이 구조적으로 불가능하다."""
    if Image is None:
        return 1
    try:
        stat = os.stat(path)
    except OSError:
        return 1
    key = (str(path), stat.st_mtime_ns)
    if key in _PITCH_CACHE:
        return _PITCH_CACHE[key]
    scale = 1
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
            px = im.load()
            w, h = im.size
            # 양축을 나누어떨어지게 하는 k 만 후보 (블록이 이미지에 딱 맞아야 한다)
            for k in range(min(w, h), 1, -1):
                if w % k or h % k:
                    continue
                if _uniform_blocks(px, w, h, k):
                    scale = k
                    break
    except OSError:
        scale = 1
    _PITCH_CACHE[key] = scale
    return scale


_REF_DIRECTIONS = ("down45", "up45", "down", "side", "up", "left", "right", "front", "back")


def _state_refs(run_dir, state, request):
    """상태 하나의 생성 레퍼런스 체인(방향 앵커/basis row/레이아웃 가이드).

    directional-anchor 규약의 관례 유도 — run dir 에 실재하는 파일만 노출한다.
    """
    refs = []
    direction = None
    for d in _REF_DIRECTIONS:
        if state == d or state.startswith(d + "_"):
            direction = d
            break
    if direction is not None:
        # 앵커 칩 = 실제 생성에 첨부되는 재료 우선 (수홍 2026-07-19 "다운앵커가 왜
        # 내가 편집해둔 아틀라스가 아니야"): raw idle 스트립은 편집 전 원시 생성물이라
        # 칩으로 보여주면 사용자 편집이 안 들어간 것처럼 오독된다. 리롤/재생성이 매번
        # curated 진실에서 다시 굽는 x8 스냅샷(references/anchors/)이 실재하면 그걸
        # 보여주고, 없을 때만 raw 로 폴백한다 (관례 유도 — 실재 파일만 노출 원칙 유지).
        snapshot = run_dir / "references" / "anchors" / f"{direction}-idle-x8.png"
        anchor_rel = raw_rel(request, f"{direction}_idle")
        anchor = run_dir / anchor_rel
        if state != f"{direction}_idle":
            if snapshot.is_file():
                refs.append({"role": "anchor", "name": snapshot.name,
                             "url": _url("run", "references", "anchors", snapshot.name)})
            elif anchor.is_file():
                refs.append({"role": "anchor", "name": anchor.name, "url": _url("run", *anchor_rel.split("/"))})
        base = state[len(direction) + 1:] if state.startswith(direction + "_") else None
        if base and direction != "down":
            basis_rel = raw_rel(request, f"down_{base}")
            basis = run_dir / basis_rel
            if basis.is_file():
                refs.append({"role": "basis", "name": basis.name, "url": _url("run", *basis_rel.split("/"))})
    guide = run_dir / "references" / "layout-guides" / f"{state}.png"
    if guide.is_file():
        refs.append({"role": "guide", "name": guide.name, "url": _url("run", "references", "layout-guides", f"{state}.png")})
    # imported runs (--pngs-dir): references/imported/<state>/<role>-<name>.png → 생성 재료 칩.
    # role 파싱 SSoT = curation.imported_ref_role. 미지 role 은 스킵한다 — import 가 fail-loud
    # 로 걸러 여기 도달하지 않지만, 손으로 놓인 파일도 조용히 guide 로 relabel 하지 않는다.
    imported_dir = run_dir / "references" / "imported" / state
    if imported_dir.is_dir():
        for ref in sorted(imported_dir.glob("*.png")):
            role = imported_ref_role(ref.name)
            if role is None:
                continue
            refs.append({"role": role, "name": ref.name,
                         "url": _url("run", "references", "imported", state, ref.name)})
    return refs



def _read_op_progress(run_dir: Path) -> dict | None:
    """엔진이 기록한 진행 파일 (.sprite-gen.progress.json) — 없으면 None."""
    try:
        return json.loads((run_dir / ".sprite-gen.progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_PIXEL_PREVIEW_DIR = ".pixel-preview"
_PIXEL_PREVIEW_BUDGET = 24  # /api/run 한 번당 신규 검출·스냅 상한 (초과분은 deferred 로 보고)


def _pixel_preview_meta(run_dir: Path, rel: str, budget: dict) -> dict:
    """트윈(.plain/orig) 없는 프레임의 온디맨드 픽셀퍼펙트 프리뷰 (파생 캐시).

    임포트 세트·비 pp 런에서도 뷰의 픽셀퍼펙트 토글이 동작해야 한다 (수홍
    2026-07-20, plan sprite-gen/per-frame-pixel-grid). 스냅은 파이프라인과 같은
    프레임별 자체 검출 격자 정책이다. 검출 실패(이미 논리 해상도 픽셀아트 포함)는
    프리뷰 없음으로 남긴다 — 가짜 격자 금지. 검출은 프레임당 수 초라 요청당
    예산(_PIXEL_PREVIEW_BUDGET)으로 캡하고, 밀린 수는 스냅샷에 보고한다
    (조용한 캡 금지 — 리로드가 이어서 계산한다).

    파생 캐시 계약: 키 = (source mtime, engine_revision) — 스냅 알고리즘이
    바뀌면 stale 프리뷰가 자동 무효화된다. PNG 유실 시 meta 를 캐시 히트로 치지
    않는다 (self-heal, 원칙 1). 쓰기는 전부 atomic replace (runio) — 스레딩
    서버의 동시 계산은 결정론 출력의 last-write-wins 로 수렴한다 (중복 계산은
    허용하되 torn file 은 없음, 원칙 4/8)."""
    src = run_dir / rel
    try:
        mtime = src.stat().st_mtime_ns
    except OSError:
        return {}
    from extract import engine_revision
    engine = engine_revision()
    out_dir = run_dir / _PIXEL_PREVIEW_DIR
    stem = rel.replace("/", "__")
    meta_path = out_dir / (stem + ".json")
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if (meta.get("mtime") == mtime and meta.get("engine") == engine and (
                    not meta.get("file") or (out_dir / meta["file"]).is_file())):
                return meta
        except ValueError:
            pass
    if budget["left"] <= 0:
        return {"deferred": True}
    budget["left"] -= 1
    from extract import binarize_alpha, detect_pixel_grid, grid_snap_downscale
    from runio import atomic_save_image, atomic_write_text
    with Image.open(src) as opened:
        image = opened.convert("RGBA")
    (pitch_x, pitch_y), (phase_x, phase_y) = detect_pixel_grid(image)
    out_dir.mkdir(exist_ok=True)
    if min(pitch_x, pitch_y) < 2.0:
        meta = {"mtime": mtime, "engine": engine, "pitch": None,
                "note": "no confident pixel grid"}
    else:
        snapped = binarize_alpha(grid_snap_downscale(
            image, (pitch_x, pitch_y), detail_bias=True, phase=(phase_x, phase_y)))
        atomic_save_image(snapped, out_dir / stem)
        meta = {"mtime": mtime, "engine": engine,
                "pitch": [round(pitch_x, 2), round(pitch_y, 2)],
                "file": stem, "size": [snapped.width, snapped.height]}
    # meta 는 PNG 다음에 쓴다 — meta 가 보이면 PNG 는 반드시 있다 (쓰기 순서 불변식)
    atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False) + "\n")
    return meta


def build_run_state(run_dir: Path) -> dict:
    """Assemble the run snapshot the SPA needs. Read under the run dir's shared read_guard
    so a concurrent `--force` re-import (which holds the exclusive publish_guard for its
    swap) can never expose a half-published state to `/api/run` — the reader sees either
    the complete prior run or the complete new run (reader isolation)."""
    with read_guard(run_dir):
        return _build_run_state_impl(run_dir)


def _build_run_state_impl(run_dir: Path) -> dict:
    """Assemble the run snapshot the SPA needs, from the canonical SSoT files."""
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    # No generation yet (no manifest AND no physical frames) → serve the request/state scaffold
    # (legitimate). A present-but-corrupt/inconsistent manifest, or an orphan (frames without a
    # manifest), fails loud (load_consistent_frames_manifest raises) and surfaces as HTTP 500 in
    # do_GET — never a silent empty-rows / stale-frame fallback (No Silent Fallback / Consistency).
    frames_manifest = load_consistent_frames_manifest(run_dir, allow_pending_states=True) or {"rows": []}
    rows_by_state = {row["state"]: row for row in frames_manifest.get("rows", [])}

    cell = request["cell"]
    cell_state = {
        "width": int(cell.get("width", cell.get("size", 0))),
        "height": int(cell.get("height", cell.get("size", 0))),
        # 안전영역/여백 알림 계산용 (여백 침범 = 정보성, 리롤 대상 아님)
        "safeMarginX": int(cell.get("safe_margin_x", cell.get("safe_margin", 0))),
        "safeMarginY": int(cell.get("safe_margin_y", cell.get("safe_margin", 0))),
    }

    # 픽셀퍼펙트 격자: 논리 픽셀 1칸이 셀 픽셀 몇 칸인가. extract 의 pp_scale 과 같은 식이어야
    # 큐레이터 오버레이가 "실제로 스냅된 격자"를 그린다 (셀 래스터가 아니라).
    fit = request.get("fit") or {}
    pixel_perfect = None
    if fit.get("pixel_perfect"):
        logical_height = int(fit.get("logical_height", cell_state["height"]))
        scale = max(1, cell_state["height"] // max(1, logical_height))
        pixel_perfect = {"logicalHeight": logical_height, "scale": scale, "source": "request", "label": f"{logical_height}px"}

    states = []
    # 온디맨드 픽셀 프리뷰 계산 예산 (요청당) — 초과분은 deferred 로 세서 보고한다
    preview_budget = {"left": _PIXEL_PREVIEW_BUDGET, "deferred": 0}
    for state, entry in request["states"].items():
        row = rows_by_state.get(state, {})
        files = row.get("files", [])
        labels = row.get("labels", [])
        frame_count = state_frame_total(request, state)
        state_raw_rel = raw_rel(request, state)
        raw_present = (run_dir / state_raw_rel).is_file()
        state_frames_rel = frames_dir_rel(request, state)
        frames = []
        for index in range(frame_count):
            # 파일 위치 SSoT = manifest row files (병합 후보 등 비패턴 경로 포함).
            # row 가 없거나(index 초과 포함) 미생성이면 리졸버의 예약 위치로 표시.
            row_files = row.get("files") or []
            if index < len(row_files):
                rel = row_files[index]
            else:
                rel = f"{state_frames_rel}/frame-{index}.png"
            present = (run_dir / rel).is_file()
            frame = {"index": index, "url": _url(*rel.split("/")), "present": present}
            # pp 해제 토글 표시본: 원본 화질(orig/ 고해상본) 우선, 없으면 셀 크기 .plain.png.
            # 둘 중 하나라도 있으면 큐레이터가 전/후 토글을 켠다.
            head, _, tail = rel.rpartition("/")
            orig_rel = f"{head}/orig/{tail}"
            plain_rel = rel[: -len(".png")] + ".plain.png"
            if (run_dir / orig_rel).is_file():
                frame["plainUrl"] = _url(*orig_rel.split("/"))
            elif (run_dir / plain_rel).is_file():
                frame["plainUrl"] = _url(*plain_rel.split("/"))
            elif present and Image is not None:
                # 트윈 없는 프레임(임포트 세트·비 pp 런): 온디맨드 픽셀퍼펙트
                # 프리뷰를 붙여 토글이 어디서나 동작하게 한다 (_pixel_preview_meta).
                preview = _pixel_preview_meta(run_dir, rel, preview_budget)
                if preview.get("file"):
                    frame["pixelPreviewUrl"] = _url("run", _PIXEL_PREVIEW_DIR, preview["file"])
                    frame["pixelPreviewPitch"] = preview["pitch"]
                elif preview.get("deferred"):
                    preview_budget["deferred"] += 1
            if index < len(labels):
                frame["label"] = labels[index]
            # 추출이 검출한 실제 절단선 (쌍둥이 셀 좌표) — 원본 화질 뷰의 최종 대응
            # 격자가 균등 분할 대신 이 선을 그린다 (중간 드리프트 제거)
            row_grids = row.get("input_grids") or []
            if index < len(row_grids) and row_grids[index]:
                frame["inputGrid"] = row_grids[index]
            if present and Image is not None:
                # 실제 스프라이트 픽셀 크기(투명 패딩 제외 알파 bbox) — 사이즈 통일 검수용
                try:
                    with Image.open(run_dir / rel) as im:
                        frame["size"] = [im.width, im.height]
                        alpha = im.getchannel("A") if "A" in im.getbands() else None
                        bbox = alpha.getbbox() if alpha is not None else im.getbbox()
                        if bbox:
                            frame["contentSize"] = [bbox[2] - bbox[0], bbox[3] - bbox[1]]
                            # 최종 픽셀 콘텐츠 bbox(셀 좌표) — 원본 뷰의 "최종 대응 격자"
                            # (칸 수 = 최종 픽셀 수)가 이 상자를 균등 분할해 그린다
                            frame["contentBox"] = list(bbox)
                except OSError:
                    pass
            frames.append(frame)
        state_scale = None
        if pixel_perfect is not None:
            state_scale = pixel_perfect["scale"]
        else:
            for fr in frames:
                if fr.get("present"):
                    # measure from the real (decoded) file path, not fr["url"] — the url is
                    # percent-encoded for HTTP, so a special-char state name would point the
                    # measurement at a nonexistent encoded dir → pixelScale silently null.
                    row_files_m = row.get("files") or []
                    rel_m = row_files_m[fr["index"]] if fr["index"] < len(row_files_m) else f"{state_frames_rel}/frame-{fr['index']}.png"
                    state_scale = detect_pixel_pitch(run_dir / rel_m)
                    break
        states.append(
            {
                "name": state,
                "rawPresent": raw_present,
                "pixelScale": state_scale,
                "refs": _state_refs(run_dir, state, request),
                "fps": int(entry.get("fps", 6)),
                "loop": bool(entry.get("loop", True)),
                "action": entry.get("action", ""),
                "requestFrames": frame_count,
                "extractOk": bool(row.get("ok", bool(files))),
                "frames": frames,
                # 테이크 선언 (호흡 에디터가 저장된 선/진폭을 복원하는 데 쓴다)
                "takes": entry.get("takes") or [],
            }
        )

    # 방향 계약 런: 뷰가 방향 그룹(앵커 우선)으로 묶고 미러 방향(생성 생략)을 표시한다.
    directions_cfg = request.get("directions")
    direction_groups = None
    if directions_cfg and directions_cfg.get("set"):
        suffix = directions_cfg.get("anchor_suffix", "idle")
        direction_groups = []
        for direction in directions_cfg["set"]:
            anchor = f"{direction}_{suffix}"
            members = [s for s in request["states"] if s.startswith(direction + "_")]
            # 앵커를 그룹 맨 앞으로 (요청 순서 보존, 앵커만 승격)
            if anchor in members:
                members = [anchor, *[m for m in members if m != anchor]]
            direction_groups.append({
                "direction": direction,
                "anchor": anchor if anchor in request["states"] else None,
                "states": members,
            })
        for target, source in (directions_cfg.get("mirror") or {}).items():
            direction_groups.append({"direction": target, "mirrorOf": source, "states": []})

    # 방향 앵커 파일 (references/anchors/*.png) — 파일트리 표시용
    anchors_dir = run_dir / "references" / "anchors"
    anchor_files = []
    if anchors_dir.is_dir():
        for path in sorted(anchors_dir.glob("*.png")):
            anchor_files.append({"name": path.name, "url": _url("run", "references", "anchors", path.name)})

    curation, curation_report = load_curation_report(run_dir)
    curation = curation or empty_curation()
    # 원본 베이스(아이덴티티 truth)가 있으면 큐레이터 최상단에 참조 줄로 노출
    base_url = None
    for candidate in sorted(run_dir.glob("base-source.*")):
        if candidate.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            base_url = _url("run", candidate.name)
            break
    # 뷰 계약 자가 보고 (run-contract.md §3): base 참조 줄 / 생성 재료 칩 / 픽셀 격자
    # 충족 여부. 셋 다 없으면 "소스 없는 뷰" — 세션마다 경험이 갈라지는 신호.
    has_base = base_url is not None
    refs_states = sum(1 for st in states if st.get("refs"))
    # 격자는 언제나 있다 — `detect_pixel_pitch` 가 실패할 수 없는 정확 판정이라
    # 모든 상태가 ≥1 의 scale 을 갖는다. 조건부 False 는 곧 컨트롤 숨김이었고
    # 그게 버그였다 (수홍 2026-07-24).
    has_grid = True
    contract = {
        "base": has_base,
        "refs": refs_states > 0,
        "refsStates": refs_states,
        "grid": has_grid,
        # 격자가 보편이 된 뒤로는 판별자가 못 된다 — 항상 참인 항을 or 에 두면
        # sourceless 가 영원히 False 라 경고가 죽는다. 남은 판별자(base/refs)로만 본다.
        "sourceless": not (has_base or refs_states > 0),
    }
    return {
        "characterId": request["character"]["id"],
        "runDir": str(run_dir),
        "baseUrl": base_url,
        "cell": cell_state,
        # 계약 scale(pp 런)이 없으면 줄별 실측이 진실이다 — null 로 접지 않는다.
        "pixelPerfect": pixel_perfect if pixel_perfect is not None else {
            "source": "auto", "label": "auto",
            "scale": min((st["pixelScale"] for st in states if st.get("pixelScale")), default=1)},
        "schemaVersion": SCHEMA_VERSION,
        "runRevision": run_revision(run_dir),
        "directionGroups": direction_groups,
        "anchorFiles": anchor_files,
        "states": states,
        "curation": curation,
        # 세대 불일치로 이번 로드에서 무효화(드롭)된 행 + 원문 백업 파일명 — 뷰가
        # 배너로 알린다 (stderr 만으로는 사용자가 못 본다; 조용한 소실 금지).
        "curationDropped": curation_report["dropped"],
        "curationBackup": curation_report["backup"],
        "iso": request.get("iso"),
        "lang": CurationHandler.lang,
        "hasAtlas": (run_dir / "sprite-sheet-alpha.png").is_file(),
        # 최종 산출물 섹션 (뷰 맨 아래, 아틀라스+manifest 좌우) — 파일이 실재할 때만.
        # 아틀라스는 다운로드/합성 시점 산출물이라 mtime 을 실어 시점을 표시한다.
        "atlas": _atlas_info(run_dir),
        "fitPixelPerfect": bool((request.get("fit") or {}).get("pixel_perfect")),
        # 예산에 밀려 아직 못 만든 온디맨드 픽셀 프리뷰 수 — 0 이 아니면 리로드가 이어서 계산
        "pixelPreviewDeferred": preview_budget["deferred"],
        "contract": contract,
    }


def _atlas_info(run_dir: Path) -> dict | None:
    """최종 아틀라스 + 런타임 manifest 존재/시점 정보 (뷰 하단 섹션용)."""
    atlas_path = run_dir / "sprite-sheet-alpha.png"
    if not atlas_path.is_file():
        return None
    mtime = int(atlas_path.stat().st_mtime)
    manifest_path = run_dir / "manifest.json"
    return {
        "url": _url("run", "sprite-sheet-alpha.png") + f"?v={mtime}",
        "mtime": mtime,
        "manifestUrl": (_url("run", "manifest.json") + f"?v={mtime}") if manifest_path.is_file() else None,
    }


def write_curation_atomic(run_dir: Path, payload: dict) -> None:
    """Atomically replace curation.json (temp file in the same dir + os.replace). Stamps the
    sidecar with the current run generation (`run_revision`) AND per-state `revision`
    segment fingerprints (stamp_curation), so a later regeneration invalidates only the
    rows it actually touched. Before replacing, any state entry in the existing file that
    this write would lose (missing from the payload, or stamped for an incompatible
    generation) triggers a `curation.stale-<hash>.json` backup of the old file — an
    autosave can never permanently destroy selections without an observable copy.
    `runRevision` is a transport-only echo field and is not stored."""
    if payload.get("kind") != "sprite-gen-curation":
        raise ValueError("payload is not a sprite-gen-curation document")
    payload = stamp_curation(run_dir, payload)
    target = run_dir / CURATION_FILENAME
    if target.is_file():
        old_text = target.read_text(encoding="utf-8")
        try:
            old = json.loads(old_text)
        except json.JSONDecodeError:
            old = None
        if isinstance(old, dict):
            new_states = payload.get("states") or {}
            same_generation = old.get("run_revision") == payload.get("run_revision")
            for name, old_entry in (old.get("states") or {}).items():
                new_entry = new_states.get(name)
                if not isinstance(old_entry, dict):
                    continue
                if not isinstance(new_entry, dict):
                    lost = True
                else:
                    old_rev, new_rev = old_entry.get("revision"), new_entry.get("revision")
                    if isinstance(old_rev, list) and isinstance(new_rev, list):
                        lost = old_rev != new_rev[:len(old_rev)]
                    else:
                        # 레거시 스탬프 없는 항목: 같은 런 세대의 정상 편집이면 호환
                        lost = not same_generation
                if lost:
                    backup_stale_curation(run_dir, old_text)
                    break
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(run_dir), prefix=".curation-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


_heal_lock = threading.Lock()


_HEAL_GRACE_SECONDS = 1.5  # 신선 케이스(해시 비교 ms)는 이 안에 끝난다 — 그 이상이면 장기 heal
_heal_state: dict = {"thread": None, "pending_report": None, "failed_attempt": None}


def _heal_attempt_key(run_dir: Path) -> tuple:
    """재시도 여부를 가르는 엔진+입력 스냅샷.

    결정론적으로 실패한 heal 을 같은 입력으로 계속 재실행하면 진행률이 끝에서
    0%로 돌아가는 무한 루프가 된다. 엔진이나 실제 재료가 바뀐 경우에만 새
    시도로 보도록 request/curation/manifest 와 raw 파일 stat 을 묶는다.
    """
    from extract import engine_revision

    paths = [run_dir / "sprite-request.json", run_dir / "curation.json",
             run_dir / "frames" / "frames-manifest.json"]
    raw_dir = run_dir / "raw"
    if raw_dir.is_dir():
        paths.extend(path for path in raw_dir.rglob("*") if path.is_file())
    fingerprints = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        try:
            stat = path.stat()
            fingerprints.append((path.relative_to(run_dir).as_posix(),
                                 stat.st_mtime_ns, stat.st_size))
        except OSError:
            fingerprints.append((path.relative_to(run_dir).as_posix(), None, None))
    return engine_revision(), tuple(fingerprints)


def _durable_failed_heal_report(run_dir: Path, attempt_key: tuple) -> dict | None:
    """현재 입력으로 이미 실패한 extract 증거가 있으면 재실행하지 않는다.

    extract-failure.json 은 실패한 원자적 배포가 남기는 canonical evidence 다.
    실패 파일이 모든 입력보다 새롭고 엔진 리비전도 같으면 서버 재기동 뒤에도
    같은 실패를 다시 태우지 않고 이전 세대를 열어 준다.
    """
    failure_path = run_dir / "extract-failure.json"
    try:
        failure_stat = failure_path.stat()
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if failure.get("ok") is not False or failure.get("engine_revision") != attempt_key[0]:
        return None
    input_mtimes = [entry[1] for entry in attempt_key[1] if entry[1] is not None]
    if input_mtimes and failure_stat.st_mtime_ns < max(input_mtimes):
        return None
    failed = sorted({str(row.get("state")) for row in failure.get("rows", [])
                     if row.get("state") and row.get("ok") is False})
    if not failed:
        failed = sorted({str(error).split(":", 1)[0] for error in failure.get("errors", [])
                         if ":" in str(error)})
    return {"healed": [], "kept_stale": [], "failed": failed,
            "notes": ["same engine/input already failed validation; previous frames kept"]}


def _run_heal(run_dir: Path, attempt_key: tuple) -> None:
    attempt_failed = False
    try:
        report = heal_run(run_dir)
    except (Exception, SystemExit) as exc:
        attempt_failed = True
        report = {"healed": [], "kept_stale": [], "failed": [],
                  "notes": [f"heal skipped: {exc}"]}
    with _heal_lock:
        if report["healed"] or report["kept_stale"] or report.get("failed") or report["notes"]:
            _heal_state["pending_report"] = report
        if report.get("failed") or attempt_failed:
            _heal_state["failed_attempt"] = attempt_key
        elif _heal_state["failed_attempt"] == attempt_key:
            _heal_state["failed_attempt"] = None
        _heal_state["thread"] = None


def maybe_heal(run_dir: Path) -> bool:
    """실시간 계약 (수홍 확정 2026-07-14): 뷰에 '재추출' 개념이 없다.

    frames/ 는 (raw + request + 현재 엔진 + 큐레이션)의 파생 캐시다 — 요청이
    들어올 때마다 행별 engine_revision 을 현재 엔진과 비교해, 다르면 raw 에서
    다시 굽는다 (heal_run). 신선하면 해시 비교 몇 ms 로 끝난다.

    장기 heal 을 요청 핸들러 안에서 동기로 돌리면 첫 탭이 진행률 UI 없이 빈
    화면으로 수십 분 멈춘다 (실사고 2026-07-20, plan long-op-loading-ux — 엔진
    갱신 후 첫 로드). 그래서 heal 은 백그라운드 스레드로 발사하고 그레이스만
    동기 대기한다: 그 안에 끝나면(신선/소규모) 기존과 동일한 동기 경로, 넘으면
    busy=True 를 반환해 /api/run 이 즉시 진행률 포함 busy 로 응답한다.
    단일 비행은 스레드 슬롯이 보장하고(동시 재추출 금지), heal 리포트는 유실
    없이 다음 성공 스냅샷에 1회 첨부된다. 실패는 뷰를 죽이지 않고 노트로 남는다.

    반환: busy 여부 (bool 단일 계약). 리포트는 여기서 소비하지 않는다 — 소비자는
    take_heal_report() 하나뿐이다 (/api/run 성공 스냅샷 경로). /api/progress
    폴링이나 다운로드가 heal 완료 직후 먼저 도착하면 리포트를 먹어버려 다음
    /api/run 첨부가 유실되던 경쟁을 막는다 (validator kongkongi 재현,
    2026-07-20 — 단일 소비자 원칙).
    """
    attempt_key = _heal_attempt_key(run_dir)
    with _heal_lock:
        thread = _heal_state["thread"]
        if thread is None or not thread.is_alive():
            if _heal_state["failed_attempt"] == attempt_key:
                return False
            prior_failure = _durable_failed_heal_report(run_dir, attempt_key)
            if prior_failure is not None:
                _heal_state["failed_attempt"] = attempt_key
                if _heal_state["pending_report"] is None:
                    _heal_state["pending_report"] = prior_failure
                return False
            thread = threading.Thread(target=_run_heal, args=(run_dir, attempt_key), daemon=True)
            _heal_state["thread"] = thread
            thread.start()
    thread.join(timeout=_HEAL_GRACE_SECONDS)
    return thread.is_alive()


def take_heal_report() -> dict | None:
    """pending heal 리포트의 유일한 소비자 — /api/run 이 스냅샷을 성공적으로
    만든 뒤에만 호출한다 (스냅샷 실패 시 리포트는 남아 다음 성공에 붙는다)."""
    with _heal_lock:
        report = _heal_state["pending_report"]
        _heal_state["pending_report"] = None
    return report


def _zip_paths(base: Path, paths: list[Path]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.relative_to(base).as_posix())
    return buffer.getvalue()


def build_download(run_dir: Path, kind: str) -> tuple[bytes, str] | dict:
    """내보내기 버튼 3종 = '지금 보이는 라이브 상태의 다운로드' (수홍 확정 2026-07-14).

    게임/어디에 적용한다는 의미가 아니다 — 현재 (프레임 캐시 + 큐레이션)를
    합성 스크립트로 계산해 파일로 손에 쥐여준다. 계산 산출물은 런 폴더에도
    남는다 (런 폴더 = 작업장, 다운로드 = 핸드오프). 실패 시 dict(에러)."""
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    character = str(request.get("character", {}).get("id") or run_dir.name)
    if kind == "atlas":
        result = run_compose(run_dir)
        if not result["ok"]:
            return result
        files = [run_dir / "sprite-sheet-alpha.png", run_dir / "manifest.json"]
        files = [f for f in files if f.is_file()]
        return _zip_paths(run_dir, files), f"{character}-atlas.zip"
    if kind == "pngs":
        result = run_export(run_dir)
        if not result["ok"]:
            return result
        out = run_dir / "curated"
        return _zip_paths(run_dir, sorted(out.rglob("*.png"))), f"{character}-pngs.zip"
    if kind == "gifs":
        result = run_export_gif(run_dir)
        if not result["ok"]:
            return result
        out = run_dir / "exports"
        return _zip_paths(run_dir, sorted(out.glob("*.gif"))), f"{character}-gifs.zip"
    if kind.startswith("gif:"):
        # 한 줄(state) 단건 — 현재 큐레이션 합성 그대로, zip 없이 GIF 원파일로.
        # 검수/공유용이라 4x 니어리스트를 기본으로 굽는다 (뷰어 확대 보간 뭉갬 방지;
        # 픽셀 데이터는 그대로). ?scale=1 로 원배율. 게임은 아틀라스를 쓰므로 무관.
        state, _, scale_part = kind[len("gif:"):].partition(":")
        scale = max(1, min(8, int(scale_part or 4)))
        result = run_export_gif(run_dir, state=state, scale=scale)
        if not result["ok"]:
            return result
        gif_path = run_dir / "exports" / f"{state}.gif"
        if not gif_path.is_file():
            return {"ok": False, "error": f"gif not produced: {gif_path}"}
        return gif_path.read_bytes(), f"{character}-{state}.gif"
    return {"ok": False, "error": f"unknown download kind: {kind}"}


def _run_script(name: str, run_dir: Path, *extra: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), "--run-dir", str(run_dir), *extra],
        capture_output=True,
        text=True,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def run_compose(run_dir: Path) -> dict:
    """Re-run the atlas compose step so curation bakes into atlas/manifest."""
    return _run_script("compose_sprite_atlas.py", run_dir)


def run_interpolate(run_dir: Path, state: str, index_a: int, index_b: int,
                    t: float, label: str | None, provider: str = "codex") -> dict:
    """AI in-between (interpolate_frames.py): 테이크 기록 + 전체 배치 재추출.

    생성은 서버 머신의 provider CLI(codex/grok, 머신 로컬 OAuth)가 수행한다 —
    브라우저에는 자격증명이 지나가지 않는다. 부분 추출은 제공하지 않는다 —
    run-wide 팔레트가 추출 배치 구성에 결합돼 있어 (docs/frame-interpolation.md)
    단일 행 추출은 그 행만 다른 팔레트로 굽는다."""
    extra = ["--state", state, "--between", str(index_a), str(index_b),
             "--provider", provider, "--t", str(t), "--extract"]
    if label:
        extra += ["--label", str(label)]
    return _run_script("interpolate_frames.py", run_dir, *extra)


def run_reroll(run_dir: Path, state: str, provider: str = "codex") -> dict:
    """행 리롤 (reroll_state_row.py): 새 테이크 생성 + 전체 배치 재추출.

    primary 를 덮지 않는다 — 후보군 병기(테이크)가 계약이다 (수홍 2026-07-19
    "리롤버튼 눌러서 후보군에 추가"). 생성은 interpolate 와 같이 서버 머신의
    provider CLI 가 수행하고, 부분 추출 없이 전체 배치를 재추출한다."""
    return _run_script("reroll_state_row.py", run_dir,
                       "--state", state, "--provider", provider, "--extract")


def run_export(run_dir: Path) -> dict:
    """Export curated frames back to named PNGs under <run-dir>/curated/."""
    result = _run_script("export_curated_pngs.py", run_dir)
    if result["ok"] and result["stdout"]:
        try:
            result["export"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return result


def run_export_gif(run_dir: Path, state: str | None = None, scale: int = 1) -> dict:
    """Export clean transparent GIF(s) under <run-dir>/exports/.

    Reuses compose_sprite_gif.py --run-dir, which applies the same curation
    selection/order/transform as the atlas compose (curation.py SSoT).
    `state` limits to one row (`--state`) — the per-row download button path."""
    extra = (["--state", state] if state else []) + (["--scale", str(scale)] if scale > 1 else [])
    result = _run_script("compose_sprite_gif.py", run_dir, *extra)
    if result["ok"] and result["stdout"]:
        try:
            result["gif"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return result


class CurationHandler(BaseHTTPRequestHandler):
    run_dir: Path = Path(".")
    lang: str = "en"

    def log_message(self, *_args):  # quieter console
        pass

    # --- helpers -------------------------------------------------------------

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "not found", "path": str(path)}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _safe_path(base: Path, rel: str) -> Path | None:
        """Resolve `rel` under `base`, refusing anything that escapes it."""
        base = base.resolve()
        candidate = (base / unquote(rel)).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        return candidate

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    # --- routes --------------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_file(CURATOR_DIR / "index.html")
            return
        if path == "/api/run":
            try:
                busy = maybe_heal(self.run_dir)  # 실시간 계약: 스냅샷 전에 캐시 자가치유
                if busy:
                    # 장기 heal 진행 중 — 첫 탭부터 진행률 로딩 UI 가 뜨도록 즉답
                    self._send_json({
                        "error": "re-extraction in progress — frames are being "
                                 "re-derived for the current engine; the view opens "
                                 "automatically when it finishes",
                        "busy": True, "lang": CurationHandler.lang,
                        "progress": _read_op_progress(self.run_dir)}, 503)
                    return
                snapshot = build_run_state(self.run_dir)
                heal = take_heal_report()  # 스냅샷 성공 뒤에만 소비 (단일 소비자)
                if heal:
                    snapshot["heal"] = heal
                self._send_json(snapshot)
            except (Exception, SystemExit) as exc:  # incl. load_frames_manifest fail-loud; no silent fallback
                # 재추출이 돌고 있으면 request(새 테이크 선언)와 manifest(이전 세대)가
                # 일시적으로 어긋난다 — corrupt 가 아니라 in-flight 다. 락 보유 중엔
                # 그렇게 말해준다 (실사고 2026-07-17: 보간 직후 로드가 'corrupt frames
                # manifest' 로 보여 사용자가 파손으로 오인).
                if (self.run_dir / ".sprite-gen.lock").exists():
                    self._send_json({"error": "re-extraction in progress — "
                                     "the run is being re-derived; reload when it finishes",
                                     "busy": True, "progress": _read_op_progress(self.run_dir)}, 503)
                else:
                    self._send_json({"error": str(exc)}, 500)
            return
        if path == "/api/op-progress":
            # 오래 걸리는 파이프라인(추출 등)의 잘게 쪼갠 진행도 — 큐레이터가 폴링해
            # 퍼센트 표시 (수홍 요청 2026-07-18). busy=락 보유 여부, progress=엔진 기록.
            self._send_json({
                "busy": (self.run_dir / ".sprite-gen.lock").exists(),
                "progress": _read_op_progress(self.run_dir),
            })
            return
        if path == "/api/base-grid":
            # 베이스의 검출 픽셀 격자 — 편집기(줌 모달)의 논리 해상도와 base-edit 의
            # 논리→raw 확장이 같은 절단선을 쓴다 (_base_grid_response, mtime 캐시).
            try:
                base_path = _find_base_path(self.run_dir)
                if base_path is None:
                    self._send_json({"error": "no base-source image in this run"}, 404)
                    return
                self._send_json(_base_grid_response(self.run_dir, base_path))
            except (Exception, SystemExit) as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if path.startswith("/download/"):
            kind = path[len("/download/"):]
            if kind == "gif":
                query = parse_qs(urlparse(self.path).query)
                state = (query.get("state") or [""])[0]
                scale = (query.get("scale") or ["4"])[0]
                kind = f"gif:{state}:{scale}"
            try:
                busy = maybe_heal(self.run_dir)  # 리포트 미소비 — 소비자는 /api/run 만
                if busy:
                    self._send_json({
                        "error": "re-extraction in progress — retry the download "
                                 "when it finishes",
                        "busy": True,
                        "progress": _read_op_progress(self.run_dir)}, 503)
                    return
                built = build_download(self.run_dir, kind)
            except (Exception, SystemExit) as exc:
                self._send_json({"error": str(exc)}, 500)
                return
            if isinstance(built, dict):
                self._send_json(built, 500)
                return
            data, filename = built
            self.send_response(200)
            self.send_header("Content-Type",
                             "image/gif" if filename.endswith(".gif") else "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("X-Filename", filename)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/progress":
            # 가벼운 생성 진행 스냅샷 (트리 실시간 갱신용 폴링 대상): 상태별 raw 유무 +
            # 추출 프레임 수 + 런 세대. /api/run 전체 스냅샷(프레임 이미지 오픈)보다 훨씬 싸다.
            try:
                # 페이지가 열린 채 엔진이 바뀌어도 다음 폴에서 자가치유된다 —
                # 프레임 세대가 바뀌면 runRevision 변화로 클라이언트가 리로드한다.
                busy = maybe_heal(self.run_dir)  # 리포트 미소비 — 소비자는 /api/run 만
                if busy:
                    # heal 이 publish 락을 쥔 동안 read_guard 에서 행 걸리지 않게 즉답
                    self._send_json({"busy": True,
                                     "progress": _read_op_progress(self.run_dir)}, 503)
                    return
                with read_guard(self.run_dir):
                    request = json.loads((self.run_dir / "sprite-request.json").read_text(encoding="utf-8"))
                    progress = []
                    for state in request["states"]:
                        state_frames = frames_dir_rel(request, state)
                        state_raw = raw_rel(request, state)
                        state_dir = self.run_dir / state_frames
                        count = 0
                        if state_dir.is_dir():
                            count = sum(1 for f in state_dir.glob("frame-*.png") if not f.name.endswith(".plain.png"))
                        progress.append({
                            "name": state,
                            "raw": (self.run_dir / state_raw).is_file(),
                            "frames": count,
                            # 트리 썸네일용 실제 경로 URL (클라이언트 패턴 조립 금지)
                            "rawUrl": _url("run", *state_raw.split("/")),
                            "frame0Url": _url(*f"{state_frames}/frame-0.png".split("/")),
                            "relRaw": state_raw,
                            "relFrames": state_frames,
                        })
                    self._send_json({"states": progress, "runRevision": run_revision(self.run_dir)})
            except (Exception, SystemExit) as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if path.startswith("/curator/"):
            resolved = self._safe_path(CURATOR_DIR, path[len("/curator/"):])
            if resolved is None:
                self._send_json({"error": "path escapes curator dir"}, 403)
                return
            self._send_file(resolved)
            return
        if path.startswith("/frames/") or path.startswith("/run/"):
            rel = path[len("/run/"):] if path.startswith("/run/") else path[1:]
            resolved = self._safe_path(self.run_dir, rel)
            if resolved is None:
                self._send_json({"error": "path escapes run dir"}, 403)
                return
            # read_guard: serve run-dir files under the shared reader lock so a concurrent
            # publish swap can't 404 a file mid-move (reader isolation, same as /api/run).
            with read_guard(self.run_dir):
                self._send_file(resolved)
            return
        # bare static asset (curator.js / curator.css served from /)
        asset = self._safe_path(CURATOR_DIR, path.lstrip("/"))
        if asset is None:
            self._send_json({"error": "path escapes curator dir"}, 403)
            return
        if asset.is_file():
            self._send_file(asset)
            return
        self._send_json({"error": "not found", "path": path}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/curation":
                payload = self._read_body()
                # Serialize the curation write with a concurrent --force publish (same
                # publish_guard the swap holds), and reject a payload whose states are no
                # longer in the current run. So a stale autosave from a webview still on the
                # pre-re-import run can neither interleave with the swap nor land old-state
                # curation on the new run (Consistency/Isolation — observable 409, not a
                # silent overwrite). This uses the rwlock, not the pipeline write lock, so
                # normal curation edits still never block on a running compose/extract.
                with publish_guard(self.run_dir):
                    # reject a stale autosave: the POST must echo the runRevision it was
                    # loaded with. If the run generation changed under this session — a
                    # `--force` re-import or a re-extract, even one keeping the same state
                    # names but swapping the candidate images — old selections/transforms
                    # must not apply to the new frames (Consistency: observable 409, not a
                    # silent overwrite). runRevision is a content fingerprint of the frames.
                    stale = payload.get("runRevision") != run_revision(self.run_dir)
                    if not stale:
                        # 엔진-소유 사이드카 필드 보존: 클라이언트 payload 는 자기가 아는
                        # 필드만 싣는다 — 에이전트/엔진이 심어둔 행 필드(frozen 등)가
                        # 클라 저장에 조용히 드랍되면 보호 계약이 사라진다 (실사고
                        # 2026-07-19: founder frozen 마커 소실, darami 무결성 감사 발견).
                        # 계약: 기존 파일에 있고 payload 의 그 행에 없는 화이트리스트
                        # 필드는 이월한다.
                        engine_owned = ("frozen",)
                        try:
                            existing = json.loads((self.run_dir / CURATION_FILENAME).read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            existing = {}
                        for state_name, prev_entry in (existing.get("states") or {}).items():
                            if not isinstance(prev_entry, dict):
                                continue
                            slot = (payload.get("states") or {}).get(state_name)
                            if not isinstance(slot, dict):
                                continue
                            for field in engine_owned:
                                if field in prev_entry and field not in slot:
                                    slot[field] = prev_entry[field]
                        write_curation_atomic(self.run_dir, payload)
                if stale:
                    self._send_json({"error": "curation is from a different run generation "
                                     "(the run changed under this session; reload the view)"}, 409)
                else:
                    self._send_json({"ok": True})
                return
            if path == "/api/compare-gif":
                # 비교 캔버스 캡처 프레임들(PNG dataURL) → 투명 GIF 조립.
                # 프레임 합성은 클라이언트(큐레이션 상태 합성 경로)가 이미 끝냈다 —
                # 여기선 조립만 한다 (결정론: 가상 시간 샘플).
                payload = self._read_body()
                raw_frames = payload.get("frames")
                try:
                    duration_ms = max(20, int(payload.get("duration_ms", 100)))
                except (TypeError, ValueError):
                    duration_ms = 100
                if not isinstance(raw_frames, list) or not raw_frames or len(raw_frames) > 200:
                    self._send_json({"error": "frames: 1..200 data URLs required"}, 400)
                    return
                import base64
                from sprite_gen.gif_utils import save_clean_gif
                images = []
                try:
                    for entry in raw_frames:
                        head, _, b64 = str(entry).partition(",")
                        if "image/png" not in head:
                            raise ValueError("expected image/png data URLs")
                        images.append(Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA"))
                except (ValueError, OSError) as exc:
                    self._send_json({"error": f"bad frame data: {exc}"}, 400)
                    return
                # 포맷 선택 (수홍 2026-07-19 "webm/mp4 로도"): gif=save_clean_gif,
                # webm=VP9 알파 보존, mp4=x264 (알파 불가 — 화이트 배경 합성).
                fmt = str(payload.get("format", "gif")).lower()
                if fmt not in ("gif", "webm", "mp4"):
                    self._send_json({"error": "format: gif|webm|mp4"}, 400)
                    return
                if fmt != "gif" and shutil.which("ffmpeg") is None:
                    self._send_json({"error": "ffmpeg not found on server machine (webm/mp4 assembly requires it)"}, 500)
                    return
                with tempfile.TemporaryDirectory() as td:
                    out = Path(td) / f"compare.{fmt}"
                    if fmt == "gif":
                        save_clean_gif(images, out, duration_ms=duration_ms)
                    else:
                        even = lambda v: (v + 1) // 2 * 2  # noqa: E731 — 코덱 짝수 치수 요구
                        for index, im in enumerate(images):
                            if fmt == "mp4":
                                frame = Image.new("RGB", (even(im.width), even(im.height)), (255, 255, 255))
                                frame.paste(im, (0, 0), im)
                            elif im.width % 2 or im.height % 2:
                                frame = Image.new("RGBA", (even(im.width), even(im.height)), (0, 0, 0, 0))
                                frame.paste(im, (0, 0))
                            else:
                                frame = im
                            frame.save(Path(td) / f"f{index:04d}.png")
                        fps = max(1, round(1000 / duration_ms))
                        args = ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(Path(td) / "f%04d.png")]
                        if fmt == "webm":
                            args += ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28", "-auto-alt-ref", "0"]
                        else:
                            args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"]
                        args.append(str(out))
                        proc = subprocess.run(args, capture_output=True, text=True)
                        if proc.returncode != 0 or not out.exists():
                            self._send_json({"error": f"ffmpeg failed: {proc.stderr[-300:]}"}, 500)
                            return
                    data = out.read_bytes()
                content_type = {"gif": "image/gif", "webm": "video/webm", "mp4": "video/mp4"}[fmt]
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("X-Filename", f"compare.{fmt}")
                self.send_header("Content-Disposition", f'attachment; filename="compare.{fmt}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/compose":
                result = run_compose(self.run_dir)
                self._send_json(result, 200 if result["ok"] else 500)
                return
            if path == "/api/base-edit":
                # 베이스(base-source) 픽셀 편집 — 프레임과 달리 사이드카가 아니라
                # 파일 자체에 굽는다: 베이스는 생성 identity truth 입력이라, 편집이
                # 이후 생성(앵커 재파생·행 리롤)에 반영되려면 파일이 바뀌어야 한다
                # (수홍 지시 2026-07-17 '베이스를 다르게 해서 뽑고 싶다').
                # 원본은 최초 1회 <base>.orig 로 백업 (관측 가능, 덮어쓰지 않음).
                payload = self._read_body()
                ops = payload.get("ops")
                transform_req = payload.get("transform")
                if not isinstance(ops, dict):
                    ops = {}
                if not ops and not transform_req:
                    self._send_json({"error": 'body needs ops {"x,y": "#rrggbb"|null} and/or transform'}, 400)
                    return
                base_path = _find_base_path(self.run_dir)
                if base_path is None:
                    self._send_json({"error": "no base-source image in this run"}, 404)
                    return
                space = str(payload.get("space") or "raw")
                grid = None
                if space == "logical":
                    # 논리 셀 좌표 (줌 모달 편집 공간) — 검출 격자로 raw 블록에 확장.
                    # 격자 SSoT = _base_grid_response (클라 표시와 동일 절단선).
                    grid = _base_grid_response(self.run_dir, base_path).get("grid")
                    if not grid:
                        self._send_json({"error": "logical ops need a detected base grid"}, 422)
                        return
                elif space != "raw":
                    self._send_json({"error": f"unknown ops space: {space}"}, 400)
                    return
                request = json.loads(
                    (self.run_dir / "sprite-request.json").read_text(encoding="utf-8"))
                chroma = tuple(request.get("chroma_key", {}).get("rgb") or (255, 0, 255))
                backup = base_path.with_name(base_path.name + ".orig")
                if not backup.exists():
                    import shutil as _shutil
                    _shutil.copyfile(base_path, backup)
                with Image.open(base_path) as opened:
                    image = opened.convert("RGBA")
                applied = 0
                for key, value in ops.items():
                    try:
                        x, y = (int(v) for v in str(key).split(","))
                    except ValueError:
                        continue
                    fill: tuple
                    if value:
                        hexv = str(value).lstrip("#")
                        if len(hexv) != 6:
                            continue
                        fill = tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
                    else:
                        fill = chroma + (255,)  # 지우개 = 크로마 배경 복원
                    if grid is not None:
                        xe, ye = grid["xEdges"], grid["yEdges"]
                        if not (0 <= x < len(xe) - 1 and 0 <= y < len(ye) - 1):
                            continue
                        for ry in range(ye[y], ye[y + 1]):
                            for rx in range(xe[x], xe[x + 1]):
                                if 0 <= rx < image.width and 0 <= ry < image.height:
                                    image.putpixel((rx, ry), fill)
                        applied += 1
                    else:
                        if not (0 <= x < image.width and 0 <= y < image.height):
                            continue
                        image.putpixel((x, y), fill)
                        applied += 1
                if transform_req:
                    # 변형 굽기 — 프레임 bake 와 같은 순서(픽셀 편집 → 변형), 같은 수학
                    # (curation.apply_transform SSoT). dx/dy 는 논리 px → 검출 피치로
                    # raw 환산. 변형 결과의 바깥은 크로마 배경으로 재합성 (베이스 계약).
                    from curation import apply_transform as _apply_transform
                    from curation import normalize_transform as _normalize_transform
                    tnorm = dict(_normalize_transform(transform_req))
                    if grid is not None:
                        tnorm["dx"] = tnorm.get("dx", 0.0) * grid["pitch"][0]
                        tnorm["dy"] = tnorm.get("dy", 0.0) * grid["pitch"][1]
                    moved = _apply_transform(image, tnorm, (image.width, image.height))
                    canvas = Image.new("RGBA", (image.width, image.height), chroma + (255,))
                    canvas.alpha_composite(moved)
                    image = canvas
                    applied += 1
                image.save(base_path)
                self._send_json({"ok": True, "applied": applied, "space": space,
                                 "backup": backup.name})
                return
            if path == "/api/state-fps":
                # 줄 전체 재생 속도 = state fps (SSoT = sprite-request.json).
                # 프레임별 지속시간은 프레임 복제가 담당 (수홍 확정 2026-07-18 —
                # per-frame duration 이중 진실 금지). fps 는 재생 메타데이터라
                # 추출/프레임 캐시와 무관하다.
                payload = self._read_body()
                state = str(payload.get("state") or "")
                try:
                    fps = int(payload.get("fps"))
                except (TypeError, ValueError):
                    self._send_json({"error": "fps:int required"}, 400)
                    return
                if not 1 <= fps <= 30:
                    self._send_json({"error": f"fps must be 1..30: {fps}"}, 400)
                    return
                with publish_guard(self.run_dir):
                    request_path = self.run_dir / "sprite-request.json"
                    request = json.loads(request_path.read_text(encoding="utf-8"))
                    if state not in request.get("states", {}):
                        self._send_json({"error": f"unknown state: {state}"}, 400)
                        return
                    request["states"][state]["fps"] = fps
                    from runio import atomic_write_text
                    atomic_write_text(request_path,
                                      json.dumps(request, ensure_ascii=False, indent=2) + "\n")
                self._send_json({"ok": True, "state": state, "fps": fps})
                return
            if path == "/api/interpolate":
                payload = self._read_body()
                state = str(payload.get("state") or "")
                request = json.loads(
                    (self.run_dir / "sprite-request.json").read_text(encoding="utf-8"))
                if state not in request.get("states", {}):
                    self._send_json({"error": f"unknown state: {state}"}, 400)
                    return
                try:
                    index_a = int(payload["from"])
                    index_b = int(payload["to"])
                    t_value = float(payload.get("t", 0.5))
                except (KeyError, TypeError, ValueError):
                    self._send_json(
                        {"error": "body needs integer 'from'/'to' (+ optional float 't')"}, 400)
                    return
                if not 0.0 < t_value < 1.0:
                    self._send_json({"error": f"t must be inside (0, 1): {t_value}"}, 400)
                    return
                provider = str(payload.get("provider") or "codex")
                if provider not in ("codex", "grok"):
                    self._send_json({"error": f"unknown provider: {provider}"}, 400)
                    return
                result = run_interpolate(self.run_dir, state, index_a, index_b,
                                         t_value, payload.get("label") or None, provider)
                self._send_json(result, 200 if result["ok"] else 500)
                return
            if path == "/api/reroll":
                payload = self._read_body()
                state = str(payload.get("state") or "")
                request = json.loads(
                    (self.run_dir / "sprite-request.json").read_text(encoding="utf-8"))
                if state not in request.get("states", {}):
                    self._send_json({"error": f"unknown state: {state}"}, 400)
                    return
                provider = str(payload.get("provider") or "codex")
                if provider not in ("codex", "grok"):
                    self._send_json({"error": f"unknown provider: {provider}"}, 400)
                    return
                result = run_reroll(self.run_dir, state, provider)
                self._send_json(result, 200 if result["ok"] else 500)
                return
            if path == "/api/export":
                result = run_export(self.run_dir)
                self._send_json(result, 200 if result["ok"] else 500)
                return
            if path == "/api/export-gif":
                result = run_export_gif(self.run_dir)
                self._send_json(result, 200 if result["ok"] else 500)
                return
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)
            return
        self._send_json({"error": "not found", "path": path}, 404)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--no-open", action="store_true", help="do not auto-open the browser")
    parser.add_argument("--lang", choices=["en", "ko"], default="en", help="initial UI language (toggleable in the webview)")
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    if not (run_dir / "sprite-request.json").is_file():
        raise SystemExit(f"not a sprite-gen run dir (no sprite-request.json): {run_dir}")
    if not CURATOR_DIR.is_dir():
        raise SystemExit(f"missing curator SPA dir: {CURATOR_DIR}")

    handler = partial(CurationHandler)
    CurationHandler.run_dir = run_dir
    CurationHandler.lang = args.lang
    server = ThreadingHTTPServer((args.host, args.port), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"sprite-gen curation webview: {url}")
    print(f"  run-dir: {run_dir}")
    # 뷰 계약 자가 보고 — base 참조 줄 / 생성 재료 칩 / 픽셀 격자 충족 여부를 한 줄로.
    # 셋 다 없으면 "소스 없는 뷰" 경고 (관측 가능하게 — No Silent Fallback).
    try:
        snapshot = build_run_state(run_dir)
        c = snapshot.get("contract", {})
        n_states = len(snapshot.get("states", []))
        print(f"  view-contract: base={'yes' if c.get('base') else 'no'} · "
              f"refs={c.get('refsStates', 0)}/{n_states} states · "
              f"grid={'yes' if c.get('grid') else 'no'}")
        if c.get("sourceless"):
            print("  WARNING: sourceless view — no base-source, no generation-material refs, no pixel grid. "
                  "이 뷰는 세션마다 경험이 갈라진다 (run-contract.md §3/§4: _base/_refs 동봉 또는 fit.pixel_perfect 권장).")
    except Exception as exc:  # 계약 보고 실패는 서빙을 막지 않는다 — 관측만
        print(f"  view-contract: unavailable ({exc})")
    print("  Ctrl-C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
