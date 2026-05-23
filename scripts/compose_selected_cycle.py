#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compose a QA-approved manual frame subset into a selected cycle.

This is for cases where generation produces a larger row, but motion QA finds
that only a human-selected subset is usable. The original extracted frame files
remain the source; this script writes a small selected-cycle manifest plus GIF
and contact-sheet previews.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

from gif_utils import delay_ticks_to_duration_ms, save_clean_gif


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checker(size: tuple[int, int], square: int = 16) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (210, 210, 210, 255))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            if ((x // square) + (y // square)) % 2 == 0:
                pixels[x, y] = (235, 235, 235, 255)
    return image


def flatten(frame: Image.Image) -> Image.Image:
    base = checker(frame.size)
    base.alpha_composite(frame)
    return base.convert("RGB")


def parse_frames(value: str) -> list[int]:
    frames = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not frames:
        raise argparse.ArgumentTypeError("at least one frame number is required")
    if any(frame <= 0 for frame in frames):
        raise argparse.ArgumentTypeError("frame numbers are 1-based and must be positive")
    return frames


def load_frame(run_dir: Path, state: str, user_frame: int) -> tuple[Path, Image.Image]:
    path = run_dir / "frames" / state / f"frame-{user_frame - 1}.png"
    if not path.is_file():
        raise SystemExit(f"missing selected frame {user_frame}: {path}")
    return path, Image.open(path).convert("RGBA")


def contact_sheet(frames: list[tuple[int, Image.Image]], gap: int = 4, label_height: int = 24) -> Image.Image:
    cell_width = max(frame.width for _number, frame in frames)
    cell_height = max(frame.height for _number, frame in frames)
    width = len(frames) * cell_width + (len(frames) + 1) * gap
    height = cell_height + label_height + gap * 2
    sheet = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    x = gap
    for number, frame in frames:
        draw.rectangle((x, gap, x + cell_width - 1, gap + label_height - 1), fill=(24, 24, 24))
        draw.text((x + 6, gap + 5), f"frame {number}", fill=(255, 255, 255))
        sheet.paste(flatten(frame), (x, gap + label_height))
        x += cell_width + gap
    return sheet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--state", required=True)
    parser.add_argument("--frames", required=True, type=parse_frames, help="1-based frame numbers, for example 2,3,4,5")
    parser.add_argument("--name", required=True, help="output basename under qa/, without extension")
    parser.add_argument("--duration-ms", type=int, default=190)
    parser.add_argument("--delay-ticks", type=int, help="GIF delay in 1/100 second ticks; overrides --duration-ms")
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)

    selected = [load_frame(run_dir, args.state, frame_number) for frame_number in args.frames]
    frame_paths = [path for path, _image in selected]
    frames = [(number, image) for number, (_path, image) in zip(args.frames, selected)]

    duration_ms = delay_ticks_to_duration_ms(args.delay_ticks) if args.delay_ticks else max(1, args.duration_ms)
    gif_path = qa_dir / f"{args.name}.gif"
    save_clean_gif(
        [frame for _number, frame in frames],
        gif_path,
        duration_ms=duration_ms,
        loop=0,
    )

    contact_path = qa_dir / f"{args.name}-contact.png"
    contact_sheet(frames).save(contact_path)

    manifest = {
        "version": 1,
        "kind": "sprite-gen-selected-cycle",
        "run_dir": str(run_dir),
        "state": args.state,
        "name": args.name,
        "selected_user_frames": args.frames,
        "selected_zero_based_frames": [frame - 1 for frame in args.frames],
        "duration_ms": duration_ms,
        "delay_ticks": round(duration_ms / 10),
        "loop": True,
        "note": args.note,
        "outputs": {
            "gif": str(gif_path.relative_to(run_dir)),
            "contact": str(contact_path.relative_to(run_dir)),
        },
        "source_frames": [
            {
                "user_frame": user_frame,
                "zero_based_frame": user_frame - 1,
                "path": str(path.relative_to(run_dir)),
                "sha256": sha256(path),
            }
            for user_frame, path in zip(args.frames, frame_paths)
        ],
    }
    manifest_path = qa_dir / f"{args.name}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(manifest_path),
                "gif": str(gif_path),
                "contact": str(contact_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
