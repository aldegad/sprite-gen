# SPDX-License-Identifier: Apache-2.0
"""The attack canvas keeps 0.20 of its height above the still; `--shape wide` keeps 0.35.

More room above an attack only made the body a smaller part of the clip, so the loop
scaled it up to the delivery height. 0.20 still holds a weapon raised overhead: the
canvas adds at least a quarter of the still's height above it, and the still's own
margin over the crown does the rest. A forced wide canvas is a different case — it
lands on states whose own row is not wide, a jump among them — so it keeps the room
a jump's tall row leaves above the still.
"""
from __future__ import annotations

import pytest
from PIL import Image

from sprite_gen.video import canvas

GREEN = (0, 255, 0)
# An overhead swing, budgeted as a share of the standing body's height above its crown.
OVERHEAD_RISE = 0.35


def _figure(side: int, top: int, bottom: int) -> Image.Image:
    """A square still on the key with a figure from row `top` (its crown) to row `bottom` (its soles)."""
    img = Image.new("RGB", (side, side), GREEN)
    img.paste((150, 60, 50), (side // 3, top, 2 * side // 3, bottom))
    return img


def _room_above_crown(padded: Image.Image) -> int:
    """Rows of key above the first row that holds the figure."""
    rgb = padded.convert("RGB")
    for y in range(rgb.height):
        if any(rgb.getpixel((x, y)) != GREEN for x in range(rgb.width)):
            return y
    raise AssertionError("no figure on the canvas")


def test_attack_row_is_020_above_with_lead_and_trail_unchanged() -> None:
    attack = canvas.profile_for("attack")
    assert (attack.shape, attack.ratio, attack.headroom, attack.lead, attack.trail) == (canvas.SHAPE_WIDE, 16 / 9, 0.20, 0.28, 0.2)


@pytest.mark.parametrize("size,facing,expected_canvas,expected_offset", [
    ((1024, 1024), "right", [2276, 1280], [455, 256]),
    ((1024, 1024), "left", [2276, 1280], [2276 - 1024 - 455, 256]),
    ((120, 160), "right", [356, 200], [71, 40]),
])
def test_attack_canvas_size_and_placement(size, facing, expected_canvas, expected_offset) -> None:
    still = Image.new("RGB", size, GREEN)
    still.paste((150, 60, 50), (size[0] // 3, size[1] // 4, 2 * size[0] // 3, size[1]))
    padded, report = canvas.pad_canvas(still, canvas.profile_for("attack"), facing=facing)
    assert report["canvas"] == expected_canvas and report["offset"] == expected_offset
    assert report["headroom"] == 0.20 and padded.size == tuple(expected_canvas)
    w, h = expected_canvas
    # head-room is the canvas fraction above the still; 16:9 sets both sides
    assert report["offset"][1] == round(h * 0.20) and abs(w / h - 16 / 9) < 0.01


def test_lead_does_not_set_the_width_of_a_square_attack_canvas() -> None:
    still = _figure(1024, 80, 1000)
    base, _ = canvas.pad_canvas(still, canvas.profile_for("attack"))
    for lead in (0.18, 0.28):
        padded, _ = canvas.pad_canvas(still, canvas.profile_for("attack"), lead=lead)
        assert padded.tobytes() == base.tobytes()


@pytest.mark.parametrize("crown_margin", [0.08, 0.19])
def test_a_raised_weapon_fits_above_the_crown(crown_margin: float) -> None:
    """0.08 is about the smallest margin over the crown that still holds the swing; 0.19 is the layout guide's crown line."""
    side = 1024
    top, bottom = round(side * crown_margin), side
    padded, _ = canvas.pad_canvas(_figure(side, top, bottom), canvas.profile_for("attack"))
    body = bottom - top
    assert _room_above_crown(padded) >= OVERHEAD_RISE * body


def test_a_still_cropped_at_the_crown_needs_more_headroom() -> None:
    """With no margin over the crown, 0.20 gives a quarter of the body; the swing needs `--headroom 0.26`."""
    side = 1024
    still = _figure(side, 0, side)
    short, _ = canvas.pad_canvas(still, canvas.profile_for("attack"))
    assert _room_above_crown(short) == side // 4 < OVERHEAD_RISE * side
    raised, _ = canvas.pad_canvas(still, canvas.profile_for("attack"), headroom=0.26)
    assert _room_above_crown(raised) >= OVERHEAD_RISE * side


def test_forced_wide_keeps_035_and_never_takes_height_from_a_jump() -> None:
    forced = canvas.profile_for("jump", shape="wide")
    assert forced is canvas.WIDE_OVERRIDE and forced is not canvas.STATE_CANVAS["attack"]
    assert (forced.shape, forced.headroom, forced.lead, forced.trail) == (canvas.SHAPE_WIDE, 0.35, 0.28, 0.2)
    assert canvas.profile_for("walk", shape="wide") is forced
    still = _figure(1024, 120, 1000)
    tall, tall_rep = canvas.pad_canvas(still, canvas.profile_for("jump"))
    wide, wide_rep = canvas.pad_canvas(still, forced)
    assert wide_rep["offset"][1] >= tall_rep["offset"][1]  # room above the still
    assert wide_rep["canvas"] == [2800, 1575]  # the forced-wide canvas is the same as before the attack row moved
