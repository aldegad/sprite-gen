# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-cycle-align` — one cycle length for every direction of a set.

Each direction of a walk is filmed on its own, so its cycle comes out its own length (a Lite
set measured 16 to 27 frames). A game that turns a character mid-stride wants every direction
to be the same number of frames and to start on the same step. So every loop of the set is
resampled to the set's median length L*: frame k of the new loop is the source loop at time
k·L/L* (cyclic, offset 0), so a time that lands on a source frame takes that frame as filmed
and only the times between two frames are made by RIFE (or, `--between nearest`, take the nearer
source frame). Then each loop is turned to start as a heel lands — read off the stride, the
lowest row or the body's top line (`foot_strike`), never off the frame's top edge, which a long
ear owns. docs/loop-repair.md section 4.

Every frame between two source frames softens a little, so the length is the median (the one
that needs the fewest made frames across the set), and a loop already that long is not touched.

The loop directories are `video-loop` output directories. Their first alignment keeps the cut
as filmed in `cycle.source/`; every later alignment reads from there, so running it again — or
with another length — never resamples a resampled loop.

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
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import rife as rife_mod

SNAP = 0.03  # a sample time within this of a source frame takes that frame
SOURCE_DIR = loop_mod.CYCLE_SOURCE_DIR  # removed by video-loop whenever it cuts the loop again
RATE_TOLERANCE = 0.002  # frame rates read back from the strip metadata agree within this
LOW_ALPHA = 128  # the foot-strike turn reads solid pixels only
# The foot-strike turn (docs/loop-repair.md section 4). A walk seen from the side or a diagonal opens
# its feet by a good part of its height each step; from the front or back its foot band hardly
# changes width, but the foot nearer the viewer is drawn lower by a few hundredths of it.
STRIDE_MIN = 0.15  # the foot band's swing (of the body's height) that says the feet open along the picture
REACH_MIN = 0.015  # the lowest row's swing (of the body's height) that says a foot steps toward the viewer
BODY_RUN = 0.5  # the body's top line: the first row at least half as wide as the frame's widest
START_FOOT = "right"  # every loop of a set starts as this own foot lands, where its view can tell
SHADE_BAND = 0.15  # the shade cue reads the feet and lower legs: the lowest 15 % of the body
# The two strikes must differ by this to name a foot (`strike_foot`): shade in luma (0..1), depth in
# body heights. A smaller difference is the drawing, not the feet, and the foot is left unnamed.
SHADE_MARGIN = 0.015
FOOT_MARGIN = 0.01
# A cue that does not swing with the step is the drawing's own flicker: its once-a-cycle swing (first
# harmonic) must be at least this share of its spread over the cycle.
CUE_RHYTHM = 0.25
BETWEEN = ("rife", "nearest")  # how a time between two source frames is filled (resample)
# A made frame whose dark pixels inside the body exceed both neighbours' by this fraction of its
# solid pixels is named in the report's warnings: a smear, not a dark part that moved.
SMEAR_WARN = 0.001


def resample(frames: list[Image.Image], length: int, interpolate: rife_mod.Interpolate | None,
             *, between: str = "rife") -> tuple[list[Image.Image], dict[str, Any]]:
    """`frames` as one cycle, resampled to `length` frames at times k·L/length (offset 0).

    A time between two source frames is made by `interpolate` (`between` rife), with what it added
    measured (`rife.smear`), or takes the nearer source frame (`between` nearest: nothing is made,
    the motion keeps the filmed frames at up to half a frame off their time)."""
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
        if frac < SNAP:
            out.append(frames[i % count])
        elif frac > 1 - SNAP:
            out.append(frames[(i + 1) % count])
        elif between == "nearest":
            out.append(frames[(i + (frac >= 0.5)) % count])
            nearest_at.append(k)
        else:
            if interpolate is None:
                raise ValueError(f"frame {k} of {length} falls between source frames and no interpolator is available")
            a, b = frames[i % count], frames[(i + 1) % count]
            out.append(interpolate(a, b, frac))
            made_at.append(k)
            smears.append({"at": k, **rife_mod.smear(out[-1], a, b)})
    facts: dict[str, Any] = {"from": count, "to": length, "between": between, "taken": length - len(made_at),
                             "made_by_rife": len(made_at), "made_at": made_at}
    if between == "nearest":
        facts["nearest_at"] = nearest_at
    else:
        facts["smear"] = smears
    return out, facts


def _longest_runs(solid: np.ndarray) -> np.ndarray:
    """The longest horizontal run of solid pixels in each row."""
    edges = np.diff(np.pad(solid.astype(np.int8), ((0, 0), (1, 1))), axis=1)
    runs = np.zeros(solid.shape[0], dtype=int)
    for y in np.nonzero(solid.any(axis=1))[0]:
        starts, ends = np.nonzero(edges[y] == 1)[0], np.nonzero(edges[y] == -1)[0]
        runs[y] = int((ends - starts).max())
    return runs


def strike_signals(frames: list[Image.Image]) -> dict[str, list[float]]:
    """Per frame, from the solid pixels: `stride`, the width of the foot band (the lowest FOOT_BAND
    of the frame's own height) as a fraction of that height; `reach`, the lowest solid row; `top`,
    the body's top line — the first row whose longest solid run is at least BODY_RUN of the
    frame's widest, so an ear, a hat's point or an antenna (a narrow run) is passed over."""
    out: dict[str, list[float]] = {"stride": [], "reach": [], "top": [], "height": []}
    for f in frames:
        solid = np.asarray(f.getchannel("A")) >= LOW_ALPHA
        rows = np.nonzero(solid.any(axis=1))[0]
        if rows.size == 0:
            raise ValueError("a loop frame has no solid body")
        y0, y1 = int(rows[0]), int(rows[-1])
        height = y1 - y0 + 1
        band = solid[y1 + 1 - max(1, round(height * loop_mod.FOOT_BAND)) : y1 + 1]
        xs = np.nonzero(band.any(axis=0))[0]
        runs = _longest_runs(solid)
        out["stride"].append((xs[-1] - xs[0] + 1) / height)
        out["reach"].append(float(y1))
        out["top"].append(float(np.nonzero(runs >= BODY_RUN * runs.max())[0][0]))
        out["height"].append(float(height))
    return out


def _feet(frame: Image.Image) -> tuple[np.ndarray, np.ndarray, int]:
    """(solid mask, the picture's right half of the feet as a column mask, body height): the halves
    split at the middle of the foot band (the lowest FOOT_BAND of the frame's own height)."""
    solid = np.asarray(frame.getchannel("A")) >= LOW_ALPHA
    rows = np.nonzero(solid.any(axis=1))[0]
    y1, height = int(rows[-1]), int(rows[-1] - rows[0] + 1)
    xs = np.nonzero(solid[y1 + 1 - max(1, round(height * loop_mod.FOOT_BAND)) : y1 + 1].any(axis=0))[0]
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
    named."""
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
    if margin < (SHADE_MARGIN if lateral else FOOT_MARGIN):
        return {**record, "feet": None, "why": f"the two strikes look alike in {record['cue']} ({margin:.4f})"}
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


def foot_strike(frames: list[Image.Image], *, view: str | None = None, foot: str = START_FOOT) -> dict[str, Any]:
    """Where a loop starts: the frame a heel has just landed, read off one signal smoothed over its
    neighbours (1-2-1), never off the frame's top edge, which a long ear or a hat's point owns.

    - `stride`: seen from the side or a diagonal the feet are widest apart as the front heel
      lands; used where the stride swings by STRIDE_MIN of the body's height or more.
    - `reach`: seen from the front or back the feet pass one behind the other and the stride
      hardly moves, but the foot nearest the viewer is drawn lowest, and lowest when the feet are
      furthest apart — as the heel lands; used where the lowest row swings by REACH_MIN or more.
    - `body_low`: neither — a body with no legs to read — starts where its top line is lowest.

    A walk has two such moments a cycle, half a cycle apart: the larger, and the frame half a cycle
    on (not the other peak: one step can stride much less than the other, or not peak at all).
    With the loop's `view` (`side@right`, `front`, …) the one where `foot` lands is taken
    (`strike_foot`); without a view, or where the view's cue cannot tell, the larger, the first on
    a tie, and `start_foot` is None with the reason."""
    if foot not in handed_mod.SIDES:
        raise ValueError(f"foot must be left or right, not {foot!r}")
    sig = strike_signals(frames)
    height = float(np.median(sig["height"]))
    swing = {k: float(max(sig[k]) - min(sig[k])) / (1.0 if k == "stride" else height) for k in ("stride", "reach")}
    by = "stride" if swing["stride"] >= STRIDE_MIN else "reach" if swing["reach"] >= REACH_MIN else "body_low"
    signal = sig["top" if by == "body_low" else by]
    n = len(signal)
    smooth = [(signal[(k - 1) % n] + 2 * signal[k] + signal[(k + 1) % n]) / 4 for k in range(n)]
    first = max(range(n), key=lambda k: smooth[k])
    second = (first + n // 2) % n
    out: dict[str, Any] = {"start": first, "by": by, "stride_swing": round(swing["stride"], 4), "reach_swing": round(swing["reach"], 4),
                           "strikes": [first, second], "start_foot": None}
    if view is None:
        return {**out, "foot_why": "no view given for this loop"}
    name, _, facing = view.partition("@")
    handed_mod.validate_view(name, facing or None)
    if by == "body_low":
        return {**out, "foot_why": "no legs to read"}
    found = strike_foot(frames, (first, second), name, facing or None)
    if found["feet"] is None:
        return {**out, "foot": found, "foot_why": found["why"]}
    start = (first, second)[found["feet"].index(foot)]
    return {**out, "start": start, "start_foot": foot, "foot": found}


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


def _rebuild(loop_dir: Path, meta_path: Path, meta: dict[str, Any], frames: list[Image.Image], fps: float,
             record: dict[str, Any]) -> dict[str, Any]:
    """Write the aligned cycle, strip, GIF and WebP over the loop's own, at the loop's own cell size rules."""
    name = meta_path.name[: -len(".strip.json")]
    cycle_dir = loop_dir / "cycle"
    cycle_dir.mkdir(exist_ok=True)
    for old in cycle_dir.glob("frame-*.png"):
        old.unlink()
    for k, im in enumerate(frames):
        im.save(cycle_dir / f"frame-{k:03d}.png")
    cycle_seconds = len(frames) / fps
    standing = meta.get("body_src_h") if meta.get("body_ref") == "first-frame" else None
    strip, strip_meta = loop_mod.build_strip(
        frames, max_height=int(meta["cell_height_cap"]), cycle_seconds=cycle_seconds, body_height=meta.get("body_height_target"),
        anchor="feet" if meta.get("foot_anchor") == "feet" else "none", kind=str(meta["kind"]), standing_src=standing)
    # what build_strip does not own (how the cut was anchored) is carried over as it was; a
    # follow-through moved the old cycle's cells, so it is cleared and the record says so
    if "follow" in meta or (loop_dir / loop_mod.FOLLOW_SOURCE).exists():
        record["follow_cleared"] = True
    (loop_dir / loop_mod.FOLLOW_SOURCE).unlink(missing_ok=True)
    merged = {**{k: v for k, v in meta.items() if k not in strip_meta and k not in ("cycle_align", "follow")}, **strip_meta}
    if meta.get("foot_anchor") and meta.get("foot_anchor") != "feet":
        merged["foot_anchor"] = meta["foot_anchor"]
    cells = [strip.crop((k * strip_meta["w"], 0, (k + 1) * strip_meta["w"], strip_meta["h"])) for k in range(strip_meta["frames"])]
    flat = np.stack([loop_mod._small_features(c) for c in cells])
    adjacent = float(np.abs(flat[1:] - flat[:-1]).mean())
    seam = float(np.abs(flat[-1] - flat[0]).mean())
    record["seam_ratio"] = round(seam / adjacent, 4) if adjacent > 0 else None
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


def align_set(loop_dirs: list[Path], *, length: int | None = None, interpolate: rife_mod.Interpolate | None = None,
              report_path: Path | None = None, between: str = "rife", views: list[str | None] | None = None,
              start_foot: str = START_FOOT) -> dict[str, Any]:
    """Resample every loop of a set to one length (default: the median), turned to a foot strike —
    where `views` (one per loop: `front`, `back`, `side@right`, `front_diagonal@left`, …) lets it,
    the strike of the same own foot (`start_foot`) in every loop."""
    if len(loop_dirs) < 1:
        raise SystemExit("video-cycle-align: at least one --loop-dir")
    if views is not None and len(views) != len(loop_dirs):
        raise SystemExit(f"video-cycle-align: {len(views)} --view for {len(loop_dirs)} --loop-dir; give one per loop, in the same order")
    if start_foot not in handed_mod.SIDES:
        raise SystemExit(f"video-cycle-align: --start-foot must be left or right, not {start_foot!r}")
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

    # Every loop is resampled before any is rewritten: a loop that cannot be made leaves the set as it was.
    aligned = []
    for (d, *_), frames, view in zip(loops, sources, views or [None] * len(loops)):
        try:
            out, facts = resample(frames, target, lazy, between=between)
            strike = foot_strike(out, view=view, foot=start_foot)
        except rife_mod.RifeNotInstalled as exc:
            raise rife_mod.RifeNotInstalled(f"{d}: {exc}") from exc
        except (ValueError, rife_mod.RifeUnavailable) as exc:
            raise SystemExit(f"video-cycle-align: {d}: {exc}; frames between source frames are made by RIFE (docs/loop-repair.md)") from exc
        start = strike["start"]
        record = {**facts, "turned_by": start, "turned_on": strike["by"], "view": view, "start_foot": strike["start_foot"],
                  **({"foot": strike["foot"]} if "foot" in strike else {}), **({"foot_why": strike["foot_why"]} if "foot_why" in strike else {}),
                  "stride_swing": strike["stride_swing"], "reach_swing": strike["reach_swing"],
                  "fps": round(fps, 4), "source": SOURCE_DIR,
                  "made_at": [(k - start) % target for k in facts["made_at"]]}
        if "nearest_at" in facts:
            record["nearest_at"] = sorted((k - start) % target for k in facts["nearest_at"])
        if "smear" in facts:
            record["smear"] = sorted(({**m, "at": (m["at"] - start) % target} for m in facts["smear"]), key=lambda m: m["at"])
        aligned.append((out[start:] + out[:start], record))
    rows = []
    for (d, meta_path, meta, _), (out, record) in zip(loops, aligned):
        merged = _rebuild(d, meta_path, meta, out, fps, record)
        rows.append({"dir": str(d), "name": meta_path.name[: -len(".strip.json")], **{k: v for k, v in record.items() if k not in ("gif", "webp")},
                     "strip": {k: merged[k] for k in ("frames", "w", "h", "body_h", "delay_ms")}})
    warnings = [f"{r['name']}: frame {m['at']} (made by RIFE) has {100 * m['dark_excess']:.2f} % more dark pixels inside the body "
                f"than either source frame beside it — a smear; see it in cycle/, or align with --between nearest"
                for r in rows for m in r.get("smear", []) if m["dark_excess"] > SMEAR_WARN]
    if len(rows) > 1 and views is None:
        warnings.append("no view given (--view): each loop starts on its larger strike, whichever foot that is")
    else:
        warnings += [f"{r['name']}: starts on its larger strike, foot not named — {r['foot_why']}" for r in rows
                     if r["view"] is not None and r["start_foot"] is None]
    report = {"kind": "sprite-gen-video-cycle-align-report", "length": target, "length_rule": "requested" if length is not None else "median",
              "between": between, "start_foot": start_foot, "warnings": warnings,
              "lengths": lengths, "fps": round(fps, 4), "cycle_seconds": round(target / fps, 4),
              "interpolator": ({"kind": "rife-ncnn-vulkan", **located[0]} if located else
                               {"kind": "injected"} if any(r["made_by_rife"] for r in rows) else None),
              "made_by_rife": sum(r["made_by_rife"] for r in rows), "loops": rows}
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
    parser.add_argument("--between", choices=BETWEEN, default="rife",
                        help="a time between two source frames: rife (default) makes that frame, its smear measured per frame in the report; "
                             "nearest takes the nearer source frame — nothing made, no RIFE needed, the motion up to half a frame off its time")


def run(**kwargs: object) -> int:
    try:
        report = align_set(list(kwargs["loop_dir"]), length=kwargs.get("length"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
                           between=str(kwargs.get("between") or "rife"), views=kwargs.get("view"),  # type: ignore[arg-type]
                           start_foot=str(kwargs.get("start_foot") or START_FOOT))
    except rife_mod.RifeNotInstalled as exc:
        # Asked for by name, so no RIFE is a failure, never a quiet skip (video-set skips with a warning).
        raise SystemExit(f"video-cycle-align: {exc}; frames between source frames are made by RIFE — "
                         f"`{rife_mod.INSTALL_COMMAND}` (docs/loop-repair.md)") from exc
    for line in report["warnings"]:
        print(f"video-cycle-align: warning: {line}", file=sys.stderr)
    print(json.dumps({k: report[k] for k in ("length", "lengths", "between", "made_by_rife", "cycle_seconds")}
                     | {"loops": [{k: r[k] for k in ("name", "from", "to", "made_by_rife", "turned_by", "turned_on", "start_foot", "seam_ratio")} for r in report["loops"]]},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-cycle-align", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
