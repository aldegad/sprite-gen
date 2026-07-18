# SPDX-License-Identifier: Apache-2.0
"""Single sprite-gen CLI entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from sprite_gen import (
    compose_atlas,
    compose_cycle,
    compose_gif,
    correction_loop,
    export_pngs,
    extract,
    gen,
    inspect,
    prepare,
    preview,
    score,
    slice_sheet,
    unpack_atlas,
)
from sprite_gen.prepare import STYLE_DEFAULT, _outline_config


def _parse_frames(value: str) -> list[int]:
    frames = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not frames:
        raise argparse.ArgumentTypeError("at least one frame number is required")
    if any(frame <= 0 for frame in frames):
        raise argparse.ArgumentTypeError("frame numbers are 1-based and must be positive")
    return frames


def _parse_frame_order(value: str) -> list[int]:
    frames = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not frames:
        raise argparse.ArgumentTypeError("frame order must contain at least one frame")
    if any(frame <= 0 for frame in frames):
        raise argparse.ArgumentTypeError("frame order is 1-based and must be positive")
    return frames


def _parse_grid(value: str) -> tuple[int, int]:
    cols, rows = value.lower().split("x")
    return int(cols), int(rows)


def _add_prepare(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--character-id", required=True)
    p.add_argument("--base-image", type=Path)
    p.add_argument("--description", default="")
    p.add_argument("--style", default=STYLE_DEFAULT)
    p.add_argument("--cell-size", type=int, default=256)
    p.add_argument("--cell-width", type=int)
    p.add_argument("--cell-height", type=int)
    p.add_argument("--safe-margin", type=int, default=24)
    p.add_argument("--chroma-key", default="auto", help="auto or #RRGGBB")
    p.add_argument("--fit-resample", choices=["lanczos", "nearest", "kcentroid"], default=None)
    p.add_argument("--fit-align-x", choices=["bbox-center", "centroid", "foot-centroid", "alpha-centroid"], default=None)
    p.add_argument("--fit-align-y", choices=["center", "bottom"], default=None)
    p.add_argument("--fit-pixel-perfect", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--fit-logical-height", type=int, default=None)
    p.add_argument("--fit-palette-size", type=int, default=None)
    p.add_argument("--fit-detail-bias", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--fit-outline", type=_outline_config, default=None, metavar="{on,off,STRENGTH}")
    p.add_argument("--fit-pitch-hint", type=int, default=None)
    p.add_argument("--motion-phase-guides", action="store_true")
    p.add_argument("--request", type=Path)
    p.add_argument("--request-json")
    p.add_argument("--force", action="store_true")


def _add_extract(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--states", default="all")
    p.add_argument("--key-threshold", type=float, default=80.0)
    p.add_argument("--fringe-key-threshold", type=float, default=180.0)
    p.add_argument("--fringe-delta", type=float, default=18.0)
    p.add_argument("--fringe-unmix-reach", type=int, default=None)
    p.add_argument("--spill-max-fraction", type=float, default=None)
    p.add_argument("--segmentation", choices=("components", "projection"), default=None)
    p.add_argument("--allow-slot-fallback", action="store_true")
    p.add_argument("--min-used-pixels", type=int, default=400)
    p.add_argument("--edge-margin", type=int, default=2)
    p.add_argument("--edge-pixel-threshold", type=int, default=24)
    p.add_argument("--chroma-adjacent-threshold", type=float, default=150.0)
    p.add_argument("--chroma-adjacent-pixel-threshold", type=int, default=120)
    p.add_argument("--small-outlier-ratio", type=float, default=0.35)
    p.add_argument("--large-outlier-ratio", type=float, default=2.75)


def _add_compose_atlas(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--atlas", default="sprite-sheet-alpha.png")
    p.add_argument("--manifest", default="manifest.json")
    p.add_argument("--report", default="sprite-sheet-alpha.report.json")
    p.add_argument("--min-used-pixels", type=int, default=400)


def _add_preview(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--delay-ticks", type=int)


def _add_compose_cycle(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--state", required=True)
    p.add_argument("--frames", type=_parse_frames)
    p.add_argument("--name", required=True)
    p.add_argument("--duration-ms", type=int, default=190)
    p.add_argument("--delay-ticks", type=int)
    p.add_argument("--note", default="")


def _add_compose_gif(p: argparse.ArgumentParser) -> None:
    p.add_argument("inputs", nargs="*", type=Path)
    p.add_argument("--frame-dir", type=Path)
    p.add_argument("--frame-order", type=_parse_frame_order)
    p.add_argument("--run-dir", type=Path)
    p.add_argument("--out-dir", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--delay-ticks", type=int, default=17)
    p.add_argument("--loop-count", type=int, default=0)
    p.add_argument("--contact-output", type=Path)
    p.add_argument("--manifest-output", type=Path)
    p.add_argument("--alpha-threshold", type=int, default=8)


def _add_unpack_atlas(p: argparse.ArgumentParser) -> None:
    p.add_argument("--atlas", type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--pngs-dir", type=Path)
    p.add_argument("--state-name", default="items")
    p.add_argument("--out-dir", type=Path)
    p.add_argument("--grid", type=_parse_grid)
    p.add_argument("--cell", type=_parse_grid)
    p.add_argument("--direction")
    p.add_argument("--states")
    p.add_argument("--auto", action="store_true")
    p.add_argument("--force", action="store_true")


def _add_export_pngs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--state")
    p.add_argument("--out-dir", type=Path)
    p.add_argument("--selected-only", action="store_true")


def _add_slice_sheet(p: argparse.ArgumentParser) -> None:
    p.add_argument("--sheet", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--chroma-key", required=True, help="magenta, green, or #RRGGBB")
    p.add_argument("--grid", type=_parse_grid, default=(3, 2), help="COLSxROWS, default 3x2")
    p.add_argument("--names", help="comma-separated output names, one per cell in reading order")
    p.add_argument("--prefix", default="")
    p.add_argument("--cell-width", type=int, default=512)
    p.add_argument("--cell-height", type=int, default=768)
    p.add_argument("--baseline-y", type=float, default=725.0)
    p.add_argument("--target-height", type=float, default=645.0)
    p.add_argument("--key-threshold", type=float, default=slice_sheet.DEFAULT_KEY_THRESHOLD)
    p.add_argument("--fringe-key-threshold", type=float, default=slice_sheet.DEFAULT_FRINGE_KEY_THRESHOLD)
    p.add_argument("--fringe-delta", type=float, default=slice_sheet.DEFAULT_FRINGE_DELTA)
    p.add_argument("--fringe-unmix-reach", type=int, default=4)
    p.add_argument("--spill-max-fraction", type=float, default=0.005)
    p.add_argument("--noise-min", type=int, default=slice_sheet.DEFAULT_NOISE_MIN)
    p.add_argument("--debris-fraction", type=float, default=slice_sheet.DEFAULT_DEBRIS_FRACTION)


def _add_inspect(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--states", default="all")
    p.add_argument("--report", default="sprite-inspect.report.json")
    p.add_argument("--histogram-min", type=float, default=inspect.DEFAULT_HISTOGRAM_MIN)
    p.add_argument("--dhash-min", type=float, default=inspect.DEFAULT_DHASH_MIN)
    p.add_argument("--motion-min", type=float, default=inspect.DEFAULT_MOTION_MIN)
    p.add_argument("--key-threshold", type=float, default=80.0)
    p.add_argument("--fringe-key-threshold", type=float, default=180.0)
    p.add_argument("--fringe-delta", type=float, default=18.0)
    p.add_argument("--fringe-unmix-reach", type=int, default=None)
    p.add_argument("--spill-max-fraction", type=float, default=None)
    p.add_argument("--chroma-mode", choices=("rgb", "ycbcr"), default=None)
    p.add_argument("--no-write", action="store_true")


def _add_score(p: argparse.ArgumentParser) -> None:
    p.add_argument("--inspect-report", required=True, type=Path)
    p.add_argument("--output", default="sprite-score.report.json")
    p.add_argument("--no-write", action="store_true")


def _add_gen(p: argparse.ArgumentParser) -> None:
    gen.add_arguments(p)


def _add_correction_loop(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--states", default="all")
    p.add_argument("--out-dir", type=Path)
    p.add_argument("--max-passes", type=int, default=3)
    p.add_argument("--pass-score", type=float, default=90.0)
    p.add_argument("--provider-command")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-preserve-best", action="store_true")


COMMANDS: dict[str, tuple[str, Callable[[argparse.ArgumentParser], None], Callable[..., int]]] = {
    "prepare": ("Prepare a sprite-gen component-row run.", _add_prepare, prepare.run),
    "extract": ("Extract component-row sprite strips into clean RGBA frames.", _add_extract, extract.run),
    "compose-atlas": (
        "Compose component-row frames into a game atlas and runtime manifest.",
        _add_compose_atlas,
        compose_atlas.run,
    ),
    "preview": ("Build motion-QA previews for a sprite-gen run.", _add_preview, preview.run),
    "compose-cycle": (
        "Compose a QA-approved manual frame subset into a selected cycle.",
        _add_compose_cycle,
        compose_cycle.run,
    ),
    "compose-gif": ("Compose selected sprite frames into a clean transparent GIF.", _add_compose_gif, compose_gif.run),
    "unpack-atlas": (
        "Unpack a composed sprite sheet back into a curator-ready run directory.",
        _add_unpack_atlas,
        unpack_atlas.run,
    ),
    "export-pngs": ("Export curated frames back to named PNGs.", _add_export_pngs, export_pngs.run),
    "slice-sheet": (
        "Slice a multi-figure grid sheet into per-cell standing cuts (tachi-e).",
        _add_slice_sheet,
        slice_sheet.run,
    ),
    "gen": (
        "Generate one image via a provider (codex image_gen / grok Imagine) into a verified PNG.",
        _add_gen,
        gen.run,
    ),
    "inspect": ("Inspect sprite rows for frame-count, identity, and motion defects.", _add_inspect, inspect.run),
    "score": ("Score a sprite inspect report and emit correction hints.", _add_score, score.run),
    "correction-loop": (
        "Run a bounded inspect -> score -> correction-hint loop.",
        _add_correction_loop,
        correction_loop.run,
    ),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sprite-gen", description="Unified CLI for the sprite-gen pipeline.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<tool>")
    for name, (description, add_args, _run) in COMMANDS.items():
        sp = sub.add_parser(name, description=description, help=description)
        add_args(sp)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    kwargs = vars(args).copy()
    command = kwargs.pop("command")
    _description, _add_args, run_fn = COMMANDS[command]
    return run_fn(**kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
