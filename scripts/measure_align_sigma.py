#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Measure per-frame horizontal jitter σ for `fit.align_x` variants.

For each variant, copies the source run's `sprite-request.json` + `raw/` strips
into a scratch work dir (the source run is never written), patches
`fit.align_x`, re-runs the extraction, and reports the population standard
deviation of the per-frame alpha-weighted centroid X (full alpha and the
bottom-20% foot band) inside the final cells. σ is the jitter a player sees as
the body axis wobbling during playback.

Usage:
  python3 scripts/measure_align_sigma.py \
    --run-dir <run with sprite-request.json + raw/> \
    --states down_walk,down_run \
    --variants foot-centroid,alpha-centroid
"""

import argparse
import json
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import sprite_gen.extract as extract


def _prepare_work_dir(source: Path, work: Path, states: list[str], align_x: str) -> None:
    (work / "raw").mkdir(parents=True, exist_ok=True)
    request = json.loads((source / "sprite-request.json").read_text(encoding="utf-8"))
    fit = dict(request.get("fit") or {})
    fit["align_x"] = align_x
    request["fit"] = fit
    (work / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for state in states:
        raw = source / "raw" / f"{state}.png"
        if not raw.is_file():
            raise SystemExit(f"missing raw strip: {raw}")
        shutil.copyfile(raw, work / "raw" / f"{state}.png")


def _measure_state(work: Path, state: str) -> dict:
    # frame-N.png 만 — frame-N.plain.png (픽셀퍼펙트 전 쌍둥이) 는 제외.
    frame_paths = sorted(
        (path for path in (work / "frames" / state).glob("frame-*.png") if path.stem.split("-")[1].isdigit()),
        key=lambda p: int(p.stem.split("-")[1]),
    )
    if not frame_paths:
        raise SystemExit(f"no extracted frames for {state} in {work}")
    full, foot = [], []
    for path in frame_paths:
        with Image.open(path) as frame:
            cell = frame.convert("RGBA")
            full.append(extract._alpha_centroid_x(cell, 1.0))
            foot.append(extract._alpha_centroid_x(cell, 0.2))
    return {
        "frames": len(frame_paths),
        "sigma_centroid": statistics.pstdev(full),
        "sigma_foot": statistics.pstdev(foot),
        "centroids": [round(value, 2) for value in full],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path, help="source run (read-only)")
    parser.add_argument("--states", default="down_walk,down_run")
    parser.add_argument("--variants", default="foot-centroid,alpha-centroid")
    parser.add_argument("--work-dir", type=Path, default=None, help="scratch root (default: mkdtemp)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of the table")
    args = parser.parse_args()

    source = args.run_dir.expanduser().resolve()
    states = [state.strip() for state in args.states.split(",") if state.strip()]
    variants = [variant.strip() for variant in args.variants.split(",") if variant.strip()]
    work_root = args.work_dir or Path(tempfile.mkdtemp(prefix="align-sigma-"))
    work_root.mkdir(parents=True, exist_ok=True)

    results: dict = {}
    for variant in variants:
        work = work_root / variant
        if work.exists():
            shutil.rmtree(work)
        _prepare_work_dir(source, work, states, variant)
        code = extract.run(run_dir=work, states=",".join(states))
        if code != 0:
            print(f"[warn] extraction for {variant} exited {code} (see warnings above)", file=sys.stderr)
        results[variant] = {state: _measure_state(work, state) for state in states}

    if args.json:
        print(json.dumps({"run_dir": str(source), "results": results}, ensure_ascii=False, indent=2))
        return 0

    print(f"\nrun: {source}")
    print(f"work dirs kept under: {work_root}\n")
    print("| state | align_x | frames | σ centroid (px) | σ foot (px) |")
    print("|---|---|---|---|---|")
    for state in states:
        for variant in variants:
            row = results[variant][state]
            print(
                f"| {state} | {variant} | {row['frames']} "
                f"| {row['sigma_centroid']:.2f} | {row['sigma_foot']:.2f} |"
            )
    for state in states:
        for variant in variants:
            print(f"{state}/{variant} centroids: {results[variant][state]['centroids']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
