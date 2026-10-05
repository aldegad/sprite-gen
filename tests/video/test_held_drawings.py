# SPDX-License-Identifier: Apache-2.0
"""Held drawings (docs/loop-repair.md section 4, sprite_gen/video/held.py): a clip that shows each
drawing for two or three frames is told from one drawn every frame by the rhythm of its steps, the
second frame of each pair redrawn a little as a video model does; `video-loop` records how many
drawings the clip shows a second, and `video-cycle-align` names a held loop it stretched past what an
interpolator bridges as a take to film again (`retake`, reason `held-drawings`).

Synthetic figures only: a body and a dark foot going round, as in test_cycle_align."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import align, held
from sprite_gen.video import loop as loop_mod

W, H = 100, 120


def _walker(phase: float, *, redraw: int = 0) -> Image.Image:
    """A body that bobs with the phase and a dark foot going round; `redraw` shifts the body's colour
    a little, as a model redraws the second frame of a pair."""
    a = np.zeros((H, W, 4), dtype=np.uint8)
    bob = round(4 * (1 + math.cos(2 * phase)) / 2)
    a[20 + bob:90, 40:60] = (90 + redraw, 90 + redraw, 200 + redraw, 255)
    fx, fy = 50 + round(20 * math.cos(phase)), 104 + round(8 * math.sin(phase))
    a[fy - 5:fy + 5, fx - 5:fx + 5] = (60, 60, 60, 255)
    return Image.fromarray(a, "RGBA")


def _clip(cycle: int, frames: int, hold: int, *, redraw: int = 6) -> list[Image.Image]:
    """`frames` frames of a walk whose cycle is `cycle` frames, each drawing shown `hold` times — each
    repeat redrawn a little (`redraw` per repeat), never a byte copy."""
    return [_walker(2 * math.pi * (k - k % hold) / cycle, redraw=redraw * (k % hold)) for k in range(frames)]


def _uneven(cycle: int, frames: int) -> list[Image.Image]:
    """Drawn every frame, but the steps alternate long and short (0.6 of each other): uneven timing, not a hold."""
    t, times = 0.0, []
    for k in range(frames):
        times.append(t)
        t += (1.25 if k % 2 == 0 else 0.75)
    return [_walker(2 * math.pi * x / cycle) for x in times]


def _steps(frames: list[Image.Image]) -> list[float]:
    feats = np.stack([loop_mod._small_features(f) for f in frames])
    return [float(v) for v in np.abs(feats[1:] - feats[:-1]).mean(axis=1)]


@pytest.mark.parametrize("hold, per_second", [(1, 24.0), (2, 12.0), (3, 8.0)])
def test_the_hold_and_the_drawings_a_second_are_read_off_the_rhythm(hold, per_second):
    m = held.measure(_steps(_clip(24, 72, hold)), fps=24.0)
    assert m["hold"] == hold and m["frames"] == 72
    assert m["drawings_per_second"] == pytest.approx(per_second, abs=0.5)
    if hold > 1:
        assert m["contrast"][str(hold)] < held.CONTRAST_MAX


def test_a_pair_redrawn_a_little_is_missed_by_a_fraction_of_the_median_step_but_not_by_the_rhythm():
    """The rule this replaces: a step under a quarter of the median is held. With half the steps held
    the median falls between the two kinds, and a redrawn pair's step is above a quarter of it."""
    s = np.array(_steps(_clip(15, 73, 2, redraw=10)))
    assert (s < 0.25 * np.median(s)).sum() < 0.35 * len(s)  # the old rule: not a paired clip
    m = held.measure(s, fps=24.0)
    assert m["hold"] == 2 and m["held_steps"] == 36 and m["drawings_per_second"] == pytest.approx(12.0, abs=0.5)


def test_steps_that_alternate_long_and_short_are_not_a_hold():
    m = held.measure(_steps(_uneven(24, 72)), fps=24.0)
    assert m["hold"] == 1 and m["drawings"] == 72
    assert min(v for v in m["contrast"].values()) > held.CONTRAST_MAX


def test_a_frame_repeated_now_and_then_is_not_a_hold():
    """A 20 fps clip carried at 24: one frame in six shown again (redrawn a little), the rest every frame."""
    times = [k - k // 6 for k in range(72)]  # 0 1 2 3 4 5 5 6 7 8 9 10 10 ...
    frames = [_walker(2 * math.pi * x / 20, redraw=6 if k and times[k] == times[k - 1] else 0) for k, x in enumerate(times)]
    m = held.measure(_steps(frames), fps=24.0)
    assert m["hold"] == 1 and min(m["contrast"].values()) > held.CONTRAST_MAX


def test_a_ring_counts_its_drawings_and_too_few_steps_say_nothing():
    ring = _clip(16, 16, 2)
    feats = np.stack([loop_mod._small_features(f) for f in ring])
    steps = [float(np.abs(feats[(k + 1) % 16] - feats[k]).mean()) for k in range(16)]
    m = held.measure(steps, fps=24.0, cyclic=True)
    assert m["hold"] == 2 and m["drawings"] == 8 and m["frames"] == 16 and m["drawings_per_second"] == pytest.approx(12.0)
    short = held.measure(steps[:4], fps=24.0)
    assert short["hold"] is None and "too few" in short["why"]
    still = held.measure([0.0] * 10, fps=24.0)
    assert still["hold"] is None and still["why"] == "no motion between frames"


def _keyed(tmp_path: Path, name: str, frames: list[Image.Image]) -> Path:
    keyed = tmp_path / f"{name}-keyed"
    keyed.mkdir(parents=True)
    for k, f in enumerate(frames):
        f.save(keyed / f"{k:04d}.png")
    return keyed


def _cut(tmp_path: Path, name: str, cycle: int, hold: int) -> Path:
    """A loop of one `cycle`-frame cycle cut out of a clip drawn with `hold`."""
    keyed = _keyed(tmp_path, name, _clip(cycle, cycle + 8, hold))
    out = tmp_path / name
    loop_mod.run_loop(keyed, out, fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                      name="walk", report_path=None, cycle_mode="fixed", start=0, length=cycle, anchor="none", repair="off")
    return out


@pytest.mark.parametrize("hold, per_second", [(1, 24.0), (2, 12.0), (3, 8.0)])
def test_video_loop_records_the_clips_drawings_a_second(tmp_path, hold, per_second):
    d = _cut(tmp_path, "S", 24, hold)
    report = json.loads((d / "walk.loop.report.json").read_text())
    assert report["drawings"]["hold"] == hold
    assert report["drawings"]["drawings_per_second"] == pytest.approx(per_second, abs=0.7)
    meta = json.loads((d / "walk.strip.json").read_text())
    assert meta["drawings"] == {k: report["drawings"][k] for k in ("hold", "drawings_per_second", "contrast", "frames")}


def _cross_fade(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
    """An interpolator that cannot follow the motion: two half-covered ghosts, the outline lost."""
    return Image.blend(a, b, t)


def _follows(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
    return a.copy()  # a frame the interpolator drew whole, outlined as its neighbours


def test_a_held_loop_stretched_past_what_an_interpolator_bridges_is_named_for_a_retake(tmp_path, capsys):
    """A cycle of 16 frames on twos (8 drawings) stretched to 24: 8 drawings a second, three frames
    apart, and the frames between them melt — a take to film again. The set is still aligned."""
    dirs = [_cut(tmp_path, "NE", 16, 2), _cut(tmp_path, "SW", 24, 2), _cut(tmp_path, "E", 26, 1)]
    report = align.align_set(dirs, interpolate=_cross_fade, length=24, report_path=tmp_path / "align.json")
    assert report["applied"] is True and report["replaced"] > 0
    by = {Path(r["dir"]).name: r for r in report["loops"]}
    assert by["NE"]["drawings"]["hold"] == 2 and by["SW"]["drawings"]["hold"] == 2 and by["E"]["drawings"]["hold"] == 1
    retake = by["NE"]["retake"]
    assert retake["reason"] == "held-drawings" and retake["hold"] == 2 and retake["source"] == "clip" and retake["drawings"] == 8
    assert retake["drawings_per_second"] == pytest.approx(8.0) and retake["frames_per_drawing"] == pytest.approx(3.0)
    assert retake["unmade"] == len(by["NE"]["nearest_at"]) > 0
    # on twos at its own length nothing is made; drawn every frame, a replaced frame is no reason to film again
    assert by["SW"]["retake"] is None and by["SW"]["made_by_rife"] == 0
    assert by["E"]["retake"] is None and len(by["E"]["nearest_at"]) > 0
    assert [(r["name"], r["reason"]) for r in report["retake"]] == [("walk", "held-drawings")]
    assert Path(report["retake"][0]["dir"]) == dirs[0]
    lines = [w for w in report["warnings"] if "film this direction again (held-drawings)" in w]
    assert len(lines) == 1 and "8 drawings a second" in lines[0] and lines[0].startswith(f"{dirs[0]}: ")  # every strip is `walk`
    written = json.loads((tmp_path / "align.json").read_text())
    assert written["retake"] == report["retake"]
    meta = json.loads((dirs[0] / "walk.strip.json").read_text())
    assert meta["cycle_align"]["retake"]["reason"] == "held-drawings"


def test_a_held_loop_whose_frames_between_are_all_made_is_not_named(tmp_path):
    dirs = [_cut(tmp_path, "NE", 16, 2), _cut(tmp_path, "S", 24, 1)]
    report = align.align_set(dirs, interpolate=_follows, length=24)
    assert report["retake"] == [] and report["replaced"] == 0
    assert not any("film this direction again" in w for w in report["warnings"])


def test_between_nearest_and_rife_name_the_frames_not_made(tmp_path):
    """nearest makes no frame, so every one between drawings is taken; rife keeps a melted frame, a fault."""
    nearest = align.align_set([_cut(tmp_path / "n", "NE", 16, 2), _cut(tmp_path / "n", "S", 24, 1)], length=24, between="nearest")
    row = next(r for r in nearest["loops"] if Path(r["dir"]).name == "NE")
    assert [r["reason"] for r in nearest["retake"]] == ["held-drawings"] and nearest["retake"][0]["unmade"] == len(row["nearest_at"]) == 16
    kept = align.align_set([_cut(tmp_path / "r", "NE", 16, 2), _cut(tmp_path / "r", "S", 24, 1)], length=24,
                           interpolate=_cross_fade, between="rife")
    assert kept["replaced"] == 0 and kept["retake"] and kept["retake"][0]["unmade"] > 0


def test_a_held_loop_squeezed_past_on_twos_is_not_named(tmp_path):
    """On twos, 32 frames to 24: sixteen drawings in a second, closer than on twos (12) and above
    RETAKE_DRAWINGS_MIN, so a melted frame there is no reason to film it again."""
    dirs = [_cut(tmp_path, "A", 32, 2), _cut(tmp_path, "B", 24, 1)]
    report = align.align_set(dirs, interpolate=_cross_fade, length=24, cycles={"A": 1})  # the bob reads as two cycles
    row = next(r for r in report["loops"] if Path(r["dir"]).name == "A")
    assert row["drawings"]["hold"] == 2 and row["drawings"]["drawings"] == pytest.approx(16, abs=0.5)
    assert len(row["nearest_at"]) > 0 and row["retake"] is None


def test_the_clips_hold_is_read_from_the_strip_and_the_cycle_is_the_fallback(tmp_path):
    """`video-loop` writes the clip's hold into the strip metadata, and the alignment reads it there; a
    loop cut before that record is measured on its own cycle, read as a ring."""
    dirs = [_cut(tmp_path, "NE", 16, 2), _cut(tmp_path, "S", 24, 1)]
    meta = json.loads((dirs[0] / "walk.strip.json").read_text())
    assert meta["drawings"]["hold"] == 2 and meta["drawings"]["drawings_per_second"] == pytest.approx(12.0)
    report = align.align_set(dirs, interpolate=_cross_fade, length=24)
    assert [r["drawings"]["source"] for r in report["loops"]] == ["clip", "clip"]
    again = json.loads((dirs[0] / "walk.strip.json").read_text())
    assert again["drawings"] == meta["drawings"]  # an alignment keeps the clip's record for the next one
    for d in dirs:
        m = json.loads((d / "walk.strip.json").read_text())
        del m["drawings"]
        (d / "walk.strip.json").write_text(json.dumps(m))
    old = align.align_set(dirs, interpolate=_cross_fade, length=24)
    row = next(r for r in old["loops"] if Path(r["dir"]).name == "NE")
    assert row["drawings"]["source"] == "cycle" and row["drawings"]["hold"] == 2 and row["drawings"]["drawings"] == 8
    assert row["retake"]["reason"] == "held-drawings" and row["retake"]["source"] == "cycle"


def test_video_cycle_align_warns_and_prints_the_retake_with_exit_zero(tmp_path, capsys, monkeypatch):
    dirs = [_cut(tmp_path, "NE", 16, 2), _cut(tmp_path, "S", 24, 1)]
    monkeypatch.setattr(align.rife_mod, "Rife", lambda: _Fade())
    assert align.run(loop_dir=dirs, length=24, report=tmp_path / "align.json") == 0
    captured = capsys.readouterr()
    assert f"video-cycle-align: warning: {dirs[0]}: film this direction again (held-drawings)" in captured.err
    assert json.loads(captured.out)["retake"][0]["reason"] == "held-drawings"


class _Fade:
    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        return _cross_fade(a, b, t)

    def describe(self) -> dict[str, str]:
        return {"binary": "stand-in"}


def test_video_set_carries_the_retake_into_its_record(tmp_path):
    from sprite_gen.video import batch

    rows = []
    for direction, cycle, hold in (("back", 16, 2), ("side", 24, 1)):
        item = tmp_path / f"{direction}-walk"
        item.mkdir()
        _cut(item, "loop", cycle, hold)
        rows.append({"item": f"{direction}-walk", "direction": direction, "state": "walk", "dir": str(item), "ok": True,
                     "loop": {"n_out": cycle}})
    out = batch.align_gaits(rows, tmp_path, "auto", interpolate=_cross_fade)
    assert out["walk"]["applied"] and [r["reason"] for r in out["walk"]["retake"]] == ["held-drawings"]
    assert rows[0]["loop"]["cycle_align"]["retake"]["reason"] == "held-drawings" and rows[1]["loop"]["cycle_align"]["retake"] is None
