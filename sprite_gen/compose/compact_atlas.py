# SPDX-License-Identifier: Apache-2.0
"""Trim and deterministically pack a composed atlas into Texture2DArray pages."""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from sprite_gen.spec.runio import acquire_run_dir_lock, atomic_save_image, atomic_write_text


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass
class Item:
    key: tuple[int, int, int, int]
    image: Image.Image
    source_x: int
    source_y: int
    states: set[str]
    page: int = -1
    x: int = -1
    y: int = -1

    @property
    def area(self) -> int:
        return self.image.width * self.image.height


class MaxRectsPage:
    """Deterministic best-short-side-fit MaxRects page without rotation."""

    def __init__(self, size: int):
        self.size = size
        self.free = [Rect(0, 0, size, size)]

    def clone(self) -> "MaxRectsPage":
        other = MaxRectsPage(self.size)
        other.free = list(self.free)
        return other

    def insert(self, width: int, height: int) -> Rect | None:
        candidates: list[tuple[int, int, int, int, Rect]] = []
        for free in self.free:
            if width > free.w or height > free.h:
                continue
            leftover_x = free.w - width
            leftover_y = free.h - height
            candidates.append((min(leftover_x, leftover_y), max(leftover_x, leftover_y), free.y, free.x, free))
        if not candidates:
            return None

        _, _, _, _, free = min(candidates)
        placed = Rect(free.x, free.y, width, height)
        self._split(placed)
        self._prune()
        return placed

    def _split(self, used: Rect) -> None:
        next_free: list[Rect] = []
        for free in self.free:
            if (used.x >= free.x + free.w or used.x + used.w <= free.x
                    or used.y >= free.y + free.h or used.y + used.h <= free.y):
                next_free.append(free)
                continue
            if used.x > free.x:
                next_free.append(Rect(free.x, free.y, used.x - free.x, free.h))
            if used.x + used.w < free.x + free.w:
                next_free.append(Rect(used.x + used.w, free.y,
                                      free.x + free.w - used.x - used.w, free.h))
            if used.y > free.y:
                next_free.append(Rect(free.x, free.y, free.w, used.y - free.y))
            if used.y + used.h < free.y + free.h:
                next_free.append(Rect(free.x, used.y + used.h, free.w,
                                      free.y + free.h - used.y - used.h))
        self.free = [rect for rect in next_free if rect.w > 0 and rect.h > 0]

    def _prune(self) -> None:
        kept: list[Rect] = []
        for index, rect in enumerate(self.free):
            contained = False
            for other_index, other in enumerate(self.free):
                if index == other_index:
                    continue
                if (rect.x >= other.x and rect.y >= other.y
                        and rect.x + rect.w <= other.x + other.w
                        and rect.y + rect.h <= other.y + other.h):
                    contained = True
                    break
            if not contained and rect not in kept:
                kept.append(rect)
        self.free = kept


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--output-manifest", default="manifest.compact.json")
    parser.add_argument("--report", default="compact-atlas.report.json")
    parser.add_argument("--page-prefix", default="sprite-sheet-alpha")
    parser.add_argument("--page-size", type=int, default=2048)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--gutter", type=int, default=2)
    parser.add_argument("--alpha-padding", type=int, default=1)
    parser.add_argument("--max-empty-percent", type=float, default=25.0)
    parser.add_argument("--allow-clip-split-over-threshold", action="store_true")


def _namespace_from_kwargs(**kwargs: object) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    add_arguments(parser)
    values: dict[str, object] = {}
    remaining = dict(kwargs)
    for action in parser._actions:
        if action.dest == "help":
            continue
        value = remaining.pop(action.dest, action.default)
        if getattr(action, "required", False) and value is None:
            raise TypeError(f"missing required argument: {action.dest}")
        values[action.dest] = value
    if remaining:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(sorted(remaining))}")
    return argparse.Namespace(**values)


def _validate_options(args: argparse.Namespace) -> None:
    if args.page_size <= 0 or args.max_pages <= 0:
        raise SystemExit("page-size and max-pages must be positive")
    if args.gutter < 0 or args.alpha_padding < 0:
        raise SystemExit("gutter and alpha-padding must be non-negative")
    if not 0 <= args.max_empty_percent <= 100:
        raise SystemExit("max-empty-percent must be between 0 and 100")


def _source_atlas(manifest: dict[str, Any], run_dir: Path) -> Path:
    name = manifest.get("sprite_sheet_alpha") or manifest.get("game_input")
    if not isinstance(name, str) or not name:
        raise SystemExit("manifest has no sprite_sheet_alpha/game_input")
    path = run_dir / name
    if not path.is_file():
        raise SystemExit(f"source atlas does not exist: {path}")
    return path


def _trim(source: Image.Image, rect: dict[str, Any], padding: int) -> tuple[Image.Image, int, int]:
    x, y = int(rect["x"]), int(rect["y"])
    width, height = int(rect["w"]), int(rect["h"])
    cell = source.crop((x, y, x + width, y + height))
    bbox = cell.getchannel("A").getbbox()
    if bbox is None:
        raise SystemExit(f"frame rect {x},{y},{width},{height} is fully transparent")
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(width, bbox[2] + padding)
    bottom = min(height, bbox[3] + padding)
    source_x = int(rect.get("sourceX", 0)) + left
    source_y = int(rect.get("sourceY", 0)) + top
    return cell.crop((left, top, right, bottom)), source_x, source_y


def _load_items(manifest: dict[str, Any], source: Image.Image, padding: int) -> tuple[dict[tuple[int, int, int, int], Item], dict[str, list[Item]]]:
    rows = manifest.get("frame_layout", {}).get("rows")
    if not isinstance(rows, dict) or not rows:
        raise SystemExit("manifest.frame_layout.rows is required")
    items: dict[tuple[int, int, int, int], Item] = {}
    clips: dict[str, list[Item]] = {}
    for state, frames in rows.items():
        if not isinstance(frames, list) or not frames:
            raise SystemExit(f"frame_layout.rows.{state} must be a non-empty array")
        clip: list[Item] = []
        for rect in frames:
            key = (int(rect["x"]), int(rect["y"]), int(rect["w"]), int(rect["h"]))
            item = items.get(key)
            if item is None:
                image, source_x, source_y = _trim(source, rect, padding)
                item = Item(key, image, source_x, source_y, set())
                items[key] = item
            item.states.add(state)
            clip.append(item)
        clips[state] = clip
    return items, clips


def _unique_items(items: Iterable[Item]) -> list[Item]:
    unique = {item.key: item for item in items}
    return sorted(unique.values(), key=lambda item: (-max(item.image.size), -item.area, item.key))


def _place_sequence(page: MaxRectsPage, items: list[Item], gutter: int) -> tuple[MaxRectsPage, list[Rect]] | None:
    trial = page.clone()
    placements: list[Rect] = []
    for item in _unique_items(items):
        placed = trial.insert(item.image.width + gutter * 2, item.image.height + gutter * 2)
        if placed is None:
            return None
        placements.append(placed)
    return trial, placements


def _commit(items: list[Item], placements: list[Rect], page_index: int, gutter: int) -> None:
    for item, rect in zip(_unique_items(items), placements):
        item.page = page_index
        item.x = rect.x + gutter
        item.y = rect.y + gutter


def _pack_clips(clips: dict[str, list[Item]], page_size: int, gutter: int, max_pages: int) -> list[MaxRectsPage]:
    pages: list[MaxRectsPage] = []
    groups = sorted(clips.items(), key=lambda pair: (
        -sum(item.area for item in _unique_items(pair[1])), pair[0]))
    for state, group in groups:
        candidates: list[tuple[int, MaxRectsPage, list[Rect]]] = []
        for index, page in enumerate(pages):
            result = _place_sequence(page, group, gutter)
            if result is not None:
                candidates.append((index, result[0], result[1]))
        if candidates:
            index, trial, placements = min(candidates, key=lambda value: value[0])
            pages[index] = trial
            _commit(group, placements, index, gutter)
            continue
        if len(pages) >= max_pages:
            raise SystemExit(f"clip-preserving pack needs more than {max_pages} pages (failed at {state})")
        page = MaxRectsPage(page_size)
        result = _place_sequence(page, group, gutter)
        if result is None:
            raise SystemExit(f"clip {state} does not fit one {page_size}x{page_size} page")
        pages.append(result[0])
        _commit(group, result[1], len(pages) - 1, gutter)
    return pages


def _pack_global(items: Iterable[Item], page_size: int, gutter: int, max_pages: int) -> list[MaxRectsPage]:
    pages: list[MaxRectsPage] = []
    for item in _unique_items(items):
        placed = None
        page_index = -1
        for index, page in enumerate(pages):
            candidate = page.insert(item.image.width + gutter * 2, item.image.height + gutter * 2)
            if candidate is not None:
                placed, page_index = candidate, index
                break
        if placed is None:
            if len(pages) >= max_pages:
                raise SystemExit(f"frame pack needs more than {max_pages} pages")
            page = MaxRectsPage(page_size)
            pages.append(page)
            placed = page.insert(item.image.width + gutter * 2, item.image.height + gutter * 2)
            page_index = len(pages) - 1
        if placed is None:
            raise SystemExit(f"frame {item.key} does not fit one {page_size}x{page_size} page")
        item.page = page_index
        item.x = placed.x + gutter
        item.y = placed.y + gutter
    return pages


def _empty_percent(items: Iterable[Item], page_count: int, page_size: int, gutter: int) -> float:
    used = sum((item.image.width + gutter * 2) * (item.image.height + gutter * 2)
               for item in _unique_items(items))
    return 100.0 * (1.0 - used / (page_count * page_size * page_size))


def _clip_pages(clips: dict[str, list[Item]]) -> dict[str, list[int]]:
    return {state: sorted({item.page for item in frames}) for state, frames in clips.items()}


def _run(args: argparse.Namespace) -> int:
    _validate_options(args)
    run_dir = args.run_dir.expanduser().resolve()
    acquire_run_dir_lock(run_dir, "compact_atlas")
    manifest_path = run_dir / args.manifest
    if not manifest_path.is_file():
        raise SystemExit(f"manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_path = _source_atlas(manifest, run_dir)
    with Image.open(source_path) as opened:
        source = opened.convert("RGBA")
    items, clips = _load_items(manifest, source, args.alpha_padding)

    pages = _pack_clips(clips, args.page_size, args.gutter, args.max_pages)
    empty_before = _empty_percent(items.values(), len(pages), args.page_size, args.gutter)
    split_applied = False
    if empty_before > args.max_empty_percent and args.allow_clip_split_over_threshold:
        pages = _pack_global(items.values(), args.page_size, args.gutter, args.max_pages)
        split_applied = True
    empty_after = _empty_percent(items.values(), len(pages), args.page_size, args.gutter)

    page_names = [f"{args.page_prefix}-{index}.png" for index in range(len(pages))]
    page_images = [Image.new("RGBA", (args.page_size, args.page_size), (0, 0, 0, 0)) for _ in pages]
    for item in items.values():
        page_images[item.page].alpha_composite(item.image, (item.x, item.y))

    output = copy.deepcopy(manifest)
    frame_layout = output["frame_layout"]
    frame_layout.update({
        "sheetWidth": args.page_size,
        "sheetHeight": args.page_size,
        "sheetCount": len(pages),
        "sheets": page_names,
        "packing": "maxrects-bssf-array-v1",
    })
    for state, frames in frame_layout["rows"].items():
        compact_frames = []
        for rect, item in zip(frames, clips[state]):
            compact_frames.append({
                "page": item.page,
                "x": item.x,
                "y": item.y,
                "w": item.image.width,
                "h": item.image.height,
                "sourceX": item.source_x,
                "sourceY": item.source_y,
            })
        frame_layout["rows"][state] = compact_frames
    output["sprite_sheet_alpha"] = page_names[0]
    output["sprite_sheet_alpha_pages"] = page_names
    output["game_input"] = page_names[0]
    output["compact_atlas"] = {
        "pageSize": args.page_size,
        "pageCount": len(pages),
        "gutter": args.gutter,
        "alphaPadding": args.alpha_padding,
        "maxEmptyPercent": args.max_empty_percent,
        "emptyPercent": round(empty_after, 4),
        "allowClipSplitOverThreshold": bool(args.allow_clip_split_over_threshold),
        "clipSplitApplied": split_applied,
    }

    for name, image in zip(page_names, page_images):
        atomic_save_image(image, run_dir / name)
    atomic_write_text(run_dir / args.output_manifest,
                      json.dumps(output, ensure_ascii=False, indent=2) + "\n")

    clip_pages = _clip_pages(clips)
    report = {
        "ok": True,
        "sourceManifest": args.manifest,
        "outputManifest": args.output_manifest,
        "pages": page_names,
        "pageCount": len(pages),
        "emptyPercentBeforeSplit": round(empty_before, 4),
        "emptyPercent": round(empty_after, 4),
        "thresholdExceeded": empty_after > args.max_empty_percent,
        "clipSplitApplied": split_applied,
        "clipsSpanningPages": {state: value for state, value in clip_pages.items() if len(value) > 1},
    }
    atomic_write_text(run_dir / args.report, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def run(**kwargs: object) -> int:
    return _run(_namespace_from_kwargs(**kwargs))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
