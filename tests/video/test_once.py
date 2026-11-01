# SPDX-License-Identifier: Apache-2.0
"""Terminal-pose cuts retain their endpoint and never ask it to match the start."""
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from conftest import as_pinned
from sprite_gen.video import loop, batch, canvas, clip_prompt


def keyed(root, *, unsettled=False, clipped=False, count=90):
    root.mkdir()
    for k in range(count):
        t = min(1, max(0, (k - 10) / (count - 25)))
        x = round(28 + 28 * t)
        if unsettled and k >= 20:
            x += 8 * (k % 2)
        im = Image.new("RGBA", (128, 96))
        ImageDraw.Draw(im).rectangle((x, 20 + round(35 * t), x + 16 + round(24 * t), 85), fill=(200, 80, 30, 255))
        if clipped and k == count // 2:
            ImageDraw.Draw(im).rectangle((0, 40, 20, 60), fill=(200, 80, 30, 255))
        im.save(root / f"{k:04d}.png")
    return root


def cut(tmp_path, **kwargs):
    return loop.run_loop(tmp_path / "keyed", tmp_path / "out", fps=24, state="death",
                         min_len=None, max_len=None, n_out=7, seam_max=0.001,
                         name="death", report_path=None, cycle_mode="once", **kwargs)


@pytest.mark.timeout(900)  # 20 calls of the binary at 128 x 96 (the held fill's frames between): seconds on a GPU, minutes on a CPU's Vulkan driver
def test_cut_keeps_final_pose_in_strip_and_once_previews(tmp_path, rife_runs):
    keyed(tmp_path / "keyed")
    report = cut(tmp_path)
    assert as_pinned(rife_runs(), 20), rife_runs()
    meta = report["strip"]
    assert report["cycle"]["kind"] == "once"
    assert meta["loop"] is False and meta["hold_last"] is True
    assert meta["end_frame"] == meta["frames"] - 1
    assert meta["source_cut"]["samples"][-1] == report["cycle"]["end_frame"]
    assert meta["sample_indices"][-1] == report["cycle"]["length"] - 1
    assert 0 <= meta["events"][0]["frame"] <= meta["end_frame"]
    strip = Image.open(meta["path"]).convert("RGBA")
    last = strip.crop((strip.width - meta["w"], 0, strip.width, strip.height))
    for suffix, repeat in (("gif", None), ("webp", 1)):
        with Image.open(tmp_path / "out" / f"death.{suffix}") as im:
            assert im.info.get("loop") == repeat
            im.seek(im.n_frames - 1)
            assert im.convert("RGBA").getchannel("A").tobytes() == last.getchannel("A").tobytes()


@pytest.mark.parametrize("kwargs,reason", [({"unsettled": True}, "stable"), ({"clipped": True}, "edge")])
def test_refuses_unfinished_or_clipped_body(tmp_path, kwargs, reason):
    keyed(tmp_path / "keyed", **kwargs)
    with pytest.raises(SystemExit, match=reason):
        cut(tmp_path)
    report = json.loads((tmp_path / "out/death.loop.report.json").read_text())
    assert report["status"] == "failed"


def test_once_cannot_apply_wrap_correction_or_take_a_loop_state(tmp_path):
    keyed(tmp_path / "keyed")
    with pytest.raises(SystemExit, match="anchor"):
        cut(tmp_path, anchor="body")
    with pytest.raises(SystemExit, match="idle"):
        loop.run_loop(tmp_path / "keyed", tmp_path / "idle", fps=24, state="idle", min_len=None,
                      max_len=None, n_out=None, seam_max=2, name="idle", report_path=None, cycle_mode="once")


@pytest.mark.parametrize("state", ["death", "fall", "land", "cast", "hurt_hold", "transform"])
def test_nonloop_states_have_terminal_prompts_and_route_once(state):
    record = clip_prompt.plan_prompt(direction="side", state=state)
    assert record["cycle"] == "once"
    assert not batch.pins_last_frame(state, "side")
    assert "evenly paced" not in record["prompt"] and "so the animation loops" not in record["prompt"]
    assert "0.4" in record["prompt"]
    assert canvas.profile_for(state) != canvas.profile_for(None)


@pytest.mark.parametrize("facing", ["right", "left"])
def test_collapse_has_a_body_length_of_room_on_both_sides(facing):
    still = Image.new("RGB", (120, 160), (0, 255, 0))
    ImageDraw.Draw(still).rectangle((50, 20, 70, 145), fill=(200, 80, 30))
    image, report = canvas.pad_canvas(still, canvas.profile_for("death"), facing=facing)
    foot_x = report["offset"][0] + 60
    body_height = 125
    assert foot_x - body_height > 0
    assert foot_x + body_height < image.width


def test_collapsing_body_preserves_anatomy_without_a_standing_constraint():
    from sprite_gen.video.body_plan import Body
    horse = batch.build_prompt("side", "death", None, body_plan=[Body("quadruped")])
    assert "It stays on all four legs" not in horse
    assert "quadruped" in horse
    slime = batch.build_prompt("side", "death", None, body_plan=[Body("legless")])
    assert "limbless" in slime


def test_end_pose_input_is_distinct_from_first_frame_pin():
    record = clip_prompt.plan_prompt(direction="side", state="death", end_frame=Path("final.png"))
    assert record["cycle"] == "once" and record["last_frame"] == "final.png"
    with pytest.raises(SystemExit, match="end frame"):
        clip_prompt.plan_prompt(direction="side", state="death", end_frame=Path("final.png"), last_frame=False)
    with pytest.raises(SystemExit, match="non-loop"):
        clip_prompt.plan_prompt(direction="side", state="idle", end_frame=Path("final.png"))


def test_terminal_gate_rejects_still_and_accumulated_drift():
    from functools import partial
    import numpy as np
    from sprite_gen.video.nonloop import hold_ending, select_cut
    # both are refused before any window's pose is judged, so a judge that takes every pose changes nothing
    ending = partial(hold_ending, pose=lambda first, end, mass: {})
    with pytest.raises(ValueError, match="performed action"):
        select_cut(np.zeros((30, 30)), np.ones(30), fps=24, ending=ending)
    # Every tail step is under 2.5%, but five steps slowly drift by 4%.
    values = np.r_[np.linspace(0, 1, 25), np.linspace(1, 1.04, 5)]
    with pytest.raises(ValueError, match="stable"):
        select_cut(np.abs(values[:, None] - values[None, :]), np.ones(30), fps=24, ending=ending)


@pytest.mark.parametrize("after", ["standing", "shaking", "clipped"])
def test_once_cuts_at_the_first_settled_ground_pose_before_later_motion(tmp_path, after):
    files = keyed(tmp_path / "keyed")
    initial = Image.open(files / "0000.png")
    settled = Image.open(files / "0089.png")
    for k in range(40, 55):
        settled.save(files / f"{k:04d}.png")
    for k in range(55, 90):
        (initial if after == "standing" or k % 2 else settled).save(files / f"{k:04d}.png")
    if after == "clipped":
        outside = Image.new("RGBA", initial.size)
        ImageDraw.Draw(outside).rectangle((0, 40, 20, 60), fill=(200, 80, 30, 255))
        outside.save(files / "0070.png")
    report = cut(tmp_path)
    assert report["cycle"]["end_frame"] == 44
    assert report["cycle"]["terminal"]["start"] == 40
    assert report["strip"]["source_cut"]["samples"][-1] == 44
    assert report["strip"]["hold_last"] is True


def test_a_recolored_upright_endpoint_is_not_a_collapse(tmp_path):
    files = keyed(tmp_path / "keyed")
    upright = Image.new("RGBA", (128, 96))
    ImageDraw.Draw(upright).rectangle((34, 27, 50, 85), fill=(30, 80, 200, 255))
    for k in range(75, 90):
        upright.save(files / f"{k:04d}.png")
    with pytest.raises(SystemExit, match="lowered"):
        cut(tmp_path)


def test_batch_sends_terminal_pin_and_does_not_reuse_a_changed_pin(tmp_path, monkeypatch):
    seen = []
    base = tmp_path / "base.png"
    Image.new("RGB", (32, 32), (0, 255, 0)).save(base)
    final = tmp_path / "final.png"
    Image.open(base).save(final)

    def canvas_stub(still, out, **kw):
        Image.open(base).save(out)
        report = {"fit": "state", "shape": "wide", "canvas": [32, 32], "offset": [0, 0]}
        kw["report_path"].write_text(json.dumps(report))
        return report

    def video_stub(image, prompt, out, report, **kw):
        seen.append(kw["last_frame"])
        out.write_bytes(b"clip")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    def frames_stub(clip, out, **kw):
        assert not kw["allow_edge_contact"] and not kw["allow_subject_edge_contact"]
        return {"fps": 24, "frames": 24, "alpha_zero_pct_min": 0, "alpha_zero_pct_max": 0, "keyed_dir": str(out)}

    def loop_stub(frames, out, **kw):
        assert kw["cycle_mode"] == "once" and kw["anchor"] == "none"
        return {"cycle": {"kind": "once", "length": 24, "period_global": None, "ratio": 0},
                "resampled_seam_ratio": None, "held_fill": {"cells": 10, "poses": 10, "held_runs": [], "made": [], "before": {"cells": 10, "poses": 10, "held_runs": []}}, "n_out": 24, "gif": {"file": "g"}, "webp": {"file": "w"}, "strip": {"path": "s"}}

    monkeypatch.setattr(batch.canvas_mod, "run_canvas", canvas_stub)
    monkeypatch.setattr(batch.frames_mod, "run_frames", frames_stub)
    monkeypatch.setattr(batch.loop_mod, "run_loop", loop_stub)
    args = dict(bases={"front": base}, states=["death"], root=tmp_path / "set", character=None,
                duration=None, resolution="480p", key="green", concurrency=1, force=False, gap=0,
                video_runner=video_stub, end_frames={"front-death": final})
    assert batch.run_set(**args)["ok"] == 1
    assert seen == [final]
    assert batch.run_set(**args)["ok"] == 1
    assert seen == [final]
    changed = Image.open(final)
    changed.putpixel((16, 16), (255, 0, 0))
    changed.save(final)
    refused = batch.run_set(**args)
    assert refused["ok"] == 0 and "end frame differs" in refused["items"][0]["error"]
