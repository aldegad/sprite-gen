# SPDX-License-Identifier: Apache-2.0
"""The seam bridge (docs/loop-repair.md section 2, "The seam bridge"): a loop that is not a walk is not read for its
flow, but its wrap is — the step from its last frame to its first against the mean of the three steps either side
(`repair.SURGE_AROUND`), on the frames as they play. A wrap over `--seam-max` there, and over the re-render noise of a
pinned first frame, jumps: one, two or four frames round it are made again between the frames either side, the fewest
first, taken only without faults and where every step from one to the other then reads within the same limit. A wrap
that reads as a step of the loop needs no interpolator and leaves the loop byte for byte as it was cut; a loop drawn on
twos, a jump no straight line closes and a pin the clip does not keep are left to the gate.

The body goes round a small circle at an even speed, one turn a loop, so every step moves it as far; its wrap goes `J`
of those steps instead of one. The stand-in interpolator draws the body at the time asked on the way between its two
frames, so the tests need no binary and can say which frames come back."""

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

W, H, SS, N = 96, 128, 4, 48
LINE = (110, 90, 120, 255)  # a mid-tone line: no pixel of it reads dark (`rife.DARK_LUMA`), so a made frame smears nothing


def _figure(phase: float, *, radius: float = 3.0, blot: bool = False) -> Image.Image:
    """A standing body whose centre is at `phase` (turns) round a circle of `radius` px. `blot` paints a dark patch
    inside it: a frame a smear reads (`rife.smear`)."""
    dx, dy = radius * math.cos(2 * math.pi * phase), radius * math.sin(2 * math.pi * phase)
    im = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = lambda v: round(v * SS)  # noqa: E731
    cx, top = 48 + dx, 58 + dy
    d.rounded_rectangle((s(cx - 20), s(top), s(cx + 20), s(top + 58)), s(9), fill=(70, 110, 190, 255), outline=LINE, width=s(1.5))
    d.rounded_rectangle((s(cx + 17), s(top + 5), s(cx + 25), s(top + 45)), s(4), fill=(240, 200, 170, 255), outline=LINE, width=s(1.5))
    hy = top - 25
    d.ellipse((s(cx - 21), s(hy - 21), s(cx + 21), s(hy + 21)), fill=(245, 210, 180, 255), outline=LINE, width=s(1.5))
    for ex in (cx - 8, cx + 8):
        d.ellipse((s(ex - 3), s(hy - 5), s(ex + 3), s(hy + 3)), fill=(100, 80, 120, 255))
    d.line((s(cx - 5), s(hy + 8), s(cx + 5), s(hy + 8)), fill=LINE, width=s(1))
    a = np.asarray(im.resize((W, H), Image.LANCZOS)).copy()
    if blot:
        a[70:100, 35:60, :3] = 10
    a[a[..., 3] == 0] = 0  # no colour under full transparency, as a keyed frame has none
    return Image.fromarray(a, "RGBA")


def _phases(jump: float, *, held: bool = False, count: int = N + 6) -> list[float]:
    """One turn over a loop of N frames whose wrap goes `jump` steps instead of one; the clip goes on past the cut as
    filmed. `held`: each drawing shown for two frames."""
    step = 1.0 / (N - 1 + jump)
    return [(k - k % 2 if held else k) * step for k in range(count)]


def _keyed(tmp_path: Path, phases: list[float], **kw) -> Path:
    d = tmp_path / "keyed"
    d.mkdir()
    for k, p in enumerate(phases):
        _figure(p, **kw).save(d / f"{k:04d}.png")
    return d


class Between:
    """Stand-in interpolator: the body at the time t of the way from one frame of the clip to another, its centre on
    the circle between theirs. Each frame it is given is told by its thumbnail."""

    def __init__(self, phases: list[float], *, blot: bool = False):
        self.phases, self.blot = phases, blot
        self.small = [loop_mod._small_features(_figure(p)) for p in phases]
        self.calls: list[tuple[int, int, float]] = []

    def which(self, im: Image.Image) -> int:
        f = loop_mod._small_features(im)
        return int(np.argmin([np.abs(f - s).mean() for s in self.small]))

    def at(self, ka: int, kb: int, t: float) -> float:
        return self.phases[ka] + t * ((self.phases[kb] - self.phases[ka]) % 1.0)

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        ka, kb = self.which(a), self.which(b)
        self.calls.append((ka, kb, round(t, 4)))
        return _figure(self.at(ka, kb, t), blot=self.blot)


def never(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
    raise AssertionError("no frame is to be made")


def _run(tmp_path: Path, keyed: Path, *, cycle_mode: str = "fixed", **kw) -> dict:
    cut = {"start": 0, "length": N} if cycle_mode == "fixed" else {}
    return loop_mod.run_loop(keyed, tmp_path / "out", fps=24.0, state="idle", min_len=None, max_len=None, n_out=None,
                             seam_max=loop_mod.SEAM_RATIO_MAX, name="idle", report_path=tmp_path / "idle.json",
                             cycle_mode=cycle_mode, **cut, **kw)


def _refused(tmp_path: Path, keyed: Path, match: str, **kw) -> dict:
    with pytest.raises(SystemExit, match=match):
        _run(tmp_path, keyed, **kw)
    return json.loads((tmp_path / "idle.json").read_text(encoding="utf-8"))


def _report(tmp_path: Path, keyed: Path, **kw) -> dict:
    """The report of the cut, whatever the gate says of it."""
    try:
        _run(tmp_path, keyed, **kw)
    except SystemExit:
        pass
    return json.loads((tmp_path / "idle.json").read_text(encoding="utf-8"))


def _cells(out: Path) -> list[np.ndarray]:
    return [np.asarray(Image.open(p)) for p in sorted((out / "cycle").glob("frame-*.png"))]


@pytest.mark.slow
def test_a_wrap_that_jumps_is_bridged_by_one_frame_and_the_loop_then_passes_the_gate(tmp_path):
    phases = _phases(2.6)
    keyed = _keyed(tmp_path, phases)
    refused = _refused(tmp_path / "off", keyed, r"loop seam ratio \d+\.\d+ exceeds 2\.0", repair="off")
    assert refused["jump_repair"] == {"applied": False, "why": "--repair off"}

    between = Between(phases)
    rep = _run(tmp_path, keyed, interpolate=between)
    jr = rep["jump_repair"]
    assert (jr["reading"], jr["applied"], jr["replaced"], jr["width"]) == ("seam", True, [N - 1], 1)
    assert jr["seam"] > loop_mod.SEAM_RATIO_MAX and jr["wrap"] > loop_mod.PIN_NOISE_MAX and jr["hold"] == 1
    assert jr["seam_after"] <= loop_mod.SEAM_RATIO_MAX
    assert between.calls == [(N - 2, 0, 0.5)]  # the frame half way between the frames either side, at its own time
    assert jr["calls"] == 1 and jr["interpolator"] == {"kind": "injected"} and jr["facing"] == "right"
    (taken,) = jr["tries"]
    assert taken["anchors"] == [N - 2, 0] and taken["outcome"] == "accepted" and taken["faults"] == []
    # the gate reads the cells that play, and the loop now passes it
    assert rep["status"] == "passed" and rep["seam_measurement"] == "rendered-cells"
    assert rep["resampled_seam_ratio"] <= loop_mod.SEAM_RATIO_MAX < refused["resampled_seam_ratio"]
    cells = _cells(tmp_path / "out")
    assert len(cells) == N  # the loop keeps its length
    assert np.array_equal(cells[N - 1], np.asarray(_figure(between.at(N - 2, 0, 0.5))))
    filmed = [np.asarray(Image.open(p).convert("RGBA")) for p in sorted(keyed.glob("*.png"))][:N]
    assert all(np.array_equal(cells[k], filmed[k]) for k in range(N - 1))  # every other frame is the clip's own
    assert json.loads((tmp_path / "idle.json").read_text(encoding="utf-8"))["jump_repair"]["replaced"] == [N - 1]


def test_the_fewest_frames_are_tried_first_and_a_straight_line_that_does_not_close_makes_none(tmp_path, capsys):
    phases = _phases(5.0)
    between = Between(phases)
    rep = _run(tmp_path, _keyed(tmp_path, phases), interpolate=between)
    jr = rep["jump_repair"]
    assert [(t["width"], t["frames"], t["anchors"], t["outcome"], t.get("rejected_for")) for t in jr["tries"]] == [
        (1, [N - 1], [N - 2, 0], "rejected", "line"),
        (2, [N - 1, 0], [N - 2, 1], "rejected", "seam"),
        (4, [N - 2, N - 1, 0, 1], [N - 3, 2], "accepted", None)]
    assert jr["tries"][0]["line"] > loop_mod.SEAM_RATIO_MAX and "made" not in jr["tries"][0]
    assert jr["tries"][1]["after"] > loop_mod.SEAM_RATIO_MAX >= jr["tries"][2]["after"] == jr["seam_after"]
    assert jr["replaced"] == [N - 2, N - 1, 0, 1] and jr["calls"] == 6
    assert between.calls == [(N - 2, 1, 0.3333), (N - 2, 1, 0.6667), (N - 3, 2, 0.2), (N - 3, 2, 0.4), (N - 3, 2, 0.6), (N - 3, 2, 0.8)]
    assert [m["t"] for m in jr["tries"][2]["made"]] == [0.2, 0.4, 0.6, 0.8]
    assert rep["status"] == "passed" and rep["resampled_seam_ratio"] <= loop_mod.SEAM_RATIO_MAX
    cells = _cells(tmp_path / "out")
    for k, t in zip(jr["replaced"], (0.2, 0.4, 0.6, 0.8)):
        assert np.array_equal(cells[k], np.asarray(_figure(between.at(N - 3, 2, t))))
    (line,) = [l for l in capsys.readouterr().err.splitlines() if "wrap jumps" in l]
    assert f"frames {N - 2}, {N - 1}, 0, 1 were made again by RIFE between frames {N - 3} and 2" in line


@pytest.mark.slow
def test_a_wrap_that_reads_as_a_step_needs_no_interpolator_and_is_cut_as_before(tmp_path):
    keyed = _keyed(tmp_path, _phases(1.0))
    off = _run(tmp_path / "off", keyed, repair="off")
    rep = _run(tmp_path, keyed, interpolate=never)
    jr = rep["jump_repair"]
    assert jr["skipped"] == "the wrap reads as a step of the loop" and jr["seam"] <= loop_mod.SEAM_RATIO_MAX
    assert (jr["applied"], jr["replaced"], jr["calls"], jr["tries"]) == (False, [], 0, [])
    assert rep["seam_measurement"] == off["seam_measurement"] == "source-frames"
    for name in ("idle.strip.png", "idle.strip.json", "idle.gif", "idle.webp", *(f"cycle/frame-{k:03d}.png" for k in range(N))):
        assert (tmp_path / "out" / name).read_bytes() == (tmp_path / "off" / "out" / name).read_bytes(), name


def test_a_loop_moved_in_whole_pixels_reads_its_wrap_against_the_steps_beside_it_not_the_loop_mean():
    """Steps of nothing between steps of a pixel: against the loop's mean step, a wrap of one pixel reads as a jump."""
    def frame(k: int) -> Image.Image:
        body = _figure(0.0, radius=0.0)
        im = Image.new("RGBA", body.size)
        im.paste(body, (round(6 * math.sin(2 * math.pi * k / N)), 0))
        a = np.asarray(im).copy()
        a[40 + k // 12, 40 + k % 12] = (90, 200, 90, 255)  # a clip never repeats a frame byte for byte
        return Image.fromarray(a, "RGBA")

    frames = [frame(k) for k in range(N)]
    flat = loop_mod.cell_features(frames)
    ring = np.abs(np.roll(flat, -1, axis=0) - flat).mean(axis=1)
    assert ring[-1] / ring[:-1].mean() > loop_mod.SEAM_RATIO_MAX  # the loop's mean step would name it a jump
    out, record = repair.seam_bridge(frames, never, limit=loop_mod.SEAM_RATIO_MAX, hold=1)
    assert record["skipped"] == "the wrap reads as a step of the loop" and record["seam"] <= loop_mod.SEAM_RATIO_MAX
    assert all(a is b for a, b in zip(out, frames))


def test_a_near_still_loop_whose_wrap_jumps_under_the_re_render_noise_is_left_alone():
    """A loop that hardly moves — a patch of it shading over the loop and back at the wrap: its steps are so small that a
    wrap of no visible size reads as many of them."""
    body = np.asarray(_figure(0.0, radius=0.0))
    frames = []
    for k in range(N):
        a = body.copy()
        a[60:64, 44:48, :3] = 120 + round(40 * k / N)
        frames.append(Image.fromarray(a, "RGBA"))
    out, record = repair.seam_bridge(frames, never, limit=loop_mod.SEAM_RATIO_MAX, hold=1)
    assert record["seam"] > loop_mod.SEAM_RATIO_MAX and record["wrap"] <= loop_mod.PIN_NOISE_MAX
    assert record["skipped"] == "the wrap reads as a step of the loop" and all(a is b for a, b in zip(out, frames))


def test_a_loop_drawn_on_twos_is_not_bridged(tmp_path):
    rep = _report(tmp_path, _keyed(tmp_path, _phases(2.6, held=True)), interpolate=never)
    jr = rep["jump_repair"]
    assert rep["cycle_drawings"]["hold"] == 2 and jr["hold"] == 2
    assert jr["skipped"] == "drawn on twos or threes: a frame made round the wrap would split a drawing"
    assert jr["seam"] > loop_mod.SEAM_RATIO_MAX and jr["calls"] == 0 and jr["replaced"] == []


def test_a_jump_no_straight_line_closes_makes_no_frame_and_the_gate_refuses_it_as_before(tmp_path, capsys):
    rep = _refused(tmp_path, _keyed(tmp_path, _phases(12.0)), r"loop seam ratio \d+\.\d+ exceeds 2\.0", interpolate=never)
    jr = rep["jump_repair"]
    assert [(t["width"], t["rejected_for"]) for t in jr["tries"]] == [(1, "line"), (2, "line"), (4, "line")]
    assert jr["calls"] == 0 and jr["applied"] is False and rep["seam_measurement"] == "source-frames"
    (line,) = [l for l in capsys.readouterr().err.splitlines() if "wrap jumps" in l]
    assert "no frames made round it close it" in line and "cut as filmed" in line


def test_a_frame_made_with_a_fault_is_not_taken(tmp_path):
    phases = _phases(2.6)
    between = Between(phases, blot=True)
    rep = _refused(tmp_path, _keyed(tmp_path, phases), r"loop seam ratio", interpolate=between)
    jr = rep["jump_repair"]
    assert [(t["width"], t["rejected_for"], t["faults"]) for t in jr["tries"]] == [
        (1, "fault", ["smear"]), (2, "fault", ["smear"]), (4, "fault", ["smear"])]
    assert jr["calls"] == len(between.calls) == 1 + 2 + 4 and jr["replaced"] == [] and jr["applied"] is False
    assert rep["seam_measurement"] == "source-frames"


def _no_rife(monkeypatch, tmp_path: Path) -> None:
    """No RIFE anywhere the engine looks: no SPRITE_GEN_RIFE, an empty PATH, an empty data directory."""
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not (Path(d) / "rife-ncnn-vulkan").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(path))
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))


def test_a_wrap_to_bridge_without_rife_is_cut_as_filmed_with_the_install_line(tmp_path, monkeypatch, capsys):
    _no_rife(monkeypatch, tmp_path)
    rep = _refused(tmp_path, _keyed(tmp_path, _phases(2.6)), r"loop seam ratio")
    jr = rep["jump_repair"]
    assert jr["why"] == "RIFE not installed — the wrap cut as filmed" and jr["install"] == "sprite-gen rife install"
    assert "rife-ncnn-vulkan not found" in jr["rife"] and jr["unmade"]["frames"] == [N - 1]
    assert jr["applied"] is False and jr["calls"] == 0 and "interpolator" not in jr
    (line,) = [l for l in capsys.readouterr().err.splitlines() if "wrap jumps" in l]
    assert "RIFE is not installed" in line and "sprite-gen rife install" in line


def test_a_wrap_that_reads_as_a_step_without_rife_has_nothing_to_say(tmp_path, monkeypatch, capsys):
    _no_rife(monkeypatch, tmp_path)
    rep = _run(tmp_path, _keyed(tmp_path, _phases(1.0)))
    assert rep["jump_repair"]["skipped"] == "the wrap reads as a step of the loop" and "rife" not in rep["jump_repair"]
    assert "wrap jumps" not in capsys.readouterr().err


def test_repair_on_without_rife_refuses_a_wrap_to_bridge(tmp_path, monkeypatch):
    _no_rife(monkeypatch, tmp_path)
    report = _refused(tmp_path, _keyed(tmp_path, _phases(2.6)), r"sprite-gen rife install.*--repair auto", repair="on")
    assert report["status"] == "failed" and "rife-ncnn-vulkan not found" in report["error"]


@pytest.mark.slow
def test_repair_on_refusing_a_wrap_to_bridge_hands_nothing_over(tmp_path, monkeypatch):
    """A refused run hands nothing over (docs/video-pipeline.md section 4, `loop.refuse`): the seam bridge's refusal
    takes back what an earlier cut in the same folder handed over, as every other `video-loop` refusal does."""
    _no_rife(monkeypatch, tmp_path)
    _run(tmp_path, _keyed(tmp_path, _phases(1.0)))
    out, handed = tmp_path / "out", ("cycle", "idle.strip.png", "idle.strip.json", "idle.gif", "idle.webp")
    assert all((out / n).exists() for n in handed)
    jumping = tmp_path / "jumping"
    jumping.mkdir()
    for k, p in enumerate(_phases(2.6)):
        _figure(p).save(jumping / f"{k:04d}.png")
    report = _refused(tmp_path, jumping, r"sprite-gen rife install.*--repair auto", repair="on")
    assert report["status"] == "failed"
    assert [n for n in handed if (out / n).exists()] == [] and not (out / loop_mod.CYCLE_REFUSED_DIR).exists()


def test_a_pinned_clip_the_pin_refuses_is_not_bridged(tmp_path):
    phases = _phases(2.6, count=N) + [0.5]  # its last frame is not its first again
    rep = _refused(tmp_path, _keyed(tmp_path, phases), r"the pinned clip does not end on its first frame",
                   cycle_mode="pinned", interpolate=never)
    assert rep["jump_repair"]["skipped"] == "the pin does not close: the clip is refused on it"
    assert rep["jump_repair"]["calls"] == 0 and "seam" not in rep["jump_repair"]


def test_the_summary_names_the_frames_made_and_the_rife_that_made_them(tmp_path, monkeypatch, capsys):
    """RIFE is located only once a frame is to be made, and named with its calls of the binary."""
    phases = _phases(2.6)
    between = Between(phases)

    class Found:
        calls = 0

        def __call__(self, a, b, t):
            Found.calls += 2
            return between(a, b, t)

        def describe(self):
            return {"kind": "rife-ncnn-vulkan", "binary": "rife-ncnn-vulkan", "model": "rife-v4.6", "tta": ""}

        def counts(self):
            return {"binary_calls": Found.calls}

    monkeypatch.setattr(loop_mod.rife_mod, "Rife", Found)
    keyed = _keyed(tmp_path, phases)
    assert loop_mod.main(["--frames-dir", str(keyed), "--out-dir", str(tmp_path / "out"), "--state", "idle", "--cycle", "fixed",
                          "--start", "0", "--length", str(N), "--name", "idle", "--report", str(tmp_path / "idle.json")]) == 0
    out = capsys.readouterr().out
    summary = json.loads(out[out.index("{"):])
    report = json.loads((tmp_path / "idle.json").read_text(encoding="utf-8"))
    assert summary["jump_repair"] == {k: report["jump_repair"][k] for k in ("reading", "seam", "replaced", "seam_after")}
    assert summary["jump_repair"]["replaced"] == [N - 1]
    assert report["jump_repair"]["interpolator"] == {"kind": "rife-ncnn-vulkan", "binary": "rife-ncnn-vulkan", "model": "rife-v4.6",
                                                     "tta": "", "binary_calls": 2}


@pytest.mark.slow
@pytest.mark.real_rife
def test_rife_bridges_a_wrap_that_jumps_without_a_fault(tmp_path):
    rep = _run(tmp_path, _keyed(tmp_path, _phases(2.6)))
    jr = rep["jump_repair"]
    assert jr["applied"] is True and jr["interpolator"]["kind"] == "rife-ncnn-vulkan"
    taken = jr["tries"][-1]
    assert taken["outcome"] == "accepted" and taken["faults"] == [] and jr["replaced"] == taken["frames"]
    assert jr["seam_after"] <= loop_mod.SEAM_RATIO_MAX
    assert rep["status"] == "passed" and rep["resampled_seam_ratio"] <= loop_mod.SEAM_RATIO_MAX
