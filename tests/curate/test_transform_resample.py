# SPDX-License-Identifier: Apache-2.0
"""A curated move, scale, rotation or shear is baked without a ring at the frame's edge.

`apply_transform` sampled a smooth row's transform with BICUBIC over premultiplied RGBA, which
drew a lighter rim and a key tint around a keyed sprite that the sprite did not have; it maps
through `transform_cell` (coverage and colour apart). A pixel row (`snap_scale`) samples
NEAREST and is not touched. The tests fail with BICUBIC put back.
"""
from __future__ import annotations

import pytest
from PIL import Image

from keyed_figure import colour_outside_mapped, figure
from sprite_gen.curate.curation import apply_transform, normalize_transform, transform_matrix
from sprite_gen.util.resample import transform_cell

TRANSFORMS = {
    "move half a pixel": {"dx": 0.5, "dy": -0.5},
    "rotate": {"rotate": 10},
    "shrink": {"scale": 0.75},
    "shear": {"shx": 0.2, "shy": -0.1},
    "grow and rotate": {"scale": 1.3, "rotate": 15},
}


def inverse_of(transform: dict, src: Image.Image, cell: tuple[int, int]) -> tuple[float, ...]:
    """The output -> input map `apply_transform` builds (about the centres, then moved)."""
    t = normalize_transform(transform)
    m00, m01, m10, m11 = transform_matrix(t)
    det = m00 * m11 - m01 * m10
    ia, ib, id_, ie = m11 / det, -m01 / det, -m10 / det, m00 / det
    ox, oy = cell[0] / 2 + t["dx"], cell[1] / 2 + t["dy"]
    return (ia, ib, src.width / 2 - ia * ox - ib * oy, id_, ie, src.height / 2 - id_ * ox - ie * oy)


@pytest.mark.parametrize("name", list(TRANSFORMS))
def test_a_baked_transform_carries_no_colour_the_frame_did_not_have(name: str) -> None:
    src = figure()
    cell = src.size
    out = apply_transform(src, TRANSFORMS[name], cell)
    inverse = inverse_of(TRANSFORMS[name], src, cell)
    assert colour_outside_mapped(src, out, inverse) == 0
    assert out.tobytes() == transform_cell(src, cell, inverse).tobytes()
    bicubic = src.transform(cell, Image.Transform.AFFINE, inverse, resample=Image.Resampling.BICUBIC)
    assert colour_outside_mapped(src, bicubic, inverse) > 50


def test_a_pixel_row_still_samples_nearest() -> None:
    src = figure()
    out = apply_transform(src, {"rotate": 10}, src.size, snap_scale=2)
    colours = set(map(tuple, src.get_flattened_data()))
    assert set(map(tuple, out.get_flattened_data())) <= colours | {(0, 0, 0, 0)}
