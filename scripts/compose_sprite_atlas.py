#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compose component-row frames into a game atlas and runtime manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image


def alpha_nonzero_count(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[1:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--atlas", default="sprite-sheet-alpha.png")
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--report", default="sprite-sheet-alpha.report.json")
    parser.add_argument("--min-used-pixels", type=int, default=400)
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    frames_manifest = json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    if not frames_manifest.get("ok"):
        raise SystemExit("frames-manifest.json is not ok; fix extraction before composing atlas")

    states = list(request["states"])
    cell_size = int(request["cell"]["size"])
    max_frames = max(int(request["states"][state]["frames"]) for state in states)
    atlas = Image.new("RGBA", (max_frames * cell_size, len(states) * cell_size), (0, 0, 0, 0))
    frame_layout: dict[str, Any] = {
        "sheetWidth": atlas.width,
        "sheetHeight": atlas.height,
        "cellWidth": cell_size,
        "cellHeight": cell_size,
        "rows": {},
    }
    animation: dict[str, Any] = {
        "cellWidth": cell_size,
        "cellHeight": cell_size,
        "columns": max_frames,
        "rows": {},
    }
    errors: list[str] = []
    cells: list[dict[str, Any]] = []

    for row_index, state in enumerate(states):
        entry = request["states"][state]
        frame_count = int(entry["frames"])
        frames = []
        for frame_index in range(frame_count):
            frame_path = run_dir / "frames" / state / f"frame-{frame_index}.png"
            if not frame_path.is_file():
                errors.append(f"missing frame: {frame_path}")
                continue
            with Image.open(frame_path) as opened:
                frame = opened.convert("RGBA")
            if frame.size != (cell_size, cell_size):
                errors.append(f"{frame_path} is {frame.width}x{frame.height}; expected {cell_size}x{cell_size}")
            nontransparent = alpha_nonzero_count(frame)
            if nontransparent < args.min_used_pixels:
                errors.append(f"{state} frame {frame_index} is too sparse ({nontransparent})")
            left = frame_index * cell_size
            top = row_index * cell_size
            atlas.alpha_composite(frame, (left, top))
            rect = {"x": left, "y": top, "w": cell_size, "h": cell_size}
            frames.append(rect)
            cells.append({"state": state, "frame": frame_index, "nontransparent_pixels": nontransparent, **rect})

        frame_layout["rows"][state] = frames
        animation["rows"][state] = {
            "row": row_index,
            "frames": frame_count,
            "fps": int(entry.get("fps", 6)),
            "loop": bool(entry.get("loop", True)),
        }

    report = {
        "ok": not errors,
        "engine": "component-row",
        "errors": errors,
        "atlas": args.atlas,
        "manifest": args.manifest,
        "cell": request["cell"],
        "states": states,
        "cells": cells,
        "frame_layout": frame_layout,
    }

    report_path = run_dir / args.report
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if errors:
        print(json.dumps({k: v for k, v in report.items() if k != "cells"}, ensure_ascii=False, indent=2))
        return 1

    atlas_path = run_dir / args.atlas
    atlas.save(atlas_path)
    manifest = {
        "characterId": request["character"]["id"],
        "engine": "component-row",
        "game_input": args.atlas,
        "degraded_static_fallback": False,
        "sprite_sheet_alpha": args.atlas,
        "sprite_sheet_alpha_report": args.report,
        "base_image": request["character"].get("base_image"),
        "cell": request["cell"],
        "chroma_key": request["chroma_key"],
        "animation": animation,
        "frame_layout": frame_layout,
    }
    (run_dir / args.manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "atlas": str(atlas_path), "manifest": str(run_dir / args.manifest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
