#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compose component-row frames into a game atlas and runtime manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image

from curation import apply_transform, load_curation, state_plan
from runio import acquire_run_dir_lock, atomic_save_image, atomic_write_text


def alpha_nonzero_count(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[1:])


def cell_geometry(cell: dict[str, Any]) -> tuple[int, int]:
    width = int(cell.get("width", cell.get("size", 0)))
    height = int(cell.get("height", cell.get("size", 0)))
    if width <= 0 or height <= 0:
        raise SystemExit("cell width/height must be positive in sprite-request.json")
    return width, height


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--atlas", default="sprite-sheet-alpha.png")
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--report", default="sprite-sheet-alpha.report.json")
    parser.add_argument("--min-used-pixels", type=int, default=400)
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    acquire_run_dir_lock(run_dir, "compose_sprite_atlas")
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    frames_manifest = json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    if not frames_manifest.get("ok"):
        raise SystemExit("frames-manifest.json is not ok; fix extraction before composing atlas")

    states = list(request["states"])
    cell_width, cell_height = cell_geometry(request["cell"])
    cell_size = (cell_width, cell_height)

    # curation.json is an optional non-destructive sidecar. When absent, every
    # state uses all extracted frames in order with identity transform.
    curation = load_curation(run_dir)
    plans = {
        state: state_plan(curation, state, int(request["states"][state]["frames"]))
        for state in states
    }

    max_frames = max(len(ordered) for ordered, _transforms in plans.values())
    atlas = Image.new("RGBA", (max_frames * cell_width, len(states) * cell_height), (0, 0, 0, 0))
    frame_layout: dict[str, Any] = {
        "sheetWidth": atlas.width,
        "sheetHeight": atlas.height,
        "cellWidth": cell_width,
        "cellHeight": cell_height,
        "rows": {},
    }
    animation: dict[str, Any] = {
        "cellWidth": cell_width,
        "cellHeight": cell_height,
        "columns": max_frames,
        "rows": {},
    }
    errors: list[str] = []
    cells: list[dict[str, Any]] = []

    for row_index, state in enumerate(states):
        entry = request["states"][state]
        ordered, transforms = plans[state]
        frames = []
        for column, frame_index in enumerate(ordered):
            frame_path = run_dir / "frames" / state / f"frame-{frame_index}.png"
            if not frame_path.is_file():
                errors.append(f"missing frame: {frame_path}")
                continue
            with Image.open(frame_path) as opened:
                source = opened.convert("RGBA")
            if source.size != cell_size:
                errors.append(f"{frame_path} is {source.width}x{source.height}; expected {cell_width}x{cell_height}")
            # apply the human curation transform (identity when uncurated)
            frame = apply_transform(source, transforms.get(frame_index), cell_size)
            nontransparent = alpha_nonzero_count(frame)
            if nontransparent < args.min_used_pixels:
                errors.append(f"{state} frame {frame_index} is too sparse ({nontransparent})")
            left = column * cell_width
            top = row_index * cell_height
            atlas.alpha_composite(frame, (left, top))
            rect = {"x": left, "y": top, "w": cell_width, "h": cell_height}
            frames.append(rect)
            cells.append({"state": state, "frame": frame_index, "nontransparent_pixels": nontransparent, **rect})

        frame_layout["rows"][state] = frames
        animation["rows"][state] = {
            "row": row_index,
            "frames": len(ordered),
            "fps": int(entry.get("fps", 6)),
            "loop": bool(entry.get("loop", True)),
        }

    report = {
        "ok": not errors,
        "engine": "component-row",
        "curation_applied": curation is not None,
        "errors": errors,
        "atlas": args.atlas,
        "manifest": args.manifest,
        "cell": request["cell"],
        "states": states,
        "cells": cells,
        "frame_layout": frame_layout,
    }

    report_path = run_dir / args.report
    atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if errors:
        print(json.dumps({k: v for k, v in report.items() if k != "cells"}, ensure_ascii=False, indent=2))
        return 1

    atlas_path = run_dir / args.atlas
    atomic_save_image(atlas, atlas_path)
    manifest = {
        "characterId": request["character"]["id"],
        "engine": "component-row",
        "game_input": args.atlas,
        "degraded_static_fallback": False,
        "curation_applied": curation is not None,
        "sprite_sheet_alpha": args.atlas,
        "sprite_sheet_alpha_report": args.report,
        "base_image": request["character"].get("base_image"),
        "cell": request["cell"],
        "chroma_key": request["chroma_key"],
        "animation": animation,
        "frame_layout": frame_layout,
    }
    atomic_write_text(run_dir / args.manifest, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "atlas": str(atlas_path), "manifest": str(run_dir / args.manifest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
