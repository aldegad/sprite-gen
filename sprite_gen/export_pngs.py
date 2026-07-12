# SPDX-License-Identifier: Apache-2.0
"""Export curated frames back to named PNGs (the inverse of an imported set).

For an imported still set (e.g. a furniture pack), the natural deliverable is
not a single atlas but the same separate PNGs with the curation transform baked
in, keeping each item's original filename so the consuming app needs no change.

Output goes INSIDE the run dir by default (`<run-dir>/curated/`). That folder is
provably writable — the curator already writes `curation.json` there — so this
works the same on macOS, Linux, and Windows without assuming write access to any
other location. The skill never creates folders elsewhere in your project tree;
you copy `curated/` wherever the app needs it. `--out-dir` may target another
path explicitly; if that path cannot be created/written it fails loudly (no
silent fallback to a different location).

    python3 export_curated_pngs.py --run-dir <run-dir> [--state <name>] [--out-dir <path>]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from PIL import Image

from sprite_gen.curation import apply_transform, frame_filename, frame_variant, load_curation, state_plan
from sprite_gen.extract import require_frames_manifest
from sprite_gen.runio import acquire_run_dir_lock, atomic_save_image


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--state", help="state to export; defaults to all states")
    parser.add_argument("--out-dir", type=Path, help="output dir (default: <run-dir>/curated)")
    parser.add_argument("--selected-only", action="store_true", help="export only selected frames")
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
    acquire_run_dir_lock(run_dir, "export_curated_pngs")
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    cell = request["cell"]
    cell_size = (int(cell.get("width", cell.get("size", 0))), int(cell.get("height", cell.get("size", 0))))
    curation = load_curation(run_dir)

    frames_manifest = require_frames_manifest(run_dir)  # fail loud if absent/corrupt
    labels_by_state = {row["state"]: row.get("labels", []) for row in frames_manifest.get("rows", [])}

    out_dir = (args.out_dir.expanduser().resolve() if args.out_dir else run_dir / "curated")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(f"cannot create output dir {out_dir}: {exc}")
    if not os.access(out_dir, os.W_OK):
        raise SystemExit(f"output dir not writable: {out_dir}")

    states = [args.state] if args.state else list(request["states"])
    written = []
    for state in states:
        if state not in request["states"]:
            raise SystemExit(f"unknown state: {state}")
        default_count = int(request["states"][state]["frames"])
        ordered, transforms = state_plan(curation, state, default_count)
        indices = ordered if args.selected_only else list(range(default_count))
        labels = labels_by_state.get(state, [])
        multi_state = len(states) > 1
        for index in indices:
            src_path = run_dir / "frames" / state / frame_filename(index, frame_variant(curation))
            if not src_path.is_file():
                continue
            with Image.open(src_path) as opened:
                baked = apply_transform(opened.convert("RGBA"), transforms.get(index), cell_size)
            name = labels[index] if index < len(labels) and labels[index] else f"frame-{index}"
            filename = f"{state}-{name}.png" if multi_state else f"{name}.png"
            dest = out_dir / filename
            atomic_save_image(baked, dest)
            written.append(str(dest))

    # carry the original meta.json along so the curated set is self-contained
    source_meta = None
    unpack_src = run_dir / "unpack-source.json"
    if unpack_src.is_file():
        info = json.loads(unpack_src.read_text(encoding="utf-8"))
        if info.get("source_dir"):
            candidate = Path(info["source_dir"]) / "meta.json"
            if candidate.is_file():
                shutil.copy2(candidate, out_dir / "meta.json")
                source_meta = str(out_dir / "meta.json")

    print(json.dumps({
        "ok": True,
        "out_dir": str(out_dir),
        "count": len(written),
        "files": written,
        "meta_copied": source_meta,
        "note": "copy this folder into your app; the skill did not write anywhere else",
    }, ensure_ascii=False, indent=2))
    return 0



def run(**kwargs: object):
    return _run(_namespace_from_kwargs(**kwargs))

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
