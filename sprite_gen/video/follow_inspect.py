# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-follow-inspect` — where a follow-through carries its regions in every cell, with nothing moved.

`video-follow` is given an ellipse over a soft part in the strip's first cell and carries it with the body
into every other cell; inside the carried ellipse every pixel moves, whatever it is a picture of. For the
first cell somebody looked and said where the part is. For the others nobody did: an arm that swings across
a chest is inside the chest's ellipse in those cells, and moves with it. The engine does not read what a
pixel belongs to, and it does not guess it here either: every region is written `ownership: unknown` in
every cell, from its centre to its rim, the first cell included.

This command writes what somebody needs in order to look, and changes nothing. It takes what `video-follow`
takes, reads the loop as `video-follow` would (the strip as cut: `follow.source.png` where a follow-through
was already written, the strip itself where none was) and solves it with the same code (`follow.read_request`,
`follow.solve`). Into `--out-dir`, which is never the loop directory, it writes:

- `follow-inspect.json`, the record: what was read (the strip's and the meta's sha256, the regions, the
  settings, the engine and the way it reads, and `input_id`, one sha256 over those); the follow-through as
  `video-follow` would record it (`follow`); per cell how far the regions are carried (`carry_px`) and, per
  region, the carried ellipse, its offset, how near that is to folding it, the least area of the source a
  pixel of the moved cell takes (`jacobian_min`: 1 where nothing moves, 0 where the picture stops and one
  pixel of the source is drawn across many, under 0 where it would run backwards), how many pixels the
  ellipse covers and how many of them are body, and how many the move changes;
- `follow-inspect.source.png`, every cell of the strip as cut with each region's carried ellipse ringed;
- `follow-inspect.moved.png`, the same cells as `video-follow` would write them, ringed alike.

The two boards are apart so that whoever says what is inside an ellipse can read the cells as cut alone,
without the answer beside them. The record holds no path, no time and nothing of the machine: the same loop
asked the same way writes the same record. `input_id` does not change when `video-follow` is run on the loop
(it reads the strip as cut, and the meta without the `follow` record `video-follow` adds) and changes with
anything the answer is read from — a new cut, an alignment, a region, a setting, the engine. The boards'
`pixels_sha256` is over their pixels, so another PNG writer gives the same one; how the boards are drawn
(`--scale`, `--columns`) is no part of `input_id`. docs/video-pipeline.md section 6.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_set
from sprite_gen.video import follow

VERB = "video-follow-inspect"
KIND = "video-follow-inspection"
SCHEMA_VERSION = 1
RECORD, SOURCE_BOARD, MOVED_BOARD = "follow-inspect.json", "follow-inspect.source.png", "follow-inspect.moved.png"
UNKNOWN = "unknown"  # what is inside a carried ellipse: the engine does not read it
SCALE_DEFAULT = 2  # board pixels per cell pixel: at 2 or more a ring covers no cell pixel whole
COLUMNS_DEFAULT = 8
PLACES = 4  # decimals of the offsets, ratios and areas: under what two machines' FFTs differ by
GROUND = (128, 128, 128)  # under a cell's clear pixels
BETWEEN = (32, 32, 32)  # between the cells, and under their numbers
INK = (255, 255, 255)  # a cell's number
DARK = (0, 0, 0)  # every other dash of a ring, so it shows on any colour
RINGS = ((255, 235, 0), (0, 255, 255), (255, 96, 0), (0, 255, 96), (255, 0, 200), (120, 160, 255))  # by region; they repeat
DASH = 2  # board pixels of a ring in its colour, then as many dark
GAP = 6  # board pixels between cells
DOT = 3  # board pixels per dot of a digit
PAD = 3  # above and under a cell's number
DIGITS = {
    "0": ("###", "# #", "# #", "# #", "###"), "1": (" # ", "## ", " # ", " # ", "###"), "2": ("###", "  #", "###", "#  ", "###"),
    "3": ("###", "  #", "###", "  #", "###"), "4": ("# #", "# #", "###", "  #", "  #"), "5": ("###", "#  ", "###", "  #", "###"),
    "6": ("###", "#  ", "###", "# #", "###"), "7": ("###", "  #", "  #", "  #", "  #"), "8": ("###", "# #", "###", "# #", "###"),
    "9": ("###", "# #", "###", "  #", "###"),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    """One spelling of a JSON value: keys sorted, no spaces, ASCII."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _engine_version() -> str:
    try:
        return package_version("sprite-gen")
    except PackageNotFoundError:  # pragma: no cover - bare checkout
        return "unknown"


def policy() -> dict[str, Any]:
    """The way the motion is read and a region moved: its name and the numbers it is fixed by."""
    return {"name": follow.POLICY, "harmonics": follow.HARMONICS, "alpha_solid": follow.ALPHA_SOLID,
            "worn": [follow.WORN_FROM, follow.WORN_TO], "gain_measured": follow.GAIN_MEASURED, "gain_step": follow.GAIN_STEP,
            "weight": "cos2"}


def jacobian(move_x: np.ndarray, move_y: np.ndarray) -> np.ndarray:
    """Per pixel of a moved cell, but for those on its edge, the area of the source it takes. The moved cell's
    pixel p is read from the source at p less its move, so the area is read between p's neighbours on either
    side, across and down, as half of how far apart those are read: 1 where nothing moves, 0 where the picture
    stops, under 0 where it runs backwards. Between the neighbours on either side, not from p to the next pixel
    one way: read one way, a round region a few pixels across reads steeper on the side it bulges to than its
    weight is anywhere. One row and one column shorter than the cell at each edge."""
    across_x, across_y = (move_x[1:-1, 2:] - move_x[1:-1, :-2]) / 2, (move_y[1:-1, 2:] - move_y[1:-1, :-2]) / 2
    down_x, down_y = (move_x[2:, 1:-1] - move_x[:-2, 1:-1]) / 2, (move_y[2:, 1:-1] - move_y[:-2, 1:-1]) / 2
    return (1 - across_x) * (1 - down_y) - down_x * across_y


def _least(area: np.ndarray | None, mask: np.ndarray) -> tuple[float, list[int] | None]:
    """The least of `area` over the pixels of `mask`, and that pixel (x, y); 1 and no pixel where nothing moves
    there."""
    if area is None:
        return 1.0, None
    inner = mask[1:-1, 1:-1]
    if not inner.any():
        return 1.0, None
    values = np.where(inner, area, np.inf)
    y, x = divmod(int(np.argmin(values)), values.shape[1])
    return float(values[y, x]), [x + 1, y + 1]


def _grown(mask: np.ndarray) -> np.ndarray:
    """`mask` and the pixels beside it: above, under, left and right."""
    grown = mask.copy()
    grown[1:] |= mask[:-1]
    grown[:-1] |= mask[1:]
    grown[:, 1:] |= mask[:, :-1]
    grown[:, :-1] |= mask[:, 1:]
    return grown


def _tile(cell: Image.Image, masks: list[np.ndarray], scale: int) -> np.ndarray:
    """A cell on the board: over the ground, `scale` board pixels to a cell pixel, each region's ring drawn on the
    board pixels just outside its ellipse — inside an ellipse nothing is drawn but another region's ring."""
    ground = Image.new("RGBA", cell.size, (*GROUND, 255))
    ground.alpha_composite(cell)
    tile = np.repeat(np.repeat(np.asarray(ground.convert("RGB")), scale, axis=0), scale, axis=1)
    yy, xx = np.mgrid[0:tile.shape[0], 0:tile.shape[1]]
    lit = (xx + yy) // DASH % 2 == 0
    for i, mask in enumerate(masks):
        covered = np.repeat(np.repeat(mask, scale, axis=0), scale, axis=1)
        ring = _grown(covered) & ~covered
        tile[ring & lit] = RINGS[i % len(RINGS)]
        tile[ring & ~lit] = DARK
    return tile


def _number(board: np.ndarray, text: str, x: int, y: int) -> None:
    for char in text:
        for row, dots in enumerate(DIGITS[char]):
            for col, dot in enumerate(dots):
                if dot == "#":
                    board[y + row * DOT: y + (row + 1) * DOT, x + col * DOT: x + (col + 1) * DOT] = INK
        x += 4 * DOT


def _boards(tiles: list[list[np.ndarray]], columns: int) -> tuple[list[np.ndarray], list[list[int]]]:
    """One board per list of tiles, all laid alike: the cells in order, `columns` to a row, each under its number
    (cell 0 first). With them, per cell, the box (x0, y0, x1, y1) its picture takes on every board."""
    n = len(tiles[0])
    tile_h, tile_w = tiles[0][0].shape[:2]
    label = 5 * DOT + 2 * PAD
    across, down = min(n, columns), math.ceil(n / columns)
    size = (GAP + down * (label + tile_h + GAP), GAP + across * (tile_w + GAP), 3)
    boards = [np.full(size, BETWEEN, np.uint8) for _ in tiles]
    boxes = []
    for k in range(n):
        x0 = GAP + (k % columns) * (tile_w + GAP)
        y0 = GAP + (k // columns) * (label + tile_h + GAP) + label
        boxes.append([x0, y0, x0 + tile_w, y0 + tile_h])
        for board, cells in zip(boards, tiles):
            _number(board, str(k), x0, y0 - label + PAD)
            board[y0: y0 + tile_h, x0: x0 + tile_w] = cells[k]
    return boards, boxes


def _png(board: np.ndarray) -> bytes:
    data = io.BytesIO()
    Image.fromarray(board).save(data, format="PNG")
    return data.getvalue()


def inspect_loop(loop_dir: Path, regions: list[tuple[float, float, float, float]], out_dir: Path, *,
                 gain: float = follow.GAIN_DEFAULT, freq: float = follow.FREQ_DEFAULT, zeta: float = follow.ZETA_DEFAULT,
                 on_fold: str = "refuse", scale: int = SCALE_DEFAULT, columns: int = COLUMNS_DEFAULT) -> dict[str, Any]:
    """The record of what `video-follow` asked the same way does to the loop, written with its two boards into
    `out_dir`. The loop directory is read and not written."""
    request = follow.read_request(loop_dir, regions, gain=gain, freq=freq, zeta=zeta, on_fold=on_fold, verb=VERB)
    out_dir = out_dir.expanduser().resolve()
    if out_dir == request.loop_dir or request.loop_dir in out_dir.parents:
        raise SystemExit(f"{VERB}: --out-dir {out_dir} is in the loop directory; an inspection writes nothing there")
    scale, columns = int(scale), int(columns)
    if scale < 1 or columns < 1:
        raise SystemExit(f"{VERB}: --scale and --columns must be 1 or more")
    # The strip as cut, as `video-follow` reads it; it keeps one beside the loop on its first run, and this does not.
    strip_path = request.source_path if request.source_path.exists() else request.strip_path
    strip_bytes = strip_path.read_bytes()
    answer = follow.solve(request, Image.open(io.BytesIO(strip_bytes)).convert("RGBA"), strip_name=strip_path.name, verb=VERB)
    n, (w, h) = len(answer.cells), answer.cells[0].size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cells, source_tiles, moved_tiles = [], [], []
    for k in range(n):
        carry = answer.carry(k)
        before, moved = np.asarray(answer.cells[k]), answer.moved(k)
        changed = (before != np.asarray(moved)).any(axis=-1)
        solid = before[..., 3] >= follow.ALPHA_SOLID
        field = follow.move_field(xx, yy, answer.moves_in(k), carry)
        area = None if field is None else jacobian(field[1], field[2])
        masks = [follow.region_weight(xx, yy, region, carry) > 0 for region in request.regions]
        entries = []
        for i, ((cx, cy, rx, ry), radius, g, hold, mask) in enumerate(zip(request.regions, answer.radii, answer.gains,
                                                                          answer.held, masks)):
            dx, dy = (0.0, 0.0) if hold else (float(answer.moves[g][0][k]), float(answer.moves[g][1][k]))
            least, at = _least(area, mask)
            entries.append({
                "index": i, "ellipse": [round(cx + float(carry[0]), 6), round(cy + float(carry[1]), 6), rx, ry],
                "offset_px": [round(dx, PLACES) + 0.0, round(dy, PLACES) + 0.0], "reach_px": round(math.hypot(dx, dy), PLACES),
                "fold_ratio": round(follow.fold_ratio(math.hypot(dx, dy), radius), PLACES),
                "jacobian_min": round(least, PLACES), "jacobian_min_at": at,
                "ellipse_px": int(mask.sum()), "solid_px": int((mask & solid).sum()), "changed_px": int((mask & changed).sum()),
                "ownership": UNKNOWN,
            })
        cells.append({
            "index": k, "carry_px": [float(carry[0]), float(carry[1])], "changed_px": int(changed.sum()),
            "jacobian_min": 1.0 if area is None or not area.size else round(float(area.min()), PLACES), "regions": entries,
        })
        source_tiles.append(_tile(answer.cells[k], masks, scale))
        moved_tiles.append(_tile(moved, masks, scale))
    (source_board, moved_board), boxes = _boards([source_tiles, moved_tiles], columns)
    for cell, box in zip(cells, boxes):
        cell["box"] = box
    source_png, moved_png = _png(source_board), _png(moved_board)

    meta = request.meta
    cut_sha256 = _sha256(_canonical({key: value for key, value in meta.items() if key != "follow"}))
    inputs = {
        "strip": {"file": strip_path.name, "sha256": _sha256(strip_bytes), "bytes": len(strip_bytes)},
        # `sha256` is the file's as read; `cut_sha256` is the meta's without the `follow` record `video-follow`
        # adds (one spelling: keys sorted, no spaces), so it is the same before and after a follow-through.
        "meta": {"file": request.meta_path.name, "sha256": _sha256(request.meta_bytes), "bytes": len(request.meta_bytes),
                 "cut_sha256": cut_sha256, "follow_recorded": "follow" in meta},
        "cut": {"frames": n, "w": w, "h": h, "delay_ms": float(meta["delay_ms"])},
        "regions": [list(region) for region in request.regions],
        "settings": {"gain": request.gain, "on_fold": request.on_fold, "freq_hz": request.freq, "zeta": request.zeta},
        "policy": policy(),
        "engine": {"name": "sprite-gen", "version": _engine_version()},
    }
    input_id = _sha256(_canonical({
        "schema_version": SCHEMA_VERSION, "engine": inputs["engine"], "policy": inputs["policy"],
        "strip_sha256": inputs["strip"]["sha256"], "cut_sha256": cut_sha256,
        "regions": inputs["regions"], "settings": inputs["settings"],
    }))
    record = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "input_id": input_id, "inputs": inputs,
        "follow": answer.record(),
        "regions": [{
            "index": i, "ellipse": list(region), "radius_px": radius, "gain": g, "held": hold,
            "gain_limit": None if (limit := follow.gain_limit(answer.reach_per_gain, radius)) is None else round(limit, 3),
            "reach_px": max(cell["regions"][i]["reach_px"] for cell in cells),
            "fold_ratio": max(cell["regions"][i]["fold_ratio"] for cell in cells),
            "jacobian_min": min(cell["regions"][i]["jacobian_min"] for cell in cells),
            "changed_px": sum(cell["regions"][i]["changed_px"] for cell in cells),
            "ring": list(RINGS[i % len(RINGS)]),
        } for i, (region, radius, g, hold) in enumerate(zip(request.regions, answer.radii, answer.gains, answer.held))],
        "cells": cells,
        "boards": {
            "scale": scale, "columns": columns, "size": [int(source_board.shape[1]), int(source_board.shape[0])],
            "ground": list(GROUND),
            "source": {"file": SOURCE_BOARD, "sha256": _sha256(source_png), "pixels_sha256": _sha256(source_board.tobytes())},
            "moved": {"file": MOVED_BOARD, "sha256": _sha256(moved_png), "pixels_sha256": _sha256(moved_board.tobytes())},
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    # The boards and the record that names them, together: a run that fails on the way leaves `out_dir` as it was.
    atomic_write_set({out_dir / SOURCE_BOARD: source_png, out_dir / MOVED_BOARD: moved_png,
                      out_dir / RECORD: json.dumps(record, ensure_ascii=False, indent=2) + "\n"})
    return record


def add_arguments(parser: argparse.ArgumentParser) -> None:
    follow.add_request_arguments(parser)
    parser.add_argument("--out-dir", required=True, type=Path,
                        help=f"where {RECORD}, {SOURCE_BOARD} and {MOVED_BOARD} are written; not the loop directory, which is only read")
    parser.add_argument("--scale", type=int, default=SCALE_DEFAULT,
                        help=f"board pixels per cell pixel (default {SCALE_DEFAULT}; at 2 or more a ring covers no cell pixel whole)")
    parser.add_argument("--columns", type=int, default=COLUMNS_DEFAULT, help=f"cells per row of a board (default {COLUMNS_DEFAULT})")


def run(**kwargs: object) -> int:
    out_dir = Path(str(kwargs["out_dir"]))
    record = inspect_loop(Path(str(kwargs["loop_dir"])), list(kwargs["region"]), out_dir,  # type: ignore[arg-type]
                          gain=float(kwargs.get("gain", follow.GAIN_DEFAULT)), freq=float(kwargs.get("freq", follow.FREQ_DEFAULT)),  # type: ignore[arg-type]
                          zeta=float(kwargs.get("zeta", follow.ZETA_DEFAULT)), on_fold=str(kwargs.get("on_fold") or "refuse"),  # type: ignore[arg-type]
                          scale=int(kwargs.get("scale", SCALE_DEFAULT)), columns=int(kwargs.get("columns", COLUMNS_DEFAULT)))  # type: ignore[call-overload]
    for note in follow.fold_notes(record["follow"], VERB):
        print(note, file=sys.stderr)
    out_dir = out_dir.expanduser().resolve()
    print(json.dumps({
        "record": str(out_dir / RECORD), "input_id": record["input_id"], "cells": len(record["cells"]),
        "region_gains": [region["gain"] for region in record["regions"]],
        "held": [region["index"] for region in record["regions"] if region["held"]],
        "ownership": UNKNOWN,
        "boards": {name: {"path": str(out_dir / record["boards"][name]["file"]), "pixels_sha256": record["boards"][name]["pixels_sha256"]}
                   for name in ("source", "moved")},
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen video-follow-inspect", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
