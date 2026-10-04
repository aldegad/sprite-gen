# SPDX-License-Identifier: Apache-2.0
"""The gait fallback scales a frame back without a ring at its edge.

`undo_scale` used BICUBIC over premultiplied RGBA, which drew a lighter rim and a key tint
around the keyed subject that the frame did not have; it maps through `transform_cell`
(coverage and colour apart). The tests fail with BICUBIC put back.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from keyed_figure import colour_outside_mapped, figure
from sprite_gen.util.resample import transform_cell
from sprite_gen.video import gait_fallback

FOOT = (80.0, 170.0)


def scaled_back(scale: float) -> tuple[Image.Image, Image.Image, tuple[float, ...]]:
    """A second frame `1 / scale` the first's height, scaled back about the foot point."""
    src = figure()
    measured = {"height": np.array([100.0, 100.0 / scale]), "foot_x": np.full(2, FOOT[0]), "foot_y": np.full(2, FOOT[1])}
    first, second = gait_fallback.undo_scale([src, src], measured)
    assert first.tobytes() == src.tobytes()  # the first frame is the reference: not resampled
    inverse = (1 / scale, 0.0, FOOT[0] * (1 - 1 / scale), 0.0, 1 / scale, FOOT[1] * (1 - 1 / scale))
    return src, second, inverse


@pytest.mark.parametrize("scale", (0.84, 0.95, 1.05, 1.19))
def test_a_scaled_back_frame_carries_no_colour_it_did_not_have(scale: float) -> None:
    src, out, inverse = scaled_back(scale)
    assert colour_outside_mapped(src, out, inverse) == 0
    assert out.tobytes() == transform_cell(src, src.size, inverse).tobytes()
    bicubic = src.convert("RGBa").transform(src.size, Image.Transform.AFFINE, inverse, resample=Image.Resampling.BICUBIC)
    assert colour_outside_mapped(src, bicubic.convert("RGBA"), inverse) > 100
