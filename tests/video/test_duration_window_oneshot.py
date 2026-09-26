"""Synthetic regressions for the 3 s default and its consequences (2026-09-18/20 field tests):

* `video-set` asks for 3 s clips by default (4 s for attack);
* a gait's period window is taken in seconds (floor) and half the clip (ceiling), so a
  1.0-1.3 s stride is still inside the window of a 3 s clip; idle and the action
  states keep their clip-length fractions;
* the strip sidecar says how the cycle was cut and whether to loop it, and the spec
  loader reads that back;
* the GIF/WebP never ask for more frames than the strip has cells.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.spec.assets import load_asset
from sprite_gen.video import batch as batch_mod
from sprite_gen.video import loop as loop_mod
from tests.video.test_video_pipeline import HAS_IMG2WEBP, _gait_frames, _one_shot_frames


def test_video_set_duration_defaults_per_state(monkeypatch) -> None:
    parser = argparse.ArgumentParser()
    batch_mod.add_arguments(parser)
    # unset on the command line means "each state's own default", not one number for all
    assert parser.parse_args(["--out-dir", "x"]).duration is None
    assert parser.parse_args(["--out-dir", "x", "--duration", "6"]).duration == 6
    seen: dict = {}

    def fake_run_set(**kw):
        seen.update(kw)
        return {"ok": 0, "failed": [], "items": []}

    monkeypatch.setattr(batch_mod, "run_set", fake_run_set)
    monkeypatch.setattr(batch_mod, "_parse_bases", lambda items: {})
    batch_mod.run(out_dir="x", duration=None)
    assert seen["duration"] is None
    assert batch_mod.DEFAULT_DURATION_SECONDS == 3
    assert batch_mod.duration_for("walk", None) == batch_mod.duration_for("jump", None) == 3
    assert batch_mod.duration_for("attack", None) == 2
    assert batch_mod.duration_for("attack", 6) == batch_mod.duration_for("walk", 6) == 6


def test_gait_windows_are_in_seconds_capped_at_half_the_clip() -> None:
    walk, run, idle, jump = (loop_mod.profile_for(s) for s in ("walk", "run", "idle", "jump"))
    # a 3 s clip at 24 fps: a 1.0-1.3 s stride (24-32 frames) must fit; the old 6-31 %
    # window topped out at 23 frames (measured 2026-09-18: sookuma refused, xazinga half-stepped).
    # The ceiling is 1.6 s (38 frames) but a 73-frame clip can only confirm 36.
    assert walk.window(73, 24.0) == (12, 36)
    assert run.window(73, 24.0) == (7, 29)
    # a 6 s clip: the legless 13-frame bounce stays reachable, the ceiling is the 1.6 s bound
    assert walk.window(145, 24.0) == (12, 38)
    # the bounds are physical, so a different frame rate moves them
    assert walk.window(90, 30.0) == (15, 45)
    # non-gait states are untouched: idle wants nearly the whole clip, jump keeps its fractions
    assert idle.window(73, 24.0) == (round(73 * 0.60), round(73 * 0.95))
    assert jump.window(145, 24.0) == (round(145 * 0.11), round(145 * 0.45))


def test_gait_window_does_not_let_a_single_hop_pass_as_a_walk(tmp_path: Path) -> None:
    """A wider ceiling inflates the periodicity gate's profile mean: with 'half the clip'
    the one-hop stand scored 0.23 (a walk!); with the 1.6 s bound it scores ~0.03."""
    files = _one_shot_frames(tmp_path)
    D = loop_mod.distance_matrix(files)
    lo, hi = loop_mod.profile_for("walk").window(len(files), 24.0)
    assert loop_mod.detect_cycle(D, min_len=lo, max_len=hi, gait_floor=round(0.6 * 24))["periodicity"] < loop_mod.PERIODICITY_MIN


def test_run_loop_reports_the_gait_window_from_the_profile(tmp_path: Path, monkeypatch) -> None:
    _gait_frames(tmp_path, period=12, n=72)
    seen: dict = {}

    def fake_detect(D, *, min_len, max_len, gait_floor=None):
        seen.update(lo=min_len, hi=max_len)
        raise SystemExit("stop here")

    monkeypatch.setattr(loop_mod, "detect_cycle", fake_detect)
    with pytest.raises(SystemExit, match="stop here"):
        loop_mod.run_loop(tmp_path / "keyed", tmp_path / "out", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=2.0, name="w", report_path=None)
    assert (seen["lo"], seen["hi"]) == (12, 36)


def test_strip_sidecar_says_how_the_cycle_was_cut_and_whether_to_loop() -> None:
    frames = [Image.new("RGBA", (40, 40), (0, 0, 0, 0)) for _ in range(6)]
    for im in frames:
        for y in range(10, 38):
            for x in range(14, 26):
                im.putpixel((x, y), (200, 60, 60, 255))
    _, periodic = loop_mod.build_strip(frames, cycle_seconds=0.25)
    _, once = loop_mod.build_strip(frames, cycle_seconds=0.25, kind="one-shot")
    assert (periodic["kind"], periodic["loop"]) == ("periodic", True)
    assert (once["kind"], once["loop"]) == ("one-shot", False)


def test_spec_loader_reads_loop_from_the_strip_sidecar(tmp_path: Path) -> None:
    frames = [Image.new("RGBA", (40, 40), (0, 0, 0, 0)) for _ in range(4)]
    for im in frames:
        for y in range(10, 38):
            for x in range(14, 26):
                im.putpixel((x, y), (200, 60, 60, 255))
    strip, meta = loop_mod.build_strip(frames, cycle_seconds=0.2, kind="one-shot")
    strip.save(tmp_path / "jump.strip.png")
    (tmp_path / "jump.strip.json").write_text(json.dumps(meta))
    assert load_asset(tmp_path / "jump.strip.json").metadata["loop"] is False
    # a sidecar from before the key existed is a loop, as it always was
    meta.pop("loop"); meta.pop("kind")
    (tmp_path / "jump.strip.json").write_text(json.dumps(meta))
    assert load_asset(tmp_path / "jump.strip.json").metadata["loop"] is True


@pytest.mark.skipif(not HAS_IMG2WEBP, reason="img2webp not installed")
def test_one_shot_run_writes_a_non_looping_sidecar(tmp_path: Path) -> None:
    _one_shot_frames(tmp_path)
    rep = loop_mod.run_loop(tmp_path / "keyed", tmp_path / "jump", fps=24.0, state="jump", min_len=None, max_len=None, n_out=8, seam_max=2.0, name="j", report_path=None)
    assert rep["cycle"]["kind"] == "one-shot"
    side = json.loads((tmp_path / "jump" / "j.strip.json").read_text())
    assert side["kind"] == "one-shot" and side["loop"] is False
    assert load_asset(tmp_path / "jump" / "j.strip.json").metadata["loop"] is False


@pytest.mark.skipif(not HAS_IMG2WEBP, reason="img2webp not installed")
def test_gif_frame_count_never_exceeds_the_strip_cells(tmp_path: Path) -> None:
    """A cycle longer than the cell cap is subsampled into the strip; the GIF/WebP are cut
    from those cells, so they hold that many frames — and verification expects that, not
    the cycle length (2026-09-20: a 68-frame jump cycle came back "64 frames, expected 68")."""
    _gait_frames(tmp_path, period=34, n=80, stamp=True)
    rep = loop_mod.run_loop(tmp_path / "keyed", tmp_path / "out", fps=24.0, state="jump", min_len=None, max_len=None, n_out=None, seam_max=99.0, name="long", report_path=None, cycle_mode="fixed", start=0, length=68)
    assert rep["cycle"]["length"] == 68
    assert rep["strip"]["frames"] == loop_mod.STRIP_MAX_CELLS == 64 and rep["strip"]["subsampled"] is True
    assert rep["n_out"] == 64 and rep["gif"]["n_frames"] == 64 and rep["webp"]["n_frames"] == 64
