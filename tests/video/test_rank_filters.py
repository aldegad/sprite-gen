# SPDX-License-Identifier: Apache-2.0
"""The square minimum and maximum the made-frame readings take (`rife._mask`, `rife._local_range`) are PIL's MinFilter and
MaxFilter, pixel for pixel, the edge pixels repeated outward as PIL repeats them: read one axis at a time instead of PIL
ranking every square."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageFilter

from sprite_gen.video import rife


def _pil_mask(mask: np.ndarray, size: int, op: type) -> np.ndarray:
    return np.asarray(Image.fromarray(np.uint8(mask) * 255).filter(op(size))) > 0


def _pil_local_range(x: np.ndarray, size: int) -> np.ndarray:
    out = np.zeros(x.shape[:2], dtype=np.float32)
    for c in range(x.shape[2]):
        channel = Image.fromarray(np.uint8(np.clip(x[..., c], 0, 1) * 255 + 0.5), "L")
        hi = np.asarray(channel.filter(ImageFilter.MaxFilter(size)), dtype=np.float32)
        lo = np.asarray(channel.filter(ImageFilter.MinFilter(size)), dtype=np.float32)
        out = np.maximum(out, (hi - lo) / 255.0)
    return out


@pytest.mark.parametrize("op", [ImageFilter.MinFilter, ImageFilter.MaxFilter])
@pytest.mark.parametrize("size", [3, 5, 7])
def test_a_mask_filtered_square_by_square_is_pils(op, size):
    rng = np.random.default_rng(size)
    for h, w, share in ((1, 1, 0.5), (2, 9, 0.5), (11, 4, 0.2), (40, 33, 0.05), (40, 33, 0.5), (40, 33, 0.95)):
        mask = rng.random((h, w)) < share
        mask[:, 0] = mask[0, :] = True  # pixels on the border, repeated outward
        assert np.array_equal(rife._mask(mask, size, op), _pil_mask(mask, size, op)), (h, w, share)


def test_a_filter_size_pil_refuses_is_refused():
    with pytest.raises(ValueError):
        rife._mask(np.ones((8, 8), bool), 4, ImageFilter.MinFilter)


def test_the_local_range_is_pils():
    rng = np.random.default_rng(7)
    for h, w in ((3, 3), (17, 40), (64, 48)):
        x = rng.random((h, w, 4)).astype(np.float32)
        x[: h // 2, : w // 2] = 0  # flat ground beside the noise
        assert np.array_equal(rife._local_range(x), _pil_local_range(x, rife.CROSSFADE_RANGE)), (h, w)
