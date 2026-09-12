# SPDX-License-Identifier: Apache-2.0
"""The standalone entrypoint publishes external PNGs through canonical run IO."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image

from sprite_gen.background import tile
from sprite_gen.spec import runio


def invoke(source, out, *extra):
    return subprocess.run(
        [sys.executable, "-m", "sprite_gen.background.tile", "--source", str(source),
         "--out", str(out), "--period", "8", "--overlap", "3", *map(str, extra)],
        capture_output=True, text=True, timeout=20,
    )


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "external.png"
    Image.new("RGBA", (24, 13), (32, 71, 117, 97)).save(path)
    return path


def test_module_publishes_png_and_report_without_sprite_run(source, tmp_path):
    before = source.read_bytes()
    out, report = tmp_path / "output" / "tile.png", tmp_path / "reports" / "tile.json"
    proc = invoke(source, out, "--report", report)
    assert proc.returncode == 0, proc.stderr
    assert "pass" in proc.stdout
    with Image.open(out) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.size == (8, 13)
        assert image.getpixel((0, 0)) == (32, 71, 117, 97)
    metrics = json.loads(report.read_text())
    assert metrics["source"] == str(source)
    assert metrics["output"] == str(out)
    assert metrics["status"] == "pass"
    assert source.read_bytes() == before
    assert not list(tmp_path.rglob("sprite-request.json"))
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob(runio.LOCK_FILENAME))


def test_registration_arguments_use_the_same_run_function(source, tmp_path):
    parser = argparse.ArgumentParser()
    tile.add_arguments(parser)
    args = parser.parse_args(["--source", str(source), "--out", str(tmp_path / "out.png"),
                              "--period", "8", "--overlap", "3", "--axis", "y"])
    try:
        assert tile.run(**vars(args)) == 0
    finally:
        runio.release_run_dir_lock(tmp_path)
    report = json.loads((tmp_path / "out.report.json").read_text())
    assert report["size"] == [24, 8]
    assert report["axis"] == "y"


@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink", "report-hardlink"])
def test_source_overwrite_and_aliases_are_refused(source, tmp_path, alias):
    before = source.read_bytes()
    out = tmp_path / "new" / "tile.png"
    report = tmp_path / "new" / "report.json"
    if alias == "direct":
        out = source
    elif alias in ("symlink", "hardlink"):
        out = tmp_path / "alias.png"
        if alias == "symlink":
            out.symlink_to(source)
        else:
            os.link(source, out)
    else:
        report = tmp_path / "alias.json"
        os.link(source, report)
    proc = invoke(source, out, "--report", report)
    assert proc.returncode != 0
    assert "overwrite source" in proc.stderr
    assert source.read_bytes() == before
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.rglob(runio.LOCK_FILENAME))


@pytest.mark.parametrize("failure", ["geometry", "bad-axis", "bad-overlap", "not-png", "animation",
                                      "out-format", "report-format", "nested-paths", "parent-file"])
def test_invalid_inputs_do_not_create_output_dirs_or_locks(source, tmp_path, failure):
    out = tmp_path / "new" / "out.png"
    report = tmp_path / "new" / "out.json"
    extra = []
    if failure == "geometry":
        extra = ["--period", "40"]
    elif failure == "bad-axis":
        extra = ["--axis", "z"]
    elif failure == "bad-overlap":
        extra = ["--overlap", "0"]
    elif failure == "not-png":
        Image.new("RGB", (24, 13)).save(source, format="JPEG")
    elif failure == "animation":
        Image.new("RGBA", (24, 13), "red").save(
            source, save_all=True, append_images=[Image.new("RGBA", (24, 13), "blue")], duration=50)
    elif failure == "out-format":
        out = out.with_suffix(".jpg")
    elif failure == "report-format":
        report = report.with_suffix(".txt")
    elif failure == "nested-paths":
        report = out / "report.json"
    elif failure == "parent-file":
        report = source / "report.json"
    before = source.read_bytes()
    proc = invoke(source, out, "--report", report, *extra)
    assert proc.returncode != 0
    assert source.read_bytes() == before
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.rglob(runio.LOCK_FILENAME))


def test_report_may_not_alias_output(source, tmp_path):
    out, report = tmp_path / "out.png", tmp_path / "report.json"
    out.write_bytes(b"original output")
    os.link(out, report)
    proc = invoke(source, out, "--report", report)
    assert proc.returncode != 0
    assert "different files" in proc.stderr
    assert out.read_bytes() == report.read_bytes() == b"original output"


def test_active_writer_lock_is_not_retried_or_overwritten(source, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    lock = output / runio.LOCK_FILENAME
    lock.write_text(json.dumps({"pid": os.getpid(), "owner": "synthetic-test-writer"}))
    before = lock.read_bytes()
    proc = invoke(source, output / "tile.png")
    assert proc.returncode != 0
    assert "locked by synthetic-test-writer" in proc.stderr
    assert lock.read_bytes() == before
    assert not (output / "tile.png").exists()
    assert not (output / "tile.report.json").exists()


def test_canonical_publication_stages_complete_set_and_surfaces_failure(source, tmp_path, monkeypatch):
    out, report = tmp_path / "tile.png", tmp_path / "tile.json"
    out.write_bytes(b"previous PNG")
    report.write_text("previous report")
    called = []

    def fail_second_staging_file(*args, **kwargs):
        called.append(kwargs["prefix"])
        if len(called) == 2:
            raise OSError("synthetic staging failure")
        return original_mkstemp(*args, **kwargs)

    original_mkstemp = runio.tempfile.mkstemp
    monkeypatch.setattr(runio.tempfile, "mkstemp", fail_second_staging_file)
    try:
        with pytest.raises(SystemExit, match="synthetic staging failure"):
            tile.run(source=source, out=out, report=report, period=8, overlap=3)
    finally:
        runio.release_run_dir_lock(tmp_path)
    assert len(called) == 2
    assert out.read_bytes() == b"previous PNG"
    assert report.read_text() == "previous report"
    assert not list(tmp_path.glob("*.tmp"))


def test_in_process_run_releases_every_lock_it_acquired_on_success(source, tmp_path):
    out, report = tmp_path / "png" / "tile.png", tmp_path / "json" / "tile.json"
    assert tile.run(source=source, out=out, report=report, period=8, overlap=3) == 0
    for directory in (out.parent, report.parent):
        assert not (directory / runio.LOCK_FILENAME).exists()
        assert (directory / runio.LOCK_FILENAME).resolve() not in runio._HELD_LOCKS
    assert tile.run(source=source, out=out, report=report, period=8, overlap=3) == 0


def test_partial_lock_acquisition_is_undone_when_the_second_directory_is_held(source, tmp_path):
    out, report = tmp_path / "a" / "tile.png", tmp_path / "b" / "tile.json"
    report.parent.mkdir()
    foreign = report.parent / runio.LOCK_FILENAME
    foreign.write_text(json.dumps({"pid": os.getpid(), "owner": "synthetic-other-writer"}))
    before = foreign.read_bytes()
    with pytest.raises(SystemExit, match="locked by synthetic-other-writer"):
        tile.run(source=source, out=out, report=report, period=8, overlap=3)
    assert not (out.parent / runio.LOCK_FILENAME).exists()
    assert (out.parent / runio.LOCK_FILENAME).resolve() not in runio._HELD_LOCKS
    assert foreign.read_bytes() == before
    assert not out.exists() and not report.exists()
