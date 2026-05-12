#!/usr/bin/env python3
"""Extract per-row frame bounding boxes from a sprite-sheet-alpha.png.

One-shot master-mode generation does not guarantee an exact 256px uniform grid:
rows come back with uneven frame counts and frames drift off the cell lattice.
This module recovers the *real* frame rectangles by reading the alpha channel:

  1. row alpha profile  -> split the sheet into up to 4 row bands (idle/run/jump/talk)
  2. column alpha profile within each band -> per-frame x-extent
  3. local alpha profile within each frame  -> tight y-extent

The result is the canonical SSoT layout the game runtime consumes via
`manifest.json.frame_layout` -> `three-game-starter/src/sprite-runtime.js`
`getSpriteFrame()` (`sprite.frameLayout.rows[state]` = `[{x,y,w,h}, ...]`).

Modes:
  --image PATH [--manifest PATH] [--write]   single sheet; print layout, optionally
                                             write `frame_layout` into the manifest
  --root DIR [--write]                        iterate DIR/*/sprite-sheet-alpha.png and
                                             update each sibling manifest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROW_NAMES = ["idle", "run", "jump", "talk"]
ROW_PROFILE_FRACTION = 0.02   # band threshold = 2% of peak scanline alpha sum
COL_PROFILE_FRACTION = 0.02   # frame threshold = 2% of peak column alpha sum
FRAME_PROFILE_FRACTION = 0.02
MIN_ROW_RUN_PX = 8            # a row band must be at least this tall
MIN_FRAME_RUN_PX = 6          # a frame must be at least this wide
MIN_FRAME_HEIGHT_PX = 3


def runs_above_threshold(profile, threshold, min_run):
    """Return [(start, end_exclusive), ...] where profile > threshold for >= min_run."""
    out = []
    in_run = False
    start = 0
    for i, value in enumerate(profile):
        if value > threshold:
            if not in_run:
                in_run = True
                start = i
        elif in_run:
            if i - start >= min_run:
                out.append((start, i))
            in_run = False
    if in_run and len(profile) - start >= min_run:
        out.append((start, len(profile)))
    return out


def _alpha_pixels(image: Image.Image):
    alpha = image.getchannel("A")
    width, height = alpha.size
    data = alpha.load()
    return data, width, height


def analyze(sheet_path) -> tuple[int, int, dict]:
    """Return (sheet_width, sheet_height, {row_name: [{x,y,w,h}, ...]})."""
    image = Image.open(sheet_path).convert("RGBA")
    px, width, height = _alpha_pixels(image)

    row_profile = [0] * height
    for y in range(height):
        s = 0
        for x in range(width):
            s += px[x, y]
        row_profile[y] = s
    row_peak = max(row_profile) or 1
    row_bands = runs_above_threshold(row_profile, row_peak * ROW_PROFILE_FRACTION, MIN_ROW_RUN_PX)

    rows: dict[str, list[dict]] = {}
    for index, (y0, y1) in enumerate(row_bands[: len(ROW_NAMES)]):
        col_profile = [0] * width
        for x in range(width):
            s = 0
            for y in range(y0, y1):
                s += px[x, y]
            col_profile[x] = s
        col_peak = max(col_profile) or 1
        col_runs = runs_above_threshold(col_profile, col_peak * COL_PROFILE_FRACTION, MIN_FRAME_RUN_PX)

        frames: list[dict] = []
        for x0, x1 in col_runs:
            local = []
            for y in range(y0, y1):
                s = 0
                for x in range(x0, x1):
                    s += px[x, y]
                local.append(s)
            local_peak = max(local) or 1
            y_runs = runs_above_threshold(local, local_peak * FRAME_PROFILE_FRACTION, MIN_FRAME_HEIGHT_PX)
            if y_runs:
                fy0 = y0 + y_runs[0][0]
                fy1 = y0 + y_runs[-1][1]
            else:
                fy0, fy1 = y0, y1
            frames.append({"x": x0, "y": fy0, "w": x1 - x0, "h": fy1 - fy0})
        rows[ROW_NAMES[index]] = frames
    return width, height, rows


def build_frame_layout(sheet_path) -> dict:
    width, height, rows = analyze(sheet_path)
    return {"sheetWidth": width, "sheetHeight": height, "rows": rows}


def _write_into_manifest(manifest_path: Path, layout: dict) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["frame_layout"] = layout
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _counts(layout: dict) -> dict:
    return {name: len(frames) for name, frames in layout["rows"].items()}


def _process_one(image_path: Path, manifest_path: Path | None, write: bool) -> dict:
    layout = build_frame_layout(image_path)
    if write and manifest_path is not None:
        _write_into_manifest(manifest_path, layout)
    return {
        "image": str(image_path),
        "manifest": str(manifest_path) if manifest_path else None,
        "written": bool(write and manifest_path is not None),
        "frame_layout": layout,
        "row_frame_counts": _counts(layout),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image", help="path to a single sprite-sheet-alpha.png")
    group.add_argument("--root", help="directory containing <character-id>/sprite-sheet-alpha.png")
    parser.add_argument("--manifest", help="manifest.json to write frame_layout into (single-image mode)")
    parser.add_argument("--write", action="store_true", help="write frame_layout into the manifest(s)")
    parser.add_argument("--report", help="optional JSON report path")
    parser.add_argument("--sheet-name", default="sprite-sheet-alpha.png", help="sheet filename in --root mode")
    args = parser.parse_args()

    results: list[dict] = []

    if args.image:
        image_path = Path(args.image)
        if not image_path.exists():
            print(json.dumps({"ok": False, "error": f"missing image: {image_path}"}, ensure_ascii=False))
            return 1
        manifest_path = Path(args.manifest) if args.manifest else (image_path.parent / "manifest.json")
        if not manifest_path.exists():
            manifest_path = None
        results.append(_process_one(image_path, manifest_path, args.write))
    else:
        root = Path(args.root)
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            sheet = child / args.sheet_name
            if not sheet.exists():
                results.append({"image": str(sheet), "manifest": None, "skipped": "no sprite sheet"})
                continue
            manifest_path = child / "manifest.json"
            results.append(_process_one(sheet, manifest_path if manifest_path.exists() else None, args.write))

    report = {"ok": True, "results": results}
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
