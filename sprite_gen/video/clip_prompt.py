# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-prompt` — the clip prompt for one (direction, state), for a clip made elsewhere.

`video-set` builds this prompt (`batch.build_prompt`) and sends it to `sprite-gen video` itself.
When the clip is made by another tool instead — a video MCP connected to the user's own agent,
such as ZCRE — the agent asks for the prompt here, sends it with the `video-canvas` still through
that tool, and hands the mp4 back to `video-frames` and `video-loop`. The engine never calls the
other tool: this verb prints text and reads nothing but its arguments.

A clip source that cannot pin an end frame (`--no-last-frame`; ZCRE's `grok-imagine-video-1.5` has
image-to-video only) cannot film what `video-set` films pinned (`pins_last_frame`: idle, attack, a
walk or run in a diagonal). Those are refused, or with `--unpinned` asked for as an unpinned clip,
with a warning: the prompt then asks for an evenly paced repeat (an attack still asks for one
strike), the loop is searched instead of cut whole, and it may not close.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sprite_gen.gen.facing import FACINGS
from sprite_gen.gen import handedness as handed_mod
from sprite_gen.gen.handedness import Handed
from sprite_gen.video import batch as batch_mod
from sprite_gen.video import body_plan as body_mod
from sprite_gen.video.body_plan import Body

DEFAULT_MODEL = "grok-imagine-video-1.5"
START_STILL_KEYS = ("green", "magenta")


def plan_prompt(*, direction: str, state: str, facing: str = "right", character: str | None = None,
                motion: str | None = None, model: str = DEFAULT_MODEL, last_frame: bool = True,
                unpinned: bool = False, duration: int | None = None, key: str = "green",
                handed: list[Handed] | None = None, body_plan: list[Body] | None = None,
                camera_elevation: int | None = None) -> dict[str, Any]:
    """The prompt `video-set` would send for (direction, state), and how its clip is to be cut. `handed`
    (`handedness.parse`) adds where each asymmetric item is in this view, as `video-set --handed` does;
    `body_plan` (`body_plan.parse_all`) what the subject stands on, as `video-set --body-plan` does;
    `camera_elevation` that the still was drawn looking down and the angle stays, as `video-set --camera-elevation`
    does (the mid-step redraw keeps it too)."""
    if direction not in batch_mod.VIEW_TEXT:
        raise SystemExit(f"video-prompt: --direction must be one of {', '.join(sorted(batch_mod.VIEW_TEXT))}, got {direction!r}")
    batch_mod.check_camera_elevation(camera_elevation, "video-prompt")
    if key not in START_STILL_KEYS:
        raise SystemExit(f"video-prompt: --key must be one of {', '.join(START_STILL_KEYS)}, got {key!r}")
    if unpinned and last_frame:
        raise SystemExit("video-prompt: --unpinned goes with --no-last-frame (a source that can pin the end frame films it pinned)")
    wants_pin = batch_mod.pins_last_frame(state, direction)
    warnings: list[str] = []
    pinned: bool | None = None
    if wants_pin and not last_frame:
        if not unpinned:
            raise SystemExit(
                f"video-prompt: a {direction} {state} is filmed ending on its first frame (the canvas pinned as the end "
                "frame), and this clip source has no end frame (--no-last-frame). Leave the state out, or pass --unpinned "
                "to film it without the pin: the prompt then asks for an evenly paced repeat (an attack for one strike), "
                "the loop is searched instead of cut whole, and it may not close.")
        pinned = False
        warnings.append(f"{direction} {state} filmed without its end-frame pin: the loop is searched, not cut whole, and may not close")
    pins = wants_pin and last_frame
    parts = batch_mod.clip_prompt_parts(direction, state, character, facing=facing, motion=motion, pinned=pinned,
                                        model=model, handed=handed, body_plan=body_plan, camera_elevation=camera_elevation)
    prompt = parts.text
    # the caller's own words (--character, --motion) against the engine's: a conflict is a warning
    warnings += [note["text"] for note in parts.notes if note["kind"] == "conflict"]
    cycle = "pinned" if pins and state in batch_mod.PINNED_LOOP_STATES else "auto"
    # as video-set places and cuts: a side or diagonal view takes the facing, front and back face right
    canvas_facing = loop_facing = facing if direction in handed_mod.LATERAL_VIEWS else "right"
    mid_step = batch_mod.starts_mid_step(state, direction, body_plan)
    still = "<dir>/walk-start.png" if mid_step else "<still.png>"
    duration = batch_mod.duration_for(state, duration)
    end = ", end frame <dir>/canvas.png" if pins else ""
    record: dict[str, Any] = {
        "kind": "sprite-gen-video-prompt",
        "direction": direction,
        "state": state,
        "facing": facing,
        **({"handed": [vars(h) for h in handed]} if handed else {}),
        **({"body_plan": [vars(b) for b in body_plan]} if body_plan else {}),
        **({"camera_elevation": camera_elevation} if camera_elevation is not None else {}),
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "last_frame": "canvas" if pins else None,
        "cycle": cycle,
        "commands": {
            "canvas": f"sprite-gen video-canvas --still {still} --state {state} --facing {canvas_facing} --out <dir>/canvas.png --report <dir>/canvas.report.json",
            "clip": f"your video tool, not sprite-gen: image-to-video from <dir>/canvas.png with this prompt, {duration} s{end}, saved as <dir>/clip.mp4",
            "frames": "sprite-gen video-frames --clip <dir>/clip.mp4 --out-dir <dir>/frames --spill auto --reference <dir>/canvas.png",
            "loop": f"sprite-gen video-loop --frames-dir <dir>/frames/keyed --out-dir <dir>/loop --fps <fps in frames.report.json> --state {state} --cycle {cycle} --facing {loop_facing} --name {direction}-{state}",
        },
        "warnings": warnings,
        **({"notes": parts.notes} if parts.notes else {}),
    }
    needs = handed_mod.first_frame_needs(handed or [], direction, canvas_facing if direction in handed_mod.LATERAL_VIEWS else None,
                                         gait=state in batch_mod.GAIT_STATES)
    if needs:
        record["still_needs"] = needs
    if mid_step:
        record["start_still"] = {
            "why": "a front or back walk films from its still redrawn mid-step; from a standing still the clip model walks askew",
            "prompt": batch_mod.walk_start_prompt(direction, key, handed, body_plan, camera_elevation),
            "command": "sprite-gen gen --ref <still.png> --prompt-file <start-prompt.txt> --out <dir>/walk-start.png --report <dir>/walk-start.report.json",
        }
    return record


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--direction", required=True, choices=sorted(batch_mod.VIEW_TEXT), help="view of the still")
    parser.add_argument("--state", required=True, help="motion state (idle/walk/run/jump/attack/...)")
    parser.add_argument("--facing", choices=FACINGS, default="right", help="side or diagonal view: which way it faces (default right)")
    parser.add_argument("--handed", action="append", default=[], metavar="ITEM=SIDE [PART]", help="an asymmetric item on one of the character's own sides, e.g. 'the black smartwatch=left wrist' (repeatable): the prompt says where it is in this view")
    parser.add_argument("--body-plan", action="append", default=[], metavar="PLAN | FIGURE=PLAN", help="what the character stands on: biped (default), quadruped or legless; a scene of several figures names each, e.g. 'the man=biped' 'the horse=quadruped' (repeatable), as video-set --body-plan")
    parser.add_argument("--camera-elevation", type=int, default=None, metavar="DEG", help=f"the still was drawn by a top-down orthographic game camera looking down DEG degrees ({batch_mod.CAMERA_ELEVATION_RANGE[0]}..{batch_mod.CAMERA_ELEVATION_RANGE[1]}; 35 for an RPG village): the prompt says the angle stays, and a front or back walk's start_still keeps it, as video-set --camera-elevation")
    parser.add_argument("--character", help="short subject phrase used in the prompt (e.g. 'The armored knight')")
    parser.add_argument("--motion", help="the caller's own motion paragraph in place of the state's sentence (build_prompt(motion=...))")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"clip model the prompt is for (default {DEFAULT_MODEL}); a Lite model calms a walk")
    parser.add_argument("--duration", type=int, help="seconds to ask for (default: the state's own, as video-set)")
    parser.add_argument("--no-last-frame", action="store_true", help="the clip source cannot pin an end frame (ZCRE's grok-imagine-video-1.5): states filmed pinned are refused")
    parser.add_argument("--unpinned", action="store_true", help="with --no-last-frame: film a pinned state without the pin, with a warning, instead of refusing it")
    parser.add_argument("--key", choices=START_STILL_KEYS, default="green", help="the still's chroma key, for a front or back walk's mid-step redraw sentence (default green)")
    parser.add_argument("--json", action="store_true", help="print the whole record (prompt, duration, end-frame pin, loop cut, commands) instead of the prompt alone")


def run(**kwargs: object) -> int:
    record = plan_prompt(
        direction=str(kwargs["direction"]), state=str(kwargs["state"]), facing=str(kwargs.get("facing") or "right"),
        character=kwargs.get("character"), motion=kwargs.get("motion"),  # type: ignore[arg-type]
        model=str(kwargs.get("model") or DEFAULT_MODEL), last_frame=not kwargs.get("no_last_frame"),
        unpinned=bool(kwargs.get("unpinned")), duration=(int(kwargs["duration"]) if kwargs.get("duration") else None),  # type: ignore[arg-type]
        key=str(kwargs.get("key") or "green"),
        handed=handed_mod.parse_all(list(kwargs.get("handed") or [])) or None,  # type: ignore[arg-type]
        body_plan=body_mod.parse_all(list(kwargs.get("body_plan") or [])) or None,  # type: ignore[arg-type]
        camera_elevation=kwargs.get("camera_elevation"),  # type: ignore[arg-type]
    )
    for line in record["warnings"]:
        print(f"video-prompt: warning: {line}", file=sys.stderr)
    for line in record.get("still_needs", []):
        print(f"video-prompt: the still: {line}", file=sys.stderr)
    print(json.dumps(record, ensure_ascii=False, indent=2) if kwargs.get("json") else record["prompt"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-prompt", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
