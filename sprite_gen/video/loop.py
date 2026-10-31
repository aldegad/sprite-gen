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

The deepest repeat can hold two cycles (a clip drawn on twos whose cycle is an odd number of
frames repeats better two cycles on than one), but nothing here cuts shorter than it on the
pixels' word: a gait's half step with the legs drawn alike is the same picture as a true cycle
half a drawing late. The half and the third of the period are screened and recorded
(`cycle.fundamental`, sprite_gen/video/period.py); `video-cycle-align` stops a set on a loop the
screen suspects, and only a count given to it (`--cycles`) takes one cycle out of that loop.

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
import sys
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.gen import handedness as handed_mod
from sprite_gen.spec.runio import atomic_write_text, new_stage, refuse_overlap
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.util.resample import resize_cell
from sprite_gen.video import motion_anchor, auto_motion, local_cycle, gait_fallback, nonloop, effect
from sprite_gen.video import part_pop as part_pop_mod
from sprite_gen.video import cadence as cadence_mod
from sprite_gen.video import crossings as crossings_mod
from sprite_gen.video import held as held_mod
from sprite_gen.video import held_fill as held_fill_mod
from sprite_gen.video import root_motion as root_motion_mod
from sprite_gen.video import body_turn as body_turn_mod
from sprite_gen.video import legs as legs_mod
from sprite_gen.video import period as period_mod
from sprite_gen.video import repair as repair_mod
from sprite_gen.video import rife as rife_mod
from sprite_gen.video.interpolation_quality import ghost_screen, judge_scale as quality_judge_scale
from sprite_gen.video import evidence as evidence_mod
from sprite_gen.video.frames import drop_specks
from sprite_gen.util.console import utf8_streams
from sprite_gen.video import wrap_blend as wrap_blend_mod

ANALYSIS_SIZE = 96  # thumbnail edge for the distance matrix
STRIP_MAX_CELLS = 64  # upper bound on cells even when they are narrow
STRIP_MAX_WIDTH = 32000  # Chrome refuses images wider than ~32767 px; the cap is on PIXELS — a 650 px cell allows only 49 cells (2026-09-09 wolf idle: 64 cells = 41,664 px, unrenderable)
STRIP_MAX_HEIGHT = 520
SEAM_RATIO_MAX = 2.0  # loop seam / mean adjacent distance inside the cycle (a walk's or run's between its drawings, `seam_drawings`)
# A walk's or run's largest step between drawings as it plays, the wrap one of them, over their mean step
# (`drawing_steps`): past it the loop jumps inside, where the seam ratio, read at the wrap alone, does not look. Read
# between drawings, not frames: a loop held on twos or threes steps nothing and then two or three steps at every drawing.
# The step gate stands where the seam gate does: a --seam-max past it lifts it, every step's bound is then --seam-max,
# and a fixed cut the clip's crossings count an odd number of steps is kept with a warning line (`fixed_count`).
STEP_RATIO_MAX = 2.5
# The step gate's pixel jitter: a step over STEP_RATIO_MAX that its drawing moving at most this many pixels of the frames
# as filmed makes (`step_jitter`: the move that lays it on the drawing before, read on coverage, so no colour enters it,
# and with that move taken back the step within STEP_RATIO_MAX) is a pixel's jitter of a loop that barely moves, not a
# jump, over however small a mean step: one pixel and not two. Read as a mean RGBA difference instead, the same jump of a
# frame moved 2 px reads 0.057 drawn in colour and 0.021 drawn black.
STEP_JITTER_PX = 1.5
PERIODICITY_MIN = 0.15  # the period must dip at least 15% below the profile mean (flat profile = no repeat)
# Where a size hold or the gait fallback writes the frames it scaled back: a folder of the run's own in the out dir,
# `.gait-fallback-frames.<pid>-<random>` (`_Scratch`), removed however the run ends.
FALLBACK_FRAMES = "gait-fallback-frames"
# What a video-loop report is (its `kind`), and the `status` of one whose cut was handed over: align, compare and
# source read a report as a delivered loop's only when it says both.
REPORT_KIND = "sprite-gen-video-loop-report"
REPORT_PASSED = "passed"
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
ONE_SHOT_RETURN_MAX = 0.25  # one-shot: the return pair comes back to within a quarter of its departure
CYCLE_MODES = ("auto", "periodic", "one-shot", "fixed", "pinned", *nonloop.KINDS)
# --cycle pinned: the clip was asked to end on its first frame (first-last mode). A re-rendered
# first frame is never byte-identical to its source; on the analysis thumbnail (mean absolute
# RGBA difference, 0..1) it lands within this of it, well under one frame of visible motion.
PIN_NOISE_MAX = 0.005
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
# `--steps`: how many steps the cut `--anchor motion-auto` takes holds, as counted by whoever looked at
# it (docs/loop-repair.md section 3, "The step screen"). 1 cuts again two of its lengths long, 2 keeps it.
STEPS = (1, 2)
# A cut two steps long (one cycle) that the clip holds less than this share of a cycle past is cut and
# warned about (`cycle.coverage`): its repeat is barely in the clip, and the seam gate says whether it
# closes. The search itself sees a window repeat only with a quarter of its length past it in the clip,
# so a cut under this was taken on the count alone (local_cycle.counted).
COVERAGE_MIN = 0.25
ANCHOR_MODES = ("none", "feet", "body", "motion", "motion-auto")
BODY_ANCHOR_BAND = 0.6  # --anchor body reads the wrap offset from the top 60 % of the first frame's box: head and torso, not the legs
BODY_ANCHOR_SEARCH = 24  # px either side searched for the last frame's horizontal offset against the first
FOOT_BAND = legs_mod.FOOT_BAND  # fraction of the frame's own height, measured up from its lowest opaque row
# Jump-frame repair (docs/loop-repair.md section 2). auto: walk and run loops get a RIFE frame in
# place of each frame that follows a jump, any other loop RIFE frames round a wrap that jumps (the
# seam bridge), and a loop that needs one where no RIFE is installed is cut as filmed with a warning
# (a walk that cut before RIFE existed still cuts); on: the same, but no RIFE fails the loop — a
# caller that asked for the repair is never handed less; off: the loop is cut as filmed, and says so.
REPAIR_MODES = ("auto", "on", "off")
# The overlapped wrap (docs/loop-repair.md section 2, "The overlapped wrap"), under --repair auto or on. on: a two-step
# cut whose wrap the gate refuses has the frames beside its wrap made again from the clip's frames a cycle apart, each
# read with RIFE's sixteen single passes, 34 calls of the binary a frame and up to forty frames; off: it is not tried, the
# report says so, and the gate refuses the cut as filmed — for a host whose RIFE runs on a CPU, where one frame of a
# 960 px clip takes minutes and the loop's caller would stop it before it fails on its own.
OVERLAP_WRAP_MODES = ("on", "off")
OVERLAP_WRAP_OFF = "turned off (--overlap-wrap off)"
# A frame the jump repair leaves off the walk's flow (`jump_repair.unfixed`: no group brings it back, or the group that
# would was refused), or a part that pops at the wrap of the cut taken (`part_pop.left`, a take to film again: `retake`).
# warn: a warning line, the loop kept; refuse: the loop fails.
UNFIXED_MODES = ("warn", "refuse")
SIZE_HOLD_MODES = ("auto", "off")
# Where `video-cycle-align` keeps the cut as filmed (sprite_gen/video/align.py). A new cut makes
# the kept one stale, so writing the cycle removes it: an alignment after a re-cut (say, once RIFE
# is installed and the jumps are repaired) reads the new cut, not the old one.
CYCLE_SOURCE_DIR = "cycle.source"
# The strip as it was before `video-sway` (the Sway layer; old name `video-follow`) moved a region of it;
# a later sway reads from it, and a new cut or alignment removes it (docs/video-pipeline.md section 6).
# Its name, and the key the sway's record is written under, stay `follow` in this release: a consumer reads
# them by those names. The record is read under either key.
FOLLOW_SOURCE = "follow.source.png"
# A refused cut hands nothing over (`refuse`); the frames it wrote, if it got that far, are kept here to look at.
CYCLE_REFUSED_DIR = "cycle.refused"
SWAY_RECORD = "follow"
SWAY_RECORD_KEYS = ("sway", "follow")
# The strip's record of each of its cells, in its `<name>.strip.json` and bound to it by its PNG's sha256 (`strip_sha256`):
# per cell the source frame it is, where the anchor moved that frame before the crop (`anchor_move_px`, across and down),
# the factor each step that holds a clip's frames to one size scaled it by about its feet before the cut (`scale`; 1
# where the step did not hold them), and whether it is that frame as filmed but for that move (`as_filmed`: its pixels at
# the end of the cut are its pixels anchored, before any repair, and nothing held the clip's frames). `video-sway
# --read-on-cut` pairs two cuts of one clip by it alone. An alignment does not carry it over to the strip it makes; it
# keeps the cut's own strip record beside the cut's frames (`cycle.source/<name>.strip.json`).
CELL_RECORD = "cell_record"
CELL_SCALE_STEPS = {"size_hold": "the size hold", "gait_fallback": "the gait fallback"}  # each, in words
# How the frames of a cut at --anchor motion or motion-auto are made before any repair, as their receipt names it
# (`source_projection.recipe`; a cut at --anchor none or body is made by `source.RECIPE`): laid by the correction.
MOTION_RECIPE = "motion-anchor-correction-v1"


def sway_recorded(meta: dict) -> bool:
    """Whether a loop's strip sidecar carries a sway's record, under its new key or its old one."""
    return any(key in meta for key in SWAY_RECORD_KEYS)


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


@dataclass(frozen=True)
class CutWindow:
    """The cut's length window, worked out once (`of`) and read by every search that sets a length.

    `lo`/`hi` are the first search's window: the state's, save where the caller set a bound
    (`min_len`/`max_len`, --min-len/--max-len). A search that looks elsewhere asks this for its own
    window — the gait fallback and the one-shot failover past the state's ceiling (`past`), a count of
    steps two lengths on (`within`), a window two steps long (`max_len`, local_cycle's `length_cap`) —
    and gets it inside the caller's bounds: an explicit bound is never widened, and no search sets one
    of its own. `hold` is the last word on any cut, whatever chose it."""
    lo: int
    hi: int
    min_len: int | None = None
    max_len: int | None = None

    @classmethod
    def of(cls, prof: LoopProfile, n: int, fps: float, min_len: int | None, max_len: int | None) -> "CutWindow":
        lo_default, hi_default = prof.window(n, fps)
        lo = min_len if min_len is not None else lo_default
        return cls(lo, max_len if max_len is not None else max(lo + 2, hi_default), min_len, max_len)

    def within(self, lo: int, hi: int) -> tuple[int, int]:
        """A search's own window [lo, hi] inside the caller's bounds (empty when lo > hi comes back)."""
        return (lo if self.min_len is None else max(lo, self.min_len),
                hi if self.max_len is None else min(hi, self.max_len))

    def past(self, hi: int) -> int:
        """The ceiling of a search past the state's window: `hi`, or the caller's own where it set one."""
        return hi if self.max_len is None else self.max_len

    def said(self) -> str:
        """The bounds the caller set, as given on the command line."""
        return " ".join(f"--{flag} {v}" for flag, v in (("min-len", self.min_len), ("max-len", self.max_len)) if v is not None)

    def hold(self, cycle: dict[str, Any]) -> None:
        """Raise when a cut is outside the caller's bounds: two explicit instructions that disagree are
        a failure, never one of them followed without a word."""
        length = cycle["length"]
        if (self.min_len is not None and length < self.min_len) or (self.max_len is not None and length > self.max_len):
            exc = ValueError(f"the cut is {length} frames long ({cycle.get('kind', 'cut')} from frame {cycle['start']}), outside {self.said()}")
            exc.diagnostics = {**cycle, "window_refused": {"min_len": self.min_len, "max_len": self.max_len}}  # type: ignore[attr-defined]
            raise exc


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


def leg_signals(frames: list[Image.Image]) -> dict[str, list[float]] | None:
    """The legs' per-frame signals for the two-cycle screen's record (`legs.strike_signals`), or
    None where a frame has no solid body to read them from."""
    try:
        return legs_mod.strike_signals(frames)
    except ValueError:
        return None


def distance_matrix(files: list[Path]) -> np.ndarray:
    return _distances(np.stack([_load_small(f) for f in files]))


def _seam_on_cells(cells: list[Image.Image]) -> tuple[float, float, np.ndarray]:
    """Cells as they play (a walk's each drawing's first, `seam_drawings`), on their analysis features: the mean step, the
    wrap (last -> first) and each step."""
    flat = np.stack([_small_features(im) for im in cells])
    steps = np.abs(flat[1:] - flat[:-1])
    return float(steps.mean()), float(np.abs(flat[-1] - flat[0]).mean()), steps.mean(axis=1)


def _distances(flat: np.ndarray) -> np.ndarray:
    """The distance between every two frames of their analysis features (`_small_features`)."""
    n = len(flat)
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


def detect_cycle(D: np.ndarray, *, min_len: int, max_len: int, gait_floor: int | None = None,
                 signals: dict[str, list[float]] | None = None) -> dict[str, Any]:
    """Global period, then a wrap-compatible start with coherent gait context.

    `gait_floor` (frames) turns on the half-period guard: a period below it is one step
    of a two-step gait, so the doubled period is taken when it repeats about as well
    (see GAIT_DOUBLE_TOL). Above the floor, ambiguous non-exact harmonics may also
    retain two phase occurrences; the report flags that decision for visual review.
    Gait starts balance the wrap step with observed repetition around the cut;
    other states keep their wrap-only ranking. The period taken is then screened for
    two or three cycles (`fundamental`, `period.screen`; `signals` are the frames'
    `legs.strike_signals`, for the record): what is found is recorded, nothing is
    cut shorter."""
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
    # A half or a third of the period that returns to a pose on the motion's path may be the
    # cycle, or one step of it with the legs alike: recorded, never taken (sprite_gen/video/period.py).
    fundamental = period_mod.screen(
        period, D=D, prof=prof, minima=period_mod.local_minima(prof), mean=profile_mean,
        lowest=max(min_len, gait_floor or 0), periodicity_min=PERIODICITY_MIN, signals=signals, gait=gait_floor is not None,
        floor_why="the gait floor" if gait_floor is not None and gait_floor >= min_len else "the window")
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
    best["fundamental"] = fundamental
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
    be at most a quarter of the departure (ONE_SHOT_RETURN_MAX). No seam threshold is relaxed.
    No cut is shorter than `min_len` (nor ONE_SHOT_MIN_LEN) or longer than `max_len`; `run_loop`
    passes ONE_SHOT_MIN_LEN unless the caller set --min-len (`CutWindow`).

    Where no pair returns, the refusal names the closest return (`closest_return`: the pair, its return over its
    departure, its moved), not the moved of the returned pairs, of which there are none.
    """
    n = len(D)
    adjacent = np.diag(D, 1)
    candidates = []
    max_moved = 0.0
    closest: dict[str, Any] | None = None
    shortest = max(ONE_SHOT_MIN_LEN, min_len)
    for start in range(n - ONE_SHOT_MIN_LEN + 1):
        for end in range(start + shortest - 1, min(n, start + max_len)):
            seam = float(D[start, end])
            e = (D[start] + D[end]) / 2
            peak_at = start + int(np.argmax(e[start:end + 1]))
            peak = float(e[peak_at])
            departure = peak - seam / 2
            if departure <= 0:
                continue
            mass = float((frame_mass[start] + frame_mass[end]) / 2) if frame_mass is not None else 0
            moved = departure / mass if mass > 0 else None
            if closest is None or seam / departure < closest["ratio"]:
                closest = {"pair": [start, end], "ratio": seam / departure, "seam": seam, "departure": departure,
                           "moved": moved}
            if seam > departure * ONE_SHOT_RETURN_MAX:
                continue
            med = float(np.median(e))
            mad = float(np.median(np.abs(e - med))) or 1e-6
            contrast = (peak - med) / mad
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
        if closest is not None and closest["ratio"] > ONE_SHOT_RETURN_MAX:
            # No pair returns: the condition failed is the return, said on the pair that came closest.
            a, b = closest["pair"]
            said = (f"the clip does not come back to a pose it left: the closest return, frames {a}-{b}, is "
                    f"{closest['ratio']:.2f} of its departure away, over {ONE_SHOT_RETURN_MAX}"
                    + (f" (moved {closest['moved']:.2f}, need {ONE_SHOT_MIN_MOVED})" if closest["moved"] is not None else ""))
        else:
            said = (f"the clip never leaves its rest pose and returns with observed endpoints (moved {max_moved:.2f}, "
                    f"need {ONE_SHOT_MIN_MOVED})")
        raise CycleSelectionError(
            f"video-loop: no complete one-shot return — {said}",
            {"kind": "one-shot", "candidates": [], **({"closest_return": closest} if closest is not None else {})},
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


def pinned_cycle(D: np.ndarray, *, seam_max: float) -> dict[str, Any]:
    """The whole clip as one cycle, for a clip pinned to end on its first frame.

    Every frame but the last is kept: the last re-renders the first, so playing it too would show
    that pose twice at the wrap. The wrap (second-to-last -> first) then plays like the step into
    that last frame, and closes when the last frame really is the first again — so the gate is
    the pin error, allowed the same `seam_max` steps as any seam or the re-render noise, whichever
    is larger. A near-still clip moves so little per frame that the seam ratio alone reads its
    re-render noise as a jump.
    """
    n = D.shape[0]
    length = n - 1
    adjacent = np.array([D[i, i + 1] for i in range(length - 1)])
    inner = float(adjacent.mean())
    seam = float(D[length - 1, 0])
    pin_error = float(D[n - 1, 0])
    return {"kind": "pinned", "start": 0, "length": length, "seam": seam, "inner_mean_adjacent": inner,
            "ratio": seam / inner if inner > 0 else math.inf, "pin_error": pin_error,
            "pin_tolerance": max(seam_max * inner, PIN_NOISE_MAX), "period_global": None, "periodicity": None}


def once_return_cycle(D: np.ndarray, *, seam_max: float) -> dict[str, Any]:
    """The whole clip played once, for a clip pinned to end on its first frame (`--cycle once-return`).

    Every frame is kept, the last too: the strip starts on the first pose and ends on it again, so a game plays it
    once and goes on from the pose it started in. The gate is the pin, as `pinned_cycle`'s: the last frame is the
    first again within `seam_max` ordinary steps or the re-render noise. The motion's peak is its largest step,
    as a `once` cut's (`nonloop.select_cut`).
    """
    n = D.shape[0]
    steps = np.diag(D, 1)
    inner = float(steps.mean())
    pin_error = float(D[n - 1, 0])
    return {"kind": "once-return", "start": 0, "length": n, "end_frame": n - 1, "event_frame": int(np.argmax(steps)) + 1,
            "seam": pin_error, "inner_mean_adjacent": inner, "pin_error": pin_error,
            "pin_tolerance": max(seam_max * inner, PIN_NOISE_MAX), "period_global": None, "periodicity": None,
            "ratio": 0.0, "review_recommended": False}


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


def ramp_offset(wrap_dx: int, k: int, length: int) -> int:
    """How far across the ramp of a cycle `length` frames long moves its frame k (`ramp_frames`): round(wrap_dx * k / L)."""
    return int(round(wrap_dx * k / length))


def ramp_frames(frames: list[Image.Image], wrap_dx: int) -> list[Image.Image]:
    """Shift frame k by round(wrap_dx * k / L) (`ramp_offset`) so the cycle's end returns to its start."""
    L = len(frames)
    if wrap_dx == 0 or L < 2:
        return frames
    out = []
    for k, im in enumerate(frames):
        dx = ramp_offset(wrap_dx, k, L)
        if dx == 0:
            out.append(im)
            continue
        shifted = Image.new("RGBA", im.size, (0, 0, 0, 0))
        shifted.paste(im, (dx, 0))
        out.append(shifted)
    return out


def standing_height(path: Path, frame: int = 0) -> int:
    """The subject's height in clip frame `frame`: the clip's first frame, the base still's pose as filmed.

    Every clip starts from its still (image-to-video), so this is the same pose in every
    state of one character — which is what one `--body-height` across states has to measure
    to give that character one size. A walk or run whose cut is filmed at another size than the
    clip's first frame is read on the cut's first frame instead (run_loop).
    """
    box = Image.open(path).convert("RGBA").getchannel("A").point(lambda v: 255 if v >= 8 else 0).getbbox()
    if box is None:
        which = "the clip's first frame" if frame == 0 else f"frame {frame}, the cut's first"
        raise SystemExit(f"video-loop: {path.name}, {which}, has no subject to measure the standing height on (--body-height)")
    return box[3] - box[1]


def build_strip(frames: list[Image.Image], *, max_cells: int = STRIP_MAX_CELLS, max_height: int = STRIP_MAX_HEIGHT, max_width: int = STRIP_MAX_WIDTH, cycle_seconds: float, body_height: int | None = None, anchor: str = "none", kind: str = "periodic", standing_src: int | None = None, standing_frame: int = 0) -> tuple[Image.Image, dict[str, Any]]:
    """Union-crop (no bottom pad so feet meet the floor), scale, bottom-align, tile horizontally.

    The cell count is capped by the strip's PIXEL width (`max_width`) as well as by
    `max_cells`: the crop is measured over every cycle frame first, then as many evenly
    spaced frames as fit are kept. Subsampling is recorded in the meta, never silent."""
    L = len(frames)
    lifetime = kind == "lifetime"
    boxes = [(im.getchannel("A") if lifetime else im.getchannel("A").point(lambda v: 255 if v >= 8 else 0)).getbbox() for im in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        raise SystemExit("video-loop: every cycle frame is fully transparent")
    # The side margins may reach past the frame: a subject allowed to touch the left or right
    # edge (video-frames --allow-subject-edge-contact) still gets its 8 transparent columns,
    # so no cell corner is ever the subject. `crop` fills outside the frame with alpha 0.
    left = min(b[0] for b in boxes) - 8
    top = min(b[1] for b in boxes) - 8 if lifetime else max(0, min(b[1] for b in boxes) - 8)
    right = max(b[2] for b in boxes) + 8
    bottom = max(b[3] for b in boxes) + (8 if lifetime else 0)
    # body_h = the STANDING height: the tallest frame whose feet touch the floor. A median
    # over the whole cycle undercounts a jump (crouch + airborne frames dominate) and then
    # over-scales it — the 2026-09-09 hero read 22 % taller than the walk beside it.
    floor = max(b[3] for b in boxes)
    grounded = [b[3] - b[1] for b in boxes if nonloop.on_floor(b, floor)] or [b[3] - b[1] for b in boxes]
    # `standing_src` is the caller's own measurement of the standing pose (run_loop: the clip's
    # first frame, or the cut's first — clip frame `standing_frame` — where the cut is filmed at
    # another size than it). The tallest grounded frame counts whatever is raised overhead — an attack's
    # windup lifts the weapon above the head — and would shrink that state against the others.
    body_src = (max(b[3] for b in boxes) - min(b[1] for b in boxes) if lifetime else
                standing_src if standing_src is not None else max(grounded))
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
    w = max(1, round(cell_src_w * scale))
    h = max(1, round((bottom - top) * scale))
    cap = max(1, min(max_cells, max_width // max(1, w)))
    idx = list(range(L))
    if L > cap:
        idx = nonloop.samples(L, cap) if kind in nonloop.KINDS else [round(k * L / cap) for k in range(cap)]
    chosen = [frames[i] for i in idx]
    if feet:
        cells = []
        for i in idx:
            im = frames[i]
            cell = Image.new("RGBA", (round(cell_src_w), bottom - top), (0, 0, 0, 0))
            cell.alpha_composite(im.crop((0, top, im.width, bottom)), (round(lead - feet[i]), 0))
            cells.append(resize_cell(cell, (w, h)))
    else:
        cells = [resize_cell(im.crop((left, top, right, bottom)), (w, h)) for im in chosen]
    strip = Image.new("RGBA", (w * len(cells), h), (0, 0, 0, 0))
    for k, im in enumerate(cells):
        strip.alpha_composite(im, (k * w, 0))
    meta = {
        "frames": len(cells),
        "sample_indices": idx,
        "source_rect": [left, top, right, bottom] if not feet else None,
        "w": w,
        "h": h,
        # how the cycle was cut, and whether a runtime should repeat it: a one-shot
        # (rest -> action -> rest) plays once on a trigger; every other cut is a loop.
        # The spec asset loader reads `loop` from this sidecar.
        "kind": kind,
        "loop": kind not in ("one-shot", *nonloop.KINDS),
        "body_h": round(body_src * scale),
        "body_src_h": body_src,  # the standing height as filmed; body_h / body_src_h > 1 means the cells were upscaled
        "body_ref": ("cut-first-frame" if standing_frame else "first-frame") if standing_src is not None else "tallest-grounded",
        "scale": round(scale, 4),
        "delay_ms": round(1000 * cycle_seconds / len(cells), 2),
        "cycle_frames": L,
        "cycle_seconds": round(cycle_seconds, 4),
        "subsampled": L > cap,
        "cell_cap": cap,
        "top_margin_px": top,
        "cell_height_cap": max_height,  # --strip-height: what a rebuild of these cells (video-cycle-align) scales against
        "body_height_target": body_height,
        "foot_anchor": anchor,  # "none" | "feet" — how the cells were aligned
        "drift_px": drift_px,  # source px of slow in-canvas drift removed across the cycle (0 when not measured)
        "foot_sway_px": round(sway),  # source px the planted foot moves within the gait, kept as is (0 when not measured)
        "foot_x": round(lead * scale) if feet else None,  # x of the foot line inside every cell
    }
    if standing_src is not None and standing_frame:
        meta["body_ref_frame"] = standing_frame  # the clip frame the standing height was read on
    if lifetime:
        meta.update(blend="add", body_ref="effect-extent",
                    anchor=[(frames[0].width / 2 - left) * w / (right - left),
                            (frames[0].height / 2 - top) * h / (bottom - top)])
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


def write_webp(frames: list[Image.Image], out: Path, *, delay_ms: int, loop: int = 0) -> None:
    """Animated WebP through libwebp's img2webp with -exact (Pillow's animated writer drops `exact`
    and rewrites RGB under alpha 0 to white; measured 2026-09-08). The frames reach img2webp as PNGs in a
    folder of this call's own beside `out` (`runio.new_stage`: `.<out>.<pid>-<random>.frames`), removed once
    it has run: a folder already there, an input's or another run's, is never written into or removed."""
    img2webp = shutil.which("img2webp")
    if not img2webp:
        raise SystemExit("video-loop: `img2webp` not found on PATH — install libwebp (brew install webp) for exact-alpha WebP")
    if not img2webp_supports_exact(img2webp):
        raise SystemExit(
            "video-loop: this img2webp has no `-exact` option (libwebp < 1.5; Ubuntu 24.04 ships 1.3.x) — "
            "install libwebp >= 1.5 (brew install webp, or the official binaries from storage.googleapis.com/downloads.webmproject.org)"
        )
    workdir = new_stage(out, ".frames")
    try:
        paths = []
        for k, im in enumerate(frames):
            p = workdir / f"webp-{k:03d}.png"
            im.save(p)
            paths.append(str(p))
        proc = subprocess.run([img2webp, "-loop", str(loop), "-lossless", "-exact", "-d", str(delay_ms), *paths, "-o", str(out)], capture_output=True, text=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(f"video-loop: img2webp failed: {proc.stderr.strip()[:300]}")


def verify_animation(path: Path, *, expect_frames: int, check_stale: bool, expected_loop: int | None = 0,
                     background: str | None = None) -> dict[str, Any]:
    # Closed on the way out: `video-follow` checks its animations before it moves them over the loop's.
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        loop = im.info.get("loop")
        corners_ok = True
        background_pixel = Image.new("RGBA", (1, 1), background).getpixel((0, 0)) if background is not None else None
        background_ok = True
        opaque = True
        stale = 0
        for k in range(n):
            im.seek(k)
            f = im.convert("RGBA")
            w, h = f.size
            corners_ok &= all(f.getpixel(c)[3] == 0 for c in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
            if background_pixel is not None:
                background_ok &= all(f.getpixel(c) == background_pixel for c in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
                opaque &= f.getchannel("A").getextrema() == (255, 255)
            if check_stale:
                stale += sum(1 for p in f.get_flattened_data() if p[3] == 0 and (p[0] or p[1] or p[2]))
        report = {"file": path.name, "format": im.format, "n_frames": n, "loop": loop, "size": list(im.size), "corners_transparent": corners_ok, "stale_rgb_under_alpha0": stale, "bytes": path.stat().st_size}
        if background is not None:
            report.update(background=background, opaque=opaque)
    problems = []
    if n != expect_frames:
        problems.append(f"{n} frames, expected {expect_frames}")
    if loop != expected_loop:
        problems.append(f"loop={loop!r}, expected {expected_loop!r}")
    if background is not None:
        if not opaque:
            problems.append("a frame is not opaque on its declared background")
        if not background_ok:
            problems.append("a corner does not match the declared background")
    elif not corners_ok:
        problems.append("a corner is not transparent")
    if stale:
        problems.append(f"{stale} transparent pixels carry RGB")
    if problems:
        raise SystemExit(f"video-loop: {path.name} failed verification: {'; '.join(problems)}")
    return report


def handed_over(out_dir: Path, name: str) -> list[Path]:
    """What a cut that passed hands over in `out_dir`: its frames (`cycle/`), the strip and its metadata, the GIF and
    the WebP, and what a later alignment and follow-through keep beside them (`cycle.source/`, `follow.source.png`)."""
    return [out_dir / "cycle", out_dir / CYCLE_SOURCE_DIR, out_dir / FOLLOW_SOURCE, out_dir / f"{name}.strip.png",
            out_dir / f"{name}.strip.json", out_dir / f"{name}.gif", out_dir / f"{name}.webp"]


def refuse(target: Path, out_dir: Path, name: str, report: dict[str, Any], *, cut_written: bool = False) -> None:
    """Refuse the cut: take back all an earlier cut handed over in `out_dir` (`handed_over`), so a caller that does not
    read the exit code finds nothing to pass on, and write the report (`status: failed`). A cut this run wrote stays as
    `cycle.refused/`, in place of an earlier one."""
    out_dir = out_dir.expanduser().resolve()
    shutil.rmtree(out_dir / CYCLE_REFUSED_DIR, ignore_errors=True)
    if cut_written:
        (out_dir / "cycle").rename(out_dir / CYCLE_REFUSED_DIR)
    for path in handed_over(out_dir, name):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    write_loop_report(target, report)


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


def resampled_cells(strip: Image.Image, meta: dict[str, Any], idx: list[int], length: int) -> list[Image.Image]:
    """The cells of `strip` (`build_strip`, its `meta`) at the frames `idx` of a cycle `length` frames long: the
    GIF and WebP frames, and what the seam gate reads on rendered cells. A strip subsampled past its cell cap
    holds fewer cells than the cycle has frames, and gives the nearest."""
    cells = [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]
    return [cells[min(len(cells) - 1, round(j * len(cells) / length))] for j in idx]


def shown_runs(cells: list[Image.Image]) -> int:
    """The frames a GIF or WebP of `cells` holds: a cell shown twice in a row (a filmed ghost given way
    to the frame beside it, `--between nearest` stretching a loop) is one frame of twice the delay in
    both, so one per run of identical cells."""
    return 1 + sum(1 for a, b in zip(cells, cells[1:]) if a.tobytes() != b.tobytes())


def _repair_jumps(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, *, facing: str,
                  required: bool, screen: dict[str, Any], wrap: dict[str, Any] | None = None
                  ) -> tuple[list[Image.Image], dict[str, Any], dict[str, Any] | None]:
    """`repair_mod.repair_jumps` on the cut as filmed and its ghost `screen`, with RIFE located only
    once a frame is to be made: a loop with no frame off its flow and no ghost between two clean frames
    needs no binary, nor one with no part that rushes (the eased retime, `repair.surges`). The report names the
    interpolator that made the frames.

    `wrap` (the clip's frames a cycle from the cut's beside its wrap, `cycle_apart`, and the gate's `seam_max`) asks
    first for the overlapped wrap (`repair_mod.overlap_wrap`) of a cut two steps long whose wrap the gate refuses;
    the frames it makes are then neither made from, nor beside, nor again (`overlapped`). Its record is returned
    third, None where it was not asked; a `wrap` with `off` (`--overlap-wrap off`) and no frames records it turned off,
    nothing read or made. The overlapped wrap and the eased retime after it read the body's drift on one torso, the
    cut's as filmed (`repair_mod.Torso`).

    A frame to make and no RIFE installed: `required` lets `RifeNotInstalled` through; otherwise
    each filmed ghost gives way to the clean frame beside it, a jump (a frame off the flow), a rush and a wrap that does
    not close stay as filmed, and the record says why where a jump or a rush was left (`rife`, `install`; `applied`
    false where nothing was replaced)."""
    made_by: list[dict[str, str]] = []
    calls = 0

    def located() -> rife_mod.Interpolate:
        nonlocal interpolate
        if interpolate is None:
            found = rife_mod.Rife()
            made_by.append(found.describe())
            interpolate = found
        return interpolate

    def lazy(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        nonlocal calls
        calls += 1
        return located()(a, b, t)

    def lazy_read(a: Image.Image, b: Image.Image, t: float) -> tuple[Image.Image, np.ndarray]:
        # the overlapped wrap reads each frame it makes with RIFE's single passes, for a part torn: an interpolator given
        # that cannot say how far its passes disagree is refused, never read as agreeing
        nonlocal calls
        read = getattr(located(), "read", None)
        if read is None:
            raise ValueError("the overlapped wrap reads how far RIFE's single passes disagree on each frame it makes, and "
                             "the interpolator given has no `read` (rife.Rife.read)")
        calls += 1
        return read(a, b, t)

    overlap: dict[str, Any] | None = None
    overlapped: list[int] = []
    torso = None
    if wrap is not None:
        torso = None if "off" in wrap else repair_mod.Torso(frames)
        asked = partial(repair_mod.overlap_wrap, others=wrap.get("others", {}), seam=wrap_on_cells,
                        steps=partial(ring_steps, cycles=wrap.get("cycles")),
                        seam_max=wrap["seam_max"], reads=bool(screen["reads"]), torso=torso, off=wrap.get("off"))
        try:
            frames, overlap = asked(frames, read=lazy_read)
        except rife_mod.RifeNotInstalled as exc:
            if required:
                raise
            frames, overlap = asked(frames, read=None)
            overlap.update(why="RIFE not installed — the wrap cut as filmed", rife=str(exc), install=rife_mod.install_command())
        overlapped = overlap["frames"]
        if overlap["calls"]:
            overlap["interpolator"] = {**made_by[0], **interpolate.counts()} if made_by else {"kind": "injected"}
    wrap_calls = calls
    try:
        repaired, record = repair_mod.repair_jumps(frames, lazy, facing=facing, screen=screen, overlapped=overlapped,
                                                   torso=torso)
    except rife_mod.RifeNotInstalled as exc:
        if required:
            raise
        # RIFE is located before the first frame is made, so nothing was made yet: the stage runs again
        # with no interpolator, each ghost giving way to the clean frame beside it and a jump left as filmed.
        repaired, record = repair_mod.repair_jumps(frames, None, facing=facing, screen=screen, overlapped=overlapped,
                                                   torso=torso)
        record["applied"] = bool(record["replaced"])
        if "unmade" in record:
            record.update(why="RIFE not installed — cut as filmed" if not record["replaced"] else
                          "RIFE not installed — the jump cut as filmed, each filmed ghost given way to the clean frame beside it",
                          rife=str(exc), install=rife_mod.install_command())
        elif record.get("ease_skipped") == "no interpolator":
            # no jump, but a part that rushes: the frames either side of it were to be made again at eased times
            record.update(why="RIFE not installed — the rush cut as filmed" if not record["replaced"] else
                          "RIFE not installed — the rush cut as filmed, each filmed ghost given way to the clean frame beside it",
                          rife=str(exc), install=rife_mod.install_command())
        return repaired, record, overlap
    frames = repaired
    record["applied"] = bool(record["replaced"])
    if calls > wrap_calls:
        record["interpolator"] = dict(made_by[0]) if made_by else {"kind": "injected"}
    return frames, record, overlap


def _ghost_lines(jump_repair: dict[str, Any]) -> list[str]:
    """What became of each filmed ghost the jump repair stage read, in words (`video-loop: warning:` lines)."""
    lines = []
    for r in jump_repair.get("rounds", []):
        if r.get("why") != "ghost":
            continue
        j = r["target"]
        line = (f"frame {j} of the cut carries a part-covered band over {100 * r['original']['ghost']:.2f} % of its body, at least "
                f"{rife_mod.GHOST_THICK} px thick — a filmed ghost; ")
        if r["taken"] == "rife":
            lines.append(line + f"given way to RIFE's frame between frames {r['neighbours'][0]} and {r['neighbours'][1]} "
                         f"(cycle/frame-{j:03d}.png) (docs/loop-repair.md section 2)")
            continue
        if r["taken"] is None:
            lines.append(line + f"and so are the frames beside it ({r['neighbours'][0]} and {r['neighbours'][1]}); kept, as there "
                         f"is no clean frame to give way to (cycle/frame-{j:03d}.png) — film this direction again (docs/loop-repair.md section 2)")
            continue
        if r["proposal"] is not None:
            why = f"RIFE's frame between its neighbours has a fault ({', '.join(r['faults'])})"
        elif r["no_proposal"] == "no interpolator":
            why = f"RIFE is not installed, so no frame between its neighbours was made (`{rife_mod.install_command()}`)"
        else:
            why = r["no_proposal"]
        lines.append(line + f"given way to frame {r['taken']['source']} beside it, shown twice in a row (cycle/frame-{j:03d}.png): "
                     f"{why} (docs/loop-repair.md section 2)")
    return lines


def _overlap_lines(overlap: dict[str, Any]) -> list[str]:
    """What became of the overlapped wrap (`repair.overlap_wrap`), in words (`video-loop: warning:` lines): the frames a
    person should look at where it was made, and why it was not where it was not."""
    said = (f"the cut's wrap does not close (seam {overlap['seam']:.2f} over {overlap['seam_max']:g}): a part on a beat of its "
            "own closes on no cut of two steps")
    if overlap["frames"]:
        taken = overlap["tries"][-1]
        return [f"frame {r['frame']} was made at the wrap between the clip's frame a cycle apart and its own, and may cross-fade "
                f"two drawings inside its outline (crossfade {r['proposal']['crossfade']:.4f}); kept, a reading to look at "
                "(docs/loop-repair.md section 2)" for r in taken["made"] if r["look"]]
    if "rife" in overlap:
        return [f"{said}, and RIFE is not installed, so the overlapped wrap was not made — run `{rife_mod.install_command()}` "
                "and cut it again (docs/loop-repair.md section 2)"]
    if "skipped" in overlap:
        return [f"{said}; the overlapped wrap was not made: {overlap['skipped']} (docs/loop-repair.md section 2)"]
    return [f"{said}, and the overlapped wrap was refused ({_overlap_refused(overlap)}): cut as filmed (docs/loop-repair.md section 2)"]


def _overlap_refused(overlap: dict[str, Any]) -> str:
    """Why each run of the overlapped wrap was refused, in words."""
    def why(t: dict[str, Any]) -> str:
        if "unmade" not in t:
            return ", ".join(t["rejected_for"])
        stopped = next(r for r in t["made"] if r["faults"])  # the first frame with a fault ended the run
        rest = ", the rest not made" if t["unmade"] else ""
        return f"{', '.join(t['rejected_for'])} ({', '.join(stopped['faults'])} at frame {stopped['frame']}{rest})"

    return "; ".join(f"{t['width']} frames {t['side']} the wrap: {why(t)}" for t in overlap["tries"])


def _unfixed_lines(jump_repair: dict[str, Any]) -> list[str]:
    """Each drawing the jump repair left off the walk's flow (`jump_repair.unfixed`), in words."""
    lines = []
    for u in jump_repair.get("unfixed") or []:
        frames = u["frames"]
        why = (f"the group that would bring it back was refused ({', '.join(u['refused'])}), so it plays as filmed"
               if "refused" in u else f"no group of up to {jump_repair['span']} drawings brings it under {jump_repair['limit']:g}")
        lines.append(f"frame{'s' if len(frames) > 1 else ''} {', '.join(map(str, frames))} of the loop lie{'' if len(frames) > 1 else 's'} "
                     f"{u['off']:.2f}x its window's usual step off the walk's flow after the jump repair: {why}")
    return lines


def _pop_settled(record: dict[str, Any], frames: list[Image.Image], cut: list[Image.Image]) -> None:
    """What became of a part that pops at the wrap of the cut taken (`part_pop`, docs/loop-repair.md section 2, "The part
    pop"), recorded: `outcome` — `none` where no part pops, `closed` where the jump repair changed frames of the cut and no
    part pops on the loop as it now plays, `named` where it plays as filmed — and the pops `left`, read again (`after`)
    where a frame of the cut was changed."""
    if not record["pops"]:
        record.update(outcome="none", left=[])
        return
    left = record["pops"]
    if any(a is not b for a, b in zip(frames, cut)):
        again = part_pop_mod.public(part_pop_mod.read(frames))
        record["after"] = {k: again[k] for k in ("drawings", "hold", "parts", "followed", "nearest", "pops")}
        left = again["pops"]
    record.update(outcome="named" if left else "closed", left=left)


def _pop_said(p: dict[str, Any], together: int = 1) -> str:
    """A part that pops (`part_pop.read`), in words; `together`, the parts that pop over the same run, the worst said."""
    return (f"the part at ({p['at'][0]:.0f}, {p['at'][1]:.0f}) px of frame {p['from']} moves {p['body_pct']:.2f} % of the body's "
            f"height into frame {p['to']}, {p['jerk_pct']:.2f} % off the curve its steps either side draw ({p['speed']:.1f}x its "
            f"usual step, {p['curve']:.1f}x the step that curve gives, {p['lead']:.2f}x its largest such jump elsewhere; it "
            f"{'comes back' if p['kind'] == 'flung' else 'stays'}{_beside(together)})")


def _beside(together: int) -> str:
    """The parts that pop with the one said, in words."""
    if together < 2:
        return ""
    return f"; {together - 1} part{'s' if together > 2 else ''} beside it pop{'' if together > 2 else 's'} with it"


def _pop_runs(pops: list[dict[str, Any]]) -> list[tuple[dict[str, Any], int]]:
    """The pops (worst first) grouped by the run they pop over: the worst of each and how many pop there."""
    runs: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for p in pops:
        runs.setdefault((p["from"], p["to"]), []).append(p)
    return [(group[0], len(group)) for group in runs.values()]


RETAKE_REASON = "part-pop"


def part_retake(record: dict[str, Any], cut: tuple[int, int], *, given: bool) -> dict[str, Any] | None:
    """A take to film again where a part pops at the wrap of the loop as it plays (`part_pop.left`), as
    `video-cycle-align`'s `retake` is: `reason` (RETAKE_REASON), `reasons`, the numbers under `part_pop` — the pops left,
    the cut, and whether the search chose it (`searched`) — the `limits`, and `remedy`: `retake` where the engine's search
    chose the cut, `recut-or-retake` where it was given (`given`: --cycle fixed, or pinned). No other cut is taken for it.
    None where no part pops."""
    if not record.get("left"):
        return None
    return {"reason": RETAKE_REASON, "reasons": [RETAKE_REASON],
            "part_pop": {"pops": [{k: p[k] for k in ("seed", "from", "to", "at", "body_pct", "jerk_pct", "speed", "curve", "lead",
                                                     "kind", "margin")} for p in record["left"]],
                         "cut": list(cut), "searched": not given},
            "limits": {k: record[k] for k in ("floor", "speed", "curve", "lead")},
            "remedy": "recut-or-retake" if given else "retake"}


STAND_REASON = "start-stop"


def stand_retake(cycle: dict[str, Any]) -> dict[str, Any] | None:
    """A take to film again where the walk starts or stops in it: the clip shows its steps (`step_count`), its feet stand
    together longer than any pass (the reading's `stands`) — the walk stands still — and the cut kept is not counted
    two steps (its `cut` verdict is not `two`): no cut of two steps closes between the stands. `reason` STAND_REASON,
    `reasons`, the stands, the run limit, the crossings and the cut kept with the count's word on it under
    `start_stop`, and `remedy` `retake`. None otherwise."""
    counted = cycle.get("step_count") or {}
    cut = counted.get("cut") or {}
    if not counted.get("readable") or not counted.get("stands") or cut.get("verdict") == "two":
        return None
    return {"reason": STAND_REASON, "reasons": [STAND_REASON],
            "start_stop": {"stands": counted["stands"], "run_limit": counted["run_limit"], "crossings": counted["crossings"],
                           "cut": [cycle["start"], cycle["length"]],
                           **{k: cut[k] for k in ("verdict", "code", "why") if k in cut}},
            "remedy": "retake"}


def retakes(*records: dict[str, Any] | None) -> dict[str, Any] | None:
    """The loop's `retake`: the first reason's record, every reason in `reasons` and each other reason's numbers beside
    it. None where there is none."""
    found = [r for r in records if r]
    if not found:
        return None
    out = dict(found[0])
    for other in found[1:]:
        out.update({k: v for k, v in other.items() if k not in ("reason", "reasons", "remedy")})
        out["reasons"] = [*out["reasons"], *other["reasons"]]
    return out


def _stand_line(record: dict[str, Any]) -> str:
    """A walk that starts or stops in the take (`stand_retake`), in words (a `video-loop: warning:` line)."""
    said = record["start_stop"]
    stands = ", ".join(f"{a}..{b}" for a, b in said["stands"])
    why = f": {said['why']}" if "why" in said else ""
    return (f"film this direction again ({STAND_REASON}) — the feet stand together {stands}, longer than any pass "
            f"({said['run_limit']:g} frames): the walk starts or stops in the take, and the cut {said['cut'][0]}/{said['cut'][1]} "
            f"kept is not two steps ({said.get('verdict', 'unread')}{why})")


def _pop_lines(record: dict[str, Any], *, given: bool) -> list[str]:
    """Each pop that plays as filmed, a take to film again (`retake`), in words (`video-loop: warning:` lines)."""
    why = ("the cut was given (--cycle fixed or pinned): cut it where no part pops, or film it again" if given else
           "the search's cut is kept as cut, and no other cut is taken for a part: film it again")
    return [f"film this direction again ({RETAKE_REASON}) — {_pop_said(p, together)}: {why}" for p, together in
            _pop_runs(record.get("left") or [])]


def _step_reason(jump_repair: dict[str, Any], frames: set[int]) -> dict[str, Any]:
    """Why a step between drawings jumps, where the jump repair says it: a drawing of it (`frames`) left off the walk's
    flow (`jump_repair.unfixed`) — the group that would bring it back refused (`repair-refused`, its faults), or none
    found (`unfixed`) — a refused group's drawing first: the drawings beside it read off the flow because it is; with the
    failure line's `words`. Elsewhere the reason is not known (`reason` None)."""
    for u in sorted(jump_repair.get("unfixed") or [], key=lambda u: "refused" not in u):
        if not frames & set(u["frames"]):
            continue
        at = f"frame{'s' if len(u['frames']) > 1 else ''} {', '.join(map(str, u['frames']))} lie{'' if len(u['frames']) > 1 else 's'} off the walk's flow"
        if "refused" in u:
            return {"reason": "repair-refused", "frames": u["frames"], "refused": u["refused"],
                    "words": f": {at}, and the jump repair's frame there was refused ({', '.join(u['refused'])}), so it plays as filmed"}
        return {"reason": "unfixed", "frames": u["frames"],
                "words": f": {at}, and no group of up to {jump_repair['span']} drawings brings it back"}
    return {"reason": None}


def seam_reason(report: dict[str, Any], cycle: dict[str, Any]) -> dict[str, Any]:
    """Why the seam gate refuses a cut, where the warning lines say it: a cut of two steps by the clip's crossings whose
    wrap a part on a beat of its own leaves open (`own-beat`) — the overlapped wrap was asked (`wrap_overlap`,
    `_overlap_lines`), or no two-step cut closes (`local_cycle.step_verdict`). `overlap_wrap` says what became of the
    overlapped wrap (made, skipped, rife not installed, refused; None where it was not asked) and `words` the failure
    line's. Elsewhere the reason is not known (`reason` None)."""
    overlap = report.get("wrap_overlap")
    counted = cycle.get("step_count") or {}
    if overlap is None and not (counted.get("chosen_by") == "two steps" and not counted.get("gated")):
        return {"reason": None}
    words = "the cut covers two steps by the clip's crossings, and a part on a beat of its own (a tail, a held prop) closes on no cut of two steps"
    if overlap is None:
        outcome = None
    elif overlap["frames"]:
        outcome = "made"
        words += f": the overlapped wrap was made (frames {', '.join(map(str, overlap['frames']))}) and the wrap still does not close"
    elif "rife" in overlap:
        outcome = "rife not installed"
        words += ": RIFE is not installed, so the overlapped wrap was not made"
    elif "skipped" in overlap:
        outcome = "skipped"
        words += f": the overlapped wrap was not made ({overlap['skipped']})"
    else:
        outcome = "refused"
        words += f": the overlapped wrap was refused ({_overlap_refused(overlap)})"
    return {"reason": "own-beat", "overlap_wrap": outcome, "words": words}


def _bridge_seam(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, *, facing: str, required: bool,
                 seam_max: float, hold: int | None, skip: str | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """`repair_mod.seam_bridge` on a loop that is not a walk, with RIFE located only once a frame is to be made: a wrap
    that reads as a step of the loop needs no binary. The record names the interpolator that made the frames.

    A frame to make and no RIFE installed: `required` lets `RifeNotInstalled` through; otherwise the wrap is read again
    with no interpolator, cut as filmed, and the record says why (`rife`, `install`, the run it stopped at `unmade`)."""
    made_by: list[dict[str, str]] = []

    def lazy(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        nonlocal interpolate
        if interpolate is None:
            found = rife_mod.Rife()
            made_by.append(found.describe())
            interpolate = found
        return interpolate(a, b, t)

    asked = partial(repair_mod.seam_bridge, limit=seam_max, hold=hold, facing=facing, skip=skip)
    try:
        frames, record = asked(frames, lazy)
    except rife_mod.RifeNotInstalled as exc:
        if required:
            raise
        frames, record = asked(frames, None)
        record.update(why="RIFE not installed — the wrap cut as filmed", rife=str(exc), install=rife_mod.install_command())
    record["applied"] = bool(record["replaced"])
    if record["calls"]:
        record["interpolator"] = {**made_by[0], **interpolate.counts()} if made_by else {"kind": "injected"}
    return frames, record


def _bridge_lines(bridge: dict[str, Any]) -> list[str]:
    """What became of a wrap the seam bridge read as a jump (`repair.seam_bridge`), in words (`video-loop: warning:`
    lines): the frames made and the ones to look at, or why none was made. A wrap that reads as a step says nothing."""
    if "skipped" in bridge:
        return []
    said = (f"the loop's wrap jumps (its step {bridge['seam']:.2f}x the mean of the {bridge['around']} steps either side, "
            f"over {bridge['limit']:g})")
    if bridge["replaced"]:
        taken = bridge["tries"][-1]
        a, b = taken["anchors"]
        return [f"{said}: frames {', '.join(map(str, taken['frames']))} were made again by RIFE between frames {a} and {b} "
                "(cycle/), so the loop plays across it — look at them in the loop, or cut it with --repair off "
                "(docs/loop-repair.md section 2)",
                *(f"frame {m['frame']} was made at the wrap with RIFE's frame between frames {a} and {b}, which may "
                  f"cross-fade two drawings inside its outline (crossfade {m['proposal']['crossfade']:.4f}); kept, a reading "
                  "to look at (docs/loop-repair.md section 4)" for m in taken["made"] if m["look"])]
    if "unmade" in bridge:
        return [f"{said}, and RIFE is not installed, so no frame was made round it — run `{rife_mod.install_command()}` "
                "and cut it again (docs/loop-repair.md section 2)"]
    reasons = {"line": "the straight line between its anchors does not close it", "fault": "a frame made has a fault",
               "seam": "the frames made do not close it"}
    refused = "; ".join(f"{t['width']}: {reasons[t['rejected_for']]}" + (f" ({', '.join(t['faults'])})" if t.get("faults") else "")
                        for t in bridge["tries"])
    return [f"{said}, and no frames made round it close it ({refused}): cut as filmed (docs/loop-repair.md section 2)"]


def _located(interpolate: rife_mod.Interpolate | None, made_by: list[dict[str, str]]) -> Any:
    """`interpolate`, or RIFE located the first time a frame is asked for (`made_by` then names it)."""
    def lazy(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        nonlocal interpolate
        if interpolate is None:
            found = rife_mod.Rife()
            made_by.append(found.describe())
            interpolate = found
        return interpolate(a, b, t)

    lazy.counts = lambda: interpolate.counts() if made_by else {}  # type: ignore[attr-defined]
    return lazy


def _fill_held(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, *, ring: bool, same: list[bool],
               mode: str, hold: int | None, judge_scale: float) -> tuple[list[Image.Image], dict[str, Any]]:
    """`held_fill.fill` on the cut, with RIFE located only once a frame is to be made: a cut with no held cell, or only
    runs kept as filmed, needs no binary. The record names the interpolator that made the frames. A frame to make and no
    RIFE installed: the held cells are delivered as filmed, and the record says which and how to install it (`unmade`)."""
    made_by: list[dict[str, str]] = []
    lazy = _located(interpolate, made_by)
    asked = partial(held_fill_mod.fill, ring=ring, same=same, mode=mode, hold=hold, judge_scale=judge_scale)
    try:
        frames, record = asked(frames, lazy)
    except rife_mod.RifeNotInstalled as exc:
        # RIFE is located before the first frame is made, so nothing was made yet: read again with no interpolator.
        frames, record = asked(frames, None)
        record["unmade"].update(why="RIFE is not installed", rife=str(exc), install=rife_mod.install_command())
        return frames, record
    if record["made"]:
        record["interpolator"] = {**made_by[0], **lazy.counts()} if made_by else {"kind": "injected"}
    return frames, record


def _upsample(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, *, ring: bool, fps: float,
              out_fps: float, judge_scale: float) -> tuple[list[Image.Image], dict[str, Any]]:
    """`held_fill.upsample` on the cut, with RIFE located only once a frame is to be made. No RIFE installed: the cut is
    delivered at its own rate, never with a frame shown again for each cell between, and the record says so."""
    made_by: list[dict[str, str]] = []
    lazy = _located(interpolate, made_by)
    try:
        out, record = held_fill_mod.upsample(frames, lazy, ring=ring, fps=fps, out_fps=out_fps, judge_scale=judge_scale)
    except rife_mod.RifeNotInstalled as exc:
        return frames, {"asked": out_fps, "fps": fps, "applied": False, "cells": len(frames),
                        "why": "RIFE is not installed", "rife": str(exc), "install": rife_mod.install_command()}
    if record["made"]:
        record["interpolator"] = {**made_by[0], **lazy.counts()} if made_by else {"kind": "injected"}
    return out, record


def _air_retime(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, **kw: Any
                ) -> tuple[list[Image.Image], dict[str, Any], dict[str, Any] | None]:
    """`root_motion.retime` on a cut played once, with RIFE located only once a frame is to be made: an air given its
    own length at the clip's rate makes none. A frame to make and no RIFE installed: the cut is delivered as filmed, and
    the record says which air and how to install it."""
    made_by: list[dict[str, str]] = []
    lazy = _located(interpolate, made_by)
    try:
        frames_out, record, plan = root_motion_mod.retime(frames, lazy, **kw)
    except rife_mod.RifeNotInstalled as exc:
        roots, touch = root_motion_mod.reading(frames, floor=kw["floor"], times=kw["times"], declared=kw["declared"])
        before = root_motion_mod.read(roots, touch, [k / kw["fps"] for k in range(len(frames))])["air"]
        return frames, {"asked": dict(kw["asked"]), "applied": False, "before": before, "why": "RIFE is not installed",
                        "rife": str(exc), "install": rife_mod.install_command()}, None
    if record["applied"] and record["made"]:
        record["interpolator"] = {**made_by[0], **lazy.counts()} if made_by else {"kind": "injected"}
    return frames_out, record, plan


def _air_lines(name: str, record: dict[str, Any]) -> list[str]:
    """What became of `--air-retime`, in words: the air not retimed and why, or the cells of it that show the frame
    beside them again."""
    if not record["applied"]:
        if record["why"] == "RIFE is not installed":
            spans = ", ".join(f"cells {s['cells'][0]}-{s['cells'][1]}" for s in record["before"] if s["seconds"] is not None)
            return [f"the air of {name} ({spans}) was not retimed — RIFE is not installed, so no frame between was made; "
                    f"run `{record['install']}` and cut it again (docs/video-pipeline.md, \"Root motion\")"]
        return [f"--air-retime: {record['why']}: cut as filmed"]
    again = record["shown_again"]
    return [f"{len(again)} cell{'s' if len(again) > 1 else ''} of the retimed air ({', '.join(map(str, again))}) show the "
            "frame beside them again: no frame between was made there without a fault (docs/video-pipeline.md, \"Root "
            "motion\")"] if again else []


def _fill_lines(record: dict[str, Any]) -> list[str]:
    """What became of the cut's held cells, in words (`video-loop: warning:` lines): the cells left for want of RIFE, the
    runs a frame between was refused for, the frames made to look at. A run kept as the motion's own says nothing."""
    lines = []
    if "unmade" in record:
        cells = record["unmade"]["cells"]
        lines.append(f"{len(cells)} held cell{'s' if len(cells) > 1 else ''} ({', '.join(map(str, cells))}) shown as filmed — "
                     f"RIFE is not installed, so no frame between was made ({held_fill_mod.said(record['before'])} poses/cells); "
                     f"run `{record['unmade']['install']}` and cut it again (docs/loop-repair.md section 7)")
    for run in record["before"]["held_runs"]:
        if run.get("faults") is not None:
            last = run["start"] + run["length"] - 1
            lines.append(f"held cells {run['start']}-{last} kept as filmed: {run['why']} — look at them in the loop, or film "
                         "it again (docs/loop-repair.md section 7)")
    for made in record.get("frames", []):
        if made["look"]:
            a, b = made["between"]
            lines.append(f"cell {made['cell']} was made between cells {a} and {b}, which may cross-fade two drawings inside "
                         "its outline; kept, a reading to look at (docs/loop-repair.md section 4)")
    return lines


def _out_fps_lines(record: dict[str, Any]) -> list[str]:
    """What became of a rate asked above the clip's (`--out-fps`), in words: not met for want of RIFE, or cells where no
    frame between was made and the frame beside them is shown again."""
    if not record["applied"]:
        return [f"--out-fps {record['asked']:g} was not met — RIFE is not installed, so the cut is delivered at its own "
                f"{record['fps']:g} fps; run `{record['install']}` and cut it again (docs/loop-repair.md section 7)"]
    again = record["shown_again"]
    if not again:
        return []
    return [f"{len(again)} cell{'s' if len(again) > 1 else ''} of --out-fps {record['asked']:g} ({', '.join(map(str, again))}) "
            "show the frame beside them again: no frame between was made there without a fault (docs/loop-repair.md section 7)"]


def _shifts(analysis: dict[str, Any]) -> tuple[np.ndarray, int]:
    """The analysis translation `auto_motion.analyse` measured, whole pixels a frame, and the room it needs."""
    shifts = np.rint(np.asarray(analysis["analysis_only_translation_xy"], dtype=np.float64)).astype(int)
    return shifts, int(np.abs(shifts).max()) + 1


def moved_frames(frames: list[Image.Image], analysis: dict[str, Any]) -> list[Image.Image]:
    """Every frame whole, moved as `moved_alpha` moves its alpha, on one canvas: what `SeamFlow` reads a cut's wrap
    on, so a body that walks across the frame or toward it is read at the wrap as the cut will be corrected."""
    shifts, pad = _shifts(analysis)
    out = []
    for f, (dx, dy) in zip(frames, shifts):
        canvas = Image.new("RGBA", (f.width + 2 * pad, f.height + 2 * pad))
        canvas.paste(f, (pad + int(dx), pad + int(dy)))
        out.append(canvas)
    return out


def moved_alpha(frames: list[Image.Image], analysis: dict[str, Any]) -> list[np.ndarray]:
    """The alpha of every frame, each moved by the analysis translation `auto_motion.analyse` measured
    (its drift taken out, the way the cut will be corrected), cropped once to the clip's subject:
    what `wrap_pop_on` and `HeldEdge` read a cut on."""
    shifts, pad = _shifts(analysis)
    boxes = [f.getchannel("A").point(lambda v: 255 if v >= repair_mod.ALPHA_SOLID else 0).getbbox() for f in frames]
    boxes = [b for b in boxes if b]
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    w, h = box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad
    moved = []
    for f, (dx, dy) in zip(frames, shifts):
        a = np.zeros((h, w), dtype=np.uint8)
        crop = np.asarray(f.getchannel("A").crop(box))
        a[pad + dy:pad + dy + crop.shape[0], pad + dx:pad + dx + crop.shape[1]] = crop
        moved.append(a)
    return moved


def wrap_pop_on(moved: list[np.ndarray]) -> Any:
    """`repair.seam_pop` of a cut (start, length) of the moved alpha (`moved_alpha`): the top band's
    jump at the wrap, for `local_cycle.detect` to rank its candidates by. A cut's value is kept once
    measured."""
    measured: dict[tuple[int, int], dict[str, Any]] = {}

    def pop(start: int, length: int) -> dict[str, Any]:
        if (start, length) not in measured:
            try:
                measured[start, length] = repair_mod.seam_pop([a.astype(np.float32) / 255.0 for a in moved[start:start + length]])
            except ValueError as exc:
                # A frame with nothing in the top band: no jump read, and no pop to rank it by —
                # `local_cycle` neither takes it as closing nor as popping (`skipped`, why).
                measured[start, length] = {"skipped": str(exc), "reference": repair_mod.SEAM_POP_REFERENCE}
        return measured[start, length]
    return pop


class HeldEdge:
    """The held side of the moved alpha (`moved_alpha`), read by `local_cycle` only once a cut's top
    band pops: `side` of a cut (`repair.held_side`, or `skipped`) and `seam` of a cut on a side
    (`repair.held_edge`, or `skipped` with why). A side's edges are read once, on first use."""

    reference = repair_mod.HELD_EDGE_REFERENCE

    def __init__(self, moved: list[np.ndarray]):
        self.moved = moved
        self.frames = len(moved)
        self._edges: dict[str, np.ndarray] = {}

    def _masks(self, start: int, stop: int) -> list[np.ndarray]:
        return [a >= repair_mod.HELD_EDGE_ALPHA for a in self.moved[start:stop]]

    def side(self, start: int, length: int) -> dict[str, Any]:
        try:
            return {"side": repair_mod.held_side(self._masks(start, start + length))}
        except ValueError as exc:
            return {"skipped": str(exc)}

    def seam(self, start: int, length: int, side: str) -> dict[str, Any]:
        if side not in self._edges:
            self._edges[side] = repair_mod.side_edges(self._masks(0, self.frames), side)
        try:
            return repair_mod.held_edge(self._edges[side], start, length)
        except ValueError as exc:
            return {"skipped": str(exc), "reference": repair_mod.HELD_EDGE_REFERENCE}


class SeamFlow:
    """The flow at a cut's wrap, for `local_cycle.by_steps` to rank two-step cuts by: the flow off of the cut's last and
    first frames (`off`), their worst windows (`window`, fractions of the clip's union box) and turns there (`turn`) —
    `repair.ring_scores` on the clip's path, its frames moved as the cut will be corrected (`moved_frames`), read once
    on first use (`repair.clip_flow_states`)."""

    def __init__(self, frames: list[Image.Image], analysis: dict[str, Any]):
        self.frames, self.analysis = frames, analysis
        self._states: np.ndarray | None = None

    def __call__(self, start: int, length: int) -> dict[str, Any]:
        if self._states is None:
            self._states = repair_mod.clip_flow_states(moved_frames(self.frames, self.analysis))
        scores = repair_mod.ring_scores(self._states[start:start + length])
        at = (length - 1, 0)
        return {"off": [round(float(scores["off"][k]), 3) for k in at], "turn": [round(float(scores["turn"][k]), 3) for k in at],
                "window": [[round(v, 3) for v in scores["windows"][int(scores["window"][k])]] for k in at]}


def corrected_cut(frames: list[Image.Image]) -> list[Image.Image]:
    """A motion-auto cut's frames corrected as the cut will be (`auto_motion.discover`, `auto_motion.correct_cycle`,
    as `run_loop` corrects the cut it takes). Raises ValueError where they cannot be."""
    regions, _ = auto_motion.discover(frames, reference_index=0)
    frames, _ = auto_motion.correct_cycle(frames, regions, coarse_dx=body_wrap_offset(frames, search=motion_anchor.COARSE_SEARCH))
    return frames


def seam_on_cells(frames: list[Image.Image]) -> float:
    """The seam gate's reading of a motion-auto cut before it is cut: its frames corrected as the cut will be
    (`corrected_cut`), then read as `wrap_on_cells` reads them. Infinite where the frames cannot be corrected."""
    try:
        frames = corrected_cut(frames)
    except ValueError:
        return math.inf
    return wrap_on_cells(frames)


class CutOnCells:
    """A motion-auto cut (`start`, `length` of `frames`) read before it is cut as the gates read the loop: its frames
    corrected as the cut will be (`corrected_cut`), then its seam (`seam`, as `seam_on_cells` reads it) and the top band's
    jump into its first frame (`pop`, `repair.seam_pop_of`: the seam pop gate's reading, `jolt.seam_pop`), short of the
    jump repair, which runs on the cut once it is taken. A cut is corrected once, whichever is read first, and none of
    its frames is kept. `gate` is `pop` for `local_cycle` (`pop_gate`), or None where the seam pop gate is off."""

    def __init__(self, frames: list[Image.Image], *, pop_max: float | None):
        self.frames, self.pop_max = frames, pop_max
        self._read: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}

    def _of(self, start: int, length: int) -> tuple[float, dict[str, Any]]:
        if (start, length) not in self._read:
            try:
                cut = corrected_cut(self.frames[start:start + length])
            except ValueError as exc:
                self._read[start, length] = (math.inf, {"skipped": f"the cut's frames could not be corrected ({exc})"})
            else:
                self._read[start, length] = (wrap_on_cells(cut), repair_mod.seam_pop_of(cut))
        return self._read[start, length]

    def seam(self, start: int, length: int) -> float:
        return self._of(start, length)[0]

    def pop(self, start: int, length: int) -> dict[str, Any]:
        measured = self._of(start, length)[1]
        if "skipped" in measured:
            return {"skipped": measured["skipped"], "max": self.pop_max}
        return {"pop": measured["pop"], "over": self.pop_max is not None and measured["pop"] > self.pop_max, "max": self.pop_max}

    @property
    def gate(self) -> Any:
        return self.pop if self.pop_max is not None else None


def cells_box(frames: list[Image.Image]) -> tuple[int, int, int, int]:
    """The union box of a loop's frames, every pixel not fully transparent: the crop its rendered cells are read on."""
    boxes = [f.getbbox() for f in frames]
    boxes = [b for b in boxes if b]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def cell_features(frames: list[Image.Image], box: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """A loop's frames on the analysis thumbnail, each cropped to `box` (their union box, `cells_box`, where none is
    given): the rendered cells, short of the strip's scale, as the seam gate (`wrap_on_cells`) and the seam bridge
    (`repair.seam_bridge`) read them."""
    box = box if box is not None else cells_box(frames)
    return np.stack([_small_features(f.crop(box)) for f in frames])


def wrap_on_cells(frames: list[Image.Image]) -> float:
    """The seam gate's reading of a walk's frames as corrected: cropped to their union box, the wrap over the mean step
    on the analysis thumbnail, between the loop's drawings (`seam_drawings`) — the rendered cells the gate reads, short of
    the strip's scale."""
    flat = cell_features(frames)[seam_drawings(frames)[0]]
    step = float(np.abs(flat[1:] - flat[:-1]).mean())
    return float(np.abs(flat[-1] - flat[0]).mean()) / step if step > 0 else math.inf


def read_steps(frames: list[Image.Image], D: np.ndarray, *, view: str | None = None, facing: str = "right",
               signals: dict[str, list[float]] | None = None) -> crossings_mod.Reading:
    """The clip's crossings (`crossings.read`), and whether they mark its cycles (`local_cycle.read_cycles`, on `D`, the
    clip's distances, the body's bob read off `signals` — the frames' leg signals, read here where not given, with the
    body's own (`legs.body_signals`) — the order its feet are drawn in (`legs.feet_order`), and the `view`'s foot cue:
    `legs.shade_cue` from the side or a diagonal, drawn in tones of `legs.TONE`, `legs.depth_cue` from the front or
    back, in pixel rows of the body's median height). Every
    count the clip's steps make is read on it: the search's cut (`_by_steps`), a fixed cut (`fixed_count`) and, carried
    to the loop, the loop as it plays (`ring_steps`)."""
    signals = leg_signals(frames) if signals is None else signals
    try:
        reading = crossings_mod.read(crossings_mod.foot_signals(frames, gap=signals["gap"] if signals else None))
    except ValueError:
        return crossings_mod.Reading(len(frames), "gap", [], why="a frame has no solid body to read the feet from")
    cue = step = None
    if view is not None:
        lateral = view in handed_mod.LATERAL_VIEWS
        cue = [legs_mod.shade_cue(f, facing) if lateral else legs_mod.depth_cue(f) for f in frames]
        heights = signals["height"] if signals else legs_mod.strike_signals(frames)["height"]
        step = legs_mod.TONE if lateral else 1.0 / float(np.median(heights))
    body = {**signals, **legs_mod.body_signals(frames)} if signals else None
    local_cycle.read_cycles(reading, D, view=view, cue=cue, cue_step=step, signals=body,
                            order=[legs_mod.feet_order(f) for f in frames])
    return reading


def ring_steps(frames: list[Image.Image], *, cycles: dict[str, Any] | None = None) -> dict[str, Any]:
    """The steps a loop shows as it plays round: the crossings read on its frames played three times over
    (`crossings.read`), the turn in the middle judged (`Reading.judge`) — a two where the loop still walks two steps,
    joined where the walk goes on. `cycles` is what the clip the loop was cut from said of its crossings
    (`read_steps`): a loop is too short to be read two intervals on, and its crossings mark what the clip's mark."""
    ring = [*frames, *frames, *frames]
    signals = leg_signals(ring)
    try:
        reading = crossings_mod.read(crossings_mod.foot_signals(ring, gap=signals["gap"] if signals else None))
    except ValueError as exc:
        return {"verdict": "uncertain", "code": "unread", "why": str(exc)}
    if cycles is not None and reading.readable:
        reading.mark_cycles(cycles)
    return reading.judge(len(frames), len(frames))


def step_jitter(before: Image.Image, after: Image.Image, *, pixel: float, mean: float) -> dict[str, Any] | None:
    """A step between two drawings that is a pixel's jitter (STEP_JITTER_PX): the move that lays `after` on `before` with
    the least coverage difference (`stop.register`, on alpha), at most STEP_JITTER_PX pixels of the frames as filmed, each
    `pixel` cell pixels (the strip's scale; never under one cell pixel), and the step with that move taken back, on the
    analysis features, within STEP_RATIO_MAX times the loop's `mean` step. The move, in cell pixels, and the ratio of the
    step taken back, or None."""
    from sprite_gen.video import stop as stop_mod  # stop imports this module
    allowed = max(1.0, STEP_JITTER_PX * pixel)
    (dx, dy), _ = stop_mod.register(before, after, max(4, math.ceil(2 * allowed)))
    if max(abs(dx), abs(dy)) > allowed:
        return None
    back = Image.new("RGBA", after.size, (0, 0, 0, 0))
    back.paste(after, (dx, dy))
    ratio = float(np.abs(_small_features(back) - _small_features(before)).mean()) / mean
    return {"move": [dx, dy], "ratio_moved_back": round(ratio, 4)} if ratio <= STEP_RATIO_MAX else None


def drawing_firsts(cells: list[Image.Image]) -> tuple[list[int], int]:
    """A loop's cells as they play, by drawing (`repair.flow_drawings`: a cell held on twos or threes shows its drawing
    again): each drawing's first cell, in play order round the loop, and the hold. A loop drawn a frame each is every
    cell, in order."""
    runs, hold = repair_mod.flow_drawings(repair_mod.flow_states(cells))
    return [run[0] for run in runs], hold


def seam_drawings(cells: list[Image.Image]) -> tuple[list[int], dict[str, Any]]:
    """The cells a walk's or run's seam is read on, in play order round the loop, and the reading (`seam_read`): each
    drawing's first (`drawing_firsts`), as the step gate reads its steps. Read cell by cell, a loop held on twos or threes
    steps nothing and then two or three steps at each drawing: a wrap between two drawings reads two or three times the
    mean step where the loop closes, and a step into a drawing as much where nothing jumps. Every cell of a loop that
    shows fewer than three drawings (`read: cells`), as the step gate skips it; a loop drawn a frame each is every cell
    either way, and reads as it did."""
    firsts, hold = drawing_firsts(cells)
    if len(firsts) < 3:
        return list(range(len(cells))), {"read": "cells", "why": "fewer than three drawings", "drawings": len(firsts), "hold": hold}
    return firsts, {"read": "drawings", "drawings": len(firsts), "hold": hold}


def drawing_steps(cells: list[Image.Image], pixel: float = 1.0) -> dict[str, Any] | None:
    """A loop's cells as they play, read between their drawings (`repair.flow_drawings`: a cell held on twos or threes
    shows its drawing again): each drawing's first cell on the analysis features (`_small_features`) and the step into the
    next drawing's, the last drawing's into the first's across the wrap. The largest of them, the wrap's included, that is
    not a pixel's jitter (`step_jitter`, a drawing's pixel `pixel` cell pixels) over the mean of the others
    (`largest_ratio`, from the cell `largest_from` into the cell `largest_into`, each a drawing's first), the steps over
    STEP_RATIO_MAX that are one (`jitter`, larger first, where there are any), and the wrap's (`wrap_ratio`). None for a
    loop of fewer than three drawings."""
    firsts, hold = drawing_firsts(cells)
    if len(firsts) < 3:
        return None
    flat = np.stack([_small_features(cells[k]) for k in firsts])
    steps = np.abs(np.roll(flat, -1, axis=0) - flat).mean(axis=1)
    mean = float(steps[:-1].mean())
    jitter = []
    # Larger first: a step the ratio holds to the bound only where it is no pixel's jitter. One of the steps the mean is
    # taken over is at most the mean, so the walk stops by it.
    for largest in (int(k) for k in np.argsort(-steps, kind="stable")):
        into = firsts[(largest + 1) % len(firsts)]
        if mean <= 0 or float(steps[largest]) / mean <= STEP_RATIO_MAX:
            break
        moved = step_jitter(cells[firsts[largest]], cells[into], pixel=pixel, mean=mean)
        if moved is None:
            break
        jitter.append({"from": firsts[largest], "into": into, "ratio": round(float(steps[largest]) / mean, 4), **moved})
    return {"read": "drawings", "drawings": len(firsts), "hold": hold, "mean_step": round(mean, 6),
            "largest_ratio": round(float(steps[largest]) / mean, 4) if mean > 0 else None,
            "largest_from": firsts[largest], "largest_into": into,
            "wrap_ratio": round(float(steps[-1]) / mean, 4) if mean > 0 else None,
            **({"jitter": jitter} if jitter else {})}


def fixed_count(frames: list[Image.Image], D: np.ndarray, start: int, length: int, *, view: str | None = None,
                facing: str = "right") -> dict[str, Any]:
    """The clip's own count of the steps a fixed cut covers: the crossings of its frames, steps or cycles as the clip
    says (`read_steps`, on `D`, the clip's distances, as `--anchor motion-auto` reads them), the cut judged
    (`Reading.judge`). Recorded `chosen_by: caller`: the caller named the cut."""
    reading = read_steps(frames, D, view=view, facing=facing)
    return {**reading.record(), "chosen_by": "caller", "cut": reading.judge(start, length)}


def odd_count(cycle: dict[str, Any]) -> bool:
    """A fixed cut the clip's crossings count an odd number of steps (`fixed_count`): its legs swap at the wrap."""
    counted = (cycle.get("step_count") or {}).get("cut") or {}
    return cycle.get("kind") == "fixed" and counted.get("verdict") == "refuted" and bool(counted["k"][0] % 2)


def cycle_apart(files: list[Path], *, start: int, length: int, skip: int, analysis: dict[str, Any],
                motion: dict[str, Any], size: tuple[int, int]) -> dict[str, dict[int, Image.Image]]:
    """The clip's frames a cycle from a motion-auto cut's frames beside its wrap, for `repair.overlap_wrap`: a cycle after
    its first frames ("after") and before its last ("before"), one past the widest run, as far as the clip after its
    lead-in holds them. Each is placed where its cut frame is: moved by the analysis translation between the two
    (`_shifts`, the drift the clip walks over a cycle), then corrected as the cut frame was (`motion`'s shift and padding)."""
    shifts, _ = _shifts(analysis)
    left, top = motion["padding_ltrb"][:2]
    widest = max(repair_mod.OVERLAP_WIDTHS)
    out: dict[str, dict[int, Image.Image]] = {}
    for side, sign in zip(repair_mod.OVERLAP_SIDES, (1, -1)):
        out[side] = {}
        for q in repair_mod.overlap_run(side, widest + 1, length):
            cut, other = start + q, start + q + sign * length
            if not skip <= other < len(files):
                continue
            dx, dy = (int(v) for v in shifts[other - skip] - shifts[cut - skip] + np.asarray(motion["shifts_xy"][q]))
            canvas = Image.new("RGBA", size)
            canvas.paste(Image.open(files[other]).convert("RGBA"), (left + dx, top + dy))
            out[side][q] = canvas
    return out


def _by_steps(cycle: dict[str, Any] | None, refused: ValueError | None, *, frames: list[Image.Image], analysis: dict[str, Any],
              D: np.ndarray, trajectory: np.ndarray, window: CutWindow, searched: dict[str, Any], detect: dict[str, Any],
              seam_max: float, cells: CutOnCells, view: str | None = None, facing: str = "right",
              turn: body_turn_mod.Turn | None = None) -> dict[str, Any]:
    """The cut the clip's own steps choose (`local_cycle.by_steps`, the crossings of the frames the search read,
    sprite_gen/video/crossings.py, steps or cycles as the clip and its `view` say: `read_steps`), recorded
    `step_count`, from the search's cut or its refusal (`refused`).
    `cells` reads a cut of `frames` before it is cut as the seam gate and the seam pop gate read the loop.

    Where the clip shows its steps but no cut of two, and the search's cut is one they refute (one step, three), the
    search runs again on the window it searched (`searched`) refusing such cuts (`detect`'s `judge`). A search that
    finds nothing names each refused window's count, those the crossings refute last, and raises. With `turn`, no cut
    that holds a frame of a body turn is taken, a two-step cut or the search's again."""
    signals = leg_signals(frames)
    reading = read_steps(frames, D, view=view, facing=facing, signals=signals)
    moved = moved_alpha(frames, analysis)
    picked, record = local_cycle.by_steps(
        D, trajectory, reading, found=cycle, refused=refused, min_len=window.lo, max_len=window.max_len or len(frames),
        seam_flow=SeamFlow(frames, analysis), wrap_pop=wrap_pop_on(moved), seam_gate=cells.seam, pop_gate=cells.gate,
        flow_limit=repair_mod.FLOW_LIMIT, seam_max=seam_max,
        gait_floor=detect["gait_floor"], periodicity_min=detect["periodicity_min"], signals=signals, turn=turn)
    if picked is not None and record.get("cut", {}).get("verdict") == "refuted":
        first = record["cut"]
        try:
            picked = local_cycle.detect(D, trajectory, signals=signals, wrap_pop=wrap_pop_on(moved), held_edge=HeldEdge(moved),
                                        length_cap=window.max_len, judge=reading.judge, pop_gate=cells.gate, turn=turn,
                                        **searched, **detect)
        except ValueError as exc:
            picked, refused = None, exc
        record["searched_again"] = {"refuted": first, "why": f"no cut of two steps, and the search's cut is {first['why']}: "
                                                              "searched again, refusing the cuts the crossings refute"}
        if picked is not None:
            record["cut"] = reading.judge(picked["start"], picked["length"])
        elif window.said() and record.get("two_cuts") == 0 and reading.twos(1, len(frames)):
            # The caller's window holds none of the walk's two steps: the window and the walk disagree.
            lengths = sorted({row["cut"][1] for row in reading.twos(1, len(frames))})
            failed = ValueError(f"no cut of two steps in {window.said()}: the clip's crossings show two steps take "
                                f"{lengths[0]}-{lengths[-1]} frames, and every cut the window holds that the search found is "
                                f"{first['why']} — widen the window, or count the steps (--steps)")
            failed.diagnostics = getattr(refused, "diagnostics", {})  # type: ignore[attr-defined]
            refused = failed
    if picked is None:
        diagnostics = getattr(refused, "diagnostics", None)
        if isinstance(diagnostics, dict):
            if reading.readable and diagnostics.get("candidates") and "step_count" not in diagnostics["candidates"][0]:
                local_cycle.judged(diagnostics["candidates"], reading.judge)
                diagnostics["candidates"].sort(key=lambda row: row["step_count"]["verdict"] == "refuted")
                diagnostics["order"] = f"{diagnostics.get('order', 'score')}, windows whose steps the crossings refute after the rest"
            diagnostics["step_count"] = record
        assert refused is not None
        raise refused
    picked["step_count"] = record
    return picked


def _counted(cycle: dict[str, Any], steps: int, *, window: CutWindow, D: np.ndarray, trajectory: np.ndarray,
             frames: list[Image.Image], moved: list[np.ndarray], detect: dict[str, Any], cells: CutOnCells,
             turn: body_turn_mod.Turn | None = None, by: str = "given", length: int | None = None) -> dict[str, Any]:
    """The cut once its steps are counted (`--steps`): 2 keeps it; 1 cuts again in the window two of
    its lengths long, [2L-1, 2L+1], inside the caller's bounds (`window.within`) — not half the clip —
    with the same search (a repeat seen in the clip, the top band and the held side read again) save that
    the cut whose wrap the clip itself plays is taken among the equivalent ones (`local_cycle._closure`),
    and no window twice that. Where the caller's bounds leave none of [2L-1, 2L+1], the count and the
    bounds disagree: a failure, neither followed. Where they leave part of it, that part is read
    (`steps.window`, beside `steps.counted_window`). A clip too short to see such a window repeat is cut
    all the same, the count standing for the repeat (`local_cycle.counted`, `unseen` says why);
    `cycle.coverage` and the seam gate say the rest. Recorded `steps` (`first_cut`: the cut counted; `by`, who counted
    it: "given", or "two-lengths" where the clip did, `_two_lengths`, which reads one `length` of the window at a time).
    With `turn`, no cut that holds a frame of a body turn is taken."""
    first = {"start": cycle["start"], "length": cycle["length"]}
    if steps == 2:
        cycle["steps"] = {"count": 2, "by": "given", "given": 2}
        return cycle
    asked = "--steps 1" if by == "given" else "two lengths"
    counted_window = [2 * first["length"] - 1, 2 * first["length"] + 1]
    lo, hi = window.within(*((length, length) if length is not None else counted_window))
    record = {"count": 2, "by": by, **({"given": 1} if by == "given" else {}), "first_cut": first, "window": [lo, hi]}
    if [lo, hi] != counted_window:
        record["counted_window"] = counted_window
    if lo > hi:
        exc = ValueError(f"{asked}: the cut of {first['length']} frames from {first['start']} is one step, so the cycle is "
                         f"{counted_window[0]}-{counted_window[1]} frames long, and {window.said()} leaves none of that: the count "
                         f"and the window disagree — drop one, or give a window that holds the cycle")
        exc.diagnostics = {**cycle, "steps": {**record, "window": None, "refused": f"outside {window.said()}"}}  # type: ignore[attr-defined]
        raise exc
    signals = leg_signals(frames)
    try:
        found = local_cycle.detect(D, trajectory, min_len=lo, max_len=hi, length_cap=hi, signals=signals,
                                   wrap_pop=wrap_pop_on(moved), held_edge=HeldEdge(moved), double=False, closure=True,
                                   pop_gate=cells.gate, turn=turn, **detect)
    except ValueError as unseen:
        # Too short a clip to see the cycle repeat: the count stands for it (`cycle.coverage` says how short).
        try:
            found = local_cycle.counted(D, trajectory, min_len=lo, max_len=hi, periodicity_min=detect["periodicity_min"],
                                        signals=signals, pop_gate=cells.gate, turn=turn)
        except ValueError as exc:
            failed = ValueError(f"{asked}: the cut of {first['length']} frames from {first['start']} is one step, and the clip "
                                f"({len(D)} frames) has no window {lo}-{hi} frames long to cut: {exc}")
            failed.diagnostics = {**getattr(unseen, "diagnostics", {}), "steps": record}  # type: ignore[attr-defined]
            raise failed from exc
        found["unseen"] = str(unseen)
    found["steps"] = record
    return found


def _witnessed(cycle: dict[str, Any], *, frames: list[Image.Image], view: str | None = None) -> dict[str, Any]:
    """The cut a walk's search and step count chose (`_by_steps`), held to one word before it is cut whichever way it
    was chosen — the search's, two steps by the clip's crossings, crossings read or not: the body's
    (`local_cycle.cut_word`: one of its lines saying so both on the screen and over its feet), or where it says
    no cycle the order the feet are drawn in (`legs.feet_order`, as the step count reads it; not from the side `view`),
    recorded `cut_witness` — not the order where the cut is the search's, kept because none of the clip's two-step cuts
    closes (`declined`). Legs drawn alike loop as well on one step as on two, and crossings read off feet that slide,
    or off a shadow under them on a picture that moves, count half steps.

    - Its own rise and fall twice the cut, or four times: the cut is a cycle, or two, and stands. Cut two cycles, its
      strip still counts two steps where the crossings counted them, and `video-set` leaves it out of its state's cycle
      alignment (local_cycle.FOUR_STEPS, `local_cycle.four_steps`). Where its lowest row rises and falls twice the cut
      more than its top line on the screen, the record carries a mark (`mark`), and nothing is held on it.
    - Else nothing witnesses the cut handed out whole cycles, and it is held (`held: unwitnessed`): kept — two of the
      clip's crossing intervals where it counted them, else as the search chose it — and named
      (local_cycle.UNWITNESSED, `held_verdict`); `video-set` leaves it out of its state's cycle alignment
      (`local_cycle.unwitnessed`). So is a cut the body rises and falls once in (`says` one step): one step of a walk,
      or a whole cycle of a body that rises and falls once a cycle — a limp, a body with no legs that bounces as it
      goes — which nothing that reads no leg tells apart. It is not cut again two long on its word: that would loop
      two of such a body's cycles. A cut two lengths long comes only from the clip's own picture, read before this
      (`_two_lengths`), and its cut is the cut read here.

    A cut whose crossings the step count left on steps, nothing witnessing two steps an interval (`cycles.unwitnessed`),
    is held so already, and named on its step count (`held: step count`). Only a walk or a run comes here
    (motion-auto, its cycle searched, no count given)."""
    first = [cycle["start"], cycle["length"]]
    if ((cycle.get("step_count") or {}).get("cycles") or {}).get("unwitnessed"):
        cycle["cut_witness"] = {"cut": first, "held": "step count"}
        return cycle
    strike = leg_signals(frames)
    order = None if view == local_cycle.SIDE_VIEW or not strike else [legs_mod.feet_order(f) for f in frames]
    said = {"cut": first, **local_cycle.cut_word({**strike, **legs_mod.body_signals(frames)} if strike else None,
                                                 cycle["length"], order=order,
                                                 declined=(cycle.get("step_count") or {}).get("declined"))}
    cycle["cut_witness"] = said if said["says"] in ("two steps", "four steps") else {**said, "held": "unwitnessed"}
    return cycle


def _motion_auto(frames: list[Image.Image], files: list[Path], D: np.ndarray, trajectory: np.ndarray, analysis: dict[str, Any],
                 *, fps: float, window: CutWindow, prof: LoopProfile, state: str, steps: int | None, seam_max: float,
                 seam_pop_max: float | None, scratch: _Scratch, said: dict[str, Any],
                 turn: body_turn_mod.Turn | None = None, view: str | None = None, facing: str = "right"
                 ) -> tuple[dict[str, Any], list[Path], dict[str, Any], list[float] | None]:
    """`--anchor motion-auto`'s cut of `frames` — the clip after its lead-in, held, `files` written of them and `D`,
    `trajectory` and `analysis` read on them (auto_motion.analyse): the search, the gait fallback after a first search
    that finds nothing, then the count, given (`steps`, `_counted`) or the clip's crossings (`_by_steps`) and, for a cut
    one step long that neither counted, the clip two lengths on (`_two_lengths`), and the cut so chosen held to one
    word (`_witnessed`; `view`, the view the clip is filmed from, as the step count reads it). Returns the
    cut, the files and the analysis it was read on (the gait fallback's, where it scaled the frames back), and the factor
    the gait fallback scaled each frame by (None where it did not); `said`
    takes what the report adds (`automatic_motion_analysis` read again, `gait_fallback`), whether a cut is found or the
    search raises. With `turn` (body_turn.Turn), no cut that holds a frame of a body turn is taken."""
    lo, hi = window.lo, window.hi
    detect = dict(gait_floor=round(prof.min_seconds*fps), periodicity_min=PERIODICITY_MIN,
                  double_tolerance=GAIT_DOUBLE_TOL, double_search=GAIT_DOUBLE_SEARCH)
    searched: dict[str, Any] = {"min_len": lo, "max_len": hi}
    refused: ValueError | None = None
    cycle = None
    undone: list[float] | None = None
    # The cuts the search chooses among, read before one is cut as the seam gate and the seam pop gate read the loop.
    cells = CutOnCells(frames, pop_max=seam_pop_max)
    try:
        moved = moved_alpha(frames, analysis)
        cycle = local_cycle.detect(D, trajectory, min_len=lo, max_len=hi, signals=leg_signals(frames),
                                   wrap_pop=wrap_pop_on(moved), held_edge=HeldEdge(moved), length_cap=window.max_len,
                                   pop_gate=cells.gate, turn=turn, **detect)
    except ValueError as first:
        # A front or back gait that walked toward the camera, or a slow one: one more
        # search, recorded (`gait_fallback`), and only after the first found nothing.
        drift = gait_fallback.cycle_drift(frames, min_lag=lo, max_lag=hi)
        fallback = {"reason": str(first), "scale_drift": {k: drift[k] for k in ("height_first_px", "height_last_px", "drift", "method")},
                    "scale_drift_min": gait_fallback.SCALE_DRIFT_MIN, "scale_undone": False}
        if abs(drift["drift"]) >= gait_fallback.SCALE_DRIFT_MIN:
            fallback["padding_ltrb"] = list(gait_fallback.undo_padding(frames, drift))
            frames = gait_fallback.undo_scale(frames, drift, pad=tuple(fallback["padding_ltrb"]))
            undone = [gait_fallback.frame_hold(k, drift)["scale"] for k in range(len(frames))]
            files = gait_fallback.write_frames(frames, [f.name for f in files], scratch.new())
            D, trajectory, analysis = auto_motion.analyse(frames, fps=fps)
            said["automatic_motion_analysis"] = analysis
            fallback["scale_undone"] = True
            cells = CutOnCells(frames, pop_max=seam_pop_max)
        # An explicit --max-len is the caller's ceiling and stays one.
        hi_long = window.past(gait_fallback.long_window(lo, len(files), fps))
        fallback["window"] = [lo, hi_long]
        said["gait_fallback"] = fallback
        searched = {"min_len": lo, "max_len": hi_long, "max_fraction": gait_fallback.LONG_CYCLE_FRACTION}
        try:
            moved = moved_alpha(frames, analysis)
            cycle = local_cycle.detect(D, trajectory, min_len=lo, max_len=hi_long,
                                       max_fraction=gait_fallback.LONG_CYCLE_FRACTION,
                                       signals=leg_signals(frames), wrap_pop=wrap_pop_on(moved),
                                       held_edge=HeldEdge(moved), length_cap=window.max_len, pop_gate=cells.gate, turn=turn,
                                       **detect)
        except ValueError as second:
            failed = ValueError(f"{second} (also with the gait fallback: window [{lo}, {hi_long}], "
                                f"scale drift {drift['drift']:+.1%}{', undone' if fallback['scale_undone'] else ''})")
            # The windows the wider search measured and refused go into the report's `cycle`.
            if hasattr(second, "diagnostics"):
                failed.diagnostics = second.diagnostics  # type: ignore[attr-defined]
            failed.__cause__ = first
            refused, cycle = failed, None
    if steps is not None:
        # A count given is the word on the search's cut; the crossings are not asked.
        if refused is not None:
            raise refused
        cycle = _counted(cycle, steps, window=window, D=D, trajectory=trajectory, frames=frames, moved=moved,
                         detect=detect, cells=cells, turn=turn)
    else:
        cycle = _by_steps(cycle, refused, frames=frames, analysis=analysis, D=D, trajectory=trajectory,
                          window=window, searched=searched, detect=detect, seam_max=seam_max, cells=cells, view=view,
                          facing=facing, turn=turn)
        # A cut one step long that nothing counted: the clip itself is asked for two (`_two_lengths`).
        cycle = _two_lengths(cycle, prof=prof, fps=fps, state=state, window=window, D=D, trajectory=trajectory, frames=frames,
                             files=files, moved=moved, detect=detect, cells=cells, seam_max=seam_max, turn=turn)
        # Whichever way it was chosen, the cut that will play is held to one word.
        cycle = _witnessed(cycle, frames=frames, view=view)
    return cycle, files, analysis, undone


def _from_lead_in(record: Any, lead: int) -> Any:
    """A search's record (a cut, or the windows a refusal measured) in the clip's own frame numbers: the
    search read the clip after its first `lead` frames, so every `start` and `context_pair_range` it wrote
    moves on by `lead`. A caller cuts again from these (`--cycle fixed --start`) on the whole clip."""
    if isinstance(record, dict):
        return {key: (value + lead if key == "start" and isinstance(value, int)
                      else [value[0] + lead, value[1] + lead] if key == "context_pair_range"
                      else _from_lead_in(value, lead)) for key, value in record.items()}
    if isinstance(record, list):
        return [_from_lead_in(value, lead) for value in record]
    return record


def _one_shot(D: np.ndarray, window: CutWindow, hi: int, masses: np.ndarray) -> dict[str, Any]:
    """`detect_one_shot` in its window: ONE_SHOT_MIN_LEN to `hi`, inside the caller's bounds. Where the
    caller set one and no one-shot fits, the refusal says the window it searched."""
    shortest, longest = window.within(ONE_SHOT_MIN_LEN, hi)
    try:
        return detect_one_shot(D, min_len=shortest, max_len=longest, frame_mass=masses)
    except CycleSelectionError as exc:
        if not window.said():
            raise
        raise CycleSelectionError(f"{exc} — in {shortest}-{longest} frames, inside {window.said()}", exc.diagnostics) from exc


def coverage_verdict(cycle: dict[str, Any]) -> list[str]:
    """A cut two steps long whose clip holds under COVERAGE_MIN of a cycle past it, in words; empty
    otherwise (and for a cut whose steps are not known)."""
    if "steps" not in cycle or cycle["coverage"] >= COVERAGE_MIN:
        return []
    return [f"the cut is two steps long ({cycle['length']} frames, one cycle) and the clip holds {cycle['coverage']:.2f} of a cycle "
            f"past it (under {COVERAGE_MIN:g}): its repeat is barely seen — the seam says whether it closes; a longer take shows it repeat"]


# A walk or run loops on an even number of steps — two, one cycle: the left foot forward once and the right once. A loop
# of one step plays the same leg leading every time round (a half step). The gait floor (`LoopProfile.min_seconds`) is
# the duration under which a period is one step; the half-period guard reads it in whole frames, rounded, to choose among
# the search's candidates (`detect_cycle`, `local_cycle.detect`), so a cut up to half a frame under it is taken. Such a
# cut whose steps nothing counted (no `cycle.steps`: neither the clip's crossings, --steps nor the held side's two
# lengths) is one step. Under `--anchor motion-auto` the clip is asked for two (`_two_lengths`); a cut it does not
# give two to is named — `cycle.half_step` and a warning line — and kept.
HALF_STEP_RULE = "a walk or run loops on an even number of steps — two, one cycle"
# The clip's word on a cut one step long (`_two_lengths`). One step on, the legs are back in place with near and far
# swapped, and where the two are drawn alike (one colour of trousers) the silhouette repeats; the body's turn, the
# shading across the torso and the folds of the clothes come back only two steps on. So the cut two lengths long —
# `--steps 1`'s own (`_counted`), at each length it reads — is read about its wrap, TWO_LENGTHS_AROUND frames either
# side of its first (two, so a clip drawn on twos shows two drawings there), on the frames as filmed
# (`distance_matrix`): the clip two lengths on over one length on, the same frames (`two_lengths_on`). The nearest at
# most period.STEP_RATIO_MAX — the step screen's bound for the same ratio, which it reads over the whole clip, where a
# run that changes its pace does not repeat — is taken where the seam gate and the seam pop gate pass it as they will
# the loop (`CutOnCells`). Not on the search's distances: the analysis canvas moves every frame by the drift it reads
# (`auto_motion.analyse`), and a drift read where the body runs in place (one slope over the clip, tilted by frames
# that open higher) moves frames two steps apart further than frames one step apart, so two lengths on reads no nearer
# there. A drift the clip does walk only moves two lengths on further: it keeps a cut one step, never doubles one.
TWO_LENGTHS_AROUND = 2


def half_step(cycle: dict[str, Any], prof: LoopProfile, fps: float, *, state: str, clip_frames: int) -> dict[str, Any] | None:
    """The record of a periodic walk or run cut shorter than its state's gait floor whose steps were not counted
    (HALF_STEP_RULE); None for any other cut. `clip_frames` is what the clip holds after its lead-in. Where the clip
    was asked for two steps and did not give them (`cycle.two_lengths`, `_two_lengths`), `two_lengths` says why."""
    if not prof.gait or prof.min_seconds <= 0 or cycle.get("kind") != "periodic" or "steps" in cycle:
        return None
    seconds = cycle["length"] / fps
    if seconds >= prof.min_seconds:
        return None
    counted = cycle.get("step_count")
    if counted is None:
        why = "the clip's crossings were not read"
    elif not counted.get("readable"):
        why = counted.get("why") or "the clip does not show its steps"
    else:
        why = (counted.get("cut") or {}).get("why") or "no count of two"
    two = 2 * cycle["length"]
    said = {"rule": HALF_STEP_RULE, "state": state, "seconds": round(seconds, 4),
            "gait_floor": {"seconds": prof.min_seconds, "frames": round(prof.min_seconds * fps)},
            "steps": "not counted", "why": why, "two_steps": {"frames": two, "in_clip": two <= clip_frames}}
    if "two_lengths" in cycle:
        said["two_lengths"] = cycle["two_lengths"]["why"]
    return said


def half_step_verdict(cycle: dict[str, Any]) -> list[str]:
    """A cut recorded as one step (`cycle.half_step`), in words; empty otherwise."""
    said = cycle.get("half_step")
    if not said:
        return []
    asked = f", and the clip gives it no two ({said['two_lengths']})" if "two_lengths" in said else ""
    return [f"the cut {cycle['start']}/{cycle['length']} plays {said['seconds']:.2f} s, under the {said['state']}'s gait floor of "
            f"{said['gait_floor']['seconds']:g} s, and its steps were not counted ({said['why']}): {HALF_STEP_RULE}, and a cut "
            f"this short is one, the same leg leading every time round (a half step){asked} — count the steps in the loop "
            f"(video-loop --steps 1 cuts it two lengths long, {said['two_steps']['frames']} frames)"]


def two_lengths_on(D: np.ndarray, start: int, length: int, step: int) -> dict[str, Any] | None:
    """About a cut's first frame (TWO_LENGTHS_AROUND frames either side of its wrap, from `start`): the mean distance to
    the frame `length` on (`near`) and to the frame `step` on (`one`), over the same frames, and `ratio` near over one —
    None where one `step` on is the same picture, which two lengths on can come back no nearer than. None where no such
    frame has the one `length` on in the clip."""
    js = np.arange(max(0, start - TWO_LENGTHS_AROUND), start + TWO_LENGTHS_AROUND)
    js = js[js + length < len(D)]
    if js.size == 0:
        return None
    near, one = float(D[js, js + length].mean()), float(D[js, js + step].mean())
    return {"context_pair_range": [int(js[0]), int(js[-1]) + 1], "near": round(near, 6), "one": round(one, 6),
            "ratio": round(near / one, 4) if one > 0 else None}


def _two_lengths(cycle: dict[str, Any], *, prof: LoopProfile, fps: float, state: str, window: CutWindow, D: np.ndarray,
                 trajectory: np.ndarray, frames: list[Image.Image], files: list[Path], moved: list[np.ndarray],
                 detect: dict[str, Any], cells: CutOnCells, seam_max: float,
                 turn: body_turn_mod.Turn | None = None) -> dict[str, Any]:
    """A motion-auto cut one step long (`half_step`: under the gait floor, its steps not counted), cut two lengths long
    where the clip says it holds two (TWO_LENGTHS_AROUND): that cut, `steps` by "two-lengths" and `two_lengths` (taken).
    Else the cut as it was, `two_lengths` (not taken, `why`), which `half_step` names. Any other cut, as it was.

    Each length of the window two lengths long ([2L-1, 2L+1] inside the caller's bounds, as --steps 1 reads it) is cut
    by --steps 1's own search at that length (`_counted`) and read (`two_lengths_on`, `tried`); nearest first, the
    first within period.STEP_RATIO_MAX that the seam gate and the seam pop gate pass is taken."""
    if half_step(cycle, prof, fps, state=state, clip_frames=len(frames)) is None:
        return cycle
    first = {"start": cycle["start"], "length": cycle["length"]}
    record: dict[str, Any] = {"read": "the frames as filmed, about the wrap", "first_cut": first,
                              "ratio_max": period_mod.STEP_RATIO_MAX, "seam_max": seam_max, "tried": []}

    def kept(why: str) -> dict[str, Any]:
        cycle["two_lengths"] = {**record, "taken": False, "why": why}
        return cycle

    lo, hi = window.within(2 * first["length"] - 1, 2 * first["length"] + 1)
    if lo > hi:
        return kept(f"{window.said()} leaves no length of {2 * first['length'] - 1}-{2 * first['length'] + 1} frames to cut")
    filmed = distance_matrix(files)
    cuts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for length in range(lo, hi + 1):
        try:
            found = _counted(cycle, 1, window=window, D=D, trajectory=trajectory, frames=frames, moved=moved, detect=detect,
                             cells=cells, turn=turn, by="two-lengths", length=length)
        except ValueError as exc:
            record["tried"].append({"length": length, "unread": str(exc)})
            continue
        row = {"start": found["start"], "length": found["length"]}
        read = two_lengths_on(filmed, found["start"], found["length"], first["length"])
        row.update(read if read is not None else {"unread": "the clip holds no frame two lengths on about its wrap"})
        if read is not None and read["ratio"] is None:
            row["unread"] = "one length on is the same picture, which two lengths on comes back no nearer than"
        record["tried"].append(row)
        if "unread" not in row:
            cuts.append((row, found))
    if not cuts:
        return kept(f"no cut {lo}-{hi} frames long is read two lengths on ({record['tried'][-1]['unread']})")
    cuts.sort(key=lambda pair: (pair[0]["ratio"], pair[0]["start"], pair[0]["length"]))
    for row, found in cuts:
        if row["ratio"] > period_mod.STEP_RATIO_MAX:
            break
        s, L = row["start"], row["length"]
        row["seam"] = round(cells.seam(s, L), 3)
        if row["seam"] > seam_max:
            row["gate"] = (f"its wrap on the cells is {row['seam']:g} of its step, over {seam_max:g}: the seam gate would "
                           "not pass it")
            continue
        if cells.gate is not None:
            row["seam_pop"] = pop = cells.gate(s, L)
            if pop.get("over"):
                row["gate"] = (f"its top band jumps {pop['pop']:g} into its first frame, over {pop['max']:g}: the seam pop "
                               "gate would not pass it")
                continue
        found["two_lengths"] = {**record, "cut": {"start": s, "length": L}, "taken": True}
        if "step_count" in cycle:
            found["step_count"] = cycle["step_count"]
        return found
    row = cuts[0][0]
    if "gate" in row:
        return kept(f"the cut {row['start']}/{row['length']} comes back two lengths on ({row['ratio']:.2f}x as far as one "
                    f"length on), but {row['gate']}")
    return kept(f"about the wrap of the nearest, {row['start']}/{row['length']}, the clip two lengths on is {row['ratio']:.2f}x "
                f"as far as one length on, over {period_mod.STEP_RATIO_MAX:g}: the step comes back no nearer two steps on")


def _blend_wrap(frames: list[Image.Image], pin_file: Path, *, mode: str, anchor: str, cycle: dict[str, Any], fps: float,
                seam_max: float) -> tuple[list[Image.Image], dict[str, Any]]:
    """`wrap_blend.apply` on a pinned cut, the pinned last frame cleaned as the cut's frames are (`_scrub`,
    `drop_specks`). Not tried, and said why: `--wrap-blend off`; an anchor that moves the cells off the place the pin
    holds; a pin the gate refuses, which fails the clip whatever its wrap shows."""
    if mode == "off":
        return frames, {"applied": False, "why": "--wrap-blend off"}
    if anchor != "none":
        return frames, {"applied": False, "why": f"--anchor {anchor} moves the cells off the place the pinned frame holds"}
    if cycle["pin_error"] > cycle["pin_tolerance"]:
        return frames, {"applied": False, "why": "the pin does not close: the clip is refused on it"}
    pin = Image.open(pin_file).convert("RGBA")
    _scrub(pin)
    pin, _ = drop_specks(pin, alpha_over=16, diagonal=False, apart=0)
    return wrap_blend_mod.apply(frames, pin, fps=fps, seam_max=seam_max)


def _size_hold(frames: list[Image.Image], *, lo: int, hi: int, cut: tuple[int, int] | None = None
               ) -> tuple[list[Image.Image] | None, dict[str, Any], Any, list[float] | None]:
    """A walk or run held at its first frame's size, and its feet on the first frame's row, before its cut is chosen
    (`--size-hold auto`): the frames held (None: the clip changes size by less than SIZE_HOLD_MIN, read one cycle on,
    and is cut as filmed), the report's `size_hold`, where a box of a frame as filmed lands in its held frame
    (`gait_fallback.held_box`), and the factor each frame was scaled by (`gait_fallback.frame_hold`); the last two None
    when not held.

    A clip filmed from its first frame only grows or shrinks as it plays, so the frame one cycle on is not the size of
    the first and the loop pops at the wrap; a walk away from the camera climbs the frame as well, and held at its size
    with its feet where they were filmed it pops up instead. The change is read one cycle on, so a first pose that
    settles into the walk is not taken for a body that shrinks, nor for feet that climb (gait_fallback.cycle_drift,
    foot_drift). A cut the caller names (`cut`: start, length) is held only where it shows the change itself, a cycle
    of its length apart (`gait_fallback.at_cut`), and its feet are moved only where it shows them move: a clip whose
    frames that are not the walk read a change the cut does not make is cut as filmed (`size_hold.at_cut`, `why`).
    Held, its record carries the move each frame of the cut was held by (`size_hold.cut_frames`)."""
    measured = gait_fallback.cycle_drift(frames, min_lag=lo, max_lag=hi)
    hold: dict[str, Any] = {"applied": False, "min": gait_fallback.SIZE_HOLD_MIN,
                            **{k: measured[k] for k in ("height_first_px", "height_last_px", "drift", "method",
                                                        "lag", "pairs", "per_lag", "pose_match", "drift_fitted_line")}}
    if abs(measured["drift"]) < gait_fallback.SIZE_HOLD_MIN:
        return None, hold, None, None
    feet = gait_fallback.foot_drift(frames, measured)
    if cut is not None:
        seen = hold["at_cut"] = gait_fallback.at_cut(frames, measured, feet, start=cut[0], length=cut[1])
        if not seen["size"]:
            hold["why"] = ("the cut does not change size a cycle of its length apart as the clip reads it (frames that are "
                           "not the walk, or a clip too short to show its cycle twice): cut as filmed")
            return None, hold, None, None
        if feet["held"] and not seen["feet"]:
            feet = {**feet, "held": False, "lift": None,
                    "why": "the cut's feet do not move a cycle of its length apart as the clip reads them"}
    lift = feet.pop("lift")
    pad = gait_fallback.undo_padding(frames, measured, lift)
    held = gait_fallback.undo_scale(frames, measured, pad=pad, lift=lift)
    hold.update(applied=True, padding_ltrb=list(pad), feet=feet)
    if cut is not None:
        # The move each frame of the cut was held by, exact (`gait_fallback.frame_hold`): `video-loop-repair` holds the
        # cut's source frames by it again before it reads them (sprite_gen/video/source.py).
        hold["cut_frames"] = [gait_fallback.frame_hold(k, measured, lift) for k in range(cut[0], cut[0] + cut[1])]
    return (held, hold, partial(gait_fallback.held_box, measured=measured, pad=pad, lift=lift),
            [gait_fallback.frame_hold(k, measured, lift)["scale"] for k in range(len(frames))])


class _Scratch:
    """The folders a run writes the frames a size hold or the gait fallback scaled back into: each a new one of the
    run's own in the out dir (`runio.new_stage`: `.gait-fallback-frames.<pid>-<random>`), so a folder already there —
    an input's, or another run's — is never written into or removed; `run_loop` removes them however the run ends."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.made: list[Path] = []

    def new(self) -> Path:
        out = self.out_dir.expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        self.made.append(new_stage(out / FALLBACK_FRAMES, ""))
        return self.made[-1]

    def remove(self) -> None:
        for folder in self.made:
            shutil.rmtree(folder, ignore_errors=True)


def _refuse_overlap(frames_dir: Path, files: list[Path], out_dir: Path, name: str, target: Path, report_given: bool) -> None:
    """A cut never writes over, moves or deletes the frames it reads: `--frames-dir`, every frame found in it and every
    link or folder their paths look up, against what the cut replaces in `--out-dir` (`handed_over`, `cycle.refused/`)
    and its report (`runio.refuse_overlap`). Refused before a frame is read or anything is made. `--out-dir` itself is
    not compared: a cut adds to it, and the clip's frames are often inside it (`video-frames`' `keyed/`)."""
    out = out_dir.expanduser().resolve()
    outputs = {f"{path.name} in --out-dir": path for path in (*handed_over(out, name), out / CYCLE_REFUSED_DIR)}
    outputs["--report" if report_given else f"{target.name} in --out-dir"] = target
    refuse_overlap({"--frames-dir": frames_dir, **{f"{f.name} in --frames-dir": f for f in files}}, outputs, verb="video-loop")


def _cell_record(strip_bytes: bytes, cells: list[Image.Image], *, start: int, length: int, samples: list[int],
                 anchored: list[str], wrap_dx: int, motion: dict[str, Any] | None,
                 scales: dict[str, list[float]], upsampled: dict[str, Any] | None = None) -> dict[str, Any]:
    """The strip's record of its cells (`CELL_RECORD`), bound to the strip by its PNG's sha256: for each cell, the frame of
    the cut it shows at the end of the cut (`cells`, frame `samples[c]` of a cut `length` frames long from clip frame
    `start`; at --out-fps or with its air retimed, `upsampled`, the frame cell `samples[c]` is, and one made between two
    frames reads as the frame at or before its time, as the seam reads it), the source frame it is, where the anchor
    moved that frame before the crop (the body anchor's ramp, `ramp_offset` of `wrap_dx`; the motion correction's place,
    `motion_anchor.laid_at`; nothing at --anchor none or feet),
    the factor each step that held the clip's frames to one size scaled it by (`scales`, per clip frame; 1 where none did),
    and whether it is that frame as filmed but for the anchor's move: its pixels are its pixels anchored, before any
    repair (`anchored`, the cut's receipt), and no step held the clip's frames. What made a cell otherwise — a repair, a
    blend at the wrap, a hold, a frame between at --out-fps or in a retimed air, the air retime's move — is not listed:
    the cell's pixels say it."""
    def moved(k: int) -> list[int]:
        if motion is not None:
            return list(motion_anchor.laid_at(motion, k))
        return [ramp_offset(wrap_dx, k, length), 0]

    def frame(c: int) -> int:
        if upsampled is None:
            return c
        shown = upsampled["source_frames"][c]
        return shown if shown is not None else min(length - 1, math.floor(upsampled["times"][c]))

    return {"strip_sha256": evidence_mod.digest(strip_bytes), "cells": [
        {"source_frame": start + k, "anchor_move_px": moved(k),
         "scale": {step: scales[step][start + k] if step in scales else 1.0 for step in CELL_SCALE_STEPS},
         "as_filmed": not scales and evidence_mod.digest(cell.tobytes()) == anchored[k]}
        for k, cell in ((frame(c), cell) for c, cell in zip(samples, cells))]}


def run_loop(frames_dir: Path, out_dir: Path, **options: Any) -> dict[str, Any]:
    """Cut the loop (`_cut_loop`, which takes the options). The frames a size hold or the gait fallback scales back
    are written in folders of the run's own (`_Scratch`), removed however the run ends."""
    scratch = _Scratch(out_dir)
    try:
        return _cut_loop(frames_dir, out_dir, scratch=scratch, **options)
    finally:
        scratch.remove()


def _cut_loop(
    frames_dir: Path,
    out_dir: Path,
    *,
    scratch: _Scratch,
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
    repair: str = "auto",
    facing: str = "right",
    interpolate: rife_mod.Interpolate | None = None,
    jolt_max: float | None = None,
    head_step_max: float | None = None,
    size_hold: str = "auto",
    steps: int | None = None,
    overlap_wrap: str = "on",
    wrap_blend: str = "auto",
    unfixed: str = "warn",
    seam_pop_max: float | None = repair_mod.SEAM_POP_REFERENCE,
    view: str | None = None,
    body_turn: str = "warn",
    fill_held: str = "auto",
    out_fps: float | None = None,
    air_retime: str | None = None,
    meters_per_px: float | None = None,
    contacts: list[str] | None = None,
    judge_scale: float = held_fill_mod.JUDGE_SCALE,
) -> dict[str, Any]:
    state = state.strip().lower() if state else state
    if state == effect.STATE:
        if cycle_mode == "auto":
            cycle_mode = "lifetime"
        elif cycle_mode != "lifetime":
            raise SystemExit("video-loop: an effect requires --cycle lifetime")
    if state in nonloop.STATES:
        if cycle_mode == "auto":
            cycle_mode = "once"
        elif cycle_mode != "once":
            raise SystemExit(f"video-loop: non-loop state {state} requires --cycle once")
    if cycle_mode in nonloop.KINDS:
        if state in nonloop.CYCLIC_STATES:
            raise SystemExit(f"video-loop: cyclic state {state} cannot use --cycle {cycle_mode}")
        if anchor not in (None, "none"):
            raise SystemExit("video-loop: a single-play cut requires --anchor none")
        if (start is not None or length is not None) and not (cycle_mode == "once" and None not in (start, length)):
            raise SystemExit("video-loop: a single-play cut selects its terminal bounds; drop --start/--length (--cycle once "
                             "takes both: the frames the caller plays once)")
        if not math.isfinite(fps) or fps <= 0 or not math.isfinite(gif_fps) or gif_fps <= 0 or (n_out is not None and n_out < 2):
            raise SystemExit("video-loop: positive fps and at least two output frames are required")
    if steps is not None and steps not in STEPS:
        raise SystemExit(f"video-loop: --steps {steps}: the cut holds {' or '.join(map(str, STEPS))} steps here")
    if size_hold not in SIZE_HOLD_MODES:
        raise SystemExit(f"video-loop: unknown --size-hold {size_hold!r}; expected one of {', '.join(SIZE_HOLD_MODES)}")
    if repair not in REPAIR_MODES:
        raise SystemExit(f"video-loop: unknown --repair {repair!r}; expected one of {', '.join(REPAIR_MODES)}")
    if overlap_wrap not in OVERLAP_WRAP_MODES:
        raise SystemExit(f"video-loop: unknown --overlap-wrap {overlap_wrap!r}; expected one of {', '.join(OVERLAP_WRAP_MODES)}")
    if wrap_blend not in wrap_blend_mod.MODES:
        raise SystemExit(f"video-loop: unknown --wrap-blend {wrap_blend!r}; expected one of {', '.join(wrap_blend_mod.MODES)}")
    if unfixed not in UNFIXED_MODES:
        raise SystemExit(f"video-loop: unknown --unfixed {unfixed!r}; expected one of {', '.join(UNFIXED_MODES)}")
    from sprite_gen.gen.facing import FACINGS
    if facing not in FACINGS:
        raise SystemExit(f"video-loop: unknown --facing {facing!r}; expected one of {', '.join(FACINGS)}")
    if view is not None and view not in handed_mod.VIEWS:
        raise SystemExit(f"video-loop: unknown --view {view!r}; expected one of {', '.join(handed_mod.VIEWS)}")
    if seam_pop_max is not None and not (math.isfinite(seam_pop_max) and seam_pop_max > 0):
        raise SystemExit(f"video-loop: --seam-pop-max {seam_pop_max}: a bound over 0, or off")
    if body_turn not in body_turn_mod.MODES:
        raise SystemExit(f"video-loop: unknown --body-turn {body_turn!r}; expected one of {', '.join(body_turn_mod.MODES)}")
    if fill_held not in held_fill_mod.MODES:
        raise SystemExit(f"video-loop: unknown --fill-held {fill_held!r}; expected one of {', '.join(held_fill_mod.MODES)}")
    try:
        judge_scale = quality_judge_scale(str(judge_scale))
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(f"video-loop: {exc}") from exc
    if out_fps is not None:
        if not (math.isfinite(out_fps) and out_fps > fps):
            raise SystemExit(f"video-loop: --out-fps {out_fps:g} is not above the clip's {fps:g} fps: a cut is given at a higher "
                             "rate than it was filmed (its frames between made), a lower one is its preview's (--gif-fps)")
        if cycle_mode == "lifetime":
            raise SystemExit("video-loop: --out-fps: an effect's lifetime plays as filmed")
    asked_air = None
    if air_retime is not None:
        try:
            asked_air = root_motion_mod.parse_retime(air_retime)
        except ValueError as exc:
            raise SystemExit(f"video-loop: {exc}") from None
        if meters_per_px is None and {"gravity", "speed"} & set(asked_air):
            raise SystemExit("video-loop: --air-retime gravity= and speed= are metres: give --meters-per-px, the metres a "
                             "pixel of the clip as filmed holds (the standing height in metres over the strip's body_src_h)")
    if meters_per_px is not None and not (math.isfinite(meters_per_px) and meters_per_px > 0):
        raise SystemExit(f"video-loop: --meters-per-px {meters_per_px:g}: a number over 0")
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
    if steps is not None and anchor != "motion-auto":
        raise SystemExit("video-loop: --steps counts the steps in the cut --anchor motion-auto takes; pass --anchor motion-auto")
    if body_turn == "refuse" and anchor != "motion-auto":
        raise SystemExit("video-loop: --body-turn refuse reads the body's turn in the walk --anchor motion-auto searches; "
                         "pass --anchor motion-auto")
    if cycle_mode not in CYCLE_MODES:
        raise SystemExit(f"video-loop: unknown --cycle {cycle_mode!r}; expected one of {', '.join(CYCLE_MODES)}")
    given = frames_dir
    frames_dir = frames_dir.expanduser().resolve()
    files = sorted(frames_dir.glob("*.png"))
    if len(files) < 6:
        raise SystemExit(f"video-loop: need at least 6 keyed frames in {frames_dir}, found {len(files)}")
    target = (report_path or (out_dir / f"{name}.loop.report.json")).expanduser().resolve()
    _refuse_overlap(given, files, out_dir, name, target, report_path is not None)
    prof = profile_for(state)
    n = len(files)
    try:
        declared = root_motion_mod.parse_contacts(contacts or [], n)
    except ValueError as exc:
        raise SystemExit(f"video-loop: {exc}") from None
    # A walk or run may open on a lead-in (gait_fallback.lead_in): the video model reframing a small
    # subject before it walks at the clip's size. An automatic search reads the clip after it as a clip
    # of its own, and what it finds is said in the clip's own frame numbers (`_from_lead_in`); a cut the
    # caller names (fixed, pinned) is the caller's frames, lead-in or not.
    gait_frames = [Image.open(f).convert("RGBA") for f in files] if prof.gait else None
    lead: dict[str, Any] | None = None
    if gait_frames is not None:
        whole = CutWindow.of(prof, n, fps, min_len, max_len)
        lead = gait_fallback.lead_in(gait_frames, min_lag=whole.lo, max_lag=whole.hi)
    skip = lead["frames"] if lead is not None and cycle_mode in ("auto", "periodic") else 0
    # Every search below reads its window off this one (`CutWindow`): none sets a bound of its own.
    window = CutWindow.of(prof, n - skip, fps, min_len, max_len)
    lo, hi = window.lo, window.hi
    D = distance_matrix(files)
    # The clip's steps as keyed, before `--anchor motion-auto` reads its own distance or moves a frame:
    # the hold is read on them, over the clip and over the cut (sprite_gen/video/held.py).
    clip_steps = [float(D[k, k + 1]) for k in range(n - 1)]
    masses = effect.frame_masses(files) if cycle_mode == "lifetime" else frame_masses(files)
    periodic_attempt: dict[str, Any] | None = None
    report_base = {
        "kind": REPORT_KIND, "frames_dir": str(frames_dir),
        "source": evidence_mod.source_identity(files, fps),
        "producer": evidence_mod.engine_identity(),
        "out_dir": str(out_dir), "state": state, "fps": fps, "frames_total": n,
        "window": [lo, hi], "profile": prof.why, "cycle_mode": cycle_mode,
        "seam_max": seam_max,
        "anchor": anchor,
        "seam_measurement": "rendered-cells" if anchor in ("motion", "motion-auto") else "source-frames",
        # How many drawings the clip shows a second, and whether it shows each for two or three frames
        # (sprite_gen/video/held.py) — over the whole clip as keyed, before any cut. A record for
        # `video-cycle-align`, which says when a held clip stretched to the set's length must be filmed again.
        "drawings": held_mod.measure(clip_steps, fps=fps),
    }
    if lead is not None:
        # `search_from`: the first clip frame the search read (0: the whole clip, or a cut the caller named).
        report_base["lead_in"] = {**lead, "search_from": skip}
    cycle = None
    shifted = False
    clip_files, clip_D = files, D
    files, D = files[skip:], D[skip:, skip:]
    # Per step that holds a clip's frames to one size (`CELL_SCALE_STEPS`), the factor it scaled each frame of the clip by,
    # where it held them (`cell_record`).
    clip_scales: dict[str, list[float]] = {}
    try:
        if anchor == "motion-auto":
            source_frames = gait_frames[skip:]
            if size_hold == "auto":
                # Held before the search (`_size_hold`), the cut is chosen on frames of one size whose feet stand on one
                # row. The gait fallback below then finds nothing to undo.
                held, report_base["size_hold"], _, scales = _size_hold(source_frames, lo=lo, hi=hi)
                if held is not None:
                    source_frames = held
                    clip_scales["size_hold"] = [1.0] * skip + scales
                    files = gait_fallback.write_frames(held, [f.name for f in files], scratch.new())
            else:
                report_base["size_hold"] = {"applied": False, "why": "--size-hold off"}
            try:
                D, trajectory, analysis = auto_motion.analyse(source_frames, fps=fps)
            except auto_motion.RegionsUnfound as unfound:
                # No two regions track through the clip: the frames it opens on, fewer than the shortest cut, are left
                # out as a lead-in is and the clip after them is searched (`auto_motion.analyse_after_opening`), said
                # in the report's `untracked_opening` and, once a cut is found, a warning line; where no such opening
                # leaves it tracked, the clip is refused as before, in the same words and what was tried.
                opening, (D, trajectory, analysis) = auto_motion.analyse_after_opening(
                    source_frames, fps=fps, shorter_than=lo, unfound=unfound)
                report_base["untracked_opening"] = {"frames": opening, "first": skip, "shorter_than": lo,
                                                    "refused": str(unfound)}
                skip += opening
                source_frames, files = source_frames[opening:], files[opening:]
                window = CutWindow.of(prof, n - skip, fps, min_len, max_len)
                lo, hi = window.lo, window.hi
                report_base["window"] = [lo, hi]
                report_base["lead_in"]["search_from"] = skip
            report_base["automatic_motion_analysis"] = analysis
            search = partial(_motion_auto, source_frames, files, D, trajectory, analysis, fps=fps, window=window, prof=prof,
                             state=state, steps=steps, seam_max=seam_max, seam_pop_max=seam_pop_max, scratch=scratch, view=view,
                             facing=facing)
            # The body's turn (sprite_gen/video/body_turn.py), read on the clip as keyed against its first frame, the still,
            # lead-in or not: a cut that holds a frame of one is not taken while another holds a cycle. Where none does,
            # --body-turn warn takes the cut the search takes with the turn in, and says so; refuse refuses the clip in a line
            # of its own. A clip that turns nowhere is searched once, as before; one whose walk reads square against a
            # still that shows a side is not read either, and a warning line says so.
            turning = {**body_turn_mod.read(gait_frames), "mode": body_turn}
            report_base["body_turn"] = turning
            turn = body_turn_mod.Turn(turning, skip) if turning["turns"] else None
            if turn is None:
                turning["verdict"] = "none" if turning["read"] else "not-read"
                if seen := body_turn_mod.square(turning):
                    print(f"video-loop: warning: {seen}", file=sys.stderr)
            said: dict[str, Any] = {}
            found = avoided = None
            if turn is not None:
                try:
                    found = search(said=said, turn=turn)
                except ValueError as exc:
                    avoided = exc
                turning["refused"] = turn.refused
                if found is None and body_turn == "refuse":
                    report_base.update(said)
                    turning["verdict"] = "refused"
                    failed = ValueError(body_turn_mod.refusal(turning))
                    if hasattr(avoided, "diagnostics"):
                        failed.diagnostics = avoided.diagnostics  # type: ignore[attr-defined]
                    raise failed from avoided
            if found is None:
                said = {}
                try:
                    found = search(said=said)
                except ValueError:
                    report_base.update(said)
                    if turn is not None:
                        turning.update(verdict="unfound", avoiding=str(avoided))
                        print(f"video-loop: warning: {body_turn_mod.unfound(turning)}", file=sys.stderr)
                    raise
            report_base.update(said)
            cycle, files, analysis, undone = found
            if undone is not None:
                clip_scales["gait_fallback"] = [1.0] * skip + undone
            if turn is not None:
                cut = (cycle["start"] + skip, cycle["length"])
                holds = turn.held(cycle["start"], cycle["length"])
                turning["cut"] = {"frames": [cut[0], cut[0] + cut[1] - 1], "holds": holds}
                if avoided is not None:
                    turning["avoiding"] = str(avoided)
                if not holds:
                    turning["verdict"] = "avoided" if avoided is None and turn.refused else "clear"
                elif body_turn == "refuse":
                    turning["verdict"] = "refused"
                    raise ValueError(body_turn_mod.refusal(turning, cut=cut, held=holds))
                else:
                    turning["verdict"] = "kept"
                    print(f"video-loop: warning: {body_turn_mod.kept(turning, cut=cut, held=holds)}", file=sys.stderr)
            if opened := report_base.get("untracked_opening"):
                # Not the refusal's words: a caller that reads the stderr for them reads a refusal, and this cut is found.
                left = (f"frame {opened['first']}" if opened["frames"] == 1
                        else f"frames {opened['first']}-{opened['first'] + opened['frames'] - 1}")
                print(f"video-loop: warning: the automatic motion anchor found its two regions from frame {skip} on, not from "
                      f"the clip's first frames: {left} left out of the search as a lead-in is (untracked_opening)",
                      file=sys.stderr)
        elif cycle_mode == "once" and start is not None:
            # The frames the caller plays once (a move cut out of a run, which settles on no pose): no terminal pose read.
            cycle = nonloop.select_cut(D, masses, fps=fps, ending=partial(nonloop.given_ending, start=start, length=length))
            nonloop.validate_body([Image.open(f).convert("RGBA") for f in files[start:start + length]])
        elif cycle_mode == "once":
            source_frames = [Image.open(f).convert("RGBA") for f in files]
            cycle = nonloop.select_cut(D, masses, fps=fps, ending=partial(
                nonloop.hold_ending, pose=nonloop.end_pose(state, source_frames, _small_features)))
            nonloop.validate_body(source_frames[cycle["start"]:cycle["end_frame"] + 1])
        elif cycle_mode == "lifetime":
            cycle = nonloop.select_cut(D, masses, fps=fps, kind=cycle_mode,
                                      ending=effect.lifetime_ending)
        elif cycle_mode == "fixed":
            if start is None or length is None:
                raise SystemExit("video-loop: --cycle fixed needs --start and --length")
            cycle = fixed_cycle(D, start=start, length=length)
            if gait_frames is not None and size_hold == "auto":
                # A walk or run cut the caller names is held as a search's cut is (`_size_hold`), read over the whole clip,
                # and cut on the frames held: the cut's distances, its count and the seam the gate reads are the frames
                # that play. The clip is then the clip held (a fixed cut leaves no lead-in out).
                held, report_base["size_hold"], box_of, scales = _size_hold(gait_frames, lo=lo, hi=hi, cut=(start, length))
                if held is not None:
                    gait_frames = held
                    clip_scales["size_hold"] = scales
                    files = gait_fallback.write_frames(held, [f.name for f in files], scratch.new())
                    D = distance_matrix(files)
                    clip_files, clip_D = files, D
                    cycle = fixed_cycle(D, start=start, length=length)
                    if anchor_regions is not None:
                        # `--anchor-region` names the cut's first frame as filmed: the same parts of it, held.
                        anchor_regions = [box_of(tuple(box), start) for box in anchor_regions]
                        report_base["size_hold"]["anchor_regions"] = [list(box) for box in anchor_regions]
            elif gait_frames is not None:
                report_base["size_hold"] = {"applied": False, "why": "--size-hold off"}
            if gait_frames is not None:
                # A walk or run loops on two steps: the clip's own count of the cut the caller named, as a search's cut is
                # counted. An odd count swaps the legs at the wrap, however close the frames there look.
                cycle["step_count"] = fixed_count(gait_frames, D, start, length, view=view, facing=facing)
                if odd_count(cycle):
                    counted = cycle["step_count"]
                    if seam_max <= STEP_RATIO_MAX:
                        two = round(2 * counted["step_range"][1])
                        report_base["seam_refusal"] = {"reading": "count", "cut": [start, length], "verdict": "refuted",
                                                       "k": counted["cut"]["k"], "why": counted["cut"]["why"], "two_steps": two}
                        raise SystemExit(
                            f"video-loop: loop step count refutes the fixed cut {start}/{length} — the clip's crossings count "
                            f"{counted['cut']['why']} where a walk loops on two, and an odd count swaps the legs at the wrap; "
                            f"cut it again two steps long (about {two} frames)")
                    counted["lifted"] = {"seam_max": seam_max}
                    print(f"video-loop: warning: the clip's crossings count the fixed cut {start}/{length} "
                          f"{counted['cut']['why']}: a walk loops on two steps, and an odd count swaps the legs at the wrap — "
                          f"kept, as --seam-max {seam_max:g} lifts the step gate", file=sys.stderr)
        elif cycle_mode == "one-shot":
            cycle = _one_shot(D, window, hi, masses)
        elif cycle_mode == "pinned":
            cycle = pinned_cycle(D, seam_max=seam_max)
        elif cycle_mode == "once-return":
            cycle = once_return_cycle(D, seam_max=seam_max)
        else:
            gait_floor = round(prof.min_seconds * fps) if prof.gait and prof.min_seconds > 0 else None
            cycle = detect_cycle(D, min_len=lo, max_len=hi, gait_floor=gait_floor,
                                 signals=leg_signals(gait_frames[skip:] if gait_frames is not None else [Image.open(f).convert("RGBA") for f in files]))
            cycle["kind"] = "periodic"
            floor = periodicity_floor(len(files), cycle["period_global"], partial_repeat=prof.action_seconds is not None)
            cycle["periodicity_min"] = floor
            if prof.periodic and cycle["periodicity"] < floor:
                flat = (
                    f"the period profile is flat (periodicity {cycle['periodicity']:.2f} < {floor:.3f}) in window [{lo},{hi}]"
                )
                if cycle_mode == "auto" and prof.one_shot_ok:
                    # explicit, recorded failover: the action happened once (allowed for this state),
                    # so cut rest -> excursion -> rest instead. The periodic attempt stays in the report.
                    periodic_attempt = {**cycle, "window": [lo, hi], "why_rejected": flat}
                    # A one-shot may run past the periodic window — not past the caller's ceiling.
                    cycle = _one_shot(D, window, max(hi, round(n * 0.9)), masses)
                else:
                    raise SystemExit(
                        f"video-loop: no periodic cycle found — {flat}; the motion does not repeat, widen the window, "
                        "regenerate the clip, or pass --cycle one-shot for a single performed action"
                    )
        if skip:
            cycle, shifted = _from_lead_in(cycle, skip), True
            if "step_count" in cycle:
                # Its frame numbers are the search's: the clip's after its lead-in.
                cycle["step_count"]["read_from"] = skip
        # Whatever chose the cut — a search, a count, --cycle fixed or pinned — it is inside the caller's bounds.
        window.hold(cycle)
    except (SystemExit, ValueError) as exc:
        refused = getattr(exc, "diagnostics", cycle)
        refuse(target, out_dir, name, {**report_base, "status": "failed", "error": str(exc),
                                       "cycle": _from_lead_in(refused, skip) if skip and not shifted else refused,
                                       "periodic_attempt": periodic_attempt})
        if isinstance(exc, ValueError):
            raise SystemExit(f"video-loop: {exc}") from exc
        raise
    # The cut is in the clip's own frame numbers; the frames it reads are the clip's, scaled where the search scaled them.
    files, D = clip_files[:skip] + files, clip_D
    i, L = cycle["start"], cycle["length"]
    if "steps" in cycle:
        # A cut two steps long is one cycle: how much of a second the clip holds past it (after its lead-in).
        cycle["coverage"] = round((n - skip - L) / L, 4)
    if cycle_mode in ("auto", "periodic") and (said := half_step(cycle, prof, fps, state=state, clip_frames=n - skip)):
        cycle["half_step"] = said
    # The same, over the cut alone: a clip held for part of its length is held where the cut is.
    report_base["cycle_drawings"] = held_mod.measure_cycle(clip_steps, start=i, length=L, fps=fps)
    if gait_frames is not None:
        # Every cut of the cut's length the clip holds, read for the same foot stepping twice in a row (sprite_gen/video/
        # cadence.py): `video-cycle-align` says from it whether cutting a loop that does so again could help.
        report_base["clip_feet"] = cadence_mod.clip_feet(gait_frames[skip:], L, first=skip)
    # playback density, not a fixed count: a long cycle gets more frames so every state plays at
    # ~gif_fps (a fixed 12 made a 2.5 s jump hold each frame 210 ms while a 1.1 s walk held 90 ms)
    if n_out is None:
        n_out = max(GIF_FRAMES_MIN, round(L / fps * gif_fps))
    n_asked = n_out
    n_out = min(n_out, L)  # never ask for more distinct frames than the cycle holds

    out_dir = out_dir.expanduser().resolve()
    cycle_dir = out_dir / "cycle"
    frames: list[Image.Image] = []
    scrubbed = specks = 0
    # A walk or run cut for a `--body-height` has its frames' sizes read as filmed, for the standing height below.
    sized = body_height is not None and prof.gait
    cut_sizes = []
    for k, f in enumerate(files[i : i + L]):
        im = Image.open(f).convert("RGBA")
        if sized:
            cut_sizes.append(gait_fallback.body_size(im))
        # Motion review is pixel preserving: cleanup belongs to the keyed input (`video-frames` erases
        # the specks far from the body). The other cuts erase every loose piece of solid pixels under
        # the speck size, wherever it is: the outline's islands and a drawn shadow as well.
        if anchor not in ("motion", "motion-auto") and cycle["kind"] != "lifetime":
            scrubbed += _scrub(im)
            im, d = drop_specks(im, alpha_over=16, diagonal=False, apart=0)
            specks += d
        frames.append(im)
    # Each step of the cut as filmed, read for a picture shown again (held_fill.same_steps): before an anchor moves each
    # frame of a pair apart.
    same = held_fill_mod.same_steps(frames)

    if prof.gait:
        report_base["gait"] = evidence_mod.gait_observation(frames, cycle)
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
            # The search found its regions on the clip; the cut's own frames, read again for the correction, may hold none.
            on_cut = f" — on the cut the search took (frames {i}-{i + L - 1})" if isinstance(exc, auto_motion.RegionsUnfound) else ""
            error = f"video-loop: {exc}{on_cut}"
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error) from exc
        report_base["motion_anchor"] = motion
    # Each frame of the cut anchored, before any repair, pixel for pixel: its receipt, and what each cell's record reads
    # whether it is that frame as filmed (`cell_record`).
    anchored = [evidence_mod.digest(f.tobytes()) for f in frames]
    if anchor != "feet" and cycle["kind"] != "lifetime":
        from sprite_gen.video.source import RECIPE
        report_base["source_projection"] = {
            "recipe": MOTION_RECIPE if motion is not None else RECIPE, "wrap_dx_px": wrap_dx,
            "before_repair_pixels_sha256": anchored,
        }
    # A loop closes on its first cell again; a cut played once runs from its first to its last.
    ring = cycle["kind"] not in ("one-shot", "lifetime", *nonloop.KINDS)
    if cycle["kind"] == "lifetime":
        report_base["held_fill"] = {"mode": fill_held, "why": "an effect's lifetime is played as filmed", "made": [],
                                    "before": held_fill_mod.reading(same), **held_fill_mod.reading(same)}
    else:
        # The cells that show the picture before them again, made between the poses beside them (docs/loop-repair.md
        # section 7), on the cut as anchored, before anything reads its flow or its wrap. A cut drawn on twos or threes
        # keeps them: its hold as video-cycle-align reads it, over the cut, else over the clip.
        cut_drawings = report_base["cycle_drawings"]
        hold = cut_drawings["hold"] if cut_drawings["hold"] is not None else report_base["drawings"]["hold"]
        try:
            frames, report_base["held_fill"] = _fill_held(frames, interpolate, ring=ring, same=same, mode=fill_held, hold=hold,
                                                          judge_scale=judge_scale)
        except (ValueError, rife_mod.RifeUnavailable) as exc:
            error = (f"video-loop: {exc}; the cut shows a picture again and the frames between are to be made — fix RIFE, "
                     "or pass --fill-held off to cut it as filmed (docs/loop-repair.md section 7)")
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error) from exc
        if report_base["held_fill"]["made"]:
            report_base["seam_measurement"] = "rendered-cells"
        for line in _fill_lines(report_base["held_fill"]):
            print(f"video-loop: warning: {name}: {line}", file=sys.stderr)
    if prof.gait:
        # The ghost screen of the cut as filmed (docs/loop-repair.md section 4), read before the jump repair: each
        # filmed ghost it names is a target of that repair, which gives it way to a clean frame where there is one.
        # A loop delivered as cut, never aligned, would show it otherwise.
        report_base["ghost_screen"] = ghost_screen(frames)
    if cycle["kind"] == "pinned":
        # The pinned last frame is the model's drawing of the first: where it redraws it, the cut's last half second takes
        # the first frame's drawing back (docs/loop-repair.md section 6). After the anchor, on the frames as filmed, before
        # any repair reads the wrap; `source_projection` above is the cut before it.
        frames, report_base["wrap_blend"] = _blend_wrap(frames, files[i + L], mode=wrap_blend, anchor=anchor, cycle=cycle,
                                                        fps=fps, seam_max=seam_max)
        for line in wrap_blend_mod.warnings(report_base["wrap_blend"], seam_max=seam_max):
            print(f"video-loop: warning: {line}", file=sys.stderr)
    # Each cut frame given way to the frame beside it, as filmed: the seam is read on it there (`seam_as_filmed`).
    as_filmed: dict[int, Image.Image] = {}
    if prof.gait:
        # The part reading (docs/loop-repair.md section 2, "The part pop") of every walk and run, on the cut as filmed —
        # anchored, before any repair — whatever --repair asks: a hand or a foot that pops at the wrap.
        report_base["part_pop"] = part_pop_mod.public(part_pop_mod.read(frames))
        cut_frames = frames
    if repair != "off" and prof.gait:
        report_base["jump_repair"] = None
        filmed = frames
        wrap = None
        if anchor == "motion-auto" and (cycle.get("steps") or {}).get("by") == "crossings" and wrap_on_cells(frames) > seam_max:
            # Two steps by the clip's own crossings, and a wrap the gate refuses: a part on a beat of its own closes on no
            # cut of two steps. The clip's frames a cycle from the cut's beside the wrap make it again (the overlapped wrap),
            # unless it is turned off.
            wrap = ({"others": cycle_apart(files, start=i, length=L, skip=skip, analysis=analysis, motion=motion,
                                           size=frames[0].size), "seam_max": seam_max} if overlap_wrap == "on"
                    else {"off": OVERLAP_WRAP_OFF, "seam_max": seam_max})
            wrap["cycles"] = cycle["step_count"].get("cycles")
        try:
            frames, report_base["jump_repair"], overlap = _repair_jumps(frames, interpolate, facing=facing, required=repair == "on",
                                                                        screen=report_base["ghost_screen"], wrap=wrap)
        except (ValueError, rife_mod.RifeUnavailable) as exc:
            fix = (f"install RIFE (`{rife_mod.install_command()}`), or pass --repair auto to cut it as filmed with a warning"
                   if isinstance(exc, rife_mod.RifeNotInstalled) else "fix RIFE, or pass --repair off to cut it as filmed")
            error = (f"video-loop: {exc}; the loop has a frame to make (after a jump, for a part that rushes, at a wrap a part "
                     f"on its own beat does not close, or for a filmed ghost between two clean frames) — {fix} (docs/loop-repair.md)")
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error) from exc
        if overlap is not None:
            report_base["wrap_overlap"] = overlap
            for line in _overlap_lines(overlap):
                print(f"video-loop: warning: {line}", file=sys.stderr)
        # What became of each filmed ghost: given way (the cell and what it shows now) or kept, no clean frame beside it.
        ghost_rounds = [r for r in report_base["jump_repair"]["rounds"] if r.get("why") == "ghost"]
        # A cell given way to the frame beside it is that frame shown twice in a row — a step of nothing and a step of two,
        # wherever the ghost was. The seam gate asks whether the cut closes, so it reads that cell as filmed (`seam_as_filmed`):
        # at the wrap the step of two would read as a cut that does not close, and the step of nothing as one that closes
        # whatever the cut. Only a frame made (RIFE's, for a jump or a ghost) has the gate read the rendered cells.
        as_filmed = {r["target"]: filmed[r["target"]] for r in ghost_rounds if isinstance(r["taken"], dict)}
        if as_filmed:
            report_base["seam_as_filmed"] = sorted(as_filmed)
        if set(report_base["jump_repair"]["replaced"]) - set(as_filmed):
            report_base["seam_measurement"] = "rendered-cells"
        report_base["ghost_given_way"] = [{"frame": r["target"], "ghost": r["original"]["ghost"], "taken": r["taken"]}
                                          for r in ghost_rounds if r["taken"] is not None]
        report_base["ghost_kept"] = [{"frame": r["target"], "ghost": r["original"]["ghost"]} for r in ghost_rounds if r["taken"] is None]
        for line in _ghost_lines(report_base["jump_repair"]):
            print(f"video-loop: warning: {line}", file=sys.stderr)
        for r in report_base["jump_repair"].get("rounds", []):
            if r["outcome"] != "accepted" or not r["look"]:
                continue
            # A ghost round made one frame; a flow round a frame a drawing it took, each read on its own.
            looked = ([(f"frame {r['target']} was given way for a filmed ghost", r["proposal"])] if r["why"] == "ghost" else
                      [(f"frame{'s' if len(m['frames']) > 1 else ''} {', '.join(map(str, m['frames']))} "
                        f"{'were' if len(m['frames']) > 1 else 'was'} repaired", m["proposal"]) for m in r["made"] if m["look"]])
            for what, proposal in looked:
                print(f"video-loop: warning: {what} with RIFE's frame between its neighbours, which may "
                      f"cross-fade two drawings inside its outline (crossfade {proposal['crossfade']:.4f}); kept, a reading to "
                      "look at — see it in the loop, or cut it with --repair off (docs/loop-repair.md section 4)", file=sys.stderr)
        if "unmade" in report_base["jump_repair"]:
            unmade = report_base["jump_repair"]["unmade"]
            print(f"video-loop: warning: a jump frame was not repaired (frames {unmade['frames']}, {unmade['off']:.2f}x its "
                  "region's usual step off the line between its neighbours) — RIFE is not installed, so the loop is cut as filmed; "
                  f"run `{rife_mod.install_command()}` and cut it again (docs/loop-repair.md)", file=sys.stderr)
        elif "rife" in report_base["jump_repair"]:
            rush = report_base["jump_repair"]["surge"]
            print(f"video-loop: warning: a part of the loop rushes (frames {rush['frames']}, {rush['ratio']:.1f}x its steps "
                  "either side) and was not eased — RIFE is not installed, so the loop is cut as filmed; "
                  f"run `{rife_mod.install_command()}` and cut it again (docs/loop-repair.md section 2)", file=sys.stderr)
    elif repair != "off" and cycle["kind"] not in nonloop.KINDS:
        # A loop that is not a walk: its flow is not read (an idle's blink would leave it), its wrap alone is, and the
        # frames round a wrap that jumps are made again (the seam bridge, docs/loop-repair.md section 2). A pinned clip
        # the pin refuses is refused whatever its wrap shows.
        pin_refused = cycle["kind"] == "pinned" and cycle["pin_error"] > cycle["pin_tolerance"]
        try:
            frames, report_base["jump_repair"] = _bridge_seam(
                frames, interpolate, facing=facing, required=repair == "on", seam_max=seam_max,
                hold=report_base["cycle_drawings"]["hold"],
                skip="the pin does not close: the clip is refused on it" if pin_refused else None)
        except (ValueError, rife_mod.RifeUnavailable) as exc:
            fix = (f"install RIFE (`{rife_mod.install_command()}`), or pass --repair auto to cut it as filmed with a warning"
                   if isinstance(exc, rife_mod.RifeNotInstalled) else "fix RIFE, or pass --repair off to cut it as filmed")
            error = f"video-loop: {exc}; the loop's wrap jumps and the frames round it are to be made — {fix} (docs/loop-repair.md section 2)"
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error) from exc
        if report_base["jump_repair"]["replaced"]:
            report_base["seam_measurement"] = "rendered-cells"
        for line in _bridge_lines(report_base["jump_repair"]):
            print(f"video-loop: warning: {line}", file=sys.stderr)
    else:
        report_base["jump_repair"] = {"applied": False, "why": "--repair off" if repair == "off" else f"not a gait state ({state})"}
    if prof.gait:
        # The jolt is measured on the loop as it will play — after the repair — and reported on
        # every walk and run (docs/loop-repair.md section 3). Beyond the reference bounds it is a
        # warning line; it fails the loop only against a bound the caller passed (--jolt-max /
        # --head-step-max), and never with --repair off, which cuts and reports as filmed. A loop
        # whose repair could not run (no RIFE) is gated as filmed, and the failure says why.
        jolt = repair_mod.measure_jolt(frames, facing=facing)
        reference = {"jolt_max": repair_mod.JOLT_REFERENCE, "head_step_max": repair_mod.HEAD_STEP_REFERENCE}
        # The seam pop is a gate where the seam ratio gates the cut — not a pinned clip, whose gate is its pin — read on
        # the loop as it plays, whatever cut it and whether the repair ran. Its word is given after the seam and step
        # gates (below), so a cut they refuse is refused in their line.
        report_base["seam_pop_gate"] = (
            repair_mod.seam_pop_gate(jolt["seam_pop"], pop_max=seam_pop_max) if cycle["kind"] not in ("pinned", *nonloop.KINDS)
            else {"verdict": "not-applicable", "why": f"a {cycle['kind']} cut is gated by its pin"})
        warnings = repair_mod.jolt_verdict(jolt, **reference)
        if report_base["seam_pop_gate"]["verdict"] != "refused":
            # A pop the gate refuses is said in its refusal, not in a line that keeps the loop.
            warnings += repair_mod.seam_pop_verdict(jolt, head_step_max=repair_mod.HEAD_STEP_REFERENCE)
        # The held side below the top, read on the source frames when the cut was chosen again (local_cycle).
        warnings += repair_mod.held_edge_verdict(cycle.get("seam_pop"))
        warnings += coverage_verdict(cycle)
        warnings += local_cycle.step_verdict(cycle)
        warnings += half_step_verdict(cycle)
        gated = repair != "off" and (jolt_max is not None or head_step_max is not None)
        over = repair_mod.jolt_verdict(jolt, jolt_max=jolt_max, head_step_max=head_step_max) if gated else []
        report_base["jolt"] = {**jolt, "reference": reference, "warnings": warnings,
                               "gate": {"jolt_max": jolt_max, "head_step_max": head_step_max} if gated else None,
                               "gated": gated, "over": over, "passed": not over,
                               "measured": "after the jump repair" if report_base["jump_repair"].get("replaced") else "as filmed"}
        if over:
            error = (f"video-loop: loop jolts — {'; '.join(over)} ({cycle.get('kind', 'periodic')} length {L} frames from {i}"
                     f"{', after repairing frames ' + str(report_base['jump_repair']['replaced']) if report_base['jump_repair'].get('replaced') else ''}); "
                     "the motion does not play smoothly — regenerate the clip"
                     f"{' (the jump repair did not run: RIFE is not installed — ' + rife_mod.install_command() + ')' if 'rife' in report_base['jump_repair'] else ''}")
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error)
        left = _unfixed_lines(report_base["jump_repair"])
        if left and unfixed == "refuse":
            error = (f"video-loop: loop frames left off the flow — {'; '.join(left)} ({cycle.get('kind', 'periodic')} length {L} "
                     f"frames from {i}); the motion does not play smoothly — regenerate the clip")
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error)
        # A part that pops at the wrap of the cut taken: closed where the jump repair changed it away, else a take to film
        # again (`retake`), named.
        _pop_settled(report_base["part_pop"], frames, cut_frames)
        given = cycle_mode in ("fixed", "pinned")
        stand = stand_retake(cycle)
        report_base["retake"] = retakes(part_retake(report_base["part_pop"], (i, L), given=given), stand)
        if stand is not None:
            print(f"video-loop: warning: {_stand_line(stand)}", file=sys.stderr)
        pops_left = _pop_lines(report_base["part_pop"], given=given)
        if pops_left and unfixed == "refuse":
            error = (f"video-loop: a part pops at the loop's wrap — {'; '.join(pops_left)} ({cycle.get('kind', 'periodic')} "
                     f"length {L} frames from {i}); the motion does not play smoothly")
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error)
        for line in left:
            print(f"video-loop: warning: {line} — a jump played as filmed; --unfixed refuse refuses such a loop "
                  "(docs/loop-repair.md section 2)", file=sys.stderr)
        for line in pops_left:
            print(f"video-loop: warning: {line}; --unfixed refuse refuses such a loop (docs/loop-repair.md section 2, "
                  '"The part pop")', file=sys.stderr)
        for line in warnings:
            print(f"video-loop: warning: {line} (reference bound; the loop is kept — docs/loop-repair.md)", file=sys.stderr)
        if "ghost_given_way" not in report_base:
            # The jump repair did not run (--repair off): a filmed ghost is delivered as cut, named here;
            # `video-cycle-align` screens its source again and takes a clean frame beside it.
            for g in report_base["ghost_screen"]["ghosts"]:
                print(f"video-loop: warning: frame {g['frame']} of the cut (cycle/frame-{g['frame']:03d}.png) carries a part-covered band "
                      f"over {100 * g['ghost']:.2f} % of its body, at least {rife_mod.GHOST_THICK} px thick — a filmed ghost; delivered as cut "
                      "it shows (--repair off), and video-cycle-align takes a clean frame beside it (docs/loop-repair.md)", file=sys.stderr)
        if not report_base["ghost_screen"]["reads"]:
            print(f"video-loop: warning: the ghost screen does not read this cut — {report_base['ghost_screen']['why']}; no frame "
                  "of it is named a ghost, here or in video-cycle-align, so look at cycle/ (docs/loop-repair.md)", file=sys.stderr)
    held_record = report_base["held_fill"]
    upsampled = None
    # A cut played once carries its root motion (docs/video-pipeline.md, "Root motion"), read against the clip's floor.
    floor = root_motion_mod.clip_floor(files) if cycle["kind"] in root_motion_mod.KINDS else None
    retimed = None
    if asked_air is not None:
        if cycle["kind"] not in root_motion_mod.KINDS:
            report_base["air_retime"] = {"asked": asked_air, "applied": False,
                                         "why": f"a {cycle['kind']} cut: the air of a cut played once is retimed"}
        else:
            # The air given its own length, gravity and speed: its cells made again between the clip's frames at the
            # cut's rate (held_fill.at_times), each moved onto the parabola, the landing on along.
            try:
                frames, report_base["air_retime"], retimed = _air_retime(
                    frames, interpolate, fps=fps, rate=out_fps or fps, floor=floor, times=[i + k for k in range(L)],
                    declared=declared, asked=asked_air, meters_per_px=meters_per_px, facing=facing, judge_scale=judge_scale)
            except (ValueError, rife_mod.RifeUnavailable) as exc:
                error = (f"video-loop: {exc}; --air-retime makes the frames between the clip's — fix RIFE, or cut it "
                         "without --air-retime (docs/video-pipeline.md, \"Root motion\")")
                refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
                raise SystemExit(error) from exc
        for line in _air_lines(name, report_base["air_retime"]):
            print(f"video-loop: warning: {name}: {line}", file=sys.stderr)
        if retimed is not None:
            upsampled = retimed["times"]
            report_base["seam_measurement"] = "rendered-cells"
            as_filmed = {}  # the cells are each made at a time of their own: read as they play
            cycle_seconds = len(frames) / (out_fps or fps)
            n_out = min(len(frames), max(n_asked, round(cycle_seconds * gif_fps)))
            if out_fps is not None:
                record = report_base["air_retime"]
                held_record["out_fps"] = {"asked": out_fps, "fps": fps, "applied": True, "cells": len(frames),
                                          "times": [round(x, 4) for x in upsampled], "made": record["made"],
                                          "shown_again": record["shown_again"], "source_frames": retimed["source_frames"]}
    if out_fps is not None and retimed is None:
        # A rate above the clip's: every cell at its own time, made between the frames beside it (held_fill.upsample).
        try:
            frames, held_record["out_fps"] = _upsample(frames, interpolate, ring=ring, fps=fps, out_fps=out_fps,
                                                       judge_scale=judge_scale)
        except (ValueError, rife_mod.RifeUnavailable) as exc:
            error = (f"video-loop: {exc}; --out-fps {out_fps:g} makes the frames between the clip's — fix RIFE, or cut it "
                     "without --out-fps (docs/loop-repair.md section 7)")
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle})
            raise SystemExit(error) from exc
        for line in _out_fps_lines(held_record["out_fps"]):
            print(f"video-loop: warning: {name}: {line}", file=sys.stderr)
        if held_record["out_fps"]["applied"]:
            upsampled = held_record["out_fps"]["times"]
            report_base["seam_measurement"] = "rendered-cells"
            as_filmed = {}  # the cells are each made at a time of their own: read as they play
            if not ring:
                cycle_seconds = len(frames) / out_fps  # first to last frame as filmed, each cell 1/out_fps
            n_out = min(n_asked, len(frames))
    # The cut as delivered, read again: its poses over its cells.
    held_record.update(held_fill_mod.reading(held_fill_mod.same_steps(frames)))
    C = len(frames)  # the cells delivered: the cut's frames, or more at --out-fps
    cycle_dir.mkdir(parents=True, exist_ok=True)
    for old in cycle_dir.glob("frame-*.png"):
        old.unlink()
    shutil.rmtree(out_dir / CYCLE_SOURCE_DIR, ignore_errors=True)
    # A new cut makes a follow-through over the old strip stale (`video-follow` reads this source).
    (out_dir / FOLLOW_SOURCE).unlink(missing_ok=True)
    shutil.rmtree(out_dir / CYCLE_REFUSED_DIR, ignore_errors=True)
    for k, im in enumerate(frames):
        im.save(cycle_dir / f"frame-{k:03d}.png")
    # The standing height is the base still's pose as filmed, the clip's first frame — but a video model that
    # reframes the subject after it films that pose at another size than the walk. Where the cut is filmed
    # SIZE_HOLD_MIN or more larger or smaller than the first frame (gait_fallback.size_change: what its height,
    # mass and breadth all agree on, so not a pose), the standing height is read on the cut's own first frame.
    # A lead-in is such a reframing, and so is one too small to open a lead-in.
    standing_frame = 0
    if sized:
        report_base["cut_size"] = gait_fallback.size_change(gait_fallback.body_size(Image.open(files[0]).convert("RGBA")), cut_sizes)
        if abs(report_base["cut_size"]["change"]) >= gait_fallback.SIZE_HOLD_MIN:
            standing_frame = i
    try:
        strip_of = partial(build_strip, max_height=strip_height, cycle_seconds=cycle_seconds, body_height=body_height, anchor="feet" if anchor == "feet" else "none", kind=str(cycle.get("kind") or "periodic"),
                           standing_src=standing_height(files[standing_frame], standing_frame) if body_height is not None and cycle["kind"] != "lifetime" else None,
                           standing_frame=standing_frame)
        strip, strip_meta = strip_of(frames)
    except SystemExit as exc:
        # No body to measure or to crop the strip to: the cut is refused like any other.
        refuse(target, out_dir, name, {**report_base, "status": "failed", "error": str(exc), "cycle": cycle}, cut_written=True)
        raise
    # The cut as its seam is read: each cell given way to the frame beside it as filmed (`seam_as_filmed`).
    seam_frames = [as_filmed.get(k, f) for k, f in enumerate(frames)]
    strip_meta["source_cut"] = {"source": report_base["source"], "start": i, "length": L,
                                **({"samples": [i + k for k in strip_meta["sample_indices"]]} if upsampled is None else
                                   {"times": [round(i + upsampled[k], 4) for k in strip_meta["sample_indices"]]})}
    if upsampled is not None and out_fps is not None:
        # Each cell is a time of its own, at a rate above the clip's: the cells are not the clip's frames.
        strip_meta.update(out_fps=out_fps, source_fps=fps)
    if "air_retime" in report_base:
        # The air given its own length, or why it was not: a retimed cut's cells are times of their own, and the air's
        # are moved onto its parabola.
        strip_meta["air_retime"] = root_motion_mod.public(report_base["air_retime"])
    if cycle["kind"] in nonloop.KINDS:
        strip_meta.update(nonloop.metadata(cycle, strip_meta["sample_indices"] if upsampled is None else
                                           [upsampled[k] for k in strip_meta["sample_indices"]]))
    if state:
        # `video-cycle-align` reads the gait floor of this state when it screens a loop for two cycles.
        strip_meta["state"] = str(state).strip().lower()
    if "steps" in cycle:
        # A cut whose steps are known — counted (`--steps`) or two candidate lengths taken so a held part
        # closes (local_cycle._held_rank): `video-cycle-align` reads a return half way as its step.
        strip_meta["steps"] = cycle["steps"]["count"]
    # `video-cycle-align` reads the hold here, over the cut (`cycle_drawings`) and over the clip
    # (`drawings`, where the cut is too short to read): read on the keyed steps, since `--anchor
    # motion-auto` moves each frame of a pair apart in the cut it writes (sprite_gen/video/held.py).
    strip_meta["drawings"] = {k: report_base["drawings"][k] for k in ("hold", "drawings_per_second", "contrast", "frames")}
    strip_meta["cycle_drawings"] = {k: report_base["cycle_drawings"][k] for k in ("start", "length", "steps", "hold", "drawings_per_second", "contrast")
                                    } | ({"why": report_base["cycle_drawings"]["why"]} if "why" in report_base["cycle_drawings"] else {})
    if "clip_feet" in report_base:
        strip_meta["clip_feet"] = report_base["clip_feet"]
    # The cut's held cells, what became of them, and its poses over its cells (docs/loop-repair.md section 7).
    strip_meta["held_fill"] = held_fill_mod.public(held_record)
    if cycle["kind"] in root_motion_mod.KINDS:
        # Where the body is in each cell, what each touches and the air (docs/video-pipeline.md, "Root motion"): read on
        # the cells' frames as delivered, each at its time as it plays.
        cells_of = strip_meta["sample_indices"]
        played = len(frames) / cycle_seconds
        shown = [frames[k] for k in cells_of]
        touch = ([retimed["touch"][k] for k in cells_of] if retimed is not None else root_motion_mod.touching(
            shown, floor=floor, times=[i + (k if upsampled is None else upsampled[k]) for k in cells_of], declared=declared))
        report_base["root_motion"] = strip_meta["root_motion"] = root_motion_mod.sidecar(
            shown, rect=strip_meta["source_rect"], size=(strip_meta["w"], strip_meta["h"]),
            floor=None if floor is None else floor + (retimed["offset"][1] if retimed is not None else 0),
            seconds=[k / played for k in cells_of], touch=touch, meters_per_px=meters_per_px)
    # A cut frame that is no longer the frame as filmed for a filmed ghost, and one kept with no clean frame beside
    # it: `cycle/` is the alignment's material (`cycle.source/`), whose screen no longer sees a ghost given way, so the
    # strip says it, and an alignment carries it over (frames of the cut).
    for key in ("ghost_given_way", "ghost_kept"):
        if report_base.get(key):
            strip_meta[key] = report_base[key]
    if report_base.get("wrap_blend", {}).get("applied"):
        # The cells the wrap blend changed: they are no longer the cut's frames as filmed.
        strip_meta["wrap_blend"] = {k: report_base["wrap_blend"][k] for k in ("cells", "K")}
    if motion is not None:
        strip_meta["foot_anchor"] = anchor
        strip_meta["motion_anchor"] = motion
    if anchor == "body":
        strip_meta["foot_anchor"] = "body"
        strip_meta["wrap_dx_px"] = wrap_dx
        # the wrap as it now plays: last ramped frame -> first, against an ordinary step (a cell given way read as filmed)
        Dr = _distances(np.stack([_small_features(f) for f in seam_frames]))
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
    cell_record = _cell_record(strip_path.read_bytes(), [frames[k] for k in strip_meta["sample_indices"]], start=i,
                               length=L, samples=strip_meta["sample_indices"], anchored=anchored, wrap_dx=wrap_dx,
                               motion=motion, scales=clip_scales,
                               upsampled=None if upsampled is None else retimed if retimed is not None else held_record["out_fps"])
    # The record of its cells is the strip's own: written beside it, not into the report.
    atomic_write_text(out_dir / f"{name}.strip.json", json.dumps({**strip_meta, CELL_RECORD: cell_record}, indent=2) + "\n")

    # resampled GIF/WebP frames: same crop as the strip, n_out evenly across the cycle
    single_play = cycle["kind"] in nonloop.KINDS
    if single_play:
        preview_indices = nonloop.samples(strip_meta["frames"], n_out)
        idx = [strip_meta["sample_indices"][k] for k in preview_indices]
        pick = [strip.crop((k * strip_meta["w"], 0, (k + 1) * strip_meta["w"], strip_meta["h"])) for k in preview_indices]
    else:
        idx = [min(C - 1, i2) for i2 in (round(k * C / n_out) for k in range(n_out))]
        pick = resampled_cells(strip, strip_meta, idx, C)
    # The source frames the distances were read on: a cell made at --out-fps reads as the frame at or before its time.
    seam_idx = [i + (j if upsampled is None else min(L - 1, math.floor(upsampled[j]))) for j in idx]
    on_cells = report_base["seam_measurement"] == "rendered-cells"
    # A walk's or run's seam is read between its drawings, as its steps are (`seam_drawings`), where the seam ratio gates
    # the cut: not a pin or a cut played once. Any other loop's, and a walk drawn a frame each, on every frame.
    by_drawing = prof.gait and cycle["kind"] not in ("pinned", *nonloop.KINDS)
    seam_read = None
    read_idx = seam_idx
    if by_drawing and not on_cells:
        # The source frames the distances were read on, each drawing's first.
        firsts, seam_read = seam_drawings([gait_frames[j] for j in seam_idx])
        read_idx = [seam_idx[k] for k in firsts]
    resampled_adjacent = float(np.mean([D[read_idx[k], read_idx[k + 1]] for k in range(len(read_idx) - 1)]))
    resampled_seam = float(D[read_idx[-1], read_idx[0]])
    source_ratio = resampled_seam / resampled_adjacent if resampled_adjacent > 0 else math.inf
    # The body anchor moved the cells to close a wrap the source frames refuse: the cells that play are read again. Where
    # the seam ratio gates the cut, not a pin or a cut played once.
    ramped = (not on_cells and anchor == "body" and wrap_dx != 0 and cycle["kind"] not in ("pinned", *nonloop.KINDS)
              and source_ratio > seam_max)
    if on_cells or ramped:
        # The actual corrected/repaired/resampled strip cells, with the same limit — a cell given way to the frame
        # beside it as filmed (`seam_as_filmed`), on the strip the cut makes with those frames; a walk's between its drawings.
        cells = resampled_cells(*strip_of(seam_frames), idx, C) if as_filmed else pick
        on, cells_read = seam_drawings(cells) if by_drawing else (list(range(len(cells))), None)
        adjacent, wrap, steps = _seam_on_cells([cells[k] for k in on])
    if ramped:
        # The ramp spreads whatever offset the wrap shows over the loop: a jump of a few frames comes out moved into the
        # loop, not closed. So the cells overrule the source frames only where their wrap and every step of the loop are
        # within the same limit; otherwise the refusal is the source frames'.
        largest = int(np.argmax(steps))
        report_base["seam_after_anchor"] = {
            "source_seam_ratio": round(source_ratio, 4) if math.isfinite(source_ratio) else None,
            "seam_ratio": round(wrap / adjacent, 4) if adjacent > 0 else None,
            "largest_step_ratio": round(float(steps[largest]) / adjacent, 4) if adjacent > 0 else None,
            "largest_step_into": idx[on[largest + 1]],
            **(cells_read or {}),
        }
        if adjacent > 0 and wrap / adjacent <= seam_max and float(steps[largest]) / adjacent <= seam_max:
            report_base["seam_measurement"] = "rendered-cells"
            on_cells = True
    if on_cells:
        resampled_adjacent, resampled_seam = adjacent, wrap
        seam_read = cells_read
    if seam_read is not None:
        # What the seam ratio below was read on: the drawings, as many as there are, and the hold.
        report_base["seam_read"] = seam_read
    seam_ratio = resampled_seam / resampled_adjacent if resampled_adjacent > 0 else math.inf
    delay_ms = max(20, round(1000 * cycle_seconds / n_out))
    gif_path = out_dir / f"{name}.gif"
    webp_path = out_dir / f"{name}.webp"
    if cycle["kind"] in ("pinned", "once-return"):
        if cycle["pin_error"] > cycle["pin_tolerance"]:
            what = "the loop does not close" if cycle["kind"] == "pinned" else "it does not return to its first pose"
            error = (
                f"video-loop: the {cycle['kind']} clip does not end on its first frame (pin error {cycle['pin_error']:.4f} "
                f"exceeds {cycle['pin_tolerance']:.4f}); {what} — regenerate the clip with "
                "--last-frame set to its first frame"
            )
            refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle,
                                           "resampled_seam": resampled_seam,
                                           "resampled_inner_mean_adjacent": resampled_adjacent,
                                           "resampled_seam_ratio": seam_ratio}, cut_written=True)
            raise SystemExit(error)
    elif not single_play and seam_ratio > seam_max:
        known = seam_reason(report_base, cycle)
        error = (
            f"video-loop: loop seam ratio {seam_ratio:.2f} exceeds {seam_max} "
            f"({cycle['kind']} length {L} frames from {i}); the cycle does not close — "
            + (f"{known['words']} — regenerate with that part moving on the walk's beat" if known["reason"] else
               "regenerate with a complete return to the starting pose")
        )
        refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error,
                                       "cycle": cycle, "periodic_attempt": periodic_attempt,
                                       "resampled_seam": resampled_seam,
                                       "resampled_inner_mean_adjacent": resampled_adjacent,
                                       "resampled_seam_ratio": seam_ratio,
                                       "seam_refusal": {"reading": "wrap", "ratio": seam_ratio, "max": seam_max,
                                                        **{k: v for k, v in known.items() if k != "words"}}},
               cut_written=True)
        raise SystemExit(error)
    if prof.gait and not single_play:
        # Every step of the loop as it plays, read between its drawings: a frame that jumps inside the loop, which the
        # seam ratio at the wrap does not see. The bound is the step gate's, or the seam gate's where --seam-max lifts it;
        # a step its drawing moving a pixel of the frames as filmed makes is a pixel's jitter of a loop that barely moves,
        # and is not a jump (`step_jitter`, the cells' pixels of one of them the strip's scale).
        step_max = max(STEP_RATIO_MAX, seam_max)
        read = drawing_steps([strip.crop((k * strip_meta["w"], 0, (k + 1) * strip_meta["w"], strip_meta["h"]))
                              for k in range(strip_meta["frames"])], pixel=strip_meta["scale"])
        if read is None:
            report_base["step_gate"] = {"read": "drawings", "skipped": "fewer than three drawings", "max": step_max}
        else:
            of = strip_meta["sample_indices"]
            read.update(largest_into=of[read["largest_into"]], largest_from=of[read["largest_from"]], max=step_max,
                        lifted=seam_max > STEP_RATIO_MAX)
            for step in read.get("jitter", []):
                step.update({"from": of[step["from"]], "into": of[step["into"]]})
            report_base["step_gate"] = read
            ratio = read["largest_ratio"]
            if ratio is not None and ratio > step_max:
                known = _step_reason(report_base["jump_repair"], {read["largest_from"], read["largest_into"]})
                error = (f"video-loop: loop step ratio {ratio:.2f} exceeds {step_max:g} ({cycle['kind']} length {L} frames "
                         f"from {i}, into frame {read['largest_into']}, read between its {read['drawings']} drawings); the loop "
                         f"jumps inside{known.pop('words', '')} — regenerate the clip")
                refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error,
                                               "cycle": cycle, "periodic_attempt": periodic_attempt,
                                               "resampled_seam": resampled_seam,
                                               "resampled_inner_mean_adjacent": resampled_adjacent,
                                               "resampled_seam_ratio": seam_ratio,
                                               "seam_refusal": {"reading": "steps", "ratio": ratio, "max": step_max,
                                                                "into": read["largest_into"], **known}},
                       cut_written=True)
                raise SystemExit(error)
            if ratio is not None and ratio > STEP_RATIO_MAX:
                read["kept"] = f"--seam-max {seam_max:g} lifts the step gate"
                print(f"video-loop: warning: the loop's largest step between drawings is {ratio:.2f}x their mean (into frame "
                      f"{read['largest_into']}), over {STEP_RATIO_MAX:g}: it jumps inside — kept, as --seam-max {seam_max:g} "
                      "lifts the step gate", file=sys.stderr)
    popped = report_base.get("seam_pop_gate")
    if popped is not None and popped["verdict"] == "refused":
        # The top of the loop — the head, or a part held above it — jumps at the wrap: the loop does not close there,
        # whatever its seam ratio, an area measure, says. Not lifted by --seam-max: --seam-pop-max off turns it off.
        error = (f"video-loop: loop seam pop {popped['pop']:.2f} exceeds {popped['max']:g} ({cycle['kind']} length {L} frames "
                 f"from {i}): the top of the silhouette jumps into the loop's first frame "
                 f"{repair_mod.seam_pop_words(report_base['jolt']['seam_pop'], popped['max'])} — the loop does not close at "
                 "its top; cut it where it does, or regenerate the clip")
        refuse(target, out_dir, name, {**report_base, "status": "failed", "error": error, "cycle": cycle,
                                       "periodic_attempt": periodic_attempt, "resampled_seam": resampled_seam,
                                       "resampled_inner_mean_adjacent": resampled_adjacent,
                                       "resampled_seam_ratio": seam_ratio}, cut_written=True)
        raise SystemExit(error)
    # GIF has binary transparency. Bake the effect onto its declared black preview
    # background instead of turning its faint partial-alpha glow into opaque dark pixels.
    gif_background = "#000000" if cycle["kind"] == "lifetime" else None
    gif_pick = [effect.on_black(im) for im in pick] if gif_background is not None else pick
    try:
        save_clean_gif(gif_pick, gif_path, duration_ms=delay_ms, loop=None if single_play else 0,
                       alpha_threshold=128, opaque=gif_background is not None)
        write_webp(pick, webp_path, delay_ms=delay_ms, **({"loop": 1} if single_play else {}))
        # A filmed ghost given way to the frame beside it is that frame shown twice in a row: one frame of the GIF or WebP
        shown = shown_runs(pick)
        gif_report = verify_animation(gif_path, expect_frames=shown_runs(gif_pick), check_stale=False,
                                      **({"expected_loop": None} if single_play else {}),
                                      **({"background": gif_background} if gif_background is not None else {}))
        webp_report = verify_animation(webp_path, expect_frames=shown, check_stale=True, **({"expected_loop": 1} if single_play else {}))
    except SystemExit as exc:
        # The GIF or WebP could not be written (no img2webp) or failed its verification: nothing of the cut is handed over.
        refuse(target, out_dir, name, {**report_base, "status": "failed", "error": str(exc), "cycle": cycle}, cut_written=True)
        raise

    payload = {
        **report_base,
        "kind": REPORT_KIND,
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
        "status": REPORT_PASSED,
        "resampled_seam": resampled_seam,
        "resampled_inner_mean_adjacent": resampled_adjacent,
        "resampled_seam_ratio": None if single_play else round(seam_ratio, 4),
        **({"seam_measurement": "not-applicable", "playback_indices": preview_indices} if single_play else {}),
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


def _seam_pop_max(value: str) -> float | None:
    """`--seam-pop-max`: a bound over 0, or `off` (None)."""
    if value.strip().lower() == "off":
        return None
    try:
        bound = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r}: a bound over 0, or off") from None
    if not (math.isfinite(bound) and bound > 0):
        raise argparse.ArgumentTypeError(f"{value!r}: a bound over 0, or off")
    return bound


def add_arguments(parser: argparse.ArgumentParser) -> None:
    from sprite_gen.gen.facing import FACINGS
    parser.add_argument("--frames-dir", required=True, type=Path, help="keyed RGBA frames from `sprite-gen video-frames` (…/keyed)")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fps", type=float, default=24.0, help="frame rate of the keyed frames (from the frames report)")
    parser.add_argument("--state", help="motion state (idle/walk/run/jump/attack) — selects the detection window")
    parser.add_argument("--min-len", type=int, help="override: minimum cycle length in frames, on every path that sets one (a cut shorter fails)")
    parser.add_argument("--max-len", type=int, help="override: maximum cycle length in frames, on every path that sets one (a cut longer fails)")
    parser.add_argument("--n-out", type=int, help="frames in the GIF/WebP (default: cycle seconds x --gif-fps)")
    parser.add_argument("--gif-fps", type=float, default=GIF_FPS_DEFAULT, help=f"GIF/WebP playback density (default {GIF_FPS_DEFAULT:g}); every state plays at this rate regardless of cycle length")
    parser.add_argument("--seam-max", type=float, default=SEAM_RATIO_MAX, help=f"loop seam gate (default {SEAM_RATIO_MAX}): the wrap over the mean step. A walk or run is also held to the step gate — its largest step between drawings over their mean step at most {STEP_RATIO_MAX:g} (a step its drawing moving at most {STEP_JITTER_PX:g} pixels of the frames as filmed makes is a pixel's jitter, not held to it), and a fixed cut not one the clip's crossings count an odd number of steps; a --seam-max past {STEP_RATIO_MAX:g} lifts the step gate with it (every step's bound is then --seam-max, and an odd count a warning line)")
    parser.add_argument("--cycle", choices=CYCLE_MODES, default="auto", help="once: a performed action ending in a stable held pose, with no return or seam gate (with --start and --length, exactly those frames played once: a move cut out of a longer motion, no end pose read); auto: non-loop states use once, otherwise periodic first, one-shot failover for action states (recorded in the report); periodic / one-shot force one detector; fixed cuts exactly --start/--length (no detection, reported as kind=fixed); pinned: the whole clip but its last frame, for a clip pinned to end on its first frame (the gate is that pin); once-return: the whole clip with its last frame, played once from its first pose back to it (loop false, returns true), for a clip pinned to end on its first frame (the gate is that pin)")
    parser.add_argument("--start", type=int, help="fixed cut: first keyed frame of the cycle (with --cycle fixed, or --cycle once: the first frame played once)")
    parser.add_argument("--length", type=int, help="fixed cut: cycle length in frames (with --cycle fixed, or --cycle once: the frames played once)")
    parser.add_argument("--strip-height", type=int, default=STRIP_MAX_HEIGHT, help=f"cell/strip/GIF height cap in px (default {STRIP_MAX_HEIGHT}); the cycle is scaled down to fit, and never up unless --body-height asks for it")
    parser.add_argument("--body-height", type=int, help="scale so the STANDING height (the subject in the clip's first frame, i.e. the base still's pose) is this many px — the same value across states gives the same character size; --strip-height stays the cap")
    parser.add_argument("--anchor", choices=ANCHOR_MODES, default=None, help="motion-auto: automatic stable regions, local repeat selection and XY ramp (walk/run); motion: fixed cut with explicit regions; body (default for walk/run): measure the last-to-first head-and-torso offset once and spread it as a ramp over the cycle; feet: remove in-canvas drift (a straight-line trend) so every cell stands on the mean foot line; none: leave placement as filmed (default for other states)")
    parser.add_argument("--anchor-region", type=motion_anchor.parse_region, action="append", help="motion anchor only: repeat twice with head then torso x0,y0,x1,y1 in the first selected frame; requires --cycle fixed and at least 6 frames")
    parser.add_argument("--repair", choices=REPAIR_MODES, default="auto", help="auto (default): take out the frames off the walk's flow (one or two drawings that lie 3x their region's usual step off the line between the frames either side) and make them again with RIFE between those frames, each at its own time, so the loop keeps its length; adopt only without smear/outline faults, at most 3 calls and never made from or beside a frame made (docs/loop-repair.md section 2). A rejected group keeps its frames. A loop that is not a walk or run is not read for its flow: where its wrap steps over --seam-max times the mean of the three steps either side, one, two or four frames round it are made again by RIFE between the frames either side, the fewest first, taken only without faults and where every step from one to the other then reads within that limit (docs/loop-repair.md section 2, \"The seam bridge\"). Missing RIFE keeps the filmed loop with a warning (`sprite-gen rife install`); on: the same repair, but no RIFE fails the loop; off: cut as filmed")
    parser.add_argument("--overlap-wrap", choices=OVERLAP_WRAP_MODES, default="on", help="--anchor motion-auto, under --repair auto or on: on (default): a cut two steps long whose wrap a part on its own beat leaves open (the seam gate refuses it) has four to twelve frames beside its wrap made again between the clip's frames a cycle apart and its own, each read with RIFE's single passes for a part torn — 34 calls of the binary a frame, up to forty frames (docs/loop-repair.md section 2, \"The overlapped wrap\"); off: not tried, recorded `wrap_overlap.skipped` with a warning line, and the gate refuses the cut as filmed — for a host whose RIFE runs on a CPU, where one frame of a 960 px clip takes minutes")
    parser.add_argument("--wrap-blend", choices=wrap_blend_mod.MODES, default="auto", help="--cycle pinned with --anchor none: auto (default): where the pinned last frame draws the first again (the first frame and it differ by more than --seam-max times the cut's median step, at full size), the last half second of the cut takes the first frame's drawing back in a ramp, only where the two ends differ by a redraw and the cell stands where the pin stands; a ramp that steps more than the cut already does, or does not shrink the wrap, is not used and a warning line says so (report `wrap_blend`, docs/loop-repair.md section 6); off: the cut as filmed")
    parser.add_argument("--unfixed", choices=UNFIXED_MODES, default="warn", help="walk/run: a frame the jump repair leaves off the walk's flow (under --repair auto or on) — no group of up to two drawings brings it back, or the group that would was refused (its RIFE frame had a fault) — is recorded in jump_repair.unfixed; a part the size of a hand or a foot that pops at the wrap of the cut taken — the cut kept as cut — in part_pop.left, a take to film again (retake, reason part-pop; docs/loop-repair.md section 2, \"The part pop\"); warn (default): a warning line, the loop kept; refuse: the loop fails")
    parser.add_argument("--jolt-max", type=float, default=None, help=f"walk/run: fail the repaired loop when its jolt index (how far each step strays from its neighbours' mean, over the median step) exceeds this. Default: no gate — the index is reported and a value over {repair_mod.JOLT_REFERENCE} is a warning line")
    parser.add_argument("--head-step-max", type=float, default=None, help=f"walk/run: fail the repaired loop when the head's largest sideways move in one frame exceeds this %% of the body height. Default: no gate — reported, and over {repair_mod.HEAD_STEP_REFERENCE} is a warning line")
    parser.add_argument("--seam-pop-max", type=_seam_pop_max, default=repair_mod.SEAM_POP_REFERENCE, metavar="BOUND|off",
                        help=f"walk/run: fail the loop when the top of the silhouette (the head, or a part held above it) jumps into "
                             f"its first frame more than this many times its median step (`jolt.seam_pop`, read on the loop as it "
                             f"plays; default {repair_mod.SEAM_POP_REFERENCE:g}), a cut fixed by hand as one --anchor motion-auto "
                             "chooses — which reads the cuts it chooses among the same way and takes the next where the gate would "
                             "refuse the first. A top that cannot be read is not refused (`seam_pop_gate: unreadable`); a pinned "
                             f"cut is gated by its pin. --seam-max does not lift it. off: no gate — over {repair_mod.SEAM_POP_REFERENCE:g} "
                             "is a warning line (docs/loop-repair.md section 3, \"The seam pop\")")
    parser.add_argument("--facing", choices=FACINGS, default="right", help="which way the body faces in the frames (default right): the jolt index's hair, and the size reading video-loop-compare's jump guard reads, are behind it; the jump repair reads the flow either way")
    parser.add_argument("--size-hold", choices=SIZE_HOLD_MODES, default="auto", help="walk/run, --anchor motion-auto or --cycle fixed: auto (default) scales a clip whose height, read one cycle on, changes by 1 %% or more over the clip back to its first frame's size about its feet, and moves its feet to the first frame's row where they climb or sink by 1 %% of the height or more, applied before the cycle search or over the whole clip before a fixed cut is read (a fixed cut is held only where the cut itself shows the change a cycle of its length apart; recorded as size_hold, with each frame's move on a held fixed cut (size_hold.cut_frames), which video-loop-repair holds the source frames by again); off: the frames as filmed, except motion-auto's gait fallback, which after a first search that finds nothing still scales back a change of 3 %% or more by the same reading, its feet where they were filmed (recorded as gait_fallback)")
    parser.add_argument("--steps", type=int, choices=STEPS, default=None,
                        help="--anchor motion-auto: how many steps the cut it takes without this holds, as counted by whoever looked "
                             "at the loop (how often each foot lands) — the report's `cycle.step_screen` names a cut that may be one "
                             "step. 1: the cut is one step, so the walk is cut again two of its lengths long (the window [2L-1, 2L+1], "
                             "not bounded by half the clip but held inside --min-len/--max-len: a window that leaves none of it "
                             "fails), recorded `cycle.steps`; 2: the cut is kept, recorded. Either way the strip "
                             "metadata says `steps: 2`, which `video-cycle-align` reads")
    parser.add_argument("--view", choices=handed_mod.VIEWS, default=None,
                        help="walk/run: the view the clip is filmed from. The step count asks whether the feet, which need not "
                             "meet on the screen at every pass, meet once a cycle (`cycle.step_count.cycles`): where a pass "
                             "can hide, as without it, and from the front, the back or a diagonal also where passes are brief. "
                             "Wherever it is asked, the crossings mark cycles only where the body's top line rises and falls "
                             "twice an interval; the view's foot cue only refuses, where it says the other leg leads one "
                             "interval on. With no witness they stay on steps, as from the side, with a warning "
                             "(docs/loop-repair.md section 3, \"The step count\"). Default: not given, asked only where a pass "
                             "can hide")
    parser.add_argument("--body-turn", choices=body_turn_mod.MODES, default="warn",
                        help="--anchor motion-auto: the body's turn in the walk — each frame's torso read between the clip's first "
                             "frame and that frame mirrored (`body_turn`, docs/video-pipeline.md section 4, \"The body turn\"; a "
                             f"square still, a front or back view, is not read): a turn is the torso under {body_turn_mod.TURN_SIDE_MAX:g} "
                             f"for {body_turn_mod.TURN_RUN_MIN} frames or more, and no cut that holds a frame of one is taken while "
                             "another holds a cycle. Where none does: warn (default), the cut the search takes with the turn in, "
                             "and a warning line; refuse, the clip is refused in a line of its own (`video-loop: the body turns "
                             "in the walk`), to be filmed again at the still's angle")
    parser.add_argument("--fill-held", choices=held_fill_mod.MODES, default="auto",
                        help="neighbour cells of the cut that show the same picture (a frame shown twice or three times): auto "
                             "(default) makes each run of them again with RIFE's frames between the pose before and the pose "
                             f"after, the cut's length, beat, first and last cells and wrap kept, and a run longer than "
                             f"{held_fill_mod.HOLD_MAX} cells, the opening or closing pose of a cut played once, a loop's last "
                             "cells and a cut drawn on twos or threes kept as filmed; off cuts them as filmed. Either way the report and the strip metadata record "
                             "the held runs and the poses over the cells (`held_fill`, docs/loop-repair.md section 7); with no "
                             "RIFE installed the cells are shown as filmed, with a warning")
    parser.add_argument("--out-fps", type=float, default=None,
                        help="give the cut at this rate, above the clip's (--fps): every cell at its own time, made by RIFE "
                             "between the frames beside it — never a frame shown again. A loop keeps its cycle's seconds, a "
                             "cut played once runs from its first frame to its last; the strip metadata says `out_fps`. With no "
                             "RIFE installed the cut is delivered at its own rate, with a warning (docs/loop-repair.md section 7)")
    parser.add_argument("--judge-scale", type=quality_judge_scale, default=held_fill_mod.JUDGE_SCALE, metavar="S",
                        help="the size the game shows the cells at, S of the cut's size, over 0 and at most 1 (0.6 for cells "
                             "drawn at 60 %%): a frame made between two frames (the held fill, --out-fps, --air-retime) and "
                             "refused for its outline as cut is taken where its line keeps its ink as seen at that size; every "
                             "other reading (smear, ghost, hole, doubled, contact) and every other refusal as at 1, the cells "
                             "delivered at their own size, the strip metadata saying `judge_scale`. "
                             "Default 1, the frames as cut (docs/loop-repair.md section 7)")
    parser.add_argument("--air-retime", default=None, metavar="gravity=G,air=T,speed=V",
                        help="a cut played once: give each air with its take-off and landing in the cut (the frames between the "
                             "last contact and the next) a length of T seconds, a gravity of G m/s² and a speed along of V m/s, "
                             "any of them, each the clip's own where not given: its cells made again at the cut's rate, each "
                             "showing the clip's pose at the same share of its air, made by RIFE between the frames where it "
                             "falls between them, and moved onto the parabola; the take-off, the landing and the contacts as "
                             "filmed, the landing moved along. G and V need --meters-per-px. The strip metadata says "
                             "`air_retime`; with no RIFE installed the air is cut as filmed, with a warning "
                             "(docs/video-pipeline.md, \"Root motion\")")
    parser.add_argument("--meters-per-px", type=float, default=None,
                        help="the metres a pixel of the clip as filmed holds (the standing height in metres over the strip's "
                             "`body_src_h`): the root motion in metres too (`root_m`, each apex's `apex_m`), and --air-retime's "
                             "gravity and speed")
    parser.add_argument("--contact", action="append", default=None, metavar="NAME=SPANS",
                        help="a contact the floor does not show (a hand on a wall the clip does not draw), over zero-based frames "
                             "of the clip, as inspect-motion's spans: hand=12:20 (repeatable). The ground is read off the floor; "
                             "the air is the frames that touch neither")
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
        repair=str(kwargs.get("repair") or "auto"), facing=str(kwargs.get("facing") or "right"),
        jolt_max=kwargs.get("jolt_max"), head_step_max=kwargs.get("head_step_max"),  # type: ignore[arg-type]
        size_hold=str(kwargs.get("size_hold") or "auto"), steps=kwargs.get("steps"),  # type: ignore[arg-type]
        overlap_wrap=str(kwargs.get("overlap_wrap") or "on"), wrap_blend=str(kwargs.get("wrap_blend") or "auto"),
        unfixed=str(kwargs.get("unfixed") or "warn"),
        seam_pop_max=kwargs.get("seam_pop_max", repair_mod.SEAM_POP_REFERENCE),  # type: ignore[arg-type]
        view=kwargs.get("view"),  # type: ignore[arg-type]
        body_turn=str(kwargs.get("body_turn") or "warn"),
        fill_held=str(kwargs.get("fill_held") or "auto"), out_fps=kwargs.get("out_fps"),  # type: ignore[arg-type]
        air_retime=kwargs.get("air_retime"), meters_per_px=kwargs.get("meters_per_px"),  # type: ignore[arg-type]
        contacts=kwargs.get("contact"),  # type: ignore[arg-type]
        judge_scale=float(kwargs.get("judge_scale") or held_fill_mod.JUDGE_SCALE),  # type: ignore[arg-type]
    )
    summary = {k: payload[k] for k in ("state", "frames_total", "window", "cycle_seconds", "n_out", "delay_ms", "resampled_seam_ratio", "specks_dropped", "report")}
    summary["cycle"] = {k: payload["cycle"].get(k) for k in ("kind", "start", "length", "period_global", "ratio", "review_recommended", "half_period_guard", "fundamental")}
    summary["drawings"] = {k: payload["drawings"][k] for k in ("hold", "drawings_per_second")}
    if payload.get("wrap_blend"):
        summary["wrap_blend"] = {k: payload["wrap_blend"][k] for k in ("applied", "why", "residual_ratio", "wrap_ratio_before",
                                                                      "wrap_ratio_after") if k in payload["wrap_blend"]}
    if payload["periodic_attempt"]:
        summary["periodic_attempt"] = payload["periodic_attempt"]["why_rejected"]
    if payload.get("motion_anchor", {}).get("applied") is False:
        summary["motion_anchor"] = payload["motion_anchor"]
    if payload.get("jolt"):
        summary["jolt"] = {"index": payload["jolt"]["index"], "warnings": payload["jolt"]["warnings"],
                           "head_x_step_max_pct": payload["jolt"]["head"].get("x", {}).get("step_max_pct"),
                           "seam_pop": payload["jolt"]["seam_pop"].get("pop")}
    if payload["cycle"].get("seam_pop", {}).get("applied"):
        summary["cycle"]["seam_pop"] = payload["cycle"]["seam_pop"]
    if payload["cycle"].get("seam_pop_gate", {}).get("applied"):
        summary["cycle"]["seam_pop_gate"] = payload["cycle"]["seam_pop_gate"]
    if payload.get("seam_pop_gate"):
        summary["seam_pop_gate"] = payload["seam_pop_gate"]
    if "steps" in payload["cycle"]:
        summary["cycle"]["steps"] = payload["cycle"]["steps"]
        summary["cycle"]["coverage"] = payload["cycle"]["coverage"]
    if "step_screen" in payload["cycle"]:
        summary["cycle"]["step_screen"] = payload["cycle"]["step_screen"]
    if "two_lengths" in payload["cycle"]:
        summary["cycle"]["two_lengths"] = payload["cycle"]["two_lengths"]
    if "half_step" in payload["cycle"]:
        summary["cycle"]["half_step"] = payload["cycle"]["half_step"]
    if "step_count" in payload["cycle"]:
        counted = payload["cycle"]["step_count"]
        summary["cycle"]["step_count"] = {k: counted[k] for k in ("mode", "readable", "chosen_by", "cut", "why") if k in counted}
    if "cut_witness" in payload["cycle"]:
        summary["cycle"]["cut_witness"] = {k: payload["cycle"]["cut_witness"][k] for k in ("cut", "says", "by", "held", "mark")
                                           if k in payload["cycle"]["cut_witness"]}
    if payload.get("body_turn", {}).get("turns"):
        summary["body_turn"] = {k: payload["body_turn"][k] for k in ("verdict", "turns", "cut") if k in payload["body_turn"]}
    if payload.get("ghost_screen", {}).get("ghosts") or payload.get("ghost_screen", {}).get("reads") is False:
        summary["ghost_screen"] = payload["ghost_screen"]
    for key in ("ghost_given_way", "ghost_kept"):
        if payload.get(key):
            summary[key] = payload[key]
    if payload.get("wrap_overlap"):
        overlap = payload["wrap_overlap"]
        summary["wrap_overlap"] = {k: overlap[k] for k in ("seam", "frames", "side", "width", "seam_after", "skipped") if k in overlap}
    if payload.get("step_gate"):
        summary["step_gate"] = {k: payload["step_gate"][k] for k in ("drawings", "hold", "largest_ratio", "largest_into", "max",
                                                                     "skipped")
                                if k in payload["step_gate"]}
        if payload["step_gate"].get("jitter"):
            summary["step_gate"]["jitter"] = [step["into"] for step in payload["step_gate"]["jitter"]]
    if payload.get("jump_repair", {}).get("reading") == "seam":
        bridge = payload["jump_repair"]
        if "skipped" not in bridge:
            summary["jump_repair"] = {k: bridge[k] for k in ("reading", "seam", "replaced", "seam_after", "why") if k in bridge}
    elif payload.get("jump_repair", {}).get("replaced"):
        summary["jump_repair"] = {k: payload["jump_repair"][k] for k in ("replaced", "off_max_before", "off_max_after")}
        eased = [r for r in payload["jump_repair"]["rounds"] if r.get("why") == "ease" and r["outcome"] == "accepted"]
        if eased:
            summary["jump_repair"]["eased"] = eased[0]["replaced"]
    if payload.get("jump_repair", {}).get("unfixed"):
        summary.setdefault("jump_repair", {})["unfixed"] = [u["frames"] for u in payload["jump_repair"]["unfixed"]]
    if payload.get("part_pop", {}).get("pops"):
        popped = payload["part_pop"]
        summary["part_pop"] = {"pops": [{k: p[k] for k in ("from", "to", "at", "body_pct", "kind", "margin")} for p in popped["pops"]],
                               "outcome": popped["outcome"], "left": [[p["from"], p["to"]] for p in popped["left"]]}
    if payload.get("retake"):
        summary["retake"] = {k: payload["retake"][k] for k in ("reason", "remedy")}
    summary["strip"] = {k: payload["strip"][k] for k in ("path", "frames", "w", "h", "body_h", "delay_ms")}
    held = payload["held_fill"]
    summary["held_fill"] = {"poses_cells": held_fill_mod.said(held), "before": held_fill_mod.said(held["before"]),
                            "held_runs": held["held_runs"], "made": held["made"],
                            **({"unmade": held["unmade"]["cells"]} if "unmade" in held else {}),
                            **({"out_fps": {k: held["out_fps"][k] for k in ("asked", "applied", "cells")}} if "out_fps" in held else {})}
    if "root_motion" in payload:
        # Each air of a cut played once, as a person is told it: its seconds and its apex.
        summary["air"] = root_motion_mod.said(payload["root_motion"])
    if "air_retime" in payload:
        summary["air_retime"] = {k: payload["air_retime"][k] for k in ("applied", "why", "made", "shown_again", "cells", "poses")
                                 if k in payload["air_retime"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog="sprite-gen video-loop", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
