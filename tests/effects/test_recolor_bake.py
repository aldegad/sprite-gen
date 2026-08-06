# SPDX-License-Identifier: Apache-2.0
"""Palette-swap bake: determinism, exact/tolerance substitution accuracy, and
the No-Silent-Fallback reporting contract (nothing outside the map vanishes
silently — it is named and counted)."""

import json
from pathlib import Path

import numpy as np
from PIL import Image

import sprite_gen.effects.recolor as recolor

# Flat art colours in the fixture sheet.
COAT = (180, 40, 40)      # mapped by every variant
TRIM = (40, 80, 180)      # opaque, never mapped -> passthrough
GLOW = (10, 200, 90)      # opaque, never mapped -> passthrough
NEAR_COAT = (182, 42, 41) # 2 away from COAT (Chebyshev) -> tolerance only


def _build_sheet(path: Path) -> None:
    arr = np.zeros((4, 8, 4), dtype=np.uint8)  # H=4, W=8, fully transparent
    # row 0: four COAT pixels
    arr[0, 0:4, :3] = COAT
    arr[0, 0:4, 3] = 255
    # row 1: two TRIM, two GLOW
    arr[1, 0:2, :3] = TRIM
    arr[1, 0:2, 3] = 255
    arr[1, 2:4, :3] = GLOW
    arr[1, 2:4, 3] = 255
    # row 2: one NEAR_COAT (opaque) + one COAT-coloured but nearly transparent
    arr[2, 0, :3] = NEAR_COAT
    arr[2, 0, 3] = 255
    arr[2, 1, :3] = COAT
    arr[2, 1, 3] = 4  # below alpha threshold -> ignored, not recoloured
    Image.fromarray(arr, "RGBA").save(path)


def _spec(path: Path, *, match: str = "exact", tolerance: int = 0, extra_source: bool = False) -> None:
    variant_map = {recolor.format_hex(COAT): "#14c814"}
    if extra_source:
        variant_map["#010203"] = "#ffffff"  # source absent from the sheet -> unused
    spec = {
        "version": 1,
        "kind": "sprite-gen-recolor",
        "match": match,
        "tolerance": tolerance,
        "variants": [
            {"name": "green", "map": variant_map},
            {"name": "gold", "map": {recolor.format_hex(COAT): "#f0c000"}},
        ],
    }
    path.write_text(json.dumps(spec), encoding="utf-8")


def test_bake_is_byte_deterministic(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    spec = tmp_path / "spec.json"
    _build_sheet(base)
    _spec(spec)

    recolor.bake(base, spec, tmp_path / "a")
    recolor.bake(base, spec, tmp_path / "b")

    for name in ("green.png", "gold.png"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_exact_swap_hits_only_exact_colour_and_keeps_geometry(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    spec = tmp_path / "spec.json"
    _build_sheet(base)
    _spec(spec)

    recolor.bake(base, spec, tmp_path / "out")

    baked = np.asarray(Image.open(tmp_path / "out" / "green.png").convert("RGBA"), dtype=np.uint8)
    original = np.asarray(Image.open(base).convert("RGBA"), dtype=np.uint8)

    # COAT pixels became the target; alpha unchanged.
    assert tuple(baked[0, 0, :3]) == (0x14, 0xc8, 0x14)
    assert baked[0, 0, 3] == 255
    # TRIM / GLOW untouched.
    assert tuple(baked[1, 0, :3]) == TRIM
    assert tuple(baked[1, 2, :3]) == GLOW
    # NEAR_COAT untouched under exact match.
    assert tuple(baked[2, 0, :3]) == NEAR_COAT
    # Sub-threshold COAT pixel not recoloured (RGB preserved).
    assert tuple(baked[2, 1, :3]) == COAT
    # Geometry never moves: alpha channel is identical to the base.
    assert np.array_equal(baked[:, :, 3], original[:, :, 3])


def test_report_names_substitutions_passthrough_and_unused(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    spec = tmp_path / "spec.json"
    _build_sheet(base)
    _spec(spec, extra_source=True)

    report = recolor.bake(base, spec, tmp_path / "out")
    green = next(v for v in report["variants"] if v["name"] == "green")

    # 4 COAT pixels swapped (the sub-threshold one does not count).
    hit = next(s for s in green["substitutions"] if s["from"] == recolor.format_hex(COAT))
    assert hit["pixels"] == 4
    assert green["substituted_pixels"] == 4

    # The absent source is reported as unused, not silently ignored.
    assert any(u["from"] == "#010203" for u in green["unused_sources"])

    # TRIM, GLOW, NEAR_COAT pass through — named and counted, never dropped.
    passthrough = {c["hex"]: c["pixels"] for c in green["passthrough_colors"]}
    assert passthrough[recolor.format_hex(TRIM)] == 2
    assert passthrough[recolor.format_hex(GLOW)] == 2
    assert passthrough[recolor.format_hex(NEAR_COAT)] == 1
    assert green["passthrough_pixels"] == 5
    assert green["passthrough_color_count"] == 3


def test_tolerance_matches_near_colour_nearest_wins(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    spec = tmp_path / "spec.json"
    _build_sheet(base)
    _spec(spec, match="tolerance", tolerance=3)

    report = recolor.bake(base, spec, tmp_path / "out")
    baked = np.asarray(Image.open(tmp_path / "out" / "green.png").convert("RGBA"), dtype=np.uint8)

    # NEAR_COAT (2 away) now snaps to COAT's target under tolerance 3.
    assert tuple(baked[2, 0, :3]) == (0x14, 0xc8, 0x14)
    green = next(v for v in report["variants"] if v["name"] == "green")
    hit = next(s for s in green["substitutions"] if s["from"] == recolor.format_hex(COAT))
    assert hit["pixels"] == 5  # 4 exact + 1 near


def test_exact_with_nonzero_tolerance_fails_loud(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    spec = tmp_path / "spec.json"
    _build_sheet(base)
    _spec(spec, match="exact", tolerance=3)
    try:
        recolor.bake(base, spec, tmp_path / "out")
    except SystemExit as exc:
        assert "contradictory" in str(exc)
    else:
        raise AssertionError("expected SystemExit on exact+tolerance")


def test_extract_palette_orders_by_frequency(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    _build_sheet(base)
    palette = recolor.extract_palette(base)

    hexes = [c["hex"] for c in palette["colors"]]
    # COAT is the most frequent opaque colour (4 px), so it leads.
    assert hexes[0] == recolor.format_hex(COAT)
    # Sub-threshold pixel excluded -> COAT count is 4, not 5.
    coat = next(c for c in palette["colors"] if c["hex"] == recolor.format_hex(COAT))
    assert coat["pixels"] == 4
    assert palette["color_count"] == len(palette["colors"])


def test_extract_palette_max_colors_reports_drop(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    _build_sheet(base)
    palette = recolor.extract_palette(base, max_colors=1)
    assert palette["color_count"] == 1
    # The dropped colours' pixels are reported, not silently cut.
    assert palette["dropped_colors_pixels"] > 0


def test_manifest_propagates_per_variant(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    spec = tmp_path / "spec.json"
    manifest = tmp_path / "manifest.json"
    _build_sheet(base)
    _spec(spec)
    manifest.write_text(json.dumps({"atlas": "base.png", "sprite_sheet_alpha": "base.png", "cell": {"width": 8}}), encoding="utf-8")

    recolor.bake(base, spec, tmp_path / "out", manifest=manifest)

    for name in ("green", "gold"):
        data = json.loads((tmp_path / "out" / f"{name}.manifest.json").read_text(encoding="utf-8"))
        assert data["atlas"] == f"{name}.png"
        assert data["sprite_sheet_alpha"] == f"{name}.png"
        assert data["cell"] == {"width": 8}  # unrelated fields untouched
