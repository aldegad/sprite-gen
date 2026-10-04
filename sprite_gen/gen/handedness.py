# SPDX-License-Identifier: Apache-2.0
"""Handedness: which of the character's own sides an asymmetric item is on, seen from each view.

A watch on one wrist, a pin on one side of the head, a bag on one shoulder: turned over, the
picture puts the item on the other side. A character like that has its left-facing views drawn,
not mirrored, and each drawing is told where the item goes. The caller names the item and its
side once (`parse`: "the black smartwatch=left wrist"); the engine works out where that side is in
each view (`placement`) and says it in the still and clip prompts (`text`).

Where the character's own left side is, by view and facing (the side view and the three-quarter
views turn to `facing`; front and back have none):

| view | facing right | facing left |
|---|---|---|
| front | the picture's right | (same) |
| back | the picture's left | (same) |
| side | the far side, behind the body | the near side, toward the viewer |
| front_diagonal | the picture's right, on the far side | the picture's right, on the near side |
| back_diagonal | the picture's left, on the far side | the picture's left, on the near side |

A view turned to the right shows the character's right side; turned to the left, its left. The
own right side is the mirror of every row. A side view puts both arms over the middle of the body,
so it has no picture side, only near and far.
"""

from __future__ import annotations

from dataclasses import dataclass

VIEWS = ("side", "front", "back", "front_diagonal", "back_diagonal")
# Views turned toward a side of the picture: each is drawn facing right or left.
LATERAL_VIEWS = frozenset({"side", "front_diagonal", "back_diagonal"})
SIDES = ("left", "right")
SPEC_FORM = "'<item>=<left|right> [body part]', e.g. 'the black smartwatch=left wrist'"


@dataclass(frozen=True)
class Handed:
    """One asymmetric item: what it is, the character's own side it is on, and where on that side."""

    item: str
    side: str
    part: str = ""

    def where(self, side: str | None = None) -> str:
        side = side or self.side
        return f"{side} {self.part}" if self.part else f"{side} side"

    @property
    def other(self) -> str:
        return "right" if self.side == "left" else "left"


def parse(spec: str) -> Handed:
    """'<item>=<left|right> [body part]' -> Handed. The side is the character's own, not the picture's."""
    if "=" not in spec:
        raise SystemExit(f"handedness: expected {SPEC_FORM}, got {spec!r}")
    item, rest = (s.strip() for s in spec.rsplit("=", 1))
    words = rest.split()
    if not item or not words or words[0].lower() not in SIDES:
        raise SystemExit(f"handedness: expected {SPEC_FORM}, got {spec!r}")
    return Handed(item=" ".join(item.split()), side=words[0].lower(), part=" ".join(words[1:]))


def parse_all(specs: list[str] | None) -> list[Handed]:
    return [parse(s) for s in (specs or [])]


def validate_view(view: str, facing: str | None) -> None:
    if view not in VIEWS:
        raise SystemExit(f"handedness: view must be one of {', '.join(VIEWS)}, got {view!r}")
    if view in LATERAL_VIEWS and facing not in SIDES:
        raise SystemExit(f"handedness: the {view} view is turned right or left; say which (facing right|left)")


def placement(side: str, view: str, facing: str | None = None) -> dict[str, str | None]:
    """Where the character's own `side` is in `view`: `picture` (left / right of the picture, None in
    a side view) and `depth` (near / far from the viewer, None in a front or back view)."""
    if side not in SIDES:
        raise SystemExit(f"handedness: side must be left or right, got {side!r}")
    validate_view(view, facing)
    flip = {"left": "right", "right": "left"}
    # the own left side; the own right side is its mirror
    picture = {"front": "right", "front_diagonal": "right", "back": "left", "back_diagonal": "left"}.get(view)
    depth = None if view not in LATERAL_VIEWS else ("near" if facing == "left" else "far")
    if side == "right":
        picture = flip[picture] if picture else None
        depth = {"near": "far", "far": "near"}[depth] if depth else None
    return {"picture": picture, "depth": depth}


def _view_clause(h: Handed, view: str, facing: str | None) -> str:
    at = placement(h.side, view, facing)
    if view == "side":
        if at["depth"] == "near":
            return (f"Facing {facing}, the character's own {h.side} side is toward the viewer: its {h.where()} is the near "
                    f"one, in front of the body, and {h.item} on it shows clearly.")
        return (f"Facing {facing}, the character's own {h.other} side is toward the viewer: the near {h.part or 'side'}, in "
                f"front of the body, is its own {h.where(h.other)}, with none; the {h.where()} with {h.item} is on the far "
                f"side, behind the body, so {h.item} is hidden or at most a sliver of it shows.")
    lead = {"front": "Seen from the front", "back": "Seen from behind"}.get(view, "In this view")
    if at["depth"] is None:
        return (f"{lead}, the character's own {h.where()} is the one at the {at['picture']} of the picture, so {h.item} is "
                f"drawn at the {at['picture']} of the picture.")
    near = "nearer the viewer and fully visible" if at["depth"] == "near" else "on the far side of the body"
    other_picture = "left" if at["picture"] == "right" else "right"
    return (f"{lead}, the character's own {h.where()} is the one at the {at['picture']} of the picture, {near}, and it has "
            f"{h.item}; the {h.part or 'side'} at the {other_picture} of the picture is its own {h.where(h.other)}, with none.")


def _clip_clause(h: Handed, view: str, facing: str | None) -> str:
    """One item in a clip. The clip starts from a still already drawn with the item in place, so the sentence
    anchors to the image and says the item positively, once, where it shows. Where it is hidden (the far side of
    a side view) the item is not named at all, only that the near part stays bare as drawn: a clip prompt that
    names a hidden item, and lists what the near part must not wear, draws it on the near part."""
    at = placement(h.side, view, facing)
    part = h.part or "side"
    if view == "side" and at["depth"] == "far":
        return f"The {part} in front of the body stays bare, exactly as in the image, for the whole clip."
    if view == "side":
        where = f"the {part} in front of the body"
    elif at["depth"] is None:
        where = f"the {part} at the {at['picture']} of the picture"
    else:
        near = "nearer the viewer" if at["depth"] == "near" else "on the far side of the body"
        where = f"the {part} at the {at['picture']} of the picture, {near}"
    item = h.item[0].upper() + h.item[1:]
    return f"{item} stays on {where} for the whole clip, exactly as in the image, and on no other {part}."


def text(items: list[Handed], view: str, facing: str | None = None, *, clip: bool = False) -> str:
    """The handedness sentences for drawing `view` (a still) or filming it (`clip`). A still: per item, which own
    side it is on and nowhere else, where that side is in this view, and that the sentences outrank a picture.
    A clip: where the item stays, anchored to its first frame (`_clip_clause`)."""
    validate_view(view, facing)
    if clip:
        return " ".join(_clip_clause(h, view, facing) for h in items)
    out = []
    for h in items:
        out.append(f"{h.item[0].upper()}{h.item[1:]} is on the character's own {h.where()} only; the "
                   f"{h.where(h.other)} has none, and it never moves to the other side.")
        out.append(_view_clause(h, view, facing))
    if items:
        # a still is often drawn from pictures that disagree with the item's side
        out.append("These sentences decide which side each item is on, whatever an attached picture shows.")
    return " ".join(out)
