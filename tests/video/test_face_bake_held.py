# SPDX-License-Identifier: Apache-2.0
"""`face-bake` reads the body it bakes for a picture shown again (docs/face-parts.md, "face-bake"): which held runs a
clip means and which it names in a warning. The runs here are given as the steps a bake reads; the bake itself, on a
pack, is tested in tests/video/test_face_layer.py."""

from __future__ import annotations

from sprite_gen.video import face_bake

BODIES = {"idle": {"loop": True, "cells": ["i"] * 6}, "greet": {"loop": False, "cells": ["g"] * 6}}


def _read(shown, held_steps, bodies=BODIES):
    same = [t in held_steps for t in range(len(shown) - 1)]
    return face_bake.held_reading(shown, same, bodies)


def test_a_clip_s_opening_and_closing_pose_and_a_long_hold_are_meant_and_said_nothing_of():
    shown = [("greet", k) for k in range(6)] + [("idle", k) for k in range(6)]
    record = _read(shown, {0, 4, 7, 8, 9})  # greet 0-1, greet 4-5, idle 1-4
    assert (record["frames"], record["poses"]) == (12, 7)
    assert [(r["start"], r["length"], r["kind"]) for r in record["held_runs"]] == [
        (0, 2, "opening"), (4, 2, "closing"), (7, 4, "motion-hold")]
    assert [r["why"] for r in record["held_runs"]] == [
        "the clip greet's opening pose", "the clip greet's closing pose",
        "held 4 frames, longer than a drawing on threes (3): a hold the motion means"]
    assert record["held_runs"][0]["body"] == [["greet", 0], ["greet", 1]]
    assert face_bake.held_lines(record) == []


def test_a_picture_shown_again_mid_clip_across_the_clips_or_across_the_loop_s_wrap_is_named():
    shown = [("greet", k) for k in range(6)] + [("idle", k) for k in range(6)] + [("idle", 0), ("idle", 1)]
    record = _read(shown, {2, 5, 11})  # greet 2-3, greet 5 to idle 0, idle 5 to idle 0 (the wrap)
    assert [(r["start"], r["length"]) for r in record["held_runs"]] == [(2, 2), (5, 2), (11, 2)]
    assert not any("why" in r for r in record["held_runs"])
    lines = face_bake.held_lines(record)
    assert lines[0] == (
        "frames 2-3 show one body picture (greet:2, greet:3; 11/14 poses/frames): the body stands still there "
        "mid-motion, baked as the pack has it — make the pack from frames whose held cells are made again "
        "(video-loop --fill-held auto, docs/loop-repair.md section 7)")
    assert [line.split(";")[0] for line in lines[1:]] == [
        "frames 5-6 show one body picture (greet:5, idle:0", "frames 11-12 show one body picture (idle:5, idle:0"]


def test_a_hold_the_pack_lists_for_a_clip_is_meant_with_its_own_reason():
    bodies = {**BODIES, "greet": {**BODIES["greet"], "holds": [{"from": 2, "to": 4, "kind": "drawn", "reason": "drawn on threes"}]}}
    shown = [("greet", k) for k in range(6)]
    record = _read(shown, {2, 3}, bodies)
    assert [(r["start"], r["length"], r["kind"], r["why"]) for r in record["held_runs"]] == [(2, 3, "drawn", "drawn on threes")]
    assert face_bake.held_lines(record) == []
    assert face_bake.held_lines(_read(shown, {1, 2}, bodies))  # greet 1-3: past the hold listed, named
