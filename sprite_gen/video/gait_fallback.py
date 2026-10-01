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

Only a failed search reaches this, so a clip that loops today is cut exactly as before. What
was done is recorded in the report (`gait_fallback`).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from sprite_gen._deps import np

# Height change over the clip, from the fitted trend, at which the frames are scaled back.
# Nine in ten walk clips stay under it (median 0.5 %): their change is a head bob and hair.
SCALE_DRIFT_MIN = 0.03
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


def undo_scale(frames: list[Image.Image], measured: dict) -> list[Image.Image]:
    """Each frame scaled to the first frame's fitted height about its fitted foot point.

    Premultiplied while it is resampled, so the soft edge does not pick up the colour that
    sits under fully transparent pixels.
    """
    height, foot_x, foot_y = measured["height"], measured["foot_x"], measured["foot_y"]
    out = []
    for k, frame in enumerate(frames):
        s = float(height[0] / height[k])
        ax, ay = float(foot_x[k]), float(foot_y[k])
        # Output (x, y) reads input anchor + (x - anchor) / s.
        inverse = (1 / s, 0.0, ax * (1 - 1 / s), 0.0, 1 / s, ay * (1 - 1 / s))
        resampled = frame.convert("RGBa").transform(frame.size, Image.Transform.AFFINE, inverse,
                                                    resample=Image.Resampling.BICUBIC)
        out.append(resampled.convert("RGBA"))
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
