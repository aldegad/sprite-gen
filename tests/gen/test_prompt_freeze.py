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

A sentence changed on purpose since, after a before-and-after comparison on the app's own model, is listed in
`MEASURED` with the words 2.22.0 sent: a frozen prompt that holds it is compared with the new sentence in its
place, and nothing else in that prompt, or in any other, may move.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

import prompt_freeze_table as table
from sprite_gen.video import batch

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "prompts-v2.22.0.json.gz"
GROUPS = ("clip", "video-prompt", "start-still", "still", "video-side")
# The two diagonal stills' view sentences as 2.22.0 said them: the turn, the head and the feet only, words a still
# that turns only its head meets. `STILL_VIEW_TEXT` now says what that angle shows of the chest or back and the
# legs (docs/video-pipeline.md).
DIAGONAL_STILLS_2_22_0 = {
    "front_diagonal": (
        "seen from a three-quarter front angle: the whole body and head turned about 45 degrees to the {facing}, halfway "
        "between facing the viewer and facing {facing}, the face looking the same way as the chest and the feet pointing "
        "toward the lower {facing}"
    ),
    "back_diagonal": (
        "seen from a three-quarter back angle: the whole body and head turned about 45 degrees away from the viewer toward "
        "the upper {facing}, halfway between facing away and facing {facing}, the face hidden and not looking back over the "
        "shoulder, and the feet pointing diagonally up and to the {facing} so the backs of the shoes face the viewer at an angle"
    ),
}
MEASURED = tuple((was.format(facing=facing), batch.still_view_text(view, facing))
                 for view, was in DIAGONAL_STILLS_2_22_0.items() for facing in ("right", "left"))


def _as_measured(prompt: str) -> str:
    """A frozen prompt with each measured sentence in its new words. `gen` starts the view sentence with a capital,
    so the first letter is left out of the match."""
    for was, now in MEASURED:
        prompt = prompt.replace(was[1:], now[1:])
    return prompt


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
    changed = [name for name in names if drawn[name] != _as_measured(frozen[name])]
    assert not changed, (f"{len(changed)} of {len(names)} {group} prompts differ from 2.22.0; first:\n"
                         + _first_difference(changed[0], _as_measured(frozen[changed[0]]), drawn[changed[0]]))


def test_only_the_diagonal_still_sentences_moved(frozen, drawn) -> None:
    """Each measured sentence is in every still `gen --direction` draws at its view, facing either way (twice in a
    facing regeneration), and in no clip, `video-prompt`, mid-step redraw or other view; those prompts, and only
    those, differ from 2.22.0, and the new words say the body and legs turn."""
    holds = {name for name, prompt in frozen.items() if any(was[1:] in prompt for was, _ in MEASURED)}
    views = {f"{view}@{facing}" for view in DIAGONAL_STILLS_2_22_0 for facing in ("right", "left")}
    assert {name.split("/")[2] for name in holds} == views
    assert all(name.startswith("still/") for name in holds) and len(holds) == 4 * 144
    assert {name for name in frozen if drawn[name] != frozen[name]} == holds
    for name in holds:
        angle = "front" if "/front_diagonal@" in name else "back"
        said = f"Seen from a three-quarter {angle} angle"
        assert frozen[name].count(said) == drawn[name].count(f"{said}: the whole body turned about 45 degrees")
        assert f"not a {angle} view with only the head turned" in drawn[name]


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


PREPARE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "prepare-rows-v2.32.0.json.gz"


@pytest.mark.parametrize("body_plan", [None, ["biped"]], ids=["no-body-plan", "biped"])
def test_every_sheet_row_with_no_body_plan_or_one_biped_is_2_32_0s_byte_for_byte(body_plan) -> None:
    """The rows `prepare` writes (`prepare_freeze_table.py`) were frozen at 2.32.0 when the body plan reached
    them: a run with no body plan, or `--body-plan biped`, writes every row prompt as 2.32.0 did."""
    import prepare_freeze_table as rows

    frozen = rows.read(PREPARE_FIXTURE)
    drawn = rows.draw(**({"body_plan": body_plan} if body_plan else {}))
    assert sorted(drawn) == sorted(frozen) and len(frozen) == 52
    changed = [name for name in frozen if drawn[name] != frozen[name]]
    assert not changed, (f"{len(changed)} of {len(frozen)} sheet rows differ from 2.32.0; first:\n"
                         + _first_difference(changed[0], frozen[changed[0]], drawn[changed[0]]))
