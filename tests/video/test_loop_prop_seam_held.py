# SPDX-License-Identifier: Apache-2.0
"""A held staff that swings once a cycle closes below the hand only on a cut two steps long
(docs/loop-repair.md section 3, "The held side").

The walker's legs come back every 24 frames — a step, as the search reads it; the staff it holds
turns about the hand once every 48, once a cycle of two steps, as the arm that holds it swings, its
tip above the head one way and its foot below the hand the other. A cut one step long whose top
closes takes the staff where its tip passes the same place moving the other way: the top band reads
no jump, and the foot swings back at the wrap. `--anchor motion-auto` reads the held side's edge on a
cut whose top popped, and takes a window two steps long — one cycle — when no one-step cut closes it.

Synthetic frames only; the staff's foot is known in closed form, so the tests read the cut against it."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import align, local_cycle, loop, repair

STEP, SWING, TIP, LOW, PHASE, FRAMES = 24, 48, 14, 14, math.pi / 2, 97


def _foot(k: int, swing: int = SWING) -> float:
    """The staff foot's sideways place against the body, px."""
    return -LOW * math.sin(2 * math.pi * k / swing + PHASE)


def _walker(k: int, *, staff: bool = True, tip_swing: int = SWING, foot_swing: int = SWING,
            tip_amp: float = TIP, flag: tuple[int, float] | None = None) -> Image.Image:
    """A walker holding a staff at the hand: a rod up to the tip and one down to the foot, each turning
    on its own beat (one staff, turning about the hand, when the two beats are the same). `flag`
    (beat, phase) hangs a pennant off the tip that sways on a beat of its own."""
    im = Image.new("RGBA", (300, 240))
    d = ImageDraw.Draw(im)
    x = 45 + k
    y = 70 + round(k * .2) + round(3 * math.sin(k * 2 * math.pi / STEP))
    d.rectangle((x, y, x + 28, y + 30), fill=(210, 150, 60, 255))
    d.rectangle((x - 3, y + 32, x + 30, y + 78), fill=(20, 90, 180, 255))
    leg = round(12 * math.sin(k * 2 * math.pi / STEP))
    d.rectangle((x + leg, y + 79, x + 6 + leg, y + 119), fill=(80, 30, 60, 255))
    d.rectangle((x + 24 - leg, y + 79, x + 30 - leg, y + 119), fill=(30, 60, 90, 255))
    d.point((x + leg + 2, y + 85 + k % 12), fill=(240, 240, 240, 255))  # no two frames the same picture
    if not staff:
        return im
    hand = (x + 38, y + 50)
    tip = (hand[0] + tip_amp * math.sin(2 * math.pi * k / tip_swing + PHASE), y - 45)
    d.line([hand, tip], fill=(120, 80, 40, 255), width=3)
    d.line([hand, (hand[0] + 8 + _foot(k, foot_swing), y + 122)], fill=(120, 80, 40, 255), width=3)
    d.ellipse((tip[0] - 4, tip[1] - 4, tip[0] + 4, tip[1] + 4), fill=(230, 200, 60, 255))
    if flag is not None:
        fx = tip[0] - 12 + 14 * math.sin(2 * math.pi * k / flag[0] + flag[1])
        d.ellipse((fx - 5, tip[1] - 12, fx + 5, tip[1] - 2), fill=(200, 40, 40, 255))
    return im


def _keyed(root: Path, **kw) -> Path:
    keyed = root / "keyed"
    keyed.mkdir(parents=True)
    for k in range(FRAMES):
        _walker(k, **kw).save(keyed / f"{k:03}.png")
    return keyed


def _cut(root: Path, keyed: Path, *extra: str) -> dict:
    out = root / "out"
    assert loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
                      "--anchor", "motion-auto", "--repair", "off", *extra]) == 0
    return json.loads((out / "loop.loop.report.json").read_text())


def _foot_miss(start: int, length: int, swing: int = SWING) -> float:
    """How far the cut's wrap is from the staff foot's own way on, in place and in speed, over the
    largest step the foot takes inside the cut."""
    f = lambda k: _foot(k, swing)  # noqa: E731
    s, L = start, length
    inner = max(abs(f(k + 1) - f(k)) for k in range(s, s + L - 1))
    place = abs(f(s) - f(s + L))
    speed = abs((f(s + 1) - f(s)) - (f(s + L + 1) - f(s + L)))
    return max(place, speed) / inner


@pytest.fixture(scope="module")
def held(tmp_path_factory) -> tuple[Path, dict]:
    root = tmp_path_factory.mktemp("held")
    return root, _cut(root, _keyed(root))


def test_motion_auto_takes_two_steps_where_the_held_staff_closes_only_there(held):
    root, report = held
    cycle = report["cycle"]
    shape = cycle["seam_pop"]
    # The staff's foot closes at the cut: at the wrap it is where, and moving as, the clip had it.
    assert _foot_miss(cycle["start"], cycle["length"]) <= 0.5
    # The first choice popped at the top; a one-step cut whose top closes swings the foot back.
    assert shape["first_choice"]["pop"] > repair.SEAM_POP_REFERENCE
    calm_one_step = [r for r in cycle["candidates"] if r.get("held_edge") is not None]
    assert calm_one_step and all(r["held_edge"] >= repair.HELD_EDGE_REFERENCE for r in calm_one_step)
    assert min(_foot_miss(r["start"], r["length"]) for r in calm_one_step) > 1.5
    # So the cut is two steps long — one cycle, the step it is two of recorded — and the staff's foot closes there.
    assert cycle["length"] in (2 * STEP - 1, 2 * STEP, 2 * STEP + 1) and cycle["period_local"] == cycle["length"]
    steps = cycle["steps"]
    assert steps["count"] == 2 and steps["by"] == "held-edge" and set(steps["step_lengths"]) <= {STEP - 1, STEP, STEP + 1}
    assert cycle["coverage"] == round((FRAMES - cycle["length"]) / cycle["length"], 4)
    held_edge = shape["held_edge"]
    assert held_edge["side"] == "right" and held_edge["doubled"] is True
    assert held_edge["chosen"] == {"start": cycle["start"], "length": cycle["length"], "seam": held_edge["chosen"]["seam"], "closes": True}
    assert shape["chosen"]["steps"] == 2 and shape["applied"] is True
    strip = json.loads((root / "out" / "loop.strip.json").read_text())
    assert strip["steps"] == 2 and "cycles" not in strip and strip["cycle_frames"] == cycle["length"]
    assert not any("held part" in line for line in report["jolt"]["warnings"])


def test_a_held_part_that_swings_with_the_step_is_cut_one_step_long(tmp_path):
    """A pennant at the tip sways on a beat of its own (53 frames) and pops at the first choice; the
    staff stands straight above the hand and swings with the step below it, so a one-step cut whose
    top closes closes below the hand too and is taken. The first choice's pop is 12.6 and no other
    candidate's reaches 8.5; the cut taken closes below the hand at 0.0 (over 1.2 does not)."""
    report = _cut(tmp_path, _keyed(tmp_path, tip_amp=0, foot_swing=STEP, flag=(53, 0.75)))
    cycle = report["cycle"]
    shape = cycle["seam_pop"]
    assert shape["first_choice"]["pop"] > repair.SEAM_POP_REFERENCE and shape["applied"] is True
    assert "steps" not in cycle and cycle["length"] in (STEP - 1, STEP, STEP + 1)
    assert shape["held_edge"]["doubled"] is False and shape["held_edge"]["chosen"]["closes"] is True
    assert "two_step" not in shape["held_edge"]
    assert _foot_miss(cycle["start"], cycle["length"], STEP) <= 0.5


def test_a_held_part_no_window_closes_is_kept_on_the_top_bands_choice_and_warned(tmp_path):
    """The foot swings every 37 frames: neither one step nor two closes it. The cut is the top band's,
    and the jolt says the held part does not close."""
    report = _cut(tmp_path, _keyed(tmp_path, tip_swing=SWING, foot_swing=37))
    cycle = report["cycle"]
    held_edge = cycle["seam_pop"]["held_edge"]
    assert "steps" not in cycle and held_edge["two_step"]["closing"] == 0
    assert held_edge["chosen"]["closes"] is False and held_edge["chosen"]["seam"] >= repair.HELD_EDGE_REFERENCE
    lines = [line for line in report["jolt"]["warnings"] if "held part does not close" in line]
    assert len(lines) == 1 and "right side" in lines[0]


def test_an_explicit_max_len_is_not_widened_by_a_window_two_steps_long(tmp_path):
    """--max-len 25 is the caller's ceiling: the windows two steps long (47-49 frames) that would close
    the staff are over it and are not read. The cut is the top band's, one step long, and the jolt says
    the held part does not close and why nothing longer was looked at."""
    report = _cut(tmp_path, _keyed(tmp_path), "--max-len", "25")
    cycle = report["cycle"]
    assert cycle["length"] <= 25 and "steps" not in cycle
    held_edge = cycle["seam_pop"]["held_edge"]
    assert held_edge["two_step_capped"] == {"max_len": 25, "lengths_over": [2 * STEP - 1, 2 * STEP + 1]}
    assert "two_step" not in held_edge and held_edge["chosen"]["closes"] is False
    assert "steps" not in json.loads((tmp_path / "out" / "loop.strip.json").read_text())
    (line,) = [line for line in report["jolt"]["warnings"] if "held part does not close" in line]
    assert "over --max-len 25" in line


def test_a_walk_whose_top_does_not_pop_reads_nothing_below_it(tmp_path):
    report = _cut(tmp_path, _keyed(tmp_path, staff=False))
    shape = report["cycle"]["seam_pop"]
    assert shape["applied"] is False and "held_edge" not in shape and "steps" not in report["cycle"]
    assert not any(r.get("held_edge") is not None for r in report["cycle"]["candidates"])


# --- the held side, read on edges -------------------------------------------------------------

def _edges(series: list[float], rows: int = 30) -> np.ndarray:
    """A rod edge at the same column on every row, per frame."""
    return np.repeat(np.asarray(series, dtype=np.float64)[:, None], rows, axis=1)


def test_held_edge_reads_place_and_speed_at_the_wrap():
    period = 24
    wave = [100 + 10 * math.sin(2 * math.pi * k / period) for k in range(80)]
    closes = repair.held_edge(_edges(wave), 10, period)
    assert closes["closes"] is True and closes["seam"] < 0.2 and closes["rows"] == 30
    # Half a period: the edge comes back to its place moving the other way — it swings back at the wrap.
    swings = repair.held_edge(_edges([100 + 10 * math.sin(2 * math.pi * k / 48) for k in range(80)]), 12, period)
    assert swings["closes"] is False and swings["seam"] > repair.HELD_EDGE_REFERENCE
    # A row empty in some frames is read on the frames it has; a row empty everywhere is not read.
    gappy = _edges(wave)
    gappy[:, :5] = np.nan
    gappy[11, 5] = np.nan
    assert repair.held_edge(gappy, 10, period)["rows"] == 25


def test_held_edge_needs_two_clip_frames_either_side_of_the_cut():
    edges = _edges([100.0 + k % 24 for k in range(60)])
    for start, length in ((1, 24), (0, 24), (35, 24), (36, 24)):
        with pytest.raises(ValueError, match="no two frames either side"):
            repair.held_edge(edges, start, length)
    assert "seam" in repair.held_edge(edges, 2, 24) and "seam" in repair.held_edge(edges, 34, 24)
    with pytest.raises(ValueError, match="transparent"):
        repair.held_edge(np.full((60, 10), np.nan), 10, 24)


def test_held_side_is_the_side_the_top_band_sits_on():
    body = np.zeros((100, 60), bool)
    body[30:100, 20:40] = True
    right, left = body.copy(), body.copy()
    right[0:30, 48:50] = True
    left[0:30, 8:10] = True
    assert repair.held_side([right, right]) == "right" and repair.held_side([left]) == "left"
    edges = repair.side_edges([right], "right")
    assert edges[0, 10] == 49 and edges[0, 50] == 39
    assert repair.side_edges([left], "left")[0, 10] == 8
    with pytest.raises(ValueError):
        repair.held_side([np.zeros((10, 10), bool)])


def test_held_edge_verdict_speaks_only_for_a_held_side_that_does_not_close():
    assert repair.held_edge_verdict(None) == [] and repair.held_edge_verdict({"applied": False}) == []
    closes = {"held_edge": {"side": "right", "reference": 1.2, "chosen": {"start": 1, "length": 46, "seam": 0.7, "closes": True}}}
    assert repair.held_edge_verdict(closes) == []
    misses = {"held_edge": {"side": "left", "reference": 1.2, "chosen": {"start": 1, "length": 23, "seam": 2.1, "closes": False}}}
    (line,) = repair.held_edge_verdict(misses)
    assert "left side" in line and "2.10x" in line and "over 1.2" in line
    (line,) = repair.held_edge_verdict({"held_edge": {"method": "held-side-edge-v1", "skipped": "every loop frame is fully transparent"}})
    assert "could not be read" in line
    (line,) = repair.held_edge_verdict({"held_edge": {"side": "right", "reference": 1.2,
                                                       "chosen": {"start": 0, "length": 23, "skipped": "the clip has no two frames"}}})
    assert "could not be read at this cut" in line
    capped = {"held_edge": {**misses["held_edge"], "two_step_capped": {"max_len": 25, "lengths_over": [45, 47]}}}
    (line,) = repair.held_edge_verdict(capped)
    assert "no window one length long closes it" in line and "(45-47 frames) are over --max-len 25" in line
    assert "beat of its own" not in line
    counted = {"held_edge": {**misses["held_edge"], "two_step": {"read": False, "why": "the steps were counted"}}}
    (line,) = repair.held_edge_verdict(counted)
    assert "no window of the counted length closes it" in line and "beat of its own" not in line


# --- choosing on it ---------------------------------------------------------------------------

class _FakeHeld:
    """A `loop.HeldEdge` reading a table: (start, length) -> seam, or a reason string it cannot read."""

    @property
    def reference(self):
        return repair.HELD_EDGE_REFERENCE

    def __init__(self, table, frames=80, side="right"):
        self.table, self.frames, self._side = table, frames, side
        self.read = []

    def side(self, start, length):
        return {"skipped": self._side} if self._side.startswith("no ") else {"side": self._side}

    def seam(self, start, length, side):
        self.read.append((start, length))
        value = self.table.get((start, length), 5.0)
        if isinstance(value, str):
            return {"skipped": value, "reference": self.reference}
        return {"seam": value, "closes": value < self.reference, "rows": 30, "reference": self.reference}


def _pop(table, default=0.0):
    def pop(start, length):
        value = table.get((start, length), default)
        return {"pop": value, "pops": value > repair.SEAM_POP_REFERENCE, "reference": repair.SEAM_POP_REFERENCE}
    return pop


ROWS = [{"start": 0, "length": 24, "score": 1.0}, {"start": 3, "length": 24, "score": 1.01}, {"start": 6, "length": 24, "score": 1.05}]


def test_shape_rank_takes_a_one_length_cut_whose_held_side_closes():
    candidates = [dict(r) for r in ROWS]
    fake = _FakeHeld({(3, 24): 1.0, (6, 24): 0.5})
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _pop({(0, 24): 14.0}), fake)
    assert chosen["start"] == 6 and record["held_edge"]["doubled"] is False
    assert record["chosen"] == {"start": 6, "length": 24, "pop": 0.0}
    assert all(length == 24 for _, length in fake.read)  # nothing two lengths long was read


def test_shape_rank_takes_two_steps_when_no_one_length_cut_closes_and_never_an_unread_one():
    candidates = [dict(r) for r in ROWS]
    fake = _FakeHeld({(3, 24): "the clip has no two frames either side of the cut to read its wrap against",
                      (10, 48): 0.9, (12, 47): 0.4, (20, 49): 0.3})
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _pop({(0, 24): 14.0, (20, 49): 12.0}), fake)
    # (20, 49) closes below but its top pops: not taken. (12, 47) has the smallest held seam left: two
    # steps of 24 — one cycle, screened as one (`period_local` is its own length).
    assert (chosen["start"], chosen["length"], chosen["period_local"]) == (12, 47, 47)
    assert chosen["steps"] == {"count": 2, "by": "held-edge", "step_lengths": [24]}
    held = record["held_edge"]
    assert held["doubled"] is True and held["unread"] == 1 and held["two_step"]["closing"] == 2
    assert held["two_step"]["top_pops"] == 1 and held["two_step"]["lengths"] == [47, 49]
    assert candidates[1]["held_edge"] is None and candidates[1]["held_edge_skipped"]
    assert record["chosen"] == {"start": 12, "length": 47, "pop": 0.0, "steps": 2}


def test_shape_rank_reads_no_window_two_lengths_long_over_the_length_cap_nor_once_the_steps_are_counted():
    table = {(10, 48): 0.9, (12, 47): 0.4, (20, 49): 0.3}
    # Under a cap of 46 no window two lengths long is read: the top band's choice, and the record says why.
    candidates = [dict(r) for r in ROWS]
    fake = _FakeHeld(dict(table))
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _pop({(0, 24): 14.0}), fake, length_cap=46)
    assert (chosen["start"], chosen["length"]) == (3, 24) and "steps" not in chosen
    assert all(length <= 46 for _, length in fake.read)
    held = record["held_edge"]
    assert held["two_step_capped"] == {"max_len": 46, "lengths_over": [47, 49]} and "two_step" not in held
    # A cap of 48 reads 47 and 48 only: (20, 49) is over it, so (12, 47) is taken.
    candidates = [dict(r) for r in ROWS]
    fake = _FakeHeld(dict(table))
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _pop({(0, 24): 14.0}), fake, length_cap=48)
    assert (chosen["start"], chosen["length"], chosen["steps"]["count"]) == (12, 47, 2)
    assert record["held_edge"]["two_step"]["lengths"] == [47, 48] and max(length for _, length in fake.read) == 48
    assert record["held_edge"]["two_step_capped"] == {"max_len": 48, "lengths_over": [49, 49]}
    # Counted steps (`double` false): the length is the count's, and nothing twice it is read.
    candidates = [dict(r) for r in ROWS]
    fake = _FakeHeld(dict(table))
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _pop({(0, 24): 14.0}), fake, double=False)
    assert (chosen["start"], chosen["length"]) == (3, 24) and all(length == 24 for _, length in fake.read)
    assert record["held_edge"]["two_step"] == {"read": False, "why": "the steps were counted (--steps): no window twice as long is read"}


def test_shape_rank_without_a_closing_window_keeps_the_top_bands_choice_and_records_it():
    candidates = [dict(r) for r in ROWS]
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _pop({(0, 24): 14.0}), _FakeHeld({}, frames=60))
    assert chosen["start"] == 3 and record["chosen"]["pop"] == 0.0 and "steps" not in chosen
    assert record["held_edge"]["chosen"] == {"start": 3, "length": 24, "seam": 5.0, "closes": False}
    assert record["held_edge"]["two_step"]["closing"] == 0
    # A side that cannot be read: the top band's choice, and the record says why.
    candidates = [dict(r) for r in ROWS]
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _pop({(0, 24): 14.0}), _FakeHeld({}, side="no head"))
    assert chosen["start"] == 3 and record["held_edge"] == {"method": "held-side-edge-v1", "skipped": "no head"}


def test_shape_rank_reads_no_held_side_when_the_first_choice_does_not_pop():
    fake = _FakeHeld({})
    candidates = [dict(r) for r in ROWS]
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _pop({}), fake)
    assert chosen["start"] == 0 and "held_edge" not in record and fake.read == []


# --- the set ----------------------------------------------------------------------------------

def test_cycle_align_aligns_a_two_step_loop_as_the_one_cycle_it_is(held, tmp_path):
    """The loop cut two steps long is one cycle: it returns half way at its second step, which is no
    suspect (status `steps`), and it is resampled to the set's length like the rest — the set's length
    the median of the loops as cut, nothing kept twice as long."""
    root, report = held
    other = tmp_path / "plain"
    plain = _cut(other, _keyed(other, staff=False))
    lengths = [report["cycle"]["length"], plain["cycle"]["length"]]
    out = align.align_set([root / "out", other / "out"], between="nearest")
    two, one = out["loops"]
    target = round(float(np.median(lengths)))
    assert out["applied"] is True and out["length"] == target and two["to"] == target and one["to"] == target
    assert "cycles_kept" not in two and "cycles_kept" not in one
    mine = [e for e in out["suspects"] if e["dir"] == str((root / "out").resolve())]
    assert mine and all(e["status"] == "steps" and e["steps"] == 2 and [c["cycles"] for c in e["candidates"]] == [2] for e in mine)
    assert isinstance(report["cycle"]["ratio"], float)  # a window two lengths long reads its wrap as a candidate does
    assert json.loads((root / "out" / "loop.strip.json").read_text())["steps"] == 2
    assert "steps" not in json.loads((other / "out" / "loop.strip.json").read_text())
    # A count given for it is still the last word: one half taken out of it, no longer two steps.
    out = align.align_set([root / "out", other / "out"], between="nearest", cycles={str(root / "out"): 2})
    assert "cycle_taken" in out["loops"][0]
    assert "steps" not in json.loads((root / "out" / "loop.strip.json").read_text())


# What v2.35.0 wrote for a loop whose steps nobody counted (no `steps` in its strip metadata), and the
# source each cut names since 2.39 (docs/loop-comparison.md): aligning it writes exactly these, so a set
# without a held part is rewritten as before.
STRIP_KEYS_2350 = {"body_h", "body_height_target", "body_ref", "body_src_h", "cell_cap", "cell_height_cap", "cycle_align",
                   "cycle_drawings", "cycle_frames", "cycle_seconds", "delay_ms", "drawings", "drift_px", "foot_anchor",
                   "foot_sway_px", "foot_x", "frames", "h", "kind", "loop", "motion_anchor", "scale", "state", "subsampled",
                   "top_margin_px", "w"}
SOURCE_KEYS_2390 = {"sample_indices", "source_cut", "source_rect"}
CYCLE_ALIGN_KEYS_2350 = {"between", "cycle_screen", "cycles_given", "drawings", "foot_why", "fps", "from", "made_at",
                         "made_by_rife", "nearest_at", "reach_swing", "retake", "seam_ratio", "source", "start_foot",
                         "start_foot_source", "stride_swing", "strikes", "taken", "to", "turned_by", "turned_on", "view"}
# 2.41.0: where every cell is from, and the cut the first alignment was made from (video-follow --read-on)
CORRESPONDENCE_KEYS_2410 = {"cells_from", "origin"}
# 2.43.0: the ghost screen of the loop as filmed, and each cell where a filmed ghost gave way or was kept
GHOST_KEYS_2430 = {"ghost_screen", "ghost_at"}


def test_cycle_align_writes_a_loop_without_a_held_part_as_before(tmp_path):
    src = tmp_path / "src"
    report = _cut(src, _keyed(src, staff=False))
    cut = json.loads((src / "out" / "loop.strip.json").read_text())
    # The cut names its cells' source: the keyed frames it read, its window in them, and each cell's frame
    # there — every frame of the window one cell, under one crop.
    start, length = report["cycle"]["start"], report["cycle"]["length"]
    assert cut["frames"] == length and cut["sample_indices"] == list(range(length))
    assert cut["source_cut"] == {"source": report["source"], "start": start, "length": length,
                                 "samples": list(range(start, start + length))}
    left, top, right, bottom = cut["source_rect"]
    assert cut["scale"] == 1.0 and [right - left, bottom - top, top] == [cut["w"], cut["h"], cut["top_margin_px"]]
    dirs = []
    for name in ("a", "b"):
        shutil.copytree(src / "out", tmp_path / name)
        dirs.append(tmp_path / name)
    out = align.align_set(dirs, between="nearest")
    assert all("cycles_kept" not in row and "steps" not in row for row in out["loops"])
    for d in dirs:
        meta = json.loads((d / "loop.strip.json").read_text())
        assert set(meta) == STRIP_KEYS_2350 | SOURCE_KEYS_2390
        assert set(meta["cycle_align"]) == CYCLE_ALIGN_KEYS_2350 | CORRESPONDENCE_KEYS_2410 | GHOST_KEYS_2430
        # The cut's source stays as the cut wrote it: `cycle_align` says the cells were turned after it, so it
        # no longer names each cell's frame (docs/loop-comparison.md). The rebuilt strip names its own cells —
        # the same frames, turned, under the same crop.
        assert meta["source_cut"] == cut["source_cut"]
        assert meta["sample_indices"] == list(range(length)) and meta["source_rect"] == cut["source_rect"]
