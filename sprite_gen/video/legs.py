# SPDX-License-Identifier: Apache-2.0
"""What a walk's legs say, read off the solid pixels of each frame.

Two stages read them. `video-cycle-align` turns each loop to start as a heel lands
(`align.foot_strike`), and the two-cycle screen (sprite_gen/video/period.py) records how a gait's
steps fall in a shorter return it found — evidence for whoever looks at it, never what decides.
Both read the same per-frame signals (`strike_signals`) and choose between them by the same swings
(`legs_by`):

- `stride`: the width of the foot band (the lowest FOOT_BAND of the frame's own height) as a
  fraction of that height. Seen from the side or a diagonal the feet open and close once a step.
- `reach`: the lowest solid row. Seen from the front or back the foot nearer the viewer is drawn
  lower, once a step.
- `top`: the body's top line, for a body with no legs to read. It rises and falls once a step
  whichever leg leads, so the step count reads it as the witness that a crossing interval, or a
  cut, holds two steps (`local_cycle.body_word`, `local_cycle.cut_word`) — against the lowest row,
  the lowest opaque row, the rows down the middle of the body and the widths of the upper body,
  since the picture moving or zoomed with the body moves the line as well.
- `gap`: the widest empty run between the foot band's two ends, of the frame's height — two feet
  seen apart.

The step count's witness reads more (`body_signals`), recorded nowhere else:

- `bands`: the upper body's widths, the mean width (in alpha, pixels) of each band of BANDS, each
  at its own depth below the first solid row, of the clip's median body height: from the top of
  the head down to the middle of the body. A body rising and falling on its legs carries them
  whole and leaves their widths; a picture zoomed in and out widens and narrows each as the top
  line lifts and lowers, as far as its outline lets it: a band whose sides run straight down by
  the zoom's whole part, a band across a top that widens as it goes down (a pointed head, an
  ear's tip, a hat's point) by less, the band under a point by none. How far is measured, not
  assumed (`zoomed`, `band_widths`): `band_rows` are the bands' rows below the first solid row,
  and `median_frame` is the alpha of the frame whose body height is the clip's median, to be
  zoomed as far as the beat the witness reads.
- `sole`: the lowest row with a pixel drawn fully opaque (alpha OPAQUE), or the lowest solid row
  where a frame has none. A shadow is drawn translucent under the feet, solid all the same, and
  it need not move with them: one drawn on the screen stays while the picture moves, and the
  lowest solid row is then its, not the feet's. The feet are drawn opaque.
- `edge`: 1 where the lowest solid row is the frame's last row — the feet may run off the
  frame, and the lowest row is the frame's, not theirs — else 0.
- `first_row`: the first solid row. `top` passes over what is narrower than half the frame's
  widest row, and it is taken off the head by a part as wide as that, a tail or a cape lifted over
  it, where the first solid row stays on the body's top. The first solid row takes an ear's or a
  hat's tip in. Neither is the better line, so the witness reads both (`local_cycle.TOP_LINES`).
- `strips`: per frame, the rows down the middle of the body (`_strip`), DEEP of its median height
  from the first solid row, in colour and cover. A body rising on its legs carries its rows whole,
  where a picture zoomed in and out draws them longer and shorter. Arms swinging across the bands
  hardly touch them, so the witness reads them where the bands cannot tell (`local_cycle.body_depth`).

Which of the two feet is which is read on cues the view draws (`depth_cue`, `shade_cue`): from the front or back the
foot nearer the viewer is drawn lower, from the side or a diagonal the far leg is drawn in shade. `video-cycle-align`
names the foot that lands by them (`align.strike_foot`), and the step count asks whether the other leg leads one
crossing interval on (`local_cycle.read_cycles`): a cue can only refuse cycles there, never witness them, since a light
fixed on the screen turns it with how far apart the feet are drawn, whichever leg leads. Legs drawn alike to a cue
leave it still: its range over the clip under half the step it is drawn in (a TONE of luma, a pixel row of the body's
height) is the arithmetic's, not the feet's (`local_cycle.cue_word`).

The swing of the stride or the lowest row says there is something to read, not that it is legs:
a legless body that squashes as it bounces widens its foot band once a cycle, where a walker's
widens once a step. Counting steps therefore also asks for two feet seen apart (`feet_apart`).
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image

from sprite_gen._deps import np

FOOT_BAND = 0.08  # fraction of the frame's own height, measured up from its lowest opaque row
LOW_ALPHA = 128  # the legs are read off solid pixels only
OPAQUE = 255  # drawn fully opaque: the feet, not a shadow drawn translucent under them
BODY_RUN = 0.5  # the body's top line: the first row at least half as wide as the frame's widest
# The upper body's bands, each from and to these fractions of the body's median height below its first solid row: the
# head's top band first, then down to the middle of the body, where the legs part.
BANDS = ((0.02, 0.10), (0.10, 0.18), (0.18, 0.26), (0.26, 0.34), (0.34, 0.42), (0.42, 0.50))
# The strip down the body's middle (`_strip`): STRIP of the body's median height either side of its middle column, DEEP
# of it down from the first solid row: past the waist, where a body's rows are still the body's and not its legs'.
STRIP = 0.12
DEEP = 0.7
# A walk seen from the side or a diagonal opens its feet by a good part of its height each step;
# from the front or back its foot band hardly changes width, but the foot nearer the viewer is
# drawn lower by a few hundredths of it.
STRIDE_MIN = 0.15  # the foot band's swing (of the body's height) that says the feet open along the picture
REACH_MIN = 0.015  # the lowest row's swing (of the body's height) that says a foot steps toward the viewer
# Two feet seen apart: an empty run at least this wide (of the body's height) between the foot
# band's ends, in at least FEET_APART_SHARE of the frames. A walker's feet part for a good share of
# each step, from any view; a legless body's foot band is one run.
FEET_APART = 0.02
FEET_APART_SHARE = 0.2
SHADE_BAND = 0.15  # the shade cue reads the feet and lower legs: the lowest 15 % of the body
TONE = 1 / 255  # one 8-bit level of luma (0..1): the step the shade cue's tones are drawn in; the depth cue's is a pixel row
CONTACT = 0.005  # a foot's contact: its solid pixels this far (of the body's height, at least a row) up from its lowest row


def _longest_runs(solid: np.ndarray) -> np.ndarray:
    """The longest horizontal run of solid pixels in each row."""
    edges = np.diff(np.pad(solid.astype(np.int8), ((0, 0), (1, 1))), axis=1)
    runs = np.zeros(solid.shape[0], dtype=int)
    for y in np.nonzero(solid.any(axis=1))[0]:
        starts, ends = np.nonzero(edges[y] == 1)[0], np.nonzero(edges[y] == -1)[0]
        runs[y] = int((ends - starts).max())
    return runs


def _widest_gap(cols: np.ndarray) -> int:
    """The longest run of False between the first and the last True of `cols`."""
    xs = np.nonzero(cols)[0]
    inner = ~cols[xs[0] : xs[-1] + 1]
    if not inner.any():
        return 0
    edges = np.diff(np.pad(inner.astype(np.int8), 1))
    return int((np.nonzero(edges == -1)[0] - np.nonzero(edges == 1)[0]).max())


def _gap_at(cols: np.ndarray) -> tuple[int, int] | None:
    """(the first, the last column) of the longest run of False between the first and the last True of `cols`, or None
    where there is none."""
    xs = np.nonzero(cols)[0]
    inner = ~cols[xs[0] : xs[-1] + 1]
    if not inner.any():
        return None
    edges = np.diff(np.pad(inner.astype(np.int8), 1))
    starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
    widest = int(np.argmax(ends - starts))
    return int(xs[0] + starts[widest]), int(xs[0] + ends[widest] - 1)


def strike_signals(frames: list[Image.Image]) -> dict[str, list[float]]:
    """Per frame, from the solid pixels: `stride`, `reach`, `top`, `gap` (see the module) and
    `height`, the frame's own body height in pixels. `top` passes over an ear, a hat's point or an
    antenna: the first row whose longest solid run is at least BODY_RUN of the frame's widest."""
    out: dict[str, list[float]] = {"stride": [], "reach": [], "top": [], "gap": [], "height": []}
    for f in frames:
        solid = np.asarray(f.getchannel("A")) >= LOW_ALPHA
        rows = np.nonzero(solid.any(axis=1))[0]
        if rows.size == 0:
            raise ValueError("a loop frame has no solid body")
        y0, y1 = int(rows[0]), int(rows[-1])
        height = y1 - y0 + 1
        cols = solid[y1 + 1 - max(1, round(height * FOOT_BAND)) : y1 + 1].any(axis=0)
        xs = np.nonzero(cols)[0]
        runs = _longest_runs(solid)
        out["stride"].append((xs[-1] - xs[0] + 1) / height)
        out["reach"].append(float(y1))
        out["top"].append(float(np.nonzero(runs >= BODY_RUN * runs.max())[0][0]))
        out["gap"].append(_widest_gap(cols) / height)
        out["height"].append(float(height))
    return out


def band_rows(median: float) -> list[tuple[int, int]]:
    """The rows of each band of BANDS below the first solid row of a body `median` rows tall, from and to (not
    including), each at least a row."""
    rows = []
    for a, b in BANDS:
        a0 = max(1, math.ceil(a * median))
        rows.append((a0, max(a0 + 1, math.ceil(b * median))))
    return rows


def _row_widths(alpha: np.ndarray) -> tuple[int, np.ndarray]:
    """(the first solid row, each row's width in alpha, pixels) of `alpha`."""
    solid = np.nonzero((alpha >= LOW_ALPHA).any(axis=1))[0]
    if solid.size == 0:
        raise ValueError("a loop frame has no solid body")
    return int(solid[0]), alpha.sum(axis=1, dtype=np.float64) / 255.0


def _bands_of(y0: int, width: np.ndarray, rows: list[tuple[int, int]]) -> list[float]:
    return [float(width[y0 + a0 : y0 + a1].sum() / (a1 - a0)) for a0, a1 in rows]


def band_widths(alpha: np.ndarray, rows: list[tuple[int, int]]) -> list[float]:
    """The mean width (in alpha, pixels) of each band `rows` (`band_rows`) below the first solid row of `alpha`."""
    return _bands_of(*_row_widths(alpha), rows)


def zoomed(alpha: np.ndarray, scale: float) -> np.ndarray:
    """`alpha` zoomed in by `scale` (bilinear) on a canvas grown to hold it."""
    im = Image.fromarray(np.asarray(alpha, dtype=np.uint8))
    size = (math.ceil(im.width * scale), math.ceil(im.height * scale))
    return np.asarray(im.transform(size, Image.Transform.AFFINE, (1 / scale, 0, 0, 0, 1 / scale, 0),
                                   resample=Image.Resampling.BILINEAR))


def _strip(frame: Image.Image, alpha: np.ndarray, first: int, rows: int, half: float) -> np.ndarray:
    """The strip down the middle of the body of `frame` (`alpha` its alpha, `first` its first solid row): its `rows` from
    the first solid row, `half` px either side of the mean column of the solid pixels among them, each row's mean
    premultiplied colour and its mean cover, (rows, 4) in 0..1, nothing past the frame's last row."""
    middle = float(np.nonzero(alpha[first:first + rows] >= LOW_ALPHA)[1].mean())
    left, right = max(0, round(middle - half)), min(alpha.shape[1], round(middle + half) + 1)
    cover = alpha[first:first + rows, left:right].astype(np.float32) / 255.0
    colour = np.asarray(frame.crop((left, first, right, first + cover.shape[0])).convert("RGB")).astype(np.float32) / 255.0
    out = np.zeros((rows, 4), dtype=np.float32)
    out[:cover.shape[0], :3] = (colour * cover[..., None]).mean(axis=1)
    out[:cover.shape[0], 3] = cover.mean(axis=1)
    return out


def body_signals(frames: list[Image.Image]) -> dict[str, Any]:
    """From the solid pixels, what the step count's witness reads besides `strike_signals` (see the module): per
    frame `bands`, `sole`, `edge`, `first_row` and `strips`; and `band_rows` and `median_frame`. `bands` and `strips`
    read the same rows of every frame, of the clip's median body height."""
    edge, sole, heights, read, firsts = [], [], [], [], []
    for f in frames:
        alpha = np.asarray(f.getchannel("A"))
        rows = np.nonzero((alpha >= LOW_ALPHA).any(axis=1))[0]
        if rows.size == 0:
            raise ValueError("a loop frame has no solid body")
        y0, y1 = int(rows[0]), int(rows[-1])
        opaque = np.nonzero((alpha >= OPAQUE).any(axis=1))[0]
        sole.append(float(opaque[-1] if opaque.size else y1))
        edge.append(float(y1 == alpha.shape[0] - 1))
        heights.append(y1 - y0 + 1)
        read.append(_row_widths(alpha))
        firsts.append(y0)
    median = float(np.median(heights)) if frames else 0.0
    rows = band_rows(median)
    middle = (np.asarray(frames[int(np.argmin([abs(h - median) for h in heights]))].getchannel("A")) if frames
              else np.zeros((1, 1), np.uint8))
    deep = math.ceil(DEEP * median)
    strips = [_strip(f, np.asarray(f.getchannel("A")), y0, deep, STRIP * median) for f, y0 in zip(frames, firsts)]
    return {"bands": [_bands_of(y0, width, rows) for y0, width in read], "band_rows": rows, "median_frame": middle,
            "sole": sole, "edge": edge, "first_row": [float(y0) for y0 in firsts],
            "strips": np.stack(strips) if strips else np.zeros((0, 0, 4), dtype=np.float32)}


def swings(sig: dict[str, list[float]]) -> dict[str, float]:
    """How far the stride and the lowest row swing over the frames, of the median body height."""
    height = float(np.median(sig["height"]))
    return {k: float(max(sig[k]) - min(sig[k])) / (1.0 if k == "stride" else height) for k in ("stride", "reach")}


def legs_by(sig: dict[str, list[float]]) -> list[str]:
    """The signals that swing enough to read legs by, the stride first."""
    swing = swings(sig)
    return [k for k, least in (("stride", STRIDE_MIN), ("reach", REACH_MIN)) if swing[k] >= least]


def feet_apart(sig: dict[str, list[float]]) -> float:
    """The share of the frames that show two feet apart (a gap of FEET_APART or more)."""
    return float(np.mean([g >= FEET_APART for g in sig["gap"]])) if sig["gap"] else 0.0


def step_signal(sig: dict[str, list[float]]) -> dict[str, Any]:
    """The signal a gait's steps are counted on, or why there is none: the stride where it swings
    by STRIDE_MIN, else the lowest row where it swings by REACH_MIN — the turn's rule without a
    view — and only where two feet are seen apart."""
    by = legs_by(sig)
    apart = feet_apart(sig)
    record: dict[str, Any] = {"feet_apart": round(apart, 3), **{f"{k}_swing": round(v, 4) for k, v in swings(sig).items()}}
    if not by:
        return {**record, "by": None, "why": f"no legs to read: the foot band swings under {STRIDE_MIN} of the body's height "
                                              f"and the lowest row under {REACH_MIN}"}
    if apart < FEET_APART_SHARE:
        return {**record, "by": None, "why": f"no two feet seen apart ({apart:.0%} of the frames, legs from {FEET_APART_SHARE:.0%})"}
    return {**record, "by": by[0], "values": [float(v) for v in sig[by[0]]]}


def feet_halves(frame: Image.Image) -> tuple[np.ndarray, np.ndarray, int]:
    """(solid mask, the picture's right half of the feet as a column mask, body height): the halves
    split at the middle of the foot band (the lowest FOOT_BAND of the frame's own height)."""
    solid = np.asarray(frame.getchannel("A")) >= LOW_ALPHA
    rows = np.nonzero(solid.any(axis=1))[0]
    y1, height = int(rows[-1]), int(rows[-1] - rows[0] + 1)
    xs = np.nonzero(solid[y1 + 1 - max(1, round(height * FOOT_BAND)) : y1 + 1].any(axis=0))[0]
    return solid, np.arange(solid.shape[1]) >= (xs[0] + xs[-1]) / 2, height


def depth_cue(frame: Image.Image) -> float:
    """How much lower the picture's right foot is drawn than its left, as a fraction of the body's
    height (the lowest solid row of each half)."""
    solid, right, height = feet_halves(frame)
    low = [int(np.nonzero(solid[:, half].any(axis=1))[0].max()) if solid[:, half].any() else 0 for half in (~right, right)]
    return (low[1] - low[0]) / height


def _parts(cols: np.ndarray) -> list[list[int]]:
    """[first, last] column of each run of True in `cols`."""
    edges = np.diff(np.pad(cols.astype(np.int8), 1))
    return [[int(a), int(b) - 1] for a, b in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0])]


def feet_order(frame: Image.Image) -> dict[str, Any]:
    """The order a frame draws its two feet in (`local_cycle.order_word` reads it). `ground`: what stands on its lowest
    row, the [first, last] column of each run of its solid pixels CONTACT of the height (at least a row) up from it;
    `height`, the frame's own body height; `apart`, whether two feet are seen apart: the foot band (the lowest
    FOOT_BAND of the height) parted at its widest empty run, that run FEET_APART of the height or wider, the picture's
    left foot left of it and its right foot right of it. Where they are, `depth`, how many rows lower the right foot's
    lowest solid row is drawn than the left's (from the front, the back or a diagonal the foot nearer the viewer is
    drawn lower); `left` and `right`, the first and the last column of each foot's contact (its solid pixels CONTACT of
    the height up from its own lowest row); and `sway`, how far right of the run's middle the mean column of the upper
    body's solid pixels lies (the first solid row down to the middle of the body)."""
    solid = np.asarray(frame.getchannel("A")) >= LOW_ALPHA
    rows = np.nonzero(solid.any(axis=1))[0]
    if rows.size == 0:
        raise ValueError("a loop frame has no solid body")
    y0, y1 = int(rows[0]), int(rows[-1])
    height = y1 - y0 + 1
    contact = max(1, round(CONTACT * height))
    read: dict[str, Any] = {"ground": _parts(solid[y1 + 1 - contact : y1 + 1].any(axis=0)), "height": height, "apart": False}
    gap = _gap_at(solid[y1 + 1 - max(1, round(height * FOOT_BAND)) : y1 + 1].any(axis=0))
    if gap is None or gap[1] - gap[0] + 1 < FEET_APART * height:
        return read
    feet = {}
    for side, cols in (("left", slice(0, gap[0])), ("right", slice(gap[1] + 1, solid.shape[1]))):
        part = solid[:, cols]
        low = int(np.nonzero(part.any(axis=1))[0][-1])
        xs = np.nonzero(part[low + 1 - contact : low + 1].any(axis=0))[0] + cols.start
        feet[side] = (low, [int(xs[0]), int(xs[-1])])
    upper = np.nonzero(solid[y0 : y0 + height // 2 + 1])[1]
    return {**read, "apart": True, "depth": feet["right"][0] - feet["left"][0], "left": feet["left"][1],
            "right": feet["right"][1], "sway": float(upper.mean() - (gap[0] + gap[1]) / 2)}


def shade_cue(frame: Image.Image, facing: str) -> float:
    """How much lighter the front foot is drawn than the back one (mean luma 0..1 of the lowest
    SHADE_BAND of the body, front half against back half; the front is the side faced)."""
    solid, right, height = feet_halves(frame)
    rows = np.nonzero(solid.any(axis=1))[0]
    band = np.zeros_like(solid)
    band[rows[-1] + 1 - max(1, round(height * SHADE_BAND)) : rows[-1] + 1] = True
    rgb = np.asarray(frame.convert("RGB"), dtype=np.float32) / 255.0
    luma = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    front = right if facing == "right" else ~right
    parts = [luma[solid & band & cols[None, :]] for cols in (front, ~front)]
    return float(parts[0].mean() - parts[1].mean()) if all(p.size for p in parts) else 0.0
