# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-canvas` — pad a base still into the canvas a motion state needs.

Grok Imagine keeps the input image's framing and ignores `aspect_ratio` on
image-to-video (2026-09-08 실측: a `3:4` request still returned 960x960). So the
canvas is decided HERE, on the still: a jump needs head-room above (tall), an
attack or projectile needs room in front (wide), everything else stays square.
The state -> canvas table below is the single owner of that rule; `--shape`
overrides it per call.

The padding is filled with the still's own corner colour (its chroma key) so the
clip stays keyable end to end. A still whose corners disagree is refused — a
non-flat background cannot be extended without guessing.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.spec.runio import atomic_write_text

SHAPE_SQUARE = "square"
SHAPE_TALL = "tall"
SHAPE_WIDE = "wide"
SHAPES = (SHAPE_SQUARE, SHAPE_TALL, SHAPE_WIDE)


@dataclass(frozen=True)
class CanvasProfile:
    shape: str
    ratio: float  # width / height
    headroom: float  # fraction of the canvas height kept empty ABOVE the subject (tall)
    lead: float  # fraction of the canvas width kept empty IN FRONT of the subject (wide)
    why: str


# The one table. Keys are state names as the sprite-request uses them; unknown
# states fall through to `default`.
STATE_CANVAS: dict[str, CanvasProfile] = {
    "jump": CanvasProfile(SHAPE_TALL, 3 / 4, 0.34, 0.0, "airborne frames need head-room; hair clipped at 1:1"),
    "attack": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.28, "weapon swings and projectiles extend in front"),
    "projectile": CanvasProfile(SHAPE_WIDE, 16 / 9, 0.0, 0.34, "projectile travels away from the body"),
    "default": CanvasProfile(SHAPE_SQUARE, 1.0, 0.0, 0.0, "in-place motion fits the still's own frame"),
}
SHAPE_DEFAULTS: dict[str, CanvasProfile] = {
    SHAPE_SQUARE: STATE_CANVAS["default"],
    SHAPE_TALL: STATE_CANVAS["jump"],
    SHAPE_WIDE: STATE_CANVAS["attack"],
}
CORNER_TOLERANCE = 24  # max per-channel spread across the four corners for a "flat" background


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


def pad_canvas(
    image: Image.Image,
    profile: CanvasProfile,
    *,
    facing: str = "right",
    headroom: float | None = None,
    lead: float | None = None,
) -> tuple[Image.Image, dict[str, Any]]:
    """Return (padded RGB image, placement report). Never downsizes the still."""
    key = corner_key(image)
    src = image.convert("RGB")
    w, h = src.size
    head = profile.headroom if headroom is None else headroom
    front = profile.lead if lead is None else lead
    if not 0 <= head < 0.9 or not 0 <= front < 0.9:
        raise SystemExit("video-canvas: --headroom/--lead must be in [0, 0.9)")
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
    else:  # wide: extra width goes in front of the facing direction; at least the profile ratio
        canvas_w = max(w, round(w / (1 - front)), round(h * profile.ratio))
        canvas_h = max(h, round(canvas_w / profile.ratio))
        y = canvas_h - h
        x = 0 if facing == "right" else canvas_w - w
    canvas = Image.new("RGB", (canvas_w, canvas_h), key)
    canvas.paste(src, (x, y))
    report = {
        "shape": profile.shape,
        "ratio": round(canvas_w / canvas_h, 4),
        "canvas": [canvas_w, canvas_h],
        "still": [w, h],
        "offset": [x, y],
        "headroom": head,
        "lead": front,
        "facing": facing,
        "key_rgb": list(key),
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
) -> dict[str, Any]:
    still = still.expanduser().resolve()
    if not still.is_file():
        raise SystemExit(f"video-canvas: still not found: {still}")
    profile = profile_for(state, shape)
    canvas, report = pad_canvas(Image.open(still), profile, facing=facing, headroom=headroom, lead=lead)
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
    parser.add_argument("--headroom", type=float, help="tall: empty fraction above the subject (default from the profile)")
    parser.add_argument("--lead", type=float, help="wide: empty fraction in front of the subject (default from the profile)")
    parser.add_argument("--report", type=Path, help="write the canvas report JSON here")


def run(**kwargs: object) -> int:
    payload = run_canvas(
        Path(str(kwargs["still"])), Path(str(kwargs["out"])),
        state=kwargs.get("state"), shape=kwargs.get("shape"), facing=str(kwargs.get("facing") or "right"),  # type: ignore[arg-type]
        headroom=kwargs.get("headroom"), lead=kwargs.get("lead"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-canvas", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
