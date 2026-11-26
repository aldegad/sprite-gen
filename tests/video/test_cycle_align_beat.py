# SPDX-License-Identifier: Apache-2.0
"""The beat an aligned walk keeps, and the feet it steps with (docs/loop-repair.md section 4, sprite_gen/video/cadence.py).

A frame `--between auto` cannot make gives way to the nearer source frame. Where a cut holds its drawings for three
frames and the set's length puts a frame half way between two drawings, the nearer one is the next drawing: the legs
skip into that frame and halt out of it, the beat jolts. Where the jolt is BEAT_JOLT or more the same two source frames
are made again at another fraction: the one nearest the frame's own, with no fault and within BEAT_JOLT of its time, of
the choices over a run of such frames side by side that keep every gap under BEAT_JOLT. Two frames side by side each
made again nearest its own fraction can jolt the gap between them past it, so the choice is the run's, not each
frame's. A run no choice keeps has its jolt spread over two (or three) frames on each side, moved off their own time by
an even share of it and made there; one that cannot stays on the nearer source frame, named in the report and the
warnings. A loop whose frames given way already keep the beat is delivered as before.

Seen from the back the feet stand side by side and the stepping foot is drawn higher: a loop that lifts one foot twice
with no step of the other between steps the same foot twice, named — or, with `--same-foot refuse`, the set stopped —
with what every cut of its clip (`video-loop`'s `clip_feet`) says.

Synthetic figures only: an outlined body over two outlined feet. For the beat the feet slide along the ground together
by a block's width at each drawing (so every step reads alike); the interpolator draws them where the fraction puts them,
and smears the body where it is told the frame breaks. For the feet they stand still and lift in turn."""

from __future__ import annotations

import functools
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import align, cadence
from sprite_gen.video.interpolation_quality import GHOST_WINDOW_WARN
from sprite_gen.video import loop as loop_mod

H = 120
LINE, BODY, FOOT = (0, 0, 0, 255), (230, 230, 230, 255), (150, 90, 60, 255)
STEP = 8  # px the feet slide at each drawing: a block's width, so every step's blocks read alike


def _box(d: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, fill: tuple[int, ...]) -> None:
    d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=LINE)
    d.rectangle([x0 + 2, y0 + 2, x1 - 3, y1 - 3], fill=fill)


def _figure(width: int, left: float, right: float, *, lift: tuple[int, int] = (0, 0), smear: bool = False,
            height: int = H) -> Image.Image:
    """A body from row 10 over two feet 16 px wide (two blocks) and 10 high on the ground 10 px above the bottom, the left
    from column `left`, the right from `right`, each `lift` px off the ground; `smear` paints a dark blot inside the body,
    as RIFE smears a frame it cannot follow."""
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ground = height - 10
    _box(d, 60, 10, 120, ground - 15, BODY)
    for x, up in ((left, lift[0]), (right, lift[1])):
        x = round(x)
        _box(d, x, ground - 10 - up, x + 16, ground - up, FOOT)
    if smear:
        d.rectangle([80, 40, 87, 47], fill=(20, 20, 20, 255))
    return im


def _slide(drawing: int, drawings: int) -> int:
    """Where the feet stand at a drawing of a cycle of `drawings`: forward a step a drawing for half of it, then back."""
    half = drawings // 2
    return STEP * (drawing if drawing <= half else drawings - drawing)


class Walk:
    """A cut of `frames` frames, each drawing held `hold` frames (the first drawing beginning at frame `first`), the feet
    sliding a step at each drawing; and its interpolator, which draws the feet where the fraction puts them and smears
    the frame of a pair `breaks(i, t)` names."""

    def __init__(self, frames: int, hold: int, *, first: int = 0, width: int = 200, breaks=lambda i, t: False):
        self.count, self.width, self.breaks = frames, width, breaks
        drawings = frames // hold
        self.at = [_slide(((f - first) // hold) % drawings, drawings) for f in range(frames)]
        self.frames = [self._drawn(x) for x in self.at]
        self.calls: list[tuple[int, float]] = []

    def _drawn(self, x: float, smear: bool = False) -> Image.Image:
        return _figure(self.width, 8 + x, 96 + x, smear=smear)

    @functools.cached_property
    def path(self) -> tuple[float, ...]:
        """The leg path of the cut's frames (`cadence.leg_path`), read once a walk: every jolt a test reads of it
        (`_jolt`) is of the same frames."""
        return tuple(cadence.leg_path(self.frames))

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        i = next(k for k, f in enumerate(self.frames) if f is a)
        self.calls.append((i, round(t, 4)))
        x = self.at[i] + t * (self.at[(i + 1) % self.count] - self.at[i])
        return self._drawn(x, smear=self.breaks(i, round(t, 4)))


def _jolt(walk: Walk, cells: list[dict], length: int) -> float:
    """The largest beat jolt of a loop whose frames come from `cells` (`cadence.Beat`, the definition)."""
    beat = cadence.Beat(walk.path, length)
    return max(abs(j) for j in beat.jolts([beat.place(c) for c in cells]))


@pytest.mark.slow
def test_a_frame_half_way_between_two_held_drawings_is_made_again_where_the_nearer_drawing_breaks_the_beat():
    """A cut of 54 frames on threes (18 drawings) aligned to 24: a frame every 2.25 source frames, and every fourth is
    half way between two drawings, where RIFE smears. The nearer source frame there is the next drawing, two thirds of a
    beat early: the legs skip into it and halt out of it. Made again at 0.4 of its pair it stands 0.13 of a beat early."""
    walk = Walk(54, 3, first=-1, breaks=lambda i, t: abs(t - 0.5) < 0.08 and walk.at[i] != walk.at[(i + 1) % 54])
    steps = walk.path
    assert {round(s, 3) for k, s in enumerate(steps) if walk.at[k] == walk.at[(k + 1) % 54]} == {0.0}
    assert len({round(s, 3) for k, s in enumerate(steps) if walk.at[k] != walk.at[(k + 1) % 54]}) == 1
    cells: list[dict] = []
    out, facts = align.resample(walk.frames, 24, walk, cells_from=cells)
    half_way = [2, 6, 10, 14, 18, 22]  # source times 4.5, 13.5, ... 49.5: a drawing ends at 4, the next begins at 5
    assert _jolt(walk, cells, 24) < cadence.BEAT_JOLT
    assert [cells[k] for k in half_way] == [{"between": [i, i + 1], "t": 0.4} for i in (4, 13, 22, 31, 40, 49)]
    nearer = [dict(c) for c in cells]
    for k, i in zip(half_way, (5, 14, 23, 32, 41, 50)):
        nearer[k] = {"source": i}
    assert _jolt(walk, nearer, 24) == pytest.approx(2 / 3, abs=0.01)  # as the nearer drawing left it
    assert facts["beat"]["jolt"] == pytest.approx(_jolt(walk, cells, 24), abs=1e-3) == pytest.approx(0.4 / 3, abs=0.01)
    assert facts["beat"]["over"] == [] and facts["beat"]["named"] == [] and facts["beat"]["retimed"] == half_way
    assert facts["nearest_at"] == [] and set(half_way) <= set(facts["made_at"]) and facts["made_by_rife"] == 18
    smear = {m["at"]: m for m in facts["smear"]}
    for k in half_way:
        assert smear[k]["method"] == "retimed" and smear[k]["faults"] == ["smear"]
        assert smear[k]["retimed"]["t"] == 0.4 and smear[k]["retimed"]["off"] == pytest.approx(-0.4 / 3, abs=0.01)
        assert [x["t"] for x in smear[k]["retime"]["tried"]] == [0.45, 0.55, 0.4, 0.6, 0.35, 0.65]
        assert [x["faults"] for x in smear[k]["retime"]["tried"]] == [["smear"], ["smear"], [], [], [], []]
    assert all(out[k] is not walk.frames[cells[k]["between"][0]] for k in half_way)


class SeenThrough(Walk):
    """A Walk whose frames made at `t` carry a block of the body 6 x 7 px at alpha 0.5: no fault (its ghost band is 0.1 %
    of the body, its window about 1.1 %, under GHOST_WINDOW_WARN), but over RETIME_WINDOW_MAX."""

    def __init__(self, *args, t: float, **kw):
        super().__init__(*args, **kw)
        self.seen_at = t

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        frame = super().__call__(a, b, t)
        if abs(t - self.seen_at) < 1e-6:
            px = np.asarray(frame).copy()
            px[50:57, 80:86, 3] = 128
            frame = Image.fromarray(px, "RGBA")
        return frame


@pytest.mark.slow
def test_a_fraction_made_again_is_taken_only_where_it_reads_clean_in_every_window():
    """As the first case, but the frames made at 0.4 carry a block the background shows through: no fault, yet over
    RETIME_WINDOW_MAX in its window. Made only to keep the beat, it is passed over for the next clean fraction, 0.6."""
    walk = SeenThrough(54, 3, first=-1, t=0.4, breaks=lambda i, t: abs(t - 0.5) < 0.08 and walk.at[i] != walk.at[(i + 1) % 54])
    cells: list[dict] = []
    _, facts = align.resample(walk.frames, 24, walk, cells_from=cells)
    half_way = [2, 6, 10, 14, 18, 22]
    assert [cells[k] for k in half_way] == [{"between": [i, i + 1], "t": 0.6} for i in (4, 13, 22, 31, 40, 49)]
    smear = {m["at"]: m for m in facts["smear"]}
    for k in half_way:
        (passed,) = [x for x in smear[k]["retime"]["tried"] if x["t"] == 0.4]
        assert passed["faults"] == [] and align.RETIME_WINDOW_MAX < passed["ghost_window"] < GHOST_WINDOW_WARN
        assert passed["window_over"] == align.RETIME_WINDOW_MAX and smear[k]["retimed"]["t"] == 0.6
    assert facts["beat"]["over"] == [] and facts["beat"]["jolt"] == pytest.approx(0.4 / 3, abs=0.01)


def _stretched(clean: dict[tuple[int, int], set[float]]) -> Walk:
    """A cut of 20 frames, one drawing each, stretched to 30: frames 1 and 2 stand at source times 0.67 and 1.33, two
    thirds and a third of their pairs, where RIFE smears except at the fractions `clean` names for the pair."""
    return Walk(20, 1, width=208, breaks=lambda i, t: i in (0, 1) and t not in clean[(i, i + 1)])


@pytest.mark.slow
def test_two_frames_side_by_side_are_made_again_as_a_run_whose_gaps_keep_the_beat():
    """Frame 1 made again nearest its own fraction (0.75, an eighth of a beat late) and frame 2 nearest its own (0.2, a
    fifth of a beat early) each stand within BEAT_JOLT of their time, but the gap between them jolts by a third of a beat.
    Frame 1 made at 0.55 instead keeps every gap under it."""
    walk = _stretched({(0, 1): {0.55, 0.75}, (1, 2): {0.2}})
    cells: list[dict] = []
    _, facts = align.resample(walk.frames, 30, walk, cells_from=cells)
    each_nearest = [*cells[:1], {"between": [0, 1], "t": 0.75}, {"between": [1, 2], "t": 0.2}, *cells[3:]]
    assert _jolt(walk, each_nearest, 30) == pytest.approx(0.325, abs=0.01)  # over BEAT_JOLT
    assert cells[1:3] == [{"between": [0, 1], "t": 0.55}, {"between": [1, 2], "t": 0.2}]
    assert _jolt(walk, cells, 30) == pytest.approx(0.2, abs=0.01) and facts["beat"]["over"] == []
    assert facts["beat"]["retimed"] == [1, 2] and facts["beat"]["named"] == []


@pytest.mark.slow
def test_a_run_no_choice_keeps_on_the_beat_stays_on_the_nearer_frame_and_is_named():
    """With 0.55 not clean either, every choice for the two frames jolts a gap past BEAT_JOLT: both keep the nearer
    source frame, the run named."""
    walk = _stretched({(0, 1): {0.75}, (1, 2): {0.2}})
    cells: list[dict] = []
    _, facts = align.resample(walk.frames, 30, walk, cells_from=cells)
    assert cells[1:3] == [{"source": 1}, {"source": 1}]
    assert facts["nearest_at"] == [1, 2] and facts["beat"]["named"] == [[1, 2]] and facts["beat"]["retimed"] == []
    assert facts["beat"]["jolt"] == pytest.approx(1.0, abs=0.01) and [0, 1] in facts["beat"]["over"]
    assert {m["at"]: m["method"] for m in facts["smear"] if m["faults"]} == {1: "nearest", 2: "nearest"}


@pytest.mark.slow
def test_a_frame_no_fraction_keeps_spreads_its_jolt_over_the_frames_beside_it():
    """Frame 4 of the cut of 20 stretched to 30 stands at source time 2.67, two thirds of the pair 2 and 3, where RIFE
    smears from 0.4 on: no fraction within BEAT_JOLT of its time is clean, and the nearer source frame stands half a beat
    late. Two frames each side move a third and a sixth of a beat late, made there, and every gap jolts a sixth of a beat:
    the frame keeps the nearer source frame, its jolt shared."""
    walk = Walk(20, 1, width=208, breaks=lambda i, t: i == 2 and t >= 0.4)
    cells: list[dict] = []
    _, facts = align.resample(walk.frames, 30, walk, cells_from=cells)
    assert cells[4] == {"source": 3} and facts["nearest_at"] == [4] and facts["beat"]["named"] == []
    (spread,) = facts["beat"]["widened"]
    assert spread["run"] == [4] and spread["width"] == 2
    assert {m["at"]: round(m["off"], 2) for m in spread["moved"]} == {3: 0.33, 2: 0.17, 5: 0.33, 6: 0.17}
    assert [cells[k]["between"] for k in (2, 3, 5, 6)] == [[1, 2], [2, 3], [3, 4], [4, 5]]
    assert [cells[k]["t"] for k in (2, 3, 5, 6)] == pytest.approx([4 / 9, 2 / 9, 5 / 9, 1 / 9], abs=2e-4)
    assert all(type(cells[k]["t"]) is float for k in (2, 3, 5, 6))  # a number JSON writes as it reads
    assert facts["beat"]["jolt"] == pytest.approx(1 / 6, abs=0.01) and facts["beat"]["over"] == []
    assert _jolt(walk, cells, 30) == pytest.approx(1 / 6, abs=0.01)
    smear = {m["at"]: m for m in facts["smear"]}
    assert all(smear[k]["method"] == "moved" and smear[k]["moved"]["t"] == cells[k]["t"] for k in (2, 3, 5, 6))
    assert {3, 6} <= set(facts["made_at"])  # source frames before, made now


@pytest.mark.slow
def test_frames_given_way_that_keep_the_beat_are_delivered_as_before():
    """A cut of 40 frames squeezed to 24: a frame given way stands a fifth of a beat off its time on the nearer source
    frame, under BEAT_JOLT. Nothing is made again, and the loop is the one the nearer frame makes."""
    walk = Walk(40, 1, width=290, breaks=lambda i, t: i == 1)
    cells: list[dict] = []
    out, facts = align.resample(walk.frames, 24, walk, cells_from=cells)
    assert cells[1] == {"source": 2} and out[1] is walk.frames[2] and facts["nearest_at"] == [1]
    assert [c for c in walk.calls if c[0] == 1] == [(1, round(5 / 3 - 1, 4))]  # made once, never again
    assert facts["beat"]["jolt"] == pytest.approx(0.2, abs=0.01) and facts["beat"]["over"] == []
    assert "retimed" not in facts["beat"] and "retime" not in facts["smear"][0]


def test_a_loop_every_frame_of_which_stands_at_its_own_time_reads_no_jolt_without_reading_the_legs(monkeypatch):
    walk = Walk(20, 1, width=208)
    monkeypatch.setattr(cadence, "leg_path", lambda frames: pytest.fail("the legs read"))
    _, facts = align.resample(walk.frames, 30, walk)
    assert facts["beat"] == {"jolt": 0.0, "gap": None, "over": [], "limit": cadence.BEAT_JOLT, "band": cadence.LEG_PATH_BAND,
                             "path": "on-time"}


def test_between_nearest_records_the_beat_it_leaves():
    walk = Walk(54, 3, first=-1)
    _, facts = align.resample(walk.frames, 24, None, between="nearest")
    assert facts["beat"]["path"] == "read" and facts["beat"]["jolt"] == pytest.approx(2 / 3, abs=0.01)
    assert "retimed" not in facts["beat"]


def _back(lifts: list[tuple[int, int]], *, height: int = H) -> list[Image.Image]:
    """A loop seen from the back: the feet side by side, the left from column 50, the right from 110, each lifted as
    `lifts` says frame by frame (px of a box `height` - 20 px high)."""
    return [_figure(180, 50, 110, lift=up, height=height) for up in lifts]


def _lifted(frames: int, steps: list[tuple[int, int, int]], *, px: int = 2) -> list[tuple[int, int]]:
    """Per frame, the lift of each foot: each step (foot 0 left or 1 right, first frame, frames) rises `px` a frame to
    its middle and comes down the same way."""
    out = [[0, 0] for _ in range(frames)]
    for foot, first, span in steps:
        for j in range(span):
            out[(first + j) % frames][foot] = px * (min(j, span - 1 - j) + 1)
    return [tuple(x) for x in out]  # type: ignore[misc]


SAME_FOOT = [(1, 2, 5), (1, 9, 3), (0, 15, 5)]  # the right foot, down, the right again, then the left
ALTERNATE = [(1, 2, 5), (0, 14, 5)]


def test_a_loop_that_lifts_one_foot_twice_with_no_step_of_the_other_between_steps_the_same_foot_twice():
    same = cadence.same_foot(_back(_lifted(24, SAME_FOOT)), "back")
    assert same["read"] and same["order"] == "RRL" and same["twice"] == [[4, 10]]
    assert [(s["half"], s["frames"]) for s in same["steps"]] == [("R", [2, 3, 4, 5, 6]), ("R", [9, 10, 11]), ("L", [15, 16, 17, 18, 19])]
    alternate = cadence.same_foot(_back(_lifted(24, ALTERNATE)), "back_diagonal@left")
    assert alternate["read"] and alternate["order"] == "RL" and alternate["twice"] == []
    # read round the ring: turned to start mid-step, the same steps
    turned = _back(_lifted(24, SAME_FOOT))
    assert cadence.same_foot(turned[10:] + turned[:10], "back")["order"] == "RLR"
    assert cadence.same_foot(turned[10:] + turned[:10], "back")["twice"] == [[18, 0]]


def test_a_foot_down_between_two_lifts_of_it_makes_them_two_steps_and_a_foot_only_dipping_one():
    """A box 250 px high: 3 px a frame is over LIFT_MIN, 2 px between it and DOWN_MAX, 1 px under DOWN_MAX."""
    held = _lifted(24, [(1, 2, 10), (0, 15, 5)], px=3)
    held[6] = (0, 2)  # the right foot dips under LIFT_MIN between two lifts, not down to DOWN_MAX: one step
    assert cadence.same_foot(_back(held, height=270), "back")["order"] == "RL"
    held[6] = (0, 1)  # down to DOWN_MAX: two
    assert cadence.same_foot(_back(held, height=270), "back")["order"] == "RRL"


@pytest.mark.parametrize("view", ["front", "front_diagonal@right", "side@left", None])
def test_the_feet_are_read_only_where_they_stand_side_by_side_seen_from_behind(view):
    reading = cadence.same_foot(_back(_lifted(24, SAME_FOOT)), view)
    assert not reading["read"] and "not side by side" in reading["why"]


def test_a_loop_one_foot_of_which_never_steps_does_not_read():
    reading = cadence.same_foot(_back(_lifted(24, [(1, 2, 5), (1, 12, 5)])), "back")
    assert not reading["read"] and "only the picture's right half steps" in reading["why"]


def test_every_cut_of_a_clip_that_steps_one_foot_twice_every_cycle_steps_so():
    clip = _back(_lifted(24, SAME_FOOT) * 3)
    reading = cadence.clip_feet(clip, 24, first=2)
    # a cut starting inside a step (its last frame and its first lifted) is counted apart: 10 of every 24 starts here
    inside = {3, 4, 5, 6, 10, 11, 16, 17, 18, 19}
    assert reading == {"length": 24, "cuts": 49, "same_foot": 29, "alternate": 0, "unread": 0, "seam_in_step": 20,
                       "same_foot_at": [2 + s for s in range(49) if s % 24 not in inside]}
    walk = cadence.clip_feet(_back(_lifted(24, ALTERNATE) * 3), 24)
    assert walk["alternate"] == 33 and walk["same_foot"] == 0 and walk["seam_in_step"] == 16
    # a cut a cycle and a half long of a walk that alternates joins a step to the same foot's next
    assert cadence.clip_feet(_back(_lifted(24, ALTERNATE) * 3), 36)["same_foot"] > 0
    assert cadence.clip_feet(clip[:10], 24) == {"length": 24, "cuts": 0, "why": "the clip holds 10 frames, no cut of 24"}


def test_a_cut_whose_seam_joins_two_lifts_of_one_foot_is_not_a_cut_that_alternates():
    """A cut shorter than the cycle, from inside the second right lift to inside the first right lift of the next cycle:
    looped, its seam joins the two lifts into one, so it reads right then left, alternating, the second lift hidden in a
    seam that jumps mid-step. The clip's cuts count it apart, neither alternating nor the same foot twice."""
    clip = _back(_lifted(24, SAME_FOOT) * 3)
    looped = cadence.same_foot(clip[10:31], "back")
    assert looped["read"] and looped["twice"] == [] and sorted(looped["order"]) == ["L", "R"]
    assert cadence.clip_feet(clip[10:31], 21) == {"length": 21, "cuts": 1, "same_foot": 0, "alternate": 0, "unread": 0,
                                                  "seam_in_step": 1, "same_foot_at": []}


def _cut(tmp_path: Path, steps: list[tuple[int, int, int]]) -> Path:
    keyed = tmp_path / "keyed"
    keyed.mkdir()
    for k, f in enumerate(_back(_lifted(24, steps) * 2)):
        f.save(keyed / f"{k:04d}.png")
    out = tmp_path / "back"
    loop_mod.run_loop(keyed, out, fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                      name="walk", report_path=None, cycle_mode="fixed", start=0, length=24, anchor="none", repair="off")
    return out


@pytest.mark.slow
def test_an_aligned_loop_that_steps_the_same_foot_twice_is_named_and_with_refuse_the_set_is_stopped(tmp_path):
    d = _cut(tmp_path, SAME_FOOT)
    meta = json.loads((d / "walk.strip.json").read_text())
    inside = {3, 4, 5, 6, 10, 11, 16, 17, 18, 19}
    assert meta["clip_feet"] == {"length": 24, "cuts": 25, "same_foot": 15, "alternate": 0, "unread": 0, "seam_in_step": 10,
                                 "same_foot_at": [s for s in range(25) if s not in inside]}
    before = (d / "walk.strip.json").read_bytes()
    report = tmp_path / "set.cycle-align.json"
    with pytest.raises(SystemExit, match="steps the same foot twice in a row .* film this direction again .*--same-foot refuse"):
        align.align_set([d], views=["back"], report_path=report, same_foot="refuse")
    refused = json.loads(report.read_text())
    assert refused["applied"] is False and refused["refused"] == "same-foot" and refused["same_foot"][0]["twice"]
    assert (d / "walk.strip.json").read_bytes() == before
    done = align.align_set([d], views=["back"])
    row = done["loops"][0]
    assert row["same_foot"]["twice"] and row["same_foot"]["clip"]["same_foot"] == 15
    assert any(w.startswith("walk: film this direction again (same-foot): steps the same foot twice in a row")
               and "every cut of its clip 24 frames long that reads steps so (15 of 25; 10 more with their seam inside a step" in w
               for w in done["warnings"])
    assert row["retake"]["reasons"] == ["same-foot"] and row["retake"]["same_foot"]["remedy"] == "retake"
    assert row["retake"]["same_foot"]["clip"] == {"length": 24, "cuts": 25, "same_foot": 15, "alternate": 0, "unread": 0, "seam_in_step": 10}
    assert done["retake"] == [{"dir": row["dir"], "name": "walk", **row["retake"]}]
    assert json.loads((d / "walk.strip.json").read_text())["cycle_align"]["same_foot"] == row["same_foot"]
    assert row["beat"]["path"] == "on-time" and done["same_foot"] == "warn"


@pytest.mark.slow
def test_an_aligned_loop_that_alternates_its_feet_is_not_named(tmp_path):
    d = _cut(tmp_path, ALTERNATE)
    done = align.align_set([d], views=["back"], same_foot="refuse")
    assert done["loops"][0]["same_foot"]["order"] in ("RL", "LR") and done["loops"][0]["same_foot"]["twice"] == []
    assert not any("same foot" in w for w in done["warnings"])


def _retake(beat=None, feet=None, between="auto"):
    return align.retake({"hold": 1}, {"between": between, "to": 24, "nearest_at": [], "smear": []}, [], fps=24.0, beat=beat, feet=feet)


def test_a_beat_still_broken_and_the_same_foot_twice_are_reasons_to_cut_or_film_again():
    broken = {"path": "read", "jolt": 0.5, "gap": [5, 6], "over": [[4, 5], [5, 6]], "named": [[5, 6], [12]], "limit": cadence.BEAT_JOLT}
    r = _retake(beat=broken)
    assert r == {"reason": "broken-beat", "reasons": ["broken-beat"],
                 "broken_beat": {"jolt": 0.5, "gap": [5, 6], "over": [[4, 5], [5, 6]], "named": [5, 6, 12], "remedy": "recut-or-retake"},
                 "limits": {"beat_jolt": cadence.BEAT_JOLT}}
    assert _retake(beat=broken, between="nearest") is None  # nearest takes the nearer frame by choice: recorded, not named
    assert _retake(beat={**broken, "over": []}) is None and _retake(beat={"path": "on-time", "over": []}) is None
    feet = {"read": True, "order": "RLR", "twice": [[14, 3]]}
    every = {"length": 20, "cuts": 40, "same_foot": 30, "alternate": 0, "unread": 0, "seam_in_step": 10, "same_foot_at": [0]}
    assert _retake(feet={**feet, "clip": every})["same_foot"] == {"order": "RLR", "twice": [[14, 3]], "clip": {k: v for k, v in every.items() if k != "same_foot_at"},
                                                                   "remedy": "retake"}
    assert _retake(feet={**feet, "clip": {**every, "alternate": 3}})["same_foot"]["remedy"] == "recut"
    assert _retake(feet={**feet, "clip": None})["same_foot"] == {"order": "RLR", "twice": [[14, 3]], "clip": None, "remedy": "recut-or-retake"}
    both = _retake(beat=broken, feet={**feet, "clip": every})
    assert both["reasons"] == ["broken-beat", "same-foot"] and set(both["limits"]) == {"beat_jolt", "lift_min", "lift_cells", "down_max"}
    assert _retake(feet={**feet, "twice": []}) is None and _retake(feet={"read": False}) is None


def test_the_beat_breaks_named_in_the_warnings_in_words():
    row = {"beat": {"jolt": 0.55, "gap": [11, 12], "over": [[7, 8], [11, 12]], "limit": cadence.BEAT_JOLT, "named": [[12]]}}
    line = align._beat_line("walk-back", row)
    assert line.startswith("walk-back: its beat breaks — the legs jolt 0.55 of a beat between frames 11 and 12 (at gaps 7-8, 11-12")
    assert "frame(s) 12 took the nearer source frame" in line and "film the direction again" in line
    retake = align.retake({"hold": 1}, {"between": "auto", "to": 27}, [], fps=24.0, beat={**row["beat"], "path": "read"})
    said = align._retake_line("walk-back", retake, "broken-beat", row)
    assert said.startswith("walk-back: cut it again or film this direction again (broken-beat): its beat breaks — the legs jolt 0.55")


def test_the_turned_beat_names_frames_of_the_turned_loop():
    record = {"jolt": 0.5, "gap": [1, 2], "over": [[0, 1], [1, 2]], "jolts": [0.3, -0.5, 0.1, 0.1], "limit": 0.25}
    assert cadence.turned(record, 1) == {**record, "gap": [0, 1], "over": [[0, 1], [3, 0]], "jolts": [-0.5, 0.1, 0.1, 0.3]}
    assert np.isclose(sum(record["jolts"]), sum(cadence.turned(record, 3)["jolts"]))
