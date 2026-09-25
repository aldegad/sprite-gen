# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-loop` — cut one seamless cycle out of keyed frames and emit the
loop as cycle frames, a horizontal strip (+ metadata), a transparent GIF and a WebP.

Period first, seam second. A single-start "most similar later frame" search lands
on the 1.5-cycle look-alike of a gait (legs swapped) and produces a loop that
hitches at the wrap (2026-09-08 실측: side walk picked 39 frames where the period
was 28, run picked 25 where it was 17). So the true period is read from the
GLOBAL profile `P[L] = mean_j |f[j] - f[j+L]|` — its deepest local minimum inside
the state's window — and only then is the start chosen as the best seam for that
period. Gaits also compare the motion around the two cycle boundaries so an
accidental single-frame seam cannot outrank a coherent repeat. Idle motion is
tiny and not strictly periodic: its window is opened to
most of the clip, where the seam is lowest.

Everything downstream is measured, never assumed: the seam ratio (wrap distance
over the mean adjacent-frame distance inside the cycle) gates the run, the GIF and
WebP are re-opened and verified (frame count, loop flag, transparent corners,
no stale RGB under alpha 0). The strip metadata carries `body_h` — the standing
body height (median per-frame bbox height) — so a consumer can scale a jump strip,
whose cells include air room, to the same on-screen body size as a walk strip.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.video import motion_anchor, auto_motion, local_cycle

ANALYSIS_SIZE = 96  # thumbnail edge for the distance matrix
STRIP_MAX_CELLS = 64  # upper bound on cells even when they are narrow
STRIP_MAX_WIDTH = 32000  # Chrome refuses images wider than ~32767 px; the cap is on PIXELS — a 650 px cell allows only 49 cells (2026-09-09 wolf idle: 64 cells = 41,664 px, unrenderable)
STRIP_MAX_HEIGHT = 520
SEAM_RATIO_MAX = 2.0  # loop seam / mean adjacent distance inside the cycle
SPECK_MIN_FRACTION = 0.01  # detached components smaller than this fraction of the body are keying specks
PERIODICITY_MIN = 0.15  # the period must dip at least 15% below the profile mean (flat profile = no repeat)
GIF_FPS_DEFAULT = 24.0  # GIF/WebP playback density = the source rate: every cycle frame is kept, a fast action never looks slow (12 made a jump read sluggish, 2026-09-09)
GIF_FRAMES_MIN = 4
ONE_SHOT_MIN_CONTRAST = 3.0  # one-shot: the excursion peak must stand this far above the rest-pose noise (in MADs)
# Endpoint-relative departure in units of mean subject pixel mass. Contrast against
# the whole distance profile can miss a held action, so mass is a second explicit
# acceptance rule. Sustained excursion and departure/step gates reject stand jitter.
ONE_SHOT_MIN_MOVED = 0.4
ONE_SHOT_MIN_COHERENCE = 3.0  # departure must exceed three ordinary playback steps
ONE_SHOT_MIN_ACTIVE = 4  # isolated keying/jitter spikes are not a performed action
ONE_SHOT_PAD = 2  # one-shot: rest frames kept on each side of the excursion so the seam is rest -> rest
CYCLE_MODES = ("auto", "periodic", "one-shot", "fixed")
ONE_SHOT_MIN_LEN = 4  # a one-shot's length is the clip's own fact; only a degenerate cut is refused
# Gait half-period guard. A walk cycle is two steps; when the two halves look alike in
# pixels (legs hidden by a costume, near/far leg not distinguishable) the half period dips
# as deep as — or deeper than — the full one, and the "smallest minimum within 15 %"
# rule picks one step. Pixels cannot settle that case, so a duration prior does: a gait
# period shorter than the state's floor is treated as one step, and the doubled period is
# taken when it repeats about as well. The cost is asymmetric — a wrongly doubled cycle
# is still a clean two-cycle loop, a halved one walks on one leg.
GAIT_DOUBLE_TOL = 0.25
GAIT_DOUBLE_SEARCH = 3  # frames either side of 2x the step where the full gait's own minimum may sit
GAIT_NEAR_EXACT_STEP_FRACTION = 0.10  # no ambiguity extension when repeat error is tiny compared with a playback step
ANCHOR_MODES = ("none", "feet", "body", "motion", "motion-auto")
BODY_ANCHOR_BAND = 0.6  # --anchor body reads the wrap offset from the top 60 % of the first frame's box: head and torso, not the legs
BODY_ANCHOR_SEARCH = 24  # px either side searched for the last frame's horizontal offset against the first
FOOT_BAND = 0.08  # fraction of the frame's own height, measured up from its lowest opaque row


# A gait's period is a fact about the body, not about how long the clip runs: the
# same walker takes the same ~1 s stride in a 3 s clip and a 6 s clip. A window cut
# from the clip length (the fractions below) shrank with the clip and, at 3 s, put a
# 1.0-1.3 s stride above the walk ceiling (measured 2026-09-18: one of four walks refused
# outright, another cut at a half step). Gait states therefore take their window in
# seconds — physical bounds on how fast and how slow a locomotion cycle can be — capped
# above by half the clip, the most a detector can *confirm* repeats (a cycle must be seen
# twice to be a cycle at all). The ceiling is a bound in seconds and not simply "half
# the clip" because the periodicity gate compares the period's dip with the profile's
# mean over the whole window: a ceiling that grows with the clip drags in long, sparsely
# sampled lags, inflates that mean, and let a clip with ONE hop and a jittering stand
# pass as a walk (measured 2026-09-20 on the one-shot fixture: 0.23 with a half-clip
# ceiling, 0.03 with 1.6 s). The half-period guard (`min_seconds`) is unchanged.


@dataclass(frozen=True)
class LoopProfile:
    min_frac: float  # window as a fraction of the clip's frame count (non-gait states)
    max_frac: float
    why: str
    periodic: bool = True  # gate: the profile must show a real period (idle is exempt)
    one_shot_ok: bool = False  # the state is an action that may legitimately happen once (jump, attack) -> one-shot detector may take over
    gait: bool = False  # locomotion: a period must hold two steps, so the half-period guard applies
    min_seconds: float = 0.0  # gait floor: a detected period shorter than this is treated as one step
    window_seconds: tuple[float, float] | None = None  # gait states: period bounds in seconds (see above)

    action_seconds: float | None = None  # partial repeats need observed context, not a clip fraction

    def window(self, n: int, fps: float) -> tuple[int, int]:
        """Period-length window [lo, hi] in frames for a clip of `n` keyed frames.

        Gait states: `window_seconds`, the ceiling capped at half the clip. Everything
        else uses clip-length fractions, except attack: its action-time ceiling retains
        actual comparison context and uses a coverage-dependent periodicity floor)."""
        if self.action_seconds is not None:
            lo = max(4, round(n * self.min_frac))
            context = max(8, math.ceil(fps * 0.5))
            return lo, min(round(self.action_seconds * fps), n - context)
        if self.window_seconds is not None:
            lo = max(4, round(self.window_seconds[0] * fps))
            return lo, max(lo + 2, min(n // 2, round(self.window_seconds[1] * fps)))
        lo = max(4, round(n * self.min_frac))
        return lo, max(lo + 2, round(n * self.max_frac))


# State -> detection window. Non-gait profiles are fractions of the clip length so 6 s and
# 10 s clips both work; gait profiles use `window_seconds` (see above). Walk: measured
# strides 0.58-1.5 s (2026-09-09..20, seven bodies); run: 0.67-0.8 s.
STATE_PROFILES: dict[str, LoopProfile] = {
    "idle": LoopProfile(0.60, 0.95, "breathing is slow and not strictly periodic; the lowest seam is a long window", periodic=False),
    "walk": LoopProfile(0.06, 0.31, "full gait = two steps; the floor admits a legless body's fast bounce (~13 frames at 24 fps) — the 15% depth rule rejects the one-step half period when the near/far leg shows, and the gait floor (min_seconds) catches it when it does not", gait=True, min_seconds=0.6, window_seconds=(0.5, 1.6)),
    "run": LoopProfile(0.07, 0.23, "faster gait", gait=True, min_seconds=0.35, window_seconds=(0.3, 1.2)),
    "jump": LoopProfile(0.11, 0.45, "crouch-spring-land-return", one_shot_ok=True),
    "attack": LoopProfile(0.11, 0.45, "swing and return to ready; retain at least half a second of repeat context", one_shot_ok=True, action_seconds=2.5),
    "default": LoopProfile(0.10, 0.45, "generic in-place action", one_shot_ok=True),
}


def profile_for(state: str | None) -> LoopProfile:
    return STATE_PROFILES.get((state or "").strip().lower(), STATE_PROFILES["default"])


def _load_small(path: Path) -> np.ndarray:
    return _small_features(Image.open(path).convert("RGBA"))


def _small_features(image: Image.Image) -> np.ndarray:
    im = image.copy()
    im.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE))
    a = np.asarray(im, dtype=np.float32) / 255.0
    rgb = a[..., :3] * a[..., 3:4]  # premultiplied: transparent pixels contribute 0
    return np.concatenate([rgb, a[..., 3:4]], axis=-1).reshape(-1)


def frame_masses(files: list[Path]) -> np.ndarray:
    """Per-frame pixel mass of the analysis thumbnail (mean |premultiplied rgba|): how much
    subject there is, on the same scale as the distance matrix."""
    return np.array([float(np.abs(_load_small(f)).mean()) for f in files], dtype=np.float32)


def distance_matrix(files: list[Path]) -> np.ndarray:
    flat = np.stack([_load_small(f) for f in files])
    n = len(files)
    D = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        D[i] = np.abs(flat - flat[i]).mean(axis=1)
    return D


def _repeat_context(D: np.ndarray, start: int, length: int, step: float) -> dict[str, Any]:
    """Compare a neighbourhood at the cut with the same poses one cycle later.

    A quarter-cycle on either side samples half a cycle of motion instead of one
    coincident pose. At a clip edge only real pairs participate; no padding or
    synthetic wrap is evidence of repetition. Normalise by the candidate's
    playback step so the cost is independent of character size and contrast.
    This measures temporal consistency, not limb identity.
    """
    radius = max(1, length // 4)
    first = max(0, start - radius)
    stop = min(len(D) - length, start + radius + 1)
    error = float(np.mean([D[j, j + length] for j in range(first, stop)]))
    return {
        "context_pair_range": [first, stop],  # half-open source indices
        "context_repeat_error": error,
        "context_repeat_over_step": error / step if step > 0 else math.inf,
    }


def detect_cycle(D: np.ndarray, *, min_len: int, max_len: int, gait_floor: int | None = None) -> dict[str, Any]:
    """Global period, then a wrap-compatible start with coherent gait context.

    `gait_floor` (frames) turns on the half-period guard: a period below it is one step
    of a two-step gait, so the doubled period is taken when it repeats about as well
    (see GAIT_DOUBLE_TOL). Above the floor, ambiguous non-exact harmonics may also
    retain two phase occurrences; the report flags that decision for visual review.
    Gait starts balance the wrap step with observed repetition around the cut;
    other states keep their wrap-only ranking."""
    n = D.shape[0]
    max_len = min(max_len, n - 2)
    if min_len < 2 or max_len < min_len:
        raise SystemExit(f"video-loop: window [{min_len}, {max_len}] is empty for {n} frames")
    adjacent = np.array([D[i, i + 1] for i in range(n - 1)])
    prof: dict[int, float] = {}
    for L in range(max(2, min_len // 2), max_len + 1):
        prof[L] = float(np.mean([D[j, j + L] for j in range(0, n - L, 2)]))
    cands = [L for L in range(min_len, max_len + 1) if L - 1 in prof and L + 1 in prof and prof[L] <= prof[L - 1] and prof[L] <= prof[L + 1]]
    if not cands:
        cands = list(range(min_len, max_len + 1))
    # The true period is the SMALLEST minimum that is about as deep as the deepest one:
    # exact repeats also dip at 2x and 3x the period, and a half-period look-alike dips
    # noticeably less (near/far limb difference) — so a 15% depth tolerance separates both.
    deepest = min(prof[L] for L in cands)
    period = min(L for L in cands if prof[L] <= deepest * 1.15 + 1e-4)  # abs floor: exact repeats sit at ~0
    guard: dict[str, Any] = {"applied": False}
    if gait_floor is not None and period < gait_floor:
        # A period under the gait floor is one step, not a gait. The full gait is the
        # repeat at about twice that; the profile's own minimum near 2x (within
        # GAIT_DOUBLE_SEARCH) is the candidate, since a real cycle rarely lands on
        # exactly 2p. If no such repeat exists, the clip holds one step only — a
        # quadruped whose near and far legs read alike does this — and keeping the
        # step would loop half a stride without a word. Refuse instead, under the
        # same words as a flat profile, so a caller's regenerate-on-no-period rule
        # covers both.
        doubles = [L for L in cands if abs(L - 2 * period) <= GAIT_DOUBLE_SEARCH and L <= max_len]
        if not doubles:
            doubles = [L for L in (2 * period - 1, 2 * period, 2 * period + 1) if L in prof and L <= max_len]
        L2 = min(doubles, key=lambda L: prof[L]) if doubles else None
        if L2 is not None and prof[L2] <= prof[period] * (1 + GAIT_DOUBLE_TOL) + 1e-4:
            guard = {"applied": True, "from": period, "to": L2, "gait_floor": gait_floor, "depth_ratio": round(prof[L2] / prof[period], 3) if prof[period] > 0 else None}
            period = L2
        else:
            guard = {"applied": False, "below_floor": period, "gait_floor": gait_floor,
                     "double_candidate": L2, "double_depth_ratio": round(prof[L2] / prof[period], 3) if L2 is not None and prof[period] > 0 else None,
                     "why": "the only repeat is one step: nothing near twice the period repeats about as well"}
            exc = SystemExit(
                f"video-loop: no periodic cycle found — the only repeat is one step ({period} frames, under the gait floor of "
                f"{gait_floor}) and nothing near twice that ({2 * period - GAIT_DOUBLE_SEARCH}..{2 * period + GAIT_DOUBLE_SEARCH}) "
                f"repeats within {round(GAIT_DOUBLE_TOL * 100)} % of it"
                + (f" (best {L2}: {round(prof[L2] / prof[period], 2)}x worse)" if L2 is not None and prof[period] > 0 else "")
                + "; the clip shows a half stride, regenerate it"
            )
            exc.diagnostics = {"kind": "periodic", "period_global": period, "half_period_guard": guard,
                               "profile_minima": [[L, round(prof[L], 5)] for L in sorted(cands, key=lambda L: prof[L])[:6]]}
            raise exc
    # A plausible duration does not prove that a gait contains both phases. If a
    # second local minimum at twice the period is similarly good, retain both
    # occurrences at the original fps. This is a conservative ambiguity policy,
    # not an anatomical inference: a true short cycle may be shown twice.
    # Do it only once, inside the requested window, and leave near-exact repeats
    # alone. The duration-floor guard above still owns implausibly short beats.
    profile_mean = float(np.mean([prof[L] for L in prof]))
    review_recommended = False
    if gait_floor is not None and period >= gait_floor and not guard["applied"]:
        ordinary_step = float(adjacent.mean())
        repeat_fraction = prof[period] / ordinary_step if ordinary_step > 0 else math.inf
        doubles = [L for L in cands if abs(L - 2 * period) <= 1]
        if doubles and repeat_fraction > GAIT_NEAR_EXACT_STEP_FRACTION:
            L2 = min(doubles, key=lambda L: prof[L])
            if (prof[L2] <= prof[period] * (1 + GAIT_DOUBLE_TOL) + 1e-4
                    and profile_mean > 0
                    and (profile_mean - prof[L2]) / profile_mean >= PERIODICITY_MIN):
                guard = {
                    "applied": True, "from": period, "to": L2,
                    "gait_floor": gait_floor, "reason": "ambiguous-harmonic",
                    "depth_ratio": round(prof[L2] / prof[period], 3),
                    "repeat_error_over_step": round(repeat_fraction, 3),
                    "why": "both periods are plausible; retain two phase occurrences at source speed",
                }
                period = L2
                review_recommended = True
    periodicity = (profile_mean - prof[period]) / profile_mean if profile_mean > 0 else 0.0
    # Score the last displayed frame -> first frame transition against an ordinary
    # playback step. Minimising distance alone rewards a repeated pose (a stall).
    # Log distance penalises steps that are too short or too long symmetrically.
    best: dict[str, Any] | None = None
    best_score = math.inf
    candidates = []
    for L in (period - 1, period, period + 1):
        if L < min_len or L > max_len:
            continue
        for i in range(0, n - L):
            seam = float(D[i + L - 1, i])
            inner = float(adjacent[i : i + L - 1].mean())
            ratio = seam / inner if inner > 0 else math.inf
            score = abs(math.log(ratio)) if ratio > 0 else math.inf
            candidates.append({"start": i, "length": L, "seam": seam, "inner_mean_adjacent": inner, "ratio": ratio})
            selection = None
            if gait_floor is not None:
                selection = _repeat_context(D, i, L, inner)
                selection["method"] = "repeat-context-and-wrap"
                selection["wrap_log_error"] = score
                score += selection["context_repeat_over_step"]
                selection["score"] = score
            if best is None or score < best_score:
                best_score = score
                best = {"start": i, "length": L, "seam": seam, "inner_mean_adjacent": inner,
                        "ratio": ratio,
                        "next_frame_distance": float(D[i, i + L]) if i + L < n else None}
                if selection is not None:
                    best["selection"] = selection
    assert best is not None
    best["candidates"] = candidates
    best["repeat_pairs"] = n - period
    best["profile_sample_pairs"] = len(range(0, n - period, 2))
    best["period_global"] = period
    best["half_period_guard"] = guard
    best["review_recommended"] = review_recommended
    best["periodicity"] = round(periodicity, 4)  # how far below the profile mean the period dips (0 = flat = no period)
    best["profile_minima"] = [[L, round(prof[L], 5)] for L in sorted(cands, key=lambda L: prof[L])[:6]]
    return best


class CycleSelectionError(SystemExit):
    """A selection refusal with measurements that survive into the failure report."""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


def periodicity_floor(n: int, period: int, *, partial_repeat: bool) -> float:
    """Require stronger evidence when fewer than one full period can be compared.

    Full-cycle coverage keeps the existing 15% floor. Missing coverage increases
    the required dip linearly toward 100%; one or two lucky pairs cannot prove
    a repeat. This is a conservative coverage policy, not a confidence interval.
    """
    coverage = min(1.0, max(0, n - period) / period)
    return PERIODICITY_MIN + (1 - PERIODICITY_MIN) * (1 - coverage) if partial_repeat else PERIODICITY_MIN


def detect_one_shot(D: np.ndarray, *, min_len: int, max_len: int, frame_mass: np.ndarray | None = None) -> dict[str, Any]:
    """Find an observed return pair enclosing a complete excursion.

    A global medoid can be the held strike instead of ready. Enumerate close
    endpoint poses, measure departure from both, and require the peak's entire
    active component to have observed rest on BOTH sides. A clipped component
    at frame zero or n-1 is never repaired by padding. The existing contrast /
    moved-mass floors still reject stand jitter; return distance must additionally
    be at most a quarter of the departure. No seam threshold is relaxed.
    """
    n = len(D)
    adjacent = np.diag(D, 1)
    candidates = []
    max_moved = 0.0
    for start in range(n - ONE_SHOT_MIN_LEN + 1):
        for end in range(start + ONE_SHOT_MIN_LEN - 1, min(n, start + max_len)):
            seam = float(D[start, end])
            e = (D[start] + D[end]) / 2
            peak_at = start + int(np.argmax(e[start:end + 1]))
            peak = float(e[peak_at])
            departure = peak - seam / 2
            if departure <= 0 or seam > departure * 0.25:
                continue
            med = float(np.median(e))
            mad = float(np.median(np.abs(e - med))) or 1e-6
            contrast = (peak - med) / mad
            mass = float((frame_mass[start] + frame_mass[end]) / 2) if frame_mass is not None else 0
            moved = departure / mass if mass > 0 else None
            max_moved = max(max_moved, moved or 0)
            if contrast >= ONE_SHOT_MIN_CONTRAST:
                rule = "contrast"
                active = e > med + ONE_SHOT_MIN_CONTRAST * mad
            elif moved is not None and moved >= ONE_SHOT_MIN_MOVED:
                rule = "moved"
                active = e > seam / 2 + 0.5 * departure
            else:
                continue
            if not active[peak_at]:
                continue
            a = b = peak_at
            while a > 0 and active[a - 1]:
                a -= 1
            while b + 1 < n and active[b + 1]:
                b += 1
            if b - a + 1 < ONE_SHOT_MIN_ACTIVE:
                continue
            if not (start <= a - ONE_SHOT_PAD and end >= b + ONE_SHOT_PAD):
                continue
            inner = float(adjacent[start:end].mean())
            if inner <= 0 or departure < ONE_SHOT_MIN_COHERENCE * inner:
                continue
            candidates.append({
                "excursion_over_step": departure / inner,
                "kind": "one-shot", "start": start, "length": end - start + 1,
                "seam": seam, "inner_mean_adjacent": inner,
                "ratio": seam / inner if inner > 0 else math.inf,
                "period_global": None, "periodicity": None,
                "rest_frame": start, "return_pair": [start, end],
                "return_distance_over_departure": seam / departure,
                "excursion": [a, b], "excursion_peak": peak_at,
                "excursion_contrast": round(contrast, 2),
                "excursion_moved": round(moved, 3) if moved is not None else None,
                "excursion_rule": rule, "departure": departure,
            })
    if not candidates:
        raise CycleSelectionError(
            f"video-loop: no complete one-shot return — the clip never leaves its rest pose "
            f"and returns with observed endpoints (moved {max_moved:.2f}, "
            f"need {ONE_SHOT_MIN_MOVED})",
            {"kind": "one-shot", "candidates": []},
        )
    # Keep the full strongest action, then minimise excess rest. Do not choose a
    # tiny low-seam twitch just because its endpoints happen to match exactly.
    strongest = max(c["departure"] for c in candidates)
    eligible = [c for c in candidates if c["departure"] >= strongest * 0.95]
    best = min(eligible, key=lambda c: (c["length"], c["ratio"], c["start"]))
    return {**best, "candidates": candidates}


def fixed_cycle(D: np.ndarray, *, start: int, length: int) -> dict[str, Any]:
    """An explicitly requested cut (`--cycle fixed --start N --length L`): no detection, no
    periodicity gate — the caller says which frames are the cycle and the report says so
    (kind = "fixed"). The seam is still measured and the seam gate still applies."""
    n = D.shape[0]
    if start < 0 or length < 2 or start + length > n:
        raise SystemExit(f"video-loop: --start {start} --length {length} does not fit {n} frames")
    end = start + length - 1
    adjacent = np.array([D[i, i + 1] for i in range(start, end)])
    inner = float(adjacent.mean())
    seam = float(D[start, end])
    return {"kind": "fixed", "start": start, "length": length, "seam": seam, "inner_mean_adjacent": inner,
            "ratio": seam / inner if inner > 0 else math.inf, "period_global": None, "periodicity": None}


def _drop_specks(image: Image.Image, min_fraction: float) -> tuple[Image.Image, int]:
    """Erase detached alpha components smaller than `min_fraction` of the largest one."""
    a = np.asarray(image)[..., 3] > 16
    H, W = a.shape
    seen = np.zeros_like(a, dtype=bool)
    comps: list[list[tuple[int, int]]] = []
    for y in range(H):
        for x in range(W):
            if a[y, x] and not seen[y, x]:
                q = deque([(y, x)])
                seen[y, x] = True
                pts: list[tuple[int, int]] = []
                while q:
                    cy, cx = q.popleft()
                    pts.append((cy, cx))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < H and 0 <= nx < W and a[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
                comps.append(pts)
    if not comps:
        return image, 0
    big = max(len(c) for c in comps)
    px = image.load()
    dropped = 0
    for c in comps:
        if len(c) < max(8, big * min_fraction):
            for y, x in c:
                px[x, y] = (0, 0, 0, 0)
            dropped += 1
    return image, dropped


def _scrub(image: Image.Image) -> int:
    px = image.load()
    n = 0
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = px[x, y]
            if a == 0 and (r or g or b):
                px[x, y] = (0, 0, 0, 0)
                n += 1
    return n


def foot_centre(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean x of the opaque pixels in the lowest FOOT_BAND of the subject's own bbox —
    the ground-contact line, which is what a placed sprite is judged by."""
    a = np.asarray(image.getchannel("A"))
    band = max(1, round((box[3] - box[1]) * FOOT_BAND))
    rows = a[box[3] - band : box[3], box[0] : box[2]]
    ys, xs = np.nonzero(rows >= 8)
    if xs.size == 0:
        return (box[0] + box[2]) / 2
    return float(box[0] + xs.mean())


def body_centre(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean x of every opaque pixel — the whole body's mass, which a gait swings far less
    than it swings the foot line."""
    a = np.asarray(image.getchannel("A"))[box[1] : box[3], box[0] : box[2]]
    xs = np.nonzero(a >= 8)[1]
    return float(box[0] + xs.mean()) if xs.size else (box[0] + box[2]) / 2


def drift_reference(frames: list[Image.Image], boxes: list[tuple[int, int, int, int]]) -> tuple[list[float], float, float]:
    """Per-frame alignment reference for `--anchor feet`.

    Drift is a slow translation; a gait is periodic. The cycle holds whole periods, so a
    straight line fitted to the body's centre across it carries the drift and not the
    step. Only that line is removed: the reference is the mean foot line riding the
    drift. Pinning each frame's own foot line instead pins the planted foot — which
    changes every step — and makes a body that stood still lurch by the stride.
    Returns (reference x per frame, drift removed in px, foot-line sway in px)."""
    feet = np.array([foot_centre(im, b) for im, b in zip(frames, boxes)])
    centres = np.array([body_centre(im, b) for im, b in zip(frames, boxes)])
    t = np.arange(len(frames), dtype=float)
    slope = float(np.polyfit(t, centres, 1)[0]) if len(frames) > 1 else 0.0
    trend = slope * t
    ref = float(np.mean(feet - trend)) + trend
    return [float(r) for r in ref], abs(slope * (len(frames) - 1)), float(np.ptp(feet - trend))


def body_wrap_offset(frames: list[Image.Image], *, band: float = BODY_ANCHOR_BAND, search: int = BODY_ANCHOR_SEARCH) -> int:
    """Horizontal offset (px) that best lays the LAST frame's head and torso over the FIRST's.

    A gait clip drifts a few pixels over one cycle, and the last-to-first wrap shows that
    drift as a sideways jump. The legs are mid-stride and the tail mid-swing at both ends,
    so they cannot say where the body is; the top of the first frame's box (head, torso,
    `band` of its height) can. Whole-pixel registration over that region, over a small
    horizontal search, is the one measurement `--anchor body` makes — it is then spread
    as a ramp across the cycle, never applied frame by frame (per-frame fitting turns a
    head bob into a full-body shiver, measured 2026-09-22)."""
    if len(frames) < 2:
        return 0
    first = np.asarray(frames[0], dtype=np.float32)
    last = np.asarray(frames[-1], dtype=np.float32)
    alpha = first[:, :, 3] >= 8
    rows = np.where(alpha.any(axis=1))[0]
    cols = np.where(alpha.any(axis=0))[0]
    if len(rows) == 0 or len(cols) == 0:
        return 0
    top, bottom, left, right = rows[0], rows[-1], cols[0], cols[-1]
    mask = np.zeros_like(alpha)
    mask[top : top + max(1, int((bottom - top) * band)), left : right + 1] = True
    ref = first[mask]

    def cost(dx: int) -> float:
        return float(np.abs(ref - np.roll(last, dx, axis=1)[mask]).mean())

    return min(range(-search, search + 1), key=cost)


def ramp_frames(frames: list[Image.Image], wrap_dx: int) -> list[Image.Image]:
    """Shift frame k by round(wrap_dx * k / L) so the cycle's end returns to its start."""
    L = len(frames)
    if wrap_dx == 0 or L < 2:
        return frames
    out = []
    for k, im in enumerate(frames):
        dx = int(round(wrap_dx * k / L))
        if dx == 0:
            out.append(im)
            continue
        shifted = Image.new("RGBA", im.size, (0, 0, 0, 0))
        shifted.paste(im, (dx, 0))
        out.append(shifted)
    return out


def first_frame_height(path: Path) -> int:
    """The subject's height in the clip's first frame: the base still's pose as filmed.

    Every clip starts from its still (image-to-video), so this is the same pose in every
    state of one character — which is what one `--body-height` across states has to measure
    to give that character one size.
    """
    box = Image.open(path).convert("RGBA").getchannel("A").point(lambda v: 255 if v >= 8 else 0).getbbox()
    if box is None:
        raise SystemExit(f"video-loop: {path.name}, the clip's first frame, has no subject to measure the standing height on (--body-height)")
    return box[3] - box[1]


def build_strip(frames: list[Image.Image], *, max_cells: int = STRIP_MAX_CELLS, max_height: int = STRIP_MAX_HEIGHT, max_width: int = STRIP_MAX_WIDTH, cycle_seconds: float, body_height: int | None = None, anchor: str = "none", kind: str = "periodic", standing_src: int | None = None) -> tuple[Image.Image, dict[str, Any]]:
    """Union-crop (no bottom pad so feet meet the floor), scale, bottom-align, tile horizontally.

    The cell count is capped by the strip's PIXEL width (`max_width`) as well as by
    `max_cells`: the crop is measured over every cycle frame first, then as many evenly
    spaced frames as fit are kept. Subsampling is recorded in the meta, never silent."""
    L = len(frames)
    boxes = [im.getchannel("A").point(lambda v: 255 if v >= 8 else 0).getbbox() for im in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        raise SystemExit("video-loop: every cycle frame is fully transparent")
    # The side margins may reach past the frame: a subject allowed to touch the left or right
    # edge (video-frames --allow-subject-edge-contact) still gets its 8 transparent columns,
    # so no cell corner is ever the subject. `crop` fills outside the frame with alpha 0.
    left = min(b[0] for b in boxes) - 8
    top = max(0, min(b[1] for b in boxes) - 8)
    right = max(b[2] for b in boxes) + 8
    bottom = max(b[3] for b in boxes)
    # body_h = the STANDING height: the tallest frame whose feet touch the floor. A median
    # over the whole cycle undercounts a jump (crouch + airborne frames dominate) and then
    # over-scales it — the 2026-09-09 hero read 22 % taller than the walk beside it.
    floor = max(b[3] for b in boxes)
    grounded = [b[3] - b[1] for b in boxes if b[3] >= floor - 4] or [b[3] - b[1] for b in boxes]
    # `standing_src` is the caller's own measurement of the standing pose (run_loop: the clip's
    # first frame). The tallest grounded frame counts whatever is raised overhead — an attack's
    # windup lifts the weapon above the head — and would shrink that state against the others.
    body_src = standing_src if standing_src is not None else max(grounded)
    # max_height is a ceiling on the CELL; an explicit body_height is a target for the BODY.
    # Without one, nothing is ever scaled up. With one, a state whose source body is SHORTER
    # than the request has to grow — clamping to 1.0 first made the option a downward clamp
    # only, so "the same value across states gives the same character size" was false for
    # exactly the states that needed it: a 200 px source and a 400 px source both asked for
    # 300 came out 200 and 300 (measured 2026-09-10). The cap still wins over the target.
    fit = max_height / (bottom - top)
    scale = min(1.0, fit) if body_height is None else min(fit, body_height / body_src)
    # --anchor feet: the model may have walked the subject across an "in-place" canvas;
    # a union crop keeps that drift inside every cell. Remove the drift (see
    # drift_reference) so every cell stands on the same mean foot line, and report it.
    feet, drift, sway = drift_reference(frames, boxes) if anchor == "feet" else (None, 0.0, 0.0)
    drift_px = round(drift)
    if feet:
        lead = max(fc - b[0] for fc, b in zip(feet, boxes)) + 8
        trail = max(b[2] - fc for fc, b in zip(feet, boxes)) + 8
        cell_src_w = lead + trail
    else:
        cell_src_w = right - left
    w = round(cell_src_w * scale)
    h = round((bottom - top) * scale)
    cap = max(1, min(max_cells, max_width // max(1, w)))
    idx = list(range(L))
    if L > cap:
        idx = [round(k * L / cap) for k in range(cap)]
    chosen = [frames[i] for i in idx]
    if feet:
        cells = []
        for i in idx:
            im = frames[i]
            cell = Image.new("RGBA", (round(cell_src_w), bottom - top), (0, 0, 0, 0))
            cell.alpha_composite(im.crop((0, top, im.width, bottom)), (round(lead - feet[i]), 0))
            cells.append(cell.resize((w, h), Image.LANCZOS))
    else:
        cells = [im.crop((left, top, right, bottom)).resize((w, h), Image.LANCZOS) for im in chosen]
    strip = Image.new("RGBA", (w * len(cells), h), (0, 0, 0, 0))
    for k, im in enumerate(cells):
        strip.alpha_composite(im, (k * w, 0))
    meta = {
        "frames": len(cells),
        "w": w,
        "h": h,
        # how the cycle was cut, and whether a runtime should repeat it: a one-shot
        # (rest -> action -> rest) plays once on a trigger; every other cut is a loop.
        # The spec asset loader reads `loop` from this sidecar.
        "kind": kind,
        "loop": kind != "one-shot",
        "body_h": round(body_src * scale),
        "body_src_h": body_src,  # the standing height as filmed; body_h / body_src_h > 1 means the cells were upscaled
        "body_ref": "first-frame" if standing_src is not None else "tallest-grounded",
        "scale": round(scale, 4),
        "delay_ms": round(1000 * cycle_seconds / len(cells), 2),
        "cycle_frames": L,
        "cycle_seconds": round(cycle_seconds, 4),
        "subsampled": L > cap,
        "cell_cap": cap,
        "top_margin_px": top,
        "body_height_target": body_height,
        "foot_anchor": anchor,  # "none" | "feet" — how the cells were aligned
        "drift_px": drift_px,  # source px of slow in-canvas drift removed across the cycle (0 when not measured)
        "foot_sway_px": round(sway),  # source px the planted foot moves within the gait, kept as is (0 when not measured)
        "foot_x": round(lead * scale) if feet else None,  # x of the foot line inside every cell
    }
    if feet:
        # the spec asset loader reads a sidecar `anchor` as the [x, y] pivot (default:
        # bottom-centre); with feet alignment the true pivot is the foot line at the
        # cell bottom, so declare it and scenes stand the sprite on its feet
        meta["anchor"] = [meta["foot_x"], h]
    return strip, meta


def img2webp_supports_exact(binary: str | None = None) -> bool:
    """libwebp added `-exact` to img2webp in 1.5.0 (checked against the 1.4.0 and 1.5.0 release
    binaries, 2026-09-09); older builds (Ubuntu 24.04: 1.3.x) reject the flag with "Unknown option"
    and would rewrite RGB under alpha 0. Detected from `-h`, never assumed."""
    binary = binary or shutil.which("img2webp")
    if not binary:
        return False
    try:
        proc = subprocess.run([binary, "-h"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "-exact" in (proc.stdout + proc.stderr)


def write_webp(frames: list[Image.Image], out: Path, *, delay_ms: int, workdir: Path) -> None:
    """Animated WebP through libwebp's img2webp with -exact (Pillow's animated writer drops `exact`
    and rewrites RGB under alpha 0 to white; measured 2026-09-08)."""
    img2webp = shutil.which("img2webp")
    if not img2webp:
        raise SystemExit("video-loop: `img2webp` not found on PATH — install libwebp (brew install webp) for exact-alpha WebP")
    if not img2webp_supports_exact(img2webp):
        raise SystemExit(
            "video-loop: this img2webp has no `-exact` option (libwebp < 1.5; Ubuntu 24.04 ships 1.3.x) — "
            "install libwebp >= 1.5 (brew install webp, or the official binaries from storage.googleapis.com/downloads.webmproject.org)"
        )
    workdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, im in enumerate(frames):
        p = workdir / f"webp-{k:03d}.png"
        im.save(p)
        paths.append(str(p))
    proc = subprocess.run([img2webp, "-loop", "0", "-lossless", "-exact", "-d", str(delay_ms), *paths, "-o", str(out)], capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(f"video-loop: img2webp failed: {proc.stderr.strip()[:300]}")


def verify_animation(path: Path, *, expect_frames: int, check_stale: bool) -> dict[str, Any]:
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    loop = im.info.get("loop")
    corners_ok = True
    stale = 0
    for k in range(n):
        im.seek(k)
        f = im.convert("RGBA")
        w, h = f.size
        corners_ok &= all(f.getpixel(c)[3] == 0 for c in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
        if check_stale:
            stale += sum(1 for p in f.get_flattened_data() if p[3] == 0 and (p[0] or p[1] or p[2]))
    report = {"file": path.name, "format": im.format, "n_frames": n, "loop": loop, "size": list(im.size), "corners_transparent": corners_ok, "stale_rgb_under_alpha0": stale, "bytes": path.stat().st_size}
    problems = []
    if n != expect_frames:
        problems.append(f"{n} frames, expected {expect_frames}")
    if loop != 0:
        problems.append(f"loop={loop!r}, expected 0 (infinite)")
    if not corners_ok:
        problems.append("a corner is not transparent")
    if stale:
        problems.append(f"{stale} transparent pixels carry RGB")
    if problems:
        raise SystemExit(f"video-loop: {path.name} failed verification: {'; '.join(problems)}")
    return report


def write_loop_report(target: Path, payload: dict[str, Any]) -> None:
    """Strict JSON, including refusals with undefined (zero-motion) ratios."""
    def finite(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: finite(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [finite(item) for item in value]
        return value

    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(finite(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def run_loop(
    frames_dir: Path,
    out_dir: Path,
    *,
    fps: float,
    state: str | None,
    min_len: int | None,
    max_len: int | None,
    n_out: int | None,
    seam_max: float,
    name: str,
    report_path: Path | None,
    cycle_mode: str = "auto",
    gif_fps: float = GIF_FPS_DEFAULT,
    start: int | None = None,
    length: int | None = None,
    strip_height: int = STRIP_MAX_HEIGHT,
    body_height: int | None = None,
    anchor: str | None = None,
    anchor_regions: list[motion_anchor.Box] | None = None,
) -> dict[str, Any]:
    if anchor is None:
        # gaits drift a few pixels over a cycle and the wrap shows it; in-place states do not
        anchor = "body" if profile_for(state).gait else "none"
    if anchor not in ANCHOR_MODES:
        raise SystemExit(f"video-loop: unknown --anchor {anchor!r}; expected one of {', '.join(ANCHOR_MODES)}")
    try:
        motion_anchor.validate_request(anchor, cycle_mode, length, anchor_regions)
    except ValueError as exc:
        raise SystemExit(f"video-loop: {exc}") from exc
    if anchor == "motion-auto" and (cycle_mode not in ("auto", "periodic") or not profile_for(state).gait
                                     or start is not None or length is not None):
        raise SystemExit("video-loop: --anchor motion-auto requires walk/run with automatic or periodic selection; no fixed cut")
    if cycle_mode not in CYCLE_MODES:
        raise SystemExit(f"video-loop: unknown --cycle {cycle_mode!r}; expected one of {', '.join(CYCLE_MODES)}")
    frames_dir = frames_dir.expanduser().resolve()
    files = sorted(frames_dir.glob("*.png"))
    if len(files) < 6:
        raise SystemExit(f"video-loop: need at least 6 keyed frames in {frames_dir}, found {len(files)}")
    prof = profile_for(state)
    n = len(files)
    lo_default, hi_default = prof.window(n, fps)
    lo = min_len if min_len is not None else lo_default
    hi = max_len if max_len is not None else max(lo + 2, hi_default)
    D = distance_matrix(files)
    masses = frame_masses(files)
    periodic_attempt: dict[str, Any] | None = None
    target = (report_path or (out_dir / f"{name}.loop.report.json")).expanduser().resolve()
    report_base = {
        "kind": "sprite-gen-video-loop-report", "frames_dir": str(frames_dir),
        "out_dir": str(out_dir), "state": state, "fps": fps, "frames_total": n,
        "window": [lo, hi], "profile": prof.why, "cycle_mode": cycle_mode,
        "seam_max": seam_max,
        "anchor": anchor,
        "seam_measurement": "rendered-cells" if anchor in ("motion", "motion-auto") else "source-frames",
    }
    cycle = None
    try:
        if anchor == "motion-auto":
            source_frames = [Image.open(f).convert("RGBA") for f in files]
            D, trajectory, analysis = auto_motion.analyse(source_frames, fps=fps)
            report_base["automatic_motion_analysis"] = analysis
            cycle = local_cycle.detect(
                D, trajectory, min_len=lo, max_len=hi, gait_floor=round(prof.min_seconds*fps),
                periodicity_min=PERIODICITY_MIN, double_tolerance=GAIT_DOUBLE_TOL,
                double_search=GAIT_DOUBLE_SEARCH,
            )
        elif cycle_mode == "fixed":
            if start is None or length is None:
                raise SystemExit("video-loop: --cycle fixed needs --start and --length")
            cycle = fixed_cycle(D, start=start, length=length)
        elif cycle_mode == "one-shot":
            cycle = detect_one_shot(D, min_len=lo, max_len=hi, frame_mass=masses)
        else:
            gait_floor = round(prof.min_seconds * fps) if prof.gait and prof.min_seconds > 0 else None
            cycle = detect_cycle(D, min_len=lo, max_len=hi, gait_floor=gait_floor)
            cycle["kind"] = "periodic"
            floor = periodicity_floor(n, cycle["period_global"], partial_repeat=prof.action_seconds is not None)
            cycle["periodicity_min"] = floor
            if prof.periodic and cycle["periodicity"] < floor:
                flat = (
                    f"the period profile is flat (periodicity {cycle['periodicity']:.2f} < {floor:.3f}) in window [{lo},{hi}]"
                )
                if cycle_mode == "auto" and prof.one_shot_ok:
                    # explicit, recorded failover: the action happened once (allowed for this state),
                    # so cut rest -> excursion -> rest instead. The periodic attempt stays in the report.
                    periodic_attempt = {**cycle, "window": [lo, hi], "why_rejected": flat}
                    cycle = detect_one_shot(D, min_len=lo, max_len=max(hi, round(n * 0.9)), frame_mass=masses)
                else:
                    raise SystemExit(
                        f"video-loop: no periodic cycle found — {flat}; the motion does not repeat, widen the window, "
                        "regenerate the clip, or pass --cycle one-shot for a single performed action"
                    )
    except (SystemExit, ValueError) as exc:
        write_loop_report(target, {**report_base, "status": "failed", "error": str(exc),
                                  "cycle": getattr(exc, "diagnostics", cycle),
                                  "periodic_attempt": periodic_attempt})
        if isinstance(exc, ValueError):
            raise SystemExit(f"video-loop: {exc}") from exc
        raise
    i, L = cycle["start"], cycle["length"]
    # playback density, not a fixed count: a long cycle gets more frames so every state plays at
    # ~gif_fps (a fixed 12 made a 2.5 s jump hold each frame 210 ms while a 1.1 s walk held 90 ms)
    if n_out is None:
        n_out = max(GIF_FRAMES_MIN, round(L / fps * gif_fps))
    n_out = min(n_out, L)  # never ask for more distinct frames than the cycle holds

    out_dir = out_dir.expanduser().resolve()
    cycle_dir = out_dir / "cycle"
    frames: list[Image.Image] = []
    scrubbed = specks = 0
    for k, f in enumerate(files[i : i + L]):
        im = Image.open(f).convert("RGBA")
        # Motion review is pixel preserving: cleanup belongs to the keyed input.
        if anchor not in ("motion", "motion-auto"):
            scrubbed += _scrub(im)
            im, d = _drop_specks(im, SPECK_MIN_FRACTION)
            specks += d
        frames.append(im)

    cycle_seconds = L / fps
    wrap_dx = 0
    if anchor == "body":
        wrap_dx = body_wrap_offset(frames)
        frames = ramp_frames(frames, wrap_dx)
    motion = None
    if anchor in ("motion", "motion-auto"):
        try:
            if anchor == "motion-auto":
                anchor_regions, region_report = auto_motion.discover(frames, reference_index=0)
                report_base["automatic_motion_regions"] = region_report
            correct = auto_motion.correct_cycle if anchor == "motion-auto" else motion_anchor.correct_motion
            frames, motion = correct(
                frames, anchor_regions, coarse_dx=body_wrap_offset(frames, search=motion_anchor.COARSE_SEARCH),
            )
        except ValueError as exc:
            error = f"video-loop: {exc}"
            write_loop_report(target, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error) from exc
        report_base["motion_anchor"] = motion
    cycle_dir.mkdir(parents=True, exist_ok=True)
    for old in cycle_dir.glob("frame-*.png"):
        old.unlink()
    for k, im in enumerate(frames):
        im.save(cycle_dir / f"frame-{k:03d}.png")
    strip, strip_meta = build_strip(frames, max_height=strip_height, cycle_seconds=cycle_seconds, body_height=body_height, anchor="feet" if anchor == "feet" else "none", kind=str(cycle.get("kind") or "periodic"),
                                    standing_src=first_frame_height(files[0]) if body_height is not None else None)
    if motion is not None:
        strip_meta["foot_anchor"] = anchor
        strip_meta["motion_anchor"] = motion
    if anchor == "body":
        strip_meta["foot_anchor"] = "body"
        strip_meta["wrap_dx_px"] = wrap_dx
        # the wrap as it now plays: last ramped frame -> first, against an ordinary step
        Dr = distance_matrix(sorted(cycle_dir.glob("frame-*.png")))
        inner = float(np.mean([Dr[k, k + 1] for k in range(len(frames) - 1)])) if len(frames) > 1 else 0.0
        strip_meta["seam_ratio_after_anchor"] = round(float(Dr[len(frames) - 1, 0]) / inner, 4) if inner > 0 else None
    # The GIF/WebP are cut from the strip's cells. A cycle longer than the cell cap was
    # subsampled there, so asking for more frames than cells repeats cells back to back
    # and the GIF writer merges the identical neighbours — the file then holds fewer
    # frames than requested and verification refused it (2026-09-20: a 68-frame jump cycle
    # capped at 64 cells came back as a 64-frame GIF "expected 68").
    n_out = min(n_out, int(strip_meta["frames"]))
    strip_path = out_dir / f"{name}.strip.png"
    strip.save(strip_path)
    atomic_write_text(out_dir / f"{name}.strip.json", json.dumps(strip_meta, indent=2) + "\n")

    # resampled GIF/WebP frames: same crop as the strip, n_out evenly across the cycle
    idx = [min(L - 1, i2) for i2 in (round(k * L / n_out) for k in range(n_out))]
    cells = [strip.crop((k * strip_meta["w"], 0, (k + 1) * strip_meta["w"], strip_meta["h"])) for k in range(strip_meta["frames"])]
    pick = [cells[min(len(cells) - 1, round(j * len(cells) / L))] for j in idx]
    seam_idx = [i + j for j in idx]
    resampled_adjacent = float(np.mean([D[seam_idx[k], seam_idx[k + 1]] for k in range(len(seam_idx) - 1)]))
    resampled_seam = float(D[seam_idx[-1], seam_idx[0]])
    if anchor in ("motion", "motion-auto"):
        # Gate the actual corrected/resampled strip cells, with the same limit.
        flat = np.stack([_small_features(im) for im in pick])
        resampled_adjacent = float(np.abs(flat[1:] - flat[:-1]).mean())
        resampled_seam = float(np.abs(flat[-1] - flat[0]).mean())
    seam_ratio = resampled_seam / resampled_adjacent if resampled_adjacent > 0 else math.inf
    delay_ms = max(20, round(1000 * cycle_seconds / n_out))
    gif_path = out_dir / f"{name}.gif"
    webp_path = out_dir / f"{name}.webp"
    if seam_ratio > seam_max:
        error = (
            f"video-loop: loop seam ratio {seam_ratio:.2f} exceeds {seam_max} "
            f"({cycle['kind']} length {L} frames from {i}); the cycle does not close — "
            "regenerate with a complete return to the starting pose"
        )
        write_loop_report(target, {**report_base, "status": "failed", "error": error,
                                  "cycle": cycle, "periodic_attempt": periodic_attempt,
                                  "resampled_seam": resampled_seam,
                                  "resampled_inner_mean_adjacent": resampled_adjacent,
                                  "resampled_seam_ratio": seam_ratio})
        raise SystemExit(error)
    save_clean_gif(pick, gif_path, duration_ms=delay_ms, loop=0, alpha_threshold=128)
    write_webp(pick, webp_path, delay_ms=delay_ms, workdir=out_dir / ".webp-frames")
    shutil.rmtree(out_dir / ".webp-frames", ignore_errors=True)
    gif_report = verify_animation(gif_path, expect_frames=n_out, check_stale=False)
    webp_report = verify_animation(webp_path, expect_frames=n_out, check_stale=True)

    payload = {
        **report_base,
        "kind": "sprite-gen-video-loop-report",
        "frames_dir": str(frames_dir),
        "out_dir": str(out_dir),
        "state": state,
        "fps": fps,
        "frames_total": n,
        "window": [lo, hi],
        "profile": prof.why,
        "cycle_mode": cycle_mode,
        "cycle": cycle,
        "periodic_attempt": periodic_attempt,
        "cycle_seconds": round(cycle_seconds, 4),
        "n_out": n_out,
        "gif_fps": gif_fps,
        "delay_ms": delay_ms,
        "status": "passed",
        "resampled_seam": resampled_seam,
        "resampled_inner_mean_adjacent": resampled_adjacent,
        "resampled_seam_ratio": round(seam_ratio, 4),
        "seam_max": seam_max,
        "scrubbed_rgb_pixels": scrubbed,
        "specks_dropped": specks,
        "strip": {"path": str(strip_path), **strip_meta},
        "gif": gif_report,
        "webp": webp_report,
    }
    write_loop_report(target, payload)
    payload["report"] = str(target)
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--frames-dir", required=True, type=Path, help="keyed RGBA frames from `sprite-gen video-frames` (…/keyed)")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fps", type=float, default=24.0, help="frame rate of the keyed frames (from the frames report)")
    parser.add_argument("--state", help="motion state (idle/walk/run/jump/attack) — selects the detection window")
    parser.add_argument("--min-len", type=int, help="override: minimum cycle length in frames")
    parser.add_argument("--max-len", type=int, help="override: maximum cycle length in frames")
    parser.add_argument("--n-out", type=int, help="frames in the GIF/WebP (default: cycle seconds x --gif-fps)")
    parser.add_argument("--gif-fps", type=float, default=GIF_FPS_DEFAULT, help=f"GIF/WebP playback density (default {GIF_FPS_DEFAULT:g}); every state plays at this rate regardless of cycle length")
    parser.add_argument("--seam-max", type=float, default=SEAM_RATIO_MAX, help=f"loop seam gate (default {SEAM_RATIO_MAX})")
    parser.add_argument("--cycle", choices=CYCLE_MODES, default="auto", help="auto: periodic first, one-shot failover for action states (recorded in the report); periodic / one-shot force one detector; fixed cuts exactly --start/--length (no detection, reported as kind=fixed)")
    parser.add_argument("--start", type=int, help="fixed cut: first keyed frame of the cycle (with --cycle fixed)")
    parser.add_argument("--length", type=int, help="fixed cut: cycle length in frames (with --cycle fixed)")
    parser.add_argument("--strip-height", type=int, default=STRIP_MAX_HEIGHT, help=f"cell/strip/GIF height cap in px (default {STRIP_MAX_HEIGHT}); the cycle is scaled down to fit, and never up unless --body-height asks for it")
    parser.add_argument("--body-height", type=int, help="scale so the STANDING height (the subject in the clip's first frame, i.e. the base still's pose) is this many px — the same value across states gives the same character size; --strip-height stays the cap")
    parser.add_argument("--anchor", choices=ANCHOR_MODES, default=None, help="motion-auto: automatic stable regions, local repeat selection and XY ramp (walk/run); motion: fixed cut with explicit regions; body (default for walk/run): measure the last-to-first head-and-torso offset once and spread it as a ramp over the cycle; feet: remove in-canvas drift (a straight-line trend) so every cell stands on the mean foot line; none: leave placement as filmed (default for other states)")
    parser.add_argument("--anchor-region", type=motion_anchor.parse_region, action="append", help="motion anchor only: repeat twice with head then torso x0,y0,x1,y1 in the first selected frame; requires --cycle fixed and at least 6 frames")
    parser.add_argument("--name", default="loop", help="basename for strip/gif/webp outputs")
    parser.add_argument("--report", type=Path)


def run(**kwargs: object) -> int:
    payload = run_loop(
        Path(str(kwargs["frames_dir"])), Path(str(kwargs["out_dir"])),
        fps=float(kwargs.get("fps") or 24.0), state=kwargs.get("state"),  # type: ignore[arg-type]
        min_len=kwargs.get("min_len"), max_len=kwargs.get("max_len"), n_out=kwargs.get("n_out"),  # type: ignore[arg-type]
        seam_max=float(kwargs.get("seam_max") or SEAM_RATIO_MAX), name=str(kwargs.get("name") or "loop"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
        cycle_mode=str(kwargs.get("cycle") or "auto"), gif_fps=float(kwargs.get("gif_fps") or GIF_FPS_DEFAULT),
        start=kwargs.get("start"), length=kwargs.get("length"), strip_height=int(kwargs.get("strip_height") or STRIP_MAX_HEIGHT),  # type: ignore[arg-type]
        body_height=kwargs.get("body_height"),  # type: ignore[arg-type]
        anchor=kwargs.get("anchor"),  # None resolves by state inside run_loop
        anchor_regions=kwargs.get("anchor_region"),
    )
    summary = {k: payload[k] for k in ("state", "frames_total", "window", "cycle_seconds", "n_out", "delay_ms", "resampled_seam_ratio", "specks_dropped", "report")}
    summary["cycle"] = {k: payload["cycle"].get(k) for k in ("kind", "start", "length", "period_global", "ratio", "review_recommended", "half_period_guard")}
    if payload["periodic_attempt"]:
        summary["periodic_attempt"] = payload["periodic_attempt"]["why_rejected"]
    if payload.get("motion_anchor", {}).get("applied") is False:
        summary["motion_anchor"] = payload["motion_anchor"]
    summary["strip"] = {k: payload["strip"][k] for k in ("path", "frames", "w", "h", "body_h", "delay_ms")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-loop", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
