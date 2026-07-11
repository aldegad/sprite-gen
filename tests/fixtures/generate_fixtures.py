#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Regenerate the checked-in golden fixture row strips.

The PNGs under tests/fixtures/run/raw/ are the canonical fixture inputs; this
script is only the recorded recipe that produced them. The shapes are sized to
fit inside the cell safe area so extraction never hits the LANCZOS resize
path — every downstream number (bbox, pixel counts) is then pure integer math
and stable across Pillow versions and platforms.

After regenerating, re-record the golden expectations:
    python3 scripts/extract_sprite_row_frames.py --run-dir <copy of tests/fixtures/run>
and refresh tests/fixtures/expected-frames-manifest.json (drop the run_dir key).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

MAGENTA = (255, 0, 255)
CELL = 96


def idle_strip() -> Image.Image:
    strip = Image.new("RGB", (4 * CELL, CELL), MAGENTA)
    draw = ImageDraw.Draw(strip)
    # frame 0: plain rectangle
    draw.rectangle((12, 20, 51, 79), fill=(40, 180, 60))
    # frame 1: rectangle plus a detached satellite that must group to this seed
    draw.rectangle((108, 30, 157, 79), fill=(60, 160, 80))
    draw.rectangle((120, 10, 127, 17), fill=(60, 160, 80))
    # frame 2: ellipse
    draw.ellipse((215, 20, 264, 79), fill=(30, 140, 90))
    # frame 3: connected L-shape
    draw.rectangle((300, 20, 319, 79), fill=(50, 170, 70))
    draw.rectangle((300, 60, 359, 79), fill=(50, 170, 70))
    return strip


def walk_strip() -> Image.Image:
    strip = Image.new("RGB", (3 * CELL, CELL), MAGENTA)
    draw = ImageDraw.Draw(strip)
    # frame 0: tall rectangle
    draw.rectangle((10, 16, 69, 87), fill=(40, 180, 60))
    # frame 1: square
    draw.rectangle((120, 40, 169, 89), fill=(60, 160, 80))
    # frame 2: two overlapping rectangles (single connected component)
    draw.rectangle((200, 20, 229, 49), fill=(30, 140, 90))
    draw.rectangle((215, 45, 259, 84), fill=(30, 140, 90))
    return strip


def fused_kick_strip() -> Image.Image:
    # Three poses whose arms touch the neighbouring pose: connected-component
    # extraction sees ONE blob and fails; projection-profile + DP segmentation
    # (fit.segmentation: "projection") must recover exactly 3 frames by cutting
    # through the thin arm columns. Shapes stay inside the 80x80 safe area of
    # each recovered slice so extraction never hits the LANCZOS resize path.
    strip = Image.new("RGB", (3 * CELL, CELL), MAGENTA)
    draw = ImageDraw.Draw(strip)
    # pose bodies: tall rectangles (the projection peaks)
    draw.rectangle((40, 20, 67, 79), fill=(40, 180, 60))
    draw.rectangle((130, 16, 157, 75), fill=(60, 160, 80))
    draw.rectangle((220, 22, 247, 81), fill=(30, 140, 90))
    # connecting arms: thin bridges (the projection valleys the DP cuts through)
    draw.rectangle((64, 44, 133, 49), fill=(200, 120, 60))
    draw.rectangle((154, 52, 223, 57), fill=(200, 120, 60))
    return strip


def separated_pair_strip() -> Image.Image:
    # Control state for the fused fixture: two cleanly separated poses. It
    # extracts through plain connected components, so the run keeps writing a
    # frames manifest even when the fused state fails without the opt-in.
    strip = Image.new("RGB", (2 * CELL, CELL), MAGENTA)
    draw = ImageDraw.Draw(strip)
    draw.rectangle((30, 24, 69, 83), fill=(40, 180, 60))
    draw.rectangle((126, 24, 165, 83), fill=(60, 160, 80))
    return strip


def main() -> None:
    raw_dir = Path(__file__).parent / "run" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    idle_strip().save(raw_dir / "idle.png")
    walk_strip().save(raw_dir / "walk.png")
    fused_raw_dir = Path(__file__).parent / "run-fused" / "raw"
    fused_raw_dir.mkdir(parents=True, exist_ok=True)
    fused_kick_strip().save(fused_raw_dir / "kick.png")
    separated_pair_strip().save(fused_raw_dir / "pair.png")
    print(f"wrote fixtures to {raw_dir} and {fused_raw_dir}")


if __name__ == "__main__":
    main()
