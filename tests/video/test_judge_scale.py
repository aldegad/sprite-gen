# SPDX-License-Identifier: Apache-2.0
"""The size a game shows the cells at (`--judge-scale`, docs/loop-repair.md section 7): the alignment
(`video-cycle-align`) and the held fill, `--out-fps` and `--air-retime` (`video-loop`, `video-set`) judge each frame they
make on the frames as cut by default. Given a share of the frames' size, a frame refused for its outline as cut is taken
where its line keeps its ink as seen at that size (`rife.outline_faded`, read on the frames as cut); every other reading
(smear, ghost, hole, doubled, contact) is as at 1, and the frames delivered are the size they were cut at.

A thin line an interpolator softened reads lost as cut and kept at the size a game shows it; legs melted into one shape
of fill, a line gone to the fill and the same line softened are refused at every size from 1 down to 0.2, 1 and 2 px
lines alike; two poses laid over each other are refused at every size. The figures are synthetic and the interpolator a
stand-in that draws the frame between again (`held_fill_inputs.Between`)."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from sprite_gen.video import align, held_fill, interpolation_quality, rife
from sprite_gen.video import loop as loop_mod
from tests.video.held_fill_inputs import N as held_n
from tests.video.held_fill_inputs import Between, save, shown_again, walk

W, H = 160, 200
N = 12  # the walk's cycle, frames
LINE = (20, 20, 30, 255)
PALE = (95, 95, 120, 255)  # a line gone most of the way to the fill beside it


def figure(time: float, *, line: tuple[int, int, int, int] = LINE) -> Image.Image:
    """A body and a head swaying round a small circle and a foot going round a larger one, each filled and outlined one
    pixel wide, `time` frames into the cycle: every part moves every frame, so no part stands still for the contact
    reading."""
    phase = 2 * math.pi * time / N
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x, y = 12 * math.sin(phase), 6 * math.cos(phase)
    d.rounded_rectangle((60 + x, 50 + y, 100 + x, 140 + y), 8, fill=(90, 90, 200, 255), outline=line, width=1)
    d.ellipse((62 + x, 14 + y, 98 + x, 50 + y), fill=(230, 190, 160, 255), outline=line, width=1)
    fx, fy = 80 + 34 * math.cos(phase), 168 + 16 * math.sin(phase)
    d.ellipse((fx - 12, fy - 9, fx + 12, fy + 9), fill=(200, 160, 40, 255), outline=line, width=1)
    return im


def softened(im: Image.Image) -> Image.Image:
    """The frame with its lines softened, as an interpolator's flow softens a thin line on a moving part: lighter, not
    gone."""
    return im.filter(ImageFilter.GaussianBlur(1.0))


def paled(im: Image.Image) -> Image.Image:
    """The frame with its lines gone most of the way to the fill: the outline lost, at any size."""
    a = np.asarray(im).copy()
    line = (np.abs(a[..., :3].astype(int) - np.array(LINE[:3])).max(axis=-1) < 8) & (a[..., 3] == 255)
    a[line] = PALE
    return Image.fromarray(a, "RGBA")


def stalled() -> list[Image.Image]:
    """The walk with two of its frames shown twice: two held runs to fill, neither at an end."""
    return shown_again([figure(k) for k in range(N + 3)], {4: 2, 8: 2})


def legs(time: float, *, width: int = 1, melt: bool = False) -> Image.Image:
    """A body and a head on two legs swinging apart and together, each filled and outlined `width` px, `time` frames
    into the cycle: every part moves every frame. With `melt`, the legs are one shape of fill with no line round or
    between them, as legs crossing further than the flow can follow come out."""
    phase = 2 * math.pi * time / N
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x, y = 8 * math.sin(phase), 4 * math.cos(phase)
    swing = 14 * math.sin(phase)
    boxes = [(66 + x - swing, 120 + y, 80 + x - swing, 184 + y), (80 + x + swing, 120 + y, 94 + x + swing, 184 + y)]
    if melt:
        d.rounded_rectangle((min(l[0] for l in boxes), 120 + y, max(l[2] for l in boxes), 184 + y), 4,
                            fill=(70, 120, 70, 255))
    else:
        for box in boxes:
            d.rounded_rectangle(box, 4, fill=(70, 120, 70, 255), outline=LINE, width=width)
    d.rounded_rectangle((60 + x, 50 + y, 100 + x, 130 + y), 8, fill=(90, 90, 200, 255), outline=LINE, width=width)
    d.ellipse((62 + x, 14 + y, 98 + x, 50 + y), fill=(230, 190, 160, 255), outline=LINE, width=width)
    return im


def soft(im: Image.Image) -> Image.Image:
    """The frame softened as an interpolator's flow softens a line on a moving part, its colours blurred with their
    coverage (premultiplied), so the clear pixels round it lend its rim no dark."""
    x = np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0
    carried = np.dstack([x[..., :3] * x[..., 3:], x[..., 3:]])
    blurred = np.dstack([np.asarray(Image.fromarray(np.uint8(np.round(carried[..., c] * 255))).filter(
        ImageFilter.GaussianBlur(1.0)), dtype=np.float32) / 255.0 for c in range(4)])
    alpha = blurred[..., 3:]
    colour = np.clip(blurred[..., :3] / np.maximum(alpha, 1e-6), 0.0, 1.0)
    return Image.fromarray(np.uint8(np.round(np.dstack([colour, alpha]) * 255)), "RGBA")


# Every share of the size a game might show the cells at, 1 down to 0.2 by 0.05.
SCALES = [round(1 - 0.05 * k, 2) for k in range(17)]
BROKEN = {
    "legs melted": lambda time, width: legs(time, width=width, melt=True),
    "line gone to the fill": lambda time, width: paled(legs(time, width=width)),
    "line gone to the fill, softened": lambda time, width: soft(paled(legs(time, width=width))),
    # Blurred with the clear pixels' black (`softened`), which darkens its rim: some ink back, not a line. At 2 px it
    # reads as kept on the frames as cut already, so the size has nothing to take back there.
    "line gone to the fill, softened over black": lambda time, width: softened(paled(legs(time, width=width))),
}
CASES = [(broken, width) for broken in BROKEN for width in (1, 2)
         if (broken, width) != ("line gone to the fill, softened over black", 2)]


def _outline(made: Image.Image, a: Image.Image, b: Image.Image, scale: float = 1.0) -> bool:
    return "outline" in interpolation_quality.faults(align.measure_made(made, a, b, 0.5, reads=True, scale=scale))


# 1 (the default) judges as cut and adds nothing; a share of the size adds one reading, which can only take back.


def test_the_frames_are_judged_as_cut_by_default():
    a, b, made = figure(0), figure(1), softened(figure(0.5))
    assert interpolation_quality.JUDGE_SCALE == 1.0
    as_cut = align.measure_made(made, a, b, 0.5, reads=True)
    assert "outline_faded" not in as_cut
    assert align.measure_made(made, a, b, 0.5, reads=True, scale=1.0) == as_cut


@pytest.mark.parametrize("judge_scale", [0.6, 0.5, 0.35])
def test_the_size_adds_the_line_seen_there_and_changes_no_other_reading(judge_scale):
    a, b, made = figure(0), figure(1), softened(figure(0.5))
    as_cut = align.measure_made(made, a, b, 0.5, reads=True)
    seen = align.measure_made(made, a, b, 0.5, reads=True, scale=judge_scale)
    assert seen == {**as_cut, "outline_faded": rife.outline_faded(made, a, b, judge_scale)}
    assert "outline" in interpolation_quality.faults(as_cut) and "outline" not in interpolation_quality.faults(seen)


def test_a_frame_with_no_line_to_read_against_has_nothing_taken_back():
    a, b, made = figure(0), figure(1), paled(figure(0.5))
    assert rife.outline_faded(made, Image.new("RGBA", (W, H)), b, 0.5) is None
    measure = align.measure_made(made, a, b, 0.5, reads=True)
    assert "outline" in interpolation_quality.faults({**measure, "outline_faded": None})


@pytest.mark.parametrize(("broken", "width"), CASES)
def test_a_broken_line_is_refused_at_every_size_from_1_to_0_2(broken, width):
    for time in (0, 7):
        a, b, made = legs(time, width=width), legs(time + 1, width=width), BROKEN[broken](time + 0.5, width)
        assert [scale for scale in SCALES if not _outline(made, a, b, scale)] == [], time


def test_the_size_adds_no_refusal_to_a_frame_taken_as_cut():
    # The 2 px line gone to the fill, softened over black, is taken as cut (the black lends its rim a line); read for
    # ink at the size seen its line faded, and still it is taken at every size: the size only takes back.
    for time in (0, 7):
        a, b = legs(time, width=2), legs(time + 1, width=2)
        made = BROKEN["line gone to the fill, softened over black"](time + 0.5, 2)
        assert not _outline(made, a, b) and rife.outline_faded(made, a, b, 0.6) > interpolation_quality.OUTLINE_WARN
        assert [scale for scale in SCALES if _outline(made, a, b, scale)] == [], time


@pytest.mark.parametrize("judge_scale", [0.6, 0.5, 0.35, 0.2])
def test_a_thin_line_softened_is_refused_as_cut_and_taken_at_the_size_seen(judge_scale):
    for time in (0, 7):
        a, b, made = legs(time), legs(time + 1), soft(legs(time + 0.5))
        assert _outline(made, a, b) and not _outline(made, a, b, judge_scale), time


@pytest.mark.parametrize("given", ["0", "-0.5", "1.5", "nan", "inf", "x"])
def test_a_judge_scale_outside_zero_to_one_is_refused(given):
    with pytest.raises(argparse.ArgumentTypeError, match="judge-scale"):
        interpolation_quality.judge_scale(given)


def test_a_judge_scale_in_zero_to_one_is_taken():
    assert interpolation_quality.judge_scale("0.6") == 0.6 and interpolation_quality.judge_scale("1") == 1.0


# The held fill and --out-fps: a frame between whose line was softened is taken at the size seen, not as cut.


def test_a_softened_line_is_refused_as_cut_and_its_held_cells_made_at_the_size_seen():
    frames = stalled()
    out, record = held_fill.fill(frames, Between(figure, range(N + 3), after=softened), ring=False)
    assert record["made"] == [] and all(not r["filled"] and "outline" in r["faults"] for r in record["before"]["held_runs"])
    assert "judge_scale" not in record
    out, record = held_fill.fill(frames, Between(figure, range(N + 3), after=softened), ring=False, judge_scale=0.6)
    assert record["made"] == [5, 10] and record["judge_scale"] == 0.6
    assert all(r["filled"] for r in record["before"]["held_runs"])
    assert out[5].size == frames[5].size  # delivered at the size cut, judged smaller
    assert out[5].tobytes() == softened(figure(4.5)).tobytes()
    assert held_fill.public(record)["judge_scale"] == 0.6


@pytest.mark.parametrize("judge_scale", [1.0, 0.6, 0.5, 0.35])
def test_a_line_gone_to_the_fill_is_refused_at_every_size(judge_scale):
    frames = stalled()
    out, record = held_fill.fill(frames, Between(figure, range(N + 3), after=paled), ring=False, judge_scale=judge_scale)
    assert record["made"] == [] and [o.tobytes() for o in out] == [f.tobytes() for f in frames]
    assert all("outline" in r["faults"] for r in record["before"]["held_runs"])


def test_a_cut_asked_for_at_a_higher_rate_takes_the_softened_frames_at_the_size_seen():
    frames = [figure(k) for k in range(N)]
    out, record = held_fill.upsample(frames, Between(figure, range(N), after=softened), ring=False, fps=24, out_fps=48)
    assert record["made"] == 0 and len(record["shown_again"]) == record["cells"] - N
    assert "judge_scale" not in record
    out, record = held_fill.upsample(frames, Between(figure, range(N), after=softened), ring=False, fps=24, out_fps=48,
                                     judge_scale=0.6)
    assert record["made"] == record["cells"] - N and record["shown_again"] == [] and record["judge_scale"] == 0.6
    assert {o.size for o in out} == {(W, H)}


# The alignment: the same judge, the same size.


def test_the_alignment_keeps_a_softened_frame_at_the_size_seen_and_gives_way_to_the_nearer_frame_as_cut():
    frames = [figure(k) for k in range(N)]
    soft = Between(figure, range(N), period=N, after=softened)
    out, facts = align.resample(frames, 2 * N, soft, between="auto")
    assert facts["made_by_rife"] == 0 and all("outline" in m["faults"] for m in facts["smear"])
    assert "judge_scale" not in facts
    out, facts = align.resample(frames, 2 * N, Between(figure, range(N), period=N, after=softened), between="auto",
                                judge_scale=0.6)
    assert facts["made_by_rife"] == N and all(m["faults"] == [] for m in facts["smear"]) and facts["judge_scale"] == 0.6
    assert {o.size for o in out} == {(W, H)}


@pytest.mark.parametrize("judge_scale", [1.0, 0.6, 0.5, 0.35])
def test_the_alignment_gives_way_for_a_line_gone_to_the_fill_at_every_size(judge_scale):
    frames = [figure(k) for k in range(N)]
    out, facts = align.resample(frames, 2 * N, Between(figure, range(N), period=N, after=paled), between="auto",
                                judge_scale=judge_scale)
    assert facts["made_by_rife"] == 0 and all("outline" in m["faults"] for m in facts["smear"])
    line = align._fault_line("walk", facts["smear"][0])
    assert ("faded at the size the game shows it" in line) is (judge_scale != 1.0)
    assert ("outline_faded" in facts["smear"][0]) is (judge_scale != 1.0)


@pytest.mark.parametrize("judge_scale", [1.0, 0.6, 0.5, 0.35])
def test_the_alignment_gives_way_for_two_poses_laid_over_each_other_at_every_size(judge_scale):
    # a walk of 8 frames a cycle made 24 long by cross-fading its frames: each frame between two poses at part strength
    frames = walk(held_n)[::3]
    out, facts = align.resample(frames, held_n, lambda a, b, t: Image.blend(a, b, t), between="auto",
                                judge_scale=judge_scale)
    assert facts["made_by_rife"] == 0 and all("ghost" in m["faults"] for m in facts["smear"])


# The switches: video-loop, video-set and video-cycle-align take --judge-scale, and hand it on.


def _parsed(add, argv):
    parser = argparse.ArgumentParser()
    add(parser)
    return parser.parse_args(argv)


def test_video_loop_video_set_and_video_cycle_align_take_the_judge_scale():
    from sprite_gen.video import batch

    loop_args = ["--frames-dir", "f", "--out-dir", "o"]
    assert _parsed(loop_mod.add_arguments, loop_args).judge_scale == 1.0
    assert _parsed(loop_mod.add_arguments, loop_args + ["--judge-scale", "0.6"]).judge_scale == 0.6
    assert _parsed(batch.add_arguments, ["--out-dir", "o"]).judge_scale == 1.0
    assert _parsed(batch.add_arguments, ["--out-dir", "o", "--judge-scale", "0.5"]).judge_scale == 0.5
    assert _parsed(align.add_arguments, ["--loop-dir", "a", "--loop-dir", "b"]).judge_scale == 1.0
    assert _parsed(align.add_arguments, ["--loop-dir", "a", "--loop-dir", "b", "--judge-scale", "0.6"]).judge_scale == 0.6
    for add, argv in ((loop_mod.add_arguments, loop_args), (batch.add_arguments, ["--out-dir", "o"]),
                      (align.add_arguments, ["--loop-dir", "a", "--loop-dir", "b"])):
        with pytest.raises(SystemExit):
            _parsed(add, argv + ["--judge-scale", "1.5"])


def test_video_loop_judges_the_held_fill_at_the_scale_given_and_records_it(tmp_path):
    keyed = save(stalled(), tmp_path / "keyed")
    common = dict(fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0, report_path=None,
                  cycle_mode="fixed", start=0, length=N + 2, anchor="none", repair="off")
    rep = loop_mod.run_loop(keyed, tmp_path / "as-cut", name="m", interpolate=Between(figure, range(N + 3), after=softened),
                            **common)
    assert rep["held_fill"]["made"] == [] and "judge_scale" not in rep["held_fill"]
    rep = loop_mod.run_loop(keyed, tmp_path / "seen", name="m", interpolate=Between(figure, range(N + 3), after=softened),
                            judge_scale=0.6, **common)
    assert rep["held_fill"]["made"] == [5, 10] and rep["held_fill"]["judge_scale"] == 0.6
    meta = json.loads((tmp_path / "seen" / "m.strip.json").read_text())
    assert meta["held_fill"]["judge_scale"] == 0.6


def test_video_set_hands_the_judge_scale_to_each_cut_and_to_the_alignment(monkeypatch, tmp_path):
    from sprite_gen.video import batch

    seen: list[dict] = []
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})

    def run_loop(*a, **kw):
        seen.append(kw)
        return {"cycle": {"length": 10, "period_global": 10, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 10,
                "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"},
                "held_fill": {"cells": 10, "poses": 10, "held_runs": [], "made": [],
                              "before": {"cells": 10, "poses": 10, "held_runs": []}}}

    def video(image, prompt, out, report, **kw):
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    aligned: list[dict] = []
    monkeypatch.setattr(batch.loop_mod, "run_loop", run_loop)
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)
    monkeypatch.setattr(batch, "align_gaits", lambda results, root, mode, **kw: aligned.append(kw) or {})
    still = tmp_path / "front.png"
    Image.new("RGBA", (64, 64), (200, 80, 30, 255)).save(still)
    batch.run_set(bases={"front": still}, states=["idle"], root=tmp_path / "set", character=None, duration=3,
                  resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=video,
                  align_cycles="auto", judge_scale=0.6)
    assert [kw["judge_scale"] for kw in seen] == [0.6]
    assert [kw["judge_scale"] for kw in aligned] == [0.6]


def test_the_air_retime_judges_its_frames_between_at_the_scale_given(monkeypatch, tmp_path):
    from sprite_gen.video import root_motion
    from tests.video import jump_inputs as J

    asked: list[float] = []
    real = held_fill.at_times

    def at_times(*a, **kw):
        asked.append(kw.get("judge_scale", interpolation_quality.JUDGE_SCALE))
        return real(*a, **kw)

    monkeypatch.setattr(root_motion.held_fill_mod, "at_times", at_times)
    keyed = save(J.jump(), tmp_path / "keyed")
    for given, name in ((None, "as-cut"), (0.6, "seen")):
        loop_mod.run_loop(keyed, tmp_path / name, fps=24.0, min_len=None, max_len=None, n_out=None,
                          seam_max=loop_mod.SEAM_RATIO_MAX, name=name, report_path=None, state="jump", cycle_mode="once",
                          air_retime=f"gravity=9.81,air={2 * J.AIR}", meters_per_px=J.METERS_PER_PX,
                          interpolate=Between(J.draw, range(J.FRAMES)), **({} if given is None else {"judge_scale": given}))
    assert asked == [1.0, 0.6]
