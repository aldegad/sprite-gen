# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-canvas` — pad a base still into the canvas a motion state needs.

Grok Imagine keeps the input image's framing and ignores `aspect_ratio` on
image-to-video (2026-09-08 실측: a `3:4` request still returned 960x960). So the
canvas is decided HERE, on the still: a jump needs head-room above (tall), an
attack needs room above and in front, a projectile needs room in front (wide), everything else stays square.
The state -> canvas table below is the single owner of that rule; `--shape`
overrides it per call.

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
from sprite_gen.frames.cutout import KEY_TARGETS, extract_route
from sprite_gen.frames.extract import is_border_key_candidate
from sprite_gen.spec.runio import atomic_write_text

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


# The one table. Keys are state names as the sprite-request uses them; unknown
# states fall through to `default`.
STATE_CANVAS: dict[str, CanvasProfile] = {
    "jump": CanvasProfile(SHAPE_TALL, 3 / 4, 0.34, 0.0, 0.0, "airborne frames need head-room; hair clipped at 1:1"),
    "attack": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.35, 0.28, 0.2, "weapon swings rise overhead and extend in front; a long weapon drawn back reaches behind"),
    "projectile": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.34, 0.0, "projectile travels away from the body"),
    # Raised-limb celebrations leave a square frame at the top corners; wide with a
    # symmetric margin keeps them inside (lead applies in front, the rest pads the back).
    "cheer": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.30, 0.0, "arms raised and spread leave a 1:1 frame"),
    "wave": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.30, 0.0, "a raised waving arm leaves a 1:1 frame"),
    "celebrate": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.30, 0.0, "same envelope as cheer"),
    "default": CanvasProfile(SHAPE_SQUARE, 1.0, 0.0, 0.0, 0.0, "in-place motion fits the still's own frame"),
}
SHAPE_DEFAULTS: dict[str, CanvasProfile] = {
    SHAPE_SQUARE: STATE_CANVAS["default"],
    SHAPE_TALL: STATE_CANVAS["jump"],
    SHAPE_WIDE: STATE_CANVAS["attack"],
}
CORNER_TOLERANCE = 24  # max per-channel spread across the four corners for a "flat" background
FITS = ("state", "tight")  # state: the table's room for the motion; tight: none
# The framings Grok returns for an input image, width / height. A tight canvas snaps to
# the nearest one so the clip keeps the proportions the subject was cut to.
TIGHT_RATIOS = {SHAPE_TALL: 9 / 16, SHAPE_SQUARE: 1.0, SHAPE_WIDE: 16 / 9}
TIGHT_HEADROOM = 0.04  # of the subject's own height, kept above it so the head is not flush with the edge
KEYS = ("auto", "green", "magenta", "white")  # auto: the corners decide; white: no chroma key, pad with the corner colour


def profile_for(state: str | None, shape: str | None = None) -> CanvasProfile:
    """Resolve the canvas profile: an explicit `shape` wins, else the state's row, else default."""
    if shape is not None:
        if shape not in SHAPES:
            raise SystemExit(f"video-canvas: unknown --shape {shape!r}; expected one of {', '.join(SHAPES)}")
        return SHAPE_DEFAULTS[shape]
    key = (state or "").strip().lower()
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
    if key == "white":
        return None
    if key == "auto":
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


def subject_rows(image: Image.Image, fill: tuple[int, int, int], tolerance: int) -> tuple[int, int]:
    """First and one-past-last row holding a pixel that is not the background `fill`."""
    data = np.asarray(image.convert("RGB"), dtype=np.int16)
    diff = np.abs(data - np.array(fill, dtype=np.int16)).max(axis=2)
    rows = np.flatnonzero((diff > tolerance).any(axis=1))
    if rows.size == 0:
        raise SystemExit("video-canvas: --fit tight found no subject on the still's background")
    return int(rows[0]), int(rows[-1]) + 1


def tight_canvas(src: Image.Image, fill: tuple[int, int, int], *, tolerance: int) -> tuple[Image.Image, dict[str, Any]]:
    """Drop the empty rows above and below the subject, keep the width, pad to the nearest framing.

    Never scales the subject and never cuts a column of it. Extra height goes above
    the subject (it stands on the bottom edge, as on every canvas); extra width is
    split evenly, because a tight frame adds no room in front on purpose.
    """
    w, h = src.size
    top, bottom = subject_rows(src, fill, tolerance)
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
    key: str = "auto",
    fit: str = "state",
) -> tuple[Image.Image, dict[str, Any]]:
    """Return (padded RGB image, placement report). Never downsizes the still.

    On a green/magenta base the background is normalized to the exact key and
    the padding is that key; otherwise the padding is the corner colour.
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
    w, h = src.size
    head = profile.headroom if headroom is None else headroom
    front = profile.lead if lead is None else lead
    back = profile.trail if trail is None else trail
    if not 0 <= head < 0.9 or not 0 <= front < 0.9 or not 0 <= back < 0.9 or not front + back < 0.9:
        raise SystemExit("video-canvas: --headroom/--lead/--trail must be in [0, 0.9) and lead + trail below 0.9")
    if profile.shape == SHAPE_SQUARE:
        side = max(w, h)
        canvas_w, canvas_h = side, side
        x, y = (side - w) // 2, side - h
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
        "why": profile.why,
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
    key: str = "auto",
    trail: float | None = None,
    fit: str = "state",
) -> dict[str, Any]:
    still = still.expanduser().resolve()
    if not still.is_file():
        raise SystemExit(f"video-canvas: still not found: {still}")
    if fit == "tight" and (shape is not None or headroom is not None or lead is not None or trail is not None):
        raise SystemExit("video-canvas: --fit tight picks its own shape and adds no room; drop --shape/--headroom/--lead/--trail")
    profile = profile_for(state, shape)
    canvas, report = pad_canvas(Image.open(still), profile, facing=facing, headroom=headroom, lead=lead, trail=trail, key=key, fit=fit)
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    canvas.save(tmp, format="PNG")
    tmp.replace(out)
    payload = {"kind": "sprite-gen-video-canvas-report", "still": str(still), "out": str(out), "state": state, **report}
    if report_path is not None:
        atomic_write_text(report_path.expanduser().resolve(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--still", required=True, type=Path, help="base still on a flat chroma background")
    parser.add_argument("--out", required=True, type=Path, help="padded PNG to feed `sprite-gen video`")
    parser.add_argument("--state", help="motion state name (jump/attack/projectile/... — selects the canvas row)")
    parser.add_argument("--shape", choices=SHAPES, help="override the state's canvas shape")
    parser.add_argument("--facing", choices=("right", "left"), default="right", help="which way the subject faces (wide canvases add room in front)")
    parser.add_argument("--headroom", type=float, help="tall/wide: empty fraction of canvas height above the still (default from the profile)")
    parser.add_argument("--lead", type=float, help="wide: empty fraction in front of the subject (default from the profile)")
    parser.add_argument("--trail", type=float, help="wide: empty fraction behind the subject, for a weapon drawn back (default from the profile)")
    parser.add_argument("--key", choices=KEYS, default="auto", help="chroma key of the still (auto reads the corners; green/magenta are normalized to the exact key; white pads with the corner colour)")
    parser.add_argument("--fit", choices=FITS, default="state", help="state: the state's room for the motion (default); tight: drop the empty rows around the subject and add no room, so it fills the frame and a motion that leaves it is clipped")
    parser.add_argument("--report", type=Path, help="write the canvas report JSON here")


def run(**kwargs: object) -> int:
    payload = run_canvas(
        Path(str(kwargs["still"])), Path(str(kwargs["out"])),
        state=kwargs.get("state"), shape=kwargs.get("shape"), facing=str(kwargs.get("facing") or "right"),  # type: ignore[arg-type]
        headroom=kwargs.get("headroom"), lead=kwargs.get("lead"), trail=kwargs.get("trail"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
        key=str(kwargs.get("key") or "auto"), fit=str(kwargs.get("fit") or "state"),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-canvas", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
