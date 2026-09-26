"""Attack clips: one timed strike, the drawn grip, no turning, pinned to end where they start.

A 4 s clip, the frame, camera and background rules without the "evenly paced" line, and a
caller's own motion paragraph (`build_prompt(motion=...)`) in place of the built-in one.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sprite_gen.video import batch as batch_mod

FIXTURE = Path(__file__).parents[1] / "fixtures" / "facing" / "left.png"


def test_attack_text_is_one_timed_strike_that_keeps_the_drawn_grip() -> None:
    text = batch_mod.build_prompt("side", "attack", None)
    for phrase in ("one melee attack", "Every grip stays exactly as shown in the image", "one hand stays one hand, both hands stay both hands",
                   "windup (about 0.5 s)", "strike in front (about 0.25 s)", "held impact pose (about 0.3 s)",
                   "exact starting stance", "nothing is let go or switched to the other hand",
                   "A hand the motion does not use stays where it is drawn, with anything it holds",
                   "without turning", "anything it holds always stay fully inside the frame", "no motion blur"):
        assert phrase in text, phrase
    # what stays put is said once, in the same words a caller's own motion gets
    assert text.count(batch_mod.HOLD_TEXT["attack"]) == 1
    assert text.count("stays exactly as shown") == 1 and text.count("without turning") == 1
    # the grip is the image's: a heavy weapon drawn in both hands is not asked into one
    assert "hand nearest the viewer" not in text
    # one strike: the whole pinned clip is the loop, so nothing asks for a second one
    assert "twice" not in text and "Perform this attack" not in text
    assert "evenly paced" not in text
    # the repeating states keep their template, evenly paced line included
    assert "evenly paced" in batch_mod.build_prompt("side", "walk", None)
    assert "no motion blur" not in batch_mod.build_prompt("side", "walk", None)


def test_a_callers_motion_replaces_the_built_in_sentence() -> None:
    motion = "The knight draws the sword back, then strikes forward and holds the impact pose. It never turns."
    text = batch_mod.build_prompt("side", "attack", "A small knight.", facing="right", motion=motion)
    hold = batch_mod.HOLD_TEXT["attack"]
    assert text.startswith(f"2D game sprite animation. {motion} {hold} "
                           "A small knight is seen from the exact side, facing right. Stays centered")
    assert text.endswith("no motion blur, no smears and no afterimages.")
    assert "melee attack" not in text  # the built-in attack sentence is not added on top
    # whitespace in the paragraph is folded, a missing subject reads "The character"
    assert "The character is seen from the front" in batch_mod.build_prompt("front", "attack", None, motion=" Strikes.\n Holds. ")
    # a state with no repeat sentence and its own template
    walk = batch_mod.build_prompt("side", "walk", None, motion="The fox trots in place.")
    assert "Perform this attack" not in walk and hold not in walk and walk.endswith("so the animation loops.")
    with pytest.raises(SystemExit):
        batch_mod.build_prompt("side", "attack", None, motion="  \n ")


def test_video_cli_pins_the_last_frame_only_when_asked(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(batch_mod.subprocess, "run", fake_run)
    common = dict(duration=4, resolution="480p", log=tmp_path / "log")
    batch_mod.run_video_cli(tmp_path / "canvas.png", "p", tmp_path / "c.mp4", tmp_path / "r.json", **common)
    batch_mod.run_video_cli(tmp_path / "canvas.png", "p", tmp_path / "c.mp4", tmp_path / "r.json", last_frame=tmp_path / "canvas.png", **common)
    assert "--last-frame" not in seen[0]
    assert seen[1][seen[1].index("--last-frame") + 1] == str(tmp_path / "canvas.png")
    assert seen[1][seen[1].index("--duration") + 1] == "4"


@pytest.mark.parametrize("state,duration,pinned", [("attack", 2, True), ("jump", 3, False)])
def test_video_set_pins_attack_to_its_canvas_for_two_seconds(tmp_path: Path, state: str, duration: int, pinned: bool, monkeypatch) -> None:
    monkeypatch.setattr(batch_mod.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("right", {}))
    calls: list[dict] = []

    def video(image, prompt, out, report, **kw):
        calls.append({"image": image, **kw})
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    result = batch_mod.run_item(item=f"side-{state}", direction="side", state=state, base=FIXTURE, root=tmp_path,
                                character=None, duration=None, resolution="480p", key="green", force=False, gap=0,
                                video_runner=video)
    assert calls[0]["duration"] == duration
    assert ("last_frame" in calls[0]) is pinned
    if pinned:
        assert calls[0]["last_frame"] == calls[0]["image"] == tmp_path / f"side-{state}" / "canvas.png"
    # frames/loop may refuse a synthetic clip; the clip call itself is what this pins
    assert result.get("clip", {}).get("last_frame", pinned) is pinned or not result.get("ok")


def test_video_set_gives_every_state_the_same_body_height(tmp_path: Path, monkeypatch) -> None:
    from PIL import Image
    seen: list = []
    cycles: dict = {}

    def fake_canvas(base, out, *, state, shape, facing, headroom, lead, report_path, fit="state"):
        Image.new("RGB", (32, 32), (0, 255, 0)).save(out)
        return {"shape": "square", "canvas": [32, 32], "offset": [0, 0]}

    def fake_frames(clip, out_dir, **kw):
        keyed = Path(out_dir) / "keyed"
        keyed.mkdir(parents=True, exist_ok=True)
        return {"fps": 24.0, "frames": 30, "alpha_zero_pct_min": 0.0, "alpha_zero_pct_max": 0.0, "keyed_dir": str(keyed), "spill": {"mode": "full"}}

    def fake_loop(frames_dir, out_dir, **kw):
        seen.append((kw["state"], kw.get("body_height")))
        cycles[kw["state"]] = kw.get("cycle_mode")
        return {"cycle": {"kind": "periodic", "length": 24, "period_global": 24, "ratio": 0.2}, "resampled_seam_ratio": 0.3,
                "n_out": 8, "gif": {"file": "g"}, "webp": {"file": "w"}, "strip": {"path": "s", "drift_px": 0}}

    def video(image, prompt, out, report, **kw):
        Path(out).write_bytes(b"x")
        Path(report).write_text(json.dumps({"prompt": prompt}))
        return 0

    monkeypatch.setattr(batch_mod.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("right", {}))
    monkeypatch.setattr(batch_mod.canvas_mod, "run_canvas", fake_canvas)
    monkeypatch.setattr(batch_mod.frames_mod, "run_frames", fake_frames)
    monkeypatch.setattr(batch_mod.loop_mod, "run_loop", fake_loop)
    base = tmp_path / "base.png"
    Image.new("RGB", (32, 32), (0, 255, 0)).save(base)
    payload = batch_mod.run_set(bases={"side": base}, states=["walk", "attack"], root=tmp_path / "set", character=None,
                                duration=None, resolution="720p", key="green", concurrency=1, force=False, gap=0.0,
                                video_runner=video, body_height=300)
    assert payload["ok"] == 2 and payload["body_height"] == 300
    assert sorted(seen) == [("attack", 300), ("walk", 300)]
    # one strike, pinned: the attack's whole clip is its loop; a walk still searches its period
    assert cycles == {"attack": "pinned", "walk": "auto"}
    import argparse
    parser = argparse.ArgumentParser()
    batch_mod.add_arguments(parser)
    assert parser.parse_args(["--out-dir", "x", "--body-height", "320"]).body_height == 320
    assert parser.parse_args(["--out-dir", "x"]).body_height is None
