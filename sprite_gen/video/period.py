# SPDX-License-Identifier: Apache-2.0
"""Two cycles in one loop: a screen that suspects a shorter cycle, never takes or refuses one.

A motion that repeats every P frames also repeats every 2P, and the longer lag can read as the
better repeat: a clip drawn on twos whose cycle is an odd number of frames shows each cycle half a
drawing off the one before, so the profile dips deeper two cycles on than one. A loop cut at 2P
plays clean on its own. Resampled to the length of a set whose other loops hold one cycle
(`video-cycle-align`), it walks at twice their speed.

Pixels cannot tell that loop from a good one. Half a gait cycle on, the legs are back in place
with near and far swapped; where the two legs are drawn alike (one colour of trousers, white fur)
that one step and a true cycle half a drawing late are the same picture. Every rule that took the
half as a cycle took a gait's half step somewhere (docs/video-pipeline.md, "The fundamental
period"), and the leg signal fails both ways: a heel kick swings the stride twice a step, a
diagonal view's two steps open the feet unequally. So nothing here says "this is a cycle", and
nothing says "this is not one" either: a second cycle drawn a little differently (moved a pixel, a
few per cent lighter) is, in pixels, off the motion's path just as a step with the legs swapped
is, so a rule that refused one refused the other. The screen only says whether a loop may hold
more than one cycle, for someone who can count them to settle.

`screen` looks at a half and a third of a period. A candidate there is the lowest local minimum
of the lag profile within NEAR frames, no shorter than the floor (under a gait's floor a repeat is
one step, which the half-period guard owns). It is measured (`measure`: how far the frame one
candidate on lies off the motion's path, `pose`, and the legs' step signal, `steps` and `follow`,
sprite_gen/video/legs.py) and judged by one rule, `verdict` — the only place that decides: a
candidate that repeats (dips `periodicity_min` below the profile's mean) makes the loop a
**suspect**. It may hold two (or three) cycles, and only someone who counts them — by eye, or a
vision call — can say; `--cycles <loop>=k` on `video-cycle-align` is how that count comes back.
The measurements go in the record as the evidence for whoever looks, never into the decision.
"""

from __future__ import annotations

import math
from typing import Any

from sprite_gen._deps import np
from sprite_gen.video import legs as legs_mod

# The spans (a, b) around a frame j that a returned frame may lie between: j and a neighbour, or
# two frames across j. Two frames wide, so a clip that holds each drawing for two frames (a span
# of one frame is then no span at all) still has a span around every frame.
SPANS = ((-1, 0), (0, 1), (-2, 0), (-1, 1), (0, 2))
DIVISORS = (2, 3)  # a half and a third of the period: two or three cycles in it
NEAR = 1.0  # frames either side of period / divisor where the shorter cycle's own minimum may sit
EXACT = 0.05  # a lag closer than this (in playback steps) returns the same picture: `pose` is 0


def _returns(D: np.ndarray, lag: int, js: np.ndarray | None, cyclic: bool) -> tuple[float, float] | None:
    """(off the path, the lag's own distance), both in playback steps, or None (see `off_path`)."""
    n = len(D)
    reach = max(abs(v) for span in SPANS for v in span)
    if cyclic:
        js = np.arange(n) if js is None else np.asarray(js)
        at = lambda v: v % n  # noqa: E731
    else:
        every = np.arange(reach, n - lag) if js is None else np.asarray(js)
        js = every[(every >= reach) & (every + lag < n)]
        at = lambda v: v  # noqa: E731
    if js.size == 0:
        return None
    x = at(js + lag)
    extra = np.minimum.reduce([D[at(js + a), x] + D[x, at(js + b)] - D[at(js + a), at(js + b)] for a, b in SPANS])
    step = float(D[js, at(js + 1)].mean())
    return (float(extra.mean()) / step, float(D[js, x].mean()) / step) if step > 0 else None


def off_path(D: np.ndarray, lag: int, *, js: np.ndarray | None = None, cyclic: bool = False) -> float | None:
    """How far off the motion's path the frames one `lag` on lie, in playback steps.

    For a frame j and the frame x one lag on, over the short spans (a, b) of the clip around j, the
    least `D[a, x] + D[x, b] - D[a, b]`: a frame between a and b adds nothing (the distance is a sum
    of absolute differences, so it adds up along a path), a frame off the path adds twice its own
    distance. `js` are the frames compared (default: every frame with room for the spans and the
    lag); `cyclic` reads `D` as one loop, the lag and the spans wrapping. None when no frame has
    room or the frames do not move."""
    found = _returns(D, lag, js, cyclic)
    return None if found is None else found[0]


def steps(values: list[float], lag: int, *, js: np.ndarray | None = None, cyclic: bool = False) -> tuple[float, float] | None:
    """(`steps`, `follow`) of the step signal `values` for a candidate of `lag` frames, each over the
    signal's mean difference a quarter of the lag on: `steps`, its difference half the lag on (two
    equal steps put it back where it was half way: near 0); `follow`, its difference one lag on (how
    far the signal is from repeating where the picture does). Recorded, never decided by. None when
    there is no room or the signal does not move."""
    s = np.asarray(values, dtype=float)
    n = len(s)
    starts = np.arange(n) if js is None else np.asarray(js)

    def apart(h: int) -> float | None:
        if h <= 0:
            return None
        if cyclic:
            return float(np.abs(s[starts] - s[(starts + h) % n]).mean())
        t = starts[starts + h < n]
        return float(np.abs(s[t] - s[t + h]).mean()) if t.size else None

    half = [d for d in (apart(math.floor(lag / 2)), apart(math.ceil(lag / 2))) if d is not None]
    quarter = [d for d in (apart(math.floor(lag / 4)), apart(math.ceil(lag / 4))) if d is not None]
    whole = apart(lag)
    if not half or not quarter or whole is None or max(quarter) <= 0:
        return None
    return min(half) / max(quarter), whole / max(quarter)


def measure(D: np.ndarray, lag: int, *, legs: dict[str, Any], js: np.ndarray | None = None, cyclic: bool = False) -> dict[str, Any]:
    """What the return one `lag` on looks like: its `pose` (`off_path` over the lag's own
    `distance`, 0 where the distance is under EXACT; absent where too few frames measure it) and
    the legs' `steps` and `follow` where they are read."""
    row: dict[str, Any] = {}
    if legs.get("by") is not None:
        found_steps = steps(legs["values"], lag, js=js, cyclic=cyclic)
        if found_steps is not None:
            row = {"steps": round(found_steps[0], 3), "follow": round(found_steps[1], 3)}
    found = _returns(D, lag, js, cyclic)
    if found is not None:
        off, dist = found
        row.update(off_path=round(off, 3), distance=round(dist, 3), pose=round(0.0 if dist < EXACT else off / dist, 3))
    return row


def verdict(row: dict[str, Any], *, periodicity_min: float) -> tuple[str | None, str]:
    """The screen's one rule, read off a candidate's measurements: (None — not a repeat, |
    "suspect", why). A candidate that dips `periodicity_min` or more below the profile's mean is a
    suspect; that is all. The pose and the legs are not consulted: a second cycle redrawn a pixel
    aside or a few per cent lighter reads in pixels as another pose, just as a step with the legs
    swapped does, so nothing measured here refuses a repeat. Everything that decides whether a loop
    is suspected is here; the rest of the module only measures."""
    depth = row["periodicity"]
    if depth < periodicity_min:
        return None, f"dips {depth:.2f} below the profile mean, under {periodicity_min:.2f}: not a repeat"
    return "suspect", (f"repeats {depth:.2f} below the profile mean (a repeat from {periodicity_min:.2f}): one cycle of a loop that holds "
                       f"{row['cycles']}, or one step of a stride whose two steps look alike — pixels cannot tell")


def screen(period: int, *, D: np.ndarray, prof: dict[int, float], minima: list[int], mean: float, lowest: int,
           periodicity_min: float, signals: dict[str, list[float]] | None, gait: bool, js: np.ndarray | None = None,
           cyclic: bool = False, floor_why: str = "the window", fps: float | None = None) -> dict[str, Any]:
    """Whether `period` may hold two or three cycles: the record of each candidate at a half and a
    third of it (`checked`), and those `verdict` suspects (`suspects`, each with the `cycles` it
    would make the period). Nothing is chosen.

    `D` is the distance matrix the profile `prof` was measured on (read as a ring with `cyclic`;
    `js` the frames compared), `minima` the profile's local minima, `mean` its mean, `lowest` the
    shortest lag a cycle may be (`floor_why` names it), `signals` the frames' `legs.strike_signals`
    (or None) and `gait` whether there are steps to read in them; with `fps` each candidate also
    says how long it is."""
    if signals is None:
        legs: dict[str, Any] = {"by": None, "why": "no frames to read the legs from"}
    elif not gait:
        legs = {"by": None, "why": "not a gait: no steps to read"}
    else:
        legs = legs_mod.step_signal(signals)
    record: dict[str, Any] = {"period": period, "legs": {k: v for k, v in legs.items() if k != "values"},
                              "checked": [], "suspects": []}
    for k in DIVISORS:
        near = [q for q in minima if abs(q - period / k) <= NEAR and q < period]
        if not near:
            continue  # nothing repeats there: not a candidate, nothing to record
        q = min(near, key=lambda lag: prof[lag])
        row: dict[str, Any] = {"period": q, "of": period, "divisor": k, "cycles": k}
        if fps:
            row["seconds"] = round(q / fps, 3)
        record["checked"].append(row)
        if q < lowest:
            row.update(verdict=None, why=f"shorter than {floor_why} ({lowest} frames)")
            continue
        row["periodicity"] = round((mean - prof[q]) / mean if mean > 0 else 0.0, 4)
        row.update(measure(D, q, legs=legs, js=js, cyclic=cyclic))
        row["verdict"], row["why"] = verdict(row, periodicity_min=periodicity_min)
        if row["verdict"] == "suspect":
            record["suspects"].append(row)
    return record


def local_minima(prof: dict[int, float]) -> list[int]:
    """The lags of `prof` no deeper than either neighbour."""
    return [lag for lag in prof if lag - 1 in prof and lag + 1 in prof and prof[lag] <= prof[lag - 1] and prof[lag] <= prof[lag + 1]]


# The other way round (docs/loop-repair.md section 3, "The step screen"): a walk filmed slower than its
# window can hold is cut one step long — the legs drawn alike, the step repeats in pixels — and nothing
# shorter than the cut is there to suspect. The lag twice the cut, read over the whole clip, repeats
# better than the cut itself where the arms or a held part tell the two steps apart.
STEP_SEARCH = 3  # frames either side of twice the cut where the cycle's own lag may sit
STEP_PAIRS_MIN = 4  # frame pairs a lag must have in the clip to be read
STEP_RATIO_MAX = 0.85  # the lag twice the cut over the cut's own lag, same frames: at or under this, a suspect


def leg_peaks(values: list[float]) -> list[int]:
    """The step signal's peaks over a cut read as a ring: 1-2-1 smoothed, a frame that is the largest
    within a sixth of the ring either side, first of a plateau, standing 30 % of the signal's range over
    the least there. A cycle shows two, a step one. Recorded, never decided by."""
    v = np.asarray(values, dtype=float)
    n = len(v)
    if n < 3:
        return []
    s = np.array([(v[k - 1] + 2 * v[k] + v[(k + 1) % n]) / 4 for k in range(n)])
    r, spread = max(1, n // 6), float(s.max() - s.min())
    out = []
    for k in range(n):
        near = [s[(k + d) % n] for d in range(-r, r + 1)]
        if s[k] == max(near) and s[(k - 1) % n] < s[k] and s[k] - min(near) >= 0.3 * spread:
            out.append(k)
    return out


def step_screen(D: np.ndarray, *, start: int, length: int, periodicity_min: float,
                signals: dict[str, list[float]] | None) -> dict[str, Any]:
    """Whether a gait's cut (`start`, `length`) may be one step of a cycle twice as long — never what
    is cut. Over the whole clip (not the search window): the lag within STEP_SEARCH of twice the cut
    whose mean distance is least (`lag`), and, over every frame with both lags in the clip (`pairs`),
    its distance over the cut's own (`ratio`); how far that lag dips under the clip's profile from half
    the cut to the lags read (`depth`); how much of a second cycle the clip holds past one cycle of that
    lag, `coverage` (n − lag) / lag; and the step signal's peaks over the cut (`leg_peaks`, a record
    only). A **suspect** repeats at the lag (`depth` at least `periodicity_min`) and better than at the
    cut (`ratio` at most STEP_RATIO_MAX). A true cycle repeats about as well a cycle on (its drift makes
    it a little worse); a step repeats better two cut lengths on, where the arms and a held part come
    back too. Pixels cannot tell a cycle on a frame count that is not whole (it repeats better at its
    double, being whole there) from a step, so only someone who counts the steps settles it — and the
    count comes back as `video-loop --steps`."""
    n = len(D)
    record: dict[str, Any] = {"method": "two-cut-lengths-v1", "length": length,
                              "searched": [2 * length - STEP_SEARCH, 2 * length + STEP_SEARCH],
                              "ratio_max": STEP_RATIO_MAX, "periodicity_min": periodicity_min}
    if signals is None:
        record["leg_peaks"] = {"by": None, "why": "no frames to read the legs from"}
    else:
        legs = legs_mod.step_signal({k: v[start:start + length] for k, v in signals.items()})
        record["leg_peaks"] = ({"by": legs["by"], "count": len(leg_peaks(legs["values"]))} if legs.get("by") is not None
                               else {"by": None, "why": legs["why"]})

    def mean(lag: int) -> float:
        return float(D[np.arange(n - lag), np.arange(lag, n)].mean())

    lags = [lag for lag in range(2 * length - STEP_SEARCH, 2 * length + STEP_SEARCH + 1) if n - lag >= STEP_PAIRS_MIN]
    if not lags:
        record.update(lag=None, suspect=False,
                      why=f"the clip ({n} frames) holds fewer than {STEP_PAIRS_MIN} frame pairs {record['searched'][0]} or more frames apart: "
                          "twice the cut is not read")
        return record
    profile = {lag: mean(lag) for lag in range(max(2, length // 2), lags[-1] + 1)}
    lag = min(lags, key=lambda k: (profile[k], k))
    js = np.arange(n - lag)
    at_cut, at_lag = float(D[js, js + length].mean()), float(D[js, js + lag].mean())
    baseline = float(np.mean(list(profile.values())))
    ratio = round(at_lag / at_cut, 4) if at_cut > 0 else None
    depth = round((baseline - profile[lag]) / baseline, 4) if baseline > 0 else 0.0
    record.update(lag=lag, pairs=int(js.size), ratio=ratio, depth=depth, coverage=round((n - lag) / lag, 4))
    if depth < periodicity_min:
        record.update(suspect=False, why=f"{lag} frames on dips {depth:.2f} under the clip's profile, under {periodicity_min:.2f}: not a repeat")
    elif ratio is None or ratio > STEP_RATIO_MAX:
        record.update(suspect=False, why=f"{lag} frames on repeats {ratio}x as far as the cut's own {length}, over {STEP_RATIO_MAX}: "
                                         "no better two cut lengths on")
    else:
        record.update(suspect=True, why=f"{lag} frames on repeats {ratio:.2f}x as far as the cut's own {length} (at most {STEP_RATIO_MAX}) "
                                        f"and dips {depth:.2f} under the clip's profile: the cut may be one step of a cycle {lag} frames "
                                        "long, or a cycle that repeats better at its double — pixels cannot tell; count the steps in "
                                        "the loop (video-loop --steps)")
    return record
