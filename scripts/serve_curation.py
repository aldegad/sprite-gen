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


def _axis_pitch(edge_mass, length):
    total = sum(edge_mass)
    if total <= 0 or length < 8:
        return None
    best = None
    for pitch in range(2, min(96, length // 3) + 1):
        phase = [0] * pitch
        for pos, mass in enumerate(edge_mass):
            if mass:
                phase[pos % pitch] += mass
        if max(phase) / total >= 0.8:
            best = pitch
    return best


def detect_pixel_pitch(path):
    """프레임 한 장의 블록 피치(셀px/논리px)를 측정한다. 실패 시 None."""
    if Image is None:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns)
    if key in _PITCH_CACHE:
        return _PITCH_CACHE[key]
    pitch = None
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
            px = im.load()
            w, h = im.size
            col = [0] * max(0, w - 1)
            row = [0] * max(0, h - 1)
            for y in range(h):
                for x in range(w - 1):
                    a, b = px[x, y], px[x + 1, y]
                    if abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) + abs(a[3]-b[3]) > 48:
                        col[x] += 1
            for x in range(w):
                for y in range(h - 1):
                    a, b = px[x, y], px[x, y + 1]
                    if abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) + abs(a[3]-b[3]) > 48:
                        row[y] += 1
        pw, ph = _axis_pitch(col, w), _axis_pitch(row, h)
        if pw and ph:
            if max(pw, ph) % min(pw, ph) == 0 or abs(pw - ph) <= 1:
                pitch = min(pw, ph)
        else:
            pitch = pw or ph
        if pitch is not None and pitch < 2:
            pitch = None
    except OSError:
        pitch = None
    _PITCH_CACHE[key] = pitch
    return pitch


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
        anchor_rel = raw_rel(request, f"{direction}_idle")
        anchor = run_dir / anchor_rel
        if state != f"{direction}_idle" and anchor.is_file():
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
            if index < len(labels):
                frame["label"] = labels[index]
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
    has_grid = pixel_perfect is not None or any(st.get("pixelScale") for st in states)
    contract = {
        "base": has_base,
        "refs": refs_states > 0,
        "refsStates": refs_states,
        "grid": has_grid,
        "sourceless": not (has_base or refs_states > 0 or has_grid),
    }
    return {
        "characterId": request["character"]["id"],
        "runDir": str(run_dir),
        "baseUrl": base_url,
        "cell": cell_state,
        "pixelPerfect": pixel_perfect if pixel_perfect is not None else ({"source": "auto", "label": "auto", "scale": None} if any(st.get("pixelScale") for st in states) else None),
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


def maybe_heal(run_dir: Path) -> dict | None:
    """실시간 계약 (수홍 확정 2026-07-14): 뷰에 '재추출' 개념이 없다.

    frames/ 는 (raw + request + 현재 엔진 + 큐레이션)의 파생 캐시다 — 요청이
    들어올 때마다 행별 engine_revision 을 현재 엔진과 비교해, 다르면 raw 에서
    조용히 다시 굽는다 (heal_run). 신선하면 해시 비교 몇 ms 로 끝난다.
    ThreadingHTTPServer 라 락으로 단일 비행을 보장한다 (동시 재추출 금지).
    실패는 뷰를 죽이지 않고 노트로 관측 가능하게 남긴다 — 이전 세대는
    스테이징 통짜 스왑 덕에 바이트 그대로다.
    """
    with _heal_lock:
        try:
            report = heal_run(run_dir)
        except (Exception, SystemExit) as exc:
            return {"healed": [], "kept_stale": [], "failed": [],
                    "notes": [f"heal skipped: {exc}"]}
    if report["healed"] or report["kept_stale"] or report.get("failed") or report["notes"]:
        return report
    return None


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
                heal = maybe_heal(self.run_dir)  # 실시간 계약: 스냅샷 전에 캐시 자가치유
                snapshot = build_run_state(self.run_dir)
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
                                     "busy": True}, 503)
                else:
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
                maybe_heal(self.run_dir)
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
                maybe_heal(self.run_dir)
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
                        write_curation_atomic(self.run_dir, payload)
                if stale:
                    self._send_json({"error": "curation is from a different run generation "
                                     "(the run changed under this session; reload the view)"}, 409)
                else:
                    self._send_json({"ok": True})
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
                if not isinstance(ops, dict) or not ops:
                    self._send_json({"error": 'body needs non-empty ops {"x,y": "#rrggbb"|null}'}, 400)
                    return
                base_path = None
                for candidate in sorted(self.run_dir.glob("base-source.*")):
                    if candidate.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        base_path = candidate
                        break
                if base_path is None:
                    self._send_json({"error": "no base-source image in this run"}, 404)
                    return
                request = json.loads(
                    (self.run_dir / "sprite-request.json").read_text(encoding="utf-8"))
                chroma = tuple(request.get("chroma_key", {}).get("rgb") or (255, 0, 255))
                from PIL import Image as _Image
                backup = base_path.with_name(base_path.name + ".orig")
                if not backup.exists():
                    import shutil as _shutil
                    _shutil.copyfile(base_path, backup)
                with _Image.open(base_path) as opened:
                    image = opened.convert("RGBA")
                applied = 0
                for key, value in ops.items():
                    try:
                        x, y = (int(v) for v in str(key).split(","))
                    except ValueError:
                        continue
                    if not (0 <= x < image.width and 0 <= y < image.height):
                        continue
                    if value:
                        hexv = str(value).lstrip("#")
                        if len(hexv) != 6:
                            continue
                        rgb = tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
                        image.putpixel((x, y), rgb + (255,))
                    else:
                        image.putpixel((x, y), chroma + (255,))  # 지우개 = 크로마 배경 복원
                    applied += 1
                image.save(base_path)
                self._send_json({"ok": True, "applied": applied, "backup": backup.name})
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
