# SPDX-License-Identifier: Apache-2.0
"""Composite bake — deterministic stacking, run-dir diagnostics, and what it must not touch.

`sprite_gen.compose.layers` answers what a request may declare; this file covers what only
the run dir can answer (`docs/layer-tracks.md` §4/§5):

1. the bake itself — integer pivot translation, arbitrary alpha masks, cell reuse,
   and the same bytes on every run;
2. the diagnostics — a missing frame, an unusable mask, a clipped element, a
   re-rolled source row, a curated clone with no declared pivot;
3. the boundaries — nothing is written when a bake fails, and a non-layer run is
   refused rather than half-composed;
4. the published CLI (`sprite-gen compose-layers`, §7) — exit code, summary, and
   the same guarantees across a process boundary.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.compose import compose_layers
from sprite_gen.curate.curation import empty_curation, write_curation_atomic

from conftest import run_script

MAGENTA = (255, 0, 255)
CELL = 96

# Declared pivots. Values are in-cell integers; the bake never infers one, so the
# only thing that matters here is that the same names exist on every pool frame.
# `can` is a `prop_effect` row, so it declares its own `root` / `grip` and no
# `crown`: required landmarks follow profile AND track (`layer-tracks.md` §3.1).
LANDMARKS = {
    "walk": {
        "0": {"root": [48, 60], "crown": [48, 30], "hand_r": [60, 50]},
        "1": {"root": [48, 61], "crown": [48, 40], "hand_r": [62, 52]},
    },
    "hold": {
        "0": {"root": [48, 60], "crown": [48, 30], "hand_r": [58, 50]},
        "1": {"root": [48, 60], "crown": [48, 30], "hand_r": [58, 50]},
    },
    "can": {"0": {"root": [48, 72], "grip": [48, 72]}},
}


def _strip(shapes: list[tuple[tuple[int, int, int, int], tuple[int, int, int]]],
           frames: int) -> Image.Image:
    """A magenta row strip whose shapes sit inside the cell safe area."""
    strip = Image.new("RGB", (frames * CELL, CELL), MAGENTA)
    draw = ImageDraw.Draw(strip)
    for box, fill in shapes:
        draw.rectangle(box, fill=fill)
    return strip


def _request(**overrides) -> dict:
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "rigbot", "description": "layer bake fixture", "base_image": None},
        "cell": {"shape": "square", "width": CELL, "height": CELL, "safe_margin_x": 8,
                 "safe_margin_y": 8, "size": CELL, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255],
                       "selection": "fallback"},
        "states": {
            "walk": {"frames": 2, "fps": 8, "loop": True, "action": "walk", "track": "base"},
            "hold": {"frames": 2, "fps": 8, "loop": True, "action": "hold", "track": "base"},
            "can": {"frames": 1, "fps": 8, "loop": False, "action": "prop",
                    "track": "prop_effect"},
        },
        "style": "flat solid shapes on magenta",
        "motion_phase_guides": False,
        "rig": {"profile": "humanoid_biped", "landmarks": json.loads(json.dumps(LANDMARKS))},
        "layers": {
            "walk_with_can": {"stack": [
                {"state": "walk"},
                {"state": "can", "from": "grip", "to": "hand_r"},
            ]},
        },
    }
    request.update(overrides)
    return request


def _write_request(run_dir: Path, request: dict) -> None:
    (run_dir / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_run(tmp_path: Path, request: dict | None = None, *, extract: bool = True) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "raw").mkdir(parents=True)
    _strip([((12, 20, 51, 79), (40, 180, 60)),
            ((108, 30, 157, 79), (60, 160, 80))], 2).save(run_dir / "raw" / "walk.png")
    # two identical poses: their composite cells are byte identical, so the sheet
    # must share one column between them (atlas cell-reuse rule, §2 B7).
    _strip([((12, 20, 51, 79), (30, 140, 90)),
            ((108, 20, 147, 79), (30, 140, 90))], 2).save(run_dir / "raw" / "hold.png")
    _strip([((30, 30, 61, 61), (200, 120, 60))], 1).save(run_dir / "raw" / "can.png")
    _write_request(run_dir, request if request is not None else _request())
    if extract:
        result = run_script("extract_sprite_row_frames.py", "--run-dir", str(run_dir))
        assert result.returncode == 0, result.stdout + result.stderr
    return run_dir


def _mask(run_dir: Path, rel: str, value: int, size: tuple[int, int] = (CELL, CELL)) -> str:
    path = run_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", size, value).save(path)
    return rel


def _curate(run_dir: Path, states: dict) -> None:
    write_curation_atomic(run_dir, {**empty_curation(), "states": states})


def _rows(run_dir: Path) -> dict[str, dict]:
    manifest = json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    return {row["state"]: row for row in manifest["rows"]}


def _frame(run_dir: Path, state: str, index: int) -> Image.Image:
    return Image.open(run_dir / _rows(run_dir)[state]["files"][index]).convert("RGBA")


def _cell_of(sheet: Image.Image, rect: dict) -> Image.Image:
    return sheet.crop((rect["x"], rect["y"], rect["x"] + rect["w"], rect["y"] + rect["h"]))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layers_dir(run_dir: Path) -> Path:
    return run_dir / compose_layers.LAYERS_DIRNAME


# ------------------------------------------------------------------ 1. the bake


def test_composite_bake_is_byte_identical_across_runs(tmp_path: Path) -> None:
    """Same run, same bytes — the determinism claim the whole feature rests on."""
    run_dir = _build_run(tmp_path)

    compose_layers.bake(run_dir)
    first = {path.name: _digest(path) for path in sorted(_layers_dir(run_dir).iterdir())}
    compose_layers.bake(run_dir)
    second = {path.name: _digest(path) for path in sorted(_layers_dir(run_dir).iterdir())}

    assert set(first) == {"walk_with_can.png", "walk_with_can.manifest.json",
                          "layers.report.json"}
    assert first == second


def test_element_is_placed_at_the_declared_integer_pivot_offset(tmp_path: Path) -> None:
    """`to - from`, composed onto the body — recomputed here from the frames on disk."""
    run_dir = _build_run(tmp_path)

    report = compose_layers.bake(run_dir)
    sheet = Image.open(_layers_dir(run_dir) / "walk_with_can.png").convert("RGBA")
    manifest = json.loads(
        (_layers_dir(run_dir) / "walk_with_can.manifest.json").read_text(encoding="utf-8"))

    for position in range(2):
        offset = (LANDMARKS["walk"][str(position)]["hand_r"][0] - LANDMARKS["can"]["0"]["grip"][0],
                  LANDMARKS["walk"][str(position)]["hand_r"][1] - LANDMARKS["can"]["0"]["grip"][1])
        expected = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
        expected.alpha_composite(_frame(run_dir, "walk", position))
        placed = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
        placed.paste(_frame(run_dir, "can", 0), offset)
        expected.alpha_composite(placed)

        rect = manifest["frame_layout"]["rows"]["walk_with_can"][position]
        assert _cell_of(sheet, rect).tobytes() == expected.tobytes(), f"frame {position}"

    element = report["composites"][0]["elements"][1]
    assert element["offsets"] == [[12, -22], [14, -20]]
    assert element["clipped_pixels"] == 0


@pytest.mark.parametrize("value", [0, 1, 2, 63, 127, 128, 200, 254, 255])
def test_mask_alpha_arithmetic_is_the_published_formula(value: int) -> None:
    """Every (alpha, mask) pair, not just the opaque ones.

    Fully opaque art cannot tell `(a * m + 127) // 255` from `(a * m) // 255` —
    they agree for a = 255 at every m — so the rounding clause is pinned here,
    across the whole alpha range, instead of through a baked sheet.
    """
    frame = Image.new("RGBA", (256, 1), (10, 20, 30, 255))
    frame.putalpha(Image.frombytes("L", (256, 1), bytes(range(256))))

    masked = compose_layers.apply_alpha_mask(frame, Image.new("L", (256, 1), value))

    assert masked.getchannel("A").tobytes() == bytes((a * value + 127) // 255 for a in range(256))
    assert masked.convert("RGB").tobytes() == frame.convert("RGB").tobytes()


def test_mask_multiplies_alpha_with_documented_integer_rounding(tmp_path: Path) -> None:
    """`a' = (a * m + 127) // 255` on an arbitrary mask, no resampling anywhere."""
    request = _request()
    rel = "references/masks/can.png"
    request["layers"]["walk_with_can"]["stack"][1]["mask"] = rel
    run_dir = _build_run(tmp_path, request)
    _mask(run_dir, rel, 128)

    compose_layers.bake(run_dir)
    sheet = Image.open(_layers_dir(run_dir) / "walk_with_can.png").convert("RGBA")
    manifest = json.loads(
        (_layers_dir(run_dir) / "walk_with_can.manifest.json").read_text(encoding="utf-8"))

    can = _frame(run_dir, "can", 0)
    masked = can.copy()
    masked.putalpha(can.getchannel("A").point(lambda a: (a * 128 + 127) // 255))
    expected = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
    expected.alpha_composite(_frame(run_dir, "walk", 0))
    placed = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
    placed.paste(masked, (12, -22))
    expected.alpha_composite(placed)

    rect = manifest["frame_layout"]["rows"]["walk_with_can"][0]
    assert _cell_of(sheet, rect).tobytes() == expected.tobytes()


def test_a_fully_transparent_mask_removes_the_element_entirely(tmp_path: Path) -> None:
    request = _request()
    request["layers"]["body_only"] = {"stack": [{"state": "walk"}]}
    request["layers"]["walk_with_can"]["stack"][1]["mask"] = "references/masks/none.png"
    run_dir = _build_run(tmp_path, request)
    _mask(run_dir, "references/masks/none.png", 0)

    compose_layers.bake(run_dir)
    assert (_digest(_layers_dir(run_dir) / "walk_with_can.png")
            == _digest(_layers_dir(run_dir) / "body_only.png"))


def test_identical_composite_cells_share_one_column(tmp_path: Path) -> None:
    request = _request()
    request["layers"] = {"hold_with_can": {"stack": [
        {"state": "hold"},
        {"state": "can", "from": "grip", "to": "hand_r"},
    ]}}
    run_dir = _build_run(tmp_path, request)

    report = compose_layers.bake(run_dir)
    manifest = json.loads(
        (_layers_dir(run_dir) / "hold_with_can.manifest.json").read_text(encoding="utf-8"))
    sheet = Image.open(_layers_dir(run_dir) / "hold_with_can.png")

    assert report["composites"][0] == {**report["composites"][0], "frames": 2, "cells": 1}
    assert sheet.size == (CELL, CELL)
    rects = manifest["frame_layout"]["rows"]["hold_with_can"]
    assert len(rects) == 2 and rects[0] == rects[1]


def test_composite_manifest_keeps_the_runtime_manifest_shape(tmp_path: Path) -> None:
    """A runtime reads a composite with the code it already has, plus provenance."""
    run_dir = _build_run(tmp_path)
    assert run_script("compose_sprite_atlas.py", "--run-dir", str(run_dir)).returncode == 0
    base = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    compose_layers.bake(run_dir)
    manifest = json.loads(
        (_layers_dir(run_dir) / "walk_with_can.manifest.json").read_text(encoding="utf-8"))

    assert set(manifest) == (set(base) - {"rig"}) | {"layers"}
    assert manifest["game_input"] == "walk_with_can.png"
    assert manifest["animation"]["rows"]["walk_with_can"]["frames"] == 2
    assert manifest["animation"]["rows"]["walk_with_can"]["track"] == "composite"
    assert [element["state"] for element in manifest["layers"]["stack"]] == ["walk", "can"]
    assert manifest["layers"]["stack"][1]["track"] == "prop_effect"


def test_a_composite_states_it_has_no_alpha_report_instead_of_naming_another_kind(
        tmp_path: Path) -> None:
    """`sprite_sheet_alpha_report` names THIS sheet's alpha report — a composite has none.

    The key stays (key-set parity above) but resolves to nothing, because
    `layers.report.json` is the bake's provenance record for every composite and
    not this sheet's alpha report: a consumer that opened it through this field
    would be reading a different kind of document. The provenance pointer has its
    own name inside the `layers` block (`docs/layer-tracks.md` §5).
    """
    run_dir = _build_run(tmp_path)
    assert run_script("compose_sprite_atlas.py", "--run-dir", str(run_dir)).returncode == 0
    base = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    base_report = json.loads(
        (run_dir / base["sprite_sheet_alpha_report"]).read_text(encoding="utf-8"))

    compose_layers.bake(run_dir)
    manifest = json.loads(
        (_layers_dir(run_dir) / "walk_with_can.manifest.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (_layers_dir(run_dir) / manifest["layers"]["report"]).read_text(encoding="utf-8"))

    assert manifest["sprite_sheet_alpha_report"] is None
    assert manifest["layers"]["report"] == compose_layers.REPORT_NAME
    assert provenance["kind"] == compose_layers.REPORT_KIND
    # The two documents are not interchangeable, which is the whole reason the
    # field could not keep pointing at the provenance record.
    assert "kind" not in base_report
    assert set(provenance) != set(base_report)


def test_a_bake_publishes_its_whole_set_in_one_transaction(tmp_path: Path,
                                                           monkeypatch) -> None:
    """Sheets, manifests and the report land together or not at all (§5).

    A write that fails partway through must not leave a sheet published with no
    report naming it, so the bake hands every payload to one publish call. Here
    that call fails: the previous bake's bytes must still be exactly what is on
    disk, and the second composite must not exist at all.
    """
    request = _request()
    request["layers"]["walk_alone"] = {"stack": [{"state": "walk"}]}
    run_dir = _build_run(tmp_path, request)

    compose_layers.bake(run_dir, names=["walk_with_can"])
    before = {path.name: _digest(path) for path in sorted(_layers_dir(run_dir).iterdir())}

    seen: list[list[str]] = []

    def explode(payloads):
        seen.append([path.name for path in payloads])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(compose_layers, "atomic_write_set", explode)
    with pytest.raises(OSError):
        compose_layers.bake(run_dir)

    assert seen == [["walk_alone.png", "walk_alone.manifest.json",
                     "walk_with_can.png", "walk_with_can.manifest.json",
                     "layers.report.json"]], "the bake published in more than one call"
    after = {path.name: _digest(path) for path in sorted(_layers_dir(run_dir).iterdir())}
    assert after == before


def test_base_manifest_gains_rig_and_track_only_for_a_rig_run(tmp_path: Path) -> None:
    """Landmarks reach the runtime as atlas-absolute integers, one per play position."""
    run_dir = _build_run(tmp_path)
    assert run_script("compose_sprite_atlas.py", "--run-dir", str(run_dir)).returncode == 0
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["rig"]["profile"] == "humanoid_biped"
    for state in ("walk", "hold", "can"):
        rects = manifest["frame_layout"]["rows"][state]
        entries = manifest["rig"]["landmarks"][state]
        assert len(entries) == len(rects), state
        for index, (rect, entry) in enumerate(zip(rects, entries)):
            declared = LANDMARKS[state][str(index)]
            assert entry == {name: [rect["x"] + point[0], rect["y"] + point[1]]
                             for name, point in declared.items()}
    assert manifest["animation"]["rows"]["can"]["track"] == "prop_effect"
    assert manifest["animation"]["rows"]["walk"]["track"] == "base"


# ---------------------------------------------------------- 2. the diagnostics


def test_clipping_fails_loud_and_allow_clip_records_the_loss(tmp_path: Path) -> None:
    request = _request()
    request["rig"]["landmarks"]["walk"]["0"]["hand_r"] = [95, 95]
    request["rig"]["landmarks"]["walk"]["1"]["hand_r"] = [95, 95]
    run_dir = _build_run(tmp_path, request)

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "outside the 96x96 cell" in str(excinfo.value)
    assert "allow_clip" in str(excinfo.value)
    assert not _layers_dir(run_dir).exists()

    request["layers"]["walk_with_can"]["stack"][1]["allow_clip"] = True
    _write_request(run_dir, request)
    report = compose_layers.bake(run_dir)
    assert report["composites"][0]["elements"][1]["clipped_pixels"] > 0


def test_a_missing_source_frame_is_reported_against_the_published_generation(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    (run_dir / _rows(run_dir)["can"]["files"][0]).unlink()

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "missing frame" in str(excinfo.value)


@pytest.mark.parametrize("size,expected", [((48, 48), "expected the run's 96x96 cell"),
                                           (None, "not found")])
def test_an_unusable_mask_is_a_hard_error(tmp_path: Path, size, expected: str) -> None:
    request = _request()
    request["layers"]["walk_with_can"]["stack"][1]["mask"] = "references/masks/can.png"
    run_dir = _build_run(tmp_path, request)
    if size is not None:
        _mask(run_dir, "references/masks/can.png", 255, size)

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert expected in str(excinfo.value)


def test_a_mask_outside_the_run_dir_is_refused(tmp_path: Path) -> None:
    request = _request()
    request["layers"]["walk_with_can"]["stack"][1]["mask"] = "../escape.png"
    run_dir = _build_run(tmp_path, request)
    Image.new("L", (CELL, CELL), 255).save(tmp_path / "escape.png")

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "must stay inside the run dir" in str(excinfo.value)


def test_a_re_rolled_source_row_fails_a_pinned_element(tmp_path: Path) -> None:
    """The pin is the same mechanism as `curation.anchors`: prove it, or fail loud."""
    request = _request()
    request["layers"]["walk_with_can"]["stack"][1]["revision"] = ["deadbeefcafe"]
    run_dir = _build_run(tmp_path, request)

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "pins revision" in str(excinfo.value)
    assert "re-rolled" in str(excinfo.value)


def test_a_pinned_element_bakes_when_the_pin_matches(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    report = compose_layers.bake(run_dir)
    pin = report["composites"][0]["elements"][1]["state_revision"]
    assert pin, "an extracted row must have a provable revision"

    request = _request()
    request["layers"]["walk_with_can"]["stack"][1]["revision"] = pin
    _write_request(run_dir, request)

    assert compose_layers.bake(run_dir)["composites"][0]["elements"][1]["state_revision"] == pin


def test_a_curated_clone_instance_has_no_declared_pivot(tmp_path: Path) -> None:
    """A clone lives outside the landmark pool and carries its own transform."""
    run_dir = _build_run(tmp_path)
    _curate(run_dir, {"walk": {
        "clones": {"2": 0},
        "selected": [0, 1, 2],
        "transforms": {"2": {"dx": 6, "dy": 0, "scale": 1.0, "rotate": 0.0, "shear": 0.0}},
    }})

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "declares no landmarks" in str(excinfo.value)
    assert "clone" in str(excinfo.value)


def test_the_atlas_refuses_to_publish_landmarks_it_cannot_prove(tmp_path: Path) -> None:
    """The same clone rule on the base manifest: no pivot is invented for the runtime."""
    run_dir = _build_run(tmp_path)
    _curate(run_dir, {"walk": {"clones": {"2": 0}, "selected": [0, 1, 2]}})

    result = run_script("compose_sprite_atlas.py", "--run-dir", str(run_dir))

    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((run_dir / "sprite-sheet-alpha.report.json").read_text(encoding="utf-8"))
    assert any("declares no rig landmarks" in error for error in report["errors"])
    assert not (run_dir / "manifest.json").exists()


def test_an_invalid_rig_stops_the_atlas_before_it_bakes(tmp_path: Path) -> None:
    request = _request()
    request["rig"]["profile"] = "mecha"
    run_dir = _build_run(tmp_path, request)

    result = run_script("compose_sprite_atlas.py", "--run-dir", str(run_dir))

    assert result.returncode != 0
    assert "invalid layer contract" in result.stderr
    assert not (run_dir / "sprite-sheet-alpha.png").exists()


def test_a_deleted_body_row_has_nothing_to_stack_onto(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    _curate(run_dir, {"walk": {"deleted": [0, 1]}})

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "plays no frames" in str(excinfo.value)


def test_an_element_that_plays_a_different_length_is_rejected(tmp_path: Path) -> None:
    """Pool sizes match by declaration; curation can still cut one sequence short."""
    run_dir = _build_run(tmp_path)
    request = _request()
    request["layers"]["walk_and_hold"] = {"stack": [
        {"state": "walk"},
        {"state": "hold", "from": "root", "to": "root"},
    ]}
    request["states"]["hold"]["track"] = "action_overlay"
    _write_request(run_dir, request)
    _curate(run_dir, {"hold": {"deleted": [1]}})

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "plays 1 frames against a body of 2" in str(excinfo.value)
    assert "only a single-frame row holds one pose" in str(excinfo.value)


# ----------------------------------------------------------- 3. the boundaries


def test_nothing_is_written_when_any_composite_fails(tmp_path: Path) -> None:
    """Atomicity: a good composite is not published beside a failed one."""
    request = _request()
    request["layers"]["broken"] = {"stack": [
        {"state": "walk"},
        {"state": "can", "from": "grip", "to": "hand_r", "mask": "references/masks/gone.png"},
    ]}
    run_dir = _build_run(tmp_path, request)

    with pytest.raises(SystemExit):
        compose_layers.bake(run_dir)
    assert not _layers_dir(run_dir).exists()


def test_a_non_layer_run_is_refused_not_composed(tmp_path: Path) -> None:
    request = _request()
    del request["rig"]
    del request["layers"]
    for entry in request["states"].values():
        entry.pop("track")
    run_dir = _build_run(tmp_path, request)

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "not a layer run" in str(excinfo.value)
    assert not _layers_dir(run_dir).exists()


def test_an_invalid_declaration_fails_before_the_run_dir_is_touched(tmp_path: Path) -> None:
    request = _request()
    request["rig"]["landmarks"]["walk"]["1"].pop("crown")
    run_dir = _build_run(tmp_path, request)

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir)
    assert "invalid layer contract" in str(excinfo.value)
    assert not _layers_dir(run_dir).exists()


def test_an_unknown_composite_name_is_refused(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir, names=["nope"])
    assert "no such composite" in str(excinfo.value)


def test_a_repeated_name_is_refused_at_the_python_entry_point(tmp_path: Path) -> None:
    """The CLI string is not the only door into the selection.

    `parse_names` diagnoses a `--names a,a` typo early, but the invariant belongs
    to the bake: a repeat that got through would write one sheet while the report
    counted two composites, so `composite_count` and `layers/` would disagree —
    the same defect, reached by the library form.
    """
    run_dir = _build_run(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        compose_layers.bake(run_dir, names=["walk_with_can", "walk_with_can"])
    assert "named more than once" in str(excinfo.value)
    assert not _layers_dir(run_dir).exists()


def test_load_report_distinguishes_absent_from_foreign(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    assert compose_layers.load_report(run_dir) is None

    compose_layers.bake(run_dir)
    assert compose_layers.load_report(run_dir)["composite_count"] == 1

    path = _layers_dir(run_dir) / compose_layers.REPORT_NAME
    path.write_text(json.dumps({"kind": "sprite-gen-recolor-report"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        compose_layers.load_report(run_dir)


# ------------------------------------------------------------------- 4. the CLI
#
# The library form is covered above; these run the published command as an agent
# runs it, so the process boundary (exit code, stdout summary, stderr diagnostic)
# is part of the contract and not an implementation detail.


def _cli(run_dir: Path, *args: str):
    return run_script("compose_layers.py", "--run-dir", str(run_dir), *args)


def test_the_cli_bakes_and_summarizes_the_declared_composites(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)

    result = _cli(run_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["rig_profile"] == "humanoid_biped"
    assert summary["report"] == "layers/layers.report.json"
    assert summary["composites"] == [{
        "name": "walk_with_can",
        "sheet": "layers/walk_with_can.png",
        "manifest": "layers/walk_with_can.manifest.json",
        "frames": 2,
        "cells": 2,
        "clipped_pixels": 0,
    }]
    for rel in (entry for composite in summary["composites"]
                for entry in (composite["sheet"], composite["manifest"])):
        assert (run_dir / rel).is_file(), rel


def test_two_cli_bakes_in_separate_processes_are_byte_identical(tmp_path: Path) -> None:
    """Determinism across the process boundary, not just inside one interpreter."""
    run_dir = _build_run(tmp_path)

    assert _cli(run_dir).returncode == 0
    first = {path.name: _digest(path) for path in sorted(_layers_dir(run_dir).iterdir())}
    assert _cli(run_dir).returncode == 0
    second = {path.name: _digest(path) for path in sorted(_layers_dir(run_dir).iterdir())}

    assert first == second


def test_the_cli_bakes_only_the_named_composites(tmp_path: Path) -> None:
    request = _request()
    request["layers"]["walk_alone"] = {"stack": [{"state": "walk"}]}
    run_dir = _build_run(tmp_path, request)

    assert _cli(run_dir).returncode == 0
    assert _cli(run_dir, "--names", "walk_alone").returncode == 0

    # The other sheet stays (a subset bake leaves it alone), but the report — the
    # record of what the last bake produced — names only what was baked (§5).
    assert (_layers_dir(run_dir) / "walk_with_can.png").is_file()
    report = compose_layers.load_report(run_dir)
    assert [entry["name"] for entry in report["composites"]] == ["walk_alone"]


@pytest.mark.parametrize("value", ["", "walk_with_can,", "a,,b", " "])
def test_the_cli_refuses_a_malformed_names_list(tmp_path: Path, value: str) -> None:
    """An empty entry is a typo, never "all" — silently dropping it bakes another set."""
    run_dir = _build_run(tmp_path)

    result = _cli(run_dir, "--names", value)

    assert result.returncode != 0
    assert "--names expects a comma-separated list" in result.stderr
    assert not _layers_dir(run_dir).exists()


@pytest.mark.parametrize("value", ["walk_with_can,walk_with_can", "nope,walk_with_can,nope"])
def test_the_cli_refuses_a_repeated_name(tmp_path: Path, value: str) -> None:
    """Same class as an empty entry: the selection is the caller's to fix, not ours to guess."""
    run_dir = _build_run(tmp_path)

    result = _cli(run_dir, "--names", value)

    assert result.returncode != 0
    assert "named more than once" in result.stderr
    assert not _layers_dir(run_dir).exists()


def test_the_cli_reports_every_violation_at_once_and_writes_nothing(tmp_path: Path) -> None:
    request = _request()
    request["rig"]["landmarks"]["walk"]["1"].pop("crown")
    request["layers"]["walk_with_can"]["fps"] = "8"
    run_dir = _build_run(tmp_path, request)

    result = _cli(run_dir)

    assert result.returncode != 0
    assert "miss required landmark 'crown'" in result.stderr
    assert "layers.walk_with_can.fps must be a positive integer" in result.stderr
    assert not _layers_dir(run_dir).exists()


def test_the_cli_refuses_a_non_layer_run_by_name(tmp_path: Path) -> None:
    """"Not a layer run" and "nothing to compose" are different answers."""
    request = _request()
    del request["rig"]
    del request["layers"]
    for entry in request["states"].values():
        entry.pop("track")
    run_dir = _build_run(tmp_path, request)

    result = _cli(run_dir)

    assert result.returncode != 0
    assert "not a layer run" in result.stderr
    assert not _layers_dir(run_dir).exists()
