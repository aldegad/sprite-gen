# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-canvas` — pad a base still into the canvas a motion state needs.

Grok Imagine keeps the input image's framing and ignores `aspect_ratio` on
image-to-video (2026-09-08 실측: a `3:4` request still returned 960x960). So the
canvas is decided HERE, on the still: a jump needs head-room above (tall), an
attack needs room above and in front, a projectile needs room in front (wide), everything else stays square.
A walk or run filmed from the side stays square too, the square widened by whatever room the still does
not leave its silhouette for the stride and what trails behind it (`gait_room`, `--view`).
The state -> canvas table below is the single owner of that rule; `--shape`
overrides it per call, with the state's own row for that shape where it has one (a walk or run
forced wide gets no head-room).

`--fit tight` is the other way to frame: no room is added for the motion. The
empty rows above and below the subject are dropped (a little headroom stays),
the still's width is kept, and the result is padded to the nearest framing the
video model returns (9:16, 1:1, 16:9). The subject then fills as much of the
clip's height as it can, which is what keeps a fixed body height from being
upscaled at a low clip resolution; a motion that leaves that frame is clipped.

The padding is filled with the chroma key so the clip stays keyable end to end.
A still whose corners disagree is refused — a non-flat background cannot be
extended without guessing.

Image models paint "#00FF00" a little differently every time ((8, 166, 25) on
2026-09-11), and the video model reproduces the input colour almost exactly. So
when the corners are a green/magenta key at *any* brightness or balance, the flat
background is repainted to the exact declared key here — the pixels the
`cutout` chroma matte erases become the pure key, the subject is untouched —
and the padding uses that same pure key. A white/ivory base is padded with its
own corner colour as before (no chroma key to normalize to).

`--prop-plate` paints a flat magenta plate on the green key in front of the subject, a low wall for it to clear
(`prop_plate`, sized by the subject's height), and writes its box into the report, where `video-frames --prop-plate`
reads it to erase the plate from the clip's keyed frames.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.frames.cutout import GREEN, KEY_AUTO, KEY_TARGETS, KEY_WHITE, MAGENTA, extract_route
from sprite_gen.frames.extract import is_border_key_candidate
from sprite_gen.gen.facing import FACINGS
from sprite_gen.gen.handedness import VIEW_SIDE, VIEWS
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.console import utf8_streams
from sprite_gen.video import effect
from sprite_gen.video import prop_plate as plate_mod

SHAPE_SQUARE = "square"
SHAPE_TALL = "tall"
SHAPE_WIDE = "wide"
SHAPES = (SHAPE_SQUARE, SHAPE_TALL, SHAPE_WIDE)


@dataclass(frozen=True)
class CanvasProfile:
    shape: str
    ratio: float  # width / height
    headroom: float  # fraction of the canvas height kept empty ABOVE the still (tall/wide)
    lead: float  # fraction of the canvas width kept empty IN FRONT of the subject (wide)
    trail: float  # fraction of the canvas width kept empty BEHIND the subject (wide)
    why: str
    # A square row that, filmed in a view the motion crosses (ROOM_VIEWS), reads the still's room around its
    # silhouette and widens the square by the room it lacks (`gait_room`).
    room: bool = False


# Filmed from the side, a walk's stride reaches in front of the standing silhouette, and behind it the stride
# and whatever trails behind the body — a big tail, a long coat — swing past it, in the picture's plane. The
# square a walk is filmed on keeps GAIT_ROOM of the silhouette's height clear between the silhouette and the
# frame's edge on each side, more behind than in front: where the still leaves less, the square is widened by what
# it lacks on that side, the extra height going above (`video-frames` refuses a clip whose subject meets a side of
# the frame). The frame stays square — the clip keeps the 1:1 a walk is filmed at, and the subject loses only the
# share of the clip the room takes. A diagonal walk strides half into the picture and a front or back walk wholly
# into it, so their room is not read (ROOM_VIEWS). A run is read at the walk's bounds. The rule:
# docs/video-pipeline.md section 1.
GAIT_ROOM = {"front": 0.15, "behind": 0.25}
ROOM_VIEWS = frozenset({VIEW_SIDE})
GAIT_ROOM_WHY = "the still leaves its silhouette too little room for a walk or run from the side: the square is widened by the room it lacks"
# The one table. Keys are state names as the sprite-request uses them; unknown
# states fall through to `default`.
STATE_CANVAS: dict[str, CanvasProfile] = {
    "jump": CanvasProfile(SHAPE_TALL, 3 / 4, 0.34, 0.0, 0.0, "airborne frames need head-room; hair clipped at 1:1"),
    # Head-room 0.20, not more: at least a quarter of the still's height goes above it,
    # which with the still's own margin over the crown holds a weapon raised about a
    # third of the body above the head. More room only shrinks the body in the clip —
    # the loop then scales it up to the delivery height instead of down. Lead and
    # trail stay: for a square or upright still at this head-room the 16:9 ratio, not
    # the lead, sets the width.
    "attack": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.20, 0.28, 0.2, "a weapon raised overhead needs room above, a swing extends in front, and a long weapon drawn back reaches behind"),
    "projectile": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.34, 0.0, "projectile travels away from the body"),
    # Raised-limb celebrations leave a square frame at the top corners; wide with a
    # symmetric margin keeps them inside (lead applies in front, the rest pads the back).
    "cheer": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.30, 0.0, "arms raised and spread leave a 1:1 frame"),
    "wave": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.30, 0.0, "a raised waving arm leaves a 1:1 frame"),
    "celebrate": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.30, 0.0, "same envelope as cheer"),
    "death": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.1, 0.32, 0.32, "room for the death terminal pose"),
    "fall": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.1, 0.32, 0.32, "room for the fall terminal pose"),
    "land": CanvasProfile(SHAPE_TALL, 3 / 4, 0.34, 0.0, 0.0, "room for the land terminal pose"),
    "cast": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.25, 0.25, 0.25, "room for the cast terminal pose"),
    "hurt_hold": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.1, 0.32, 0.32, "room for the hurt_hold terminal pose"),
    "transform": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.25, 0.25, 0.25, "room for the transform terminal pose"),
    # In place on the still's own frame, as any state, while the still leaves the stride its room (GAIT_ROOM).
    "walk": CanvasProfile(SHAPE_SQUARE, 1.0, 0.0, 0.0, 0.0, "in-place motion fits the still's own frame", room=True),
    "run": CanvasProfile(SHAPE_SQUARE, 1.0, 0.0, 0.0, 0.0, "in-place motion fits the still's own frame", room=True),
    "default": CanvasProfile(SHAPE_SQUARE, 1.0, 0.0, 0.0, 0.0, "in-place motion fits the still's own frame"),
}
# `--shape wide` is not the attack row. It is forced on a state whose own row is
# another shape — `video-set --shape wide` on every state of a batch, or a retry after
# a side-edge contact — and that state may be a jump. 0.35 on 16:9 keeps about the
# room a jump's tall row leaves above a square or upright still (0.35/0.65 ≈
# 0.34/0.66 of its height). A still wider than it is tall keeps less than the tall
# row would give it (a 3:2 still about three fifths), because the wide canvas's
# height follows the still's width there.
WIDE_OVERRIDE = CanvasProfile(SHAPE_WIDE, 16 / 9, 0.35, 0.28, 0.2, "forced wide: room in front and behind, and a jump's head-room above, whatever the state")
# A walk or run forced wide keeps the room in front and behind and gets none above: its feet do
# not leave the ground. Above a square still the jump's head-room set the canvas's width too —
# 1575 rows on 16:9 are 2800 columns, 1216 of them in front of the still — and a subject that
# small in its frame is one a video model may reframe in its first frames (`video-loop`'s
# lead-in). The same 16:9 with no head-room gives a square still 1969 x 1108.
GAIT_WIDE = CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.28, 0.2, "forced wide for a walk or run: room in front and behind, none above — its feet stay on the ground")
# A state's own row for a forced shape, read before the shape's default (`profile_for`).
STATE_SHAPE_CANVAS: dict[tuple[str, str], CanvasProfile] = {
    ("walk", SHAPE_WIDE): GAIT_WIDE,
    ("run", SHAPE_WIDE): GAIT_WIDE,
}
SHAPE_DEFAULTS: dict[str, CanvasProfile] = {
    SHAPE_SQUARE: STATE_CANVAS["default"],
    SHAPE_TALL: STATE_CANVAS["jump"],
    SHAPE_WIDE: WIDE_OVERRIDE,
}
CORNER_TOLERANCE = 24  # max per-channel spread across the four corners for a "flat" background
FITS = ("state", "tight")  # state: the table's room for the motion; tight: none
# The framings Grok returns for an input image, width / height. A tight canvas snaps to
# the nearest one so the clip keeps the proportions the subject was cut to.
TIGHT_RATIOS = {SHAPE_TALL: 9 / 16, SHAPE_SQUARE: 1.0, SHAPE_WIDE: 16 / 9}
TIGHT_HEADROOM = 0.04  # of the subject's own height, kept above it so the head is not flush with the edge
KEYS = (KEY_AUTO, GREEN, MAGENTA, KEY_WHITE)  # auto: the corners decide; white: no chroma key, pad with the corner colour


def profile_for(state: str | None, shape: str | None = None) -> CanvasProfile:
    """Resolve the canvas profile: an explicit `shape` wins — the state's own row for that shape
    (`STATE_SHAPE_CANVAS`), else the shape's default — else the state's row, else default."""
    key = (state or "").strip().lower()
    if shape is not None:
        if shape not in SHAPES:
            raise SystemExit(f"video-canvas: unknown --shape {shape!r}; expected one of {', '.join(SHAPES)}")
        return STATE_SHAPE_CANVAS.get((key, shape), SHAPE_DEFAULTS[shape])
    return STATE_CANVAS.get(key, STATE_CANVAS["default"])


def corner_key(image: Image.Image) -> tuple[int, int, int]:
    """The still's flat background colour, read from its four corners; refuses a non-flat one."""
    rgb = image.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    for channel in range(3):
        values = [c[channel] for c in corners]
        if max(values) - min(values) > CORNER_TOLERANCE:
            raise SystemExit(
                f"video-canvas: the still's corners are not one flat colour ({corners}); "
                "generate the base on a flat chroma key before padding it"
            )
    return tuple(round(sum(c[i] for c in corners) / 4) for i in range(3))  # type: ignore[return-value]


def resolve_key(corner: tuple[int, int, int], key: str) -> str | None:
    """Which chroma key the still is on: `green` | `magenta`, or None for a non-key (white/ivory) base.

    `auto` classifies the flat corner colour with the engine's own border rule
    (`is_border_key_candidate` — the key's hue signature at any brightness or
    balance; a flat corner is background evidence). An explicit green/magenta
    that the corners are not is refused rather than repainted blindly.
    """
    if key not in KEYS:
        raise SystemExit(f"video-canvas: unknown --key {key!r}; expected one of {', '.join(KEYS)}")
    if key == KEY_WHITE:
        return None
    if key == KEY_AUTO:
        for kind, target in KEY_TARGETS.items():
            if is_border_key_candidate(corner, target):
                return kind
        return None
    if not is_border_key_candidate(corner, KEY_TARGETS[key]):
        raise SystemExit(
            f"video-canvas: --key {key} but the still's corners are {corner}, not a {key} key family colour; "
            "pass --key auto to let the corners decide or --key white for a non-chroma base"
        )
    return key


def normalize_key(image: Image.Image, kind: str) -> tuple[Image.Image, dict[str, Any]]:
    """Repaint the still's flat background to the exact declared key; the subject stays byte-identical.

    The mask is the `cutout` chroma matte's own alpha-0 set (keyed from the
    painted background colour, see `extract.detect_background_key_rgb`), so the
    pixels this repaints are exactly the pixels `video-frames` will erase. Fails
    loud when a corner survives the matte — then the still is not on a key the
    engine can cut and padding it would only hide that.
    """
    target = KEY_TARGETS[kind]
    rgb = image.convert("RGB")
    keyed, stats = extract_route(rgb.convert("RGBA"), kind)
    erased = np.array(keyed, dtype=np.uint8)[..., 3] == 0
    data = np.array(rgb, dtype=np.uint8)
    h, w = erased.shape
    corners = ((0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1))
    if not all(erased[y, x] for y, x in corners):
        raise SystemExit(
            f"video-canvas: the still's corners survived the {kind} key matte "
            f"(painted {tuple(stats['chroma_key_painted'])}); the background is not a {kind} key the engine can cut"
        )
    data[erased] = target
    report = {
        "key": kind,
        "key_painted": stats["chroma_key_painted"],
        "normalized_px": int(erased.sum()),
        "normalized_pct": round(100 * float(erased.mean()), 2),
    }
    return Image.fromarray(data, "RGB"), report


def subject_box(image: Image.Image, fill: tuple[int, int, int], tolerance: int) -> tuple[int, int, int, int] | None:
    """The box (left, top, right, bottom; right and bottom one past) of the pixels more than `tolerance` off the
    background `fill` on some channel, or None when there are none."""
    data = np.asarray(image.convert("RGB"), dtype=np.int16)
    subject = np.abs(data - np.array(fill, dtype=np.int16)).max(axis=2) > tolerance
    rows = np.flatnonzero(subject.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(subject.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def gait_room(image: Image.Image, fill: tuple[int, int, int], tolerance: int, *, facing: str, view: str) -> dict[str, Any]:
    """The empty margin between the still's silhouette (`subject_box`) and the left and right edges of the square frame
    a square row makes of it (`pad_canvas`: max(w, h) wide, the still centred, so a still taller than it is wide gains
    room on either side), read as the room in front of the subject (the side it faces) and behind it, in px and of the
    silhouette's height. `short` names the sides under their GAIT_ROOM of that height, front first; `added_px` is what
    each side lacks, in whole px, the columns the square gains there, and `added_rows` the rows it gains above."""
    box = subject_box(image, fill, tolerance)
    if box is None:
        raise SystemExit("video-canvas: found no subject on the still's background to read a walk's room around")
    left, top, right, bottom = box
    height = bottom - top
    w, h = image.size
    x = (max(w, h) - w) // 2
    margin = {"left": x + left, "right": max(w, h) - x - right}
    ahead, behind = ("right", "left") if facing == "right" else ("left", "right")
    room = {"front": margin[ahead], "behind": margin[behind]}
    need = {side: GAIT_ROOM[side] * height for side in ("front", "behind")}
    added = {side: max(0, math.ceil(need[side] - room[side])) for side in ("front", "behind")}
    return {
        "view": view,
        "silhouette": [left, top, right, bottom],
        "front_px": room["front"],
        "behind_px": room["behind"],
        "front": round(room["front"] / height, 4),
        "behind": round(room["behind"] / height, 4),
        "need": dict(GAIT_ROOM),
        "need_px": {side: round(need[side], 1) for side in ("front", "behind")},
        "short": [side for side in ("front", "behind") if room[side] < need[side]],
        "added_px": added,
        "added_rows": added["front"] + added["behind"],
    }


def tight_canvas(src: Image.Image, fill: tuple[int, int, int], *, tolerance: int) -> tuple[Image.Image, dict[str, Any]]:
    """Drop the empty rows above and below the subject, keep the width, pad to the nearest framing.

    Never scales the subject and never cuts a column of it. Extra height goes above
    the subject (it stands on the bottom edge, as on every canvas); extra width is
    split evenly, because a tight frame adds no room in front on purpose.
    """
    w, h = src.size
    box = subject_box(src, fill, tolerance)
    if box is None:
        raise SystemExit("video-canvas: --fit tight found no subject on the still's background")
    _, top, _, bottom = box
    head = round((bottom - top) * TIGHT_HEADROOM)
    kept_top = max(0, top - head)
    band = src.crop((0, kept_top, w, bottom))
    bw, bh = band.size
    shape = min(TIGHT_RATIOS, key=lambda name: abs(math.log((bw / bh) / TIGHT_RATIOS[name])))
    ratio = TIGHT_RATIOS[shape]
    if bw / bh >= ratio:
        canvas_w, canvas_h = bw, max(bh, round(bw / ratio))
    else:
        canvas_w, canvas_h = max(bw, round(bh * ratio)), bh
    x, y = (canvas_w - bw) // 2, canvas_h - bh
    canvas = Image.new("RGB", (canvas_w, canvas_h), fill)
    canvas.paste(band, (x, y))
    report = {
        "shape": shape,
        "ratio": round(canvas_w / canvas_h, 4),
        "canvas": [canvas_w, canvas_h],
        "still": [w, h],
        "offset": [x, y],
        "tight": {"subject_rows": [top, bottom], "kept_rows": [kept_top, bottom], "headroom_px": top - kept_top},
    }
    return canvas, report


def pad_canvas(
    image: Image.Image,
    profile: CanvasProfile,
    *,
    facing: str = "right",
    headroom: float | None = None,
    lead: float | None = None,
    trail: float | None = None,
    key: str = KEY_AUTO,
    fit: str = "state",
    view: str | None = None,
) -> tuple[Image.Image, dict[str, Any]]:
    """Return (padded RGB image, placement report). Never downsizes the still.

    On a green/magenta base the background is normalized to the exact key and
    the padding is that key; otherwise the padding is the corner colour.
    A `room` row filmed in one of ROOM_VIEWS (`view`) has the still's room read
    (`gait_room`, the report's `room`); a side that is short gains the columns it
    lacks and the square the rows to match, above the still. With no `view` the
    room is not read.
    """
    corner = corner_key(image)
    kind = resolve_key(corner, key)
    if kind is None:
        src, fill, key_report = image.convert("RGB"), corner, {"key": None, "key_painted": list(corner), "normalized_px": 0, "normalized_pct": 0.0}
    else:
        src, key_report = normalize_key(image, kind)
        fill = KEY_TARGETS[kind]
    if fit not in FITS:
        raise SystemExit(f"video-canvas: unknown --fit {fit!r}; expected one of {', '.join(FITS)}")
    if fit == "tight":
        # A normalized key background is the exact key, so any other value is the subject;
        # a white/ivory base only has its corner colour to go by.
        canvas, placed = tight_canvas(src, tuple(fill), tolerance=0 if kind is not None else CORNER_TOLERANCE)  # type: ignore[arg-type]
        report = {
            "fit": "tight",
            **placed,
            "headroom": 0.0,
            "lead": 0.0,
            "trail": 0.0,
            "facing": facing,
            "key_rgb": list(fill),
            "corner_rgb": list(corner),
            **key_report,
            "why": "tight: no room for the motion; the subject fills the frame and a motion that leaves it is clipped",
        }
        return canvas, report
    room = None
    if profile.room and view in ROOM_VIEWS:
        room = gait_room(src, tuple(fill), 0 if kind is not None else CORNER_TOLERANCE, facing=facing, view=view)  # type: ignore[arg-type]
    w, h = src.size
    head = profile.headroom if headroom is None else headroom
    front = profile.lead if lead is None else lead
    back = profile.trail if trail is None else trail
    if not 0 <= head < 0.9 or not 0 <= front < 0.9 or not 0 <= back < 0.9 or not front + back < 0.9:
        raise SystemExit("video-canvas: --headroom/--lead/--trail must be in [0, 0.9) and lead + trail below 0.9")
    if profile.shape == SHAPE_SQUARE:
        side = max(w, h)
        x = (side - w) // 2
        if room is not None and room["short"]:
            # the columns each side lacks; the square's rows grow to match, above the still
            added = {"left": room["added_px"]["behind" if facing == "right" else "front"],
                     "right": room["added_px"]["front" if facing == "right" else "behind"]}
            side += added["left"] + added["right"]
            x += added["left"]
        canvas_w, canvas_h = side, side
        y = side - h
    elif profile.shape == SHAPE_TALL:
        # the still becomes the bottom (1 - headroom) of a canvas at least as tall as the
        # profile ratio demands for the still's width — never narrower than the still
        canvas_h = max(h, round(h / (1 - head)), round(w / profile.ratio))
        canvas_w = max(w, round(canvas_h * profile.ratio))
        x, y = (canvas_w - w) // 2, canvas_h - h
    else:  # wide: `trail` of the width stays empty behind the subject, the rest of the extra width goes in front; at least the profile ratio
        required_h = max(h, round(h / (1 - head)))
        canvas_w = max(w, round(w / (1 - front - back)), round(required_h * profile.ratio))
        canvas_h = max(h, round(canvas_w / profile.ratio))
        y = canvas_h - h
        behind = min(round(canvas_w * back), canvas_w - w)
        x = behind if facing == "right" else canvas_w - w - behind
    canvas = Image.new("RGB", (canvas_w, canvas_h), fill)
    canvas.paste(src, (x, y))
    report = {
        "fit": "state",
        "shape": profile.shape,
        "ratio": round(canvas_w / canvas_h, 4),
        "canvas": [canvas_w, canvas_h],
        "still": [w, h],
        "offset": [x, y],
        "headroom": head,
        "lead": front,
        "trail": back,
        "facing": facing,
        "key_rgb": list(fill),
        "corner_rgb": list(corner),
        **key_report,
        "why": GAIT_ROOM_WHY if room is not None and room["short"] else profile.why,
        **({"room": room} if room is not None else {}),
    }
    return canvas, report


def run_canvas(
    still: Path,
    out: Path,
    *,
    state: str | None,
    shape: str | None,
    facing: str,
    headroom: float | None,
    lead: float | None,
    report_path: Path | None,
    key: str = KEY_AUTO,
    trail: float | None = None,
    fit: str = "state",
    view: str | None = None,
    prop_plate: bool = False,
) -> dict[str, Any]:
    still = still.expanduser().resolve()
    if not still.is_file():
        raise SystemExit(f"video-canvas: still not found: {still}")
    if fit == "tight" and (shape is not None or headroom is not None or lead is not None or trail is not None):
        raise SystemExit("video-canvas: --fit tight picks its own shape and adds no room; drop --shape/--headroom/--lead/--trail")
    if prop_plate and report_path is None:
        raise SystemExit("video-canvas: --prop-plate needs --report: the plate's box is written there for video-frames --prop-plate")
    if state == effect.STATE:
        if prop_plate:
            raise SystemExit("video-canvas: an effect keeps its source canvas; drop --prop-plate")
        if fit != "state" or any(v is not None for v in (shape, headroom, lead, trail)):
            raise SystemExit("video-canvas: an effect keeps its source canvas and center; drop framing overrides")
        with Image.open(still) as image:
            canvas, report = effect.canvas(image)
    else:
        profile = profile_for(state, shape)
        canvas, report = pad_canvas(Image.open(still), profile, facing=facing, headroom=headroom, lead=lead, trail=trail, key=key, fit=fit, view=view)
        if prop_plate:
            if report["key"] != GREEN:
                raise SystemExit(f"video-canvas: --prop-plate paints a magenta plate on a green key; the still's key is {report['key'] or 'not a chroma key'}")
            report["prop_plate"] = plate_mod.paint(canvas, subject_box(canvas, tuple(report["key_rgb"]), 0), facing=facing)  # type: ignore[arg-type]
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    canvas.save(tmp, format="PNG")
    tmp.replace(out)
    if state == effect.STATE:
        report["end_frame"] = str(effect.empty_end(out, out.with_name(out.stem + ".end.png")))
    payload = {"kind": plate_mod.CANVAS_REPORT, "still": str(still), "out": str(out), "state": state, **report}
    if report_path is not None:
        atomic_write_text(report_path.expanduser().resolve(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--still", required=True, type=Path, help="base still on a flat chroma background")
    parser.add_argument("--out", required=True, type=Path, help="padded PNG to feed `sprite-gen video`")
    parser.add_argument("--state", help="motion state name (jump/attack/projectile/... — selects the canvas row)")
    parser.add_argument("--shape", choices=SHAPES, help="override the state's canvas shape")
    parser.add_argument("--facing", choices=FACINGS, default="right", help="which way the subject faces (wide canvases add room in front)")
    parser.add_argument("--headroom", type=float, help="tall/wide: empty fraction of canvas height above the still (default from the profile)")
    parser.add_argument("--lead", type=float, help="wide: empty fraction in front of the subject (default from the profile)")
    parser.add_argument("--trail", type=float, help="wide: empty fraction behind the subject, for a weapon drawn back (default from the profile)")
    parser.add_argument("--key", choices=KEYS, default=KEY_AUTO, help="chroma key of the still (auto reads the corners; green/magenta are normalized to the exact key; white pads with the corner colour)")
    parser.add_argument("--fit", choices=FITS, default="state", help="state: the state's room for the motion (default); tight: drop the empty rows around the subject and add no room, so it fills the frame and a motion that leaves it is clipped")
    parser.add_argument("--view", choices=VIEWS, help="the still's view; a walk or run from the side has the still's room around its silhouette read, and its square widened by the room it lacks (`room` in the report). Left out, the room is not read")
    parser.add_argument("--prop-plate", action="store_true", help="paint a flat magenta plate, a low wall for the subject to clear, on the ground in front of it (the side it faces), sized by its height; green key only, needs --report, where its box is written for `video-frames --prop-plate` to erase")
    parser.add_argument("--report", type=Path, help="write the canvas report JSON here")


def run(**kwargs: object) -> int:
    payload = run_canvas(
        Path(str(kwargs["still"])), Path(str(kwargs["out"])),
        state=kwargs.get("state"), shape=kwargs.get("shape"), facing=str(kwargs.get("facing") or "right"),  # type: ignore[arg-type]
        headroom=kwargs.get("headroom"), lead=kwargs.get("lead"), trail=kwargs.get("trail"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
        key=str(kwargs.get("key") or KEY_AUTO), fit=str(kwargs.get("fit") or "state"), view=kwargs.get("view"),  # type: ignore[arg-type]
        prop_plate=bool(kwargs.get("prop_plate")),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog="sprite-gen video-canvas", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
