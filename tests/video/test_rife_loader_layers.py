# SPDX-License-Identifier: Apache-2.0
"""A run of the binary is started with the implicit layers a machine installs into every Vulkan process — an overlay, a
capture hook, a shader recorder — left out, but the ones that choose the GPU (`rife.binary_env`): they are no part of a
frame, and where a machine has them they can be most of what a run costs to start (docs/loop-repair.md section 1). The
loader is told so (VK_LOADER_LAYERS_DISABLE, _ALLOW), and on Windows, where a loader reads neither in an elevated process,
each layer the machine registers is also left out by the variable its own manifest names for it; a RIFE says once which
layers its runs leave out, and which manifests it could not read. A caller that names the layers to leave out itself
keeps its own choice. The frame is the same bytes either way, made on the same GPU, through the real binary where one is
installed (`real_rife`)."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import rife

LOADER = ("VK_LOADER_LAYERS_DISABLE", "VK_LOADER_LAYERS_ALLOW")


def _legs(x_near: int, x_far: int) -> Image.Image:
    """Hips and two outlined legs on transparency, the near one drawn over the far one."""
    im = np.zeros((96, 128, 4), dtype=np.uint8)
    for x, grey in ((x_far, 200), (x_near, 245)):
        im[16:86, x - 8:x + 8] = (20, 20, 20, 255)
        im[17:85, x - 7:x + 7] = (grey, grey, grey, 255)
    im[10:18, 30:90] = (20, 20, 20, 255)
    im[11:17, 31:89] = (230, 230, 230, 255)
    return Image.fromarray(im, "RGBA")


def _telling_binary(root: Path) -> tuple[Path, Path]:
    """A stand-in binary that writes, a line a run, the loader's two layer variables it was started with ("unset" where
    there is none) and the test's own variable, and hands back its first image."""
    seen = root / "seen"
    binary = root / rife.BINARY
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s|%s\\n" "${{VK_LOADER_LAYERS_DISABLE-unset}}" "${{VK_LOADER_LAYERS_ALLOW-unset}}" '
        f'"${{SPRITE_GEN_TEST_PASSED-unset}}" >> "{seen}"\n'
        'while [ $# -gt 0 ]; do case "$1" in -0) a="$2"; shift;; -o) o="$2"; shift;; esac; shift; done\n'
        'cp "$a" "$o"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary, seen


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in binary is a shell script")
def test_a_run_of_the_binary_leaves_the_loaders_implicit_layers_out_but_those_that_choose_the_gpu(tmp_path, monkeypatch):
    """Each run of a frame — alone (`rife.between`) or started together (a `Rife`'s) — is started with the loader told to
    leave the implicit layers out and allow the GPU-choosing ones, and with the rest of this process's environment as
    it is."""
    for name in LOADER:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rife, "WINDOWS", False)  # no layer registered: the loader filter alone
    monkeypatch.setenv("SPRITE_GEN_TEST_PASSED", "kept")
    binary, seen = _telling_binary(tmp_path)
    a, b = _legs(36, 70), _legs(60, 46)
    rife.between(a, b, 0.5, binary=binary, model=tmp_path / rife.MODEL, tmp=tmp_path)
    rife.Rife(binary, tmp_path / rife.MODEL)(a, b, 0.5)
    lines = seen.read_text().splitlines()
    assert lines == [f"~implicit~|{rife.LOADER_LAYERS_ALLOW}|kept"] * 4
    assert rife.LOADER_LAYERS_ALLOW == "*device_select*,*optimus*,*switchable_graphics*"


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in binary is a shell script")
@pytest.mark.parametrize("given", ["", "*obs*", "~all~"])
def test_a_caller_that_names_the_layers_to_leave_out_keeps_its_own_choice(tmp_path, monkeypatch, given):
    """VK_LOADER_LAYERS_DISABLE set by the caller — empty (no layer left out), a glob, every layer — reaches the binary
    as it is, and no allow list is added beside it."""
    monkeypatch.setenv("VK_LOADER_LAYERS_DISABLE", given)
    monkeypatch.delenv("VK_LOADER_LAYERS_ALLOW", raising=False)
    monkeypatch.setattr(rife, "WINDOWS", False)
    binary, seen = _telling_binary(tmp_path)
    rife.between(_legs(36, 70), _legs(60, 46), 0.5, binary=binary, model=tmp_path / rife.MODEL, tmp=tmp_path)
    assert seen.read_text().splitlines() == [f"{given}|unset|unset"] * 2


def _manifest(path: Path, text: str, bom: bool = False) -> Path:
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode())
    return path


def _layer(name: str, switch: str) -> dict:
    return {"name": name, "type": "GLOBAL", "library_path": f"{name}.dll", "api_version": "1.3.0",
            "implementation_version": "1", "description": name, "disable_environment": {switch: "1"}}


def _one(layer: dict) -> str:
    return json.dumps({"file_format_version": "1.0.0", "layer": layer})


def test_each_implicit_layer_is_left_out_by_its_own_manifests_variable_but_those_that_choose_the_gpu(tmp_path):
    """An overlay's and a capture hook's manifest (one layer, or a list of them, with or without a byte order mark) each
    name the variable that leaves the layer out; the layers that choose the GPU (Mesa's device select, NVIDIA's Optimus,
    AMD's switchable graphics, their names in any case) are kept; a file that is not a layer manifest, or is not there,
    is named as not read."""
    manifests = [
        _manifest(tmp_path / "overlay.json", _one(_layer("VK_LAYER_reshade", "DISABLE_VK_LAYER_reshade_1"))),
        _manifest(tmp_path / "hook.json", json.dumps({"file_format_version": "1.1.2", "layers": [
            _layer("VK_LAYER_OBS_HOOK", "DISABLE_VULKAN_OBS_CAPTURE")]}), bom=True),
        _manifest(tmp_path / "optimus.json", _one(_layer("VK_LAYER_NV_optimus", "DISABLE_LAYER_NV_OPTIMUS_1"))),
        _manifest(tmp_path / "amd.json", _one(_layer("VK_LAYER_AMD_switchable_graphics_64",
                                                     "DISABLE_LAYER_AMD_SWITCHABLE_GRAPHICS_1"))),
        _manifest(tmp_path / "mesa.json", _one(_layer("VK_LAYER_MESA_Device_Select", "NODEVICE_SELECT"))),
        _manifest(tmp_path / "broken.json", "{ not a manifest"),
        _manifest(tmp_path / "driver.json", json.dumps({"file_format_version": "1.0.0", "ICD": {}})),
        tmp_path / "gone.json",
    ]
    left_out = rife.implicit_layers_left_out(manifests)
    assert left_out.switches == {"DISABLE_VK_LAYER_reshade_1": "1", "DISABLE_VULKAN_OBS_CAPTURE": "1"}
    assert left_out.names == ["VK_LAYER_reshade", "VK_LAYER_OBS_HOOK"]
    assert [unread.split(" (")[0] for unread in left_out.unread] == [str(tmp_path / n) for n in
                                                                    ("broken.json", "driver.json", "gone.json")]


def test_on_windows_a_run_is_started_with_each_registered_layers_own_variable_beside_the_loader_filter(tmp_path,
                                                                                                      monkeypatch):
    """On Windows the layers the machine registers are left out by their own manifests' variables too, one the caller has
    set already kept at its value, beside the loader filter. A caller that names the layers to leave out
    (VK_LOADER_LAYERS_DISABLE, empty for none) keeps its own choice."""
    overlay = _manifest(tmp_path / "overlay.json", _one(_layer("VK_LAYER_reshade", "DISABLE_VK_LAYER_reshade_1")))
    hook = _manifest(tmp_path / "hook.json", _one(_layer("VK_LAYER_OBS_HOOK", "DISABLE_VULKAN_OBS_CAPTURE")))
    monkeypatch.setattr(rife, "WINDOWS", True)
    monkeypatch.setattr(rife, "registered_implicit_layers", lambda: [overlay, hook])
    for name in (*LOADER, "DISABLE_VK_LAYER_reshade_1"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISABLE_VULKAN_OBS_CAPTURE", "own")
    env = rife.binary_env()
    assert (env["DISABLE_VK_LAYER_reshade_1"], env["DISABLE_VULKAN_OBS_CAPTURE"]) == ("1", "own")
    assert (env["VK_LOADER_LAYERS_DISABLE"], env["VK_LOADER_LAYERS_ALLOW"]) == ("~implicit~", rife.LOADER_LAYERS_ALLOW)
    monkeypatch.setenv("VK_LOADER_LAYERS_DISABLE", "")
    env = rife.binary_env()
    assert env["VK_LOADER_LAYERS_DISABLE"] == "" and "DISABLE_VK_LAYER_reshade_1" not in env


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in binary is a shell script")
def test_a_rife_says_once_which_layers_its_runs_leave_out_and_which_manifests_it_could_not_read(tmp_path, monkeypatch,
                                                                                               capsys):
    """A RIFE says on its first frame, a line, the implicit layers its runs leave out, and a line, the manifests it could
    not read — once for all the frames it makes, not a line a run; a caller's own VK_LOADER_LAYERS_DISABLE, nothing; a
    machine that registers no layer, nothing."""
    overlay = _manifest(tmp_path / "overlay.json", _one(_layer("VK_LAYER_reshade", "DISABLE_VK_LAYER_reshade_1")))
    hook = _manifest(tmp_path / "hook.json", _one(_layer("VK_LAYER_OBS_HOOK", "DISABLE_VULKAN_OBS_CAPTURE")))
    broken = _manifest(tmp_path / "broken.json", "{ not a manifest")
    monkeypatch.setattr(rife, "WINDOWS", True)
    monkeypatch.setattr(rife, "registered_implicit_layers", lambda: [overlay, hook, broken])
    monkeypatch.delenv("VK_LOADER_LAYERS_DISABLE", raising=False)
    binary, _ = _telling_binary(tmp_path)
    a, b = _legs(36, 70), _legs(60, 46)
    interpolate = rife.Rife(binary, tmp_path / rife.MODEL)
    for t in (0.25, 0.5, 0.75):
        interpolate(a, b, t)
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2, lines
    assert lines[0].startswith("rife: ") and "VK_LAYER_reshade, VK_LAYER_OBS_HOOK" in lines[0]
    assert lines[1].startswith("rife: ") and str(broken) in lines[1] and "could not be read" in lines[1]
    monkeypatch.setenv("VK_LOADER_LAYERS_DISABLE", "")
    rife.Rife(binary, tmp_path / rife.MODEL)(a, b, 0.5)
    monkeypatch.delenv("VK_LOADER_LAYERS_DISABLE")
    monkeypatch.setattr(rife, "registered_implicit_layers", lambda: [])
    rife.Rife(binary, tmp_path / rife.MODEL)(a, b, 0.5)
    assert capsys.readouterr().err == ""


@pytest.mark.slow  # two seconds on a GPU: the fast bundle leaves it to --full and the GPU lane
@pytest.mark.real_rife
def test_real_rife_frames_with_the_implicit_layers_left_out_are_the_frames_made_with_them_on_the_same_gpu(tmp_path,
                                                                                                          monkeypatch):
    """Through the real binary and model, a frame made with the implicit layers left out (the engine's default) is byte
    for byte the frame made with every layer the machine installs (VK_LOADER_LAYERS_DISABLE set empty), and the binary
    names the same GPUs to choose from either way (its "queueC" lines): on a machine with implicit layers, they are no
    part of a frame."""
    binary, model = rife.locate()
    a, b = _legs(36, 70), _legs(60, 46)
    real_run, devices = subprocess.run, []

    def run(*args, **kwargs):
        proc = real_run(*args, **kwargs)
        devices.append(sorted(line.strip() for line in (proc.stderr or "").splitlines() if "queueC" in line))
        return proc

    monkeypatch.setattr(rife.subprocess, "run", run)
    for t in (0.25, 0.6):
        monkeypatch.delenv("VK_LOADER_LAYERS_DISABLE", raising=False)
        left_out = rife.between(a, b, t, binary=binary, model=model, tmp=tmp_path)
        monkeypatch.setenv("VK_LOADER_LAYERS_DISABLE", "")
        with_them = rife.between(a, b, t, binary=binary, model=model, tmp=tmp_path)
        assert np.array_equal(np.asarray(left_out), np.asarray(with_them)), t
    assert devices[0] and devices.count(devices[0]) == len(devices), devices
