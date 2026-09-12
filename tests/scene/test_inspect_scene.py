# SPDX-License-Identifier: Apache-2.0
"""Scene inspection: the seam a viewer sees, phase closure, visibility and guarded report publication."""

import json
import os
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw

from sprite_gen.scene import inspect_scene as module
from sprite_gen.scene.inspect_scene import inspect_scene, output_path, run
from sprite_gen.scene.model import load_scene
from sprite_gen.spec import runio


def frames_asset(tmp_path, name, images, duration=0.1):
    entries = []
    for index, image in enumerate(images):
        file = f"{name}-{index}.png"
        image.save(tmp_path / file)
        entries.append({"file": file, "duration": duration})
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"kind": "sprite-gen-asset", "version": 1, "frames": entries}))
    return path


def write_scene(tmp_path, assets, layers, *, width=16, height=16, fps=10, duration=1.0, background=(0, 0, 0), name="scene.json"):
    spec = {"kind": "sprite-gen-scene", "version": 1, "canvas": {"width": width, "height": height, "background": list(background)},
            "fps": fps, "duration": duration, "assets": assets, "layers": layers}
    path = tmp_path / name
    path.write_text(json.dumps(spec))
    return path


def ramp_scene(tmp_path):
    frames_asset(tmp_path, "ramp", [Image.new("RGBA", (16, 16), (i * 25, i * 25, i * 25, 255)) for i in range(10)])
    return write_scene(tmp_path, {"ramp": "ramp.json"}, [{"id": "ramp", "asset": "ramp", "at": [8, 16]}])


def bob_frames(xs=(4, 6, 8, 6)):
    images = []
    for x in xs:
        image = Image.new("RGBA", (16, 16))
        ImageDraw.Draw(image).rectangle((x, 4, x + 3, 11), fill=(220, 220, 220, 255))
        images.append(image)
    return images


def test_ramp_wrap_pop_fails_the_seam_even_though_the_phase_closes(tmp_path):
    report = inspect_scene(load_scene(ramp_scene(tmp_path)))
    loop = report["loop"]
    assert loop["adjacent_mae_mean"] == pytest.approx(18.75) and loop["adjacent_mae_max"] == pytest.approx(18.75)
    assert loop["seam_mae"] == pytest.approx(168.75)
    assert loop["seam_limit"] == pytest.approx(37.5)
    assert loop["phase_closure_mae"] == 0 and loop["phase_closure_pass"] is True
    assert loop["seam_pass"] is False and loop["pass"] is False
    assert report["status"] == "needs-review"
    assert any("loop wrap" in warning for warning in report["warnings"])


def test_periodic_cycle_passes_seam_and_phase_closure(tmp_path):
    frames_asset(tmp_path, "bob", bob_frames())
    spec = write_scene(tmp_path, {"bob": "bob.json"}, [{"id": "bob", "asset": "bob", "at": [8, 16]}], duration=0.4)
    loop = inspect_scene(load_scene(spec))["loop"]
    assert loop["adjacent_mae_max"] > 0
    assert loop["seam_mae"] == pytest.approx(loop["adjacent_mae_max"])
    assert loop["phase_closure_mae"] == 0
    assert loop["pass"] is True


def test_moving_sprite_that_does_not_return_fails_phase_closure_not_the_seam(tmp_path):
    frames_asset(tmp_path, "bob", bob_frames())
    spec = write_scene(tmp_path, {"bob": "bob.json"}, [{"id": "bob", "asset": "bob", "at": [2, 16], "velocity": [10, 0]}], duration=0.4)
    report = inspect_scene(load_scene(spec))
    assert report["loop"]["phase_closure_pass"] is False and report["loop"]["pass"] is False
    assert any("end-to-start" in warning for warning in report["warnings"])


@pytest.mark.parametrize("velocity, closes", [([16, 0], True), ([10, 0], False)])
def test_scrolling_background_closes_only_when_it_travels_whole_periods(tmp_path, velocity, closes):
    strip = Image.new("RGBA", (16, 4))
    for x in range(16):
        ImageDraw.Draw(strip).line((x, 0, x, 3), fill=(x * 16, 40, 200 - x * 12, 255))
    strip.save(tmp_path / "strip.png")
    spec = write_scene(tmp_path, {"strip": "strip.png"}, [{"id": "ground", "asset": "strip", "at": [0, 16], "repeat_x": True, "velocity": velocity}])
    report = inspect_scene(load_scene(spec))
    # 16 px/s over one second is exactly one period: the wrap is an ordinary
    # one-frame scroll step. 10 px/s leaves a 6 px jump at the wrap, which both
    # the phase closure and the seam report.
    assert report["loop"]["phase_closure_pass"] is closes
    assert report["loop"]["seam_pass"] is closes
    assert report["loop"]["pass"] is closes
    assert report["tile_joins"]["ground"]["axis"] == "x" and report["tile_joins"]["ground"]["period"] == 16
    assert report["clipping"]["ground"]["side_frames"] == 0 and report["clipping"]["ground"]["offscreen_frames"] == 0


def test_static_scene_is_a_trivial_loop(tmp_path):
    frames_asset(tmp_path, "still", bob_frames((4,)))
    report = inspect_scene(load_scene(write_scene(tmp_path, {"still": "still.json"}, [{"id": "s", "asset": "still", "at": [8, 16]}])))
    assert report["loop"] == {"seam_mae": 0.0, "adjacent_mae_mean": 0.0, "adjacent_mae_max": 0.0, "seam_limit": 1.0, "seam_pass": True,
                              "phase_closure_mae": 0.0, "phase_closure_pass": True, "pass": True}
    assert report["status"] == "measured" and report["warnings"] == []
    assert report["visual_review_required"] is True


def test_offscreen_partially_clipped_and_empty_frames_are_reported_separately(tmp_path):
    frames_asset(tmp_path, "box", bob_frames((6,)))
    blank = Image.new("RGBA", (16, 16))
    frames_asset(tmp_path, "blink", [bob_frames((6,))[0], blank, blank])
    spec = write_scene(tmp_path, {"box": "box.json", "blink": "blink.json"}, [
        {"id": "gone", "asset": "box", "at": [8, -50]},
        {"id": "cut", "asset": "box", "at": [8, 8]},
        {"id": "blink", "asset": "blink", "at": [8, 16]},
    ], duration=0.3)
    report = inspect_scene(load_scene(spec))
    assert report["clipping"]["gone"] == {"top_frames": 0, "bottom_frames": 0, "side_frames": 0, "offscreen_frames": 3,
                                          "empty_frames": 0, "minimum_top_y": -62.0}
    assert report["clipping"]["cut"]["top_frames"] == 3 and report["clipping"]["cut"]["offscreen_frames"] == 0
    assert report["clipping"]["cut"]["minimum_top_y"] == -4.0
    assert report["clipping"]["blink"]["empty_frames"] == 2 and report["clipping"]["blink"]["offscreen_frames"] == 0
    assert report["status"] == "needs-review"
    assert any(warning.endswith("gone") for warning in report["warnings"])
    assert any("viewport top or bottom" in warning for warning in report["warnings"])


def test_key_like_colours_are_counted_and_flagged(tmp_path):
    frames_asset(tmp_path, "green", [Image.new("RGBA", (4, 4), (0, 255, 0, 255))])
    report = inspect_scene(load_scene(write_scene(tmp_path, {"green": "green.json"}, [{"id": "g", "asset": "green", "at": [8, 16]}], duration=0.1)))
    assert report["key_like_pixels"] == {"green": 16, "magenta": 0}
    assert any("key-like" in warning for warning in report["warnings"])
    assert report["sources"]["green"]["edge_contact"][0] == {"top": 4, "bottom": 4, "left": 4, "right": 4}


def test_run_publishes_the_report_under_the_writer_lock_and_releases_it(tmp_path, monkeypatch, capsys):
    spec = ramp_scene(tmp_path)
    out = tmp_path / "reports" / "inspection.json"
    calls = []
    real_acquire, real_write, real_release = module.acquire_run_dir_lock, module.atomic_write_set, module.release_run_dir_lock

    def acquire(directory, owner):
        calls.append(("lock", owner))
        return real_acquire(directory, owner)

    def write(payloads):
        assert calls == [("lock", "scene-inspect")] and list(payloads) == [out]
        assert (out.parent / runio.LOCK_FILENAME).exists()
        calls.append(("write",))
        real_write(payloads)

    def release(directory):
        calls.append(("release",))
        real_release(directory)

    monkeypatch.setattr(module, "acquire_run_dir_lock", acquire)
    monkeypatch.setattr(module, "atomic_write_set", write)
    monkeypatch.setattr(module, "release_run_dir_lock", release)
    assert run(spec, out=out, require_loop=True) == 1
    assert calls == [("lock", "scene-inspect"), ("write",), ("release",)]
    assert json.loads(out.read_text()) == json.loads(capsys.readouterr().out)
    assert not (out.parent / runio.LOCK_FILENAME).exists()
    calls.clear()
    assert run(spec, out=out) == 0
    assert calls == [("lock", "scene-inspect"), ("write",), ("release",)]


@pytest.mark.parametrize("target", [".sprite-gen.lock", "reports/.sprite-gen.lock", ".out.sg-rwlock", "scene.json", "ramp-3.png",
                                    "spec-symlink.json", "asset-hardlink.png", "a-directory", "ramp-0.png/report.json"])
def test_run_refuses_reserved_names_scene_inputs_and_their_aliases(tmp_path, capsys, target):
    spec = ramp_scene(tmp_path)
    out = tmp_path / target
    if target == "spec-symlink.json":
        out.symlink_to(spec)
    elif target == "asset-hardlink.png":
        os.link(tmp_path / "ramp-1.png", out)
    elif target == "a-directory":
        out.mkdir()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit, match="scene-inspect: inspection output"):
        run(spec, out=out)
    assert capsys.readouterr().out == ""
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert not list(tmp_path.rglob(runio.LOCK_FILENAME))


def test_output_path_is_resolved_and_checked_before_rendering(tmp_path):
    scene = load_scene(ramp_scene(tmp_path))
    assert output_path(tmp_path / "reports" / "x.json", scene) == (tmp_path / "reports" / "x.json").resolve()
    with pytest.raises(ValueError, match="reserved lock name"):
        output_path(tmp_path / runio.LOCK_FILENAME, scene)


def test_run_respects_another_writers_live_lock_without_publishing(tmp_path, capsys):
    spec = ramp_scene(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    lock = reports / runio.LOCK_FILENAME
    lock.write_text(json.dumps({"owner": "synthetic-other-writer", "pid": os.getpid()}))
    before = lock.read_bytes()
    with pytest.raises(SystemExit, match="locked by synthetic-other-writer"):
        run(spec, out=reports / "inspection.json")
    assert capsys.readouterr().out == ""
    assert list(reports.iterdir()) == [lock] and lock.read_bytes() == before


def test_stdout_only_run_never_locks_or_writes(tmp_path, capsys):
    spec = ramp_scene(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert run(spec) == 0
    assert json.loads(capsys.readouterr().out)["loop"]["pass"] is False
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_module_entrypoint_reports_and_signals_a_failed_loop(tmp_path):
    spec = ramp_scene(tmp_path)
    out = tmp_path / "inspection.json"
    result = subprocess.run([sys.executable, "-m", "sprite_gen.scene.inspect_scene", "--spec", str(spec), "--out", str(out), "--require-loop"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report == json.loads(out.read_text())
    assert report["kind"] == "sprite-gen-scene-inspection" and report["loop"]["seam_pass"] is False
    assert not (tmp_path / runio.LOCK_FILENAME).exists()
