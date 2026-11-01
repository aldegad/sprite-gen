# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen rife install` without a network: a release-shaped zip built here, its SHA-256 put
in place of the pinned one. The zip is checked before anything is unpacked, only what the engine
runs is kept — the binary and both models, rife-v4.25 and rife-v4.6 — the install lands where
`rife.locate()` looks last, and a second run does nothing."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from sprite_gen import cli
from sprite_gen.video import rife, rife_install

PLATFORM = rife.platform_release() or "ubuntu"
TOP = f"rife-ncnn-vulkan-{rife.RELEASE}-{PLATFORM}"  # where the install puts it, whatever the zip's folder is named
BINARY = rife.installed_binary(Path("."), PLATFORM).name


# The fixture zip's model files, which its pins name in place of the models' (`rife.MODEL_SHA256`).
MODEL_FILES = {model: {"flownet.param": f"{model} param\n".encode(), "flownet.bin": f"{model} bin\n".encode()}
               for model in rife.MODELS}
MODEL_PINS = {model: {name: {p: hashlib.sha256(data).hexdigest() for p in rife.PLATFORMS} for name, data in files.items()}
              for model, files in MODEL_FILES.items()}


def _release_zip(tmp_path: Path, *, models=tuple(rife.MODELS), platform: str = PLATFORM) -> Path:
    """A zip shaped as the release's for `platform`: its folder named as the release names it (`rife_install.ZIP_TOP`)."""
    path = tmp_path / rife_install.zip_name(platform)
    top, binary = rife_install.ZIP_TOP[platform], rife.installed_binary(Path("."), platform).name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{top}/", "")
        zf.writestr(f"{top}/{binary}", "#!/bin/sh\nexit 0\n")
        zf.writestr(f"{top}/LICENSE", "MIT\n")
        zf.writestr(f"{top}/README.md", "readme\n")
        for model in models:
            for name, data in MODEL_FILES[model].items():
                zf.writestr(f"{top}/{model}/{name}", data)
        zf.writestr(f"{top}/rife-v4/flownet.bin", "another model\n")  # not what the engine runs
        zf.writestr(f"{top}/{rife.MODEL}/../../escape.txt", "outside\n")
        zf.writestr("elsewhere/rife-ncnn-vulkan", "not in the release folder\n")
    return path


def test_each_platform_s_zip_is_named_and_unpacked_as_the_release_ships_it():
    """TNTwise's 20250112 release: `<platform>.zip` from its GitHub release, the Windows zip's folder named after the branch
    it was built from — pinned by sha256, each of the three."""
    assert rife.RELEASE == "20250112" and rife_install.URL.startswith("https://github.com/TNTwise/rife-ncnn-vulkan/")
    assert [rife_install.zip_name(p) for p in rife.PLATFORMS] == ["macos.zip", "ubuntu.zip", "windows.zip"]
    assert rife_install.ZIP_TOP == {"macos": "macos", "ubuntu": "ubuntu", "windows": "rife-ncnn-vulkan-refs/heads/master-windows"}
    assert set(rife_install.ZIP_SHA256) == set(rife.PLATFORMS) and all(len(v) == 64 for v in rife_install.ZIP_SHA256.values())


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    """The fixture zip, with its SHA-256 and its models' as the pinned ones, and the engine looking only at tmp_path."""
    (tmp_path / "zips").mkdir()
    path = _release_zip(tmp_path / "zips")
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(rife, "MODEL_SHA256", MODEL_PINS)
    monkeypatch.delenv("SPRITE_GEN_RIFE_BACKEND", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL_NAME", raising=False)
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
    assert sorted(report["files"]) == sorted([BINARY, "LICENSE", "README.md",
                                              *(f"{m}/{f}" for m in rife.MODELS for f in ("flownet.bin", "flownet.param"))])
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


@pytest.mark.parametrize("missing", list(rife.MODELS))
def test_a_zip_without_either_model_is_refused(pinned, tmp_path, monkeypatch, missing):
    (tmp_path / "bare").mkdir()
    bare = _release_zip(tmp_path / "bare", models=[m for m in rife.MODELS if m != missing])
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


@pytest.mark.parametrize("model", list(rife.MODELS))
@pytest.mark.parametrize("backend", ["ncnn", "torch"])
def test_a_zip_whose_model_is_not_the_pinned_one_installs_nothing(pinned, tmp_path, monkeypatch, backend, model):
    """Its hash checked, the zip unpacked, each model's two files are checked too (`rife.MODEL_SHA256`), for either backend:
    a model that is not the one pinned is named with what it is and what was expected, and nothing is installed."""
    pins = {**MODEL_PINS, model: {**MODEL_PINS[model], "flownet.bin": {p: "0" * 64 for p in rife.PLATFORMS}}}
    monkeypatch.setattr(rife, "MODEL_SHA256", pins)
    with pytest.raises(SystemExit, match=rf"{model}: flownet.bin has sha256 [0-9a-f]{{64}}, expected one of 0{{64}}.*nothing was installed"):
        rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False, backend=backend)
    assert not (tmp_path / "data" / "rife" / TOP).exists()


class _Halfway:
    """A torch runner stood in: the frame between is the first disc moved half the 36 px to the second (the check's discs)."""

    def __init__(self, model: Path):
        self.model = model

    def frame(self, colour, cover, t):
        return np.roll(colour[0], 18, axis=1), np.roll(cover[0], 18, axis=1)

    def identity(self):
        return {"backend": "torch"}

    def describe(self):
        return {"kind": "rife-torch", "device": "cpu", "model": self.model.name}


def test_the_torch_backend_installs_where_the_binary_does_not_run_and_checks_its_frame_through_torch(monkeypatch, tmp_path, capsys):
    """`--backend torch` (or SPRITE_GEN_RIFE_BACKEND=torch, which it defaults to) reads the zip's model only: on a Linux
    machine its x86-64 binary does not run on, it is not refused as the binary's install is; the model's files are checked;
    the check frame is made through the torch backend from the model just installed — the binary in it never runs (it makes
    no frame) — and the report names the model the engine will read."""
    monkeypatch.setattr(rife, "platform_release", lambda: "ubuntu")
    monkeypatch.setattr(rife_install.platform_mod, "machine", lambda: "aarch64")
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))
    for name in ("SPRITE_GEN_RIFE", "SPRITE_GEN_RIFE_MODEL", "SPRITE_GEN_RIFE_BACKEND", "SPRITE_GEN_RIFE_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "zips").mkdir()
    zipped = _release_zip(tmp_path / "zips", platform="ubuntu")
    monkeypatch.setitem(rife_install.ZIP_SHA256, "ubuntu", hashlib.sha256(zipped.read_bytes()).hexdigest())
    monkeypatch.setattr(rife, "MODEL_SHA256", MODEL_PINS)
    with pytest.raises(SystemExit, match="x86-64 only and this machine is aarch64"):
        rife_install.install(zip_path=zipped, run_check=False)
    asked = []
    monkeypatch.setattr(rife, "torch_runner", lambda model=None: asked.append(model) or _Halfway(model), raising=False)
    report = rife_install.install(zip_path=zipped, backend="torch")
    model = (tmp_path / "data" / "rife" / f"rife-ncnn-vulkan-{rife.RELEASE}-ubuntu" / rife.MODEL).resolve()
    assert report["backend"] == "torch" and report["installed"] == "now" and asked == [model]
    assert report["check"] == {"passed": True, "disc_centre_x": 48.0}
    assert report["engine_finds"] == str(model) and "note" not in report
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    assert cli.main(["rife", "install", "--zip", str(zipped)]) == 0
    assert json.loads(capsys.readouterr().out)["backend"] == "torch" and len(asked) == 2
