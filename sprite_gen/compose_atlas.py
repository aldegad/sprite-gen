# SPDX-License-Identifier: Apache-2.0
"""Compose component-row frames into a game atlas and runtime manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.curation import apply_transform, frame_filename, frame_variant, load_curation, state_plan
from sprite_gen.extract import require_frames_manifest
from sprite_gen.runio import acquire_run_dir_lock, atomic_save_image, atomic_write_text


def alpha_nonzero_count(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[1:])


def cell_geometry(cell: dict[str, Any]) -> tuple[int, int]:
    width = int(cell.get("width", cell.get("size", 0)))
    height = int(cell.get("height", cell.get("size", 0)))
    if width <= 0 or height <= 0:
        raise SystemExit("cell width/height must be positive in sprite-request.json")
    return width, height


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--atlas", default="sprite-sheet-alpha.png")
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--report", default="sprite-sheet-alpha.report.json")
    parser.add_argument("--min-used-pixels", type=int, default=400)
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

    run_dir = args.run_dir.expanduser().resolve()
    acquire_run_dir_lock(run_dir, "compose_sprite_atlas")
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    frames_manifest = require_frames_manifest(run_dir)  # fail loud if absent/corrupt/not-ok

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
    # 'plain' = pre-pixel-perfect twin (curator toggle off). Resolved per state
    # (per-row curator toggle > run-wide default). Fail loud when the twin is
    # missing — a plain bake must never silently fall back to pixel.
    variants = {state: frame_variant(curation, state) for state in states}

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
        variant = variants[state]
        frames = []
        for column, frame_index in enumerate(ordered):
            frame_path = run_dir / "frames" / state / frame_filename(frame_index, variant)
            if not frame_path.is_file():
                errors.append(f"missing frame ({variant} variant): {frame_path}")
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
            "frame_variant": variant,
        }

    # top-level summary: uniform value when every row agrees, else 'mixed'
    # (per-row truth lives in animation.rows.<state>.frame_variant).
    unique_variants = set(variants.values())
    variant_summary = unique_variants.pop() if len(unique_variants) == 1 else "mixed"

    report = {
        "ok": not errors,
        "engine": "component-row",
        "curation_applied": curation is not None,
        "frame_variant": variant_summary,
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
        "frame_variant": variant_summary,
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



def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
