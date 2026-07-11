# SPDX-License-Identifier: Apache-2.0
"""Deterministic chroma-key -> transparent PNG contract for generated images.

Ported verbatim in behaviour from the standalone `image-gen` skill helper
(`scripts/chroma_key_transparent.py`, MIT, aldegad/image-gen). `image_gen` /
Grok Imagine alpha output is not reliable, so the canonical transparent contract
is: generate on a solid `#FF00FF` magenta or `#00FF00` green background, then key
it out here. Removes the key colour, clears RGB for fully-transparent pixels, and
neutralises semi-transparent chroma fringe. No silent success — transparent
pixels that still carry non-zero RGB raise loudly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

KEYS: dict[str, dict[str, int | tuple[int, int, int]]] = {
    "magenta": {"target": (255, 0, 255), "min_primary": 170, "max_opposite": 115, "min_delta": 70},
    "green": {"target": (0, 255, 0), "min_primary": 170, "max_opposite": 115, "min_delta": 70},
}


def is_key_pixel(r: int, g: int, b: int, key: str, min_primary: int, max_opposite: int, min_delta: int) -> bool:
    if key == "magenta":
        return r >= min_primary and b >= min_primary and g <= max_opposite and (r - g) >= min_delta and (b - g) >= min_delta
    return g >= min_primary and r <= max_opposite and b <= max_opposite and (g - r) >= min_delta and (g - b) >= min_delta


def is_fringe_pixel(r: int, g: int, b: int, key: str, threshold: int) -> bool:
    if key == "magenta":
        return (r - g) > threshold and (b - g) > threshold
    return (g - r) > threshold and (g - b) > threshold


def neutralize_fringe(r: int, g: int, b: int, a: int, key: str) -> tuple[int, int, int, int]:
    neutral = g if key == "magenta" else max(r, b)
    return (neutral, neutral, neutral, max(0, a // 3))


def write_white_check(image: Image.Image, path: Path) -> None:
    bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
    bg.alpha_composite(image)
    bg.convert("RGB").save(path)


def key_transparent(
    input_path: Path,
    out_path: Path,
    *,
    key: str = "magenta",
    min_primary: int | None = None,
    max_opposite: int | None = None,
    min_delta: int | None = None,
    fringe_threshold: int = 4,
    fringe_alpha_max: int = 239,
    fringe_cleanup: bool = True,
    white_check: Path | None = None,
) -> dict[str, Any]:
    """Key a solid-chroma-background PNG to a clean transparent RGBA PNG.

    Returns a stats dict (keyed/fringe/cleaned pixel counts, alpha_zero_pct).
    Raises SystemExit if any transparent pixel keeps non-zero RGB (No Silent
    Fallback: a "transparent" result that still leaks the key colour is a defect).
    """
    if key not in KEYS:
        raise SystemExit(f"chroma: unknown key {key!r}; expected one of {sorted(KEYS)}")
    defaults = KEYS[key]
    min_primary = int(defaults["min_primary"]) if min_primary is None else min_primary
    max_opposite = int(defaults["max_opposite"]) if max_opposite is None else max_opposite
    min_delta = int(defaults["min_delta"]) if min_delta is None else min_delta

    image = Image.open(input_path).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    keyed = fringe = cleaned_rgb = 0

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a and is_key_pixel(r, g, b, key, min_primary, max_opposite, min_delta):
                pixels[x, y] = (0, 0, 0, 0)
                keyed += 1
                continue
            if a == 0:
                if r or g or b:
                    pixels[x, y] = (0, 0, 0, 0)
                    cleaned_rgb += 1
                continue
            if fringe_cleanup and 0 < a <= fringe_alpha_max and is_fringe_pixel(r, g, b, key, fringe_threshold):
                pixels[x, y] = neutralize_fringe(r, g, b, a, key)
                fringe += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)

    if white_check is not None:
        white_check.parent.mkdir(parents=True, exist_ok=True)
        write_white_check(image, white_check)

    total = width * height
    alpha_zero = stale_rgb = 0
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                alpha_zero += 1
                if r or g or b:
                    stale_rgb += 1

    stats: dict[str, Any] = {
        "out": str(out_path),
        "mode": "RGBA",
        "size": f"{width}x{height}",
        "key": key,
        "keyed_pixels": keyed,
        "fringe_pixels": fringe,
        "cleaned_transparent_rgb_pixels": cleaned_rgb,
        "alpha_zero_pct": round(alpha_zero / total * 100, 2) if total else 0.0,
        "stale_transparent_rgb_pixels": stale_rgb,
    }
    if white_check is not None:
        stats["white_check"] = str(white_check)
    if stale_rgb:
        raise SystemExit(f"chroma: transparent pixels still contain non-zero RGB ({stale_rgb} px) in {out_path}")
    return stats
