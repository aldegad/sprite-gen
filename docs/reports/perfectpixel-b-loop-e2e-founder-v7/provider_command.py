#!/usr/bin/env python3
"""Real-provider callback used by the founder_v7 correction-loop evidence run."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sprite_gen.gen import generate_image


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--source-run", required=True, type=Path)
	parser.add_argument("--next-run-dir", required=True, type=Path)
	parser.add_argument("--prompt-file", required=True, type=Path)
	parser.add_argument("--attempt", required=True, type=int)
	parser.add_argument("--provider", choices=("codex", "grok"), default="grok")
	parser.add_argument("--state", default="up_idle")
	args = parser.parse_args()

	source = args.source_run.resolve()
	current = source if args.attempt == 2 else args.next_run_dir.parent / f"candidate-{args.attempt - 1}"
	next_run = args.next_run_dir.resolve()
	if next_run.exists():
		shutil.rmtree(next_run)
	(next_run / "raw").mkdir(parents=True)
	(next_run / "prompts").mkdir()
	(next_run / "references" / "layout-guides").mkdir(parents=True)

	request = json.loads((current / "sprite-request.json").read_text(encoding="utf-8"))
	request["states"] = {args.state: request["states"][args.state]}
	request.setdefault("fit", {}).update({
		"pixel_perfect": True,
		"segmentation": "projection",
		"align_x": "alpha-centroid",
	})
	request.setdefault("chroma", {}).update({"mode": "ycbcr"})
	(next_run / "sprite-request.json").write_text(
		json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
	)

	base_prompt = (source / "prompts" / f"{args.state}.txt").read_text(encoding="utf-8")
	hints = args.prompt_file.read_text(encoding="utf-8")
	prompt = base_prompt + "\n\nCorrection feedback from the deterministic inspector:\n" + hints
	(next_run / "prompts" / f"{args.state}.txt").write_text(prompt, encoding="utf-8")

	guide_src = source / "references" / "layout-guides" / f"{args.state}.png"
	guide_dst = next_run / "references" / "layout-guides" / guide_src.name
	shutil.copy2(guide_src, guide_dst)
	anchor_src = current / "raw" / f"{args.state}.png"
	anchor_dst = next_run / f"accepted-{args.state}-anchor.png"
	shutil.copy2(anchor_src, anchor_dst)

	result = generate_image(
		args.provider,
		prompt,
		next_run / "raw" / f"{args.state}.png",
		refs=[anchor_dst, guide_dst],
	)
	(next_run / "generation.report.json").write_text(
		json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
	)

	extract = subprocess.run(
		[
			sys.executable,
			str(Path(__file__).resolve().parents[3] / "scripts" / "extract_sprite_row_frames.py"),
			"--run-dir", str(next_run),
			"--states", args.state,
		],
		text=True,
		capture_output=True,
	)
	(next_run / "extract.log").write_text(
		extract.stdout + ("\n--- stderr ---\n" + extract.stderr if extract.stderr else ""),
		encoding="utf-8",
	)
	if extract.returncode != 0:
		print((next_run / "extract.log").read_text(encoding="utf-8"), file=sys.stderr)
		return extract.returncode
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
