#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare a sprite-gen component-row run.

This script owns the numeric sprite recipe. It writes one request JSON, one
layout guide per state, and one prompt per state. Image generation should read
these files instead of hand-copying frame counts into ad hoc prompts.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


DEFAULT_STATES: dict[str, dict[str, Any]] = {
    "idle": {"frames": 4, "fps": 4, "loop": True, "action": "subtle breathing and blinking"},
    "run": {"frames": 6, "fps": 10, "loop": True, "action": "running cycle with limb and body motion only"},
    "jump": {"frames": 4, "fps": 8, "loop": False, "action": "jump arc through body position only"},
    "talk": {"frames": 4, "fps": 6, "loop": True, "action": "mouth and small hand or face gesture"},
}

STYLE_DEFAULT = (
    "compact pixel-adjacent game sprite, square-cell friendly silhouette, "
    "thick dark 1-2 px outline, flat cel shading, limited palette, readable at small size"
)

CHROMA_CANDIDATES = [
    ("magenta", "#FF00FF"),
    ("green", "#00FF00"),
    ("cyan", "#00FFFF"),
    ("blue", "#004DFF"),
]


def parse_hex_color(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise SystemExit(f"invalid chroma key color: {value}; expected #RRGGBB")
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def sampled_reference_pixels(path: Path | None) -> list[tuple[int, int, int]]:
    if path is None or not path.is_file():
        return []
    pixels: list[tuple[int, int, int]] = []
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
        image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        data = image.tobytes()
        for index in range(0, len(data), 4):
            red, green, blue, alpha = data[index : index + 4]
            if alpha <= 16:
                continue
            if red > 244 and green > 244 and blue > 244:
                continue
            pixels.append((red, green, blue))
    return pixels


def choose_chroma_key(reference: Path | None, requested: str) -> dict[str, Any]:
    if requested.lower() != "auto":
        rgb = parse_hex_color(requested)
        return {"name": "manual", "hex": rgb_to_hex(rgb), "rgb": list(rgb), "selection": "manual"}

    pixels = sampled_reference_pixels(reference)
    if not pixels:
        rgb = parse_hex_color("#FF00FF")
        return {"name": "magenta", "hex": "#FF00FF", "rgb": list(rgb), "selection": "fallback"}

    scored: list[tuple[float, int, str, tuple[int, int, int]]] = []
    for preference_index, (name, hex_color) in enumerate(CHROMA_CANDIDATES):
        rgb = parse_hex_color(hex_color)
        distances = sorted(color_distance(rgb, pixel) for pixel in pixels)
        percentile_index = max(0, min(len(distances) - 1, int(len(distances) * 0.01)))
        scored.append((distances[percentile_index], -preference_index, name, rgb))
    score, _preference, name, rgb = max(scored)
    return {
        "name": name,
        "hex": rgb_to_hex(rgb),
        "rgb": list(rgb),
        "selection": "auto",
        "score": round(score, 2),
    }


def normalize_states(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    source = raw or DEFAULT_STATES
    normalized: dict[str, dict[str, Any]] = {}
    for state, entry in source.items():
        if not isinstance(entry, dict):
            raise SystemExit(f"state {state!r} must be an object")
        frames = int(entry.get("frames", 0))
        if frames <= 0:
            raise SystemExit(f"state {state!r} must have positive frames")
        normalized[state] = {
            "frames": frames,
            "fps": int(entry.get("fps", DEFAULT_STATES.get(state, {}).get("fps", 6))),
            "loop": bool(entry.get("loop", True)),
            "action": str(entry.get("action", DEFAULT_STATES.get(state, {}).get("action", state))),
        }
    return normalized


def load_request(path: Path | None, inline_json: str | None) -> dict[str, Any]:
    if path and inline_json:
        raise SystemExit("use only one of --request or --request-json")
    if path:
        return json.loads(path.read_text(encoding="utf-8"))
    if inline_json:
        return json.loads(inline_json)
    return {}


def draw_guide(path: Path, state: str, frames: int, cell_size: int, safe_margin: int) -> None:
    width = frames * cell_size
    height = cell_size
    image = Image.new("RGB", (width, height), "#f6f6f6")
    draw = ImageDraw.Draw(image)
    for index in range(frames):
        left = index * cell_size
        right = left + cell_size - 1
        draw.rectangle((left, 0, right, height - 1), outline="#333333", width=3)
        safe = (
            left + safe_margin,
            safe_margin,
            right - safe_margin,
            height - 1 - safe_margin,
        )
        draw.rectangle(safe, outline="#2f80ed", width=2)
        draw.line((left + cell_size // 2, safe_margin, left + cell_size // 2, height - safe_margin), fill="#b8c8e8", width=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def row_prompt(request: dict[str, Any], state: str, entry: dict[str, Any]) -> str:
    cell = request["cell"]
    chroma = request["chroma_key"]
    character = request["character"]
    frames = int(entry["frames"])
    return f"""Create one horizontal sprite row for state `{state}`.

Use the attached base character image as the canonical identity. Use the attached layout guide only for frame count, slot spacing, centering, and safe margin. Do not draw the guide itself.

Character: {character.get("description") or character["id"]}.
Style: {request["style"]}.
Action: {entry["action"]}.

Hard requirements:
- Exactly {frames} separated full-body poses, left to right, one pose per invisible square slot.
- Each slot is a {cell["size"]}x{cell["size"]} square with at least {cell["safe_margin"]} px safe margin.
- Keep every whole character inside its slot. Do not cross slot boundaries or overlap neighboring poses.
- Preserve the same character identity, face, markings, palette, outline weight, proportions, and props in every frame.
- Use a perfectly flat pure {chroma["name"]} {chroma["hex"]} chroma-key background across the whole image.
- Do not use {chroma["hex"]}, pure {chroma["name"]}, or chroma-adjacent colors in the character, highlights, props, shadows, or effects.
- No shadows, glows, smears, speed lines, dust, landing marks, detached effects, text, UI, scenery, frame numbers, borders, or checkerboard transparency.
- Prefer clean pose changes over decorative effects.

Output only the sprite row image."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--character-id", required=True)
    parser.add_argument("--base-image", type=Path)
    parser.add_argument("--description", default="")
    parser.add_argument("--style", default=STYLE_DEFAULT)
    parser.add_argument("--cell-size", type=int, default=256)
    parser.add_argument("--safe-margin", type=int, default=24)
    parser.add_argument("--chroma-key", default="auto", help="auto or #RRGGBB")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--request-json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out_dir = args.out_dir.expanduser().resolve()
    if out_dir.exists() and any(out_dir.iterdir()) and not args.force:
        raise SystemExit(f"output dir exists and is not empty: {out_dir}; pass --force")

    raw_request = load_request(args.request, args.request_json)
    states = normalize_states(raw_request.get("states"))
    cell_size = int(raw_request.get("cell", {}).get("size", args.cell_size))
    safe_margin = int(raw_request.get("cell", {}).get("safe_margin", args.safe_margin))
    if cell_size <= 0 or safe_margin < 0 or safe_margin * 2 >= cell_size:
        raise SystemExit("cell-size and safe-margin must form a positive square cell")

    out_dir.mkdir(parents=True, exist_ok=True)
    base_dest = None
    if args.base_image:
        base_source = args.base_image.expanduser().resolve()
        if not base_source.is_file():
            raise SystemExit(f"missing base image: {base_source}")
        base_dest = out_dir / f"base-source{base_source.suffix.lower() or '.png'}"
        shutil.copy2(base_source, base_dest)

    chroma_key = choose_chroma_key(base_dest, args.chroma_key)
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {
            "id": args.character_id,
            "description": args.description,
            "base_image": base_dest.name if base_dest else None,
        },
        "cell": {"shape": "square", "size": cell_size, "safe_margin": safe_margin},
        "chroma_key": chroma_key,
        "states": states,
        "style": raw_request.get("style", args.style),
    }

    references = out_dir / "references" / "layout-guides"
    prompts = out_dir / "prompts"
    raw = out_dir / "raw"
    frames = out_dir / "frames"
    for directory in (references, prompts, raw, frames):
        directory.mkdir(parents=True, exist_ok=True)

    for state, entry in states.items():
        draw_guide(references / f"{state}.png", state, int(entry["frames"]), cell_size, safe_margin)
        (prompts / f"{state}.txt").write_text(row_prompt(request, state, entry).rstrip() + "\n", encoding="utf-8")

    (out_dir / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({"ok": True, "run_dir": str(out_dir), "states": list(states)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
