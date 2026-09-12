# SPDX-License-Identifier: Apache-2.0
"""Measure rendered evidence; numeric checks do not certify artistic completeness."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sprite_gen._deps import np

from sprite_gen.scene.model import Scene, load_scene
from sprite_gen.spec.runio import LOCK_FILENAME, RWLOCK_SUFFIX, acquire_run_dir_lock, atomic_write_set, release_run_dir_lock


# The wrap of a loop may be no worse than twice the largest ordinary transition
# (with a one-level floor), mirroring the tile inspector's edge-to-interior rule.
SEAM_SLACK = 2.0
SEAM_FLOOR = 1.0
PHASE_CLOSURE_LIMIT = 1.0


def _mae(a, b) -> float:
    return float(np.abs(a - b).mean())


def inspect_scene(scene: Scene, *, renderer=None, frame_dir=None):
    from sprite_gen.scene.render import Renderer
    renderer = renderer or Renderer(scene)
    first = previous = None
    green, magenta = 0, 0
    clipped = {layer.id: {"top_frames": 0, "bottom_frames": 0, "side_frames": 0, "offscreen_frames": 0,
                          "empty_frames": 0, "minimum_top_y": None} for layer in scene.layers}
    adjacent = []
    for i in range(scene.frame_count):
        image, placements = renderer.frame(i/scene.fps, geometry=True)
        if frame_dir is not None:
            image.save(Path(frame_dir) / f"frame-{i:05d}.png")
        pixels = np.asarray(image).astype(np.int16)
        if first is None:
            first = pixels.copy()
        r, g, b, a = [pixels[..., j] for j in range(4)]
        green += int(((a > 0) & (g >= 100) & (r <= .35*g) & (b <= .35*g)).sum())
        magenta += int(((a > 0) & (r >= 100) & (b >= .8*r) & (g <= .35*np.maximum(r,b))).sum())
        if previous is not None:
            adjacent.append(_mae(pixels, previous))
        previous = pixels
        by_layer = {}
        for entry in placements:
            by_layer.setdefault(entry["id"], []).append(entry)
        for name, stat in clipped.items():
            boxes = [e for e in by_layer.get(name, []) if e["bbox"] is not None]
            if not boxes:
                stat["empty_frames"] += 1
                continue
            visible = [e for e in boxes if e["visible"]]
            if not visible:
                stat["offscreen_frames"] += 1
            else:
                stat["top_frames"] += int(any(e["bbox"][1] < 0 for e in visible))
                stat["bottom_frames"] += int(any(e["bbox"][3] > scene.height for e in visible))
                stat["side_frames"] += int(any((e["bbox"][0] < 0 or e["bbox"][2] > scene.width) and not e["repeat_x"] for e in visible))
            y = min(e["bbox"][1] for e in boxes)
            stat["minimum_top_y"] = y if stat["minimum_top_y"] is None else min(y, stat["minimum_top_y"])
    # The seam a viewer sees is the last rendered frame followed by the first.
    # Phase closure (the scene state at t=duration equals t=0) is a separate
    # fact: it holds for any periodic scene, including one whose asset jumps
    # at its own wrap, so it cannot stand in for the seam.
    seam = _mae(previous, first)
    adjacent_max = max(adjacent) if adjacent else 0.0
    seam_limit = max(SEAM_FLOOR, SEAM_SLACK * adjacent_max)
    closure = _mae(np.asarray(renderer.frame(scene.duration)).astype(np.int16), first)
    loop = {"seam_mae": seam, "adjacent_mae_mean": float(np.mean(adjacent)) if adjacent else 0.0,
            "adjacent_mae_max": adjacent_max, "seam_limit": seam_limit, "seam_pass": seam <= seam_limit,
            "phase_closure_mae": closure, "phase_closure_pass": closure <= PHASE_CLOSURE_LIMIT}
    loop["pass"] = loop["seam_pass"] and loop["phase_closure_pass"]
    sources = {}
    tiles = {}
    for name, seq in scene.assets.items():
        contacts = []
        for frame in seq.frames:
            a = np.asarray(frame)[..., 3] > 128
            contacts.append({"top": int(a[0].sum()), "bottom": int(a[-1].sum()), "left": int(a[:,0].sum()), "right": int(a[:,-1].sum())})
        sources[name] = {"edge_contact": contacts, "completeness": "unverified", "note": "Edge contact is a diagnostic; missing artwork inside a source needs visual review."}
    from sprite_gen.background.tile import inspect_tile
    for layer in scene.layers:
        if layer.repeat_x:
            tiles[layer.id] = inspect_tile(layer.sequence.frames[0].crop((0,0,int(layer.period),layer.sequence.size[1])), axis="x")
    warnings = []
    if green or magenta:
        warnings.append("key-like colours detected; compare to source subject colours before treating as residue")
    if not loop["phase_closure_pass"]:
        warnings.append("end-to-start scene differs; review motion/scroll periods for an intended loop")
    if not loop["seam_pass"]:
        warnings.append("the last frame to first frame step is larger than ordinary transitions; review the loop wrap")
    if any(s["top_frames"] or s["bottom_frames"] for s in clipped.values()):
        warnings.append("some source content crosses the viewport top or bottom; inspect layer bounds")
    offscreen = [name for name, s in clipped.items() if s["offscreen_frames"]]
    if offscreen:
        warnings.append(f"layers entirely outside the viewport in some frames: {', '.join(offscreen)}")
    return {"kind": "sprite-gen-scene-inspection", "version": 1, "frames": scene.frame_count,
            "source_fingerprints": scene.source_fingerprints, "status": "needs-review" if warnings else "measured",
            "loop": loop, "key_like_pixels": {"green": green, "magenta": magenta}, "clipping": clipped,
            "sources": sources, "tile_joins": tiles, "warnings": warnings, "visual_review_required": True}


def output_path(out, scene: Scene) -> Path:
    """Resolve the report path and refuse scene inputs, their aliases and reserved lock names."""
    path = Path(out).expanduser().resolve()
    if path.name == LOCK_FILENAME or path.name.endswith(RWLOCK_SUFFIX):
        raise ValueError("inspection output must not use a reserved lock name")
    protected = [Path(p) for p in scene.source_fingerprints]
    if path in protected or (path.exists() and any(p.exists() and path.samefile(p) for p in protected)):
        raise ValueError("inspection output cannot overwrite scene inputs")
    if path.exists() and not path.is_file():
        raise ValueError("inspection output must be a file")
    for ancestor in path.parents:
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError("inspection output parent must be a directory")
    return path


def add_arguments(parser):
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--require-loop", action="store_true")


def run(spec, out=None, require_loop=False):
    try:
        scene = load_scene(spec)
        if out is not None:
            output_path(out, scene)
        report = inspect_scene(scene)
        payload = json.dumps(report, indent=2) + "\n"
        if out is not None:
            target = output_path(out, scene)
            target.parent.mkdir(parents=True, exist_ok=True)
            acquire_run_dir_lock(target.parent, "scene-inspect")
            try:
                atomic_write_set({target: payload})
            finally:
                release_run_dir_lock(target.parent)
        print(payload, end="")
        return int(require_loop and not report["loop"]["pass"])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f"scene-inspect: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
