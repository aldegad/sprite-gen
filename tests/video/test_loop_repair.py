# SPDX-License-Identifier: Apache-2.0
"""Jump-frame repair (docs/loop-repair.md section 2): only the frame after a jump is replaced,
by the interpolator's frame between its two neighbours; at most three, never two side by side;
the hair watched is behind the body whichever way it faces; and `video-loop` records what it
replaced, gates the cells as they now play, and refuses — naming `--repair off` — when a frame
needs making and no RIFE is installed.

The interpolator is a stand-in that returns the true in-between of the synthetic loop, so the
tests need no binary and can say exactly which frame should come back."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import loop as loop_mod
from sprite_gen.video import repair

W, H, N = 120, 140, 24


def _frame(phase: float, *, tail_dx: int = 0, tail_side: str = "left", tail_jump: int = 0) -> Image.Image:
    """A body (static block), a foot going round with the phase, and a 'ponytail' behind it."""
    a = np.zeros((H, W, 4), dtype=np.uint8)
    a[30:100, 50:70] = (90, 90, 200, 255)  # torso
    a[10:30, 48:72] = (230, 190, 160, 255)  # head
    # a foot going round at an even speed, so every ordinary step changes about as much
    fx, fy = 60 + round(22 * math.cos(phase)), 121 + round(10 * math.sin(phase))
    a[fy - 6:fy + 6, fx - 6:fx + 6] = (60, 60, 60, 255)
    tx = (14 if tail_side == "left" else W - 30) + tail_dx + tail_jump
    a[45:95, tx:tx + 16] = (200, 40, 40, 255)  # ponytail, in the hair box behind a right-facing body
    return Image.fromarray(a, "RGBA")


def _sway(k: int) -> int:
    """The true ponytail sway: one pixel a frame, there and back over the cycle."""
    return min(k % N, N - k % N)


def _loop(jumps: dict[int, int] | None = None, *, side: str = "left") -> tuple[list[Image.Image], list[Image.Image]]:
    """(frames as filmed, the true loop). The true ponytail sways a little; a jump
    frame puts it `jumps[k]` px off its true place."""
    jumps = jumps or {}
    truth = [_frame(2 * math.pi * k / N, tail_dx=_sway(k), tail_side=side) for k in range(N)]
    filmed = [_frame(2 * math.pi * k / N, tail_dx=_sway(k), tail_side=side,
                     tail_jump=jumps.get(k, 0)) for k in range(N)]
    return filmed, truth


class TrueMiddle:
    """Stand-in interpolator: the true frame between two frames of the synthetic loop."""

    def __init__(self, filmed: list[Image.Image], truth: list[Image.Image]):
        self.index = {id(f): k for k, f in enumerate(filmed)}
        self.truth = truth
        self.calls: list[tuple[int, int, float]] = []

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        ka, kb = self.index[id(a)], self.index[id(b)]
        self.calls.append((ka, kb, t))
        assert (kb - ka) % N == 2 and t == 0.5  # always the two neighbours, half way
        return self.truth[(ka + 1) % N]


def test_only_the_frame_after_a_jump_is_replaced_by_its_neighbours_middle():
    filmed, truth = _loop({10: 12})
    fake = TrueMiddle(filmed, truth)
    out, record = repair.repair_jumps(filmed, fake)
    assert record["replaced"] == [10]
    assert fake.calls == [(9, 11, 0.5)]
    assert out[10] is truth[10]
    assert all(out[k] is filmed[k] for k in range(N) if k != 10)  # every other frame stays the video's own
    assert record["score_max_before"] >= repair.JUMP_RATIO > record["score_max_after"]
    assert record["stopped"].startswith("no step at or above")


def test_a_smooth_loop_is_left_alone_and_needs_no_interpolator():
    filmed, _ = _loop()
    out, record = repair.repair_jumps(filmed, None)
    assert record["replaced"] == [] and all(a is b for a, b in zip(out, filmed))


def test_at_most_three_and_never_next_to_a_made_frame():
    filmed, truth = _loop({3: 14, 9: 13, 15: 12, 20: 11})
    out, record = repair.repair_jumps(filmed, TrueMiddle(filmed, truth))
    assert len(record["replaced"]) == 3 and record["stopped"] == "3 frames replaced"
    assert record["replaced"][0] == 3 and set(record["replaced"]) < {3, 9, 15, 20}  # worst first, only stray frames

    filmed, truth = _loop({8: 14, 9: 14})  # two jump frames side by side
    out, record = repair.repair_jumps(filmed, TrueMiddle(filmed, truth))
    assert len(record["replaced"]) == 1
    assert "next to a frame already made" in record["stopped"]


def test_the_hair_watched_is_behind_the_body_whichever_way_it_faces():
    # A small ponytail jump: the whole frame barely notices, the hair box does.
    right_filmed, right_truth = _loop({12: 6}, side="left")  # tail on the left = behind a right-facing body
    assert repair.jump_scores(right_filmed, facing="right")["hair"].max() >= repair.JUMP_RATIO
    assert repair.jump_scores(right_filmed, facing="left")["hair"].max() < repair.JUMP_RATIO
    left_filmed, _ = _loop({12: 6}, side="right")  # tail on the right = behind a left-facing body
    assert repair.jump_scores(left_filmed, facing="left")["hair"].max() >= repair.JUMP_RATIO
    assert repair.hair_box("left") == (0.55, 0.30, 1.0, 0.80)


def _keyed(tmp_path: Path, frames: list[Image.Image]) -> Path:
    d = tmp_path / "keyed"
    d.mkdir()
    for k, f in enumerate(frames + frames[:6]):  # a little more than one cycle, as a clip is
        f.save(d / f"{k:04d}.png")
    return d


def _run(tmp_path: Path, keyed: Path, **kw):
    return loop_mod.run_loop(keyed, tmp_path / "out", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                             seam_max=1000.0, name="w", report_path=tmp_path / "w.json", cycle_mode="fixed",
                             start=0, length=N, anchor="none", **kw)


def test_video_loop_records_the_repair_and_plays_the_made_frame(tmp_path):
    filmed, truth = _loop({10: 12})
    keyed = _keyed(tmp_path, filmed)
    reopened = [Image.open(p).convert("RGBA") for p in sorted(keyed.glob("*.png"))][:N]
    made = []

    def middle(a, b, t):
        made.append(t)
        return truth[10]

    rep = _run(tmp_path, keyed, interpolate=middle)
    assert rep["jump_repair"]["replaced"] == [10] and rep["jump_repair"]["applied"] is True
    assert rep["jump_repair"]["interpolator"] == {"kind": "injected"}
    assert rep["seam_measurement"] == "rendered-cells"
    on_disk = np.asarray(Image.open(tmp_path / "out" / "cycle" / "frame-010.png"))
    assert np.array_equal(on_disk, np.asarray(truth[10]))
    assert np.array_equal(np.asarray(Image.open(tmp_path / "out" / "cycle" / "frame-009.png")), np.asarray(reopened[9]))
    assert json.loads((tmp_path / "w.json").read_text())["jump_repair"]["replaced"] == [10]
    assert made == [0.5]


def test_a_jump_without_rife_is_refused_naming_repair_off(tmp_path, monkeypatch):
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    filmed, _ = _loop({10: 12})
    keyed = _keyed(tmp_path, filmed)
    with pytest.raises(SystemExit, match=r"--repair off"):
        _run(tmp_path, keyed)
    report = json.loads((tmp_path / "w.json").read_text())
    assert report["status"] == "failed" and "rife-ncnn-vulkan not found" in report["error"]


def test_repair_off_cuts_as_filmed_and_says_so(tmp_path):
    filmed, _ = _loop({10: 12})
    rep = _run(tmp_path, _keyed(tmp_path, filmed), repair="off")
    assert rep["jump_repair"] == {"applied": False, "why": "--repair off"}
    assert rep["seam_measurement"] == "source-frames"
