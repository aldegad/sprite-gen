# SPDX-License-Identifier: Apache-2.0
"""Jump-frame repair (docs/loop-repair.md section 2): the frames off the loop's flow are taken out
and made again by the interpolator between the frames either side, each at its own time; at most
three calls, never made from or beside a frame made; and `video-loop` records what it replaced and
gates the cells as they now play. The size reading (`jump_scores`, the hair behind the body
whichever way it faces) is what the jump guard of `video-loop-compare` reads. When a frame needs
making and no RIFE is installed, `--repair auto` cuts the loop as filmed with a warning and the
install line, and `--repair on` refuses.

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
SIZE_JUMP = 1.4  # a step this many times the loop's median: the size reading's jump, which picked frames before the flow reading


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


class TrueBetween:
    """Stand-in interpolator: the true frame of the synthetic loop at the time t of the way from one
    frame to another (a time the loop has a frame for)."""

    def __init__(self, filmed: list[Image.Image], truth: list[Image.Image]):
        self.index = {id(f): k for k, f in enumerate(filmed)}
        self.truth = truth
        self.calls: list[tuple[int, int, float]] = []

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        ka, kb = self.index[id(a)], self.index[id(b)]
        self.calls.append((ka, kb, round(t, 4)))
        at = t * ((kb - ka) % N)
        assert abs(at - round(at)) < 1e-9  # always a frame's own time
        return self.truth[(ka + round(at)) % N]


def forced_flow_group(monkeypatch, frames: list[int], neighbours: tuple[int, int], t: list[float]) -> None:
    """Isolate adoption from target detection: the flow reading names this one group (a drawing a frame),
    until a round has made it or refused it."""
    group = {"frames": frames, "drawings": [[k] for k in frames], "neighbours": list(neighbours), "t": t, "off": 9.0}

    def read(loop, *, fixed=(), skip=(), **kw):
        groups = [] if set(frames) & (set(fixed) | set(skip)) else [group]
        return {"groups": groups, "unfixed": [], "hold": 1, "drawings": len(loop), "off_max_before": 9.0 if groups else 0.0}

    monkeypatch.setattr(repair, "flow_targets", read)


def test_only_the_frame_off_the_flow_is_made_again_between_its_neighbours():
    filmed, truth = _loop({10: 12})
    fake = TrueBetween(filmed, truth)
    out, record = repair.repair_jumps(filmed, fake)
    assert record["replaced"] == [10]
    assert fake.calls == [(9, 11, 0.5)]
    assert out[10] is truth[10]
    assert all(out[k] is filmed[k] for k in range(N) if k != 10)  # every other frame stays the video's own
    assert record["off_max_before"] >= repair.FLOW_LIMIT > record["off_max_after"]
    assert record["stopped"] == "no frame off the flow"


def test_a_smooth_loop_is_left_alone_and_needs_no_interpolator():
    filmed, _ = _loop()
    out, record = repair.repair_jumps(filmed, None)
    assert record["replaced"] == [] and all(a is b for a, b in zip(out, filmed))


def test_at_most_three_calls_worst_first():
    filmed, truth = _loop({3: 14, 10: 13, 15: 12, 21: 11})
    fake = TrueBetween(filmed, truth)
    out, record = repair.repair_jumps(filmed, fake)
    assert len(record["replaced"]) == 3 and record["stopped"] == "3 interpolation calls used"
    assert set(record["replaced"]) < {3, 10, 15, 21} and all(out[k] is truth[k] for k in record["replaced"])
    offs = [r["off"] for r in record["rounds"]]
    assert offs == sorted(offs, reverse=True)  # worst first, by the flow reading
    assert [g["frames"] for g in record["left"]] == [[k] for k in {3, 10, 15, 21} - set(record["replaced"])]


def test_jumps_a_few_frames_apart_are_not_read_as_a_loop_on_threes():
    # Its only large steps are the ones into and out of the jumps, every few frames: to `held.measure` the ordinary
    # steps are repeats and the jumps drawings. Its runs are not a hold (five and six frames, and one), so it is read
    # a drawing a frame: each jump a frame of its own, and a frame made stands for itself alone.
    filmed, truth = _loop({3: 14, 9: 13, 15: 12, 20: 11})
    record = repair.flow_targets(filmed)
    assert record["hold"] == 1 and sorted(g["frames"] for g in record["groups"]) == [[3], [9], [15], [20]]
    out, record = repair.repair_jumps(filmed, TrueBetween(filmed, truth))
    assert len(record["replaced"]) == 3 and all(len(r["frames"]) == 1 for r in record["rounds"])


def test_two_jump_frames_side_by_side_are_made_again_as_two():
    filmed, truth = _loop({8: 14, 9: 14})
    fake = TrueBetween(filmed, truth)
    out, record = repair.repair_jumps(filmed, fake)
    assert record["replaced"] == [8, 9] and fake.calls == [(7, 10, 0.3333), (7, 10, 0.6667)]
    assert out[8] is truth[8] and out[9] is truth[9]


def test_the_hair_watched_is_behind_the_body_whichever_way_it_faces():
    # A small ponytail jump: the whole frame barely notices, the hair box does.
    right_filmed, right_truth = _loop({12: 6}, side="left")  # tail on the left = behind a right-facing body
    assert repair.jump_scores(right_filmed, facing="right")["hair"].max() >= SIZE_JUMP
    assert repair.jump_scores(right_filmed, facing="left")["hair"].max() < SIZE_JUMP
    left_filmed, _ = _loop({12: 6}, side="right")  # tail on the right = behind a left-facing body
    assert repair.jump_scores(left_filmed, facing="left")["hair"].max() >= SIZE_JUMP
    assert repair.hair_box("left") == (0.55, 0.30, 1.0, 0.80)
    # The flow reading needs no facing: the jump is taken out on either side.
    for filmed, truth in (_loop({12: 6}, side="left"), _loop({12: 6}, side="right")):
        out, record = repair.repair_jumps(filmed, TrueBetween(filmed, truth))
        assert record["replaced"] == [12]


def test_each_group_is_made_from_frames_as_filmed_never_beside_a_frame_made():
    filmed, truth = _loop({8: 14, 9: 14, 18: 10})
    fake = TrueBetween(filmed, truth)
    out, record = repair.repair_jumps(filmed, fake)
    assert sorted(record["replaced"]) == [8, 9, 18] and len(fake.calls) == repair.MAX_REPAIRS
    for r in record["rounds"]:
        assert not set(r["neighbours"]) & set(record["replaced"])
        assert all(filmed[k] is out[k] for k in r["neighbours"])


def test_bad_outline_proposals_preserve_originals_and_consume_the_call_budget():
    filmed, truth = _loop({3: 14, 10: 13, 15: 12, 21: 11})
    for frame in filmed + truth:
        d = ImageDraw.Draw(frame)
        d.rectangle((50, 30, 69, 99), outline=(10, 10, 10, 255), width=2)
        d.rectangle((48, 10, 71, 29), outline=(10, 10, 10, 255), width=2)
    before = [f.tobytes() for f in filmed]
    fake = TrueBetween(filmed, truth)

    def melted(a, b, t):
        pixels = np.asarray(fake(a, b, t)).copy()
        dark = (pixels[..., :3].max(axis=2) < 70) & (pixels[..., 3] > 0)
        pixels[dark, :3] = (200, 180, 150)
        return Image.fromarray(pixels, "RGBA")

    out, record = repair.repair_jumps(filmed, melted)
    assert record["replaced"] == []
    assert record["attempts"] == len(fake.calls) == repair.MAX_REPAIRS
    assert len({tuple(r["frames"]) for r in record["rounds"]}) == repair.MAX_REPAIRS
    assert all(r["outcome"] == "rejected" and "outline" in r["faults"] for r in record["rounds"])
    assert [f.tobytes() for f in out] == before == [f.tobytes() for f in filmed]


def test_a_pair_flung_at_the_wrap_is_taken_out_whole_not_smoothed_at_its_edges():
    # Frames N-1 and 0 flung together: the frames either side of them read as far off the line as they do, and
    # an in-between there brings the reading under the limit too, leaving the pair. The pair is what the
    # in-between moves furthest: it is taken out, made again from frames N-2 and 1.
    filmed, truth = _loop({0: 16, N - 1: 15, 12: 10})
    fake = TrueBetween(filmed, truth)
    out, record = repair.repair_jumps(filmed, fake)
    assert sorted(record["replaced"]) == [0, 12, N - 1]
    assert sorted(fake.calls) == [(11, 13, 0.5), (N - 2, 1, 0.3333), (N - 2, 1, 0.6667)]
    assert all(out[k] is truth[k] for k in (0, 12, N - 1))
    no_calls = TrueBetween(filmed, truth)
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
    assert json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))["jump_repair"]["replaced"] == [10]
    assert made == [0.5]
    # the jolt of the loop as it plays after the repair (docs/loop-repair.md section 3)
    jolt = rep["jolt"]
    assert jolt["measured"] == "after the jump repair" and jolt["warnings"] == [] and jolt["passed"]
    assert jolt["gated"] is False and jolt["gate"] is None
    assert jolt["reference"] == {"jolt_max": repair.JOLT_REFERENCE, "head_step_max": repair.HEAD_STEP_REFERENCE}
    assert set(jolt["head"]["x"]) >= {"step_max_pct", "step_median_pct", "max_over_median", "range_pct"}
    assert set(jolt["head"]["y"]) >= {"step_max_pct", "step_median_pct", "max_over_median", "range_pct"}


def test_video_loop_plays_two_frames_made_again_at_their_own_times_in_a_loop_as_long(tmp_path):
    filmed, truth = _loop({8: 14, 9: 14})
    keyed = _keyed(tmp_path, filmed)
    made = []

    def between(a, b, t):
        made.append(round(t, 4))
        return truth[8] if t < 0.5 else truth[9]

    rep = _run(tmp_path, keyed, interpolate=between)
    assert rep["jump_repair"]["replaced"] == [8, 9] and made == [0.3333, 0.6667]
    (round_,) = [r for r in rep["jump_repair"]["rounds"] if r["why"] == "flow"]
    assert round_["neighbours"] == [7, 10] and round_["t"] == [0.3333, 0.6667]
    cells = sorted((tmp_path / "out" / "cycle").glob("frame-*.png"))
    assert len(cells) == N  # the loop keeps its length: each frame its own time
    for k in (8, 9):
        assert np.array_equal(np.asarray(Image.open(cells[k])), np.asarray(truth[k]))


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
    assert jr["off_max_before"] >= repair.FLOW_LIMIT and jr["off_max_after"] == jr["off_max_before"]
    assert jr["unmade"]["frames"] == [10]
    assert rep["seam_measurement"] == "source-frames" and rep["jolt"]["measured"] == "as filmed"
    on_disk = np.asarray(Image.open(tmp_path / "out" / "cycle" / "frame-010.png"))
    assert np.array_equal(on_disk, np.asarray(reopened[10]))  # the jump frame stays as filmed
    warning = [l for l in capsys.readouterr().err.splitlines() if "jump frame was not repaired" in l]
    assert len(warning) == 1 and "sprite-gen rife install" in warning[0]
    assert json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))["jump_repair"]["applied"] is False


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
    report = json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and "rife-ncnn-vulkan not found" in report["error"]


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
    report = json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))
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
