# SPDX-License-Identifier: Apache-2.0
"""Jump-frame repair (docs/loop-repair.md section 2): only the frame after a jump is replaced,
by the interpolator's frame between its two neighbours; at most three, never two side by side;
the hair watched is behind the body whichever way it faces; and `video-loop` records what it
replaced and gates the cells as they now play. When a frame needs making and no RIFE is
installed, `--repair auto` cuts the loop as filmed with a warning and the install line, and
`--repair on` refuses.

The interpolator is a stand-in that returns the true in-between of the synthetic loop, so the
tests need no binary and can say exactly which frame should come back."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

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
    assert len(record["replaced"]) == 3 and record["stopped"] == "3 interpolation calls used"
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


def test_blocked_worst_target_does_not_hide_an_independent_jump():
    filmed, truth = _loop({8: 14, 9: 14, 18: 10})
    fake = TrueMiddle(filmed, truth)
    out, record = repair.repair_jumps(filmed, fake)
    assert 18 in record["replaced"] and out[18] is truth[18]
    assert record["blocked"] and len(fake.calls) <= repair.MAX_REPAIRS
    assert all((k + 1) % N not in record["replaced"] for k in record["replaced"])


def test_bad_outline_proposals_preserve_originals_and_consume_the_call_budget():
    filmed, truth = _loop({3: 14, 9: 13, 15: 12, 20: 11})
    for frame in filmed + truth:
        d = ImageDraw.Draw(frame)
        d.rectangle((50, 30, 69, 99), outline=(10, 10, 10, 255), width=2)
        d.rectangle((48, 10, 71, 29), outline=(10, 10, 10, 255), width=2)
    before = [f.tobytes() for f in filmed]
    fake = TrueMiddle(filmed, truth)

    def melted(a, b, t):
        pixels = np.asarray(fake(a, b, t)).copy()
        dark = (pixels[..., :3].max(axis=2) < 70) & (pixels[..., 3] > 0)
        pixels[dark, :3] = (200, 180, 150)
        return Image.fromarray(pixels, "RGBA")

    out, record = repair.repair_jumps(filmed, melted)
    assert record["replaced"] == []
    assert record["attempts"] == len(fake.calls) == repair.MAX_REPAIRS
    assert len({r["target"] for r in record["rounds"]}) == repair.MAX_REPAIRS
    assert all(r["outcome"] == "rejected" and "outline" in r["faults"] for r in record["rounds"])
    assert [f.tobytes() for f in out] == before == [f.tobytes() for f in filmed]


def test_circular_adjacency_and_zero_call_budget():
    filmed, truth = _loop({0: 16, N - 1: 15, 12: 10})
    fake = TrueMiddle(filmed, truth)
    _, record = repair.repair_jumps(filmed, fake)
    made = record["replaced"]
    assert 12 in made and not (0 in made and N - 1 in made)
    assert all((k + 1) % N not in made for k in made)
    no_calls = TrueMiddle(filmed, truth)
    out, record = repair.repair_jumps(filmed, no_calls, max_frames=0)
    assert not no_calls.calls and record["attempts"] == 0
    assert all(a is b for a, b in zip(out, filmed))


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


def _no_rife(monkeypatch, tmp_path: Path) -> None:
    """No RIFE anywhere the engine looks: no SPRITE_GEN_RIFE, an empty PATH, an empty data directory."""
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    # PATH keeps every other tool (img2webp, ffmpeg); only the directories holding a RIFE go
    path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not (Path(d) / "rife-ncnn-vulkan").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(path))
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))


def test_a_jump_without_rife_is_cut_as_filmed_with_a_warning(tmp_path, monkeypatch, capsys):
    _no_rife(monkeypatch, tmp_path)
    filmed, _ = _loop({10: 12})
    keyed = _keyed(tmp_path, filmed)
    reopened = [Image.open(p).convert("RGBA") for p in sorted(keyed.glob("*.png"))][:N]
    rep = _run(tmp_path, keyed)
    assert rep["status"] == "passed"
    jr = rep["jump_repair"]
    assert jr["applied"] is False and jr["replaced"] == [] and jr["why"] == "RIFE not installed — cut as filmed"
    assert jr["install"] == "sprite-gen rife install" and "rife-ncnn-vulkan not found" in jr["rife"]
    assert jr["score_max_before"] >= repair.JUMP_RATIO and jr["score_max_after"] == jr["score_max_before"]
    assert rep["seam_measurement"] == "source-frames" and rep["jolt"]["measured"] == "as filmed"
    on_disk = np.asarray(Image.open(tmp_path / "out" / "cycle" / "frame-010.png"))
    assert np.array_equal(on_disk, np.asarray(reopened[10]))  # the jump frame stays as filmed
    warning = [l for l in capsys.readouterr().err.splitlines() if "jump frame was not repaired" in l]
    assert len(warning) == 1 and "sprite-gen rife install" in warning[0]
    assert json.loads((tmp_path / "w.json").read_text())["jump_repair"]["applied"] is False


def test_a_smooth_walk_without_rife_has_nothing_to_warn_about(tmp_path, monkeypatch, capsys):
    _no_rife(monkeypatch, tmp_path)
    filmed, _ = _loop()
    rep = _run(tmp_path, _keyed(tmp_path, filmed))
    assert rep["jump_repair"]["replaced"] == [] and "rife" not in rep["jump_repair"]
    assert "not repaired" not in capsys.readouterr().err


def test_repair_on_without_rife_is_refused_with_the_install_line(tmp_path, monkeypatch):
    _no_rife(monkeypatch, tmp_path)
    filmed, _ = _loop({10: 12})
    with pytest.raises(SystemExit, match=r"sprite-gen rife install.*--repair auto"):
        _run(tmp_path, _keyed(tmp_path, filmed), repair="on")
    report = json.loads((tmp_path / "w.json").read_text())
    assert report["status"] == "failed" and "rife-ncnn-vulkan not found" in report["error"]


def test_repair_on_with_rife_repairs_as_auto_does(tmp_path):
    filmed, truth = _loop({10: 12})
    rep = _run(tmp_path, _keyed(tmp_path, filmed), repair="on", interpolate=lambda a, b, t: truth[10])
    assert rep["jump_repair"]["replaced"] == [10] and rep["jump_repair"]["applied"] is True


def test_a_rife_that_is_found_and_fails_still_fails_the_loop(tmp_path):
    from sprite_gen.video import rife

    def broken(a, b, t):
        raise rife.RifeUnavailable("rife-ncnn-vulkan failed (exit 255): vkCreateInstance failed")

    filmed, _ = _loop({10: 12})
    with pytest.raises(SystemExit, match=r"vkCreateInstance failed.*--repair off"):
        _run(tmp_path, _keyed(tmp_path, filmed), interpolate=broken)


def test_a_jolt_gate_on_a_loop_left_unrepaired_says_rife_was_missing(tmp_path, monkeypatch):
    _no_rife(monkeypatch, tmp_path)
    filmed, _ = _loop({10: 12})
    with pytest.raises(SystemExit, match=r"regenerate the clip \(the jump repair did not run: RIFE is not installed"):
        _run(tmp_path, _keyed(tmp_path, filmed), jolt_max=0.01)


def test_repair_off_cuts_as_filmed_and_says_so(tmp_path):
    filmed, _ = _loop({10: 12})
    rep = _run(tmp_path, _keyed(tmp_path, filmed), repair="off")
    assert rep["jump_repair"] == {"applied": False, "why": "--repair off"}
    assert rep["seam_measurement"] == "source-frames"


# --- the jolt index and its gate (docs/loop-repair.md section 3) ---------------------------------


def test_jolt_index_is_zero_for_even_steps_and_high_for_alternating_ones():
    assert repair.jolt_index(np.full(12, 0.3)) == 0.0
    alternating = np.array([0.2, 0.4] * 6)
    assert repair.jolt_index(alternating) == pytest.approx(0.2 / 0.3)


def _head_pop(frames: list[Image.Image], k: int, dx: int) -> list[Image.Image]:
    """Frame k with the head (rows 10..30) moved `dx` px sideways."""
    out = list(frames)
    a = np.asarray(frames[k]).copy()
    head = a[10:30].copy()
    a[10:30] = 0
    a[10:30] = np.roll(head, dx, axis=1)
    out[k] = Image.fromarray(a, "RGBA")
    return out


def test_a_head_that_pops_sideways_is_named_by_the_gate():
    filmed, _ = _loop()
    smooth = repair.measure_jolt(filmed)
    assert repair.jolt_verdict(smooth) == []
    assert smooth["index"] < repair.JOLT_REFERENCE and smooth["head"]["x"]["step_max_pct"] < repair.HEAD_STEP_REFERENCE
    popped = repair.measure_jolt(_head_pop(filmed, 7, 4))
    over = repair.jolt_verdict(popped)
    assert len(over) == 1 and "head moves sideways" in over[0] and "(into frame 7)" in over[0]
    assert popped["head"]["x"]["worst_into_frame"] == 7


def test_a_loop_with_no_head_to_track_says_so_instead_of_passing():
    filmed, _ = _loop()
    blank = list(filmed)
    blank[3] = Image.new("RGBA", filmed[3].size, (0, 0, 0, 0))
    measured = repair.measure_jolt(blank)
    assert "skipped" in measured["head"]
    assert any("could not be tracked" in o for o in repair.jolt_verdict(measured))


def test_video_loop_reports_the_jolt_of_the_loop_as_it_plays_after_the_repair(tmp_path):
    filmed, truth = _loop({10: 12})
    rep = _run(tmp_path, _keyed(tmp_path, filmed), interpolate=lambda a, b, t: truth[10])
    jolt = rep["jolt"]
    assert jolt["measured"] == "after the jump repair" and jolt["warnings"] == [] and jolt["passed"]
    assert jolt["gated"] is False and jolt["gate"] is None
    assert jolt["reference"] == {"jolt_max": repair.JOLT_REFERENCE, "head_step_max": repair.HEAD_STEP_REFERENCE}
    assert set(jolt["head"]["x"]) >= {"step_max_pct", "step_median_pct", "max_over_median", "range_pct"}
    assert set(jolt["head"]["y"]) >= {"step_max_pct", "step_median_pct", "max_over_median", "range_pct"}


def test_by_default_a_jolting_loop_is_kept_with_a_warning_line(tmp_path, capsys):
    filmed, _ = _loop()
    popped = _head_pop(filmed, 7, 4)
    rep = _run(tmp_path, _keyed(tmp_path, popped), interpolate=lambda a, b, t: popped[7])
    assert rep["status"] == "passed" and rep["jolt"]["passed"] and rep["jolt"]["gated"] is False
    assert len(rep["jolt"]["warnings"]) == 1 and "head moves sideways" in rep["jolt"]["warnings"][0]
    assert "video-loop: warning: the head moves sideways" in capsys.readouterr().err


def test_a_bound_the_caller_passes_refuses_a_jolting_loop(tmp_path):
    filmed, _ = _loop()
    popped = _head_pop(filmed, 7, 4)
    stays = lambda a, b, t: popped[7]  # an in-between that cannot take the pop out
    with pytest.raises(SystemExit, match=r"^video-loop: loop jolts — the head moves sideways .* regenerate the clip$"):
        _run(tmp_path, _keyed(tmp_path, popped), interpolate=stays, head_step_max=repair.HEAD_STEP_REFERENCE)
    report = json.loads((tmp_path / "w.json").read_text())
    assert report["status"] == "failed" and report["jolt"]["passed"] is False and report["jolt"]["over"]
    assert report["jolt"]["gate"] == {"jolt_max": None, "head_step_max": repair.HEAD_STEP_REFERENCE}
    assert report["jump_repair"]["replaced"] == [7]
    # only the bound passed is checked: a jolt bound alone lets the head through
    again = tmp_path / "again"
    again.mkdir()
    rep = _run(again, _keyed(again, popped), interpolate=stays, jolt_max=10.0)
    assert rep["status"] == "passed" and rep["jolt"]["gated"] and rep["jolt"]["warnings"]


def test_repair_off_measures_the_jolt_and_does_not_gate_it(tmp_path):
    filmed, _ = _loop()
    rep = _run(tmp_path, _keyed(tmp_path, _head_pop(filmed, 7, 4)), repair="off", head_step_max=0.1)
    assert rep["jolt"]["gated"] is False and rep["jolt"]["warnings"] and rep["status"] == "passed"
