#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare a sprite-gen component-row run.

This script owns the numeric sprite recipe. It writes one request JSON, one
layout guide per state, and one prompt per state. Image generation should read
these files instead of hand-copying frame counts into ad hoc prompts.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


DEFAULT_STATES: dict[str, dict[str, Any]] = {
    "idle": {"frames": 4, "fps": 4, "loop": True, "action": "subtle breathing and blinking"},
    "attack": {
        "frames": 4,
        "fps": 8,
        "loop": False,
        "action": "simple windup, strike, recovery attack pose sequence with no detached effects",
    },
    "jump": {"frames": 4, "fps": 8, "loop": False, "action": "jump arc through body position only"},
    "wave": {
        "frames": 4,
        "fps": 6,
        "loop": False,
        "action": "friendly hand wave gesture; arm changes clearly while feet stay planted",
    },
}

STYLE_DEFAULT = (
    "pixel-art-adjacent low-resolution game sprite, compact chibi/mascot-friendly "
    "proportions when the base allows it, chunky whole-body silhouette, thick dark "
    "1-2 px outline, visible stepped/pixel edges, limited palette, flat cel shading "
    "with at most one small highlight and one shadow step, simple readable face, "
    "clear limbs, and no detail that disappears at small runtime size. Avoid polished "
    "illustration, painterly rendering, anime key art, 3D render, vector app-icon "
    "polish, glossy lighting, soft gradients, anti-aliased high-detail edges, and "
    "complex tiny accessories."
)

TRANSPARENCY_ARTIFACT_RULES = [
    "Prefer pose, expression, and silhouette changes over decorative effects.",
    "Effects are allowed only when state-relevant, opaque, hard-edged, sprite-like, fully inside the same frame slot, and physically touching or overlapping the character silhouette.",
    "Do not draw detached effects: floating stars, loose sparkles, floating punctuation, floating icons, separated smoke clouds, loose dust, disconnected outline bits, or stray pixels.",
    "Do not draw wave marks, motion arcs, speed lines, action streaks, afterimages, blur, smears, halos, glows, auras, floor patches, cast shadows, contact shadows, drop shadows, oval floor shadows, landing marks, or impact bursts.",
    "Do not include text, labels, frame numbers, visible grids, guide marks, speech bubbles, thought bubbles, UI panels, code snippets, scenery, checkerboard transparency, white backgrounds, or black backgrounds.",
    "Reject any pose that is cropped, overlaps another pose, crosses into a neighboring frame slot, or creates a separate disconnected component that is not attached to the character.",
]

STATE_REQUIREMENTS = {
    "running-right": [
        "Show rightward locomotion through body, arm, leg, hair, and prop movement only.",
        "Use distinct gait poses that create a readable cycle instead of repeated standing or static bobbing.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "running-left": [
        "Show leftward locomotion through body, arm, leg, hair, and prop movement only.",
        "Use distinct gait poses that create a readable cycle instead of repeated standing or static bobbing.",
        "If an additional rightward gait row is attached, use it only as a motion-rhythm reference for limb phase and body bounce; do not copy its facing direction or redraw the character from that row.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "running-front-right": [
        "Show 45-degree diagonal locomotion toward camera-right and slightly toward the viewer.",
        "Keep the body three-quarter-front, not pure side view and not straight front view.",
        "Use alternating foot-contact phases so the left and right legs clearly trade forward reach.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "running-front-left": [
        "Show 45-degree diagonal locomotion toward camera-left and slightly toward the viewer.",
        "Keep the body three-quarter-front, not pure side view and not straight front view.",
        "Use alternating foot-contact phases so the left and right legs clearly trade forward reach.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "running-back-right": [
        "Show 45-degree diagonal locomotion away from the viewer toward camera-right.",
        "Keep the body three-quarter-back, with the face partly hidden but the character still identifiable.",
        "Use alternating foot-contact phases so the left and right legs clearly trade backward/forward reach.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "running-back-left": [
        "Show 45-degree diagonal locomotion away from the viewer toward camera-left.",
        "Keep the body three-quarter-back, with the face partly hidden but the character still identifiable.",
        "Use alternating foot-contact phases so the left and right legs clearly trade backward/forward reach.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "run": [
        "Show locomotion through body, arm, leg, hair, and prop movement only.",
        "Use distinct gait poses that create a readable cycle instead of repeated standing or static bobbing.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "walk": [
        "Show locomotion through body, arm, leg, hair, and prop movement only.",
        "Use distinct gait poses that create a readable cycle instead of repeated standing or static bobbing.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "frontwalk": [
        "Show front-view walking through alternating leg, arm, shoulder, and body-height changes.",
        "This is difficult: make the foot-contact and passing poses visibly different without changing identity.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "45_frontwalk": [
        "Show three-quarter-front walking through alternating leg, arm, shoulder, and body-height changes.",
        "This is difficult: make the foot-contact and passing poses visibly different without changing identity.",
        "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects.",
    ],
    "wave": [
        "Show the gesture through arm pose only: arm down, arm raised, hand tilted, arm returning.",
        "Keep the feet planted unless the action explicitly requests stepping.",
        "Do not draw wave marks, motion arcs, lines, sparkles, symbols, or floating effects around the hand.",
    ],
    "jump": [
        "Show the jump through pose and vertical body position only: anticipation, lift, airborne peak, descent, settle.",
        "Do not draw ground shadows, contact shadows, oval shadows, landing marks, dust, smears, or motion marks under the character.",
    ],
}

RUN_PHASE_CYCLE = [
    {
        "name": "contact",
        "body_y": 0,
        "front_leg": "forward_straight",
        "back_leg": "back_extended",
        "note": "front foot contacts ground, back foot pushes off",
    },
    {
        "name": "down",
        "body_y": 6,
        "front_leg": "under_bent",
        "back_leg": "back_bent",
        "note": "weight drops over planted foot",
    },
    {
        "name": "passing",
        "body_y": 2,
        "front_leg": "under_vertical",
        "back_leg": "passing_forward",
        "note": "swing leg passes under body",
    },
    {
        "name": "up",
        "body_y": -6,
        "front_leg": "back_lifted",
        "back_leg": "forward_lifted",
        "note": "body lifts before the opposite contact",
    },
    {
        "name": "opposite_contact",
        "body_y": 0,
        "front_leg": "back_extended",
        "back_leg": "forward_straight",
        "note": "opposite foot contacts ground",
    },
    {
        "name": "opposite_down",
        "body_y": 6,
        "front_leg": "back_bent",
        "back_leg": "under_bent",
        "note": "weight drops over the opposite planted foot",
    },
    {
        "name": "opposite_passing",
        "body_y": 2,
        "front_leg": "passing_forward",
        "back_leg": "under_vertical",
        "note": "first leg passes under body",
    },
    {
        "name": "opposite_up",
        "body_y": -6,
        "front_leg": "forward_lifted",
        "back_leg": "back_lifted",
        "note": "body lifts back toward frame 1",
    },
]

CHROMA_CANDIDATES = [
    ("magenta", "#FF00FF"),
    ("green", "#00FF00"),
    ("cyan", "#00FFFF"),
    ("blue", "#004DFF"),
]


def parse_hex_color(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise SystemExit(f"invalid chroma key color: {value}; expected #RRGGBB")
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def sampled_reference_pixels(path: Path | None) -> list[tuple[int, int, int]]:
    if path is None or not path.is_file():
        return []
    pixels: list[tuple[int, int, int]] = []
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        data = image.tobytes()
        for index in range(0, len(data), 4):
            red, green, blue, alpha = data[index : index + 4]
            if alpha <= 16:
                continue
            if red > 244 and green > 244 and blue > 244:
                continue
            pixels.append((red, green, blue))
    return pixels


# Mirrors the default --key-threshold in extract_sprite_row_frames.py: any subject
# pixel within this color distance of the chroma key is removed at extraction, so a
# key whose nearest subject pixel falls inside this radius will erase that feature.
MIN_SUBJECT_KEY_DISTANCE = 96.0


def choose_chroma_key(reference: Path | None, requested: str) -> dict[str, Any]:
    if requested.lower() != "auto":
        rgb = parse_hex_color(requested)
        hex_value = rgb_to_hex(rgb)
        name = next((candidate_name for candidate_name, candidate_hex in CHROMA_CANDIDATES if candidate_hex == hex_value), "manual")
        return {"name": name, "hex": hex_value, "rgb": list(rgb), "selection": "manual"}

    pixels = sampled_reference_pixels(reference)
    if not pixels:
        rgb = parse_hex_color("#FF00FF")
        return {"name": "magenta", "hex": "#FF00FF", "rgb": list(rgb), "selection": "fallback"}

    scored: list[tuple[float, float, int, str, tuple[int, int, int]]] = []
    for preference_index, (name, hex_color) in enumerate(CHROMA_CANDIDATES):
        rgb = parse_hex_color(hex_color)
        distances = sorted(color_distance(rgb, pixel) for pixel in pixels)
        percentile_index = max(0, min(len(distances) - 1, int(len(distances) * 0.01)))
        scored.append((distances[percentile_index], distances[0], -preference_index, name, rgb))

    # Rank by the 1st-percentile distance, but that metric ignores sub-1% features
    # (eyes, gems, ear lamps): a key can look "safe" while its nearest subject pixel
    # is still inside the erase radius. Prefer candidates that clear every subject
    # pixel; only fall back to the raw ranking (with a warning) when none do.
    safe = [entry for entry in scored if entry[1] > MIN_SUBJECT_KEY_DISTANCE]
    score, min_distance, _preference, name, rgb = max(safe) if safe else max(scored)
    result = {
        "name": name,
        "hex": rgb_to_hex(rgb),
        "rgb": list(rgb),
        "selection": "auto",
        "score": round(score, 2),
        "min_subject_distance": round(min_distance, 2),
    }
    if min_distance <= MIN_SUBJECT_KEY_DISTANCE:
        result["warning"] = (
            f"nearest subject pixel is {min_distance:.1f} from {name} "
            f"(<= {MIN_SUBJECT_KEY_DISTANCE:.0f}); that feature will be erased at extraction — "
            f"recolor it or force a different --chroma-key"
        )
        print(f"WARNING: chroma_key auto: {result['warning']}", file=sys.stderr)
    return result


def normalize_states(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    source = raw or DEFAULT_STATES
    normalized: dict[str, dict[str, Any]] = {}
    for state, entry in source.items():
        if not isinstance(entry, dict):
            raise SystemExit(f"state {state!r} must be an object")
        frames = int(entry.get("frames", 0))
        if frames <= 0:
            raise SystemExit(f"state {state!r} must have positive frames")
        normalized[state] = {
            "frames": frames,
            "fps": int(entry.get("fps", DEFAULT_STATES.get(state, {}).get("fps", 6))),
            "loop": bool(entry.get("loop", True)),
            "action": str(entry.get("action", DEFAULT_STATES.get(state, {}).get("action", state))),
        }
    return normalized


def normalize_cell(raw_cell: dict[str, Any], size: int, safe_margin: int) -> dict[str, Any]:
    width = int(raw_cell.get("width", raw_cell.get("cell_width", raw_cell.get("size", size))))
    height = int(raw_cell.get("height", raw_cell.get("cell_height", raw_cell.get("size", size))))
    margin_x = int(raw_cell.get("safe_margin_x", raw_cell.get("safe_margin", safe_margin)))
    margin_y = int(raw_cell.get("safe_margin_y", raw_cell.get("safe_margin", safe_margin)))
    if width <= 0 or height <= 0:
        raise SystemExit("cell width and height must be positive")
    if margin_x < 0 or margin_y < 0 or margin_x * 2 >= width or margin_y * 2 >= height:
        raise SystemExit("cell safe margins must fit inside cell width/height")
    cell: dict[str, Any] = {
        "shape": "rect" if width != height else "square",
        "width": width,
        "height": height,
        "safe_margin_x": margin_x,
        "safe_margin_y": margin_y,
    }
    if width == height and margin_x == margin_y:
        cell["size"] = width
        cell["safe_margin"] = margin_x
    return cell


def load_request(path: Path | None, inline_json: str | None) -> dict[str, Any]:
    if path and inline_json:
        raise SystemExit("use only one of --request or --request-json")
    if path:
        return json.loads(path.read_text(encoding="utf-8"))
    if inline_json:
        return json.loads(inline_json)
    return {}


def state_motion_phases(state: str, frames: int) -> list[dict[str, Any]]:
    if frames != 8:
        return []
    if (
        state in {"running-right", "running-left", "run", "walk"}
        or state.startswith("running-front-")
        or state.startswith("running-back-")
        or state.startswith("walking-front-")
        or state.startswith("walking-back-")
    ):
        return RUN_PHASE_CYCLE
    return []


def directional_parts(state: str) -> tuple[str, str] | None:
    match = re.search(r"-(front|back)-(left|right)$", state)
    if not match:
        return None
    return match.group(1), match.group(2)


def directional_requirements(state: str) -> list[str]:
    parts = directional_parts(state)
    if not parts:
        return []
    depth, side = parts
    toward = "toward the viewer" if depth == "front" else "away from the viewer"
    body_view = "three-quarter-front" if depth == "front" else "three-quarter-back"
    camera_side = f"camera-{side}"
    opposite_side = "left" if side == "right" else "right"
    requirements = [
        f"Lock the whole row to a 45-degree {body_view} view facing {camera_side} and slightly {toward}.",
        f"Do not average this into a straight front, straight back, or pure side-view sprite.",
        f"Make {camera_side} readable through face/body orientation, hair silhouette, shoulder overlap, hand/foot placement, and prop angle.",
        "If a 4-direction reference sheet is attached, use it as the direction SSoT for facing only; do not copy its pose or state.",
        "If a single target-direction anchor is attached, its facing direction is authoritative and overrides any paired-row reference.",
    ]
    if side == "left":
        requirements.append(
            f"If a generated {depth}-{opposite_side} basis row is attached, use it only for timing, scale, and pose-family consistency; change the facing to camera-left."
        )
    return requirements


def mirrored_x(center_x: int, x: int, facing: str) -> int:
    if facing == "left":
        return center_x - (x - center_x)
    return x


def leg_points(root: tuple[int, int], pose: str, facing: str, scale: float) -> tuple[tuple[int, int], tuple[int, int]]:
    root_x, root_y = root
    forward = round(34 * scale)
    back = round(32 * scale)
    down = round(54 * scale)
    bend = round(24 * scale)
    lift = round(22 * scale)
    if pose == "forward_straight":
        knee = (root_x + round(forward * 0.45), root_y + round(down * 0.48))
        foot = (root_x + forward, root_y + down)
    elif pose == "back_extended":
        knee = (root_x - round(back * 0.45), root_y + round(down * 0.48))
        foot = (root_x - back, root_y + down)
    elif pose == "under_bent":
        knee = (root_x + round(bend * 0.2), root_y + round(down * 0.45))
        foot = (root_x + round(bend * 0.55), root_y + round(down * 0.82))
    elif pose == "back_bent":
        knee = (root_x - round(bend * 0.65), root_y + round(down * 0.42))
        foot = (root_x - round(bend * 0.2), root_y + round(down * 0.78))
    elif pose == "passing_forward":
        knee = (root_x + round(bend * 0.45), root_y + round(down * 0.35))
        foot = (root_x + round(bend * 0.1), root_y + round(down * 0.63))
    elif pose == "under_vertical":
        knee = (root_x, root_y + round(down * 0.42))
        foot = (root_x, root_y + round(down * 0.88))
    elif pose == "forward_lifted":
        knee = (root_x + round(forward * 0.45), root_y + round(down * 0.18))
        foot = (root_x + round(forward * 0.7), root_y + round(down * 0.35))
    elif pose == "back_lifted":
        knee = (root_x - round(back * 0.45), root_y + round(down * 0.18))
        foot = (root_x - round(back * 0.7), root_y + round(down * 0.35))
    else:
        knee = (root_x, root_y + round(down * 0.45))
        foot = (root_x, root_y + down)
    if facing == "left":
        knee = (root_x - (knee[0] - root_x), knee[1])
        foot = (root_x - (foot[0] - root_x), foot[1])
    return knee, foot


def draw_motion_phase(draw: ImageDraw.ImageDraw, slot_left: int, cell_width: int, cell_height: int, phase: dict[str, Any], facing: str) -> None:
    scale = min(cell_width / 192, cell_height / 208)
    center_x = slot_left + cell_width // 2
    hip_y = round(cell_height * 0.52 + int(phase["body_y"]) * scale)
    shoulder_y = hip_y - round(42 * scale)
    head_y = shoulder_y - round(26 * scale)
    hip = (center_x, hip_y)
    shoulder = (center_x, shoulder_y)
    head_bbox = (
        center_x - round(11 * scale),
        head_y - round(11 * scale),
        center_x + round(11 * scale),
        head_y + round(11 * scale),
    )
    draw.ellipse(head_bbox, outline="#6b7280", width=max(1, round(2 * scale)))
    draw.line((shoulder, hip), fill="#6b7280", width=max(2, round(3 * scale)))
    front_arm = (mirrored_x(center_x, center_x - round(26 * scale), facing), shoulder_y + round(30 * scale))
    back_arm = (mirrored_x(center_x, center_x + round(26 * scale), facing), shoulder_y + round(18 * scale))
    draw.line((shoulder, front_arm), fill="#94a3b8", width=max(1, round(2 * scale)))
    draw.line((shoulder, back_arm), fill="#cbd5e1", width=max(1, round(2 * scale)))
    front_knee, front_foot = leg_points(hip, str(phase["front_leg"]), facing, scale)
    back_knee, back_foot = leg_points(hip, str(phase["back_leg"]), facing, scale)
    draw.line((hip, front_knee, front_foot), fill="#ef4444", width=max(2, round(4 * scale)))
    draw.line((hip, back_knee, back_foot), fill="#2563eb", width=max(2, round(4 * scale)))
    ground_y = round(cell_height * 0.52 + 54 * scale + int(phase["body_y"]) * scale)
    draw.line((slot_left + round(34 * scale), ground_y, slot_left + cell_width - round(34 * scale), ground_y), fill="#cbd5e1", width=1)


def draw_guide(path: Path, state: str, frames: int, cell: dict[str, Any], motion_phase_guides: bool = False) -> None:
    cell_width = int(cell["width"])
    cell_height = int(cell["height"])
    safe_margin_x = int(cell["safe_margin_x"])
    safe_margin_y = int(cell["safe_margin_y"])
    width = frames * cell_width
    height = cell_height
    image = Image.new("RGB", (width, height), "#f6f6f6")
    draw = ImageDraw.Draw(image)
    for index in range(frames):
        left = index * cell_width
        right = left + cell_width - 1
        draw.rectangle((left, 0, right, height - 1), outline="#333333", width=3)
        safe = (
            left + safe_margin_x,
            safe_margin_y,
            right - safe_margin_x,
            height - 1 - safe_margin_y,
        )
        draw.rectangle(safe, outline="#2f80ed", width=2)
        draw.line((left + cell_width // 2, safe_margin_y, left + cell_width // 2, height - safe_margin_y), fill="#b8c8e8", width=1)
    if motion_phase_guides:
        phases = state_motion_phases(state, frames)
        facing = "left" if state.endswith("left") else "right"
        for index, phase in enumerate(phases):
            draw_motion_phase(draw, index * cell_width, cell_width, cell_height, phase, facing)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def row_prompt(request: dict[str, Any], state: str, entry: dict[str, Any]) -> str:
    cell = request["cell"]
    chroma = request["chroma_key"]
    character = request["character"]
    frames = int(entry["frames"])
    cell_width = int(cell["width"])
    cell_height = int(cell["height"])
    safe_margin_x = int(cell["safe_margin_x"])
    safe_margin_y = int(cell["safe_margin_y"])
    state_requirements = [*directional_requirements(state), *STATE_REQUIREMENTS.get(state, [])]
    state_requirement_text = ""
    if state_requirements:
        state_requirement_text = "\n\nState-specific requirements:\n" + "\n".join(
            f"- {requirement}" for requirement in state_requirements
        )
    phase_prompt_text = ""
    phases = state_motion_phases(state, frames) if request.get("motion_phase_guides") else []
    if phases:
        phase_lines = [
            f"- frame {index + 1}: {phase['name']} — {phase['note']}"
            for index, phase in enumerate(phases)
        ]
        phase_prompt_text = (
            "\n\nMotion phase requirements:\n"
            "- The layout guide includes simple stick-pose motion hints inside each slot. Use those hints only for body height, foot contact, and leg phase. Do not copy guide colors or guide lines into the artwork.\n"
            "- Make the sequence loop as one continuous locomotion cycle, not eight unrelated poses.\n"
            "- The motion phase guide and any multi-pose contact sheet override a single running/walking pose anchor for leg phase. Do not repeat one anchor's forward leg across every frame.\n"
            "- Opposite contact frames must visibly trade which leg reaches forward; passing frames must not look like duplicate contact frames.\n"
            + "\n".join(phase_lines)
        )
    transparency_artifact_text = "\n".join(f"- {rule}" for rule in TRANSPARENCY_ARTIFACT_RULES)
    runtime_size = f"{cell_width}x{cell_height}"
    reference_contract = (
        "Use the attached accepted idle/direction anchor as the canonical character design for this row. "
        "If a state anchor is attached for a non-locomotion state, treat it as approved state vocabulary only. "
        "Use the attached layout guide image only for frame count, slot spacing, centering, and safe padding. "
        "If an additional generated row strip is attached, use it only as a motion reference, never as a replacement identity source. "
        "Do not simply copy the still reference pose. Generate distinct animation poses that create a readable cycle or action."
    )
    if character.get("base_image"):
        reference_contract = (
            "If this is a pre-idle/simple run, the attached base image may be used as the canonical character design. "
            "In direction-anchor mode, do not use base images for final action rows; accepted idle/direction anchors own row identity. "
            + reference_contract
        )
    return f"""Create a single horizontal sprite strip for the game character `{character["id"]}` in the state `{state}`.

{reference_contract}

Character: {character.get("description") or character["id"]}.
Style contract: {request["style"]}.

Use this prompt as an authoritative sprite-production spec. Do not expand it into a polished illustration, painterly character image, anime key art, 3D render, vector mascot, glossy app icon, realistic portrait, or marketing artwork.

Animation action: {entry["action"]}.

Anchor lock:
- Accepted idle/direction anchors own character identity, outfit details, colors, face design, asymmetric markings, and side-specific accessories for final action rows.
- Base character images and original character sheets are pre-idle sources only. Do not reinterpret or reintroduce base-character details inside a direction-anchor action row.
- This row owns motion only. Spend the variation budget on limb contacts, arm counter-swing, body height, torso lean, head bob, hair bounce, and loop continuity.
- Do not redesign or reinterpret identity details while animating. Keep face, hair shape, markings, palette, outline weight, body proportions, outfit, props, and silhouette copied from the approved anchors.
- Preserve side-specific features exactly as the approved anchors show them. Do not solve hairpin side, earring side, logos, handed props, scars, one-sided markings, asymmetric clothing, or lighting cues from scratch inside the row.
- When generating a paired left/right row, use the paired row reference only for timing, scale, and animation intensity. Rotate the body, feet, shoulders, face angle, and gaze to the target facing, but keep identity details attached according to the accepted target-direction anchor.
- For cyclic locomotion, do not let a single running/walking pose anchor determine every frame's leg phase. Use multi-pose motion references and the layout phase guide for foot contacts.
- Prefer a subtler animation over any change that mutates the character identity.
{state_requirement_text}
{phase_prompt_text}

Transparency and artifact rules:
{transparency_artifact_text}

Layout requirements:
- Exactly {frames} full-body frames, left to right, in one horizontal row.
- The attached layout guide shows the {frames} frame boxes, inner safe area, and optional motion phase hints for this row. Follow its slot count, spacing, centering, padding, and phase timing.
- Do not reproduce the layout guide itself: no visible boxes, guide lines, center marks, labels, stick figures, guide colors, or guide background may appear in the output.
- Treat the image as {frames} equal-width invisible {runtime_size} frame slots. Fill every slot: each requested slot must contain exactly one complete full-body pose.
- Spread the {frames} poses evenly across the whole image width. Do not leave any requested slot blank or create large empty gaps between poses.
- Center one complete pose in each slot. No pose may cross into the neighboring slot.
- Use a perfectly flat pure {chroma["name"]} {chroma["hex"]} chroma-key background across the whole image.
- Do not use {chroma["hex"]}, pure {chroma["name"]}, or chroma-adjacent colors in the character, highlights, props, shadows, or effects.
- Keep the rendering sprite-like: chunky silhouette, dark pixel-style outline, limited palette, flat shading, minimal tiny detail.
- Keep every frame self-contained with at least {safe_margin_x} px horizontal and {safe_margin_y} px vertical safe padding. No character body part should be clipped by the frame slot.
- Avoid motion blur. Use clear pose changes readable at {runtime_size}.
- Preserve the same silhouette, face, proportions, palette, material, and props across every frame.

Output only the sprite strip image."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--character-id", required=True)
    parser.add_argument("--base-image", type=Path)
    parser.add_argument("--description", default="")
    parser.add_argument("--style", default=STYLE_DEFAULT)
    parser.add_argument("--cell-size", type=int, default=256)
    parser.add_argument("--cell-width", type=int)
    parser.add_argument("--cell-height", type=int)
    parser.add_argument("--safe-margin", type=int, default=24)
    parser.add_argument("--chroma-key", default="auto", help="auto or #RRGGBB")
    parser.add_argument("--motion-phase-guides", action="store_true", help="draw simple per-frame motion phase hints into locomotion layout guides")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--request-json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out_dir = args.out_dir.expanduser().resolve()
    if out_dir.exists() and any(out_dir.iterdir()) and not args.force:
        raise SystemExit(f"output dir exists and is not empty: {out_dir}; pass --force")

    raw_request = load_request(args.request, args.request_json)
    states = normalize_states(raw_request.get("states"))
    raw_cell = dict(raw_request.get("cell", {}))
    if args.cell_width is not None:
        raw_cell["width"] = args.cell_width
    if args.cell_height is not None:
        raw_cell["height"] = args.cell_height
    cell = normalize_cell(raw_cell, args.cell_size, args.safe_margin)

    out_dir.mkdir(parents=True, exist_ok=True)
    base_dest = None
    if args.base_image:
        base_source = args.base_image.expanduser().resolve()
        if not base_source.is_file():
            raise SystemExit(f"missing base image: {base_source}")
        base_dest = out_dir / f"base-source{base_source.suffix.lower() or '.png'}"
        shutil.copy2(base_source, base_dest)

    chroma_key = choose_chroma_key(base_dest, args.chroma_key)
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {
            "id": args.character_id,
            "description": args.description,
            "base_image": base_dest.name if base_dest else None,
        },
        "cell": cell,
        "chroma_key": chroma_key,
        "states": states,
        "style": raw_request.get("style", args.style),
        "motion_phase_guides": bool(raw_request.get("motion_phase_guides", args.motion_phase_guides)),
    }

    references = out_dir / "references" / "layout-guides"
    prompts = out_dir / "prompts"
    raw = out_dir / "raw"
    frames = out_dir / "frames"
    for directory in (references, prompts, raw, frames):
        directory.mkdir(parents=True, exist_ok=True)

    for state, entry in states.items():
        draw_guide(
            references / f"{state}.png",
            state,
            int(entry["frames"]),
            cell,
            motion_phase_guides=bool(request["motion_phase_guides"]),
        )
        (prompts / f"{state}.txt").write_text(row_prompt(request, state, entry).rstrip() + "\n", encoding="utf-8")

    (out_dir / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({"ok": True, "run_dir": str(out_dir), "states": list(states)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
