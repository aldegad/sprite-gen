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

The far side of a side view is hidden behind the body, except for what swings out from behind it:
an arm or a leg (`limb`). A walking character's far arm comes forward of the body with every step,
and what it wears shows then. An item on a far limb is therefore drawn and filmed as showing
whenever that limb is out in front of the body, and the near limb stays bare. Said as hidden, and
never named in the clip, a watch on the far wrist was in no frame of a walk at all (2.22.0).
"""

from __future__ import annotations

from dataclasses import dataclass

VIEWS = ("side", "front", "back", "front_diagonal", "back_diagonal")
# Views turned toward a side of the picture: each is drawn facing right or left.
LATERAL_VIEWS = frozenset({"side", "front_diagonal", "back_diagonal"})
SIDES = ("left", "right")
# Body parts on a limb that swings with a step: in a side view the far one comes out in front of the body.
ARM_WORDS = frozenset({"wrist", "hand", "arm", "forearm", "elbow", "palm", "finger", "fingers", "thumb", "knuckles"})
LEG_WORDS = frozenset({"ankle", "foot", "leg", "knee", "shin", "calf", "thigh", "heel", "toes"})
# A side walk or run with an item on an arm: the arms are said to swing, never held still. An arm held at
# the body keeps the item off the other arm but walks stiffly, and a far arm that does not swing never
# brings its item into view.
ARMS_SWING_TEXT = "Both arms swing back and forth with each step, opposite to the legs."
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


def limb(part: str) -> str | None:
    """"arm" or "leg" when `part` is on a limb that swings with a step ("wrist", "upper arm", "ankle"), else None
    (a side of the head, a shoulder, a hip: on the far side these stay behind the body)."""
    words = set(part.lower().split())
    return "arm" if words & ARM_WORDS else "leg" if words & LEG_WORDS else None


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


def _none(h: Handed, items: list[Handed]) -> str:
    """How the other side's part is said to lack `h`'s item: "none", or "none of it" when another item is on
    that part, so the two sentences are not read against each other."""
    return "none of it" if any(o is not h and (o.side, o.part) == (h.other, h.part) for o in items) else "none"


def _view_clause(h: Handed, view: str, facing: str | None, items: list[Handed]) -> str:
    at = placement(h.side, view, facing)
    none = _none(h, items)
    if view == "side":
        if at["depth"] == "near":
            return (f"Facing {facing}, the character's own {h.side} side is toward the viewer: its {h.where()} is the near "
                    f"one, nearer the viewer, and {h.item} on it shows clearly.")
        lead = (f"Facing {facing}, the character's own {h.other} side is toward the viewer: the near {h.part or 'side'}, on the "
                f"side nearer the viewer, is its own {h.where(h.other)}, with {none}; the {h.where()} with {h.item} is on the far ")
        swings = limb(h.part)
        if swings:
            # the far limb is in view whenever it is forward of the body, and its item with it
            return (f"{lead}{swings}. Wherever the far {swings} reaches out in front of the body, clear of its outline, "
                    f"{h.item} shows whole on it; where the body covers that {swings}, it is hidden.")
        return f"{lead}side, behind the body, so {h.item} is hidden or at most a sliver of it shows."
    lead = {"front": "Seen from the front", "back": "Seen from behind"}.get(view, "In this view")
    if at["depth"] is None:
        return (f"{lead}, the character's own {h.where()} is the one at the {at['picture']} of the picture, so {h.item} is "
                f"drawn at the {at['picture']} of the picture.")
    near = "nearer the viewer and fully visible" if at["depth"] == "near" else "on the far side of the body"
    other_picture = "left" if at["picture"] == "right" else "right"
    return (f"{lead}, the character's own {h.where()} is the one at the {at['picture']} of the picture, {near}, and it has "
            f"{h.item}; the {h.part or 'side'} at the {other_picture} of the picture is its own {h.where(h.other)}, with {none}.")


def _names(group: list[Handed]) -> str:
    names = [h.item for h in group]
    text = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return text[0].upper() + text[1:]


def _clip_clause(group: list[Handed], view: str, facing: str | None, *, gait: bool, other_bare: bool) -> str:
    """The items on one part of one side (`group`), in a clip. The clip starts from a still already drawn with
    them in place, so the sentence anchors to the image and says them positively, once, where they show.

    A side view's far limb in a walk or run (`gait`) shows what it wears each time it swings forward, and the
    sentence says so: left unnamed, a watch on the far wrist was in no frame of the walk. The near limb is said
    bare (`other_bare`: nothing of the caller's is on it), never as a list of what it must not wear — a clip
    prompt that lists that draws it there. Where nothing brings the far side into view (a part that does not
    swing, a state that does not step) the items are not named at all, only that the near part stays bare.
    """
    h = group[0]
    at = placement(h.side, view, facing)
    part = h.part or "side"
    stay, they = ("stays", "it") if len(group) == 1 else ("stay", "them")
    swings = limb(h.part) if view == "side" else None
    if swings and gait:
        if at["depth"] == "far":
            bare = f"; the {swings} nearer the viewer stays bare, exactly as in the image" if other_bare else ""
            return (f"{_names(group)} {stay} on the {part} that wears {they} in the image, on the {swings} on the far side "
                    f"of the body, and {'shows' if len(group) == 1 else 'show'} each time that {swings} swings forward{bare}.")
        bare = (f"; the other {swings}, on the far side of the body, stays bare both where it shows behind the body and "
                f"where it swings out in front of it") if other_bare else ""
        return (f"{_names(group)} {stay} on the {part} that wears {they} in the image, on the {swings} nearer the "
                f"viewer{bare}.")
    if view == "side" and at["depth"] == "far":
        # "nearer the viewer", not "in front of the body": mid-stride, the far arm is the one in front of the body
        return f"The {part} nearer the viewer stays bare, exactly as in the image, for the whole clip." if other_bare else ""
    if view == "side":
        where = f"the {part} nearer the viewer"
    elif at["depth"] is None:
        where = f"the {part} at the {at['picture']} of the picture"
    else:
        near = "nearer the viewer" if at["depth"] == "near" else "on the far side of the body"
        where = f"the {part} at the {at['picture']} of the picture, {near},"
    return f"{_names(group)} {stay} on {where} for the whole clip, exactly as in the image, and on no other {part}."


def first_frame_needs(items: list[Handed], view: str, facing: str | None = None, *, gait: bool = False) -> list[str]:
    """What a clip's first frame has to show for the handedness sentences to hold. A clip keeps what its first
    frame shows and makes up what it hides, so a walk or run whose far limb wears an item starts from a still
    with that limb out in front of the body and the item in view."""
    validate_view(view, facing)
    if view != "side" or not gait:
        return []
    return [f"{h.item} is on the far {limb(h.part)} in this view, and the clip shows it only if its first frame does: start "
            f"from a still drawn mid-stride, the far {limb(h.part)} out in front of the body with {h.item} in view and the "
            f"near {limb(h.part)} swung back"
            for h in items if limb(h.part) and placement(h.side, view, facing)["depth"] == "far"]


def text(items: list[Handed], view: str, facing: str | None = None, *, clip: bool = False, gait: bool = False) -> str:
    """The handedness sentences for drawing `view` (a still) or filming it (`clip`). A still: per item, which own
    side it is on and nowhere else, where that side is in this view, and that the sentences outrank a picture.
    A clip: where the items stay, anchored to its first frame (`_clip_clause`); `gait` says the clip is a walk
    or run, whose far limb swings into view."""
    validate_view(view, facing)
    if clip:
        groups: dict[tuple[str, str], list[Handed]] = {}
        for h in items:
            groups.setdefault((h.side, h.part), []).append(h)
        clauses = [_clip_clause(group, view, facing, gait=gait, other_bare=(group[0].other, part) not in groups)
                   for (_, part), group in groups.items()]
        if gait and view == "side" and any(limb(h.part) == "arm" for h in items):
            # the far arm's item shows only if the arms swing: said outright, once, before where the items are
            clauses.insert(0, ARMS_SWING_TEXT)
        return " ".join(clause for clause in clauses if clause)
    out = []
    for h in items:
        out.append(f"{h.item[0].upper()}{h.item[1:]} is on the character's own {h.where()} only; the "
                   f"{h.where(h.other)} has {_none(h, items)}, and it never moves to the other side.")
        out.append(_view_clause(h, view, facing, items))
    if items:
        # a still is often drawn from pictures that disagree with the item's side
        out.append("These sentences decide which side each item is on, whatever an attached picture shows.")
    return " ".join(out)
