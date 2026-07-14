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

from sprite_gen.curation import apply_pixel_edits, apply_transform, frame_variant, load_curation, pixel_snap_scale, state_pixel_ops, state_plan
from sprite_gen.layout import row_frame_rel
from sprite_gen.extract import require_frames_manifest
from sprite_gen.gif_utils import delay_ticks_to_duration_ms, save_clean_gif
from sprite_gen.runio import read_guard, relative_posix


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


def load_frame(
    run_dir: Path,
    row: dict,
    user_frame: int,
    transform: dict[str, float] | None = None,
    cell_size: tuple[int, int] | None = None,
    variant: str = "pixel",
    snap_scale: int | None = None,
    pixel_ops: dict | None = None,
) -> tuple[Path, Image.Image]:
    path = run_dir / row_frame_rel(row, user_frame - 1, variant)
    if not path.is_file():
        raise SystemExit(f"missing selected frame {user_frame}: {path}")
    image = apply_pixel_edits(Image.open(path).convert("RGBA"), pixel_ops)
    if transform and cell_size:
        image = apply_transform(image, transform, cell_size,
                                snap_scale=snap_scale if variant == "pixel" else None)
    return path, image


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--state", required=True)
    parser.add_argument("--frames", type=parse_frames, help="1-based frame numbers, for example 2,3,4,5; defaults to curation.json selection")
    parser.add_argument("--name", required=True, help="output basename under qa/, without extension")
    parser.add_argument("--duration-ms", type=int, default=190)
    parser.add_argument("--delay-ticks", type=int, help="GIF delay in 1/100 second ticks; overrides --duration-ms")
    parser.add_argument("--note", default="")
    return parser


def _namespace_from_kwargs(**kwargs: object) -> argparse.Namespace:
    parser = _build_parser()
    values: dict[str, object] = {}
    remaining = dict(kwargs)
    for action in parser._actions:
        if action.dest == "help":
            continue
        default = [] if action.nargs == "*" else action.default
        if default is argparse.SUPPRESS:
            default = None
        value = remaining.pop(action.dest, default)
        if getattr(action, "required", False) and value is None:
            raise TypeError(f"missing required argument: {action.dest}")
        values[action.dest] = value
    if remaining:
        names = ", ".join(sorted(remaining))
        raise TypeError(f"unexpected keyword argument(s): {names}")
    return argparse.Namespace(**values)
def _run(args: argparse.Namespace):

    run_dir = args.run_dir.expanduser().resolve()
    # Reader isolation: read manifest + request + curation + frames under one shared read_guard so
    # a concurrent publish swap never yields a mid-swap/partial read (runio.read_guard ↔ publish_guard).
    with read_guard(run_dir):
        return _run_guarded(args, run_dir)


def _run_guarded(args, run_dir):
    frames_manifest = require_frames_manifest(run_dir)  # fail loud if this generation's manifest is absent/corrupt
    rows_by_state = {row["state"]: row for row in frames_manifest.get("rows", [])}
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)

    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    cell = request["cell"]
    cell_size = (
        int(cell.get("width", cell.get("size", 0))),
        int(cell.get("height", cell.get("size", 0))),
    )
    default_count = int(request["states"][args.state]["frames"])
    curation = load_curation(run_dir)
    ordered, transforms = state_plan(curation, args.state, default_count)

    # explicit --frames (1-based) wins; otherwise use the curation.json selection.
    if args.frames is not None:
        user_frames = args.frames
    else:
        user_frames = [index + 1 for index in ordered]

    selected = [
        load_frame(run_dir, rows_by_state[args.state], number, transforms.get(number - 1), cell_size,
                   frame_variant(curation, args.state), pixel_snap_scale(request),
                   state_pixel_ops(curation, args.state).get(number - 1))
        for number in user_frames
    ]
    frame_paths = [path for path, _image in selected]
    frames = [(number, image) for number, (_path, image) in zip(user_frames, selected)]

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
        "selected_user_frames": user_frames,
        "selected_zero_based_frames": [frame - 1 for frame in user_frames],
        "selection_source": "explicit-frames" if args.frames is not None else "curation.json",
        "transforms_applied": {str(n - 1): transforms[n - 1] for n in user_frames if (n - 1) in transforms},
        "duration_ms": duration_ms,
        "delay_ticks": round(duration_ms / 10),
        "loop": True,
        "note": args.note,
        "outputs": {
            "gif": relative_posix(gif_path, run_dir),
            "contact": relative_posix(contact_path, run_dir),
        },
        "source_frames": [
            {
                "user_frame": user_frame,
                "zero_based_frame": user_frame - 1,
                "path": relative_posix(path, run_dir),
                "sha256": sha256(path),
            }
            for user_frame, path in zip(user_frames, frame_paths)
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



def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
