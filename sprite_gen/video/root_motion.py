# SPDX-License-Identifier: Apache-2.0
"""Root motion: where the body is in each cell of a cut played once, what it stands on and its air (`read`,
`sidecar`); and the air given a length, a gravity and a speed of its own (`retime`). docs/video-pipeline.md, "Root
motion".

The root is the subject's mass centre: the mean of its pixels (alpha over 0), the point inspect-motion reads as a
frame's `centroid_px` (`qa.motion.centroid`). No body part is named. In the air it is the point that falls on a parabola
under gravity whatever the arms and legs do, so it is the point a game moves: a cell is drawn with its root on the
body's place. A frame stands on the ground where its lowest body row is within nonloop.GROUND_PX of the clip's floor,
the lowest row any frame of the clip reaches (`nonloop.on_floor`, the rule the strip's standing height reads). A
contact the floor does not show, a hand on a wall the clip does not draw, is named by the caller over frames of the clip
in inspect-motion's spans (`--contact hand=12:20`, `qa.motion.contact_spans`). The air is each run of frames touching
nothing: its take-off is the frame before it, its landing the frame after, its seconds those between the two, and its
apex how far the root rises over the take-off's.

`retime` gives each air with a take-off and a landing a length (`air`, seconds), a gravity (`gravity`) and a speed
along (`speed`), each the clip's own where not given. The cut is made again at its rate: the frames up to the take-off
and from the landing at the clip's pace, the air's cells spread evenly over it, each showing the clip's pose at the same
share of the clip's air, made between the two frames it falls between where it falls between them (held_fill.at_times,
the cells the 3.8.0 fill makes). Each air cell is then moved whole, by whole pixels, until its root stands on the
parabola from the take-off's root to the landing's; the landing and every cell after it is moved along by as much
further as the air now carries the body, so a planted foot stays planted. The take-off and the landing are the frames
as filmed, and so is every cell up to the take-off.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.qa import motion as qa_motion
from sprite_gen.video import held_fill as held_fill_mod
from sprite_gen.video import nonloop
from sprite_gen.video import rife as rife_mod

KINDS = ("once", "once-return", "one-shot")  # the cuts played once that carry a body (not an effect's lifetime)
RETIME_KEYS = ("gravity", "air", "speed")
GROUND = "ground"
SNAP = held_fill_mod.SNAP  # a time within this of a frame is that frame


def parse_retime(text: str) -> dict[str, float]:
    """`gravity=G,air=T,speed=V`, any of them, each once: metres a second squared, seconds, metres a second along."""
    out: dict[str, float] = {}
    for part in text.split(","):
        key, eq, value = (s.strip() for s in part.partition("="))
        if not eq or key not in RETIME_KEYS or key in out:
            raise ValueError(f"--air-retime {text!r}: KEY=VALUE pairs of {', '.join(RETIME_KEYS)}, each once "
                             "(gravity=9.81,air=0.6,speed=4)")
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"--air-retime {key}={value}: not a number") from None
        if not math.isfinite(number) or number < 0 or (number == 0 and key != "speed"):
            raise ValueError(f"--air-retime {key}={value}: " + ("0 or more" if key == "speed" else "more than 0"))
        out[key] = number
    return out


def parse_contacts(values: Sequence[str], frames: int) -> list[tuple[str, list[list[int]]]]:
    """`NAME=SPANS` (`hand=12:20`): a contact the floor does not show, over zero-based frames of the clip, in
    inspect-motion's spans (`qa.motion.contact_spans`). `ground` is the floor's own."""
    out: list[tuple[str, list[list[int]]]] = []
    for value in values:
        name, eq, spans = (s.strip() for s in value.partition("="))
        if not eq or not name or name == GROUND or any(name == n for n, _ in out):
            raise ValueError(f"--contact {value!r}: NAME=SPANS, a name of its own (not {GROUND!r}) and the clip's frames "
                             "it touches through, e.g. hand=12:20")
        try:
            out.append((name, qa_motion.contact_spans(spans, frames)))
        except ValueError as exc:
            raise ValueError(f"--contact {value!r}: {exc} (the clip has {frames} frames, numbered from 0)") from None
    return out


def clip_floor(files: Sequence[Path]) -> int | None:
    """The clip's floor: the lowest body row any of its frames reaches (`nonloop.body_box`), as filmed."""
    bottoms = [box[3] for f in files if (box := nonloop.body_box(Image.open(f).convert("RGBA"))) is not None]
    return max(bottoms) if bottoms else None


def root(frame: Image.Image) -> list[float] | None:
    """A frame's root: its subject's mass centre, [x, y] px (`qa.motion.centroid`)."""
    return qa_motion.centroid(np.asarray(frame.getchannel("A")) > 0)


def touching(frames: Sequence[Image.Image], *, floor: int | None, times: Sequence[float],
             declared: Sequence[tuple[str, list[list[int]]]] = ()) -> list[list[str]]:
    """What each frame touches: the ground where its body stands on the floor, and each declared contact whose span
    holds its time (in frames of the clip)."""
    out = []
    for frame, t in zip(frames, times):
        box = nonloop.body_box(frame)
        on = [GROUND] if floor is not None and box is not None and nonloop.on_floor(box, floor) else []
        on += [name for name, spans in declared if any(a - SNAP <= t <= b + SNAP for a, b in spans)]
        out.append(on)
    return out


def reading(frames: Sequence[Image.Image], *, floor: int | None, times: Sequence[float],
            declared: Sequence[tuple[str, list[list[int]]]] = ()) -> tuple[list[list[float] | None], list[list[str]]]:
    """Each frame's root and what it touches (`root`, `touching`)."""
    return [root(f) for f in frames], touching(frames, floor=floor, times=times, declared=declared)


def _runs(flags: Sequence[bool]) -> list[list[int]]:
    """Each run of true flags: [first, last]."""
    found: list[list[int]] = []
    for k, flag in enumerate(flags):
        if flag and found and found[-1][1] == k - 1:
            found[-1][1] = k
        elif flag:
            found.append([k, k])
    return found


def read(roots: Sequence[list[float] | None], touch: Sequence[list[str]], seconds: Sequence[float]) -> dict[str, Any]:
    """The contacts (each name's runs of frames) and the air (each run of frames touching nothing: its take-off and
    landing frames, the seconds between them, and its apex, how far the root rises over the take-off's, px up)."""
    n = len(touch)
    names = list(dict.fromkeys(name for on in touch for name in on))
    contacts = [{"by": name, "cells": run} for name in names for run in _runs([name in on for on in touch])]
    air = []
    for first, last in _runs([not on for on in touch]):
        takeoff = first - 1 if first > 0 else None
        landing = last + 1 if last < n - 1 else None
        span: dict[str, Any] = {"cells": [first, last], "takeoff": takeoff, "landing": landing,
                                "seconds": round(seconds[landing] - seconds[takeoff], 4) if None not in (takeoff, landing) else None}
        ys = [(roots[k][1], k) for k in range(first, last + 1) if roots[k] is not None]
        if takeoff is not None and roots[takeoff] is not None and ys:
            top, at = min(ys)
            span.update(apex_px=round(roots[takeoff][1] - top, 2), apex_cell=at)
        air.append(span)
    return {"contacts": sorted(contacts, key=lambda c: (c["cells"][0], c["by"])), "air": air}


def sidecar(frames: Sequence[Image.Image], *, rect: Sequence[int] | None, size: tuple[int, int], floor: int | None,
            seconds: Sequence[float], touch: Sequence[list[str]], meters_per_px: float | None) -> dict[str, Any]:
    """The strip's `root_motion`: each cell's root in cell px from the cell's top-left (draw the cell with that point on
    the body's place), the floor's row, the contacts and the air in cells; with `meters_per_px` (of the frames as cut)
    the roots in metres from the first cell's, up positive, and each apex in metres. `frames` are the cells' frames as
    cut, `rect` the source rectangle the strip crops them to, `size` a cell's (w, h)."""
    if rect is None:
        return {"why": "the cells are each moved to a foot line of their own (--anchor feet): no one rectangle maps a frame to its cell"}
    left, top, right, bottom = rect
    sx, sy = size[0] / (right - left), size[1] / (bottom - top)
    at = [root(f) for f in frames]
    measured = read(at, touch, seconds)
    for span in measured["air"]:
        if "apex_px" in span:
            span["apex_px"] = round(span["apex_px"] * sy, 2)
    out: dict[str, Any] = {
        "point": "mass-centre",
        "root": [None if r is None else [round((r[0] - left) * sx, 2), round((r[1] - top) * sy, 2)] for r in at],
        "ground_y": None if floor is None else round((floor - top) * sy, 2),
        **measured,
    }
    if meters_per_px is not None:
        per_cell = meters_per_px / sy
        out["meters_per_px"] = round(per_cell, 8)
        first = next((r for r in out["root"] if r is not None), None)
        out["root_m"] = [None if r is None or first is None else
                         [round((r[0] - first[0]) * per_cell, 4), round((first[1] - r[1]) * per_cell, 4)] for r in out["root"]]
        for span in out["air"]:
            if "apex_px" in span:
                span["apex_m"] = round(span["apex_px"] * per_cell, 4)
    return out


def said(record: dict[str, Any]) -> list[str]:
    """Each air of a cut as a person is told it: its seconds and its apex."""
    lines = []
    for span in record.get("air", []):
        if span.get("seconds") is None:
            lines.append(f"cells {span['cells'][0]}-{span['cells'][1]} in the air, no "
                         f"{'take-off' if span['takeoff'] is None else 'landing'} in the cut")
            continue
        apex = f"{span['apex_m']:.2f} m" if "apex_m" in span else f"{span['apex_px']:g} px"
        lines.append(f"air {span['seconds']:.2f} s, apex {apex} (cells {span['cells'][0]}-{span['cells'][1]})")
    return lines


def _gravity(ys: Sequence[float], a: int, b: int, fps: float) -> float | None:
    """The clip's own gravity over an air from frame `a` to `b` (px a second squared, down): the least squares fit of
    the root's rise over the line from take-off to landing to g·t·(T - t)/2. None where the root is unread."""
    T = (b - a) / fps
    num = den = 0.0
    for k in range(a + 1, b):
        t = (k - a) / fps
        s = t * (T - t) / 2
        num += (ys[a] + (ys[b] - ys[a]) * t / T - ys[k]) * s
        den += s * s
    return num / den if den > 0 else None


def apex(rise: float, gravity: float, seconds: float) -> float:
    """The highest a root rises over its take-off's on the parabola that ends `rise` px higher after `seconds` under
    `gravity` (px a second squared): g·T²/8 where it lands at its own height."""
    t = min(seconds, max(0.0, seconds / 2 + rise / (gravity * seconds)))
    return rise * t / seconds + gravity * t * (seconds - t) / 2


def _pieces(L: int, spans: Sequence[dict[str, Any]], *, fps: float, rate: float,
            airs: Sequence[float]) -> list[tuple[float, float, int, int | None]]:
    """The cut as pieces (from frame, to frame, cells, which air or None): the ground at the clip's pace, each air in
    as many cells as its new seconds hold at `rate`."""
    pieces: list[tuple[float, float, int, int | None]] = []
    at = 0
    for which, span in enumerate(spans):
        a, b = span["takeoff"], span["landing"]
        if a > at:
            pieces.append((at, a, max(1, round((a - at) * rate / fps)), None))
        pieces.append((a, b, max(1, round(airs[which] * rate)), which))
        at = b
    if L - 1 > at:
        pieces.append((at, L - 1, max(1, round((L - 1 - at) * rate / fps)), None))
    return pieces


def retime(frames: list[Image.Image], interpolate: rife_mod.Interpolate, *, fps: float, rate: float, floor: int | None,
           times: Sequence[float], declared: Sequence[tuple[str, list[list[int]]]], asked: dict[str, float],
           meters_per_px: float | None, facing: str = "right", judge_scale: float = held_fill_mod.JUDGE_SCALE
           ) -> tuple[list[Image.Image], dict[str, Any], dict[str, Any] | None]:
    """The cut with each air that has a take-off and a landing retimed (see the module), at `rate` cells a second.
    `times` are the frames' times in frames of the clip (for the declared contacts); `judge_scale` the share of the
    frames' size the game shows the cells at, where a frame made and refused for its outline as cut is taken if its
    line keeps its ink (held_fill.at_times). Returns the frames, the record, and
    what the cut's reading needs where it was retimed (None where not): each cell's time in frames of the cut (`times`),
    the frame each cell is, None where it was made (`source_frames`, held_fill.at_times'), what each cell touches (`touch`), and
    the canvas the cells were moved on (`offset`, where the frames' (0, 0) is)."""
    L = len(frames)
    roots, touch = reading(frames, floor=floor, times=times, declared=declared)
    before = read(roots, touch, [k / fps for k in range(L)])
    record: dict[str, Any] = {"asked": dict(asked), "applied": False, "before": before["air"]}
    if meters_per_px is not None:
        record["meters_per_px"] = meters_per_px
    spans = [s for s in before["air"] if s["seconds"] is not None]
    if not spans:
        record["why"] = ("no air in the cut" if not before["air"] else
                         "no air with both its take-off and its landing in the cut")
        return frames, record, None
    if any(roots[s["takeoff"]] is None or roots[s["landing"]] is None for s in spans):
        record["why"] = "a take-off or a landing frame shows no body"
        return frames, record, None
    plans = []
    for span in spans:
        a, b = span["takeoff"], span["landing"]
        T0 = (b - a) / fps
        ys = [r[1] if r is not None else math.nan for r in roots]
        g0 = _gravity(ys, a, b, fps)
        g = asked["gravity"] / meters_per_px if "gravity" in asked else g0
        if g is None or not g > 0:
            record["why"] = (f"the clip's own air (frames {a}-{b}) does not rise over the line from its take-off to its "
                             "landing (no gravity to read): give gravity=")
            return frames, record, None
        along = roots[b][0] - roots[a][0]
        sign = (1 if along > 0 else -1) if abs(along) >= 1 else (1 if facing == "right" else -1)
        speed = asked["speed"] / meters_per_px if "speed" in asked else abs(along) / T0
        T1 = asked.get("air", T0)
        plans.append({"a": a, "b": b, "T0": T0, "g0": g0, "g": g, "speed": speed, "sign": sign, "T1": T1})
    pieces = _pieces(L, spans, fps=fps, rate=rate, airs=[p["T1"] for p in plans])
    at, spacing, piece_of = [0.0], [1.0], [None]
    for start, end, cells, which in pieces:
        for j in range(1, cells + 1):
            at.append(start + (end - start) * j / cells)
            spacing.append((end - start) / cells)
            piece_of.append((which, j, cells))
    made_frames, made = held_fill_mod.at_times(frames, interpolate, at, ring=False, spacing=spacing, judge_scale=judge_scale)
    # Each cell moved: none up to the first take-off; an air cell onto its parabola; from a landing on, along.
    moves: list[tuple[int, int]] = []
    along_px = 0
    for k, of in enumerate(piece_of):
        which = None if of is None else of[0]
        if which is not None and of[1] == 1:
            plans[which].update(moved_from=along_px, cells=of[2], seconds=of[2] / rate, first_cell=k - 1)
        if which is None or of[1] == of[2]:
            if which is not None:
                # the landing: as far along as the air carried the body from its take-off
                p = plans[which]
                xa = roots[p["a"]][0] + p["moved_from"]
                along_px = round(xa + p["sign"] * p["speed"] * p["seconds"] - roots[p["b"]][0])
                p["moved_px"] = along_px - p["moved_from"]
            moves.append((along_px, 0))
            continue
        p = plans[which]
        T, t, w = p["seconds"], of[1] / rate, of[1] / of[2]
        xa, ya = roots[p["a"]][0] + p["moved_from"], roots[p["a"]][1]
        x = xa + p["sign"] * p["speed"] * T * w
        y = ya + (roots[p["b"]][1] - ya) * w - p["g"] * t * (T - t) / 2
        here = root(made_frames[k])
        moves.append((0, 0) if here is None else (round(x - here[0]), round(y - here[1])))
    boxes = [nonloop.body_box(f) for f in made_frames]
    W, H = frames[0].size
    lo_x = min([0] + [b[0] + m[0] for b, m in zip(boxes, moves) if b is not None])
    lo_y = min([0] + [b[1] + m[1] for b, m in zip(boxes, moves) if b is not None])
    hi_x = max([W] + [b[2] + m[0] for b, m in zip(boxes, moves) if b is not None])
    hi_y = max([H] + [b[3] + m[1] for b, m in zip(boxes, moves) if b is not None])
    offset, size = (-lo_x, -lo_y), (hi_x - lo_x, hi_y - lo_y)
    out = []
    for f, (dx, dy) in zip(made_frames, moves):
        if (dx, dy) == (0, 0) and offset == (0, 0) and size == (W, H):
            out.append(f)
            continue
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.paste(f, (offset[0] + dx, offset[1] + dy))
        out.append(canvas)
    cell_touch = []
    for k, of in enumerate(piece_of):
        if of is not None and of[0] is not None and of[1] < of[2]:
            cell_touch.append([])
        else:
            cell_touch.append(touch[min(L - 1, round(at[k]))])
    airs = []
    for span, p in zip(spans, plans):
        rise = roots[p["a"]][1] - roots[p["b"]][1]
        row = {"cells": [p["first_cell"] + 1, p["first_cell"] + p["cells"] - 1],
               "takeoff": p["first_cell"], "landing": p["first_cell"] + p["cells"],
               "frames": [p["a"], p["b"]], "seconds_before": round(p["T0"], 4), "seconds": round(p["seconds"], 4),
               "apex_before_px": span.get("apex_px"), "apex_px": round(apex(rise, p["g"], p["seconds"]), 2),
               "gravity_px": round(p["g"], 2), "speed_px": round(p["speed"], 2), "moved_along_px": p["moved_px"]}
        if p["g0"] is not None:
            row["gravity_before_px"] = round(p["g0"], 2)
        if meters_per_px is not None:
            row.update(apex_m=round(row["apex_px"] * meters_per_px, 4), gravity=round(p["g"] * meters_per_px, 4),
                       speed=round(p["speed"] * meters_per_px, 4))
        if "air" in asked and abs(p["seconds"] - asked["air"]) > 1e-9:
            row["asked_seconds"] = asked["air"]
        airs.append(row)
    same = held_fill_mod.same_steps(out)
    record.update(applied=True, rate=rate, air=airs, made=made["made"], shown_again=made["shown_again"],
                  canvas_offset=list(offset), **{k: made[k] for k in ("judge_scale",) if k in made},
                  **held_fill_mod.reading(same))
    record["frames"] = made["frames"]
    return out, record, {"times": at, "source_frames": made["source_frames"], "touch": cell_touch, "offset": offset}


def public(record: dict[str, Any]) -> dict[str, Any]:
    """The retime as the strip metadata carries it: what was asked, each air before and after, the cells made and the
    cells shown again, the poses over the cells — not how each frame was made."""
    return {k: v for k, v in record.items() if k != "frames"}
