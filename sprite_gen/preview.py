# SPDX-License-Identifier: Apache-2.0
"""Build motion-QA previews for a sprite-gen run.

For each state in frames/frames-manifest.json this writes:
  qa/<state>-contact.png  - frames left-to-right on a checker so motion is readable
  qa/<state>.gif          - frames played at the state fps (loops)
  qa/all-contact.png      - every state stacked, one row per state

These are QA instruments, not runtime assets. The runtime SSoT stays
manifest.json.frame_layout over sprite-sheet-alpha.png.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from sprite_gen.gif_utils import delay_ticks_to_duration_ms, save_clean_gif


def checker(size: tuple[int, int], square: int = 16) -> Image.Image:
    """Neutral checker so transparent pixels and stray fringe are both visible."""
    w, h = size
    bg = Image.new("RGBA", size, (210, 210, 210, 255))
    px = bg.load()
    for y in range(h):
        for x in range(w):
            if ((x // square) + (y // square)) % 2 == 0:
                px[x, y] = (235, 235, 235, 255)
    return bg


def flatten(frame: Image.Image) -> Image.Image:
    base = checker(frame.size)
    base.alpha_composite(frame)
    return base.convert("RGB")


def load_frames(run_dir: Path, files: list[str]) -> list[Image.Image]:
    return [Image.open(run_dir / rel).convert("RGBA") for rel in files]


def contact_sheet(frames: list[Image.Image], gap: int = 4) -> Image.Image:
    cw = max(f.width for f in frames)
    ch = max(f.height for f in frames)
    n = len(frames)
    sheet = Image.new("RGB", (n * cw + (n + 1) * gap, ch + 2 * gap), (255, 255, 255))
    x = gap
    for f in frames:
        sheet.paste(flatten(f), (x, gap))
        x += cw + gap
    return sheet


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--delay-ticks",
        type=int,
        help="override every GIF preview delay in 1/100 second ticks",
    )
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
    manifest_path = run_dir / "frames" / "frames-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"missing frames manifest: {manifest_path} (run extract first)")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    request_path = run_dir / "sprite-request.json"
    request = json.loads(request_path.read_text(encoding="utf-8")) if request_path.is_file() else {}
    state_meta = request.get("states", {})

    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    state_sheets: list[tuple[str, Image.Image]] = []
    for row in manifest.get("rows", []):
        state = row["state"]
        files = row.get("files", [])
        if not files:
            summary.append({"state": state, "ok": False, "note": "no frame files"})
            continue
        frames = load_frames(run_dir, files)
        fps = int(state_meta.get(state, {}).get("fps", 6)) or 6
        loop = bool(state_meta.get(state, {}).get("loop", True))

        sheet = contact_sheet(frames)
        sheet.save(qa_dir / f"{state}-contact.png")
        state_sheets.append((state, sheet))

        duration = (
            delay_ticks_to_duration_ms(args.delay_ticks)
            if args.delay_ticks
            else max(1, round(1000 / fps))
        )
        save_clean_gif(
            frames,
            qa_dir / f"{state}.gif",
            duration_ms=duration,
            loop=0 if loop else 1,
        )
        summary.append(
            {
                "state": state,
                "ok": True,
                "frames": len(frames),
                "fps": fps,
                "delay_ticks": round(duration / 10),
                "loop": loop,
            }
        )

    # stacked all-state contact sheet
    if state_sheets:
        gap = 8
        width = max(s.width for _, s in state_sheets) + 2 * gap
        height = sum(s.height for _, s in state_sheets) + gap * (len(state_sheets) + 1)
        stacked = Image.new("RGB", (width, height), (255, 255, 255))
        y = gap
        for _state, s in state_sheets:
            stacked.paste(s, (gap, y))
            y += s.height + gap
        stacked.save(qa_dir / "all-contact.png")

    print(json.dumps({"ok": True, "qa_dir": str(qa_dir), "states": summary}, ensure_ascii=False, indent=2))
    return 0



def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
