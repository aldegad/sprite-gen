# SPDX-License-Identifier: Apache-2.0
"""Holds: the frames on which a figure shows the picture it showed on the frame before, so that on screen it stands
still for a frame and then goes on (`inspect-holds`, docs/asset-tools.md "Frames shown again").

A user of the engine reads its delivery with it: a cut, a set of clips joined into one move, a scene rendered frame by
frame. Whatever made the frames, a frame that shows its figure's picture again is counted, frame by frame, figure by
figure, and named: listed where a list of the holds meant names it with its reason, unlisted where none does. `--gate`
fails on an unlisted one.

The figure is read four ways. By its alpha (frames that carry one: a figure layer, keyed frames, a strip's cells), each
figure apart where there are several: the solid pixels gathered into figures (`MERGE_PX` joins a hand drawn apart from
its body) and each followed from frame to frame by where it stands. A part much smaller than the largest part of its
frame is a speck, not a figure (`_parts`); the largest part is a figure however small. By a key (`--key`, the keyer
`video-frames` keys a clip with). Inside a box of a still background (`--box`). Or by a log of the cell each figure
shows on each frame (`--log`), which reads the cells themselves and no pixel. An opaque frame whose background moves is
not read: its background changes more pixels a frame than a figure's step does; give its alpha, a key or a log.

Two pictures are the same picture by `held_fill`'s rule (`held_fill.same_steps`: at most CHANGED_MAX of the figure's
pixels change by more than PIXEL_SAME, on its thumbnail and as drawn), read on the figure alone, each pair placed where
it fits best first: its alpha's box, then within SEARCH_PX of it, the place that changes the fewest pixels. A camera that
follows the figure moves a held picture on the screen; it is still the picture shown again.

The report is the one a renderer's holds check gives (each figure's `frames`, `holds`, `listed`, `unlisted`,
`unlistedRuns` and `listedRuns`, and the `unlisted` of all of them), and a list of holds meant reads in its format:
`{"holds": [{"scene", "figure", "from", "to", "reason"}]}`, frames from..to inclusive, `scene` and `figure` left out
where the list is for one video or every figure. A strip's own `holds` (the holds its cut means, docs/loop-repair.md
section 7) are listed with it. Each figure also gives its `steps`, its frames read against the frame before, and the
report `inspected`, the figures read on a step or more. A reading that reads no frame against the frame before (no
frame, no figure on any frame, or none on two frames in a row) knows nothing of a picture shown again: it is no reading
but an error (`NothingRead`, its report `inspected` 0), never a reading that found none."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.cli_kwargs import namespace_from_kwargs
from sprite_gen.util.console import utf8_streams
from sprite_gen.video import held_fill as held_fill_mod

REPORT_KIND = "sprite-gen-holds-report"
SEARCH_PX = 3  # a pair is placed within this many pixels of its alpha boxes' corner, where it fits best
ALPHA_MIN = 8  # a pixel of a figure: alpha over this (0..255)
MERGE_PX = 6  # solid pixels this near each other are one figure (a hand drawn apart from its arm)
FIGURE_MIN_PX = 64  # a figure holds at least this many solid pixels, or FIGURE_MIN_PX_CAP of the frame's largest part ...
FIGURE_MIN_PX_CAP = 0.5  # ... where that is fewer: the largest part of a frame is a figure however small ...
FIGURE_MIN_SHARE = 0.02  # ... and this share of the largest part in its frame: less is a speck
KEYS = ("auto", "green", "magenta", "white")  # video-frames' --key
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
LIST_SUFFIX = ".holds.json"
NOTHING = "no frame was read against the frame before"  # the error of a reading that read nothing (NothingRead)


class NothingRead(ValueError):
    """A reading that read no frame against the frame before: no frame, no figure on any frame, or none on two frames
    in a row. It found no picture shown again because it read none; `report` is its report (`inspected` 0), where one
    was made."""

    def __init__(self, why: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(why)
        self.report = report


# --- pictures ---------------------------------------------------------------------------------------------------------

def _premultiplied(image: Image.Image) -> np.ndarray:
    return held_fill_mod.premultiplied(image)


def same_figure(a: Image.Image, b: Image.Image, *, search: int = SEARCH_PX) -> bool:
    """Whether two crops of one figure show the same picture: placed on one canvas by their corners, then within
    `search` px of it where the fewest pixels change, and read as `held_fill.same_steps` reads a step."""
    A, B = _premultiplied(a), _premultiplied(b)
    h, w = max(A.shape[0], B.shape[0]) + 2 * search, max(A.shape[1], B.shape[1]) + 2 * search
    base = np.zeros((h, w, 4), dtype=np.float32)
    base[search:search + A.shape[0], search:search + A.shape[1]] = A
    best: tuple[int, int, int, int] | None = None
    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            moved = np.zeros_like(base)
            moved[search + dy:search + dy + B.shape[0], search + dx:search + dx + B.shape[1]] = B
            changed = int((np.abs(base - moved).max(axis=-1) > held_fill_mod.PIXEL_SAME).sum())
            if best is None or (changed, abs(dy) + abs(dx)) < best[:2]:
                best = (changed, abs(dy) + abs(dx), dy, dx)
    _, _, dy, dx = best  # type: ignore[misc]
    canvas_a = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas_b = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas_a.paste(a.convert("RGBA"), (search, search))
    canvas_b.paste(b.convert("RGBA"), (search + dx, search + dy))
    return held_fill_mod.same_steps([canvas_a, canvas_b])[0]


def _same_in_place(a: Image.Image, b: Image.Image) -> bool:
    """Two crops of one still box: read in place, as `held_fill.same_steps` reads a step."""
    return held_fill_mod.same_steps([a, b])[0]


def _parts(solid: np.ndarray) -> list[tuple[int, int, int, int, np.ndarray]]:
    """The figures of a frame's solid pixels: their pixels gathered within MERGE_PX of each other, each as its box
    (left, top, right, bottom) and its pixels inside that box; specks left out. A speck is a part under FIGURE_MIN_SHARE
    of the frame's largest part, or under FIGURE_MIN_PX, a floor never above FIGURE_MIN_PX_CAP of the largest part:
    a part's size tells a speck from a figure only beside a larger part, so the largest part of a frame is a figure
    however small (the only figure of a frame drawn in a few pixels is read), and so is one half its size or more."""
    from sprite_gen.video.frames import _components, _near

    if not solid.any():
        return []
    ys, starts, stops, labels, _ = _components(_near(solid, MERGE_PX))
    found = []
    for label in np.unique(labels):
        pick = labels == label
        top, bottom = int(ys[pick].min()), int(ys[pick].max()) + 1
        left, right = int(starts[pick].min()), int(stops[pick].max())
        region = np.zeros((bottom - top, right - left), dtype=bool)
        for y, x0, x1 in zip(ys[pick], starts[pick], stops[pick]):
            region[y - top, x0 - left:x1 - left] = True
        own = region & solid[top:bottom, left:right]
        count = int(own.sum())
        if count:
            rows, cols = np.nonzero(own)
            t, b_, l, r = int(rows.min()), int(rows.max()) + 1, int(cols.min()), int(cols.max()) + 1
            found.append((left + l, top + t, left + r, top + b_, own[t:b_, l:r], count))
    if not found:
        return []
    largest = max(f[5] for f in found)
    floor = max(FIGURE_MIN_SHARE * largest, min(FIGURE_MIN_PX, FIGURE_MIN_PX_CAP * largest))
    return [f[:5] for f in found if f[5] >= floor]


def alpha_figures(frames: Sequence[Image.Image], *, name: str = "figure") -> dict[str, dict[int, Image.Image]]:
    """Each figure the frames' alpha draws, followed from frame to frame by where it stands (the nearest figure of the
    frame before, within its own size): its crop on each frame it is drawn on, by the frame's place in `frames`. One
    figure is `name`; several are `name`0, `name`1, ... in the order they are first seen, left to right."""
    tracks: list[dict[str, Any]] = []
    for k, frame in enumerate(frames):
        rgba = frame.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"))
        parts = sorted(_parts(alpha > ALPHA_MIN), key=lambda p: (p[0], p[1]))
        taken: set[int] = set()
        for left, top, right, bottom, own in parts:
            centre = ((left + right) / 2, (top + bottom) / 2)
            near = None
            for i, track in enumerate(tracks):
                if i in taken or track["seen"] != k - 1:
                    continue
                reach = max(track["size"])
                d = ((centre[0] - track["centre"][0]) ** 2 + (centre[1] - track["centre"][1]) ** 2) ** 0.5
                if d <= reach and (near is None or d < near[1]):
                    near = (i, d)
            if near is None:
                tracks.append({"frames": {}})
                near = (len(tracks) - 1, 0.0)
            i = near[0]
            taken.add(i)
            crop = rgba.crop((left, top, right, bottom))
            crop.putalpha(Image.fromarray(np.where(own, np.asarray(crop.getchannel("A")), 0).astype(np.uint8)))
            tracks[i].update(seen=k, centre=centre, size=(right - left, bottom - top))
            tracks[i]["frames"][k] = crop
    if len(tracks) == 1:
        return {name: tracks[0]["frames"]}
    return {f"{name}{i}": t["frames"] for i, t in enumerate(tracks)}


def box_figure(frames: Sequence[Image.Image], box: tuple[int, int, int, int], *,
               name: str = "figure") -> dict[str, dict[int, Image.Image]]:
    """The box x, y, w, h of each frame: the figure over a still background, read in place. A frame whose box holds no
    pixel with alpha over ALPHA_MIN (clear there, or the box past its edge) shows no figure: two such frames are no
    picture shown again."""
    x, y, w, h = box
    crops = {k: f.convert("RGBA").crop((x, y, x + w, y + h)) for k, f in enumerate(frames)}
    return {name: {k: c for k, c in crops.items() if c.getchannel("A").getextrema()[1] > ALPHA_MIN}}


# --- logs -------------------------------------------------------------------------------------------------------------

def _ident(value: Any) -> str | None:
    """A cell as a log names it: a string or a number as written, an object as its values joined by spaces (a sheet
    and its cell), nothing for null."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, dict):
        parts = [p for p in (_ident(v) for v in value.values()) if p is not None]
        return " ".join(parts) if parts else None
    return json.dumps(value, sort_keys=True)


def _log_rows(path: Path) -> list[dict[str, Any]]:
    """A log's lines, one a frame from 0; a log of none is nothing to read (`NothingRead`)."""
    lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    if not rows:
        raise NothingRead(f"{path}: no frame logged")
    return rows


def log_figures(path: Path, keys: Sequence[str]) -> dict[str, dict[int, str]]:
    """The cell each figure shows on each frame, from a log of one JSON object a line, one line a frame from 0: each of
    `keys` holds a figure's cell (`_ident`), or a list of figures (named by the key less its plural s, and their place
    in it: `figures` gives figure0, figure1, ...)."""
    return _log_cells(_log_rows(path), keys, path)


def _log_cells(rows: Sequence[dict[str, Any]], keys: Sequence[str], path: Path) -> dict[str, dict[int, str]]:
    if not keys:
        raise ValueError(f"{path}: name the keys that hold a figure's cell with --log-figure KEY (the first line holds: "
                         f"{', '.join(map(str, rows[0]))})")
    missing = [key for key in keys if not any(key in row for row in rows)]
    if missing:
        raise ValueError(f"{path}: no line holds {', '.join(missing)} (the first line holds: {', '.join(map(str, rows[0]))})")
    out: dict[str, dict[int, str]] = {}
    for k, row in enumerate(rows):
        for key in keys:
            value = row.get(key)
            if isinstance(value, list):
                base = key[:-1] if key.endswith("s") and len(key) > 1 else key
                for i, item in enumerate(value):
                    ident = _ident(item)
                    if ident is not None:
                        out.setdefault(f"{base}{i}", {})[k] = ident
                continue
            ident = _ident(value)
            if ident is not None:
                out.setdefault(key, {})[k] = ident
    return out


# --- lists of holds meant ---------------------------------------------------------------------------------------------

def read_lists(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Every hold the lists name, each with its file: a file, or every *.holds.json under a folder. A hold without a
    reason, or whose frames are not from <= to, refuses the list."""
    files: list[Path] = []
    for path in paths:
        path = Path(path)
        files += sorted(path.rglob(f"*{LIST_SUFFIX}")) if path.is_dir() else [path]
    out = []
    for file in files:
        data = json.loads(file.read_text(encoding="utf-8"))
        for h in (data or {}).get("holds") or []:
            if not isinstance(h.get("reason"), str) or not h["reason"].strip():
                raise ValueError(f"{file}: a hold without a reason")
            ints = [h.get(k) for k in ("from", "to")]
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in ints) or ints[0] > ints[1]:
                raise ValueError(f"{file}: a hold's from..to (two whole frame numbers, from <= to)")
            out.append({**h, "file": str(file)})
    return out


def for_scene(entries: Sequence[dict[str, Any]], scene: str | None) -> list[dict[str, Any]]:
    """The holds of `scene`, and those that name none. A list that names scenes is read for one, named."""
    named = sorted({str(e["scene"]) for e in entries if e.get("scene") is not None})
    if named and scene is None:
        raise ValueError(f"the list names scenes ({', '.join(named)}): pass --scene to read the holds of one")
    return [e for e in entries if e.get("scene") is None or e["scene"] == scene]


def strip_holds(meta: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    """A strip's own holds (the holds its cut means), as a list names them: every figure, its cells inclusive."""
    out = []
    for h in meta.get("holds") or []:
        reason = str(h.get("reason") or h.get("kind") or "").strip()
        if not reason or not isinstance(h.get("from"), int) or not isinstance(h.get("to"), int):
            raise ValueError(f"{path}: a hold of the strip without a reason or its cells")
        out.append({"from": h["from"], "to": h["to"], "reason": reason, "kind": h.get("kind"), "file": str(path)})
    return out


# --- the reading ------------------------------------------------------------------------------------------------------

def _runs(ns: Sequence[int]) -> list[dict[str, int]]:
    out: list[dict[str, int]] = []
    for n in ns:
        if out and out[-1]["to"] == n - 1:
            out[-1]["to"] = n
        else:
            out.append({"from": n, "to": n})
    return out


def inspect(figures: dict[str, dict[int, Any]], *, first: int = 0, start: int | None = None,
            listed: Sequence[dict[str, Any]] = (), wrap: bool = False,
            same: Callable[[Any, Any], bool] | None = None) -> dict[str, Any]:
    """The holds of each figure: frame n (`first` + its place) holds where the figure is shown on it and on the frame
    before and both show one picture (`same`: `same_figure` on crops, equal names on a log's cells); with `wrap`, the
    first frame against the last, as a loop plays. Frames before `start` are read only as the frame before. Each hold
    is listed where a hold of `listed` names its figure (or none) and its frame. The report is a renderer's holds
    check's: each figure's frames, holds, listed, unlisted and their runs, and the unlisted of all of them; and each
    figure's steps (its frames read against the frame before) and the figures `inspected` (read on a step or more).
    None inspected is no reading: `NothingRead`, with the report."""
    start = first if start is None else start
    table: dict[str, Any] = {}
    unlisted = 0
    for name, shown in figures.items():
        order = sorted(shown)
        compare = same or (lambda a, b: a == b if isinstance(a, str) else same_figure(a, b))
        began: dict[int, int] = {}
        held: list[dict[str, Any]] = []
        steps = 0
        for k in order:
            began[k] = k
            if k - 1 not in shown:
                continue
            n = first + k
            if n >= start:
                steps += 1
            if compare(shown[k - 1], shown[k]):
                began[k] = began[k - 1]
                if n >= start:
                    held.append({"n": n, "drawing": _drawing(shown[k], first + began[k])})
        if wrap and len(order) > 1 and order[0] == 0:
            steps += 1
            if compare(shown[order[-1]], shown[0]):
                held.insert(0, {"n": first, "drawing": _drawing(shown[0], first + began[order[-1]])})
        for h in held:
            why = next((e for e in listed if (e.get("figure") is None or e["figure"] == name) and e["from"] <= h["n"] <= e["to"]),
                       None)
            h["listed"] = why["reason"] if why else None
        bad = [h for h in held if not h["listed"]]
        unlisted += len(bad)

        def group(hs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{**r, "drawings": list(dict.fromkeys(h["drawing"] for h in hs if r["from"] <= h["n"] <= r["to"]))}
                    for r in _runs([h["n"] for h in hs])]

        good = [h for h in held if h["listed"]]
        table[name] = {
            "frames": sum(1 for k in order if first + k >= start), "steps": steps, "holds": len(held),
            "listed": len(good), "unlisted": len(bad), "unlistedRuns": group(bad),
            "listedRuns": [{**r, "reason": next(h["listed"] for h in good if h["n"] == r["from"])} for r in group(good)],
        }
    report = {"unlisted": unlisted, "inspected": sum(1 for row in table.values() if row["steps"]), "table": table}
    if not report["inspected"]:
        raise NothingRead("no figure is shown on two frames in a row" if any(row["frames"] for row in table.values())
                          else "no figure is shown on any frame read", report)
    return report


def _drawing(item: Any, began: int) -> str:
    """What a hold shows again: the cell a log names, or the frame its picture was first shown on."""
    return item if isinstance(item, str) else f"frame {began}"


# --- sources ----------------------------------------------------------------------------------------------------------

def _frames_range(value: str | None) -> tuple[int, int | None] | None:
    if value is None:
        return None
    lo, sep, hi = value.partition("-")
    try:
        a, b = int(lo), (int(hi) if hi else None)
    except ValueError:
        raise ValueError(f"--frames {value!r}: FROM-TO, two frame numbers from 0 (TO may be left out)") from None
    if not sep or a < 0 or (b is not None and b < a):
        raise ValueError(f"--frames {value!r}: FROM-TO, two frame numbers from 0, FROM <= TO")
    return a, b


def _box(value: str | None) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    try:
        x, y, w, h = (int(v) for v in value.split(","))
    except ValueError:
        raise ValueError(f"--box {value!r}: x,y,w,h in whole pixels") from None
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise ValueError(f"--box {value!r}: x,y >= 0 and w,h > 0")
    return x, y, w, h


def _video_frames(clip: Path, folder: Path) -> list[Path]:
    from sprite_gen.video import frames as frames_mod

    try:
        return frames_mod.extract(clip, folder, frames_mod.probe(clip)["stream_index"])
    except SystemExit as exc:
        raise ValueError(str(exc)) from None


def _keyed(files: list[Path], folder: Path, key: str) -> list[Path]:
    from sprite_gen.video import frames as frames_mod

    try:
        frames_mod.key_frames(files, folder, key=key, check_edges=False, spill="small", decontam="off")
    except SystemExit as exc:
        raise ValueError(str(exc)) from None
    return [folder / f.name for f in files]


def load(source: Path, *, key: str | None, work: Path, frames: tuple[int, int | None] | None
         ) -> tuple[list[Image.Image], int, dict[str, Any]]:
    """The frames of `source` (a folder of PNG frames, a video, or a strip sidecar), keyed by `key` where given: the
    frames read (`frames` FROM-TO and the one before it), the number of the first, and what was read. A source with no
    frame, or none from FROM on, is nothing to read (`NothingRead`)."""
    source = Path(source).expanduser().resolve()
    meta: dict[str, Any] = {}
    if source.is_dir():
        files = sorted(source.glob("*.png"))
        if not files:
            raise NothingRead(f"{source}: no *.png frames")
        kind = "frames"
    elif source.name.endswith(".strip.json"):
        meta = json.loads(source.read_text(encoding="utf-8"))
        sheet = Image.open(source.with_name(source.name[: -len(".json")] + ".png")).convert("RGBA")
        n, w, h = (int(meta[k]) for k in ("frames", "w", "h"))
        if sheet.size != (n * w, h):
            raise ValueError(f"{source}: the strip's size disagrees with its sidecar")
        if key is not None:
            raise ValueError("--key: a strip's cells carry their alpha")
        cells = [sheet.crop((k * w, 0, (k + 1) * w, h)) for k in range(n)]
        first = _window(len(cells), frames, source, "cells")
        hi = frames[1] if frames else None
        return cells[first:(hi + 1 if hi is not None else None)], first, {"kind": "strip", "meta": meta}
    elif source.suffix.lower() in VIDEO_SUFFIXES:
        files = _video_frames(source, work / "raw")
        kind = "video"
    else:
        raise ValueError(f"{source}: a folder of PNG frames, a video ({', '.join(VIDEO_SUFFIXES)}) or a *.strip.json")
    first = _window(len(files), frames, source, "frames")
    hi = frames[1] if frames else None
    files = files[first:(hi + 1 if hi is not None else None)]
    if key is not None:
        files = _keyed(files, work / "keyed", key)
    return [Image.open(f).convert("RGBA") for f in files], first, {"kind": kind}


def _window(total: int, frames: tuple[int, int | None] | None, source: Path, unit: str) -> int:
    """The first frame read of `total` (the one before FROM), where a frame from FROM on is there to read; none is
    nothing to read (`NothingRead`)."""
    lo, hi = frames or (0, None)
    if lo >= total:
        raise NothingRead(f"{source}: no frame in --frames {lo}-{'' if hi is None else hi}: it has {total} {unit}"
                          f"{f' (0-{total - 1})' if total else ''}")
    return max(0, lo - 1)


def _opaque(frames: Sequence[Image.Image]) -> bool:
    return all(f.getchannel("A").getextrema()[0] == 255 for f in frames)


def read(*, source: Path | None = None, key: str | None = None, box: str | None = None, log: Path | None = None,
         log_figures_: Sequence[str] = (), lists: Sequence[Path] = (), scene: str | None = None,
         frames: str | None = None, figure: str = "figure") -> dict[str, Any]:
    """The report of `inspect-holds` (see the module): the source read one of the four ways, its holds listed or not.
    A reading that reads no frame against the frame before raises `NothingRead`, its report `inspected` 0, saying why."""
    ways = [w for w, given in (("--key", key), ("--box", box), ("--log", log)) if given is not None]
    if len(ways) > 1:
        raise ValueError(f"{' and '.join(ways)}: give one way to the figure")
    if (source is None) == (log is None):
        raise ValueError("give --source (frames, a video or a strip) or --log (a log of the cells), one of them")
    if key is not None and key not in KEYS:
        raise ValueError(f"--key {key!r}: one of {', '.join(KEYS)}")
    span = _frames_range(frames)
    in_box = _box(box)
    entries = for_scene(read_lists(lists), scene)
    report: dict[str, Any] = {"kind": REPORT_KIND, "scene": scene,
                              "rule": {"pixel_same": held_fill_mod.PIXEL_SAME, "changed_max": held_fill_mod.CHANGED_MAX,
                                       "search_px": SEARCH_PX},
                              "lists": sorted({e["file"] for e in entries}),
                              "source": str(Path(log if log is not None else source).expanduser().resolve()),  # type: ignore[arg-type]
                              "read_by": "log" if log is not None else "box" if box is not None else "key" if key else "alpha"}
    wrap = False
    size = None
    try:
        if log is not None:
            path = Path(log).expanduser().resolve()
            rows = _log_rows(path)
            first = _window(len(rows), span, path, "lines")
            last = len(rows) - 1 if span is None or span[1] is None else min(span[1], len(rows) - 1)
            figures: dict[str, dict[int, Any]] = {
                name: {k - first: v for k, v in shown.items() if first <= k <= last}
                for name, shown in _log_cells(rows, log_figures_, path).items()}
            same = None
            count = last - first + 1
        else:
            with tempfile.TemporaryDirectory(prefix="sprite-gen-holds-") as work:
                loaded, first, what = load(Path(source), key=key, work=Path(work), frames=span)  # type: ignore[arg-type]
            count, size = len(loaded), loaded[0].size
            if what["kind"] == "strip":
                entries = entries + strip_holds(what["meta"], Path(source))
                wrap = bool(what["meta"].get("loop", True)) and span is None
            if in_box is not None:
                figures, same = box_figure(loaded, in_box, name=figure), _same_in_place
            else:
                if key is None and _opaque(loaded):
                    raise ValueError("the frames are opaque: a figure is read by its alpha — give --key (a keyed clip), "
                                     "--box x,y,w,h (a still background) or --log (the cells a renderer logged); an opaque "
                                     "frame whose background moves is not read")
                figures, same = alpha_figures(loaded, name=figure), None
    except NothingRead as exc:
        report.update(first_frame=span[0] if span else 0, frames=0, wrap=False, unlisted=0, inspected=0, table={})
        raise NothingRead(f"{NOTHING}: {exc}", report) from None
    start = span[0] if span else first
    report.update(first_frame=start, frames=count - (start - first), wrap=wrap)
    try:
        report.update(inspect(figures, first=first, start=start, listed=entries, wrap=wrap, same=same))
    except NothingRead as exc:
        report.update(exc.report or {})
        why = _why_none(report, first, first + count - 1, key=key, box=in_box, size=size, keys=log_figures_)
        raise NothingRead(f"{NOTHING}: {why}", report) from None
    return report


def _why_none(report: dict[str, Any], first: int, last: int, *, key: str | None, box: tuple[int, int, int, int] | None,
              size: tuple[int, int] | None, keys: Sequence[str]) -> str:
    """Why a reading of frames `first`..`last` read no frame against the frame before, in its way's words."""
    start = report["first_frame"]
    asked = f"frame {start}" if start == last else f"frames {start}-{last}"
    if first == last:
        return f"one frame read ({asked}), and no frame before it to read it against"
    if any(row["frames"] for row in report["table"].values()):
        if report["read_by"] == "log":
            return f"the log names a figure's cell on no two lines in a row of {asked}"
        return (f"no figure is shown on two frames in a row of {asked} (a figure is followed from a frame to the next by "
                "where it stands, within its own size)")
    solid = f"pixel with alpha over {ALPHA_MIN}"
    if report["read_by"] == "log":
        return f"the log names no cell for {', '.join(keys)} on {asked}"
    if box is not None:
        w, h = size or (0, 0)
        return f"the box {','.join(map(str, box))} holds no {solid} on {asked} (the frames are {w}x{h})"
    if key is not None:
        return f"no figure on {asked} once keyed with --key {key} (no {solid})"
    return f"no figure on {asked} (no {solid})"


# --- the command ------------------------------------------------------------------------------------------------------

def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", type=Path, help="a folder of PNG frames (in name order, the first frame 0), a video "
                        "(mp4, mov, webm, mkv) or a video-loop strip sidecar (*.strip.json, its cells the frames)")
    way = parser.add_mutually_exclusive_group()
    way.add_argument("--key", choices=KEYS, help="key each frame as video-frames keys a clip, and read the figure by the "
                     "alpha it gives (a clip filmed on a key colour)")
    way.add_argument("--box", help="x,y,w,h: read the box of each frame in place (a figure over a still background)")
    way.add_argument("--log", type=Path, help="a log of the cell each figure shows on each frame, one JSON object a "
                     "line, one line a frame from 0 (a renderer's log); read in place of --source")
    parser.add_argument("--log-figure", action="append", default=[], help="with --log: a key of each line that holds a "
                        "figure's cell (repeat for each figure); a list holds several (figures gives figure0, figure1, ...)")
    parser.add_argument("--holds", action="append", type=Path, default=[], help="a list of the holds meant, "
                        '{"holds": [{"scene", "figure", "from", "to", "reason"}]}, frames inclusive; or a folder, every '
                        "*.holds.json under it (repeat for more)")
    parser.add_argument("--scene", help="the scene the video is, for a list whose holds name scenes")
    parser.add_argument("--figure", default="figure", help="the name of the figure the frames show (several: the name "
                        "and 0, 1, ... in the order they are first seen, left to right); default figure")
    parser.add_argument("--frames", help="FROM-TO: read these frames only (and the one before FROM, to compare with)")
    parser.add_argument("--gate", action="store_true", help="exit 1 where any hold is on no list")
    parser.add_argument("--report", type=Path, help="also write the report here (JSON)")


def run(**kwargs: object) -> int:
    """Read once; print the report. 0, or 1 under --gate with a hold on no list; 2 on an error, a reading that read
    nothing among them (its report printed and written, `inspected` 0, with or without --gate)."""
    parser = argparse.ArgumentParser(add_help=False)
    add_arguments(parser)
    args = namespace_from_kwargs(parser, kwargs)
    nothing: NothingRead | None = None
    try:
        try:
            report = read(source=args.source, key=args.key, box=args.box, log=args.log, log_figures_=args.log_figure,
                          lists=args.holds, scene=args.scene, frames=args.frames, figure=args.figure)
        except NothingRead as exc:
            if exc.report is None:
                raise
            report, nothing = exc.report, exc
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.report is not None:
            out = Path(args.report).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(out, payload)
    except (ValueError, OSError) as exc:
        print(f"inspect-holds: {exc}", file=sys.stderr)
        return 2
    print(payload, end="")
    if nothing is not None:
        print(f"inspect-holds: {nothing}", file=sys.stderr)
        return 2
    if report["unlisted"]:
        runs = "; ".join(f"{name} {', '.join(str(r['from']) if r['from'] == r['to'] else f'{r['from']}-{r['to']}' for r in row['unlistedRuns'])}"
                         for name, row in report["table"].items() if row["unlisted"])
        many = report["unlisted"] > 1
        print(f"inspect-holds: {report['unlisted']} frame{'s' if many else ''} show{'' if many else 's'} the picture of the "
              f"frame before and {'are' if many else 'is'} on no list: {runs}", file=sys.stderr)
        if args.gate:
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog="inspect-holds", description=__doc__.split("\n\n")[0])
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
