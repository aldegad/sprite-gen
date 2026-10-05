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
JOLT_REFERENCE = 0.43  # alpha jolt index; the kept takes reached 0.414 on strip cells, 0.4225 on the cut frames video-loop reads
HEAD_STEP_REFERENCE = 0.75  # head's sideways move in one frame, % of body height; kept <= 0.68, refused 0.82
HEAD_BAND = 0.20  # the head is the top fifth of the body's height over the loop
HEAD_MEDIAN_FLOOR = 0.05  # % of body height: a median step below this is the tracker's rounding
# The top band's step into the loop's first frame, over the loop's median step, beyond which the
# wrap pops (docs/loop-repair.md section 3, "The seam pop"). It sits between walks holding a part
# that sways on its own beat and walks without one, on the cut as it plays and on the source frames
# the cut is chosen from. Above it the cut is chosen again (`--anchor motion-auto`), and a pop that
# stays is a warning line.
SEAM_POP_REFERENCE = 10.0
# The held side's seam (docs/loop-repair.md section 3, "The held side"): a held part that swings on
# its own beat misses below its top too — under the hand, where the top band does not reach. Read
# on the silhouette's outer edge on the side the top band sits on, row by row: how far the cut's
# wrap is from the clip's own way on there, in place and in speed, over that row's median step in
# the loop; the HELD_EDGE_QUANTILE-th percentile of the rows. Under HELD_EDGE_REFERENCE the held
# side closes. On a walk without a held part the edge is the hair or an arm, and the same reading
# runs as high as on a staff that does not close, so it is read only on a cut whose top band pops
# (`--anchor motion-auto` chooses again there), where the top band has found the held part and its
# side. The bound sits between cuts that close a held staff and cuts where it swings back at the wrap.
HELD_EDGE_REFERENCE = 1.2
HELD_EDGE_QUANTILE = 80
HELD_EDGE_FLOOR = 0.005  # of the body height, a pixel at least: a row's median step under it is the edge's rounding
HELD_EDGE_ALPHA = 128  # alpha at or above this is the edge (0..255)


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
    x, y, height = _head_series(alphas)
    return {"body_height_px": height, "x": _track_stats(x), "y": _track_stats(y)}


def _head_series(alphas: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, int]:
    """`head_track`'s two places per frame (% of the body height) and the body height in px."""
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
    return x, y, height


def seam_pop(alphas: list[np.ndarray]) -> dict[str, Any]:
    """How far the top of the silhouette jumps into the loop's first frame (docs/loop-repair.md
    section 3, "The seam pop").

    The top band `head_track` reads is the head, or the tip of whatever the body holds higher than its
    head — a staff, a flag, a raised spear. A thin part like that can sway on its own beat, slower than
    the steps: a cut one step long then ends with it somewhere else, and it jumps at the wrap while
    the body closes. The seam ratio is an area measure and hardly sees a thin part move: a tip that
    jumps many times its usual step changes few pixels.

    Per axis, the wrap's step (last frame -> first) over the loop's median step, a pixel at least: a
    step under a pixel is the tracker's rounding. It pops when the wrap is that axis' largest step
    and exceeds SEAM_POP_REFERENCE times the median; `pop` is the larger of the two, 0 when neither
    axis has its largest step at the wrap. A loop whose top jitters more inside than at the wrap
    (hair redrawn every frame) has its wrap ordinary for it."""
    x, y, height = _head_series(alphas)
    floor = max(HEAD_MEDIAN_FLOOR, 100.0 / height)
    record: dict[str, Any] = {"body_height_px": height, "reference": SEAM_POP_REFERENCE}
    pop = 0.0
    for axis, values in (("x", x), ("y", y)):
        n = len(values)
        d = np.abs(np.array([values[(k + 1) % n] - values[k] for k in range(n)]))
        over = float(d[-1]) / max(float(np.median(d)), floor)
        worst = bool(d[-1] >= d.max())
        record[axis] = {"wrap_pct": round(float(d[-1]), 3), "step_median_pct": round(float(np.median(d)), 3),
                        "wrap_over_median": round(over, 3), "wrap_is_largest": worst}
        if worst:
            pop = max(pop, over)
    record["pop"] = round(pop, 3)
    record["pops"] = pop > SEAM_POP_REFERENCE
    return record


def held_side(masks: list[np.ndarray]) -> str:
    """The side of the body the top band (HEAD_BAND) sits on, "left" or "right": where a part held
    higher than the head is held. `masks` are a cut's frames, True where the body is."""
    stack = np.stack(masks)
    rows = np.where(stack.any(axis=(0, 2)))[0]
    if rows.size == 0:
        raise ValueError("every loop frame is fully transparent")
    top, bottom = int(rows.min()), int(rows.max())
    band = stack[:, top:top + max(1, int(max(1, bottom - top) * HEAD_BAND))]
    cols = np.arange(stack.shape[2])
    body, tip = stack.sum(axis=(0, 1)), band.sum(axis=(0, 1))
    return "right" if float((tip * cols).sum()) / float(tip.sum()) >= float((body * cols).sum()) / float(body.sum()) else "left"


def side_edges(masks: list[np.ndarray], side: str) -> np.ndarray:
    """(frames, rows): each row's outermost body column on `side`, NaN where the row is empty."""
    stack = np.stack(masks)
    filled = stack.any(axis=2)
    if side == "right":
        edge = stack.shape[2] - 1 - stack[:, :, ::-1].argmax(axis=2)
    else:
        edge = stack.argmax(axis=2)
    out = edge.astype(np.float64)
    out[~filled] = np.nan
    return out


def held_edge(edges: np.ndarray, start: int, length: int) -> dict[str, Any]:
    """The held side's seam of a cut (start, length) of a clip whose side edges are `edges`
    (`side_edges`), section 3, "The held side".

    A loop closes where its last frame is the frame before its first and its first the frame after its
    last: per row, the cut's first frame against the clip's frame after its last, its last against the
    clip's frame before its first, and the step into and out of each against the clip's own (a part
    that comes back to its place moving the other way swings back at the wrap). Their mean over the
    row's median step in the loop, a floor of HELD_EDGE_FLOOR; the HELD_EDGE_QUANTILE-th percentile
    over the rows is `seam`, and the held side `closes` under HELD_EDGE_REFERENCE. Raises ValueError
    for a cut with fewer than two clip frames on either side, or no row to read."""
    n = len(edges)
    s, L = start, length
    if s < 2 or s + L + 1 >= n:
        raise ValueError("the clip has no two frames either side of the cut to read its wrap against")
    loop = edges[s:s + L]
    rows = np.where(np.isfinite(loop).any(axis=0))[0]
    if rows.size == 0:
        raise ValueError("every loop frame is fully transparent")
    steps = np.abs(np.diff(np.vstack([loop, loop[:1]]), axis=0))
    terms = np.stack([np.abs(edges[s] - edges[s + L]),
                      np.abs((edges[s + 1] - edges[s]) - (edges[s + L + 1] - edges[s + L])),
                      np.abs(edges[s + L - 1] - edges[s - 1]),
                      np.abs((edges[s + L - 1] - edges[s + L - 2]) - (edges[s - 1] - edges[s - 2]))])
    with np.errstate(invalid="ignore"):
        counted = np.isfinite(steps).sum(axis=0)
        median = np.where(counted > 0, np.nanmedian(np.where(counted > 0, steps, 0.0), axis=0), np.nan)
        read = np.isfinite(terms).sum(axis=0)
        mean = np.where(read > 0, np.nansum(terms, axis=0) / np.maximum(read, 1), np.nan)
    floor = max(1.0, HELD_EDGE_FLOOR * float(rows.max() - rows.min()))
    ratio = mean / np.maximum(median, floor)
    ratio = ratio[np.isfinite(ratio)]
    if ratio.size == 0:
        raise ValueError("no row has the held side's edge in the frames its wrap is read on")
    seam = round(float(np.percentile(ratio, HELD_EDGE_QUANTILE)), 3)
    return {"seam": seam, "closes": seam < HELD_EDGE_REFERENCE, "rows": int(ratio.size), "reference": HELD_EDGE_REFERENCE}


def held_edge_verdict(shape: dict[str, Any] | None) -> list[str]:
    """The held side of a cut chosen again on its top band (`cycle.seam_pop.held_edge`), in words, when
    the chosen cut does not close it or it could not be read; empty otherwise (and for a cut whose top
    band did not pop, where it is not read)."""
    held = (shape or {}).get("held_edge")
    if not held:
        return []
    if "skipped" in held:
        return [f"the top of the silhouette jumps at the first choice, and the side its held part is on could not be read "
                f"({held['skipped']}): whether the part closes below its top at this cut is not known"]
    chosen = held["chosen"]
    if "skipped" in chosen:
        return [f"the held part's side ({held['side']}) could not be read at this cut ({chosen['skipped']}): whether it "
                "closes below its top is not known"]
    if chosen["closes"]:
        return []
    line = (f"the held part does not close at the wrap on the {held['side']} side of the silhouette, below its top: "
            f"{chosen['seam']:.2f}x its rows' median step (over {held['reference']:g}); ")
    capped = held.get("two_cycle_capped")
    if capped:
        # An explicit --max-len is the caller's ceiling: windows two cycles long past it are not read.
        low, high = capped["lengths_over"]
        return [line + f"no window one cycle long closes it, and windows two cycles long ({low}-{high} frames) are over "
                f"--max-len {capped['max_len']}, so they were not read"]
    return [line + "no window one or two cycles long closes it — it swings on a beat of its own"]


def measure_jolt(frames: list[Image.Image], *, facing: str = "right") -> dict[str, Any]:
    """The jolt index of a loop as it plays, and the head's frame-to-frame moves."""
    al = coverage(frames, union_box(frames))
    whole, hair = steps(al), steps(al, hair_box(facing))
    med = float(np.median(whole))
    try:
        head: dict[str, Any] = head_track(al)
        pop: dict[str, Any] = seam_pop(al)
    except ValueError as exc:
        head = {"skipped": str(exc)}  # recorded, and the head gate says it could not read it
        pop = {"skipped": str(exc)}
    return {
        "index": round(jolt_index(whole), 4),
        "hair_index": round(jolt_index(hair), 4),
        "step_max_over_median": round(float(whole.max()) / med, 4) if med > 0 else None,
        "head": head,
        "seam_pop": pop,
    }


def seam_pop_verdict(measured: dict[str, Any], *, head_step_max: float | None = HEAD_STEP_REFERENCE) -> list[str]:
    """The top band's jump into the loop's first frame, in words, when it pops (section 3, "The seam
    pop"); empty otherwise. A sideways jump the head bound already names (`jolt_verdict`, into frame
    0) is not said twice."""
    pop = measured.get("seam_pop") or {}
    if "skipped" in pop or not pop.get("pops"):
        return []
    head = measured["head"].get("x", {})
    said = head_step_max is not None and head.get("step_max_pct", 0) > head_step_max and head.get("worst_into_frame") == 0
    axes = [(axis, pop[axis]) for axis in ("y", "x") if pop[axis]["wrap_is_largest"]
            and pop[axis]["wrap_over_median"] > pop["reference"] and not (axis == "x" and said)]
    if not axes:
        return []
    words = {"x": "sideways", "y": "up or down"}
    return ["the top of the silhouette jumps into the loop's first frame "
            + ", ".join(f"{words[a]} {v['wrap_pct']:.2f} % of the body height, {v['wrap_over_median']:.1f}x its median step" for a, v in axes)
            + f" (over {pop['reference']:g}x): a part held above the head swings on its own beat and does not close at this cut"]


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
