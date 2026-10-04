# SPDX-License-Identifier: Apache-2.0
"""Without `--handed`, every prompt string the engine sends is 2.22.0's, to the byte.

`prompt_freeze_table.py` draws each prompt a character with no handed item is drawn or filmed from — a clip
(built-in or `--motion`, every model and pin), `video-prompt`, a still (view, facing, reference, key line,
layout guide, the `--facing-fix regen` regeneration), `video --direction side` and the mid-step redraw — and
`tests/fixtures/prompts-v2.22.0.json.gz` is that table drawn from the 2.22.0 tag's source (`013355b`). These
tests draw it again against this tree.

What is compared is the prompt strings only. The notes and warnings a verb prints beside a prompt (`gen`
stderr and `extra.prompt_notes`, `video-prompt` `warnings` and `notes`, `video-set` `prompt_notes`) are not
part of the freeze and may differ.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

import prompt_freeze_table as table

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "prompts-v2.22.0.json.gz"
GROUPS = ("clip", "video-prompt", "start-still", "still", "video-side")


@pytest.fixture(scope="module")
def frozen() -> dict[str, str]:
    return table.read(FIXTURE)


@pytest.fixture(scope="module")
def drawn() -> dict[str, str]:
    return table.draw()


def _first_difference(name: str, was: str, now: str) -> str:
    diff = difflib.unified_diff(was.splitlines(), now.splitlines(), "2.22.0", "now", lineterm="", n=0)
    return f"{name}:\n" + "\n".join(diff)


def test_the_table_is_the_one_the_fixture_was_drawn_from(frozen, drawn) -> None:
    assert sorted(drawn) == sorted(frozen)
    assert {name.split("/")[0] for name in frozen} == set(GROUPS)
    assert len(frozen) > 6000


@pytest.mark.parametrize("group", GROUPS)
def test_every_prompt_string_without_handed_is_2_22_0s_byte_for_byte(group, frozen, drawn) -> None:
    names = [name for name in frozen if name.split("/")[0] == group]
    changed = [name for name in names if drawn[name] != frozen[name]]
    assert not changed, (f"{len(changed)} of {len(names)} {group} prompts differ from 2.22.0; first:\n"
                         + _first_difference(changed[0], frozen[changed[0]], drawn[changed[0]]))


def test_the_default_walk_keeps_its_treadmill_and_screen_sentences(frozen, drawn) -> None:
    """The built-in walk and run paragraphs are frozen with everything else: "as if on a treadmill" (kept in
    place for a body without legs to read, a slime's bounce included), "without moving across the screen" in
    the gait sentence as well as the frame sentence, and the view sentence after the gait sentence."""
    for name, prompt in frozen.items():
        if name.startswith("clip/") and name.split("/")[2] in ("walk", "run") and "/built-in/" in name:
            assert "as if on a treadmill" in prompt and drawn[name] == prompt, name
    side = drawn["clip/side@right/walk/built-in/default/pinned=None/no-character"]
    assert "walks naturally in place, as if on a treadmill, without moving across the screen." in side
    assert "The character is seen from the exact side, facing right." in side
    front = drawn["clip/front@right/walk/built-in/default/pinned=None/no-character"]
    assert "The character is seen from the front" in front
