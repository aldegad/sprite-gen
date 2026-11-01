# SPDX-License-Identifier: Apache-2.0
"""How a motion plays is a request value (`video-set --play`, `video-prompt --play`, `sprite_gen.video.play`): a
named state the request gives no mode plays as its preset, exactly as before; a mode the request gives decides the
end-frame pin, the prompt's ending, the cut and the strip's metadata, whatever the motion is called. Synthetic
fixtures only."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from conftest import as_pinned
from sprite_gen.video import batch, clip_prompt, effect, loop, nonloop, play
from test_clip_prompt import PUNCH, punch_frames

# state -> (preset, pinned seen from the side, cut): the engine as it was before play modes, by state name alone.
PRESETS = {
    "idle": ("loop", True, "pinned"),
    "walk": ("loop", False, "auto"),
    "run": ("loop", False, "auto"),
    "jump": ("loop", False, "auto"),
    "attack": ("loop", True, "pinned"),
    "cheer": ("loop", False, "auto"),
    "wave": ("loop", False, "auto"),
    "dance": ("loop", False, "auto"),
    **{state: ("once-hold", False, "once") for state in nonloop.STATES},
    "effect": ("lifetime", True, "lifetime"),
}


@pytest.mark.parametrize("state", sorted(PRESETS))
def test_a_named_state_with_no_mode_plays_as_its_preset(state) -> None:
    mode, pinned, cut = PRESETS[state]
    assert play.preset(state) == mode and play.resolve(state, None) == mode
    assert batch.pins_last_frame(state, "side") is pinned and batch.cycle_for(state, "side") == cut
    for direction in ("side", "front", "back_diagonal"):
        assert batch.build_prompt(direction, state, "The knight") == batch.build_prompt(direction, state, "The knight", play=mode)
        assert (batch.build_prompt(direction, state, None, motion="It moves.")
                == batch.build_prompt(direction, state, None, motion="It moves.", play=mode))
        assert batch.pins_last_frame(state, direction) == batch.pins_last_frame(state, direction, mode)
    # the preset named in the request cuts the same, except that a loop asked for is never failed over to a single play
    assert batch.cycle_for(state, "side", mode) == ("periodic" if cut == "auto" else cut)
    record = clip_prompt.plan_prompt(direction="side", state=state)
    assert record["play"] == mode and record["cycle"] == cut


def test_a_pinned_gait_keeps_its_searched_cut() -> None:
    assert batch.pins_last_frame("walk", "back_diagonal") and batch.cycle_for("walk", "back_diagonal") == "auto"
    assert batch.cycle_for("walk", "back_diagonal", "loop") == "periodic"


@pytest.mark.parametrize("state,mode", [("effect", "loop"), ("death", "once-return"), ("hurt_hold", "loop"),
                                        ("walk", "once-hold"), ("idle", "lifetime"), ("run", "once-return"),
                                        ("punch", "bounce"), ("punch", "once")])
def test_a_mode_a_state_cannot_play_is_refused(state, mode) -> None:
    with pytest.raises(SystemExit, match="play mode is one of" if mode in ("bounce", "once") else f"{state} plays"):
        play.resolve(state, mode)
    with pytest.raises(SystemExit):
        clip_prompt.plan_prompt(direction="side", state=state, play=mode)


def test_once_return_is_asked_for_pinned_and_cut_whole() -> None:
    record = clip_prompt.plan_prompt(direction="side", state="punch", play="once-return", motion=PUNCH, character="The boxer")
    assert record["play"] == "once-return" and record["cycle"] == "once-return" and record["last_frame"] == "canvas"
    assert "end frame <dir>/canvas.png" in record["commands"]["clip"] and "--cycle once-return" in record["commands"]["loop"]
    prompt = record["prompt"]
    assert prompt.startswith(f"2D game sprite animation. {PUNCH} {batch.RETURN_TEXT} The boxer is seen from the exact side")
    assert prompt.endswith("Crisp, clean frames with no motion blur, no smears and no afterimages.")
    assert "evenly paced" not in prompt and "loops" not in prompt
    # no text of its own: the mode's sentence names the motion
    plain = clip_prompt.plan_prompt(direction="side", state="punch", play="once-return")["prompt"]
    assert "performs the 'punch' action once in place, then returns to the exact starting stance." in plain
    # a source with no end frame films it only when asked, and then searches the return
    with pytest.raises(SystemExit, match="no end frame"):
        clip_prompt.plan_prompt(direction="side", state="punch", play="once-return", last_frame=False)
    unpinned = clip_prompt.plan_prompt(direction="side", state="punch", play="once-return", last_frame=False, unpinned=True)
    assert unpinned["cycle"] == "one-shot" and unpinned["last_frame"] is None
    assert "the return is searched" in unpinned["warnings"][0]


def test_an_attack_played_once_return_is_the_attack_it_always_filmed() -> None:
    for direction in ("side", "front"):
        assert batch.build_prompt(direction, "attack", None, play="once-return") == batch.build_prompt(direction, "attack", None)
    assert batch.pins_last_frame("attack", "side", "once-return")
    assert batch.cycle_for("attack", "side", "once-return") == "once-return" and batch.cycle_for("attack", "side") == "pinned"


def test_a_named_state_played_another_way_takes_the_modes_sentence() -> None:
    cheer = batch.build_prompt("side", "cheer", None, play="once-return")
    assert "performs the 'cheer' action once in place" in cheer and "repeating" not in cheer
    assert batch.build_prompt("side", "jump", None, play="once-hold").count("performs the 'jump' action once and holds its final pose.") == 1


def test_once_hold_is_cut_at_its_held_pose() -> None:
    record = clip_prompt.plan_prompt(direction="side", state="block", play="once-hold", motion="It raises its shield and holds it.",
                                     end_frame=Path("blocked.png"))
    assert record["cycle"] == "once" and record["last_frame"] == "blocked.png"
    assert "It raises its shield and holds it." in record["prompt"] and nonloop.COMMON_TEXT in record["prompt"]
    assert not batch.pins_last_frame("block", "side", "once-hold")
    with pytest.raises(SystemExit, match="once-hold"):
        clip_prompt.plan_prompt(direction="side", state="block", play="once-return", end_frame=Path("blocked.png"))


def shield_frames(root: Path, *, back: bool = False, slide: int = 0) -> Path:
    """Keyed frames of a figure seen from the front, 48 at 24 fps, whose head, torso and feet fix its body box: a
    shield inside that box rises once from the waist to the chest and is held still for the last second. `back`
    brings it down again before the hold and `slide` moves the whole figure that many px sideways on the way, so the
    clip ends in its first drawing, slid."""
    root.mkdir(parents=True)
    for k in range(48):
        raised = (1 - abs(k - 12) / 12 if k < 24 else 0) if back else min(k, 23) / 23
        x, y = round(slide * min(k, 23) / 23), 55 - round(22 * raised)
        im = Image.new("RGBA", (96, 96))
        draw = ImageDraw.Draw(im)
        draw.ellipse((40 + x, 12, 56 + x, 28), fill=(235, 180, 130, 255))
        draw.rectangle((36 + x, 28, 60 + x, 62), fill=(110, 80, 160, 255))
        draw.rectangle((36 + x, 62, 44 + x, 82), fill=(80, 50, 110, 255))
        draw.rectangle((52 + x, 62, 60 + x, 82), fill=(80, 50, 110, 255))
        draw.rectangle((38 + x, y, 58 + x, y + 15), fill=(30, 190, 225, 255))
        im.save(root / f"frame-{k:04d}.png")
    return root


def cut_once_hold(tmp_path: Path, frames: Path, state: str) -> dict:
    """`frames` cut as `video-set` cuts an item of `state` played once-hold."""
    return loop.run_loop(frames, tmp_path / state, fps=24, state=state, min_len=None, max_len=None, n_out=None,
                         seam_max=loop.SEAM_RATIO_MAX, name=state, report_path=None,
                         cycle_mode=batch.cycle_for(state, "front", "once-hold"))


def test_a_once_hold_of_your_own_ends_on_a_new_drawing_inside_the_same_body_box(tmp_path) -> None:
    frames = shield_frames(tmp_path / "frames")
    first, last = Image.open(frames / "frame-0000.png"), Image.open(frames / "frame-0047.png")
    assert first.getbbox() == last.getbbox() and first.tobytes() != last.tobytes()
    report = cut_once_hold(tmp_path, frames, "shield_block")
    pose = report["cycle"]["terminal"]["pose"]
    assert report["cycle"]["kind"] == "once" and pose["profile"] == "drawing" and pose["departure"] > pose["departure_min"]
    assert report["strip"]["loop"] is False and report["strip"]["hold_last"] is True
    # a named terminal state keeps its own screen: the same clip as a cast has not changed its body geometry
    with pytest.raises(SystemExit, match="body height, aspect and floor position have returned"):
        cut_once_hold(tmp_path, frames, "cast")


@pytest.mark.parametrize("slide", [0, 4])
def test_a_once_hold_that_comes_back_to_its_first_drawing_is_refused(tmp_path, slide) -> None:
    with pytest.raises(SystemExit, match="ends in the starting pose"):
        cut_once_hold(tmp_path, shield_frames(tmp_path / "frames", back=True, slide=slide), "shield_block")


def test_lifetime_is_filmed_as_an_effect_whatever_its_name() -> None:
    text = "A white crescent arcs from left to right."
    record = clip_prompt.plan_prompt(direction="front", state="slash", play="lifetime", motion=text, character="A white slash")
    assert record["prompt"] == effect.prompt("A white slash", text)
    assert record["cycle"] == "lifetime" and record["last_frame"] == "black" and record["blend"] == "add"
    assert "--state effect" in record["commands"]["canvas"] and "--alpha-mode brightness" in record["commands"]["frames"]
    assert "--state slash --cycle lifetime" in record["commands"]["loop"]


def test_a_loop_asked_for_is_searched_as_a_loop_with_the_callers_text() -> None:
    prompt = batch.build_prompt("side", "type_at_desk", None, play="loop", motion="It types at a desk.")
    assert prompt.startswith("2D game sprite animation. It types at a desk. The character is seen from the exact side")
    assert prompt.endswith("Consistent, evenly paced motion so the animation loops.")
    assert batch.cycle_for("type_at_desk", "side", "loop") == "periodic" and batch.cycle_for("type_at_desk", "side") == "auto"


def stub_set(tmp_path, monkeypatch, *, bases, states, **kwargs):
    """`run_set` with every stage but the item's own choices stubbed: what each item asked for, and how it was cut."""
    seen: dict[str, dict] = {}

    def video_stub(image, prompt, out, report, **kw):
        seen.setdefault(out.parent.name, {}).update(prompt=prompt, last_frame=kw.get("last_frame"))
        out.write_bytes(b"clip")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    def frames_stub(clip, out, **kw):
        seen.setdefault(clip.parent.name, {})["alpha_mode"] = kw.get("alpha_mode")
        return {"fps": 24, "frames": 24, "alpha_zero_pct_min": 0, "alpha_zero_pct_max": 0, "keyed_dir": str(out)}

    def loop_stub(frames, out, **kw):
        seen.setdefault(out.parent.name, {})["cycle"] = kw["cycle_mode"]
        return {"cycle": {"kind": kw["cycle_mode"], "length": 24, "period_global": None, "ratio": 0},
                "resampled_seam_ratio": None, "held_fill": {"cells": 10, "poses": 10, "held_runs": [], "made": [], "before": {"cells": 10, "poses": 10, "held_runs": []}}, "n_out": 24, "gif": {"file": "g"}, "webp": {"file": "w"}, "strip": {"path": "s"}}

    monkeypatch.setattr(batch.frames_mod, "run_frames", frames_stub)
    monkeypatch.setattr(batch.loop_mod, "run_loop", loop_stub)
    monkeypatch.setattr(batch.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("right", {}))
    report = batch.run_set(bases=bases, states=states, root=tmp_path / "set", character=None, duration=None, resolution="480p",
                           key="green", concurrency=1, force=False, gap=0, video_runner=video_stub, align_cycles="off", **kwargs)
    return report, seen


def still(path: Path) -> Path:
    im = Image.new("RGB", (96, 96), (0, 255, 0))
    ImageDraw.Draw(im).rectangle((40, 20, 56, 86), fill=(200, 80, 30))
    im.save(path)
    return path


def test_video_set_takes_a_mode_and_a_text_per_state_or_per_item(tmp_path, monkeypatch) -> None:
    bases = {"side": still(tmp_path / "side.png"), "front": still(tmp_path / "front.png")}
    report, seen = stub_set(tmp_path, monkeypatch, bases=bases, states=["punch", "jump", "idle", "slash"],
                            play={"punch": "once-return", "jump": "loop", "slash": "lifetime"},
                            motion={"punch": PUNCH, "front-punch": "It punches straight at the viewer.", "slash": "A slash."})
    assert report["ok"] == 8, [r.get("error") for r in report["items"]]
    assert PUNCH in seen["side-punch"]["prompt"] and "straight at the viewer" in seen["front-punch"]["prompt"]
    assert {item: seen[item]["cycle"] for item in ("side-punch", "side-jump", "side-idle", "side-slash")} == {
        "side-punch": "once-return", "side-jump": "periodic", "side-idle": "pinned", "side-slash": "lifetime"}
    assert seen["side-punch"]["last_frame"].name == "canvas.png" and seen["side-jump"]["last_frame"] is None
    assert seen["side-slash"]["last_frame"].name == "canvas.end.png" and seen["side-slash"]["alpha_mode"] == "brightness"
    assert seen["side-slash"]["prompt"] == effect.prompt(None, "A slash.")
    rows = {r["item"]: r for r in report["items"]}
    assert rows["side-idle"]["play"] == "loop" and "motion" not in rows["side-idle"]
    assert rows["front-punch"]["motion"] == "It punches straight at the viewer."
    assert report["play"] == {"punch": "once-return", "jump": "loop", "slash": "lifetime"}


@pytest.mark.parametrize("kwargs,match", [
    ({"play": {"kick": "loop"}}, "--play kick"),
    ({"motion": {"side-kick": "It kicks."}}, "--motion side-kick"),
    ({"play": {"death": "loop"}}, "death plays once-hold only"),
    ({"play": {"punch": "once-return"}, "anchor": "feet", "end_frames": {"side-punch": Path("x.png")}}, "once-hold"),
])
def test_video_set_refuses_a_bad_mode_or_key_before_any_clip(tmp_path, monkeypatch, kwargs, match) -> None:
    called = []
    monkeypatch.setattr(batch, "run_item", lambda **kw: called.append(kw))
    with pytest.raises(SystemExit, match=match):
        batch.run_set(bases={"side": still(tmp_path / "side.png")}, states=["punch", "death"], root=tmp_path / "set",
                      character=None, duration=None, resolution="480p", key="green", concurrency=1, force=False, gap=0, **kwargs)
    assert called == []


@pytest.mark.parametrize("values", [["punch"], ["punch="], ["=once-return"], ["punch=loop", "punch=once-return"]])
def test_keyed_values_are_one_key_and_one_value_each(values) -> None:
    with pytest.raises(SystemExit, match="--play expects"):
        batch._parse_keyed(values, "--play")
    assert batch._parse_keyed(["punch = once-return", "side-punch=It punches = hard."], "--play") == {
        "punch": "once-return", "side-punch": "It punches = hard."}


def cut(tmp_path: Path, frames: Path) -> dict:
    return loop.run_loop(frames, tmp_path / "out", fps=24, state="punch", min_len=None, max_len=None, n_out=None,
                         seam_max=loop.SEAM_RATIO_MAX, name="punch", report_path=None, cycle_mode="once-return")


def test_a_once_return_that_does_not_come_back_is_refused(tmp_path) -> None:
    frames = punch_frames(tmp_path / "frames", (96, 96))
    reached = Image.open(frames / "frame-0017.png")
    for k in range(30, 36):
        reached.save(frames / f"frame-{k:04d}.png")
    with pytest.raises(SystemExit, match="does not return to its first pose"):
        cut(tmp_path, frames)
    assert json.loads((tmp_path / "out" / "punch.loop.report.json").read_text())["status"] == "failed"


# The binary's calls the held fill makes on this cut, by model (`rife.model_name()`): rife-v4.25's frame between held cells
# 31 to 33 shows a neighbour's picture again, so it is refused (`held`), tried at the group's other times and kept as filmed.
ONCE_RETURN_RUNS = {"rife-v4.25": (30, 44), "rife-v4.6": 36}


@pytest.mark.timeout(900)  # 36 to 44 calls of the binary at 96 x 96 (the held fill's frames between): seconds on a GPU, minutes on a CPU's Vulkan driver
def test_a_once_return_cut_keeps_both_ends_and_plays_once(tmp_path, rife_runs) -> None:
    report = cut(tmp_path, punch_frames(tmp_path / "frames", (96, 96), count=90))
    assert as_pinned(rife_runs(), ONCE_RETURN_RUNS), rife_runs()
    meta = report["strip"]
    assert report["cycle"]["kind"] == "once-return" and report["cycle"]["length"] == 90
    assert meta["loop"] is False and meta["hold_last"] is False and meta["returns"] is True
    # subsampled to the cell cap, the strip still starts and ends on the clip's first and last frames
    assert meta["subsampled"] and meta["source_cut"]["samples"][0] == 0 and meta["source_cut"]["samples"][-1] == 89
    assert 0 <= meta["events"][0]["frame"] <= meta["end_frame"] == meta["frames"] - 1
    for suffix, repeat in (("gif", None), ("webp", 1)):
        with Image.open(tmp_path / "out" / f"punch.{suffix}") as im:
            assert im.info.get("loop") == repeat
    with pytest.raises(SystemExit, match="cyclic state walk"):
        loop.run_loop(tmp_path / "frames" / "keyed", tmp_path / "walk", fps=24, state="walk", min_len=None, max_len=None,
                      n_out=None, seam_max=2, name="walk", report_path=None, cycle_mode="once-return")


def test_the_clis_take_the_play_mode(tmp_path) -> None:
    out = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "video-prompt", "--direction", "side", "--state", "punch",
                          "--play", "once-return", "--motion", PUNCH, "--json"], capture_output=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    record = json.loads(out.stdout)
    assert record["play"] == "once-return" and record["cycle"] == "once-return" and PUNCH in record["prompt"]
    refused = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "video-set", "--base", f"side={still(tmp_path / 's.png')}",
                              "--states", "death", "--play", "death=loop", "--out-dir", str(tmp_path / "set")],
                             capture_output=True, encoding="utf-8")
    assert refused.returncode != 0 and "death plays once-hold only" in refused.stderr
