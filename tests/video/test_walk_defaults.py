# SPDX-License-Identifier: Apache-2.0
"""Two measured walk defaults (2026-10-02/03): a Lite walk gets a calming clause after the walk
sentence, and a head-steady one in the back-diagonal view; a front or back walk films from its
base still redrawn mid-step. Pro prompts and every other state stay byte-for-byte as they were."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.video import batch

LITE, PRO = "grok-imagine-video-1.5-lite", "grok-imagine-video-1.5"


def test_a_lite_walk_is_calmed_and_nothing_else_changes():
    for direction in ("side", "front", "back", "front_diagonal"):
        plain = batch.build_prompt(direction, "walk", "The knight")
        assert batch.build_prompt(direction, "walk", "The knight", model=PRO) == plain
        assert batch.build_prompt(direction, "walk", "The knight", model=LITE) == f"{plain} {batch.LITE_WALK_TEXT}"
    for state in ("run", "idle", "attack", "jump"):
        assert batch.build_prompt("side", state, "The knight", model=LITE) == batch.build_prompt("side", state, "The knight")


def test_a_lite_back_diagonal_walk_also_holds_its_head_after_the_calm_clause():
    plain = batch.build_prompt("back_diagonal", "walk", "The knight")
    lite = batch.build_prompt("back_diagonal", "walk", "The knight", model=LITE)
    assert lite == f"{plain} {batch.LITE_WALK_TEXT} {batch.LITE_HEAD_TEXT['back_diagonal']}"
    assert "ponytail" not in lite  # the measured sentence named the test character's ponytail; the engine's names hair


def test_a_callers_own_walk_is_not_calmed_but_the_head_still_holds():
    own = "The knight marches briskly in place, knees high."
    plain = batch.build_prompt("back_diagonal", "walk", "The knight", motion=own)
    lite = batch.build_prompt("back_diagonal", "walk", "The knight", motion=own, model=LITE)
    assert lite == f"{plain} {batch.LITE_HEAD_TEXT['back_diagonal']}"
    assert batch.build_prompt("side", "walk", "The knight", motion=own, model=LITE) == batch.build_prompt("side", "walk", "The knight", motion=own)


def test_only_a_front_or_back_walk_starts_mid_step():
    assert batch.starts_mid_step("walk", "front") and batch.starts_mid_step("walk", "back")
    for state, direction in (("walk", "side"), ("walk", "front_diagonal"), ("walk", "back_diagonal"), ("run", "front"), ("idle", "front")):
        assert not batch.starts_mid_step(state, direction)
    assert batch.walk_start_prompt("front", "green").endswith(batch.KEY_BACKGROUND_TEXT["green"])
    assert "caught mid-step while walking straight away from the viewer" in batch.walk_start_prompt("back")
    with pytest.raises(SystemExit, match="green or magenta"):
        batch.walk_start_prompt("front", "white")


def _still(path: Path, background: tuple[int, int, int]) -> Path:
    im = Image.new("RGB", (96, 128), background)
    for y in range(20, 110):
        for x in range(36, 60):
            im.putpixel((x, y), (120, 60, 40))
    im.save(path)
    return path


class Painter:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, base, prompt, out, report, *, log, provider=None):
        self.calls.append(prompt)
        Image.open(base).save(out)
        report.write_text(json.dumps({"prompt": prompt, "refs": [str(base)], "provider": provider or "stub"}))
        return 0


def test_the_start_still_is_redrawn_once_on_the_bases_key_and_reused(tmp_path):
    base = _still(tmp_path / "front.png", (0, 255, 0))
    item = tmp_path / "front-walk"
    item.mkdir()
    paint = Painter()
    still, record = batch.walk_start_still(base, "front", item, key="auto", force=False, runner=paint)
    assert still == item / "walk-start.png" and record["key"] == "green" and record["reused"] is False
    assert paint.calls == [batch.walk_start_prompt("front", "green")]
    _, again = batch.walk_start_still(base, "front", item, key="auto", force=False, runner=paint)
    assert again["reused"] is True and len(paint.calls) == 1
    batch.walk_start_still(base, "front", item, key="auto", force=True, runner=paint)
    assert len(paint.calls) == 2


def test_a_base_on_no_chroma_key_is_refused_naming_as_given(tmp_path):
    base = _still(tmp_path / "front.png", (255, 255, 255))
    with pytest.raises(SystemExit, match="--walk-start as-given"):
        batch.walk_start_still(base, "front", tmp_path, key="auto", force=False, runner=Painter())


def test_a_failed_redraw_stops_the_item(tmp_path):
    base = _still(tmp_path / "back.png", (255, 0, 255))
    with pytest.raises(SystemExit, match="mid-step start still for the back walk was not drawn"):
        batch.walk_start_still(base, "back", tmp_path, key="auto", force=False, runner=lambda *a, **k: 1)
