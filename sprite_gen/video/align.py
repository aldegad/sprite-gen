# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-cycle-align` — one cycle length for every direction of a set.

Each direction of a walk is filmed on its own, so its cycle comes out its own length (a Lite
set measured 16 to 27 frames). A game that turns a character mid-stride wants every direction
to be the same number of frames and to start on the same step. So every loop of the set is
resampled to the set's median length L*: frame k of the new loop is the source loop at time
k·L/L* (cyclic, offset 0), so a time that lands on a source frame takes that frame as filmed
and only the times between two frames are made by RIFE. Then each loop is turned to start where
the body is lowest — a foot just landed. docs/loop-repair.md section 4.

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
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import rife as rife_mod

SNAP = 0.03  # a sample time within this of a source frame takes that frame
SOURCE_DIR = loop_mod.CYCLE_SOURCE_DIR  # removed by video-loop whenever it cuts the loop again
RATE_TOLERANCE = 0.002  # frame rates read back from the strip metadata agree within this
LOW_ALPHA = 128  # the body's top line for the foot-strike turn: solid pixels only


def resample(frames: list[Image.Image], length: int, interpolate: rife_mod.Interpolate | None) -> tuple[list[Image.Image], dict[str, Any]]:
    """`frames` as one cycle, resampled to `length` frames at times k·L/length (offset 0)."""
    count = len(frames)
    if length < 2:
        raise ValueError(f"cycle length {length} is too short")
    out: list[Image.Image] = []
    made_at: list[int] = []
    for k in range(length):
        t = k * count / length
        i = int(np.floor(t))
        frac = t - i
        if frac < SNAP:
            out.append(frames[i % count])
        elif frac > 1 - SNAP:
            out.append(frames[(i + 1) % count])
        else:
            if interpolate is None:
                raise ValueError(f"frame {k} of {length} falls between source frames and no interpolator is available")
            out.append(interpolate(frames[i % count], frames[(i + 1) % count], frac))
            made_at.append(k)
    return out, {"from": count, "to": length, "taken": length - len(made_at), "made_by_rife": len(made_at), "made_at": made_at}


def foot_strike_start(frames: list[Image.Image]) -> int:
    """The frame where the body is lowest — its top line lowest, both feet down just after a heel
    lands — smoothed over its neighbours (1-2-1). A walk has two such moments a cycle; the first
    is taken."""
    tops = []
    for f in frames:
        box = f.getchannel("A").point(lambda v: 255 if v >= LOW_ALPHA else 0).getbbox()
        if box is None:
            raise ValueError("a loop frame has no solid body")
        tops.append(box[1])
    n = len(tops)
    smooth = [(tops[(k - 1) % n] + 2 * tops[k] + tops[(k + 1) % n]) / 4 for k in range(n)]
    return max(range(n), key=lambda k: smooth[k])


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
    record["gif"] = loop_mod.verify_animation(gif_path, expect_frames=len(cells), check_stale=False)
    record["webp"] = loop_mod.verify_animation(webp_path, expect_frames=len(cells), check_stale=True)
    return merged


def align_set(loop_dirs: list[Path], *, length: int | None = None, interpolate: rife_mod.Interpolate | None = None,
              report_path: Path | None = None) -> dict[str, Any]:
    """Resample every loop of a set to one length (default: the median), turned to a foot strike."""
    if len(loop_dirs) < 1:
        raise SystemExit("video-cycle-align: at least one --loop-dir")
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
    for (d, *_), frames in zip(loops, sources):
        try:
            out, facts = resample(frames, target, lazy)
            start = foot_strike_start(out)
        except rife_mod.RifeNotInstalled as exc:
            raise rife_mod.RifeNotInstalled(f"{d}: {exc}") from exc
        except (ValueError, rife_mod.RifeUnavailable) as exc:
            raise SystemExit(f"video-cycle-align: {d}: {exc}; frames between source frames are made by RIFE (docs/loop-repair.md)") from exc
        record = {**facts, "turned_by": start, "fps": round(fps, 4), "source": SOURCE_DIR,
                  "made_at": [(k - start) % target for k in facts["made_at"]]}
        aligned.append((out[start:] + out[:start], record))
    rows = []
    for (d, meta_path, meta, _), (out, record) in zip(loops, aligned):
        merged = _rebuild(d, meta_path, meta, out, fps, record)
        rows.append({"dir": str(d), "name": meta_path.name[: -len(".strip.json")], **{k: v for k, v in record.items() if k not in ("gif", "webp")},
                     "strip": {k: merged[k] for k in ("frames", "w", "h", "body_h", "delay_ms")}})
    report = {"kind": "sprite-gen-video-cycle-align-report", "length": target, "length_rule": "requested" if length is not None else "median",
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


def run(**kwargs: object) -> int:
    try:
        report = align_set(list(kwargs["loop_dir"]), length=kwargs.get("length"), report_path=kwargs.get("report"))  # type: ignore[arg-type]
    except rife_mod.RifeNotInstalled as exc:
        # Asked for by name, so no RIFE is a failure, never a quiet skip (video-set skips with a warning).
        raise SystemExit(f"video-cycle-align: {exc}; frames between source frames are made by RIFE — "
                         f"`{rife_mod.INSTALL_COMMAND}` (docs/loop-repair.md)") from exc
    print(json.dumps({k: report[k] for k in ("length", "lengths", "made_by_rife", "cycle_seconds")}
                     | {"loops": [{k: r[k] for k in ("name", "from", "to", "made_by_rife", "turned_by", "seam_ratio")} for r in report["loops"]]},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-cycle-align", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
