#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared GIF helpers for sprite-gen.

GIF previews must not leak the previous frame through transparent pixels. Use a
fresh transparent palette index per frame and GIF disposal method 2, so each
frame clears before the next one is drawn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageSequence


def delay_ticks_to_duration_ms(delay_ticks: int) -> int:
    """Convert ImageMagick/GIF delay ticks (1/100 sec) to PIL milliseconds."""
    if delay_ticks <= 0:
        raise ValueError("delay_ticks must be positive")
    return delay_ticks * 10


def _prepare_transparent_frame(frame: Image.Image, alpha_threshold: int) -> Image.Image:
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")

    # Keep invisible RGB out of the adaptive palette; otherwise transparent
    # source pixels can steal palette entries and produce colored fringe.
    rgb = Image.new("RGB", rgba.size, (0, 0, 0))
    rgb.paste(rgba.convert("RGB"), mask=alpha)

    paletted = rgb.convert("P", palette=Image.ADAPTIVE, colors=255)
    palette = paletted.getpalette() or []
    if len(palette) < 768:
        palette.extend([0] * (768 - len(palette)))
    palette[255 * 3 : 255 * 3 + 3] = [0, 0, 0]
    paletted.putpalette(palette)

    transparent_mask = Image.eval(alpha, lambda value: 255 if value <= alpha_threshold else 0)
    paletted.paste(255, transparent_mask)
    paletted.info["transparency"] = 255
    paletted.info["disposal"] = 2
    return paletted


def save_clean_gif(
    frames: Iterable[Image.Image],
    output_path: Path,
    *,
    duration_ms: int,
    loop: int = 0,
    alpha_threshold: int = 8,
) -> None:
    """Save RGBA frames as a clean transparent GIF.

    `loop=0` means infinite loop in GIF/Pillow terminology.
    """
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    prepared = [_prepare_transparent_frame(frame, alpha_threshold) for frame in frames]
    if not prepared:
        raise ValueError("at least one frame is required")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prepared[0].save(
        output_path,
        save_all=True,
        append_images=prepared[1:],
        duration=duration_ms,
        loop=loop,
        disposal=2,
        transparency=255,
    )


def gif_report(path: Path) -> dict[str, object]:
    """Return frame count, delay list, loop, transparency, and disposal values."""
    with Image.open(path) as image:
        frames = list(ImageSequence.Iterator(image))
        delays = [int(frame.info.get("duration", 0) / 10) for frame in frames]
        alpha_extrema = [frame.convert("RGBA").getchannel("A").getextrema() for frame in frames]
        disposal = [getattr(frame, "disposal_method", None) for frame in frames]
        return {
            "path": str(path),
            "frames": len(frames),
            "delay_ticks": delays,
            "loop": image.info.get("loop", 0),
            "transparent": all(min_alpha < 255 for min_alpha, _max_alpha in alpha_extrema),
            "disposal": disposal,
        }
