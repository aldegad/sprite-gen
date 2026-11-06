# SPDX-License-Identifier: Apache-2.0
"""`video-stop`: a run that stops, cut from the clip's own start played backwards — on synthetic runners.

A synthetic clip opens on a standing runner, sets it off (its stride grows over a few frames) and runs it
in place; `video-loop --cycle fixed` cuts a later cycle. The stop must leave the loop where the clip
reaches it, play the start backwards to the clip's first frame without sliding, and refuse a start that
reaches the loop by a jump, holds another motion before it, or runs on longer than a setting-off.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.util.resample import resize_cell
from sprite_gen.video import evidence, loop, stop

SS = 4  # drawn this many times larger and scaled down, so a slow swing moves by sub-pixels
W, H = 240, 260
GROUND = 236
PERIOD = 16
REST = 6
SET_OFF = 8


def runner(*, run: float, phase: float, dx: float = 0.0, dy: float = 0.0, arm_up: float = 0.0, crouch: float = 0.0,
           ghost: bool = False) -> Image.Image:
    """A side-view runner on GROUND: head, torso, two legs of different colours and an arm. `run` takes it from standing
    (0: upright, legs together, arm down) to running (1: leaning in, hips low, legs and arm swinging at `phase`, the body
    bobbing with the stride); `arm_up` raises the arm and `crouch` lowers the body instead. `ghost` lays a part-covered
    copy of the front leg a little behind it, the band a video model leaves between two drawings."""
    big = Image.new("RGBA", (W * SS, H * SS))
    d = ImageDraw.Draw(big)
    amp = run
    cx = 110 + dx
    bob = 5 * amp * abs(math.sin(phase)) + 30 * crouch + 10 * run
    hip = GROUND - 78 + bob + dy
    shoulder = hip - 64
    lean = 14 * run

    def line(p, q, width, fill):
        d.line([tuple(round(v * SS) for v in p), tuple(round(v * SS) for v in q)], fill=fill, width=round(width * SS))

    def foot(sign):
        swing = sign * amp * 34 * math.sin(phase)
        lift = amp * 16 * max(0.0, sign * math.cos(phase))
        return (cx + swing, GROUND + dy - lift - 2)

    if ghost:
        line((cx - 8, hip), (foot(1)[0] - 14, foot(1)[1]), 12, (240, 200, 40, 110))
    line((cx, hip), foot(-1), 12, (30, 60, 90, 255))
    line((cx, hip), foot(1), 12, (240, 200, 40, 255))
    d.polygon([tuple(round(v * SS) for v in p) for p in ((cx - 16 + lean, shoulder), (cx + 16 + lean, shoulder),
                                                          (cx + 16, hip + 4), (cx - 16, hip + 4))], fill=(20, 90, 180, 255))
    d.ellipse(tuple(round(v * SS) for v in (cx - 15 + lean, shoulder - 34, cx + 15 + lean, shoulder - 4)), fill=(230, 180, 140, 255))
    if arm_up:
        hand = (cx + 20, shoulder - 30 - 30 * arm_up)
    else:
        angle = amp * 0.9 * math.sin(phase + math.pi)
        hand = (cx + 52 * math.sin(angle), shoulder + 52 * math.cos(angle))
    line((cx + lean, shoulder + 6), (hand[0] + lean, hand[1]), 9, (200, 40, 40, 255))
    return big.convert("RGBa").resize((W, H), Image.Resampling.BOX).convert("RGBA")


def clip(tmp_path: Path, *, cycles: int = 3, drift: tuple[float, float] = (0.0, 0.0), other: int = 0,
         instant: bool = False, ghost_at: int | None = None, set_off: int = SET_OFF) -> tuple[Path, int]:
    """Keyed frames of a clip: REST standing frames, `other` frames of another motion (arm raised and waved, legs
    crouched, no stride), a setting-off of `set_off` frames from standing (none when `instant`: the stride is there at
    once), then the run, `cycles` cycles of PERIOD frames. `drift` moves the body across the frame while it sets off.
    Returns the folder and the first frame of the run."""
    frames = [runner(run=0.0, phase=0.0) for _ in range(REST)]
    ramp = 0 if instant else set_off
    for k in range(other):
        frames.append(runner(run=0.0, phase=0.0, arm_up=0.5 + 0.5 * abs(math.sin(k * math.pi / 5)), crouch=0.4 + 0.3 * math.sin(k * 0.7)))

    def setting_off(k: int, ghost: bool = False) -> Image.Image:
        t = (k + 1) / (ramp + 1)
        return runner(run=t, phase=2 * math.pi * k / PERIOD, dx=drift[0] * t, dy=drift[1] * t, ghost=ghost)

    frames += [setting_off(k) for k in range(ramp)]
    run_from = len(frames)
    for k in range(cycles * PERIOD):
        frames.append(runner(run=1.0, phase=2 * math.pi * (ramp + k) / PERIOD, dx=drift[0], dy=drift[1]))
    if ghost_at is not None:
        frames[ghost_at] = setting_off(ghost_at - REST - other, ghost=True)
    keyed = tmp_path / "keyed"
    keyed.mkdir()
    for i, im in enumerate(frames):
        im.save(keyed / f"frame-{i + 1:04d}.png")
    return keyed, run_from


def cut(tmp_path: Path, keyed: Path, start: int, *, name: str = "run") -> Path:
    """The loop as video-loop cuts it: one cycle from `start`, as filmed."""
    loop.run_loop(keyed, tmp_path / "loop", fps=24.0, state="run", min_len=None, max_len=None, n_out=None, seam_max=1000.0,
                  name=name, report_path=None, cycle_mode="fixed", start=start, length=PERIOD, anchor="none", repair="off")
    return tmp_path / "loop" / f"{name}.strip.json"


def cells_of(meta_path: Path) -> tuple[dict, list[Image.Image]]:
    meta = json.loads(meta_path.read_text())
    strip = Image.open(meta_path.with_name(meta_path.name[:-5] + ".png")).convert("RGBA")
    return meta, [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]


def hip_x(im: Image.Image) -> float:
    """Where the synthetic runner's body is across: the middle of its torso's lowest rows, over the hip the legs swing from."""
    a = np.asarray(im.convert("RGBA")).astype(int)
    torso = (np.abs(a[..., 0] - 20) < 8) & (np.abs(a[..., 1] - 90) < 8) & (np.abs(a[..., 2] - 180) < 8) & (a[..., 3] == 255)
    ys, xs = np.nonzero(torso)
    low = ys >= ys.max() - 6
    return float(xs[low].mean())


def make(tmp_path: Path, keyed: Path, meta: Path, **kwargs) -> dict:
    return stop.make_stop(keyed, meta, tmp_path / "stop", **kwargs)


def test_stop_plays_the_start_backwards_from_the_loop_to_the_first_frame(tmp_path):
    keyed, run_from = clip(tmp_path)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    out = make(tmp_path, keyed, meta)
    assert out["status"] == "passed"
    shown = out["shown"]
    # It reaches the loop where the run starts, and plays back down through the setting-off to the still.
    assert run_from - 2 <= out["entry"]["frame"] <= run_from
    assert shown == sorted(shown, reverse=True) and shown[-1] == 0
    assert all(j > out["rest"]["end"] for j in shown[:-1]) and out["rest"]["end"] >= REST - 1
    # The cell it leaves is the loop's cell at the entry's phase: the run is cut a cycle on, so the same phase.
    assert out["entry"]["from_cell"] == (out["entry"]["frame"] - (run_from + PERIOD)) % PERIOD
    assert out["seam"]["steps"] <= stop.SEAM_MAX and out["run_up"]["cycles"] <= stop.RUN_UP_MAX_CYCLES
    # The last cell is the clip's first frame — the still as filmed — at the loop's scale, pixel for pixel.
    smeta, cells = cells_of(tmp_path / "stop" / "stop.strip.json")
    lmeta = json.loads(meta.read_text())
    first = Image.open(sorted(keyed.glob("*.png"))[0]).convert("RGBA")
    first = resize_cell(first, (round(first.width * lmeta["scale"]), round(first.height * lmeta["scale"])))
    assert cells[-1].crop(cells[-1].getbbox()).tobytes() == first.crop(first.getbbox()).tobytes()
    # The one-shot contract other single plays keep: plays once and holds the still, sized as its strip.
    assert (smeta["loop"], smeta["hold_last"], smeta["kind"], smeta["end_frame"]) == (False, True, "stop", len(shown) - 1)
    assert smeta["frames"] == len(shown) and smeta["source_cut"]["frames"] == shown
    assert smeta["from_loop"]["from_cell"] == out["entry"]["from_cell"] and smeta["body_h"] == lmeta["body_h"]
    assert smeta["source_cut"]["source"]["sha256"] == lmeta["source_cut"]["source"]["sha256"]
    assert out["source"]["same_as_loop"] is True
    # Read for a picture shown again, as every cut is; none is, so none is made between.
    assert smeta["held_fill"]["mode"] == "auto" and smeta["held_fill"]["made"] == []
    assert (smeta["held_fill"]["cells"], smeta["held_fill"]["poses"], smeta["held_fill"]["held_runs"]) == (len(shown), len(shown), [])
    with Image.open(tmp_path / "stop" / "stop.gif") as gif:
        assert gif.n_frames == len(shown) and gif.info.get("loop") is None
    report = json.loads((tmp_path / "stop" / "stop.stop.report.json").read_text())
    assert report["status"] == "passed" and report["shown"] == shown


@pytest.mark.slow
def test_stop_does_not_slide_when_the_body_drifts_as_it_sets_off(tmp_path):
    """A clip whose body moves 36 px across and 18 px up while it sets off, beside the same clip filmed in place: played
    back as filmed, the first would slide that far into the still. Its stop is the in-place clip's, the drift taken out
    to the pixel: the body stays over the loop's place, the still on the loop's ground."""
    runs = {}
    for label, drift in (("in-place", (0.0, 0.0)), ("drifts", (36.0, -18.0))):
        (tmp_path / label).mkdir()
        keyed, run_from = clip(tmp_path / label, drift=drift)
        meta = cut(tmp_path / label, keyed, run_from + PERIOD)
        out = make(tmp_path / label, keyed, meta)
        smeta, cells = cells_of(tmp_path / label / "stop" / "stop.strip.json")
        lmeta, lcells = cells_of(meta)
        ox, oy = smeta["loop_cell_origin"]
        hips = [hip_x(c) - ox for c in cells]  # in the loop cell's own pixels
        # From the loop's last cell into the stop the body does not jump; the still stands on the loop's ground.
        assert abs(hip_x(lcells[out["entry"]["from_cell"]]) - hips[0]) < 3.0
        assert out["drift"]["ends_on_ground"] is True
        assert stop._box(cells[-1])[3] - 1 == max(stop._box(c)[3] - 1 for c in lcells) + oy
        # What moves the hips is the standing pose laid by its coverage on a run that leans 14 px: a few pixels.
        assert max(hips) - min(hips) < 9.0
        runs[label] = (out, hips)
    (still, still_hips), (drifts, drift_hips) = runs["in-place"], runs["drifts"]
    # The still lands where the in-place clip's does, the whole drift taken out; the frames between take a straight
    # share of it per step, and this drift was not straight over them (none on the last rest frames): a tenth of it left.
    taken = [b - a for a, b in zip(still["drift"]["clip_px"], drifts["drift"]["clip_px"])]
    assert taken == [36, -18] and still_hips[-1] == drift_hips[-1]
    assert max(abs(a - b) for a, b in zip(still_hips, drift_hips)) <= 0.1 * 36 + 0.5


@pytest.mark.parametrize("other", [2, PERIOD // 2])
def test_stop_refuses_a_short_other_motion(tmp_path, other):
    """Another motion between the still and the run (an arm waved high, the body crouched) that ends standing, the run
    setting off from there: two frames of it, or half a cycle, the run-up well inside a setting-off's length. The clip
    comes back to the still it had left, and the stop would play the motion backwards: refused by name, the frame that
    turns back, the pose it is back at and the farthest frame between named in the report, no strip written."""
    keyed, run_from = clip(tmp_path, other=other)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    with pytest.raises(SystemExit, match="another motion"):
        make(tmp_path, keyed, meta)
    report = json.loads((tmp_path / "stop" / "stop.stop.report.json").read_text())
    assert report["status"] == "failed" and report["run_up"]["cycles"] <= stop.RUN_UP_MAX_CYCLES
    turn = report["turn_back"]
    # Back at a standing frame where the motion has ended and the setting-off starts, a step and more after leaving it.
    assert turn["frame"] == REST + other and turn["back_to"] < REST and turn["steps"] <= stop.SAME_POSE
    assert REST <= turn["away_frame"] < REST + other and turn["away_steps"] >= stop.TURN_BACK_AWAY
    assert not (tmp_path / "stop" / "stop.strip.png").exists()


@pytest.mark.slow
def test_stop_refuses_a_start_that_holds_another_motion(tmp_path):
    """Two and a half cycles of another motion between the still and the run: it turns back again and again, and the
    run-up is past the run-up bound too. Refused as another motion, the report written; the run-up bound, which only
    bounds the run-up's length, is not what refuses it: an explicit --run-up-max does not let it through."""
    keyed, run_from = clip(tmp_path, other=2 * PERIOD + PERIOD // 2)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    with pytest.raises(SystemExit, match="another motion"):
        make(tmp_path, keyed, meta)
    report = json.loads((tmp_path / "stop" / "stop.stop.report.json").read_text())
    assert report["status"] == "failed" and report["turn_back"]["steps"] <= stop.SAME_POSE
    assert report["run_up"]["cycles"] > stop.RUN_UP_MAX_CYCLES
    assert not (tmp_path / "stop" / "stop.strip.png").exists()
    with pytest.raises(SystemExit, match="another motion"):
        make(tmp_path, keyed, meta, run_up_max=4.0)


@pytest.mark.slow
def test_stop_refuses_a_run_up_longer_than_a_setting_off(tmp_path):
    """A start whose stride grows over two and a half cycles goes one way into the run — nothing in it turns back — and
    plays strides unlike the loop's for longer than a setting-off: refused by its length, not as another motion, the
    report written; an explicit --run-up-max lets it through, ending on the still."""
    keyed, run_from = clip(tmp_path, set_off=2 * PERIOD + PERIOD // 2)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    with pytest.raises(SystemExit, match="strides unlike the loop's") as refusal:
        make(tmp_path, keyed, meta)
    assert "another motion" not in str(refusal.value)
    report = json.loads((tmp_path / "stop" / "stop.stop.report.json").read_text())
    assert report["status"] == "failed" and report["run_up"]["cycles"] > stop.RUN_UP_MAX_CYCLES
    assert report["turn_back"]["steps"] > stop.SAME_POSE
    out = make(tmp_path, keyed, meta, run_up_max=4.0)
    assert out["status"] == "passed" and out["run_up_max_cycles"] == 4.0 and out["shown"][-1] == 0


def test_stop_refuses_a_seam_past_the_jump_line(tmp_path):
    """A clip that runs at once from the still reaches the loop by a jump: the stop would leave the run for the
    standing pose in one frame."""
    keyed, run_from = clip(tmp_path, instant=True)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    with pytest.raises(SystemExit, match="jump line"):
        make(tmp_path, keyed, meta)
    report = json.loads((tmp_path / "stop" / "stop.stop.report.json").read_text())
    assert report["status"] == "failed" and report["seam"]["steps"] > stop.SEAM_MAX


@pytest.mark.slow
def test_stop_leaves_an_aligned_loop_from_the_cell_nearest_the_entry(tmp_path):
    """A loop turned to start elsewhere (as video-cycle-align turns one): the cell left from follows the pose, not the index."""
    keyed, run_from = clip(tmp_path)
    meta_path = cut(tmp_path, keyed, run_from + PERIOD)
    meta, cells = cells_of(meta_path)
    turn = 5
    turned = cells[turn:] + cells[:turn]
    strip = Image.new("RGBA", (meta["w"] * len(turned), meta["h"]))
    for k, c in enumerate(turned):
        strip.paste(c, (k * meta["w"], 0))
    aligned = tmp_path / "loop" / "run.aligned.strip.json"
    strip.save(aligned.with_name("run.aligned.strip.png"))
    aligned.write_text(json.dumps(meta))
    plain = make(tmp_path, keyed, meta_path, name="plain")
    turned_out = make(tmp_path, keyed, aligned, name="turned")
    assert turned_out["entry"]["frame"] == plain["entry"]["frame"]
    assert turned_out["entry"]["from_cell"] == (plain["entry"]["from_cell"] - turn) % PERIOD
    assert turned_out["shown"] == plain["shown"]


def test_stop_speed_and_frame_cap(tmp_path):
    keyed, run_from = clip(tmp_path)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    once = make(tmp_path, keyed, meta, name="once")
    twice = make(tmp_path, keyed, meta, name="twice", speed=2)
    played = once["shown"]
    assert twice["shown"] == played[::2] + ([] if played[::2][-1] == 0 else [0])
    capped = make(tmp_path, keyed, meta, name="capped", max_frames=4)
    assert len(capped["shown"]) == 4 and capped["shown"][0] == played[0] and capped["shown"][-1] == 0
    assert capped["strip"]["frames"] == 4 and capped["strip"]["delay_ms"] == once["strip"]["delay_ms"]
    with pytest.raises(SystemExit, match="--speed"):
        make(tmp_path, keyed, meta, name="x", speed=3)
    with pytest.raises(SystemExit, match="--max-frames"):
        make(tmp_path, keyed, meta, name="x", max_frames=1)
    # An explicit bound is the caller's, never replaced by the default: 0 is refused, not read as 1.4.
    with pytest.raises(SystemExit, match="--seam-max"):
        stop.run(frames_dir=keyed, loop=meta, out_dir=tmp_path / "x", seam_max=0.0)


def test_stop_names_a_filmed_ghost_and_keeps_it(tmp_path):
    keyed, run_from = clip(tmp_path, ghost_at=REST + SET_OFF - 3)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    out = make(tmp_path, keyed, meta)
    assert out["status"] == "passed" and out["ghost_screen"]["reads"] is True
    named = [g["clip_frame"] for g in out["ghost_screen"]["ghosts"]]
    assert named == [REST + SET_OFF - 3]
    assert REST + SET_OFF - 3 in out["shown"]


def test_stop_refuses_a_loop_it_cannot_leave(tmp_path):
    keyed, run_from = clip(tmp_path, cycles=2)
    meta_path = cut(tmp_path, keyed, run_from + PERIOD)
    meta = json.loads(meta_path.read_text())
    for change, match in (({"state": "idle"}, "walk or run"), ({"loop": False}, "plays once"), ({"source_cut": None}, "source cut")):
        bad = tmp_path / "loop" / "bad.strip.json"
        bad.with_name("bad.strip.png").write_bytes(meta_path.with_name("run.strip.png").read_bytes())
        bad.write_text(json.dumps({**meta, **change}))
        with pytest.raises(SystemExit, match=match):
            make(tmp_path, keyed, bad, name="bad")
    few = tmp_path / "few"
    few.mkdir()
    for f in sorted(keyed.glob("*.png"))[:run_from]:
        (few / f.name).write_bytes(f.read_bytes())
    with pytest.raises(SystemExit, match="holds"):
        make(tmp_path, few, meta_path, name="few")


def test_stop_source_identity_is_the_loop_cut_from(tmp_path):
    keyed, run_from = clip(tmp_path, cycles=2)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    files = sorted(keyed.glob("*.png"))
    assert json.loads(meta.read_text())["source_cut"]["source"] == evidence.source_identity(files, 24.0)


# --- Held cells (docs/loop-repair.md section 7): a clip that stalls in its setting-off ---------------------------------

# Pinned on the engine before the stop filled its held cells (4f5ccd5), as pictures (tests/picture_digest.py): the stop
# of `clip()` cut a cycle on, and of the same clip whose frame STALL + 1 shows frame STALL again (cells 3 and 4 one
# picture), as that engine played them.
STALL = REST + 3
BEFORE = {
    "plain": {"strip": "5dfc0343acb98991481aa8e9036ebc8ddbeafb61cd4d4011f5cbdbaa0bae4ee7",
              "gif": "7859e66d30ace8837f5da19317182e9a465c3f6f22162e7881a3d305ccb4e3b6",
              "webp": "93aec374211792679b1dcdde4fba446005a92e771596466feb12e08e2acd0137"},
    "held": {"strip": "756c3d1c33194ad27605f610d9b38c3bd95476daa32f97a0760c7ffaf0a4843a",
             "gif": "8517e8fdeaa02a7c45acdfae38b17e1c43912968e1477cd2b23964796f905e08",
             "webp": "8f1d51cf173f2e3ff20d117751b4189faf5c01b8b76d5e164f721a1d43e878df"},
}


def clip_at(i: float) -> Image.Image:
    """`clip()`'s frame `i` (its defaults), at any time: standing, setting off, then running — what an interpolator
    that follows the motion would draw between two of its frames."""
    if i < REST:
        return runner(run=0.0, phase=0.0)
    k = i - REST
    if k < SET_OFF:
        return runner(run=(k + 1) / (SET_OFF + 1), phase=2 * math.pi * k / PERIOD)
    return runner(run=1.0, phase=2 * math.pi * k / PERIOD)


def stalled(tmp_path: Path) -> tuple[Path, int]:
    """`clip()` with frame STALL shown twice: frame STALL + 1 is it again, byte for byte (a video model that stalls)."""
    keyed, run_from = clip(tmp_path)
    files = sorted(keyed.glob("*.png"))
    files[STALL + 1].write_bytes(files[STALL].read_bytes())
    return keyed, run_from


def pictures(out: Path, name: str = "stop") -> dict[str, str]:
    from tests.picture_digest import picture_digest
    return {k: picture_digest([out / f"{name}.{k}" if k != "strip" else out / f"{name}.strip.png"]) for k in ("strip", "gif", "webp")}


def no_rife(monkeypatch, tmp_path: Path) -> None:
    """No RIFE anywhere the engine looks: no SPRITE_GEN_RIFE, no directory on PATH holding one, an empty data directory."""
    import os
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not (Path(d) / "rife-ncnn-vulkan").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(path))
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))


def test_a_stop_whose_clip_stalls_has_its_held_cell_made_between_the_poses_beside_it(tmp_path, capsys):
    from sprite_gen.video import held_fill
    from tests.video.held_fill_inputs import Between

    keyed, run_from = stalled(tmp_path)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    between = Between(clip_at, range(len(sorted(keyed.glob("*.png")))))
    out = make(tmp_path, keyed, meta, interpolate=between)
    shown = out["shown"]
    assert shown[3:5] == [STALL + 1, STALL]  # played backwards: the stalled pair, one picture
    held = out["held_fill"]
    # Before: cells 3 and 4 one picture; the second made again between the first and the pose after it, at its time.
    assert held["before"]["held_runs"] == [{"start": 3, "length": 2, "filled": True, "made": [4]}]
    assert held["made"] == [4] and between.calls == [(STALL, STALL - 1, 0.5)]
    assert (held["before"]["poses"], held["before"]["cells"]) == (len(shown) - 1, len(shown))
    # Delivered: no cell shows the picture of the cell before it.
    smeta, cells = cells_of(tmp_path / "stop" / "stop.strip.json")
    assert held_fill.same_steps(cells) == [False] * (len(cells) - 1)
    assert (smeta["held_fill"]["poses"], smeta["held_fill"]["cells"], smeta["held_fill"]["held_runs"]) == (len(shown), len(shown), [])
    assert smeta["holds"] == []  # no hold the motion means is left
    assert smeta["held_fill"]["made"] == [4] and smeta["frames"] == len(shown)
    # The closing still is the clip's first frame as filmed, held: the stop ends as it did.
    lmeta = json.loads(meta.read_text())
    first = Image.open(sorted(keyed.glob("*.png"))[0]).convert("RGBA")
    first = resize_cell(first, (round(first.width * lmeta["scale"]), round(first.height * lmeta["scale"])))
    assert cells[-1].crop(cells[-1].getbbox()).tobytes() == first.crop(first.getbbox()).tobytes()
    assert (smeta["hold_last"], smeta["end_frame"]) == (True, len(shown) - 1)
    assert "video-stop: warning" not in capsys.readouterr().err
    # The pictures are no longer the engine before's (which played cell 4 as cell 3 again).
    assert pictures(tmp_path / "stop") != BEFORE["held"]


def test_a_stop_with_no_held_cell_is_the_pictures_it_was_and_asks_for_no_frame(tmp_path):
    def never(a, b, t):
        raise AssertionError("a stop with no held cell asks for no frame between")

    keyed, run_from = clip(tmp_path)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    out = make(tmp_path, keyed, meta, interpolate=never)
    assert out["held_fill"]["made"] == [] and out["held_fill"]["before"]["held_runs"] == []
    assert pictures(tmp_path / "stop") == BEFORE["plain"]


def test_fill_held_off_plays_the_stalled_stop_as_before_and_names_the_run(tmp_path, capsys):
    keyed, run_from = stalled(tmp_path)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    out = make(tmp_path, keyed, meta, fill_held="off", interpolate=lambda a, b, t: pytest.fail("off makes no frame"))
    assert pictures(tmp_path / "stop") == BEFORE["held"]
    held = out["held_fill"]
    assert held["mode"] == "off" and held["made"] == [] and held["held_runs"] == [{"start": 3, "length": 2}]
    assert held["before"]["held_runs"][0]["why"] == "--fill-held off"
    err = capsys.readouterr().err
    assert "video-stop: warning: cells 3-4 show one picture (7/8 poses/cells)" in err and "--fill-held off" in err
    with pytest.raises(SystemExit):
        stop.main(["--frames-dir", str(keyed), "--loop", str(meta), "--out-dir", str(tmp_path / "x"), "--fill-held", "on"])


def test_a_frame_between_the_judge_refuses_keeps_the_stop_s_held_cells_as_filmed_and_says_so(tmp_path, capsys):
    keyed, run_from = stalled(tmp_path)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    out = make(tmp_path, keyed, meta, interpolate=lambda a, b, t: a.copy())  # gives the pose back: a held cell again
    run = out["held_fill"]["before"]["held_runs"][0]
    assert run["filled"] is False and "held" in run["faults"] and out["held_fill"]["made"] == []
    assert pictures(tmp_path / "stop") == BEFORE["held"]
    assert "video-stop: warning: held cells 3-4 kept as filmed: a frame between was refused (held" in capsys.readouterr().err


def test_no_rife_plays_the_stop_s_held_cells_as_filmed_and_says_how_to_install_it(tmp_path, monkeypatch, capsys):
    no_rife(monkeypatch, tmp_path)
    keyed, run_from = stalled(tmp_path)
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    out = make(tmp_path, keyed, meta)
    assert out["status"] == "passed" and out["held_fill"]["made"] == []
    assert out["held_fill"]["unmade"]["cells"] == [4] and out["held_fill"]["unmade"]["why"] == "RIFE is not installed"
    smeta = json.loads((tmp_path / "stop" / "stop.strip.json").read_text())
    assert smeta["held_fill"]["unmade"]["cells"] == [4] and "install" in smeta["held_fill"]["unmade"]
    assert pictures(tmp_path / "stop") == BEFORE["held"]
    err = capsys.readouterr().err
    assert "video-stop: warning: 1 held cell (4) shown as filmed — RIFE is not installed" in err and "rife install" in err


def test_a_stop_s_opening_pose_shown_twice_is_kept_as_filmed_and_listed_as_a_hold_meant(tmp_path, capsys):
    keyed, run_from = clip(tmp_path)
    files = sorted(keyed.glob("*.png"))
    files[12].write_bytes(files[13].read_bytes())  # the stop's first two frames (played backwards: 13, 12) one picture
    meta = cut(tmp_path, keyed, run_from + PERIOD)
    out = make(tmp_path, keyed, meta, interpolate=lambda a, b, t: pytest.fail("the opening pose is kept: no frame between"))
    assert out["shown"][:2] == [13, 12]
    run = out["held_fill"]["before"]["held_runs"][0]
    assert (run["start"], run["length"], run["filled"], run["keep"]) == (0, 2, False, "opening")
    smeta = json.loads((tmp_path / "stop" / "stop.strip.json").read_text())
    assert smeta["holds"] == [{"from": 0, "to": 1, "kind": "opening", "reason": "the cut's opening pose, kept as filmed"}]
    assert "video-stop: warning" not in capsys.readouterr().err
