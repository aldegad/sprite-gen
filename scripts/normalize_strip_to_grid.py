#!/usr/bin/env python3
"""Normalize a generated sprite strip into equal transparent frame cells."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def is_background(pixel: tuple[int, int, int, int], args: argparse.Namespace) -> bool:
    r, g, b, a = pixel
    if a == 0:
        return True
    return (
        r >= args.min_red
        and b >= args.min_blue
        and g <= args.max_green
        and (r - g) >= args.min_red_delta
        and (b - g) >= args.min_blue_delta
    )


def foreground_bbox(image: Image.Image, args: argparse.Namespace) -> tuple[int, int, int, int] | None:
    pixels = image.load()
    width, height = image.size
    left, top = width, height
    right, bottom = -1, -1
    for y in range(height):
        for x in range(width):
            if not is_background(pixels[x, y], args):
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)
    if right < left or bottom < top:
        return None
    return left, top, right + 1, bottom + 1


def column_has_foreground(image: Image.Image, x: int, args: argparse.Namespace) -> bool:
    pixels = image.load()
    for y in range(image.height):
        if not is_background(pixels[x, y], args):
            return True
    return False


def foreground_runs(image: Image.Image, args: argparse.Namespace) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    empty_count = 0
    for x in range(image.width):
        has = column_has_foreground(image, x, args)
        if has:
            if start is None:
                start = x
            empty_count = 0
            continue
        if start is not None:
            empty_count += 1
            if empty_count >= args.min_gap:
                end = x - empty_count + 1
                if end - start >= args.min_component_width:
                    runs.append((start, end))
                start = None
                empty_count = 0
    if start is not None:
        runs.append((start, image.width))
    return runs


def split_evenly(bbox: tuple[int, int, int, int], frames: int) -> list[tuple[int, int]]:
    left, _top, right, _bottom = bbox
    width = right - left
    return [
        (left + round(width * index / frames), left + round(width * (index + 1) / frames))
        for index in range(frames)
    ]


def choose_frame_runs(
    image: Image.Image,
    frames: int,
    bbox: tuple[int, int, int, int],
    args: argparse.Namespace,
) -> tuple[list[tuple[int, int]], str]:
    runs = foreground_runs(image, args)
    if len(runs) == frames:
        return runs, "component-runs"
    return split_evenly(bbox, frames), f"even-split-after-{len(runs)}-runs"


def make_transparent(image: Image.Image, args: argparse.Namespace) -> Image.Image:
    out = image.copy()
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            if is_background(pixels[x, y], args):
                r, g, b, _a = pixels[x, y]
                pixels[x, y] = (r, g, b, 0)
    return out


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    alpha = image.getchannel("A")
    return alpha.getbbox()


def normalize(input_path: Path, out_path: Path, args: argparse.Namespace) -> dict[str, object]:
    source = Image.open(input_path).convert("RGBA")
    bbox = foreground_bbox(source, args)
    if bbox is None:
        raise SystemExit("no foreground pixels found")
    runs, split_method = choose_frame_runs(source, args.frames, bbox, args)

    output = Image.new("RGBA", (args.frames * args.cell_width, args.cell_height), (0, 0, 0, 0))
    frame_reports: list[dict[str, object]] = []

    for index, (left, right) in enumerate(runs[: args.frames]):
        crop = source.crop((left, 0, right, source.height))
        transparent = make_transparent(crop, args)
        content_bbox = alpha_bbox(transparent)
        if content_bbox is None:
            frame_reports.append({"frame": index, "status": "empty"})
            continue
        sprite = transparent.crop(content_bbox)
        max_width = args.cell_width - args.safe_margin_x * 2
        max_height = args.cell_height - args.safe_margin_y * 2
        scale = min(max_width / sprite.width, max_height / sprite.height, 1.0)
        if scale < 1.0:
            sprite = sprite.resize(
                (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))),
                Image.Resampling.LANCZOS,
            )
        x = index * args.cell_width + (args.cell_width - sprite.width) // 2
        y = (args.cell_height - sprite.height) // 2
        output.alpha_composite(sprite, (x, y))
        frame_reports.append(
            {
                "frame": index,
                "source_run": [left, right],
                "sprite_size": [sprite.width, sprite.height],
                "paste": [x, y],
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.save(out_path)
    return {
        "input": str(input_path),
        "out": str(out_path),
        "source_size": [source.width, source.height],
        "output_size": [output.width, output.height],
        "frames": args.frames,
        "cell_width": args.cell_width,
        "cell_height": args.cell_height,
        "split_method": split_method,
        "runs": runs,
        "frame_reports": frame_reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--frames", required=True, type=int)
    parser.add_argument("--cell-width", type=int, default=256)
    parser.add_argument("--cell-height", type=int, default=256)
    parser.add_argument("--safe-margin-x", type=int, default=24)
    parser.add_argument("--safe-margin-y", type=int, default=24)
    parser.add_argument("--min-gap", type=int, default=18)
    parser.add_argument("--min-component-width", type=int, default=24)
    parser.add_argument("--min-red", type=int, default=180)
    parser.add_argument("--min-blue", type=int, default=145)
    parser.add_argument("--max-green", type=int, default=95)
    parser.add_argument("--min-red-delta", type=int, default=110)
    parser.add_argument("--min-blue-delta", type=int, default=80)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = normalize(args.input, args.out, args)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
