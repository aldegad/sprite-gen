# SPDX-License-Identifier: Apache-2.0
"""A keyed picture is scaled without a ring wherever `extract` and `slice-sheet` scale one.

`fit_to_cell` (a row frame shrunk into its cell, `fit.resample: lanczos`), `fit_component_to_bbox`
(the original-quality twin beside a pixel-unfake frame) and `slice_sheet` (a sheet's figures
brought to one height) used LANCZOS over premultiplied RGBA, which drew a lighter rim and a
key tint at the edge that the keyed picture did not have. They scale through `resize_cell`
(`tests/video/test_strip_resample.py` holds its own properties). Each test fails with LANCZOS
put back.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from keyed_figure import colour_outside, figure
from sprite_gen.frames import extract, slice_sheet
from sprite_gen.util.resample import resize_cell

GREEN = (0, 255, 0)


def _shrunk_into(cell: int, margin: int, sprite: Image.Image) -> tuple[int, int]:
    scale = min((cell - 2 * margin) / sprite.width, (cell - 2 * margin) / sprite.height, 1.0)
    return max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))


@pytest.mark.parametrize("cell", (64, 96, 150))
def test_a_frame_fitted_to_its_cell_carries_no_colour_the_frame_did_not_have(cell: int) -> None:
    src = figure()
    sprite = src.crop(src.getbbox())
    size = _shrunk_into(cell, 4, sprite)
    assert size != sprite.size
    fitted = extract.fit_to_cell(src, cell, cell, 4, 4, {"align_x": "bbox-center"})
    want = resize_cell(sprite, size)
    assert colour_outside(sprite, want) == 0
    assert fitted.crop(fitted.getbbox()).tobytes() == want.crop(want.getbbox()).tobytes()
    # what the cell held before: colour nothing under it had, so not these bytes
    old = sprite.resize(size, Image.Resampling.LANCZOS)
    assert colour_outside(sprite, old) > 50
    assert fitted.crop(fitted.getbbox()).tobytes() != old.crop(old.getbbox()).tobytes()


def test_the_other_fit_filters_are_left_as_they_were(monkeypatch) -> None:
    def never(*_args, **_kwargs):
        raise AssertionError("nearest and kcentroid do not go through resize_cell")

    monkeypatch.setattr(extract, "resize_cell", never)
    src = figure()
    sprite = src.crop(src.getbbox())
    size = _shrunk_into(96, 4, sprite)
    nearest = extract.fit_to_cell(src, 96, 96, 4, 4, {"resample": "nearest", "align_x": "bbox-center"})
    want = sprite.resize(size, Image.Resampling.NEAREST)
    assert nearest.crop(nearest.getbbox()).tobytes() == want.crop(want.getbbox()).tobytes()
    assert extract.fit_to_cell(src, 96, 96, 4, 4, {"resample": "kcentroid"}).getbbox() is not None


@pytest.mark.parametrize("box_height", (60, 172, 240))  # the twin shrinks, nearly keeps its size, grows
def test_the_twin_beside_a_pixel_frame_carries_no_colour_the_component_did_not_have(box_height: int) -> None:
    src = figure()
    sprite = src.crop(src.getbbox())
    cell = 256
    bbox = (40, cell - 8 - box_height, 216, cell - 8)
    twin, mapping = extract.fit_component_to_bbox(src, cell, cell, bbox)
    ratio = min((bbox[2] - bbox[0]) / sprite.width, (bbox[3] - bbox[1]) / sprite.height)
    size = (max(1, round(sprite.width * ratio)), max(1, round(sprite.height * ratio)))
    assert size != sprite.size and mapping["ratio"] == ratio
    left, top = int(mapping["left"]), int(mapping["top"])
    placed = twin.crop((left, top, left + size[0], top + size[1]))
    assert colour_outside(sprite, placed) == 0
    assert placed.tobytes() == resize_cell(sprite, size).tobytes()
    assert colour_outside(sprite, sprite.resize(size, Image.Resampling.LANCZOS)) > 50


# the main component (the body) is 96-97 px tall on the sheet: the figures shrink, nearly keep their size, grow
@pytest.mark.parametrize("target_height", (72.0, 99.0, 130.0))
def test_a_sliced_figure_carries_no_colour_its_keyed_piece_did_not_have(tmp_path: Path, monkeypatch, target_height: float) -> None:
    fig = figure()
    sheet = Image.new("RGBA", (2 * fig.width, fig.height), GREEN + (255,))
    sheet.alpha_composite(fig, (0, 0))
    sheet.alpha_composite(figure(shift=0.5), (fig.width, 0))
    sheet.convert("RGB").save(tmp_path / "sheet.png")
    pieces: list[tuple[Image.Image, Image.Image]] = []

    def spy(image: Image.Image, size: tuple[int, int]) -> Image.Image:
        out = resize_cell(image, size)
        pieces.append((image.copy(), out))
        return out

    monkeypatch.setattr(slice_sheet, "resize_cell", spy)
    written = slice_sheet.slice_sheet(tmp_path / "sheet.png", tmp_path / "out", GREEN, grid=(2, 1), canvas=(256, 256),
                                      baseline_y=240.0, target_height=target_height)
    assert len(written) == len(pieces) == 2
    for path, (piece, scaled) in zip(written, pieces):
        assert scaled.size != piece.size
        assert colour_outside(piece, scaled) == 0
        cell = Image.open(path)
        assert cell.crop(cell.getbbox()).tobytes() == scaled.crop(scaled.getbbox()).tobytes()
        # the keyed piece under LANCZOS: colour nothing under it had
        assert colour_outside(piece, piece.resize(scaled.size, Image.Resampling.LANCZOS)) > 50
