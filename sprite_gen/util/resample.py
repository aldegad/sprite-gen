# SPDX-License-Identifier: Apache-2.0
"""The one resample for an RGBA cutout: coverage and colour scaled apart.

The places that scaled a keyed picture with LANCZOS go through `resize_cell`: a loop's cells
(`video-loop`), a row frame fitted to its cell and the twins beside a
pixel-unfake frame (`extract`), a sliced sheet's figures (`slice-sheet`) and a scene's layers
(`scene`). The places that mapped one with a BICUBIC affine go through `transform_cell`: the
gait fallback's scaled-back frames (`video-loop`) and a curated transform on a smooth row
(`curation.apply_transform`). docs/video-pipeline.md "Cells".
"""
from __future__ import annotations

from PIL import Image

from sprite_gen._deps import np


# How far `resize_cell`'s colour filter (Hamming) reaches, in source pixels at 1:1.
MIX_SUPPORT = 1.0


def support_bounds(n_src: int, n_out: int, support: float) -> tuple[np.ndarray, np.ndarray]:
    """First and last source index Pillow's resample reads for each output index, for a filter
    of `support` source pixels (`precompute_coeffs` in Pillow's Resample.c: the support widens
    by the scale when shrinking)."""
    scale = n_src / n_out
    reach = support * max(1.0, scale)
    centre = (np.arange(n_out) + 0.5) * scale
    lo = np.clip(np.floor(centre - reach + 0.5).astype(int), 0, n_src - 1)
    hi = np.clip(np.floor(centre + reach + 0.5).astype(int) - 1, 0, n_src - 1)
    return lo, np.maximum(lo, hi)


def mix_windows(src_size: tuple[int, int], size: tuple[int, int]) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """The source pixels `resize_cell` mixes each output pixel's colour from: the first and last
    source row for each output row, then the same for the columns. `resize_cell` holds its
    coverage to these windows; a caller that has to know what a cell's pixel was made of reads
    the same ones (a cell at its own size is a copy: its window still holds its own pixel)."""
    return support_bounds(src_size[1], size[1], MIX_SUPPORT), support_bounds(src_size[0], size[0], MIX_SUPPORT)


def window_extrema(a: np.ndarray, rows: tuple[np.ndarray, np.ndarray], cols: tuple[np.ndarray, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
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
    colours under it and never one they did not have. Each channel therefore stays within the
    colours under it. The key hue's excess (G - max(R, B) for green) does not: a colour led by
    red mixed with one led by blue is led by neither as far, so the mix can carry more excess
    than both. The cell is left as mixed; nothing here knows the key. docs/video-pipeline.md
    "Cells"."""
    if image.size == tuple(size):
        return image.copy()
    w, h = size
    src = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = src[..., 3]

    def scaled(channel: np.ndarray, resample: Image.Resampling) -> np.ndarray:
        return np.asarray(Image.fromarray(np.ascontiguousarray(channel)).resize(size, resample), dtype=np.float32)

    mix_alpha = scaled(alpha, Image.Resampling.HAMMING)
    mix = np.stack([scaled(src[..., c] * alpha, Image.Resampling.HAMMING) for c in range(3)], axis=-1)
    low, high = window_extrema(alpha, *mix_windows(image.size, size))
    cover = np.clip(scaled(alpha, Image.Resampling.LANCZOS), low, high)
    cover = np.where(mix_alpha > 1e-3, np.round(cover), 0)
    colour = np.where((cover > 0)[..., None], np.round(mix / np.maximum(mix_alpha, 1e-3)[..., None]), 0)
    out = np.dstack([np.clip(colour, 0, 255), np.clip(cover, 0, 255)]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def transform_cell(image: Image.Image, size: tuple[int, int], inverse: tuple[float, ...]) -> Image.Image:
    """`resize_cell` for an affine map: one RGBA cell moved, scaled, rotated or sheared into `size`.

    `inverse` is Pillow's AFFINE data, the output -> input map. BICUBIC over premultiplied RGBA
    rings the way LANCZOS does (a smaller negative lobe, the same division by low coverage at
    the edge), so coverage and colour are mapped apart here too. Coverage keeps BICUBIC's edge,
    held to the range of the 2 x 2 source pixels the colour mixes from; colour is a BILINEAR mix
    of premultiplied colour, a filter with no negative lobe. docs/video-pipeline.md "Cells"."""
    w, h = size
    src = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = src[..., 3]
    a, b, c, d, e, f = inverse

    def mapped(channel: np.ndarray, resample: Image.Resampling, dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
        layer = Image.fromarray(np.ascontiguousarray(channel), "F")
        return np.asarray(layer.transform(size, Image.Transform.AFFINE, (a, b, c + dx, d, e, f + dy), resample=resample),
                          dtype=np.float32)

    mix_alpha = mapped(alpha, Image.Resampling.BILINEAR)
    mix = np.stack([mapped(src[..., k] * alpha, Image.Resampling.BILINEAR) for k in range(3)], axis=-1)
    # The four source pixels BILINEAR reads sit half a pixel either side of the mapped centre;
    # NEAREST at those four offsets reads exactly them (nothing outside the source: alpha 0).
    corners = [mapped(alpha, Image.Resampling.NEAREST, dx, dy) for dx in (-0.5, 0.5) for dy in (-0.5, 0.5)]
    low, high = np.minimum.reduce(corners), np.maximum.reduce(corners)
    cover = np.clip(mapped(alpha, Image.Resampling.BICUBIC), low, high)
    cover = np.where(mix_alpha > 1e-3, np.round(cover), 0)
    colour = np.where((cover > 0)[..., None], np.round(mix / np.maximum(mix_alpha, 1e-3)[..., None]), 0)
    out = np.dstack([np.clip(colour, 0, 255), np.clip(cover, 0, 255)]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")
