# SPDX-License-Identifier: Apache-2.0
"""Deterministic placement, staged publication and explicit encoding of scene renders."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw

from sprite_gen.scene import render
from sprite_gen.scene.model import load_scene
from sprite_gen.scene.render import Renderer, render_scene
from sprite_gen.spec import assets, runio


HAS_FFMPEG = shutil.which("ffmpeg") is not None
RED, GREEN, BLUE, BLACK = (200, 30, 30, 255), (30, 200, 30, 255), (30, 30, 200, 255), (0, 0, 0, 255)


def square(path, color=RED, size=(4, 4)):
    Image.new("RGBA", size, color).save(path)
    return path


def write_scene(tmp_path, layers, *, width=16, height=12, fps=10, duration=0.5, background=(0, 0, 0), **extra):
    assets_block = extra.pop("assets", None) or {"red": "red.png"}
    if "red.png" in json.dumps(assets_block) and not (tmp_path / "red.png").exists():
        square(tmp_path / "red.png")
    spec = {"kind": "sprite-gen-scene", "version": 1, "canvas": {"width": width, "height": height, "background": list(background)},
            "fps": fps, "duration": duration, "assets": assets_block, "layers": layers, **extra}
    path = tmp_path / "scene.json"
    path.write_text(json.dumps(spec))
    return path


def snapshot(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in Path(directory).rglob("*") if p.is_file()}


def test_anchor_velocity_camera_parallax_and_z_order_place_pixels(tmp_path):
    square(tmp_path / "green.png", GREEN, (2, 2))
    spec = write_scene(tmp_path, [
        {"id": "front", "asset": "green", "at": [8, 12], "z": 1},
        {"id": "hero", "asset": "red", "at": [8, 12], "velocity": [10, 0], "parallax": 0.5},
    ], assets={"red": "red.png", "green": "green.png"}, camera={"at": [2, 0], "velocity": [4, 0]})
    renderer = Renderer(load_scene(spec))
    frame0 = renderer.frame(0)
    # Bottom-centre anchor: a 4x4 red square at (8, 12) covers x 6..9, y 8..11; camera at 2 with parallax .5 shifts it left by 1.
    assert frame0.getpixel((5, 8)) == RED and frame0.getpixel((8, 11)) == RED and frame0.getpixel((9, 8)) == BLACK
    # The green 2x2 layer (parallax 1, so the camera shifts it by 2) sits above the red one.
    assert frame0.getpixel((5, 10)) == GREEN and frame0.getpixel((6, 11)) == GREEN and frame0.getpixel((7, 10)) == RED
    frame = renderer.frame(0.3)
    # 10 px/s minus half the 4 px/s camera velocity: +2.4 px after 0.3 s, rounded to 2.
    assert frame.getpixel((7, 8)) == RED and frame.getpixel((10, 8)) == RED and frame.getpixel((11, 8)) == BLACK
    plate = renderer.frame(0, only="hero")
    assert plate.getpixel((0, 0)) == (0, 0, 0, 0) and plate.getpixel((7, 10)) == RED


def test_repeat_x_tiles_the_period_across_the_canvas_and_wraps_offsets(tmp_path):
    strip = Image.new("RGBA", (6, 3))
    ImageDraw.Draw(strip).rectangle((0, 0, 1, 2), fill=BLUE)
    strip.save(tmp_path / "strip.png")
    spec = write_scene(tmp_path, [{"id": "ground", "asset": "strip", "at": [0, 12], "repeat_x": True, "period": 3, "velocity": [1, 0]}],
                       assets={"strip": "strip.png"}, duration=1)
    renderer = Renderer(load_scene(spec))
    row = [renderer.frame(0).getpixel((x, 10)) for x in range(16)]
    assert row == [BLUE, BLUE, BLACK] * 5 + [BLUE]
    row = [renderer.frame(0.9).getpixel((x, 10)) for x in range(16)]
    # 0.9 px of travel rounds each copy one pixel to the right; the copy from the left wraps in.
    assert row == [BLACK, BLUE, BLUE] * 5 + [BLACK]
    frame, geometry = renderer.frame(0, geometry=True)
    assert all(entry["repeat_x"] for entry in geometry) and any(entry["visible"] for entry in geometry)


def test_geometry_reports_offscreen_copies_unclipped_and_empty_frames(tmp_path):
    empty = tmp_path / "blank.png"
    Image.new("RGBA", (4, 4)).save(empty)
    spec = write_scene(tmp_path, [
        {"id": "gone", "asset": "red", "at": [8, -100]},
        {"id": "cut", "asset": "red", "at": [8, 2]},
        {"id": "blank", "asset": "blank", "at": [8, 8]},
    ], assets={"red": "red.png", "blank": "blank.png"})
    _, geometry = Renderer(load_scene(spec)).frame(0, geometry=True)
    by_id = {entry["id"]: entry for entry in geometry}
    assert by_id["gone"] == {"id": "gone", "repeat_x": False, "bbox": [6, -104, 10, -100], "visible": False}
    assert by_id["cut"] == {"id": "cut", "repeat_x": False, "bbox": [6, -2, 10, 2], "visible": True}
    assert by_id["blank"] == {"id": "blank", "repeat_x": False, "bbox": None, "visible": False}


def test_png_render_publishes_the_reported_set_atomically_and_leaves_inputs_alone(tmp_path):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12], "scale": 0.5}], fps=4, duration=1)
    before = snapshot(tmp_path)
    out = tmp_path / "out"
    report = render_scene(spec, out, formats="png")
    published = snapshot(out)
    assert sorted(published) == sorted(report["files"])
    assert set(report["files"]) == {"check-00000.png", "check-00001.png", "check-00002.png", "check-00003.png",
                                    *(f"frames/frame-{i:05d}.png" for i in range(4)), "placement.json", "scene.report.json"}
    assert json.loads(published["scene.report.json"]) == report
    placement = json.loads(published["placement.json"])
    assert placement["layers"][0]["raster_size"] == [2, 2] and placement["layers"][0]["scale"] == 0.5
    assert placement["source_fingerprints"] == report["source_fingerprints"] == {
        str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in (spec, tmp_path / "red.png")}
    assert report["encoding"] == {} and report["inspection"]["status"] == "measured"
    with Image.open(out / "frames" / "frame-00000.png") as frame:
        assert frame.size == (16, 12) and frame.getpixel((7, 10)) == RED and frame.getpixel((6, 10)) == BLACK
    assert snapshot(tmp_path) == {**before, **{f"out/{k}": v for k, v in published.items()}}
    assert not (out / runio.LOCK_FILENAME).exists()


def test_export_layers_publishes_frame_zero_plates_and_records_it(tmp_path):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}])
    out = tmp_path / "out"
    report = render_scene(spec, out, formats="png", export_layers=True)
    assert "layers/hero.png" in report["files"]
    with Image.open(out / "layers" / "hero.png") as plate:
        assert plate.getpixel((0, 0)) == (0, 0, 0, 0) and plate.getpixel((7, 10)) == RED
    assert json.loads((out / "placement.json").read_text())["layer_plate_time"] == 0


@pytest.mark.parametrize("formats, kwargs, message", [
    ("png,mp4", {}, "even canvas dimensions"),
    ("gif", {"gif_fps": 20}, "cannot exceed the scene fps"),
    ("gif", {"gif_width": 40}, "gif_width"),
    ("gif", {"gif_width": 0}, "gif_width"),
    ("gif", {"gif_colors": 1}, "gif_colors"),
    ("gif", {"gif_colors": 257}, "gif_colors"),
    ("gif", {"gif_dither": "random"}, "gif_dither"),
    ("gif", {"gif_max_bytes": 0}, "gif_max_bytes"),
    ("webm", {}, "formats"),
    ("", {}, "formats"),
])
def test_prerequisites_fail_before_any_output_exists_and_the_corrected_call_succeeds(tmp_path, formats, kwargs, message):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}], width=15)
    out = tmp_path / "out"
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match=message):
        render_scene(spec, out, formats=formats, **kwargs)
    assert not out.exists()
    assert snapshot(tmp_path) == before
    assert render_scene(spec, out, formats="png")["frames"] == 5


@pytest.mark.parametrize("formats", ["mp4", "gif", "png,gif"])
def test_missing_ffmpeg_is_reported_before_any_directory_is_created(tmp_path, monkeypatch, formats):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}])
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="ffmpeg is required"):
        render_scene(spec, tmp_path / "out", formats=formats)
    assert not (tmp_path / "out").exists()


def test_transparent_background_cannot_encode_video_but_renders_png(tmp_path):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}], background=(0, 0, 0, 0))
    for formats, message in (("mp4", "opaque"), ("gif", "opaque")):
        with pytest.raises(ValueError, match=message):
            render_scene(spec, tmp_path / "out", formats=formats)
    assert not (tmp_path / "out").exists()
    render_scene(spec, tmp_path / "out", formats="png")
    with Image.open(tmp_path / "out" / "check-00000.png") as frame:
        assert frame.getpixel((0, 0)) == (0, 0, 0, 0)


def test_encoder_failure_leaves_no_output_no_lock_and_is_not_retried(tmp_path, monkeypatch):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}])
    calls = []

    def fail(args):
        calls.append(args)
        raise ValueError("ffmpeg failed: synthetic encoder failure")

    monkeypatch.setattr(render, "_ffmpeg", fail)
    monkeypatch.setattr(render.shutil, "which", lambda name: "/synthetic/ffmpeg")
    out = tmp_path / "renders" / "out"
    with pytest.raises(ValueError, match="synthetic encoder failure"):
        render_scene(spec, out, formats="png,mp4")
    assert len(calls) == 1
    assert not out.exists()
    assert (tmp_path / "renders").is_dir()
    assert not list(tmp_path.rglob(runio.LOCK_FILENAME))
    monkeypatch.undo()
    assert render_scene(spec, out, formats="png")["frames"] == 5


def test_staging_failure_keeps_a_preexisting_output_directory_empty_without_subdirectories(tmp_path, monkeypatch):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}])
    out = tmp_path / "out"
    out.mkdir()
    fsync = runio.os.fsync
    calls = []

    def fail_third_staged_file(fd):
        calls.append(fd)
        if len(calls) == 3:
            raise OSError("synthetic disk failure")
        fsync(fd)

    monkeypatch.setattr(runio.os, "fsync", fail_third_staged_file)
    with pytest.raises(OSError, match="synthetic disk failure"):
        render_scene(spec, out, formats="png", export_layers=True)
    assert len(calls) == 3
    assert out.is_dir() and list(out.iterdir()) == []
    monkeypatch.undo()
    assert "layers/hero.png" in render_scene(spec, out, formats="png", export_layers=True)["files"]


def test_input_replaced_after_decoding_is_detected_and_nothing_is_published(tmp_path, monkeypatch):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [2, 4]}], width=4, height=4, fps=1, duration=1)
    original = assets._read_bytes

    def read_then_replace(path):
        data = original(path)
        if Path(path).name == "red.png":
            square(Path(path), BLUE)
        return data

    monkeypatch.setattr(assets, "_read_bytes", read_then_replace)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="inputs changed during render"):
        render_scene(spec, out, formats="png")
    assert not out.exists()
    monkeypatch.undo()
    report = render_scene(spec, out, formats="png")
    assert report["source_fingerprints"][str((tmp_path / "red.png").resolve())] == hashlib.sha256((tmp_path / "red.png").read_bytes()).hexdigest()
    with Image.open(out / "check-00000.png") as frame:
        assert frame.getpixel((2, 2)) == BLUE


def test_shared_source_replaced_between_asset_loads_fails_before_any_output_exists(tmp_path, monkeypatch):
    actor = square(tmp_path / "actor.png")
    spec = write_scene(tmp_path, [{"id": "left", "asset": "first", "at": [4, 8]}, {"id": "right", "asset": "second", "at": [12, 8]}],
                       assets={"first": "actor.png", "second": "actor.png"}, height=8, fps=1, duration=1)
    original = assets._read_bytes
    reads = []

    def replace_after_first_read(path):
        data = original(path)
        if Path(path).name == "actor.png":
            reads.append(data)
            if len(reads) == 1:
                square(actor, BLUE)
        return data

    monkeypatch.setattr(assets, "_read_bytes", replace_after_first_read)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="changed during load"):
        render_scene(spec, out, formats="png")
    assert len(reads) == 2 and reads[0] != reads[1]
    assert not out.exists() and sorted(p.name for p in tmp_path.iterdir()) == ["actor.png", "scene.json"]
    monkeypatch.undo()
    report = render_scene(spec, out, formats="png")
    assert report["source_fingerprints"][str(actor.resolve())] == hashlib.sha256(actor.read_bytes()).hexdigest()
    with Image.open(out / "check-00000.png") as frame:
        assert frame.getpixel((4, 6)) == BLUE and frame.getpixel((12, 6)) == BLUE


@pytest.mark.parametrize("problem", ["inputs-inside", "nonempty", "live-lock", "file"])
def test_output_directory_rules_are_enforced_without_touching_the_destination(tmp_path, problem):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}])
    out, message, expected = tmp_path / "out", "", ValueError
    if problem == "inputs-inside":
        out, message = tmp_path, "contains scene inputs"
    elif problem == "nonempty":
        out.mkdir()
        (out / "previous.txt").write_text("keep me")
        message = "must be empty"
    elif problem == "live-lock":
        out.mkdir()
        (out / runio.LOCK_FILENAME).write_text(json.dumps({"owner": "synthetic-other-writer", "pid": os.getpid()}))
        message, expected = "locked by synthetic-other-writer", SystemExit
    else:
        out.write_text("a file")
        message = "must be a directory"
    before = snapshot(tmp_path)
    with pytest.raises(expected, match=message):
        render_scene(spec, out, formats="png")
    assert snapshot(tmp_path) == before


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_gif_and_mp4_use_explicit_settings_with_source_fps_and_full_palette_by_default(tmp_path):
    frames = []
    for i in range(6):
        image = Image.new("RGBA", (24, 20))
        ImageDraw.Draw(image).rectangle((4 + i, 4, 10 + i, 16), fill=RED)
        image.save(tmp_path / f"f{i}.png")
        frames.append({"file": f"f{i}.png", "duration": 0.1})
    (tmp_path / "walk.json").write_text(json.dumps({"kind": "sprite-gen-asset", "version": 1, "frames": frames}))
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "walk", "at": [32, 36]}], assets={"walk": "walk.json"},
                       width=64, height=40, fps=10, duration=0.6, background=(30, 60, 90))
    report = render_scene(spec, tmp_path / "default", formats="gif,mp4")
    assert report["encoding"]["gif"] == {"width": 64, "height": 40, "fps": 10.0, "colors": 256, "dither": "none", "frames": 6,
                                         "bytes": (tmp_path / "default" / "scene.gif").stat().st_size, "max_bytes": 8_000_000}
    assert report["encoding"]["mp4"]["codec"] == "libx264" and (tmp_path / "default" / "scene.mp4").stat().st_size == report["encoding"]["mp4"]["bytes"]
    with Image.open(tmp_path / "default" / "scene.gif") as gif:
        assert gif.n_frames == 6 and gif.size == (64, 40)
        assert gif.info["duration"] == 100 and gif.info.get("loop") == 0
    explicit = render_scene(spec, tmp_path / "explicit", formats="gif", gif_width=32, gif_fps=5, gif_colors=16, gif_dither="bayer")
    assert explicit["encoding"]["gif"]["width"] == 32 and explicit["encoding"]["gif"]["height"] == 20
    assert explicit["encoding"]["gif"]["frames"] == 3 and explicit["encoding"]["gif"]["colors"] == 16
    assert explicit["encoding"]["gif"]["dither"] == "bayer"
    with Image.open(tmp_path / "explicit" / "scene.gif") as gif:
        assert gif.n_frames == 3 and gif.size == (32, 20) and gif.info["duration"] == 200


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_gif_over_budget_fails_by_name_without_degrading_or_publishing(tmp_path, monkeypatch):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}], background=(30, 60, 90))
    calls = []
    original = render._ffmpeg
    monkeypatch.setattr(render, "_ffmpeg", lambda args: calls.append(args) or original(args))
    with pytest.raises(ValueError, match=r"over the 100 byte budget.*never reduced automatically"):
        render_scene(spec, tmp_path / "out", formats="gif", gif_max_bytes=100)
    assert len(calls) == 2
    assert not (tmp_path / "out").exists()


def test_parser_exposes_the_gif_controls_with_documented_defaults():
    parser = argparse.ArgumentParser()
    render.add_arguments(parser)
    args = parser.parse_args(["--spec", "s.json", "--out-dir", "o"])
    assert (args.formats, args.gif_max_bytes, args.gif_width, args.gif_fps, args.gif_colors, args.gif_dither, args.export_layers) == \
        ("mp4,gif", 8_000_000, None, None, 256, "none", False)
    args = parser.parse_args(["--spec", "s.json", "--out-dir", "o", "--gif-width", "320", "--gif-fps", "12", "--gif-colors", "64", "--gif-dither", "sierra2_4a"])
    assert (args.gif_width, args.gif_fps, args.gif_colors, args.gif_dither) == (320, 12.0, 64, "sierra2_4a")
    with pytest.raises(SystemExit):
        parser.parse_args(["--spec", "s.json", "--out-dir", "o", "--gif-dither", "random"])
    assert render.GIF_MAX_WIDTH_DEFAULT == 640


def test_module_entrypoint_renders_and_reports_failures_by_name(tmp_path):
    spec = write_scene(tmp_path, [{"id": "hero", "asset": "red", "at": [8, 12]}], width=15)
    out = tmp_path / "out"
    result = subprocess.run([sys.executable, "-m", "sprite_gen.scene.render", "--spec", str(spec), "--out-dir", str(out), "--formats", "png"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["frames"] == 5 and summary["inspection_status"] == "measured" and "placement.json" in summary["files"]
    failed = subprocess.run([sys.executable, "-m", "sprite_gen.scene.render", "--spec", str(spec), "--out-dir", str(tmp_path / "video"), "--formats", "mp4"],
                            capture_output=True, text=True, check=False)
    assert failed.returncode != 0 and "scene-render: MP4 requires even canvas dimensions" in failed.stderr
    assert not (tmp_path / "video").exists()
