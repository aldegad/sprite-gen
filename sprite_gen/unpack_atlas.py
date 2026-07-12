# SPDX-License-Identifier: Apache-2.0
"""Unpack a composed sprite sheet back into a curator-ready run directory.

This is the inverse of `compose_sprite_atlas.py`. When only the combined
`sprite-sheet-alpha.png` (+ optional manifest) survives — for example a deployed
asset whose original `frames/` source is gone — this rebuilds the per-frame
editable representation so the curation webview can open it.

Layout source priority (explicit wins, auto-detect is the no-instruction
default — and the chosen path is always reported, never silent):

  1. --grid <cols>x<rows>     a human said the grid; slice uniform cells (position-faithful).
  2. --manifest <json>        read exact frame rectangles from the manifest.
  3. auto-detect (default)    read the atlas alpha and cut on transparent gutters.

Output (a normal sprite-gen run dir):

  <out-dir>/
    sprite-request.json        synthesized recipe (fps/loop are defaults unless a manifest had them)
    frames/<state>/frame-N.png
    frames/frames-manifest.json
    unpack-source.json         provenance + original manifest format, for a future writeback

Then: serve_curation.py --run-dir <out-dir>
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.curation import imported_ref_role
from sprite_gen.runio import LOCK_FILENAME, acquire_run_dir_lock, atomic_save_image, atomic_write_text, relative_posix

ALPHA_THRESHOLD = 16  # a pixel counts as content above this alpha
MIN_GUTTER = 1        # a fully-empty line of >= this many px separates frames


# --- auto-detect (visual blob clustering) -----------------------------------

def _components(mask: list[bool], w: int, h: int, min_area: int) -> list[tuple[int, int, int, int]]:
    """4-neighbour connected components over a boolean mask -> list of bboxes."""
    visited = bytearray(len(mask))
    boxes: list[tuple[int, int, int, int]] = []
    for seed in range(len(mask)):
        if not mask[seed] or visited[seed]:
            continue
        stack = [seed]
        visited[seed] = 1
        minx = miny = 1 << 30
        maxx = maxy = -1
        area = 0
        while stack:
            cur = stack.pop()
            area += 1
            x, y = cur % w, cur // w
            minx, miny, maxx, maxy = min(minx, x), min(miny, y), max(maxx, x), max(maxy, y)
            if x > 0 and mask[cur - 1] and not visited[cur - 1]:
                visited[cur - 1] = 1; stack.append(cur - 1)
            if x < w - 1 and mask[cur + 1] and not visited[cur + 1]:
                visited[cur + 1] = 1; stack.append(cur + 1)
            if y > 0 and mask[cur - w] and not visited[cur - w]:
                visited[cur - w] = 1; stack.append(cur - w)
            if y < h - 1 and mask[cur + w] and not visited[cur + w]:
                visited[cur + w] = 1; stack.append(cur + w)
        if area >= min_area:
            boxes.append((minx, miny, maxx + 1, maxy + 1))
    return boxes


def auto_detect(atlas: Image.Image) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    """Read the sheet visually: find content blobs, cluster them into a grid.

    Connected components survive a character's internal transparency (a single
    pose stays one blob), which is why this is far more robust on packed grids
    than cutting on transparent gutters. Blobs are clustered into rows by their
    vertical center, then into frames within a row by horizontal overlap.

    Returns (states, cell_size). Each frame rect is the blob's content bbox;
    write_run centers it in the cell.
    """
    scale = max(1, max(atlas.width, atlas.height) // 600)  # downsample for speed
    sw, sh = atlas.width // scale, atlas.height // scale
    small = atlas.getchannel("A").resize((sw, sh), Image.BILINEAR)
    mask = [b > ALPHA_THRESHOLD for b in small.tobytes()]  # 'L' mode: 1 byte/px
    boxes_small = _components(mask, sw, sh, min_area=max(6, (sw * sh) // 4000))
    if not boxes_small:
        raise SystemExit("auto-detect found no content blobs in the atlas")

    # map blob bboxes back to full resolution
    boxes = [(x0 * scale, y0 * scale, x1 * scale, y1 * scale) for (x0, y0, x1, y1) in boxes_small]
    heights = sorted(y1 - y0 for _x0, y0, _x1, y1 in boxes)
    widths = sorted(x1 - x0 for x0, _y0, x1, _y1 in boxes)
    med_h = heights[len(heights) // 2]
    med_w = widths[len(widths) // 2]

    # cluster into rows by vertical center
    boxes.sort(key=lambda b: (b[1] + b[3]) / 2)
    rows: list[list[tuple[int, int, int, int]]] = []
    row_tol = med_h * 0.6
    for box in boxes:
        cy = (box[1] + box[3]) / 2
        if rows and abs(cy - sum((b[1] + b[3]) / 2 for b in rows[-1]) / len(rows[-1])) <= row_tol:
            rows[-1].append(box)
        else:
            rows.append([box])

    states: list[dict[str, Any]] = []
    max_w = max_h = 0
    for row_index, row_boxes in enumerate(rows):
        row_boxes.sort(key=lambda b: b[0])
        # merge horizontally overlapping / near blobs into one frame
        frames: list[list[int]] = []
        gap = med_w * 0.3
        for box in row_boxes:
            if frames and box[0] - frames[-1][2] <= gap:
                f = frames[-1]
                f[0], f[1] = min(f[0], box[0]), min(f[1], box[1])
                f[2], f[3] = max(f[2], box[2]), max(f[3], box[3])
            else:
                frames.append(list(box))
        rects = []
        for f in frames:
            rect = (f[0], f[1], f[2] - f[0], f[3] - f[1])
            rects.append(rect)
            max_w, max_h = max(max_w, rect[2]), max(max_h, rect[3])
        if rects:
            states.append({"name": f"row-{row_index}", "rects": rects})

    if max_w == 0 or max_h == 0:
        raise SystemExit("auto-detect could not size any frame")
    cell = (max_w + 8, max_h + 8)  # pad so centered content is not flush to the edge
    return states, cell


# --- explicit layout sources ------------------------------------------------

def grid_layout(atlas: Image.Image, cols: int, rows: int) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    cell_w = atlas.width // cols
    cell_h = atlas.height // rows
    states = []
    for r in range(rows):
        rects = []
        for c in range(cols):
            rect = (c * cell_w, r * cell_h, cell_w, cell_h)
            crop = atlas.crop((rect[0], rect[1], rect[0] + cell_w, rect[1] + cell_h))
            if crop.getchannel("A").getbbox() is None:
                continue  # skip empty trailing cells
            rects.append(rect)
        if rects:
            states.append({"name": f"row-{r}", "rects": rects})
    return states, (cell_w, cell_h)


def manifest_layout(
    manifest: dict[str, Any],
    direction: str | None,
) -> tuple[list[dict[str, Any]], tuple[int, int], str, dict[str, Any]]:
    """Resolve frame rectangles from a known manifest format.

    Returns (states, cell, atlas_filename, per_state_meta).
    """
    cell = manifest.get("cell", {})
    cell_w = int(cell.get("width", cell.get("size", 0)))
    cell_h = int(cell.get("height", cell.get("size", 0)))

    # compose-format: explicit frame_layout rectangles
    if "frame_layout" in manifest and manifest["frame_layout"].get("rows"):
        fl = manifest["frame_layout"]
        cell_w = cell_w or fl.get("cellWidth", 0)
        cell_h = cell_h or fl.get("cellHeight", 0)
        states = []
        meta = {}
        anim = manifest.get("animation", {}).get("rows", {})
        for state, rects in fl["rows"].items():
            states.append({"name": state, "rects": [(r["x"], r["y"], r["w"], r["h"]) for r in rects]})
            meta[state] = {"fps": anim.get(state, {}).get("fps", 6), "loop": anim.get(state, {}).get("loop", True)}
        atlas_file = manifest.get("game_input") or manifest.get("sprite_sheet_alpha")
        return states, (cell_w, cell_h), atlas_file, meta

    # archive-2dir-mirror / grid-row format: rows carry {row, frames, fps, loop}
    cols = int(cell.get("columns", 0)) or None
    rows_src = None
    atlas_file = None
    if "directions" in manifest:
        directions = manifest["directions"]
        chosen = direction or next(iter(directions))
        if chosen not in directions:
            raise SystemExit(f"direction '{chosen}' not in manifest; have {list(directions)}")
        rows_src = directions[chosen]["rows"]
        atlas_file = directions[chosen]["sprite_sheet"]
    elif manifest.get("animation", {}).get("rows"):
        rows_src = {k: v for k, v in manifest["animation"]["rows"].items()}
        atlas_file = manifest.get("game_input") or manifest.get("sprite_sheet_alpha")

    if not rows_src:
        raise SystemExit("manifest has no frame_layout, directions, or animation rows to read")
    if not (cell_w and cell_h):
        raise SystemExit("manifest cell width/height missing; pass --cell WxH")

    states = []
    meta = {}
    for state, info in rows_src.items():
        row = int(info["row"])
        frames = int(info["frames"])
        rects = [(c * cell_w, row * cell_h, cell_w, cell_h) for c in range(frames)]
        states.append({"name": state, "rects": rects})
        meta[state] = {"fps": int(info.get("fps", 6)), "loop": bool(info.get("loop", True))}
    return states, (cell_w, cell_h), atlas_file, meta


# --- writing ----------------------------------------------------------------

def write_run(
    out_dir: Path,
    atlas: Image.Image,
    states: list[dict[str, Any]],
    cell: tuple[int, int],
    meta: dict[str, Any],
    layout_source: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    cell_w, cell_h = cell
    frames_root = out_dir / "frames"
    frames_root.mkdir(parents=True, exist_ok=True)

    request_states = {}
    manifest_rows = []
    for state in states:
        name = state["name"]
        state_dir = frames_root / name
        state_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for index, (x, y, w, h) in enumerate(state["rects"]):
            crop = atlas.crop((x, y, x + w, y + h)).convert("RGBA")
            # place into a clean cell; center when the crop is smaller (auto-detect)
            if crop.size == (cell_w, cell_h):
                framed = crop
            else:
                framed = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
                framed.alpha_composite(crop, ((cell_w - w) // 2, (cell_h - h) // 2))
            out = state_dir / f"frame-{index}.png"
            atomic_save_image(framed, out)
            files.append(relative_posix(out, out_dir))
        m = meta.get(name, {})
        request_states[name] = {
            "frames": len(state["rects"]),
            "fps": int(m.get("fps", 6)),
            "loop": bool(m.get("loop", True)),
            "action": "",
        }
        manifest_rows.append({"state": name, "frames": len(state["rects"]), "method": "unpacked", "files": files, "ok": True})

    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": out_dir.name, "description": f"unpacked from atlas ({layout_source})"},
        "cell": {"shape": "rect" if cell_w != cell_h else "square", "width": cell_w, "height": cell_h, "size": cell_w, "safe_margin": 0},
        "chroma_key": provenance.get("chroma_key", {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255]}),
        "states": request_states,
    }
    atomic_write_text(out_dir / "sprite-request.json", json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(
        frames_root / "frames-manifest.json",
        json.dumps({"ok": True, "engine": "component-row", "run_dir": str(out_dir), "cell": request["cell"], "rows": manifest_rows, "errors": [], "warnings": []}, ensure_ascii=False, indent=2) + "\n",
    )
    source_doc = {
        "version": 1,
        "kind": "sprite-gen-unpack-source",
        "layout_source": layout_source,
        "cell": {"width": cell_w, "height": cell_h},
        **provenance,
    }
    atomic_write_text(out_dir / "unpack-source.json", json.dumps(source_doc, ensure_ascii=False, indent=2) + "\n")
    return {"layout_source": layout_source, "states": [s["name"] for s in states], "cell": [cell_w, cell_h]}


def import_pngs(out_dir: Path, png_paths: list[Path], state_name: str, labels: list[str], iso: dict[str, Any] | None = None) -> dict[str, Any]:
    """단일 그룹 임포트 (하위호환 래퍼) — 그룹 지원 본체는 import_png_groups."""
    return import_png_groups(out_dir, [{"name": state_name, "paths": png_paths, "labels": labels}], iso)


def import_png_groups(out_dir: Path, groups: list[dict[str, Any]], iso: dict[str, Any] | None = None,
                      base_src: Path | None = None) -> dict[str, Any]:
    """Import separate PNGs as one or more curator rows (states).

    groups: [{"name": str, "paths": [Path], "labels": [str], "refs": [Path]}] —
    하위폴더 하나가 큐레이터 줄 하나가 된다 (예: reference/ 와 portraits/ 를 분리).
    셀 크기는 전 그룹 공유 최대치라 카드 배율이 그룹 간에도 일관된다. 원본은 복사만 한다.

    소스 1급 수용 (run-contract.md §4): `base_src` 는 아이덴티티 truth →
    `base-source.<ext>` (큐레이션뷰 베이스 참조 줄). group 의 `refs` 는 그 줄의
    생성 재료 `<role>-<name>.png` → `references/imported/<state>/` (상태별 생성 재료
    칩). 역할(anchor/basis/guide) 해석은 serve_curation `_state_refs` 가 파일명에서
    단독 수행한다 (여긴 파일명 보존 복사만 — 역할 파싱 SSoT 이원화 금지).
    """
    loaded: list[tuple[dict[str, Any], list[Image.Image]]] = []
    cell_w = 0
    cell_h = 0
    for group in groups:
        imgs = [Image.open(p).convert("RGBA") for p in group["paths"]]
        cell_w = max(cell_w, max(i.width for i in imgs))
        cell_h = max(cell_h, max(i.height for i in imgs))
        loaded.append((group, imgs))

    request_states: dict[str, Any] = {}
    manifest_rows = []
    source_files: list[str] = []
    source_labels: list[str] = []
    imported_refs_manifest: dict[str, list[str]] = {}
    for group, imgs in loaded:
        state_name = str(group["name"])
        labels = list(group.get("labels", []))
        state_dir = out_dir / "frames" / state_name
        state_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for index, im in enumerate(imgs):
            if im.size == (cell_w, cell_h):
                framed = im
            else:
                framed = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
                framed.alpha_composite(im, ((cell_w - im.width) // 2, (cell_h - im.height) // 2))
            out = state_dir / f"frame-{index}.png"
            atomic_save_image(framed, out)
            files.append(relative_posix(out, out_dir))
        request_states[state_name] = {"frames": len(imgs), "fps": 2, "loop": False, "action": "imported still set"}
        manifest_rows.append({"state": state_name, "frames": len(imgs), "method": "imported-pngs", "files": files, "labels": labels, "ok": True})
        source_files.extend(p.name for p in group["paths"])
        source_labels.extend(labels)
        # generation-material refs → references/imported/<state>/ (큐레이터 생성 재료 칩).
        # 파일명을 보존해 복사한다 — role 파싱은 serve_curation 이 단독으로.
        ref_paths = [Path(r) for r in group.get("refs", [])]
        if ref_paths:
            ref_dir = out_dir / "references" / "imported" / state_name
            ref_dir.mkdir(parents=True, exist_ok=True)
            for rp in ref_paths:
                shutil.copy2(rp, ref_dir / rp.name)
            imported_refs_manifest[state_name] = [rp.name for rp in ref_paths]

    # base 아이덴티티 truth → base-source.<ext> (큐레이션뷰 베이스 참조 줄). prepare.py 와
    # 동일하게 원본 확장자 보존 복사 (identity 는 재인코딩 없이 pristine 유지).
    base_out_name = None
    if base_src is not None:
        base_src = Path(base_src)
        base_out = out_dir / f"base-source{base_src.suffix.lower() or '.png'}"
        shutil.copy2(base_src, base_out)
        base_out_name = base_out.name

    first_dir = groups[0]["paths"][0].parent
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": out_dir.name, "description": f"imported PNG set from {first_dir}"},
        "cell": {"shape": "square" if cell_w == cell_h else "rect", "width": cell_w, "height": cell_h, "size": cell_w, "safe_margin": 0},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255]},
        "states": request_states,
    }
    if iso:
        request["iso"] = iso  # ground-grid geometry for the curator overlay
    atomic_write_text(out_dir / "sprite-request.json", json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(
        out_dir / "frames" / "frames-manifest.json",
        json.dumps({"ok": True, "engine": "component-row", "run_dir": str(out_dir), "cell": request["cell"],
                    "rows": manifest_rows, "errors": [], "warnings": []}, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(
        out_dir / "unpack-source.json",
        json.dumps({"version": 1, "kind": "sprite-gen-unpack-source", "layout_source": "imported-pngs",
                    "cell": {"width": cell_w, "height": cell_h}, "source_dir": str(first_dir),
                    "files": source_files, "labels": source_labels,
                    "groups": [str(g["name"]) for g in groups],
                    "base_source": base_out_name, "imported_refs": imported_refs_manifest}, ensure_ascii=False, indent=2) + "\n",
    )
    return {"layout_source": "imported-pngs", "states": [str(g["name"]) for g in groups], "cell": [cell_w, cell_h],
            "frames": sum(len(imgs) for _, imgs in loaded),
            "base_source": base_out_name, "imported_refs": imported_refs_manifest}


def _new_staging(out_dir: Path) -> Path:
    """A fresh empty staging dir beside out_dir. The run is built here first, then
    published atomically, so a failed/partial build never touches the prior run."""
    staging = out_dir.parent / f".{out_dir.name}.sg-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    return staging


def _publish_run(staging: Path, out_dir: Path) -> None:
    """Replace out_dir's contents with the fully-built staging run, keeping the held
    lock. Called only after a successful build — so validation/build failures leave the
    prior canonical run untouched (Atomicity: a rebuild fully succeeds or rolls back)."""
    for child in out_dir.iterdir():
        if child.name == LOCK_FILENAME:
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for child in staging.iterdir():
        if child.name == LOCK_FILENAME:  # never clobber the lock we hold
            continue
        shutil.move(str(child), str(out_dir / child.name))


def parse_grid(value: str) -> tuple[int, int]:
    cols, rows = value.lower().split("x")
    return int(cols), int(rows)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", type=Path, help="sprite sheet PNG (or use --manifest)")
    parser.add_argument("--manifest", type=Path, help="manifest JSON with frame layout")
    parser.add_argument("--pngs-dir", type=Path, help="folder of separate PNGs to import as one state's frames")
    parser.add_argument("--state-name", default="items", help="state name for --pngs-dir import")
    parser.add_argument("--out-dir", type=Path, help="run dir for output; defaults to a '<source>-curator' folder next to the input so it is easy to find")
    parser.add_argument("--grid", type=parse_grid, help="explicit COLSxROWS, for example 8x9")
    parser.add_argument("--cell", type=parse_grid, help="explicit cell WxH (for manifests missing cell size)")
    parser.add_argument("--direction", help="which direction to unpack from a multi-direction manifest")
    parser.add_argument("--states", help="comma-separated state names to override detected/row names")
    parser.add_argument("--auto", action="store_true", help="force alpha auto-detect even if a manifest is given")
    parser.add_argument("--force", action="store_true", help="overwrite an existing out-dir")
    return parser


def _namespace_from_kwargs(**kwargs: object) -> argparse.Namespace:
    parser = _build_parser()
    values: dict[str, object] = {}
    remaining = dict(kwargs)
    for action in parser._actions:
        if action.dest == "help":
            continue
        default = [] if action.nargs == "*" else action.default
        if default is argparse.SUPPRESS:
            default = None
        value = remaining.pop(action.dest, default)
        if getattr(action, "required", False) and value is None:
            raise TypeError(f"missing required argument: {action.dest}")
        values[action.dest] = value
    if remaining:
        names = ", ".join(sorted(remaining))
        raise TypeError(f"unexpected keyword argument(s): {names}")
    return argparse.Namespace(**values)
def _run(args: argparse.Namespace):

    # default the run dir to a clearly-findable sibling next to the input.
    if args.out_dir:
        out_dir = args.out_dir.expanduser().resolve()
    else:
        if args.pngs_dir:
            base = args.pngs_dir.expanduser().resolve()
            out_dir = base.parent / f"{base.name}-curator"
        elif args.atlas:
            base = args.atlas.expanduser().resolve()
            out_dir = base.parent / f"{base.stem}-curator"
        elif args.manifest:
            base = args.manifest.expanduser().resolve()
            out_dir = base.parent / f"{base.stem}-curator"
        else:
            raise SystemExit("need one of --pngs-dir / --atlas / --manifest (or pass --out-dir)")

    if out_dir.exists() and any(out_dir.iterdir()) and not args.force:
        raise SystemExit(f"out-dir not empty: {out_dir} (use --force)")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(f"cannot create run dir next to the input: {out_dir}\n  {exc}\n  pass --out-dir <writable path> to choose another location")
    acquire_run_dir_lock(out_dir, "unpack_atlas_run")

    # --pngs-dir: import a folder of separate PNGs (e.g. a furniture set).
    # 하위폴더가 있으면 각 하위폴더가 큐레이터 줄(state) 하나가 된다 —
    # 예: reference/ (베이스 이미지 칸) + portraits/ (표정 세트 줄).
    if args.pngs_dir:
        src = args.pngs_dir.expanduser().resolve()
        top_pngs = sorted(p for p in src.glob("*.png"))
        # reserved underscore dirs carry sources, not curator rows (run-contract.md §4):
        #   _base/ = whole-set identity image, <group>/_refs/ = that row's generation material.
        reserved = {"_base", "_refs"}
        subdirs = sorted(d for d in src.iterdir() if d.is_dir() and d.name not in reserved)
        # _base/<img> → base-source (base reference row). Accept the same formats serve_curation shows.
        base_src = None
        base_dir = src / "_base"
        if base_dir.is_dir():
            base_candidates = sorted(p for p in base_dir.iterdir()
                                     if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
            if base_candidates:
                base_src = base_candidates[0]

        def _group_refs(group_dir: Path) -> list[Path]:
            rd = group_dir / "_refs"
            return sorted(rd.glob("*.png")) if rd.is_dir() else []

        # prefer human names from a sibling meta.json (file -> item name), else filename stem
        file_to_name: dict[str, str] = {}
        iso = None
        meta_path = src / "meta.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            file_to_name = {info.get("file"): name for name, info in meta.get("items", {}).items() if isinstance(info, dict)}
            tile = meta.get("tile")
            anchor = meta.get("anchor")
            if tile and anchor:
                iso = {
                    "tile": {"width": int(tile["width"]), "height": int(tile["height"])},
                    "projection": tile.get("projection", "2:1 dimetric diamond"),
                    "anchor_pixel": anchor.get("pixel", [128, 222]),
                    "canvas": meta.get("style", {}).get("canvas", [256, 256]),
                }
        groups: list[dict[str, Any]] = []
        if top_pngs:
            groups.append({"name": args.state_name, "paths": top_pngs,
                           "labels": [file_to_name.get(p.name, p.stem) for p in top_pngs],
                           "refs": _group_refs(src)})
        for sub in subdirs:
            sub_pngs = sorted(p for p in sub.glob("*.png"))
            if sub_pngs:
                groups.append({"name": sub.name, "paths": sub_pngs,
                               "labels": [file_to_name.get(p.name, p.stem) for p in sub_pngs],
                               "refs": _group_refs(sub)})
        if not groups:
            raise SystemExit(f"no PNGs in {src}")
        # imported ref roles are validated fail-loud (run-contract §4). A _refs file whose
        # prefix isn't a known role (anchor/basis/guide) is malformed input — reject it
        # loudly instead of silently relabeling it as guide (No Silent Fallback).
        bad_refs = [f"{g['name']}/_refs/{Path(r).name}"
                    for g in groups for r in g.get("refs", [])
                    if imported_ref_role(Path(r).name) is None]
        if bad_refs:
            raise SystemExit("invalid _refs role prefix — expected anchor-/basis-/guide-<name>.png, got: "
                             + ", ".join(bad_refs))
        # atomic rebuild: all validation above ran before any mutation; build into a
        # staging dir, then publish over out_dir. A --force re-import that fails (or is
        # invalid) leaves the prior run intact — never clear-then-fail.
        staging = _new_staging(out_dir)
        try:
            result = import_png_groups(staging, groups, iso, base_src=base_src)
            _publish_run(staging, out_dir)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        result["ok"] = True
        result["out_dir"] = str(out_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
    provenance: dict[str, Any] = {
        "atlas": str(args.atlas) if args.atlas else None,
        "manifest": str(args.manifest) if args.manifest else None,
        "direction": args.direction,
    }

    # resolve layout + atlas image
    atlas_path = args.atlas
    meta: dict[str, Any] = {}
    if args.grid:
        layout_source = "grid-explicit"
        if not atlas_path:
            raise SystemExit("--grid needs --atlas")
        atlas = Image.open(atlas_path).convert("RGBA")
        states, cell = grid_layout(atlas, *args.grid)
    elif manifest and not args.auto:
        layout_source = "manifest"
        states, cell, atlas_name, meta = manifest_layout(manifest, args.direction)
        provenance["chroma_key"] = manifest.get("chroma_key") if isinstance(manifest.get("chroma_key"), dict) else None
        if not atlas_path:
            atlas_path = (args.manifest.parent / atlas_name) if atlas_name else None
        if not atlas_path or not Path(atlas_path).is_file():
            raise SystemExit(f"could not locate atlas image (manifest pointed to {atlas_name}); pass --atlas")
        atlas = Image.open(atlas_path).convert("RGBA")
    else:
        layout_source = "auto-detect"
        if not atlas_path:
            raise SystemExit("auto-detect needs --atlas")
        atlas = Image.open(atlas_path).convert("RGBA")
        states, cell = auto_detect(atlas)

    if args.states:
        names = [n.strip() for n in args.states.split(",")]
        for i, name in enumerate(names):
            if i < len(states):
                states[i]["name"] = name

    provenance["atlas"] = str(atlas_path)
    # atomic rebuild (see --pngs-dir): atlas/layout resolution above is read-only; build
    # into staging, then publish over out_dir so a failed rebuild leaves the prior run intact.
    staging = _new_staging(out_dir)
    try:
        result = write_run(staging, atlas, states, cell, meta, layout_source, provenance)
        _publish_run(staging, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result["ok"] = True
    result["out_dir"] = str(out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0



def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
