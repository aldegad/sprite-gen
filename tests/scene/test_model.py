# SPDX-License-Identifier: Apache-2.0
"""Scene specs: timing, placement defaults, planes and stride binding to the measured sequence."""

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.qa.motion import analyze_motion
from sprite_gen.scene import model
from sprite_gen.scene.model import bound_stride, load_scene
from sprite_gen.spec.assets import load_asset


FOOT_BOX = (8, 25, 34, 12)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atlas(tmp_path):
    sheet = Image.new("RGBA", (48 * 5, 40))
    for index, x in enumerate((24, 22, 20, 18, 16)):
        draw = ImageDraw.Draw(sheet)
        draw.rectangle((48 * index + 15, 5, 48 * index + 30, 20), fill="white")
        draw.rectangle((48 * index + x, 30, 48 * index + x + 3, 32), fill="white")
    sheet.save(tmp_path / "atlas.png")
    rects = [{"x": 48 * i, "y": 0, "w": 48, "h": 40} for i in range(5)]
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps({
        "sprite_sheet_alpha": "atlas.png",
        "frame_layout": {"rows": {"walk": rects, "idle": [rects[0]] * 5}},
        "animation": {"rows": {s: {"fps": 10, "durations_ms": [100] * 5, "loop": True} for s in ("walk", "idle")}},
    }))
    return path


def motion_report(tmp_path, name="walk.motion.json", **load):
    report = analyze_motion(load_asset(tmp_path / "atlas.json", **load), contacts="0:4", foot_box=FOOT_BOX)
    assert report["stride_verified"] is True
    path = tmp_path / name
    path.write_text(json.dumps(report))
    return path


def scene(tmp_path, layers=None, **overrides):
    spec = {"kind": "sprite-gen-scene", "version": 1, "canvas": {"width": 48, "height": 40, "background": [0, 0, 0]},
            "fps": 10, "duration": 0.5, "assets": {"walk": {"source": "atlas.json", "state": "walk"}},
            "layers": layers if layers is not None else [{"id": "hero", "asset": "walk", "at": [24, 40]}]}
    spec.update(overrides)
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(spec))
    return path


def test_layers_carry_defaults_raster_size_and_z_order(tmp_path):
    atlas(tmp_path)
    loaded = load_scene(scene(tmp_path, layers=[
        {"id": "front", "asset": "walk", "at": [10, 40], "z": 5},
        {"id": "back", "asset": "walk", "at": [20, 40], "z": -1, "scale": 0.33, "opacity": 0.5, "loop": False},
    ], assets={"walk": {"source": "atlas.json", "state": "walk"}}))
    assert [layer.id for layer in loaded.layers] == ["back", "front"]
    front, back = loaded.layers[1], loaded.layers[0]
    assert front.velocity == (0, 0) and front.scale == 1 and front.raster_size == (48, 40)
    assert front.opacity == 1 and front.loop is True and front.plane is None and front.playback_rate == 1
    assert back.raster_size == (16, 13)
    assert back.opacity == 0.5 and back.loop is False
    assert loaded.frame_count == 5 and loaded.background == (0, 0, 0, 255)
    assert loaded.spec["canvas"]["background"] == [0, 0, 0]
    assert loaded.source_fingerprints == {str(p.resolve()): sha(p) for p in (tmp_path / "scene.json", tmp_path / "atlas.json", tmp_path / "atlas.png")}


def test_plane_velocity_adds_to_layer_velocity(tmp_path):
    atlas(tmp_path)
    loaded = load_scene(scene(tmp_path, planes={"ground": {"velocity": [-30, 0]}},
                              layers=[{"id": "hero", "asset": "walk", "at": [24, 40], "velocity": [5, 2], "plane": "ground"}]))
    assert loaded.layers[0].velocity == (-25, 2)
    with pytest.raises(ValueError, match="unknown plane"):
        load_scene(scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "at": [24, 40], "plane": "sky"}]))


def test_stride_applies_actual_raster_scale_playback_rate_and_explicit_direction(tmp_path):
    atlas(tmp_path)
    report = motion_report(tmp_path, state="walk")
    speed = json.loads(report.read_text())["stride_px_per_second"]
    assert speed == pytest.approx(20)
    loaded = load_scene(scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "at": [24, 40], "scale": 0.33,
                                                 "playback_rate": 2, "stride": {"report": "walk.motion.json", "direction": "left"}}]))
    layer = loaded.layers[0]
    assert layer.raster_size == (16, 13)
    assert layer.velocity == (pytest.approx(-20 * (16 / 48) * 2), 0)
    assert layer.velocity[0] != pytest.approx(-20 * 0.33 * 2)
    assert loaded.source_fingerprints[str(report.resolve())] == sha(report)
    right = load_scene(scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "at": [24, 40],
                                                "stride": {"report": "walk.motion.json", "direction": "right"}}]))
    assert right.layers[0].velocity == (pytest.approx(20), 0)


@pytest.mark.parametrize("case, message", [
    ("other-state", "another state, frame rate or anchor"),
    ("fps-override", "another state, frame rate or anchor"),
    ("anchor-override", "another state, frame rate or anchor"),
    ("unverified", "unverified"),
    ("stale-bytes", "stale or belongs to another asset"),
    ("no-identity", "lacks its sequence identity"),
    ("not-a-report", "sprite-gen-motion-report"),
])
def test_stride_rejects_reports_of_any_other_selection_of_the_same_files(tmp_path, case, message):
    atlas(tmp_path)
    if case == "other-state":
        report = motion_report(tmp_path, state="walk")
        spec = scene(tmp_path, assets={"walk": {"source": "atlas.json", "state": "idle"}},
                     layers=[{"id": "hero", "asset": "walk", "at": [24, 40], "stride": {"report": report.name, "direction": "right"}}])
    else:
        load = {"state": "walk"}
        if case == "fps-override":
            load["fps"] = 5
        if case == "anchor-override":
            load["anchor"] = (24, 39)
        report = motion_report(tmp_path, **load)
        data = json.loads(report.read_text())
        if case == "unverified":
            data["stride_verified"] = False
        if case == "no-identity":
            del data["sequence"]
        if case == "not-a-report":
            data["kind"] = "sprite-gen-scene-inspection"
        report.write_text(json.dumps(data))
        if case == "stale-bytes":
            with Image.open(tmp_path / "atlas.png") as sheet:
                sheet.convert("RGBA").save(tmp_path / "atlas.png", optimize=True)
            assert sha(tmp_path / "atlas.png") != data["source_fingerprints"][str((tmp_path / "atlas.png").resolve())]
        spec = scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "at": [24, 40], "stride": {"report": report.name, "direction": "right"}}])
    with pytest.raises(ValueError, match=message):
        load_scene(spec)


def test_stride_report_and_velocity_are_exclusive_and_direction_is_required(tmp_path):
    atlas(tmp_path)
    report = motion_report(tmp_path, state="walk")
    with pytest.raises(ValueError, match="not both"):
        load_scene(scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "velocity": [1, 0], "stride": {"report": report.name, "direction": "right"}}]))
    with pytest.raises(ValueError, match="explicit left/right"):
        load_scene(scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "stride": {"report": report.name}}]))
    with pytest.raises(ValueError, match="explicit left/right"):
        load_scene(scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "stride": {"report": report.name, "direction": "up"}}]))
    with pytest.raises(ValueError, match="unknown stride fields"):
        load_scene(scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "stride": {"report": report.name, "direction": "right", "flip": True}}]))


def test_bound_stride_is_the_single_gate_for_measured_speed(tmp_path):
    atlas(tmp_path)
    walk = load_asset(tmp_path / "atlas.json", state="walk")
    report = json.loads(json.dumps(analyze_motion(walk, contacts="0:4", foot_box=FOOT_BOX)))
    assert bound_stride(report, walk) == pytest.approx(20)
    with pytest.raises(ValueError):
        bound_stride(report, load_asset(tmp_path / "atlas.json", state="idle"))
    with pytest.raises(ValueError):
        bound_stride({**report, "stride_px_per_second": 0}, walk)


def test_scene_and_report_identity_are_the_parsed_bytes(tmp_path, monkeypatch):
    atlas(tmp_path)
    report = motion_report(tmp_path, state="walk")
    spec = scene(tmp_path, layers=[{"id": "hero", "asset": "walk", "at": [24, 40], "stride": {"report": report.name, "direction": "right"}}])
    original_hashes = {str(p.resolve()): sha(p) for p in (spec, report)}
    original = model.read_json

    def read_then_rewrite(path):
        document, digest = original(path)
        Path(path).write_text(json.dumps(document, indent=4))
        return document, digest

    monkeypatch.setattr(model, "read_json", read_then_rewrite)
    loaded = load_scene(spec)
    for path, digest in original_hashes.items():
        assert loaded.source_fingerprints[path] == digest
        assert sha(path) != digest
    assert loaded.layers[0].velocity == (pytest.approx(20), 0)


@pytest.mark.parametrize("overrides, message", [
    ({"kind": "scene"}, "kind sprite-gen-scene"),
    ({"extra": 1}, "unknown scene fields"),
    ({"duration": 0.55}, "integer"),
    ({"duration": 1e-10}, "integer"),
    ({"fps": 200, "duration": 1}, "fps <= 120"),
    ({"canvas": {"width": 48.5, "height": 40}}, "integer dimensions"),
    ({"canvas": {"width": 48, "height": 40, "background": [0, 0, 300]}}, "RGB or RGBA"),
    ({"assets": {}}, "nonempty"),
    ({"layers": []}, "nonempty"),
    ({"layers": [{"id": "hero", "asset": "walk"}, {"id": "hero", "asset": "walk"}]}, "unique"),
    ({"layers": [{"id": "hero", "asset": "ghost"}]}, "unknown asset"),
    ({"layers": [{"id": "hero", "asset": "walk", "opacity": 1.5}]}, "0..1"),
    ({"layers": [{"id": "hero", "asset": "walk", "repeat_x": True, "period": 49}]}, "within the source width"),
    ({"layers": [{"id": "hero", "asset": "walk", "repeat_x": "yes"}]}, "boolean"),
    ({"layers": [{"id": "hero", "asset": "walk", "scale": 0}]}, "positive"),
    ({"light": {"angle": 3}}, "unknown light fields"),
])
def test_invalid_scene_specs_are_rejected(tmp_path, overrides, message):
    atlas(tmp_path)
    with pytest.raises(ValueError, match=message):
        load_scene(scene(tmp_path, **overrides))
