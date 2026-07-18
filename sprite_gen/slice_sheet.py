# SPDX-License-Identifier: Apache-2.0
"""Slice a multi-figure grid sheet into per-cell standing cuts (tachi-e).

One generated image contains a COLSxROWS grid of the same character in
different expressions/poses on a flat chroma background. This module turns
that sheet into per-cell RGBA cuts on a fixed canvas with a shared feet
baseline and a normalized subject height.

Alpha truth is `sprite_gen.extract.remove_chroma_background` (the 4-pass
chain: hard key cut -> key-depth in-band unmix -> soft-alpha unmix ->
trapped-spill despill). This module owns only cell geometry:

- whole-sheet connected components assigned to grid cells by centroid, so a
  figure's own cape/prop keeps its overflow past the cell border while a
  neighbour's overflow stays with the neighbour;
- components wider/taller than ~1.5 cells (figures fused through touching
  props) are split at cell borders and re-labelled *inside* each cell, so a
  neighbour's overhang does not stay fused to the cell's main figure;
- small split fragments touching the cell border are dropped as neighbour
  debris, while detached effects (hearts, sparkles, ZZZ) survive because
  they sit inside the cell;
- every cell's main figure is scaled so its height equals ``target_height``
  (per-cell normalization — generators routinely draw rows at different
  sizes) and its feet rest on ``baseline_y`` of the output canvas.

Field lessons baked in here (Sol Valley cut-in overhaul, 2026-07-09):
sheet-wide max-height scaling preserves the generator's size jitter, grid
cropping without component logic imports neighbour fragments, and skipping
the in-cell re-label leaves a neighbour's hair fused to the figure.
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

from PIL import Image

from sprite_gen.extract import remove_chroma_background

CHROMA_KEYS = {
    "magenta": (255, 0, 255),
    "green": (0, 255, 0),
}

DEFAULT_KEY_THRESHOLD = 80.0
DEFAULT_FRINGE_KEY_THRESHOLD = 180.0
DEFAULT_FRINGE_DELTA = 18.0
DEFAULT_NOISE_MIN = 60
DEFAULT_DEBRIS_FRACTION = 0.30
MERGED_SPAN_FACTOR = 1.5


def parse_chroma_key(value: str) -> tuple[int, int, int]:
    name = value.strip().lower()
    if name in CHROMA_KEYS:
        return CHROMA_KEYS[name]
    text = name.lstrip("#")
    if len(text) == 6:
        return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    raise ValueError(f"unsupported chroma key {value!r}; use magenta, green, or #RRGGBB")


def _components(alpha: bytearray, width: int, height: int) -> list[list[int]]:
    label = bytearray(width * height)
    comps: list[list[int]] = []
    for start in range(width * height):
        if not alpha[start] or label[start]:
            continue
        queue = deque([start])
        label[start] = 1
        pixels: list[int] = []
        while queue:
            index = queue.popleft()
            pixels.append(index)
            x, y = index % width, index // width
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    other = ny * width + nx
                    if alpha[other] and not label[other]:
                        label[other] = 1
                        queue.append(other)
        comps.append(pixels)
    return comps


def _relabel_within_cell(part: list[int], width: int) -> list[list[int]]:
    """Re-run connectivity inside one cell's pixel set.

    After a merged component is split at cell borders, the cell's figure and
    a neighbour's overhang are disconnected *within* the cell even though
    they were one component sheet-wide.
    """
    part_set = set(part)
    seen: set[int] = set()
    subs: list[list[int]] = []
    for start in part:
        if start in seen:
            continue
        queue = deque([start])
        seen.add(start)
        sub: list[int] = []
        while queue:
            index = queue.popleft()
            sub.append(index)
            x, y = index % width, index // width
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                other = (y + dy) * width + (x + dx)
                if other in part_set and other not in seen:
                    seen.add(other)
                    queue.append(other)
        subs.append(sub)
    return subs


def slice_sheet(
    sheet: Path,
    out_dir: Path,
    chroma_key: tuple[int, int, int],
    *,
    grid: tuple[int, int] = (3, 2),
    names: list[str] | None = None,
    prefix: str = "",
    canvas: tuple[int, int] = (512, 768),
    baseline_y: float = 725.0,
    target_height: float = 645.0,
    key_threshold: float = DEFAULT_KEY_THRESHOLD,
    fringe_key_threshold: float = DEFAULT_FRINGE_KEY_THRESHOLD,
    fringe_delta: float = DEFAULT_FRINGE_DELTA,
    unmix_reach: int = 4,
    spill_max_fraction: float = 0.005,
    noise_min: int = DEFAULT_NOISE_MIN,
    debris_fraction: float = DEFAULT_DEBRIS_FRACTION,
) -> list[Path]:
    cols, rows = grid
    cell_count = cols * rows
    if names is not None and len(names) != cell_count:
        raise ValueError(f"--names needs exactly {cell_count} entries for grid {cols}x{rows}")
    if target_height > baseline_y:
        raise ValueError("target height must fit above the feet baseline")

    rgba = remove_chroma_background(
        Image.open(sheet).convert("RGBA"),
        chroma_key,
        key_threshold,
        fringe_key_threshold,
        fringe_delta,
        unmix_reach=unmix_reach,
        spill_max_fraction=spill_max_fraction,
    )
    width, height = rgba.size
    pixels = rgba.load()
    cell_w, cell_h = width / cols, height / rows

    alpha = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            if pixels[x, y][3] > 0:
                alpha[y * width + x] = 1

    def cell_of(index: int) -> int:
        return min(int((index // width) // cell_h), rows - 1) * cols + min(int((index % width) // cell_w), cols - 1)

    buckets: dict[int, list[list[int]]] = {i: [] for i in range(cell_count)}
    for comp in _components(alpha, width, height):
        if len(comp) < noise_min:
            continue
        xs = [i % width for i in comp]
        ys = [i // width for i in comp]
        if (max(xs) - min(xs)) > cell_w * MERGED_SPAN_FACTOR or (max(ys) - min(ys)) > cell_h * MERGED_SPAN_FACTOR:
            parts: dict[int, list[int]] = {}
            for index in comp:
                parts.setdefault(cell_of(index), []).append(index)
            for cell, part in parts.items():
                if len(part) < noise_min:
                    continue
                for sub in _relabel_within_cell(part, width):
                    if len(sub) >= noise_min:
                        buckets[cell].append(sub)
            continue
        centroid = min(int((sum(ys) / len(ys)) // cell_h), rows - 1) * cols + min(
            int((sum(xs) / len(xs)) // cell_w), cols - 1
        )
        buckets[centroid].append(comp)

    # Neighbour-debris rule: fragments touching the cell border and much
    # smaller than the cell's main figure are overhang from a neighbour.
    for cell, comps_in in buckets.items():
        if len(comps_in) < 2:
            continue
        main_size = max(len(c) for c in comps_in)
        row, col = divmod(cell, cols)
        bx0, bx1 = col * cell_w, (col + 1) * cell_w
        by0, by1 = row * cell_h, (row + 1) * cell_h
        kept = []
        for comp in comps_in:
            if len(comp) < main_size * debris_fraction:
                touches = any(
                    abs(i % width - bx0) < 2
                    or abs(i % width - bx1) < 2
                    or abs(i // width - by0) < 2
                    or abs(i // width - by1) < 2
                    for i in comp
                )
                if touches:
                    continue
            kept.append(comp)
        buckets[cell] = kept

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for cell in range(cell_count):
        comps_in = buckets[cell]
        if not comps_in:
            raise SystemExit(f"{sheet}: cell {cell} has no subject after extraction")
        main = max(comps_in, key=len)
        main_ys = [i // width for i in main]
        main_xs = [i % width for i in main]
        main_top, main_bottom = min(main_ys), max(main_ys)
        scale = target_height / (main_bottom - main_top + 1)

        all_pixels = {i for comp in comps_in for i in comp}
        xs = [i % width for i in all_pixels]
        ys = [i // width for i in all_pixels]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        piece = Image.new("RGBA", (x1 - x0 + 1, y1 - y0 + 1), (0, 0, 0, 0))
        piece_px = piece.load()
        for index in all_pixels:
            x, y = index % width, index // width
            piece_px[x - x0, y - y0] = pixels[x, y]
        new_size = (max(1, round(piece.size[0] * scale)), max(1, round(piece.size[1] * scale)))
        piece = piece.resize(new_size, Image.LANCZOS)

        main_center_x = (min(main_xs) + max(main_xs)) / 2
        out = Image.new("RGBA", canvas, (0, 0, 0, 0))
        offset_x = round(canvas[0] / 2 - (main_center_x - x0) * scale)
        offset_y = round(baseline_y - (main_bottom - y0 + 1) * scale)
        out.alpha_composite(piece, (max(0, offset_x), max(0, offset_y)))

        name = names[cell] if names else f"cell-{cell}"
        path = out_dir / f"{prefix}{name}.png"
        out.save(path)
        written.append(path)
        print(
            f"{sheet.name} cell{cell} -> {path.name} "
            f"main_h {main_bottom - main_top + 1} -> {round((main_bottom - main_top + 1) * scale)} "
            f"comps {len(comps_in)}"
        )
    return written


def run(
    *,
    sheet: Path,
    out_dir: Path,
    chroma_key: str,
    grid: tuple[int, int] = (3, 2),
    names: str | None = None,
    prefix: str = "",
    cell_width: int = 512,
    cell_height: int = 768,
    baseline_y: float = 725.0,
    target_height: float = 645.0,
    key_threshold: float = DEFAULT_KEY_THRESHOLD,
    fringe_key_threshold: float = DEFAULT_FRINGE_KEY_THRESHOLD,
    fringe_delta: float = DEFAULT_FRINGE_DELTA,
    fringe_unmix_reach: int = 4,
    spill_max_fraction: float = 0.005,
    noise_min: int = DEFAULT_NOISE_MIN,
    debris_fraction: float = DEFAULT_DEBRIS_FRACTION,
) -> int:
    name_list = [part.strip() for part in names.split(",") if part.strip()] if names else None
    slice_sheet(
        sheet,
        out_dir,
        parse_chroma_key(chroma_key),
        grid=grid,
        names=name_list,
        prefix=prefix,
        canvas=(cell_width, cell_height),
        baseline_y=baseline_y,
        target_height=target_height,
        key_threshold=key_threshold,
        fringe_key_threshold=fringe_key_threshold,
        fringe_delta=fringe_delta,
        unmix_reach=fringe_unmix_reach,
        spill_max_fraction=spill_max_fraction,
        noise_min=noise_min,
        debris_fraction=debris_fraction,
    )
    return 0


def _parse_grid(value: str) -> tuple[int, int]:
    cols, rows = value.lower().split("x")
    return int(cols), int(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sheet", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--chroma-key", required=True, help="magenta, green, or #RRGGBB")
    parser.add_argument("--grid", type=_parse_grid, default=(3, 2), help="COLSxROWS, default 3x2")
    parser.add_argument("--names", help="comma-separated output names, one per cell in reading order")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--cell-width", type=int, default=512)
    parser.add_argument("--cell-height", type=int, default=768)
    parser.add_argument("--baseline-y", type=float, default=725.0)
    parser.add_argument("--target-height", type=float, default=645.0)
    parser.add_argument("--key-threshold", type=float, default=DEFAULT_KEY_THRESHOLD)
    parser.add_argument("--fringe-key-threshold", type=float, default=DEFAULT_FRINGE_KEY_THRESHOLD)
    parser.add_argument("--fringe-delta", type=float, default=DEFAULT_FRINGE_DELTA)
    parser.add_argument("--fringe-unmix-reach", type=int, default=4)
    parser.add_argument("--spill-max-fraction", type=float, default=0.005)
    parser.add_argument("--noise-min", type=int, default=DEFAULT_NOISE_MIN)
    parser.add_argument("--debris-fraction", type=float, default=DEFAULT_DEBRIS_FRACTION)
    args = parser.parse_args(argv)
    return run(**vars(args))


if __name__ == "__main__":
    raise SystemExit(main())
