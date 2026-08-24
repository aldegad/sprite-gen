# SPDX-License-Identifier: Apache-2.0
"""Deterministic chroma-key -> transparent PNG contract for generated images.

`image_gen` / Grok Imagine alpha output is not reliable, so generated key
backgrounds are routed through the same YCbCr matte used by frame extraction.
No silent success: an output with no measurable transparent area, or transparent
pixels that retain RGB, fails before any output is published.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.frames.extract import remove_chroma_background_ycbcr
from sprite_gen.spec.runio import atomic_save_image


KEYS: dict[str, dict[str, tuple[int, int, int]]] = {
    "magenta": {"target": (255, 0, 255)},
    "green": {"target": (0, 255, 0)},
}


def write_white_check(image: Image.Image, path: Path) -> None:
    bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
    bg.alpha_composite(image)
    bg.convert("RGB").save(path)


def key_transparent(
    input_path: Path,
    out_path: Path,
    *,
    key: str = "magenta",
    white_check: Path | None = None,
) -> dict[str, Any]:
    """Key a chroma-background PNG to a clean transparent RGBA PNG.

    Returns a stats dict (keyed/fringe/cleaned pixel counts, alpha_zero_pct).
    Raises SystemExit before publishing if no measurable transparent area was
    made, or if a transparent pixel keeps non-zero RGB.
    """
    if key not in KEYS:
        raise SystemExit(f"chroma: unknown key {key!r}; expected one of {sorted(KEYS)}")
    source = Image.open(input_path).convert("RGBA")
    source_pixels = source.load()
    warnings: list[str] = []
    image = remove_chroma_background_ycbcr(source, KEYS[key]["target"], warnings)
    pixels = image.load()
    width, height = image.size
    total = width * height
    alpha_zero = stale_rgb = keyed = fringe = cleaned_rgb = 0
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            source_a = source_pixels[x, y][3]
            if a == 0:
                alpha_zero += 1
                if source_a > 0:
                    keyed += 1
                if r or g or b:
                    pixels[x, y] = (0, 0, 0, 0)
                    cleaned_rgb += 1
            elif source_a > a:
                fringe += 1

    for r, g, b, a in image.getdata():
        if a == 0 and (r or g or b):
            stale_rgb += 1

    alpha_zero_pct = round(alpha_zero / total * 100, 2) if total else 0.0

    stats: dict[str, Any] = {
        "out": str(out_path),
        "mode": "RGBA",
        "method": "ycbcr",
        "size": f"{width}x{height}",
        "key": key,
        "keyed_pixels": keyed,
        "fringe_pixels": fringe,
        "cleaned_transparent_rgb_pixels": cleaned_rgb,
        "alpha_zero_pct": alpha_zero_pct,
        "stale_transparent_rgb_pixels": stale_rgb,
    }
    if warnings:
        stats["warnings"] = warnings
    if white_check is not None:
        stats["white_check"] = str(white_check)
    if alpha_zero_pct == 0.0:
        raise SystemExit(
            f"chroma: generated 0.0% transparent pixels for {key} key; refusing successful transparent output"
        )
    if stale_rgb:
        raise SystemExit(f"chroma: transparent pixels still contain non-zero RGB ({stale_rgb} px) in {out_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_save_image(image, out_path)
    if white_check is not None:
        white_check.parent.mkdir(parents=True, exist_ok=True)
        write_white_check(image, white_check)
    return stats
