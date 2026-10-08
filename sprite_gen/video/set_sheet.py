# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-set-sheet` — the directions of one set on one sheet, standing on one ground line.

A set's loops are cut one direction at a time, each strip its own cell size: a strip is the union of
its own cycle's bodies (`video-loop`), so a side walk is wider than a front one. Laid side by side as
they are, the directions stand at different heights and drift apart as a board plays. This lays them
on one sheet the way a board needs them:

- **Rows in the compass order** S, SW, W, NW, N, NE, E, SE — clockwise from the viewer, the order a
  character turns in — whatever order the loops are given in; a direction not given has no row. Each
  loop's `--view` says which it is, as `video-cycle-align` takes it: `front` is S, `back` N,
  `side|front_diagonal|back_diagonal@right|left` the rest (`@left` faces the screen's left: W, SW, NW).
  A strip turned over to face the other way is a direction like any other.
- **One cell** that holds every pixel of every cell of every direction (and of the centre picture),
  centred on the pivot, so every cell's pivot is its bottom centre. Each strip is laid on its own pivot
  — the one its sidecar declares (`anchor`, the foot line under `video-loop --anchor feet`), else its
  bottom centre — and on its own bottom, where `video-loop` stands the feet.
- **One ground line** (`baseline_y`): the lowest body pixel of any cell of any direction — body as
  `video-loop` reads one, alpha 8 and over (`ALPHA_SOLID`); a fainter pixel (a soft shadow) is kept in the cell
  and is no body. A direction whose strip leaves room under its feet stands that much above it, as cut
  (`body_bottom_y`).
- **No row scaled.** Every cell is the strip's own cell, pixel for pixel; one size across the set is
  `video-loop --body-height`'s, never made here direction by direction.
- **The centre picture** (`--center`, a transparent still such as the set's base): scaled to the rows'
  standing height (the median of their `body_h`, against the still's own height read as `video-loop`
  reads a standing pose), its body stood on the same ground line and centred on the pivot, in a cell of
  its own.

Loops of different lengths (frames or frame time) are laid all the same, the shorter rows' last cells
empty, and said to differ: `same_length` false and a warning. `video-cycle-align` gives a set one length.
The rows' pixels, frames and frame times are read, never changed; the loop directories are only read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.gen import handedness as handed_mod
from sprite_gen.spec.assets import load_asset
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.resample import resize_cell
from sprite_gen.video import loop as loop_mod
from sprite_gen.video.repair import ALPHA_SOLID

PROG = "video-set-sheet"
KIND = "sprite-gen-video-set-sheet"
SCHEMA = 1
# The compass code of each view, and the order of a sheet's rows: clockwise from the viewer.
ORDER = ("S", "SW", "W", "NW", "N", "NE", "E", "SE")
CODES = {"front": "S", "front_diagonal@left": "SW", "side@left": "W", "back_diagonal@left": "NW", "back": "N",
         "back_diagonal@right": "NE", "side@right": "E", "front_diagonal@right": "SE"}


@dataclass(frozen=True)
class Row:
    """One direction of the set: its loop's strip cells as cut, the pivot it stands on and its timing."""

    code: str
    view: str
    dir: Path
    name: str
    frames: tuple[Image.Image, ...]
    anchor: tuple[float, float]
    delay_ms: float
    body_h: int | None
    sha256: str

    def origin(self) -> tuple[int, int]:
        """Where each cell's top-left corner goes with the set's pivot at (0, 0). A pivot between two pixels
        (an odd width's centre) is taken at the one right of it, so the cell lies half a pixel left of it."""
        return -math.ceil(self.anchor[0]), -math.ceil(self.anchor[1])


def code_of(view: str) -> str:
    """The compass code of a `--view`, or a refusal: a front or back view turned over faces no side."""
    name, _, facing = view.partition("@")
    handed_mod.validate_view(name, facing or None)
    code = CODES.get(view)
    if code is None:
        raise SystemExit(f"{PROG}: --view {view}: a front or back view faces no side on a sheet — its picture turned over is "
                         f"not a compass direction; give {name}")
    return code


def read_row(loop_dir: Path, view: str) -> Row:
    """A loop directory's strip as one direction: the `<name>.strip.json` it holds and the strip beside it,
    read as the asset loader reads a loop strip (`spec.assets.load_asset`: its cells, frame time and pivot)."""
    code = code_of(view)
    d = loop_dir.expanduser().resolve()
    metas = sorted(d.glob("*.strip.json"))
    if len(metas) != 1:
        raise SystemExit(f"{PROG}: {d}: expected one <name>.strip.json (a video-loop output directory), found {len(metas)}")
    name = metas[0].name[: -len(".strip.json")]
    strip = d / f"{name}.strip.png"
    if not strip.is_file():
        raise SystemExit(f"{PROG}: {d}: no {strip.name} beside {metas[0].name}")
    try:
        seq = load_asset(metas[0])
        meta = json.loads(metas[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{PROG}: {d}: {exc}") from exc
    body_h = meta.get("body_h")
    return Row(code=code, view=view, dir=d, name=name, frames=tuple(seq.frames), anchor=(float(seq.anchor[0]), float(seq.anchor[1])),
               delay_ms=meta["delay_ms"], body_h=int(body_h) if body_h is not None else None,
               sha256=hashlib.sha256(strip.read_bytes()).hexdigest())


def _box(im: Image.Image) -> tuple[int, int, int, int] | None:
    """Every pixel of a cell that is not fully transparent: nothing outside it is dropped from the sheet."""
    return im.getchannel("A").getbbox()


def _body(im: Image.Image) -> tuple[int, int, int, int] | None:
    """The body's box: alpha ALPHA_SOLID and over, as video-loop reads a body (its crop, `body_h`)."""
    return im.getchannel("A").point(lambda v: 255 if v >= ALPHA_SOLID else 0).getbbox()


def _centre(path: Path, rows: list[Row]) -> tuple[Image.Image, dict[str, Any]]:
    """The centre picture cropped to its pixels and scaled to the rows' standing height, with its record."""
    p = path.expanduser().resolve()
    try:
        still = Image.open(p).convert("RGBA")
    except OSError as exc:
        raise SystemExit(f"{PROG}: --center {p}: {exc}") from exc
    if still.getchannel("A").getextrema()[0] == 255:
        raise SystemExit(f"{PROG}: --center {p}: no transparent background — cut it off first (sprite-gen cutout)")
    missing = [str(r.dir) for r in rows if r.body_h is None]
    if missing:
        raise SystemExit(f"{PROG}: --center needs the rows' standing height, and {', '.join(missing)} has no body_h in its "
                         "strip metadata — cut it with video-loop")
    target = round(statistics.median(r.body_h for r in rows))  # type: ignore[misc]
    try:
        standing = loop_mod.standing_height(p)
    except SystemExit as exc:
        raise SystemExit(f"{PROG}: --center {p}: no body in it to stand on the ground line "
                         f"(every pixel under alpha {ALPHA_SOLID})") from exc
    scale = target / standing
    crop = still.crop(_box(still))
    scaled = resize_cell(crop, (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))))
    scaled = scaled.crop(_box(scaled))
    if _body(scaled) is None:
        raise SystemExit(f"{PROG}: --center {p}: no body left at {scale:.4f} of its size to stand on the ground line")
    return scaled, {"input": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "standing_h": standing,
                    "body_h": target, "scale": round(scale, 4)}


def lay(rows: list[Row], centre: Image.Image | None = None) -> tuple[Image.Image, Image.Image | None, dict[str, Any]]:
    """The sheet (rows in the given order, a frame a column), the centre picture's cell, and the geometry:
    `cell_w`, `cell_h`, `baseline_y`, `anchor`, `columns`, each row's `body_bottom_y`, the centre's `box`.
    Coordinates are the set's: its pivot at (0, 0), every row's own pivot laid on it."""
    boxes: list[tuple[int, int, int, int]] = []
    bottoms = []
    for row in rows:
        ox, oy = row.origin()
        bodies = [b[3] + oy for b in map(_body, row.frames) if b]
        if not bodies:
            raise SystemExit(f"{PROG}: {row.dir}: no cell of its strip holds a body (every pixel under alpha {ALPHA_SOLID})")
        boxes += [(b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy) for b in map(_box, row.frames) if b]
        bottoms.append(max(bodies))
    ground = max(bottoms)
    place = None
    if centre is not None:
        # its body's box centred on the pivot and its body's lowest pixel on the ground line
        body = _body(centre)
        place = (-math.ceil((body[0] + body[2]) / 2), ground - body[3])  # type: ignore[index]
        boxes.append((place[0], place[1], place[0] + centre.width, place[1] + centre.height))
    half = max(1, -min(b[0] for b in boxes), max(b[2] for b in boxes))
    left, top = -half, min(b[1] for b in boxes)
    cell_w, cell_h = 2 * half, max(b[3] for b in boxes) - top
    columns = max(len(row.frames) for row in rows)
    sheet = Image.new("RGBA", (cell_w * columns, cell_h * len(rows)), (0, 0, 0, 0))
    for r, row in enumerate(rows):
        ox, oy = row.origin()
        for k, frame in enumerate(row.frames):
            # cells never overlap, so each is copied as it is (a crop past the strip's edge is transparent)
            sheet.paste(frame.crop((left - ox, top - oy, left - ox + cell_w, top - oy + cell_h)), (k * cell_w, r * cell_h))
    geometry: dict[str, Any] = {"cell_w": cell_w, "cell_h": cell_h, "baseline_y": ground - top, "anchor": [half, ground - top],
                                "columns": columns, "body_bottom_y": [b - top for b in bottoms]}
    cell = None
    if centre is not None and place is not None:
        cell = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 0))
        x, y = place[0] - left, place[1] - top
        cell.paste(centre, (x, y))
        geometry["center_box"] = [x, y, x + centre.width, y + centre.height]
    return sheet, cell, geometry


def make_sheet(loop_dirs: list[Path], views: list[str] | None, *, out_dir: Path, name: str = "set",
               center: Path | None = None) -> dict[str, Any]:
    """Lay the loops on one sheet and write `<name>.sheet.png`, `<name>.sheet.json` and, with a centre
    picture, `<name>.center.png` into `out_dir`. Everything is read and checked before anything is written."""
    views = list(views or [])
    if not loop_dirs:
        raise SystemExit(f"{PROG}: at least one --loop-dir")
    if len(views) != len(loop_dirs):
        raise SystemExit(f"{PROG}: {len(views)} --view for {len(loop_dirs)} --loop-dir; give one per loop, in the same order")
    read = [read_row(Path(d), v) for d, v in zip(loop_dirs, views)]
    seen: dict[str, Row] = {}
    for row in read:
        if row.code in seen:
            raise SystemExit(f"{PROG}: --view {row.view} is given twice ({seen[row.code].dir}, {row.dir}); one loop per direction")
        seen[row.code] = row
    rows = [seen[c] for c in ORDER if c in seen]
    centre, centre_record = _centre(Path(center), rows) if center is not None else (None, None)
    sheet, centre_cell, geometry = lay(rows, centre)
    same = len({(len(r.frames), r.delay_ms) for r in rows}) == 1
    record: dict[str, Any] = {
        "kind": KIND, "schema": SCHEMA, "sheet": f"{name}.sheet.png", "order": [r.code for r in rows],
        "columns": geometry["columns"], "cell_w": geometry["cell_w"], "cell_h": geometry["cell_h"],
        "baseline_y": geometry["baseline_y"], "anchor": geometry["anchor"], "same_length": same,
        "rows": [{"row": i, "code": r.code, "view": r.view, "frames": len(r.frames), "delay_ms": r.delay_ms, "name": r.name,
                  "input": str(r.dir), "strip_sha256": r.sha256, "body_h": r.body_h, "body_bottom_y": bottom}
                 for i, (r, bottom) in enumerate(zip(rows, geometry["body_bottom_y"]))],
        "center": ({"png": f"{name}.center.png", **centre_record, "box": geometry["center_box"]} if centre_record else None),
    }
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    sheet.save(out / record["sheet"])
    if centre_cell is not None:
        centre_cell.save(out / record["center"]["png"])
    atomic_write_text(out / f"{name}.sheet.json", json.dumps(record, indent=2) + "\n")
    if not same:
        print(f"{PROG}: warning: the directions differ in length — "
              + ", ".join(f"{r.code} {len(r.frames)} frames at {r.delay_ms:g} ms" for r in rows)
              + "; the sheet is laid all the same, the shorter rows' last cells empty (same_length false). "
                "video-cycle-align gives a set one cycle length", file=sys.stderr)
    return record


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--loop-dir", action="append", type=Path, required=True,
                        help="a video-loop output directory (its <name>.strip.png and .strip.json), aligned or not, one per direction "
                             "(repeat); a strip turned over to face the other way is a direction like any other")
    parser.add_argument("--view", action="append", required=True,
                        help="the view of each --loop-dir, in the same order, as video-cycle-align takes it: front (S), back (N), or "
                             "side|front_diagonal|back_diagonal@right|left (E W, SE SW, NE NW); it names the row")
    parser.add_argument("--center", type=Path,
                        help="a transparent still for the middle of a board (the set's base): scaled to the rows' standing height "
                             "(the median of their body_h) and stood on the same ground line, written as <name>.center.png")
    parser.add_argument("--out-dir", type=Path, required=True, help="where <name>.sheet.png, <name>.sheet.json (and <name>.center.png) go")
    parser.add_argument("--name", default="set", help="the files' stem (default set)")


def run(**kwargs: object) -> int:
    record = make_sheet(list(kwargs["loop_dir"]), kwargs.get("view"), out_dir=Path(kwargs["out_dir"]),  # type: ignore[arg-type]
                        name=str(kwargs.get("name") or "set"), center=kwargs.get("center"))  # type: ignore[arg-type]
    out = Path(kwargs["out_dir"]).expanduser()  # type: ignore[arg-type]
    print(json.dumps({"sheet": str(out / record["sheet"]), **{k: record[k] for k in ("order", "columns", "cell_w", "cell_h",
                                                                                      "baseline_y", "same_length")},
                      "lengths": {r["code"]: r["frames"] for r in record["rows"]},
                      "center": str(out / record["center"]["png"]) if record["center"] else None}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"sprite-gen {PROG}", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
