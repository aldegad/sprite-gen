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
from PIL import Image, ImageDraw

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
    assert {k: v for k, v in facts.items() if k != "smear"} == {"from": 6, "to": 12, "between": "rife", "taken": 6, "made_by_rife": 6,
                                                                "made_at": [1, 3, 5, 7, 9, 11]}
    assert [m["at"] for m in facts["smear"]] == [1, 3, 5, 7, 9, 11]
    assert all(out[2 * k] is frames[k] for k in range(6)) and rec.calls == [0.5] * 6
    same, facts = align.resample(frames, 6, None)  # already that long: untouched, nothing made
    assert facts["made_by_rife"] == 0 and all(a is b for a, b in zip(same, frames))
    with pytest.raises(ValueError, match="no interpolator"):
        align.resample(frames, 9, None)


def test_between_nearest_takes_the_nearer_source_frame_and_makes_none():
    frames = [_walker(2 * math.pi * k / 8) for k in range(8)]
    out, facts = align.resample(frames, 6, None, between="nearest")  # times 0, 1.33, 2.67, 4, 5.33, 6.67
    assert [frames.index(f) for f in out] == [0, 1, 3, 4, 5, 7]
    assert facts["made_by_rife"] == 0 and facts["nearest_at"] == [1, 2, 4, 5] and "smear" not in facts


def _old_top_line_start(frames: list[Image.Image]) -> int:
    """The 2.24 turn: the frame whose alpha box top is lowest (1-2-1) — what a long ear owns."""
    tops = [f.getchannel("A").point(lambda v: 255 if v >= 128 else 0).getbbox()[1] for f in frames]
    n = len(tops)
    return max(range(n), key=lambda k: tops[(k - 1) % n] + 2 * tops[k] + tops[(k + 1) % n])


def _figure(phase: float, *, view: str, legs: bool = True, bob_px: int = 2, shaded: bool = True) -> Image.Image:
    """A drawn walker at `phase` (radians): its own right heel lands at 0, its left at pi. The body
    bobs lowest (by `bob_px`) a sixth of a step after a heel lands; two thin ears on top flop on their
    own rhythm, lowest half a step after it, and further than the body bobs.

    `side`: faces right, its feet swing apart along the ground; the right leg is the near one, drawn
    light over the far left leg, which is shaded (`shaded`). `front`: the feet stay side by side, its
    right foot on the picture's left, and the foot stepping toward the viewer is drawn lower. With no
    legs the body sits on the ground and squats as it bobs."""
    a = np.zeros((H, W, 4), dtype=np.uint8)
    bob = round(bob_px * (1 + math.cos(2 * phase - math.pi / 3)) / 2)
    flop = round(7 * (1 + math.cos(2 * phase - math.pi)) / 2)
    top = 30 + bob
    a[top:top + 58 if legs else 100, 36:64] = (230, 230, 230, 255)  # head and body; with no legs it squats on the ground
    for x in (40, 56):
        a[top - 26 + flop:top, x:x + 4] = (240, 180, 190, 255)  # an ear: a narrow run
    if legs:
        if view == "side":
            for sign, leg, foot in ((-1, 150, 50), (1, 210, 110)):  # far left leg first, near right leg over it
                if not shaded:
                    leg, foot = 210, 110
                fx = 50 + round(sign * 18 * math.cos(phase))
                a[top + 58:106, fx - 3:fx + 3] = (leg, leg, leg, 255)
                a[100:106, fx - 7:fx + 7] = (foot, foot, foot, 255)
        else:
            for x, sign in ((38, 1), (52, -1)):  # its right foot, then its left
                fy = 105 + round(sign * 3 * math.cos(phase))
                a[top + 58:fy, x + 2:x + 8] = (200, 200, 200, 255)
                a[fy - 10:fy, x:x + 10] = (60, 60, 60, 255)
    return Image.fromarray(a, "RGBA")


def _cycle(**kw) -> list[Image.Image]:
    return [_figure(2 * math.pi * k / 12, **kw) for k in range(12)]


def _mirror(frames: list[Image.Image]) -> list[Image.Image]:
    return [f.transpose(Image.Transpose.FLIP_LEFT_RIGHT) for f in frames]


@pytest.mark.parametrize("view, by", [("side", "stride"), ("front", "reach")])
@pytest.mark.parametrize("bob_px", [2, 0])
def test_the_loop_starts_as_a_heel_lands_not_where_the_ears_are_lowest(view, by, bob_px):
    """With the body bobbing or not at all, the start is read off the legs; the 2.24 rule read the
    top edge, which the ears own."""
    frames = _cycle(view=view, bob_px=bob_px)
    turned = frames[5:] + frames[:5]
    strike = align.foot_strike(turned)
    assert strike["by"] == by
    assert any(turned[strike["start"]] is frames[k] for k in (0, 6))  # a heel lands at phase 0 and pi
    assert any(turned[_old_top_line_start(turned)] is frames[k] for k in (3, 9))  # the ears' low


def test_a_body_with_no_legs_starts_where_its_top_line_is_lowest_past_the_ears():
    frames = _cycle(view="front", legs=False)
    strike = align.foot_strike(frames)
    assert strike["by"] == "body_low" and strike["start"] in (1, 7)  # the body's own low, a sixth of a step on
    assert _old_top_line_start(frames) in (3, 9)  # the ears' low


@pytest.mark.parametrize("view, figure", [("side@right", "side"), ("front", "front"), ("back", "front")])
def test_every_view_starts_as_the_same_own_foot_lands(view, figure):
    """Its right heel lands at frame 0 and its left at 6. Mirrored, the picture is the same walk on
    the other own feet: a side view faces left (its near leg, the light one, is its left), a front
    view puts its right foot on the picture's right, and a back view seen this way round has its
    right foot stepping away where the front view's left steps near."""
    frames = _cycle(view=figure)
    mirrored = _mirror(frames)
    flipped = view.replace("@right", "@left")
    for walk, seen, right_at in ((frames, view, 0), (mirrored, flipped, 6)):
        turned = walk[5:] + walk[:5]
        strike = align.foot_strike(turned, view=seen)
        assert strike["start_foot"] == "right", strike
        assert turned[strike["start"]] is walk[right_at], (seen, strike)
        assert turned[align.foot_strike(turned, view=seen, foot="left")["start"]] is walk[(right_at + 6) % 12]


def test_a_foot_the_view_cannot_tell_is_not_named():
    turned = (lambda f: f[5:] + f[:5])(_cycle(view="side", shaded=False))
    strike = align.foot_strike(turned, view="side@right")
    assert strike["start_foot"] is None and "shade does not follow the step" in strike["foot_why"]
    assert any(turned[strike["start"]] is f for f in (turned[7], turned[1]))  # still on a strike
    unseen = align.foot_strike(turned)
    assert unseen["start_foot"] is None and unseen["foot_why"] == "no view given for this loop"


def _chibi(phase: float, *, view: str, step: float = 22, lift: int = 8, persp: int = 6, apart: int = 14) -> Image.Image:
    """An earless walker with short legs under a big head, at `phase` (radians): its own right heel
    lands at 0, its left at pi. Each leg swings `step` degrees either way about the hip; the stance
    leg stays planted, so the hip rides lowest as a heel lands, and the foot behind lifts by `lift`
    px mid-step. `front`: the feet `apart` px either side of the middle, its right foot on the
    picture's left, the foot stepping toward the viewer drawn up to `persp` px lower. `side`: faces
    right, its near right leg drawn light and `persp` px lower over the shaded far left leg."""
    img = Image.new("RGBA", (300, 360))
    draw = ImageDraw.Draw(img)
    ground, leg, head, a = 340, 42, 50, math.radians(step)
    swing = {"right": a * math.cos(phase), "left": -a * math.cos(phase)}
    stance = "right" if phase % (2 * math.pi) < math.pi else "left"
    hip, mid = ground - leg * math.cos(swing[stance]), 150
    for own in ("left", "right") if view == "side" else ("right", "left"):
        lifted = lift * abs(math.sin(phase)) if own != stance else 0
        if view == "side":
            fx, fy, tone = mid + leg * math.sin(swing[own]), ground - lifted - (persp if own == "left" else 0), 210 if own == "right" else 140
        else:
            fx, fy, tone = mid - apart if own == "right" else mid + apart, ground - lifted + persp * (math.sin(swing[own]) / math.sin(a) - 1), 200
        draw.line([(mid if view == "side" else fx, hip), (fx, fy - 6)], fill=(tone, tone, tone, 255), width=7)
        draw.rectangle([fx - 9, fy - 8, fx + 9, fy], fill=(tone - 60, tone - 60, tone - 60, 255))
    torso = int(leg * 0.9)
    draw.rectangle([mid - 22, hip - torso, mid + 22, hip], fill=(230, 200, 180, 255))
    draw.ellipse([mid - head, hip - torso - 2 * head, mid + head, hip - torso], fill=(240, 210, 190, 255))
    return img


@pytest.mark.parametrize("view, figure, kw, by", [
    ("front", "front", {"lift": 8, "persp": 6}, "reach"),  # its feet lift out of the foot band: the band swings like a side stride
    ("front", "front", {"lift": 12, "persp": 6}, "reach"),
    ("front", "front", {"lift": 4, "persp": 10}, "reach"),
    ("front", "front", {"lift": 20, "persp": 0, "apart": 16}, "body_low"),  # no foot drawn nearer: the band's swing is a lift, not a strike
    ("side@right", "side", {"step": 10, "persp": 6}, "stride"),  # a short step under STRIDE_MIN still opens the feet as a heel lands
    ("side@right", "side", {"step": 14, "persp": 3, "lift": 12}, "stride"),
])
def test_the_view_picks_the_signal_so_a_short_legged_walk_starts_on_a_strike(view, figure, kw, by):
    """A short-legged walk's swings mislead the thresholds: from the front its stride swings past
    STRIDE_MIN as the foot behind lifts out of the band, and from the side a short step swings under
    it. The view says which signal the step is on; the start is on a strike (or one frame after it,
    the heel just landed), and a foot it names is the one landing there."""
    frames = [_chibi(2 * math.pi * k / 12, view=figure, **kw) for k in range(12)]
    turned = frames[5:] + frames[:5]  # its right heel lands at turned[7], its left at turned[1]
    strike = align.foot_strike(turned, view=view)
    assert strike["by"] == by, strike
    landed = {"right": 7, "left": 1}
    near = min(landed, key=lambda f: min((strike["start"] - landed[f]) % 12, (landed[f] - strike["start"]) % 12))
    assert (strike["start"] - landed[near]) % 12 in (0, 1), strike
    assert strike["start_foot"] in (None, near), strike
    if by == "body_low":
        assert strike["foot_why"].startswith("no legs to read"), strike


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


def test_each_loop_records_its_view_and_start_foot_and_a_view_per_loop_is_required(tmp_path):
    dirs = [_cut(tmp_path, n, L) for n, L in (("S", 20), ("E", 24))]
    with pytest.raises(SystemExit, match="1 --view for 2 --loop-dir"):
        align.align_set(dirs, interpolate=Recorder(), views=["front"])
    with pytest.raises(SystemExit, match="facing right|left"):
        align.align_set(dirs, interpolate=Recorder(), views=["front", "side"])
    report = align.align_set(dirs, interpolate=Recorder(), views=["front", "side@right"])
    assert report["start_foot"] == "right" and [r["view"] for r in report["loops"]] == ["front", "side@right"]
    for r in report["loops"]:
        assert r["start_foot"] in ("right", None) and (r["start_foot"] or r["foot_why"])
    unseen = align.align_set(dirs, interpolate=Recorder())
    assert any("no view given" in w for w in unseen["warnings"])


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


def test_video_cycle_align_between_nearest_needs_no_rife(tmp_path, monkeypatch):
    dirs = [_cut(tmp_path, n, L) for n, L in (("S", 20), ("E", 24), ("N", 28))]
    _no_rife(monkeypatch, tmp_path)
    report = align.align_set(dirs, between="nearest")
    assert report["between"] == "nearest" and report["made_by_rife"] == 0 and report["interpolator"] is None
    by = {Path(r["dir"]).name: r for r in report["loops"]}
    assert len(by["S"]["nearest_at"]) > 0 and by["E"]["nearest_at"] == []
    assert all(json.loads((d / "walk.strip.json").read_text())["frames"] == 24 for d in dirs)


def test_a_made_frame_darker_than_both_neighbours_is_named_in_the_warnings(tmp_path):
    def smudge(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        made = np.asarray(Image.blend(a, b, t)).copy()
        made[60:80, 42:58, :3] = 0  # a black blot inside the body
        return Image.fromarray(made, "RGBA")

    dirs = [_cut(tmp_path, n, L) for n, L in (("S", 20), ("E", 24))]
    report = align.align_set(dirs, interpolate=smudge)
    smears = [w for w in report["warnings"] if "more dark pixels inside the body" in w]
    assert len(smears) == report["made_by_rife"] > 0
    row = next(r for r in report["loops"] if Path(r["dir"]).name == "S")
    assert len(row["smear"]) == row["made_by_rife"] and min(m["dark_excess"] for m in row["smear"]) > align.SMEAR_WARN
    assert [m["at"] for m in row["smear"]] == sorted(row["made_at"])


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
