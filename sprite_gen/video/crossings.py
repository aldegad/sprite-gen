# SPDX-License-Identifier: Apache-2.0
"""How many steps a walk's cut covers, read only from the steps the clip itself shows (docs/loop-repair.md
section 3, "The step count").

A walk cycle is two steps: the left foot forward once and the right once. Seen from the side or a diagonal, left
foot forward and right foot forward are nearly the same silhouette, so a cut that matches its shape at the wrap can
be one step (a half step loop: one foot goes forward), or three, the wrap putting the near foot back (a stomp in
place). Pixels cannot count them. The feet can: each step the feet pass each other once, a **crossing**, and a cut
that covers two crossings, joined at the wrap where the walk itself goes on, is one cycle.

Every crossing counted is one the clip shows whole. A cut s/L plays frames s..s+L-1 and then frame s again where
the clip itself plays frame e = s+L; the wrap joins two frames the clip never filmed side by side, so nothing the
loop shows across it is evidence. The loop as played only has to agree.

Reading the crossings (`read`):

- **gap** (the feet are seen apart in at least `legs.FEET_APART_SHARE` of the frames: side, diagonals): the foot
  band's widest gap (`legs.strike_signals`). The feet are together under `legs.FEET_APART` of the body's height
  and apart again only at APART_AGAIN times it; a run of frames together with apart frames on both sides is a
  crossing, at its middle.
- **sign** (front, back: the feet are never seen apart): which side of the body's axis the foot band leans
  (`foot_signals`). The side changes only when the lean reaches SIDE_SWING of its swing past the axis on the other
  side; the crossing is where it last crossed the axis. The lean must alternate a step apart (its correlation one
  step on at most ALTERNATION_MAX), else it is not feet and the clip is not read.
- A run cut by the clip's first or last frame is partial: it may be the stance the clip starts from. It is a
  crossing only where its twin (the run two runs away, the same foot's pass) is whole, at least as long as the part
  seen, and the part seen moves like the twin's matching part; its middle is then bounded by the twin's length.
  Frames no reading confirms (an unconfirmed partial run; in sign mode the frames before the first side and after a
  last change the clip ends before confirming) make every cut that plays them uncertain.

Judging a cut (`Reading.judge`): the clip's steps (between consecutive crossings) set the step range, their median
m within a factor of sqrt 2 each way. Every step the cut plays, on both sides of the wrap, lies in it (a stall, a
stomp or a stand fails it). The walk's phase phi (crossings so far, plus how far into the step) advances
A = phi(e) - phi(s) over the cut; k = round(A) steps, and the wrap joins two points of a step delta = A - k apart,
which must lie in [1/sqrt2 - 1, sqrt2 - 1]. A `two` is k = 2 under every bound of what the clip does not show, with
the loop as played showing exactly two crossings as well; `refuted` is k steps, each seen (`one step`,
`three steps`); everything else is `uncertain`, never taken as two. `phase` is delta in frames (delta x m), the
worst over the bounds: how far the wrap is from joining the same point of the walk.

Crossings that mark cycles (`Reading.mark_cycles`, read by `local_cycle.read_cycles`): in gap mode a crossing is
where the feet meet on the screen, and they need not meet at every pass. The far foot can pass behind the near one
inside a run of the feet together (a pass hidden), or a foot can step toward or away from the camera and pass the
other apart on the screen. The feet then meet once a cycle, and the interval between two crossings holds two steps —
marked so only where the body's top line witnesses it, rising and falling twice an interval less its flow (the picture
a step on is a cycle on's where the legs are drawn alike, and the picture drifting up and down moves the line too).
Every count then reads two steps an interval (`per`): the step is half the interval, a cut's advance is twice the
crossings it covers, and the loop as played shows two steps a crossing. The step range is still read on the intervals.

What crossings cannot see: a hold the step range absorbs (a 13-frame step held to 17), a still run of the feet
together as long as a pass, a wobble that reaches the reading's sides at a step's beat. They read as steps.
"""

from __future__ import annotations

import bisect
import itertools
import math
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import legs as legs_mod

AXIS_PART = 0.4  # the body's axis: the middle of the solid pixels of its top 40 % (head and torso)
FOOT_TOP = 0.88  # the foot band the sides are read on: from 88 % of the body's height down (its lowest 12 %)
MERGE = 3  # sign mode: a side held fewer frames than this is the side before it
SIDE_SWING = 0.1  # sign mode: of the lean's swing (P90 - P10), past the axis, before a side is seen
APART_AGAIN = 1.5  # gap mode: x FEET_APART before feet seen together are seen apart again
ALTERNATION_MAX = -0.7  # sign mode: the lean's correlation one step on, at most: feet stepping turn it over
ROOT2 = math.sqrt(2.0)
SEAM_BAND = (1 / ROOT2 - 1, ROOT2 - 1)  # the wrap's delta: its step within the step range of the steps it joins
OPEN_SAMPLES = 9  # values an open step's unseen end is tried at, across its bound
PARTIAL_SAMPLES = 3  # values a partial crossing's middle is tried at, across its bound
PHASE_TOLERANCE = 1.5  # frames: crossings are read to half a frame, a cut's length to one
# gap mode: the feet seen together at a pass, the median whole run over the step, past which a pass can hide among
# them — the far foot standing behind the near one through part of the step (`Reading.overlap`)
HIDE_SHARE = 0.5
EPS = 1e-9


def foot_signals(frames: list[Image.Image], gap: list[float] | None = None) -> dict[str, list[float]]:
    """Per frame: `gap`, the foot band's widest gap (`legs.strike_signals`, or as given); `lean`, the foot band's
    solid pixels left of the body's axis less those right of it, over both; `mass`, both, over the body's height
    squared. Raises ValueError where a frame has no solid body."""
    gap = legs_mod.strike_signals(frames)["gap"] if gap is None else gap
    lean, mass = [], []
    for frame in frames:
        solid = np.asarray(frame.getchannel("A")) >= legs_mod.LOW_ALPHA
        rows = np.nonzero(solid.any(axis=1))[0]
        if rows.size == 0:
            raise ValueError("a loop frame has no solid body")
        y0, y1 = int(rows[0]), int(rows[-1])
        height = y1 - y0 + 1
        xs = np.nonzero(solid[y0:y0 + max(1, int(AXIS_PART * height))])[1]
        axis = float(xs.mean())
        band = solid[y0 + int(FOOT_TOP * height):y1 + 1]
        left = band & (np.arange(solid.shape[1])[None, :] < axis)
        side_l, side_r = float(left.sum()) / height / height, float((band & ~left).sum()) / height / height
        lean.append((side_l - side_r) / max(side_l + side_r, 1e-9))
        mass.append(side_l + side_r)
    return {"gap": [float(g) for g in gap], "lean": lean, "mass": mass}


def _smooth(v: Any) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return np.convolve(np.pad(v, 1, mode="edge"), [.25, .5, .25], mode="valid")


def _merge_short(sides: np.ndarray) -> np.ndarray:
    """Sides held fewer than MERGE frames go to the side before them (the reading's noise filter)."""
    sides = sides.copy()
    k = 0
    while k < len(sides):
        j = k
        while j + 1 < len(sides) and sides[j + 1] == sides[k]:
            j += 1
        if j - k + 1 < MERGE and k > 0 and sides[k] != 0:
            sides[k:j + 1] = sides[k - 1]
        k = j + 1
    return sides


def _sides(lean: list[float]) -> tuple[np.ndarray, int | None]:
    """+1/-1 a frame once a side is seen (0 before), and the frame from which the clip's end leaves a change
    unconfirmed (or None)."""
    v = _smooth(lean)
    v = v - np.median(v)
    reach = SIDE_SWING * float(np.percentile(v, 90) - np.percentile(v, 10))
    sides = np.zeros(len(v), dtype=int)
    side, axis = 0, 0
    for t in range(len(v)):
        if t > 0 and np.sign(v[t]) != np.sign(v[t - 1]):
            axis = t
        if side == 0 and abs(v[t]) >= reach:
            side = 1 if v[t] > 0 else -1
        elif side != 0 and -side * v[t] >= reach:
            side = -side
            sides[axis:t] = side
        sides[t] = side
    sides = _merge_short(sides)
    pending = axis if side != 0 and axis > 0 and np.sign(v[axis]) == -side and np.all(-side * v[axis:] < reach) else None
    return sides, pending


def _together(gap: list[float]) -> list[bool]:
    out, together = [], None
    for g in gap:
        if together is None:
            together = g < legs_mod.FEET_APART
        elif together and g >= APART_AGAIN * legs_mod.FEET_APART:
            together = False
        elif not together and g < legs_mod.FEET_APART:
            together = True
        out.append(bool(together))
    return out


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    out, k, n = [], 0, len(flags)
    while k < n:
        if flags[k]:
            j = k
            while j + 1 < n and flags[j + 1]:
                j += 1
            out.append((k, j))
            k = j + 1
        else:
            k += 1
    return out


def _motion(sig: dict[str, list[float]], a: int, b: int) -> float | None:
    """How much the foot band moves a frame over frames a..b, in swings of its mass and lean: a pass moves, a stance
    does not. None where it cannot be read."""
    if b <= a:
        return None
    total = 0.0
    for key in ("mass", "lean"):
        v = np.asarray(sig[key], dtype=float)
        swing = float(np.percentile(v, 90) - np.percentile(v, 10))
        if swing <= 0:
            return None
        total += float(np.mean(np.abs(np.diff(v[a:b + 1])))) / swing
    return total


def _moves_like(sig: dict[str, list[float]], part: tuple[int, int], twin: tuple[int, int]) -> bool:
    m, t = _motion(sig, *part), _motion(sig, *twin)
    return m is not None and t is not None and t > 0 and m >= t / ROOT2


def _phi(c: list[float], t: float) -> float:
    i = bisect.bisect_right(c, t) - 1
    i = min(max(i, 0), len(c) - 2)
    return i + (t - c[i]) / (c[i + 1] - c[i])


class Reading:
    """What the clip shows of its steps: `mode`, the crossings (each (lo, hi): a whole one has lo == hi), the frames
    no reading confirms, the frame states the loop as played is read on, and whether it reads at all (`readable`,
    `why`)."""

    def __init__(self, n: int, mode: str, crossings: list[tuple[float, float]], *, head_limit: float = -0.5,
                 tail_limit: float | None = None, runs: list[tuple[int, int]] | None = None, together: list[bool] | None = None,
                 sides: list[int] | None = None, unconfirmed: list[tuple[int, int]] | None = None, why: str | None = None):
        self.n, self.mode = n, mode
        self.crossings = sorted(crossings)
        self.head_limit = head_limit  # an unseen crossing before the first lies at or before this
        self.tail_limit = n - 0.5 if tail_limit is None else tail_limit  # ... after the last, at or after this
        self.runs = runs or []
        self.together, self.sides = together, sides
        self.unconfirmed = unconfirmed or []
        whole = [b - a + 1 for a, b in self.runs if a > 0 and b < n - 1]
        self.run_limit = ROOT2 * float(np.median(whole)) if whole else None
        middles = [(lo + hi) / 2 for lo, hi in self.crossings]
        self.interval = float(np.median(np.diff(middles))) if len(middles) >= 3 else None  # between two crossings
        self.per = 1  # steps an interval holds: 2 where the crossings mark cycles (`mark_cycles`)
        self.cycles: dict[str, Any] | None = None  # whether they do, as read (`mark_cycles`); None where not asked
        self.alternation: float | None = None  # sign mode: the lean's correlation one step on (`read`)
        self.why = why if why is not None else (None if self.interval is not None else
                                                f"{len(middles)} crossing{'s' if len(middles) != 1 else ''} in the clip: under two steps, no step range")

    @property
    def step(self) -> float | None:
        """The walk's step, in frames: the interval between two crossings, or half of it where they mark cycles."""
        return None if self.interval is None else self.interval / self.per

    def mark_cycles(self, said: dict[str, Any]) -> None:
        """Whether the crossings mark cycles, not steps, as read (`said`, kept in the record; `local_cycle.read_cycles`).
        Where `said['cycles']` holds, the feet are seen to meet once a cycle — the other pass hidden, or made apart on the
        screen — and every count is two steps an interval (`judge`, `ring_count`, `lengths`)."""
        self.cycles = said
        self.per = 2 if said.get("cycles") else 1

    @property
    def stands(self) -> list[tuple[int, int]]:
        """Gap mode: the runs of the feet together longer than any pass (over `run_limit`, ROOT2 times the median run
        the clip shows whole), whole or cut by its first or last frame — the walk standing still."""
        if self.run_limit is None:
            return []
        return [(a, b) for a, b in self.runs if b - a + 1 > self.run_limit + EPS]

    @property
    def readable(self) -> bool:
        return self.why is None

    @property
    def overlap(self) -> float | None:
        """Gap mode: how long the feet are seen together at a pass, over the interval between two crossings — the median
        of the runs the clip shows whole. A pass is brief; past HIDE_SHARE the far foot stands behind the near one
        through part of the step, and a pass can hide in the run. None in sign mode, or without a step or a whole run."""
        whole = [b - a + 1 for a, b in self.runs if a > 0 and b < self.n - 1]
        if self.mode != "gap" or self.interval is None or not whole:
            return None
        return float(np.median(whole)) / self.interval

    @property
    def hides(self) -> bool:
        """Gap mode: whether a pass can hide among the feet seen together (`overlap` over HIDE_SHARE)."""
        overlap = self.overlap
        return overlap is not None and overlap > HIDE_SHARE

    def record(self) -> dict[str, Any]:
        """The reading as the report keeps it."""
        out: dict[str, Any] = {"method": "observed-crossings-v1", "mode": self.mode, "readable": self.readable,
                               "crossings": [round(lo, 2) if hi - lo < EPS else [round(lo, 2), round(hi, 2)] for lo, hi in self.crossings],
                               "unconfirmed": [list(r) for r in self.unconfirmed]}
        if self.step is not None:
            out["step_range"] = [round(self.step / ROOT2, 2), round(self.step, 2), round(self.step * ROOT2, 2)]
        if self.stands:
            out["stands"] = [list(r) for r in self.stands]
            out["run_limit"] = round(self.run_limit, 2)
        if self.cycles is not None:
            out["cycles"] = self.cycles
        if self.alternation is not None:
            out["alternation"] = self.alternation
        if self.why is not None:
            out["why"] = self.why
        return out

    def scenarios(self):
        """Crossing position lists, each bounded crossing at its low end, middle and high end."""
        choices = [[lo] if hi - lo < EPS else list(np.linspace(lo, hi, PARTIAL_SAMPLES)) for lo, hi in self.crossings]
        for pick in itertools.product(*choices):
            yield sorted(pick)

    def ring_count(self, s: int, L: int) -> int | None:
        """Steps on the loop cut s/L plays, its wrap included — a crossing each, two where the crossings mark cycles: a
        check that the loop shows what the clip counts."""
        if self.sides is not None:
            w = self.sides[s:s + L]
            return self.per * (sum(1 for j in range(1, L) if w[j] != w[j - 1]) + (1 if w[L - 1] != w[0] else 0))
        if self.together is None:
            return None
        t = list(self.together[s:s + L])
        if all(t) or not any(t):
            return 0
        r = t.index(False)
        return self.per * len(_runs(t[r:] + t[:r]))

    def _scenario(self, c: list[float], s: int, L: int) -> tuple[str, str, str, list[int], list[float], float | None]:
        """One scenario (every bounded crossing placed): (verdict, code, why, ks, deltas, the step). The step range is
        read on the intervals between crossings, and the count is `per` steps an interval."""
        e, last = s + L, s + L - 1
        if len(c) < 3:
            return "uncertain", "no-range", "under two steps in the clip: no step range", [], [], None
        gaps = sorted(b - a for a, b in zip(c, c[1:]))
        mid = len(gaps) // 2
        m = float(gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2)
        lo, hi = m / ROOT2, m * ROOT2
        step = m / self.per

        def played(a: float, b: float) -> bool:
            return max(math.floor(a) + 1, s) <= min(math.ceil(b) - 1, last)

        partial = [(a, b) for a, b in self.unconfirmed if a <= last and b >= s]
        if partial:
            what = ", ".join(f"{a}..{b}" for a, b in partial)
            return "uncertain", "partial", f"plays frames no crossing confirms ({what}): a stance, a pass the clip cut or a change it ended on", [], [], step
        head_played, tail_played = s < c[0], last > c[-1]
        before = (c[0] - hi, min(c[0] - lo, self.head_limit))
        after = (max(c[-1] + lo, self.tail_limit), c[-1] + hi)
        if head_played and before[0] > before[1] + EPS:
            return "uncertain", "head-stall", f"plays the clip's head: {c[0] - self.head_limit:.1f}+ frames with no crossing, over {hi:.1f}", [], [], step
        if tail_played and after[0] > after[1] + EPS:
            return "uncertain", "tail-stall", f"plays the clip's tail: {self.tail_limit - c[-1]:.1f}+ frames with no crossing, over {hi:.1f}", [], [], step
        bad = [(a, b) for a, b in zip(c, c[1:]) if played(a, b) and not lo - EPS <= b - a <= hi + EPS]
        if bad:
            what = ", ".join(f"{a:g}->{b:g} ({b - a:.1f})" for a, b in bad)
            return ("uncertain", "step-long" if any(b - a > hi for a, b in bad) else "step-short",
                    f"plays a step out of [{lo:.1f}, {hi:.1f}]: {what}", [], [], step)
        if self.run_limit is not None:
            stands = [(a, b) for a, b in self.stands if a <= last and b >= s]
            if stands:
                what = ", ".join(f"{a}..{b}" for a, b in stands)
                return "uncertain", "stand", f"plays a stand: feet together {what}, over {self.run_limit:.1f} frames", [], [], step
        heads = np.linspace(*before, OPEN_SAMPLES) if head_played else [None]
        if e <= c[-1]:
            tails: Any = [None]
        elif tail_played:
            tails = np.linspace(*after, OPEN_SAMPLES)
        else:  # frame e alone lies past the last crossing, unplayed: any step the clip allows
            tails = np.linspace(c[-1] + max(lo, self.tail_limit - c[-1]), c[-1] + 10 * hi, OPEN_SAMPLES)
        base = list(c)

        def ring(x: Any, y: Any) -> list[float]:
            return ([x] if x is not None else []) + base + ([y] if y is not None else [])

        def phases(t: int) -> list[list[float]]:
            # phi at t for every (head, tail): before the first crossing it reads the head alone, from the last on the
            # tail alone, else neither — so each value is computed once, not once a pair
            if heads[0] is not None and t < c[0]:
                return [[_phi(ring(x, tails[0]), t)] * len(tails) for x in heads]
            if tails[0] is not None and t >= c[-1]:
                return [[_phi(ring(heads[0], y), t) for y in tails]] * len(heads)
            return [[_phi(ring(heads[0], tails[0]), t)] * len(tails)] * len(heads)

        ordered = all(x <= c[0] for x in heads if x is not None) and all(y >= c[-1] for y in tails if y is not None)
        if ordered:
            pe, ps = phases(e), phases(s)
        else:
            pe = [[_phi(ring(x, y), e) for y in tails] for x in heads]
            ps = [[_phi(ring(x, y), s) for y in tails] for x in heads]
        ks, deltas = set(), []
        for row_e, row_s in zip(pe, ps):
            for phi_e, phi_s in zip(row_e, row_s):
                advance = self.per * (phi_e - phi_s)
                k = int(math.floor(advance + 0.5))
                ks.add(k)
                deltas.append(advance - k)
        if len(ks) > 1:
            return "uncertain", "open-count", f"the open step leaves the count open: {sorted(ks)}", sorted(ks), deltas, step
        k = next(iter(ks))
        if not all(SEAM_BAND[0] - EPS <= d <= SEAM_BAND[1] + EPS for d in deltas):
            return ("uncertain", "seam", f"the wrap joins points {min(deltas):+.2f}..{max(deltas):+.2f} of a step apart: it makes or hides a crossing",
                    [k], deltas, step)
        if k == 0:
            return "uncertain", "no-step", "under half a step: no crossing", [k], deltas, step
        if k == 2:
            return "two", "two", "two steps", [k], deltas, step
        return "refuted", "refuted", f"{'one step' if k == 1 else f'{k} steps'}, each seen", [k], deltas, step

    def judge(self, s: int, L: int) -> dict[str, Any]:
        """The cut s/L: `verdict` (two, refuted, uncertain) with its `code` and `why`, the steps it covers (`k`), the
        wrap's `delta` (its least and largest over the bounds), `phase` (the worst delta, in frames) and the
        crossings the loop as played shows (`ring`)."""
        rec: dict[str, Any] = {"cut": [s, L]}
        if not self.readable:
            return {**rec, "verdict": "uncertain", "code": "unread", "why": self.why}
        outs = [self._scenario(c, s, L) for c in self.scenarios()]
        verdicts = {o[0] for o in outs}
        ks = sorted({k for o in outs for k in o[3]})
        deltas = [d for o in outs for d in o[4]]
        ms = [o[5] for o in outs if o[5] is not None]
        worst = max(deltas, key=abs) if deltas else None
        rec.update(k=ks, delta=[round(float(min(deltas)), 3), round(float(max(deltas)), 3)] if deltas else None,
                   phase=round(float(worst) * float(np.median(ms)), 1) if worst is not None and ms else None, ring=self.ring_count(s, L))
        if len(verdicts) > 1 or (verdicts == {"refuted"} and len(ks) > 1):
            whys = "; ".join(sorted({f"{o[0]}: {o[2]}" for o in outs}))
            return {**rec, "verdict": "uncertain", "code": "bounds", "why": f"what the clip does not show leaves it open: {whys}"}
        verdict, code, why = outs[0][0], outs[0][1], outs[0][2]
        if verdict == "two" and rec["ring"] is not None and rec["ring"] != 2:
            return {**rec, "verdict": "uncertain", "code": "ring",
                    "why": f"two steps in the clip, but the loop shows {rec['ring']} crossings: the wrap adds or hides one"}
        return {**rec, "verdict": verdict, "code": code, "why": why}

    def in_phase(self, judged: dict[str, Any]) -> bool:
        """A two whose wrap joins the same point of the walk, within PHASE_TOLERANCE frames."""
        return judged["verdict"] == "two" and judged.get("phase") is not None and abs(judged["phase"]) <= PHASE_TOLERANCE

    def lengths(self, lo: int, hi: int) -> range:
        """The cut lengths in [lo, hi] that can cover two steps: two steps in the step range, the wrap's delta in its band."""
        if self.step is None:
            return range(0)
        return range(max(lo, math.floor((2 + SEAM_BAND[0]) * self.step / ROOT2)), min(hi, self.n, math.ceil((2 + SEAM_BAND[1]) * self.step * ROOT2)) + 1)

    def twos(self, lo: int, hi: int) -> list[dict[str, Any]]:
        """Every cut [lo, hi] frames long that covers two steps (`judge`), by length then start."""
        out = []
        for L in self.lengths(lo, hi):
            for s in range(self.n - L + 1):
                judged = self.judge(s, L)
                if judged["verdict"] == "two":
                    out.append(judged)
        return out


def read(sig: dict[str, list[float]]) -> Reading:
    """The crossings of the clip whose `foot_signals` are `sig`."""
    gap = sig["gap"]
    n = len(gap)
    if float(np.mean([g >= legs_mod.FEET_APART for g in gap])) >= legs_mod.FEET_APART_SHARE:
        together = _together(gap)
        runs = _runs(together)
        if not runs or any(a == 0 and b == n - 1 for a, b in runs):
            return Reading(n, "gap", [], runs=runs, together=together)
        crossings, head_limit, tail_limit, unconfirmed = [], -0.5, None, []
        for i, (a, b) in enumerate(runs):
            if a > 0 and b < n - 1:
                crossings.append(((a + b) / 2, (a + b) / 2))
                continue
            twin = runs[i + 2] if a == 0 and i + 2 < len(runs) else runs[i - 2] if b == n - 1 and i >= 2 else None
            seen = twin[1] - twin[0] + 1 if twin is not None and twin[0] > 0 and twin[1] < n - 1 else 0
            if a == 0 and seen >= b + 1 and _moves_like(sig, (0, b), (twin[1] - b, twin[1])):
                crossings.append(((2 * b + 1 - seen) / 2, b / 2))
            elif a == 0:
                head_limit = b / 2
                unconfirmed.append((a, b))
            elif seen >= n - a and _moves_like(sig, (a, n - 1), (twin[0], twin[0] + n - 1 - a)):
                crossings.append(((a + n - 1) / 2, (2 * a + seen - 1) / 2))
            else:
                tail_limit = (a + n - 1) / 2
                unconfirmed.append((a, b))
        return Reading(n, "gap", crossings, head_limit=head_limit, tail_limit=tail_limit, runs=runs, together=together,
                       unconfirmed=unconfirmed)
    sides, pending = _sides(sig["lean"])
    unseen = int(np.argmax(sides != 0)) if np.any(sides != 0) else n
    crossings = [(k - .5, k - .5) for k in range(max(1, unseen + 1), n) if sides[k] != sides[k - 1]]
    unconfirmed = ([(0, unseen - 1)] if unseen > 0 else []) + ([(pending, n - 1)] if pending is not None else [])
    reading = Reading(n, "sign", crossings, head_limit=max(-0.5, unseen - 0.5), tail_limit=None if pending is None else pending - 0.5,
                      sides=[int(x) for x in sides], unconfirmed=unconfirmed)
    if reading.readable:
        # The lean of feet stepping turns over every step; a body swaying or wobbling on another beat does not.
        alternation = correlation_on(sig["lean"], reading.step)
        reading.alternation = None if alternation is None else round(alternation, 3)
        if alternation is None or alternation > ALTERNATION_MAX:
            reading.why = (f"the foot band's lean does not turn over a step on (correlation {alternation:+.2f} at "
                           f"{reading.step:g} frames, over {ALTERNATION_MAX:+.2f}): not feet stepping" if alternation is not None
                           else "the foot band's lean does not move")
    return reading


def correlation_on(values: list[float], span: float) -> float | None:
    """A per-frame signal's correlation `span` frames on (its trend taken out), the larger at the two whole lags about
    `span`: the lean's one step on (`read`), a foot cue's one interval on, and the body's top line less its flow half
    and a quarter of an interval on (`local_cycle.read_cycles`)."""
    x = np.asarray(values, dtype=float)
    t = np.arange(len(x))
    x = x - np.polyval(np.polyfit(t, x, 1), t)
    out = []
    for lag in sorted({math.floor(span), math.ceil(span)}):
        a, b = x[:-lag], x[lag:]
        if lag < 1 or len(a) < 3 or a.std() < 1e-12 or b.std() < 1e-12:
            return None
        out.append(float(np.corrcoef(a, b)[0, 1]))
    return max(out)
