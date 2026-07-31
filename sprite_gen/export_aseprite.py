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

Flutter/Flame consumes the same family through two extra switches: Flame's
`SpriteAnimation.fromAsepriteData` reads `frames` as a **map** (json-hash) and
builds ONE animation from the whole file without ever reading
`meta.frameTags`, so `--format json-hash --split-states` writes one
per-state file that loads as exactly one animation.

    sprite-gen export-aseprite --run-dir <run-folder>
    sprite-gen export-aseprite --run-dir <run-folder> --format json-hash --split-states
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


def _validated_rows(manifest: dict[str, Any]) -> dict[str, Any]:
    layout_rows = manifest["frame_layout"]["rows"]
    animation_rows = manifest["animation"]["rows"]
    if set(layout_rows) != set(animation_rows):
        raise ValueError(
            "manifest rows disagree: "
            f"frame_layout has {sorted(layout_rows)}, animation has {sorted(animation_rows)}"
        )
    return layout_rows


def _state_durations(state: str, anim: dict[str, Any], rect_count: int) -> list[int]:
    durations = anim.get("durations_ms")
    if not durations:
        fps = float(anim.get("fps", 6)) or 6.0
        durations = [max(1, round(1000.0 / fps))] * rect_count
    if len(durations) != rect_count:
        raise ValueError(
            f"{state}: {rect_count} layout rects but {len(durations)} durations_ms entries"
        )
    return [int(d) for d in durations]


def _frame_entry(rect: dict[str, Any], duration: int, filename: str) -> dict[str, Any]:
    size = {"w": rect["w"], "h": rect["h"]}
    return {
        # Phaser addresses Aseprite frames by stringified index
        # (AnimationManager.createFromAseprite: `frameKey = i.toString()`),
        # which is also what Aseprite's documented export settings produce.
        "filename": filename,
        "frame": {"x": rect["x"], "y": rect["y"], "w": rect["w"], "h": rect["h"]},
        "rotated": False,
        "trimmed": False,
        "spriteSourceSize": {"x": 0, "y": 0, **size},
        "sourceSize": size,
        "duration": duration,
    }


def _meta(manifest: dict[str, Any], frame_tags: list[dict[str, Any]]) -> dict[str, Any]:
    frame_layout = manifest["frame_layout"]
    return {
        "app": "sprite-gen",
        "version": _package_version(),
        "image": manifest["game_input"],
        "format": "RGBA8888",
        "size": {"w": frame_layout["sheetWidth"], "h": frame_layout["sheetHeight"]},
        "scale": "1",
        "frameTags": frame_tags,
    }


def _hashed(frames: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # Aseprite `json-hash`: the filename is the key, not an entry field.
    # Insertion order is playback order — dicts preserve it, and Flame's
    # `fromAsepriteData` iterates `jsonFrames.values` in that order.
    return {e["filename"]: {k: v for k, v in e.items() if k != "filename"} for e in frames}


def aseprite_json(manifest: dict[str, Any], *, fmt: str = "json-array") -> dict[str, Any]:
    """Pure manifest -> Aseprite mapping (no IO). `fmt` mirrors Aseprite's own
    `--format` flag: `json-array` (frames list) or `json-hash` (frames map).

    Raises KeyError/ValueError loudly on a manifest that misses the runtime
    contract — a partial export that an engine half-loads is worse than none.
    """
    if fmt not in ("json-array", "json-hash"):
        raise ValueError(f"unknown format: {fmt!r} (expected json-array or json-hash)")
    layout_rows = _validated_rows(manifest)
    animation_rows = manifest["animation"]["rows"]

    frames: list[dict[str, Any]] = []
    frame_tags: list[dict[str, Any]] = []
    for state, rects in layout_rows.items():
        durations = _state_durations(state, animation_rows[state], len(rects))
        start = len(frames)
        for rect, duration in zip(rects, durations):
            frames.append(_frame_entry(rect, duration, str(len(frames))))
        frame_tags.append({
            "name": state,
            "from": start,
            "to": len(frames) - 1,
            "direction": "forward",
        })

    return {
        "frames": _hashed(frames) if fmt == "json-hash" else frames,
        "meta": _meta(manifest, frame_tags),
    }


def split_state_jsons(manifest: dict[str, Any], *, fmt: str = "json-hash") -> dict[str, dict[str, Any]]:
    """One Aseprite doc per state, frames indexed locally from "0".

    This is the shape Flame's `SpriteAnimation.fromAsepriteData` consumes: it
    reads `frames` as a map and builds ONE animation from all of its values
    (`meta.frameTags` is never read), so a whole-run file would play every
    state concatenated. Per-state files make each one a single animation while
    still pointing at the same atlas image.
    """
    if fmt not in ("json-array", "json-hash"):
        raise ValueError(f"unknown format: {fmt!r} (expected json-array or json-hash)")
    layout_rows = _validated_rows(manifest)
    animation_rows = manifest["animation"]["rows"]

    docs: dict[str, dict[str, Any]] = {}
    for state, rects in layout_rows.items():
        durations = _state_durations(state, animation_rows[state], len(rects))
        frames = [_frame_entry(rect, duration, str(i))
                  for i, (rect, duration) in enumerate(zip(rects, durations))]
        tag = [{"name": state, "from": 0, "to": len(frames) - 1, "direction": "forward"}]
        docs[state] = {
            "frames": _hashed(frames) if fmt == "json-hash" else frames,
            "meta": _meta(manifest, tag),
        }
    return docs


DEFAULT_SPLIT_DIR = "exports/aseprite"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--output", default=None,
                        help=f"path relative to the run dir (default {DEFAULT_OUTPUT}; "
                             f"with --split-states, a directory, default {DEFAULT_SPLIT_DIR})")
    parser.add_argument("--format", dest="fmt", choices=("json-array", "json-hash"),
                        default="json-array",
                        help="Aseprite data format (mirrors Aseprite's own --format flag)")
    parser.add_argument("--split-states", action="store_true",
                        help="write one <state>.json per state (local frame indices) — "
                             "the shape Flame's SpriteAnimation.fromAsepriteData consumes")


def run(run_dir: Path, manifest: str = "manifest.json", output: str | None = None,
        fmt: str = "json-array", split_states: bool = False) -> int:
    manifest_path = run_dir / manifest
    if not manifest_path.is_file():
        print(json.dumps({
            "ok": False,
            "error": f"missing {manifest_path} — run compose-atlas first",
        }, ensure_ascii=False, indent=2))
        return 1
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    if split_states:
        out_dir = run_dir / (output or DEFAULT_SPLIT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        docs = split_state_jsons(data, fmt=fmt)
        for state, doc in docs.items():
            atomic_write_text(out_dir / f"{state}.json",
                              json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({
            "ok": True,
            "output": str(out_dir),
            "image": data["game_input"],
            "format": fmt,
            "states": list(docs),
        }, ensure_ascii=False, indent=2))
        return 0

    exported = aseprite_json(data, fmt=fmt)
    out_path = run_dir / (output or DEFAULT_OUTPUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out_path, json.dumps(exported, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "ok": True,
        "output": str(out_path),
        "image": exported["meta"]["image"],
        "format": fmt,
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
