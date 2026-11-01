# SPDX-License-Identifier: Apache-2.0
"""Held cells: neighbour cells of a cut that show the same picture, found, made again as the frames between the poses
beside them, and read again (docs/loop-repair.md section 7).

A clip may show a frame twice or three times in a row: a video model that stalls, a rate carried to another by
repeating frames (20 fps in 24 repeats one in six), a cut mapped to a higher rate by taking the nearer frame. Played,
the motion stops on that picture and jumps on: one pose, two or three cells. Each cut `video-loop` makes is read for
them (`same_steps`, `reading`): the cells, the poses (the cells less the ones that show their neighbour's picture
again) and each run of cells showing one picture (`held_runs`, its first cell and how many). The cells are read as
filmed, before an anchor moves each frame of a pair apart.

The same picture is a step at most CHANGED_MAX of whose subject's pixels (premultiplied) change by more than
PIXEL_SAME, read twice: on the analysis thumbnail `video-loop` cuts by and on the frames as cut. A textured figure shown
three times, encoded by libx264 at crf 18, 23 and 28 and keyed by `video-frames`, changes no thumbnail pixel of a frame
shown again by more than 0.008, 0.020 and 0.051, and 0.012 %, 0.049 % and 0.11 % of its frame's subject pixels by more
than PIXEL_SAME. An arm 8 px thick on a 120 x 140 figure grown by one pixel changes 1.2 % of the thumbnail's subject
pixels (0.38 % of the frame's); a walker going away in a frame 280 px wide changes 4.9 % of the frame's in a step that
moves none of the thumbnail's. A distance over the subject's mass (`nonloop.STABLE_MAX`, which reads a once cut's
settled end) does not part them: the arm's pixel reads under it. A small part's move of a fraction of a pixel reads as
the same picture, as a hold plays.

`fill` makes each run's cells again, between the run's first cell and the next pose, at their own times — the cells of a
run of three a third and two thirds of the way — so the cut keeps its length, its beat and its first cell: a made cell
stands where a held cell stood. A loop is a ring (`wrap`): the run that closes it is made between its last pose and the
loop's first cell, and last cells that show the first cell's picture again (`wrap_same`, a cut a cell too long) are
made between the cell before them and the first, which stays. A run is kept as filmed where filling it would change
what the cut means (`KEEPS`, each kept run's `keep`, listed by `intended` as the strip's `holds`): longer than HOLD_MAX
cells, joined across a loop's wrap (a drawing held on threes is three: longer is a hold the motion means), the opening
or the closing pose of a cut played once. `again` reads the cut once more after its repairs and fills what they left
shown again, the same way. A cut drawn on
twos or threes keeps every run (`drawn`): limited animation, each drawing shown as long as the next. It is drawn so where
its held-drawings reading (`held.measure_cycle`, which `video-cycle-align` reads) gives a hold of 2 or 3 and every run is
that long, but a run cut short at the cut's first or last cell: a clip carried to a higher rate by repeating frames
reads a hold too (24 fps carried to 60 shows each frame two or three times), its runs two and three cells by turns, and
a clip that stalls now and then reads none. A frame made
is judged as the frames the alignment makes are (`align.measure_made`, `faults`), for a part standing still on both
sides of it (`still_kept`: a foot planted, a hand on a ledge), which must stand where it stood, and for a picture of its
own (`same_picture`, `held`): one that shows the picture of either frame it is made between, or of the cell before it,
is no frame between but a held cell again. A frame refused is made again at another fraction of RETIME_T within half a
cell of its own time, else the run is kept as filmed and says why. One refused as `held` is made again only on the side
away from the picture it showed, and not again where it showed both: the frames between run from one pose to the next,
so one nearer that picture shows it too. A run is made whole or kept whole: half made it would step back.

`upsample` gives a cut at a rate above its own (`--out-fps`) its frames between, at their own times, judged as the
fill's are, never a frame shown again; a cell where both frames beside its time show one picture (a hold kept) is that
picture, and one no frame made is taken for is the nearer frame, named. `at_times` makes a cut's cells at any times
so: `upsample`'s, and the air a single play's air retime gives a new length (`root_motion.retime`,
docs/video-pipeline.md "Root motion")."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

from PIL import Image, ImageFilter

from sprite_gen._deps import np
from sprite_gen.video import held as held_mod
from sprite_gen.video import legs as legs_mod
from sprite_gen.video import rife as rife_mod
from sprite_gen.video.interpolation_quality import faults, ghost_screen, looks

MODES = ("auto", "off")
PIXEL_SAME = 0.1  # a thumbnail pixel (premultiplied, 0..1) that changes by more than this has changed
CHANGED_MAX = 0.005  # the same picture: at most this share of the subject's thumbnail pixels changed
HOLD_MAX = max(held_mod.HOLDS)  # a run of cells longer than a drawing on threes is a hold the motion means: kept
# A part standing still on both sides of a frame made (`still_kept`): its outline pixels, solid (legs.LOW_ALPHA) and
# within STILL_RGB of each other in both frames, must be solid and within STILL_RGB in the frame made, within a pixel,
# for at least STILL_KEPT_MIN of them in every window of rife.GHOST_BAND of the body high holding STILL_MIN_PX or more.
STILL_RGB = 24
STILL_KEPT_MIN = 0.8
STILL_MIN_PX = 12
SNAP = 0.03  # a time within this of a frame takes that frame (align.SNAP)


def _thumbs(frames: Sequence[Image.Image]) -> np.ndarray:
    from sprite_gen.video.loop import _small_features  # the thumbnail video-loop cuts by (loop imports this module)

    return np.stack([_small_features(f).reshape(-1, 4) for f in frames])


def premultiplied(frame: Image.Image) -> np.ndarray:
    a = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    return np.concatenate([a[..., :3] * a[..., 3:], a[..., 3:]], axis=-1)


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether at most CHANGED_MAX of the subject's pixels (alpha over half in either) change by more than PIXEL_SAME:
    premultiplied RGBA, 0..1, pixels along the last axis."""
    subject = int((np.maximum(a[..., 3], b[..., 3]) > 0.5).sum())
    changed = int((np.abs(a - b).max(axis=-1) > PIXEL_SAME).sum())
    return changed <= CHANGED_MAX * subject


def same_steps(frames: Sequence[Image.Image]) -> list[bool]:
    """For each step of `frames` (cell k to k + 1), whether the two show the same picture: on the thumbnail and on the
    frames as cut (see the module)."""
    t = _thumbs(frames)
    return [_same(t[k], t[k + 1]) and _same(premultiplied(frames[k]), premultiplied(frames[k + 1]))
            for k in range(len(frames) - 1)]


def same_picture(frame: Image.Image, others: Sequence[Image.Image]) -> bool:
    """Whether `frame` shows the same picture as one of `others`: each pair read as `same_steps` reads a step."""
    return any(same_steps([other, frame])[0] for other in others)


def runs(same: Sequence[bool]) -> list[dict[str, int]]:
    """Each run of cells showing one picture: its first cell and how many (two or more). Not read across a wrap."""
    found, k = [], 0
    while k < len(same):
        if not same[k]:
            k += 1
            continue
        start = k
        while k < len(same) and same[k]:
            k += 1
        found.append({"start": start, "length": k - start + 1})
    return found


def reading(same: Sequence[bool], *, wrap: bool = False) -> dict[str, Any]:
    """The cells, the poses and the held runs of a cut whose steps are `same`. `wrap`: a loop whose last cell shows its
    first cell's picture again, one pose less, said `wrap_held` (the runs are not read across the wrap)."""
    held = runs(same)
    cells = len(same) + 1
    poses = cells - sum(r["length"] - 1 for r in held)
    return {"cells": cells, "poses": poses - 1 if wrap and poses > 1 else poses, "held_runs": held,
            **({"wrap_held": True} if wrap and poses > 1 else {})}


def wrap_same(frames: Sequence[Image.Image]) -> bool:
    """Whether a loop's last cell shows its first cell's picture again: the step across its wrap, read as `same_steps`
    reads a step."""
    return len(frames) > 1 and same_steps([frames[-1], frames[0]])[0]


def said(record: dict[str, Any]) -> str:
    """The poses over the cells, as a person is told them: `poses/cells`."""
    return f"{record['poses']}/{record['cells']}"


def _edge(solid: np.ndarray) -> np.ndarray:
    inner = np.asarray(Image.fromarray(np.uint8(solid) * 255).filter(ImageFilter.MinFilter(3))) > 0
    return solid & ~inner


def still_kept(frame: Image.Image, a: Image.Image, b: Image.Image) -> dict[str, Any]:
    """Whether a part standing still in `a` and `b` stands where it stood in `frame`, made between them: the outline
    pixels both draw in one place in one colour (`still`), and the share of them `frame` draws there too, within a
    pixel (`kept`), the least over windows of rife.GHOST_BAND of the body high that hold STILL_MIN_PX of them (`reads`
    false where none does)."""
    A, B, F = (np.asarray(im.convert("RGBA"), dtype=np.int16) for im in (a, b, frame))
    sa, sb, sf = (x[..., 3] >= legs_mod.LOW_ALPHA for x in (A, B, F))
    still = _edge(sa) & _edge(sb) & (np.abs(A[..., :3] - B[..., :3]).max(axis=-1) <= STILL_RGB)
    drawn = sf & (np.abs(F[..., :3] - A[..., :3]).max(axis=-1) <= STILL_RGB)
    near = np.asarray(Image.fromarray(np.uint8(drawn) * 255).filter(ImageFilter.MaxFilter(3))) > 0
    rows = np.nonzero((sa | sb).any(axis=1))[0]
    if not rows.size:
        return {"reads": False, "still_px": 0}
    top, bottom = int(rows[0]), int(rows[-1])
    win = max(8, round((bottom - top + 1) * rife_mod.GHOST_BAND))
    still_rows, kept_rows = still.sum(axis=1), (still & near).sum(axis=1)
    least, at = None, None
    for y in range(top, max(top, bottom - win + 1) + 1, max(1, win // 4)):
        n = int(still_rows[y:y + win].sum())
        if n >= STILL_MIN_PX:
            share = float(kept_rows[y:y + win].sum()) / n
            if least is None or share < least:
                least, at = share, y
    if least is None:
        return {"reads": False, "still_px": int(still.sum())}
    return {"reads": True, "still_px": int(still.sum()), "kept": round(least, 4), "at_row": at, "window_px": win}


def judge(frame: Image.Image, a: Image.Image, b: Image.Image, t: float, *, reads: bool,
          before: Image.Image | None = None) -> tuple[dict[str, Any], list[str]]:
    """A frame made between `a` and `b` at `t`: the alignment's measure and faults (`align.measure_made`, `faults`),
    `contact` where a part standing still on both sides does not stand where it stood (`still_kept`), and `held` where
    it shows the same picture as `a`, `b` or the cell delivered `before` it (`same_picture`; which of them, `shows`): no
    frame between, but a held cell again."""
    from sprite_gen.video.align import measure_made  # align imports loop, which imports this module

    measure = measure_made(frame, a, b, t, reads=reads)
    contact = still_kept(frame, a, b)
    shows = [name for name, other in (("a", a), ("b", b), ("before", before))
             if other is not None and same_picture(frame, (other,))]
    wrong = (faults(measure) + (["contact"] if contact["reads"] and contact["kept"] < STILL_KEPT_MIN else [])
             + (["held"] if shows else []))
    return {**measure, "contact": contact, "shows": shows}, wrong


def _made(interpolate: rife_mod.Interpolate, a: Image.Image, b: Image.Image, t: float, window: tuple[float, float], *,
          reads: bool, before: Image.Image | None = None) -> tuple[Image.Image | None, dict[str, Any]]:
    """The frame between `a` and `b` at `t`, or at the fraction of RETIME_T nearest it inside `window` that is made
    with no fault (`judge`, `before` the cell delivered before it, None where that is `a`) and, made only off its time,
    with its ghost band under RETIME_WINDOW_MAX in every window (align's bar for a frame made again at another
    fraction); None and what each try was refused for, where none is. The frames between run from `a` to `b`, so one
    that shows `a` or the cell before it again (`held`, `shows`) is made again only later, one that shows `b` only
    earlier, and one that shows both not again: a fraction on the side of a picture it showed would show it too."""
    from sprite_gen.video.align import RETIME_T, RETIME_WINDOW_MAX

    lo, hi = window
    tries = []
    for at in [t] + sorted((r for r in RETIME_T if window[0] < r < window[1] and abs(r - t) > 1e-3), key=lambda r: (abs(r - t), r)):
        if not lo < at < hi:
            continue
        frame = interpolate(a, b, at)
        measure, wrong = judge(frame, a, b, at, reads=reads, before=before)
        seen = at != t and (measure.get("ghost_window") or 0.0) >= RETIME_WINDOW_MAX
        tries.append({"t": round(at, 4), "faults": wrong, **({"shows": measure["shows"]} if measure["shows"] else {}),
                      **({"window_over": RETIME_WINDOW_MAX} if seen and not wrong else {})})
        if not wrong and not seen:
            return frame, {"t": round(at, 4), **({"from_t": round(t, 4)} if at != t else {}),
                           "contact": measure["contact"], "look": looks(measure), "tries": tries}
        if "b" in measure["shows"]:
            hi = at
        if "a" in measure["shows"] or "before" in measure["shows"]:
            lo = at
    return None, {"t": round(t, 4), "tries": tries}


def drawn(found: Sequence[dict[str, int]], n: int, hold: int | None) -> int | None:
    """The hold of a cut of `n` cells drawn on twos or threes, or None: the held-drawings reading's `hold` 2 or 3, and
    every held run `found` that many cells long, but one cut short at the cut's first or last cell (see the module)."""
    if hold not in held_mod.HOLDS or not found:
        return None
    edge = lambda r: r["start"] == 0 or r["start"] + r["length"] == n  # noqa: E731
    return hold if all(r["length"] == hold or (r["length"] < hold and edge(r)) for r in found) else None


def _drawn_why(on: int) -> str:
    return (f"the cut is drawn on {'twos' if on == 2 else 'threes'} (its held-drawings reading holds {on}, and so does "
            "every run): limited animation, kept as filmed")


KEEPS = ("motion-hold", "drawn", "opening", "closing")  # why a run is kept as the motion means it (`intended`)


def _fillable(run: dict[str, int], n: int, *, ring: bool, on: int | None = None,
              length: int | None = None) -> tuple[str, str] | None:
    """Why a held run is kept as filmed and what kind of hold it is (one of KEEPS), or None where it is made again. `on`
    is the hold of a cut drawn on twos or threes (`drawn`); `length` the cells showing its picture where a loop's wrap
    joins it to the run across it. A loop's last cells are made again too, between their first and the loop's first
    cell across the wrap: a loop has no closing pose."""
    if on is not None:
        return _drawn_why(on), "drawn"
    held = run["length"] if length is None else length
    if held > HOLD_MAX:
        across = "" if length is None else " across the loop's wrap"
        return (f"held {held} cells{across}, longer than a drawing on threes ({HOLD_MAX}): a hold the motion means, "
                "kept as filmed"), "motion-hold"
    if run["start"] + run["length"] == n and not ring:
        return "the cut's closing pose, kept as filmed", "closing"
    if run["start"] == 0 and not ring:
        return "the cut's opening pose, kept as filmed", "opening"
    return None


def _make_run(frames: Sequence[Image.Image], interpolate: rife_mod.Interpolate, a: int, b: int, cells: Sequence[int], *,
              reads: bool) -> tuple[list[tuple[int, Image.Image]], list[dict[str, Any]]]:
    """Each of `cells` made between frame `a` and frame `b` (a loop's `b` n is its first cell across the wrap) at its own
    time, within half a cell of it (`_made`), the cell delivered before each the one made before it: the frames taken,
    up to the first refused, and how each was tried."""
    n = len(frames)
    taken: list[tuple[int, Image.Image]] = []
    tried: list[dict[str, Any]] = []
    for c in cells:
        t = (c - a) / (b - a)
        half = 0.5 / (b - a)
        frame, how = _made(interpolate, frames[a], frames[b % n], t, (t - half, t + half), reads=reads,
                           before=taken[-1][1] if taken else None)
        tried.append({"cell": c, "between": [a, b % n], **how})
        if frame is None:
            break
        taken.append((c, frame))
    return taken, tried


def fill(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, *, ring: bool,
         same: Sequence[bool] | None = None, mode: str = "auto", reads: bool | None = None,
         hold: int | None = None, wrap: bool | None = None, drawn_on: int | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """`frames` with each held run's cells made again between the run's first cell and the next pose (see the module),
    and the record: the reading before (`before`, each run `filled` or why not, and a loop's wrap, `wrap`), the cells
    made (`made`, and how each was, `frames`), the cells left for want of an interpolator (`unmade`, `interpolate`
    None), the hold of a cut drawn on twos or threes (`drawn`), and the reading of the frames returned (`cells`,
    `poses`, `held_runs`, `wrap_held`). `same` is the steps' reading as filmed (`same_steps` of `frames` where None);
    `wrap`, for a loop, whether its last cell shows its first cell's picture as filmed (`wrap_same` of `frames` where
    None); `reads` whether the ghost screen reads the cut (read on `frames` where None); `hold` the cut's held-drawings
    reading (`held.measure_cycle`); `drawn_on` the hold of a cut known drawn on twos or threes where its own runs do
    not read so (a loop resampled to another length from one drawn so, every run of it kept).

    A loop whose last cells show its first cell's picture again (`wrap`) keeps its first cell: the cells before the wrap
    that show it are made again between the pose before them and the first cell, as one run with the cells after it that
    show it too (a run starting the loop, made as any other) — kept whole where that run is longer than HOLD_MAX."""
    if mode not in MODES:
        raise ValueError(f"fill mode {mode!r}: one of {', '.join(MODES)}")
    same = list(same_steps(frames) if same is None else same)
    n = len(frames)
    found = runs(same)
    on = drawn(found, n, hold) if drawn_on is None else drawn_on
    whole = len(found) == 1 and found[0]["length"] == n  # one picture throughout: no pose to make anything between
    wrapped = ring and not whole and (wrap_same(frames) if wrap is None else bool(wrap))
    out = list(frames)
    before = []
    made: list[int] = []
    unmade: list[int] = []
    rows: list[dict[str, Any]] = []
    tail: dict[str, Any] | None = None
    joined_keep: tuple[str, str] | None = None
    if wrapped:
        last = found[-1] if found and found[-1]["start"] + found[-1]["length"] == n else None
        first = found[0] if found and found[0]["start"] == 0 else None
        s = last["start"] if last is not None else n - 1
        joined = (n - s) + (first["length"] if first is not None else 1)
        tail = {"start": s, "length": n - s, "joined": joined}
        joined_keep = None if mode == "off" else _fillable(tail, n, ring=True, on=on, length=joined)

    def attempt(row: dict[str, Any], a: int, b: int, cells: list[int], keep: tuple[str, str] | None) -> dict[str, Any]:
        nonlocal reads, unmade, made, rows
        if mode == "off":
            return {**row, "filled": False, "why": "--fill-held off"}
        if keep is not None:
            return {**row, "filled": False, "why": keep[0], "keep": keep[1]}
        if interpolate is None:
            unmade += cells
            return {**row, "filled": False, "why": "no interpolator"}
        if reads is None:
            reads = bool(ghost_screen(frames)["reads"])
        taken, tried = _make_run(frames, interpolate, a, b, cells, reads=reads)
        if len(taken) < len(cells):
            refused = sorted({f for row_ in tried for t_ in row_["tries"] for f in t_["faults"]})
            return {**row, "filled": False, "faults": refused, "tries": tried,
                    "why": f"a frame between was refused ({', '.join(refused) or 'ghost band over the retime bar'}): kept as filmed"}
        for c, frame in taken:
            out[c] = frame
        made += cells
        rows += tried
        return {**row, "filled": True, "made": cells}

    wrap_row: dict[str, Any] | None = None
    if tail is not None:
        # The cells before the wrap showing the first cell's picture, made between the pose before them and the first cell.
        s = tail["start"]
        wrap_row = attempt(dict(tail), s - 1, n, list(range(s, n)), joined_keep)
    for run in found:
        row: dict[str, Any] = dict(run)
        if tail is not None and run["start"] + run["length"] == n:
            before.append({**row, "wrap": True, **{k: v for k, v in wrap_row.items() if k not in ("start", "length", "joined")}})
            continue
        cells = list(range(run["start"] + 1, run["start"] + run["length"]))
        if tail is not None and run["start"] == 0 and joined_keep is not None and joined_keep[1] == "motion-hold":
            # one hold with the cells before the wrap, longer than a drawing on threes: kept whole with them
            before.append(attempt(row, 0, run["length"], cells, joined_keep))
            continue
        before.append(attempt(row, run["start"], run["start"] + run["length"], cells, _fillable(run, n, ring=ring, on=on)))
    record: dict[str, Any] = {
        "mode": mode, "pixel_same": PIXEL_SAME, "changed_max": CHANGED_MAX, "hold_max": HOLD_MAX,
        "before": {**reading(same, wrap=wrapped), "held_runs": before, **({"wrap": wrap_row} if wrap_row is not None else {})},
        "made": sorted(made), "frames": sorted(rows, key=lambda r: r["cell"]),
    }
    record["contact"] = [{"cell": r["cell"], **r["contact"]} for r in record["frames"] if r["contact"]["reads"]]
    if on is not None:
        record["drawn"] = on
    if unmade:
        record["unmade"] = {"cells": sorted(unmade)}
    record.update(reading(same_steps(out), wrap=ring and wrap_same(out)) if made else reading(same, wrap=wrapped))
    return out, record


def intended(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The runs the fill kept as the motion means them (KEEPS: longer than HOLD_MAX, drawn on twos or threes, the opening
    or closing pose of a cut played once), each as its cells, both ends in (`from`, `to`), its `kind` and `reason`: the
    holds a reader of the cut should not take for a picture shown again by mistake."""
    before = record["before"]
    rows = [r for r in before["held_runs"] if r.get("keep")]
    found = [{"from": r["start"], "to": r["start"] + r["length"] - 1, "kind": r["keep"], "reason": r["why"]} for r in rows
             if not r.get("wrap")]
    wrap = before.get("wrap")
    if wrap and wrap.get("keep"):
        found.append({"from": wrap["start"], "to": wrap["start"] + wrap["length"] - 1, "kind": wrap["keep"], "reason": wrap["why"]})
    return sorted(found, key=lambda h: h["from"])


def again(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, record: dict[str, Any], *, ring: bool,
          mode: str = "auto", skip: Sequence[int] = (),
          hold: Callable[[list[Image.Image]], int | None] | None = None) -> tuple[list[Image.Image], dict[str, Any] | None]:
    """The cut read again once everything after the fill has run, each run of cells showing one picture that the fill
    did not leave (no kept run of `record` holds a cell of it, and no cell of it is in `skip`: cells a repair already gave
    way to the frame beside it, its frame between refused) made again as the fill makes a run (`fill`, with the same
    rules): the frames, and the record of it, None where no such run was read. A cut drawn on twos or threes, or cut
    with the fill off, is not read again. `hold` reads the held-drawings hold of the cut as it now plays, asked only
    where a run is read: a cut whose redrawn repeats read as drawings as filmed (a pixel aside each) and as held once
    the anchor moved them back is drawn on twos or threes all the same, and keeps them (`drawn`)."""
    if mode == "off" or "drawn" in record:
        return frames, None
    before = record["before"]
    kept: set[int] = set()
    for r in [*before["held_runs"], *([before["wrap"]] if before.get("wrap") else [])]:
        if not r["filled"]:
            kept.update(range(r["start"], r["start"] + r["length"]))
    if before.get("wrap") and not before["wrap"]["filled"]:
        kept.add(0)
    same = same_steps(frames)
    n = len(frames)
    left = set(skip) | kept
    new = [r for r in runs(same) if not left & set(range(r["start"], r["start"] + r["length"]))]
    wrap = ring and "wrap" not in before and 0 not in left and (n - 1) not in left and wrap_same(frames)
    if not new and not wrap:
        return frames, None
    masked = [False] * len(same)
    for r in new:
        for k in range(r["start"], r["start"] + r["length"] - 1):
            masked[k] = True
    out, made = fill(frames, interpolate, ring=ring, same=masked, mode=mode, wrap=wrap,
                     hold=hold(frames) if hold is not None else None)
    return out, made


def times(n: int, fps: float, out_fps: float, *, ring: bool) -> list[float]:
    """The times, in frames of the cut, of the cells of a cut of `n` frames at `fps` given at `out_fps`: a loop's
    round(n·out_fps/fps) cells over its cycle, a cut played once from its first frame to its last."""
    if ring:
        count = round(n * out_fps / fps)
        return [k * n / count for k in range(count)]
    count = round((n - 1) * out_fps / fps) + 1
    return [k * (n - 1) / (count - 1) for k in range(count)]


def at_times(frames: list[Image.Image], interpolate: rife_mod.Interpolate, at: Sequence[float], *, ring: bool,
             spacing: float | Sequence[float], reads: bool | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """`frames` at the times `at`, in frames of the cut: a cell on a frame's time is that frame, one between two frames
    showing one picture is that picture, any other is made between the two at its own time (`_made`, within half its
    `spacing` of it: frames a cell, one for all or one each), and one no frame made is taken for is the nearer frame and
    named (`shown_again`). The record: how many were made, the cells shown again, how each was (`frames`), and the
    frame each cell is, None where it was made (`source_frames`)."""
    n = len(frames)
    same = same_steps(frames + ([frames[0]] if ring else []))
    if reads is None:
        reads = bool(ghost_screen(frames)["reads"])
    out: list[Image.Image] = []
    made: list[dict[str, Any]] = []
    shown_again: list[int] = []
    source_frames: list[int | None] = []
    for k, tau in enumerate(at):
        near = round(tau)
        if abs(tau - near) <= SNAP:
            out.append(frames[near % n])
            source_frames.append(near % n)
            continue
        a = math.floor(tau)
        if same[a]:
            out.append(frames[a])
            source_frames.append(a)
            continue
        t = tau - a
        half = (spacing if isinstance(spacing, (int, float)) else spacing[k]) / 2
        frame, how = _made(interpolate, frames[a], frames[(a + 1) % n], t, (t - half, t + half), reads=reads,
                           before=out[-1] if out else None)
        if frame is None:
            out.append(frames[near % n])
            source_frames.append(near % n)
            shown_again.append(k)
            made.append({"cell": k, "between": [a, (a + 1) % n], "taken": near % n, **how})
            continue
        out.append(frame)
        source_frames.append(None)
        made.append({"cell": k, "between": [a, (a + 1) % n], **how})
    return out, {"made": sum(1 for m in made if "taken" not in m), "shown_again": shown_again, "frames": made,
                 "source_frames": source_frames}


def upsample(frames: list[Image.Image], interpolate: rife_mod.Interpolate, *, ring: bool, fps: float, out_fps: float,
             reads: bool | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """`frames` given at `out_fps` (`times`), each cell at its own time (`at_times`, within half a cell of it)."""
    at = times(len(frames), fps, out_fps, ring=ring)
    out, made = at_times(frames, interpolate, at, ring=ring, spacing=fps / out_fps, reads=reads)
    return out, {"asked": out_fps, "fps": fps, "applied": True, "cells": len(out), "times": [round(x, 4) for x in at], **made}


def public(record: dict[str, Any]) -> dict[str, Any]:
    """The record as the strip metadata carries it: the reading as delivered (`cells`, `poses`, `held_runs`), before
    (each run `filled` or why not, with its faults), the cells made and left, and the rate asked — not each try."""
    out: dict[str, Any] = {k: record[k] for k in ("mode", "why", "drawn", "cells", "poses", "held_runs", "made") if k in record}
    before = record["before"]
    out["before"] = {"cells": before["cells"], "poses": before["poses"],
                     "held_runs": [{k: v for k, v in r.items() if k != "tries"} for r in before["held_runs"]],
                     **({"wrap_held": True} if before.get("wrap_held") else {}),
                     **({"wrap": {k: v for k, v in before["wrap"].items() if k != "tries"}} if "wrap" in before else {})}
    if record.get("wrap_held"):
        out["wrap_held"] = True
    if "unmade" in record:
        out["unmade"] = {k: record["unmade"][k] for k in ("cells", "why", "install") if k in record["unmade"]}
    if "out_fps" in record:
        out["out_fps"] = {k: record["out_fps"][k] for k in ("asked", "fps", "applied", "cells", "made", "shown_again", "why", "install")
                          if k in record["out_fps"]}
    if "again" in record:
        out["again"] = public(record["again"])
    return out


def strip_holds(holds: Sequence[dict[str, Any]], samples: Sequence[int], times: Sequence[float] | None = None) -> list[dict[str, Any]]:
    """`holds` (cells of the cut, both ends in, as `intended` gives them) as cells of the strip that holds `samples` of
    the frames delivered: a frame delivered at a rate of its own (`times`, in cells of the cut) is in a hold where its
    time is. A hold left on fewer than two of the strip's cells is not one there."""
    out = []
    for h in holds:
        cells = [j for j, k in enumerate(samples) if h["from"] <= (k if times is None else times[k]) <= h["to"]]
        if len(cells) >= 2:
            out.append({"from": cells[0], "to": cells[-1], "kind": h["kind"], "reason": h["reason"]})
    return out
