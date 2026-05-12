#!/usr/bin/env python3
"""Hard-key generated magenta sprite backgrounds for live demos."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def is_key_pixel(r: int, g: int, b: int, args: argparse.Namespace) -> bool:
    return (
        r >= args.min_red
        and b >= args.min_blue
        and g <= args.max_green
        and (r - g) >= args.min_red_delta
        and (b - g) >= args.min_blue_delta
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove generated magenta backgrounds without soft matte despill."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--min-red", type=int, default=180)
    parser.add_argument("--min-blue", type=int, default=145)
    parser.add_argument("--max-green", type=int, default=95)
    parser.add_argument("--min-red-delta", type=int, default=110)
    parser.add_argument("--min-blue-delta", type=int, default=80)
    args = parser.parse_args()

    image = Image.open(args.input).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    transparent = 0

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a and is_key_pixel(r, g, b, args):
                pixels[x, y] = (r, g, b, 0)
                transparent += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(f"wrote={args.out}")
    print(f"size={width}x{height}")
    print(f"transparent_pct={transparent / (width * height) * 100:.2f}")


if __name__ == "__main__":
    main()
