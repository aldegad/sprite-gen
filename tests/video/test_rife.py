# SPDX-License-Identifier: Apache-2.0
"""RIFE runner: it is found or refused by name, and a real binary (when one is installed)
interpolates an RGBA frame that sits between its two neighbours, alpha included.

The real-binary test runs only where `rife-ncnn-vulkan` is reachable (SPRITE_GEN_RIFE or PATH);
the locating rules need no binary at all."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import rife


def _fake_binary(tmp_path: Path, with_model: bool = True) -> Path:
    binary = tmp_path / "bin" / rife.BINARY
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    if with_model:
        (binary.parent / rife.MODEL).mkdir()
        for name in ("flownet.param", "flownet.bin"):
            (binary.parent / rife.MODEL / name).write_bytes(b"x")
    return binary


def test_missing_binary_is_refused_with_the_install_line(monkeypatch, tmp_path):
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(rife.RifeUnavailable, match="install rife-ncnn-vulkan 20221029"):
        rife.locate()


def test_model_beside_the_binary_is_found(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    found, model = rife.locate()
    assert found == binary.resolve() and model == binary.resolve().parent / rife.MODEL


def test_missing_model_is_refused(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path, with_model=False)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    with pytest.raises(rife.RifeUnavailable, match="flownet.param"):
        rife.locate()


def _disc(cx: int) -> Image.Image:
    im = np.zeros((96, 128, 4), dtype=np.uint8)
    yy, xx = np.mgrid[:96, :128]
    inside = (yy - 48) ** 2 + (xx - cx) ** 2 <= 18 ** 2
    im[inside] = (200, 60, 40, 255)
    return Image.fromarray(im, "RGBA")


def _real_rife_available() -> bool:
    try:
        rife.locate()
    except rife.RifeUnavailable:
        return False
    return True


@pytest.mark.skipif(not _real_rife_available(), reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH)")
def test_real_rife_makes_the_frame_between_in_colour_and_alpha():
    interpolate = rife.Rife()
    mid = interpolate(_disc(40), _disc(72), 0.5)
    a = np.asarray(mid)[..., 3] / 255.0
    xs = np.nonzero(a > 0.5)[1]
    assert abs(xs.mean() - 56) <= 3  # the disc moved half way, not cross-faded at both ends
    assert a[:, :20].max() == 0 and a[:, -20:].max() == 0  # no coverage invented far from the body
    rgb = np.asarray(mid)[..., :3][a > 0.9]
    assert np.abs(rgb.mean(axis=0) - (200, 60, 40)).max() < 12  # unpremultiplied back to the body colour
    assert interpolate.made == 1
