# SPDX-License-Identifier: Apache-2.0
"""One cycle length per set (docs/loop-repair.md section 4): every loop resampled to the median
length at times k·L/L*, a time on a source frame keeping that frame as filmed and only the times
between frames made by the interpolator; each loop turned to start where the body is lowest; the
cut as filmed kept in `cycle.source/`, so a second alignment reads the source, not its own output.

The interpolator is a stand-in that records what it was asked for and cross-fades."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import align
from sprite_gen.video import loop as loop_mod

W, H = 100, 120


def _walker(phase: float) -> Image.Image:
    """A body that bobs with the phase (lowest at phase 0) and a foot going round."""
    a = np.zeros((H, W, 4), dtype=np.uint8)
    bob = round(4 * (1 + math.cos(2 * phase)) / 2)  # two lows a cycle, as a walk has
    a[20 + bob:90, 40:60] = (90, 90, 200, 255)
    fx, fy = 50 + round(20 * math.cos(phase)), 104 + round(8 * math.sin(phase))
    a[fy - 5:fy + 5, fx - 5:fx + 5] = (60, 60, 60, 255)
    return Image.fromarray(a, "RGBA")


class Recorder:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        self.calls.append(round(t, 4))
        return Image.blend(a, b, t)  # a frame of its own, never a copy of a neighbour


def test_resample_keeps_every_frame_that_lands_on_a_source_frame():
    frames = [_walker(2 * math.pi * k / 6) for k in range(6)]
    rec = Recorder()
    out, facts = align.resample(frames, 12, rec)
    assert facts == {"from": 6, "to": 12, "taken": 6, "made_by_rife": 6, "made_at": [1, 3, 5, 7, 9, 11]}
    assert all(out[2 * k] is frames[k] for k in range(6)) and rec.calls == [0.5] * 6
    same, facts = align.resample(frames, 6, None)  # already that long: untouched, nothing made
    assert facts["made_by_rife"] == 0 and all(a is b for a, b in zip(same, frames))
    with pytest.raises(ValueError, match="no interpolator"):
        align.resample(frames, 9, None)


def test_the_loop_starts_where_the_body_is_lowest():
    frames = [_walker(2 * math.pi * k / 12) for k in range(12)]
    turned = frames[5:] + frames[:5]
    start = align.foot_strike_start(turned)
    assert turned[start] is frames[0] or turned[start] is frames[6]  # the two lows of the bob


def _cut(tmp_path: Path, name: str, length: int) -> Path:
    keyed = tmp_path / f"{name}-keyed"
    keyed.mkdir()
    for k in range(length + 6):
        _walker(2 * math.pi * k / length).save(keyed / f"{k:04d}.png")
    out = tmp_path / name
    loop_mod.run_loop(keyed, out, fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                      name="walk", report_path=None, cycle_mode="fixed", start=0, length=length, anchor="none", repair="off")
    return out


def test_a_set_is_aligned_to_its_median_length_and_a_second_run_reads_the_source(tmp_path):
    dirs = [_cut(tmp_path, n, L) for n, L in (("S", 20), ("E", 24), ("N", 28))]
    rec = Recorder()
    report = align.align_set(dirs, interpolate=rec, report_path=tmp_path / "align.json")
    assert report["length"] == 24 and report["lengths"] == [20, 24, 28] and report["length_rule"] == "median"
    by = {Path(r["dir"]).name: r for r in report["loops"]}
    assert by["E"]["made_by_rife"] == 0 and by["E"]["taken"] == 24
    assert by["S"]["made_by_rife"] > 0 and by["N"]["made_by_rife"] > 0
    assert report["made_by_rife"] == len(rec.calls) == by["S"]["made_by_rife"] + by["N"]["made_by_rife"]
    for d in dirs:
        meta = json.loads((d / "walk.strip.json").read_text())
        assert meta["frames"] == 24 and meta["cycle_frames"] == 24 and meta["cycle_align"]["to"] == 24
        assert meta["cycle_seconds"] == pytest.approx(1.0)
        assert len(list((d / "cycle").glob("frame-*.png"))) == 24
        assert Image.open(d / "walk.gif").n_frames == 24
        assert (d / align.SOURCE_DIR).is_dir()
        lowest = align.foot_strike_start([Image.open(p).convert("RGBA") for p in sorted((d / "cycle").glob("frame-*.png"))])
        assert lowest == 0
    assert len(list((dirs[0] / align.SOURCE_DIR).glob("frame-*.png"))) == 20
    first = (dirs[0] / "walk.strip.png").read_bytes()
    again = align.align_set(dirs, interpolate=Recorder())
    assert again["lengths"] == [20, 24, 28]  # read from cycle.source, not the aligned cycle
    assert (dirs[0] / "walk.strip.png").read_bytes() == first
    assert json.loads((tmp_path / "align.json").read_text())["kind"] == "sprite-gen-video-cycle-align-report"


def test_loops_at_different_frame_rates_are_refused(tmp_path):
    a, b = _cut(tmp_path, "A", 20), _cut(tmp_path, "B", 24)
    meta = json.loads((b / "walk.strip.json").read_text())
    meta["cycle_seconds"] = 24 / 30
    (b / "walk.strip.json").write_text(json.dumps(meta))
    with pytest.raises(SystemExit, match="different frame rates"):
        align.align_set([a, b], interpolate=Recorder())


def test_a_loop_cut_before_the_cell_cap_was_recorded_is_refused_by_name(tmp_path):
    d = _cut(tmp_path, "A", 20)
    meta = json.loads((d / "walk.strip.json").read_text())
    del meta["cell_height_cap"]
    (d / "walk.strip.json").write_text(json.dumps(meta))
    with pytest.raises(SystemExit, match="cell_height_cap"):
        align.align_set([d], interpolate=Recorder())


def test_video_set_aligns_each_gait_filmed_in_two_or_more_directions(tmp_path):
    from sprite_gen.video import batch

    rows = []
    for direction, length in (("front", 20), ("side", 24), ("back", 28)):
        item = tmp_path / f"{direction}-walk"
        item.mkdir()
        _cut(item, "loop", length)
        rows.append({"item": f"{direction}-walk", "direction": direction, "state": "walk", "dir": str(item), "ok": True,
                     "loop": {"n_out": length}})
    lone = tmp_path / "side-run"
    lone.mkdir()
    _cut(lone, "loop", 18)
    rows.append({"item": "side-run", "direction": "side", "state": "run", "dir": str(lone), "ok": True, "loop": {"n_out": 18}})
    out = batch.align_gaits(rows, tmp_path, "auto", interpolate=Recorder())
    assert set(out) == {"walk"}  # a run in one direction has nothing to match
    assert out["walk"]["ok"] and out["walk"]["length"] == 24 and out["walk"]["lengths"] == [20, 24, 28]
    assert [r["loop"]["n_out"] for r in rows[:3]] == [24, 24, 24] and rows[3]["loop"]["n_out"] == 18
    assert rows[0]["loop"]["cycle_align"]["from"] == 20
    assert (tmp_path / "walk.cycle-align.json").is_file()
    assert batch.align_gaits(rows, tmp_path, "off") == {}


def _no_rife(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    # PATH keeps every other tool (img2webp, ffmpeg); only the directories holding a RIFE go
    path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not (Path(d) / "rife-ncnn-vulkan").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(path))
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))


def test_video_cycle_align_without_rife_fails_and_leaves_the_set_as_cut(tmp_path, monkeypatch):
    dirs = [_cut(tmp_path, n, L) for n, L in (("S", 20), ("E", 24), ("N", 28))]
    before = [(d / "walk.strip.png").read_bytes() for d in dirs]
    _no_rife(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match=r"video-cycle-align: .*rife-ncnn-vulkan not found.*sprite-gen rife install"):
        align.run(loop_dir=dirs, length=None, report=None)
    assert [(d / "walk.strip.png").read_bytes() for d in dirs] == before


def test_video_set_without_rife_skips_the_alignment_with_a_warning(tmp_path, monkeypatch, capsys):
    from sprite_gen.video import batch

    rows = []
    for direction, length in (("front", 20), ("side", 24)):
        item = tmp_path / f"{direction}-walk"
        item.mkdir()
        _cut(item, "loop", length)
        rows.append({"item": f"{direction}-walk", "direction": direction, "state": "walk", "dir": str(item), "ok": True,
                     "loop": {"n_out": length}})
    _no_rife(monkeypatch, tmp_path)
    out = batch.align_gaits(rows, tmp_path, "auto")
    assert out["walk"]["ok"] is True and out["walk"]["applied"] is False
    assert out["walk"]["install"] == "sprite-gen rife install" and "rife-ncnn-vulkan not found" in out["walk"]["rife"]
    assert [r["loop"]["n_out"] for r in rows] == [20, 24] and "cycle_align" not in rows[0]["loop"]
    assert not (tmp_path / "walk.cycle-align.json").exists()
    err = capsys.readouterr().err
    assert "video-set: warning: walk: cycles not aligned" in err and "sprite-gen rife install" in err


def test_cutting_a_loop_again_drops_the_kept_source(tmp_path):
    """Once RIFE is installed a loop is cut again; the next alignment must read that cut."""
    dirs = [_cut(tmp_path, n, L) for n, L in (("S", 20), ("E", 24))]
    align.align_set(dirs, interpolate=Recorder())
    assert (dirs[0] / align.SOURCE_DIR).is_dir()
    keyed = tmp_path / "S-keyed"
    loop_mod.run_loop(keyed, dirs[0], fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                      name="walk", report_path=None, cycle_mode="fixed", start=0, length=22, anchor="none", repair="off")
    assert not (dirs[0] / align.SOURCE_DIR).exists()
    assert align.align_set(dirs, interpolate=Recorder())["lengths"] == [22, 24]
