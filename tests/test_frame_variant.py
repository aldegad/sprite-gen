# SPDX-License-Identifier: Apache-2.0
"""Per-state pixel-perfect variant resolution (curation.frame_variant) + mixed bake.

The curator has a per-row pixel-perfect toggle (states.<state>.pixel_perfect) and a
toggle-all that writes the run-wide default (top-level pixel_perfect). frame_variant
is the single resolver every consumer (atlas/GIF/PNG export/cycle) goes through:
per-state override > run-wide default > pixel.
"""

import json
from pathlib import Path

from PIL import Image

from conftest import run_script
from sprite_gen.curation import frame_variant, run_revision


def test_frame_variant_resolution_order() -> None:
    # absent sidecar -> pixel, with or without a state
    assert frame_variant(None) == "pixel"
    assert frame_variant(None, "idle") == "pixel"

    # run-wide default only
    assert frame_variant({"pixel_perfect": False, "states": {}}) == "plain"
    assert frame_variant({"pixel_perfect": False, "states": {}}, "idle") == "plain"
    assert frame_variant({"pixel_perfect": True, "states": {}}, "idle") == "pixel"

    # per-state override beats the run-wide default, both directions
    mixed = {
        "pixel_perfect": False,
        "states": {"idle": {"pixel_perfect": True}, "walk": {}},
    }
    assert frame_variant(mixed, "idle") == "pixel"    # override wins over global false
    assert frame_variant(mixed, "walk") == "plain"    # no override -> global
    assert frame_variant(mixed, "unknown") == "plain"  # unknown state -> global
    assert frame_variant(mixed) == "plain"             # stateless call -> global only

    over_off = {"states": {"idle": {"pixel_perfect": False}}}
    assert frame_variant(over_off, "idle") == "plain"  # override wins over absent global
    assert frame_variant(over_off, "walk") == "pixel"

    # a non-bool per-state value is ignored (hand-edited sidecar), not truthy-coerced
    corrupt = {"states": {"idle": {"pixel_perfect": "yes"}}}
    assert frame_variant(corrupt, "idle") == "pixel"


def test_compose_bakes_mixed_variants_per_state(fixture_run_dir: Path) -> None:
    """One state toggled off bakes its .plain.png twin while the others bake the
    canonical frames; the manifest records per-row frame_variant + a 'mixed' summary."""
    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0

    request = json.loads((run / "sprite-request.json").read_text(encoding="utf-8"))
    states = list(request["states"])
    assert len(states) >= 2
    plain_state, pixel_state = states[0], states[1]

    # manufacture a distinguishable plain twin for every frame of the toggled-off state
    # (a solid magenta-ish fill: trivially detectable in the baked atlas row)
    cell = request["cell"]
    cw, ch = int(cell.get("width", cell.get("size", 0))), int(cell.get("height", cell.get("size", 0)))
    marker = (250, 10, 250, 255)
    count = int(request["states"][plain_state]["frames"])
    for index in range(count):
        Image.new("RGBA", (cw, ch), marker).save(run / "frames" / plain_state / f"frame-{index}.plain.png")

    sidecar = {
        "version": 1,
        "kind": "sprite-gen-curation",
        "run_revision": run_revision(run),  # .plain.png twins do not shift the revision
        "states": {plain_state: {"pixel_perfect": False}},
    }
    (run / "curation.json").write_text(json.dumps(sidecar), encoding="utf-8")

    compose = run_script("compose_sprite_atlas.py", "--run-dir", str(run))
    assert compose.returncode == 0, compose.stdout + compose.stderr

    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["frame_variant"] == "mixed"
    assert manifest["animation"]["rows"][plain_state]["frame_variant"] == "plain"
    assert manifest["animation"]["rows"][pixel_state]["frame_variant"] == "pixel"

    # the plain row actually baked the marker twin; the pixel row did not
    row = states.index(plain_state)
    with Image.open(run / "sprite-sheet-alpha.png") as atlas:
        assert atlas.convert("RGBA").getpixel((cw // 2, row * ch + ch // 2)) == marker
        other_row = states.index(pixel_state)
        assert atlas.convert("RGBA").getpixel((cw // 2, other_row * ch + ch // 2)) != marker
