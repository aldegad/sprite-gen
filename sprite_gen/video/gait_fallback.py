# SPDX-License-Identifier: Apache-2.0
"""A second look for a walk or run cycle when the automatic search finds none.

Two things a front or back gait does that the first search cannot see past:

* It walks toward the camera (or away from it) although it was asked to stay in place, so the
  body grows or shrinks through the clip and the same pose never matches itself in size. A
  linear trend in the subject's height measures that; the frames are scaled back to the first
  frame's size about the subject's foot point, which stays where it was filmed. Horizontal and
  vertical drift are left to the motion analysis, which already measures them.
* It walks slowly. A calm walk in long clothing can take longer than half the clip per cycle,
  the most the first search confirms. The second search allows a cycle up to
  `LONG_CYCLE_FRACTION` of the clip: it is still seen once whole and repeating for the rest.

Only a failed search reaches the long window. The size is also held before the first search
(`video-loop --size-hold auto`, the default for `--anchor motion-auto`): a clip that changes size
by `SIZE_HOLD_MIN` or more is scaled back first, so the loop is cut on frames of one size and its
last frame leads into its first. What was done is recorded in the report (`size_hold`,
`gait_fallback`).

The hold reads the change one cycle on (`cycle_drift`), not off a line fitted through the
clip: a clip that starts from a standing pose and settles into the walk over its first steps
(knees bending, the body leaning in) is shorter from then on, and a line through every frame
reads that one change of pose as a body that shrinks all the way. The same pose a cycle later
is the same size unless the body really grows or shrinks, so the hold compares each frame's
height with the frames whole pose-matched lags later and takes the middle of those changes; the
few frames of the first pose do not move a median of the rest. The second search scales back by
the same reading, so both hold one size model.

A clip can also open on a **lead-in**: the video model reframes a small subject in its first
frames, so the body grows (or shrinks) by a third or more in under half a second and then walks
at its new size. The one-cycle-on model, a median over the whole clip, holds the walk's size;
the frames off it by `LEAD_IN_MIN` or more from the first one on are the lead-in (`lead_in`). A
walk's own frames stray from it by a head bob and hair, a few percent; a reframing starts tens
of percent off it.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.util import lsq
from sprite_gen.util.resample import transform_cell

# Height change over the clip, read one cycle on (`cycle_drift`), at which the second search scales
# the frames back. Nine in ten walk clips stay under it (median 0.5 %): their change is a head bob and hair.
SCALE_DRIFT_MIN = 0.03
# Height change over the clip at which a walk or run is held at its first frame's size before the
# first cycle search (`video-loop --size-hold auto`). Below it the fitted trend is a head bob and
# hair; above it a clip filmed from its first frame only grows or shrinks enough that the loop's
# last frame is a different size from its first, and the loop pops at the wrap.
SIZE_HOLD_MIN = 0.01
# A frame whose height is off the one-cycle-on model by this much is not yet walking at the clip's size:
# the first frames off it by this much are a lead-in (`lead_in`).
LEAD_IN_MIN = 0.1
# The longest cycle the second search accepts, as a share of the clip and in seconds.
LONG_CYCLE_FRACTION = 0.6
LONG_CYCLE_SECONDS = 2.0
# Opaque enough to be the subject (the keyed frames' soft edge is below it).
ALPHA_SUBJECT = 128


def subject_boxes(frames: list[Image.Image]) -> np.ndarray:
    """Per frame: left, top, right, bottom of the opaque subject. A frame with none is NaN."""
    boxes = []
    for frame in frames:
        alpha = np.asarray(frame.getchannel("A")) >= ALPHA_SUBJECT
        ys, xs = np.nonzero(alpha)
        boxes.append((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) if len(xs) else (np.nan,) * 4)
    return np.asarray(boxes, dtype=np.float64)


def _trend(values: np.ndarray) -> np.ndarray:
    """The least-squares line through the known values, read at every frame. It is the exact line
    (`sprite_gen.util.lsq`), not np.polyfit's, whose last bit differs from machine to machine: the
    foot point and height it gives scale the frames back and size the room they need, so that bit
    could tip a resampled pixel, and feet standing on the frame's edge read a hair past it — on
    which side decided whether the frames got 2 px of room."""
    t = np.arange(len(values), dtype=np.float64)
    known = ~np.isnan(values)
    if known.sum() < 2:
        return np.full(len(values), np.nanmean(values) if known.any() else np.nan)
    slope, intercept = lsq.line(t[known], values[known])
    return slope*t+intercept


def scale_drift(frames: list[Image.Image]) -> dict:
    """The fitted height of the subject over the clip and where its feet stand."""
    boxes = subject_boxes(frames)
    height = _trend(boxes[:, 3] - boxes[:, 1])
    foot_x = _trend((boxes[:, 0] + boxes[:, 2]) / 2)
    foot_y = _trend(boxes[:, 3])
    first, last = float(height[0]), float(height[-1])
    drift = last / first - 1 if first > 0 else 0.0
    return {"height_first_px": round(first, 2), "height_last_px": round(last, 2), "drift": round(drift, 4),
            "height": height, "foot_x": foot_x, "foot_y": foot_y}


POSE_EDGE = 48  # px: the pose thumbnail's longer side, the body cut out about its feet


def _poses(frames: list[Image.Image], boxes: np.ndarray) -> np.ndarray:
    """Per frame: the body's thumbnail, cut out about its own feet (the bottom middle of its box),
    so a body that walks across the frame or bobs still matches itself a cycle later."""
    known = ~np.isnan(boxes[:, 0])
    width = int(np.ceil(np.max(boxes[known, 2] - boxes[known, 0]))) + 2
    height = int(np.ceil(np.max(boxes[known, 3] - boxes[known, 1]))) + 2
    scale = POSE_EDGE / max(width, height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    out = []
    for frame, (x0, _, x1, y1) in zip(frames, boxes):
        if np.isnan(x0):
            out.append(np.full(size[0] * size[1] * 4, np.nan, np.float32))
            continue
        left, top = round((x0 + x1) / 2 - width / 2), round(y1 - height)
        cut = frame.crop((left, top, left + width, top + height)).resize(size, Image.Resampling.BOX)
        a = np.asarray(cut, np.float32) / 255
        out.append(np.concatenate([a[..., :3] * a[..., 3:4], a[..., 3:4]], axis=-1).reshape(-1))
    return np.stack(out)


def coverage_heights(frames: list[Image.Image]) -> np.ndarray:
    """Per frame: the subject's height to a fraction of a pixel, the rows' coverage summed (each
    row counts as much as its most opaque pixel) from three rows over the solid crown to three
    under the solid feet. A soft edge row adds its part, so a body 0.4 px taller reads 0.4 px
    taller rather than 0 or 1. A frame with no solid row is NaN."""
    out = []
    for frame in frames:
        rows = np.asarray(frame.getchannel("A"), np.float64).max(axis=1) / 255
        solid = np.nonzero(rows >= ALPHA_SUBJECT / 255)[0]
        out.append(float(rows[max(0, solid[0] - 3): solid[-1] + 4].sum()) if len(solid) else np.nan)
    return np.asarray(out)


def cycle_drift(frames: list[Image.Image], *, min_lag: int, max_lag: int) -> dict:
    """The subject's change of size one cycle on, as `scale_drift` reports it.

    The lag is the one in [min_lag, max_lag] (no more than half the clip, so every frame of the
    first half has a partner) at which the pose matches itself best: the cycle, or one step of a
    gait whose two halves look alike (the same size either way). Each frame's height
    (`coverage_heights`) is compared with the frame one, two, … lags later, and the median of
    those changes per frame is the rate, so a pose seen only at the start (a standing first frame)
    is outvoted by the walk, and the longer spans keep a pixel's rounding from reading as a trend.
    `drift` is that rate carried over the clip, on the same scale as the fitted line
    (`drift_fitted_line`), and the heights `undo_scale` holds to grow by the same factor every
    frame. No lag in range (a clip too short to show a cycle twice): `lag` is None and the drift 0."""
    boxes = subject_boxes(frames)
    n = len(frames)
    heights = coverage_heights(frames)
    fitted = scale_drift(frames)
    hi = min(max_lag, n // 2)
    base = {"method": "one-cycle-on", "drift_fitted_line": fitted["drift"],
            "foot_x": fitted["foot_x"], "foot_y": fitted["foot_y"]}
    if hi < max(1, min_lag) or np.isnan(heights).all():
        return {**base, "lag": None, "pairs": 0, "per_lag": 1.0, "pose_match": None, "drift": 0.0,
                "height_first_px": fitted["height_first_px"], "height_last_px": fitted["height_first_px"],
                "height": np.full(n, fitted["height"][0])}
    poses = _poses(frames, boxes)
    profile = {lag: float(np.nanmean(np.abs(poses[lag:] - poses[:-lag]))) for lag in range(max(1, min_lag), hi + 1)}
    lag = min(profile, key=lambda k: (profile[k], k))
    rates = np.concatenate([np.log(heights[span:] / heights[:-span]) / span for span in range(lag, n, lag)])
    rates = rates[np.isfinite(rates)]
    rate = float(np.exp(np.median(rates)))
    model = rate ** np.arange(n, dtype=np.float64)
    level = float(np.nanmedian(heights / model))
    height = level * model
    # How well the pose matches itself at that lag, against the mean over the lags tried (0 is
    # exact); None when no lag differs from another (a body that never moves).
    mean = float(np.mean(list(profile.values())))
    return {**base, "lag": lag, "pairs": int(len(rates)), "per_lag": round(rate ** lag, 4),
            "pose_match": round(profile[lag] / mean, 3) if mean > 0 else None,
            "drift": round(float(rate ** (n - 1) - 1), 4), "height_first_px": round(float(height[0]), 2),
            "height_last_px": round(float(height[-1]), 2), "height": height}


def lead_in(frames: list[Image.Image], *, min_lag: int, max_lag: int) -> dict:
    """The frames the clip opens on before its subject walks at the clip's size: from the first, every
    frame whose height (`coverage_heights`) is off the one-cycle-on model (`cycle_drift`) by
    LEAD_IN_MIN or more. `frames` is how many (0: none), `height_change` the model's height where the
    lead-in ends over the first frame's, less 1 (+0.5: the body grew by half), `first_off` how far the
    first frame is off the model. A frame with no subject ends it. A clip too short to show a cycle
    twice has no model to read it on: no lead-in, and `why` says so."""
    heights = coverage_heights(frames)
    measured = cycle_drift(frames, min_lag=min_lag, max_lag=max_lag)
    if measured["lag"] is None:
        return {"frames": 0, "height_change": 0.0, "first_off": None, "min": LEAD_IN_MIN, "method": "one-cycle-on",
                "why": "no lag in range: the clip is too short to read its size one cycle on"}
    model = measured["height"]
    off = heights / model - 1
    count = 0
    while count < len(frames) - 1 and np.isfinite(off[count]) and abs(off[count]) >= LEAD_IN_MIN:
        count += 1
    change = float(model[count] / heights[0] - 1) if count and heights[0] > 0 else 0.0
    return {"frames": count, "height_change": round(change, 4),
            "first_off": round(float(off[0]), 4) if np.isfinite(off[0]) else None,
            "min": LEAD_IN_MIN, "method": "one-cycle-on"}


def undo_padding(frames: list[Image.Image], measured: dict) -> tuple[int, int, int, int]:
    """Left, top, right, bottom room every frame needs so `undo_scale` cuts nothing.

    A clip that shrinks is scaled up about its feet, and what was near the top of the frame (the
    crown) or its sides is carried past the edge. Each frame's subject box is mapped the way
    `undo_scale` maps it; the room is how far the furthest one reaches past each edge. All
    frames get the same room, so they stay one size. Nothing reaches out: (0, 0, 0, 0)."""
    boxes = subject_boxes(frames)
    height, foot_x, foot_y = measured["height"], measured["foot_x"], measured["foot_y"]
    width, rows = frames[0].size
    reach = [0.0, 0.0, 0.0, 0.0]
    for k, (x0, y0, x1, y1) in enumerate(boxes):
        if np.isnan(x0):
            continue
        s = float(height[0] / height[k])
        ax, ay = float(foot_x[k]), float(foot_y[k])
        reach[0] = max(reach[0], -(ax + (x0 - ax) * s))
        reach[1] = max(reach[1], -(ay + (y0 - ay) * s))
        reach[2] = max(reach[2], ax + (x1 - ax) * s - width)
        reach[3] = max(reach[3], ay + (y1 - ay) * s - rows)
    # A pixel's bilinear footprint reaches one past its mapped edge.
    return tuple(int(np.ceil(r)) + 1 if r > 0 else 0 for r in reach)  # type: ignore[return-value]


def undo_scale(frames: list[Image.Image], measured: dict, pad: tuple[int, int, int, int] | None = None) -> list[Image.Image]:
    """Each frame scaled to the first frame's fitted height about its fitted foot point.

    Coverage and colour are resampled apart (`transform_cell`), so the soft edge neither picks
    up the colour under fully transparent pixels nor rings into a colour the frame did not have:
    BICUBIC over the keyed frame drew a light rim and a key tint around the outline.

    The frames are widened by `pad` (left, top, right, bottom; `undo_padding` when not given), so
    a part scaled up past the frame's edge is kept. A crown carried above the frame was cut off
    in every frame it reached. With no room needed the frames keep their size and bytes.
    """
    height, foot_x, foot_y = measured["height"], measured["foot_x"], measured["foot_y"]
    left, top, right, bottom = undo_padding(frames, measured) if pad is None else pad
    out = []
    for k, frame in enumerate(frames):
        s = float(height[0] / height[k])
        ax, ay = float(foot_x[k]), float(foot_y[k])
        # Output (x, y) reads input anchor + (x - room - anchor) / s.
        inverse = (1 / s, 0.0, ax * (1 - 1 / s) - left / s, 0.0, 1 / s, ay * (1 - 1 / s) - top / s)
        size = (frame.width + left + right, frame.height + top + bottom)
        out.append(transform_cell(frame, size, inverse))
    return out


def long_window(lo: int, n: int, fps: float) -> int:
    """The second search's ceiling in frames."""
    return max(lo + 2, min(round(LONG_CYCLE_SECONDS * fps), int(n * LONG_CYCLE_FRACTION)))


def write_frames(frames: list[Image.Image], names: list[str], work_dir: Path) -> list[Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    for old in work_dir.glob("*.png"):
        old.unlink()
    paths = []
    for name, frame in zip(names, frames):
        path = work_dir / name
        frame.save(path)
        paths.append(path)
    return paths
