# SPDX-License-Identifier: Apache-2.0
"""Jump frames in a walk loop, found by how much the body's coverage changes, replaced by RIFE.

A video model redraws thin hair a little differently every frame, and now and then a ponytail
lands somewhere it was not one frame earlier: one step of the loop changes far more than the
loop's usual step. That step is a jump; the frame after it is replaced by the frame RIFE makes
half-way between its two neighbours, and every other frame stays the video's own
(docs/loop-repair.md section 2). Re-making every frame instead (an offset of half a frame) also
softens the frames that were fine, and was judged "not corrected" (2026-10-03).

Measured on the loop as it plays — cyclic, the last frame followed by the first — and inside
the union box of the body over the whole loop, the way the strip cells are cut, so a frame's
score does not depend on where the canvas put the body.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video.rife import Interpolate

# A step this many times the loop's median step (whole body, or the hair behind it) is a jump.
JUMP_RATIO = 1.4
# Frames replaced at most per loop. A loop that needs more is jolting everywhere, not jumping
# once, and a few made frames do not fix it (the jolt index, section 3, says so instead).
MAX_REPAIRS = 3
# The hair behind a right-facing body, below the head, as fractions of the union box: where a
# ponytail cut at the wrong moment jumps. A left-facing body has it on the other side.
HAIR_BOX_RIGHT = (0.0, 0.30, 0.45, 0.80)
ALPHA_SOLID = 8  # alpha at or above this is body, as everywhere in video-loop
BOX_MARGIN = 8  # px around the union box, the strip cells' own side margin


def union_box(frames: list[Image.Image]) -> tuple[int, int, int, int]:
    """The body's box over the whole loop, widened by the strip cells' margin (the bottom stays on the feet)."""
    boxes = [f.getchannel("A").point(lambda v: 255 if v >= ALPHA_SOLID else 0).getbbox() for f in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        raise ValueError("every loop frame is fully transparent")
    return (min(b[0] for b in boxes) - BOX_MARGIN, max(0, min(b[1] for b in boxes) - BOX_MARGIN),
            max(b[2] for b in boxes) + BOX_MARGIN, max(b[3] for b in boxes))


def hair_box(facing: str) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = HAIR_BOX_RIGHT
    return (x0, y0, x1, y1) if facing != "left" else (1 - x1, y0, 1 - x0, y1)


def coverage(frames: list[Image.Image], box: tuple[int, int, int, int]) -> list[np.ndarray]:
    """Alpha 0..1 of every frame inside `box` (outside the frame counts as empty)."""
    return [np.asarray(f.crop(box), dtype=np.float32)[..., 3] / 255.0 for f in frames]


def steps(alphas: list[np.ndarray], frac: tuple[float, float, float, float] | None = None) -> np.ndarray:
    """Mean coverage change from frame k to k+1, the last to the first at the end; inside `frac`
    (fractions of the box) when given."""
    if frac is not None:
        h, w = alphas[0].shape
        alphas = [a[int(h * frac[1]):int(h * frac[3]), int(w * frac[0]):int(w * frac[2])] for a in alphas]
    n = len(alphas)
    return np.array([float(np.abs(alphas[(k + 1) % n] - alphas[k]).mean()) for k in range(n)])


def jump_scores(frames: list[Image.Image], *, facing: str = "right", box: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
    """Per step: the whole body's change and the hair's, each over its own median; the score is the larger."""
    al = coverage(frames, box or union_box(frames))
    whole, hair = steps(al), steps(al, hair_box(facing))
    mw, mh = float(np.median(whole)), float(np.median(hair))
    w = whole / mw if mw > 0 else np.zeros_like(whole)
    h = hair / mh if mh > 0 else np.zeros_like(hair)
    return {"whole": w, "hair": h, "score": np.maximum(w, h)}


def repair_jumps(frames: list[Image.Image], interpolate: Interpolate | None, *, facing: str = "right",
                 ratio: float = JUMP_RATIO, max_frames: int = MAX_REPAIRS) -> tuple[list[Image.Image], dict[str, Any]]:
    """Replace the frame that breaks each jump with RIFE's frame between its neighbours, worst jump first.

    At most `max_frames`, and never next to a frame already made: two made frames side by side
    are made from each other and melt the legs. `interpolate` may be None while no jump is
    found; it is asked for only when a frame is to be made (a ValueError says so otherwise).
    Scores are re-read after each replacement, on the union box of the original loop."""
    out = list(frames)
    n = len(out)
    box = union_box(frames)
    first = jump_scores(out, facing=facing, box=box)
    record: dict[str, Any] = {
        "ratio": ratio, "max_frames": max_frames, "facing": facing, "hair_box": list(hair_box(facing)),
        "score_max_before": round(float(first["score"].max()), 4), "replaced": [], "rounds": [],
    }
    if n < 4:
        record.update(stopped="loop shorter than 4 frames", score_max_after=record["score_max_before"])
        return out, record
    replaced: list[int] = []
    stopped = f"{max_frames} frames replaced"
    scores = first
    for _ in range(max_frames):
        k = int(np.argmax(scores["score"]))
        score = float(scores["score"][k])
        if score < ratio:
            stopped = f"no step at or above {ratio}x the median"
            break
        # A jump is a step; the frame to remake is the one that broke it. A cut (the ponytail
        # lands somewhere new and stays) breaks only step k, and the frame after it is remade
        # half way. A single stray frame breaks the step into it and the step out of it: when
        # the step before k is also a jump and larger than the step after k+1, frame k is the
        # stray one, and remaking the frame after it would leave it standing.
        before, after = float(scores["score"][(k - 1) % n]), float(scores["score"][(k + 1) % n])
        j = k if before >= ratio and before > after else (k + 1) % n
        if j in replaced or (j - 1) % n in replaced or (j + 1) % n in replaced:
            stopped = f"the worst jump (frame {j}) sits next to a frame already made"
            break
        if interpolate is None:
            raise ValueError(f"frame {j} follows a jump ({score:.2f}x the median step) and no interpolator is available")
        out[j] = interpolate(out[(j - 1) % n], out[(j + 1) % n], 0.5)
        replaced.append(j)
        record["rounds"].append({"step": [k, (k + 1) % n], "score": round(score, 4), "whole": round(float(scores["whole"][k]), 4),
                                 "hair": round(float(scores["hair"][k]), 4), "replaced": j})
        scores = jump_scores(out, facing=facing, box=box)
    record["replaced"] = replaced
    record["stopped"] = stopped
    record["score_max_after"] = round(float(scores["score"].max()), 4)
    return out, record


# --- the jolt index (docs/loop-repair.md section 3) ------------------------------------------

# Reference bounds, at the edge of what was kept on the 2026-10-03 takes (section 3): nothing kept
# exceeds them, and the back-diagonal take refused because "it jumps" does. A loop beyond them is
# warned about; it fails only when a caller passes a bound (--jolt-max / --head-step-max), because
# eleven judged takes are too few to refilm on, and at these values 16 of 30 Lite takes would be.
JOLT_REFERENCE = 0.42  # alpha jolt index; the kept takes reached 0.414 after repair
HEAD_STEP_REFERENCE = 0.75  # head's sideways move in one frame, % of body height; kept <= 0.68, refused 0.82
HEAD_BAND = 0.20  # the head is the top fifth of the body's height over the loop
HEAD_MEDIAN_FLOOR = 0.05  # % of body height: a median step below this is the tracker's rounding


def jolt_index(step_values: np.ndarray) -> float:
    """How far each step strays from the mean of its two neighbours, on average, over the median
    step. 0 for a loop whose steps change evenly; alternating big and small steps read high."""
    n = len(step_values)
    med = float(np.median(step_values))
    if n < 3 or med <= 0:
        return 0.0
    return float(np.mean([abs(step_values[k] - (step_values[k - 1] + step_values[(k + 1) % n]) / 2) for k in range(n)]) / med)


def _track_stats(values: np.ndarray) -> dict[str, float]:
    n = len(values)
    d = np.abs(np.array([values[(k + 1) % n] - values[k] for k in range(n)]))
    med = float(np.median(d))
    return {"step_max_pct": round(float(d.max()), 3), "step_median_pct": round(med, 3),
            "max_over_median": round(float(d.max()) / max(med, HEAD_MEDIAN_FLOOR), 3),
            "range_pct": round(float(np.ptp(values)), 3), "worst_into_frame": int((np.argmax(d) + 1) % n)}


def head_track(alphas: list[np.ndarray]) -> dict[str, Any]:
    """The head's place frame by frame, in % of the body's height over the loop: x is the coverage
    centroid of the top fifth (HEAD_BAND), y the body's top line. A take whose head pops sideways
    reads here while its coverage change stays ordinary (2026-10-03, Lite back diagonal)."""
    rows = np.where(np.max(np.stack(alphas), axis=0).max(axis=1) > 0.1)[0]
    if rows.size == 0:
        raise ValueError("every loop frame is fully transparent")
    top, bottom = int(rows.min()), int(rows.max())
    height = max(1, bottom - top)
    band = slice(top, top + max(1, int(height * HEAD_BAND)))
    xs, ys = [], []
    for a in alphas:
        b = a[band]
        mass = float(b.sum())
        xs.append(float((b.sum(axis=0) * np.arange(b.shape[1])).sum() / mass) if mass > 0 else float("nan"))
        filled = np.where(a.sum(axis=1) > 0.5)[0]
        ys.append(float(filled.min()) if filled.size else float("nan"))
    x = np.array(xs) / height * 100
    y = np.array(ys) / height * 100
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError("a loop frame has no head to track")
    return {"body_height_px": height, "x": _track_stats(x), "y": _track_stats(y)}


def measure_jolt(frames: list[Image.Image], *, facing: str = "right") -> dict[str, Any]:
    """The jolt index of a loop as it plays, and the head's frame-to-frame moves."""
    al = coverage(frames, union_box(frames))
    whole, hair = steps(al), steps(al, hair_box(facing))
    med = float(np.median(whole))
    try:
        head: dict[str, Any] = head_track(al)
    except ValueError as exc:
        head = {"skipped": str(exc)}  # recorded, and the head gate says it could not read it
    return {
        "index": round(jolt_index(whole), 4),
        "hair_index": round(jolt_index(hair), 4),
        "step_max_over_median": round(float(whole.max()) / med, 4) if med > 0 else None,
        "head": head,
    }


def jolt_verdict(measured: dict[str, Any], *, jolt_max: float | None = JOLT_REFERENCE,
                 head_step_max: float | None = HEAD_STEP_REFERENCE) -> list[str]:
    """What exceeds the bounds, in words; empty when the loop stays inside. A bound of None is not checked."""
    over = []
    if jolt_max is not None and measured["index"] > jolt_max:
        over.append(f"jolt index {measured['index']:.3f} exceeds {jolt_max}")
    if head_step_max is None:
        return over
    if "skipped" in measured["head"]:
        over.append(f"the head could not be tracked ({measured['head']['skipped']})")
        return over
    head = measured["head"]["x"]
    if head["step_max_pct"] > head_step_max:
        over.append(f"the head moves sideways {head['step_max_pct']:.2f} % of the body height in one frame "
                    f"(into frame {head['worst_into_frame']}), over {head_step_max} %")
    return over
