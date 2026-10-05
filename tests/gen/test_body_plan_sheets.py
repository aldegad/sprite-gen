# SPDX-License-Identifier: Apache-2.0
"""A sheet of a horse does not swing its arms, and a horse's attack is not made with its hands (`--body-plan`).

The clip's walk, idle and still took a body plan in 2.32.0; the sheet rows `prepare` writes and the clip's
attack did not. The walk and run rows moved "body, arm, leg", the front walks "alternating leg, arm,
shoulder", the wave "arm pose only ... hand tilted", every row's anchor lock spent its motion on "arm
counter-swing" and turned "the body, feet, shoulders", the diagonal rows read the facing by "shoulder overlap,
hand/foot placement", and the attack struck "bare hands only if it holds nothing" with "one hand stays one
hand". A quadruped, a body without legs and a scene of a man leading a horse each get rows and an attack with
no part they lack; no body plan, or one biped, keeps every prompt byte for byte (`test_prompt_freeze.py`).
Subjects are synthetic.
"""

from __future__ import annotations

import itertools
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import prepare_freeze_table as table
from sprite_gen.gen import prepare
from sprite_gen.video import batch, body_plan as bp, clip_prompt

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "prepare-rows-v2.32.0.json.gz"
LITE, PRO = "grok-imagine-video-1.5-lite", "grok-imagine-video-1.5"
BODIES = {
    "quadruped": (["quadruped"], "A brown horse"),
    "legless": (["legless"], "A green slime"),
    "scene": (["the man=biped", "the horse=quadruped"], "A man leading a brown horse by its reins"),
}
# Words that name a part only a person has, or count a person's feet (as `tests/video/test_body_plan.py`), and a fist.
PERSON = re.compile(r"\b(arms?|hands?|fists?|wrists?|elbows?|shoulders?|chest|hips?|hip-width|knees?|both feet|one foot|"
                    r"the other foot|shoes?|heels?|toes?)\b", re.IGNORECASE)
# A body without legs has no legs or feet to step on, apart from the sentence saying it has none.
LEGS = re.compile(r"\b(legs?|feet|foot)\b", re.IGNORECASE)


def _named(text: str, specs: list[str]) -> list[str]:
    bodies = bp.parse_all(specs)
    words = {m.group(0).lower() for m in PERSON.finditer(text)}
    if bp.legless(bodies):
        words |= {m.group(0).lower() for m in LEGS.finditer(text.replace(bp.text(bodies), ""))}
    return sorted(words)


@pytest.fixture(scope="module")
def rows() -> dict[str, dict[str, str]]:
    return {name: table.draw(body_plan=specs) for name, (specs, _) in BODIES.items()}


@pytest.mark.parametrize("body", list(BODIES))
def test_no_sheet_row_names_a_part_the_body_lacks(body, rows) -> None:
    """Every row `prepare` writes — each state with a sentence of its own, the default states, a state with
    none, with and without a base image, and a direction-contract run — and each says what it stands on."""
    specs, _ = BODIES[body]
    drawn = rows[body]
    assert sorted(drawn) == sorted(table.read(FIXTURE))
    assert not {name: words for name, text in drawn.items() if (words := _named(text, specs))}
    assert all(bp.text(bp.parse_all(specs)) in text for text in drawn.values())


def _every_clip(body_plan, character):
    """Each clip prompt for every state the engine has a sentence for, and one it has none for: built-in and a
    caller's paragraph, every view and model; and the video-prompt record."""
    for view, state, model in itertools.product(batch.VIEW_TEXT, (*batch.MOTION_TEXT, "kick"), (None, PRO, LITE)):
        yield f"{view}/{state}/{model}", batch.build_prompt(view, state, character, model=model, body_plan=body_plan)
        yield (f"{view}/{state}/{model}/motion",
               batch.build_prompt(view, state, character, motion="It strikes once, about 1 s.", model=model,
                                  body_plan=body_plan))
    for view, state in itertools.product(batch.VIEW_TEXT, batch.MOTION_TEXT):
        yield f"video-prompt/{view}/{state}", clip_prompt.plan_prompt(
            direction=view, state=state, character=character, body_plan=body_plan)["prompt"]


@pytest.mark.parametrize("body", list(BODIES))
def test_no_clip_prompt_of_any_state_names_a_part_the_body_lacks(body) -> None:
    specs, character = BODIES[body]
    named = {name: _named(text, specs) for name, text in _every_clip(bp.parse_all(specs), character)}
    assert len(named) > 250
    assert not {name: words for name, words in named.items() if words}


def test_without_a_body_plan_the_v2_32_rows_and_attack_did_name_a_persons_parts() -> None:
    """What the report was about, as 2.32.0 said it to every body."""
    frozen = table.read(FIXTURE)
    assert "body, arm, leg, hair" in frozen["every-state/base/walk.txt"]
    assert "alternating leg, arm, shoulder" in frozen["every-state/base/frontwalk.txt"]
    assert "alternating leg, arm, shoulder" in frozen["every-state/base/45_frontwalk.txt"]
    assert "hand tilted" in frozen["every-state/base/wave.txt"]
    assert "friendly hand wave gesture" in frozen["default-states/base/wave.txt"]
    assert "arm counter-swing" in frozen["every-state/base/jump.txt"]
    assert "shoulder overlap, hand/foot placement" in frozen["every-state/base/running-front-right.txt"]
    attack = batch.build_prompt("side", "attack", "A brown horse")
    assert "bare hands only if it holds nothing" in attack and "one hand stays one hand" in attack
    assert "one hand stays one hand" in batch.build_prompt("side", "attack", "A brown horse", motion="It bites.")


def test_a_horse_attacks_with_what_it_holds_or_its_own_body() -> None:
    horse = bp.parse_all(["quadruped"])
    assert batch.build_prompt("side", "attack", "A brown horse", body_plan=horse) == (
        "2D game sprite animation. A brown horse performs one melee attack with what it is already holding, or with "
        "its own body if it holds nothing, keeping every piece of its gear and outfit exactly as drawn: a windup "
        "(about 0.5 s), one clean strike in front (about 0.25 s), a held impact pose (about 0.3 s), then a recovery "
        "to the exact starting stance (about 0.5 s). Anything it holds or carries stays exactly as shown in the "
        "image, held the same way, and nothing is dropped or moved to another place. A part the motion does not use "
        "stays where it is drawn, with anything it holds, and the body keeps facing the same direction without "
        "turning. It stays on all four legs, as in the image, and never rises onto its hind legs. The character is "
        "seen from the exact side, facing right. Stays centered in the frame and does not move across the screen; "
        "the body, hair and anything it holds always stay fully inside the frame with margin. Camera completely "
        "locked, no zoom, no pan, no reframing. The background stays a perfectly flat, pure chroma-key fill for the "
        "whole clip — no shadows, no ground line, no particles, no lighting changes, no effects. Keep the design, "
        "colors and proportions exactly as in the image. Crisp, clean frames with no motion blur, no smears and no "
        "afterimages."
    )
    told = batch.build_prompt("side", "attack", "A brown horse", motion="It bites.", body_plan=horse)
    assert told.startswith(f"2D game sprite animation. It bites. {bp.ONE_TEXT['quadruped']} "
                           f"{batch.HOLD_TEXT_ANY_BODY['attack']} A brown horse is seen")


def test_a_horse_walk_row_and_wave(rows) -> None:
    drawn = rows["quadruped"]
    walk = drawn["every-state/base/walk.txt"]
    assert ("- Show locomotion through the movement of the body, whatever it moves on, loose parts such as hair, a "
            "mane or a tail, and props only.\n") in walk
    assert walk.index("- Do not draw speed lines") < walk.index(f"- {bp.ONE_TEXT['quadruped']}\n") < walk.index(
        "Transparency and artifact rules:")
    assert ("Animation action: friendly wave gesture lifting one side; the waving side changes clearly while the "
            "body stays planted.") in drawn["default-states/base/wave.txt"]
    # a state with no sentence of its own still says what the body stands on
    assert f"State-specific requirements:\n- {bp.ONE_TEXT['quadruped']}\n" in drawn["every-state/base/cheer.txt"]


def _prepare_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "sprite_gen.cli", "prepare", *args], text=True, capture_output=True)


def test_the_sheet_cli_takes_records_and_carries_the_body_plan(tmp_path) -> None:
    first = tmp_path / "first"
    result = _prepare_cli("--out-dir", str(first), "--character-id", "horse", "--description", "A brown horse",
                          "--request-json", json.dumps({"states": {"walk": {"frames": 6}, "wave": {"frames": 4}}}),
                          "--body-plan", "the man=biped", "--body-plan", "the horse=quadruped")
    assert result.returncode == 0, result.stderr
    request = json.loads((first / "sprite-request.json").read_text())
    assert request["body_plan"] == [{"plan": "biped", "figure": "the man"}, {"plan": "quadruped", "figure": "the horse"}]
    assert request["states"]["wave"]["action"] == prepare.DEFAULT_ACTIONS_ANY_BODY["wave"]
    # the recorded request, reused, carries its body plan: same rows, nothing dropped
    again = tmp_path / "again"
    result = _prepare_cli("--out-dir", str(again), "--character-id", "horse", "--description", "A brown horse",
                          "--request", str(first / "sprite-request.json"))
    assert result.returncode == 0 and "body_plan" not in result.stderr, result.stderr
    assert json.loads((again / "sprite-request.json").read_text())["body_plan"] == request["body_plan"]
    for prompt in (first / "prompts").rglob("*.txt"):
        assert (again / "prompts" / prompt.relative_to(first / "prompts")).read_text() == prompt.read_text()
    # the CLI's body plan overrides the request's
    legless = tmp_path / "legless"
    prepare.run(out_dir=legless, character_id="slime", request=first / "sprite-request.json", body_plan=["legless"])
    assert json.loads((legless / "sprite-request.json").read_text())["body_plan"] == [{"plan": "legless", "figure": ""}]
    # without one, the request has no body_plan key at all
    plain = tmp_path / "plain"
    prepare.run(out_dir=plain, character_id="fox")
    assert "body_plan" not in json.loads((plain / "sprite-request.json").read_text())


@pytest.mark.parametrize("args, match", [
    (["--body-plan", "horse"], "expected"),
    (["--body-plan", "quadruped", "--body-plan", "the man=biped"], "names each figure"),
    (["--request-json", json.dumps({"body_plan": "quadruped"})], "must be a list"),
])
def test_a_sheet_body_plan_is_refused_before_the_run_dir_is_written(tmp_path, args, match) -> None:
    result = _prepare_cli("--out-dir", str(tmp_path / "run"), "--character-id", "horse", *args)
    assert result.returncode != 0 and re.search(match, result.stderr), result.stderr
    assert not (tmp_path / "run").exists()
