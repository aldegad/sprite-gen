#!/usr/bin/env python3
"""Center and pad an alpha sprite sheet without changing the 6x4 grid contract."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


CELL = 256


def repair_cell(cell: Image.Image, padding: int, alpha_threshold: int) -> Image.Image:
    cell = cell.convert("RGBA")
    alpha = cell.getchannel("A")
    mask = alpha.point(lambda value: 255 if value > alpha_threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))

    cropped = cell.crop(bbox)
    max_size = CELL - (padding * 2)
    width, height = cropped.size
    scale = min(1.0, max_size / float(max(width, height)))
    if scale < 1.0:
        cropped = cropped.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )

    repaired = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
    x = (CELL - cropped.width) // 2
    y = (CELL - cropped.height) // 2
    repaired.alpha_composite(cropped, (x, y))
    return repaired


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="input sprite-sheet-alpha.png")
    parser.add_argument("--output", required=True, help="repaired output path")
    parser.add_argument("--padding", type=int, default=14, help="minimum cell padding in pixels")
    parser.add_argument("--alpha-threshold", type=int, default=10)
    args = parser.parse_args()

    source_path = Path(args.image)
    output_path = Path(args.output)
    with Image.open(source_path) as source:
        image = source.convert("RGBA")

    if image.size[0] % CELL != 0 or image.size[1] % CELL != 0:
        raise SystemExit(f"image size must be a multiple of {CELL}: {image.size}")

    repaired = Image.new("RGBA", image.size, (0, 0, 0, 0))
    columns = image.size[0] // CELL
    rows = image.size[1] // CELL
    for row in range(rows):
        for column in range(columns):
            box = (column * CELL, row * CELL, (column + 1) * CELL, (row + 1) * CELL)
            repaired_cell = repair_cell(image.crop(box), args.padding, args.alpha_threshold)
            repaired.alpha_composite(repaired_cell, (column * CELL, row * CELL))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    repaired.save(output_path)
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
