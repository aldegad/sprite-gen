# SPDX-License-Identifier: Apache-2.0
"""A thin part held above the head that sways on its own beat (docs/loop-repair.md section 3,
"The seam pop").

The walker steps every 24 frames; the staff it holds sways every 61, so a cut one step long ends
with the staff's tip somewhere else unless it is taken where the tip passes the same place again.
The seam ratio is an area measure and hardly sees a thin rod move. `--anchor motion-auto` chooses
again among its candidates when the top of the silhouette jumps into the first frame, a kept jump
is a warning line, and a search that finds no cycle reports the windows it measured and refused.

Synthetic frames only; the tip's place is known in closed form, so the tests read the cut against it."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import local_cycle, loop, repair

STEP, SWAY, AMP = 24, 61, 14


def _tip(k: int, phase: float) -> float:
    """The staff tip's sideways place against the body, px."""
    return AMP * math.sin(2 * math.pi * k / SWAY + phase)


def _walker(k: int, *, phase: float = 1.0, legs=None, tip_dy: float = 0.0, staff: bool = True,
            room: int = 0) -> Image.Image:
    """`room` px more canvas above the walker, for a tip raised higher than the usual canvas holds."""
    im = Image.new("RGBA", (280, 220 + room))
    d = ImageDraw.Draw(im)
    x = 45 + k
    y = 58 + room + round(k * .2) + round(3 * math.sin(k * 2 * math.pi / STEP))
    d.rectangle((x, y, x + 28, y + 30), fill=(210, 150, 60, 255))
    d.rectangle((x + 20, y + 8, x + 24, y + 12), fill=(10, 30, 50, 255))
    d.rectangle((x - 3, y + 32, x + 30, y + 78), fill=(20, 90, 180, 255))
    d.rectangle((x + 10, y + 35, x + 15, y + 73), fill=(210, 190, 40, 255))
    leg = round(12 * math.sin(k * 2 * math.pi / STEP)) if legs is None else legs(k)
    d.rectangle((x + leg, y + 79, x + 6 + leg, y + 119), fill=(80, 30, 60, 255))
    d.rectangle((x + 24 - leg, y + 79, x + 30 - leg, y + 119), fill=(30, 60, 90, 255))
    d.point((x + leg + 2, y + 85 + k % 12), fill=(240, 240, 240, 255))
    if not staff:
        return im
    hand = (x + 34, y + 50)
    tip = (x + 34 + _tip(k, phase), y - 40 + tip_dy)
    d.line([hand, tip], fill=(120, 80, 40, 255), width=3)
    d.ellipse((tip[0] - 4, tip[1] - 4, tip[0] + 4, tip[1] + 4), fill=(230, 200, 60, 255))
    return im


def _keyed(tmp_path: Path, frames: list[Image.Image]) -> Path:
    keyed = tmp_path / "keyed"
    keyed.mkdir()
    for k, im in enumerate(frames):
        im.save(keyed / f"{k:03}.png")
    return keyed


def _wrap_over_step(start: int, length: int, phase: float) -> float:
    """The tip's jump from the cut's last frame to its first, over the largest step inside the cut."""
    inner = max(abs(_tip(k + 1, phase) - _tip(k, phase)) for k in range(start, start + length - 1))
    return abs(_tip(start, phase) - _tip(start + length - 1, phase)) / inner


def test_motion_auto_does_not_cut_where_the_staff_jumps(tmp_path):
    keyed = _keyed(tmp_path, [_walker(k) for k in range(73)])
    out = tmp_path / "out"
    assert loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
                      "--anchor", "motion-auto", "--repair", "off"]) == 0
    report = json.loads((out / "loop.loop.report.json").read_text())
    cycle = report["cycle"]
    assert cycle["length"] in (STEP - 1, STEP, STEP + 1)
    # The cut closes the staff too: its tip at the wrap moves no more than a step inside the loop moves it.
    assert _wrap_over_step(cycle["start"], cycle["length"], 1.0) <= 1.5
    # The first choice, by the area measure alone, ended with the tip elsewhere; it was chosen again.
    shape = cycle["seam_pop"]
    assert shape["applied"] is True and shape["first_choice"]["pop"] > repair.SEAM_POP_REFERENCE
    first = shape["first_choice"]
    assert _wrap_over_step(first["start"], first["length"], 1.0) > 3
    assert shape["chosen"] == {"start": cycle["start"], "length": cycle["length"], "pop": shape["chosen"]["pop"]}
    assert report["jolt"]["seam_pop"]["pops"] is False
    assert not any("jumps into the loop's first frame" in line for line in report["jolt"]["warnings"])


def test_a_window_whose_top_cannot_be_read_is_not_taken_as_closing(tmp_path):
    """One frame (50) has the staff's tip flung 90 px up: every window holding it has a frame with nothing
    in the top band, and its pop cannot be read. Such a window is neither calm nor chosen; before, it was
    recorded as pop 0 and chosen (29/24, the tip's wrap 8x its largest step) over a read one that closes."""
    keyed = _keyed(tmp_path, [_walker(k, room=100, tip_dy=-90 if k == 50 else 0) for k in range(73)])
    out = tmp_path / "out"
    assert loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
                      "--anchor", "motion-auto", "--repair", "off"]) == 0
    report = json.loads((out / "loop.loop.report.json").read_text())
    cycle = report["cycle"]
    assert not cycle["start"] <= 50 < cycle["start"] + cycle["length"]
    assert _wrap_over_step(cycle["start"], cycle["length"], 1.0) <= 1.5
    shape = cycle["seam_pop"]
    assert shape["applied"] is True and shape["first_choice"]["pop"] > repair.SEAM_POP_REFERENCE
    assert shape["unread"] > 0 and shape["measured"] + shape["unread"] == cycle["candidate_count"]
    assert isinstance(shape["chosen"]["pop"], float) and "skipped" not in shape["chosen"]
    assert report["jolt"]["seam_pop"]["pops"] is False
    unread = [r for r in cycle["candidates"] if r.get("seam_pop_skipped")]
    assert all(r["seam_pop"] is None for r in unread)


def _fake_pop(table):
    """A `wrap_pop` reading a table: (start, length) -> pop, or a reason string for a top it cannot read."""
    def pop(start, length):
        value = table[start, length]
        if isinstance(value, str):
            return {"skipped": value, "reference": repair.SEAM_POP_REFERENCE}
        return {"pop": value, "pops": value > repair.SEAM_POP_REFERENCE, "reference": repair.SEAM_POP_REFERENCE}
    return pop


def test_shape_rank_keeps_an_unread_first_choice_and_never_chooses_an_unread_candidate():
    rows = [{"start": 0, "length": 24, "score": 1.0}, {"start": 3, "length": 24, "score": 1.01},
            {"start": 6, "length": 24, "score": 1.05}]
    chosen, record = local_cycle._shape_rank([dict(r) for r in rows], dict(rows[0]),
                                             _fake_pop({(0, 24): "a loop frame has no head to track"}))
    assert chosen["start"] == 0 and record["applied"] is False
    assert record["first_choice"] == {"start": 0, "length": 24, "skipped": "a loop frame has no head to track"}
    candidates = [dict(r) for r in rows]
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _fake_pop(
        {(0, 24): 14.0, (3, 24): "a loop frame has no head to track", (6, 24): 0.0}))
    assert chosen["start"] == 6 and record["chosen"] == {"start": 6, "length": 24, "pop": 0.0}
    assert record["measured"] == 2 and record["unread"] == 1
    assert candidates[1]["seam_pop"] is None and candidates[1]["seam_pop_skipped"]
    # Nothing read closes: the first choice is kept, not the unread one.
    candidates = [dict(r) for r in rows]
    chosen, record = local_cycle._shape_rank(candidates, candidates[0], _fake_pop(
        {(0, 24): 14.0, (3, 24): "a loop frame has no head to track", (6, 24): 12.0}))
    assert chosen["start"] == 0 and record["applied"] is False and "why" in record


def test_a_refused_window_whose_top_cannot_be_read_is_not_listed_as_closing():
    values = np.arange(80) + np.sin(np.arange(80))
    D = np.abs(values[:, None] - values[None, :]).astype(np.float32)
    kinds = {}

    def pop(start, length):
        kind = kinds.setdefault((start, length), ("pops", "unread", "calm")[len(kinds) % 3])
        if kind == "unread":
            return {"skipped": "a loop frame has no head to track", "reference": repair.SEAM_POP_REFERENCE}
        value = 14.0 if kind == "pops" else 0.0
        return {"pop": value, "pops": kind == "pops", "reference": repair.SEAM_POP_REFERENCE}
    with pytest.raises(ValueError, match="no periodic cycle") as caught:
        local_cycle.detect(D, np.zeros(80), min_len=12, max_len=36, gait_floor=14,
                           periodicity_min=.15, double_tolerance=.25, double_search=3, wrap_pop=pop)
    rows = caught.value.diagnostics["candidates"]
    order = [kinds[r["start"], r["length"]] for r in rows]
    assert order == sorted(order, key=("calm", "unread", "pops").index) and "unread" in order
    for r in rows:
        assert (r["seam_pop"] is None) == (kinds[r["start"], r["length"]] == "unread") == ("seam_pop_skipped" in r)


def test_a_cut_without_a_part_on_its_own_beat_is_chosen_as_before(tmp_path):
    """No staff: the wrap does not pop, nothing past the first choice is measured."""
    keyed = _keyed(tmp_path, [_walker(k, staff=False) for k in range(73)])
    out = tmp_path / "out"
    assert loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
                      "--anchor", "motion-auto", "--repair", "off"]) == 0
    shape = json.loads((out / "loop.loop.report.json").read_text())["cycle"]["seam_pop"]
    assert shape["applied"] is False and "measured" not in shape


def _alphas(tops: list[int], xs: list[int]) -> list[np.ndarray]:
    """A block body with a 2 px rod on top: the rod's top row and column per frame."""
    out = []
    for top, x in zip(tops, xs):
        a = np.zeros((120, 80), np.float32)
        a[40:118, 20:60] = 1
        a[top:40, x:x + 2] = 1
        out.append(a)
    return out


def test_seam_pop_is_the_wrap_over_the_median_when_the_wrap_is_the_largest_step():
    n = 20
    smooth = _alphas([10] * n, [30 + k // 4 for k in range(n)])
    calm = repair.seam_pop(smooth)
    assert calm["pops"] is False and calm["x"]["wrap_is_largest"]
    # The rod's top rises a little every frame and drops back at the wrap: y pops.
    rising = _alphas([22 - k for k in range(n)], [30] * n)
    popped = repair.seam_pop(rising)
    assert popped["y"]["wrap_is_largest"] and popped["y"]["wrap_over_median"] > repair.SEAM_POP_REFERENCE
    assert popped["pops"] is True and popped["pop"] == popped["y"]["wrap_over_median"]
    # A rod that jumps further inside the loop than at the wrap: the wrap is ordinary for it.
    jitter = repair.seam_pop(_alphas([10] * n, [30 + k // 4 if k != 9 else 60 for k in range(n)]))
    assert jitter["x"]["wrap_is_largest"] is False and jitter["pop"] == 0 and jitter["pops"] is False


def test_a_kept_jump_up_or_down_at_the_wrap_is_a_warning_line(tmp_path, capsys):
    """A fixed cut, nothing chosen again: the staff's tip rises along the cut and drops back at the wrap."""
    frames = [_walker(k, phase=0.0, tip_dy=-(k % STEP)) for k in range(73)]
    out = tmp_path / "out"
    assert loop.main(["--frames-dir", str(_keyed(tmp_path, frames)), "--out-dir", str(out), "--state", "walk",
                      "--cycle", "fixed", "--start", "0", "--length", str(STEP), "--anchor", "none", "--repair", "off",
                      "--seam-max", "1000"]) == 0
    jolt = json.loads((out / "loop.loop.report.json").read_text())["jolt"]
    assert jolt["seam_pop"]["pops"] is True and jolt["seam_pop"]["y"]["wrap_is_largest"]
    line = [w for w in jolt["warnings"] if "jumps into the loop's first frame" in w]
    assert len(line) == 1 and "up or down" in line[0]
    assert "video-loop: warning: the top of the silhouette jumps into the loop's first frame" in capsys.readouterr().err


def test_a_sideways_jump_the_head_bound_names_is_not_said_twice():
    measured = {"head": {"x": {"step_max_pct": 2.0, "worst_into_frame": 0}},
                "seam_pop": {"reference": 10.0, "pops": True, "pop": 14.0,
                             "x": {"wrap_pct": 2.0, "wrap_over_median": 14.0, "wrap_is_largest": True},
                             "y": {"wrap_pct": 0.1, "wrap_over_median": 1.0, "wrap_is_largest": False}}}
    assert repair.seam_pop_verdict(measured) == []
    under = {**measured, "head": {"x": {"step_max_pct": 0.5, "worst_into_frame": 0}}}
    assert len(repair.seam_pop_verdict(under)) == 1 and "sideways" in repair.seam_pop_verdict(under)[0]


def test_a_search_that_finds_nothing_reports_the_windows_it_refused():
    values = np.arange(80) + np.sin(np.arange(80))  # moves on and never comes back
    D = np.abs(values[:, None] - values[None, :]).astype(np.float32)
    with pytest.raises(ValueError, match="no periodic cycle") as caught:
        local_cycle.detect(D, np.zeros(80), min_len=12, max_len=36, gait_floor=14,
                           periodicity_min=.15, double_tolerance=.25, double_search=3)
    found = caught.value.diagnostics
    assert found["status"] == "refused" and found["refused_count"] >= len(found["candidates"]) > 0
    rows = found["candidates"]
    assert len(rows) <= local_cycle.REFUSED_SHOWN
    assert [r["score"] for r in rows] == sorted(r["score"] for r in rows)
    for r in rows:
        assert {"start", "length", "ratio", "score", "refused"} <= set(r)
        assert r["start"] >= 0 and r["length"] >= 12 and r["start"] + r["length"] <= 80


def test_a_refused_motion_auto_cut_leaves_measured_windows_in_the_report(tmp_path):
    """The legs spread apart over the clip and never come back: the search refuses, and the report
    names windows to deliver from anyway (before, `cycle` was null and a forced cut took the whole clip)."""
    keyed = _keyed(tmp_path, [_walker(k, legs=lambda j: round(-12 + 24 * j / 72)) for k in range(73)])
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="no periodic cycle found"):
        loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
                   "--anchor", "motion-auto", "--repair", "off"])
    report = json.loads((out / "loop.loop.report.json").read_text())
    assert report["status"] == "failed"
    rows = report["cycle"]["candidates"]
    assert rows and all({"start", "length", "ratio", "seam_pop", "refused"} <= set(r) for r in rows)
    assert all(r["start"] + r["length"] <= 73 for r in rows)
