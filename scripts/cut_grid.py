#!/usr/bin/env python3
"""Mechanically cut a magenta-keyed sprite sheet on the FIXED 256px master grid.

The master skeleton (`assets/skeletons/sprite-gen-master-256.png`) defines a fixed
6x4 lattice of 256x256 cells. Generated sprite sheets are expected to land on that
lattice because image generation is steered by the master as a grid reference.

This cutter applies the lattice as CONSTANTS:
  - no image analysis
  - no alpha-bbox heuristics
  - no per-image adaptation
Every frame is a 256x256 square at `(col*256, row*256)`.

Pipeline position:
    raw sheet (magenta bg)  ->  chroma_key_magenta.py  ->  sprite-sheet-alpha.png  ->  THIS

Output:
  - manifest.json.frame_layout  (fixed grid coords; the runtime SSoT consumed by
    three-game-starter/src/sprite-runtime.js getSpriteFrame -> sprite.frameLayout.rows[state])
  - optional per-frame PNG dump for visual verification

If the generated sprites do NOT sit cleanly inside the 256px cells, the fix is in
*generation* (improve the master skeleton / strengthen the prompt so characters land
on the lattice), NOT in this cutter. The cutter is intentionally dumb and constant.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

CELL = 256
COLUMNS = 6
ROWS_COUNT = 4
EXPECTED_W = COLUMNS * CELL   # 1536
EXPECTED_H = ROWS_COUNT * CELL  # 1024

# state -> (row index, frame count). Must match assets/sprite-gen-assets.json
# master_skeleton.row_entries. These are CONSTANTS, never derived from an image.
FRAME_MAP: dict[str, tuple[int, int]] = {
    "idle": (0, 4),
    "run": (1, 6),
    "jump": (2, 4),
    "talk": (3, 4),
}


def grid_layout() -> dict:
    rows: dict[str, list[dict[str, int]]] = {}
    for state, (row, frames) in FRAME_MAP.items():
        rows[state] = [
            {"x": col * CELL, "y": row * CELL, "w": CELL, "h": CELL}
            for col in range(frames)
        ]
    return {"sheetWidth": EXPECTED_W, "sheetHeight": EXPECTED_H, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True, help="magenta-keyed sprite-sheet-alpha.png (must be 1536x1024)")
    parser.add_argument("--manifest", help="manifest.json to write frame_layout into")
    parser.add_argument("--write", action="store_true", help="actually write frame_layout into --manifest")
    parser.add_argument(
        "--dump-frames",
        help="optional dir; dump each 256x256 frame as <dir>/<state>/frame-<i>.png for debugging "
        "(idle/, run/, jump/, talk/ subfolders — the actual playback frames the runtime cycles through)",
    )
    args = parser.parse_args()

    image = Image.open(args.image).convert("RGBA")
    if image.size != (EXPECTED_W, EXPECTED_H):
        raise SystemExit(f"image must be {EXPECTED_W}x{EXPECTED_H} (the master lattice); got {image.size}")

    layout = grid_layout()

    if args.dump_frames:
        out_dir = Path(args.dump_frames)
        for state, frames in layout["rows"].items():
            state_dir = out_dir / state
            state_dir.mkdir(parents=True, exist_ok=True)
            for index, frame in enumerate(frames):
                cell = image.crop((frame["x"], frame["y"], frame["x"] + frame["w"], frame["y"] + frame["h"]))
                cell.save(state_dir / f"frame-{index}.png")

    if args.manifest and args.write:
        manifest_path = Path(args.manifest)
        manifest = json.loads(manifest_path.read_text())
        manifest["frame_layout"] = layout
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote frame_layout into {manifest_path}")

    print(json.dumps(layout, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
