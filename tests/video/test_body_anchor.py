"""`--anchor body`: one wrap measurement, spread as a ramp.

A gait clip drifts a few pixels over one cycle and the last-to-first wrap shows it
as a sideways jump. The body anchor measures the last frame's head-and-torso offset
against the first once and shifts frame k by that offset times k/L. It never fits
frame by frame: that turns a head bob into a full-body shiver.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import loop


def _walker(x: int, leg: int, w: int = 160, h: int = 160) -> Image.Image:
    """A body (head + torso) at column x with one leg whose x differs per frame."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = im.load()
    for yy in range(20, 40):  # head
        for xx in range(x + 10, x + 30):
            px[xx, yy] = (200, 120, 60, 255)
    for yy in range(40, 110):  # torso
        for xx in range(x, x + 40):
            px[xx, yy] = (60, 90, 200, 255)
    for yy in range(110, 150):  # leg, swinging
        for xx in range(x + leg, x + leg + 10):
            px[xx, yy] = (30, 30, 30, 255)
    return im


def _cycle(drift: int, n: int = 12) -> list[Image.Image]:
    return [_walker(40 + round(drift * k / n), leg=(k * 3) % 30) for k in range(n)]


def test_wrap_offset_reads_the_body_not_the_leg():
    frames = _cycle(drift=6)
    # the leg alone moved 33 px between first and last; the body moved 5-6
    assert loop.body_wrap_offset(frames) in (-6, -5)


def test_ramp_spreads_the_offset_and_closes_the_wrap():
    frames = _cycle(drift=6)
    dx = loop.body_wrap_offset(frames)
    ramped = loop.ramp_frames(frames, dx)
    assert ramped[0] is frames[0]
    # after the ramp the last frame's body sits over the first's
    assert abs(loop.body_wrap_offset(ramped)) <= 1


def test_zero_offset_leaves_frames_untouched():
    frames = _cycle(drift=0)
    assert loop.body_wrap_offset(frames) == 0
    assert loop.ramp_frames(frames, 0) is frames


def test_run_loop_defaults_to_body_for_gaits_and_none_otherwise(tmp_path: Path):
    keyed = tmp_path / "keyed"
    keyed.mkdir()
    for k, im in enumerate(_cycle(drift=6, n=24) * 3):
        im.save(keyed / f"frame-{k + 1:04d}.png")
    walk = loop.run_loop(keyed, tmp_path / "walk", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0, name="w", report_path=tmp_path / "w.json", cycle_mode="fixed", start=0, length=24, repair="off")
    assert walk["strip"]["foot_anchor"] == "body" and walk["strip"]["wrap_dx_px"] in (-6, -5)
    idle = loop.run_loop(keyed, tmp_path / "idle", fps=24.0, state="idle", min_len=None, max_len=None, n_out=None, seam_max=1000.0, name="i", report_path=tmp_path / "i.json", cycle_mode="fixed", start=0, length=24, repair="off")
    assert idle["strip"]["foot_anchor"] == "none" and "wrap_dx_px" not in idle["strip"]
    feet = loop.run_loop(keyed, tmp_path / "feet", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=1000.0, name="f", report_path=tmp_path / "f.json", cycle_mode="fixed", start=0, length=24, anchor="feet", repair="off")
    assert feet["strip"]["foot_anchor"] == "feet" and "wrap_dx_px" not in feet["strip"]


def test_unknown_anchor_is_refused():
    with pytest.raises(SystemExit):
        loop.run_loop(Path("/nonexistent"), Path("/tmp/x"), fps=24.0, state="walk", min_len=None, max_len=None, n_out=None, seam_max=2.0, name="x", report_path=None, anchor="head")
