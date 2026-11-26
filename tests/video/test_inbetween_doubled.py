# SPDX-License-Identifier: Apache-2.0
"""A double outline (docs/loop-repair.md section 4): `rife.doubled` reads a line one source frame draws that a made frame
left at part strength in its own place, and the cycle alignment gives such a frame way to the nearer source frame
(`--between auto`). Where RIFE's flow does not follow a line — the top of a boot one source frame tilts and the other
plants — it can fade the line out instead of moving it, and the frame shows it, lighter, beside the line it drew. Its
coverage is whole, nothing darker is added and its edge keeps its line, so the ghost screen, the smear and both outline
readings pass it; the cross-fade reading names whole drawings blended, and is no fault. A line both source frames draw
within 4 px is theirs, at any strength; a line the made frame carries at its source frame's own strength is that frame's
drawing; the soft edge of a line the flow moved lies beside the line at full strength; a line left less than 50/255
darker than the other source frame shows there is not seen; a run shorter than 3 % of the body is not read. A line only
one source frame draws — a crease that comes and goes — is read the same way: left at part strength, it is a double
outline too.

The fixtures are synthetic: a coat over two legs in boots, outlined 3 px in near-black, with a hand beside the coat; the
near boot lifted 18 px in the first source frame and planted in the second. The frame drawn between, and the same with a
source frame's near-boot lines laid over it at part strength; blurred, smeared along the step (a ghost by its window
reading, its feet part-covered); a hand moving 60 px over the coat; boots drawn with a second line; a hem drawn with two
lines and softened; a crease one source frame draws. And
the walker of tests/video/test_crossfade.py: its drawing between far-apart drawings, and its cross-fades."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from sprite_gen.video import align, rife
from sprite_gen.video.interpolation_quality import CROSSFADE_LOOK, DOUBLED_WARN, faults, looks
from tests.video.test_crossfade import BLACK, _inside

W, H = 200, 260
LINE = (20, 16, 12, 255)
COAT, LEG, BOOT = (250, 240, 235, 255), (190, 120, 80, 255), (240, 200, 40, 255)
LIGHT = (150, 120, 60, 255)  # a light crease: 72 levels darker than the boot, under 50/255 once faded to half
LIFTED, PLANTED = 176, 194  # the near boot's top: lifted in the first source frame, planted in the second
NEAR = np.zeros((H, W), dtype=bool)
NEAR[:, 100:150] = True  # the near boot's columns


def _figure(near: float, *, hand: float | None = 150, over: float | None = None, cuff: bool = False, hem: bool = False,
            crease: tuple[int, ...] | None = None) -> Image.Image:
    """A coat over two legs in boots, the far boot's top at 200 and the near boot's at `near`, lines 3 px; a hand beside
    the coat at column `hand`, or over it at column `over`. `cuff`: a second line 5 px under each boot's top; `hem`: the
    coat's hem drawn with two lines; `crease`: a line of that colour across the near boot, 14 px under its top."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def box(x0: float, y0: float, x1: float, y1: float, fill: tuple[int, ...], r: int = 0) -> None:
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=LINE)
        d.rounded_rectangle([x0 + 3, y0 + 3, x1 - 3, y1 - 3], radius=max(0, r - 3), fill=fill)

    for x, top in ((62, 200), (108, near)):
        box(x + 4, 140, x + 30, top + 6, LEG)
        box(x, top, x + 34, top + 46, BOOT, r=8)
        if cuff:
            d.rectangle([x + 3, top + 8, x + 31, top + 10], fill=LINE)
        if crease is not None and x == 108:
            d.rectangle([x + 3, top + 14, x + 31, top + 16], fill=crease)
    box(50, 40, 150, 150, COAT, r=10)
    if hem:
        d.rectangle([53, 138, 147, 140], fill=LINE)
    if hand is not None:
        box(int(hand) - 14, 110, int(hand) + 14, 136, LEG, r=10)
    if over is not None:
        box(over - 14, 70, over + 14, 96, LEG, r=10)
    return im


def _step(t: float, **kw) -> Image.Image:
    """The figure at fraction t of the step: the near boot from lifted to planted."""
    return _figure(LIFTED + t * (PLANTED - LIFTED), **kw)


def _lines(im: Image.Image) -> np.ndarray:
    x = np.asarray(im, dtype=np.float32) / 255
    return (x[..., 3] >= 0.5) & (x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114 < 0.2)


def _left(drawn: Image.Image, source: Image.Image, k: float, region: np.ndarray = NEAR) -> Image.Image:
    """`drawn` with the lines of `source` it has not got laid over it at strength k, where it is solid inside `region`:
    a line RIFE faded out in its source frame's place instead of moving it."""
    x = np.asarray(drawn, dtype=np.float32).copy()
    m = _lines(source) & ~_lines(drawn) & (x[..., 3] >= 128) & region
    x[m, :3] = (1 - k) * x[m, :3] + k * np.asarray(source, dtype=np.float32)[m, :3]
    return Image.fromarray(np.uint8(x + 0.5), "RGBA")


def _pre(im: Image.Image) -> np.ndarray:
    x = np.asarray(im, dtype=np.float32) / 255
    return np.dstack([x[..., :3] * x[..., 3:], x[..., 3:]])


def _unpre(p: np.ndarray) -> Image.Image:
    a = p[..., 3:]
    return Image.fromarray(np.uint8(np.clip(np.dstack([p[..., :3] / np.maximum(a, 1e-6), a]), 0, 1) * 255 + 0.5), "RGBA")


def _soft(im: Image.Image, radius: float) -> Image.Image:
    """`im` blurred, premultiplied: a soft frame."""
    p = _pre(im)
    return _unpre(np.dstack([np.asarray(Image.fromarray(np.uint8(np.clip(p[..., c], 0, 1) * 255 + 0.5)).filter(
        ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255 for c in range(4)]))


def _smeared(make, t: float, spread: float, n: int = 7) -> Image.Image:
    """The figure at t smeared along its way: the mean of n drawings from t - spread to t + spread."""
    return _unpre(np.mean([_pre(make(t + spread * (2 * j / (n - 1) - 1))) for j in range(n)], axis=0))


def _hand(t: float) -> Image.Image:
    """A hand moving 60 px across the coat, over it, the boots still."""
    return _figure(185, hand=None, over=75 + 60 * t)


def _readings(made: Image.Image, a: Image.Image, b: Image.Image, t: float = 0.5) -> dict[str, float | None]:
    """Every reading the alignment judges a made frame by but the double outline's."""
    return {**rife.smear(made, a, b), "outline_shift": rife.outline_shift(made, a, b), "ghost": rife.ghost(made),
            "crossfade": rife.crossfade(made, a, b, t), "hole": rife.hole(made, a, b, t)}


def _resampled(a: Image.Image, b: Image.Image, there: Image.Image, back: Image.Image, between: str = "auto"):
    """The two source frames resampled to four (times 0, .5, 1, 1.5), RIFE's frames `there` (a to b) and `back` (b to a)."""
    made = {(id(a), id(b)): there, (id(b), id(a)): back}
    return align.resample([a, b], 4, lambda x, y, t: made[(id(x), id(y))], between=between)


def test_a_line_left_at_part_strength_is_a_double_outline_that_every_reading_before_passes():
    """The near boot's old top faded out at 0.45 beside the top the frame drew: coverage whole, no part coverage, nothing
    darker, its edge outlined — no fault before; `doubled` reads the run of the line left, 0.102 of the body's height
    (its own length, the boot's width). The same with both source frames' lines left at half, and the frame whose colour
    is the plain blend of the two inside the drawing's coverage, read the same."""
    a, b, between = _step(0), _step(1), _step(0.5)
    made = _left(between, a, 0.45)
    measure = _readings(made, a, b)
    assert faults(measure) == [] and looks(measure) == []
    assert measure["ghost"] == 0 and measure["hole"] == 0 and measure["crossfade"] < CROSSFADE_LOOK
    assert rife.doubled(made, a, b) == pytest.approx(0.1022, abs=5e-4)
    assert faults({**measure, "doubled": rife.doubled(made, a, b)}) == ["doubled"]
    assert rife.doubled(between, a, b) == 0 and rife.doubled(a, a, b) == 0 and rife.doubled(b, a, b) == 0
    assert rife.doubled(_left(_left(between, a, 0.5), b, 0.5), a, b) > DOUBLED_WARN
    blend = (_pre(a) + _pre(b)) / 2
    inside = np.asarray(between, dtype=np.float32)[..., 3:] / 255
    faded = _unpre(np.dstack([blend[..., :3] / np.maximum(blend[..., 3:], 1e-6) * inside, inside]))
    assert faults(_readings(faded, a, b)) == [] and rife.doubled(faded, a, b) > DOUBLED_WARN


def test_auto_gives_a_frame_with_a_double_outline_way_to_the_nearer_source_frame(monkeypatch):
    """Resampled two to four, each frame RIFE made leaving its first source frame's near-boot lines at 0.45: auto takes the
    nearer source frame at both (cells 1 and 3, half way rounding up), the fault named in the warning's words; rife
    keeps both, named. Both resamplings judge the same two frames made between the same source frames at the same
    times: each is measured once (`align.measure_made`, kept by what it was asked), and rife reads auto's measures."""
    measured: dict[tuple[int, int, int, float, bool], dict] = {}
    measure = align.measure_made

    def once(frame, x, y, t, *, reads):
        key = (id(frame), id(x), id(y), t, reads)  # every frame asked about lives to the end of the test
        if key not in measured:
            measured[key] = measure(frame, x, y, t, reads=reads)
        return dict(measured[key])

    monkeypatch.setattr(align, "measure_made", once)
    a, b = _step(0), _step(1)
    there, back = _left(_step(0.5), a, 0.45), _left(_step(0.5), b, 0.45)
    out, facts = _resampled(a, b, there, back)
    assert [m["at"] for m in facts["smear"]] == [1, 3] and facts["nearest_at"] == [1, 3] and facts["made_by_rife"] == 0
    assert all(m["method"] == "nearest" and m["faults"] == ["doubled"] and m["doubled"] > DOUBLED_WARN for m in facts["smear"])
    assert out[1] is b and out[3] is a
    line = align._fault_line("walk", facts["smear"][0])
    assert "at part strength in its own place" in line and "a double outline" in line and "(--between auto)" in line
    out, facts = _resampled(a, b, there, back, between="rife")
    assert out[1] is there and out[3] is back and all(m["method"] == "rife" and m["faults"] == ["doubled"] for m in facts["smear"])


def test_a_line_only_one_source_frame_draws_left_at_part_strength_is_a_double_outline():
    """A crease the first source frame draws across the still near boot and the second does not — a line that comes or
    goes, with no line of the other's to pair it within 4 px: left at 0.3 to 0.8 of its contrast it is a double outline
    and gives way; the frame that draws it, or does not, keeps its bytes; left at 0.25 it is lighter than 50/255 under
    the boot and not seen."""
    a, b, plain = _figure(185, crease=LINE), _figure(185), _figure(185)
    for k in (0.3, 0.45, 0.6):
        assert rife.doubled(_left(plain, a, k), a, b) > DOUBLED_WARN
    assert rife.doubled(_left(plain, a, 0.25), a, b) == 0 and rife.doubled(a, a, b) == 0 and rife.doubled(b, a, b) == 0
    out, facts = _resampled(a, b, _left(plain, a, 0.45), _left(plain, b, 0.45))
    assert facts["smear"][0]["faults"] == ["doubled"] and out[1] is b
    out, facts = _resampled(a, b, a, b)
    assert facts["made_by_rife"] == 2 and out[1] is a and out[3] is b


def _clean() -> dict[str, tuple[Image.Image, Image.Image, Image.Image]]:
    """(first source frame, second, the frame made at t 0.5) of frames that keep their bytes."""
    lit = _figure(LIFTED, crease=LIGHT)
    own = np.asarray(_step(0.5), dtype=np.float32).copy()
    drawn = (np.abs(np.asarray(lit, dtype=np.float32) - np.asarray(_figure(LIFTED), dtype=np.float32)).sum(-1) > 0) & (own[..., 3] >= 128)
    own[drawn, :3] = np.asarray(lit, dtype=np.float32)[drawn, :3]
    hem = np.asarray(_step(0.5, hem=True), dtype=np.float32).copy()
    lines = _lines(_step(0.5, hem=True))
    lines[:134] = lines[146:] = False
    hem[lines, :3] = 0.4 * hem[lines, :3] + 0.6 * np.array(COAT[:3])
    return {
        "drawn between": (_step(0), _step(1), _step(0.5)),
        "drawn between, soft": (_step(0), _step(1), _soft(_step(0.5), 1.5)),
        "a hand moving 60 px over the coat": (_hand(0), _hand(1), _hand(0.5)),
        "the hand, soft": (_hand(0), _hand(1), _soft(_hand(0.5), 1.5)),
        "the hand, smeared along its way": (_hand(0), _hand(1), _smeared(_hand, 0.5, 0.15)),
        "boots drawn with a second line": (_step(0, cuff=True), _step(1, cuff=True), _step(0.5, cuff=True)),
        "a hem both draw with two lines, softened": (_step(0, hem=True), _step(1, hem=True), Image.fromarray(np.uint8(hem + 0.5), "RGBA")),
        "a light crease carried at its own strength": (lit, _figure(PLANTED), Image.fromarray(np.uint8(own + 0.5), "RGBA")),
    }


def test_feet_smeared_along_the_step_read_no_double_outline_and_are_a_ghost_in_their_window():
    """The drawing between smeared along the step (the mean of drawings from t 0.35 to 0.65): no double outline, but the
    feet are left part-covered over a band 4.5 % of the solid pixels in the feet's tenth of the body, so the frame is a
    ghost by its window reading (`rife.ghost_window`) though 0.25 % of the whole body; smeared half as far, it is not."""
    a, b = _step(0), _step(1)
    made = _smeared(_step, 0.5, 0.15)
    assert rife.doubled(made, a, b) == 0
    m = align.measure_made(made, a, b, 0.5, reads=True)
    assert m["ghost"] < 0.003 < 0.014 < m["ghost_window"] and m["ghost_window_at"][0] > 0.85 and faults(m) == ["ghost"]
    assert faults(align.measure_made(_smeared(_step, 0.5, 0.075), a, b, 0.5, reads=True)) == []


@pytest.mark.parametrize("name", list(_clean()))
def test_a_clean_soft_or_source_drawn_frame_reads_no_double_outline_and_keeps_its_bytes(name):
    """The drawing between, or blurred; a hand moving fast over the coat, drawn, soft or smeared;
    boots both source frames draw with two lines; a hem both draw with two lines, softened to 60 % in the frame between (a
    line both draw is theirs at any strength); a light crease one draws, carried at its own strength: each reads 0, passes
    every fault and is delivered as the very frame made."""
    a, b, made = _clean()[name]
    assert rife.doubled(made, a, b) == 0
    out, facts = _resampled(a, b, made, made)  # each made half way, the same drawing either way
    assert all(m["faults"] == [] and m["method"] == "rife" for m in facts["smear"]) and out[1] is made and out[3] is made


def test_the_walkers_drawing_between_keeps_its_bytes_and_its_cross_fade_gives_way_where_its_lines_left_run_long():
    """The walker of tests/video/test_crossfade.py, legs closing 16 to 3 degrees: the drawing between, which the cross-fade
    reading reads over its line in grey (why that reading names and never judges), reads no double outline in grey or
    black and keeps its bytes; the colour blended inside its coverage leaves both drawings' lines at half and gives way.
    Legs closing 10 to 5 degrees, their cross-fade's lines left inside every silhouette run 0.0195 of the body — the rest
    of its doubled edge lies on the silhouette, where one source frame is not solid — under the line, and it is kept."""
    for kw in ({}, {"outline": BLACK, "boot": None}):
        a, b, drawn, faded = _inside((16, -16), (3, -3), 0.5, **kw)
        assert rife.doubled(drawn, a, b) == 0
        _, _, back, _ = _inside((3, -3), (16, -16), 0.5, **kw)
        out, facts = _resampled(a, b, drawn, back)
        assert all(m["method"] == "rife" and m["faults"] == [] for m in facts["smear"]) and out[1] is drawn and out[3] is back
        assert rife.doubled(faded, a, b) > DOUBLED_WARN and "doubled" in faults({**_readings(faded, a, b), "doubled": rife.doubled(faded, a, b)})
    a, b, _, faded = _inside((10, -10), (5, -5), 0.5)
    assert rife.doubled(faded, a, b) == pytest.approx(0.0195, abs=5e-4)


def test_the_double_outlines_named_bounds():
    """Where each bound turns, on the boot's old top left in the frame drawn between (its line 0.69 darker than the boot):
    seen — left at 0.28 of its contrast it is 0.1960 darker than the boot, just under 50/255 (0.1961), and not read; at
    0.30, 0.2076, read. Part strength — at 0.80, read; at 0.82, more than 0.8 of its contrast, it is the source frame's own
    line. Moved — the boot moved 8 px, the pixels of its old line the reading keeps are too thin to hold a 3 px square,
    and are not read; moved 9 px, read. Width — a dark patch 10 px square one source frame draws is no line (the closing, 2.5 %
    of the body high, keeps it), where a stroke 3 px wide is. Length — left over 6 columns the run is 0.0294 of the body,
    under 3 %; over 7, 0.0343. And what the reading does not tell: a hand drawn smeared along its way, back over the line
    one source frame draws there (t 0.3 to 0.7 of a 60 px move), reads as that line left."""
    a, b, between = _step(0), _step(1), _step(0.5)
    reads = lambda made, x=a, y=b: rife.doubled(made, x, y)
    assert reads(_left(between, a, 0.28)) == 0 and reads(_left(between, a, 0.30)) > DOUBLED_WARN
    assert reads(_left(between, a, 0.80)) > DOUBLED_WARN and reads(_left(between, a, 0.82)) == 0
    for moved, read in ((8, False), (9, True)):
        x, y = _figure(LIFTED), _figure(LIFTED + moved)
        assert (reads(_left(_figure(LIFTED + moved / 2), x, 0.45), x, y) > DOUBLED_WARN) is read

    def patched(shape: list[int]) -> Image.Image:
        im = _figure(185)
        ImageDraw.Draw(im).rectangle(shape, fill=LINE)
        return im

    for shape, read in (([120, 205, 129, 214], False), ([114, 208, 137, 210], True)):
        x = patched(shape)
        assert (reads(_left(_figure(185), x, 0.45, np.ones((H, W), dtype=bool)), x, _figure(185)) > DOUBLED_WARN) is read
    for columns, value in ((6, 0.0294), (7, 0.0343)):
        region = np.zeros((H, W), dtype=bool)
        region[170:182, 120:120 + columns] = True
        assert reads(_left(between, a, 0.45, region)) == pytest.approx(value, abs=5e-4)
    assert reads(_smeared(_hand, 0.5, 0.2), _hand(0), _hand(1)) > DOUBLED_WARN
