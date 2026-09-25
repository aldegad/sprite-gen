# SPDX-License-Identifier: Apache-2.0
"""Idle holds its feet and is pinned: the clip ends on its first frame and `video-loop --cycle
pinned` keeps the whole clip as the loop, gated on that pin rather than on the seam ratio."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import batch as batch_mod
from sprite_gen.video import loop as loop_mod


def test_the_idle_prompt_holds_the_feet_and_asks_for_the_return_not_a_rhythm() -> None:
    prompt = batch_mod.build_prompt("side", "idle", None)
    assert "both feet planted flat on the ground" in prompt
    assert "no walking, no marching in place" in prompt
    assert "weight sway" not in prompt
    assert "evenly paced" not in prompt
    assert prompt.endswith("The last frame returns to the exact pose of the first frame, so the animation loops seamlessly.")
    # the rest of the frame rules are the ordinary ones, not the attack's
    assert "anything it holds" not in prompt
    assert prompt.replace(batch_mod.MOTION_TEXT["idle"], "{m}") == batch_mod.PINNED_LOOP_TEXT.format(
        motion="{m}", view=batch_mod.VIEW_TEXT["side"].format(facing="right"))


def test_idle_is_pinned_and_its_whole_clip_is_the_loop() -> None:
    assert "idle" in batch_mod.PIN_LAST_FRAME_STATES
    assert batch_mod.PINNED_LOOP_STATES == frozenset({"idle"})
    # an attack is pinned too, but cut as a one-shot with its own template
    assert "attack" not in batch_mod.PINNED_LOOP_STATES
    assert "Crisp, clean frames" in batch_mod.build_prompt("side", "attack", None)
    # the other states keep the evenly paced loop
    assert "evenly paced" in batch_mod.build_prompt("side", "walk", None)


def _still_frames(tmp_path: Path, *, n: int = 48, breath: int = 1, end_offset: int = 0) -> Path:
    """A standing body that breathes by `breath` px; the last frame is the first moved by `end_offset`."""
    d = tmp_path / "keyed"
    d.mkdir()
    for t in range(n):
        im = Image.new("RGBA", (64, 96), (0, 0, 0, 0))
        lift = round(breath * (1 - np.cos(2 * np.pi * t / (n - 1))) / 2)
        dx = end_offset if t == n - 1 else 0
        for y in range(20 - lift, 90):
            for x in range(24 + dx, 40 + dx):
                im.putpixel((x, y), (180, 60, 60, 255))
        # a real clip never repeats a frame byte for byte; GIF writers merge frames that do
        im.putpixel((26 + t % 12, 40 + t // 12), (90, 200, 90, 255))
        im.save(d / f"frame-{t:04d}.png")
    return d


def _loop(tmp_path: Path, keyed: Path, name: str = "idle") -> dict:
    return loop_mod.run_loop(keyed, tmp_path / "out", fps=24.0, state="idle", min_len=None, max_len=None, n_out=None,
                             seam_max=loop_mod.SEAM_RATIO_MAX, name=name, report_path=None, cycle_mode="pinned")


def test_a_pinned_still_clip_is_one_whole_loop(tmp_path: Path) -> None:
    rep = _loop(tmp_path, _still_frames(tmp_path))
    cycle = rep["cycle"]
    assert (cycle["kind"], cycle["start"], cycle["length"]) == ("pinned", 0, 47)  # every frame but the re-rendered first
    assert cycle["pin_error"] <= cycle["pin_tolerance"]
    assert rep["strip"]["kind"] == "pinned"


def test_a_near_still_clip_is_not_refused_for_its_seam_ratio(tmp_path: Path) -> None:
    """Breathing moves almost nothing per frame, so the ratio of the wrap to one step can be large
    while the wrap itself is invisible; the pin decides, with a re-render noise floor."""
    frames = _still_frames(tmp_path, breath=1)
    D = loop_mod.distance_matrix(sorted(frames.glob("*.png")))
    cycle = loop_mod.pinned_cycle(D, seam_max=loop_mod.SEAM_RATIO_MAX)
    assert cycle["pin_tolerance"] == max(loop_mod.SEAM_RATIO_MAX * cycle["inner_mean_adjacent"], loop_mod.PIN_NOISE_MAX)
    assert cycle["pin_error"] <= cycle["pin_tolerance"]


def test_a_clip_that_does_not_come_back_to_its_first_frame_is_refused(tmp_path: Path) -> None:
    keyed = _still_frames(tmp_path, end_offset=10)
    with pytest.raises(SystemExit, match="the pinned clip does not end on its first frame"):
        _loop(tmp_path, keyed)
    report = json.loads((tmp_path / "out" / "idle.loop.report.json").read_text())
    assert report["status"] == "failed" and report["cycle"]["kind"] == "pinned"


def test_the_cli_takes_pinned() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    loop_mod.add_arguments(parser)
    assert parser.parse_args(["--frames-dir", "k", "--out-dir", "o", "--fps", "24", "--cycle", "pinned"]).cycle == "pinned"


def test_video_set_pins_idle_and_cuts_it_whole(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_canvas(base, out, *, state, shape, facing, headroom, lead, report_path, fit="state"):
        Image.new("RGB", (32, 32), (0, 255, 0)).save(out)
        return {"fit": fit, "shape": "square", "canvas": [32, 32], "offset": [0, 0]}

    def fake_frames(clip, out_dir, **kw):
        keyed = Path(out_dir) / "keyed"
        keyed.mkdir(parents=True, exist_ok=True)
        return {"fps": 24.0, "frames": 48, "alpha_zero_pct_min": 0.0, "alpha_zero_pct_max": 0.0, "keyed_dir": str(keyed), "spill": {"mode": "small"}}

    def fake_loop(frames_dir, out_dir, **kw):
        seen["cycle_mode"] = kw.get("cycle_mode")
        return {"cycle": {"kind": "pinned", "length": 47, "period_global": None, "ratio": 1.0}, "resampled_seam_ratio": 1.0, "n_out": 47,
                "gif": {"file": "g"}, "webp": {"file": "w"}, "strip": {"path": "s", "drift_px": 0}}

    def video(image, prompt, out, report, **kw):
        seen["last_frame"] = kw.get("last_frame")
        seen["prompt"] = prompt
        Path(out).write_bytes(b"x")
        Path(report).write_text("{}")
        Path(kw["log"]).write_text("")
        return 0

    monkeypatch.setattr(batch_mod.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("right", {}))
    monkeypatch.setattr(batch_mod.canvas_mod, "run_canvas", fake_canvas)
    monkeypatch.setattr(batch_mod.frames_mod, "run_frames", fake_frames)
    monkeypatch.setattr(batch_mod.loop_mod, "run_loop", fake_loop)
    base = tmp_path / "base.png"
    Image.new("RGB", (32, 32), (0, 255, 0)).save(base)
    payload = batch_mod.run_set(bases={"side": base}, states=["idle"], root=tmp_path / "set", character=None, duration=None,
                                resolution="480p", key="green", concurrency=1, force=False, gap=0.0, video_runner=video)
    assert payload["ok"] == 1
    assert seen["cycle_mode"] == "pinned"
    assert Path(str(seen["last_frame"])).name == "canvas.png"
    assert seen["prompt"] == batch_mod.build_prompt("side", "idle", None)
