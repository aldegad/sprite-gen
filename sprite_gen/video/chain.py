# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-chain` — pieces of keyed clips on one canvas played as one move, at the time the caller gives
(docs/video-pipeline.md section 9).

The caller says what only the scene knows: where the pieces join (each piece's frames, `--piece NAME=DIR:FROM:TO`) and
when each frame is shown. A pose alike does not tell a join (a body is joined where its path goes on), and the time is
read off a camera and an eye: the engine picks neither. What the engine does is what every other way out of it does:

- **The pieces** are one canvas and one key, or they are refused: every frame the same size, every piece keyed the same
  way and at one rate (`frames.report.json` beside the frames, or `--key` and `--fps` for a piece no report speaks for).
- **The chain** is the frames of the pieces in turn, numbered from 0, each join with the frames between it asks for
  (`--bridge K`, at k/(K+1)). A place on the chain is a number on that count.
- **The time** is one of three: the chain at its own rate; `--out-fps F`, evenly from its first frame to its last
  (`held_fill.times`); `--places FILE`, a place for each frame delivered. A place between two frames is made between them
  and judged (`held_fill.at_times`, `held_fill.judge`, again at another fraction of `align.RETIME_T` within half the
  place's spacing); one no frame made is taken for is the nearer frame, named.
- **The faults that refuse a frame** are every fault `judge` can find, or those `--refuse` names. Which of them are a
  fault in a material and which are the material's own (art painted without a line reads as outline loss on every frame
  made) is read off the material by whoever knows it, as a scene's time is: the engine does not pick. The judge and its
  bars do not change. `held`, the bar of a frame made off its time and the final reading refuse whatever `--refuse`
  says; a fault found and not refused stays on the frame's record (`kept_faults`) and on a `warning:` line naming it.
- **No picture shown again.** The frames delivered are read (`held_fill.same_steps`) and each run the list of holds meant
  (`--holds`) does not name is made again between the poses beside it (`held_fill.fill`, its rules for what it keeps;
  `--fill-held off` makes none). Then they are read as `inspect-holds` reads a delivery (`qa.holds.inspect`): a frame
  still showing the picture before it, on no list, fails the chain, by its number. A piece drawn on twos or threes
  (`held.measure`) is not filled: its holds are listed, or the chain fails. A chain that reads no frame against the frame
  before is an error, never a chain with none shown again (`qa.holds.NothingRead`); so is one none of whose joins can be
  read.
- **No RIFE:** a frame to make takes the nearer frame, with a `warning:` line and the record; `--gate` fails it.
- **The record** opens with the faults this run refused for (`refuse`), and gives each frame's `source` (`{piece, frame}`,
  or `{between: [a, b], t}`), its judged values and its refused tries; each join's pose distance, in steps of the two pieces (the median step between their neighbouring frames, the
  distance `video-stop` reads, `gait_fallback.poses`), and the frames within `--near` of it whose poses are closer. Those
  are told and never taken.

A failed chain's frames are left in `keyed.refused/` and no `keyed/` is handed over."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.qa import holds as holds_mod
from sprite_gen.spec.runio import atomic_write_text, refuse_overlap
from sprite_gen.util.cli_kwargs import namespace_from_kwargs
from sprite_gen.util.console import utf8_streams
from sprite_gen.video import evidence as evidence_mod
from sprite_gen.video import gait_fallback
from sprite_gen.video import held as held_mod
from sprite_gen.video import held_fill as held_fill_mod
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import rife as rife_mod
from sprite_gen.video.frames import KEYS_READ, refused_dir
from sprite_gen.video.interpolation_quality import ghost_screen

PROG = "video-chain"
REPORT_KIND = "sprite-gen-video-chain-report"
REPORT_NAME = "chain.report.json"
FRAMES_REPORT = "frames.report.json"  # `video-frames`' report, beside its keyed/
KEYED = "keyed"  # the folder a chain's frames are handed over in, as `video-frames` hands over its own
NEAR = 6  # how far either way of a join its closer poses are looked for, frames
NEAR_SHOWN = 5  # how many of them the record names
ROOM = 40  # px kept round the figures when they are read and made: RIFE reads the frame it is given, a canvas's empty rest with it
RATE_SAME = 1e-3  # two rates this near are one
EXIT_OK, EXIT_SHOWN_AGAIN, EXIT_ERROR = 0, 1, 2


class ChainRefused(ValueError):
    """Pieces, a time or a reading the chain cannot be made from: exit EXIT_ERROR, nothing handed over."""


@dataclass
class Piece:
    name: str
    folder: Path
    files: list[Path]  # every frame of the clip, in name order: a join's closer poses may lie past the piece
    first: int
    last: int
    key: str | None
    fps: float | None
    start: int = 0  # the chain's number of its first frame

    @property
    def used(self) -> range:
        return range(self.first, self.last + 1)

    def source(self, frame: int) -> dict[str, Any]:
        return {"piece": self.name, "frame": frame}


# --- the pieces -------------------------------------------------------------------------------------------------------

def _frames_report(folder: Path) -> dict[str, Any]:
    path = folder.parent / FRAMES_REPORT
    return json.loads(path.read_text(encoding="utf-8")) if folder.name == KEYED and path.is_file() else {}


def _stated_key(report: dict[str, Any]) -> str | None:
    """The key a `video-frames` report says its frames were keyed on; a report that says only `auto` says none."""
    if report.get("alpha_mode") == "brightness":
        return "brightness"
    key = report.get("key_read") or report.get("key")
    return None if key in (None, "auto") else str(key)


def read_piece(spec: str) -> Piece:
    """`NAME=DIR:FROM:TO`: frames FROM..TO, both ends in, counted from 0 in name order, of the keyed PNGs in DIR (or in its
    keyed/, a `video-frames` folder)."""
    name, eq, rest = spec.partition("=")
    parts = rest.rsplit(":", 2)
    if not eq or not name or len(parts) != 3:
        raise ChainRefused(f"--piece {spec!r}: NAME=DIR:FROM:TO")
    try:
        first, last = int(parts[1]), int(parts[2])
    except ValueError:
        raise ChainRefused(f"--piece {spec!r}: FROM and TO are whole frame numbers from 0") from None
    given = Path(parts[0]).expanduser().resolve()
    folder = given / KEYED if (given / KEYED).is_dir() else given
    files = sorted(folder.glob("*.png"))
    if not files:
        raise ChainRefused(f"piece {name}: no *.png frames in {folder}")
    if not 0 <= first <= last < len(files):
        raise ChainRefused(f"piece {name}: frames {first}..{last}, and {folder} holds frames 0..{len(files) - 1}")
    report = _frames_report(folder)
    return Piece(name, folder, files, first, last, _stated_key(report), report.get("fps"))


def _one_key(pieces: Sequence[Piece], key: str | None) -> str:
    """The one key of `pieces`, each as its report says or as `key` (`--key`) says for one no report speaks for."""
    named = [(p.name, p.key) for p in pieces if p.key is not None] + ([("--key", key)] if key is not None else [])
    if len({k for _, k in named}) > 1:
        raise ChainRefused("the pieces are keyed differently (" + ", ".join(f"{n} {k}" for n, k in named) + "): a chain is one key")
    unknown = [p.name for p in pieces if p.key is None]
    if unknown and key is None:
        raise ChainRefused(f"no report says what {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} keyed on (no "
                           f"{FRAMES_REPORT} beside the frames, or one that says only auto): name it with --key")
    return named[0][1]  # type: ignore[return-value]


def _one_rate(pieces: Sequence[Piece], fps: float | None) -> float | None:
    """The pieces' one rate, as their reports say or as `fps` (`--fps`) says."""
    named = [(p.name, float(p.fps)) for p in pieces if p.fps] + ([("--fps", float(fps))] if fps else [])
    if any(abs(r - named[0][1]) > RATE_SAME for _, r in named):
        raise ChainRefused("the pieces are at different rates (" + ", ".join(f"{n} {r:g}" for n, r in named) + "): a chain plays at one")
    return named[0][1] if named else None


def _bridges(given: Sequence[int], joins: int) -> list[int]:
    """Each join's frames between: one value for every join, or one a join in order."""
    if any(k < 0 for k in given):
        raise ChainRefused("--bridge: a number of frames between, 0 or more")
    if not given:
        return [0] * joins
    if not joins:
        raise ChainRefused("--bridge: one piece has no join")
    if len(given) == 1:
        return list(given) * joins
    if len(given) != joins:
        raise ChainRefused(f"--bridge given {len(given)} times, and the pieces have {joins} joins: once for all of them, or once for each")
    return list(given)


def _read_places(path: Path) -> list[float]:
    """A place for each frame delivered: a JSON list, or an object's `places`."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChainRefused(f"--places {path}: {exc}") from None
    places = data.get("places") if isinstance(data, dict) else data
    if not isinstance(places, list) or not places:
        raise ChainRefused(f"--places {path}: a list of places, or an object whose `places` is one")
    if not all(isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) for p in places):
        raise ChainRefused(f"--places {path}: every place is a number on the chain's count")
    return [float(p) for p in places]


def _read_refuse(text: str | None) -> frozenset[str] | None:
    """`--refuse`'s faults, `held` always among them; None where it was not given (every fault refuses)."""
    if text is None:
        return None
    named = [name.strip() for name in text.split(",")]
    unknown = [name for name in named if name not in held_fill_mod.FAULTS]
    if unknown:
        raise ChainRefused(f"--refuse: {', '.join(repr(name) for name in unknown)} {'is' if len(unknown) == 1 else 'are'} no fault: "
                           f"one of {', '.join(held_fill_mod.FAULTS)}")
    return frozenset(named) | {"held"}


def _canvas_and_box(pieces: Sequence[Piece]) -> tuple[tuple[int, int], tuple[int, int, int, int] | None]:
    """The one size of every frame used, and the box of everything they draw with ROOM round it (None where they draw
    nothing). Refused where a frame is another size, or a piece is opaque (no key was taken off it)."""
    size = None
    box = None
    for p in pieces:
        keyed = False
        for k in p.used:
            with Image.open(p.files[k]) as raw:
                image = raw.convert("RGBA")
            if size is None:
                size = image.size
            elif image.size != size:
                raise ChainRefused(f"piece {p.name}'s frame {k} is {image.width}x{image.height} and the chain's first is "
                                   f"{size[0]}x{size[1]}: a chain is one canvas")
            alpha = image.getchannel("A")
            keyed = keyed or alpha.getextrema()[0] < 255
            b = alpha.getbbox()
            if b is not None:
                box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
        if not keyed:
            raise ChainRefused(f"piece {p.name}'s frames are opaque: a chain is of keyed frames (video-frames)")
    assert size is not None
    if box is None:
        return size, None
    return size, (max(0, box[0] - ROOM), max(0, box[1] - ROOM), min(size[0], box[2] + ROOM), min(size[1], box[3] + ROOM))


# --- the joins --------------------------------------------------------------------------------------------------------

def read_join(left: Piece, right: Piece, frames: dict[tuple[str, int], Image.Image], near: int) -> dict[str, Any]:
    """The join from `left`'s last frame into `right`'s first: the pose distance, in steps (the median step between
    neighbouring frames of the two pieces), and the pairs within `near` frames of it whose poses are closer, nearest
    first. `reads` false, with why, where a frame shows no figure or the pieces do not move. Told, never taken."""
    head = {"from": left.source(left.last), "into": right.source(right.first), "near": near}
    wanted = {(left.name, k) for k in left.used} | {(right.name, k) for k in right.used}
    around_l = [k for k in range(left.last - near, left.last + near + 1) if 0 <= k < len(left.files)]
    around_r = [k for k in range(right.first - near, right.first + near + 1) if 0 <= k < len(right.files)]
    files = {(p.name, k): p.files[k] for p, ks in ((left, around_l), (right, around_r)) for k in ks}
    keys = sorted(wanted | set(files))
    images = [frames[k] if k in frames else Image.open(files[k]).convert("RGBA") for k in keys]
    boxes = gait_fallback.subject_boxes(images)
    if np.isnan(boxes[:, 0]).all():
        return {**head, "reads": False, "why": "no frame shows a figure"}
    poses = dict(zip(keys, gait_fallback.poses(images, boxes)))
    bare = [k for k in ((left.name, left.last), (right.name, right.first)) if np.isnan(poses[k]).any()]
    if bare:
        return {**head, "reads": False, "why": f"no figure on {', '.join(f'{n} {k}' for n, k in bare)}"}

    def apart(a: tuple[str, int], b: tuple[str, int]) -> float:
        return float(np.nanmean(np.abs(poses[a] - poses[b])))

    neighbours = [apart((p.name, k), (p.name, k + 1)) for p in (left, right) for k in range(p.first, p.last)
                  if not (np.isnan(poses[(p.name, k)]).any() or np.isnan(poses[(p.name, k + 1)]).any())]
    step = float(np.median(neighbours)) if neighbours else 0.0
    if not step > 0:
        return {**head, "reads": False, "why": "the pieces do not move: no step to read the join in"}
    at = apart((left.name, left.last), (right.name, right.first)) / step
    closer = []
    for i in around_l:
        for j in around_r:
            a, b = (left.name, i), (right.name, j)
            if (i, j) == (left.last, right.first) or np.isnan(poses[a]).any() or np.isnan(poses[b]).any():
                continue
            steps = apart(a, b) / step
            if steps < at:
                closer.append({"from": left.source(i), "into": right.source(j), "steps": round(steps, 4)})
    closer.sort(key=lambda c: (c["steps"], abs(c["from"]["frame"] - left.last) + abs(c["into"]["frame"] - right.first)))
    return {**head, "reads": True, "step": round(step, 6), "steps": round(at, 4), "closer": closer[:NEAR_SHOWN], "closer_found": len(closer)}


# --- the frames -------------------------------------------------------------------------------------------------------

def _passed(attempt: dict[str, Any]) -> bool:
    return not attempt["faults"] and "window_over" not in attempt


def _tries(how: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """A frame's tries (`held_fill._made`): the one taken, None where none was, and those refused."""
    tries = how.get("tries") or []
    return (tries[-1], tries[:-1]) if "taken" not in how and tries and _passed(tries[-1]) else (None, tries)


def _judged(taken: dict[str, Any] | None) -> dict[str, Any]:
    """What the try taken for a frame gives its record: the values it was judged by, and the faults found and not refused."""
    if taken is None:
        return {"judged": None}
    return {"judged": taken["judged"], **({"kept_faults": taken["kept_faults"]} if "kept_faults" in taken else {})}


def _nearest(frames: Sequence[Image.Image], at: Sequence[float]) -> tuple[list[Image.Image], dict[str, Any]]:
    """`held_fill.at_times`' answer with nothing to make frames with: each place the nearer frame, named."""
    near = [min(round(p), len(frames) - 1) for p in at]
    rows = [{"cell": k, "between": [math.floor(p), math.floor(p) + 1], "taken": near[k], "t": round(p - math.floor(p), 4), "tries": []}
            for k, p in enumerate(at) if abs(p - near[k]) > held_fill_mod.SNAP]
    return [frames[k] for k in near], {"made": 0, "shown_again": [r["cell"] for r in rows], "frames": rows, "source_frames": near}


def _spacing(places: Sequence[float]) -> list[float]:
    """How far apart each place is from those beside it: the less of the two gaps that is not none, a frame where none is.
    A frame refused at its place is made again within half of this."""
    out = []
    for k, p in enumerate(places):
        gaps = [abs(p - places[j]) for j in (k - 1, k + 1) if 0 <= j < len(places) and places[j] != p]
        out.append(min(gaps) if gaps else 1.0)
    return out


class _Maker:
    """Frames made between others, RIFE located once one is asked for; `missing` holds why none could be where RIFE is
    not installed, and every place then takes the nearer frame (`_nearest`)."""

    def __init__(self, interpolate: rife_mod.Interpolate | None, reads: bool, refuse: frozenset[str] | None):
        self.made_by: list[dict[str, str]] = []
        self.given = interpolate is not None
        self.lazy = loop_mod._located(interpolate, self.made_by)
        self.reads = reads
        self.refuse = refuse
        self.missing: dict[str, Any] | None = None

    def at(self, frames: list[Image.Image], at: Sequence[float], spacing: float | Sequence[float]) -> tuple[list[Image.Image], dict[str, Any]]:
        if self.missing is None:
            try:
                return held_fill_mod.at_times(frames, self.lazy, at, ring=False, spacing=spacing, reads=self.reads, refuse=self.refuse)
            except rife_mod.RifeNotInstalled as exc:
                self.missing = {"why": "RIFE is not installed", "rife": str(exc), "install": rife_mod.install_command()}
        return _nearest(frames, at)

    def fill(self, frames: list[Image.Image], same: list[bool], mode: str) -> tuple[list[Image.Image], dict[str, Any]]:
        out, record = loop_mod.fill_cut(frames, self.lazy, ring=False, same=same, mode=mode, hold=None, refuse=self.refuse)
        record.pop("interpolator", None)  # this chain's, one for all it made: `named`
        if "unmade" in record and self.missing is None:
            self.missing = {k: record["unmade"][k] for k in ("why", "rife", "install")}
        return out, record

    def named(self) -> dict[str, Any] | None:
        if self.made_by:
            return {**self.made_by[0], **self.lazy.counts()}  # type: ignore[attr-defined]
        return {"kind": "injected"} if self.given else None


def _links(pieces: Sequence[Piece], bridges: Sequence[int], loaded: dict[tuple[str, int], Image.Image],
           maker: _Maker) -> list[dict[str, Any]]:
    """The chain, frame by frame: each a piece's (`source`, `file`, `image`), or one made between a join's two frames
    (`source` with `bridge`, `image`, its judged values and refused tries)."""
    links: list[dict[str, Any]] = []
    for j, p in enumerate(pieces):
        if j and bridges[j - 1]:
            left, count = pieces[j - 1], bridges[j - 1]
            a, b = left.source(left.last), p.source(p.first)
            ends = [loaded[(left.name, left.last)], loaded[(p.name, p.first)]]
            out, made = maker.at(ends, [(k + 1) / (count + 1) for k in range(count)], 1 / (count + 1))
            rows = {r["cell"]: r for r in made["frames"]}
            for k, image in enumerate(out):
                row = rows.get(k, {})
                taken, refused = _tries(row)
                src = made["source_frames"][k]
                link: dict[str, Any] = {"image": image, "refused": refused, "bridge": j - 1}
                if src is None:
                    link["source"] = {"between": [a, b], "t": row["t"], "bridge": True}
                    link.update(_judged(taken))
                else:
                    link.update(source=b if src else a, file=(p if src else left).files[p.first if src else left.last])
                    if "taken" in row:
                        link["taken"] = True
                links.append(link)
        p.start = len(links)
        links += [{"source": p.source(k), "file": p.files[k], "image": loaded[(p.name, k)]} for k in p.used]
    return links


def _cell(k: int, place: float, links: Sequence[dict[str, Any]], timed: dict[str, Any], rows: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """The record of frame `k` as the time gave it: where it comes from, the values it was judged by and what was
    refused for it."""
    src = timed["source_frames"][k]
    row = rows.get(k, {})
    taken, refused = _tries(row)
    if src is None:
        a, b = row["between"]
        return {"frame": k, "place": place, "source": {"between": [links[a]["source"], links[b]["source"]], "t": row["t"]},
                **_judged(taken), "refused": refused}
    link = links[src]
    cell = {"frame": k, "place": place, "source": link["source"], "refused": refused or link.get("refused", [])}
    if link.get("judged") is not None:
        cell.update(judged=link["judged"], **({"kept_faults": link["kept_faults"]} if "kept_faults" in link else {}))
    if "taken" in row:
        cell["nearer_frame"] = True
    return cell


def _drawn_pieces(pieces: Sequence[Piece], loaded: dict[tuple[str, int], Image.Image], rate: float | None) -> dict[str, int]:
    """The pieces drawn on twos or threes, read on their own frames as filmed (`held.measure`): a place may show any frame
    twice, and that is no drawing."""
    found = {}
    for p in pieces:
        thumbs = [loop_mod._small_features(loaded[(p.name, k)]) for k in p.used]
        steps = [float(np.abs(a - b).mean()) for a, b in zip(thumbs, thumbs[1:])]
        hold = held_mod.measure(steps, fps=rate or 24.0)["hold"] if steps else None
        if hold in held_mod.HOLDS:
            found[p.name] = hold
    return found


def _masked(same: Sequence[bool], cells: Sequence[dict[str, Any]], entries: Sequence[dict[str, Any]], drawn: dict[str, int]) -> list[bool]:
    """The steps the fill may make again: those into a frame a list of holds names are as meant, and so are a drawn piece's
    between its own frames."""
    def in_drawn(k: int) -> bool:
        a, b = cells[k]["source"], cells[k + 1]["source"]
        return a.get("piece") in drawn and a.get("piece") == b.get("piece")

    return [bool(s) and not any(e["from"] <= k + 1 <= e["to"] for e in entries) and not in_drawn(k) for k, s in enumerate(same)]


def _place_on(out_dir: Path, name: str, images: Sequence[Image.Image], cells: Sequence[dict[str, Any]], files: Sequence[Path | None],
              size: tuple[int, int], box: tuple[int, int, int, int]) -> Path:
    """The frames in a working folder beside `name` and handed over whole, a frame of a piece byte for byte as it came."""
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=out_dir))
    try:
        for cell, image, file in zip(cells, images, files):
            target = work / f"frame-{cell['frame'] + 1:04d}.png"
            if file is not None:
                shutil.copyfile(file, target)
                continue
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            canvas.paste(image, box[:2])
            canvas.save(target)
        final = out_dir / name
        work.rename(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return final


def _kept_lines(cells: Sequence[dict[str, Any]]) -> list[str]:
    """The frames delivered with a fault `--refuse` did not name, frame by frame, those with the same faults on one line."""
    alike: dict[tuple[str, ...], list[int]] = {}
    for cell in cells:
        if "kept_faults" in cell:
            alike.setdefault(tuple(cell["kept_faults"]), []).append(cell["frame"])
    return [f"frame{'s' if len(frames) > 1 else ''} {', '.join(map(str, frames))} kept with {', '.join(faults)}: found, and not named by --refuse" for faults, frames in alike.items()]


def _said_runs(row: dict[str, Any]) -> str:
    return ", ".join(str(r["from"]) if r["from"] == r["to"] else f"{r['from']}-{r['to']}" for r in row["unlistedRuns"])


def make_chain(pieces: Sequence[str], out_dir: Path, *, bridge: Sequence[int] = (), places: Path | None = None, out_fps: float | None = None,
               fps: float | None = None, key: str | None = None, holds: Sequence[Path] = (), scene: str | None = None, figure: str = "figure",
               fill_held: str = "auto", gate: bool = False, near: int = NEAR, refuse: str | None = None, report_path: Path | None = None,
               interpolate: rife_mod.Interpolate | None = None) -> dict[str, Any]:
    """The chain's frames in `out_dir/keyed/` and its report (`chain.report.json`, or `report_path`); see the module. The
    payload's `exit` is 0, or EXIT_SHOWN_AGAIN where the frames are refused (a frame shown again on no list, or, with
    `gate`, no RIFE to make what was asked for): they are in `keyed.refused/`. `ChainRefused` where nothing could be made
    or read. `interpolate` stands in for RIFE."""
    if fill_held not in held_fill_mod.MODES:
        raise ChainRefused(f"--fill-held {fill_held!r}: one of {', '.join(held_fill_mod.MODES)}")
    if key is not None and key not in KEYS_READ:
        raise ChainRefused(f"--key {key!r}: one of {', '.join(KEYS_READ)}")
    if places is not None and out_fps is not None:
        raise ChainRefused("--places and --out-fps: give one time")
    if out_fps is not None and not (math.isfinite(out_fps) and out_fps > 0):
        raise ChainRefused(f"--out-fps {out_fps:g}: a rate above 0")
    if fps is not None and not (math.isfinite(fps) and fps > 0):
        raise ChainRefused(f"--fps {fps:g}: a rate above 0")
    if near < 0:
        raise ChainRefused(f"--near {near}: a number of frames, 0 or more")
    refused_for = _read_refuse(refuse)
    if not pieces:
        raise ChainRefused("no --piece: NAME=DIR:FROM:TO, once for each piece in the order they play")
    chain_pieces = [read_piece(spec) for spec in pieces]
    names = [p.name for p in chain_pieces]
    if len(set(names)) != len(names):
        raise ChainRefused(f"--piece: two pieces are named {next(n for n in names if names.count(n) > 1)}")
    bridges = _bridges(bridge, len(chain_pieces) - 1)
    the_key = _one_key(chain_pieces, key)
    rate = _one_rate(chain_pieces, fps)
    if out_fps is not None and rate is None:
        raise ChainRefused(f"--out-fps needs the pieces' rate: no {FRAMES_REPORT} beside their frames says one, so name it with --fps")
    asked = _read_places(places) if places is not None else None
    count = sum(len(p.used) for p in chain_pieces) + sum(bridges)
    off = [p for p in asked or [] if not 0 <= p <= count - 1]
    if off:
        raise ChainRefused(f"--places: {off[0]:g} is off the chain, whose frames are numbered 0..{count - 1}")
    entries = holds_mod.for_scene(holds_mod.read_lists(holds), scene)
    out_dir = Path(out_dir).expanduser().resolve()
    target = Path(report_path or out_dir / REPORT_NAME).expanduser().resolve()
    keyed_dir, refused = out_dir / KEYED, refused_dir(out_dir / KEYED)
    refuse_overlap({**{f"piece {p.name}": p.folder for p in chain_pieces}, **({"--places": Path(places)} if places else {}),
                    **{f"--holds {h}": Path(h) for h in holds}},
                   {"keyed in --out-dir": keyed_dir, "keyed.refused in --out-dir": refused,
                    ("--report" if report_path else f"{REPORT_NAME} in --out-dir"): target}, verb=PROG)
    size, box = _canvas_and_box(chain_pieces)
    base: dict[str, Any] = {
        "kind": REPORT_KIND, "producer": evidence_mod.engine_identity(),
        "refuse": [f for f in held_fill_mod.FAULTS if refused_for is None or f in refused_for], "key": the_key, "canvas": list(size), "fill_held": fill_held, "gate": gate,
        "pieces": [{"name": p.name, "frames_dir": str(p.folder), "from": p.first, "to": p.last, "key": p.key or the_key,
                    "key_from": FRAMES_REPORT if p.key else "--key", "source": evidence_mod.source_identity([p.files[k] for k in p.used], rate or 0.0)}
                   for p in chain_pieces],
    }
    shutil.rmtree(keyed_dir, ignore_errors=True)  # from here an earlier run's frames are no longer handed over
    shutil.rmtree(refused, ignore_errors=True)

    def refuse(error: str, **more: Any) -> ChainRefused:
        loop_mod.write_loop_report(target, {**base, **more, "status": "failed", "error": error})
        return ChainRefused(error)

    if box is None:
        raise refuse(f"{holds_mod.NOTHING}: no piece draws a figure on any frame", read={"inspected": 0, "unlisted": 0, "table": {}})
    loaded = {(p.name, k): Image.open(p.files[k]).convert("RGBA").crop(box) for p in chain_pieces for k in p.used}
    joins = [read_join(chain_pieces[j], chain_pieces[j + 1], loaded, near) for j in range(len(chain_pieces) - 1)]
    for j, b in enumerate(bridges):
        joins[j]["bridge"] = b
    base["joins"] = joins
    if joins and not any(j["reads"] for j in joins):
        raise refuse("no join was read: " + "; ".join(f"{j['from']['piece']} into {j['into']['piece']}: {j['why']}" for j in joins))
    filmed = [loaded[(p.name, k)] for p in chain_pieces for k in p.used]
    maker = _Maker(interpolate, bool(ghost_screen(filmed)["reads"]), refused_for)
    warnings: list[str] = []
    try:
        links = _links(chain_pieces, bridges, loaded, maker)
        if asked is not None:
            at, kind = asked, "places"
            spacing: float | list[float] = _spacing(at)
        elif out_fps is not None:
            at, kind = held_fill_mod.times(count, float(rate), out_fps, ring=False) if count > 1 else [0.0], "out-fps"  # type: ignore[arg-type]
            spacing = float(rate) / out_fps  # type: ignore[arg-type]
        else:
            at, kind, spacing = [float(k) for k in range(count)], "clip", 1.0
        base["chain"] = {"frames": count, "pieces": {p.name: [p.start, p.start + len(p.used) - 1] for p in chain_pieces},
                         "bridges": [{"place": k, "source": link["source"], "judged": link.get("judged"), "refused": link["refused"],
                                      **({"kept_faults": link["kept_faults"]} if "kept_faults" in link else {})}
                                     for k, link in enumerate(links) if "bridge" in link]}
        base["time"] = {"kind": kind, "fps": rate, **({"out_fps": out_fps} if out_fps is not None else {}),
                        **({"places_file": str(Path(places).expanduser().resolve())} if places is not None else {}), "frames": len(at)}
        shown, timed = maker.at([link["image"] for link in links], at, spacing)
        rows = {r["cell"]: r for r in timed["frames"]}
        cells = [_cell(k, at[k], links, timed, rows) for k in range(len(at))]
        files: list[Path | None] = [links[s]["file"] if s is not None and "file" in links[s] else None for s in timed["source_frames"]]
        drawn = _drawn_pieces(chain_pieces, loaded, rate)
        same = held_fill_mod.same_steps(shown) if len(shown) > 1 else []
        delivered, filled = maker.fill(shown, _masked(same, cells, entries, drawn), fill_held)
    except rife_mod.RifeUnavailable as exc:
        raise refuse(f"{exc}; the chain's frames between are to be made: fix RIFE") from exc
    sources = [cell["source"] for cell in cells]
    for made in filled["frames"]:
        taken, tried = _tries(made)
        a, b = made["between"]
        c = made["cell"]
        cells[c].pop("kept_faults", None)  # those of the picture it showed before it was made
        cells[c].update(source={"between": [sources[a], sources[b]], "t": made["t"]}, **_judged(taken), refused=tried)
        files[c] = None
    for run in filled["before"]["held_runs"]:
        for tried in run.get("tries") or []:
            cells[tried["cell"]]["refused"] = [t for t in tried["tries"] if not _passed(t)]
    base.update(frames=cells, held_fill=held_fill_mod.public(filled), interpolator=maker.named(), drawn_pieces=drawn,
                holds={"lists": sorted({e["file"] for e in entries})})
    try:
        read = holds_mod.inspect(holds_mod.alpha_figures(delivered, name=figure), listed=entries)
    except holds_mod.NothingRead as exc:
        raise refuse(f"{holds_mod.NOTHING}: {exc}", read=exc.report or {"inspected": 0, "unlisted": 0, "table": {}}) from None
    base["read"] = read
    if maker.missing is not None:
        base["rife"] = maker.missing
        between = [c["frame"] for c in cells if "nearer_frame" in c]
        if between:
            warnings.append(f"{len(between)} frame{'s' if len(between) > 1 else ''} ({', '.join(map(str, between))}) took the nearer frame for a place between two: "
                            f"RIFE is not installed, so none was made; run `{maker.missing['install']}` and make the chain again (docs/video-pipeline.md section 9)")
        if any("bridge" in link and "taken" in link for link in links):
            warnings.append(f"a join's frames between are the frames beside it: RIFE is not installed; run `{maker.missing['install']}` and make the chain again")
    else:
        warnings += [f"frame {k} took the nearer frame: every frame made for its place was refused ({', '.join(sorted({f for t in cells[k]['refused'] for f in t['faults']})) or 'ghost band'})"
                     for k in timed["shown_again"]]
        warnings += [f"the frame between at chain place {k} is the frame beside it: every frame made for it was refused" for k, link in enumerate(links) if "taken" in link]
    warnings += loop_mod.fill_lines(filled)
    warnings += _kept_lines(cells)
    warnings += [f"the join from {j['from']['piece']} into {j['into']['piece']} could not be read: {j['why']}" for j in joins if not j["reads"]]
    shown_again = read["unlisted"]
    named = "; ".join(f"{name} frame{'s' if row['unlisted'] > 1 else ''} {_said_runs(row)}" for name, row in read["table"].items() if row["unlisted"])
    failed: str | None = None
    if maker.missing is not None:
        if shown_again:
            warnings.append(f"{shown_again} frame{'s' if shown_again > 1 else ''} show{'' if shown_again > 1 else 's'} the picture of the frame before: {named}")
        if gate:
            failed = f"--gate: RIFE is not installed, and the chain's frames between were not made{f' ({named} shown again)' if shown_again else ''}"
    elif shown_again:
        why = sorted({r["why"] for r in filled["before"]["held_runs"] if not r.get("filled") and r.get("why")})
        drawn_said = [f"{n} is drawn on {'twos' if h == 2 else 'threes'}" for n, h in drawn.items()]
        failed = (f"{shown_again} frame{'s' if shown_again > 1 else ''} show{'' if shown_again > 1 else 's'} the picture of the frame before and "
                  f"{'are' if shown_again > 1 else 'is'} on no list: {named}" + (f" ({'; '.join(why + drawn_said)})" if why or drawn_said else "")
                  + ". List a hold meant in --holds, or change the places or the pieces"
                  + ("; a fault named that is the material's own and no fault in it is left out of --refuse (docs/video-pipeline.md section 9)"
                     if any(r.get("faults") for r in filled["before"]["held_runs"]) else ""))
    final = _place_on(out_dir, refused.name if failed else KEYED, delivered, cells, files, size, box)
    payload = {**base, "status": "failed" if failed else "passed", "warnings": warnings, **({"error": failed} if failed else {}),
               ("refused_dir" if failed else "keyed_dir"): str(final)}
    loop_mod.write_loop_report(target, payload)
    payload.update(report=str(target), exit=EXIT_SHOWN_AGAIN if failed else EXIT_OK)
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--piece", action="append", default=[], metavar="NAME=DIR:FROM:TO",
                        help="a piece, in the order they play: frames FROM..TO (both in, counted from 0 in name order) of the keyed "
                             "PNGs in DIR or DIR/keyed. Where a piece ends and the next begins is yours to choose: the join's pose is "
                             "reported, never chosen")
    parser.add_argument("--bridge", action="append", type=int, default=[], metavar="K",
                        help="frames made between a join's two frames, at k/(K+1): once for every join, or once per join in order (default 0)")
    time = parser.add_mutually_exclusive_group()
    time.add_argument("--out-fps", type=float, help="the chain resampled evenly from its first frame to its last at this rate (default: at its own rate)")
    time.add_argument("--places", type=Path, help="a JSON list (or an object's `places`) of a place on the chain for each frame "
                      "delivered, its frames numbered from 0 with a bridge's among them; a place between two frames is made between them")
    parser.add_argument("--holds", action="append", type=Path, default=[], help="a list of the holds meant, as inspect-holds reads it, "
                        "frames counted on the frames delivered: those stay; or a folder, every *.holds.json under it (repeat for more)")
    parser.add_argument("--scene", help="the scene this is, for a list whose holds name scenes")
    parser.add_argument("--figure", default="figure", help="the figure's name, for a list whose holds name figures (several: the name and 0, 1, ...)")
    parser.add_argument("--fill-held", choices=held_fill_mod.MODES, default="auto",
                        help="auto (default): a frame showing the picture before it, on no list, is made again between the poses beside it "
                             "(docs/loop-repair.md section 7); off: none is, and any left fails the chain as one refused does")
    parser.add_argument("--gate", action="store_true", help="fail where RIFE is not installed and a frame was to be made (else the nearer frame and a warning line)")
    parser.add_argument("--refuse", metavar="FAULT,FAULT", help=f"the faults that refuse a frame made, of {', '.join(held_fill_mod.FAULTS)} (default: all). Which are a fault "
                        "in your material and which are its own is yours to read off it (docs/video-pipeline.md section 9); held and the final reading refuse whatever this says, "
                        "and a fault left out stays on the frame's record and a warning line")
    parser.add_argument("--key", choices=KEYS_READ, help=f"what the pieces are keyed on, for one with no {FRAMES_REPORT} that says; a report that says otherwise refuses the chain")
    parser.add_argument("--fps", type=float, help=f"the pieces' rate, for one with no {FRAMES_REPORT} that says (needed by --out-fps)")
    parser.add_argument("--near", type=int, default=NEAR, help=f"how many frames either way of a join to look for closer poses, reported only (default {NEAR})")
    parser.add_argument("--out-dir", type=Path, required=True, help="writes keyed/ and chain.report.json here; a refused chain leaves keyed.refused/ and no keyed/")
    parser.add_argument("--report", type=Path, help=f"report path (default <out-dir>/{REPORT_NAME})")


def run(**kwargs: object) -> int:
    """Make the chain and print what became of it. 0; 1 where a frame shown again is on no list (or --gate and no RIFE); 2 on
    an error, a chain that read nothing among them."""
    parser = argparse.ArgumentParser(add_help=False)
    add_arguments(parser)
    args = namespace_from_kwargs(parser, kwargs)
    try:
        payload = make_chain(args.piece, args.out_dir, bridge=args.bridge, places=args.places, out_fps=args.out_fps, fps=args.fps, key=args.key,
                             holds=args.holds, scene=args.scene, figure=args.figure, fill_held=args.fill_held, gate=args.gate, near=args.near,
                             refuse=args.refuse, report_path=args.report)
    except (ValueError, OSError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except SystemExit as exc:  # a refusal of the shared helpers (`refuse_overlap`): an error here as any other
        print(exc, file=sys.stderr)
        return EXIT_ERROR
    held = payload["held_fill"]
    print(json.dumps({"keyed_dir": payload.get("keyed_dir"), "refused_dir": payload.get("refused_dir"), "frames": len(payload["frames"]),
                      "made": sum(1 for c in payload["frames"] if "between" in c["source"]),
                      "poses_cells": {"before": held_fill_mod.said(held["before"]), "after": held_fill_mod.said(held)},
                      "refuse": payload["refuse"], "unlisted": payload["read"]["unlisted"], "listed": sum(r["listed"] for r in payload["read"]["table"].values()),
                      "joins": [{"from": j["from"], "into": j["into"], "bridge": j["bridge"], "steps": j.get("steps"), "closer": j.get("closer_found")} for j in payload["joins"]],
                      "interpolator": payload["interpolator"], "report": payload["report"]}, ensure_ascii=False, indent=2))
    for line in payload["warnings"]:
        print(f"{PROG}: warning: {line}", file=sys.stderr)
    if "error" in payload:
        print(f"{PROG}: {payload['error']}", file=sys.stderr)
    return payload["exit"]


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog=f"sprite-gen {PROG}", description=__doc__.split("\n\n")[0])
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
