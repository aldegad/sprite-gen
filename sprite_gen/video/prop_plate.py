# SPDX-License-Identifier: Apache-2.0
"""A flat magenta plate painted into a clip's still for the subject to clear, found in each frame of the clip and taken
out of its keyed frames again (`video-canvas --prop-plate`, `video-frames --prop-plate`).

A vault needs something to vault. Asked in words for a wall in the background colour, a video model draws a black
slab, and keying leaves the slab in the frames. So the wall is drawn into the still instead, as a plate the key
does not take and the erase does: flat magenta on the green key, standing on the ground in front of the subject.

`place` sets the plate's box on the canvas, and `video-canvas` paints it and writes it into its report
(`prop_plate.box`, canvas px, right and bottom one past). `video-frames` reads it from that report (`read`).

The plate, of the subject's height on the canvas (`canvas.subject_box`): a wall as high as the thighs (HEIGHT), a
fifth of the height thick (WIDTH), its near side GAP ahead of the subject's front (the side it faces), its foot on
the subject's foot row.

A video model told to keep the camera still pans it anyway, after the body, and turns the plate the body pushes off:
after the first frames the plate is not where the canvas has it. So it is found in each frame (`find`), on the frame
as decoded. Its pieces are the pixels of its colour (each channel within PLATE_TOLERANCE of RGB) joined side by side or
at corners, each at least PIECE_SHARE of the plate's area on the frame, near where it was last found: within the
plate's own diagonal of that box, and on the first frame of the canvas's box carried onto the frame (`frame_box`). The
plate is every pixel of its colour in the box its pieces span, so a plate the subject in front of it cuts into pieces
is found whole. A frame with no piece has no plate found, and the next frame is searched from where it was last found.

The erase (`erase`) runs on each keyed frame its plate was found in, within RIM px of the plate's pixels, and decides
on the frame as decoded, not on what the matte or the edge pass made of it. A pixel goes where red and blue lead green
there (by more than MAGENTA_LEAD, red and blue within MAGENTA_BALANCE of each other: the plate, its edge, an edge pixel
of the subject it tinted), or where it is the line the plate's edge blends into the key. A codec's 4:2:0 chroma leaves
that line neither colour (a grey, a dark magenta, a pale green), so it is read by where and what it is: within
BLEND_REACH px of a pixel of the key's colour (each channel within PLATE_TOLERANCE of it), its light within LUMA_SLACK
of the span between the plate's and the key's, and its hue on the axis between them (red and blue within BLEND_BALANCE
plus BLEND_SLOPE of how far their mean is from green). The subject in front of the plate is none of these: its skin,
cloth and hair lie off that axis, its dark outline below that light and its white above it. A grey of the subject's
own between the plate and the key goes with the line. The codec can also ring a blue grey into that line, which no
colour tells from denim: what is left within RIM px of the plate after the two rules, in a piece (opaque pixels joined
side by side or at corners) that lies wholly there, is the plate's edge as well. The subject reaches beyond the
plate's rim and stays, with the rest of the line where its pixels touch it; a piece of the subject the plate cuts off
within its rim would go.

The edge pass (`--decontam`) learns the subject's palette on the first frame it runs on, where the plate stands as
opaque as the subject: `not_subject` is the plate's place there (its pixels and RIM px round them), left out of what the
palette is learned from, so the plate's magenta is not taken for a colour the subject owns.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.frames.cutout import GREEN, KEY_TARGETS, MAGENTA
from sprite_gen.frames.decontam import LUMA

RGB = KEY_TARGETS[MAGENTA]
KEY = KEY_TARGETS[GREEN]
HEIGHT = 0.49  # of the subject's height
WIDTH = 0.20
GAP = 0.84
# px round the plate's pixels the erase reaches: a decoded edge runs on past the plate's colour into a dark magenta and
# then the line blended into the key, a pixel or two each
RIM = 4
BLEND_REACH = 2  # px from a pixel of the key's colour: the line blended into the key lies against it
PLATE_TOLERANCE = 48  # on each channel: the plate's colour (and the key's) as a codec returns it
PIECE_SHARE = 0.01  # of the plate's area on the frame: the least a piece of it holds
MAGENTA_LEAD = 35
MAGENTA_BALANCE = 90
LUMA_SLACK = 40
BLEND_BALANCE = 40
BLEND_SLOPE = 0.35
NAMED = 6  # frames a warning names before it counts the rest
CANVAS_REPORT = "sprite-gen-video-canvas-report"


def place(subject: tuple[int, int, int, int], *, facing: str, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The plate's box (left, top, right, bottom; right and bottom one past) on a canvas of `size`, in front of the
    `subject` box facing `facing`. Refused where it would leave the canvas."""
    left, top, right, bottom = subject
    height = bottom - top
    width, gap = round(WIDTH * height), round(GAP * height)
    if facing == "right":
        x0 = right + gap
        x1 = x0 + width
    else:
        x1 = left - gap
        x0 = x1 - width
    if x0 < 0 or x1 > size[0]:
        raise SystemExit(
            f"video-canvas: --prop-plate: the plate ({x0}..{x1}) falls past the canvas's {facing} edge ({size[0]} px wide); "
            "frame the subject with room in front (--shape wide, --lead)"
        )
    return x0, bottom - round(HEIGHT * height), x1, bottom


def paint(canvas: Image.Image, subject: tuple[int, int, int, int], *, facing: str) -> dict[str, Any]:
    """Paint the plate in front of `subject` on `canvas` (in place); the report's `prop_plate`."""
    box = place(subject, facing=facing, size=canvas.size)
    canvas.paste(RGB, box)
    return {"box": list(box), "rgb": list(RGB), "subject": list(subject),
            "of_subject_height": {"height": HEIGHT, "width": WIDTH, "gap": GAP}}


def read(report: Path) -> dict[str, Any]:
    """The plate a `video-canvas --report` holds: its `box` and the `canvas` size it is on, and the report's path."""
    path = Path(report).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"video-frames: --prop-plate: cannot read the canvas report {path}: {e}") from e
    if not isinstance(data, dict) or data.get("kind") != CANVAS_REPORT:
        raise SystemExit(f"video-frames: --prop-plate: {path} is not a video-canvas report ({CANVAS_REPORT})")
    plate = data.get("prop_plate")
    if not isinstance(plate, dict):
        raise SystemExit(f"video-frames: --prop-plate: {path} holds no plate; the canvas was made without --prop-plate")
    return {"report": str(path), "box": [int(v) for v in plate["box"]], "canvas": [int(v) for v in data["canvas"]]}


def frame_box(box: list[int], canvas: list[int], frame: tuple[int, int]) -> tuple[int, int, int, int]:
    """The canvas `box` on a frame of size `frame`: scaled by frame / canvas each way, rounded outward, kept on the
    frame."""
    (x0, y0, x1, y1), (cw, ch), (fw, fh) = box, canvas, frame
    return max(0, x0 * fw // cw), max(0, y0 * fh // ch), min(fw, -(-x1 * fw // cw)), min(fh, -(-y1 * fh // ch))


def _colour(rgb: np.ndarray, target: tuple[int, int, int]) -> np.ndarray:
    """Where `rgb` (H, W, 3 int) is within PLATE_TOLERANCE of `target` on each channel."""
    return np.abs(rgb - np.asarray(target, dtype=np.int32)).max(axis=2) <= PLATE_TOLERANCE


def _decoded(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).astype(np.int32)


def _tilt(ys: np.ndarray, xs: np.ndarray) -> float:
    """How far the long axis of the pixels at `ys`, `xs` is turned from upright: degrees, clockwise on the frame
    positive (its top to the right)."""
    if len(xs) < 2:
        return 0.0
    _, vectors = np.linalg.eigh(np.cov(np.stack([xs - xs.mean(), ys - ys.mean()])))
    vx, vy = vectors[:, 1]
    if vy > 0:
        vx, vy = -vx, -vy
    return round(math.degrees(math.atan2(vx, -vy)), 1) + 0.0


def find(files: list[Path], plate: dict[str, Any]) -> list[dict[str, Any]]:
    """Where the plate (`read`) is in each frame of `files`, the clip's frames as decoded: a row a frame, `found` and,
    where it was, the `box` its pieces span (frame px, right and bottom one past), its pixels' count (`plate_px`), how
    far it is turned (`tilt`, `_tilt`), and its pixels in the box (`pixels`, for `region`)."""
    from sprite_gen.video.frames import _components
    with Image.open(files[0]) as first:
        size = first.size
    last = frame_box(plate["box"], plate["canvas"], size)
    width, height = last[2] - last[0], last[3] - last[1]
    reach = math.ceil(math.hypot(width, height))
    least = max(2, round(PIECE_SHARE * width * height))
    rows: list[dict[str, Any]] = []
    for f in files:
        rgb = _decoded(f)
        h, w = rgb.shape[:2]
        wx0, wy0 = max(0, last[0] - reach), max(0, last[1] - reach)
        wx1, wy1 = min(w, last[2] + reach), min(h, last[3] + reach)
        own = _colour(rgb[wy0:wy1, wx0:wx1], RGB)
        ys, starts, stops, labels, sizes = _components(own, diagonal=True)
        piece = sizes[labels] >= least if len(ys) else np.zeros(0, dtype=bool)
        if not piece.any():
            rows.append({"frame": f.name, "found": False})
            continue
        x0, x1 = int(starts[piece].min()), int(stops[piece].max())
        y0, y1 = int(ys[piece].min()), int(ys[piece].max()) + 1
        pixels = own[y0:y1, x0:x1]
        py, px = np.nonzero(pixels)
        box = (x0 + wx0, y0 + wy0, x1 + wx0, y1 + wy0)
        rows.append({"frame": f.name, "found": True, "box": list(box), "plate_px": int(len(px)),
                     "tilt": _tilt(py.astype(np.float64), px.astype(np.float64)), "pixels": pixels})
        last = box
    return rows


def region(row: dict[str, Any], size: tuple[int, int]) -> tuple[tuple[int, int, int, int], np.ndarray]:
    """Where the erase reaches in a frame of `size` whose plate `find` found (`row`): a box (left, top, right, bottom)
    and, in it, every pixel within RIM px of the plate's, each way and diagonally."""
    from sprite_gen.video.frames import _near
    x0, y0, x1, y1 = row["box"]
    bx0, by0, bx1, by1 = max(0, x0 - RIM), max(0, y0 - RIM), min(size[0], x1 + RIM), min(size[1], y1 + RIM)
    mask = np.zeros((by1 - by0, bx1 - bx0), dtype=bool)
    mask[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0] = row["pixels"]
    return (bx0, by0, bx1, by1), _near(mask, RIM)


def not_subject(row: dict[str, Any], size: tuple[int, int]) -> np.ndarray | None:
    """The plate's place on a frame of `size` (`region`, the whole frame), for the edge pass to learn the subject's
    palette without; None where `find` found no plate."""
    if not row["found"]:
        return None
    (x0, y0, x1, y1), mask = region(row, size)
    out = np.zeros((size[1], size[0]), dtype=bool)
    out[y0:y1, x0:x1] = mask
    return out


def _blend_line(rgb: np.ndarray, near_key: np.ndarray) -> np.ndarray:
    """Where `rgb` (H, W, 3 int, as decoded) is the line the plate's edge blends into the key: `near_key`, its light
    within LUMA_SLACK of the span between the plate's and the key's, its hue on the axis between them."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    low, high = sorted((float(np.dot(RGB, LUMA)), float(np.dot(KEY, LUMA))))
    light = rgb @ LUMA
    return (near_key & (light >= low - LUMA_SLACK) & (light <= high + LUMA_SLACK)
            & (np.abs(r - b) <= BLEND_BALANCE + BLEND_SLOPE * np.abs(r + b - 2 * g)))


def _within(left: np.ndarray, reach: np.ndarray) -> np.ndarray:
    """The pixels of `left` in a piece (joined side by side or at corners) that lies wholly in `reach`."""
    from sprite_gen.video.frames import _components
    ys, starts, stops, labels, _ = _components(left, diagonal=True)
    out = np.zeros(len(ys), dtype=bool)  # by piece: some pixel of it outside `reach`
    for y, x0, x1, label in zip(ys, starts, stops, labels):
        if not reach[y, x0:x1].all():
            out[label] = True
    inside = np.zeros(left.shape, dtype=bool)
    for y, x0, x1, label in zip(ys, starts, stops, labels):
        if not out[label]:
            inside[y, x0:x1] = True
    return inside


def erase(files: list[Path], decoded: list[Path], rows: list[dict[str, Any]], plate: dict[str, Any]) -> dict[str, Any]:
    """Erase the plate `find` found (`rows`) from every keyed frame in `files` (in place), each read on its frame as
    decoded (`decoded`, in the same order); the frames report's `prop_plate`."""
    from sprite_gen.video.frames import _near
    with Image.open(decoded[0]) as first:
        size = first.size
    report_rows, not_found, touching = [], [], []
    changed = erased = 0
    for f, src, row in zip(files, decoded, rows):
        kept = {k: v for k, v in row.items() if k != "pixels"}
        report_rows.append(kept)
        if not row["found"]:
            not_found.append(row["frame"])
            continue
        (rx0, ry0, rx1, ry1), reach = region(row, size)
        # seen a pixel wider than the reach's box, so a piece that leaves the box is seen to leave the reach
        x0, y0, x1, y1 = max(0, rx0 - 1), max(0, ry0 - 1), min(size[0], rx1 + 1), min(size[1], ry1 + 1)
        wide = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        wide[ry0 - y0:ry1 - y0, rx0 - x0:rx1 - x0] = reach
        reach = wide
        rgb = _decoded(src)
        # a pixel of the key's colour within BLEND_REACH px of the reach's own: seen in a box that much wider
        kx0, ky0 = max(0, x0 - BLEND_REACH), max(0, y0 - BLEND_REACH)
        kx1, ky1 = min(size[0], x1 + BLEND_REACH), min(size[1], y1 + BLEND_REACH)
        near_key = _near(_colour(rgb[ky0:ky1, kx0:kx1], KEY), BLEND_REACH)[y0 - ky0:y1 - ky0, x0 - kx0:x1 - kx0]
        seen = rgb[y0:y1, x0:x1]
        r, g, b = seen[..., 0], seen[..., 1], seen[..., 2]
        tinted = (np.minimum(r, b) - g > MAGENTA_LEAD) & (np.abs(r - b) < MAGENTA_BALANCE)
        gone = reach & (tinted | _blend_line(seen, near_key))
        with Image.open(f) as im:
            a = np.array(im.convert("RGBA"))
        part = a[y0:y1, x0:x1]
        opaque = part[..., 3] > 0
        gone |= _within(opaque & ~gone, reach)
        took, touch = int((gone & opaque).sum()), int((reach & ~gone & opaque).sum())
        kept.update(erased_px=took, touch_px=touch)
        if touch:
            touching.append(row["frame"])
        if took:
            part[gone] = 0
            Image.fromarray(a, "RGBA").save(f)
            changed += 1
            erased += took
    return {"canvas_report": plate["report"], "start_box": list(frame_box(plate["box"], plate["canvas"], size)), "rim": RIM,
            "frames": len(files), "frames_changed": changed, "erased_px": erased, "not_found": not_found,
            "touching": touching, "rows": report_rows}


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:NAMED])
    return shown if len(names) <= NAMED else f"{shown} and {len(names) - NAMED} more"


def warnings(done: dict[str, Any]) -> list[str]:
    """The lines to read after an erase (`erase`'s report): the frames no plate was found in, which nothing was
    erased from, and those the plate meets the subject in, whose pixels beside it were judged one by one."""
    lines = []
    if done.get("not_found"):
        lines.append(f"--prop-plate: no plate found in {len(done['not_found'])} frame(s) ({_names(done['not_found'])}): "
                     "nothing was erased there, so any of the plate those frames show is still in them")
    if done.get("touching"):
        lines.append(f"--prop-plate: the plate meets the subject in {len(done['touching'])} frame(s) "
                     f"({_names(done['touching'])}): within {RIM} px of the plate the subject's pixels stay, but those the "
                     "plate tinted magenta and a grey between the plate and the key went with it; look at those frames")
    return lines
