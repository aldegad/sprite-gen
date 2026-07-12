# SPDX-License-Identifier: Apache-2.0
"""Compose selected sprite frames into a clean transparent GIF.

This is the reusable sprite-gen GIF exporter. It is intentionally small:
source frame PNGs remain the SSoT, while this script only chooses order and
timing for preview/runtime GIFs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from sprite_gen.curation import apply_transform, frame_filename, frame_variant, load_curation, state_plan
from sprite_gen.extract import require_frames_manifest
from sprite_gen.runio import read_guard
from sprite_gen.gif_utils import delay_ticks_to_duration_ms, gif_report, save_clean_gif


def parse_frame_order(value: str) -> list[int]:
    frames = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not frames:
        raise argparse.ArgumentTypeError("frame order must contain at least one frame")
    if any(frame <= 0 for frame in frames):
        raise argparse.ArgumentTypeError("frame order is 1-based and must be positive")
    return frames


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
    base.alpha_composite(frame.convert("RGBA"))
    return base.convert("RGB")


def load_frames(args: argparse.Namespace) -> list[tuple[int, Path, Image.Image]]:
    if args.frame_dir:
        frame_dir = args.frame_dir.expanduser().resolve()
        order = args.frame_order
        frames = []
        for user_frame in order:
            path = frame_dir / f"frame-{user_frame - 1}.png"
            if not path.is_file():
                raise SystemExit(f"missing frame {user_frame}: {path}")
            frames.append((user_frame, path, Image.open(path).convert("RGBA")))
        return frames

    if args.inputs:
        frames = []
        for index, path in enumerate(args.inputs, start=1):
            resolved = path.expanduser().resolve()
            if not resolved.is_file():
                raise SystemExit(f"missing input frame: {resolved}")
            frames.append((index, resolved, Image.open(resolved).convert("RGBA")))
        return frames

    raise SystemExit("provide either --frame-dir with --frame-order, or input frame files")


def contact_sheet(frames: list[tuple[int, Path, Image.Image]], gap: int = 4, label_height: int = 24) -> Image.Image:
    cell_width = max(frame.width for _number, _path, frame in frames)
    cell_height = max(frame.height for _number, _path, frame in frames)
    width = len(frames) * cell_width + (len(frames) + 1) * gap
    height = cell_height + label_height + gap * 2
    sheet = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    x = gap
    for number, _path, frame in frames:
        draw.rectangle((x, gap, x + cell_width - 1, gap + label_height - 1), fill=(24, 24, 24))
        draw.text((x + 6, gap + 5), f"frame {number}", fill=(255, 255, 255))
        sheet.paste(flatten(frame), (x, gap + label_height))
        x += cell_width + gap
    return sheet


def run_dir_mode(args: argparse.Namespace) -> int:
    """Export one clean GIF per state from a finished run dir.

    Reads `sprite-request.json` (per-state frames + fps), applies the
    `curation.json` selection/order/transform via the shared `curation` module
    (SSoT with the atlas compose), and writes `<out-dir>/<state>.gif` plus a
    `gif-manifest.json`. This is the path both the v1 webview server and the v2
    desktop app call so they never reimplement frame selection.
    """
    run_dir = args.run_dir.expanduser().resolve()
    # Reader isolation: read manifest + request + curation + frame tree under one shared read_guard
    # so a concurrent publish swap never yields a mid-swap/partial read (runio.read_guard ↔ publish_guard).
    with read_guard(run_dir):
        return _run_dir_mode_guarded(args, run_dir)


def _run_dir_mode_guarded(args, run_dir):
    require_frames_manifest(run_dir)  # fail loud if this generation's manifest is absent/corrupt
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    cell = request.get("cell", {})
    cell_w = int(cell.get("width") or cell.get("size") or 0)
    cell_h = int(cell.get("height") or cell.get("size") or 0)
    states = request.get("states", {})
    curation = load_curation(run_dir)
    out_root = (args.out_dir.expanduser().resolve() if args.out_dir else run_dir / "exports")
    out_root.mkdir(parents=True, exist_ok=True)

    exports: list[dict] = []
    for state, spec in states.items():
        count = int(spec.get("frames", 0))
        if count <= 0:
            continue
        fps = float(spec.get("fps", 8)) or 8.0
        ordered, transforms = state_plan(curation, state, count)
        images: list[Image.Image] = []
        for index in ordered:
            path = run_dir / "frames" / state / frame_filename(index, frame_variant(curation))
            if not path.is_file():
                raise SystemExit(
                    f"selected frame {path} is missing — the generation is incomplete or the "
                    f"'{frame_variant(curation)}' variant was not baked (re-extract before composing); "
                    f"skipping it would silently produce a shorter GIF."
                )
            frame = Image.open(path).convert("RGBA")
            if cell_w and cell_h:
                frame = apply_transform(frame, transforms.get(index), (cell_w, cell_h))
            images.append(frame)
        if not images:
            continue
        out_path = out_root / f"{state}.gif"
        duration_ms = max(1, round(1000.0 / fps))
        save_clean_gif(images, out_path, duration_ms=duration_ms, loop=args.loop_count, alpha_threshold=args.alpha_threshold)
        exports.append({
            "state": state,
            "output": str(out_path),
            "frames": len(images),
            "fps": fps,
            "loop": bool(spec.get("loop", True)),
            "gif_report": gif_report(out_path),
        })

    manifest = {"version": 1, "kind": "sprite-gen-gif-run", "run_dir": str(run_dir), "exports": exports}
    (out_root / "gif-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, **manifest}, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", type=Path, help="ordered frame PNG files")
    parser.add_argument("--frame-dir", type=Path, help="directory containing frame-0.png, frame-1.png, ...")
    parser.add_argument("--frame-order", type=parse_frame_order, help="1-based order, for example 2,1,5,3")
    parser.add_argument("--run-dir", type=Path, help="run dir: export one GIF per state from sprite-request + curation")
    parser.add_argument("--out-dir", type=Path, help="output dir for --run-dir mode (default <run-dir>/exports)")
    parser.add_argument("--output", type=Path, help="output GIF (required unless --run-dir)")
    parser.add_argument("--delay-ticks", type=int, default=17, help="GIF delay in 1/100 second ticks")
    parser.add_argument("--loop-count", type=int, default=0, help="0 means infinite loop")
    parser.add_argument("--contact-output", type=Path)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--alpha-threshold", type=int, default=8)
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

    if args.run_dir:
        return run_dir_mode(args)

    if not args.output:
        raise SystemExit("--output is required unless --run-dir is used")

    if bool(args.frame_dir) != bool(args.frame_order):
        raise SystemExit("--frame-dir and --frame-order must be used together")

    frames = load_frames(args)
    output = args.output.expanduser().resolve()
    duration_ms = delay_ticks_to_duration_ms(args.delay_ticks)
    save_clean_gif(
        [frame for _number, _path, frame in frames],
        output,
        duration_ms=duration_ms,
        loop=args.loop_count,
        alpha_threshold=args.alpha_threshold,
    )

    contact_path = args.contact_output.expanduser().resolve() if args.contact_output else None
    if contact_path:
        contact_path.parent.mkdir(parents=True, exist_ok=True)
        contact_sheet(frames).save(contact_path)

    manifest = {
        "version": 1,
        "kind": "sprite-gen-gif",
        "output": str(output),
        "delay_ticks": args.delay_ticks,
        "duration_ms": duration_ms,
        "loop_count": args.loop_count,
        "selected_user_frames": [number for number, _path, _frame in frames],
        "source_frames": [str(path) for _number, path, _frame in frames],
        "contact": str(contact_path) if contact_path else None,
        "gif_report": gif_report(output),
    }

    manifest_path = args.manifest_output.expanduser().resolve() if args.manifest_output else None
    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"ok": True, **manifest}, ensure_ascii=False, indent=2))
    return 0



def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
