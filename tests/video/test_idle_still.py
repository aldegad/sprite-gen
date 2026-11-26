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
    """Breathing moves almost nothing per frame, so the ratio of the wrap to one step can be large
    while the wrap itself is invisible; the pin decides, with a re-render noise floor."""
    rep = _loop(tmp_path, _still_frames(tmp_path))
    cycle = rep["cycle"]
    assert (cycle["kind"], cycle["start"], cycle["length"]) == ("pinned", 0, 47)  # every frame but the re-rendered first
    assert cycle["pin_tolerance"] == max(loop_mod.SEAM_RATIO_MAX * cycle["inner_mean_adjacent"], loop_mod.PIN_NOISE_MAX)
    assert cycle["pin_error"] <= cycle["pin_tolerance"]
    assert rep["strip"]["kind"] == "pinned"


def test_a_clip_that_does_not_come_back_to_its_first_frame_is_refused(tmp_path: Path) -> None:
    keyed = _still_frames(tmp_path, end_offset=10)
    with pytest.raises(SystemExit, match="the pinned clip does not end on its first frame"):
        _loop(tmp_path, keyed)
    report = json.loads((tmp_path / "out" / "idle.loop.report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["cycle"]["kind"] == "pinned"


def test_the_cli_takes_pinned() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    loop_mod.add_arguments(parser)
    assert parser.parse_args(["--frames-dir", "k", "--out-dir", "o", "--fps", "24", "--cycle", "pinned"]).cycle == "pinned"


def test_video_set_pins_idle_and_cuts_it_whole(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_canvas(base, out, *, state, shape, facing, headroom, lead, report_path, fit="state", view=None):
        Image.new("RGB", (32, 32), (0, 255, 0)).save(out)
        return {"fit": fit, "shape": "square", "canvas": [32, 32], "offset": [0, 0]}

    def fake_frames(clip, out_dir, **kw):
        keyed = Path(out_dir) / "keyed"
        keyed.mkdir(parents=True, exist_ok=True)
        return {"fps": 24.0, "frames": 48, "alpha_zero_pct_min": 0.0, "alpha_zero_pct_max": 0.0, "keyed_dir": str(keyed), "spill": {"mode": "small"}}

    def fake_loop(frames_dir, out_dir, **kw):
        seen["cycle_mode"] = kw.get("cycle_mode")
        return {"cycle": {"kind": "pinned", "length": 47, "period_global": None, "ratio": 1.0}, "resampled_seam_ratio": 1.0, "held_fill": {"cells": 10, "poses": 10, "held_runs": [], "made": [], "before": {"cells": 10, "poses": 10, "held_runs": []}}, "n_out": 47,
                "gif": {"file": "g"}, "webp": {"file": "w"}, "strip": {"path": "s", "drift_px": 0}}

    def video(image, prompt, out, report, **kw):
        seen["last_frame"] = kw.get("last_frame")
        seen["prompt"] = prompt
        Path(out).write_bytes(b"x")
        Path(report).write_text("{}", encoding="utf-8")
        Path(kw["log"]).write_text("", encoding="utf-8")
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
