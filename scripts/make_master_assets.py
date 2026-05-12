#!/usr/bin/env python3
"""Generate built-in sprite-gen guide and skeleton reference assets."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw


SKILL_DIR = Path(__file__).resolve().parents[1]
ASSET_DIR = SKILL_DIR / "assets"

CELL = 256
SAFE_MARGIN = 24
MASTER_COLUMNS = 6
ROWS = [
    ("idle", 4),
    ("run", 6),
    ("jump", 4),
    ("talk", 4),
]


def draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    fill: str,
    dash: int = 8,
    gap: int = 6,
) -> None:
    x1, y1 = start
    x2, y2 = end
    if x1 == x2:
        for y in range(min(y1, y2), max(y1, y2), dash + gap):
            draw.line((x1, y, x2, min(y + dash, max(y1, y2))), fill=fill)
        return
    if y1 == y2:
        for x in range(min(x1, x2), max(x1, x2), dash + gap):
            draw.line((x, y1, min(x + dash, max(x1, x2)), y2), fill=fill)
        return
    raise ValueError("only horizontal or vertical dashed lines are supported")


def draw_guide_cell(draw: ImageDraw.ImageDraw, left: int, top: int) -> None:
    right = left + CELL - 1
    bottom = top + CELL - 1
    draw.rectangle((left, top, right, bottom), outline="#111111", width=2)
    safe_left = left + SAFE_MARGIN
    safe_top = top + SAFE_MARGIN
    safe_right = right - SAFE_MARGIN
    safe_bottom = bottom - SAFE_MARGIN
    draw.rectangle((safe_left, safe_top, safe_right, safe_bottom), outline="#2f80ed", width=2)
    center_x = left + CELL // 2
    center_y = top + CELL // 2
    draw_dashed_line(draw, (center_x, safe_top), (center_x, safe_bottom), fill="#b8b8b8")
    draw_dashed_line(draw, (safe_left, center_y), (safe_right, center_y), fill="#b8b8b8")


def create_row_guide(path: Path, frames: int) -> dict[str, object]:
    image = Image.new("RGB", (frames * CELL, CELL), "#f7f7f7")
    draw = ImageDraw.Draw(image)
    for index in range(frames):
        draw_guide_cell(draw, index * CELL, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return {
        "path": str(path.relative_to(SKILL_DIR)),
        "frames": frames,
        "width": image.width,
        "height": image.height,
    }


def pose_points(state: str, frame: int) -> dict[str, tuple[int, int]]:
    cx = CELL // 2
    cy = CELL // 2
    head_y = 76
    body_y = 132
    foot_y = 206

    if state == "idle":
        bob = [0, 3, 0, -2][frame]
        return {
            "head": (cx, head_y + bob),
            "neck": (cx, 108 + bob),
            "hip": (cx, 158 + bob),
            "left_hand": (cx - 42, 138 + bob),
            "right_hand": (cx + 42, 138 + bob),
            "left_foot": (cx - 28, foot_y + bob),
            "right_foot": (cx + 28, foot_y + bob),
        }
    if state == "run":
        phase = frame % 6
        stride = [-34, -18, 4, 34, 18, -4][phase]
        arm = -stride // 2
        return {
            "head": (cx, head_y),
            "neck": (cx, 108),
            "hip": (cx + stride // 8, 158),
            "left_hand": (cx - 44 + arm, 132),
            "right_hand": (cx + 44 - arm, 132),
            "left_foot": (cx - 24 + stride, foot_y),
            "right_foot": (cx + 24 - stride, foot_y),
        }
    if state == "jump":
        # lift must keep the head ellipse (radius 35) inside the 256px cell:
        # head_y(76) + lift_min - 35 >= 0  ->  lift_min >= -41. Use -24 for headroom
        # because the rendered character head is usually larger than the stick head.
        lift = [0, -12, -24, -8][frame]
        spread = [16, 24, 32, 20][frame]
        return {
            "head": (cx, head_y + lift),
            "neck": (cx, 108 + lift),
            "hip": (cx, 158 + lift),
            "left_hand": (cx - 44, 132 + lift - spread // 3),
            "right_hand": (cx + 44, 132 + lift - spread // 3),
            "left_foot": (cx - spread, foot_y + lift),
            "right_foot": (cx + spread, foot_y + lift),
        }
    if state == "talk":
        hand_y = [144, 126, 118, 132][frame]
        mouth_y = [91, 92, 93, 92][frame]
        return {
            "head": (cx, head_y),
            "mouth": (cx, mouth_y),
            "neck": (cx, 108),
            "hip": (cx, 158),
            "left_hand": (cx - 42, 138),
            "right_hand": (cx + 48, hand_y),
            "left_foot": (cx - 28, foot_y),
            "right_foot": (cx + 28, foot_y),
        }
    raise ValueError(state)


def draw_skeleton_cell(draw: ImageDraw.ImageDraw, left: int, top: int, state: str, frame: int) -> None:
    draw_guide_cell(draw, left, top)
    points = pose_points(state, frame)

    def p(name: str) -> tuple[int, int]:
        x, y = points[name]
        return left + x, top + y

    line = "#555555"
    joint = "#e15b64"
    draw.line((p("head"), p("neck"), p("hip")), fill=line, width=6)
    draw.line((p("neck"), p("left_hand")), fill=line, width=5)
    draw.line((p("neck"), p("right_hand")), fill=line, width=5)
    draw.line((p("hip"), p("left_foot")), fill=line, width=5)
    draw.line((p("hip"), p("right_foot")), fill=line, width=5)
    hx, hy = p("head")
    draw.ellipse((hx - 35, hy - 35, hx + 35, hy + 35), outline=line, width=5)
    for name in ["neck", "hip", "left_hand", "right_hand", "left_foot", "right_foot"]:
        x, y = p(name)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=joint)
    if "mouth" in points:
        mx, my = p("mouth")
        draw.ellipse((mx - 8, my - 3, mx + 8, my + 3), fill="#1f1f1f")


def create_master_skeleton(path: Path) -> dict[str, object]:
    width = MASTER_COLUMNS * CELL
    height = len(ROWS) * CELL
    image = Image.new("RGB", (width, height), "#f7f7f7")
    draw = ImageDraw.Draw(image)
    row_entries = []
    for row_index, (state, frames) in enumerate(ROWS):
        top = row_index * CELL
        for column in range(MASTER_COLUMNS):
            left = column * CELL
            if column < frames:
                draw_skeleton_cell(draw, left, top, state, column)
            else:
                draw_guide_cell(draw, left, top)
                draw.line((left + 42, top + 42, left + CELL - 42, top + CELL - 42), fill="#d0d0d0", width=4)
                draw.line((left + CELL - 42, top + 42, left + 42, top + CELL - 42), fill="#d0d0d0", width=4)
        row_entries.append(
            {
                "state": state,
                "row": row_index,
                "frames": frames,
                "columns": [0, frames - 1],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return {
        "path": str(path.relative_to(SKILL_DIR)),
        "width": width,
        "height": height,
        "columns": MASTER_COLUMNS,
        "rows": len(ROWS),
        "cell_width": CELL,
        "cell_height": CELL,
        "row_entries": row_entries,
    }


def main() -> None:
    guide_dir = ASSET_DIR / "guides" / "square-256"
    guides = {
        state: create_row_guide(guide_dir / f"{state}.png", frames)
        for state, frames in ROWS
    }
    master = create_master_skeleton(ASSET_DIR / "skeletons" / "sprite-gen-master-256.png")
    manifest = {
        "version": 1,
        "cell_width": CELL,
        "cell_height": CELL,
        "safe_margin": SAFE_MARGIN,
        "states": {state: {"frames": frames} for state, frames in ROWS},
        "guides": guides,
        "master_skeleton": master,
        "usage": [
            "Attach master_skeleton plus the character base image for one-shot sheet generation experiments.",
            "Attach row guide plus character base image for safer per-row generation.",
            "Do not allow visible guide boxes, skeleton lines, labels, or guide colors in final generated sprites.",
            "Normalize accepted per-row strips with scripts/normalize_strip_to_grid.py before game use.",
            "One-shot master output does NOT land exactly on this 256px lattice (row frame counts vary, frames drift off-cell). After chroma-key (do NOT run repair_cell_padding on one-shot output — it re-cuts on the 256px grid and shreds drifted sprites), run scripts/extract_frame_layout.py to recover the real per-frame rectangles and write manifest.json.frame_layout; the game must follow frame_layout, not uniform cellWidth slicing.",
        ],
    }
    manifest_path = ASSET_DIR / "sprite-gen-assets.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote={manifest_path}")


if __name__ == "__main__":
    main()
