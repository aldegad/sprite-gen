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
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.util.resample import transform_cell

# Height change over the clip, from the fitted trend, at which the frames are scaled back.
# Nine in ten walk clips stay under it (median 0.5 %): their change is a head bob and hair.
SCALE_DRIFT_MIN = 0.03
# Height change over the clip at which a walk or run is held at its first frame's size before the
# first cycle search (`video-loop --size-hold auto`). Below it the fitted trend is a head bob and
# hair; above it a clip filmed from its first frame only grows or shrinks enough that the loop's
# last frame is a different size from its first, and the loop pops at the wrap.
SIZE_HOLD_MIN = 0.01
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
    t = np.arange(len(values), dtype=np.float64)
    known = ~np.isnan(values)
    if known.sum() < 2:
        return np.full(len(values), np.nanmean(values) if known.any() else np.nan)
    return np.polyval(np.polyfit(t[known], values[known], 1), t)


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
