# SPDX-License-Identifier: Apache-2.0
"""A hole through a limb (docs/loop-repair.md section 4): `rife.hole` reads the transparent holes a made frame opened
inside its silhouette that neither source frame beside it has there, and the cycle alignment gives such a frame way to
the nearer source frame (`--between auto`). RIFE runs a frame's colour and its coverage apart, and the coverage run can
carry the gap one source frame has between two legs into a leg the colour run keeps whole: a transparent band through the
leg with its fill on both sides. Nothing is added, no part coverage is left, and around the band the leg's own fill is
all the line there is — the outline readings judge an outline, not a hole. A hole of the frame between is the source
frames' own where either has a pixel under solid within 4 px of where it lay, in place or carried by the whole figure's
motion — a gap kept, moved with the body, or opened by a step between two legs — or where it is, whatever carried it,
one hole of each source frame: alike in area, in shape and in the colour round it, and on its way between theirs. So a
hole a part carries over a body that stays, turned, grown, sheared or swung, is theirs. A hole the first two readings
give the source frames takes no pair; of the holes they leave, each source frame's hole is one hole of the frame
between, and of the sets of pairs the one taken pairs the most, so alike holes whose ways run near each other each keep
their own two. A hole more than they have among the holes left is given way, and so is a hole unlike theirs, off its
way or farther than the figure's motion is followed, and a gap only one source frame opens on a part the figure's
motion does not carry, which the reading does not tell from a band the coverage run carried out of one source frame's
gap. A hole more is read as theirs where it lies within 4 px of a hole of theirs, or where it pairs with one source
frame's hole and the other's alike hole whose own holes the first two readings explain: what the reading does not tell.

The fixtures are synthetic: a figure of rectangles outlined in black, legs filled dark (under the dark line, 70/255,
as dark tights or boots are), the near leg over the far one; the first source frame with its legs side by side, the
second with a gap between them, as a step opens it; a gap between the arm and the body in every frame. Then a body with
a gap through it, moved whole; a figure whose legs part above boots that close the gap below, still and moving; a body
that stays with a part on it, a hole through the part, moved; the same over a body patterned in stripes or checks; a
torso patterned in blocks with an arm beside it, the gap between them moving with the arm; a plate with a gap through it,
turned, grown, sheared or swung over a body that stays; a plate with a row of holes; a row of holes moving on a curved
way; a column of holes; a plate with a hole one source frame keeps in place beside a hole moving between them; and a
body moving whole with a gap or a row of holes through it."""

from __future__ import annotations

import itertools
import math
import random

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import align, rife
from sprite_gen.video.interpolation_quality import faults

W, H = 160, 200
LINE, BODY = (0, 0, 0, 255), (250, 240, 235, 255)
FAR, NEAR = (70, 52, 40, 255), (78, 58, 46, 255)  # luma 56 and 62: under the dark line, over the black one by 50/255 and more
SIDE_BY_SIDE, APART, BETWEEN = (50, 80), (45, 85), (47, 82)  # the far and the near leg's left column
BAND = (58, 120, 66, 160)  # inside the far leg between, 8 px wide: 8 px or more from anything under solid in either source
MOVES = (0, 4, 8, 12, 24, 32)  # px the body with a gap moves from one source frame to the next
DIM, PART = (45, 55, 60), (150, 90, 60)  # the patterned body's darker colour (lighter by its contrast) and the part's
PATTERNS = ((3, 6, "v"), (2, 4, "v"), (6, 12, "v"), (3, 6, "h"), (4, 8, "c"))  # stripes wide, their period, which way
CONTRASTS = (15, 40, 120)
# A part's side, its hole's, the px it moves and whether diagonally: the moves at t 0.5 where a motion read over the box
# round the part's hole takes the body's stillness, or the stripes laid on themselves, on some pattern above.
OUTWEIGHED = (((24, 10), 32, 0), ((24, 10), 32, 1), ((24, 10), 48, 0), ((24, 10), 48, 1), ((40, 17), 32, 0),
              ((40, 17), 32, 1), ((60, 25), 32, 1))


def _box(d: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, fill: tuple[int, ...]) -> None:
    d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=LINE)
    d.rectangle([x0 + 2, y0 + 2, x1 - 3, y1 - 3], fill=fill)


def _figure(legs: tuple[int, int], *, arm_gap: int = 0) -> Image.Image:
    """A body over two legs 30 px wide, the far leg from column legs[0], the near from legs[1] drawn over it; an arm
    on the right joined to the body above and below, the gap between them 8 px wide, `arm_gap` px further right."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    far, near = legs
    _box(d, far, 96, far + 30, 180, FAR)
    _box(d, near, 96, near + 30, 185, NEAR)
    x = 128 + arm_gap
    _box(d, x, 30, x + 12, 90, BODY)
    for y in (30, 80):
        _box(d, 110, y, x + 12, y + 10, BODY)
    _box(d, 40, 20, 120, 100, BODY)
    d.rectangle([118, 32, 119, 37], fill=BODY)  # the arm's joins with no line across them
    d.rectangle([118, 82, 119, 87], fill=BODY)
    return im


def _body(dx: int = 0, dy: int = 0) -> Image.Image:
    """A body 120 x 180 px of one colour with a gap 8 x 40 px through it, on a 240 x 220 px canvas, moved dx columns
    right and dy rows down."""
    im = Image.new("RGBA", (240, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([40 + dx, 20 + dy, 159 + dx, 199 + dy], fill=(45, 55, 60, 255))
    d.rectangle([120 + dx, 60 + dy, 127 + dx, 99 + dy], fill=(0, 0, 0, 0))
    return im


def _parting(x: int, far: int, near: int) -> Image.Image:
    """A body 90 px wide from column x over two legs 24 px wide, the far leg from column x + far and the near from
    x + near, each standing in a boot that reaches under the other leg: legs side by side touch, and a gap a step opens
    between them is closed by the boots below."""
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fx, nx = x + far, x + near
    _box(d, fx, 90, fx + 24, 170, FAR)
    _box(d, fx, 160, nx + 12, 180, (120, 80, 50, 255))
    _box(d, nx, 90, nx + 24, 172, NEAR)
    _box(d, nx - 6, 162, nx + 30, 184, (130, 90, 60, 255))
    _box(d, x, 20, x + 90, 100, BODY)
    return im


def _ring(dx: int) -> Image.Image:
    """A body 200 x 180 px of one colour that stays, with a part on it 40 px square, a hole 14 px square through the part,
    the part moved dx columns right."""
    im = Image.new("RGBA", (260, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([30, 20, 229, 199], fill=(45, 55, 60, 255))
    d.rectangle([100 + dx, 80, 139 + dx, 119], fill=(150, 90, 60, 255))
    d.rectangle([113 + dx, 93, 126 + dx, 106], fill=(0, 0, 0, 0))
    return im


def _patterned(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], stripe: int, period: int, way: str,
               contrast: int) -> None:
    """`box` (x0, y0, x1, y1, inclusive) in DIM with stripes `stripe` px wide every `period` px, up and down ("v") or
    across ("h"), or checks `stripe` px square ("c"), or blocks `stripe` px square each lighter by its own whole number up
    to `contrast` from one fixed sequence ("n"); the stripes and checks lighter by `contrast`."""
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=DIM + (255,))
    light = tuple(min(255, c + contrast) for c in DIM) + (255,)
    if way == "v":
        for x in range(x0, x1 + 1, period):
            d.rectangle((x, y0, min(x1, x + stripe - 1), y1), fill=light)
    elif way == "h":
        for y in range(y0, y1 + 1, period):
            d.rectangle((x0, y, x1, min(y1, y + stripe - 1)), fill=light)
    elif way == "c":
        for y in range(y0, y1 + 1, stripe):
            for x in range(x0 + (y - y0) // stripe % 2 * stripe, x1 + 1, 2 * stripe):
                d.rectangle((x, y, min(x1, x + stripe - 1), min(y1, y + stripe - 1)), fill=light)
    else:
        state = 11
        for y in range(y0, y1 + 1, stripe):
            for x in range(x0, x1 + 1, stripe):
                state = (state * 1103515245 + 12345) % 2 ** 31
                lift = (state >> 16) % (contrast + 1)
                d.rectangle((x, y, min(x1, x + stripe - 1), min(y1, y + stripe - 1)),
                            fill=tuple(min(255, c + lift) for c in DIM) + (255,))


def _over(dx: int, dy: int, *, part: int = 24, gap: int = 10, pattern: tuple[int, int, str] = (3, 6, "v"),
          contrast: int = 40, size: tuple[int, int] = (300, 300), body: tuple[int, int, int, int] = (30, 20, 269, 279),
          at: tuple[int, int] = (120, 110)) -> Image.Image:
    """A patterned body that stays (`_patterned`; 240 x 260 px on a 300 x 300 px canvas), with a part `part` px square on
    it at `at`, a hole `gap` px square through the part's middle (none where 0), the part moved dx columns right and dy
    rows down."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _patterned(d, body, *pattern, contrast)
    x0, y0 = at[0] + dx, at[1] + dy
    d.rectangle((x0, y0, x0 + part - 1, y0 + part - 1), fill=PART + (255,))
    if gap:
        o = (part - gap) // 2
        d.rectangle((x0 + o, y0 + o, x0 + o + gap - 1, y0 + o + gap - 1), fill=(0, 0, 0, 0))
    return im


def _validators(dx: int) -> Image.Image:
    """The checkpoint validator's frame: a body 200 x 180 px with stripes 3 px wide every 6 px on a 260 x 220 px canvas,
    a part 24 px square with a hole 10 px square through it, the part moved dx columns right."""
    return _over(dx, 0, size=(260, 220), body=(30, 20, 229, 199), at=(100, 80))


def _arm(dy: int) -> Image.Image:
    """A torso that stays, 120 x 260 px, patterned in blocks 4 px square at contrast 200; an arm beside its right edge,
    the upper arm and the forearm lying along that edge and joined by the arm's outer side, so the gap between arm and
    torso, 16 x 12 px, has the torso's edge on its left and the arm on its other sides; the arm reaches 48 px above and
    52 px below the gap and is moved dy rows down."""
    im = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _patterned(d, (40, 20, 159, 279), 4, 0, "n", 200)
    for x0, y0, x1, y1 in ((150, 60, 191, 111), (176, 60, 191, 175), (150, 124, 191, 175)):
        d.rectangle((x0, y0 + dy, x1, y1 + dy), fill=PART + (255,))
    return im


def _still(plain: bool) -> np.ndarray:
    """A body that stays, 240 x 260 px on a 300 x 300 px canvas (RGBA), in stripes 3 px wide every 6 px lighter by 40,
    or plain DIM."""
    x = np.mgrid[:300, :300][1]
    rgba = np.zeros((300, 300, 4), np.uint8)
    lift = np.zeros_like(x) if plain else (x % 6 < 3) * 40
    rgba[20:280, 30:270, :3] = (np.array(DIM) + lift[..., None])[20:280, 30:270]
    rgba[20:280, 30:270, 3] = 255
    return rgba


def _plate(t: float, *, plain: bool = False, d: float = 0.0, turn: float = 0.0, grow: float = 1.0, shear: float = 0.0,
           swing: float = 0.0, off: float = 0.0) -> Image.Image:
    """The body that stays (`_still`) with a plate on it (|u| < 24, |v| < 28 px in its own axes) and a gap through the
    plate (|u| < 6, |v| < 16), drawn exactly at time t of one motion from t 0 to t 1, half of it before t 0.5 and half
    after: the plate's centre moves d px across, and the plate turns `turn` degrees, grows `grow` times and is sheared by
    `shear` about it; or, with `swing`, the plate hangs from a point 100 px above its centre at t 0.5 and turns `swing`
    degrees about that point; `off` px lower than its way. Turned 60 degrees and moved 48 px over the stripes, it is the
    third checkpoint validator's frame."""
    y, x = np.mgrid[:300, :300].astype(np.float64)
    rgba = _still(plain)
    s = t - 0.5
    if swing:
        phi = math.radians(swing * s)
        cx, cy, theta = 150 + 100 * math.sin(phi), 40 + 100 * math.cos(phi), -phi
    else:
        cx, cy, theta = 150 + d * s, 140 + off, math.radians(turn * s)
    u = (x - cx) * math.cos(theta) + (y - cy) * math.sin(theta)
    v = -(x - cx) * math.sin(theta) + (y - cy) * math.cos(theta)
    u, v = (u - shear * s * v) / grow ** s, v / grow ** s
    rgba[(np.abs(u) < 24) & (np.abs(v) < 28)] = PART + (255,)
    rgba[(np.abs(u) < 6) & (np.abs(v) < 16)] = 0
    return Image.fromarray(rgba, "RGBA")


def _row(t: float, *, plain: bool = False, d: int = 0, dy: int = 0, more: tuple[int, ...] = (), taller: int = 0,
         filled: tuple[int, ...] = ()) -> Image.Image:
    """The body that stays (`_still`) with a plate 120 x 40 px on it and a row of four holes 8 px square through it, 24 px
    apart (at -36, -12, 12 and 36 px from its centre), the plate moving d px across and dy px down from t 0 to t 1,
    drawn at t: the row's holes `taller` rows taller at their foot, those at the columns in `filled` left solid, and holes
    8 px square more at the columns in `more`."""
    rgba = _still(plain)
    cx, cy = int(round(150 + d * (t - 0.5))), int(round(140 + dy * (t - 0.5)))
    rgba[cy - 20:cy + 20, cx - 60:cx + 60] = PART + (255,)
    for ox in (-36, -12, 12, 36):
        if ox not in filled:
            rgba[cy - 4:cy + 4 + taller, cx + ox - 4:cx + ox + 4] = 0
    for ox in more:
        rgba[cy - 4:cy + 4, cx + ox - 4:cx + ox + 4] = 0
    return Image.fromarray(rgba, "RGBA")


def _way(t: float, n: int = 2, *, still: bool = False, more: tuple[int, ...] = (), shut: tuple[int, ...] = ()) -> Image.Image:
    """The body that stays (`_still`) with a plate on it and a row of n holes 8 px square through it, 12 px apart, moving
    on a curved way from t 0 to t 1, int(40 t + 20 t (1 - t)) px across and int(32 t) px down — at t 0.5 each hole lies
    5 px beside the midpoint of where the source frames have it — drawn at t: the plate moving with them, or `still` and
    wide enough for their whole way; holes 8 px square more at the places in `more` (-1 before the first), and the row's
    holes at the places in `shut` left solid. With two holes it is the fourth checkpoint validator's frame."""
    rgba = _still(False)
    dx, dy = int(40 * t + 20 * t * (1 - t)), int(32 * t)
    if still:
        rgba[95:185, 85:200] = PART + (255,)
    else:
        rgba[90 + dy:160 + dy, 85 + dx:150 + 12 * (n - 2) + dx] = PART + (255,)
    for place in [p for p in range(n) if p not in shut] + list(more):
        x, y = 110 + 12 * place + dx, 124 + dy
        rgba[y - 4:y + 4, x - 4:x + 4] = 0
    return Image.fromarray(rgba, "RGBA")


def _column(t: float, *, shut: tuple[int, ...] = ()) -> Image.Image:
    """The body that stays (`_still`) with a plate 40 x 90 px on it and a column of three holes 8 px square through it,
    24 px apart, the plate moving 32 px down from t 0 to t 1, drawn at t; the holes at the places in `shut` left solid.
    At t 0.5 each hole lies against the next hole of the first source frame and the one before it of the second: the
    in-place reading gives the middle hole those two, and leaves of the first and the last a strip thinner than
    rife.HOLE_THICK."""
    rgba = _still(False)
    cy = int(round(140 + 32 * (t - 0.5)))
    rgba[cy - 45:cy + 45, 130:170] = PART + (255,)
    for place in (p for p in range(3) if p not in shut):
        y = cy + 24 * (place - 1)
        rgba[y - 4:y + 4, 146:154] = 0
    return Image.fromarray(rgba, "RGBA")


def _beside(t: float, *, kept_by: int = 0) -> Image.Image:
    """The body that stays (`_still`) with a plate 105 px square on it, through it a hole 8 px square in place (rows 120
    to 127, columns 112 to 119) that one source frame keeps and the other shuts — open while t < 1, so in the first source
    frame and the frame between, or with `kept_by` 1 while t > 0 — and a hole 8 px square moving from (100, 100) to
    (152, 148), drawn at t: at t 0.5 at its exact midpoint, 10 px from the hole in place. The fifth checkpoint
    validator's frame."""
    rgba = _still(False)
    rgba[75:180, 75:180] = PART + (255,)
    if (t < 1) if kept_by == 0 else (t > 0):
        rgba[120:128, 112:120] = 0
    cx, cy = int(100 + 52 * t), int(100 + 48 * t)
    rgba[cy - 4:cy + 4, cx - 4:cx + 4] = 0
    return Image.fromarray(rgba, "RGBA")


def _moving(t: float, move: tuple[int, int], holes: tuple[tuple[int, int, int, int], ...],
            more: tuple[tuple[int, int, int, int], ...] = ()) -> Image.Image:
    """A body 200 x 260 px outlined in black on a 400 x 400 px canvas, moving whole move[0] px across and move[1] down
    from t 0 to t 1 (its corner at (100, 70) at t 0.5), drawn at t, with holes through it at `holes` (x0, y0, x1, y1 from
    its corner, the ends left out) and at `more`."""
    rgba = np.zeros((400, 400, 4), np.uint8)
    x0, y0 = 100 + int(round(move[0] * (t - 0.5))), 70 + int(round(move[1] * (t - 0.5)))
    rgba[y0:y0 + 260, x0:x0 + 200] = LINE
    rgba[y0 + 2:y0 + 258, x0 + 2:x0 + 198] = PART + (255,)
    for bx0, by0, bx1, by1 in holes + more:
        rgba[y0 + by0:y0 + by1, x0 + bx0:x0 + bx1] = 0
    return Image.fromarray(rgba, "RGBA")


def _holed(frame: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    out = frame.copy()
    ImageDraw.Draw(out).rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=(0, 0, 0, 0))
    return out


def _measure(made: Image.Image, a: Image.Image, b: Image.Image) -> dict[str, float | None]:
    return {**rife.smear(made, a, b), "outline_shift": rife.outline_shift(made, a, b), "ghost": rife.ghost(made)}


def _shifted(frame: Image.Image, dx: int, dy: int) -> Image.Image:
    out = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    out.paste(frame, (dx, dy))
    return out


# What the alignment does with a frame made between two source frames that it keeps as made: both frames made at t 0.5
# (times .5 and 1.5 of four) delivered as the very frame made, nothing given way, no fault.
KEPT = {"made_at": [1, 3], "nearest_at": [], "faults": [[], []], "delivered": True}


def _resampled_and_holes(a: Image.Image, made: Image.Image, b: Image.Image) -> tuple[dict[str, object], dict[int, float]]:
    """`_resampled`, and the hole the alignment read on each frame it made (`align.measure_made`), by cell: at 1, the frame
    made from a to b at t 0.5, `rife.hole(made, a, b, 0.5)`; at 3, from b to a, `rife.hole(made, b, a, 0.5)` — read
    once, by the alignment, not again by the test."""
    out, facts = align.resample([a, b], 4, lambda x, y, t: made)
    return ({"made_at": facts["made_at"], "nearest_at": facts["nearest_at"], "faults": [m["faults"] for m in facts["smear"]],
             "delivered": out[1] is made and out[3] is made}, {m["at"]: m["hole"] for m in facts["smear"]})


def _resampled(a: Image.Image, made: Image.Image, b: Image.Image) -> dict[str, object]:
    """What the alignment does resampling a and b to four frames, both frames made at t 0.5 being `made`."""
    return _resampled_and_holes(a, made, b)[0]


# Resampled to eight, the frames made at t 0.25, 0.5 and 0.75 each way all kept as made.
QUARTERS = {"made_at": [1, 2, 3, 5, 6, 7], "nearest_at": [], "faults": [[]] * 6, "delivered": True}


def _quarters(draw) -> dict[str, object]:
    """What the alignment does resampling draw(0) and draw(1) to eight frames, each frame between them (t 0.25, 0.5 and
    0.75 from the first to the second and back) being draw(s) at its place s from the first."""
    a, b, made = draw(0.0), draw(1.0), {}

    def interpolate(x: Image.Image, y: Image.Image, t: float) -> Image.Image:
        s = t if x is a else 1 - t
        if s not in made:
            made[s] = draw(s)
        return made[s]

    out, facts = align.resample([a, b], 8, interpolate)
    return {"made_at": facts["made_at"], "nearest_at": facts["nearest_at"], "faults": [m["faults"] for m in facts["smear"]],
            "delivered": all(out[k] is made[k / 4 if k < 4 else 2 - k / 4] for k in (1, 2, 3, 5, 6, 7))}


def test_a_hole_through_a_leg_is_read_and_a_gap_the_sources_have_is_not():
    """The band reads as a hole over its own pixels; the frame drawn between, the arm's gap all three frames have, and
    that gap moved a few pixels (within rife.HOLE_REACH of theirs) read none. A pinhole thinner than rife.HOLE_THICK is
    opened away."""
    a, b, clean = _figure(SIDE_BY_SIDE), _figure(APART), _figure(BETWEEN)
    alpha = lambda im: np.asarray(im)[..., 3]
    x0, y0, x1, y1 = BAND
    assert (alpha(a)[y0 - 8:y1 + 8, x0 - 8:x1 + 8] == 255).all() and (alpha(b)[y0 - 8:y1 + 8, x0 - 8:x1 + 8] == 255).all()
    holed = _holed(clean, BAND)
    assert rife.hole(holed, a, b, 0.5) == round((x1 - x0) * (y1 - y0) / int((alpha(holed) >= 128).sum()), 5)
    assert rife.hole(clean, a, b, 0.5) == 0
    assert (alpha(clean)[40:80, 120:128] == 0).all()  # the arm's gap, enclosed
    assert rife.hole(_figure(BETWEEN, arm_gap=3), a, b, 0.5) == 0
    assert rife.hole(_holed(clean, (60, 130, 63, 133)), a, b, 0.5) == 0
    assert rife.hole(Image.new("RGBA", (W, H)), a, b, 0.5) == 0


def test_the_outline_readings_pass_a_hole_through_a_dark_leg():
    """Around the band the leg's fill is darker than the dark line, so the dark line counts it as outline; read rank by
    rank against black lines it is lost — but the fault `outline` needs both. Nothing dark is added and nothing is part
    covered: the readings before the hole pass it."""
    a, b = _figure(SIDE_BY_SIDE), _figure(APART)
    m = _measure(_holed(_figure(BETWEEN), BAND), a, b)
    assert m["outline_loss"] <= 0 and m["outline_shift"] > 0.05 and m["dark_excess"] < 0
    assert m["ghost"] == 0 and m["partial_excess"] <= 0 and faults(m) == []
    assert faults({**m, "hole": rife.hole(_holed(_figure(BETWEEN), BAND), a, b, 0.5)}) == ["hole"]


def test_alignment_takes_the_nearer_frame_for_a_hole_through_a_leg():
    """Resampled from two frames to four (times 0, .5, 1, 1.5), both frames made at t 0.5 carry the band: `--between
    auto` takes the nearer source frame at each and names the fault in the report's words; `rife` keeps them, named."""
    a, b = _figure(SIDE_BY_SIDE), _figure(APART)
    holed = _holed(_figure(BETWEEN), BAND)
    out, facts = align.resample([a, b], 4, lambda x, y, t: holed)
    assert facts["made_at"] == [] and facts["nearest_at"] == [1, 3] and out[1] is b and out[3] is a
    assert all(m["method"] == "nearest" and m["faults"] == ["hole"] and m["hole"] > 0 for m in facts["smear"])
    assert "a gap through a limb" in align._fault_line("walk-front", facts["smear"][0])
    out, facts = align.resample([a, b], 4, lambda x, y, t: holed, between="rife")
    assert facts["made_at"] == [1, 3] and out[1] is holed and all(m["faults"] == ["hole"] for m in facts["smear"])


def test_alignment_keeps_a_clean_frame_and_one_with_the_gap_the_sources_have():
    """The frame drawn between, and the same with the arm's gap a few pixels on, are kept as made: the same frame
    delivered, nothing given way."""
    a, b = _figure(SIDE_BY_SIDE), _figure(APART)
    for made in (_figure(BETWEEN), _figure(BETWEEN, arm_gap=3)):
        out, facts = align.resample([a, b], 4, lambda x, y, t: made)
        assert facts["made_at"] == [1, 3] and facts["nearest_at"] == [] and out[1] is made and out[3] is made
        assert all(m["faults"] == [] and m["hole"] == 0 for m in facts["smear"])


@pytest.mark.slow
def test_a_gap_both_source_frames_have_moved_with_the_body_is_theirs():
    """The body with its gap moved whole from one source frame to the next, across and diagonally, by each of MOVES, the
    frame between drawn halfway: each source frame moved half the way onto it gives it byte for byte. From 24 px on, the
    gap lies 5 px and more from where either source frame has it, but on the way the body moves, so it is theirs: every
    frame is kept as made, and reads no hole (`rife.hole(made, a, b, 0.5)`, as the alignment read it on the frame it made
    at cell 1)."""
    seen = {}
    for move in MOVES:
        for down in (0, 1):
            dx, dy = move // 2, down * move // 2
            a, made, b = _body(-dx, -dy), _body(), _body(dx, dy)
            assert _shifted(a, dx, dy).tobytes() == made.tobytes() == _shifted(b, -dx, -dy).tobytes()
            kept, holes = _resampled_and_holes(a, made, b)
            seen[move, "diagonally" if down else "across"] = (kept, holes[1])
    assert {k: v[0] for k, v in seen.items() if v[0] != KEPT} == {}
    assert {k: hole for k, (_, hole) in seen.items()} == {k: 0.0 for k in seen}


def test_a_gap_one_source_frame_has_half_open_between_is_theirs():
    """Legs side by side in one source frame, touching, and parted in the other; the frame between drawn with them half
    parted. The gap there is the parted frame's, opened halfway: with the figure still, inside where that frame has it;
    with the figure moving 30 px, 8 px and more beside it, where the figure carries it. Both are kept as made."""
    seen = {}
    for name, (xa, fa, na), (xb, fb, nb) in (("still", (40, 10, 34), (40, 4, 40)), ("moving", (20, 10, 34), (50, 10, 50))):
        a, b = _parting(xa, fa, na), _parting(xb, fb, nb)
        x, far, near = (xa + xb) // 2, (fa + fb) // 2, (na + nb) // 2
        made = _parting(x, far, near)
        assert na == fa + 24 and (np.asarray(made)[110:150, x + far + 24:x + near, 3] == 0).all()
        seen[name] = (a, made, b, _resampled(a, made, b))
    assert {k: v[3] for k, v in seen.items() if v[3] != KEPT} == {}
    assert {k: rife.hole(made, a, b, 0.5) for k, (a, made, b, _) in seen.items()} == {"still": 0.0, "moving": 0.0}


def test_a_gap_moved_with_a_part_while_the_body_stays_is_theirs():
    """A part with a hole through it moves 32 px across a body that stays, the frame between drawn with the part
    halfway: the hole lies 5 px and more from where either source frame has it, on the way the part moves. The body
    outweighs the part in the whole figure, which stays; the hole is one hole of each source frame, alike and on its way
    between theirs, and the frame is kept as made."""
    a, made, b = _ring(-16), _ring(0), _ring(16)
    assert _resampled(a, made, b) == KEPT and rife.hole(made, a, b, 0.5) == 0


@pytest.mark.slow
def test_a_hole_through_a_moving_body_is_still_read():
    """The parting figure moving 30 px, the frame between with a band 8 px wide through its body: wherever its pixels
    lay, in place or carried, both source frames show the body there, so the band reads as a hole over its own pixels
    and the frame gives way to the nearer source frame. (The body's fill is light, so its edge round the band also reads
    as an outline lost.) The hole each way is the alignment's own reading (`align.measure_made`): at cell 1
    `rife.hole(holed, a, b, 0.5)`, at cell 3 `rife.hole(holed, b, a, 0.5)`, read once."""
    a, b = _parting(20, 10, 34), _parting(50, 10, 50)
    holed = _holed(_parting(35, 10, 42), (70, 40, 78, 80))
    out, facts = align.resample([a, b], 4, lambda x, y, t: holed)
    assert facts["made_at"] == [] and facts["nearest_at"] == [1, 3] and out[1] is b and out[3] is a
    assert all("hole" in m["faults"] for m in facts["smear"])
    holes = {m["at"]: m["hole"] for m in facts["smear"]}
    assert holes[1] == holes[3] == round(8 * 40 / int((np.asarray(holed)[..., 3] >= 128).sum()), 5)


@pytest.mark.slow
def test_a_hole_a_part_carries_over_a_patterned_body_that_stays_is_kept_as_made():
    """A part with a hole through it moves over a striped body that stays, each frame between drawn exactly at its
    place: the hole is both source frames', carried with the part. Over the box round it the body's stillness fits
    better than the part's motion, so a motion read there misses it; told by what it is, it is one hole of each source
    frame, alike and on its way between theirs. The checkpoint validator's own frame (its 24 px part moved 32 px),
    resampled to four, and parts of 24, 40 and 60 px moved diagonally 32 px (and the 24 px part 48 px) over stripes at
    contrast 120, resampled to eight, keep every frame made, byte for byte."""
    assert _resampled(_validators(-16), _validators(0), _validators(16)) == KEPT
    for (part, gap), move in (((24, 10), 32), ((24, 10), 48), ((40, 17), 32), ((60, 25), 32)):

        def draw(s: float, move: int = move, part: int = part, gap: int = gap) -> Image.Image:
            dx = round(s * move) - move // 2
            return _over(dx, dx, part=part, gap=gap, contrast=120)

        assert _quarters(draw) == QUARTERS


@pytest.mark.slow
def test_every_pattern_contrast_and_move_reads_no_hole_where_a_part_carries_it():
    """The same over every pattern and contrast at each move in OUTWEIGHED (t 0.5), and the three parts moved 16, 32 and
    48 px across and diagonally over the validator's stripes at t 0.25 and 0.75: every frame between reads no hole."""
    seen = {}
    for pattern, contrast, ((part, gap), move, down) in itertools.product(PATTERNS, CONTRASTS, OUTWEIGHED):
        a, made, b = (_over(dx, down * dx, part=part, gap=gap, pattern=pattern, contrast=contrast)
                      for dx in (-move // 2, 0, move // 2))
        seen[pattern, contrast, part, move, down, 0.5] = rife.hole(made, a, b, 0.5)
    for (part, gap), move, down, t in itertools.product(((24, 10), (40, 17), (60, 25)), (16, 32, 48), (0, 1), (0.25, 0.75)):
        back = int(np.floor(t * move + 0.5))
        a, made, b = (_over(dx, down * dx, part=part, gap=gap) for dx in (-back, 0, move - back))
        seen["stripes", 40, part, move, down, t] = rife.hole(made, a, b, t)
    assert {k: v for k, v in seen.items() if v} == {}


@pytest.mark.slow
def test_the_gap_between_an_arm_and_a_patterned_torso_that_stays_is_theirs():
    """An arm moves 40 px down beside a torso that stays, patterned in blocks: the gap between them, carried with the arm,
    lies where the arm covers it in the other source frame, and over the box round it the torso's stillness fits better
    than the arm's motion. It is one gap of each source frame, alike and on the arm's way between theirs: at t 0.25, 0.5
    and 0.75 the frames between read no hole and are kept as made."""
    assert {t: rife.hole(_arm(0), _arm(-int(np.floor(t * 40 + 0.5))), _arm(40 - int(np.floor(t * 40 + 0.5))), t)
            for t in (0.25, 0.5, 0.75)} == {0.25: 0.0, 0.5: 0.0, 0.75: 0.0}
    assert _quarters(lambda s: _arm(round(s * 40) - 20)) == QUARTERS


def test_a_gap_only_one_source_frame_opens_on_a_part_over_a_body_that_stays_is_given_way():
    """The boundary: a part 30 px square moves 32 px over a body that stays, a hole 14 px square open through it in the
    first source frame and closed in the second, half open (8 px) in the frame between. The hole lies 5 px and more from
    where the first frame has it in place; the whole figure stays; and the second source frame has no hole to pair it
    with — as neither has one to pair with a band the coverage run took out of one source frame's gap into a limb. Plain
    or striped, the frame gives way to the nearer source frame."""
    for contrast in (0, 40):
        a, made, b = (_over(dx, 0, part=30, gap=gap, contrast=contrast) for dx, gap in ((-16, 14), (0, 8), (16, 0)))
        out, facts = align.resample([a, b], 4, lambda x, y, t: made)
        assert facts["made_at"] == [] and facts["nearest_at"] == [1, 3] and out[1] is b and out[3] is a
        assert all(m["faults"] == ["hole"] for m in facts["smear"])
        assert rife.hole(made, a, b, 0.5) == round(8 * 8 / int((np.asarray(made)[..., 3] >= 128).sum()), 5)


# The third checkpoint validator's motion of the plate (`_plate`): turned 60 degrees as it moves 48 px.
TURNED = {"turn": 60, "d": 48}
# Motions of the plate whose gap no motion of the figure carries, over a body that stays: turned 90 degrees, grown twice
# and sheared by 0.6 as it moves 48 px, and swung 45 degrees, its centre moving 76.5 px — within the reach of a body
# 260 px high (sqrt(2) x 52 + 4 = 77.5 px).
MOTIONS = ({"turn": 90, "d": 48}, {"grow": 2.0, "d": 48}, {"shear": 0.6, "d": 48}, {"swing": 45})


@pytest.mark.slow
def test_a_hole_both_source_frames_have_on_a_turning_plate_is_kept_as_made():
    """The third checkpoint validator's frame: the plate turns 60 degrees as it moves 48 px over the stripes, the frame
    between drawn exactly halfway. No motion of the figure carries the gap, turned as it is; it is one hole of each source
    frame, alike in area, in shape and in the colour round it, and on its way between theirs. Resampled to four, both
    frames made are kept, byte for byte, and to eight (t 0.25, 0.5 and 0.75 each way) every one; so are the plate's
    other motions in MOTIONS over the stripes, and over a plain body as well each reads no hole (plain, the plate swung
    45 degrees is given way for `smear`, a reading of its own)."""
    assert _resampled(*(_plate(s, **TURNED) for s in (0.0, 0.5, 1.0))) == KEPT
    assert _quarters(lambda s: _plate(s, **TURNED)) == QUARTERS
    assert {tuple(m.items()): _resampled(*(_plate(s, **m) for s in (0.0, 0.5, 1.0))) for m in MOTIONS} == {
        tuple(m.items()): KEPT for m in MOTIONS}
    assert {tuple(m.items()): rife.hole(_plate(0.5, plain=True, **m), _plate(0.0, plain=True, **m), _plate(1.0, plain=True, **m), 0.5)
            for m in MOTIONS} == {tuple(m.items()): 0.0 for m in MOTIONS}


@pytest.mark.slow
def test_every_motion_of_a_plate_within_reach_reads_no_hole():
    """The plate turned 0 to 90 degrees, grown 1.1 to 2 times or sheared by 0.15 to 0.6 as it moves 0, 24 or 48 px, or
    swung 10 to 45 degrees, over stripes and over a plain body at t 0.5, and turned as it moves over the stripes at
    t 0.25: every frame between reads no hole."""
    motions = ([{"turn": r, "d": d} for r in (0, 10, 20, 30, 45, 60, 90) for d in (0, 24, 48)]
               + [{"grow": g, "d": d} for g in (1.1, 1.2, 1.35, 1.5, 2.0) for d in (0, 24, 48)]
               + [{"shear": h, "d": d} for h in (0.15, 0.3, 0.45, 0.6) for d in (0, 24, 48)]
               + [{"swing": w} for w in (10, 20, 30, 45)])
    seen = {}
    for m, plain in itertools.product(motions, (False, True)):
        a, made, b = (_plate(s, plain=plain, **m) for s in (0.0, 0.5, 1.0))
        seen[tuple(m.items()), plain, 0.5] = rife.hole(made, a, b, 0.5)
    for r, d in itertools.product((0, 10, 20, 30, 45, 60, 90), (0, 24, 48)):
        a, made, b = (_plate(s, turn=r, d=d) for s in (0.0, 0.25, 1.0))
        seen[(("turn", r), ("d", d)), False, 0.25] = rife.hole(made, a, b, 0.25)
    assert len(seen) == 125 and {k: v for k, v in seen.items() if v} == {}


def _gap_drawn(plain: bool, rows: tuple[int, int], cols: tuple[int, int]) -> Image.Image:
    """The plate moving 48 px (`_plate`) drawn halfway with its gap filled and a gap cut at `rows` and `cols` instead."""
    out = np.asarray(_plate(0.5, plain=plain, d=48)).copy()
    out[124:156, 144:156] = PART + (255,)
    out[rows[0]:rows[1], cols[0]:cols[1]] = 0
    return Image.fromarray(out, "RGBA")


def _given_way(a: Image.Image, made: Image.Image, b: Image.Image) -> bool:
    """Resampled to four, both frames made at t 0.5 are `made`, given way to the nearer source frame with `hole` among
    their faults."""
    out, facts = align.resample([a, b], 4, lambda x, y, t: made)
    return (facts["made_at"] == [] and facts["nearest_at"] == [1, 3] and out[1] is b and out[3] is a
            and all("hole" in m["faults"] and m["hole"] > 0 for m in facts["smear"]))


@pytest.mark.slow
def test_a_hole_unlike_the_source_frames_or_off_its_way_or_out_of_reach_is_given_way():
    """The boundaries, on the plate moving 48 px over a body that stays, plain or striped, its gap 11 x 31 px in both
    source frames: the frame between gives way to the nearer source frame where its gap is unlike theirs — drawn 16 x 40 px
    (1.9 times their area; rife.HOLE_AREA lets it be a quarter larger) or 18 x 18 px (its larger moment 2.8 times smaller
    over its area; rife.HOLE_SHAPE lets it be 1.3 times) — or off its way between theirs: the plate drawn 12 px across it,
    or where it is at t 0.8 in a frame made at t 0.5, 14.4 px along it (rife.HOLE_REACH + rife.HOLE_PATH of their 48 px
    apart lets it be 8.8 px); where the source frames' gaps are unlike each other, the plate growing 2.2 times as it
    moves (their areas more than rife.HOLE_PAIR_AREA, 4 times, apart); and where they lie farther apart than the figure's
    motion is followed (sqrt(2) x 52 + 4 = 77.5 px on a body 260 px high), the plate moving 80 px, or swinging 60 degrees,
    its centre moving 100 px."""
    seen = {}
    for plain in (False, True):
        a, b = _plate(0.0, plain=plain, d=48), _plate(1.0, plain=plain, d=48)
        for name, made in (("area", _gap_drawn(plain, (120, 160), (142, 158))), ("shape", _gap_drawn(plain, (131, 149), (141, 159))),
                           ("across", _plate(0.5, plain=plain, d=48, off=12)), ("along", _plate(0.8, plain=plain, d=48))):
            seen[name, plain] = _given_way(a, made, b)
        for name, motion in (("pair area", {"grow": 2.2, "d": 48}), ("reach", {"d": 80}), ("swing", {"swing": 60})):
            seen[name, plain] = _given_way(*(_plate(s, plain=plain, **motion) for s in (0.0, 0.5, 1.0)))
    assert seen == {k: True for k in seen}


@pytest.mark.slow
def test_each_hole_of_a_source_frame_is_one_hole_of_the_frame_between():
    """A plate with a row of four holes (`_row`) moves 32 px down over a body that stays, plain or striped: the frame
    between drawn exactly is kept as made, and so is one with the row drawn a pixel taller, where a hole of one source
    frame and the next but one of the other have their midpoint as near each hole as its own two: only with its own two
    is every hole of the row paired. Drawn with one hole more midway between two of the row's — where one source frame's
    hole and the next one of the other would put it — or past the row's end, or with one of the row's moved 12 px to a
    midway, it gives way to the nearer source frame: of the holes the first two readings leave, each source frame's hole
    is one hole of the frame between. Standing still, the row drawn a pixel taller is kept: the in-place reading gives
    the row its source frames' holes."""
    seen = {}
    for plain in (False, True):
        a, b = _row(0.0, plain=plain, dy=32), _row(1.0, plain=plain, dy=32)
        seen["row", plain] = _resampled(a, _row(0.5, plain=plain, dy=32), b) == KEPT
        seen["a pixel taller", plain] = _resampled(a, _row(0.5, plain=plain, dy=32, taller=1), b) == KEPT
        for name, made in (("midway", _row(0.5, plain=plain, dy=32, more=(0,))),
                           ("between the last two", _row(0.5, plain=plain, dy=32, more=(24,))),
                           ("past the end", _row(0.5, plain=plain, dy=32, more=(54,))),
                           ("moved to a midway", _row(0.5, plain=plain, dy=32, more=(0,), filled=(12,)))):
            seen[name, plain] = _given_way(a, made, b)
        still = _row(0.0, plain=plain)
        seen["still, a pixel taller", plain] = _resampled(still, _row(0.5, plain=plain, taller=1), still) == KEPT
    assert seen == {k: True for k in seen}


@pytest.mark.slow
def test_alike_holes_whose_ways_run_near_each_keep_their_own_two():
    """The fourth checkpoint validator's frame (`_way`): two alike holes 12 px apart move on a curved way over a body
    that stays, with the plate or through a plate that stays, and the frame between is drawn exactly, each hole 5 px
    beside the midpoint of its own two (4 px and 0.1 of their 51 px are let through). The first hole lies 1 px from the
    midpoint of the second's hole in the first source frame and its own in the second: that triple is the cheaper, and
    taken first it would leave the second hole nothing to pair with. The set of triples taken pairs the most holes, so
    each hole keeps its own two: resampled to four, both frames made are kept, byte for byte, and so are three and four
    such holes, each but the last cheapest with the next one's hole in the first source frame."""
    seen = {(n, still): _resampled(*(_way(s, n, still=still) for s in (0.0, 0.5, 1.0))) for n in (2, 3, 4)
            for still in (False, True)}
    assert seen == {k: KEPT for k in seen}


@pytest.mark.slow
def test_holes_no_set_of_pairs_explains_together_give_way(monkeypatch):
    """The boundaries on the same way (`_way`), two and four holes, the plate moving or still. One hole more before the
    row, on the first hole's way: it could take the first hole's two, but the first and the second cannot then both
    keep theirs — each source frame's holes pair as many of the made frame's as they number and no more. And the row's
    last hole shut in the second source frame: it has nothing there to pair with. Both give way to the nearer source
    frame with `hole` among their faults. Searched no further than rife.HOLE_PAIR_STEPS steps, here one, the frame of the
    test above gives way too: a group of triples both of whose searches run past it pairs none of its holes."""
    seen = {}
    for n, still in itertools.product((2, 4), (False, True)):
        seen["one more before", n, still] = _given_way(_way(0.0, n, still=still), _way(0.5, n, still=still, more=(-1,)),
                                                       _way(1.0, n, still=still))
        seen["the last only in the first", n, still] = _given_way(_way(0.0, n, still=still), _way(0.5, n, still=still),
                                                                  _way(1.0, n, still=still, shut=(n - 1,)))
    monkeypatch.setattr(rife, "HOLE_PAIR_STEPS", 1)
    seen["searched one step"] = _given_way(*(_way(s) for s in (0.0, 0.5, 1.0)))
    assert seen == {k: True for k in seen}


def _tangled(seed: int, n: int) -> list[tuple[int, int, int, float]]:
    """Triples for `rife._taken`: n made holes, each paired by its own two (i, i, i) and by up to five more pairs of a's
    and b's holes within three places of its own, at seeded random costs: one set pairs every hole."""
    rng = random.Random(seed)
    triples = {(i, i, i) for i in range(n)}
    for i in range(n):
        for _ in range(5):
            triples.add((i, min(n - 1, max(0, i + rng.randint(-3, 3))), min(n - 1, max(0, i + rng.randint(-3, 3)))))
    return [(i, j, k, round(rng.uniform(0.0, 3.0), 3)) for i, j, k in sorted(triples)]


def test_a_group_every_hole_of_which_pairs_is_paired_whole_where_the_search_for_the_most_runs_long(monkeypatch):
    """Twenty alike holes, each with its own two and up to five more pairs near its own at random costs (`_tangled`):
    the search for the set pairing the most, cheapest first, wanders among the cheaper pairs and runs past
    rife.HOLE_PAIR_STEPS here before it finds a set pairing all twenty. The search for a set pairing every hole, cut
    wherever the holes left cannot each keep a hole of each source frame of their own, finds one in a few dozen steps,
    and every hole is paired. Where that search runs past the steps too, the other answers, and here pairs none."""
    triples = _tangled(3, 20)
    assert rife._taken(triples) == list(range(20))
    monkeypatch.setattr(rife, "_whole", lambda cands: None)  # as though the search for a whole set ran past the steps
    assert rife._taken(triples) == []


def test_a_hole_the_first_two_readings_give_the_source_frames_takes_no_pair():
    """The fifth checkpoint validator's frame (`_beside`): a hole 8 px square that the first source frame and the frame
    between have in place and the second shuts, beside a hole moving 52 px across and 48 down, drawn at its exact
    midpoint 10 px from the first (within the moving hole's way: 4 px and 0.1 of its 70.8 px). The in-place reading
    gives the first hole to the source frames, so it takes no pair, and the moving hole keeps its own two: resampled to
    four, both frames made are kept, byte for byte, and so are they with the hole in place kept by the second source
    frame instead."""
    assert {k: _resampled(*(_beside(s, kept_by=k) for s in (0.0, 0.5, 1.0))) for k in (0, 1)} == {0: KEPT, 1: KEPT}


@pytest.mark.slow
def test_a_hole_more_whose_pair_the_first_two_readings_leave_free_is_read_as_theirs():
    """What the reading does not tell, pinned (docs/loop-repair.md section 4): a hole more than the source frames have
    is theirs where it lies within 4 px of where they have a hole, or where it pairs with one source frame's hole and
    the other's alike hole whose own holes in the frame between the first two readings explain. A column of three holes
    moving down (`_column`), its middle hole shut in the second source frame: the in-place reading gives the frame
    between's first and middle holes to the source frames, each lying against one of theirs, and the last pairs with
    its own two. A row of four holes standing still (`_row`), drawn a pixel taller with one hole more midway: the
    in-place reading gives the row's holes to the source frames, and the hole more pairs with one's hole and the
    other's next one. A body moving whole 32 px down (`_moving`) with a row of four holes 8 px square, 24 px apart, and
    one more midway between the middle two: the second reading carries the row, and the hole more pairs the same way.
    Each is kept as made. And a twin: the body moving whole 48 px across and 48 down with a gap 5 x 30 px through it,
    carried by the second reading, and one gap more 10 px beside it, 5 px of the body between them: it lies 10 px from
    the point at t 0.5 between the source frames' gaps, within 4 px and 0.1 of their 67.9 px (10.8 px), and pairs with
    them, so it reads `hole` 0 either way (the outline readings, readings of their own, give this frame way). A twin off
    that way gives way for `hole`: 11 px beside the gap, or 13 px beside a gap 8 px wide (5 px of the body between them
    again). A twin is let through only where the gap's width and 5 px are within 4 px and 0.1 of how far it moves."""
    seen = {"column, its middle hole shut in the second": _resampled(_column(0.0), _column(0.5), _column(1.0, shut=(1,)))
            == KEPT}
    for plain in (False, True):
        still = _row(0.0, plain=plain)
        seen["still row, a pixel taller, one more midway", plain] = _resampled(
            still, _row(0.5, plain=plain, taller=1, more=(0,)), still) == KEPT
    row = tuple((100 + ox - 4, 126, 100 + ox + 4, 134) for ox in (-36, -12, 12, 36))
    seen["row moving whole, one more midway"] = _resampled(
        _moving(0.0, (0, 32), row), _moving(0.5, (0, 32), row, ((96, 126, 104, 134),)), _moving(1.0, (0, 32), row)) == KEPT
    for width, off in ((5, 10), (5, 11), (8, 13)):
        gap = (95, 100, 95 + width, 130)
        a, b = _moving(0.0, (48, 48), (gap,)), _moving(1.0, (48, 48), (gap,))
        made = _moving(0.5, (48, 48), (gap,), ((95 + off, 100, 95 + off + width, 130),))
        seen["twin", width, off] = (rife.hole(made, a, b, 0.5) == rife.hole(made, b, a, 0.5) == 0) if off == 10 else (
            _given_way(a, made, b))
    assert seen == {k: True for k in seen}
