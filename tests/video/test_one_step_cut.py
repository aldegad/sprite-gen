# SPDX-License-Identifier: Apache-2.0
"""A walk slower than its window is cut one step long; the step screen says so, and a count of the
steps cuts it again (docs/loop-repair.md section 3, "The step screen").

The walker's cycle is 42 frames, 1.75 s at 24 fps — longer than the walk window holds in a three-second
clip (half of it, 36). Its legs are drawn alike, so one step on (21 frames) they have swapped and the
picture is the same; its arms are shaded apart, so one step on they have swapped and show it. The
search finds the step and cuts it: the loop walks on one leg, and its arms jump at the wrap. Twice the
cut, read over the whole clip, repeats better than the cut: the screen names it, the cut stays, and
`--steps 1` — the count from whoever looked — cuts the cycle.

Synthetic frames only; the arm's place is known in closed form, so the tests read the cut against it."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import align, batch, loop, period

CYCLE, FRAMES = 42, 73


def _arm(k: float, cycle: float = CYCLE) -> float:
    """The near arm's sideways place against the body, px; the far arm is its mirror."""
    return 16 * math.sin(2 * math.pi * k / cycle)


def _strider(k: int, cycle: float = CYCLE) -> Image.Image:
    """A side walker: legs drawn alike, half a cycle apart; arms opposite, the far one darker."""
    ph = 2 * math.pi * k / cycle
    im = Image.new("RGBA", (260, 220))
    d = ImageDraw.Draw(im)
    x = 60 + round(k * 0.5)
    y = 40 + round(3 * math.cos(2 * ph))
    sw = _arm(k, cycle)
    d.rectangle((x + 14 - sw - 4, y + 34, x + 14 - sw + 4, y + 72), fill=(150, 150, 150, 255))  # far arm, behind
    for sign in (1, -1):
        lx = x + 14 + 14 * sign * math.sin(ph)
        d.rectangle((lx - 5, y + 80, lx + 5, y + 126), fill=(60, 60, 70, 255))
    d.rectangle((x, y + 30, x + 28, y + 84), fill=(200, 80, 80, 255))
    d.rectangle((x + 2, y, x + 26, y + 28), fill=(230, 200, 170, 255))
    d.rectangle((x + 14 + sw - 4, y + 34, x + 14 + sw + 4, y + 72), fill=(225, 225, 225, 255))  # near arm, in front
    d.point((x + 6, y + 40 + k % 11), fill=(250, 250, 250, 255))  # no two frames the same picture
    return im


def _keyed(root: Path, cycle: float = CYCLE, frames: int = FRAMES) -> Path:
    keyed = root / "keyed"
    keyed.mkdir(parents=True)
    for k in range(frames):
        _strider(k, cycle).save(keyed / f"{k:03}.png")
    return keyed


def _cut(out: Path, keyed: Path, *extra: str) -> dict:
    assert loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
                      "--anchor", "motion-auto", "--repair", "off", *extra]) == 0
    return json.loads((out / "loop.loop.report.json").read_text())


def _arm_miss(start: int, length: int, cycle: float = CYCLE) -> float:
    """How far the cut's wrap is from the arm's own way on, in place and in speed, over the largest
    step the arm takes inside the cut: the last frame against the clip's frame before the first."""
    a = lambda k: _arm(k, cycle)  # noqa: E731
    s, L = start, length
    inner = max(abs(a(k + 1) - a(k)) for k in range(s, s + L - 1))
    place = abs(a(s + L - 1) - a(s - 1))
    speed = abs((a(s + L - 1) - a(s + L - 2)) - (a(s - 1) - a(s - 2)))
    return max(place, speed) / inner


@pytest.fixture(scope="module")
def slow(tmp_path_factory) -> tuple[Path, Path, dict]:
    root = tmp_path_factory.mktemp("slow")
    keyed = _keyed(root)
    return root, keyed, _cut(root / "out", keyed)


def test_a_walk_cut_one_step_long_is_named_by_the_step_screen(slow):
    _, _, report = slow
    cycle = report["cycle"]
    # The cycle does not fit the window: the step is cut, and its arm jumps at the wrap.
    assert cycle["length"] in (CYCLE // 2 - 1, CYCLE // 2, CYCLE // 2 + 1)
    assert _arm_miss(cycle["start"], cycle["length"]) > 3
    # Either the cut is a whole cycle, or the screen says it may be one step — v2.35.0 said nothing.
    screen = cycle.get("step_screen") or {}
    assert screen.get("suspect") is True, screen
    assert screen["lag"] in (CYCLE - 1, CYCLE, CYCLE + 1) and screen["ratio"] <= period.STEP_RATIO_MAX
    assert screen["depth"] >= loop.PERIODICITY_MIN and screen["pairs"] == FRAMES - screen["lag"]
    assert screen["coverage"] == round((FRAMES - screen["lag"]) / screen["lag"], 4)
    # Nothing is cut on it, and nothing else speaks: no warning, no `steps`.
    assert "steps" not in cycle and not any("step" in line for line in report["jolt"]["warnings"])


def _same_but_the_screen(a: Path, b: Path) -> None:
    for name in ("loop.strip.png", "loop.gif", "loop.webp", "loop.strip.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    frames = sorted(p.name for p in (a / "cycle").glob("*.png"))
    assert frames == sorted(p.name for p in (b / "cycle").glob("*.png"))
    assert all((a / "cycle" / f).read_bytes() == (b / "cycle" / f).read_bytes() for f in frames)
    # The report is JSON text, so the run dir appears in it JSON-escaped: on Windows its
    # backslashes are doubled, and a raw str(d) would match nothing.
    left, right = (json.loads((d / "loop.loop.report.json").read_text(encoding="utf-8").replace(json.dumps(str(d))[1:-1], "@"))
                   for d in (a, b))
    for report in (left, right):
        # The gait observation keeps a copy of the screen's record (sprite_gen/video/evidence.py): each
        # report's copy is its own record, and nothing else of the gait is set aside with it.
        screens = report["gait"]["screens"]
        assert "step_screen" in screens and screens.pop("step_screen") == report["cycle"].pop("step_screen")
    assert left == right


@pytest.mark.parametrize("cycle, suspect", [(CYCLE, True), (23.5, True), (25, False)])
def test_the_step_screen_changes_nothing_but_its_own_record(tmp_path, monkeypatch, cycle, suspect):
    """A one-step cut, a cycle on a frame count that is not whole (23.5 frames: twice it repeats
    better, being whole there — a suspect that is no step) and a whole cycle of 25: with the screen
    or without it, every image, the strip metadata and the report are the same but for its record, and
    the gait observation's copy of it."""
    keyed = _keyed(tmp_path, cycle)
    report = _cut(tmp_path / "screened", keyed)
    assert report["cycle"]["step_screen"]["suspect"] is suspect
    monkeypatch.setattr(period, "step_screen", lambda *a, **k: {"stubbed": True})
    _cut(tmp_path / "unscreened", keyed)
    _same_but_the_screen(tmp_path / "screened", tmp_path / "unscreened")


def test_steps_1_cuts_the_walk_again_two_steps_long_where_its_arms_close(slow, tmp_path):
    root, keyed, first = slow
    report = _cut(tmp_path / "out", keyed, "--steps", "1")
    cycle = report["cycle"]
    assert cycle["length"] in (CYCLE - 1, CYCLE, CYCLE + 1)
    L = first["cycle"]["length"]
    assert cycle["steps"] == {"count": 2, "by": "given", "given": 1,
                              "first_cut": {"start": first["cycle"]["start"], "length": L}, "window": [2 * L - 1, 2 * L + 1]}
    # The arms close at the wrap: the last frame is where, and moves as, the frame before the first.
    assert _arm_miss(cycle["start"], cycle["length"]) <= 1.0
    # Two steps in it, the clip holding most of a second cycle past it: no warning about its coverage.
    assert cycle["step_screen"]["leg_peaks"] == {"by": "stride", "count": 2}
    assert cycle["coverage"] == round((FRAMES - cycle["length"]) / cycle["length"], 4) > loop.COVERAGE_MIN
    assert cycle["choice"] == "closure" and not any("two steps long" in line for line in report["jolt"]["warnings"])
    strip = json.loads((tmp_path / "out" / "loop.strip.json").read_text())
    assert strip["steps"] == 2 and strip["cycle_frames"] == cycle["length"]


def test_steps_2_keeps_the_cut_and_records_the_count(slow, tmp_path):
    root, keyed, first = slow
    report = _cut(tmp_path / "out", keyed, "--steps", "2")
    assert {k: report["cycle"][k] for k in ("start", "length")} == {k: first["cycle"][k] for k in ("start", "length")}
    assert report["cycle"]["steps"] == {"count": 2, "by": "given", "given": 2}
    assert json.loads((tmp_path / "out" / "loop.strip.json").read_text())["steps"] == 2
    for name in ("loop.strip.png", "loop.gif", "loop.webp"):
        assert (tmp_path / "out" / name).read_bytes() == (root / "out" / name).read_bytes()


def test_a_counted_cut_the_clip_cannot_see_repeat_is_cut_and_warned_about(tmp_path):
    """50 frames hold the 42-frame cycle with 8 to spare: no window that long is seen repeating. The
    count stands for the repeat, the cut whose wrap the clip plays is taken, and its coverage — under a
    quarter of a cycle past it — is a warning, not a failure; the seam gate judges it as any cut."""
    keyed = _keyed(tmp_path, frames=50)
    report = _cut(tmp_path / "out", keyed, "--steps", "1")
    cycle = report["cycle"]
    assert cycle["length"] in (CYCLE - 1, CYCLE, CYCLE + 1) and cycle["method"] == "counted-window-v1"
    assert "no periodic cycle found" in cycle["unseen"]
    assert cycle["coverage"] == round((50 - cycle["length"]) / cycle["length"], 4) < loop.COVERAGE_MIN
    (line,) = [line for line in report["jolt"]["warnings"] if "two steps long" in line]
    assert f"holds {cycle['coverage']:.2f} of a cycle past it" in line
    assert report["resampled_seam_ratio"] <= loop.SEAM_RATIO_MAX


def test_an_explicit_window_past_half_the_clip_is_the_window(slow, tmp_path):
    """--min-len 41 --max-len 43 asks for the cycle the window could not hold. v2.35.0 cut the ceiling
    back to half the clip (36) and found no window [41, 36]; the caller's ceiling is the ceiling."""
    _, keyed, _ = slow
    report = _cut(tmp_path / "out", keyed, "--min-len", str(CYCLE - 1), "--max-len", str(CYCLE + 1))
    assert report["cycle"]["length"] in (CYCLE - 1, CYCLE, CYCLE + 1) and report["window"] == [CYCLE - 1, CYCLE + 1]


def test_steps_needs_motion_auto(slow, tmp_path):
    _, keyed, _ = slow
    with pytest.raises(SystemExit, match="--steps counts the steps in the cut --anchor motion-auto takes"):
        loop.main(["--frames-dir", str(keyed), "--out-dir", str(tmp_path / "out"), "--state", "walk", "--steps", "1"])


# --- the set ----------------------------------------------------------------------------------

def _set(root: Path, side: Path) -> list[Path]:
    """A set of the slow walker cut as a whole cycle (front, by its window) and the side given."""
    dirs = []
    for name, source in (("front", root / "front"), ("side", side)):
        shutil.copytree(source, root / "set" / name)
        dirs.append(root / "set" / name)
    return dirs


def test_cycle_align_stops_on_a_loop_that_may_be_one_step_and_names_the_count_that_settles_it(slow, tmp_path):
    root, keyed, _ = slow
    whole = _keyed(tmp_path / "whole", cycle=25)
    _cut(tmp_path / "front", whole)
    dirs = _set(tmp_path, root / "out")
    with pytest.raises(align.CycleSuspects) as stop:
        align.align_set(dirs, between="nearest")
    (entry,) = stop.value.suspects
    assert entry["dir"] == str(dirs[1].resolve()) and entry["status"] == "stopped" and entry["candidates"] == []
    assert entry["one_step"]["lag"] in (CYCLE - 1, CYCLE, CYCLE + 1) and entry["one_step"]["report"] == str((dirs[1] / "loop.loop.report.json").resolve())
    assert entry["settle"] == [f"--cycles {dirs[1].resolve()}=1"]
    assert "may be one step" in str(stop.value) and "--steps 1" in str(stop.value)
    # Nothing was rewritten: the side loop is as cut.
    assert (dirs[1] / "loop.strip.png").read_bytes() == (root / "out" / "loop.strip.png").read_bytes()
    # warn aligns it as it is and says so; a count of one cycle settles it.
    out = align.align_set(dirs, between="nearest", multi_cycle="warn")
    assert out["applied"] is True and any("one step where the rest play two" in line for line in out["warnings"])
    out = align.align_set(dirs, between="nearest", cycles={str(dirs[1]): 1})
    assert out["applied"] is True and out["suspects"][0]["status"] == "counted"


def test_cycle_align_takes_the_loop_cut_again_on_its_count(slow, tmp_path):
    root, keyed, _ = slow
    whole = _keyed(tmp_path / "whole", cycle=25)
    _cut(tmp_path / "front", whole)
    _cut(tmp_path / "recut", keyed, "--steps", "1")
    dirs = _set(tmp_path, tmp_path / "recut")
    out = align.align_set(dirs, between="nearest")
    lengths = [json.loads((d / "loop.loop.report.json").read_text())["cycle"]["length"] for d in dirs]
    assert out["applied"] is True and out["length"] == round((lengths[0] + lengths[1]) / 2)
    (entry,) = out["suspects"]  # its half is its step: no suspect to stop on
    assert entry["dir"] == str(dirs[1].resolve()) and entry["status"] == "steps" and [c["cycles"] for c in entry["candidates"]] == [2]
    assert all("cycles_kept" not in row for row in out["loops"])


# --- video-set ----------------------------------------------------------------------------

def test_video_set_names_a_loop_that_may_be_one_step_and_its_table_says_so(slow, tmp_path, capsys):
    """A video-set item keeps the cut's report beside its loop directory (`<item>/loop.report.json`):
    the alignment reads the step screen there, and the set stops on the side walk with what settles it."""
    _, keyed, _ = slow
    rows = []
    for direction, source in (("front", _keyed(tmp_path / "whole", cycle=25)), ("side", keyed)):
        item = tmp_path / f"{direction}-walk"
        lp = loop.run_loop(source, item / "loop", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                           seam_max=loop.SEAM_RATIO_MAX, name=f"{direction}-walk", report_path=item / "loop.report.json",
                           anchor="motion-auto", repair="off")
        rows.append({"item": f"{direction}-walk", "direction": direction, "state": "walk", "dir": str(item), "ok": True,
                     "loop": {"cycle": lp["cycle"]["length"], "period": None, "seam_ratio": 1.0, "n_out": lp["n_out"],
                              **({"step_screen": lp["cycle"]["step_screen"]} if lp["cycle"]["step_screen"]["suspect"] else {})}})
    out = batch.align_gaits(rows, tmp_path, "auto", interpolate=None, between="nearest")
    walk = out["walk"]
    assert walk["applied"] is False and walk["reason"] == "cycle-suspects"
    assert walk["why"].startswith("side-walk may be one step (cut it again with video-loop --steps 1 if it is)")
    assert walk["suspects"][0]["one_step"]["report"] == str((tmp_path / "side-walk" / "loop.report.json").resolve())
    assert "cycles not aligned" in capsys.readouterr().err
    table = batch.write_table(rows, tmp_path / "table.md")
    assert f"| {rows[1]['loop']['cycle']} (one step?) |" in table and f"| {rows[0]['loop']['cycle']} |" in table
    rows[1]["loop"] = {**rows[1]["loop"], "cycle": 42, "steps": 2}
    rows[1]["loop"].pop("step_screen")
    assert "| 42 (2 steps) |" in batch.write_table(rows, tmp_path / "table.md")
