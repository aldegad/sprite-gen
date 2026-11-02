# SPDX-License-Identifier: Apache-2.0
"""No picture shown again by default, on every way the engine delivers a loop (docs/loop-repair.md section 7): a loop's
last cells and the step across its wrap are read and filled like any held run; the cut is read again once its repairs
have run; an alignment aligns the cut as filmed (a filled loop is cut again with the fill off, or the set stops naming
it) and fills what it delivers at the end, at a rate of its own where one is asked; a set cuts the directions it aligns
as filmed and cuts again, filled, the ones no alignment took. A hold the motion means stays and is listed in the strip
(`holds`); a loop with no held cell is delivered as before.

The interpolator is `Between`, a stand-in that draws the synthetic clip again at the time asked for."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.video import align, held_fill
from sprite_gen.video import loop as loop_mod
from tests.picture_digest import picture_digest
from tests.video.held_fill_inputs import N, Between, save, shown_again, walk, walk_at


def _cut(tmp_path: Path, name: str, frames: list[Image.Image], *, length: int, fill_held: str = "auto", **kw) -> Path:
    keyed = save(frames, tmp_path / f"{name}-keyed")
    out = tmp_path / name
    loop_mod.run_loop(keyed, out, fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                      name="walk", report_path=None, cycle_mode="fixed", start=0, length=length, anchor="none",
                      repair="off", fill_held=fill_held, **kw)
    return out


def _cells(d: Path) -> list[Image.Image]:
    return [Image.open(p).convert("RGBA") for p in sorted((d / "cycle").glob("frame-*.png"))]


def _meta(d: Path) -> dict:
    return json.loads((d / "walk.strip.json").read_text())


def _shown_again(frames: list[Image.Image]) -> list[int]:
    """The cells that show the picture of the cell before them, round a loop."""
    same = held_fill.same_steps(frames + frames[:1])
    return [(k + 1) % len(frames) for k, s in enumerate(same) if s]


HELD = {5: 2}  # the walk's sixth frame shown twice: a clip that stalls once a cycle


def _held_clip() -> list[Image.Image]:
    return shown_again(walk(N + 6), HELD)


def _between() -> Between:
    return Between(walk_at, [k / 4 for k in range(4 * (N + 6))], period=N)


# --- video-loop: the loop's last cells and its wrap ---------------------------------------------------------------


def test_a_loop_cut_whose_last_cells_show_one_picture_is_delivered_with_none_shown_again(tmp_path):
    # The cut's last two cells show one picture (frame 22 shown twice): before, kept as filmed, "the loop's last cells".
    frames = shown_again(walk(N + 6), {N - 2: 2})
    d = _cut(tmp_path, "end", frames, length=N, interpolate=_between())
    cells = _cells(d)
    assert _shown_again(cells) == []
    held = _meta(d)["held_fill"]
    assert held["made"] == [N - 1] and held["before"]["held_runs"] == [{"start": N - 2, "length": 2, "filled": True, "made": [N - 1]}]
    assert cells[0].tobytes() == frames[0].tobytes()


def test_a_loop_cut_a_cell_too_long_has_its_last_cell_made_between_and_its_first_kept(tmp_path):
    # The cut runs one frame past the cycle: its last cell shows its first cell's picture again, across the wrap.
    frames = walk(N + 6)
    d = _cut(tmp_path, "long", frames, length=N + 1, interpolate=_between())
    cells = _cells(d)
    assert _shown_again(cells) == [] and len(cells) == N + 1
    held = _meta(d)["held_fill"]
    assert held["before"]["wrap_held"] is True and held["before"]["wrap"]["made"] == [N]
    assert cells[0].tobytes() == frames[0].tobytes()


def test_the_cut_is_read_again_after_its_repairs_and_a_run_they_left_is_made():
    # A repair that gives a cell way to the frame beside it leaves that frame shown twice; read again, it is made.
    frames = [walk_at(t) for t in range(N)]
    _, record = held_fill.fill(frames, _between(), ring=True)
    assert record["made"] == []
    repaired = list(frames)
    repaired[9] = repaired[8]
    out, again = held_fill.again(repaired, _between(), record, ring=True)
    assert again is not None and again["made"] == [9] and out[9].tobytes() == walk_at(9).tobytes()
    # a cell a repair already gave way, its frame between refused, is not tried again
    out, again = held_fill.again(repaired, _between(), record, ring=True, skip=[9])
    assert again is None and out[9].tobytes() == frames[8].tobytes()


def test_the_holds_the_motion_means_are_kept_and_listed_in_the_strip(tmp_path):
    # Frame 5 shown four times: longer than a drawing on threes, the motion's own hold.
    frames = shown_again(walk(N + 6), {5: 4})
    d = _cut(tmp_path, "hold", frames, length=N + 3, interpolate=_between())
    meta = _meta(d)
    assert meta["held_fill"]["made"] == [] and [r["start"] for r in meta["held_fill"]["held_runs"]] == [5]
    assert meta["holds"] == [{"from": 5, "to": 8, "kind": "motion-hold",
                              "reason": meta["held_fill"]["before"]["held_runs"][0]["why"]}]
    assert [c.tobytes() for c in _cells(d)[5:9]] == [frames[5].tobytes()] * 4


def test_a_loop_records_what_its_cut_was_asked_and_is_cut_again_from_it_the_same(tmp_path):
    frames = _held_clip()
    keyed = save(frames, tmp_path / "k")
    first = loop_mod.run_loop(keyed, tmp_path / "a", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                              seam_max=1000.0, name="walk", report_path=None, cycle_mode="fixed", start=0, length=N + 1,
                              anchor="none", repair="off", interpolate=_between())
    report = json.loads(Path(first["report"]).read_text())
    assert report["cut_args"]["fill_held"] == "auto" and report["cut_args"]["length"] == N + 1
    again = loop_mod.cut_again(report, tmp_path / "b", report_path=tmp_path / "b.json", fill_held="off")
    assert again["held_fill"]["made"] == [] and [r["start"] for r in again["held_fill"]["held_runs"]] == [5]
    assert [c.tobytes() for c in _cells(tmp_path / "b")] == [f.tobytes() for f in frames[: N + 1]]
    with pytest.raises(ValueError, match="no cut_args"):
        loop_mod.cut_again({k: v for k, v in report.items() if k != "cut_args"}, tmp_path / "c", report_path=tmp_path / "c.json")


def test_an_effect_s_lifetime_names_its_held_cells(tmp_path, capsys):
    import shutil

    from tests.video.test_effect import cut, lifetime_frames

    keyed = lifetime_frames(tmp_path)
    shutil.copyfile(keyed / "frame-040.png", keyed / "frame-041.png")  # the burst stalls a frame
    cut(keyed, tmp_path / "out")
    assert "of the effect's lifetime show one picture" in capsys.readouterr().err


# --- video-cycle-align: the cut as filmed in, no picture shown again out ------------------------------------------


def test_an_aligned_set_whose_cuts_hold_a_picture_twice_delivers_none_shown_again(tmp_path):
    clip = _held_clip()
    dirs = [_cut(tmp_path, n, c, length=N + 1, fill_held="off") for n, c in (("A", clip), ("B", clip))]
    report = align.align_set(dirs, interpolate=_between())
    for d, row in zip(dirs, report["loops"]):
        cells = _cells(d)
        assert len(cells) == N + 1 and _shown_again(cells) == []
        meta = _meta(d)
        # the strip's record is the alignment's, not the cut's
        assert meta["held_fill"]["made"] and meta["held_fill"] == row["held_fill"] and meta["holds"] == []
        assert any("held_fill" in c for c in meta["cycle_align"]["cells_from"])


def test_the_alignment_cuts_a_filled_loop_again_with_the_fill_off_and_aligns_the_frames_as_filmed(tmp_path):
    clip = _held_clip()
    dirs = [_cut(tmp_path, n, c, length=N + 1, interpolate=_between()) for n, c in (("A", clip), ("B", clip))]
    assert all(_meta(d)["held_fill"]["made"] == [6] for d in dirs)  # video-loop on its own fills, a loop delivered
    report = align.align_set(dirs, interpolate=_between())
    for d, row in zip(dirs, report["loops"]):
        source = sorted((d / align.SOURCE_DIR).glob("frame-*.png"))
        # the frames aligned are the cut as filmed: frame 5 shown twice, not the frame made between
        assert Path(source[6]).read_bytes() == Path(source[5]).read_bytes()
        assert row["cut_again"]["made_before"] == [6] and row["cut_again"]["fill_held"] == "off"
        assert not align.filled(json.loads((d / align.SOURCE_DIR / "walk.strip.json").read_text()))
        assert _shown_again(_cells(d)) == []
    again = align.align_set(dirs, interpolate=_between())  # a second alignment reads the staged cut, cutting nothing
    assert all("cut_again" not in r for r in again["loops"])


def test_a_filled_loop_whose_cut_cannot_be_made_again_stops_the_set_naming_it(tmp_path):
    clip = _held_clip()
    dirs = [_cut(tmp_path, n, c, length=N + 1, interpolate=_between()) for n, c in (("A", clip), ("B", clip))]
    report_path = dirs[1] / "walk.loop.report.json"
    report = json.loads(report_path.read_text())
    report.pop("cut_args")  # a report from before cuts recorded what they were asked
    report_path.write_text(json.dumps(report))
    with pytest.raises(SystemExit, match=r"no cut_args.*video-loop --fill-held off"):
        align.align_set(dirs, interpolate=_between())
    assert not (dirs[1] / align.SOURCE_DIR / "walk.strip.json").exists()


def test_an_aligned_set_asked_for_at_a_rate_of_its_own_is_given_frames_between_every_loop_alike(tmp_path):
    clip = walk(N + 6)
    dirs = [_cut(tmp_path, n, c, length=N, fill_held="off") for n, c in (("A", clip), ("B", clip))]
    report = align.align_set(dirs, interpolate=_between(), out_fps=60.0)
    assert report["out_fps"] == 60.0
    for d in dirs:
        cells = _cells(d)
        meta = _meta(d)
        assert len(cells) == 60 and _shown_again(cells) == []
        assert meta["out_fps"] == 60.0 and meta["source_fps"] == 24.0 and meta["cycle_seconds"] == pytest.approx(1.0)
    again = align.align_set(dirs, interpolate=_between())  # aligned again from cycle.source/ at the clip's rate
    assert again["lengths"] == [N, N] and "out_fps" not in _meta(dirs[0])
    with pytest.raises(SystemExit, match="is not above"):
        align.align_set(dirs, interpolate=_between(), out_fps=12.0)


# Pinned on the engine before this change (4f5ccd5): a set with no held cell is aligned as it was.
UNHELD_ALIGNED = "68abe86925f7ccd67dc86ed7b6d8214a4d3d7b0b3a3429e6c8b4ed8215e73d4c"


def test_an_aligned_set_with_no_held_cell_is_delivered_as_before(tmp_path):
    clip = walk(N + 6)
    dirs = [_cut(tmp_path, n, c, length=L, fill_held="off") for n, c, L in (("A", clip, N), ("B", clip, N + 4))]
    align.align_set(dirs, interpolate=_between())
    pictures = picture_digest(sorted(p for d in dirs for p in (d / "cycle").glob("frame-*.png")))
    assert pictures == UNHELD_ALIGNED
    off = [_cut(tmp_path, n + "off", c, length=L, fill_held="off") for n, c, L in (("A", clip, N), ("B", clip, N + 4))]
    align.align_set(off, interpolate=_between(), fill_held="off")
    assert picture_digest(sorted(p for d in off for p in (d / "cycle").glob("frame-*.png"))) == pictures


# --- video-set: the directions it aligns cut as filmed, the ones no alignment took cut again filled ----------------


def _set(monkeypatch, tmp_path, *, align_result, out_fps=None):
    from sprite_gen.video import batch

    seen: list[dict] = []
    again: list[dict] = []
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})
    lp = {"cycle": {"length": 10, "period_global": 10, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 10,
          "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"},
          "held_fill": {"cells": 10, "poses": 9, "held_runs": [{"start": 3, "length": 2}], "made": [],
                        "before": {"cells": 10, "poses": 9, "held_runs": [{"start": 3, "length": 2, "filled": False,
                                                                            "why": "--fill-held off"}]}}}

    def run_loop(*a, **kw):
        seen.append(kw)
        Path(kw["report_path"]).write_text(json.dumps({"cut_args": {}, "frames_dir": str(a[0])}))
        return lp

    def cut_again(report, out_dir, *, report_path, **asked):
        again.append(asked)
        return {**lp, "held_fill": {**lp["held_fill"], "made": [4], "held_runs": [], "poses": 10}}

    def video(image, prompt, out, report, **kw):
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    monkeypatch.setattr(batch.loop_mod, "run_loop", run_loop)
    monkeypatch.setattr(batch.loop_mod, "cut_again", cut_again)
    monkeypatch.setattr(batch.align_mod, "align_set", align_result)
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)
    stills = {}
    for view in ("front", "back"):
        stills[view] = tmp_path / f"{view}.png"
        still = Image.new("RGBA", (64, 64), (0, 255, 0, 255))
        still.paste((200, 80, 30, 255), (24, 16, 40, 56))
        still.save(stills[view])
    # The front and back walks film from their base as given: the mid-step start still is `gen`'s (a provider call),
    # and what this set is asked here is where the fill and the rate go.
    result = batch.run_set(bases=stills, states=["walk", "idle"], root=tmp_path / "set", character=None, duration=3,
                           resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=video,
                           out_fps=out_fps, walk_start="as-given")
    return result, seen, again


def test_a_set_cuts_the_directions_it_aligns_as_filmed_and_hands_the_alignment_the_fill_and_the_rate(monkeypatch, tmp_path):
    asked: list[dict] = []

    def align_set(dirs, **kw):
        asked.append(kw)
        return {"length": 10, "lengths": [10, 10], "between": "auto", "made_by_rife": 0, "replaced": 0, "retake": [],
                "unnamed_feet": [], "feet_given": {}, "warnings": [],
                "loops": [{"from": 10, "to": 10, "made_by_rife": 0, "turned_by": 0, "turned_on": "stride", "start_foot": None,
                           "start_foot_source": None, "seam_ratio": 1.0, "retake": None, "strip": {"frames": 10},
                           "held_fill": {"cells": 10, "poses": 10, "held_runs": [], "made": [4],
                                         "before": {"cells": 10, "poses": 9, "held_runs": []}}} for _ in dirs]}

    # Before, --out-fps with an alignment was refused ("pass --align-cycles off with it").
    result, seen, again = _set(monkeypatch, tmp_path, align_result=align_set, out_fps=60.0)
    by = {(kw["state"], kw["view"]): (kw["fill_held"], kw["out_fps"]) for kw in seen}
    assert by == {("walk", "front"): ("off", None), ("walk", "back"): ("off", None),
                  ("idle", "front"): ("auto", 60.0), ("idle", "back"): ("auto", 60.0)}
    assert [(kw["fill_held"], kw["out_fps"]) for kw in asked] == [("auto", 60.0)]
    assert again == []
    walks = [r for r in result["items"] if r["state"] == "walk"]
    assert all(r["loop"]["poses"] == "10/10" and r["loop"]["held_fill"]["made"] == [4] for r in walks)


def test_a_direction_cut_for_an_alignment_that_did_not_take_it_is_cut_again_filled(monkeypatch, tmp_path):
    def align_set(dirs, **kw):
        raise SystemExit("video-cycle-align: refused for the test")

    result, seen, again = _set(monkeypatch, tmp_path, align_result=align_set)
    assert again == [{"fill_held": "auto", "out_fps": None}] * 2
    walks = [r for r in result["items"] if r["state"] == "walk"]
    assert all(r["loop"]["cut_again"]["why"] == "not aligned" and r["loop"]["held_fill"]["made"] == [4] for r in walks)
