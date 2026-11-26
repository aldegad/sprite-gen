# SPDX-License-Identifier: Apache-2.0
"""A run of the binary is started with the Vulkan loader told to leave out the implicit layers a machine installs into
every Vulkan process — an overlay, a capture hook, a shader recorder — but the ones that choose the GPU (`rife.binary_env`):
they are no part of a frame, and where a machine has them they can be most of what a run costs to start
(docs/loop-repair.md section 1). A caller that names the layers to leave out itself keeps its own choice. The frame is the
same bytes either way, through the real binary where one is installed (`real_rife`)."""

from __future__ import annotations

import stat
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
    """Each run of a frame — alone (`rife.between`) or started together (a `Rife`'s) — is started with the implicit
    layers left out and the GPU-choosing ones allowed, and with the rest of this process's environment as it is."""
    for name in LOADER:
        monkeypatch.delenv(name, raising=False)
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
    binary, seen = _telling_binary(tmp_path)
    rife.between(_legs(36, 70), _legs(60, 46), 0.5, binary=binary, model=tmp_path / rife.MODEL, tmp=tmp_path)
    assert seen.read_text().splitlines() == [f"{given}|unset|unset"] * 2


@pytest.mark.slow  # two seconds on a GPU: the fast bundle leaves it to --full and the GPU lane
@pytest.mark.real_rife
def test_real_rife_frames_with_the_implicit_layers_left_out_are_the_frames_made_with_them(tmp_path, monkeypatch):
    """Through the real binary and model, a frame made with the implicit layers left out (the engine's default) is byte
    for byte the frame made with every layer the machine installs (VK_LOADER_LAYERS_DISABLE set empty): on a machine with
    implicit layers, they are no part of a frame."""
    binary, model = rife.locate()
    a, b = _legs(36, 70), _legs(60, 46)
    for t in (0.25, 0.6):
        monkeypatch.delenv("VK_LOADER_LAYERS_DISABLE", raising=False)
        left_out = rife.between(a, b, t, binary=binary, model=model, tmp=tmp_path)
        monkeypatch.setenv("VK_LOADER_LAYERS_DISABLE", "")
        with_them = rife.between(a, b, t, binary=binary, model=model, tmp=tmp_path)
        assert np.array_equal(np.asarray(left_out), np.asarray(with_them)), t
