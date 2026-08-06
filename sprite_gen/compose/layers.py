# SPDX-License-Identifier: Apache-2.0
"""Layer-track contract — rig profiles, track kinds, and composite stack validation.

This module is the declaration half of the optional layer feature: it owns the
vocabulary (`PROFILES`, `TRACKS`) and validates a `sprite-request.json` that
declares `rig` / `states.<state>.track` / `layers`. It is filesystem-free and
deterministic — the same request always produces the same error list, in the same
order — so both the composer and the CLI can validate before touching a run dir.

Boundary (SoC): everything here is answerable from the request alone. Run-dir
facts — do the declared frames exist, is a mask file the right size, does an
aligned element clip — belong to the composer, not here.

Behavior contract: `docs/layer-tracks.md`. A request that declares none of these
keys is not a layer run and validates trivially, so existing runs are untouched.
"""

from __future__ import annotations

import re
from typing import Any

from sprite_gen.spec.layout import state_frame_total

# Character type profiles. `required` landmarks must be declared on every pool
# frame of every row of a rig run; `reserved` names are the recommended
# vocabulary for optional landmarks (documentation, not a restriction — any name
# matching LANDMARK_NAME is accepted, and must then be declared consistently
# across the whole row).
#
# `root` is the composition pivot for EVERY profile. `crown` (정수리) is a
# humanoid head landmark used for generation/QA framing, never an inferred
# pivot: no profile derives a pivot from geometry (maintainer decision
# 2026-07-31 — automatic semantic inference is out of the contract).
PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "humanoid_biped": {
        "required": ("root", "crown"),
        "reserved": ("head", "neck", "hand_l", "hand_r", "foot_l", "foot_r"),
    },
    "quadruped": {
        "required": ("root",),
        "reserved": ("head", "muzzle", "tail", "foot_fl", "foot_fr", "foot_bl", "foot_br"),
    },
    "blob_or_tentacle": {
        "required": ("root",),
        "reserved": ("mouth", "eye_l", "eye_r", "tip_a", "tip_b", "tip_c", "tip_d"),
    },
    "prop": {
        "required": ("root",),
        "reserved": ("grip", "tip", "muzzle"),
    },
}

# A row's track kind. `base` is the explicit default for an undeclared state, so
# a non-layer run reads as an all-`base` run rather than as an unknown state.
TRACKS = ("base", "action_overlay", "prop_effect", "full_body_override")
# Which tracks may be a stack's single body element. This is a stack-shape rule,
# not a "draws the character" rule: an `action_overlay` also draws the body, it
# just may not be the element everything else is aligned onto.
BODY_TRACKS = ("base", "full_body_override")
DEFAULT_TRACK = "base"

# A row's required landmarks come from the profile AND the row's track meaning.
# `prop_effect` art is a prop or an effect, not the character: a watering can has
# no 정수리, so holding it to `humanoid_biped`'s `crown` demands a landmark
# nobody can place, and the only way out is to declare a fake one — which turns a
# required pivot into noise on the row the composer trusts most. A prop row is
# therefore held to the `prop` profile (its own `root`, plus whatever
# self-meaning names like `grip` / `tip` it declares), whatever the run's rig
# profile is. Every body-bearing row — `base`, `action_overlay`,
# `full_body_override` — keeps the rig profile's full set, so a humanoid run
# still cannot ship a body frame without a crown.
TRACK_PROFILE_OVERRIDE: dict[str, str] = {"prop_effect": "prop"}

LANDMARK_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
COMPOSITE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Stack element defaults. `from` is the element's own landmark, `to` is the
# landmark on the body element it is placed onto; both default to the pivot.
# `revision` is an optional pin: the source row's `state_revision` segments as
# they were when the landmarks were declared. The composer checks it (same
# prefix rule as `curation.anchors`), so a re-rolled row fails the bake instead
# of aligning new art to old pivots.
ELEMENT_KEYS = ("state", "from", "to", "mask", "allow_clip", "revision")

# Composite-level keys this contract reads, and the complete set a composite may
# declare. `stack` is required; `fps` / `loop` default to the body element's state
# entry. An unknown key is rejected exactly like an unknown stack-element key: the
# composite spec reaches a runtime manifest, so a typo'd `loops` that is passed
# over silently ships a composite that plays by defaults nobody asked for.
COMPOSITE_KEYS = ("stack", "fps", "loop")


def has_layer_contract(request: dict[str, Any]) -> bool:
    """Does this request opt into the layer feature at all?

    False for every request written before the feature existed, which is what
    keeps the non-layer pipeline byte-identical: no rig, no tracks, no stacks.
    """
    if request.get("rig") or request.get("layers"):
        return True
    return any(
        isinstance(entry, dict) and entry.get("track") is not None
        for entry in (request.get("states") or {}).values()
    )


def state_track(request: dict[str, Any], state: str) -> str:
    """The declared track kind of a row — `base` when undeclared (explicit default)."""
    entry = (request.get("states") or {}).get(state)
    if not isinstance(entry, dict):
        return DEFAULT_TRACK
    track = entry.get("track")
    return DEFAULT_TRACK if track is None else str(track)


def known_profile(profile: Any) -> str | None:
    """The declared profile this value names, or None for anything else.

    The single answer to "is this a known profile" — the validator and
    `required_landmarks` must not disagree about it. `isinstance` comes before
    the membership test on purpose: `rig.profile` is JSON, so it can arrive as a
    list or an object, and `unhashable in dict` raises. A malformed declaration
    has to come back as a reported error like every other one, never as a
    TypeError out of a validator whose whole contract is returning the error list.
    """
    return profile if isinstance(profile, str) and profile in PROFILES else None


def required_landmarks(profile: Any, track: str) -> tuple[str, ...]:
    """The landmarks every pool frame of a row on this track must declare.

    Profile validity is decided FIRST, the track narrowing
    (`TRACK_PROFILE_OVERRIDE`) only after: an unknown profile requires nothing on
    ANY track, because `rig.profile` is already its own error and a second
    cascade of "missing crown" / "missing root" would bury it. Reading the
    override first would let a `prop_effect` row borrow the `prop` profile out of
    a run that declared no valid profile at all, so the one unknown-profile run
    that still got a landmark error was the one with a prop row in it.
    """
    name = known_profile(profile)
    if name is None:
        return ()
    return tuple(PROFILES[TRACK_PROFILE_OVERRIDE.get(track, name)]["required"])


def resolve_element(raw: Any) -> dict[str, Any]:
    """Fill a stack element's defaults. Call only on a validated request."""
    element = dict(raw) if isinstance(raw, dict) else {}
    return {
        "state": element.get("state"),
        "from": element.get("from", "root"),
        "to": element.get("to", "root"),
        "mask": element.get("mask"),
        "allow_clip": bool(element.get("allow_clip", False)),
        "revision": element.get("revision"),
    }


def resolve_stack(request: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """The composite's elements with defaults filled, in draw order (bottom first)."""
    stack = ((request.get("layers") or {}).get(name) or {}).get("stack") or []
    return [resolve_element(element) for element in stack]


def _pool_size(request: dict[str, Any], state: str) -> int:
    """The row's frame pool size (primary + takes), or 0 for a malformed state entry.

    A malformed entry is already a hard error on the extract path; here it only
    has to not raise, so the whole error list still gets reported at once.
    """
    entry = (request.get("states") or {}).get(state)
    if not isinstance(entry, dict):
        return 0
    try:
        return max(0, state_frame_total(request, state))
    except (TypeError, ValueError, KeyError):
        return 0


def _landmark_frames(request: dict[str, Any], state: str) -> dict[str, Any]:
    return ((request.get("rig") or {}).get("landmarks") or {}).get(state) or {}


def landmark_map(request: dict[str, Any], state: str,
                 frame: int) -> dict[str, tuple[int, int]] | None:
    """Every landmark declared on one pool frame, or None when the frame declares none.

    None is the honest answer for an instance index nothing was declared for — a
    curated clone instance lives outside the pool (`curation.state_clones`), so it
    has no declaration and the composer fails loud instead of borrowing its
    source frame's pivots for art the clone's own transform has already moved.
    """
    points = _landmark_frames(request, state).get(str(frame))
    if not isinstance(points, dict):
        return None
    resolved = {
        str(name): (int(value[0]), int(value[1]))
        for name, value in sorted(points.items())
        if _is_point(value)
    }
    return resolved or None


def landmark(request: dict[str, Any], state: str, frame: int, name: str) -> tuple[int, int] | None:
    """A declared landmark, or None. Never inferred, never interpolated."""
    point = _landmark_frames(request, state).get(str(frame))
    if not isinstance(point, dict):
        return None
    value = point.get(name)
    if not _is_point(value):
        return None
    return (int(value[0]), int(value[1]))


def _is_int(value: Any) -> bool:
    # bool is an int subclass; a JSON true must not read as a coordinate.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_point(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_int(v) for v in value)


def _cell_bounds(request: dict[str, Any]) -> tuple[int, int] | None:
    cell = request.get("cell")
    if not isinstance(cell, dict):
        return None
    width = cell.get("width", cell.get("size"))
    height = cell.get("height", cell.get("size"))
    if not _is_int(width) or not _is_int(height) or width <= 0 or height <= 0:
        return None
    return int(width), int(height)


def _validate_rig(request: dict[str, Any], errors: list[str]) -> None:
    rig = request.get("rig")
    if rig is None:
        return
    if not isinstance(rig, dict):
        errors.append("rig must be an object")
        return
    profile = rig.get("profile")
    if known_profile(profile) is None:
        errors.append(
            f"rig.profile must be one of {', '.join(sorted(PROFILES))} (got {profile!r})")
    landmarks = rig.get("landmarks", {})
    if not isinstance(landmarks, dict):
        errors.append("rig.landmarks must be an object keyed by state")
        return
    states = request.get("states") or {}
    bounds = _cell_bounds(request)
    for state in sorted(landmarks):
        if state not in states:
            errors.append(f"rig.landmarks declares unknown state {state!r}")
            continue
        track = state_track(request, state)
        _validate_state_landmarks(request, state, landmarks[state],
                                  required_landmarks(profile, track), track, bounds, errors)


def _validate_state_landmarks(request: dict[str, Any], state: str, frames: Any,
                              required: tuple[str, ...], track: str,
                              bounds: tuple[int, int] | None,
                              errors: list[str]) -> None:
    if not isinstance(frames, dict):
        errors.append(f"rig.landmarks.{state} must be an object keyed by frame index")
        return
    total = _pool_size(request, state)
    declared = set()
    for key in sorted(frames):
        if not (isinstance(key, str) and key.isdigit()):
            errors.append(f"rig.landmarks.{state} frame key {key!r} must be a decimal index string")
            continue
        index = int(key)
        if index >= total:
            errors.append(
                f"rig.landmarks.{state} frame {index} is outside the row's frame pool "
                f"(0..{total - 1}) — landmarks are indexed by pool position, takes included")
            continue
        declared.add(index)
    missing = [i for i in range(total) if i not in declared]
    if missing:
        errors.append(
            f"rig.landmarks.{state} is missing frame(s) {missing} — every pool frame needs "
            f"declared landmarks (an undeclared pivot is not guessed)")
    # A landmark must be declared on the WHOLE row: a name that appears on some
    # frames and not others would make a composite silently skip frames.
    per_frame: dict[int, set[str]] = {}
    for key in sorted(frames):
        if not (isinstance(key, str) and key.isdigit()) or int(key) >= total:
            continue
        index = int(key)
        point_map = frames[key]
        if not isinstance(point_map, dict):
            errors.append(f"rig.landmarks.{state}.{key} must be an object of landmark -> [x, y]")
            continue
        names = set()
        for name in sorted(point_map):
            if not LANDMARK_NAME.match(str(name)):
                errors.append(
                    f"rig.landmarks.{state}.{key} landmark name {name!r} must match "
                    f"{LANDMARK_NAME.pattern}")
                continue
            value = point_map[name]
            if not _is_point(value):
                errors.append(
                    f"rig.landmarks.{state}.{key}.{name} must be [x, y] integers "
                    f"(pivots are integer cell coordinates)")
                continue
            if bounds and not (0 <= value[0] < bounds[0] and 0 <= value[1] < bounds[1]):
                errors.append(
                    f"rig.landmarks.{state}.{key}.{name} {list(value)} is outside the "
                    f"{bounds[0]}x{bounds[1]} cell")
                continue
            names.add(str(name))
        per_frame[index] = names
    for name in required:
        absent = sorted(i for i, names in per_frame.items() if name not in names)
        if absent:
            errors.append(
                f"rig.landmarks.{state} frame(s) {absent} miss required landmark {name!r} — "
                f"a {track!r} row requires {', '.join(required)} on every frame")
    if per_frame:
        union: set[str] = set().union(*per_frame.values())
        for name in sorted(union):
            partial = sorted(i for i, names in per_frame.items() if name not in names)
            if partial and len(partial) != len(per_frame):
                errors.append(
                    f"rig.landmarks.{state} declares {name!r} on some frames but not "
                    f"{partial} — a landmark is declared for the whole row or not at all")


def _validate_tracks(request: dict[str, Any], errors: list[str]) -> None:
    for state in sorted(request.get("states") or {}):
        entry = (request["states"] or {})[state]
        if not isinstance(entry, dict) or entry.get("track") is None:
            continue
        if entry["track"] not in TRACKS:
            errors.append(
                f"states.{state}.track must be one of {', '.join(TRACKS)} "
                f"(got {entry['track']!r})")


def _validate_layers(request: dict[str, Any], errors: list[str]) -> None:
    composites = request.get("layers")
    if composites is None:
        return
    if not isinstance(composites, dict):
        errors.append("layers must be an object keyed by composite name")
        return
    states = request.get("states") or {}
    for name in sorted(composites):
        if not COMPOSITE_NAME.match(str(name)):
            errors.append(f"layers.{name}: name must match {COMPOSITE_NAME.pattern}")
        if name in states:
            errors.append(
                f"layers.{name}: a composite name must not collide with a request state — "
                f"composites are a separate artifact tree, not extra rows")
        spec = composites[name]
        if not isinstance(spec, dict):
            errors.append(f"layers.{name} must be an object")
            continue
        unknown = sorted(set(spec) - set(COMPOSITE_KEYS))
        if unknown:
            errors.append(
                f"layers.{name} has unknown key(s) {unknown}; "
                f"allowed: {', '.join(COMPOSITE_KEYS)}")
        # `fps` / `loop` reach the composite manifest verbatim, so a string fps
        # would be baked into a runtime contract. Typed here, where the whole
        # declaration is checked before a run dir is touched.
        if "fps" in spec and not (_is_int(spec["fps"]) and spec["fps"] > 0):
            errors.append(
                f"layers.{name}.fps must be a positive integer (got {spec['fps']!r})")
        if "loop" in spec and not isinstance(spec["loop"], bool):
            errors.append(f"layers.{name}.loop must be true or false (got {spec['loop']!r})")
        stack = spec.get("stack")
        if not isinstance(stack, list) or not stack:
            errors.append(f"layers.{name}.stack must be a non-empty list of elements")
            continue
        _validate_stack(request, name, stack, errors)


def _validate_stack(request: dict[str, Any], name: str, stack: list[Any],
                    errors: list[str]) -> None:
    states = request.get("states") or {}
    resolved: list[dict[str, Any]] = []
    for position, raw in enumerate(stack):
        if not isinstance(raw, dict):
            errors.append(f"layers.{name}.stack[{position}] must be an object")
            continue
        unknown = sorted(set(raw) - set(ELEMENT_KEYS))
        if unknown:
            errors.append(
                f"layers.{name}.stack[{position}] has unknown key(s) {unknown}; "
                f"allowed: {', '.join(ELEMENT_KEYS)}")
        pin = raw.get("revision")
        if pin is not None and not (isinstance(pin, list) and pin
                                    and all(isinstance(seg, str) for seg in pin)):
            errors.append(
                f"layers.{name}.stack[{position}].revision must be a non-empty list of "
                f"state_revision segment strings (it pins the generation the landmarks "
                f"were declared against)")
        state = raw.get("state")
        if not isinstance(state, str) or state not in states:
            errors.append(
                f"layers.{name}.stack[{position}].state must name a request state "
                f"(got {state!r})")
            continue
        element = resolve_element(raw)
        element["position"] = position
        element["track"] = state_track(request, state)
        resolved.append(element)
    if not resolved:
        return

    body = [e for e in resolved if e["track"] in BODY_TRACKS]
    if not body:
        errors.append(
            f"layers.{name}: a stack needs exactly one body element "
            f"({' or '.join(BODY_TRACKS)}); found none")
        return
    if len(body) > 1:
        errors.append(
            f"layers.{name}: a stack has exactly one body element, got "
            f"{[e['state'] for e in body]} — a full_body_override replaces the base "
            f"instead of stacking with it")
        return
    body_element = body[0]
    if body_element["position"] != 0:
        errors.append(
            f"layers.{name}: the body element {body_element['state']!r} must be stack[0]; "
            f"list order is draw order, bottom first")
    if body_element["track"] == "full_body_override":
        overlays = [e["state"] for e in resolved if e["track"] == "action_overlay"]
        if overlays:
            errors.append(
                f"layers.{name}: full_body_override {body_element['state']!r} forbids "
                f"action_overlay element(s) {overlays} — an exception state owns the whole body")

    body_total = _pool_size(request, body_element["state"])
    for element in resolved:
        if element is body_element:
            continue
        total = _pool_size(request, element["state"])
        if total not in (1, body_total):
            errors.append(
                f"layers.{name}: element {element['state']!r} has {total} pool frames; "
                f"an overlay runs with the body ({body_total}) or holds a single frame (1)")
        _validate_element_landmarks(request, name, element, body_element, errors)


def _validate_element_landmarks(request: dict[str, Any], name: str, element: dict[str, Any],
                                body_element: dict[str, Any], errors: list[str]) -> None:
    if request.get("rig") is None:
        errors.append(
            f"layers.{name}: element {element['state']!r} needs a rig block — a composite "
            f"aligns declared landmarks, and there are none")
        return
    for role, state, wanted in (
        ("from", element["state"], element["from"]),
        ("to", body_element["state"], element["to"]),
    ):
        total = _pool_size(request, state)
        absent = [i for i in range(total) if landmark(request, state, i, str(wanted)) is None]
        if absent:
            errors.append(
                f"layers.{name}: element {element['state']!r} aligns {role}={wanted!r} but "
                f"state {state!r} does not declare it on frame(s) {absent}")


def validate_layer_request(request: dict[str, Any]) -> list[str]:
    """Every layer-contract violation in this request, deterministically ordered.

    Empty list = the request's layer declarations are self-consistent. A request
    with no layer keys always returns []; validation never invents a requirement
    for a run that did not opt in.
    """
    if not has_layer_contract(request):
        return []
    errors: list[str] = []
    _validate_rig(request, errors)
    _validate_tracks(request, errors)
    _validate_layers(request, errors)
    return errors


def require_valid_layer_request(request: dict[str, Any]) -> None:
    """Fail loud with every violation at once (No Silent Fallback)."""
    errors = validate_layer_request(request)
    if errors:
        raise SystemExit(
            "invalid layer contract in sprite-request.json:\n  - " + "\n  - ".join(errors))
