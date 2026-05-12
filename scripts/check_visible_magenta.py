#!/usr/bin/env python3
"""Fail a browser/game screenshot when visible chroma-key magenta remains."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def is_visible_magenta(r: int, g: int, b: int, a: int, args: argparse.Namespace) -> bool:
    if a < args.min_alpha:
        return False
    return (
        r >= args.min_red
        and b >= args.min_blue
        and g <= args.max_green
        and (r - g) >= args.min_red_delta
        and (b - g) >= args.min_blue_delta
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check Playwright/browser screenshots for visible #FF00FF sprite key color."
    )
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--max-pixels", type=int, default=64)
    parser.add_argument("--max-ratio", type=float, default=0.00005)
    parser.add_argument("--min-alpha", type=int, default=128)
    parser.add_argument("--min-red", type=int, default=230)
    parser.add_argument("--min-blue", type=int, default=230)
    parser.add_argument("--max-green", type=int, default=80)
    parser.add_argument("--min-red-delta", type=int, default=150)
    parser.add_argument("--min-blue-delta", type=int, default=150)
    args = parser.parse_args()

    image = Image.open(args.image).convert("RGBA")
    width, height = image.size
    pixels = image.load()
    count = 0
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if is_visible_magenta(r, g, b, a, args):
                count += 1
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    total = width * height
    ratio = count / total if total else 0
    bbox = "none" if count == 0 else f"{min_x},{min_y},{max_x},{max_y}"
    failed = count > args.max_pixels or ratio > args.max_ratio

    print(f"image={args.image}")
    print(f"size={width}x{height}")
    print(f"visible_magenta_pixels={count}")
    print(f"visible_magenta_ratio={ratio:.8f}")
    print(f"visible_magenta_bbox={bbox}")
    print(f"threshold_pixels={args.max_pixels}")
    print(f"threshold_ratio={args.max_ratio:.8f}")
    print("status=fail" if failed else "status=pass")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
