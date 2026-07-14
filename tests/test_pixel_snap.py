# SPDX-License-Identifier: Apache-2.0
"""Pixel-perfect twins share the pp footprint + curated transforms re-snap to the grid.

1. plain/orig twins are fitted into the pixel-perfect frame's content bbox, so the
   curator's toggle compares pixel quality at the same size (no size jump).
2. apply_transform(snap_scale=s) re-quantizes a curated move/rotate onto the fixed
   logical grid — a pixel-variant bake can never produce off-grid smear.
"""

import json
import random

from PIL import Image

import sprite_gen.extract as extract_module
from sprite_gen.curation import apply_transform, pixel_snap_scale

MAGENTA = (255, 0, 255)


def _logical_art(width, height, seed):
    rng = random.Random(seed)
    art = Image.new("RGB", (width, height), MAGENTA)
    for y in range(height):
        for x in range(width):
            if rng.random() < 0.55:
                art.putpixel((x, y), (rng.randrange(30, 220), rng.randrange(30, 220), rng.randrange(30, 220)))
    return art


def _build_pp_run(root):
    """2-frame pixel-perfect fixture: logical art upscaled by an integer pitch."""
    run_dir = root / "run"
    (run_dir / "raw").mkdir(parents=True)
    pitch = 8
    frame = _logical_art(20, 36, seed=7).resize((20 * pitch, 36 * pitch), Image.Resampling.NEAREST)
    gap = 40
    strip = Image.new("RGB", (frame.width * 2 + gap * 3, frame.height + gap * 2), MAGENTA)
    strip.paste(frame, (gap, gap))
    strip.paste(frame, (frame.width + gap * 2, gap))
    strip.save(run_dir / "raw" / "walk.png")
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "snapbot", "description": "pixel snap fixture", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8, "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255], "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "synthetic snap fixture"}},
        "fit": {"pixel_perfect": True, "logical_height": 48},
    }
    (run_dir / "sprite-request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_dir


def _conform_request(run_dir, conform):
    """cell 48 (pp_scale=1, cap 42) + logical_height 30 < art's native 36 logical."""
    import json as _json
    request = _json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    request["cell"] = {"shape": "square", "width": 48, "height": 48,
                      "safe_margin_x": 6, "safe_margin_y": 6, "size": 48, "safe_margin": 6}
    request["fit"]["logical_height"] = 30
    if conform is not None:
        request["fit"]["conform"] = conform
    (run_dir / "sprite-request.json").write_text(_json.dumps(request), encoding="utf-8")


def _walk_frame0_height(run_dir) -> int:
    im = Image.open(run_dir / "frames" / "walk" / "frame-0.png").convert("RGBA")
    bb = im.getchannel("A").getbbox()
    return bb[3] - bb[1]


def test_default_keeps_native_logical_size(tmp_path) -> None:
    """DEFAULT (no conform key) skips the contract squeeze: the output keeps the
    snapped native logical size (36) even though logical_height is 30 (physical
    cap 42 only). The squeeze runs only on explicit `fit.conform: true`."""
    run_dir = _build_pp_run(tmp_path)
    _conform_request(run_dir, conform=None)
    assert extract_module.run(run_dir=run_dir) == 0
    height = _walk_frame0_height(run_dir)
    assert height > 30, f"native size squeezed by default: {height}"


def test_conform_true_squeezes_to_contract(tmp_path) -> None:
    """Explicit fit.conform=true restores the legacy contract squeeze (36 -> <=30).
    min_used_pixels lowered: the squeezed 30px fixture sprite is legitimately small."""
    run_dir = _build_pp_run(tmp_path)
    _conform_request(run_dir, conform=True)
    assert extract_module.run(run_dir=run_dir, min_used_pixels=200) == 0
    height = _walk_frame0_height(run_dir)
    assert height <= 30, f"conform=true did not squeeze: {height}"


def test_pixel_snap_scale_resolution() -> None:
    assert pixel_snap_scale({"cell": {"size": 64}}) is None  # no fit
    assert pixel_snap_scale({"cell": {"size": 64}, "fit": {"resample": "kcentroid"}}) is None
    base = {"cell": {"width": 64, "height": 64, "safe_margin_x": 6, "safe_margin_y": 6}}
    assert pixel_snap_scale({**base, "fit": {"pixel_perfect": True, "logical_height": 48}}) == 1
    assert pixel_snap_scale({**base, "fit": {"pixel_perfect": True, "logical_height": 32}}) == 2
    assert pixel_snap_scale({**base, "fit": {"pixel_perfect": True}}) == 1  # logical = cell


def _blocks_uniform(image: Image.Image, scale: int) -> bool:
    px = image.load()
    for by in range(image.height // scale):
        for bx in range(image.width // scale):
            first = px[bx * scale, by * scale]
            for dy in range(scale):
                for dx in range(scale):
                    if px[bx * scale + dx, by * scale + dy] != first:
                        return False
    return True


def test_apply_transform_snap_keeps_grid() -> None:
    """A fractional move + rotation on a 2px-block frame stays block-uniform when snapped."""
    scale = 2
    cell = (64, 64)
    frame = Image.new("RGBA", cell, (0, 0, 0, 0))
    art = _logical_art(20, 24, seed=3).convert("RGBA").resize((40, 48), Image.Resampling.NEAREST)
    frame.alpha_composite(art, (12, 10))
    assert _blocks_uniform(frame, scale)

    moved = apply_transform(frame, {"dx": 0.7, "dy": -1.3, "rotate": 9.0, "scale": 1.05}, cell, snap_scale=scale)
    assert moved.size == frame.size
    assert moved.getbbox() is not None
    assert _blocks_uniform(moved, scale), "snapped bake must stay on the logical grid"

    smeared = apply_transform(frame, {"dx": 0.7, "dy": -1.3, "rotate": 9.0, "scale": 1.05}, cell)
    assert not _blocks_uniform(smeared, scale), "unsnapped BICUBIC control should break blocks"


def test_twins_share_pixel_perfect_footprint(tmp_path) -> None:
    run_dir = _build_pp_run(tmp_path)
    assert extract_module.run(run_dir=run_dir) == 0

    manifest = json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    row = manifest["rows"][0]
    assert row["ok"], row
    assert row.get("plain_files"), "pixel-perfect run must save plain twins"

    for index in range(2):
        pixel = Image.open(run_dir / "frames" / "walk" / f"frame-{index}.png").convert("RGBA")
        plain = Image.open(run_dir / "frames" / "walk" / f"frame-{index}.plain.png").convert("RGBA")
        pb = pixel.getchannel("A").getbbox()
        lb = plain.getchannel("A").getbbox()
        assert pb is not None and lb is not None
        # same footprint: identical box within grid-rounding tolerance (contain fit)
        for a, b in zip(pb, lb):
            assert abs(a - b) <= 2, f"frame {index}: pixel bbox {pb} vs plain bbox {lb}"

    # detected input grid (the actual cut lines) is recorded per frame, mapped into
    # cell coords: the lattice must cover the plain twin's content bbox.
    grids = row.get("input_grids")
    assert grids and len(grids) == 2
    for index, grid in enumerate(grids):
        assert grid and grid["x"] and grid["y"], f"frame {index}: missing input grid"
        plain = Image.open(run_dir / "frames" / "walk" / f"frame-{index}.plain.png").convert("RGBA")
        lb = plain.getchannel("A").getbbox()
        assert grid["x"][0] <= lb[0] + 2 and grid["x"][-1] >= lb[2] - 2
        assert grid["y"][0] <= lb[1] + 2 and grid["y"][-1] >= lb[3] - 2
        # ~one logical pixel per cut cell: line count tracks the sprite's logical size
        assert len(grid["x"]) - 1 >= 10 and len(grid["y"]) - 1 >= 20
        if row.get("orig_files"):
            orig = Image.open(run_dir / "frames" / "walk" / "orig" / f"frame-{index}.png").convert("RGBA")
            scale = orig.width // pixel.width
            assert scale >= 2
            ob = orig.getchannel("A").getbbox()
            for a, b in zip(ob, tuple(v * scale for v in pb)):
                assert abs(a - b) <= 2 * scale, f"frame {index}: orig bbox {ob} vs pixel bbox x{scale}"


def test_partial_generation_view_tolerance(tmp_path) -> None:
    """부분 생성(일부 상태만 추출) 세대: 관찰자(뷰)는 allow_pending_states 로 통과,
    소비자 기본 게이트는 여전히 fail-loud."""
    import json as _json
    import pytest as _pytest
    from sprite_gen.extract import load_consistent_frames_manifest
    run_dir = _build_pp_run(tmp_path)
    request = _json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    request["states"]["walk2"] = dict(request["states"]["walk"])  # 아직 raw 없는 두 번째 상태
    (run_dir / "sprite-request.json").write_text(_json.dumps(request), encoding="utf-8")
    assert extract_module.run(run_dir=run_dir, states="walk") == 0
    manifest = load_consistent_frames_manifest(run_dir, allow_pending_states=True)
    assert [r["state"] for r in manifest["rows"]] == ["walk"]
    with _pytest.raises(SystemExit, match="incomplete generation"):
        load_consistent_frames_manifest(run_dir)
