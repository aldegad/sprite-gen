# SPDX-License-Identifier: Apache-2.0
"""Single-play cuts: terminal condition, endpoint sampling and playback metadata."""
from __future__ import annotations

import math
from typing import Any, Callable

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video.body_plan import LEGLESS, QUADRUPED
from sprite_gen.video.loop_vocab import CYCLE_LIFETIME, CYCLE_ONCE, CYCLE_ONCE_RETURN

# Cuts played once: `once` ends held on its terminal pose, `lifetime` on an empty frame, `once-return` back on its
# first pose (a clip pinned to end on its first frame, kept whole, `loop.once_return_cycle`).
KINDS = (CYCLE_ONCE, CYCLE_LIFETIME, CYCLE_ONCE_RETURN)
MOTION_TEXT = {'death': 'collapses directly onto the ground, settles into a completely prone position, and remains '
          'lying entirely motionless for the final 0.4 seconds without shifting, rising, or returning '
          'to the starting posture.',
 'fall': 'drops briefly downward in place, impacts and settles onto the ground, and holds that grounded '
         'resting position completely motionless for the final 0.4 seconds without bouncing, '
         'recovering, or returning to the start.',
 'land': 'descends vertically a short distance, absorbs ground impact by compressing into a low crouch, '
         'and holds that crouched posture completely motionless for the final 0.4 seconds without '
         'standing up or returning to the start.',
 'cast': 'raises a casting implement or the forward body into an active casting pose and holds that '
         'gesture completely motionless for the final 0.4 seconds, displaying no glowing lights, '
         'bursts, or particle effects.',
 'hurt_hold': 'recoils backward from an impact once and immediately holds the recoiled posture '
              'completely motionless for the final 0.4 seconds without relaxing, recovering, or '
              'returning to the start.',
 'transform': 'changes into a final form through a single completed morph and holds that new form '
              'completely motionless for the final 0.4 seconds without shifting, reverting, or '
              'returning to the starting form.'}
STATES = frozenset(MOTION_TEXT)
COMMON_TEXT = 'Each action is executed exactly once without looping or returning to the initial state, holding the final pose completely motionless for the final 0.4 seconds, keeping the full body and any props entirely inside the frame under a fixed camera against a flat pure chroma background with no shadows, particles, visual effects, or smears, while preserving the original art style and permitting changes to character design and proportions solely for the transform action.'
ANATOMY_TEXT = {QUADRUPED: 'Preserve quadruped anatomical structure without requiring an upright or standing stance, '
              'allowing natural weight transfer, crouching, and full ground collapse during death or '
              'falls without requiring all four limbs to touch the ground on every frame.',
 LEGLESS: 'Preserve continuous limbless anatomy without forcing an upright orientation, allowing '
            'undulating, coiled, or fully flattened ground configurations during death or falls without '
            'introducing limbs.'}
CYCLIC_STATES = frozenset({"idle", "walk", "run", "talk", "blink", "breathe"})
# The gaits among them: a walk or run, filmed in place (`batch`) and checked for a far limb in view (`qa.handed`).
GAIT_STATES = frozenset({"walk", "run"})
STABLE_SECONDS = 0.2
STABLE_MAX = 0.025  # distance / visible subject mass, independent of canvas padding
POSE_SHAPE_TOLERANCE = 0.05
POSE_FLOOR_TOLERANCE = 0.02
GROUND_HEIGHT_MAX = 0.8
GROUND_STATES = frozenset({"death", "fall"})
GROUND_PX = 4  # a body whose lowest row is this close to the floor row stands on it (`on_floor`)


Box = tuple[int, int, int, int]
# How a once cut judges the pose a settled window ends on (`end_pose`): given the window [first, end] and the subject
# mass its distances are divided by, the pose's evidence, or a ValueError saying why the window ends in no end pose.
EndPose = Callable[[int, int, float], dict[str, Any]]


def hold_ending(D: np.ndarray, masses: np.ndarray, fps: float, *, pose: EndPose) -> tuple[int, int, dict[str, Any]]:
    """End at the first settled window after the action whose pose `pose` (`end_pose`) takes for the end pose,
    discarding subsequent motion."""
    n = len(D)
    count = max(4, math.ceil(fps * STABLE_SECONDS))
    if n <= count + 1 or float(masses[0]) <= 0:
        raise ValueError("once needs a visible subject and a stable end window")
    if not np.any(D[0] / max(float(masses[0]), float(np.median(masses))) > STABLE_MAX * 2):
        raise ValueError("once has no performed action before its stable end pose")
    reason = "no window meets the stability limit"
    for end in range(count + 1, n):
        first = end - count + 1
        if float(masses[first:end + 1].min()) <= 0:
            continue
        scale = float(np.median(masses[first:end + 1]))
        diameter = float(D[first:end + 1, first:end + 1].max()) / scale
        if diameter > STABLE_MAX:
            continue
        mass = max(float(masses[0]), scale)
        departure = D[0] / mass
        active = np.flatnonzero(departure[:first] > STABLE_MAX * 2)
        if not len(active):
            continue
        try:
            evidence = pose(first, end, mass)
        except ValueError as exc:
            reason = str(exc)
            continue
        start = max(0, int(active[0]) - 1)
        return start, end, {"kind": "hold", "frames": count, "start": first,
                           "diameter": diameter, "max": STABLE_MAX, "normalization": "subject-mass",
                           "pose": evidence}
    raise ValueError(f"once has no stable end pose: {reason} ({count} frames, limit {STABLE_MAX})")


def given_ending(D: np.ndarray, masses: np.ndarray, fps: float, *, start: int, length: int) -> tuple[int, int, dict[str, Any]]:
    """The bounds the caller names (`video-loop --cycle once --start --length`): those frames played once, a move cut
    out of a longer motion (a vault in a run) that settles on no pose, so none is read."""
    if start < 0 or length < 2 or start + length > len(D):
        raise ValueError(f"--start {start} --length {length} does not fit {len(D)} frames")
    return start, start + length - 1, {"kind": "given", "start": start, "length": length}


def end_pose(state: str | None, frames: list[Image.Image], features: Callable[[Image.Image], np.ndarray]) -> EndPose:
    """The one judge of the pose a once cut ends on. A named terminal state (`STATES`) ends in its own body geometry
    (`terminal_pose`): lowered for `death` and `fall`, changed for the rest. Any other motion played once — a once-hold
    the request chose, `video-loop --cycle once` on a jump or on a word of your own — ends in a drawing other than its
    first (`drawing_pose`), whatever its body box does. `features` is the distance matrix's feature function, so a
    drawing's distance is on the scale of the subject mass."""
    boxes = [body_box(frame) for frame in frames]
    if state in STATES:
        return lambda first, end, mass: terminal_pose(boxes[:end + 1], count=end - first + 1, state=state)
    return drawing_pose(frames, boxes, features)


def drawing_pose(frames: list[Image.Image], boxes: list[Box | None],
                 features: Callable[[Image.Image], np.ndarray]) -> EndPose:
    """A motion with no terminal state of its own ends in a drawing other than its first. Each frame of the settled
    window is moved sideways until its body box is centred where the first frame's is, and its distance from the
    first frame (`features`) must be more than an action's departure, `STABLE_MAX * 2` of the subject mass, as
    `hold_ending` reads the action. The body box may keep its size and floor — a shield raised across the chest is a
    new pose — while the first drawing slid sideways is not one."""
    reference = features(frames[0])
    measured: dict[int, tuple[float, int]] = {}

    def distance(k: int) -> tuple[float, int]:
        if k not in measured:
            shift = round((boxes[0][0] + boxes[0][2] - boxes[k][0] - boxes[k][2]) / 2)
            moved = Image.new("RGBA", frames[k].size)
            moved.paste(frames[k], (shift, 0))
            measured[k] = float(np.abs(features(moved) - reference).mean()), shift
        return measured[k]

    def judge(first: int, end: int, mass: float) -> dict[str, Any]:
        if boxes[0] is None or any(b is None for b in boxes[first:end + 1]):
            raise ValueError("once needs a visible end pose")
        held = [distance(k) for k in range(first, end + 1)]
        departure = float(np.median([d for d, _ in held])) / mass
        if departure <= STABLE_MAX * 2:
            raise ValueError(f"once ends in the starting pose: its drawing, moved back to where it started, differs "
                             f"from the first frame by {departure:.3f} of the subject (an end pose differs by more "
                             f"than {STABLE_MAX * 2:g})")
        return {"profile": "drawing", "departure": departure, "departure_min": STABLE_MAX * 2,
                "shift_px": round(float(np.median([shift for _, shift in held])))}

    return judge


def terminal_pose(boxes: list[Box | None], *, count: int, state: str | None) -> dict[str, Any]:
    """A named terminal state's end pose (`end_pose`): compare body geometry, independent of color noise and horizontal
    drift."""
    first = boxes[0]
    tail = boxes[-count:]
    if first is None or any(b is None for b in tail):
        raise ValueError("once needs a visible end pose")
    height = first[3] - first[1]
    width = first[2] - first[0]
    last_height = float(np.median([b[3] - b[1] for b in tail]))
    last_width = float(np.median([b[2] - b[0] for b in tail]))
    height_ratio = last_height / height
    aspect_ratio = (last_width / last_height) / (width / height)
    floor_shift = abs(float(np.median([b[3] for b in tail])) - first[3]) / height
    if (abs(height_ratio - 1) <= POSE_SHAPE_TOLERANCE
            and abs(aspect_ratio - 1) <= POSE_SHAPE_TOLERANCE
            and floor_shift <= POSE_FLOOR_TOLERANCE):
        raise ValueError("once ends in the starting pose: body height, aspect and floor position have returned")
    if state in GROUND_STATES and height_ratio > GROUND_HEIGHT_MAX:
        raise ValueError(f"once: {state} needs a lowered ground pose; end/start body height {height_ratio:.3f} exceeds {GROUND_HEIGHT_MAX}")
    return {"profile": "ground" if state in GROUND_STATES else "changed",
            "height_ratio": height_ratio, "aspect_ratio": aspect_ratio, "floor_shift": floor_shift,
            "shape_tolerance": POSE_SHAPE_TOLERANCE, "floor_tolerance": POSE_FLOOR_TOLERANCE,
            **({"height_max": GROUND_HEIGHT_MAX} if state in GROUND_STATES else {})}


Ending = Callable[[np.ndarray, np.ndarray, float], tuple[int, int, dict[str, Any]]]


def select_cut(D: np.ndarray, masses: np.ndarray, *, fps: float, ending: Ending, kind: str = CYCLE_ONCE) -> dict[str, Any]:
    """One cut body; an ending (`hold_ending` with its `end_pose`, `effect.lifetime_ending`) returns inclusive bounds
    and its measured evidence."""
    start, end, terminal = ending(D, masses, fps)
    if not 0 <= start < end < len(D):
        raise ValueError(f"{kind}: invalid terminal cut [{start}, {end}]")
    energy = np.diag(D, 1)[start:end]
    event = start + int(np.argmax(energy)) + 1
    return {"kind": kind, "start": start, "length": end - start + 1,
            "end_frame": end, "event_frame": event, "terminal": terminal,
            "period_global": None, "ratio": 0.0, "review_recommended": False}


def samples(length: int, count: int) -> list[int]:
    """Inclusive endpoint schedule for a single-play strip or preview."""
    if count < 2 or length < 2:
        raise ValueError("a single-play cut needs at least two output frames")
    return [round(k * (length - 1) / (count - 1)) for k in range(count)]


def metadata(cycle: dict[str, Any], indices: list[int]) -> dict[str, Any]:
    """A single play's strip metadata: it plays once (`loop: false`); a `once` cut holds its last cell (`hold_last`),
    and a `once-return` cut starts and ends on its first pose (`returns`)."""
    event = cycle["event_frame"] - cycle["start"]
    return {"loop": False, "hold_last": cycle["kind"] == CYCLE_ONCE, **({"returns": True} if cycle["kind"] == CYCLE_ONCE_RETURN else {}),
            "end_frame": len(indices) - 1,
            "events": [{"frame": min(range(len(indices)), key=lambda k: abs(indices[k] - event)),
                        "name": "motion_peak"}]}


def on_floor(box: Box, floor: int) -> bool:
    """Whether a body box (`body_box`) stands on the floor: its lowest row within GROUND_PX of the floor's, the lowest
    row a body reaches. The strip's standing height is read on the frames that do (`loop.build_strip`), and a single
    play's contacts with the ground are (sprite_gen/video/root_motion.py)."""
    return box[3] >= floor - GROUND_PX


def body_box(frame: Any) -> Box | None:
    return frame.getchannel("A").point(lambda a: 255 if a >= 8 else 0).getbbox()


def validate_body(frames: list[Any]) -> None:
    """No retained body may leave its source canvas."""
    for k, frame in enumerate(frames):
        box = body_box(frame)
        if box is None:
            raise ValueError(f"once: subject is empty at frame {k}")
        if box[0] == 0 or box[1] == 0 or box[2] == frame.width or box[3] == frame.height:
            raise ValueError(f"once: subject touches canvas edge at frame {k}; enlarge the canvas and film again")
