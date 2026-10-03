# SPDX-License-Identifier: Apache-2.0
"""The one resample for an RGBA cutout: coverage and colour scaled apart.

The places that scaled a keyed picture with LANCZOS go through `resize_cell`: a loop's cells
(`video-loop`), a row frame fitted to its cell and the twins beside a
pixel-unfake frame (`extract`), a sliced sheet's figures (`slice-sheet`) and a scene's layers
(`scene`). docs/video-pipeline.md "Cells".
"""
from __future__ import annotations

from PIL import Image

from sprite_gen._deps import np


def _support_bounds(n_src: int, n_out: int, support: float) -> tuple[np.ndarray, np.ndarray]:
    """First and last source index Pillow's resample reads for each output index, for a filter
    of `support` source pixels (`precompute_coeffs` in Pillow's Resample.c: the support widens
    by the scale when shrinking)."""
    scale = n_src / n_out
    reach = support * max(1.0, scale)
    centre = (np.arange(n_out) + 0.5) * scale
    lo = np.clip(np.floor(centre - reach + 0.5).astype(int), 0, n_src - 1)
    hi = np.clip(np.floor(centre + reach + 0.5).astype(int) - 1, 0, n_src - 1)
    return lo, np.maximum(lo, hi)


def _window_extrema(a: np.ndarray, rows: tuple[np.ndarray, np.ndarray], cols: tuple[np.ndarray, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Min and max of `a` over each output pixel's source window (separable, rows then columns)."""
    def along(v: np.ndarray, lo: np.ndarray, hi: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
        low = high = np.take(v, lo, axis=axis)
        for d in range(1, int((hi - lo).max()) + 1):
            step = np.take(v, np.minimum(lo + d, hi), axis=axis)
            low, high = np.minimum(low, step), np.maximum(high, step)
        return low, high

    lo_c, hi_c = along(a, *cols, axis=1)
    return along(lo_c, *rows, axis=0)[0], along(hi_c, *rows, axis=0)[1]


def resize_cell(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale one RGBA cell with its coverage and its colour taken apart.

    LANCZOS over premultiplied RGBA (Pillow's RGBa) rings: its negative lobes weigh the colours
    across an edge against each other, and dividing by the low coverage at the edge throws the
    result past every colour the source had there — a light rim around a dark outline, a key
    tint where the ink was only a little green. So the two are resampled apart. Coverage keeps
    LANCZOS's crisp edge, held to the range of the source coverage the colour mixes from (no
    halo outside the silhouette, no hole inside it). Colour is a Hamming mix of premultiplied
    colour, a filter with no negative lobe: every pixel's colour is a weighted mix of the
    colours under it and never one they did not have. docs/video-pipeline.md "Cells"."""
    if image.size == tuple(size):
        return image.copy()
    w, h = size
    src = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = src[..., 3]

    def scaled(channel: np.ndarray, resample: Image.Resampling) -> np.ndarray:
        return np.asarray(Image.fromarray(np.ascontiguousarray(channel)).resize(size, resample), dtype=np.float32)

    mix_alpha = scaled(alpha, Image.Resampling.HAMMING)
    mix = np.stack([scaled(src[..., c] * alpha, Image.Resampling.HAMMING) for c in range(3)], axis=-1)
    low, high = _window_extrema(alpha, _support_bounds(image.height, h, 1.0), _support_bounds(image.width, w, 1.0))
    cover = np.clip(scaled(alpha, Image.Resampling.LANCZOS), low, high)
    cover = np.where(mix_alpha > 1e-3, np.round(cover), 0)
    colour = np.where((cover > 0)[..., None], np.round(mix / np.maximum(mix_alpha, 1e-3)[..., None]), 0)
    out = np.dstack([np.clip(colour, 0, 255), np.clip(cover, 0, 255)]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")
