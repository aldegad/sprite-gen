# SPDX-License-Identifier: Apache-2.0
"""Transparent-PNG contracts for generated images — one per strategy.

- `key_transparent` (strategy `chroma`): the model cannot return alpha, so the
  generated key background is routed through the same YCbCr matte used by frame
  extraction.
- `verify_native_alpha` (strategy `native`): the model returned its own alpha
  channel (codex `image_gen`, 2026-09-08); the raw alpha is measured, transparent
  pixels are scrubbed of stale RGB, and the stats are published.

No silent success on either path: an output with no measurable transparent area,
no alpha channel at all, or transparent pixels that retain RGB fails before any
output is published.
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


def verify_native_alpha(
    input_path: Path,
    out_path: Path,
    *,
    white_check: Path | None = None,
) -> dict[str, Any]:
    """Publish a provider-returned RGBA PNG after measuring that its alpha is real.

    Returns a stats dict (alpha_zero_pct / partial_alpha_pct / opaque_pct and the
    transparent-RGB scrub count). Raises SystemExit before publishing when the PNG
    has no alpha band or no transparent pixel at all — a drawn checkerboard or a
    flat background is an RGB image, and no post-process can recover alpha from it.
    Partial alpha (1..254) is left exactly as the model produced it and only
    reported (2026-09-08 실측: 본체 알파 ≈253).
    """
    source = Image.open(input_path)
    if "A" not in source.getbands():
        raise SystemExit(
            f"native-alpha: provider returned mode={source.mode!r} with no alpha channel for "
            f"{input_path} — the transparent background was drawn, not generated; refusing "
            "to publish a transparent output"
        )
    image = source.convert("RGBA")
    width, height = image.size
    total = width * height
    histogram = image.getchannel("A").histogram()
    alpha_zero = histogram[0]
    opaque = histogram[255]
    partial = total - alpha_zero - opaque
    alpha_zero_pct = round(alpha_zero / total * 100, 2) if total else 0.0
    if alpha_zero_pct == 0.0:
        raise SystemExit(
            f"native-alpha: 0.0% transparent pixels in {input_path} (alpha band present but "
            "nothing is transparent); refusing successful transparent output"
        )

    pixels = image.load()
    cleaned_rgb = 0
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0 and (r or g or b):
                pixels[x, y] = (0, 0, 0, 0)
                cleaned_rgb += 1

    stats: dict[str, Any] = {
        "out": str(out_path),
        "mode": "RGBA",
        "method": "native",
        "size": f"{width}x{height}",
        "alpha_zero_pct": alpha_zero_pct,
        "partial_alpha_pct": round(partial / total * 100, 2) if total else 0.0,
        "opaque_pct": round(opaque / total * 100, 2) if total else 0.0,
        "cleaned_transparent_rgb_pixels": cleaned_rgb,
        "stale_transparent_rgb_pixels": 0,
    }
    if white_check is not None:
        stats["white_check"] = str(white_check)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_save_image(image, out_path)
    if white_check is not None:
        white_check.parent.mkdir(parents=True, exist_ok=True)
        write_white_check(image, white_check)
    return stats
