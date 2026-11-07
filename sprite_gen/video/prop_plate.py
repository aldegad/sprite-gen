# SPDX-License-Identifier: Apache-2.0
"""A flat magenta plate painted into a clip's still for the subject to clear, found in each frame of the clip and taken
out of its keyed frames again (`video-canvas --prop-plate`, `video-frames --prop-plate`).

A vault needs something to vault. Asked in words for a wall in the background colour, a video model draws a black
slab, and keying leaves the slab in the frames. So the wall is drawn into the still instead, as a plate the key
does not take and the erase does: flat magenta on the green key, standing on the ground in front of the subject.

`place` sets the plate's box on the canvas, and `video-canvas` paints it and writes it into its report
(`prop_plate.box`, canvas px, right and bottom one past, and `prop_plate.rgb`, its colour). `video-frames` reads both
from that report (`read`): the box is where the search starts, and the colour is the one the plate, its side and its
top are found by.

The plate, of the subject's height on the canvas (`canvas.subject_box`): a wall as high as the thighs (HEIGHT), a
fifth of the height thick (WIDTH), its near side GAP ahead of the subject's front (the side it faces), its foot on
the subject's foot row.

A video model told to keep the camera still pans it anyway, after the body, and turns the plate the body pushes off:
after the first frames the plate is not where the canvas has it. So it is found in each frame (`find`), on the frame
as decoded. Its pieces are the pixels of its colour (each channel within PLATE_TOLERANCE of it) joined side by side or
at corners, each at least PIECE_SHARE of the plate's area on the frame, near where it was last found: within the
plate's own diagonal of that box, and on the first frame of the canvas's box carried onto the frame (`frame_box`). The
plate's front is every pixel of its colour in the box its pieces span, so a plate the subject in front of it cuts into
pieces is found whole. A frame with no piece has no plate found, and the next frame is searched from where it was last
found.

With the camera following the body, the model draws the flat plate as a box: a side in the shadow beside its front and
a top above it, the plate's colour darker. They are found with the front: a shade of the plate's colour (`shade`: its
colour darkened, by a k from SHADE_LEAST to 1, each channel within k PLATE_TOLERANCE of that) joined to the front side by
side or at corners, through other shades, in pieces of at least PIECE_SHARE of the plate's area each. Only what is
joined to the front is read so: a shade the subject holds apart from the plate stays, and so does the dark magenta the
codec mixes where the subject's outline crosses the plate, too thin a piece to be a side. No skin is a shade; mixed with
the plate's colour it is read as one only once nearly half of it is the plate's.

The erase (`erase`) runs on each keyed frame its plate was found in, within RIM px of the plate's pixels (its front,
side and top, which go), and decides on the frame as decoded, not on what the matte or the edge pass made of it. A
pixel goes where red and blue lead green there (by more than MAGENTA_LEAD, red and blue within MAGENTA_BALANCE of each other: the plate, its edge, an edge pixel
of the subject it tinted), or where it is the line the plate's edge blends into the key. A codec's 4:2:0 chroma leaves
that line neither colour (a grey, a dark magenta, a pale green), so it is read by where and what it is: within
BLEND_REACH px of a pixel of the key's colour (each channel within PLATE_TOLERANCE of it), its light within LUMA_SLACK
of the span between the plate's and the key's, and its hue on the axis between them (red and blue within BLEND_BALANCE
plus BLEND_SLOPE of how far their mean is from green). The subject in front of the plate is none of these: its skin,
cloth and hair lie off that axis, its dark outline below that light and its white above it. A grey of the subject's
own between the plate and the key goes with the line. The codec can also ring a blue grey into that line, which no
colour tells from denim: what is left after the two rules, in a piece (opaque pixels joined side by side or at corners)
that lies wholly within RIM px of what the plate's pixels and the two rules took, is the plate's edge as well; so is a
box's far edge the model draws darker and redder than any shade, with the line beyond it. The subject reaches beyond
that and stays, with the rest of the line where its pixels touch it; a piece of the subject the plate cuts off there
would go.

The frames report's rows say, frame by frame, how many pixels of the front (`plate_px`) and of the side and top
(`side_px`) were found and erased, and where the subject stays within RIM px of the side or top (`side_touch_px`), a
frame `warnings` names.

The edge pass (`--decontam`) learns the subject's palette on the first frame it runs on, where the plate stands as
opaque as the subject: `not_subject` is the plate's place there (its pixels and RIM px round them), left out of what the
palette is learned from, so the plate's magenta, its side's and its top's are not taken for colours the subject owns.
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
SHADE_LEAST = 0.4  # of the plate's light: the darkest shade of its colour its side or top is read as
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
    if not (isinstance(plate.get("rgb"), list) and len(plate["rgb"]) == 3):
        raise SystemExit(f"video-frames: --prop-plate: {path} holds no plate colour (prop_plate.rgb); make the canvas again "
                         "with video-canvas --prop-plate")
    return {"report": str(path), "box": [int(v) for v in plate["box"]], "rgb": [int(v) for v in plate["rgb"]],
            "canvas": [int(v) for v in data["canvas"]]}


def frame_box(box: list[int], canvas: list[int], frame: tuple[int, int]) -> tuple[int, int, int, int]:
    """The canvas `box` on a frame of size `frame`: scaled by frame / canvas each way, rounded outward, kept on the
    frame."""
    (x0, y0, x1, y1), (cw, ch), (fw, fh) = box, canvas, frame
    return max(0, x0 * fw // cw), max(0, y0 * fh // ch), min(fw, -(-x1 * fw // cw)), min(fh, -(-y1 * fh // ch))


def _colour(rgb: np.ndarray, target: tuple[int, int, int] | list[int]) -> np.ndarray:
    """Where `rgb` (H, W, 3 int) is within PLATE_TOLERANCE of `target` on each channel."""
    return np.abs(rgb - np.asarray(target, dtype=np.int32)).max(axis=2) <= PLATE_TOLERANCE


def shade(rgb: np.ndarray, colour: tuple[int, int, int] | list[int]) -> np.ndarray:
    """Where `rgb` (H, W, 3 int) is a shade of `colour`, as a box's side or top in the shadow is: `colour` darkened, by
    the k (from SHADE_LEAST to 1) that brings it nearest, each channel within k times PLATE_TOLERANCE of it."""
    c = np.asarray(colour, dtype=np.float64)
    k = np.clip((rgb @ c) / float(c @ c), 0.0, 1.0)
    return (k >= SHADE_LEAST) & (np.abs(rgb - k[..., None] * c).max(axis=2) <= k * PLATE_TOLERANCE)


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
    where it was, the `box` its front and the side and top joined to it span (frame px, right and bottom one past), its
    front's pixel count (`plate_px`) and its side's and top's (`side_px`), how far its front is turned (`tilt`,
    `_tilt`), and in the box its pixels (`pixels`, for `region`) and those of its side and top (`sides`)."""
    from sprite_gen.video.frames import _components
    with Image.open(files[0]) as first:
        size = first.size
    colour = plate["rgb"]
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
        seen = rgb[wy0:wy1, wx0:wx1]
        own = _colour(seen, colour)
        ys, starts, stops, labels, sizes = _components(own, diagonal=True)
        piece = sizes[labels] >= least if len(ys) else np.zeros(0, dtype=bool)
        if not piece.any():
            rows.append({"frame": f.name, "found": False})
            continue
        x0, x1 = int(starts[piece].min()), int(stops[piece].max())
        y0, y1 = int(ys[piece].min()), int(ys[piece].max()) + 1
        front = np.zeros(own.shape, dtype=bool)
        front[y0:y1, x0:x1] = own[y0:y1, x0:x1]
        py, px = np.nonzero(front)
        sides = _pieces(own | shade(seen, colour), front, wholly=False) & ~front
        sides = _large(sides, least)
        sy, sx = np.nonzero(sides)
        if len(sy):
            x0, x1 = min(x0, int(sx.min())), max(x1, int(sx.max()) + 1)
            y0, y1 = min(y0, int(sy.min())), max(y1, int(sy.max()) + 1)
        box = (x0 + wx0, y0 + wy0, x1 + wx0, y1 + wy0)
        rows.append({"frame": f.name, "found": True, "box": list(box), "plate_px": int(len(px)), "side_px": int(len(sy)),
                     "tilt": _tilt(py.astype(np.float64), px.astype(np.float64)),
                     "pixels": (front | sides)[y0:y1, x0:x1], "sides": sides[y0:y1, x0:x1]})
        last = box
    return rows


def region(row: dict[str, Any], size: tuple[int, int], part: str = "pixels") -> tuple[tuple[int, int, int, int], np.ndarray]:
    """Where the erase reaches in a frame of `size` whose plate `find` found (`row`): a box (left, top, right, bottom)
    and, in it, every pixel within RIM px of the plate's (`part` "sides": of its side's and top's), each way and
    diagonally."""
    from sprite_gen.video.frames import _near
    x0, y0, x1, y1 = row["box"]
    bx0, by0, bx1, by1 = max(0, x0 - RIM), max(0, y0 - RIM), min(size[0], x1 + RIM), min(size[1], y1 + RIM)
    mask = np.zeros((by1 - by0, bx1 - bx0), dtype=bool)
    mask[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0] = row[part]
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


def _blend_line(rgb: np.ndarray, near_key: np.ndarray, colour: list[int]) -> np.ndarray:
    """Where `rgb` (H, W, 3 int, as decoded) is the line the plate's edge blends into the key: `near_key`, its light
    within LUMA_SLACK of the span between the plate's (`colour`) and the key's, its hue on the axis between them."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    low, high = sorted((float(np.dot(colour, LUMA)), float(np.dot(KEY, LUMA))))
    light = rgb @ LUMA
    return (near_key & (light >= low - LUMA_SLACK) & (light <= high + LUMA_SLACK)
            & (np.abs(r - b) <= BLEND_BALANCE + BLEND_SLOPE * np.abs(r + b - 2 * g)))


def _large(mask: np.ndarray, least: int) -> np.ndarray:
    """The pixels of `mask` in a piece (joined side by side or at corners) of at least `least` pixels."""
    from sprite_gen.video.frames import _components
    out = np.zeros(mask.shape, dtype=bool)
    ys, starts, stops, labels, sizes = _components(mask, diagonal=True)
    for y, x0, x1, label in zip(ys, starts, stops, labels):
        if sizes[label] >= least:
            out[y, x0:x1] = True
    return out


def _pieces(mask: np.ndarray, other: np.ndarray, *, wholly: bool) -> np.ndarray:
    """The pixels of `mask` in a piece (joined side by side or at corners) that lies wholly in `other` (`wholly`), or
    that has a pixel in it."""
    from sprite_gen.video.frames import _components
    ys, starts, stops, labels, _ = _components(mask, diagonal=True)
    flagged = np.zeros(len(ys), dtype=bool)  # by piece: some pixel of it outside `other` (wholly), or in it
    for y, x0, x1, label in zip(ys, starts, stops, labels):
        if (not other[y, x0:x1].all()) if wholly else other[y, x0:x1].any():
            flagged[label] = True
    out = np.zeros(mask.shape, dtype=bool)
    for y, x0, x1, label in zip(ys, starts, stops, labels):
        if flagged[label] != wholly:
            out[y, x0:x1] = True
    return out


def erase(files: list[Path], decoded: list[Path], rows: list[dict[str, Any]], plate: dict[str, Any]) -> dict[str, Any]:
    """Erase the plate `find` found (`rows`) from every keyed frame in `files` (in place), each read on its frame as
    decoded (`decoded`, in the same order); the frames report's `prop_plate`."""
    from sprite_gen.video.frames import _near
    with Image.open(decoded[0]) as first:
        size = first.size
    colour = plate["rgb"]
    report_rows, not_found, touching, side_touching = [], [], [], []
    changed = erased = 0
    for f, src, row in zip(files, decoded, rows):
        kept = {k: v for k, v in row.items() if k not in ("pixels", "sides")}
        report_rows.append(kept)
        if not row["found"]:
            not_found.append(row["frame"])
            continue
        (rx0, ry0, rx1, ry1), reach = region(row, size)
        _, side_reach = region(row, size, "sides")
        # seen RIM px and a pixel wider than the reach's box: what is left goes in a piece wholly within RIM px of what
        # the plate's pixels and the colour rules took, and a piece that leaves that is seen to leave it
        pad = RIM + 1
        x0, y0, x1, y1 = max(0, rx0 - pad), max(0, ry0 - pad), min(size[0], rx1 + pad), min(size[1], ry1 + pad)
        wide, found, sides = (np.zeros((y1 - y0, x1 - x0), dtype=bool) for _ in range(3))
        wide[ry0 - y0:ry1 - y0, rx0 - x0:rx1 - x0] = reach
        sides[ry0 - y0:ry1 - y0, rx0 - x0:rx1 - x0] = side_reach
        bx0, by0, bx1, by1 = row["box"]
        found[by0 - y0:by1 - y0, bx0 - x0:bx1 - x0] = row["pixels"]
        reach = wide
        rgb = _decoded(src)
        # a pixel of the key's colour within BLEND_REACH px of the reach's own: seen in a box that much wider
        kx0, ky0 = max(0, x0 - BLEND_REACH), max(0, y0 - BLEND_REACH)
        kx1, ky1 = min(size[0], x1 + BLEND_REACH), min(size[1], y1 + BLEND_REACH)
        near_key = _near(_colour(rgb[ky0:ky1, kx0:kx1], KEY), BLEND_REACH)[y0 - ky0:y1 - ky0, x0 - kx0:x1 - kx0]
        seen = rgb[y0:y1, x0:x1]
        r, g, b = seen[..., 0], seen[..., 1], seen[..., 2]
        tinted = (np.minimum(r, b) - g > MAGENTA_LEAD) & (np.abs(r - b) < MAGENTA_BALANCE)
        gone = found | (reach & (tinted | _blend_line(seen, near_key, colour)))
        with Image.open(f) as im:
            a = np.array(im.convert("RGBA"))
        part = a[y0:y1, x0:x1]
        opaque = part[..., 3] > 0
        gone |= _pieces(opaque & ~gone, _near(gone, RIM), wholly=True)
        stay = ~gone & opaque
        took, touch, side_touch = int((gone & opaque).sum()), int((reach & stay).sum()), int((sides & stay).sum())
        kept.update(erased_px=took, touch_px=touch, side_touch_px=side_touch)
        if touch:
            touching.append(row["frame"])
        if side_touch:
            side_touching.append(row["frame"])
        if took:
            part[gone] = 0
            Image.fromarray(a, "RGBA").save(f)
            changed += 1
            erased += took
    return {"canvas_report": plate["report"], "start_box": list(frame_box(plate["box"], plate["canvas"], size)), "rim": RIM,
            "frames": len(files), "frames_changed": changed, "erased_px": erased, "not_found": not_found,
            "touching": touching, "side_touching": side_touching, "rows": report_rows}


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:NAMED])
    return shown if len(names) <= NAMED else f"{shown} and {len(names) - NAMED} more"


def warnings(done: dict[str, Any]) -> list[str]:
    """The lines to read after an erase (`erase`'s report): the frames no plate was found in, which nothing was
    erased from, those the plate meets the subject in, whose pixels beside it were judged one by one, and those its
    side or top meets the subject in."""
    lines = []
    if done.get("not_found"):
        lines.append(f"--prop-plate: no plate found in {len(done['not_found'])} frame(s) ({_names(done['not_found'])}): "
                     "nothing was erased there, so any of the plate those frames show is still in them")
    if done.get("touching"):
        lines.append(f"--prop-plate: the plate meets the subject in {len(done['touching'])} frame(s) "
                     f"({_names(done['touching'])}): within {RIM} px of the plate the subject's pixels stay, but those the "
                     "plate tinted magenta and a grey between the plate and the key went with it; look at those frames")
    if done.get("side_touching"):
        lines.append(f"--prop-plate: the plate's shaded side or top meets the subject in {len(done['side_touching'])} "
                     f"frame(s) ({_names(done['side_touching'])}): they went with the plate, and within {RIM} px of them the "
                     "subject's pixels stay but for those tinted magenta; look at the subject's edge there")
    return lines
