# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-set` — directions x states, end to end, one report per item.

For every (direction, state) pair: canvas -> `sprite-gen video` -> frames -> loop.
Clip generation is rate-limited by the xAI team quota (2 requests/second measured
2026-09-08: five parallel POSTs produced two HTTP 429s), so starts are staggered
and a 429 gets a bounded, logged retry. Stages are idempotent — an item whose
clip already exists reuses it unless `--force` — and a failure stops only that
item, never the batch. The batch ends with `set.report.json` and `table.md`; an
item that failed is listed with its stage and error, never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.gen.chroma import KEY_BACKGROUND_TEXT
from sprite_gen.gen.facing import FACINGS, validate as validate_facing
from sprite_gen.gen import handedness as handed_mod
from sprite_gen.gen import prompt_parts
from sprite_gen.gen.handedness import Handed
from sprite_gen.video import facing as facing_mod
from sprite_gen.video import canvas as canvas_mod
from sprite_gen.video import frames as frames_mod
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import align as align_mod
from sprite_gen.video import rife as rife_mod

START_GAP_SECONDS = 2.0
# Clip length the batch asks the video model for. 3 s holds two or more cycles of every
# repeating state (walk periods measured at 0.6-1.3 s, 2026-09-18) and a single action
# for jump/attack, which the one-shot cut handles; 6 s bought nothing but a longer wait
# and, for jump, more idle standing between hops.
DEFAULT_DURATION_SECONDS = 3
# An attack is one timed strike: a windup, the strike, a held impact pose and the recovery, about
# 1.6 s at the stated timing. Two seconds holds it with the stance on either side; asked for 3 or
# 4 s, the clip still struck once and held the impact pose for about half of it.
STATE_DURATION_SECONDS = {"attack": 2}
# States whose clip is pinned to end on the frame it starts from (first-last mode): the model
# has to come back to the still, which closes a one-shot action instead of leaving it wherever
# the strike ended, and closes an idle without asking for a repeating rhythm.
PIN_LAST_FRAME_STATES = frozenset({"attack", "idle"})
# Pinned states whose whole clip is the loop: it starts and ends on the canvas, so `video-loop
# --cycle pinned` keeps every frame but the last (a re-render of the first). An attack asked
# for once is ready stance -> strike -> ready stance, so its whole clip is one attack too.
PINNED_LOOP_STATES = frozenset({"idle", "attack"})
# States filmed as a fast action (`ACTION_COMMON_TEXT`): crisp frames, what they hold kept inside
# the frame, and no "evenly paced" line.
ACTION_TEXT_STATES = frozenset({"attack"})
RETRY_BACKOFF_SECONDS = (15, 30)


def duration_for(state: str, requested: int | None) -> int:
    """Seconds to ask for: an explicit request wins, otherwise the state's own default."""
    return requested if requested is not None else STATE_DURATION_SECONDS.get(state, DEFAULT_DURATION_SECONDS)
VIEW_TEXT = {
    "side": "seen from the exact side, facing {facing}",
    "front": "seen from the front, facing the viewer directly",
    "back": "seen from directly behind, facing away from the viewer",
    # Three-quarter views, as an isometric game's character walks down and up to the right. The clip
    # starts from a still already drawn at that angle, so the clip's view sentence points at the image.
    "front_diagonal": "seen from a three-quarter front angle, turned exactly as in the image",
    "back_diagonal": "seen from a three-quarter back angle, turned exactly as in the image",
}
# The view sentence for drawing a still at a view, where it differs from the clip's: a diagonal still is
# often redrawn from a front or side picture, so "turned exactly as in the image" would keep the picture's
# angle. It says how far the body turns, that the head turns with it and which way the feet point; said
# less, a three-quarter back view came out as a side view or looking back over the shoulder. A front or
# back still redrawn from a side picture with the clip's one line kept the picture's turn in the head and
# chest, so those two say where the head, chest and feet point and not to follow the picture's angle.
STILL_VIEW_TEXT = {
    "front": (
        "seen from the front: the whole body and head turned to face the viewer squarely, the chest, hips and the toes "
        "of both feet pointing straight at the viewer and the face centred between both ears, looking straight out of "
        "the image, not turned toward either side even when a reference picture shows the character from another angle"
    ),
    "back": (
        "seen from directly behind: the whole body and head turned fully away from the viewer, the back of the head, "
        "the shoulders, the hips and the heels of both feet facing straight at the viewer, the face completely hidden "
        "and not turned toward either side or looking back over the shoulder, even when a reference picture shows the "
        "character from another angle"
    ),
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
DIAGONAL_VIEWS = frozenset({"front_diagonal", "back_diagonal"})


def still_view_text(direction: str, facing: str = "right") -> str:
    """The view sentence for drawing a still seen from `direction` (`STILL_VIEW_TEXT`, else `VIEW_TEXT`)."""
    validate_facing(facing)
    return STILL_VIEW_TEXT.get(direction, VIEW_TEXT[direction]).format(facing=facing)
# What stays put while an attack moves, said once: after the built-in attack sentence and after a
# caller's own motion paragraph alike. A request interpreter writes its motion before the still
# exists, so it cannot know where the other hand's shield or lantern is drawn; the engine holds it.
HOLD_TEXT = {
    "attack": (
        "Every grip stays exactly as shown in the image: one hand stays one hand, both hands stay both hands, "
        "and nothing is let go or switched to the other hand. A hand the motion does not use stays where it is "
        "drawn, with anything it holds, and the body keeps facing the same direction without turning."
    ),
}
# What stays put while a caller's own walk or run moves, by view: in place, and facing the way the image
# faces. A request interpreter may ask for any gait (a sneak, a march, a stroll); the engine keeps it on the
# spot and turned the right way, as the built-in gait sentences do. `{facing}` is the side view's.
GAIT_STATES = frozenset({"walk", "run"})
GAIT_HOLD_TEXT = {
    "front": "It stays in place as if on a treadmill, without coming any closer, and keeps facing the viewer the whole time.",
    "back": (
        "It stays in place as if on a treadmill, without moving any farther away, and keeps facing away from the viewer "
        "the whole time."
    ),
    "side": "It stays in place as if on a treadmill, without moving across the screen, and keeps facing {facing} the whole time.",
    "front_diagonal": (
        "It stays in place as if on a treadmill, without moving across the screen, and keeps the exact three-quarter front "
        "angle of the image the whole time, never turning into a side view."
    ),
    "back_diagonal": (
        "It stays in place as if on a treadmill, without moving across the screen, and keeps the exact three-quarter back "
        "angle of the image the whole time, its face hidden, never turning into a side view."
    ),
}
MOTION_TEXT = {
    # A side-view full body asked for "a subtle weight sway" and an evenly paced loop steps in place
    # more often than not, so the feet are held and walking is named as what not to do.
    "idle": (
        "stands still in a relaxed idle pose with both feet planted flat on the ground for the whole clip: "
        "slow, gentle breathing that softly rises and falls in the chest and shoulders, a slight settle of the arms, "
        "hair and loose cloth, and one natural blink if the face has eyes. The feet never lift, step, shuffle or slide "
        "— no walking, no marching in place, no turning."
    ),
    "walk": "walks naturally in place, as if on a treadmill, without moving across the screen.",
    "run": "runs naturally in place, as if on a treadmill, without moving across the screen.",
    "jump": "performs a modest vertical hop in place over and over: compress, spring up about half the body height, land softly, return to the exact starting stance, repeat at an even rhythm. Same height every time.",
    "attack": (
        "performs one melee attack with what it is already holding (bare hands only if it holds nothing), keeping "
        "every piece of its gear and outfit exactly as drawn: a windup (about 0.5 s), one clean strike in front "
        "(about 0.25 s), a held impact pose (about 0.3 s), then a recovery to the exact starting stance (about 0.5 s). "
        + HOLD_TEXT["attack"]
    ),
    "cheer": "celebrates in place: rises into a raised, spread-out cheer pose, holds it for a beat, then settles back to the exact starting stance, repeating at an even rhythm.",
    "wave": "waves in place: lifts one side into a friendly wave, sways it a few times, then settles back to the exact starting stance, repeating at an even rhythm.",
}
# A walk or run seen from the front or from behind, in place of the side view's sentences above. Every
# gait says only that it moves naturally, which way it faces and that it stays in place. Asked for "an
# even left-right or front-back rhythm", a walk that faces the viewer stepped sideways or turned the body
# (2.13 fixed the facing by naming it); spelling the steps out ("each one lifting and landing straight
# forward and back") then made some takes march in place, knees up to the waist and arms stiff, while
# "walks naturally" swung the arms and kept the knees low (2.15). 2.16 gives the run and the side view the
# same form. How a character walks is the caller's to say (`build_prompt(motion=...)`, held in place by
# `GAIT_HOLD_TEXT`); the engine's default stays generic. "as if on a treadmill" keeps the treadmill a
# comparison: said as a place, it was sometimes drawn under the feet.
VIEW_MOTION_TEXT = {
    ("walk", "front"): "walks naturally in place, facing the viewer, as if on a treadmill, without coming any closer.",
    ("walk", "back"): (
        "walks naturally in place, facing away from the viewer, as if on a treadmill, without moving any farther away."
    ),
    ("run", "front"): "runs naturally in place, facing the viewer, as if on a treadmill, without coming any closer.",
    ("run", "back"): (
        "runs naturally in place, facing away from the viewer, as if on a treadmill, without moving any farther away."
    ),
    # A diagonal says where the character is heading on the screen, the way an isometric game reads, and that it
    # keeps the image's angle. Asked only to walk "toward the way its body faces in the image", a three-quarter
    # back walk turned to a side view in 2 of 2 clips; named as a heading up and to the right, in 10 of 10 it
    # kept the angle (2026-10-02, five people twice each, pinned). `{facing}` is the way the view is turned: a
    # view drawn facing left heads down or up and to the left (it said "to the right" whatever the facing).
    ("walk", "front_diagonal"): (
        "walks naturally in place, as if on a treadmill, heading diagonally toward the viewer and to the {facing}, like a "
        "character walking down and to the {facing} in an isometric game, without moving across the screen. It keeps the "
        "exact three-quarter front angle of the image the whole time and never turns into a side view."
    ),
    ("walk", "back_diagonal"): (
        "walks naturally in place, as if on a treadmill, heading diagonally away from the viewer toward the upper {facing}, "
        "like a character walking up and to the {facing} in an isometric game, without moving across the screen. It keeps "
        "the exact three-quarter back angle of the image the whole time: its back stays turned toward the viewer at that "
        "angle and its face stays hidden. It never turns into a side view."
    ),
    ("run", "front_diagonal"): (
        "runs naturally in place, as if on a treadmill, heading diagonally toward the viewer and to the {facing}, like a "
        "character running down and to the {facing} in an isometric game, without moving across the screen. It keeps the "
        "exact three-quarter front angle of the image the whole time and never turns into a side view."
    ),
    ("run", "back_diagonal"): (
        "runs naturally in place, as if on a treadmill, heading diagonally away from the viewer toward the upper {facing}, "
        "like a character running up and to the {facing} in an isometric game, without moving across the screen. It keeps "
        "the exact three-quarter back angle of the image the whole time: its back stays turned toward the viewer at that "
        "angle and its face stays hidden. It never turns into a side view."
    ),
}
# Views a walk or run is filmed pinned to its first frame in (`video --last-frame`), with the pinned template:
# a diagonal walk ended where it began, and its angle with it. Cut as any walk (`video-loop --anchor motion-auto`).
PINNED_GAIT_VIEWS = DIAGONAL_VIEWS


# Clip models that read a walk sentence bigger than it is written. The lower-priced Lite model took
# "walks naturally" as long, bouncy steps and a wide arm swing — it ran (2026-10-03, an SD girl in
# five views, two takes each); a calming clause after the walk sentence brought it back to a walk.
LITE_VIDEO_MODELS = frozenset({"grok-imagine-video-1.5-lite"})
LITE_WALK_TEXT = (
    "A slow, relaxed, unhurried walk: small, low steps with the feet barely leaving the ground, a gentle arm swing "
    "close to the body and no bounce — never running, jogging, skipping or hopping."
)
# Lite's back-diagonal walk swayed its head 2.7-3.8 % of the body height side to side against Pro's
# 0.88 %; with this sentence four takes swayed 1.7-3.1 % (2026-10-03). Measured on that view only.
LITE_HEAD_TEXT = {
    "back_diagonal": (
        "The head stays level and steady over the body the whole time: it does not bob, nod, tilt or sway from side to "
        "side; only the legs, arms and the ends of the hair move."
    ),
}


def is_lite(model: str | None) -> bool:
    return model in LITE_VIDEO_MODELS


def pins_last_frame(state: str, direction: str) -> bool:
    """Whether the clip of (state, direction) is filmed ending on its first frame."""
    return state in PIN_LAST_FRAME_STATES or (state in GAIT_STATES and direction in PINNED_GAIT_VIEWS)
COMMON_TEXT = (
    "2D game sprite animation. The character {motion} The character is {view}. Stays centered in the frame and does "
    "not move across the screen; the body and hair always stay fully inside the frame with margin. Camera completely "
    "locked, no zoom, no pan, no reframing. The background stays a perfectly flat, pure chroma-key fill for the whole "
    "clip — no shadows, no ground line, no particles, no lighting changes, no effects. Keep the design, colors and "
    "proportions exactly as in the image. Consistent, evenly paced motion so the animation loops."
)

# A pinned loop (`PINNED_LOOP_STATES`) closes because the clip ends on its first frame, not by
# repeating a motion at an even rhythm — so it asks for the return instead of the rhythm.
PINNED_LOOP_TEXT = COMMON_TEXT.replace(
    "Consistent, evenly paced motion so the animation loops.",
    "The last frame returns to the exact pose of the first frame, so the animation loops seamlessly.",
)

# One-shot actions pinned to their first frame (`PIN_LAST_FRAME_STATES`). No "evenly paced" line:
# a strike is fast and a windup is not, so the timing lives in the motion sentence instead. What the
# character holds is kept inside the frame too, and the fast frames are asked to stay crisp.
ACTION_COMMON_TEXT = (
    "2D game sprite animation. The character {motion} The character is {view}. Stays centered in the frame and does "
    "not move across the screen; the body, hair and anything it holds always stay fully inside the frame with margin. "
    "Camera completely locked, no zoom, no pan, no reframing. The background stays a perfectly flat, pure chroma-key fill "
    "for the whole clip — no shadows, no ground line, no particles, no lighting changes, no effects. Keep the design, "
    "colors and proportions exactly as in the image. Crisp, clean frames with no motion blur, no smears and no afterimages."
)
# How many times a caller's own motion paragraph (`build_prompt(motion=...)`) is performed, for
# states that repeat it. An attack is performed once: its pinned clip is the loop.
REPEAT_TEXT: dict[str, str] = {}

_start_lock = threading.Lock()
_last_start = [0.0]


def _staggered_start(gap: float) -> None:
    with _start_lock:
        wait = _last_start[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_start[0] = time.monotonic()


def build_prompt(direction: str, state: str, character: str | None, facing: str = "right", motion: str | None = None,
                 pinned: bool | None = None, model: str | None = None, handed: list[Handed] | None = None) -> str:
    """The clip prompt for one (direction, state): `clip_prompt_parts(...).text`."""
    return clip_prompt_parts(direction, state, character, facing=facing, motion=motion, pinned=pinned, model=model,
                             handed=handed).text


def clip_prompt_parts(direction: str, state: str, character: str | None, facing: str = "right", motion: str | None = None,
                      pinned: bool | None = None, model: str | None = None,
                      handed: list[Handed] | None = None) -> prompt_parts.Prompt:
    """The clip prompt for one (direction, state), as its pieces (`prompt_parts.Prompt`: `.text` is the
    prompt, `.notes` what the caller's own words say against or again after the engine's).

    `motion` replaces the built-in state sentence with the caller's own description of the
    motion, written as whole sentences about the subject (a request interpreter's output, for
    instance). What stays put (`HOLD_TEXT`; for a walk or run, in place and facing the image's way,
    `GAIT_HOLD_TEXT`), the repeat count (`REPEAT_TEXT`) and the frame, camera, background and
    design rules stay the engine's.

    `pinned` says the clip is pinned to end on its first frame (`video --last-frame`): it then asks
    for the return to the first pose instead of an evenly paced repeat (`PINNED_LOOP_TEXT`). None
    leaves it to the state and view (`PINNED_LOOP_STATES`, a walk or run in `PINNED_GAIT_VIEWS`);
    a caller that pins any other walk or run says True.

    `model` is the clip model the prompt is for. A Lite walk (`LITE_VIDEO_MODELS`) gets the calming
    clause after the built-in walk sentence (`LITE_WALK_TEXT`; a caller's own motion is left as
    written), and a Lite walk in a view where its head sways gets `LITE_HEAD_TEXT`.

    Without `handed` the prompt is 2.22.0's, to the byte (`tests/gen/test_prompt_freeze.py`). `handed` lists
    the character's asymmetric items (`handedness.parse`): the prompt ends with where each one stays, anchored
    to the first frame (`handedness.text(clip=True)`), the one piece `prompt_parts.Prompt.add` checks.
    """
    validate_facing(facing)
    view = VIEW_TEXT.get(direction, f"seen from the {direction}").format(facing=facing)
    turned = facing if direction in handed_mod.LATERAL_VIEWS else None
    if state in ACTION_TEXT_STATES:
        template = ACTION_COMMON_TEXT
    elif (state in PINNED_LOOP_STATES or (state in GAIT_STATES and direction in PINNED_GAIT_VIEWS)) if pinned is None else pinned:
        template = PINNED_LOOP_TEXT
    else:
        template = COMMON_TEXT
    if motion is not None:
        motion = " ".join(motion.split())
        if not motion:
            raise SystemExit("video: motion description is empty")
        head = "2D game sprite animation. The character {motion} The character is {view}."
        hold = f" {HOLD_TEXT[state]}" if state in HOLD_TEXT else ""
        if state in GAIT_STATES and direction in GAIT_HOLD_TEXT:
            hold += f" {GAIT_HOLD_TEXT[direction].format(facing=facing)}"
        repeat = f" {REPEAT_TEXT[state]}" if state in REPEAT_TEXT else ""
        subject = character.strip().rstrip(".") if character else "The character"
        text = (f"2D game sprite animation. {motion}{hold}{repeat} {subject} is {view}." + template[len(head):]
                + _lite_text(state, direction, model, built_in=False))
        caller = f"{motion} {character or ''}"
    else:
        built_in = (VIEW_MOTION_TEXT[(state, direction)].format(facing=facing) if (state, direction) in VIEW_MOTION_TEXT
                    else MOTION_TEXT.get(state, f"performs the '{state}' action in place, repeating at an even rhythm."))
        text = template.format(motion=built_in, view=view)
        text = ((text.replace("The character", character, 1) if character else text)
                + _lite_text(state, direction, model, built_in=True))
        caller = character or ""
    parts = prompt_parts.Prompt(text, caller=caller, sep=" ")
    # the caller's words against the view the engine's sentence turns the clip to (a note, never an edit)
    parts.note(facing=turned or prompt_parts.NO_TURN)
    if handed:
        parts.add("handed", handed_mod.text(handed, direction, turned, clip=True, gait=state in GAIT_STATES), handed=handed)
    return parts


def _lite_text(state: str, direction: str, model: str | None, *, built_in: bool) -> str:
    """What a Lite walk adds after the prompt, in the order it was measured: the calm walk, then the head."""
    if state != "walk" or not is_lite(model):
        return ""
    text = f" {LITE_WALK_TEXT}" if built_in else ""
    if direction in LITE_HEAD_TEXT:
        text += f" {LITE_HEAD_TEXT[direction]}"
    return text


# A walk seen from the front or from behind starts from a still redrawn mid-step. From a standing
# still the clip model makes the first step itself and walks askew — the feet drawn to one line, or
# the body turned three-quarters — 1 of 14 front clips straight; from a mid-step still, 14 of 16
# (2026-10-02, four characters, blind judged). The sentence redraws the base still with a reference;
# the design is the reference's to keep, the background the caller's to say (`walk_start_prompt`).
WALK_START_TEXT = {
    "front": (
        "Redraw the character in the attached image as the same 2D game sprite, keeping the design, outfit, colors, body "
        "proportions and art style exactly as they are. Only the pose changes: the character is caught mid-step while "
        "walking straight toward the viewer, facing the viewer directly with the hips and shoulders square. One foot is "
        "planted flat directly under its own hip; the other foot is lifted a little with the knee slightly bent, directly "
        "under its own hip, so the feet stay hip-width apart with a clear gap between the legs. The arms swing gently "
        "opposite to the legs, hands as in the image. Full body, centered, seen at eye level, with generous empty margin "
        "above the head and below the feet."
    ),
    "back": (
        "Redraw the character in the attached image as the same 2D game sprite, keeping the design, outfit, colors, hair, "
        "body proportions and art style exactly as they are, and keeping the same view: seen directly from behind, the back "
        "of the character toward the viewer. Only the pose changes: the character is caught mid-step while walking straight "
        "away from the viewer, with the hips and shoulders square to the viewer and the body not turned to either side. One "
        "foot is planted flat directly under its own hip; the other foot is lifted a little with the knee slightly bent, "
        "directly under its own hip, so the feet stay hip-width apart with a clear gap between the legs. The arms swing "
        "gently opposite to the legs, hands as in the image. Full body, centered, seen at eye level, with generous empty "
        "margin above the head and below the feet; all of the hair stays well inside the image."
    ),
}
WALK_START_MODES = ("redraw", "as-given")


def starts_mid_step(state: str, direction: str) -> bool:
    """Whether the clip of (state, direction) starts from a still redrawn mid-step (`WALK_START_TEXT`)."""
    return state == "walk" and direction in WALK_START_TEXT


def walk_start_prompt(direction: str, key: str | None = None, handed: list[Handed] | None = None) -> str:
    """The redraw sentence for a walk's mid-step start still; with `key` (green / magenta) the background line,
    then with `handed` where each asymmetric item is in this view (`handedness.text`, the still's sentences)."""
    if direction not in WALK_START_TEXT:
        raise SystemExit(f"video: no mid-step start still for the {direction} view (only {', '.join(WALK_START_TEXT)})")
    text = WALK_START_TEXT[direction]
    if key is not None:
        if key not in KEY_BACKGROUND_TEXT:
            raise SystemExit(f"video: a mid-step start still is drawn on a green or magenta key, not {key!r}")
        text += f" {KEY_BACKGROUND_TEXT[key]}"
    parts = prompt_parts.Prompt(text, caller="", sep=" ")
    if handed:
        parts.add("handed", handed_mod.text(handed, direction), handed=handed)
    return parts.text


def run_video_cli(image: Path, prompt: str, out: Path, report: Path, *, duration: int, resolution: str, log: Path, last_frame: Path | None = None) -> int:
    cmd = [sys.executable, "-m", "sprite_gen.gen.video", "--image", str(image), "--prompt", prompt, "--out", str(out), "--duration", str(duration), "--resolution", resolution, "--no-audio", "--report", str(report)]
    if last_frame is not None:
        cmd += ["--last-frame", str(last_frame)]
    with log.open("w", encoding="utf-8") as fh:
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True).returncode


def run_redraw_cli(base: Path, prompt: str, out: Path, report: Path, *, log: Path, provider: str | None = None) -> int:
    """`sprite-gen gen` with the base still as the reference: one redrawn still, its report beside it."""
    cmd = [sys.executable, "-m", "sprite_gen.cli", "gen", "--ref", str(base), "--prompt", prompt, "--out", str(out), "--report", str(report)]
    if provider:
        cmd += ["--provider", provider]
    with log.open("w", encoding="utf-8") as fh:
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True).returncode


def walk_start_still(base: Path, direction: str, item_dir: Path, *, key: str, force: bool,
                     runner: Callable[..., int] = run_redraw_cli, provider: str | None = None,
                     handed: list[Handed] | None = None) -> tuple[Path, dict[str, Any]]:
    """The base still redrawn mid-step for a front or back walk (`WALK_START_TEXT`), on the base's own key,
    told where each `handed` item is (a redraw from a reference alone can move it to the other side).

    Reused when the same prompt already drew it from the same base (unless `force`)."""
    with Image.open(base) as im:
        kind = canvas_mod.resolve_key(canvas_mod.corner_key(im.convert("RGB")), key)
    if kind not in KEY_BACKGROUND_TEXT:
        raise SystemExit(f"the {direction} walk starts from a still redrawn mid-step on a green or magenta key, and this "
                         f"base's corners are not one (key {key}); pass --walk-start as-given to film from the base itself")
    prompt = walk_start_prompt(direction, kind, handed)
    out, report = item_dir / "walk-start.png", item_dir / "walk-start.report.json"
    if out.is_file() and report.is_file() and not force:
        try:
            prior = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prior = {}
        if prior.get("prompt") == prompt and [str(Path(r).resolve()) for r in prior.get("refs") or []] == [str(base.resolve())]:
            return out, {"redrawn": True, "reused": True, "still": str(out), "key": kind}
    rc = runner(base, prompt, out, report, log=item_dir / "walk-start.log", provider=provider)
    if rc != 0 or not out.is_file():
        raise SystemExit(f"the mid-step start still for the {direction} walk was not drawn; see {item_dir / 'walk-start.log'}")
    return out, {"redrawn": True, "reused": False, "still": str(out), "key": kind, "report": str(report)}


def run_item(
    *,
    item: str,
    direction: str,
    state: str,
    base: Path,
    root: Path,
    character: str | None,
    duration: int | None,
    resolution: str,
    key: str,
    force: bool,
    gap: float,
    video_runner: Callable[..., int] = run_video_cli,
    shape: str | None = None,
    anchor: str = "none",
    spill: str = "auto",
    facing: str = "right",
    facing_fix: str = "none",
    prepare_side: Callable[[Path], tuple[Path, dict]] | None = None,
    body_height: int | None = None,
    fit: str = "state",
    decontam: str = "off",
    walk_start: str = "redraw",
    redraw_runner: Callable[..., int] = run_redraw_cli,
    still_provider: str | None = None,
    handed: list[Handed] | None = None,
) -> dict[str, Any]:
    if walk_start not in WALK_START_MODES:
        raise SystemExit(f"video-set: --walk-start must be one of {', '.join(WALK_START_MODES)}")
    if anchor == "motion-auto" and not loop_mod.profile_for(state).gait:
        raise SystemExit("video-set: --anchor motion-auto requires walk/run states")
    if anchor == "motion":
        raise SystemExit("video-set: motion anchor requires reviewed regions; use video-loop --cycle fixed")
    validate_facing(facing, facing_fix)
    if facing_fix not in facing_mod.FIXES:
        raise SystemExit("video-set: --facing-fix must be mirror or none")
    _refuse_mirror_with_handed(facing_fix, handed)
    duration = duration_for(state, duration)
    item_dir = root / item
    item_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"item": item, "direction": direction, "state": state, "dir": str(item_dir)}
    if direction in handed_mod.LATERAL_VIEWS:
        result["turned"] = facing
    try:
        parts = clip_prompt_parts(direction, state, character, facing=facing, handed=handed)
        prompt = parts.text
        if parts.notes:
            result["prompt_notes"] = parts.notes
        needs = handed_mod.first_frame_needs(handed or [], direction, facing if direction in handed_mod.LATERAL_VIEWS else None,
                                             gait=state in GAIT_STATES)
        if needs:
            result["still_needs"] = needs
        clip = item_dir / "clip.mp4"
        clip_report = item_dir / "clip.report.json"
        reuse_clip = clip.exists() and clip_report.exists() and not force
        if reuse_clip:
            try:
                previous = json.loads(clip_report.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SystemExit("cannot verify cached clip prompt; use --force to regenerate") from exc
            if not isinstance(previous, dict) or previous.get("prompt") != prompt:
                raise SystemExit("cached clip prompt differs (including facing); use --force to regenerate")
        canvas_png = item_dir / "canvas.png"
        canvas_report_path = item_dir / "canvas.report.json"
        if reuse_clip:
            try:
                canvas_report = json.loads(canvas_report_path.read_text(encoding="utf-8"))
                if not isinstance(canvas_report, dict) or not canvas_png.is_file():
                    raise ValueError("missing canvas")
                if not all(key in canvas_report for key in ("shape", "canvas", "offset")):
                    raise ValueError("incomplete canvas report")
                if canvas_report.get("fit", "state") != fit:
                    raise ValueError("cached canvas was framed with another --fit")
                if direction == "side":
                    prior = canvas_report.get("facing_check") or {}
                    if not isinstance(prior, dict):
                        raise ValueError("invalid cached facing")
                    if (prior.get("requested") != facing or prior.get("fix") != facing_fix
                            or prior.get("source_sha256") != facing_mod.digest(base)):
                        raise ValueError("unverified cached facing")
            except (OSError, ValueError) as exc:
                raise SystemExit("cannot verify cached canvas/facing; use --force to regenerate") from exc
        else:
            still = base
            facing_report = None
            if starts_mid_step(state, direction) and walk_start == "redraw":
                still, result["walk_start"] = walk_start_still(base, direction, item_dir, key=key, force=force,
                                                               runner=redraw_runner, provider=still_provider, handed=handed)
            if direction == "side":
                if prepare_side is not None:
                    still, facing_report = prepare_side(base)
                else:
                    still = item_dir / "facing.png"
                    facing_report = facing_mod.prepare_still(base, still, facing=facing, fix=facing_fix)
            canvas_report = canvas_mod.run_canvas(still, canvas_png, state=state, shape=shape, facing=facing if direction not in ("front", "back") else "right", headroom=None, lead=None, report_path=canvas_report_path, fit=fit)
            if facing_report is not None:
                canvas_report["facing_check"] = facing_report
                atomic_write_text(canvas_report_path, json.dumps(canvas_report, ensure_ascii=False, indent=2) + "\n")
        result["canvas"] = {k: canvas_report[k] for k in ("fit", "shape", "canvas", "offset") if k in canvas_report}
        if "facing_check" in canvas_report:
            result["facing"] = canvas_report["facing_check"]

        if reuse_clip:
            result["clip"] = {"reused": True}
        else:
            attempts: list[int] = []
            for attempt in range(1 + len(RETRY_BACKOFF_SECONDS)):
                _staggered_start(gap)
                pin = {"last_frame": canvas_png} if pins_last_frame(state, direction) else {}
                rc = video_runner(canvas_png, prompt, clip, clip_report, duration=duration, resolution=resolution, log=item_dir / "clip.log", **pin)
                attempts.append(rc)
                if rc == 0 and clip.exists():
                    break
                text = (item_dir / "clip.log").read_text(encoding="utf-8", errors="replace") if (item_dir / "clip.log").exists() else ""
                if "HTTP 429" in text and attempt < len(RETRY_BACKOFF_SECONDS):
                    time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                    continue
                break
            result["clip"] = {"attempts": attempts, "duration": duration, "last_frame": pins_last_frame(state, direction)}
            if attempts[-1] != 0 or not clip.exists():
                raise SystemExit(f"clip generation failed after {len(attempts)} attempt(s); see {item_dir / 'clip.log'}")

        # a tight frame is clipped on purpose; leftover key background still fails. The default
        # call keeps its pre-decontam signature, so a stand-in for run_frames still fits
        extra = {} if decontam == "off" else {"decontam": decontam}
        fr = frames_mod.run_frames(clip, item_dir / "frames", key=key, allow_edge_contact=False, report_path=item_dir / "frames.report.json", spill=spill, reference=canvas_png, allow_subject_edge_contact=fit == "tight", **extra)
        result["frames"] = {k: fr[k] for k in ("fps", "frames", "alpha_zero_pct_min", "alpha_zero_pct_max")}
        result["frames"]["spill"] = fr.get("spill", {}).get("mode")
        lp = loop_mod.run_loop(Path(fr["keyed_dir"]), item_dir / "loop", fps=float(fr["fps"]), state=state, min_len=None, max_len=None, n_out=None, seam_max=loop_mod.SEAM_RATIO_MAX, name=item, report_path=item_dir / "loop.report.json", anchor=anchor, body_height=body_height,
                               cycle_mode="pinned" if state in PINNED_LOOP_STATES else "auto",
                               facing=facing if direction in handed_mod.LATERAL_VIEWS else "right")
        result["loop"] = {"kind": lp["cycle"].get("kind", "periodic"), "cycle": lp["cycle"]["length"], "period": lp["cycle"]["period_global"], "cycle_ratio": round(lp["cycle"]["ratio"], 3), "seam_ratio": lp["resampled_seam_ratio"], "n_out": lp["n_out"], "drift_px": lp["strip"].get("drift_px", 0), "gif": lp["gif"]["file"], "webp": lp["webp"]["file"], "strip": lp["strip"]["path"]}
        result["loop"]["review_recommended"] = lp["cycle"].get("review_recommended", False)
        result["loop"]["half_period_guard"] = lp["cycle"].get("half_period_guard")
        if lp.get("motion_anchor", {}).get("applied") is False:
            result["loop"]["motion_anchor"] = lp["motion_anchor"]
        if "rife" in (lp.get("jump_repair") or {}):
            # cut as filmed for want of RIFE: the set report lists it under `warnings`
            result["loop"]["jump_repair"] = {k: lp["jump_repair"][k] for k in ("applied", "why", "score_max_before", "install")}
        result["ok"] = True
    except SystemExit as exc:
        result["ok"] = False
        result["error"] = str(exc)
    return result


def _refuse_mirror_with_handed(facing_fix: str, handed: list[Handed] | None) -> None:
    if handed and facing_fix == "mirror":
        raise SystemExit("video-set: --facing-fix mirror turns the picture over and moves every --handed item to the "
                         "other side; draw the still facing the requested way instead (docs/video-pipeline.md#handedness--an-item-on-one-side)")


def facings_of(facing: str) -> tuple[str, ...]:
    """`--facing`: right, left, or both as `right,left` — every lateral view is then filmed both ways."""
    out = tuple(dict.fromkeys(f.strip() for f in str(facing).split(",") if f.strip()))
    if not out or any(f not in FACINGS for f in out):
        raise SystemExit(f"video-set: --facing must be right, left or right,left, got {facing!r}")
    return out


def resolve_bases(bases: dict[str, Path], facings: tuple[str, ...]) -> list[tuple[str, str | None, Path]]:
    """`--base` keys -> (direction, facing, still). A lateral view (`handedness.LATERAL_VIEWS`) takes the set's
    facing, or `view@facing` names it; filmed both ways, every lateral view needs a still for each facing, drawn
    that way: the engine never turns one over for the other. Front and back have no facing."""
    out: list[tuple[str, str | None, Path]] = []
    for key, path in bases.items():
        direction, _, named = key.partition("@")
        if direction not in VIEW_TEXT:
            raise SystemExit(f"video-set: --base direction must be one of {', '.join(sorted(VIEW_TEXT))}, got {direction!r}")
        if direction not in handed_mod.LATERAL_VIEWS:
            if named:
                raise SystemExit(f"video-set: the {direction} view is not turned to a side; drop @{named}")
            out.append((direction, None, path))
        elif named:
            if named not in facings:
                raise SystemExit(f"video-set: --base {key} faces {named}, and the set is filmed facing {','.join(facings)}")
            out.append((direction, named, path))
        elif len(facings) > 1:
            raise SystemExit(f"video-set: filmed facing right and left, the {direction} view needs a still for each: "
                             f"--base {direction}@right=… --base {direction}@left=…")
        else:
            out.append((direction, facings[0], path))
    seen = [(d, f) for d, f, _ in out]
    if len(set(seen)) != len(seen):
        raise SystemExit("video-set: a direction is given twice for the same facing")
    for d in {d for d, f, _ in out if f is not None}:
        missing = [f for f in facings if (d, f) not in seen]
        if missing:
            raise SystemExit(f"video-set: the {d} view has no still facing {missing[0]} (--base {d}@{missing[0]}=…); draw "
                             f"it that way (gen --direction {d} --facing {missing[0]}) — the engine does not mirror one")
    return out


ALIGN_MODES = ("auto", "off")


def align_gaits(results: list[dict[str, Any]], root: Path, mode: str, *, interpolate: Any = None) -> dict[str, Any]:
    """One cycle length per gait state across the set's directions (`video-cycle-align`,
    docs/loop-repair.md section 4). A state filmed in fewer than two directions has nothing to
    match. A failure is recorded under its state and the batch reports it; the loops stay as cut.
    Where a frame is to be made and no RIFE is installed the state is skipped, not failed: each
    loop keeps its own length, the record says why, and a warning line names the install."""
    out: dict[str, Any] = {}
    if mode == "off":
        return out
    by_state: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        if r.get("ok") and loop_mod.profile_for(r["state"]).gait:
            by_state.setdefault(r["state"], []).append(r)
    for state, rows in by_state.items():
        if len(rows) < 2:
            continue
        try:
            report = align_mod.align_set([Path(r["dir"]) / "loop" for r in rows], interpolate=interpolate,
                                         report_path=root / f"{state}.cycle-align.json")
        except rife_mod.RifeNotInstalled as exc:
            out[state] = {"ok": True, "applied": False, "why": "RIFE not installed — each loop keeps its own length",
                          "rife": str(exc), "install": rife_mod.INSTALL_COMMAND}
            print(f"video-set: warning: {state}: cycles not aligned — RIFE is not installed, so each loop keeps its own "
                  f"length; run `{rife_mod.INSTALL_COMMAND}`, then `sprite-gen video-cycle-align` (docs/loop-repair.md)",
                  file=sys.stderr)
            continue
        except SystemExit as exc:
            out[state] = {"ok": False, "error": str(exc)}
            continue
        out[state] = {"ok": True, "applied": True, "length": report["length"], "lengths": report["lengths"], "made_by_rife": report["made_by_rife"],
                      "report": str(root / f"{state}.cycle-align.json")}
        for r, row in zip(rows, report["loops"]):
            r["loop"]["cycle_align"] = {k: row[k] for k in ("from", "to", "made_by_rife", "turned_by", "seam_ratio")}
            r["loop"]["n_out"] = row["strip"]["frames"]
    return out


def _view_label(r: dict[str, Any], both: bool) -> str:
    return f"{r['direction']} ({r['turned']})" if both and r.get("turned") else r["direction"]


def write_table(results: list[dict[str, Any]], path: Path, *, both: bool = False) -> str:
    """`both`: the set is filmed facing right and left, so a lateral view's row names its facing."""
    lines = ["| direction | state | kind | cycle | period | seam | frames | status |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r.get("ok"):
            lp = r["loop"]
            status = "OK (review gait)" if lp.get("review_recommended") else "OK"
            if lp.get("motion_anchor", {}).get("applied") is False:
                status = "OK (uncorrected; review gait)"
            if "jump_repair" in lp:
                status += " (jump not repaired: no RIFE)"
            lines.append(f"| {_view_label(r, both)} | {r['state']} | {lp.get('kind', 'periodic')} | {lp['cycle']} | {lp['period'] if lp['period'] is not None else '-'} | {lp['seam_ratio']:.2f} | {lp['n_out']} | {status} |")
        else:
            lines.append(f"| {_view_label(r, both)} | {r['state']} | - | - | - | - | - | FAIL: {r.get('error', '')[:80]} |")
    text = "\n".join(lines) + "\n"
    atomic_write_text(path, text)
    return text


def run_set(
    *,
    bases: dict[str, Path],
    states: list[str],
    root: Path,
    character: str | None,
    duration: int | None,
    resolution: str,
    key: str,
    concurrency: int,
    force: bool,
    gap: float,
    video_runner: Callable[..., int] = run_video_cli,
    shape: str | None = None,
    anchor: str = "none",
    spill: str = "auto",
    facing: str = "right",
    facing_fix: str = "none",
    body_height: int | None = None,
    fit: str = "state",
    decontam: str = "off",
    align_cycles: str = "auto",
    interpolate: Any = None,
    walk_start: str = "redraw",
    redraw_runner: Callable[..., int] = run_redraw_cli,
    still_provider: str | None = None,
    handed: list[Handed] | None = None,
) -> dict[str, Any]:
    """`bases` maps a view to its still; `facing` is right, left or `right,left` (`facings_of`), and a view filmed
    both ways is keyed `view@right` and `view@left` (`resolve_bases`). `handed` (`handedness.parse`) puts each
    asymmetric item's side into every clip prompt; it refuses `facing_fix` mirror, which would move it."""
    if align_cycles not in ALIGN_MODES:
        raise SystemExit(f"video-set: --align-cycles must be one of {', '.join(ALIGN_MODES)}")
    if anchor == "motion-auto" and any(not loop_mod.profile_for(state).gait for state in states):
        raise SystemExit("video-set: --anchor motion-auto requires walk/run states")
    if anchor == "motion":
        raise SystemExit("video-set: motion anchor requires reviewed regions; use video-loop --cycle fixed")
    facings = facings_of(facing)
    validate_facing(facings[0], facing_fix)
    if facing_fix not in facing_mod.FIXES:
        raise SystemExit("video-set: --facing-fix must be mirror or none")
    _refuse_mirror_with_handed(facing_fix, handed)
    if fit == "tight" and shape is not None:
        raise SystemExit("video-set: --fit tight picks each canvas's shape itself; drop --shape")
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    views = resolve_bases(bases, facings)
    both = len(facings) > 1
    for direction, turned, base in views:
        if not base.is_file():
            raise SystemExit(f"video-set: base still for '{direction}' not found: {base}")
    # (item, direction, state, facing, base); a set filmed both ways names a lateral item's facing
    items = [(f"{d}-{f}-{s}" if both and f else f"{d}-{s}", d, s, f or facings[0], b) for d, f, b in views for s in states]
    results: list[dict[str, Any]] = []
    # States share a base. A run-local lock ensures exactly one vision call per
    # side still and facing, even when several state workers reach it together.
    prepared: dict[tuple[Path, str], tuple[Path, dict]] = {}
    prepare_lock = threading.Lock()

    def side_preparer(turned: str) -> Callable[[Path], tuple[Path, dict]]:
        def prepare_side(base: Path) -> tuple[Path, dict]:
            with prepare_lock:
                if (base, turned) not in prepared:
                    corrected = root / (f"side-{turned}.facing.png" if both else "side.facing.png")
                    report = facing_mod.prepare_still(base, corrected, facing=turned, fix=facing_fix)
                    prepared[(base, turned)] = (corrected, report)
                return prepared[(base, turned)]
        return prepare_side

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = {ex.submit(run_item, item=i, direction=d, state=s, base=b, root=root, character=character, duration=duration, resolution=resolution, key=key, force=force, gap=gap, video_runner=video_runner, shape=shape, anchor=anchor, spill=spill, facing=f, facing_fix=facing_fix, prepare_side=side_preparer(f), body_height=body_height, fit=fit, decontam=decontam, walk_start=walk_start, redraw_runner=redraw_runner, still_provider=still_provider, handed=handed): i for i, d, s, f, b in items}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            print(json.dumps({k: r[k] for k in ("item", "ok") if k in r} | ({"error": r["error"]} if not r.get("ok") else {"seam": r["loop"]["seam_ratio"]}), ensure_ascii=False), flush=True)
    results.sort(key=lambda r: [i for i, *_ in items].index(r["item"]))
    aligned = align_gaits(results, root, align_cycles, interpolate=interpolate)
    table = write_table(results, root / "table.md", both=both)
    failed = [r["item"] for r in results if not r.get("ok")] + [f"cycle-align:{st}" for st, a in aligned.items() if not a.get("ok")]
    # What the set left undone for want of RIFE — never a failure, never silent.
    warnings = ([f"{r['item']}: jump frame not repaired — {r['loop']['jump_repair']['why']}" for r in results if r.get("ok") and "jump_repair" in r["loop"]]
                + [f"cycle-align:{st}: not aligned — {a['why']}" for st, a in aligned.items() if a.get("applied") is False])
    if warnings:
        warnings.append(f"install RIFE with `{rife_mod.INSTALL_COMMAND}` (docs/loop-repair.md)")
    payload = {"kind": "sprite-gen-video-set-report", "root": str(root), "states": states, "facing": facing, "facing_fix": facing_fix, "directions": list(bases), **({"handed": [vars(h) for h in handed]} if handed else {}), "body_height": body_height, "ok": sum(1 for r in results if r.get("ok")), "failed": failed, "warnings": warnings, "items": results, "cycle_align": aligned}
    atomic_write_text(root / "set.report.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(table)
    return payload


def _parse_bases(values: list[str]) -> dict[str, Path]:
    bases: dict[str, Path] = {}
    for v in values:
        if "=" not in v:
            raise SystemExit(f"video-set: --base expects direction=path, got {v!r}")
        d, p = v.split("=", 1)
        d = d.strip()
        if d.partition("@")[0] not in VIEW_TEXT:
            raise SystemExit(f"video-set: --base direction must be one of {', '.join(sorted(VIEW_TEXT))}, got {d!r}")
        bases[d] = Path(p).expanduser().resolve()
    if not bases:
        raise SystemExit("video-set: at least one --base direction=path is required")
    return bases


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base", action="append", default=[], help="direction=still.png (repeatable: side=..., front=..., back=...); filmed facing right,left, a side or diagonal view takes one still per facing: side@right=..., side@left=...")
    parser.add_argument("--facing", default="right", help="which way the side and diagonal views face, for both prompt and canvas: right (default), left, or right,left to film each of them both ways from a still drawn each way (no mirroring); ignored for front/back")
    parser.add_argument("--facing-fix", choices=facing_mod.FIXES, default="none", help="side inputs: record only (none, default), or opt into mirror (refused with --handed)")
    parser.add_argument("--handed", action="append", default=[], metavar="ITEM=SIDE [PART]", help="an asymmetric item on one of the character's own sides, e.g. 'the black smartwatch=left wrist' (repeatable): every clip prompt says where it is in its view and that it stays there")
    parser.add_argument("--states", default="idle,walk,run,jump,attack", help="comma list of motion states")
    parser.add_argument("--out-dir", required=True, type=Path, help="batch root; one folder per direction-state")
    parser.add_argument("--character", help="short subject phrase used in the prompts (e.g. 'The armored knight')")
    parser.add_argument("--duration", type=int, default=None, help=f"seconds per clip for every state (default: {DEFAULT_DURATION_SECONDS}, attack {STATE_DURATION_SECONDS['attack']}); a repeating motion holds enough cycles at 3 s and a longer clip only costs more (2026-09-18)")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--key", choices=("auto", "green", "magenta", "white"), default="auto")
    parser.add_argument("--concurrency", type=int, default=3, help="parallel clip generations (starts are staggered regardless)")
    parser.add_argument("--start-gap", type=float, default=START_GAP_SECONDS, help="seconds between clip request starts")
    parser.add_argument("--shape", choices=canvas_mod.SHAPES, help="force one canvas shape for every state (e.g. wide for a costume or arms that leave a 1:1 frame)")
    parser.add_argument("--anchor", choices=tuple(a for a in loop_mod.ANCHOR_MODES if a != "motion"), default="none", help="motion-auto: automatic regions, local period and XY correction (walk/run only); feet: remove in-canvas drift")
    parser.add_argument("--spill", choices=frames_mod.SPILL_MODES, default="auto", help="auto: judge key reflections from each item's canvas still (default); small / full: force")
    parser.add_argument("--fit", choices=canvas_mod.FITS, default="state", help="state: each state's room for the motion (default); tight: no room, the subject fills the frame and a motion that leaves it is clipped (use with --body-height at a low --resolution)")
    parser.add_argument("--body-height", type=int, default=None, help="scale every state's loop so the standing height is this many px (video-loop --body-height): one value for the whole set keeps the character the same size across states")
    parser.add_argument("--decontam", choices=frames_mod.DECONTAM_MODES, default="off", help="passed to video-frames: palette re-explains key-tinted edges with the subject's own colours (default off)")
    parser.add_argument("--walk-start", choices=WALK_START_MODES, default="redraw", help="redraw (default): a front or back walk films from its base still redrawn mid-step (one image generation, `sprite-gen gen --ref base`, kept as walk-start.png) — from a standing still the clip model walks askew; as-given: film from the base still itself")
    parser.add_argument("--still-provider", help="image provider for the mid-step redraw (default: sprite-gen gen's own default)")
    parser.add_argument("--align-cycles", choices=ALIGN_MODES, default="auto", help="auto (default): after the loops are cut, every walk/run filmed in two or more directions is resampled to the set's median cycle length and turned to start on a foot strike (video-cycle-align; RIFE makes only the frames between source frames — where no RIFE is installed the alignment is skipped with a warning and recorded); off: each loop keeps its own length")
    parser.add_argument("--force", action="store_true", help="regenerate clips that already exist")


def run(**kwargs: object) -> int:
    payload = run_set(
        bases=_parse_bases(list(kwargs.get("base") or [])),  # type: ignore[arg-type]
        states=[s.strip() for s in str(kwargs.get("states") or "").split(",") if s.strip()],
        root=Path(str(kwargs["out_dir"])), character=kwargs.get("character"),  # type: ignore[arg-type]
        duration=(int(kwargs["duration"]) if kwargs.get("duration") else None), resolution=str(kwargs.get("resolution") or "720p"), key=str(kwargs.get("key") or "auto"),
        concurrency=int(kwargs.get("concurrency") or 3), force=bool(kwargs.get("force")), gap=float(kwargs.get("start_gap") or START_GAP_SECONDS),
        facing=str(kwargs.get("facing") or "right"), facing_fix=str(kwargs.get("facing_fix") or "none"),
        shape=(str(kwargs["shape"]) if kwargs.get("shape") else None), anchor=str(kwargs.get("anchor") or "none"), spill=str(kwargs.get("spill") or "auto"),
        body_height=(int(kwargs["body_height"]) if kwargs.get("body_height") else None), fit=str(kwargs.get("fit") or "state"),
        decontam=str(kwargs.get("decontam") or "off"), align_cycles=str(kwargs.get("align_cycles") or "auto"),
        walk_start=str(kwargs.get("walk_start") or "redraw"), still_provider=kwargs.get("still_provider"),  # type: ignore[arg-type]
        handed=handed_mod.parse_all(list(kwargs.get("handed") or [])) or None,  # type: ignore[arg-type]
    )
    return 0 if not payload["failed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-set", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
