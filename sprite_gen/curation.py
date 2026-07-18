# SPDX-License-Identifier: Apache-2.0
"""Shared curation sidecar logic for sprite-gen.

`curation.json` is an optional, non-destructive sidecar in a run directory. It
records which extracted frames a human selected (and in what order), which
frames were logically deleted, plus a per-frame affine transform. The original
`frames/<state>/frame-N.png` files are never rewritten — the atlas/GIF compose
steps read this sidecar and apply the transform at compose time, so a curation
decision is always reversible by editing `curation.json`.

This module is the single source of truth for the curation schema and for how a
transform is applied, so the webview server and the compose scripts can never
drift apart.

Schema (`curation.json`):

    {
      "version": 1,
      "kind": "sprite-gen-curation",
      "run_revision": "9f3c1a0b7e2d4c58",    # stamped at write = the frame generation this
                                              #   curation was made for. When it matches the
                                              #   current run, the whole sidecar applies
                                              #   (fast path). When it does not, each state
                                              #   is judged by its own `revision` stamp
                                              #   below — never silently applied wholesale.
      "pixel_perfect": true,                 # optional run-wide DEFAULT; false -> compose
                                              #   reads the frame-N.plain.png variant (pre-
                                              #   pixel-perfect). absent/true -> the
                                              #   canonical frame-N.png. Only
                                              #   meaningful when extraction saved
                                              #   both variants (fit.pixel_perfect).
      "states": {
        "<state>": {
          "revision": ["a1b2c3d4e5f6"],      # per-state generation stamp: ordered SOURCE-
                                              #   material segment digests (state_revision).
                                              #   Valid while it is a prefix of the current
                                              #   segments — an engine-upgrade heal of the
                                              #   same raw keeps it, a raw re-roll drops it.
          "pixel_perfect": false,            # optional per-state override of the run-wide
                                              #   default above (the curator's per-row
                                              #   toggle). absent -> the run-wide value.
          "selected": [0, 1, 2, 3],          # 0-based frame indices, in play order
                                              #   (may include clone instance indices)
          "deleted": [4],                    # optional 0-based frame indices
                                               #   excluded from UI rows and bake.
          "clones": {"12": 5},               # optional duplicate instances: new index ->
                                               #   source frame index. LINKED by default
                                               #   (Soohong 2026-07-18): a linked clone
                                               #   shares the SOURCE frame's transform and
                                               #   pixel edits (one edit truth — clones are
                                               #   play slots of the same frame). Clone
                                               #   indices live outside 0..frame_count-1.
          "unlinked": [12],                    # optional clone indices explicitly detached
                                               #   ("링크 끊기"): they own their transforms/
                                               #   pixels independently (pre-2026-07-18
                                               #   clones with own edits are treated as
                                               #   unlinked by the webview on load).
          "order": [0, 1, 2, 3, 4, 5],        # optional, webview-owned; full display
                                               #   order (sequence then candidate pool).
                                               #   Restores the row arrangement on reload.
                                               #   Consumers key off `selected`; ignored here.
          "breathe": {                         # optional idle-breathing POST-PROCESS LAYER
            "splits": [0.55],                  #   (Soohong 2026-07-18: breathing is a
            "amplitude": 1,                    #   modulation ORTHOGONAL to frame selection —
            "breaths": 1,                      #   a blink frame can breathe too). 1-3 split
            "subpixel": false                  #   lines (content-height fractions, asc);
          },                                   #   K lines = K-phase cascade. amplitude px,
                                               #   breaths = breath count PER LOOP — the loop
                                               #   length never changes (fit_breathe_pattern
                                               #   divides the played sequence exactly).
                                               #   subpixel = palette-snapped intermediate
                                               #   colors on moving seams only. Compose/GIF
                                               #   bake deterministically; frames on disk
                                               #   never change, no re-extraction.
          "transforms": {                      # keyed by 0-based frame index (string)
            "0": {"rotate": 0.0, "scale": 1.0, "dx": 0, "dy": 0}
          },
          "pixels": {                          # optional per-frame pixel edits (sidecar,
            "0": {"12,34": "#1f2430",          #   originals never rewritten): cell-coord
                  "13,34": null}                #   "x,y" -> paint hex | null = erase.
          }                                     #   Applied before the transform at bake.
        }
      }
    }

Defaults when absent (explicit, not a silent fallback):
- no `curation.json`           -> every state uses all extracted frames in order, identity transform.
- mismatched `run_revision`    -> per-state salvage: each state entry whose `revision` stamp is a
                                   prefix of the current state_revision segments is KEPT; entries
                                   without a valid stamp are dropped. Before anything is dropped the
                                   whole original file is backed up to `curation.stale-<hash>.json`
                                   (idempotent content-hash name) and the drop is reported on stderr
                                   and to the webview (load_curation_report) — stale edits are never
                                   silently applied, and never silently destroyed either.
- state missing from sidecar   -> same all-frames default for that state.
- `selected` missing/empty     -> all non-deleted frames in extraction order.
- `deleted` missing             -> no frames are deleted.
- `order` missing               -> webview rebuilds arrangement from `selected`; bake is unaffected (state_plan reads `selected`, never `order`).
- frame missing from transforms -> identity transform.

`rotate` is in degrees, counter-clockwise positive (PIL convention).
`scale` is a multiplier about the frame center.
`dx`/`dy` are pixel offsets inside the cell, +x right, +y down.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.layout import raw_rel

CURATION_FILENAME = "curation.json"
SCHEMA_VERSION = 1
IDENTITY = {"rotate": 0.0, "scale": 1.0, "dx": 0, "dy": 0, "shx": 0.0, "shy": 0.0, "flipX": 0}

# Generation-material chip roles for imported `_refs/<role>-<name>.png` files
# (run-contract.md §4). Single source shared by the importer (fail-loud on an
# unknown role) and the webview server (render), so neither silently invents a role.
IMPORTED_REF_ROLES = ("anchor", "basis", "guide")


def imported_ref_role(filename: str) -> str | None:
    """Role for an imported `_refs` filename `<role>-<name>.png`, or None if the
    prefix is not a known role. Callers must handle None explicitly (the importer
    fails loud, the server skips) — never silently relabel an unknown role."""
    stem = Path(filename).stem
    prefix = stem.split("-", 1)[0] if "-" in stem else ""
    return prefix if prefix in IMPORTED_REF_ROLES else None


def curation_path(run_dir: Path) -> Path:
    return run_dir / CURATION_FILENAME


def state_breathe(curation: dict[str, Any] | None, state: str) -> dict[str, Any] | None:
    """상태의 호흡 후처리 레이어 설정 (없으면 None) — 정규화·클램프 포함.

    호흡은 프레임 선택(깜빡임 등)과 직교하는 변조 레이어다 (수홍 확정 2026-07-18).
    반환: {"splits": [asc floats], "amplitude": int, "hold": int, "subpixel": bool}
    compose/GIF 가 재생 시퀀스 위에 결정론으로 굽는다 — 디스크 프레임 불변."""
    if not curation:
        return None
    entry = curation.get("states", {}).get(state)
    raw = entry.get("breathe") if isinstance(entry, dict) else None
    if not isinstance(raw, dict):
        return None
    try:
        splits = sorted({round(float(s), 4) for s in (raw.get("splits") or [])})
    except (TypeError, ValueError):
        return None
    splits = [s for s in splits if 0.02 < s < 0.98][:3]
    if not splits:
        return None
    try:
        amplitude = max(1, min(4, int(raw.get("amplitude", 1))))
        breaths = max(1, min(8, int(raw.get("breaths", 1))))
    except (TypeError, ValueError):
        return None
    return {"splits": splits, "amplitude": amplitude, "breaths": breaths,
            "subpixel": bool(raw.get("subpixel"))}


def edit_index(curation: dict[str, Any] | None, state: str, index: int) -> int:
    """인스턴스의 편집(변형/픽셀) truth 인덱스 — 링크된 복제는 원본을 가리킨다.

    복제는 기본 링크 (수홍 확정 2026-07-18): 같은 프레임의 재생 슬롯이므로 편집
    SSoT 는 원본 하나다. `unlinked` 에 명시된 복제만 자기 편집을 소유한다.
    베이크(compose/GIF/export)와 웹뷰가 같은 규칙을 쓴다 (드리프트 금지)."""
    if not curation:
        return index
    entry = curation.get("states", {}).get(state)
    if not isinstance(entry, dict):
        return index
    clones = entry.get("clones")
    if not isinstance(clones, dict):
        return index
    src = clones.get(str(index), clones.get(index))
    if src is None:
        return index
    unlinked = entry.get("unlinked")
    if isinstance(unlinked, list) and index in {int(u) for u in unlinked if str(u).lstrip("-").isdigit()}:
        return index
    # 레거시 자가판정 (웹뷰와 동일 규칙): 링크 개념(2026-07-18) 이전 사이드카가 복제
    # 인덱스에 직접 넣어둔 변형/픽셀 편집은 독립 의도다 — 조용한 편집 소실 금지.
    own_transform = (entry.get("transforms") or {}).get(str(index))
    own_pixels = (entry.get("pixels") or {}).get(str(index))
    if own_transform or (own_pixels and len(own_pixels)):
        return index
    try:
        return int(src)
    except (TypeError, ValueError):
        return index


def frame_variant(curation: dict[str, Any] | None, state: str | None = None) -> str:
    """Which extracted frame variant consumers read: 'pixel' or 'plain'.

    Resolution order (single source for every consumer):
    1. the state's own `states.<state>.pixel_perfect` (the curator's per-row toggle),
    2. the run-wide `pixel_perfect` default (the curator's toggle-all),
    3. absent sidecar / absent fields -> the canonical pixel-perfected frames.

    Called without `state` it resolves the run-wide default only (legacy callers,
    single-state tools that pass their state explicitly elsewhere)."""
    if not curation:
        return "pixel"
    if state is not None:
        entry = curation.get("states", {}).get(state)
        if isinstance(entry, dict) and isinstance(entry.get("pixel_perfect"), bool):
            return "pixel" if entry["pixel_perfect"] else "plain"
    if curation.get("pixel_perfect") is False:
        return "plain"
    return "pixel"


def frame_filename(index: int, variant: str = "pixel") -> str:
    """Frame file name for a variant. 'pixel' = canonical frame-N.png; 'plain'
    = the pre-pixel-perfect twin saved by extraction when fit.pixel_perfect."""
    if variant == "plain":
        return f"frame-{index}.plain.png"
    return f"frame-{index}.png"


def pixel_snap_scale(request: dict[str, Any]) -> int | None:
    """Logical-grid scale (cell px per logical px) for a `fit.pixel_perfect` run, or None
    for a legacy run. Mirrors extract's pp_scale so a curation transform baked onto the
    canonical pixel frames re-snaps to the SAME grid the extraction snapped to. Single
    source for compose/GIF/PNG-export/cycle and (mirrored) the webview preview."""
    fit = request.get("fit") or {}
    if not fit.get("pixel_perfect"):
        return None
    cell = request.get("cell", {})
    cell_height = int(cell.get("height", cell.get("size", 0)))
    margin_y = int(cell.get("safe_margin_y", cell.get("safe_margin", 0)))
    usable_height = max(1, cell_height - margin_y * 2)
    logical_height = int(fit.get("logical_height", cell_height))
    scale = max(1, cell_height // max(1, logical_height))
    if logical_height * scale > cell_height:
        scale = max(1, usable_height // max(1, logical_height))
    return scale


def run_revision(run_dir: Path) -> str:
    """Frame-content fingerprint of the run's current generation: the request + frames
    manifest bytes plus each canonical frame file's name/size/mtime. It changes whenever
    the frames are (re)written (`--force` re-import, re-extract), so a curation sidecar
    stamped for a prior generation is detected as stale. Single source of run identity for
    the server, compose, export, and the webview."""
    h = hashlib.sha256()
    for name in ("sprite-request.json", "frames/frames-manifest.json"):
        try:
            h.update((run_dir / name).read_bytes())
        except OSError:
            h.update(b"\0")
    frames_root = run_dir / "frames"
    if frames_root.is_dir():
        # 재귀 걷기 — 택소노미(frames/<dir>/<pose>/)와 flat 레거시 둘 다 커버.
        # orig/ 표시 쌍둥이는 세대 정체성에 불포함 (레거시 스탬프와 동일 규칙).
        for frame in sorted(frames_root.rglob("frame-*.png")):
            if frame.name.endswith(".plain.png") or frame.parent.name == "orig":
                continue
            try:
                st = frame.stat()
                rel = frame.relative_to(frames_root).as_posix()
                h.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                pass
    return h.hexdigest()[:16]


# raw/프레임 파일 내용 다이제스트 캐시 — (path, size, mtime_ns) 가 같으면 재해시하지
# 않는다. state_revision 이 서버 요청마다 불리므로 MB 급 raw 재해시를 피한다.
_CONTENT_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}


def _file_content_digest(path: Path) -> str | None:
    """파일 내용 sha256 12자리 (mtime/size 키 메모이즈). 없으면 None."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, st.st_mtime_ns)
    cached = _CONTENT_DIGEST_CACHE.get(key)
    if cached is None:
        try:
            cached = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            return None
        _CONTENT_DIGEST_CACHE[key] = cached
    return cached


def state_revision(run_dir: Path, state: str, request: dict[str, Any] | None = None,
                   row: dict[str, Any] | None = None) -> list[str] | None:
    """행(state) 단위 세대 지문 — 순서 있는 원료(source-material) 세그먼트 다이제스트 리스트.

    세그먼트 = 그 행의 프레임 인덱스 공간을 만드는 원료 단위: primary raw, 그리고 선언
    순서의 take raw (manifest row `takes` 가 SSoT). raw 가 아예 없는 임포트 행은 프레임
    파일 내용 자체가 원료다. 다이제스트 입력은 원료의 **내용**(sha256)·세그먼트 프레임
    수·셀/픽셀퍼펙트 기하이고, frames/ 캐시의 mtime 이나 엔진 리비전은 넣지 않는다 —
    엔진 업그레이드 heal 이 같은 raw 를 재유도해도 지문이 유지돼 큐레이션이 살아남고,
    raw 리롤·테이크 교체·셀 변경은 지문을 바꾼다.

    유효성 규칙 (load_curation_report): 저장 리스트가 현재 리스트의 접두(prefix)면 유효.
    테이크가 끝에 추가돼도 기존 프레임 인덱스 공간이 밀리지 않으므로 선택이 유지된다.
    manifest row 가 없으면 None (검증 불가 — 그 행 큐레이션은 살릴 수 없다)."""
    try:
        if request is None:
            request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if state not in (request.get("states") or {}):
        return None
    if row is None:
        try:
            manifest = json.loads(
                (run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        row = next((r for r in manifest.get("rows", []) if r.get("state") == state), None)
    if not isinstance(row, dict):
        return None
    cell = request.get("cell", {})
    fit = request.get("fit") or {}
    basis = (f"{cell.get('width', cell.get('size'))}x{cell.get('height', cell.get('size'))}"
             f":pp={1 if fit.get('pixel_perfect') else 0}:lh={fit.get('logical_height')}")
    segments = row.get("takes")
    if not segments:
        segments = [{
            "label": None, "start": 0,
            "frames": row.get("frames", len(row.get("files") or [])),
            "raw": raw_rel(request, state),
        }]
    digests: list[str] = []
    for seg in segments:
        raw_path = run_dir / str(seg.get("raw", ""))
        content = _file_content_digest(raw_path) if seg.get("raw") else None
        if content is None:
            # 임포트 행 (raw 없음): 세그먼트가 낳은 프레임 파일 내용이 곧 원료.
            start = int(seg.get("start", 0))
            count = int(seg.get("frames", 0))
            parts = []
            for rel in (row.get("files") or [])[start:start + count]:
                parts.append(_file_content_digest(run_dir / rel) or "missing")
            content = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
        h = hashlib.sha256(
            f"{seg.get('label') or ''}:{seg.get('raw') or ''}:{content}"
            f":{int(seg.get('frames', 0))}:{basis}".encode())
        digests.append(h.hexdigest()[:12])
    return digests


def backup_stale_curation(run_dir: Path, raw_text: str) -> str:
    """무효화로 버려질(또는 덮일) 큐레이션 원문을 `curation.stale-<hash>.json` 으로 보존.

    파일명이 내용 해시라 멱등 — 같은 원문은 한 번만 남고, 정상 편집 흐름에서는 절대
    생기지 않는다. 사람이 나중에 열어 selected/transforms 를 수동 복원할 수 있는
    관측 가능한 안전망이다 (백업 없는 원자 덮어쓰기 금지)."""
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:8]
    name = f"curation.stale-{digest}.json"
    path = run_dir / name
    if not path.exists():
        path.write_text(raw_text, encoding="utf-8")
    return name


def load_curation_report(run_dir: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """사이드카 로드 + 세대 검증 보고. Returns (doc|None, report).

    report = {"dropped": [state...], "backup": filename|None}. 규칙:
    - run_revision 이 현재와 일치 → 문서 전체 유효 (fast path, dropped 없음).
    - 불일치 → 행 단위 구제: `revision` 스탬프가 현재 state_revision 의 접두인 행만
      유지, 나머지는 드롭. 드롭이 하나라도 있으면 원문을 먼저 백업하고 stderr 로
      보고한다. 전 행이 드롭되면 doc 은 None (전량 기본값).
    스탬프 없는 행(레거시/수동 편집)은 불일치 세대에서 검증 불가 → 드롭 (No Silent
    Fallback — 증명 없는 선택을 새 프레임에 적용하지 않는다)."""
    path = curation_path(run_dir)
    report: dict[str, Any] = {"dropped": [], "backup": None}
    if not path.is_file():
        return None, report
    raw_text = path.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    if data.get("kind") != "sprite-gen-curation":
        raise SystemExit(f"{path} is not a sprite-gen-curation file")
    if data.get("run_revision") == run_revision(run_dir):
        return data, report
    try:
        request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        request = None
    kept: dict[str, Any] = {}
    for name, entry in (data.get("states") or {}).items():
        entry_rev = entry.get("revision") if isinstance(entry, dict) else None
        current = state_revision(run_dir, name, request=request) if request else None
        if (isinstance(entry_rev, list) and entry_rev and current
                and entry_rev == current[:len(entry_rev)]):
            kept[name] = entry
        else:
            report["dropped"].append(name)
    if not report["dropped"]:
        # 런 세대 지문은 바뀌었지만 (예: request 메타 편집) 전 행이 개별 검증을 통과.
        return {**data, "states": kept}, report
    report["backup"] = backup_stale_curation(run_dir, raw_text)
    print(f"[curation] frames regenerated under {CURATION_FILENAME}: dropped "
          f"{', '.join(report['dropped'])} (kept {len(kept)}), backup {report['backup']}: {run_dir}",
          file=sys.stderr)
    if not kept:
        return None, report
    return {**data, "states": kept}, report


def load_curation(run_dir: Path) -> dict[str, Any] | None:
    """The single gate every consumer (server, compose, export, GIF) passes through.
    Thin wrapper over load_curation_report — see it for the per-state salvage +
    backup semantics."""
    return load_curation_report(run_dir)[0]


def stamp_curation(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """쓰기 직전 세대 도장. run_revision(런 전체 fast-path 지문) + 행별 `revision`
    (state_revision 세그먼트 지문) 을 payload 사본에 찍어 반환한다. `runRevision`
    (transport echo) 은 제거. 행 스탬프가 계산 불가(행 미생성)면 스탬프를 지운다 —
    거짓 증명을 남기지 않는다."""
    payload = {k: v for k, v in payload.items() if k != "runRevision"}
    payload["run_revision"] = run_revision(run_dir)
    try:
        request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        request = None
    states = payload.get("states")
    if isinstance(states, dict):
        for name, entry in states.items():
            if not isinstance(entry, dict):
                continue
            rev = state_revision(run_dir, name, request=request) if request else None
            if rev:
                entry["revision"] = rev
            else:
                entry.pop("revision", None)
    return payload


def normalize_transform(raw: Any) -> dict[str, float]:
    """Coerce a stored transform into a full {rotate, scale, dx, dy, shx, shy, flipX} dict."""
    if not isinstance(raw, dict):
        return dict(IDENTITY)
    return {
        "rotate": float(raw.get("rotate", 0.0)),
        "scale": float(raw.get("scale", 1.0)),
        "dx": float(raw.get("dx", 0)),
        "dy": float(raw.get("dy", 0)),
        "shx": float(raw.get("shx", 0.0)),
        "shy": float(raw.get("shy", 0.0)),
        # (Alex 2026-05-28) flipX: 0 | 1 — horizontal mirror. Image-gen 결과가 좌우
        # 반대로 나올 때 frame 별로 거울 반전. matrix 마지막에 diag(-1, 1) 곱.
        "flipX": 1 if raw.get("flipX") else 0,
    }


def is_identity(transform: dict[str, float]) -> bool:
    return (
        abs(transform["rotate"]) < 1e-6
        and abs(transform["scale"] - 1.0) < 1e-6
        and abs(transform["dx"]) < 1e-6
        and abs(transform["dy"]) < 1e-6
        and abs(transform.get("shx", 0.0)) < 1e-6
        and abs(transform.get("shy", 0.0)) < 1e-6
        and not transform.get("flipX", 0)
    )


def normalize_frame_indices(raw: Any, default_count: int,
                            extra_valid: set[int] | None = None) -> list[int]:
    """Return unique 0-based frame indices that are valid for this state.
    `extra_valid` admits clone instance indices (outside the physical range)."""
    if not isinstance(raw, list):
        return []
    indices: list[int] = []
    seen: set[int] = set()
    extra = extra_valid or set()
    for value in raw:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if (0 <= index < default_count or index in extra) and index not in seen:
            indices.append(index)
            seen.add(index)
    return indices


def state_clones(curation: dict[str, Any] | None, state: str, default_count: int) -> dict[int, int]:
    """행의 복제 인스턴스 맵 {복제 인덱스: 원본 프레임 인덱스}.

    복제 인덱스는 물리 프레임 범위(0..default_count-1) 밖의 정수, 원본은 범위 안이어야
    한다. 복제는 자기만의 order 슬롯/변형/픽셀편집을 갖는 정식 인스턴스이고, 굽기 때
    원본 프레임 파일을 읽는다 (frames/ 는 파생 캐시라 복제 파일을 만들지 않는다 —
    복제 의도는 사이드카가 소유). 손상 항목은 스킵."""
    entry = ((curation or {}).get("states") or {}).get(state)
    raw = entry.get("clones") if isinstance(entry, dict) else None
    clones: dict[int, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                clone_idx, src = int(key), int(value)
            except (TypeError, ValueError):
                continue
            if clone_idx >= default_count and 0 <= src < default_count:
                clones[clone_idx] = src
    return clones


def source_frame_index(curation: dict[str, Any] | None, state: str,
                       index: int, default_count: int) -> int:
    """인스턴스 인덱스 → 실제 프레임 파일 인덱스 (복제면 원본, 아니면 자기 자신).
    소비자는 파일을 열 때만 이 리졸버를 쓰고, transforms/pixels 는 인스턴스
    인덱스로 그대로 조회한다 — 복제마다 다른 변형이 가능해야 하므로."""
    return state_clones(curation, state, default_count).get(index, index)


def transform_matrix(t: dict[str, float]) -> tuple[float, float, float, float]:
    """Forward 2x2 linear matrix (M00, M01, M10, M11) = Rotate · Shear · Scale · FlipX.

    Screen y-down. Positive `rotate` is counter-clockwise. This exact matrix is
    mirrored in the webview (CSS `matrix()` + canvas), so what the user aligns to
    the ground grid is what bakes — no preview/bake drift. flipX (when set)
    multiplies the right-most diag(-1, 1) so column-0 의 부호가 반전된다.
    """
    rr = math.radians(t["rotate"])
    c, sn = math.cos(rr), math.sin(rr)
    s, shx, shy = t["scale"], t.get("shx", 0.0), t.get("shy", 0.0)
    m00 = s * (c + sn * shy)
    m01 = s * (c * shx + sn)
    m10 = s * (-sn + c * shy)
    m11 = s * (c - sn * shx)
    if t.get("flipX"):
        m00, m10 = -m00, -m10
    return m00, m01, m10, m11


def state_pixel_ops(curation: dict[str, Any] | None, state: str) -> dict[int, dict[str, Any]]:
    """프레임별 픽셀 편집 ops — {frame_index: {"x,y": "#rrggbb"|None}}. 손상 항목은 스킵."""
    entry = ((curation or {}).get("states") or {}).get(state)
    raw = entry.get("pixels") if isinstance(entry, dict) else None
    ops: dict[int, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value:
                ops[index] = value
    return ops


def apply_pixel_edits(frame: Image.Image, ops: dict[str, Any] | None) -> Image.Image:
    """사이드카 픽셀 편집을 프레임 사본에 합성 (원본 불변). 좌표는 셀 픽셀 공간,
    변형(apply_transform) 이전에 적용한다 — 웹뷰 오버레이와 같은 순서."""
    if not ops:
        return frame
    edited = frame.convert("RGBA").copy()
    px = edited.load()
    for key, value in ops.items():
        try:
            x_str, y_str = str(key).split(",", 1)
            x, y = int(x_str), int(y_str)
        except (TypeError, ValueError):
            continue
        if not (0 <= x < edited.width and 0 <= y < edited.height):
            continue
        if value is None:
            px[x, y] = (0, 0, 0, 0)
        elif isinstance(value, str) and value.startswith("#") and len(value) in (7, 9):
            r, g, b = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
            a = int(value[7:9], 16) if len(value) == 9 else 255
            px[x, y] = (r, g, b, a)
    return edited


def state_plan(
    curation: dict[str, Any] | None,
    state: str,
    default_count: int,
) -> tuple[list[int], dict[int, dict[str, float]]]:
    """Resolve the ordered frame indices and per-frame transforms for a state.

    Returns (ordered_zero_based_indices, {frame_index: transform}).
    """
    default_order = list(range(default_count))
    if not curation:
        return default_order, {}
    entry = curation.get("states", {}).get(state)
    if not isinstance(entry, dict):
        return default_order, {}

    # 복제 인스턴스 인덱스도 selected/deleted 의 유효 인덱스다 (파일은 원본을 읽음
    # — source_frame_index). 기본값(선택 없음)은 물리 프레임만: 복제는 명시 선택으로만 굽는다.
    clone_ids = set(state_clones(curation, state, default_count))
    deleted = set(normalize_frame_indices(entry.get("deleted"), default_count, clone_ids))
    default_visible = [index for index in default_order if index not in deleted]
    selected = entry.get("selected")
    if isinstance(selected, list) and selected:
        # tolerate a hand-edited / corrupt sidecar: skip non-integer,
        # out-of-range, duplicate, or deleted entries instead of crashing.
        ordered = [
            index
            for index in normalize_frame_indices(selected, default_count, clone_ids)
            if index not in deleted
        ]
        if not ordered:
            ordered = default_visible
    else:
        ordered = default_visible

    transforms_raw = entry.get("transforms", {})
    transforms: dict[int, dict[str, float]] = {}
    if isinstance(transforms_raw, dict):
        for key, value in transforms_raw.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            transform = normalize_transform(value)
            if not is_identity(transform):
                transforms[index] = transform
    return ordered, transforms


def apply_transform(
    frame: Image.Image,
    transform: dict[str, float] | None,
    cell_size: tuple[int, int],
    snap_scale: int | None = None,
) -> Image.Image:
    """Apply scale/shear/rotate (about center) + translate, into a fresh cell.

    Rendered with one inverse-affine `Image.transform` into the cell, so cell
    size is preserved and the atlas layout never changes. Non-destructive: the
    source frame is not modified. The forward matrix matches `transform_matrix`,
    which the webview uses for its preview, so alignment to the ground grid is
    faithful to the bake.

    `snap_scale` (a `fit.pixel_perfect` run baking the canonical pixel variant,
    from `pixel_snap_scale`): the transform is sampled NEAREST and the result is
    re-quantized to the fixed logical grid (cell-anchored, `snap_scale` px per
    logical px), so a curated move/scale/rotate cannot smear the pixel grid —
    the sprite lands back on the same grid the extraction snapped to. The webview
    mirrors this quantization live while editing (curator src/display.js drawFrameInto).
    """
    transform = normalize_transform(transform) if transform else dict(IDENTITY)
    if is_identity(transform) and frame.size == cell_size:
        return frame.convert("RGBA")

    src = frame.convert("RGBA")
    cw, ch = cell_size
    m00, m01, m10, m11 = transform_matrix(transform)
    det = m00 * m11 - m01 * m10
    if abs(det) < 1e-6:
        det = 1e-6 if det >= 0 else -1e-6
    # inverse 2x2 (output -> input)
    ia, ib = m11 / det, -m01 / det
    id_, ie = -m10 / det, m00 / det
    cin_x, cin_y = src.width / 2, src.height / 2
    cout_x, cout_y = cw / 2 + transform["dx"], ch / 2 + transform["dy"]
    c = -(ia * cout_x + ib * cout_y) + cin_x
    f = -(id_ * cout_x + ie * cout_y) + cin_y
    if not snap_scale:
        return src.transform((cw, ch), Image.AFFINE, (ia, ib, c, id_, ie, f), resample=Image.BICUBIC)
    out = src.transform((cw, ch), Image.AFFINE, (ia, ib, c, id_, ie, f), resample=Image.NEAREST)
    if snap_scale > 1:
        logical_w, logical_h = max(1, cw // snap_scale), max(1, ch // snap_scale)
        out = out.resize((logical_w, logical_h), Image.Resampling.NEAREST)
        out = out.resize((logical_w * snap_scale, logical_h * snap_scale), Image.Resampling.NEAREST)
        if out.size != (cw, ch):  # cell not divisible by scale: pad back to exact cell
            padded = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            padded.alpha_composite(out, (0, 0))
            out = padded
    return out


def empty_curation() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "kind": "sprite-gen-curation", "states": {}}
