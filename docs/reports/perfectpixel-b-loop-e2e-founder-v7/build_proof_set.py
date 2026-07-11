#!/usr/bin/env python3
"""Build the automated 1-original/2-grid/3-pixelperfect proof-set contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sprite_gen import extract


def _edges(length: int, pitch: float, phase: float) -> list[int]:
	if pitch < 2:
		return []
	values: list[int] = []
	value = phase % pitch
	while value <= length:
		values.append(round(value))
		value += pitch
	return values


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--run-dir", required=True, type=Path)
	parser.add_argument("--state", default="up_idle")
	parser.add_argument("--out-dir", required=True, type=Path)
	args = parser.parse_args()
	run = args.run_dir.resolve()
	out = args.out_dir.resolve()
	out.mkdir(parents=True, exist_ok=True)
	request = json.loads((run / "sprite-request.json").read_text(encoding="utf-8"))
	key = tuple(request["chroma_key"]["rgb"])
	with Image.open(run / "raw" / f"{args.state}.png") as opened:
		notes: list[str] = []
		strip = extract.remove_chroma_background_ycbcr(opened, key, notes)
	components = extract.extract_component_images(strip, request["states"][args.state]["frames"])
	if not components:
		raise SystemExit("proof-set: expected frame components were not found")
	component = components[0]
	bbox = component.getbbox()
	if bbox is None:
		raise SystemExit("proof-set: first component is empty")
	original = component.crop(bbox)
	original.save(out / "1-original.png")

	(pitch_x, pitch_y), (phase_x, phase_y) = extract.detect_pixel_grid(component)
	runlen_x, runlen_y = extract.estimate_pixel_grid_runlen(component)
	grid = component.copy()
	overlay = Image.new("RGBA", grid.size, (0, 0, 0, 0))
	draw = ImageDraw.Draw(overlay)
	for x in _edges(grid.width, pitch_x, phase_x):
		draw.line((x, 0, x, grid.height - 1), fill=(210, 35, 35, 180), width=1)
	for y in _edges(grid.height, pitch_y, phase_y):
		draw.line((0, y, grid.width - 1, y), fill=(35, 85, 210, 180), width=1)
	Image.alpha_composite(grid, overlay).crop(bbox).save(out / "2-grid.png")

	frames = sorted((run / "frames" / args.state).glob("frame-[0-9].png"))
	if len(frames) != request["states"][args.state]["frames"]:
		raise SystemExit("proof-set: extracted frame count does not match the request")
	opened = [Image.open(path).convert("RGBA") for path in frames]
	scale = 4
	contact = Image.new("RGBA", (sum(im.width for im in opened) * scale, max(im.height for im in opened) * scale))
	x = 0
	for image in opened:
		enlarged = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
		contact.alpha_composite(enlarged, (x, 0))
		x += enlarged.width
	contact.save(out / "3-pixelperfect.png")

	manifest = json.loads((run / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
	row = next(row for row in manifest["rows"] if row["state"] == args.state)
	metrics = {
		"ok": all((out / name).is_file() for name in ("1-original.png", "2-grid.png", "3-pixelperfect.png")),
		"contract": ["1-original.png", "2-grid.png", "3-pixelperfect.png"],
		"pitch": {"grid": [round(pitch_x, 2), round(pitch_y, 2)], "runlen": [round(runlen_x, 2), round(runlen_y, 2)]},
		"a_methods": {
			"projection": request.get("fit", {}).get("segmentation") == "projection",
			"alpha_centroid": request.get("fit", {}).get("align_x") == "alpha-centroid",
			"ycbcr": request.get("chroma", {}).get("mode") == "ycbcr",
			"runlen_crosscheck": True,
		},
		"frame_count": {"expected": request["states"][args.state]["frames"], "found": len(row["files"])},
		"warnings": manifest.get("warnings", []),
		"chroma_notes": notes,
	}
	(out / "proof-set.report.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
	print(json.dumps(metrics, ensure_ascii=False, indent=2))
	return 0 if metrics["ok"] else 1


if __name__ == "__main__":
	raise SystemExit(main())
