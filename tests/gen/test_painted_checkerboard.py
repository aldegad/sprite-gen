# SPDX-License-Identifier: Apache-2.0
"""A painted transparency checkerboard is refused before it is keyed, with its own reason.

Asked for a key background, an image model sometimes paints the grey and white checkerboard an
editor shows behind transparent pixels instead. The matte reads its key off the borders, so it
reads the checkerboard as the background and keys nearly the whole image away; the result has no
subject left to cut. `chroma.painted_checkerboard` recognises the checkerboard along the image's
edges and `key_transparent` refuses it before keying, so a caller can generate again. A key
background, a plain light background, stripes, and a checkered subject inside a key background are
not a painted checkerboard. Every sheet here is synthetic.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen import gen
from sprite_gen.gen import base as gen_base
from sprite_gen.gen import chroma as chroma_mod
from sprite_gen.gen.base import GenRequest

FRAME = (110, 70, 40)
FACE = (20, 80, 90)
RED = (200, 30, 40)
MAGENTA = (255, 0, 255)


def checkerboard(size=(256, 384), square=12, levels=(254, 227), shift=(0, 0)):
    width, height = size
    yy, xx = np.indices((height, width))
    odd = ((xx + shift[0]) // square + (yy + shift[1]) // square) % 2 == 1
    grey = np.where(odd, levels[1], levels[0]).astype(np.uint8)
    return np.repeat(grey[..., None], 3, axis=-1)


def with_elements(rgb):
    """A few UI elements drawn on the background, away from its edges."""
    rgb = rgb.copy()
    rgb[40:120, 30:200] = FRAME
    rgb[50:110, 40:190] = FACE
    rgb[300:330, 30:200] = RED
    return rgb


def image(rgb):
    return Image.fromarray(np.ascontiguousarray(rgb), "RGB")


@pytest.mark.parametrize(("square", "levels", "shift"), [
    (12, (254, 227), (0, 0)),
    (8, (255, 204), (3, 5)),
    (32, (153, 102), (17, 9)),
])
def test_a_painted_checkerboard_is_recognised_along_the_edges(square, levels, shift):
    found = chroma_mod.painted_checkerboard(image(with_elements(checkerboard(square=square, levels=levels, shift=shift))))
    assert found == {"edges": ["top", "bottom", "left", "right"], "square_px": square}


def test_an_element_across_two_edges_leaves_the_checkerboard_recognised():
    rgb = with_elements(checkerboard())
    rgb[0:20, :] = FRAME  # along the whole top edge
    rgb[:, 0:20] = FACE  # down the whole left edge
    found = chroma_mod.painted_checkerboard(image(rgb))
    assert found == {"edges": ["bottom", "right"], "square_px": 12}


@pytest.mark.parametrize("background", [
    # a flat key leaves at the grey-share test whatever its hue: one key stands for both
    pytest.param(lambda: np.full((384, 256, 3), MAGENTA, dtype=np.uint8), id="magenta key"),
    pytest.param(lambda: np.full((384, 256, 3), 240, dtype=np.uint8), id="plain light grey"),
    pytest.param(lambda: np.repeat(np.linspace(200, 255, 256).astype(np.uint8)[None, :, None], 384, 0)
                 .repeat(3, -1), id="grey gradient"),
    # grey stripes alternate along one axis only; a checkerboard alternates along both
    pytest.param(lambda: np.where((np.indices((384, 256))[1] // 12 % 2 == 1)[..., None], 227, 254)
                 .astype(np.uint8).repeat(3, -1), id="vertical stripes"),
    pytest.param(lambda: np.where((np.indices((384, 256))[0] // 12 % 2 == 1)[..., None], 227, 254)
                 .astype(np.uint8).repeat(3, -1), id="horizontal stripes"),
])
def test_other_backgrounds_are_not_a_painted_checkerboard(background):
    assert chroma_mod.painted_checkerboard(image(with_elements(background()))) is None


def test_a_checkered_subject_on_a_key_background_is_keyed(tmp_path):
    rgb = np.full((384, 256, 3), MAGENTA, dtype=np.uint8)
    rgb[100:280, 40:220] = checkerboard(size=(180, 180), square=20)  # a chessboard, inside the key
    src = tmp_path / "board.png"
    image(rgb).save(src)
    assert chroma_mod.painted_checkerboard(Image.open(src)) is None
    stats = chroma_mod.key_transparent(src, tmp_path / "out.png", key="magenta")
    keyed = np.asarray(Image.open(tmp_path / "out.png").convert("RGBA"))
    assert keyed[0, 0, 3] == 0 and keyed[190, 130, 3] == 255
    assert 40 < stats["alpha_zero_pct"] < 80


def test_key_transparent_refuses_a_painted_checkerboard_before_keying(tmp_path, monkeypatch):
    src = tmp_path / "raw.png"
    image(with_elements(checkerboard())).save(src)
    out = tmp_path / "out.png"

    def no_matte(*args, **kwargs):
        raise AssertionError("a painted checkerboard must be refused before the matte runs")

    monkeypatch.setattr(chroma_mod, "remove_chroma_background_ycbcr", no_matte)
    with pytest.raises(SystemExit) as refused:
        chroma_mod.key_transparent(src, out, key="magenta")
    assert str(refused.value) == (
        "chroma: the background is a painted transparency checkerboard (12 px squares along the top, bottom, left "
        "and right edges), not the magenta key the image was asked for; keying it would erase the image — "
        "generate it again")
    assert not out.exists()


class _CheckerboardProvider:
    """A provider that answers the request for a key background with a painted checkerboard."""

    name = "fake"
    transparency = gen_base.TRANSPARENCY_NATIVE

    def generate(self, request: GenRequest, workdir: Path):
        image(with_elements(checkerboard())).save(request.raw)
        return gen_base.ProviderRun(provider=self.name, elapsed_seconds=0.1, model=request.model)


def test_a_generation_with_a_layout_guide_that_comes_back_on_a_checkerboard_is_refused(tmp_path, monkeypatch):
    # the layout guide is an attached image, so `auto` keys the result (`refs-attached`): the checkerboard is
    # refused with its own reason instead of being keyed into an empty sheet, and nothing is published
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: _CheckerboardProvider())
    out, report = tmp_path / "props.png", tmp_path / "report.json"
    with pytest.raises(SystemExit, match=r"^chroma: the background is a painted transparency checkerboard"):
        gen.run(provider="fake", prompt="A sheet of six wooden props.", out=out, report=report,
                transparent=True, layout_guide=True, aspect_ratio="2:3")
    assert not out.exists() and not report.exists()
