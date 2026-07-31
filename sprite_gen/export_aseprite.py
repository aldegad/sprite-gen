# SPDX-License-Identifier: Apache-2.0
"""Export a run's runtime manifest as an Aseprite-compatible JSON atlas.

`manifest.json.frame_layout.rows` already repeats a shared rect per playback
instance — the same pattern Aseprite's own `json-array` export uses for its
`frames` list. This module completes that isomorphism into an actual file:
one `frames` array in state playback order, one `meta.frameTags` entry per
state. The output pairs with the existing `sprite-sheet-alpha.png`; no image
is re-encoded.

Consumer contract (why these exact keys): Phaser's `createFromAseprite` reads
`meta.frameTags[].{name,from,to,direction}`, addresses frames by their
stringified global index, and takes per-frame timing from `frames[].duration`.
So `filename` is the global instance index as a string, and `duration` comes
from `animation.rows.<state>.durations_ms` — the timing SSoT.

Not represented here: `animation.rows.<state>.loop` (Aseprite frame tags carry
no loop flag; loop policy stays in `manifest.json`, engines decide at play
time) and pixel edits/transforms (already baked into the atlas by compose).

    sprite-gen export-aseprite --run-dir <run-folder>
"""

from __future__ import annotations

import argparse
import json
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from sprite_gen.runio import atomic_write_text

DEFAULT_OUTPUT = "exports/aseprite.json"


def _package_version() -> str:
    try:
        return package_version("sprite-gen")
    except PackageNotFoundError:  # pragma: no cover — running from a bare checkout
        return "unknown"


def aseprite_json(manifest: dict[str, Any]) -> dict[str, Any]:
    """Pure manifest -> Aseprite `json-array` mapping (no IO).

    Raises KeyError/ValueError loudly on a manifest that misses the runtime
    contract — a partial export that an engine half-loads is worse than none.
    """
    frame_layout = manifest["frame_layout"]
    animation_rows = manifest["animation"]["rows"]
    layout_rows = frame_layout["rows"]
    if set(layout_rows) != set(animation_rows):
        raise ValueError(
            "manifest rows disagree: "
            f"frame_layout has {sorted(layout_rows)}, animation has {sorted(animation_rows)}"
        )

    frames: list[dict[str, Any]] = []
    frame_tags: list[dict[str, Any]] = []
    for state, rects in layout_rows.items():
        anim = animation_rows[state]
        durations = anim.get("durations_ms")
        if not durations:
            fps = float(anim.get("fps", 6)) or 6.0
            durations = [max(1, round(1000.0 / fps))] * len(rects)
        if len(durations) != len(rects):
            raise ValueError(
                f"{state}: {len(rects)} layout rects but {len(durations)} durations_ms entries"
            )
        start = len(frames)
        for rect, duration in zip(rects, durations):
            size = {"w": rect["w"], "h": rect["h"]}
            frames.append({
                # Phaser addresses Aseprite frames by stringified global index
                # (AnimationManager.createFromAseprite: `frameKey = i.toString()`),
                # which is also what Aseprite's documented export settings produce.
                "filename": str(len(frames)),
                "frame": {"x": rect["x"], "y": rect["y"], "w": rect["w"], "h": rect["h"]},
                "rotated": False,
                "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, **size},
                "sourceSize": size,
                "duration": int(duration),
            })
        frame_tags.append({
            "name": state,
            "from": start,
            "to": len(frames) - 1,
            "direction": "forward",
        })

    return {
        "frames": frames,
        "meta": {
            "app": "sprite-gen",
            "version": _package_version(),
            "image": manifest["game_input"],
            "format": "RGBA8888",
            "size": {"w": frame_layout["sheetWidth"], "h": frame_layout["sheetHeight"]},
            "scale": "1",
            "frameTags": frame_tags,
        },
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"path relative to the run dir (default {DEFAULT_OUTPUT})")


def run(run_dir: Path, manifest: str = "manifest.json", output: str = DEFAULT_OUTPUT) -> int:
    manifest_path = run_dir / manifest
    if not manifest_path.is_file():
        print(json.dumps({
            "ok": False,
            "error": f"missing {manifest_path} — run compose-atlas first",
        }, ensure_ascii=False, indent=2))
        return 1
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    exported = aseprite_json(data)

    out_path = run_dir / output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out_path, json.dumps(exported, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "ok": True,
        "output": str(out_path),
        "image": exported["meta"]["image"],
        "frames": len(exported["frames"]),
        "frameTags": [tag["name"] for tag in exported["meta"]["frameTags"]],
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the runtime manifest as an Aseprite-compatible JSON atlas.")
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(**vars(args))


if __name__ == "__main__":
    raise SystemExit(main())
