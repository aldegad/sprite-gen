# SPDX-License-Identifier: Apache-2.0
"""An explicit window (--min-len / --max-len) is the window on every path that sets a cut's length:
the first search, a window two steps long (the held side), a count of steps (`--steps 1`), the gait
fallback, the one-shot failover and a fixed cut. Each path asks `loop.CutWindow` for its window, and
the cut is held to the caller's bounds (`CutWindow.hold`) whatever chose it: a cut inside them, or a
failure — never a cut outside them (docs/loop-review.md, "An explicit --max-len is never widened").

Synthetic frames only, the clips of the tests that own each path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import as_pinned
from sprite_gen.video import loop
from tests.video import test_gait_fallback as fallback_clips
from tests.video import test_loop_prop_seam_held as held_clips
from tests.video import test_one_step_cut as step_clips
from tests.video.test_video_pipeline import _one_shot_frames


def _slow(root: Path) -> Path:
    return step_clips._keyed(root)  # a 42-frame cycle the walk window holds one step (21) of; its feet pass every 21


def _short(root: Path) -> Path:
    return step_clips._keyed(root, frames=50)  # the same, too short to see a 42-frame window repeat


def _held(root: Path) -> Path:
    return held_clips._keyed(root)  # a staff that closes below the hand only two steps (47-49) long


def _slow_front(root: Path) -> Path:
    keyed = root / "keyed"
    keyed.mkdir(parents=True)
    for k in range(73):
        fallback_clips.walker(k, period=40).save(keyed / f"{k:03}.png")  # a cycle past half the clip
    return keyed


def _hop(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _one_shot_frames(root)  # one hop, frames 60-76: no period, a one-shot
    return root / "keyed"


MOTION = ("--anchor", "motion-auto", "--repair", "off")

# path, clip, arguments, what the report shows the path ran, whether the cut is made at all, and the runs of RIFE its
# held fill asks for where the binary is found (two a frame between)
ROWS = [
    # A window that holds one step of a walk whose steps are seen: no cut of two steps there, and none of one.
    ("first", _slow, ("--state", "walk", *MOTION, "--max-len", "25"),
     lambda r: "gait_fallback" not in r and r["error"].startswith("no cut of two steps in --max-len 25"), False, 0),
    ("first", _slow, ("--state", "walk", *MOTION, "--min-len", "18"), lambda r: "gait_fallback" not in r, True, 8),
    ("two-step", _held, ("--state", "walk", *MOTION, "--max-len", "25"),
     lambda r: r["cycle"]["seam_pop"]["held_edge"]["two_step_capped"]["max_len"] == 25, True, 0),
    ("two-step", _held, ("--state", "walk", *MOTION, "--max-len", "47"),  # 47 of 47-49 read
     lambda r: r["cycle"]["seam_pop"]["held_edge"]["two_step"]["lengths"] == [47, 47]
     and r["cycle"]["seam_pop"]["held_edge"]["two_step_capped"] == {"max_len": 47, "lengths_over": [48, 49]}, True, 0),
    ("two-step", _held, ("--state", "walk", *MOTION, "--min-len", "20"), lambda r: r["cycle"]["seam_pop"]["applied"], True, 0),
    ("counted", _short, ("--state", "walk", *MOTION, "--max-len", "42", "--steps", "1"),
     lambda r: r["cycle"]["steps"]["window"] == [41, 42] and r["cycle"]["steps"]["counted_window"] == [41, 43], True,
     {"rife-v4.25": (8, 22), "rife-v4.6": 8}),
    ("counted", _slow, ("--state", "walk", *MOTION, "--min-len", "18", "--steps", "1"), lambda r: r["cycle"]["steps"]["by"] == "given", True, 8),
    ("fallback", _slow_front, ("--state", "walk", *MOTION, "--max-len", "36"), lambda r: r["gait_fallback"]["window"] == [12, 36], False, 0),
    ("fallback", _slow_front, ("--state", "walk", *MOTION, "--min-len", "30"), lambda r: r["gait_fallback"]["window"][0] == 30, True,
     {"rife-v4.25": (4, 16), "rife-v4.6": 22}),
    ("one-shot", _hop, ("--state", "jump", "--max-len", "30"), lambda r: r["periodic_attempt"] is not None and r["cycle"]["kind"] == "one-shot", True, 0),
    ("one-shot", _hop, ("--state", "jump", "--min-len", "30"), lambda r: r["periodic_attempt"] is not None and r["cycle"]["kind"] == "one-shot", True, 0),
    ("fixed", _hop, ("--state", "jump", "--cycle", "fixed", "--start", "50", "--length", "40", "--max-len", "30"),
     lambda r: r["cycle"]["window_refused"] == {"min_len": None, "max_len": 30}, False, 0),
]


def _bound(args: tuple[str, ...], flag: str) -> int | None:
    return int(args[args.index(flag) + 1]) if flag in args else None


@pytest.mark.slow
@pytest.mark.timeout(900)  # up to 22 calls of the binary at about 280 x 200 (a row's runs): seconds on a GPU, minutes on a CPU's Vulkan driver
@pytest.mark.parametrize("path, clip, args, ran, cut, runs", ROWS, ids=[f"{r[0]}{''.join(r[2][-2:])}" for r in ROWS])
def test_an_explicit_window_holds_on_every_path(tmp_path, path, clip, args, ran, cut, runs, rife_runs):
    keyed = clip(tmp_path / "clip")
    report_path = tmp_path / "loop.json"
    try:
        code = loop.main(["--frames-dir", str(keyed), "--out-dir", str(tmp_path / "out"), "--report", str(report_path), *args])
    except SystemExit as exc:
        code = exc.code
    report = json.loads(report_path.read_text())
    assert ran(report), report.get("cycle")
    assert as_pinned(rife_runs(), runs), rife_runs()
    lo, hi = _bound(args, "--min-len"), _bound(args, "--max-len")
    if not cut:
        assert code != 0 and report["status"] == "failed", report.get("cycle")
        return
    assert code == 0, code
    length = report["cycle"]["length"]
    assert (lo is None or length >= lo) and (hi is None or length <= hi), (path, length, lo, hi)


def test_a_count_the_window_leaves_no_room_for_fails_and_says_both(tmp_path):
    keyed = _slow(tmp_path / "clip")
    with pytest.raises(SystemExit) as stop:
        loop.main(["--frames-dir", str(keyed), "--out-dir", str(tmp_path / "out"), "--state", "walk", *MOTION,
                   "--max-len", "25", "--steps", "1"])
    said = str(stop.value)
    assert "is one step, so the cycle is 41-43 frames long, and --max-len 25 leaves none of that" in said
    report = json.loads((tmp_path / "out" / "loop.loop.report.json").read_text())
    assert report["status"] == "failed" and report["error"] in said
    assert report["cycle"]["steps"]["counted_window"] == [41, 43] and report["cycle"]["steps"]["window"] is None
    assert report["cycle"]["steps"]["refused"] == "outside --max-len 25"


def test_a_one_shot_the_window_leaves_no_room_for_says_the_window(tmp_path):
    keyed = _hop(tmp_path / "clip")  # the hop is 16 frames: no one-shot that short
    with pytest.raises(SystemExit, match=r"no complete one-shot return .* — in 8-14 frames, inside --min-len 8 --max-len 14"):
        loop.main(["--frames-dir", str(keyed), "--out-dir", str(tmp_path / "out"), "--state", "jump", "--min-len", "8", "--max-len", "14"])
    report = json.loads((tmp_path / "out" / "loop.loop.report.json").read_text())
    assert report["status"] == "failed" and report["periodic_attempt"] is not None and report["cycle"]["kind"] == "one-shot"


def test_the_cut_window_is_worked_out_once():
    walk = loop.profile_for("walk")
    w = loop.CutWindow.of(walk, 73, 24.0, None, None)
    assert (w.lo, w.hi, w.said()) == (12, 36, "") and w.within(41, 43) == (41, 43) and w.past(60) == 60
    w = loop.CutWindow.of(walk, 73, 24.0, 18, 42)
    assert (w.lo, w.hi, w.said()) == (18, 42, "--min-len 18 --max-len 42")
    assert w.within(41, 43) == (41, 42) and w.within(83, 85) == (83, 42) and w.within(4, 90) == (18, 42) and w.past(60) == 42
    w.hold({"start": 0, "length": 42})
    with pytest.raises(ValueError, match="outside --min-len 18 --max-len 42"):
        w.hold({"start": 0, "length": 43})
