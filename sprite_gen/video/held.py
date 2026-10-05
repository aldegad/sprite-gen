# SPDX-License-Identifier: Apache-2.0
"""Held drawings: a clip that shows each drawing for two or three frames, and how many it shows a second.

A video model may film a walk at 12 drawings a second inside a 24 fps clip: each drawing is shown
twice ("on twos"), or three times ("on threes"). Played at its own rate such a loop is ordinary
limited animation. Stretched to a longer cycle (`video-cycle-align`), the frames between are made
by an interpolator across a gap two or three times as wide as one frame of an every-frame clip, and
where the legs swap places in that gap no interpolator can draw them (docs/loop-repair.md section 4).

The second frame of a pair is rarely a byte copy: the model redraws it a little, so its step from
the first is small but not zero. A rule that calls a step held when it falls under a fraction of the
median step fails exactly there: with half the steps held, the median falls between the held and
the moving steps, and a fraction of it is smaller than many of the held steps. So the judgement
reads the rhythm, not one step at a time: in a clip on twos every second step is small, on threes two
of every three, and a motion filmed every frame has no such rhythm — its steps swell and shrink
with the stride, a dozen frames or more, never every second or third frame.

`measure` reads it off the steps between neighbouring frames (the distance `video-loop` cuts by):
for a hold h of 2 or 3, in windows of WINDOW steps, the phase whose every h-th step is the change
and the rest are held, scored by `contrast` — the held steps over the change steps (their medians:
a frame repeated now and then, as a 20 fps clip carried at 24 repeats one in six, moves a median
not at all), the median over the windows. A hold whose contrast is under CONTRAST_MAX is the clip's
hold; each step is then held when it is nearer the held steps than the change steps (under the
geometric midpoint of their means), and the drawings are the frames that begin one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from sprite_gen._deps import np

HOLDS = (2, 3)  # frames a drawing is shown for: on twos, on threes
WINDOW = 12  # steps per window: six drawings on twos, four on threes; a phase that slips spoils a window or two, not the clip
# The held steps over the change steps (medians) under which a hold is the clip's. A pair
# redrawn a little sits at a quarter of a change step or below, a byte copy near zero; a motion filmed
# every frame whose steps alternate long and short stays above a third (docs/loop-repair.md section 4).
CONTRAST_MAX = 0.3
MIN_STEPS = 6  # fewer steps than this say nothing about a rhythm of two or three


def _contrast(steps: np.ndarray, hold: int) -> tuple[float, list[np.ndarray], list[np.ndarray]] | None:
    """(the median over windows of the best phase's contrast, the held steps and the change steps of
    each window's best phase), or None when no window has a step of each kind that moves."""
    n = len(steps)
    spans = [(0, n)] if n < 2 * WINDOW else [(a, a + WINDOW) for a in range(0, n - WINDOW + 1, WINDOW // 2)]
    if spans[-1][1] < n:
        spans.append((n - WINDOW, n))
    scores, held, change = [], [], []
    for a, b in spans:
        idx = np.arange(b - a)
        best = None
        for phase in range(hold):
            is_change = idx % hold == phase
            if not is_change.any() or is_change.all():
                continue
            top = float(np.median(steps[a:b][is_change]))
            if top <= 0:
                continue
            score = float(np.median(steps[a:b][~is_change])) / top
            if best is None or score < best[0]:
                best = (score, steps[a:b][~is_change], steps[a:b][is_change])
        if best is not None:
            scores.append(best[0])
            held.append(best[1])
            change.append(best[2])
    if not scores:
        return None
    return float(np.median(scores)), held, change


def measure(steps: Sequence[float], *, fps: float, cyclic: bool = False) -> dict[str, Any]:
    """Whether a run of frames holds its drawings, and how many drawings it shows a second.

    `steps` are the distances between neighbouring frames: n - 1 of them for n frames, or n read as a
    ring (`cyclic`, the last step from the last frame back to the first). The rhythm is read on the
    steps in order (a ring's closing step is left out of it: a cut may close mid-pair), the drawings
    are counted on all of them. `hold` is 1 (every frame a drawing), 2 or 3."""
    s = np.asarray(steps, dtype=np.float64)
    frames = len(s) if cyclic else len(s) + 1
    inner = s[:-1] if cyclic else s
    seconds = frames / fps
    base: dict[str, Any] = {"frames": frames, "fps": fps, "steps": len(s), "contrast": {}}
    if len(inner) < MIN_STEPS or float(inner.max(initial=0.0)) <= 0:
        why = "no motion between frames" if len(inner) >= MIN_STEPS else f"{len(inner)} steps, under {MIN_STEPS}: too few to read a rhythm"
        return {**base, "hold": None, "held_steps": None, "drawings": None, "drawings_per_second": None, "why": why}
    found = {h: _contrast(inner, h) for h in HOLDS}
    base["contrast"] = {str(h): (round(f[0], 4) if f else None) for h, f in found.items()}
    scored = [(f[0], h) for h, f in found.items() if f is not None and f[0] < CONTRAST_MAX]
    if not scored:
        return {**base, "hold": 1, "held_steps": 0, "drawings": frames, "drawings_per_second": round(frames / seconds, 3)}
    _, hold = min(scored)
    _, held, change = found[hold]  # type: ignore[misc]
    # The boundary is the geometric midpoint of the two kinds' plain means: a byte copy (step 0) beside
    # a redrawn repeat would drag a geometric mean of the held steps under the redrawn ones.
    change_mean = float(np.concatenate(change).mean())
    mid = math.sqrt(max(float(np.concatenate(held).mean()), change_mean * 1e-3) * change_mean)
    held_steps = int((s < mid).sum())
    changes = len(s) - held_steps
    drawings = max(1, changes) if cyclic else changes + 1
    return {**base, "hold": hold, "held_steps": held_steps, "drawings": drawings,
            "drawings_per_second": round(drawings / seconds, 3), "held_below": round(mid, 6)}
