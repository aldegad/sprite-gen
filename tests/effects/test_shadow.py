# SPDX-License-Identifier: Apache-2.0
"""Synthetic geometry and publication checks for projected silhouette shadows."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.effects.shadow import project_shadow
from sprite_gen.effects import shadow
from sprite_gen.spec.assets import load_asset
from sprite_gen.spec import runio


def _silhouette(size=(32, 48)):
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), fill=(220, 40, 170, 180))
    draw.rectangle((7, 8, 20, 37), fill=(30, 240, 20, 255))
    return image


def _center_of_alpha(image):
    alpha = np.asarray(image.getchannel("A"), dtype=float)
    ys, xs = np.indices(alpha.shape)
    return ((alpha * (xs + 0.5)).sum() / alpha.sum(),
            (alpha * (ys + 0.5)).sum() / alpha.sum())


def _in_world(image, anchor, size=(1024, 1024)):
    world = Image.new("RGBA", size)
    offset = (round(size[0] / 2 - anchor[0]), round(size[1] / 2 - anchor[1]))
    assert offset[0] >= 0 and offset[1] >= 0
    assert offset[0] + image.width <= size[0]
    assert offset[1] + image.height <= size[1]
    world.paste(image, offset)
    return np.asarray(world.getchannel("A"))


@pytest.mark.parametrize("shear", [-2.0, 0.0, 2.0])
def test_foot_anchor_is_a_stationary_point(shear):
    image = Image.new("RGBA", (40, 40))
    ImageDraw.Draw(image).rectangle((17, 27, 23, 33), fill=(255, 255, 255, 255))
    anchor = (20.5, 30.5)
    shadow, shadow_anchor = project_shadow(
        image, anchor, squash=0.5, shear=shear, opacity=1, blur=0)
    center = _center_of_alpha(shadow)
    assert center[0] - shadow_anchor[0] == pytest.approx(0, abs=0.6)
    assert center[1] - shadow_anchor[1] == pytest.approx(0, abs=0.6)


@pytest.mark.parametrize("shear", [-1.0, 1.0])
def test_positive_shear_falls_left_and_negative_falls_right(shear):
    image = Image.new("RGBA", (40, 40))
    ImageDraw.Draw(image).rectangle((17, 7, 22, 12), fill="white")
    shadow, anchor = project_shadow(
        image, (20, 40), squash=0.5, shear=shear, opacity=1, blur=0)
    center = _center_of_alpha(shadow)
    assert center[0] - anchor[0] == pytest.approx(-30 * shear, abs=0.7)
    assert center[1] - anchor[1] == pytest.approx(-15, abs=0.7)


@pytest.mark.parametrize("anchor", [(0, 0), (0, 48), (32, 48), (7.5, 31.5)])
@pytest.mark.parametrize("shear", [-2.0, 2.0])
@pytest.mark.parametrize("blur", [0, 3, 9])
def test_canvas_does_not_crop_even_at_offcenter_anchors(anchor, shear, blur):
    source = _silhouette()
    shadow, shadow_anchor = project_shadow(
        source, anchor, squash=0.5, shear=shear, opacity=1, blur=blur)
    # Extra source transparency cannot reveal pixels missing from the original
    # projection. This catches both clipped shear and truncated blur tails.
    padded = Image.new("RGBA", (source.width + 160, source.height + 160))
    padded.paste(source, (80, 80))
    reference, reference_anchor = project_shadow(
        padded, (anchor[0] + 80, anchor[1] + 80),
        squash=0.5, shear=shear, opacity=1, blur=blur)
    assert np.array_equal(_in_world(shadow, shadow_anchor),
                          _in_world(reference, reference_anchor))
    bounds = shadow.getchannel("A").getbbox()
    assert bounds is not None
    assert 0 < bounds[0] < bounds[2] < shadow.width
    assert 0 < bounds[1] < bounds[3] < shadow.height


def test_identity_projection_keeps_alpha_and_applies_only_requested_color():
    source = _silhouette()
    shadow, anchor = project_shadow(
        source, (16, 48), squash=1, shear=0, opacity=1, blur=0, color=(1, 2, 3))
    x, y = int(anchor[0] - 16), int(anchor[1] - 48)
    crop = shadow.crop((x, y, x + source.width, y + source.height))
    assert crop.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    assert np.all(np.asarray(crop)[:, :, :3] == (1, 2, 3))


def test_opacity_is_monotonic_and_does_not_change_geometry():
    source = _silhouette()
    outputs = [project_shadow(source, (16, 48), opacity=value)
               for value in [0, 0.2, 0.4, 0.8, 1]]
    assert len({(image.size, anchor) for image, anchor in outputs}) == 1
    alphas = [np.asarray(image.getchannel("A")) for image, _ in outputs]
    assert not alphas[0].any()
    for low, high in zip(alphas, alphas[1:]):
        assert np.all(low <= high)
        assert low.sum() < high.sum()


@pytest.mark.parametrize("mode", ["RGBA", "RGB", "L", "P"])
def test_source_is_unchanged_and_output_is_rgba(mode):
    source = _silhouette().convert(mode)
    original = source.tobytes(), source.mode, source.size, source.getpalette()
    shadow, _ = project_shadow(source, (16, 48))
    assert shadow.mode == "RGBA"
    assert (source.tobytes(), source.mode, source.size, source.getpalette()) == original


def test_empty_silhouette_stays_transparent():
    shadow, anchor = project_shadow(Image.new("RGBA", (20, 30)), (10, 30))
    assert shadow.getchannel("A").getbbox() is None
    assert all(math.isfinite(value) for value in anchor)


@pytest.mark.parametrize("name,value", [
    ("squash", 0), ("squash", -1), ("squash", 2), ("squash", 1e-100),
    ("shear", 10000), ("shear", -10000),
    ("opacity", -0.1), ("opacity", 1.1),
    ("blur", -1), ("blur", 10000),
    *[(name, value) for name in ["squash", "shear", "opacity", "blur"]
      for value in [float("nan"), float("inf"), float("-inf"), True, "bad"]],
    ("color", (1, 2)), ("color", (-1, 2, 3)), ("color", (1, 2, 256)),
    ("color", (1, 2, 3.5)), ("color", (1, 2, float("nan"))),
    ("color", (True, 2, 3)), ("color", "red"), ("color", None),
])
def test_invalid_parameters_are_rejected(name, value):
    with pytest.raises(ValueError):
        project_shadow(_silhouette(), (16, 48), **{name: value})


@pytest.mark.parametrize("anchor", [None, (1,), (1, 2, 3), (float("nan"), 2),
                                         (1, float("inf")), (True, 2), (1e12, 2)])
def test_invalid_anchor_is_rejected(anchor):
    with pytest.raises(ValueError):
        project_shadow(_silhouette(), anchor)


def test_zero_sized_image_is_rejected():
    with pytest.raises(ValueError):
        project_shadow(Image.new("RGBA", (0, 10)), (0, 10))


def _asset_files(tmp_path):
    images = [_silhouette(), Image.new("RGBA", (32, 48))]
    ImageDraw.Draw(images[1]).ellipse((12, 22, 31, 47), fill=(255, 140, 30, 255))
    entries = []
    for index, (image, duration) in enumerate(zip(images, (0.07, 0.23))):
        path = tmp_path / f"source-{index}.png"
        image.save(path)
        entries.append({"file": path.name, "duration": duration})
    source = tmp_path / "source.asset.json"
    source.write_text(json.dumps({"kind": "sprite-gen-asset", "version": 1,
                                  "frames": entries, "anchor": [7.5, 47]}))
    return source


def test_run_publishes_loadable_sequence_strip_timing_anchor_and_preserves_source(tmp_path):
    source = _asset_files(tmp_path)
    original = load_asset(source)
    fingerprints = original.fingerprints()
    out_dir = tmp_path / "shadows"
    assert shadow.run(source=source, out_dir=out_dir) == 0
    generated = load_asset(out_dir / "shadow.asset.json")
    assert generated.durations == original.durations
    assert generated.duration == pytest.approx(0.3)
    assert len(generated.frames) == 2
    assert len({frame.size for frame in generated.frames}) == 1
    for index, frame in enumerate(original.frames):
        expected, anchor = project_shadow(frame, original.anchor)
        assert generated.frames[index].tobytes() == expected.tobytes()
        assert generated.anchor == anchor
    assert generated.frame_at(0.08).tobytes() == generated.frames[1].tobytes()
    with Image.open(out_dir / "shadow.png") as sheet:
        width, height = generated.size
        assert sheet.mode == "RGBA"
        assert sheet.size == (width * 2, height)
        for index, frame in enumerate(generated.frames):
            assert sheet.crop((index * width, 0, (index + 1) * width, height)).tobytes() == frame.tobytes()
    assert original.fingerprints() == fingerprints
    assert not (out_dir / runio.LOCK_FILENAME).exists()


@pytest.mark.parametrize("source_kind", ["png", "descriptor", "loop", "atlas"])
def test_run_uses_shared_loader_for_all_source_types(tmp_path, source_kind):
    source = _asset_files(tmp_path)
    state = None
    if source_kind == "png":
        source = tmp_path / "source-0.png"
    elif source_kind in ("loop", "atlas"):
        sheet = Image.new("RGBA", (64, 48))
        for index in range(2):
            with Image.open(tmp_path / f"source-{index}.png") as frame:
                sheet.paste(frame, (index * 32, 0))
        if source_kind == "loop":
            sheet.save(tmp_path / "motion.strip.png")
            source = tmp_path / "motion.strip.json"
            data = {"frames": 2, "w": 32, "h": 48, "delay_ms": 150}
        else:
            sheet.save(tmp_path / "atlas.png")
            source = tmp_path / "atlas.json"
            state = "walk"
            data = {
                "sprite_sheet_alpha": "atlas.png",
                "frame_layout": {"rows": {"walk": [
                    {"x": index * 32, "y": 0, "w": 32, "h": 48} for index in range(2)]}},
                "animation": {"rows": {"walk": {"durations_ms": [70, 230], "loop": True}}},
            }
        source.write_text(json.dumps(data))
    original = load_asset(source, state=state)
    fingerprints = original.fingerprints()
    out_dir = tmp_path / "shadows"
    assert shadow.run(source=source, out_dir=out_dir, state=state) == 0
    result = load_asset(out_dir / "shadow.asset.json")
    assert result.durations == original.durations
    assert len(result.frames) == len(original.frames)
    assert original.fingerprints() == fingerprints


def test_run_honors_fps_and_anchor_overrides(tmp_path):
    source = _asset_files(tmp_path)
    out_dir = tmp_path / "shadows"
    shadow.run(source=source, out_dir=out_dir, fps=20, anchor_x=0, anchor_y=40)
    result = load_asset(out_dir / "shadow.asset.json")
    assert result.durations == (0.05, 0.05)
    _, expected_anchor = project_shadow(load_asset(source).frames[0], (0, 40))
    assert result.anchor == expected_anchor


@pytest.mark.parametrize("kwargs", [
    {"opacity": float("nan")}, {"squash": 0}, {"shear": float("inf")},
    {"blur": -1}, {"color": (300, 0, 0)},
    {"anchor_x": 12}, {"anchor_y": 32}, {"anchor_x": 0, "anchor_y": float("nan")},
    {"fps": 0}, {"fps": -1}, {"fps": float("nan")}, {"fps": float("inf")},
])
def test_invalid_cli_values_do_not_create_output(tmp_path, kwargs):
    source = tmp_path / "source.png"
    _silhouette().save(source)
    before = source.read_bytes()
    out_dir = tmp_path / "shadows"
    with pytest.raises(ValueError):
        shadow.run(source=source, out_dir=out_dir, **kwargs)
    assert not out_dir.exists()
    assert source.read_bytes() == before


def test_malformed_source_does_not_create_output(tmp_path):
    source = tmp_path / "source.asset.json"
    source.write_text('{"kind":"sprite-gen-asset","version":1,"frames":[]}')
    out_dir = tmp_path / "shadows"
    with pytest.raises(ValueError):
        shadow.run(source=source, out_dir=out_dir)
    assert not out_dir.exists()


def test_huge_projection_is_rejected_before_output_creation(tmp_path):
    source = _asset_files(tmp_path)
    out_dir = tmp_path / "shadows"
    with pytest.raises(ValueError, match="canvas"):
        shadow.run(source=source, out_dir=out_dir, anchor_x=1_000_000, anchor_y=1_000_000)
    assert not out_dir.exists()


@pytest.mark.parametrize("filename", ["frame-000.png", "shadow.png", "shadow.asset.json"])
def test_outputs_cannot_overwrite_input_files(tmp_path, filename):
    if filename.endswith("json"):
        original = _asset_files(tmp_path)
        source = tmp_path / filename
        source.write_bytes(original.read_bytes())
    else:
        source = tmp_path / filename
        _silhouette().save(source)
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(ValueError, match="overwrite source"):
        shadow.run(source=source, out_dir=tmp_path)
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_output_cannot_overwrite_a_frame_referenced_by_descriptor(tmp_path):
    source = _asset_files(tmp_path)
    data = json.loads(source.read_text())
    referenced_frame = tmp_path / "frame-000.png"
    referenced_frame.write_bytes((tmp_path / "source-0.png").read_bytes())
    data["frames"][0]["file"] = referenced_frame.name
    source.write_text(json.dumps(data))
    before = referenced_frame.read_bytes()
    with pytest.raises(ValueError, match="overwrite source"):
        shadow.run(source=source, out_dir=tmp_path)
    assert referenced_frame.read_bytes() == before
    assert not (tmp_path / runio.LOCK_FILENAME).exists()


def test_render_failure_leaves_no_output_and_is_not_retried(tmp_path, monkeypatch):
    source = _asset_files(tmp_path)
    out_dir = tmp_path / "shadows"
    calls = []

    def fail_encode(image):
        calls.append(image)
        raise OSError("synthetic encoder failure")

    monkeypatch.setattr(shadow, "_png_bytes", fail_encode)
    with pytest.raises(OSError, match="synthetic encoder failure"):
        shadow.run(source=source, out_dir=out_dir)
    assert len(calls) == 1
    assert not out_dir.exists()


def test_staging_failure_preserves_existing_outputs_and_releases_lock(tmp_path, monkeypatch):
    source = _asset_files(tmp_path)
    out_dir = tmp_path / "shadows"
    shadow.run(source=source, out_dir=out_dir)
    before = {path.name: path.read_bytes() for path in out_dir.iterdir()}
    fsync = runio.os.fsync
    calls = []

    def fail_second_staged_file(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("synthetic disk failure")
        fsync(fd)

    monkeypatch.setattr(runio.os, "fsync", fail_second_staged_file)
    with pytest.raises(OSError, match="synthetic disk failure"):
        shadow.run(source=source, out_dir=out_dir, shear=-1)
    assert len(calls) == 2
    assert {path.name: path.read_bytes() for path in out_dir.iterdir()} == before
    assert not (out_dir / runio.LOCK_FILENAME).exists()


def test_live_writer_lock_is_respected_without_publishing(tmp_path):
    source = _asset_files(tmp_path)
    out_dir = tmp_path / "shadows"
    out_dir.mkdir()
    lock = out_dir / runio.LOCK_FILENAME
    lock.write_text(json.dumps({"owner": "synthetic-other-writer", "pid": os.getpid()}))
    before = lock.read_bytes()
    with pytest.raises(SystemExit, match="locked by synthetic-other-writer"):
        shadow.run(source=source, out_dir=out_dir)
    assert list(out_dir.iterdir()) == [lock]
    assert lock.read_bytes() == before


@pytest.mark.parametrize("target_kind", ["symlink", "directory"])
def test_invalid_target_is_rejected_without_mutating_destination(tmp_path, target_kind):
    source = _asset_files(tmp_path)
    out_dir = tmp_path / "shadows"
    out_dir.mkdir()
    target = out_dir / "shadow.png"
    if target_kind == "symlink":
        target.symlink_to(tmp_path / "unrelated.png")
    else:
        target.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        shadow.run(source=source, out_dir=out_dir)
    assert list(out_dir.iterdir()) == [target]


def test_parser_registers_specified_names_and_color_formats():
    parser = argparse.ArgumentParser()
    shadow.add_arguments(parser)
    args = parser.parse_args(["--source", "source.png", "--out-dir", "result", "--color", "#140f1e"])
    assert args.source == Path("source.png")
    assert args.out_dir == Path("result")
    assert args.color == (20, 15, 30)
    assert parser.parse_args(["--source", "s", "--out-dir", "o", "--color", "2,3,4"]).color == (2, 3, 4)
    with pytest.raises(SystemExit):
        parser.parse_args(["--source", "s", "--out-dir", "o", "--color", "256,0,0"])


def test_module_cli_executes_the_real_publication_path(tmp_path):
    source = _asset_files(tmp_path)
    out_dir = tmp_path / "shadows"
    result = subprocess.run(
        [sys.executable, "-m", "sprite_gen.effects.shadow", "--source", str(source),
         "--out-dir", str(out_dir), "--shear", "-0.8", "--color", "10,20,30"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["frames"] == 2
    assert load_asset(Path(report["manifest"])).durations == (0.07, 0.23)
    assert not (out_dir / runio.LOCK_FILENAME).exists()
