#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract component-row sprite strips into clean RGBA frames."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image

from runio import acquire_run_dir_lock, atomic_save_image, atomic_write_text


def color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def alpha_nonzero_count(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[1:])


def edge_alpha_count(image: Image.Image, margin: int) -> int:
    alpha = image.getchannel("A")
    width, height = image.size
    total = 0
    for box in (
        (0, 0, width, margin),
        (0, height - margin, width, height),
        (0, 0, margin, height),
        (width - margin, 0, width, height),
    ):
        total += sum(alpha.crop(box).histogram()[1:])
    return total


def key_tint_score(color: tuple[int, int, int], chroma_key: tuple[int, int, int]) -> float:
    keyed_channels = [index for index, value in enumerate(chroma_key) if value >= 192]
    unkeyed_channels = [index for index, value in enumerate(chroma_key) if value < 64]
    if not keyed_channels or not unkeyed_channels:
        return 0.0
    keyed_average = sum(color[index] for index in keyed_channels) / len(keyed_channels)
    unkeyed_average = sum(color[index] for index in unkeyed_channels) / len(unkeyed_channels)
    return keyed_average - unkeyed_average



def remove_chroma_background(
    image: Image.Image,
    chroma_key: tuple[int, int, int],
    threshold: float,
    fringe_threshold: float,
    fringe_delta: float,
) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, alpha = pixels[x, y]
            color = (red, green, blue)
            distance = color_distance(color, chroma_key)
            if alpha and distance <= threshold:
                pixels[x, y] = (0, 0, 0, 0)
            elif alpha and distance <= fringe_threshold and key_tint_score(color, chroma_key) >= fringe_delta:
                pixels[x, y] = (0, 0, 0, 0)
            elif alpha == 0 and (red or green or blue):
                pixels[x, y] = (0, 0, 0, 0)
    return rgba


def connected_components(image: Image.Image) -> list[dict[str, Any]]:
    alpha = image.getchannel("A")
    width, height = image.size
    data = alpha.tobytes()
    visited = bytearray(width * height)
    components: list[dict[str, Any]] = []

    for start, alpha_value in enumerate(data):
        if alpha_value <= 16 or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        pixels: list[int] = []
        min_x = width
        min_y = height
        max_x = 0
        max_y = 0

        while stack:
            current = stack.pop()
            pixels.append(current)
            x = current % width
            y = current // width
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

            for neighbor in (current - 1, current + 1, current - width, current + width):
                if neighbor < 0 or neighbor >= len(data) or visited[neighbor]:
                    continue
                nx = neighbor % width
                if abs(nx - x) > 1:
                    continue
                if data[neighbor] > 16:
                    visited[neighbor] = 1
                    stack.append(neighbor)

        components.append(
            {
                "pixels": pixels,
                "area": len(pixels),
                "bbox": (min_x, min_y, max_x + 1, max_y + 1),
                "center_x": (min_x + max_x + 1) / 2,
            }
        )
    return components


def component_group_image(source: Image.Image, components: list[dict[str, Any]], padding: int = 4) -> Image.Image:
    width, height = source.size
    min_x = max(0, min(component["bbox"][0] for component in components) - padding)
    min_y = max(0, min(component["bbox"][1] for component in components) - padding)
    max_x = min(width, max(component["bbox"][2] for component in components) + padding)
    max_y = min(height, max(component["bbox"][3] for component in components) + padding)
    output = Image.new("RGBA", (max_x - min_x, max_y - min_y), (0, 0, 0, 0))
    source_pixels = source.load()
    output_pixels = output.load()
    for component in components:
        for pixel_index in component["pixels"]:
            x = pixel_index % width
            y = pixel_index // width
            output_pixels[x - min_x, y - min_y] = source_pixels[x, y]
    return output


def cell_geometry(cell: dict[str, Any]) -> tuple[int, int, int, int]:
    width = int(cell.get("width", cell.get("size", 0)))
    height = int(cell.get("height", cell.get("size", 0)))
    safe_margin_x = int(cell.get("safe_margin_x", cell.get("safe_margin", 0)))
    safe_margin_y = int(cell.get("safe_margin_y", cell.get("safe_margin", 0)))
    if width <= 0 or height <= 0:
        raise SystemExit("cell width/height must be positive in sprite-request.json")
    return width, height, safe_margin_x, safe_margin_y


def _alpha_centroid_x(sprite: Image.Image, bottom_fraction: float = 1.0) -> float:
    alpha = sprite.getchannel("A")
    width, height = sprite.size
    pixels = alpha.load()
    y_start = max(0, height - max(2, round(height * bottom_fraction)))
    total = 0
    weighted = 0.0
    for y in range(y_start, height):
        for x in range(width):
            value = pixels[x, y]
            if value:
                total += value
                weighted += value * (x + 0.5)
    if total == 0 and bottom_fraction < 1.0:
        return _alpha_centroid_x(sprite, 1.0)
    return (weighted / total) if total else width / 2.0


def _kcentroid_downscale(sprite: Image.Image, target_width: int, target_height: int) -> Image.Image:
    # Astropulse kCentroid-style pixel-art downscale: each output pixel takes the
    # centroid of the dominant 2-means color cluster of its source block, so dark
    # outlines survive instead of being averaged away (LANCZOS) or arbitrarily
    # sampled (NEAREST when the target grid does not match the art's pixel grid).
    source = sprite.convert("RGBA")
    source_width, source_height = source.size
    src = source.load()
    output = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    out = output.load()
    for oy in range(target_height):
        y0 = oy * source_height // target_height
        y1 = max(y0 + 1, (oy + 1) * source_height // target_height)
        for ox in range(target_width):
            x0 = ox * source_width // target_width
            x1 = max(x0 + 1, (ox + 1) * source_width // target_width)
            block = [src[x, y] for y in range(y0, y1) for x in range(x0, x1)]
            opaque = [p for p in block if p[3] >= 128]
            if len(opaque) * 2 < len(block):
                continue
            if len(opaque) == 1:
                out[ox, oy] = opaque[0]
                continue
            def luma(p):
                return p[0] * 299 + p[1] * 587 + p[2] * 114
            lo = min(opaque, key=luma)
            hi = max(opaque, key=luma)
            centroids = [lo[:3], hi[:3]]
            assign = [0] * len(opaque)
            for _ in range(3):
                for i, p in enumerate(opaque):
                    d0 = sum((p[c] - centroids[0][c]) ** 2 for c in range(3))
                    d1 = sum((p[c] - centroids[1][c]) ** 2 for c in range(3))
                    assign[i] = 0 if d0 <= d1 else 1
                for cluster in (0, 1):
                    members = [p for i, p in enumerate(opaque) if assign[i] == cluster]
                    if members:
                        centroids[cluster] = tuple(sum(p[c] for p in members) // len(members) for c in range(3))
            dominant = 0 if assign.count(0) >= assign.count(1) else 1
            members = [p for i, p in enumerate(opaque) if assign[i] == dominant]
            color = tuple(sum(p[c] for p in members) // len(members) for c in range(3))
            alpha_value = sum(p[3] for p in members) // len(members)
            out[ox, oy] = (color[0], color[1], color[2], alpha_value)
    return output


def fit_to_cell(
    image: Image.Image,
    cell_width: int,
    cell_height: int,
    safe_margin_x: int,
    safe_margin_y: int,
    fit: dict[str, Any] | None = None,
) -> Image.Image:
    # `fit` comes from sprite-request.json ("fit" object) and is opt-in:
    #   resample: "lanczos" (default) | "nearest" | "kcentroid" — kcentroid is the
    #             pixel-art downscale that keeps 1px dark outlines readable
    #   align_x:  "bbox-center" (default) | "centroid" | "foot-centroid" —
    #             centroid stabilizes variable-width poses; foot-centroid anchors on
    #             the bottom 20% alpha (the legs), so trailing hair/capes do not pull
    #             the body off the cell axis (critical for runtime horizontal flip)
    #   align_y:  "center" (default) | "bottom" — bottom pins feet to a shared baseline
    fit = fit or {}
    resample_name = str(fit.get("resample", "lanczos")).lower()
    align_x = str(fit.get("align_x", "bbox-center")).lower()
    align_y = str(fit.get("align_y", "center")).lower()
    bbox = image.getbbox()
    target = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    if bbox is None:
        return target
    sprite = image.crop(bbox)
    max_width = max(1, cell_width - safe_margin_x * 2)
    max_height = max(1, cell_height - safe_margin_y * 2)
    scale = min(max_width / sprite.width, max_height / sprite.height, 1.0)
    if scale != 1.0:
        new_size = (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale)))
        if resample_name == "kcentroid":
            sprite = _kcentroid_downscale(sprite, new_size[0], new_size[1])
        else:
            sprite = sprite.resize(
                new_size,
                Image.Resampling.NEAREST if resample_name == "nearest" else Image.Resampling.LANCZOS,
            )
        cropped = sprite.getbbox()
        if cropped is not None:
            sprite = sprite.crop(cropped)
    if align_x == "foot-centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 0.2))
        left = max(0, min(cell_width - sprite.width, left))
    elif align_x == "centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite))
        left = max(0, min(cell_width - sprite.width, left))
    else:
        left = (cell_width - sprite.width) // 2
    if align_y == "bottom":
        top = max(0, cell_height - safe_margin_y - sprite.height)
    else:
        top = (cell_height - sprite.height) // 2
    target.alpha_composite(sprite, (left, top))
    return target


def extract_component_frames(strip: Image.Image, frame_count: int, cell_width: int, cell_height: int, safe_margin_x: int, safe_margin_y: int, fit: dict[str, Any] | None = None) -> list[Image.Image] | None:
    components = connected_components(strip)
    if not components:
        return None
    largest_area = max(component["area"] for component in components)
    seed_threshold = max(120, largest_area * 0.20)
    seeds = [component for component in components if component["area"] >= seed_threshold]
    if len(seeds) < frame_count:
        seeds = sorted(components, key=lambda component: component["area"], reverse=True)[:frame_count]
    if len(seeds) < frame_count:
        return None

    seeds = sorted(
        sorted(seeds, key=lambda component: component["area"], reverse=True)[:frame_count],
        key=lambda component: component["center_x"],
    )
    seed_ids = {id(seed) for seed in seeds}
    groups: list[list[dict[str, Any]]] = [[seed] for seed in seeds]
    noise_threshold = max(12, largest_area * 0.002)

    for component in components:
        if id(component) in seed_ids or component["area"] < noise_threshold:
            continue
        nearest_index = min(
            range(len(seeds)),
            key=lambda index: abs(seeds[index]["center_x"] - component["center_x"]),
        )
        groups[nearest_index].append(component)

    return [fit_to_cell(component_group_image(strip, group), cell_width, cell_height, safe_margin_x, safe_margin_y, fit) for group in groups]


def extract_slot_frames(strip: Image.Image, frame_count: int, cell_width: int, cell_height: int, safe_margin_x: int, safe_margin_y: int, fit: dict[str, Any] | None = None) -> list[Image.Image]:
    slot_width = strip.width / frame_count
    frames = []
    for index in range(frame_count):
        left = round(index * slot_width)
        right = round((index + 1) * slot_width)
        frames.append(fit_to_cell(strip.crop((left, 0, right, strip.height)), cell_width, cell_height, safe_margin_x, safe_margin_y, fit))
    return frames


def chroma_adjacent_count(image: Image.Image, chroma_key: tuple[int, int, int], threshold: float) -> int:
    count = 0
    data = image.convert("RGBA").tobytes()
    for index in range(0, len(data), 4):
        red, green, blue, alpha = data[index : index + 4]
        if alpha > 16 and color_distance((red, green, blue), chroma_key) <= threshold:
            count += 1
    return count


def inspect_frames(frames: list[Image.Image], chroma_key: tuple[int, int, int], args: argparse.Namespace) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    areas = [alpha_nonzero_count(frame) for frame in frames]
    frame_median = median(areas) if areas else 0
    for index, frame in enumerate(frames):
        nontransparent = areas[index]
        edge = edge_alpha_count(frame, args.edge_margin)
        adjacent = chroma_adjacent_count(frame, chroma_key, args.chroma_adjacent_threshold)
        bbox = frame.getbbox()
        records.append(
            {
                "index": index,
                "nontransparent_pixels": nontransparent,
                "bbox": list(bbox) if bbox else None,
                "edge_pixels": edge,
                "chroma_adjacent_pixels": adjacent,
            }
        )
        if nontransparent < args.min_used_pixels:
            errors.append(f"frame {index:02d} is empty or too sparse ({nontransparent} pixels)")
        if edge > args.edge_pixel_threshold:
            warnings.append(f"frame {index:02d} has {edge} non-transparent edge pixels")
        if adjacent > args.chroma_adjacent_pixel_threshold:
            errors.append(f"frame {index:02d} has {adjacent} chroma-adjacent pixels")
        if frame_median and nontransparent < frame_median * args.small_outlier_ratio:
            warnings.append(f"frame {index:02d} is much smaller than median ({nontransparent} vs {frame_median:.0f})")
        if frame_median and nontransparent > frame_median * args.large_outlier_ratio:
            warnings.append(f"frame {index:02d} is much larger than median ({nontransparent} vs {frame_median:.0f})")
    return errors, warnings, records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--states", default="all")
    parser.add_argument("--key-threshold", type=float, default=96.0)
    parser.add_argument("--fringe-key-threshold", type=float, default=180.0)
    parser.add_argument("--fringe-delta", type=float, default=18.0)
    parser.add_argument("--allow-slot-fallback", action="store_true")
    parser.add_argument("--min-used-pixels", type=int, default=400)
    parser.add_argument("--edge-margin", type=int, default=2)
    parser.add_argument("--edge-pixel-threshold", type=int, default=24)
    parser.add_argument("--chroma-adjacent-threshold", type=float, default=150.0)
    parser.add_argument("--chroma-adjacent-pixel-threshold", type=int, default=120)
    parser.add_argument("--small-outlier-ratio", type=float, default=0.35)
    parser.add_argument("--large-outlier-ratio", type=float, default=2.75)
    args = parser.parse_args()
    if args.fringe_key_threshold < args.key_threshold:
        raise SystemExit("--fringe-key-threshold must be greater than or equal to --key-threshold")

    run_dir = args.run_dir.expanduser().resolve()
    acquire_run_dir_lock(run_dir, "extract_sprite_row_frames")
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    states = list(request["states"]) if args.states == "all" else [state.strip() for state in args.states.split(",") if state.strip()]
    cell_width, cell_height, safe_margin_x, safe_margin_y = cell_geometry(request["cell"])
    fit_config = request.get("fit") or {}
    chroma_key = tuple(int(value) for value in request["chroma_key"]["rgb"])
    frames_root = run_dir / "frames"
    rows = []
    all_errors: list[str] = []
    all_warnings: list[str] = []

    for state in states:
        if state not in request["states"]:
            raise SystemExit(f"unknown state in request: {state}")
        raw_path = run_dir / "raw" / f"{state}.png"
        if not raw_path.is_file():
            all_errors.append(f"{state}: missing raw strip {raw_path}")
            continue
        frame_count = int(request["states"][state]["frames"])
        with Image.open(raw_path) as opened:
            strip = remove_chroma_background(
                opened,
                chroma_key,
                args.key_threshold,
                args.fringe_key_threshold,
                args.fringe_delta,
            )
        frames = extract_component_frames(strip, frame_count, cell_width, cell_height, safe_margin_x, safe_margin_y, fit_config)
        method = "components"
        if frames is None:
            if not args.allow_slot_fallback:
                all_errors.append(f"{state}: could not extract {frame_count} sprite components")
                continue
            frames = extract_slot_frames(strip, frame_count, cell_width, cell_height, safe_margin_x, safe_margin_y, fit_config)
            method = "slots-explicit"

        state_dir = frames_root / state
        state_dir.mkdir(parents=True, exist_ok=True)
        output_paths = []
        for index, frame in enumerate(frames):
            output = state_dir / f"frame-{index}.png"
            atomic_save_image(frame, output)
            output_paths.append(str(output.relative_to(run_dir)))

        errors, warnings, frame_records = inspect_frames(frames, chroma_key, args)
        all_errors.extend(f"{state}: {error}" for error in errors)
        all_warnings.extend(f"{state}: {warning}" for warning in warnings)
        rows.append(
            {
                "state": state,
                "frames": frame_count,
                "method": method,
                "files": output_paths,
                "frame_records": frame_records,
                "ok": not errors,
            }
        )

    result = {
        "ok": not all_errors,
        "engine": "component-row",
        "run_dir": str(run_dir),
        "cell": request["cell"],
        "chroma_key": request["chroma_key"],
        "rows": rows,
        "errors": all_errors,
        "warnings": all_warnings,
    }
    atomic_write_text(frames_root / "frames-manifest.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
