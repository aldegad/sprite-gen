# SPDX-License-Identifier: Apache-2.0
"""align_y "center" in the pixel-unfake row path.

`align_y` was documented and accepted by the CLI, but only `fit_to_cell`
(the non-pixel-unfake extract path) ever read it. With `--fit-pixel-unfake`
on, placement goes through `row_placement` / `place_row_frame`, which pinned
content to the bottom unconditionally — so the option silently did nothing
in the mode most runs use.

The visible failure: a subject that grows (an explosion, a charge-up) is
re-grounded every frame, so it climbs upward as it expands instead of
expanding around a fixed centre.

Defaults must stay bottom-anchored — that contract belongs to characters and
is pinned bit-for-bit by test_extraction_golden.py.
"""

from PIL import Image

from sprite_gen.frames.extract import place_row_frame, row_placement


def _disc(size: int, canvas: int = 40) -> Image.Image:
    """Centred square blob of `size` px inside a `canvas` px frame."""
    frame = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    start = (canvas - size) // 2
    for y in range(start, start + size):
        for x in range(start, start + size):
            frame.putpixel((x, y), (255, 160, 40, 255))
    return frame


def _content_centre_y(cell: Image.Image) -> float:
    bbox = cell.getbbox()
    assert bbox is not None
    return (bbox[1] + bbox[3]) / 2


GROWING = [_disc(8), _disc(24), _disc(12)]


def test_row_placement_defaults_to_bottom() -> None:
    _, bottom_top = row_placement(GROWING, 64, 64, 2, 1, {})
    _, explicit = row_placement(GROWING, 64, 64, 2, 1, {"align_y": "bottom"})
    assert bottom_top == explicit


def test_growing_subject_climbs_when_bottom_anchored() -> None:
    """The bug this option exists to avoid — kept as the contrast case."""
    left, top = row_placement(GROWING, 64, 64, 2, 1, {})
    centres = [
        _content_centre_y(place_row_frame(f, 64, 64, 1, left, top, 2, True, "bottom"))
        for f in GROWING
    ]
    assert max(centres) - min(centres) > 4  # 커질수록 위로 자란다


def test_center_holds_the_centre_while_the_subject_grows() -> None:
    left, top = row_placement(GROWING, 64, 64, 2, 1, {"align_y": "center"})
    centres = [
        _content_centre_y(place_row_frame(f, 64, 64, 1, left, top, 2, True, "center"))
        for f in GROWING
    ]
    assert max(centres) - min(centres) <= 1  # 반올림 1px 이내
    for centre in centres:
        assert abs(centre - 32) <= 1  # 셀 한가운데


def test_center_without_ground_uses_the_shared_row_offset() -> None:
    """ground=False keeps the row-union offset — same as the bottom path."""
    left, top = row_placement(GROWING, 64, 64, 2, 1, {"align_y": "center"})
    placed = [
        place_row_frame(f, 64, 64, 1, left, top, 2, False, "center") for f in GROWING
    ]
    assert all(cell.getbbox() is not None for cell in placed)
    # 공동 오프셋이므로 프레임의 상대 위치가 그대로 남는다 (접지처럼 균일화하지 않는다)
    assert len({cell.getbbox()[1] for cell in placed}) > 1


def test_empty_frame_stays_empty() -> None:
    blank = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    assert place_row_frame(blank, 64, 64, 1, 0, 0, 2, True, "center").getbbox() is None
