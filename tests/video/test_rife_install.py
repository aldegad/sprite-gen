# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen rife install` without a network: a release-shaped zip built here, its SHA-256 put
in place of the pinned one. The zip is checked before anything is unpacked, only what the engine
runs is kept, the install lands where `rife.locate()` looks last, and a second run does nothing."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from sprite_gen import cli
from sprite_gen.video import rife, rife_install

PLATFORM = rife.platform_release() or "ubuntu"
TOP = f"rife-ncnn-vulkan-{rife.RELEASE}-{PLATFORM}"
BINARY = rife.installed_binary(Path("."), PLATFORM).name


def _release_zip(tmp_path: Path, *, model: bool = True) -> Path:
    path = tmp_path / rife_install.zip_name(PLATFORM)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{TOP}/", "")
        zf.writestr(f"{TOP}/{BINARY}", "#!/bin/sh\nexit 0\n")
        zf.writestr(f"{TOP}/LICENSE", "MIT\n")
        zf.writestr(f"{TOP}/README.md", "readme\n")
        if model:
            zf.writestr(f"{TOP}/{rife.MODEL}/flownet.param", "param\n")
            zf.writestr(f"{TOP}/{rife.MODEL}/flownet.bin", "bin\n")
        zf.writestr(f"{TOP}/rife-v4/flownet.bin", "another model\n")  # not what the engine runs
        zf.writestr(f"{TOP}/{rife.MODEL}/../../escape.txt", "outside\n")
        zf.writestr("elsewhere/rife-ncnn-vulkan", "not in the release folder\n")
    return path


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    """The fixture zip, with its SHA-256 as the pinned one, and the engine looking only at tmp_path."""
    (tmp_path / "zips").mkdir()
    path = _release_zip(tmp_path / "zips")
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    return path


def test_install_keeps_what_the_engine_runs_where_locate_finds_it(pinned, tmp_path):
    with pytest.raises(rife.RifeNotInstalled):
        rife.locate()
    report = rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    target = tmp_path / "data" / "rife" / TOP
    assert report["installed"] == "now" and report["dir"] == str(target.resolve())
    assert sorted(report["files"]) == sorted([BINARY, "LICENSE", "README.md", f"{rife.MODEL}/flownet.bin", f"{rife.MODEL}/flownet.param"])
    assert not (target / "rife-v4").exists() and not (tmp_path / "data" / "escape.txt").exists()
    assert os.access(target / BINARY, os.X_OK) and (target / BINARY).stat().st_mode & stat.S_IXUSR
    assert json.loads((target / rife_install.MARKER).read_text())["sha256"] == rife_install.ZIP_SHA256[PLATFORM]
    assert rife.locate() == ((target / BINARY).resolve(), (target / rife.MODEL).resolve())
    assert report["engine_finds"] == str((target / BINARY).resolve()) and "note" not in report
    assert [p.name for p in (tmp_path / "data" / "rife").iterdir()] == [TOP]  # no staging or download left behind


def test_a_zip_whose_hash_differs_installs_nothing(pinned, tmp_path, monkeypatch):
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, "0" * 64)
    with pytest.raises(SystemExit, match="expected 0000"):
        rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    assert not (tmp_path / "data" / "rife" / TOP).exists()


def test_a_zip_without_the_model_is_refused(pinned, tmp_path, monkeypatch):
    (tmp_path / "bare").mkdir()
    bare = _release_zip(tmp_path / "bare", model=False)
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, hashlib.sha256(bare.read_bytes()).hexdigest())
    with pytest.raises(SystemExit, match="flownet.param"):
        rife_install.install(zip_path=bare, platform=PLATFORM, run_check=False)
    assert not (tmp_path / "data" / "rife" / TOP).exists()


def test_a_second_run_does_nothing_and_force_unpacks_again(pinned, tmp_path):
    rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    stray = tmp_path / "data" / "rife" / TOP / "stray"
    stray.write_text("x")
    assert rife_install.install(platform=PLATFORM, run_check=False)["installed"] == "already"  # no zip, no download
    assert stray.exists()
    assert rife_install.install(zip_path=pinned, platform=PLATFORM, force=True, run_check=False)["installed"] == "now"
    assert not stray.exists()


def test_the_download_is_checked_like_a_given_zip(pinned, monkeypatch, tmp_path):
    monkeypatch.setattr(rife_install, "URL", pinned.parent.as_uri() + "/{name}")
    report = rife_install.install(platform=PLATFORM, run_check=False)
    assert report["installed"] == "now" and report["source"] == pinned.as_uri()
    assert rife.locate()[0].parent.name == TOP


def test_another_dir_names_the_variable_that_points_the_engine_at_it(pinned, tmp_path, capsys):
    rc = cli.main(["rife", "install", "--zip", str(pinned), "--dir", str(tmp_path / "elsewhere"), "--no-check"])
    out, err = capsys.readouterr()
    assert rc == 0 and json.loads(out)["installed"] == "now"
    assert f"SPRITE_GEN_RIFE={tmp_path / 'elsewhere' / TOP / BINARY}" in err
    with pytest.raises(rife.RifeNotInstalled):
        rife.locate()


# Windows runs the .exe it installs, never a shebang script, so a binary that starts and
# reports its own failure cannot be stood in for there; the next test covers Windows.
@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script cannot stand in for rife-ncnn-vulkan.exe")
def test_the_check_frame_fails_on_a_binary_that_does_not_run(pinned, tmp_path):
    rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    target = tmp_path / "data" / "rife" / TOP
    (target / BINARY).write_text("#!/bin/sh\necho no vulkan >&2\nexit 1\n")
    with pytest.raises(SystemExit, match="check frame failed.*no vulkan"):
        rife_install.install(platform=PLATFORM)


def test_the_check_frame_fails_on_a_binary_that_cannot_be_started(pinned, tmp_path):
    """A damaged or wrong-platform build: the OS refuses to start it (ENOEXEC, WinError 193)."""
    rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    target = tmp_path / "data" / "rife" / TOP
    (target / BINARY).write_bytes(b"\x00 not a program\n")
    with pytest.raises(SystemExit, match="check frame failed.*could not be started"):
        rife_install.install(platform=PLATFORM)


def test_a_platform_without_a_release_build_is_refused(monkeypatch):
    monkeypatch.setattr(rife, "platform_release", lambda: "")
    with pytest.raises(SystemExit, match="ships no build"):
        rife_install.install(run_check=False)
