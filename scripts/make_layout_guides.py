#!/usr/bin/env python3
"""Create sprite-gen layout guide images for row-strip generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw


DEFAULT_STATES = {
    "idle": 4,
    "run": 6,
    "jump": 4,
    "talk": 4,
}


def draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    fill: str,
    dash: int = 8,
    gap: int = 6,
) -> None:
    x1, y1 = start
    x2, y2 = end
    if x1 == x2:
        for y in range(min(y1, y2), max(y1, y2), dash + gap):
            draw.line((x1, y, x2, min(y + dash, max(y1, y2))), fill=fill)
        return
    if y1 == y2:
        for x in range(min(x1, x2), max(x1, x2), dash + gap):
            draw.line((x, y1, min(x + dash, max(x1, x2)), y2), fill=fill)
        return
    raise ValueError("only horizontal or vertical dashed lines are supported")


def parse_state(value: str) -> tuple[str, int]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("state must be NAME=FRAMES")
    name, raw_frames = value.split("=", 1)
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("state name is empty")
    try:
        frames = int(raw_frames)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("frames must be an integer") from exc
    if frames <= 0:
        raise argparse.ArgumentTypeError("frames must be positive")
    return name, frames


def create_guide(
    output: Path,
    *,
    state: str,
    frames: int,
    cell_width: int,
    cell_height: int,
    margin_x: int,
    margin_y: int,
) -> dict[str, object]:
    width = frames * cell_width
    height = cell_height
    image = Image.new("RGB", (width, height), "#f7f7f7")
    draw = ImageDraw.Draw(image)

    for index in range(frames):
        left = index * cell_width
        right = left + cell_width - 1
        draw.rectangle((left, 0, right, height - 1), outline="#111111", width=2)

        safe_left = left + margin_x
        safe_top = margin_y
        safe_right = right - margin_x
        safe_bottom = height - 1 - margin_y
        draw.rectangle(
            (safe_left, safe_top, safe_right, safe_bottom),
            outline="#2f80ed",
            width=2,
        )

        center_x = left + cell_width // 2
        center_y = height // 2
        draw_dashed_line(
            draw,
            (center_x, safe_top),
            (center_x, safe_bottom),
            fill="#b8b8b8",
        )
        draw_dashed_line(
            draw,
            (safe_left, center_y),
            (safe_right, center_y),
            fill="#b8b8b8",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return {
        "state": state,
        "path": str(output),
        "frames": frames,
        "width": width,
        "height": height,
        "cell_width": cell_width,
        "cell_height": cell_height,
        "safe_margin_x": margin_x,
        "safe_margin_y": margin_y,
        "usage": "layout guide input only; use for slot count, centering, and safe padding; do not copy guide pixels",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--cell-width", type=int, default=256)
    parser.add_argument("--cell-height", type=int, default=256)
    parser.add_argument("--safe-margin-x", type=int, default=24)
    parser.add_argument("--safe-margin-y", type=int, default=24)
    parser.add_argument(
        "--state",
        action="append",
        type=parse_state,
        help="State frame count in NAME=FRAMES form. Defaults to idle=4, run=6, jump=4, talk=4.",
    )
    args = parser.parse_args()

    states = dict(args.state or DEFAULT_STATES.items())
    guides = [
        create_guide(
            args.out_dir / f"{state}.png",
            state=state,
            frames=frames,
            cell_width=args.cell_width,
            cell_height=args.cell_height,
            margin_x=args.safe_margin_x,
            margin_y=args.safe_margin_y,
        )
        for state, frames in states.items()
    ]

    manifest = {
        "cell_width": args.cell_width,
        "cell_height": args.cell_height,
        "safe_margin_x": args.safe_margin_x,
        "safe_margin_y": args.safe_margin_y,
        "guides": guides,
    }
    manifest_path = args.out_dir / "layout-guides.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={args.out_dir}")
    print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
