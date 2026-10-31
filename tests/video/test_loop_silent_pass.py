# SPDX-License-Identifier: Apache-2.0
"""A walk loop that jumps where the seam gate does not look is not passed without a word.

- The step gate: every step of a walk's or run's loop as it plays, read between its drawings (`loop.drawing_steps`), the
  largest over their mean at most STEP_RATIO_MAX — a frame that jumps inside the loop, where the seam ratio at the wrap
  does not see it, fails the loop. A loop held on twos or threes is read a drawing a step, not a frame a step. A step
  over the bound that its drawing moving a pixel of the frames as filmed makes, the move read on coverage, is a pixel's
  jitter of a loop that barely moves, not a jump, in any colour; a drawing moved two pixels is a jump in any colour.
- A frame the jump repair took off the flow and could not make again (its group refused) and that stays off the flow is
  `jump_repair.unfixed`, said in a warning line; `--unfixed refuse` fails such a loop.
- A fixed cut the clip's own crossings count an odd number of steps fails (`loop.fixed_count`); where they mark cycles (a
  pass hidden, the clip an interval on the same picture), they count as `--anchor motion-auto` counts its own cut.
- A `--seam-max` past STEP_RATIO_MAX lifts the step gate with the seam: the loop is kept and a warning line says why.
- The seam gate's refusal says why where the warning lines know it (a part on a beat of its own), in the failure line and
  in `seam_refusal`; elsewhere its line is as it was.

Synthetic frames only: the side walk of the body anchor's gate, held or with a frame moved; the side walker whose steps
the crossings count; the front walker with a tail on its own beat of the overlapped wrap; the repair's ponytail loop."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import repair
from sprite_gen.video import loop as loop_mod
from tests.video import test_gait_fallback as gait
from tests.video import test_loop_overlap as overlap
from tests.video import test_loop_repair as ponytail
from tests.video.test_body_anchor_gate import PERIOD, _keyed, _moved, _refused, _walk
from tests.video.test_walk_steps import ARMS_IN, _behind, _side

JUMP = 12  # the frame of the cut moved sideways


def _cut(root: Path, keyed: Path, name: str, **kw) -> dict:
    """`video-loop --state walk --cycle fixed` of the keyed frames, one stride from frame 0 unless said otherwise."""
    kw = {"cycle_mode": "fixed", "start": 0, "length": PERIOD, "repair": "off", "seam_max": loop_mod.SEAM_RATIO_MAX, **kw}
    return loop_mod.run_loop(keyed, root / name, fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                             name="walk-side", report_path=root / f"{name}.json", body_height=400, strip_height=640, **kw)


def _jumped() -> list[Image.Image]:
    """The side walk standing in place, frame JUMP of the cut moved 10 px sideways: it jumps there and back."""
    frames = _walk(drift=0.0)
    frames[JUMP] = _moved(frames[JUMP], 10)
    return frames


def test_a_frame_that_jumps_inside_the_loop_fails_it_on_the_steps_between_its_drawings(tmp_path: Path, capsys):
    """The wrap closes — the seam gate passes it — and a frame in the middle of the loop jumps 10 px and back."""
    keyed = _keyed(tmp_path, "walk", _jumped())
    message, report = _refused(tmp_path, keyed, "cut")
    assert message.startswith("video-loop: loop step ratio ") and "the loop jumps inside — regenerate the clip" in message
    assert report["status"] == "failed" and report["resampled_seam_ratio"] <= loop_mod.SEAM_RATIO_MAX
    gate = report["step_gate"]
    assert gate["read"] == "drawings" and gate["drawings"] == PERIOD and gate["hold"] == 1
    assert gate["max"] == loop_mod.STEP_RATIO_MAX and gate["lifted"] is False
    assert gate["largest_ratio"] > loop_mod.STEP_RATIO_MAX and gate["largest_into"] in (JUMP, JUMP + 1)
    assert report["seam_refusal"] == {"reading": "steps", "ratio": gate["largest_ratio"], "max": loop_mod.STEP_RATIO_MAX,
                                      "into": gate["largest_into"], "reason": None}
    assert f"into frame {gate['largest_into']}, read between its {PERIOD} drawings" in message
    # a seam limit past the step gate lifts it: the loop is kept, and a warning line says so
    capsys.readouterr()
    kept = _cut(tmp_path, keyed, "forced", seam_max=1000.0)
    assert kept["status"] == "passed" and kept["step_gate"]["lifted"] is True and kept["step_gate"]["max"] == 1000.0
    assert kept["step_gate"]["largest_ratio"] == gate["largest_ratio"] and "kept" in kept["step_gate"]
    assert "video-loop: warning: the loop's largest step between drawings is" in capsys.readouterr().err


def test_a_walk_held_on_threes_is_read_between_its_drawings(tmp_path: Path):
    """On threes each drawing shows for three frames: the frames step nothing and then three steps, and the largest of
    their steps is three times their mean. Between its drawings the walk steps evenly, and it passes."""
    walk = _walk(drift=0.0)
    keyed = _keyed(tmp_path, "held", [walk[k - k % 3] for k in range(len(walk))])
    # from frame 1: its wrap is a drawing shown again, which the seam gate passes on the frames as filmed
    report = _cut(tmp_path, keyed, "cut", start=1)
    assert report["status"] == "passed"
    gate = report["step_gate"]
    assert (gate["drawings"], gate["hold"]) == (PERIOD // 3, 3) and gate["largest_ratio"] <= loop_mod.STEP_RATIO_MAX
    strip = Image.open(tmp_path / "cut" / "walk-side.strip.png")
    meta = report["strip"]
    cells = [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]
    mean, _, steps = loop_mod._seam_on_cells(cells)
    assert float(steps.max()) / mean > loop_mod.STEP_RATIO_MAX  # read a frame a step, the hold would fail it


COLOURS = ("as drawn", "black", "grey near the backdrop")


def _coloured(frames: list[Image.Image], colour: str) -> list[Image.Image]:
    """The frames with their alpha as it is and their colour changed: black, no colour at all, or one dark grey
    (16, 16, 16) near the backdrop the analysis reads a transparent pixel as (premultiplied, black) — the same geometry
    at less contrast than a walk drawn in colour."""
    if colour == "as drawn":
        return frames
    out = []
    for frame in frames:
        pixels = np.array(frame)
        pixels[..., :3] = 0 if colour == "black" else 16
        out.append(Image.fromarray(pixels, "RGBA"))
    return out


@pytest.mark.parametrize("colour", COLOURS)
def test_a_pixels_jitter_of_a_walk_that_barely_moves_is_not_held_to_the_bound(tmp_path: Path, colour: str):
    """The gait fallback's walk going away from the camera, its feet rising a whole crop row every three or four frames:
    on a walk that barely moves, the step with a row in it is over 2.5 times the loop's mean step, its drawing moved one
    pixel, and with that pixel taken back an ordinary step. It is a pixel's jitter in any colour: the loop passes, held
    to its largest step that is none, and `step_gate.jitter` names the steps. Cut with its feet where they were filmed
    (`--size-hold off`: the gait fallback holds its size alone), as the size hold cut it before it held the feet too."""
    code, report, _ = gait.run(tmp_path, _coloured([gait.walking_away(k) for k in range(73)], colour), "--size-hold", "off")
    assert code == 0 and report["status"] == "passed"
    gate = report["step_gate"]
    assert gate["largest_ratio"] <= loop_mod.STEP_RATIO_MAX and "kept" not in gate
    assert gate["jitter"] and all(j["ratio"] > loop_mod.STEP_RATIO_MAX >= j["ratio_moved_back"] for j in gate["jitter"])
    assert all(max(map(abs, j["move"])) == 1 for j in gate["jitter"])


@pytest.mark.parametrize("colour", COLOURS)
def test_a_frame_moved_two_pixels_is_a_jump_in_any_colour(tmp_path: Path, colour: str):
    """The side walk standing in place, frame JUMP of the cut moved 2 px and back (6 px as the walk is cut 400 px tall),
    as drawn, black or in a grey near the backdrop: every frame's alpha is the same, and so is the verdict. The drawing
    moved two pixels of the frames as filmed, not one: the step is no pixel's jitter, and the loop fails on it."""
    frames = _walk(drift=0.0)
    frames[JUMP] = _moved(frames[JUMP], 2)
    message, report = _refused(tmp_path, _keyed(tmp_path, "walk", _coloured(frames, colour)), "cut")
    assert message.startswith("video-loop: loop step ratio ") and report["seam_refusal"]["reading"] == "steps"
    gate = report["step_gate"]
    assert gate["largest_ratio"] > loop_mod.STEP_RATIO_MAX and gate["largest_into"] in (JUMP, JUMP + 1)
    assert "jitter" not in gate


def _smeared(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
    """A stand-in RIFE whose every frame carries a black smear across the body: a fault, so each group is refused."""
    out = b.copy()
    ImageDraw.Draw(out).rectangle((95, 70, 120, 95), fill=(0, 0, 0, 255))
    return out


def test_a_group_refused_leaves_its_drawing_off_the_flow_unfixed():
    """The repair's ponytail loop, a frame flung 12 px, outlined so a proposal that melts its outline is refused: the
    flung frame stays as filmed, off the flow, and is named `unfixed` with the fault that refused it."""
    filmed, truth = ponytail._loop({10: 12})
    for frame in filmed + truth:
        d = ImageDraw.Draw(frame)
        d.rectangle((50, 30, 69, 99), outline=(10, 10, 10, 255), width=2)
        d.rectangle((48, 10, 71, 29), outline=(10, 10, 10, 255), width=2)
    fake = ponytail.TrueBetween(filmed, truth)

    def melted(a, b, t):
        pixels = np.asarray(fake(a, b, t)).copy()
        dark = (pixels[..., :3].max(axis=2) < 70) & (pixels[..., 3] > 0)
        pixels[dark, :3] = (200, 180, 150)
        return Image.fromarray(pixels, "RGBA")

    out, record = repair.repair_jumps(filmed, melted)
    assert 10 in record["refused"] and 10 not in record["replaced"]
    (left,) = [u for u in record["unfixed"] if 10 in u["frames"]]
    assert "outline" in left["refused"] and left["off"] >= repair.FLOW_LIMIT
    assert set(left) == {"frames", "off", "turn", "into", "out", "window", "refused"}


def test_a_jump_the_repair_could_not_make_again_is_named_and_fails_the_loop(tmp_path: Path, capsys):
    """The frame that jumps inside the loop, the jump repair on, and a RIFE whose frame there is refused: the frame is
    left off the flow (`unfixed`), the step gate fails the loop and its line says why. Lifted, the loop is kept with the
    frame named in a warning line; `--unfixed refuse` fails it on that frame."""
    keyed = _keyed(tmp_path, "walk", _jumped())
    message, report = _refused(tmp_path, keyed, "cut", repair="auto", interpolate=_smeared, anchor="none")
    assert JUMP in report["jump_repair"]["refused"]
    (left,) = [u for u in report["jump_repair"]["unfixed"] if JUMP in u["frames"]]
    assert left["refused"] == ["smear"] and left["off"] >= repair.FLOW_LIMIT
    refusal = report["seam_refusal"]
    assert refusal["reading"] == "steps" and refusal["reason"] == "repair-refused" and refusal["refused"] == ["smear"]
    assert JUMP in refusal["frames"]
    assert message.startswith("video-loop: loop step ratio ") and "the jump repair's frame there was refused (smear)" in message
    capsys.readouterr()
    kept = _cut(tmp_path, keyed, "forced", repair="auto", interpolate=_smeared, anchor="none", seam_max=1000.0)
    assert kept["status"] == "passed" and [u["frames"] for u in kept["jump_repair"]["unfixed"]] == [u["frames"] for u in report["jump_repair"]["unfixed"]]
    err = capsys.readouterr().err
    assert "off the walk's flow after the jump repair: the group that would bring it back was refused (smear)" in err
    assert "--unfixed refuse refuses such a loop" in err
    message, refused = _refused(tmp_path, keyed, "refuse", repair="auto", interpolate=_smeared, anchor="none",
                                seam_max=1000.0, unfixed="refuse")
    assert message.startswith("video-loop: loop frames left off the flow — ") and "(smear)" in message
    assert refused["status"] == "failed"


def test_the_cli_passes_unfixed_and_warn_is_its_default():
    parser = argparse.ArgumentParser()
    loop_mod.add_arguments(parser)
    assert parser.parse_args(["--frames-dir", "k", "--out-dir", "o"]).unfixed == "warn"
    assert parser.parse_args(["--frames-dir", "k", "--out-dir", "o", "--unfixed", "refuse"]).unfixed == "refuse"
    with pytest.raises(SystemExit):
        parser.parse_args(["--frames-dir", "k", "--out-dir", "o", "--unfixed", "off"])


STRIDE = 24  # the side walker's cycle: a step every 12 frames


def _side_keyed(root: Path) -> Path:
    """The side walker, legs drawn alike and no arms: a step on, its silhouette is the same."""
    keyed = root / "side-keyed"
    keyed.mkdir(parents=True)
    for k in range(73):
        _side(k, cycle=STRIDE, arms=False).save(keyed / f"{k:03}.png")
    return keyed


def test_a_fixed_cut_the_clips_crossings_count_one_step_fails(tmp_path: Path, capsys):
    """A cut one step long joins the walk a step on: the silhouette closes — the seam gate passes it — and the legs swap
    at the wrap. The clip's crossings count it one step, and the cut fails; two steps long it passes, counted two."""
    keyed = _side_keyed(tmp_path)
    message, report = _refused(tmp_path, keyed, "one", start=STRIDE, length=STRIDE // 2)
    assert message.startswith(f"video-loop: loop step count refutes the fixed cut {STRIDE}/{STRIDE // 2} — ")
    counted = report["cycle"]["step_count"]
    assert counted["chosen_by"] == "caller" and counted["cut"]["verdict"] == "refuted" and counted["cut"]["k"] == [1]
    assert report["seam_refusal"] == {"reading": "count", "cut": [STRIDE, STRIDE // 2], "verdict": "refuted", "k": [1],
                                      "why": counted["cut"]["why"], "two_steps": STRIDE}
    two = _cut(tmp_path, keyed, "two", start=STRIDE, length=STRIDE)
    assert two["status"] == "passed" and two["cycle"]["step_count"]["cut"]["verdict"] == "two"
    assert "steps" not in two["cycle"] and "steps" not in two["strip"]
    # lifted with the seam, the one-step cut is kept, and a warning line says so
    capsys.readouterr()
    kept = _cut(tmp_path, keyed, "forced", start=STRIDE, length=STRIDE // 2, seam_max=1000.0)
    assert kept["status"] == "passed" and kept["cycle"]["step_count"]["lifted"] == {"seam_max": 1000.0}
    assert f"the clip's crossings count the fixed cut {STRIDE}/{STRIDE // 2} one step" in capsys.readouterr().err


def test_a_fixed_cut_of_one_cycle_whose_crossings_mark_cycles_is_counted_two_steps(tmp_path: Path):
    """Seen from behind on twos, the far foot passes hidden: one crossing a cycle. The clip one interval on is the same
    picture, the body rises and falls twice an interval, its arms swinging within its sides, the crossings mark cycles,
    and the cut of one cycle is two steps — not failed, as `--anchor motion-auto` counts its own cut (the parent engine
    counted it one step and stood the count aside). Its arms swinging past its sides, the bands they swing across move
    on the beat, but the rows down the middle of the body read a body rising whole, and it is counted the same (the
    engine before read the bands alone, left the crossings on steps, counted the cut one step and failed it)."""
    for arm in (ARMS_IN, 16):
        frames = [_behind(k, cycle=20, hold=2, arm=arm) for k in range(73)]
        D = loop_mod._distances(np.stack([loop_mod._small_features(f) for f in frames]))
        counted = loop_mod.fixed_count(frames, D, 20, 20)
        assert counted["chosen_by"] == "caller" and counted["cycles"]["cycles"] is True, arm
        assert counted["cycles"].get("unwitnessed", False) is False, arm
        assert counted["cut"]["verdict"] == "two" and counted["cut"]["k"] == [2], arm
        assert loop_mod.odd_count({"kind": "fixed", "step_count": counted}) is False, arm


def test_a_wrap_a_part_on_its_own_beat_leaves_open_says_so_in_its_failure_line(tmp_path: Path):
    """The front walker's tail swings on a beat of its own: the cut covers two steps by the clip's crossings and the
    overlapped wrap is turned off. The seam gate's line keeps its words up to the reason and gives that reason in place of
    a return to the starting pose; the report says it (`seam_refusal`)."""
    # the walker cut as `video-loop --state walk --anchor motion-auto --overlap-wrap off` cuts it
    try:
        loop_mod.run_loop(overlap._keyed(tmp_path), tmp_path / "out", fps=24.0, state="walk", min_len=None,
                          max_len=None, n_out=None, seam_max=loop_mod.SEAM_RATIO_MAX, name="loop",
                          report_path=tmp_path / "loop.json", anchor="motion-auto", repair="auto",
                          interpolate=overlap.Between(), overlap_wrap="off")
    except SystemExit:
        pass
    report = json.loads((tmp_path / "loop.json").read_text())
    error = report["error"]
    assert error.startswith("video-loop: loop seam ratio") and "the cycle does not close — the cut covers two steps" in error
    assert "a part on a beat of its own (a tail, a held prop) closes on no cut of two steps" in error
    assert "the overlapped wrap was not made (turned off (--overlap-wrap off))" in error
    assert "complete return to the starting pose" not in error
    refusal = report["seam_refusal"]
    assert (refusal["reading"], refusal["reason"], refusal["overlap_wrap"]) == ("wrap", "own-beat", "skipped")
    assert refusal["ratio"] == report["resampled_seam_ratio"] and refusal["max"] == loop_mod.SEAM_RATIO_MAX


def test_a_wrap_nothing_names_keeps_its_failure_line(tmp_path: Path):
    """A frame of the wrap moved sideways: no warning line names a reason, and the line is as it was."""
    frames = _walk(drift=0.0)
    frames[PERIOD - 1] = _moved(frames[PERIOD - 1], 10)
    message, report = _refused(tmp_path, _keyed(tmp_path, "walk", frames), "cut")
    assert message.endswith("the cycle does not close — regenerate with a complete return to the starting pose")
    assert report["seam_refusal"]["reading"] == "wrap" and report["seam_refusal"]["reason"] is None
