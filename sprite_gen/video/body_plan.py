# SPDX-License-Identifier: Apache-2.0
"""Body plan: what a clip's subject stands and walks on, so no sentence names a part the body lacks.

The engine's measured walk and idle sentences were measured on people, and some name what only a person
has: the Lite walk's arm swing (`batch.LITE_WALK_TEXT`), the arms in the Lite back-diagonal head hold
(`LITE_HEAD_TEXT`), the idle's two feet, chest, shoulders and arms (`MOTION_TEXT["idle"]`), and the front
or back walk's mid-step redraw, one foot under each hip and the arms swinging (`WALK_START_TEXT`), and the
front, back and diagonal still a clip starts from, both feet, chest, hips and shoes (`STILL_VIEW_TEXT`). Said of a
horse, they walk it on a person's legs or stand it up on two. The caller names the body plan (`parse`,
`--body-plan`): one for the character ("quadruped"), or one per figure in a scene ("the man=biped",
"the horse=quadruped").

| plan | stands on | the engine says |
|---|---|---|
| biped | two legs | the measured sentences, as without a body (the default) |
| quadruped | four legs | it stays on all four legs and never rises onto its hind legs |
| legless | no legs | it has no legs and never grows any; no mid-step redraw (no step to catch) |

The still a clip starts from is drawn with a view sentence (`batch.still_view_text`, `gen --direction`)
that counts a person's feet and names the chest, hips, shoulders and shoes; a body that is not one biped
gets it without them, ending in what it stands on (`still_text`).

No body, or one biped, is the 2.22.0 prompt to the byte (`tests/gen/test_prompt_freeze.py`). Any other
body gets the sentences without the parts it lacks and, after the motion sentence, what it stands on
(`text`); a scene of several figures says it per figure. Only the biped sentences were measured; the
others say no part the body lacks and are not yet measured on a clip.
"""

from __future__ import annotations

from dataclasses import dataclass

PLANS = ("biped", "quadruped", "legless")
SPEC_FORM = "'<biped|quadruped|legless>' for the character, or '<figure>=<plan>' per figure in a scene, e.g. 'the horse=quadruped'"
# What each figure of a scene stands on, after its name (`text`).
SCENE_TEXT = {
    "biped": "on two legs",
    "quadruped": "on all four legs, never rising onto its hind legs",
    "legless": "without legs, never growing any",
}
# One figure, after the motion sentence (`text`). A biped says nothing: the measured sentences are its own.
ONE_TEXT = {
    "quadruped": "It stays on all four legs, as in the image, and never rises onto its hind legs.",
    "legless": "It has no legs, as in the image, and never grows legs or feet.",
}
# One figure, at the end of the view sentence a still is drawn with (`still_text`): the still may be drawn
# from words alone, so it says what the body stands on rather than "as in the image".
STILL_ONE_TEXT = {
    "quadruped": "standing on all four legs, not rearing onto its hind legs",
    "legless": "resting on its base without legs, never growing any",
}


@dataclass(frozen=True)
class Body:
    """One figure's body plan, and the figure's name when the clip is a scene of several."""

    plan: str
    figure: str = ""


def parse(spec: str) -> Body:
    """'quadruped' -> Body("quadruped"); 'the horse=quadruped' -> Body("quadruped", "the horse")."""
    figure, plan = (s.strip() for s in spec.rsplit("=", 1)) if "=" in spec else ("", spec.strip())
    if plan.lower() not in PLANS or ("=" in spec and not figure):
        raise SystemExit(f"body plan: expected {SPEC_FORM}, got {spec!r}")
    return Body(plan=plan.lower(), figure=" ".join(figure.split()))


def parse_all(specs: list[str] | None) -> list[Body]:
    """Every `--body-plan`: one for the character, or one per figure, each named once."""
    bodies = [parse(s) for s in (specs or [])]
    if len(bodies) > 1 and not all(b.figure for b in bodies):
        raise SystemExit(f"body plan: a scene names each figure ({SPEC_FORM}); got {', '.join(map(repr, specs or []))}")
    figures = [b.figure.lower() for b in bodies if b.figure]
    if len(set(figures)) != len(figures):
        raise SystemExit(f"body plan: each figure is named once; got {', '.join(map(repr, specs or []))}")
    return bodies


def biped(bodies: list[Body] | None) -> bool:
    """Whether every figure stands on two legs (no body named is a biped): the measured sentences apply."""
    return all(b.plan == "biped" for b in bodies or [])


def legless(bodies: list[Body] | None) -> bool:
    """Whether a body is named and none of its figures has legs."""
    return bool(bodies) and all(b.plan == "legless" for b in bodies or [])


def scene(bodies: list[Body] | None) -> bool:
    return len(bodies or []) > 1


def text(bodies: list[Body] | None) -> str:
    """What the subject stands on, said after the motion sentence; "" for no body or one biped."""
    if not bodies or (not scene(bodies) and bodies[0].plan == "biped"):
        return ""
    if not scene(bodies):
        return ONE_TEXT[bodies[0].plan]
    return "Each figure keeps the body it has in the image: " + "; ".join(
        f"{b.figure} {SCENE_TEXT[b.plan]}" for b in bodies) + "."


def still_text(bodies: list[Body] | None) -> str:
    """What the subject stands on, at the end of a still's view sentence (lower case, no full stop); "" for
    no body or one biped."""
    if not bodies or (not scene(bodies) and bodies[0].plan == "biped"):
        return ""
    if not scene(bodies):
        return STILL_ONE_TEXT[bodies[0].plan]
    return "each figure with the body it has: " + "; ".join(f"{b.figure} {SCENE_TEXT[b.plan]}" for b in bodies)


def figures(bodies: list[Body] | None) -> str:
    """The figure names, as the caller wrote them (they are the caller's words in the prompt)."""
    return " ".join(b.figure for b in bodies or [] if b.figure)
