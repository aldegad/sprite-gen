# SPDX-License-Identifier: Apache-2.0
"""Held cells (sprite_gen/video/held_fill.py, docs/loop-repair.md section 7): neighbour cells of a cut that show the
same picture are found on every cut `video-loop` makes, recorded (`held_fill`: the held runs, the poses and the cells)
and, by default, made again as the frames between the pose before them and the pose after; the cut's first and last
cells, its length, its beat and its wrap stay as they were, and a part standing still in both stays where it stood.
A clip asked for at a higher rate than it was filmed (`--out-fps`) is given frames between, not frames shown again.
With no RIFE the cut is delivered as filmed with a warning naming the motion and the install line; `--fill-held off`
cuts it as before. A cut with no held cell comes out byte for byte as before (the six inputs below, their pictures
pinned on the engine before the fill).

The interpolator is a stand-in that draws the synthetic clip again at the time asked for (`Between`), so the tests
need no binary and say which frame comes back."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import held_fill
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import sway
from tests.picture_digest import picture_digest
from tests.video.held_fill_inputs import (N, Between, hop, planter, save, shown_again, slid, swing, topple, walk,
                                          walk_at)


def _no_rife(monkeypatch, tmp_path: Path) -> None:
    """No RIFE anywhere the engine looks: no SPRITE_GEN_RIFE, no directory on PATH holding one, an empty data
    directory."""
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not (Path(d) / "rife-ncnn-vulkan").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(path))
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))


def _run(tmp_path: Path, frames: list[Image.Image], name: str = "m", **kw) -> dict:
    keyed = save(frames, tmp_path / f"{name}-keyed")
    return loop_mod.run_loop(keyed, tmp_path / f"{name}-out", fps=24.0, min_len=None, max_len=None, n_out=None,
                             seam_max=loop_mod.SEAM_RATIO_MAX, name=name, report_path=None, **kw)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bytes(out: Path, name: str) -> dict[str, str]:
    cycle = hashlib.sha256(b"".join(p.read_bytes() for p in sorted((out / "cycle").glob("frame-*.png")))).hexdigest()
    return {"strip": _sha(out / f"{name}.strip.png"), "gif": _sha(out / f"{name}.gif"), "webp": _sha(out / f"{name}.webp"),
            "cycle": cycle}


def _pictures(out: Path, name: str) -> dict[str, str]:
    return {"strip": picture_digest([out / f"{name}.strip.png"]), "gif": picture_digest([out / f"{name}.gif"]),
            "webp": picture_digest([out / f"{name}.webp"]),
            "cycle": picture_digest(sorted((out / "cycle").glob("frame-*.png")))}


def _meta(rep: dict) -> dict:
    return json.loads(Path(rep["strip"]["path"]).with_suffix("").with_suffix(".strip.json").read_text())


def _cycle(rep: dict) -> list[Image.Image]:
    return [Image.open(p).convert("RGBA") for p in sorted((Path(rep["out_dir"]) / "cycle").glob("frame-*.png"))]


# Pinned on the engine before the fill (886936e): a cut with no held cell is delivered as it was. The pins are the
# pictures (`_pictures`, tests/picture_digest.py), not the files: the same pixels are other PNG bytes on Linux than on
# macOS, while the engine before the fill and this one write the same files on each.
UNHELD = {
    "walk-fixed": (lambda: walk(), dict(state="walk", cycle_mode="fixed", start=0, length=N, anchor="none", repair="off"), {
        "strip": "1467e5923f24c4830ef24e6525e9e537d73504a0d95badf1ba4cd0ed3288572b",
        "gif": "bf77ed39daaaecfbbd403243696dcf9e2bb6d99addc01a889a8dc00772468b8d",
        "webp": "bf77ed39daaaecfbbd403243696dcf9e2bb6d99addc01a889a8dc00772468b8d",
        "cycle": "319c0f8aee34fb537b1702eecc018afc4a03b10dcda1b84c5cb204a14d4ac3fa"}),
    "walk-auto": (lambda: walk(N * 3 + 4), dict(state="walk", repair="off"), {
        "strip": "1c3945eadb5095d3b1c929bd2009bd510ebf7760a4f0f92c8e618b04d882b6ca",
        "gif": "6e9348b6d9ac27dc1868bf8a2b9aae7daee44847274eb65c1b6ce4431c686f06",
        "webp": "6e9348b6d9ac27dc1868bf8a2b9aae7daee44847274eb65c1b6ce4431c686f06",
        "cycle": "185053b6a035a9d0b672a1d93d7e378f819189cd3cf144540e9f95a111a0b2b2"}),
    "hop-one-shot": (lambda: hop(), dict(state="jump", cycle_mode="one-shot"), {
        "strip": "6442073e3ada3b03e2f7df6c52348fc001fcbdce6c51c8e91ce157e91c313ac7",
        "gif": "ab58ee8b25599331b7958701d81526b155a05b1cddfa8b9ed823134f9f2e93fb",
        "webp": "ab58ee8b25599331b7958701d81526b155a05b1cddfa8b9ed823134f9f2e93fb",
        "cycle": "189484dd441486fe6241534fae7a03ea7afac71ec204b8871e9da938e6e69793"}),
    "swing-pinned": (lambda: swing(), dict(state="attack", cycle_mode="pinned", anchor="none"), {
        "strip": "9dd4896eef213ae44b487f92d33ffa1d7e6aea66ac32e9c97773f6c0ed27e3d1",
        "gif": "75df72100becbdd3b823ae282b2dd28815f6271198dd8562d8d91ee9363d6358",
        "webp": "75df72100becbdd3b823ae282b2dd28815f6271198dd8562d8d91ee9363d6358",
        "cycle": "b2a255d5f0018a71225501a86dcfa54915fa9ed4c4d936319fb83c31ab57bf1e"}),
    "topple-once": (lambda: topple(), dict(state="death", cycle_mode="once"), {
        "strip": "c9b7900ec1d4523bb34c07eb8077f72352bbc8251fc5caab31647317e0634957",
        "gif": "285a7352ba6345648cfc48185f9121ce75f37cc63b3b5875dce5b4bed738fc8a",
        "webp": "285a7352ba6345648cfc48185f9121ce75f37cc63b3b5875dce5b4bed738fc8a",
        "cycle": "67a1691bc7cb2eabf5a885dd9b993eb8596ea20090594958b7a02afc09948c63"}),
    "swing-once-return": (lambda: swing(), dict(state="leap", cycle_mode="once-return", anchor="none"), {
        "strip": "883f7e44cc1f54ec5a1442ed12fe8f608070e12b9795c73d2f871425d76e977f",
        "gif": "70de98ed5a45f2e9840c6946057a868260cae3588bff9969205e453ef1b024f3",
        "webp": "70de98ed5a45f2e9840c6946057a868260cae3588bff9969205e453ef1b024f3",
        "cycle": "4dfe6899ab09db62987b27d0a35ba754a22ca78e02945e731b1025dfe3af90e3"}),
}


@pytest.mark.parametrize("case", sorted(UNHELD))
def test_a_cut_with_no_held_cell_is_delivered_byte_for_byte_as_before(case, tmp_path, monkeypatch):
    _no_rife(monkeypatch, tmp_path)  # nothing is made, so nothing looks for RIFE
    frames, kw, pinned = UNHELD[case]
    rep = _run(tmp_path, frames(), case, **kw)
    assert _pictures(Path(rep["out_dir"]), case) == pinned
    record = _meta(rep)["held_fill"]
    assert record == held_fill.public(rep["held_fill"])
    assert record["mode"] == "auto" and record["made"] == [] and "unmade" not in record
    cells = rep["cycle"]["length"]
    assert record["cells"] == cells
    if case == "topple-once":
        # It lies still for its last five cells: the cut's closing pose, kept.
        assert record["held_runs"] == [{"start": cells - 5, "length": 5}]
        assert record["before"]["held_runs"][0]["filled"] is False
        assert record["poses"] == cells - 4
    else:
        assert record["held_runs"] == [] and record["poses"] == cells


STALLED = {5: 2, 13: 3}  # the walk's frame 5 shown twice, frame 13 three times
STALLED_OFF = {  # the stalled walk cut on the engine before the fill (886936e)
    "strip": "32f2c1b7f4f3aedfa3eba4d49b849fd8efea9e011aaf2e035407a97ac6e9430d",
    "gif": "bf77ed39daaaecfbbd403243696dcf9e2bb6d99addc01a889a8dc00772468b8d",
    "webp": "bf77ed39daaaecfbbd403243696dcf9e2bb6d99addc01a889a8dc00772468b8d",
    "cycle": "2d74651f64831c9c512f0486e1b03ca3827c4b9d3840bf11f8c67fad221bbfe9",
}
STALLED_CUT = dict(state="walk", cycle_mode="fixed", start=0, length=N + 3, anchor="none", repair="off")


def test_a_stalled_walk_has_its_held_cells_made_between_the_poses_beside_them(tmp_path):
    between = Between(walk_at, range(N + 6), period=N)
    rep = _run(tmp_path, shown_again(walk(), STALLED), interpolate=between, **STALLED_CUT)
    record = rep["held_fill"]
    assert record["before"]["cells"] == N + 3 and record["before"]["poses"] == N
    assert [(r["start"], r["length"], r["filled"]) for r in record["before"]["held_runs"]] == [(5, 2, True), (14, 3, True)]
    assert record["made"] == [6, 15, 16]
    # Each made at its own time between the pose before the run and the pose after it: half way, a third and two thirds.
    assert sorted(between.calls) == [(5.0, 6.0, 0.5), (13.0, 14.0, 0.3333), (13.0, 14.0, 0.6667)]
    assert (record["cells"], record["poses"], record["held_runs"]) == (N + 3, N + 3, [])
    cells = _cycle(rep)
    assert cells[6].tobytes() == walk_at(5.5).tobytes()
    assert cells[15].tobytes() == walk_at(13 + 1 / 3).tobytes()
    # Every other cell is the cut's as filmed: the first and the last, the length, the beat.
    filmed = shown_again(walk(), STALLED)[: N + 3]
    assert len(cells) == N + 3
    assert all(cells[k].tobytes() == filmed[k].tobytes() for k in range(N + 3) if k not in (6, 15, 16))
    assert _meta(rep)["held_fill"] == held_fill.public(record)
    assert rep["seam_measurement"] == "rendered-cells"


@pytest.mark.parametrize("given", ["the frame before", "the frame after"])
def test_an_interpolator_that_gives_the_pose_back_leaves_the_stalled_walk_as_filmed_and_says_so(given, tmp_path, capsys):
    # No frame between at all: each frame it gives is a pose beside it, a held cell itself. Tried again only on the
    # side away from that pose (later for the frame before, earlier for the frame after) within half a cell, never
    # taken: the cut is delivered as filmed, its record and its warnings say both runs are still held.
    calls: list[float] = []

    def stuck(a, b, t):
        calls.append(t)
        return a if given == "the frame before" else b

    rep = _run(tmp_path, shown_again(walk(), STALLED), interpolate=stuck, **STALLED_CUT)
    assert rep["status"] == "passed"
    record = rep["held_fill"]
    assert record["made"] == [] and "interpolator" not in record
    runs = record["before"]["held_runs"]
    assert [(r["start"], r["length"], r["filled"]) for r in runs] == [(5, 2, False), (14, 3, False)]
    assert all("held" in r["faults"] for r in runs)
    assert [[t["t"] for t in r["tries"][0]["tries"]] for r in runs] == (
        [[0.5, 0.55, 0.6, 0.65, 0.7], [0.3333, 0.35, 0.4, 0.45]] if given == "the frame before"
        else [[0.5, 0.45, 0.4, 0.35, 0.3], [0.3333, 0.3, 0.25, 0.2]])
    assert len(calls) == sum(len(r["tries"][0]["tries"]) for r in runs) == 9
    assert (record["cells"], record["poses"], record["held_runs"]) == (
        N + 3, N, [{"start": 5, "length": 2}, {"start": 14, "length": 3}])
    assert _pictures(Path(rep["out_dir"]), "m") == STALLED_OFF
    err = capsys.readouterr().err
    assert "held cells 5-6 kept as filmed" in err and "held cells 14-16 kept as filmed" in err


def test_fill_held_off_cuts_the_stalled_walk_as_before_and_still_names_its_runs(tmp_path):
    rep = _run(tmp_path, shown_again(walk(), STALLED), fill_held="off", **STALLED_CUT)
    assert _pictures(Path(rep["out_dir"]), "m") == STALLED_OFF
    record = rep["held_fill"]
    assert record["mode"] == "off" and record["made"] == []
    assert [(r["start"], r["length"], r["filled"], r["why"]) for r in record["before"]["held_runs"]] == [
        (5, 2, False, "--fill-held off"), (14, 3, False, "--fill-held off")]
    assert (record["cells"], record["poses"]) == (N + 3, N)


def test_no_rife_delivers_the_held_cells_as_filmed_and_says_which_motion(tmp_path, monkeypatch, capsys):
    _no_rife(monkeypatch, tmp_path)
    rep = _run(tmp_path, shown_again(walk(), STALLED), name="stalled-walk", **STALLED_CUT)
    assert rep["status"] == "passed"
    out = Path(rep["out_dir"])
    # The images are the cut as filmed: what --fill-held off delivers.
    off = _run(tmp_path, shown_again(walk(), STALLED), name="off", fill_held="off", **STALLED_CUT)
    assert {k: v for k, v in _bytes(out, "stalled-walk").items()} == _bytes(Path(off["out_dir"]), "off")
    record = rep["held_fill"]
    assert record["made"] == [] and record["unmade"]["cells"] == [6, 15, 16]
    assert record["unmade"]["install"] == loop_mod.rife_mod.install_command()
    assert (record["cells"], record["poses"]) == (N + 3, N)
    err = capsys.readouterr().err
    assert "stalled-walk" in err and "RIFE is not installed" in err and loop_mod.rife_mod.install_command() in err


def test_a_hold_longer_than_three_cells_is_the_motion_s_own_and_kept(tmp_path):
    between = Between(walk_at, range(N + 6), period=N)
    rep = _run(tmp_path, shown_again(walk(), {9: 5}), interpolate=between,
               **{**STALLED_CUT, "length": N + 4})
    run = rep["held_fill"]["before"]["held_runs"]
    assert [(r["start"], r["length"], r["filled"]) for r in run] == [(9, 5, False)]
    assert "longer than" in run[0]["why"]
    assert between.calls == [] and rep["held_fill"]["held_runs"] == [{"start": 9, "length": 5}]


@pytest.mark.slow
def test_a_walk_drawn_on_twos_is_limited_animation_and_kept(tmp_path):
    # Each drawing shown twice, as a clip of 12 drawings a second at 24 fps is (sprite_gen/video/held.py).
    twos = shown_again(walk(), {k: 2 for k in range(N + 6)})
    cut = {**STALLED_CUT, "length": 2 * N}
    between = Between(walk_at, range(N + 6), period=N)
    rep = _run(tmp_path, twos, interpolate=between, **cut)
    record = rep["held_fill"]
    assert rep["cycle_drawings"]["hold"] == 2 and record["drawn"] == 2
    assert between.calls == [] and record["made"] == []
    assert all(not r["filled"] and "drawn on twos" in r["why"] for r in record["before"]["held_runs"])
    assert (record["cells"], record["poses"]) == (2 * N, N)
    off = _run(tmp_path, twos, name="off", fill_held="off", **cut)
    assert _bytes(Path(rep["out_dir"]), "m") == _bytes(Path(off["out_dir"]), "off")


@pytest.mark.slow
def test_a_walk_carried_to_60_fps_by_repeating_frames_reads_a_hold_and_is_filled_to_a_pose_a_cell(tmp_path):
    # 24 fps carried to 60 by the frame under each time: each frame shown three and two times by turns. Its rhythm
    # reads a hold of 2, but its runs are not each two cells: the frames between are made.
    filmed = walk(2 * N + 2)
    carried = [filmed[k * 24 // 60] for k in range(len(filmed) * 60 // 24)]
    between = Between(walk_at, range(2 * N + 2), period=N)
    keyed = save(carried, tmp_path / "carried-keyed")
    rep = loop_mod.run_loop(keyed, tmp_path / "carried-out", fps=60.0, min_len=None, max_len=None, n_out=None,
                            seam_max=loop_mod.SEAM_RATIO_MAX, name="m", report_path=None, interpolate=between,
                            state="walk", anchor="none", repair="off")
    record = rep["held_fill"]
    assert rep["cycle_drawings"]["hold"] == 2 and "drawn" not in record
    assert {r["length"] for r in record["before"]["held_runs"]} == {2, 3}
    assert record["before"]["poses"] < record["before"]["cells"] == rep["cycle"]["length"] == 60
    assert (record["cells"], record["poses"], record["held_runs"]) == (60, 60, [])
    cells, start = _cycle(rep), rep["cycle"]["start"]
    assert cells[0].tobytes() == carried[start].tobytes() and cells[-1].tobytes() == carried[start + 59].tobytes()


def test_a_once_cut_and_a_one_shot_have_their_held_cells_made_too(tmp_path):
    frames = topple()
    stalled = shown_again(frames, {20: 2})
    times = {f.tobytes(): float(k) for k, f in enumerate(frames)}
    between = Between(lambda t: _topple_at(t), [])
    between.known.update(times)
    rep = _run(tmp_path, stalled, "fall", interpolate=between, state="death", cycle_mode="once")
    record = rep["held_fill"]
    start = rep["cycle"]["start"]
    assert [(r["start"], r["filled"]) for r in record["before"]["held_runs"] if r["filled"]] == [(20 - start, True)]
    # Half way, and a twentieth on, the body has sunk less than a pixel: each such frame shows frame 20 again and is
    # refused (`held`). A frame nearer frame 20 would show it too, so the cell is made again only later, at the nearest
    # fraction where the body has moved.
    assert between.calls == [(20.0, 21.0, t) for t in (0.5, 0.55, 0.6)]
    made = next(r for r in record["frames"] if r["cell"] == 21 - start)
    assert (made["t"], made["from_t"]) == (0.6, 0.5)
    assert [(t["faults"], t.get("shows")) for t in made["tries"]] == [(["held"], ["a"])] * 2 + [([], None)]
    assert all(r["start"] != 20 - start for r in record["held_runs"])  # read again, the cell made is a pose of its own
    hopped = shown_again(hop(), {18: 2})
    rep = _run(tmp_path, hopped, "hop", interpolate=lambda a, b, t: Image.blend(a, b, t), state="jump", cycle_mode="one-shot")
    assert any(r["start"] + rep["cycle"]["start"] == 18 for r in rep["held_fill"]["before"]["held_runs"])


def _topple_at(time: float) -> Image.Image:
    """`topple`'s body `time` frames in, between its frames as well."""
    t = min(1.0, max(0.0, (time - 6) / 30))
    a = np.zeros((96, 128, 4), dtype=np.uint8)
    height, width = round(64 - 40 * t), round(16 + 36 * t)
    a[86 - height:86, 40:40 + width] = (200, 80, 30, 255)
    return Image.fromarray(a, "RGBA")


# The fill on a cut's frames, as `video-loop` calls it: which runs it makes, which it keeps and why.


def _reach(time: float) -> Image.Image:
    return planter(4 * time)


def _planted(times: list[float]) -> list[Image.Image]:
    return [_reach(t) for t in times]


# At the smaller sizes a game shows the cells at (`--judge-scale`, tests/video/test_judge_scale.py) a foot dragged and
# two poses laid over each other stay refused: the size takes back outline refusals only.
JUDGED = (1.0, 0.6, 0.5, 0.35)


@pytest.mark.parametrize("judge_scale", JUDGED)
def test_a_part_standing_still_on_both_sides_stays_where_it_stood(judge_scale):
    frames = _planted([0, 1, 2, 3, 3, 4, 5, 6])
    out, record = held_fill.fill(frames, Between(_reach, range(7)), ring=False, judge_scale=judge_scale)
    assert record["made"] == [4]
    assert out[4].tobytes() == _reach(3.5).tobytes()
    assert record["before"]["held_runs"][0]["filled"] is True
    assert record["contact"][0]["kept"] == 1.0


@pytest.mark.parametrize("judge_scale", JUDGED)
def test_a_frame_between_that_drags_a_planted_foot_is_not_taken(judge_scale):
    frames = _planted([0, 1, 2, 3, 3, 4, 5, 6])
    out, record = held_fill.fill(frames, Between(_reach, range(7), after=slid), ring=False, judge_scale=judge_scale)
    assert record["made"] == []
    assert out[4].tobytes() == frames[4].tobytes()  # the held cell, as filmed
    run = record["before"]["held_runs"][0]
    assert run["filled"] is False and "contact" in run["faults"]
    assert record["held_runs"] == [{"start": 3, "length": 2}]


@pytest.mark.parametrize("judge_scale", JUDGED)
def test_a_frame_between_that_cross_fades_two_poses_is_not_taken(judge_scale):
    # Two poses laid over each other at half strength: the double exposure an interpolator gives where it cannot follow.
    frames = shown_again(walk(), STALLED)[: N + 3]
    out, record = held_fill.fill(frames, lambda a, b, t: Image.blend(a, b, t), ring=True, judge_scale=judge_scale)
    assert record["made"] == [] and [o.tobytes() for o in out] == [f.tobytes() for f in frames]
    assert all(not r["filled"] and "ghost" in r["faults"] for r in record["before"]["held_runs"])


@pytest.mark.parametrize("given", ["the frame before", "the frame after"])
def test_a_frame_between_that_shows_a_pose_beside_it_again_is_not_taken(given):
    frames = shown_again(walk(), STALLED)[: N + 3]
    calls: list[float] = []

    def back(a, b, t):
        calls.append(t)
        return a if given == "the frame before" else b

    out, record = held_fill.fill(frames, back, ring=True)
    assert record["made"] == [] and [o.tobytes() for o in out] == [f.tobytes() for f in frames]
    runs = record["before"]["held_runs"]
    assert all(not r["filled"] and r["faults"] == ["held"] for r in runs)
    # Each run's first cell tried at its own time, then at the other fractions within half a cell of it on the side
    # away from the pose it showed again: later for the frame before, earlier for the frame after.
    shown, later = ("a", True) if given == "the frame before" else ("b", False)
    for r in runs:
        tries = r["tries"][0]["tries"]
        assert len(tries) > 1 and all(t["faults"] == ["held"] and t["shows"] == [shown] for t in tries)
        assert [t["t"] for t in tries] == sorted((t["t"] for t in tries), reverse=not later)
    assert (record["poses"], record["held_runs"]) == (N, [{"start": 5, "length": 2}, {"start": 14, "length": 3}])


def _lit(blue: int) -> Image.Image:
    """`planter(10)` with its body `blue` in the blue channel: one pose, lit differently."""
    a = np.asarray(planter(10)).copy()
    a[30:110, 50:70] = (90, 90, blue, 255)
    return Image.fromarray(a, "RGBA")


def test_a_frame_between_that_shows_both_poses_beside_it_is_not_made_again():
    # The pose after the held one lit 40 levels brighter (over PIXEL_SAME): any frame between them shows one of the
    # two, and half way it shows both again. No fraction can show a picture of its own, so it is made once.
    a, b = _lit(200), _lit(240)
    frames = [planter(0), a, a, b, planter(20)]
    calls: list[float] = []
    out, record = held_fill.fill(frames, lambda x, y, t: calls.append(t) or Image.blend(x, y, t), ring=False)
    run = record["before"]["held_runs"][0]
    assert (run["start"], run["length"], run["filled"], run["faults"]) == (1, 2, False, ["held"])
    assert calls == [0.5]
    assert run["tries"][0]["tries"] == [{"t": 0.5, "faults": ["held"], "shows": ["a", "b"]}]
    assert record["made"] == [] and [o.tobytes() for o in out] == [f.tobytes() for f in frames]


def test_a_run_whose_frames_between_show_one_picture_twice_is_kept_as_filmed():
    # An interpolator that draws the time half way whatever it is asked: a run of two is made, a run of three would
    # show its one frame between on both its cells.
    frames = shown_again(walk(), STALLED)[: N + 3]
    between = Between(walk_at, range(N + 6), period=N)
    out, record = held_fill.fill(frames, lambda a, b, t: between(a, b, 0.5), ring=True)
    runs = record["before"]["held_runs"]
    assert [(r["start"], r["filled"]) for r in runs] == [(5, True), (14, False)]
    assert runs[1]["faults"] == ["held"] and [row["cell"] for row in runs[1]["tries"]] == [15, 16]
    # The second shows the first again, the cell before it: made again only later, within half a cell.
    assert [(t["t"], t["shows"]) for t in runs[1]["tries"][1]["tries"]] == [(t, ["before"]) for t in (0.6667, 0.7, 0.75, 0.8)]
    assert record["made"] == [6] and out[6].tobytes() == walk_at(5.5).tobytes()
    assert [o.tobytes() for o in out[14:17]] == [f.tobytes() for f in frames[14:17]]
    assert record["held_runs"] == [{"start": 14, "length": 3}]


def test_the_opening_and_the_closing_pose_of_a_cut_played_once_are_kept():
    frames = _planted([0, 0, 1, 2, 3, 4, 4])
    calls: list = []
    out, record = held_fill.fill(frames, lambda a, b, t: calls.append(t) or a, ring=False)
    whys = [r["why"] for r in record["before"]["held_runs"]]
    assert calls == [] and record["made"] == []
    assert "opening" in whys[0] and "closing" in whys[1]


def test_a_loop_s_held_cells_at_its_wrap_are_kept_and_at_its_start_made():
    frames = _planted([0, 0, 1, 2, 3, 4, 5, 5])
    between = Between(_reach, range(7), period=None)
    # The wrap closes on cell 0 again: a frame between the last pose and the first is the wrap's, not the hold's.
    out, record = held_fill.fill(frames, between, ring=True)
    runs = record["before"]["held_runs"]
    assert (runs[0]["start"], runs[0]["filled"]) == (0, True)
    assert (runs[1]["start"], runs[1]["filled"]) == (6, False) and "wrap" in runs[1]["why"]
    assert record["made"] == [1] and out[0].tobytes() == frames[0].tobytes() and out[7].tobytes() == frames[7].tobytes()


def test_codec_copies_read_as_the_same_picture_and_the_slowest_motion_does_not():
    still = planter(10)
    noisy = np.asarray(still).astype(np.int16)
    noisy[..., :3] += np.random.default_rng(0).integers(-3, 4, size=noisy[..., :3].shape).astype(np.int16)
    noisy = Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8), "RGBA")
    assert held_fill.same_steps([still, noisy, planter(11)]) == [True, False]


def test_a_whole_figure_moving_under_a_thumbnail_pixel_is_not_the_same_picture():
    # A walker going away, a frame 280 px wide: its steps move a thousand of its pixels, and some move none of the
    # analysis thumbnail's. The frame as cut is read too: a cut of it shows no held cell.
    from tests.video.test_gait_fallback import walking_away

    frames = [walking_away(k) for k in range(73)][24:48]
    thumbs = held_fill._thumbs(frames)
    assert any(held_fill._same(a, b) for a, b in zip(thumbs, thumbs[1:]))
    assert held_fill.same_steps(frames) == [False] * 23


# A higher rate than the clip's: frames between, never frames shown again.


@pytest.mark.slow
def test_a_walk_asked_for_at_60_fps_is_made_of_frames_between(tmp_path):
    between = Between(walk_at, range(N + 6), period=N)
    rep = _run(tmp_path, walk(), "walk60", interpolate=between, out_fps=60.0,
               state="walk", cycle_mode="fixed", start=0, length=N, anchor="none", repair="off")
    meta = _meta(rep)
    record = rep["held_fill"]
    assert record["out_fps"]["applied"] is True and record["out_fps"]["cells"] == 60
    assert (record["cells"], record["poses"], record["held_runs"]) == (60, 60, [])
    assert meta["frames"] == 60 and meta["cycle_seconds"] == 1.0 and meta["delay_ms"] == pytest.approx(16.67)
    assert meta["out_fps"] == 60.0
    cells = _cycle(rep)
    assert len(cells) == 60
    assert cells[0].tobytes() == walk_at(0).tobytes() and cells[5].tobytes() == walk_at(2).tobytes()
    assert cells[1].tobytes() == walk_at(0.4).tobytes()
    assert len({c.tobytes() for c in cells}) == 60


@pytest.mark.slow
def test_a_cut_at_a_rate_of_its_own_records_each_cell_as_the_frame_it_is_and_a_cell_made_between_as_not_filmed(tmp_path):
    # The strip's record of its cells (`cell_record`, video-sway --read-on-cut) of a walk that shows frame 9 twice, its
    # held cells kept, at --out-fps 49: a cell that is a frame (on its time, a hair before it, or between the two frames
    # of one picture, that picture again; `out_fps.source_frames`) is that frame as filmed; one made between two frames reads
    # as the frame before its time, is not as filmed but where its pixels are that frame's, and --read-on-cut does not
    # pair it.
    clip, between = shown_again(walk(), {9: 2}), Between(walk_at, range(N + 6), period=N)
    cut = dict(state="walk", cycle_mode="fixed", length=N, anchor="none", repair="off", fill_held="off")
    rep = _run(tmp_path, clip, "walk49", interpolate=between, out_fps=49.0, start=0, **cut)
    record = rep["held_fill"]["out_fps"]
    assert record["applied"] is True and record["cells"] == 49 and record["shown_again"] == []
    own = _meta(rep)
    cells = sway.cell_record(own, _sha(Path(rep["strip"]["path"])))
    shown = _cycle(rep)
    kinds = []
    for c, (cell, times, source) in enumerate(zip(cells, record["times"], record["source_frames"])):
        assert cell["anchor_move_px"] == [0, 0] and cell["scale"] == {"size_hold": 1.0, "gait_fallback": 1.0}
        frame = clip[cell["source_frame"]].tobytes()
        if source is not None:
            assert cell["source_frame"] == source and cell["as_filmed"] is True and shown[c].tobytes() == frame
            kinds.append("on" if abs(times - round(times)) <= held_fill.SNAP else "again")
        else:
            # the stand-in draws the walk again at the cell's time: now and then that is a frame's own picture
            assert cell["source_frame"] == math.floor(times) and cell["as_filmed"] is (shown[c].tobytes() == frame)
            kinds.append("made")
    # frame 1 a hair before its time (0.98), frame 9 again between its two cells (9.31, 9.80), the rest made
    assert record["times"][2] == 0.9796 and cells[2]["source_frame"] == 1 and kinds[2] == "on"
    assert [cells[c]["source_frame"] for c in (19, 20)] == [9, 9] and kinds[19:21] == ["again", "again"]
    assert [c for c, kind in enumerate(kinds) if kind != "made"] == [0, 2, 19, 20, 47] and kinds.count("made") == 44
    assert sum(not cells[c]["as_filmed"] for c, kind in enumerate(kinds) if kind == "made") >= 40
    # Read on a cut of the same clip from frame 3 (24 fps): this cut's first cell of frame 3 is made between 3 and 4.
    read = _run(tmp_path, clip, "from3", start=3, **cut)
    first = [c["source_frame"] for c in cells].index(3)
    assert cells[first]["as_filmed"] is False and kinds[first] == "made"
    with pytest.raises(SystemExit, match=rf"cell {first} is not that frame as filmed: video-loop did not cut it as filmed "
                                         rf"\(cell_record\.cells\[{first}\]\.as_filmed\)"):
        sway.recut_pair(own, _meta(read), _sha(Path(read["strip"]["path"])), strip_sha256=_sha(Path(rep["strip"]["path"])))


@pytest.mark.slow
def test_a_one_shot_asked_for_at_60_fps_keeps_its_first_and_last_frame(tmp_path):
    frames = hop()
    times = {f.tobytes(): float(k) for k, f in enumerate(frames)}
    made: list = []

    def between(a, b, t):
        made.append((times[a.tobytes()], times[b.tobytes()], round(t, 4)))
        return Image.blend(a, b, t)

    rep = _run(tmp_path, frames, "hop60", interpolate=between, out_fps=60.0, state="jump", cycle_mode="one-shot")
    L = rep["cycle"]["length"]
    record = rep["held_fill"]
    cells = _cycle(rep)
    assert len(cells) == round((L - 1) * 2.5) + 1 == record["cells"]
    filmed = hop()[rep["cycle"]["start"]: rep["cycle"]["start"] + L]
    assert cells[0].tobytes() == filmed[0].tobytes() and cells[-1].tobytes() == filmed[-1].tobytes()
    assert record["out_fps"]["applied"] is True


def test_a_cell_at_a_higher_rate_whose_frame_between_shows_a_frame_again_is_named_shown_again():
    # Five frames at 24 fps given at 60: eleven cells, three on a frame's time. An interpolator that gives back the frame
    # before makes none of the eight between: each is the nearer frame, named.
    frames = walk()[:5]
    out, record = held_fill.upsample(frames, lambda a, b, t: a, ring=False, fps=24.0, out_fps=60.0)
    assert record["made"] == 0 and record["shown_again"] == [1, 2, 3, 4, 6, 7, 8, 9]
    assert all(t["faults"] == ["held"] for m in record["frames"] for t in m["tries"])
    # Each tried again only later than its own time: a frame nearer the one it showed would show it too.
    assert all(t["t"] >= m["t"] for m in record["frames"] for t in m["tries"])
    assert [o.tobytes() for o in out] == [frames[round(k * 0.4)].tobytes() for k in range(11)]


def test_two_cells_at_a_higher_rate_between_one_pair_are_not_one_picture():
    # Given at 60, the cells at 0.4 and 0.8 of a step both lie between its two frames. An interpolator that draws the
    # time half way whatever it is asked makes the first; the second would show it again, and is the nearer frame.
    frames = walk()[:5]
    between = Between(walk_at, range(5))
    out, record = held_fill.upsample(frames, lambda a, b, t: between(a, b, 0.5), ring=False, fps=24.0, out_fps=60.0)
    assert record["shown_again"] == [2, 4, 7, 9] and record["made"] == 4
    # The second shows the first again, the cell before it: made again only later, at 0.85, the last fraction there.
    assert [[t["t"] for t in m["tries"]] for m in record["frames"] if m["cell"] == 2] == [[0.8, 0.85]]
    assert out[1].tobytes() == walk_at(0.5).tobytes() and out[2].tobytes() == frames[1].tobytes()
    assert out[3].tobytes() == walk_at(1.5).tobytes() and out[4].tobytes() == frames[2].tobytes()


def test_no_rife_delivers_a_clip_asked_for_at_a_higher_rate_at_its_own(tmp_path, monkeypatch, capsys):
    _no_rife(monkeypatch, tmp_path)
    rep = _run(tmp_path, walk(), "walk60", out_fps=60.0, state="walk", cycle_mode="fixed", start=0, length=N,
               anchor="none", repair="off")
    assert rep["status"] == "passed" and _meta(rep)["frames"] == N and "out_fps" not in _meta(rep)
    assert rep["held_fill"]["out_fps"]["applied"] is False
    err = capsys.readouterr().err
    assert "walk60" in err and "--out-fps 60" in err and loop_mod.rife_mod.install_command() in err


def test_a_rate_under_the_clip_s_own_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="--out-fps 12"):
        _run(tmp_path, walk(), out_fps=12.0, state="walk", cycle_mode="fixed", start=0, length=N, anchor="none")


def test_video_loop_and_video_set_take_the_switches():
    import argparse

    from sprite_gen.video import batch

    parser = argparse.ArgumentParser()
    loop_mod.add_arguments(parser)
    args = parser.parse_args(["--frames-dir", "f", "--out-dir", "o", "--fill-held", "off", "--out-fps", "60"])
    assert (args.fill_held, args.out_fps) == ("off", 60.0)
    assert parser.parse_args(["--frames-dir", "f", "--out-dir", "o"]).fill_held == "auto"
    parser = argparse.ArgumentParser()
    batch.add_arguments(parser)
    args = parser.parse_args(["--out-dir", "o", "--fill-held", "off", "--out-fps", "60"])
    assert (args.fill_held, args.out_fps) == ("off", 60.0)
    assert parser.parse_args(["--out-dir", "o"]).fill_held == "auto"


def test_video_set_hands_the_switches_to_each_cut(monkeypatch, tmp_path):
    from sprite_gen.video import batch

    seen: list[dict] = []
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})

    def run_loop(*a, **kw):
        seen.append(kw)
        return {"cycle": {"length": 10, "period_global": 10, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 10,
                "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"},
                "held_fill": {"cells": 10, "poses": 9, "held_runs": [{"start": 3, "length": 2}], "made": [],
                              "before": {"cells": 10, "poses": 9, "held_runs": [{"start": 3, "length": 2, "filled": False,
                                                                                  "why": "--fill-held off"}]}}}

    def video(image, prompt, out, report, **kw):
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    monkeypatch.setattr(batch.loop_mod, "run_loop", run_loop)
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)
    still = tmp_path / "front.png"
    Image.new("RGBA", (64, 64), (200, 80, 30, 255)).save(still)
    result = batch.run_set(bases={"front": still}, states=["idle"], root=tmp_path / "set", character=None, duration=3,
                           resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=video,
                           align_cycles="off", fill_held="off", out_fps=60.0)
    assert [(kw["fill_held"], kw["out_fps"]) for kw in seen] == [("off", 60.0)]
    # The set's report says each motion's poses over its cells.
    assert result["items"][0]["loop"]["poses"] == "9/10"


def test_a_cut_with_cells_made_is_not_restored_from_its_source_frames(tmp_path):
    """The source frames do not hold a cell made between two of them: restoration (`video-loop-repair`) reads the cut
    as one it cannot project (`source-processing-unsupported`), as it reads a blended wrap."""
    import shutil
    import subprocess

    from sprite_gen.video import source
    from sprite_gen.video.compare import Loop

    if not shutil.which("ffmpeg") or not loop_mod.img2webp_supports_exact():
        pytest.skip("ffmpeg and img2webp >=1.5 required")
    filmed = shown_again(walk(), STALLED)
    keyed = save(filmed, tmp_path / "keyed")
    out = tmp_path / "cut"
    loop_mod.run_loop(keyed, out, fps=24.0, min_len=None, max_len=None, n_out=None, seam_max=loop_mod.SEAM_RATIO_MAX,
                      name="walk", report_path=None, interpolate=Between(walk_at, range(N + 6), period=N), **STALLED_CUT)
    files = sorted(keyed.glob("*.png"))
    frames_report = tmp_path / "frames.json"
    frames_report.write_text(json.dumps({"kind": "sprite-gen-video-frames-report", "fps": 24.0, "frames": len(files),
                                         "width": filmed[0].width, "height": filmed[0].height, "stream_index": 0,
                                         "key": "green"}))
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-framerate", "24", "-i", str(keyed / "%04d.png"), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    manifest = tmp_path / "source.json"
    manifest.write_text(json.dumps(source.manifest(clip=clip, canvas=files[0], frames_report=frames_report, files=files,
                                                   timestamps=source.timestamps(clip, 0))))
    loop = Loop.read(out / "walk.strip.png", out / "walk.strip.json", out / "walk.loop.report.json")
    projection, issue = source.project(loop, source.Source.read(
        source_manifest=manifest, source_frames_dir=keyed, source_clip=clip, source_canvas=files[0],
        source_frames_report=frames_report))
    assert (projection, issue) == (None, "source-processing-unsupported")


@pytest.mark.slow
def test_the_alignment_and_a_stop_refuse_a_loop_cut_at_a_rate_of_its_own(tmp_path):
    from sprite_gen.video import align, stop

    rep = _run(tmp_path, walk(), "walk60", interpolate=Between(walk_at, range(N + 6), period=N), out_fps=60.0,
               state="walk", cycle_mode="fixed", start=0, length=N, anchor="none", repair="off")
    out = Path(rep["out_dir"])
    with pytest.raises(ValueError, match="--out-fps 60"):
        align._loop_files(out)
    with pytest.raises(SystemExit, match="--out-fps 60"):
        stop.read_loop(out / "walk60.strip.json")


def test_the_curation_view_marks_the_cells_made_and_the_cells_still_held(tmp_path):
    """A loop's `cycle/` imported into the curation view (`unpack-atlas --pngs-dir`): each card of a cell the fill made
    says `made`, of a cell still showing the picture before it `held`."""
    from sprite_gen import cli

    rep = _run(tmp_path, shown_again(walk(), {**STALLED, 20: 5}), interpolate=Between(walk_at, range(N + 6), period=N),
               **{**STALLED_CUT, "length": N + 7})
    assert rep["held_fill"]["made"] == [6, 15, 16]
    assert cli.main(["unpack-atlas", "--pngs-dir", str(Path(rep["out_dir"]) / "cycle"), "--out-dir", str(tmp_path / "run")]) == 0
    rows = json.loads((tmp_path / "run" / "frames" / "frames-manifest.json").read_text())["rows"]
    labels = rows[0]["labels"]
    assert [k for k, label in enumerate(labels) if label.endswith(" (made)")] == [6, 15, 16]
    assert [k for k, label in enumerate(labels) if label.endswith(" (held)")] == [24, 25, 26, 27]
    assert labels[0] == "frame-000"
