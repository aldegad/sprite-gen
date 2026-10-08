# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-cycle-align` — one cycle length for every direction of a set.

Each direction of a walk is filmed on its own, so its cycle comes out its own length (a Lite
set measured 16 to 27 frames). A game that turns a character mid-stride wants every direction
to be the same number of frames and to start on the same step. So every loop of the set is
resampled to the set's median length L*: frame k of the new loop is the source loop at time
k·L/L* (cyclic, offset 0), so a time that lands on a source frame takes that frame as filmed
and only the times between two frames are made by RIFE — each kept unless it smeared or lost its
outline, where the nearer source frame is taken instead (`--between auto`, the default; `rife`
keeps every made frame and warns, `nearest` makes none). Then each loop is turned to start as a
heel lands — read off the stride, the lowest row or the body's top line (`foot_strike`), never off
the frame's top edge, which a long ear owns. docs/loop-repair.md section 4.

Every frame between two source frames softens a little, so the length is the median (the one
that needs the fewest made frames across the set), and a loop already that long is not touched.

A loop whose cut holds each drawing for two or three frames (`held_drawings`, sprite_gen/video/
held.py), that the length leaves at under RETAKE_DRAWINGS_MIN drawings a second, with frames
between them that could not be made, is aligned all the same — and named in the report's `retake`
(reason `held-drawings`, with its numbers) and in a warning: the gap is wider than any interpolator
bridges, and only a new take fixes it.

The loop directories are `video-loop` output directories. Their first alignment keeps the cut
as filmed in `cycle.source/`; every later alignment reads from there, so running it again — or
with another length — never resamples a resampled loop.

Every alignment records where each cell of the new strip is from (`cycle_align.cells_from`): a frame
of `cycle.source/` (`{"source": i}`, as many times as it is taken) or a frame made between two of them
(`{"between": [i, j], "t": t}`). The first also records the strip as cut (`cycle_align.origin`,
`follow.cut_origin`: its sha256, cell size, scale and crop origin, its cells, and the body's motion as
`video-follow` reads it there), and every later one keeps it: regions somebody located on the cut's
first cell are carried to the aligned loop by these two (`video-follow --read-on`), not read again on a
first cell that is another. Neither changes a pixel of the strip, the GIF or the WebP.

Resampling to one length assumes every loop holds one cycle. A loop that holds two (a cut that
took two strides as one) would come out walking twice as fast as the rest, and pixels cannot tell
it from a loop of one stride whose two steps look alike. So each loop is screened first
(`cycle_screen`, sprite_gen/video/period.py): a half or a third of it that returns to a pose on
the motion's path makes it a suspect. A set with a suspect is stopped before anything is rewritten
(`--multi-cycle warn` aligns it as it is and says so), the report naming each suspect
(`suspects`). The count comes back from whoever looked: `--cycles <loop>=1` aligns that loop as
it is, `--cycles <loop>=k` takes one cycle — a k-th of it, from the start whose frame one cycle on
is the most like it — out of the loop as filmed (`take_cycle`) and aligns that. Nothing changes the
number of cycles in a loop but `--cycles`.

The other way round, a loop may be one step of a cycle twice as long: a slow walk whose cycle the
cut's window could not hold, its legs drawn alike. Nothing in the loop shows that — the next step
is not in it — so the screen is the one `video-loop --anchor motion-auto` ran over the whole clip
(`cycle.step_screen` in the cut's report, found beside the loop: `one_step`). Such a loop stops the
set the same way (`--multi-cycle`), and align cannot settle it: whoever counts its steps cuts it
again (`video-loop --steps 1`) and aligns the set anew, or says it holds a whole cycle
(`--cycles <loop>=1`). A loop whose steps were counted — cut again on that count, or two lengths
taken so a held part closes (its strip metadata says `steps: 2`) — returns half way at its second
step: that return is no suspect (status `steps`), and the loop is aligned as the one cycle it is.

Where a loop's view cannot tell its feet apart (`start_foot` null, with `foot_why`), it starts on its
larger strike, and the report names it under `unnamed_feet` with its two strike frames as they now
stand in `cycle/` (`candidates`, the first being the frame it starts on) — for whoever looks, a
person or a vision model. The answer comes back the same way as a count: `--foot <loop>=left|right`
says which own foot lands on the first candidate, and that loop alone starts as `--start-foot` lands
(half a cycle on, where the other foot lands first), recorded `start_foot_source: "given"`.

A set that needs a made frame where no RIFE is installed raises `rife.RifeNotInstalled` with
nothing rewritten: this command fails on it, `video-set` skips the alignment with a warning.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.gen import handedness as handed_mod
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.video import follow as follow_mod
from sprite_gen.video import held as held_mod
from sprite_gen.video import legs as legs_mod
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import period as period_mod
from sprite_gen.video import rife as rife_mod
from sprite_gen.video.interpolation_quality import SMEAR_WARN, OUTLINE_WARN, faults

SNAP = 0.03  # a sample time within this of a source frame takes that frame
SOURCE_DIR = loop_mod.CYCLE_SOURCE_DIR  # removed by video-loop whenever it cuts the loop again
RATE_TOLERANCE = 0.002  # frame rates read back from the strip metadata agree within this
# The foot-strike turn (docs/loop-repair.md section 4) reads the legs as the two-cycle screen
# records them (sprite_gen/video/legs.py). With a view the view picks the signal and the swings only
# say whether there are legs to read (`foot_strike`).
LOW_ALPHA, STRIDE_MIN, REACH_MIN, BODY_RUN = legs_mod.LOW_ALPHA, legs_mod.STRIDE_MIN, legs_mod.REACH_MIN, legs_mod.BODY_RUN
START_FOOT = "right"  # every loop of a set starts as this own foot lands, where its view can tell
SHADE_BAND = 0.15  # the shade cue reads the feet and lower legs: the lowest 15 % of the body
# The two strikes must differ by this to name a foot (`strike_foot`): shade in luma (0..1), depth in
# body heights. A smaller difference is the drawing as much as the feet, and the foot is left unnamed
# (`low-margin`) for whoever looks (`unnamed_feet`): a foot named wrong turns the loop half a cycle off
# with nobody asked, an unnamed one costs one look. The shade's margin is set above the margins at which
# it has named a foot wrong on drawn walks and under those at which it named them right
# (docs/loop-repair.md section 4).
SHADE_MARGIN = 0.025
FOOT_MARGIN = 0.01
LOW_MARGIN = "low-margin"  # how `foot_why` starts where the two strikes differ by less than the margin
# A cue that does not swing with the step is the drawing's own flicker: its once-a-cycle swing (first
# harmonic) must be at least this share of its spread over the cycle.
CUE_RHYTHM = 0.25
BETWEEN = ("auto", "rife", "nearest")  # how a time between two source frames is filled (resample)
DEFAULT_BETWEEN = "auto"
# A set with a loop the two-cycle screen suspects and no --cycles for it: fail (default) stops the
# set before anything is rewritten, warn aligns it as it is and names the loop in the warnings.
MULTI_CYCLE = ("fail", "warn")
CYCLES = (1, 2, 3)  # what --cycles <loop>=k may say a loop holds
# A loop whose source holds its drawings (two or three frames each, sprite_gen/video/held.py) and that
# the set's length leaves at fewer than this many drawings a second, with at least UNMADE_MIN of the
# frames between them not made (taken from the nearer source frame, or kept with a fault), is a take
# to film again: the reason `held-drawings` in the report (`retake`). A clip on twos shows 12 a
# second; at that rate or slower, each frame that could not be made holds a drawing a frame longer
# where the legs cross, and the loop halts there. A loop squeezed well past it shows its drawings
# closer than on twos, and is not named (docs/loop-repair.md section 4).
RETAKE_DRAWINGS_MIN = 13.0
RETAKE_UNMADE_MIN = 1
RETAKE_REASON = "held-drawings"


def held_drawings(frames: list[Image.Image], *, fps: float, clip: dict[str, Any] | None = None,
                  cut: dict[str, Any] | None = None) -> dict[str, Any]:
    """Whether a loop's drawings are held, and how many its cycle holds (`held.measure`).

    `cut` and `clip` are the records `video-loop` wrote in the strip metadata, both read on the clip's
    steps as keyed: `cycle_drawings` over the cut (`source` span) and `drawings` over the whole clip
    (`source` clip) — the hold and the drawings a second, over the cycle's frames. The cut's is the
    loop's: a clip held for part of its length is held where it was cut. The clip's stands where the
    cut was too short to read, or was cut before that record (`span_why` says which). The cut cycle
    is the fallback for a loop cut before either record (`source` cycle, read as a ring): it is short,
    and `--anchor motion-auto` shifts each cut frame, so a pair's repeat may no longer read as one."""
    n = len(frames)
    if clip and clip.get("hold"):
        whole = {"hold": clip["hold"], "drawings_per_second": clip["drawings_per_second"], "contrast": clip.get("contrast")}
        if cut and cut.get("hold"):
            per_second = float(cut["drawings_per_second"])
            return {"source": "span", "hold": cut["hold"], "contrast": cut.get("contrast"), "start": cut["start"], "length": cut["length"],
                    "frames": n, "fps": fps, "drawings_per_second": per_second, "drawings": round(per_second * n / fps, 2), "clip": whole}
        per_second = float(clip["drawings_per_second"])
        return {"source": "clip", "hold": clip["hold"], "contrast": clip.get("contrast"), "frames": n, "fps": fps,
                "drawings_per_second": per_second, "drawings": round(per_second * n / fps, 2),
                "span_why": cut.get("why", "the cut was not read") if cut else "no record of the cut's own steps (cut before it was kept)"}
    D = _ring(frames)
    return {"source": "cycle", **held_mod.measure([float(D[k, (k + 1) % n]) for k in range(n)], fps=fps, cyclic=True)}


def retake(drawings: dict[str, Any], facts: dict[str, Any], *, fps: float) -> dict[str, Any] | None:
    """The reason to film a loop again, with its numbers, or None. `drawings` is the source's
    `held_drawings`, `facts` its `resample` facts: a held source stretched to under
    RETAKE_DRAWINGS_MIN drawings a second, with RETAKE_UNMADE_MIN or more frames between them not made
    — taken from the nearer source frame (`nearest_at`), or made with a fault and kept (`--between rife`)."""
    if not drawings.get("hold") or drawings["hold"] < 2:
        return None
    seconds = facts["to"] / fps
    per_second = drawings["drawings"] / seconds
    unmade = len(facts.get("nearest_at", [])) + sum(1 for m in facts.get("smear", []) if m["method"] == "rife" and m["faults"])
    if per_second >= RETAKE_DRAWINGS_MIN or unmade < RETAKE_UNMADE_MIN:
        return None
    return {"reason": RETAKE_REASON, "hold": drawings["hold"], "source": drawings["source"], "drawings": drawings["drawings"],
            "drawings_per_second_filmed": drawings["drawings_per_second"], "drawings_per_second": round(per_second, 3),
            "frames_per_drawing": round(facts["to"] / drawings["drawings"], 3), "unmade": unmade,
            "limits": {"drawings_per_second_min": RETAKE_DRAWINGS_MIN, "unmade_min": RETAKE_UNMADE_MIN}}


def _retake_line(name: str, r: dict[str, Any]) -> str:
    """A loop to film again, in words."""
    return (f"{name}: film this direction again ({r['reason']}) — its {'clip' if r['source'] == 'clip' else 'cut'} shows each drawing for {r['hold']} frames "
            f"({r['drawings']:g} drawings in the cycle, {r['drawings_per_second_filmed']:g} a second as filmed); at the set's length "
            f"that is {r['drawings_per_second']:g} drawings a second, {r['frames_per_drawing']:g} frames apart, and {r['unmade']} "
            "frame(s) between them could not be made, so the loop halts there. A new take that draws every frame "
            "fixes it; no interpolator draws legs that swap places across a gap this wide (docs/loop-repair.md section 4)")


def resample(frames: list[Image.Image], length: int, interpolate: rife_mod.Interpolate | None,
             *, between: str = DEFAULT_BETWEEN, cells_from: list[dict[str, Any]] | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """`frames` as one cycle, resampled to `length` frames at times k·L/length (offset 0). Where `cells_from` is a
    list, where each frame came from is appended to it: `{"source": i}` for a frame of `frames` taken as it is, `{"between":
    [i, j], "t": t}` for one made between two.

    A time between two source frames is made by `interpolate`, with what it added and the outline it
    lost measured (`rife.smear`) and judged (`faults`); `between` auto keeps a made frame with no
    fault and takes the nearer source frame where it has one, rife keeps every made frame, nearest
    makes none and takes the nearer source frame (the motion keeps the filmed frames at up to half a
    frame off their time). Every made frame is listed in `smear` with its `method`: rife (kept) or
    nearest (the nearer source frame taken instead)."""
    count = len(frames)
    if length < 2:
        raise ValueError(f"cycle length {length} is too short")
    if between not in BETWEEN:
        raise ValueError(f"between must be one of {', '.join(BETWEEN)}, not {between!r}")
    out: list[Image.Image] = []
    made_at: list[int] = []
    nearest_at: list[int] = []
    smears: list[dict[str, Any]] = []
    for k in range(length):
        t = k * count / length
        i = int(np.floor(t))
        frac = t - i
        nearer = (i + (frac >= 0.5)) % count
        if frac < SNAP:
            taken: dict[str, Any] = {"source": i % count}
        elif frac > 1 - SNAP:
            taken = {"source": (i + 1) % count}
        elif between == "nearest":
            taken = {"source": nearer}
            nearest_at.append(k)
        else:
            if interpolate is None:
                raise ValueError(f"frame {k} of {length} falls between source frames and no interpolator is available")
            a, b = frames[i % count], frames[(i + 1) % count]
            made = interpolate(a, b, frac)
            measure = rife_mod.smear(made, a, b)
            wrong = faults(measure)
            keep = between == "rife" or not wrong
            taken = {"between": [i % count, (i + 1) % count], "t": round(frac, 4)} if keep else {"source": nearer}
            (made_at if keep else nearest_at).append(k)
            smears.append({"at": k, "method": "rife" if keep else "nearest", **measure, "faults": wrong})
        out.append(made if "between" in taken else frames[taken["source"]])
        if cells_from is not None:
            cells_from.append(taken)
    facts: dict[str, Any] = {"from": count, "to": length, "between": between, "taken": length - len(made_at),
                             "made_by_rife": len(made_at), "made_at": made_at}
    if between != "rife":
        facts["nearest_at"] = nearest_at
    if between != "nearest":
        facts["smear"] = smears
    return out, facts


strike_signals = legs_mod.strike_signals


def _feet(frame: Image.Image) -> tuple[np.ndarray, np.ndarray, int]:
    """(solid mask, the picture's right half of the feet as a column mask, body height): the halves
    split at the middle of the foot band (the lowest FOOT_BAND of the frame's own height)."""
    solid = np.asarray(frame.getchannel("A")) >= LOW_ALPHA
    rows = np.nonzero(solid.any(axis=1))[0]
    y1, height = int(rows[-1]), int(rows[-1] - rows[0] + 1)
    xs = np.nonzero(solid[y1 + 1 - max(1, round(height * legs_mod.FOOT_BAND)) : y1 + 1].any(axis=0))[0]
    return solid, np.arange(solid.shape[1]) >= (xs[0] + xs[-1]) / 2, height


def _depth_cue(frame: Image.Image) -> float:
    """How much lower the picture's right foot is drawn than its left, as a fraction of the body's
    height (the lowest solid row of each half)."""
    solid, right, height = _feet(frame)
    low = [int(np.nonzero(solid[:, half].any(axis=1))[0].max()) if solid[:, half].any() else 0 for half in (~right, right)]
    return (low[1] - low[0]) / height


def _shade_cue(frame: Image.Image, facing: str) -> float:
    """How much lighter the front foot is drawn than the back one (mean luma 0..1 of the lowest
    SHADE_BAND of the body, front half against back half; the front is the side faced)."""
    solid, right, height = _feet(frame)
    rows = np.nonzero(solid.any(axis=1))[0]
    band = np.zeros_like(solid)
    band[rows[-1] + 1 - max(1, round(height * SHADE_BAND)) : rows[-1] + 1] = True
    rgb = np.asarray(frame.convert("RGB"), dtype=np.float32) / 255.0
    luma = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    front = right if facing == "right" else ~right
    parts = [luma[solid & band & cols[None, :]] for cols in (front, ~front)]
    return float(parts[0].mean() - parts[1].mean()) if all(p.size for p in parts) else 0.0


def strike_foot(frames: list[Image.Image], strikes: tuple[int, int], view: str, facing: str | None) -> dict[str, Any]:
    """Which of the character's own feet lands at each of a walk's two strikes, from how the view
    draws them (`handedness.placement` says where each own side is), or why it cannot say.

    - front or back: the feet are side by side in the picture, one nearer the viewer and drawn
      lower. Seen from the front the foot landing is the one stepping toward the viewer, the
      lower one; seen from the back it is the one stepping away, the higher one, its own side
      where the picture puts it.
    - side or diagonal: the feet are one in front of the other, so the picture cannot tell them
      apart by place; but the far leg is drawn in shade. At the strike whose front foot is the
      lighter (against the back one), the near leg is in front.

    The cue must follow the step — its once-a-cycle swing at least CUE_RHYTHM of its spread over
    the cycle — and, read over each strike frame and its two neighbours, the two strikes must differ
    by FOOT_MARGIN (depth, of the body's height) or SHADE_MARGIN (luma); otherwise the foot is not
    named, `why` starting LOW_MARGIN."""
    n = len(frames)
    lateral = view in handed_mod.LATERAL_VIEWS
    cue = (lambda f: _shade_cue(f, facing)) if lateral else _depth_cue  # type: ignore[arg-type]
    every = np.array([cue(f) for f in frames])
    values = [float(np.mean([every[(k + d) % n] for d in (-1, 0, 1)])) for k in strikes]
    margin = abs(values[0] - values[1])
    # the cue swings with the feet, once a cycle: its first harmonic against its whole spread
    swing = float(abs(np.sum((every - every.mean()) * np.exp(-2j * np.pi * np.arange(n) / n))) * 2 / n)
    rhythm = swing / float(every.std()) if every.std() > 0 else 0.0
    record: dict[str, Any] = {"cue": "shade" if lateral else "depth", "at": list(strikes), "values": [round(v, 4) for v in values],
                              "margin": round(margin, 4), "rhythm": round(rhythm, 3)}
    if rhythm < CUE_RHYTHM:
        return {**record, "feet": None, "why": f"the {record['cue']} does not follow the step (rhythm {rhythm:.2f})"}
    least = SHADE_MARGIN if lateral else FOOT_MARGIN
    if margin < least:
        return {**record, "feet": None, "why": f"{LOW_MARGIN}: the two strikes differ by {margin:.4f} in {record['cue']}, "
                                               f"under the {least:g} it takes to name a foot"}
    hi = 0 if values[0] > values[1] else 1
    at = {s: handed_mod.placement(s, view, facing) for s in handed_mod.SIDES}
    if lateral:
        hi_foot = next(s for s in handed_mod.SIDES if at[s]["depth"] == "near")
    else:  # at the higher cue the picture's right foot is the lower one
        lower = next(s for s in handed_mod.SIDES if at[s]["picture"] == "right")
        hi_foot = lower if view == "front" else next(s for s in handed_mod.SIDES if s != lower)
    other = next(s for s in handed_mod.SIDES if s != hi_foot)
    return {**record, "feet": [hi_foot, other] if hi == 0 else [other, hi_foot]}


def foot_strike_start(frames: list[Image.Image], *, view: str | None = None) -> int:
    """The frame a loop is turned to start on (`foot_strike`)."""
    return foot_strike(frames, view=view)["start"]


def foot_strike(frames: list[Image.Image], *, view: str | None = None, foot: str = START_FOOT,
                given: str | None = None) -> dict[str, Any]:
    """Where a loop starts: the frame a heel has just landed, read off one signal smoothed over its
    neighbours (1-2-1), never off the frame's top edge, which a long ear or a hat's point owns.

    - `stride`: seen from the side or a diagonal the feet are widest apart as the front heel lands.
    - `reach`: seen from the front or back the feet pass one behind the other and the stride
      hardly moves, but the foot nearest the viewer is drawn lowest, and lowest when the feet are
      furthest apart — as the heel lands.
    - `body_low`: a body with no legs to read starts where its top line is lowest.

    With the loop's `view` the view picks the signal and the swings only say whether there are legs
    to read: a side or diagonal view turns on the stride where either swing says legs (the stride by
    STRIDE_MIN of the body's height, or the lowest row by REACH_MIN); a front or back view turns on
    the reach where the lowest row swings by REACH_MIN, since its foot band widens as a foot is
    lifted out of it, mid-step, not as a heel lands. Without a view the picture alone says: the
    stride where it swings by STRIDE_MIN, else the reach where it swings by REACH_MIN.

    A walk has two such moments a cycle, half a cycle apart: the larger, and the frame half a cycle
    on (not the other peak: one step can stride much less than the other, or not peak at all).
    With the loop's `view` (`side@right`, `front`, …) the one where `foot` lands is taken
    (`strike_foot`); without a view, or where the view's cue cannot tell, the larger, the first on
    a tie, and `start_foot` is None with the reason.

    `given` is the own foot that lands on the larger strike (`strikes[0]`) as whoever looked at the
    loop saw it (`--foot`): the loop starts where `foot` lands — that strike or the one half a cycle
    on — with `start_foot_source` "given", whatever the view's cue says; where the cue named the feet
    otherwise, `foot_disagrees` says so. `start_foot_source` is "engine" where the cue named them,
    None where nobody did."""
    if foot not in handed_mod.SIDES:
        raise ValueError(f"foot must be left or right, not {foot!r}")
    if given is not None and given not in handed_mod.SIDES:
        raise ValueError(f"the given foot must be left or right, not {given!r}")
    sig = strike_signals(frames)
    swing = legs_mod.swings(sig)
    legs = legs_mod.legs_by(sig)
    name, _, facing = (view or "").partition("@")
    if view is None:
        by = legs[0] if legs else "body_low"
    else:
        handed_mod.validate_view(name, facing or None)
        by = ("stride" if legs else "body_low") if name in handed_mod.LATERAL_VIEWS else ("reach" if "reach" in legs else "body_low")
    signal = sig["top" if by == "body_low" else by]
    n = len(signal)
    smooth = [(signal[(k - 1) % n] + 2 * signal[k] + signal[(k + 1) % n]) / 4 for k in range(n)]
    first = max(range(n), key=lambda k: smooth[k])
    second = (first + n // 2) % n
    out: dict[str, Any] = {"start": first, "by": by, "stride_swing": round(swing["stride"], 4), "reach_swing": round(swing["reach"], 4),
                           "strikes": [first, second], "start_foot": None, "start_foot_source": None}
    if view is None:
        read: dict[str, Any] = {**out, "foot_why": "no view given for this loop"}
    elif by == "body_low":
        read = {**out, "foot_why": f"no legs to read: the foot band swings {swing['stride']:.3f} of the body's height "
                                   f"(legs from {STRIDE_MIN}), the lowest row {swing['reach']:.3f} (from {REACH_MIN})"}
    else:
        found = strike_foot(frames, (first, second), name, facing or None)
        if found["feet"] is None:
            read = {**out, "foot": found, "foot_why": found["why"]}
        else:
            read = {**out, "start": (first, second)[found["feet"].index(foot)], "start_foot": foot, "start_foot_source": "engine", "foot": found}
    if given is None:
        return read
    feet = [given, next(s for s in handed_mod.SIDES if s != given)]
    told = {**{k: v for k, v in read.items() if k != "foot_why"}, "start": (first, second)[feet.index(foot)], "start_foot": foot,
            "start_foot_source": "given", "foot_given": feet}
    if read["start_foot"] is None:
        told["foot_unnamed_why"] = read["foot_why"]
    elif read["foot"]["feet"] != feet:
        told["foot_disagrees"] = read["foot"]["feet"]
    return told


def _ring(frames: list[Image.Image]) -> np.ndarray:
    """The distances between every two frames of a loop, on the analysis thumbnail `video-loop` cuts by."""
    flat = np.stack([loop_mod._small_features(f) for f in frames])
    return np.stack([np.abs(flat - row).mean(axis=1) for row in flat])


def cycle_screen(frames: list[Image.Image], *, fps: float, state: str | None = None) -> dict[str, Any]:
    """Whether a loop may hold two or three cycles (`period.screen`, the loop read as a ring).

    A half or a third of it that is a repeat, no shorter than the state's gait floor (under it two
    look-alike halves are the two steps of one stride, which the half-period guard owns), and that
    is not another pose is a suspect. `state` is the loop's motion state (`video-loop` writes it in
    the strip metadata): without one there is no floor beyond four frames and no steps to record."""
    n = len(frames)
    profile = loop_mod.profile_for(state)
    floor = round(profile.min_seconds * fps) if profile.gait else 0
    base = {"state": state, "gait_floor": floor or None}
    if n < 8:  # no half of it is four frames long: nothing shorter could be a cycle
        return {**base, "period": n, "legs": {"by": None, "why": "too short to halve"}, "checked": [], "suspects": []}
    D = _ring(frames)
    ring = np.arange(n)
    prof = {lag: float(D[ring, (ring + lag) % n].mean()) for lag in range(1, n)}
    minima = [lag for lag in range(2, n - 1) if prof[lag] <= prof[lag - 1] and prof[lag] <= prof[lag + 1]]
    return {**base, **period_mod.screen(
        n, D=D, prof=prof, minima=minima, mean=float(np.mean(list(prof.values()))), lowest=max(4, floor),
        periodicity_min=loop_mod.PERIODICITY_MIN, signals=loop_mod.leg_signals(frames), gait=profile.gait, cyclic=True,
        floor_why="the gait floor" if floor >= 4 else "a cycle", fps=fps)}


def take_cycle(frames: list[Image.Image], cycles: int) -> tuple[list[Image.Image], dict[str, Any]]:
    """One cycle out of a loop that holds `cycles`: the run of round(n / cycles) frames, read as a
    ring, whose frame one cycle on is the most like its first (the earliest on a tie) — so it wraps
    as the loop played on."""
    n = len(frames)
    length = round(n / cycles)
    if length < 2:
        raise ValueError(f"{n} frames cannot hold {cycles} cycles")
    D = _ring(frames)
    step = float(np.mean([D[k, (k + 1) % n] for k in range(n)]))
    start = min(range(n), key=lambda k: (float(D[k, (k + length) % n]), k))
    out = [frames[(start + k) % n] for k in range(length)]
    inner = float(np.mean([D[(start + k) % n, (start + k + 1) % n] for k in range(length - 1)])) if length > 1 else 0.0
    seam = float(D[(start + length - 1) % n, start])
    return out, {"from": n, "cycles": cycles, "start": start, "length": length, "exact": n % cycles == 0,
                 "repeat_over_step": round(float(D[start, (start + length) % n]) / step, 4) if step > 0 else None,
                 "seam_ratio": round(seam / inner, 4) if inner > 0 else None}


class CycleSuspects(SystemExit):
    """A set stopped, nothing rewritten, on loops that may hold more than one cycle. `suspects` is
    the report's typed list: each loop, its candidates and the arguments that settle it; `command`
    the alignment to run once they are counted (each `--cycles <loop>=<…>` filled in)."""

    def __init__(self, message: str, suspects: list[dict[str, Any]], command: str = ""):
        super().__init__(message)
        self.suspects = suspects
        self.command = command


def cut_report(loop_dir: Path, meta_path: Path, meta: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    """The report `video-loop` wrote for this loop's cut, or None: a JSON of kind
    sprite-gen-video-loop-report, passed, in the loop's directory or the one above it (a video-set
    item keeps it there), whose strip is this loop's and whose cut (start, length) is the one the strip
    metadata records (`cycle_drawings`) — a report of another cut of the same clip is not it."""
    cut = meta.get("cycle_drawings") or {}
    if "start" not in cut or "length" not in cut:
        return None
    strip_name = meta_path.name[: -len(".strip.json")] + ".strip.png"
    for folder in (loop_dir, loop_dir.parent):
        for path in sorted(folder.glob("*.json")):
            if path.name.endswith(".strip.json"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("kind") != "sprite-gen-video-loop-report" or data.get("status") != "passed":
                continue
            cycle = data.get("cycle") or {}
            if (Path(str((data.get("strip") or {}).get("path", ""))).name == strip_name
                    and (cycle.get("start"), cycle.get("length")) == (cut["start"], cut["length"])):
                return path, data
    return None


def one_step(loop_dir: Path, meta_path: Path, meta: dict[str, Any]) -> dict[str, Any] | None:
    """A loop the cut's step screen suspects of being one step (`cut_report`, `cycle.step_screen`), and
    whose steps nobody counted (no `steps` in its strip metadata): the screen's evidence and the report
    it is in. None otherwise — and where no report is found."""
    if meta.get("steps") is not None:
        return None
    found = cut_report(loop_dir, meta_path, meta)
    if found is None:
        return None
    path, data = found
    screen = (data.get("cycle") or {}).get("step_screen") or {}
    if not screen.get("suspect"):
        return None
    return {**{k: screen[k] for k in ("length", "lag", "ratio", "depth", "pairs", "coverage", "leg_peaks", "why") if k in screen},
            "seconds": round(screen["lag"] / float(data["fps"]), 3), "report": str(path)}


def _suspect_line(entry: dict[str, Any]) -> str:
    """One loop's suspects, in words: where it returns, how, and what that would make it."""
    if "one_step" in entry:
        step = entry["one_step"]
        line = (f"{entry['dir']}: may be one step of a cycle {step['lag']} frames long ({step['seconds']:.3f} s) — it repeats "
                f"{step['ratio']:.2f}x as far two lengths on in its clip as one length on; count its steps, and if it is one, cut it "
                f"again with `sprite-gen video-loop … --steps 1` (the arguments are in {step['report']}) and align the set anew")
        return line + ("; it may also hold " + "; or ".join(_returns_said(entry)) if entry["candidates"] else "")
    return f"{entry['dir']}: may hold " + "; or ".join(_returns_said(entry))


def _returns_said(entry: dict[str, Any]) -> list[str]:
    parts = []
    for row in entry["candidates"]:
        evidence = [f"pose {row['pose']:.2f}" if row.get("pose") is not None else None,
                    f"steps {row['steps']:.2f}" if row.get("steps") is not None else None]
        parts.append(f"{row['cycles']} cycles of {row['period']} frames ({row['seconds']:.3f} s) — {row['why']}"
                     + (f" [{', '.join(e for e in evidence if e)}]" if any(evidence) else ""))
    return parts


def _resolve_loops(flag: str, given: dict[str, Any] | None, loops: list[tuple[Path, Path, dict[str, Any], float]],
                   allowed: tuple[Any, ...]) -> dict[int, Any]:
    """`--cycles` or `--foot` keys to loop positions: a key names a loop by its directory, its strip's
    name, its directory's name or — for a directory named `loop` (a video-set item) — its parent's
    name, and must name exactly one; its value must be one of `allowed`."""
    out: dict[int, Any] = {}
    for key, value in (given or {}).items():
        if value not in allowed:
            raise SystemExit(f"video-cycle-align: {flag} {key}={value}: "
                             + (f"a loop holds {', '.join(map(str, CYCLES))} cycles here" if flag == "--cycles"
                                else f"the foot that lands on the loop's first strike is one of {', '.join(allowed)}"))
        path = Path(key).expanduser()
        hits = [i for i, (d, meta_path, _, _) in enumerate(loops)
                if (path.exists() and path.resolve() == d)
                or key in (meta_path.name[: -len(".strip.json")], d.name) or (d.name == "loop" and key == d.parent.name)]
        hits = sorted(set(hits))
        if len(hits) != 1:
            raise SystemExit(f"video-cycle-align: {flag} {key}={value} names {len(hits)} of the --loop-dir given; name one by its directory")
        if hits[0] in out:
            raise SystemExit(f"video-cycle-align: {flag} names {loops[hits[0]][0]} twice")
        out[hits[0]] = value
    return out


def _resolve_cycles(cycles: dict[str, int] | None, loops: list[tuple[Path, Path, dict[str, Any], float]]) -> dict[int, int]:
    """`--cycles` keys to loop positions (`_resolve_loops`)."""
    return _resolve_loops("--cycles", cycles, loops, CYCLES)


def _loop_files(loop_dir: Path) -> tuple[Path, dict[str, Any]]:
    metas = sorted(loop_dir.glob("*.strip.json"))
    if len(metas) != 1:
        raise ValueError(f"{loop_dir}: expected one <name>.strip.json from video-loop, found {len(metas)}")
    meta = json.loads(metas[0].read_text(encoding="utf-8"))
    for key in ("cycle_frames", "cycle_seconds", "cell_height_cap", "kind"):
        if key not in meta:
            raise ValueError(f"{metas[0]}: no `{key}` — cut the loop again with this sprite-gen (video-loop) before aligning it")
    return metas[0], meta


def _source_frames(loop_dir: Path) -> list[Image.Image]:
    """The cut as filmed: `cycle.source/` once an alignment has run, `cycle/` before (kept there first)."""
    source = loop_dir / SOURCE_DIR
    if not source.is_dir():
        cycle = loop_dir / "cycle"
        if not any(cycle.glob("frame-*.png")):
            raise ValueError(f"{loop_dir}: no cycle/frame-*.png")
        staging = loop_dir / f".{SOURCE_DIR}.tmp"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(cycle, staging)
        staging.rename(source)
    files = sorted(source.glob("frame-*.png"))
    if not files:
        raise ValueError(f"{source}: no frame-*.png")
    return [Image.open(f).convert("RGBA") for f in files]


def origin_of(loop_dir: Path, meta_path: Path, meta: dict[str, Any]) -> dict[str, Any] | None:
    """The strip as cut that this loop is aligned from (`follow.cut_origin`), read before anything is rewritten: on a
    loop never aligned its strip now (`follow.source.png` where a follow-through moved it); on a loop aligned before,
    the origin its first alignment recorded, kept as it is — None where that alignment recorded none (the cut is no
    longer there to read)."""
    if "cycle_align" in meta:
        return meta["cycle_align"].get("origin")
    source = loop_dir / loop_mod.FOLLOW_SOURCE
    strip = source if source.exists() else loop_dir / f"{meta_path.name[: -len('.strip.json')]}.strip.png"
    return follow_mod.cut_origin(strip.read_bytes(), meta)


def _rebuild(loop_dir: Path, meta_path: Path, meta: dict[str, Any], frames: list[Image.Image], fps: float,
             record: dict[str, Any], *, cells_from: list[dict[str, Any]] | None = None,
             origin: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the aligned cycle, strip, GIF and WebP over the loop's own, at the loop's own cell size rules. `cells_from`
    says where each of `frames` is from; the record keeps it per cell of the strip, with the cut's `origin`."""
    name = meta_path.name[: -len(".strip.json")]
    cycle_dir = loop_dir / "cycle"
    cycle_dir.mkdir(exist_ok=True)
    for old in cycle_dir.glob("frame-*.png"):
        old.unlink()
    for k, im in enumerate(frames):
        im.save(cycle_dir / f"frame-{k:03d}.png")
    cycle_seconds = len(frames) / fps
    standing = meta.get("body_src_h") if meta.get("body_ref") in ("first-frame", "cut-first-frame") else None
    strip, strip_meta = loop_mod.build_strip(
        frames, max_height=int(meta["cell_height_cap"]), cycle_seconds=cycle_seconds, body_height=meta.get("body_height_target"),
        anchor="feet" if meta.get("foot_anchor") == "feet" else "none", kind=str(meta["kind"]), standing_src=standing,
        standing_frame=int(meta.get("body_ref_frame") or 0))
    # what build_strip does not own (how the cut was anchored) is carried over as it was; a
    # follow-through moved the old cycle's cells, so it is cleared and the record says so
    if "follow" in meta or (loop_dir / loop_mod.FOLLOW_SOURCE).exists():
        record["follow_cleared"] = True
    (loop_dir / loop_mod.FOLLOW_SOURCE).unlink(missing_ok=True)
    # a loop one cycle of which was taken out (`--cycles` k > 1) no longer holds the steps it was counted with
    merged = {**{k: v for k, v in meta.items() if k not in strip_meta and k not in ("cycle_align", "follow")
                 and not (k == "steps" and "cycle_taken" in record)}, **strip_meta}
    if meta.get("foot_anchor") and meta.get("foot_anchor") != "feet":
        merged["foot_anchor"] = meta["foot_anchor"]
    cells = [strip.crop((k * strip_meta["w"], 0, (k + 1) * strip_meta["w"], strip_meta["h"])) for k in range(strip_meta["frames"])]
    flat = np.stack([loop_mod._small_features(c) for c in cells])
    adjacent = float(np.abs(flat[1:] - flat[:-1]).mean())
    seam = float(np.abs(flat[-1] - flat[0]).mean())
    record["seam_ratio"] = round(seam / adjacent, 4) if adjacent > 0 else None
    if origin is not None:
        record["origin"] = origin
    if cells_from is not None:  # per cell of the strip, which may hold fewer of the frames than the cycle (`sample_indices`)
        record["cells_from"] = [cells_from[i] for i in strip_meta["sample_indices"]]
    merged["cycle_align"] = record
    strip.save(loop_dir / f"{name}.strip.png")
    atomic_write_text(meta_path, json.dumps(merged, indent=2) + "\n")
    delay_ms = max(20, round(1000 * cycle_seconds / len(cells)))
    gif_path, webp_path = loop_dir / f"{name}.gif", loop_dir / f"{name}.webp"
    save_clean_gif(cells, gif_path, duration_ms=delay_ms, loop=0, alpha_threshold=128)
    loop_mod.write_webp(cells, webp_path, delay_ms=delay_ms, workdir=loop_dir / ".webp-frames")
    shutil.rmtree(loop_dir / ".webp-frames", ignore_errors=True)
    # a GIF or WebP holds a frame shown twice in a row (`--between nearest` stretching a loop) as one
    # frame of twice the delay, so each is checked for one frame per run of identical cells
    runs = 1 + sum(1 for a, b in zip(cells, cells[1:]) if a.tobytes() != b.tobytes())
    record["gif"] = loop_mod.verify_animation(gif_path, expect_frames=runs, check_stale=False)
    record["webp"] = loop_mod.verify_animation(webp_path, expect_frames=runs, check_stale=True)
    return merged


def _unnamed_foot(row: dict[str, Any]) -> dict[str, Any]:
    """A loop whose foot nobody named, for whoever looks: its two strikes as frames of the rebuilt
    `cycle/` (the first is the frame it starts on), why the engine could not say, and the `--foot`
    arguments that settle it — which own foot lands on the first."""
    cycle = Path(row["dir"]) / "cycle"
    return {"dir": row["dir"], "name": row["name"], "view": row["view"], "foot_why": row["foot_why"],
            "candidates": [{"strike": n, "frame": k, "path": str(cycle / f"frame-{k:03d}.png")} for n, k in enumerate(row["strikes"])],
            "settle": [f"--foot {row['dir']}={s}" for s in handed_mod.SIDES]}


def _fault_line(name: str, m: dict[str, Any]) -> str:
    """A made frame's faults, in words, and what became of it."""
    what = [*([f"has {100 * m['dark_excess']:.2f} % more dark pixels inside the body than either source frame beside it — a smear"]
              if "smear" in m["faults"] else []),
            *([f"lost its outline on {100 * m['outline_loss']:.2f} % of its edge beyond either source frame beside it — "
               "a melted or ghost limb"] if "outline" in m["faults"] else [])]
    if m["method"] == "nearest":
        return f"{name}: frame {m['at']}: RIFE's frame " + "; ".join(what) + ", so the nearer source frame was taken there (--between auto)"
    return (f"{name}: frame {m['at']} (made by RIFE) " + "; ".join(what)
            + "; see it in cycle/, or align with --between auto (the nearer source frame there) or --between nearest")


def align_set(loop_dirs: list[Path], *, length: int | None = None, interpolate: rife_mod.Interpolate | None = None,
              report_path: Path | None = None, between: str = DEFAULT_BETWEEN, views: list[str | None] | None = None,
              start_foot: str = START_FOOT, multi_cycle: str = "fail", cycles: dict[str, int] | None = None,
              state: str | None = None, feet: dict[str, str] | None = None) -> dict[str, Any]:
    """Resample every loop of a set to one length (default: the median), turned to a foot strike —
    where `views` (one per loop: `front`, `back`, `side@right`, `front_diagonal@left`, …) lets it,
    the strike of the same own foot (`start_foot`) in every loop.

    Each loop is screened for two or three cycles first (`cycle_screen`; `state` is the set's motion
    state, for loops whose strip metadata does not carry one). `cycles` maps a loop (`_resolve_cycles`)
    to the number of cycles it holds, as someone who looked counted them: 1 aligns it as it is, more
    takes one cycle out of it (`take_cycle`). A suspect with no count stops the set before anything
    is rewritten (`multi_cycle` fail: `CycleSuspects`, the report written with `applied` false) or is
    aligned as it is with a warning (warn).

    `feet` maps a loop (named as `cycles` does) to the own foot that lands on its larger strike, as
    whoever looked saw it (`--foot`): that loop starts where `start_foot` lands, recorded
    `start_foot_source` "given" (`foot_strike`). A loop whose foot nobody named is listed under the
    report's `unnamed_feet`, with its two strike frames in the rebuilt `cycle/` (`candidates`) and the
    `--foot` arguments that settle it."""
    if len(loop_dirs) < 1:
        raise SystemExit("video-cycle-align: at least one --loop-dir")
    if views is not None and len(views) != len(loop_dirs):
        raise SystemExit(f"video-cycle-align: {len(views)} --view for {len(loop_dirs)} --loop-dir; give one per loop, in the same order")
    if start_foot not in handed_mod.SIDES:
        raise SystemExit(f"video-cycle-align: --start-foot must be left or right, not {start_foot!r}")
    if multi_cycle not in MULTI_CYCLE:
        raise SystemExit(f"video-cycle-align: --multi-cycle must be one of {', '.join(MULTI_CYCLE)}, not {multi_cycle!r}")
    for v in views or []:
        if v is not None:
            name, _, facing = v.partition("@")
            handed_mod.validate_view(name, facing or None)
    loops = []
    for d in loop_dirs:
        d = d.expanduser().resolve()
        try:
            meta_path, meta = _loop_files(d)
        except ValueError as exc:
            raise SystemExit(f"video-cycle-align: {exc}") from exc
        loops.append((d, meta_path, meta, meta["cycle_frames"] / meta["cycle_seconds"]))
    # cycle_seconds is written to four decimals, so a rate read back from it carries that rounding
    rates = [fps for *_, fps in loops]
    if max(rates) / min(rates) - 1 > RATE_TOLERANCE:
        raise SystemExit(f"video-cycle-align: the loops play at different frame rates ({sorted(round(r, 3) for r in rates)}); "
                         "one length in frames means nothing across them")
    fps = round(statistics.median(rates), 3)
    sources = []
    for d, *_ in loops:
        try:
            sources.append(_source_frames(d))
        except ValueError as exc:
            raise SystemExit(f"video-cycle-align: {exc}") from exc
    given = _resolve_cycles(cycles, loops)
    told = _resolve_loops("--foot", feet, loops, handed_mod.SIDES)
    screens = [cycle_screen(frames, fps=fps, state=state or meta.get("state")) for (_, _, meta, _), frames in zip(loops, sources)]
    suspects: list[dict[str, Any]] = []
    taken: dict[int, dict[str, Any]] = {}
    cycle_warnings: list[str] = []
    for i, ((d, meta_path, meta, _), screen) in enumerate(zip(loops, screens)):
        k = given.get(i)
        # A loop counted two steps long (`steps`) returns half way at its second step: not a second cycle.
        stepped = [row for row in screen["suspects"] if meta.get("steps") == 2 and row["cycles"] == 2]
        rows = [row for row in screen["suspects"] if row not in stepped]
        step = one_step(d, meta_path, meta)
        if rows or step or stepped:
            status = ("counted" if k is not None else "steps" if not rows and not step
                      else "stopped" if multi_cycle == "fail" else "warned")
            suspects.append({"dir": str(d), "name": meta_path.name[: -len(".strip.json")], "length": len(sources[i]),
                             "status": status, "cycles_given": k,
                             "candidates": [{key: row[key] for key in ("period", "seconds", "divisor", "cycles", "periodicity", "pose",
                                                                       "off_path", "distance", "steps", "follow", "why") if key in row}
                                            for row in (rows or stepped)],
                             **({"one_step": step} if step else {}),
                             **({"steps": meta["steps"]} if stepped else {}),
                             "settle": [f"--cycles {d}={c}" for c in sorted({1, *(row["cycles"] for row in rows)})]})
        if k is not None and k > 1:
            if not any(row["cycles"] == k for row in screen["suspects"]):
                cycle_warnings.append(f"{d}: --cycles {k} given, but the screen found no return at 1/{k} of it; "
                                      f"one cycle is taken out of it as asked")
            sources[i], taken[i] = take_cycle(sources[i], k)
    stopped = [e for e in suspects if e["status"] == "stopped"]
    if stopped:
        # The command that settles it, as given plus a count for each stopped loop: K is 1 where it
        # holds one cycle, or the number it holds (each loop's `settle` lists the choices).
        rerun = ["sprite-gen video-cycle-align", *(f"--loop-dir {d}" for d, *_ in loops),
                 *(f"--view {v}" for v in (views or []) if v is not None), *([f"--length {length}"] if length is not None else []),
                 *([f"--between {between}"] if between != DEFAULT_BETWEEN else []), *([f"--start-foot {start_foot}"] if start_foot != START_FOOT else []),
                 *([f"--state {state}"] if state else []), *([f"--report {report_path}"] if report_path is not None else []),
                 *(f"--cycles {loops[i][0]}={k}" for i, k in sorted(given.items())),
                 *(f"--foot {loops[i][0]}={f}" for i, f in sorted(told.items())),
                 *(f"--cycles {e['dir']}=<{'|'.join(s.rpartition('=')[2] for s in e['settle'])}>" for e in stopped)]
        command = " ".join(rerun)
        message = ("video-cycle-align: " + "; ".join(_suspect_line(e) for e in stopped)
                   + ". Stopped before anything was rewritten: pixels cannot tell two cycles from one cycle whose steps look alike"
                   + (", nor one step from a cycle" if any("one_step" in e for e in stopped) else "")
                   + ". Count the cycles in each (look at it, or ask a vision model how often each foot lands), then run, with each count "
                   f"filled in (1: it holds one, aligned as it is; more: one cycle is taken out of it): {command}"
                   " — or add --multi-cycle warn to align the set as it is (docs/loop-repair.md section 4)")
        if report_path is not None:
            loop_mod.write_loop_report(report_path.expanduser().resolve(), {
                "kind": "sprite-gen-video-cycle-align-report", "applied": False, "refused": "cycle-suspects", "why": message, "command": command,
                "multi_cycle": multi_cycle, "lengths": [len(f) for f in sources], "fps": round(fps, 4), "suspects": suspects,
                "cycles_given": {str(loops[i][0]): k for i, k in given.items()}, "feet_given": {str(loops[i][0]): f for i, f in told.items()}})
        raise CycleSuspects(message, suspects, command)
    cycle_warnings += [f"{_suspect_line(e)}; aligned as it is (--multi-cycle warn) — at the set's length it plays "
                       + ("one step where the rest play two, if it is one" if "one_step" in e else
                          f"{max(row['cycles'] for row in e['candidates'])} times as fast as the rest if it does")
                       for e in suspects if e["status"] == "warned"]
    lengths = [len(f) for f in sources]
    target = length if length is not None else round(statistics.median(lengths))
    located: list[dict[str, str]] = []

    def lazy(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        nonlocal interpolate
        if interpolate is None:
            found = rife_mod.Rife()
            located.append(found.describe())
            interpolate = found
        return interpolate(a, b, t)

    # Every loop is resampled before any is rewritten: a loop that cannot be made leaves the set as it was. The cut
    # each is aligned from is read first, while its strip is still the cut's.
    origins = [origin_of(d, meta_path, meta) for d, meta_path, meta, _ in loops]
    aligned = []
    for i, ((d, *_), frames, view) in enumerate(zip(loops, sources, views or [None] * len(loops))):
        # which frame of cycle.source/ each of `frames` is: all of them, or the one cycle `--cycles` took out
        index = ([(taken[i]["start"] + k) % taken[i]["from"] for k in range(taken[i]["length"])] if i in taken
                 else list(range(len(frames))))
        provenance: list[dict[str, Any]] = []
        try:
            out, facts = resample(frames, target, lazy, between=between, cells_from=provenance)
            strike = foot_strike(out, view=view, foot=start_foot, given=told.get(i))
        except rife_mod.RifeNotInstalled as exc:
            raise rife_mod.RifeNotInstalled(f"{d}: {exc}") from exc
        except (ValueError, rife_mod.RifeUnavailable) as exc:
            raise SystemExit(f"video-cycle-align: {d}: {exc}; frames between source frames are made by RIFE (docs/loop-repair.md)") from exc
        start = strike["start"]
        drawings = held_drawings(frames, fps=fps, clip=loops[i][2].get("drawings"), cut=loops[i][2].get("cycle_drawings"))
        again = retake(drawings, facts, fps=fps)
        record = {**facts, "drawings": drawings, "retake": again, "turned_by": start, "turned_on": strike["by"], "view": view, "start_foot": strike["start_foot"],
                  "start_foot_source": strike["start_foot_source"],
                  # the two strikes as they stand in the rebuilt cycle/: the larger first
                  "strikes": [(k - start) % target for k in strike["strikes"]],
                  **{key: strike[key] for key in ("foot", "foot_why", "foot_given", "foot_unnamed_why", "foot_disagrees") if key in strike},
                  "stride_swing": strike["stride_swing"], "reach_swing": strike["reach_swing"],
                  "fps": round(fps, 4), "source": SOURCE_DIR, "cycles_given": given.get(i), "cycle_screen": screens[i],
                  **({"cycle_taken": taken[i]} if i in taken else {}),
                  "made_at": [(k - start) % target for k in facts["made_at"]]}
        if "nearest_at" in facts:
            record["nearest_at"] = sorted((k - start) % target for k in facts["nearest_at"])
        if "smear" in facts:
            record["smear"] = sorted(({**m, "at": (m["at"] - start) % target} for m in facts["smear"]), key=lambda m: m["at"])
        provenance = [{"source": index[p["source"]]} if "source" in p else {"between": [index[j] for j in p["between"]], "t": p["t"]}
                      for p in provenance]
        aligned.append((out[start:] + out[:start], record, provenance[start:] + provenance[:start]))
    rows = []
    for (d, meta_path, meta, _), (out, record, provenance), origin in zip(loops, aligned, origins):
        merged = _rebuild(d, meta_path, meta, out, fps, record, cells_from=provenance, origin=origin)
        # The report names where each cell is from; the cut's own record stays in the strip's meta.
        rows.append({"dir": str(d), "name": meta_path.name[: -len(".strip.json")],
                     **{k: v for k, v in record.items() if k not in ("gif", "webp", "origin")},
                     "strip": {k: merged[k] for k in ("frames", "w", "h", "body_h", "delay_ms")}})
    warnings = [_fault_line(r["name"], m) for r in rows for m in r.get("smear", []) if m["faults"]]
    retakes = [{"dir": r["dir"], "name": r["name"], **r["retake"]} for r in rows if r["retake"]]
    # A loop to film again is named by its directory where two loops share a strip name (each `walk`).
    shared = len({r["name"] for r in rows}) < len(rows)
    warnings = cycle_warnings + [_retake_line(r["dir"] if shared else r["name"], r["retake"]) for r in rows if r["retake"]] + warnings
    unnamed = [_unnamed_foot(r) for r in rows if r["start_foot"] is None]
    if len(rows) > 1 and views is None and unnamed:
        warnings.append("no view given (--view): each loop " + ("not named by --foot " if told else "")
                        + "starts on its larger strike, whichever foot that is (`unnamed_feet`)")
    warnings += [f"{r['dir'] if shared else r['name']}: starts on its larger strike, foot not named — {r['foot_why']}; look at "
                 f"cycle/frame-{r['strikes'][0]:03d}.png and say which own foot lands there: --foot {r['dir']}=left|right"
                 for r in rows if r["start_foot"] is None and r["view"] is not None]
    warnings += [f"{r['dir'] if shared else r['name']}: --foot says {r['foot_given'][0]} lands on its larger strike, its view's "
                 f"{r['foot']['cue']} said {r['foot_disagrees'][0]}; started as given" for r in rows if r.get("foot_disagrees")]
    report = {"kind": "sprite-gen-video-cycle-align-report", "applied": True, "length": target,
              "length_rule": "requested" if length is not None else "median",
              "between": between, "start_foot": start_foot, "retake": retakes, "warnings": warnings,
              "lengths": lengths, "multi_cycle": multi_cycle, "suspects": suspects, "unnamed_feet": unnamed,
              "cycles_given": {str(loops[i][0]): k for i, k in given.items()},
              "feet_given": {str(loops[i][0]): f for i, f in told.items()},
              "fps": round(fps, 4), "cycle_seconds": round(target / fps, 4),
              "interpolator": ({"kind": "rife-ncnn-vulkan", **located[0]} if located else
                               {"kind": "injected"} if any(r.get("smear") for r in rows) else None),
              "made_by_rife": sum(r["made_by_rife"] for r in rows),
              "replaced": sum(1 for r in rows for m in r.get("smear", []) if m["method"] == "nearest"), "loops": rows}
    if report_path is not None:
        loop_mod.write_loop_report(report_path.expanduser().resolve(), report)
    return report


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--loop-dir", action="append", type=Path, required=True, help="a video-loop output directory (repeat once per direction of the set)")
    parser.add_argument("--length", type=int, help="cycle length in frames for every loop (default: the median of the set's own lengths)")
    parser.add_argument("--report", type=Path, help="where to write the set's alignment report (JSON)")
    parser.add_argument("--view", action="append",
                        help="the view of each --loop-dir, in the same order: front, back, or side|front_diagonal|back_diagonal@right|left "
                             "(the way it faces); with it every loop starts as the same own foot lands (--start-foot)")
    parser.add_argument("--start-foot", choices=handed_mod.SIDES, default=START_FOOT, help="the own foot every loop starts on (default right; needs --view)")
    parser.add_argument("--cycles", action="append", default=[], metavar="LOOP=K",
                        help="how many cycles a loop holds, as counted by whoever looked at it (repeatable; LOOP is its --loop-dir, its "
                             "strip's name or its directory's name): 1 aligns it as it is; 2 or 3 takes one cycle out of it as filmed "
                             "(cycle.source/) and aligns that. Only this changes the number of cycles in a loop")
    parser.add_argument("--foot", action="append", default=[], metavar="LOOP=left|right",
                        help="which own foot lands on a loop's first strike candidate, as seen by whoever looked at it, for a loop the "
                             "report lists under `unnamed_feet` (repeatable; LOOP is named as for --cycles): that loop starts as "
                             "--start-foot lands, recorded start_foot_source given")
    parser.add_argument("--multi-cycle", choices=MULTI_CYCLE, default="fail",
                        help="a loop that may hold two or three cycles (a half or a third of it returns to a pose on the motion's path), "
                             "or may be one step (its cut's report: `cycle.step_screen`), and has no --cycles: fail (default) stops the "
                             "set before anything is rewritten, the report naming it under `suspects` (`one_step` for the latter: cut it "
                             "again with video-loop --steps 1 once its steps are counted); warn aligns it as it is and names it in the warnings")
    parser.add_argument("--state", help="the set's motion state (walk, run), for loops cut before video-loop wrote it in the strip metadata: "
                                        "its gait floor is the shortest cycle the screen looks for")
    parser.add_argument("--between", choices=BETWEEN, default=DEFAULT_BETWEEN,
                        help="a time between two source frames: auto (default) makes that frame with RIFE and keeps it unless it smeared "
                             "or lost its outline (a melted limb), where the nearer source frame is taken and named in the warnings; rife "
                             "keeps every made frame and warns on those; nearest takes the nearer source frame — nothing made, no RIFE "
                             "needed, the motion up to half a frame off its time. Every made frame is measured in the report (`smear`)")


def parse_cycles(values: list[str]) -> dict[str, int]:
    """`--cycles LOOP=K` values as {LOOP: K}."""
    out: dict[str, int] = {}
    for v in values:
        key, sep, k = v.rpartition("=")
        if not sep or not key or not k.strip().isdigit():
            raise SystemExit(f"video-cycle-align: --cycles expects LOOP=K (K one of {', '.join(map(str, CYCLES))}), got {v!r}")
        if key in out:
            raise SystemExit(f"video-cycle-align: --cycles names {key} twice")
        out[key] = int(k)
    return out


def parse_feet(values: list[str], *, prog: str = "video-cycle-align", flag: str = "--foot") -> dict[str, str]:
    """`--foot LOOP=left|right` values as {LOOP: foot}."""
    out: dict[str, str] = {}
    for v in values:
        key, sep, foot = v.rpartition("=")
        if not sep or not key or foot not in handed_mod.SIDES:
            raise SystemExit(f"{prog}: {flag} expects LOOP=left|right (the own foot that lands on the loop's first strike), got {v!r}")
        if key in out:
            raise SystemExit(f"{prog}: {flag} names {key} twice")
        out[key] = foot
    return out


def run(**kwargs: object) -> int:
    try:
        report = align_set(list(kwargs["loop_dir"]), length=kwargs.get("length"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
                           between=str(kwargs.get("between") or DEFAULT_BETWEEN), views=kwargs.get("view"),  # type: ignore[arg-type]
                           start_foot=str(kwargs.get("start_foot") or START_FOOT),
                           multi_cycle=str(kwargs.get("multi_cycle") or "fail"), cycles=parse_cycles(list(kwargs.get("cycles") or [])),  # type: ignore[arg-type]
                           state=kwargs.get("state"), feet=parse_feet(list(kwargs.get("foot") or [])))  # type: ignore[arg-type]
    except rife_mod.RifeNotInstalled as exc:
        # Asked for by name, so no RIFE is a failure, never a quiet skip (video-set skips with a warning).
        raise SystemExit(f"video-cycle-align: {exc}; frames between source frames are made by RIFE — "
                         f"`{rife_mod.INSTALL_COMMAND}` (docs/loop-repair.md)") from exc
    for line in report["warnings"]:
        print(f"video-cycle-align: warning: {line}", file=sys.stderr)
    print(json.dumps({k: report[k] for k in ("length", "lengths", "cycles_given", "feet_given", "between", "made_by_rife", "replaced", "retake",
                                             "unnamed_feet", "cycle_seconds")}
                     | {"loops": [{k: r[k] for k in ("name", "from", "to", "made_by_rife", "turned_by", "turned_on", "start_foot", "start_foot_source",
                                                     "seam_ratio")} for r in report["loops"]]},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-cycle-align", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
