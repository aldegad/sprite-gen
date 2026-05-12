#!/usr/bin/env python3
"""Validate a Hermes sprite-gen master sheet alpha image.

One-shot master-mode generation (gpt-image) does not honour an exact 256px
uniform 6x4 grid: rows come back with uneven frame counts and frames drift off
the cell lattice. So this validator no longer hard-fails on cell alignment.
Instead it requires:

  * the sheet is exactly 1536x1024 (the contract canvas size);
  * `extract_frame_layout.analyze()` can cleanly separate at least
    idle>=3, run>=4, jump>=3, talk>=3 frames per row (i.e. sprites are not
    fused into one alpha blob and the sheet is not a single tiled illustration);
  * each row shows real frame-to-frame motion (the run row across multiple
    adjacent frames);
  * frames look like character cutouts, not full source cards / room art /
    repeated static images.

The recovered layout is echoed into the report as `frame_layout` so the game
runtime can consume it via `manifest.json.frame_layout`
(`three-game-starter/src/sprite-runtime.js` `getSpriteFrame`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_frame_layout as efl  # noqa: E402

EXPECTED_WIDTH = 1536
EXPECTED_HEIGHT = 1024

MIN_FRAMES_PER_ROW = {"idle": 3, "run": 4, "jump": 3, "talk": 3}
# rows that must exist in the recovered layout
REQUIRED_ROWS = ["idle", "run", "jump", "talk"]

NORM_FRAME = (64, 64)  # frames are resized to this before motion comparison


def _frame_image(sheet: Image.Image, frame: dict) -> Image.Image:
    box = (frame["x"], frame["y"], frame["x"] + frame["w"], frame["y"] + frame["h"])
    return sheet.crop(box)


def _frame_alpha_density(sheet: Image.Image, frame: dict) -> float:
    crop = _frame_image(sheet, frame)
    alpha = crop.getchannel("A")
    hist = alpha.histogram()
    opaque = sum(hist[1:])  # any non-zero alpha
    total = max(1, frame["w"] * frame["h"])
    return opaque / float(total)


def _motion_delta(a: Image.Image, b: Image.Image) -> float:
    aa = a.convert("RGBA").resize(NORM_FRAME)
    bb = b.convert("RGBA").resize(NORM_FRAME)
    diff = ImageChops.difference(aa, bb)
    stat = ImageStat.Stat(diff)
    return sum(stat.mean) / len(stat.mean)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True, help="sprite-sheet-alpha.png to validate")
    parser.add_argument("--report", help="optional JSON report path")
    parser.add_argument("--manifest", help="if set, write the recovered frame_layout into this manifest.json")
    parser.add_argument("--min-frame-density", type=float, default=0.12,
                        help="min opaque-pixel ratio inside a frame bbox (below = empty/garbage frame)")
    parser.add_argument("--max-frame-density", type=float, default=0.94,
                        help="max opaque-pixel ratio inside a frame bbox (above = solid card/background, not a cutout)")
    parser.add_argument("--max-frame-width", type=int, default=420,
                        help="a single frame wider than this looks like two fused sprites")
    parser.add_argument("--motion-threshold", type=float, default=1.75,
                        help="mean RGBA delta between adjacent frames counted as 'distinct'")
    parser.add_argument("--run-min-distinct-adjacent", type=int, default=3)
    parser.add_argument("--short-row-min-distinct-adjacent", type=int, default=1)
    args = parser.parse_args()

    image_path = Path(args.image)
    failures: list[str] = []
    frame_layout: dict | None = None
    row_frame_counts: dict[str, int] = {}
    row_motion: dict[str, dict] = {}
    frame_density: dict[str, list[float]] = {}

    if not image_path.exists():
        failures.append(f"missing image: {image_path}")
    else:
        with Image.open(image_path) as source:
            size = source.size
            sheet = source.convert("RGBA")
        if size != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
            failures.append(f"expected {EXPECTED_WIDTH}x{EXPECTED_HEIGHT}, got {size[0]}x{size[1]}")

        try:
            w, h, rows = efl.analyze(image_path)
            frame_layout = {"sheetWidth": w, "sheetHeight": h, "rows": rows}
        except Exception as exc:  # noqa: BLE001
            failures.append(f"frame layout extraction failed: {exc}")
            rows = {}

        if frame_layout is not None:
            row_frame_counts = {name: len(frames) for name, frames in rows.items()}

            for name in REQUIRED_ROWS:
                frames = rows.get(name) or []
                need = MIN_FRAMES_PER_ROW[name]
                if len(frames) < need:
                    failures.append(
                        f"{name} row: only {len(frames)} separable frame(s); need >= {need} "
                        f"(sprites fused into one blob, or sheet is a single tiled illustration?)"
                    )
                    continue

                # frame plausibility: density + width
                densities = []
                for idx, frame in enumerate(frames):
                    d = _frame_alpha_density(sheet, frame)
                    densities.append(round(d, 4))
                    if d < args.min_frame_density:
                        failures.append(
                            f"{name} frame {idx} nearly empty: density {d:.4f} < {args.min_frame_density}"
                        )
                    if d > args.max_frame_density:
                        failures.append(
                            f"{name} frame {idx} looks like a solid card/background, not a cutout: "
                            f"density {d:.4f} > {args.max_frame_density}"
                        )
                    if frame["w"] > args.max_frame_width:
                        failures.append(
                            f"{name} frame {idx} too wide ({frame['w']}px > {args.max_frame_width}px); "
                            f"likely two fused sprites"
                        )
                frame_density[name] = densities

                # motion between adjacent frames
                deltas = []
                for left, right in zip(frames, frames[1:]):
                    deltas.append(round(_motion_delta(_frame_image(sheet, left), _frame_image(sheet, right)), 4))
                distinct = sum(1 for d in deltas if d >= args.motion_threshold)
                min_distinct = args.run_min_distinct_adjacent if name == "run" else args.short_row_min_distinct_adjacent
                row_motion[name] = {
                    "adjacent_deltas": deltas,
                    "distinct_adjacent": distinct,
                    "min_distinct_adjacent": min_distinct,
                    "motion_threshold": args.motion_threshold,
                }
                if distinct < min_distinct:
                    failures.append(
                        f"{name} row: only {distinct} distinct adjacent frame transition(s) "
                        f"(threshold {args.motion_threshold}); need >= {min_distinct} — frames look static/tiled"
                    )

    report = {
        "ok": not failures,
        "image": str(image_path),
        "failures": failures,
        "frame_layout": frame_layout,
        "row_frame_counts": row_frame_counts,
        "frame_density": frame_density,
        "row_motion": row_motion,
        "rules": {
            "expected_size": [EXPECTED_WIDTH, EXPECTED_HEIGHT],
            "min_frames_per_row": MIN_FRAMES_PER_ROW,
            "min_frame_density": args.min_frame_density,
            "max_frame_density": args.max_frame_density,
            "max_frame_width": args.max_frame_width,
            "motion_threshold": args.motion_threshold,
            "run_min_distinct_adjacent": args.run_min_distinct_adjacent,
            "short_row_min_distinct_adjacent": args.short_row_min_distinct_adjacent,
        },
    }

    if not failures and args.manifest and frame_layout is not None:
        manifest_path = Path(args.manifest)
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["frame_layout"] = frame_layout
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            report["manifest_updated"] = str(manifest_path)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
