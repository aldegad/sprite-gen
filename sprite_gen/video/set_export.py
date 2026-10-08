# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-set-export` — a finished `video-set` as one eight-heading set a top-down game loads.

A top-down orthographic game asks for its character by compass heading — S, SE, E, NE, N, NW, W, SW,
screen-up north — while a set is filmed by view and facing (`front`, `side` facing right, …). This maps
one onto the other (`HEADING_OF`), takes each item's FINAL strip and writes it under its heading, with one
foot pivot per strip and a `directions.json` a loader reads (docs/direction-set.md).

- The final strip is the loop folder's own `<item>.strip.png`: `video-cycle-align` and `video-follow`
  rewrite it in place, so whatever ran last is what is there. Its `.strip.json` says whether it was aligned.
- A heading the set did not film whose horizontal opposite it did (E↔W, SE↔SW, NE↔NW) is that strip
  mirrored CELL BY CELL, each cell turned over where it stands: the strip is one row of cells, so turning
  the whole image over would also play the cells backwards. S and N have no opposite and are never mirrored.
  Every x the strip's json holds is mirrored with it (`x → cell width − x`), and the record says which.
  A mirror swaps the character's own feet, so an aligned loop's mirror is turned to start where the set's
  start foot lands (`mirror_phase`; `--keep-mirror-phase` leaves it). Its GIF and WebP are mirrored too.
- A set filmed with `--handed` (an item on one side) is not mirrored: a mirror moves the item to the
  other side. The export stops, naming the headings, unless `--allow-mirror-handed` (recorded).
- A heading the set filmed and lost (its item failed) is reported missing, never mirrored over.
- `--headings` asks a state for fewer headings (`diagonals`, a list), as a production films its actions in
  the four diagonals only; a heading not asked for is not written, one asked for and not there is missing.

Productions did this by hand before (crashbang `stocky-village-ortho-20261006/mirror_run.py`,
`kuma-village-ortho-20261007/assemble.py`, `prep_place.py`, `pack.mjs`): per-cell mirrors with
`mirroredFrom` + sha256, a per-frame x offset negated, one foot origin per clip and a heading table.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.gen import handedness as handed_mod
from sprite_gen.spec.runio import atomic_write_set
from sprite_gen.util import gif_utils
from sprite_gen.video import loop as loop_mod

KIND = "sprite-gen-direction-set"
SCHEMA_VERSION = 1
SET_REPORT = "set.report.json"
INDEX = "directions.json"
TABLE = "table.md"

# Clockwise on screen, starting at screen-down: the order a loader that steps by 45 degrees walks.
HEADINGS = ("S", "SE", "E", "NE", "N", "NW", "W", "SW")
# The view (as `video-cycle-align --view` names one) each heading is filmed as. A diagonal view is the
# isometric-game turn the set's prompts film: front_diagonal "walking down and to the {facing}", back_diagonal
# "up and to the {facing}" (batch.VIEW_MOTION_TEXT).
HEADING_OF = {
    "front": "S", "back": "N",
    "side@right": "E", "side@left": "W",
    "front_diagonal@right": "SE", "front_diagonal@left": "SW",
    "back_diagonal@right": "NE", "back_diagonal@left": "NW",
}
VIEW_OF = {heading: view for view, heading in HEADING_OF.items()}
# The horizontal mirror of a heading. S and N are their own mirror images and are never made by one.
OPPOSITE = {"E": "W", "W": "E", "SE": "SW", "SW": "SE", "NE": "NW", "NW": "NE"}
_D = 0.7071  # cos 45°, four decimals as every number of the index
# Unit screen vector of each heading, x right and y DOWN (image coordinates): S is +y.
VECTOR = {"S": [0, 1], "SE": [_D, _D], "E": [1, 0], "NE": [_D, -_D],
          "N": [0, -1], "NW": [-_D, -_D], "W": [-1, 0], "SW": [-_D, _D]}

# A solid pixel for the pivot and the height: half covered or more. The strip's own 8 is an edge's
# outermost fringe; a sole stands where the body is solid, not where its anti-aliasing fades out.
ALPHA_SOLID = 128
# Measured on the KUMA walks (crashbang kuma-village-ortho-20261007: 640 px body, camera 35 degrees above),
# whose pivots were checked by eye on their cells, 2026-10-08:
# The feet: the lowest share of a cell's solid height that holds BOTH of them. A camera from above draws the
# near foot lower on screen than the far one, so the lowest 8 % (`legs.FOOT_BAND`, a side view's sole line)
# often holds one foot only and the pivot leans to it: the back-diagonal walk's stood at 232 px of a 548 px
# cell, on its near foot, where both feet put it at 261. 16 % is what that production's `prep_place.py` took
# for "the two shoe soles", on the same characters.
PIVOT_BAND = 0.16
# A cell stands on the ground when its lowest solid row is within this share of the cell height of the
# strip's floor. The same camera moves a walk's lowest row with every step — 55 px (9 % of the body) on the
# front walk, near foot forward or back — so 3 % kept only the cells one foot was lowest in and put the front
# walk's pivot at 152 px of a 443 px cell, 62 px left of the 214 its whole cycle gives. A hop's airborne cells
# (about half the body up, `batch.MOTION_TEXT["jump"]`) are still far outside 15 %.
GROUNDED = 0.15
# Standing heights across one state's headings may differ by this much before a warning. Rounding moves
# body_h by one pixel (under 0.5 % of any body worth a game asset) and a set cut with one `--body-height`
# lands every heading on its target unless a cell met the strip's height cap first (loop.build_strip), so past
# 3 % is a real size difference: 12 px on a 400 px body, which shows as a pop when the character turns.
# Chosen, not measured over sets; the record keeps the numbers.
SIZE_SPREAD_MAX = 0.03

# What a mirrored strip's json does with the source's fields. Positions inside a cell are mirrored, signed
# horizontal offsets are negated. The records of how the SOURCE was cut (its source-frame rectangle, motion
# correction, alignment with its view and start foot, follow-through regions) describe the source's pixels
# and sides, so they are not carried: they stay readable in the opposite heading's json, copied as filmed.
X_POSITIONS = ("anchor", "foot_x")
X_OFFSETS = ("wrap_dx_px",)
NOT_CARRIED = ("source_rect", "motion_anchor", "cycle_align", "follow")

# `--headings`: what a state asks for. The crashbang KUMA pack (2026-10-07) is the case: movement in all
# eight, actions (hit, slam, combo) in the four diagonals, two filmed and two mirrored.
PRESETS = {"all": HEADINGS, "diagonals": ("SE", "NE", "NW", "SW")}
# A GIF's transparent cut, as video-loop and video-cycle-align write theirs (`save_clean_gif`).
GIF_ALPHA_THRESHOLD = 128


def _heading_list(tokens: list[str], said: str) -> tuple[str, ...]:
    """Presets and heading names, in `HEADINGS` order."""
    out: set[str] = set()
    for t in tokens:
        if t.lower() in PRESETS:
            out.update(PRESETS[t.lower()])
        elif t.upper() in HEADINGS:
            out.add(t.upper())
        else:
            raise SystemExit(f"video-set-export: --headings {said!r}: {t!r} is not a heading ({', '.join(HEADINGS)}) "
                             f"or a preset ({', '.join(PRESETS)})")
    if not out:
        raise SystemExit(f"video-set-export: --headings {said!r} names no heading")
    return tuple(h for h in HEADINGS if h in out)


def parse_headings(spec: str | None) -> tuple[tuple[str, ...], str, dict[str, tuple[str, ...]], dict[str, str]]:
    """`--headings` → (the headings of a state not named, as said; per-state headings, as said). `all` (the
    default), `diagonals`, a list (`S,SE,E`), or per state: `walk=all,attack=diagonals,hit=SE,SW` — a token
    holding `=` opens a state and the tokens after it without one belong to it; a state not named takes `all`."""
    tokens = [t.strip() for t in str(spec or "").split(",") if t.strip()]
    if not tokens:
        return HEADINGS, "all", {}, {}
    if not any("=" in t for t in tokens):
        return _heading_list(tokens, spec or ""), ",".join(tokens), {}, {}
    if "=" not in tokens[0]:
        raise SystemExit(f"video-set-export: --headings {spec!r}: per-state entries start with state=…, not {tokens[0]!r}")
    named: dict[str, list[str]] = {}
    current = ""
    for t in tokens:
        if "=" in t:
            current, _, first = (s.strip() for s in t.partition("="))
            if not current or current in named:
                raise SystemExit(f"video-set-export: --headings {spec!r}: state {current!r} given {'twice' if current else 'no name'}")
            named[current] = [first] if first else []
        else:
            named[current].append(t)
    per = {state: _heading_list(words, f"{state}={','.join(words)}") for state, words in named.items()}
    return HEADINGS, "all", per, {state: ",".join(words) for state, words in named.items()}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _num(v: float) -> int | float:
    """A pixel coordinate as written: whole numbers stay ints, the rest keep two decimals."""
    r = round(float(v), 2)
    return int(r) if r == int(r) else r


def _facings(report: dict[str, Any]) -> list[str]:
    return [f.strip() for f in str(report.get("facing") or "right").split(",") if f.strip()]


def view_of_item(item: dict[str, Any], facings: list[str]) -> str:
    """An item's view as `video-cycle-align --view` names it (`side@right`). A turned view records its
    facing (`turned`); an older report without it is read with the set's one facing, never guessed."""
    direction = str(item.get("direction"))
    if direction not in handed_mod.VIEWS:
        raise SystemExit(f"video-set-export: item {item.get('item')!r} has direction {direction!r}, not one of "
                         f"{', '.join(handed_mod.VIEWS)}")
    if direction not in handed_mod.LATERAL_VIEWS:
        return direction
    turned = item.get("turned")
    if turned is None:
        if len(facings) != 1:
            raise SystemExit(f"video-set-export: item {item.get('item')!r} records no facing and the set was filmed "
                             f"facing {','.join(facings)}; cut the set again with this sprite-gen")
        turned = facings[0]
    if turned not in handed_mod.SIDES:
        raise SystemExit(f"video-set-export: item {item.get('item')!r} faces {turned!r}, not right or left")
    return f"{direction}@{turned}"


def final_strip(set_dir: Path, item: str) -> tuple[Path, Path]:
    """The item's strip as it stands now: the one `<name>.strip.json` in its loop folder and its png
    (the set report's `loop.strip` is where video-loop wrote it, and alignment rewrites that same file)."""
    loop_dir = set_dir / item / "loop"
    metas = sorted(loop_dir.glob("*.strip.json"))
    if len(metas) != 1:
        raise SystemExit(f"video-set-export: {loop_dir}: expected one <name>.strip.json from video-loop, found {len(metas)}")
    png = metas[0].with_name(metas[0].name[: -len(".strip.json")] + ".strip.png")
    if not png.is_file():
        raise SystemExit(f"video-set-export: {png} is missing beside its {metas[0].name}")
    return png, metas[0]


def _cells_alpha(strip: Image.Image, frames: int, w: int, h: int) -> np.ndarray:
    """(frames, h, w) solid mask, one plane per cell."""
    a = np.asarray(strip.getchannel("A")) >= ALPHA_SOLID
    return a.reshape(h, frames, w).transpose(1, 0, 2)


def mirror_cells(strip: Image.Image, frames: int, w: int, rotate: int = 0) -> Image.Image:
    """Every cell turned over left to right where it stands; the cell order is kept. (Turning the whole
    strip over would turn each cell over AND put the last cell first: a walk played backwards.) `rotate`
    then starts the loop `rotate` cells later, the same cycle in the same direction (`mirror_phase`)."""
    a = np.asarray(strip.convert("RGBA"))
    h = a.shape[0]
    cells = np.roll(a.reshape(h, frames, w, 4)[:, :, ::-1, :], -rotate, axis=1)
    return Image.fromarray(np.ascontiguousarray(cells.reshape(h, frames * w, 4)), "RGBA")


def mirror_phase(meta: dict[str, Any], frames: int) -> tuple[int, str]:
    """Cells to start a mirrored loop later so it starts as the set's start foot lands, and why (0: not
    turned). The alignment started the source as its own `start_foot` lands; the mirror swaps the own
    feet, so the mirror starts as the OTHER foot lands, and the source's other strike is where the
    mirror's start foot lands. That strike is the alignment's (`cycle_align.strikes`, frames of the aligned
    cycle, one of them 0); without it, half the cells of a loop aligned as one cycle (two steps); else none."""
    record = meta.get("cycle_align")
    if not isinstance(record, dict):
        return 0, "not cycle-aligned: no start foot to keep"
    if meta.get("loop") is False:
        return 0, "a one-shot plays once from its start"
    if record.get("start_foot") not in handed_mod.SIDES:
        return 0, "the alignment did not name the source's start foot, so the set's foot is not known here"
    strikes = record.get("strikes")
    if isinstance(strikes, list) and len(strikes) == 2 and strikes.count(0) == 1:
        other = int(next(s for s in strikes if s != 0))
        at = meta.get("sample_indices") or list(range(frames))
        cell = min(range(frames), key=lambda k: (abs(int(at[k]) - other), k))  # the cell showing that frame
        return cell, f"the source's other strike, frame {other} of its aligned cycle (cycle_align.strikes)"
    one_cycle = ("cycle_taken" in record or record.get("cycles_given") in (None, 1)) and meta.get("steps") in (None, 2)
    if one_cycle and frames > 1:
        return frames // 2, "half the cells: a loop aligned as one cycle, no strike recorded"
    return 0, "no strike recorded and not one cycle of two steps: where the other foot lands is not known"


def _frames_of(data: bytes) -> tuple[list[Image.Image], list[int], int]:
    """An animation's frames as shown (RGBA), each one's duration and its loop count."""
    im = Image.open(io.BytesIO(data))
    loop = int(im.info.get("loop", 0))
    frames, durations = [], []
    for k in range(getattr(im, "n_frames", 1)):
        im.seek(k)
        frames.append(im.convert("RGBA"))
        durations.append(int(im.info.get("duration") or 0))
    return frames, durations, loop


def _gif_bytes(frames: list[Image.Image], durations: list[int], loop: int) -> bytes:
    """A GIF written the way `save_clean_gif` writes one, with a duration per frame."""
    prepared = [gif_utils._prepare_transparent_frame(f, GIF_ALPHA_THRESHOLD) for f in frames]
    buf = io.BytesIO()
    prepared[0].save(buf, format="GIF", save_all=True, append_images=prepared[1:], duration=durations, loop=loop,
                     disposal=2, transparency=255)
    return buf.getvalue()


def _img2webp() -> str | None:
    """The img2webp video-loop writes with (`loop.write_webp`), when this one has `-exact`."""
    binary = shutil.which("img2webp")
    return binary if binary and loop_mod.img2webp_supports_exact(binary) else None


def _webp_bytes(frames: list[Image.Image], durations: list[int], loop: int, binary: str, work: Path) -> bytes:
    """A lossless, exact-alpha WebP as `loop.write_webp` makes one, with a duration per frame."""
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    cmd = [binary, "-loop", str(loop), "-lossless", "-exact"]
    for k, (im, d) in enumerate(zip(frames, durations)):
        im.save(work / f"webp-{k:03d}.png")
        cmd += ["-d", str(max(1, d)), str(work / f"webp-{k:03d}.png")]
    out = work / "out.webp"
    proc = subprocess.run([*cmd, "-o", str(out)], capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(f"video-set-export: img2webp failed: {proc.stderr.strip()[:300]}")
    return out.read_bytes()


def mirror_previews(previews: dict[str, Path], strip: Image.Image, meta: dict[str, Any], rotate: int,
                    work: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    """The source heading's GIF / WebP for its mirror → ({ext: bytes}, {ext: note}). Unturned, each frame is
    turned over as shown, its duration and the loop count kept. Turned (`rotate`), the frames no longer line
    up with the source's, so they are written from the mirror's own cells at the strip's cell delay, as
    video-cycle-align writes an aligned loop's. A WebP needs img2webp with `-exact`; without it, none."""
    out: dict[str, bytes] = {}
    notes: dict[str, str] = {}
    binary = _img2webp()
    frames_n, w, h = int(meta["frames"]), int(meta["w"]), int(meta["h"])
    if rotate:
        cells = [strip.crop((k * w, 0, (k + 1) * w, h)) for k in range(frames_n)]
        seconds = meta.get("cycle_seconds")
        delay = max(20, round(1000 * seconds / frames_n)) if isinstance(seconds, (int, float)) and seconds > 0 else round(float(meta["delay_ms"]))
    for ext, path in previews.items():
        if ext == "webp" and binary is None:
            notes[ext] = "not mirrored: img2webp (libwebp >= 1.5, with -exact) is not on PATH"
            continue
        if rotate:
            frames, durations, loop = cells, [delay] * frames_n, 0
        else:
            frames, durations, loop = _frames_of(path.read_bytes())
            frames = [f.transpose(Image.Transpose.FLIP_LEFT_RIGHT) for f in frames]
        out[ext] = _gif_bytes(frames, durations, loop) if ext == "gif" else _webp_bytes(frames, durations, loop, binary, work / "webp")
    return out, notes


def alpha_pivot(strip: Image.Image, frames: int, w: int, h: int) -> list[int | float]:
    """One foot pivot for a strip that declares none: y the floor (the lowest solid row's bottom edge over
    every cell, as video-loop's own `--anchor feet` stands its pivot on the cell's floor), x the median, over
    the cells standing on that floor (`GROUNDED`), of the centre of each cell's foot band (the lowest
    `PIVOT_BAND` of its solid height, both feet in it). Pixel-edge coordinates, so a mirror maps x to w − x
    exactly."""
    cells = _cells_alpha(strip, frames, w, h)
    bottoms, centres = [], []
    for cell in cells:
        rows = np.nonzero(cell.any(axis=1))[0]
        if rows.size == 0:
            continue
        top, low = int(rows[0]), int(rows[-1])
        band = max(1, round((low + 1 - top) * PIVOT_BAND))
        cols = np.nonzero(cell[low + 1 - band : low + 1].any(axis=0))[0]
        bottoms.append(low + 1)
        centres.append((int(cols[0]) + int(cols[-1]) + 1) / 2)
    if not bottoms:
        raise SystemExit("video-set-export: every cell of the strip is transparent; there is no body to stand")
    floor = max(bottoms)
    grounded = [c for b, c in zip(bottoms, centres) if b >= floor - GROUNDED * h]
    return [_num(statistics.median(grounded)), floor]


def pivot_of(meta: dict[str, Any], strip: Image.Image) -> tuple[list[int | float], str]:
    """The strip's own foot anchor when it has one (`video-loop --anchor feet` writes `anchor`), else
    measured on its alpha (`alpha_pivot`)."""
    w, h = int(meta["w"]), int(meta["h"])
    if isinstance(meta.get("anchor"), list) and len(meta["anchor"]) == 2 and None not in meta["anchor"]:
        pivot, source = [_num(meta["anchor"][0]), _num(meta["anchor"][1])], "strip-anchor"
    elif meta.get("foot_x") is not None:
        pivot, source = [_num(meta["foot_x"]), h], "strip-foot_x"
    else:
        pivot, source = alpha_pivot(strip, int(meta["frames"]), w, h), "alpha"
    if not (0 <= pivot[0] <= w and 0 <= pivot[1] <= h):
        raise SystemExit(f"video-set-export: pivot {pivot} ({source}) lies outside the {w}x{h} cell")
    return pivot, source


def body_height(meta: dict[str, Any], strip: Image.Image) -> tuple[int, str]:
    """The standing height video-loop recorded (`body_h`), else the tallest solid body among the cells
    standing on the floor (a raised arm counts there, which the recorded one does not)."""
    if isinstance(meta.get("body_h"), (int, float)):
        return int(meta["body_h"]), "strip-meta"
    w, h, frames = int(meta["w"]), int(meta["h"]), int(meta["frames"])
    spans = []
    for cell in _cells_alpha(strip, frames, w, h):
        rows = np.nonzero(cell.any(axis=1))[0]
        if rows.size:
            spans.append((int(rows[-1]) + 1, int(rows[-1]) + 1 - int(rows[0])))
    if not spans:
        raise SystemExit("video-set-export: every cell of the strip is transparent; there is no body to measure")
    floor = max(b for b, _ in spans)
    return max(s for b, s in spans if b >= floor - GROUNDED * h), "alpha"


def _roll(values: Any, rotate: int, frames: int) -> Any:
    """A per-cell list started `rotate` cells later, as the cells are (anything else as it is)."""
    if rotate and isinstance(values, list) and len(values) == frames:
        return values[rotate:] + values[:rotate]
    return values


def mirror_meta(meta: dict[str, Any], w: int, rotate: int = 0) -> tuple[dict[str, Any], dict[str, Any]]:
    """The source strip's json for its mirror: x positions mirrored, x offsets negated, the source's own
    cut records left out (`NOT_CARRIED`), and with `rotate` the per-cell lists (`sample_indices`,
    `source_cut.samples`) started where the cells now start. Returns (json, what was done)."""
    out: dict[str, Any] = {}
    done: dict[str, Any] = {"per_cell": True, "x_mirrored": [], "x_negated": [], "not_carried": []}
    frames = int(meta["frames"])
    for key, value in meta.items():
        if key == "sample_indices":
            out[key] = _roll(value, rotate, frames)
        elif key == "source_cut" and isinstance(value, dict):
            out[key] = {**value, **({"samples": _roll(value["samples"], rotate, frames)} if "samples" in value else {})}
        elif key in NOT_CARRIED:
            done["not_carried"].append(key)
        elif key == "anchor" and isinstance(value, list) and len(value) == 2 and value[0] is not None:
            out[key] = [_num(w - value[0]), value[1]]
            done["x_mirrored"].append(key)
        elif key in X_POSITIONS and isinstance(value, (int, float)):
            out[key] = _num(w - value)
            done["x_mirrored"].append(key)
        elif key in X_OFFSETS and isinstance(value, (int, float)):
            out[key] = -value
            done["x_negated"].append(key)
        elif key == "sequence" and isinstance(value, list) and any(isinstance(s, dict) and "shake" in s for s in value):
            # a keypose timing a production wrote into the strip json: [{frame, durationMs, shake: [dx, dy]}]
            out[key] = [{**s, "shake": [-s["shake"][0], *s["shake"][1:]]} if isinstance(s, dict) and "shake" in s else s
                        for s in value]
            done["x_negated"].append("sequence[].shake[0]")
        else:
            out[key] = value
    return out, done


def _fps(meta: dict[str, Any], png: Path) -> float:
    """Cells per second as the strip plays: its cells over the cycle's seconds (a subsampled strip plays
    fewer cells in the same time), else from its per-cell delay."""
    seconds, delay = meta.get("cycle_seconds"), meta.get("delay_ms")
    if isinstance(seconds, (int, float)) and seconds > 0:
        return int(meta["frames"]) / seconds
    if isinstance(delay, (int, float)) and delay > 0:
        return 1000 / delay
    raise SystemExit(f"video-set-export: {png.name}'s json has neither cycle_seconds nor delay_ms; its playback rate is unknown")


def _other_foot(foot: Any) -> Any:
    """A mirror swaps the character's own sides: the right foot landing becomes the left one."""
    return {"left": "right", "right": "left"}.get(foot, foot)


def _read_report(set_dir: Path) -> dict[str, Any]:
    path = set_dir / SET_REPORT
    if not path.is_file():
        raise SystemExit(f"video-set-export: {path} not found — --set-dir is a video-set --out-dir")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"video-set-export: {path} is not JSON: {exc}") from exc
    if not isinstance(report, dict) or report.get("kind") != "sprite-gen-video-set-report":
        raise SystemExit(f"video-set-export: {path} is not a video-set report (kind sprite-gen-video-set-report)")
    return report


def _old_files(out_dir: Path) -> list[Path]:
    """Files a previous export into `out_dir` wrote (its index's own list), so a heading or state that is
    gone now does not linger. A non-empty folder holding no export is refused, never cleared."""
    if not out_dir.exists() or not any(out_dir.iterdir()):
        return []
    try:
        old = json.loads((out_dir / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    if not isinstance(old, dict) or old.get("kind") != KIND:
        raise SystemExit(f"video-set-export: {out_dir} is not empty and holds no {INDEX}; name an empty or new folder")
    files = [out_dir / INDEX, out_dir / TABLE]
    for st in (old.get("states") or {}).values():
        for entry in (st.get("headings") or {}).values():
            files += [out_dir / p for p in (entry.get("file"), entry.get("meta"), *(entry.get("previews") or {}).values()) if p]
    root = out_dir.resolve()
    return [f for f in files if f.resolve().is_relative_to(root)]


def export_set(set_dir: Path, out_dir: Path, *, states: list[str] | None = None, headings: str | None = None,
               allow_mirror_handed: bool = False, keep_mirror_phase: bool = False) -> dict[str, Any]:
    set_dir = set_dir.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    if out_dir == set_dir:
        raise SystemExit("video-set-export: --out-dir is the set itself; write the export beside it")
    default_headings, default_said, per_state, per_said = parse_headings(headings)
    report = _read_report(set_dir)
    facings = _facings(report)
    filmed: dict[str, dict[str, dict[str, Any]]] = {}
    failed: dict[str, dict[str, dict[str, Any]]] = {}
    order: list[str] = [str(s) for s in report.get("states") or []]
    for item in report.get("items") or []:
        state = str(item.get("state"))
        heading = HEADING_OF[view_of_item(item, facings)]
        bucket = filmed if item.get("ok") else failed
        if heading in filmed.get(state, {}) or heading in failed.get(state, {}):
            raise SystemExit(f"video-set-export: two items of {state} are heading {heading}")
        bucket.setdefault(state, {})[heading] = item
        if state not in order:
            order.append(state)
    present = [s for s in order if s in filmed or s in failed]
    unnamed = [s for s in per_state if s not in present]
    if unnamed:
        raise SystemExit(f"video-set-export: --headings names {','.join(unnamed)}, not a state of the set ({', '.join(present) or 'none'})")
    if states:
        unknown = [s for s in states if s not in present]
        if unknown:
            raise SystemExit(f"video-set-export: --states {','.join(unknown)} not in the set ({', '.join(present) or 'none'})")
        present = [s for s in present if s in states]
    if not present:
        raise SystemExit("video-set-export: the set holds no items")
    wanted = {s: per_state.get(s, default_headings) for s in present}

    # Which heading comes from where, decided before a pixel is read: a refusal writes nothing. Only the
    # headings a state asks for are planned; a mirror's source is read even where it is not asked for.
    plan: dict[str, dict[str, tuple[str, Any]]] = {}
    for state in present:
        got, lost = filmed.get(state, {}), failed.get(state, {})
        plan[state] = {}
        for heading in wanted[state]:
            if heading in got:
                plan[state][heading] = ("filmed", got[heading])
            elif heading in lost:
                plan[state][heading] = ("missing", f"{lost[heading]['item']} failed: {lost[heading].get('error', '')}".rstrip(": "))
            elif heading in OPPOSITE and OPPOSITE[heading] in got:
                plan[state][heading] = ("mirrored", OPPOSITE[heading])
            elif heading in OPPOSITE and OPPOSITE[heading] in lost:
                plan[state][heading] = ("missing", f"not filmed, and its opposite {OPPOSITE[heading]} failed")
            else:
                plan[state][heading] = ("missing", "not filmed" + ("" if heading not in OPPOSITE else f", nor its opposite {OPPOSITE[heading]}"))
    handed = report.get("handed") or []
    mirrors = [f"{s}/{h} (from {src})" for s in present for h, (how, src) in plan[s].items() if how == "mirrored"]
    if handed and mirrors and not allow_mirror_handed:
        items = ", ".join(f"{h.get('item')} on the {h.get('side')} {h.get('part') or 'side'}".rstrip() for h in handed)
        raise SystemExit(f"video-set-export: the set was filmed with --handed ({items}); mirroring {', '.join(mirrors)} would move "
                         f"each item to the other side. Film that facing instead (video-set --facing right,left, a still drawn "
                         f"each way: docs/video-pipeline.md#handedness--an-item-on-one-side), or pass --allow-mirror-handed to "
                         f"mirror anyway (recorded)")
    stale = _old_files(out_dir)

    payloads: dict[Path, bytes | str] = {}
    index_states: dict[str, Any] = {}
    warnings: list[str] = []
    aligned_states = {s for s, a in (report.get("cycle_align") or {}).items() if isinstance(a, dict) and a.get("applied")}
    with tempfile.TemporaryDirectory(prefix="video-set-export-") as tmp:
        work = Path(tmp)
        for state in present:
            entries: dict[str, Any] = {}
            loaded: dict[str, dict[str, Any]] = {}
            sources = {h for h, (how, _) in plan[state].items() if how == "filmed"}
            sources |= {src for how, src in plan[state].values() if how == "mirrored"}
            for heading in [h for h in HEADINGS if h in sources]:
                src = filmed[state][heading]
                png, js = final_strip(set_dir, src["item"])
                png_bytes, js_bytes = png.read_bytes(), js.read_bytes()
                meta = json.loads(js_bytes)
                for key in ("frames", "w", "h"):
                    if not isinstance(meta.get(key), int) or meta[key] <= 0:
                        raise SystemExit(f"video-set-export: {js} has no `{key}`; cut the loop again with this sprite-gen (video-loop)")
                strip = Image.open(io.BytesIO(png_bytes))
                strip.load()
                if strip.size != (meta["w"] * meta["frames"], meta["h"]):
                    raise SystemExit(f"video-set-export: {png.name} is {strip.size[0]}x{strip.size[1]}, its json says {meta['frames']} "
                                     f"cells of {meta['w']}x{meta['h']} in one row")
                pivot, pivot_source = pivot_of(meta, strip)
                body_h, body_src = body_height(meta, strip)
                previews = {ext: png.with_name(png.name[: -len(".strip.png")] + f".{ext}") for ext in ("gif", "webp")}
                loaded[heading] = {"item": src["item"], "png": png, "png_bytes": png_bytes, "js_bytes": js_bytes, "meta": meta,
                                   "strip": strip, "pivot": pivot, "pivot_source": pivot_source, "body_h": body_h,
                                   "body_h_source": body_src, "previews": {k: p for k, p in previews.items() if p.is_file()}}
                if state in aligned_states and "cycle_align" not in meta:
                    warnings.append(f"{state}: set.report.json says the {state} loops were aligned, but {src['item']}'s strip carries "
                                    f"no alignment — it was cut again after; align the set again (video-cycle-align)")
            for heading in wanted[state]:
                how, src = plan[state][heading]
                if how == "missing":
                    entries[heading] = {"status": "missing", "view": VIEW_OF[heading], "reason": src}
                    continue
                base = loaded[heading if how == "filmed" else src]
                meta = base["meta"]
                w, h, frames = int(meta["w"]), int(meta["h"]), int(meta["frames"])
                fps = _fps(meta, base["png"])  # before a mirror's previews, which play at the same rate
                rel = f"{state}/{heading}"
                entry: dict[str, Any] = {"status": how, "view": VIEW_OF[heading]}
                source_strip = base["png"].relative_to(set_dir).as_posix()
                if how == "filmed":
                    payloads[out_dir / f"{rel}.png"] = base["png_bytes"]
                    payloads[out_dir / f"{rel}.json"] = base["js_bytes"]
                    previews = {}
                    for ext, p in base["previews"].items():
                        payloads[out_dir / f"{rel}.{ext}"] = p.read_bytes()
                        previews[ext] = f"{rel}.{ext}"
                    pivot, pivot_source, sha = base["pivot"], base["pivot_source"], _sha(base["png_bytes"])
                    start_foot = (meta.get("cycle_align") or {}).get("start_foot")
                    entry |= {"source_item": base["item"], "source_strip": source_strip, "source_sha256": sha}
                else:
                    rotate, phase_why = (0, "--keep-mirror-phase") if keep_mirror_phase else mirror_phase(meta, frames)
                    image = mirror_cells(base["strip"], frames, w, rotate)
                    buf = io.BytesIO()
                    image.save(buf, format="PNG")
                    out_png = buf.getvalue()
                    mirrored, done = mirror_meta(meta, w, rotate)
                    own = (meta.get("cycle_align") or {}).get("start_foot")
                    # turned to the source's other strike, the mirror starts as the source's own start foot lands
                    start_foot = own if rotate else _other_foot(own)
                    pivot = [_num(w - base["pivot"][0]), base["pivot"][1]]
                    pivot_source = f"mirrored:{base['pivot_source']}"
                    provenance = {"heading": src, "item": base["item"], "strip": source_strip, "source_sha256": _sha(base["png_bytes"]),
                                  **done, "phase_rotated_by": rotate, "phase_why": phase_why,
                                  **({"start_foot": start_foot} if start_foot else {})}
                    mirrored["mirroredFrom"] = provenance
                    payloads[out_dir / f"{rel}.png"] = out_png
                    payloads[out_dir / f"{rel}.json"] = json.dumps(mirrored, indent=2) + "\n"
                    made, notes = mirror_previews(base["previews"], image, meta, rotate, work)
                    previews = {}
                    for ext, data in made.items():
                        payloads[out_dir / f"{rel}.{ext}"] = data
                        previews[ext] = f"{rel}.{ext}"
                    sha = _sha(out_png)
                    entry |= {"mirroredFrom": src, "source_item": base["item"], "source_strip": source_strip,
                              "source_sha256": provenance["source_sha256"],
                              "mirror": {k: done[k] for k in ("per_cell", "x_mirrored", "x_negated", "not_carried")},
                              "phase_rotated_by": rotate, "phase_why": phase_why,
                              **({"previews_from": "cells" if rotate else "source-mirrored"} if made else {}),
                              **({"preview_notes": notes} if notes else {})}
                    if handed:
                        entry["handed_override"] = True
                entry |= {"file": f"{rel}.png", "sha256": sha, "meta": f"{rel}.json", "previews": previews,
                          "aligned": "cycle_align" in meta, "loop": bool(meta.get("loop", True)), "cells": frames,
                          "fps": round(fps, 4), "delay_ms": meta.get("delay_ms"), "cell": [w, h], "pivot": pivot,
                          "pivot_source": pivot_source, "body_h": base["body_h"], "body_h_source": base["body_h_source"]}
                if start_foot:
                    entry["start_foot"] = start_foot
                entries[heading] = entry
            index_states[state] = {"requested": list(wanted[state]), "requested_as": per_said.get(state, default_said),
                                   **_state_record(state, entries, warnings)}
    if handed and mirrors:
        warnings.append(f"mirrored despite --handed (--allow-mirror-handed): {', '.join(mirrors)} show every handed item on the "
                        f"other side")
    payload = {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "convention": {
            "projection": "top-down orthographic",
            "up": "N",
            "axes": "x right, y down (image pixels)",
            "headings": list(HEADINGS),
            "order": "clockwise on screen from screen-down",
            "vector": VECTOR,
            "view": {h: VIEW_OF[h] for h in HEADINGS},
            "mirror_of": dict(OPPOSITE),
            "pivot": "[x, y] in the cell's pixels from its top-left corner, on pixel edges: y = cell height is the cell's bottom edge",
        },
        "source": {"set_dir": set_dir.as_posix(), "set_report_sha256": _sha((set_dir / SET_REPORT).read_bytes()),
                   "facing": report.get("facing"), "handed": handed or None,
                   "camera_elevation": report.get("camera_elevation"), "body_height": report.get("body_height")},
        "headings_arg": headings or None,
        "allow_mirror_handed": bool(allow_mirror_handed),
        "keep_mirror_phase": bool(keep_mirror_phase),
        "size_spread_max": SIZE_SPREAD_MAX,
        "complete": all(s["complete"] for s in index_states.values()),
        "states": index_states,
        "warnings": warnings,
    }
    payloads[out_dir / INDEX] = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    payloads[out_dir / TABLE] = table(payload)
    for folder in sorted({p.parent for p in payloads}):  # a state with no heading made gets no empty folder
        folder.mkdir(parents=True, exist_ok=True)
    atomic_write_set(payloads)
    for old in stale:
        if old not in payloads:
            old.unlink(missing_ok=True)
            if old.parent != out_dir and old.parent.is_dir() and not any(old.parent.iterdir()):
                old.parent.rmdir()
    return payload


def _state_record(state: str, headings: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """A state's headings with what the set across them says: complete or not, the standing-height spread
    (over the filmed headings: a mirror has its source's height), one cell count, one start foot."""
    missing = {h: e["reason"] for h, e in headings.items() if e["status"] == "missing"}
    filmed = {h: e["body_h"] for h, e in headings.items() if e["status"] == "filmed"}
    spread = (max(filmed.values()) / min(filmed.values()) - 1) if filmed and min(filmed.values()) > 0 else 0.0
    size = {"body_h": filmed, "spread": round(spread, 4), "max": SIZE_SPREAD_MAX, "ok": spread <= SIZE_SPREAD_MAX}
    if not size["ok"]:
        warnings.append(f"{state}: the standing height differs across headings by {spread:.1%} (body_h "
                        f"{', '.join(f'{h} {v}' for h, v in filmed.items())}), past {SIZE_SPREAD_MAX:.0%} — nothing was "
                        f"rescaled; cut the set with one video-set --body-height, or cut the odd heading again")
    if missing:
        warnings.append(f"{state}: no {', '.join(missing)} — " + "; ".join(f"{h}: {r}" for h, r in missing.items()))
    made = {h: e for h, e in headings.items() if e["status"] != "missing"}
    looping = {h: e["cells"] for h, e in made.items() if e["loop"]}
    if len(set(looping.values())) > 1:
        warnings.append(f"{state}: the headings loop over different cell counts ({', '.join(f'{h} {n}' for h, n in looping.items())}) "
                        f"— a game that keeps the cell index when the character turns needs one length: video-cycle-align "
                        f"(docs/loop-repair.md section 4)")
    feet = {h: e["start_foot"] for h, e in made.items() if e.get("start_foot")}
    if len(set(feet.values())) > 1:
        warnings.append(f"{state}: the headings start on different own feet ("
                        + ", ".join(f"{h} {f}" + (f" (mirrored from {made[h]['mirroredFrom']})" if made[h]["status"] == "mirrored" else "")
                                    for h, f in feet.items())
                        + ") — a mirror not turned to the set's start foot (its phase_why says why) starts on the other own "
                          "foot; a game that keeps the cell index on a turn changes step there")
    return {"complete": not missing, "missing": missing, "size": size, "headings": headings}


def table(payload: dict[str, Any]) -> str:
    lines = ["| state | heading | status | from | cells | fps | cell | pivot | body_h |", "|---|---|---|---|---|---|---|---|---|"]
    for state, st in payload["states"].items():
        for heading, e in st["headings"].items():
            if e["status"] == "missing":
                lines.append(f"| {state} | {heading} | MISSING | {e['reason'][:60]} | - | - | - | - | - |")
                continue
            origin = e["source_item"] if e["status"] == "filmed" else f"{e['mirroredFrom']} ({e['source_item']})"
            lines.append(f"| {state} | {heading} | {e['status']}{' (aligned)' if e['aligned'] else ''} | {origin} | {e['cells']} | "
                         f"{e['fps']:g} | {e['cell'][0]}x{e['cell'][1]} | {e['pivot'][0]},{e['pivot'][1]} ({e['pivot_source']}) | {e['body_h']} |")
    lines.append("")
    for state, st in payload["states"].items():
        lines.append(f"- {state}: {'complete' if st['complete'] else 'INCOMPLETE'} ({st['requested_as']}), standing height spread "
                     f"{st['size']['spread']:.1%} (max {st['size']['max']:.0%})")
    lines += [f"- warning: {w}" for w in payload["warnings"]]
    return "\n".join(lines) + "\n"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--set-dir", required=True, type=Path, help="a finished video-set --out-dir (it holds set.report.json)")
    parser.add_argument("--out-dir", required=True, type=Path, help="where the set is written: <state>/<HEADING>.png + .json (and the "
                        "GIF/WebP), directions.json and table.md; an empty folder, or a previous export's (its files are replaced)")
    parser.add_argument("--states", default=None, help="comma list of states to export (default: every state of the set)")
    parser.add_argument("--headings", default=None, metavar="PRESET|LIST|STATE=…", help="the headings each state is exported in: all "
                        "(default, the eight), diagonals (SE,SW,NE,NW), a list (S,SE,E), or per state, e.g. walk=all,attack=diagonals,"
                        "hit=diagonals (a state not named: all). A heading not asked for is not written; one asked for that is "
                        "neither filmed nor mirrorable is missing (exit 1)")
    parser.add_argument("--allow-mirror-handed", action="store_true", help="mirror the missing left/right headings of a set filmed with "
                        "--handed anyway (each handed item then shows on the other side there; recorded per heading)")
    parser.add_argument("--keep-mirror-phase", action="store_true", help="leave a mirrored aligned loop starting on its first cell, on "
                        "the other own foot than the set's, instead of turning it to start as the set's start foot lands")


def run(**kwargs: object) -> int:
    states = [s.strip() for s in str(kwargs.get("states") or "").split(",") if s.strip()] or None
    payload = export_set(Path(str(kwargs["set_dir"])), Path(str(kwargs["out_dir"])), states=states,
                         headings=kwargs.get("headings"),  # type: ignore[arg-type]
                         allow_mirror_handed=bool(kwargs.get("allow_mirror_handed")),
                         keep_mirror_phase=bool(kwargs.get("keep_mirror_phase")))
    summary = {state: {"complete": st["complete"],
                       "filmed": [h for h, e in st["headings"].items() if e["status"] == "filmed"],
                       "mirrored": [h for h, e in st["headings"].items() if e["status"] == "mirrored"],
                       **({"missing": list(st["missing"])} if st["missing"] else {})}
               for state, st in payload["states"].items()}
    print(json.dumps(summary, ensure_ascii=False))
    for line in payload["warnings"]:
        print(f"video-set-export: warning: {line}", file=sys.stderr)
    # the files are written either way; a state short of a heading it asked for is not a set a loader can trust
    return 0 if payload["complete"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-set-export", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
