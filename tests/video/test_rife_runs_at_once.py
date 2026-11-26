# SPDX-License-Identifier: Apache-2.0
"""A `Rife` on the binary starts the runs a frame or a read asks for together (`rife.runs`, `rife.runs_at_once`), each
the very command it was alone: its frame is the bytes of the same runs made one after another (`rife.between`), through
the real binary where one is installed (`real_rife`). How the read's sixteen passes are put back together is
tests/video/test_rife.py's."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.util import cpus
from sprite_gen.video import rife


def _legs(x_near: int, x_far: int) -> Image.Image:
    """Hips and two outlined legs on transparency, the near one drawn over the far one."""
    im = np.zeros((96, 128, 4), dtype=np.uint8)
    for x, grey in ((x_far, 200), (x_near, 245)):
        im[16:86, x - 8:x + 8] = (20, 20, 20, 255)
        im[17:85, x - 7:x + 7] = (grey, grey, grey, 255)
    im[10:18, 30:90] = (20, 20, 20, 255)
    im[11:17, 31:89] = (230, 230, 230, 255)
    return Image.fromarray(im, "RGBA")


@pytest.mark.slow  # two seconds on a GPU: the fast bundle leaves it to --full and the GPU lane
@pytest.mark.real_rife
def test_real_rife_frames_with_runs_started_together_are_the_runs_made_one_after_another(tmp_path):
    """The frame a `Rife` makes, its two runs started together, is byte for byte the frame `rife.between` makes with the
    same binary and model, its runs one after another."""
    binary, model = rife.locate()
    interpolate = rife.Rife(binary, model)
    a, b = _legs(36, 70), _legs(60, 46)
    for t in (0.25, 0.6):
        alone = rife.between(a, b, t, binary=binary, model=model, tmp=tmp_path)
        assert np.array_equal(np.asarray(interpolate(a, b, t)), np.asarray(alone)), t
    assert interpolate.calls == 4


def _meeting_binary(root: Path, together: int) -> Path:
    """A stand-in binary that hands back its first image only once `together` calls of it have started: called one after
    another, the first waits three seconds for the second and fails."""
    started = root / "started"
    started.mkdir()
    binary = root / rife.BINARY
    binary.write_text(
        "#!/bin/sh\n"
        f'touch "{started}/$$"\n'
        "n=0\n"
        f'while [ "$(ls "{started}" | wc -l)" -lt {together} ]; do\n'
        "  n=$((n + 1)); [ $n -gt 60 ] && exit 1; sleep 0.05\n"
        "done\n"
        'while [ $# -gt 0 ]; do case "$1" in -0) a="$2"; shift;; -o) o="$2"; shift;; esac; shift; done\n'
        'cp "$a" "$o"\n', encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in binary is a shell script")
def test_a_frames_two_runs_are_started_together_each_in_a_folder_of_its_own(tmp_path, monkeypatch):
    """The colour run and the coverage run of a frame are under way at once: a binary that answers only once two of it
    have started makes the frame (one after another, the first would wait for a second that never comes)."""
    monkeypatch.setattr(cpus, "usable", lambda: 4)  # whatever this machine has: two at once wants two CPUs
    assert rife.runs_at_once() == rife.RUNS_AT_ONCE == 2
    binary = _meeting_binary(tmp_path, 2)
    interpolate = rife.Rife(binary, tmp_path / rife.MODEL)
    a, b = _legs(36, 70), _legs(60, 46)
    frame = interpolate(a, b, 0.5)
    assert np.array_equal(np.asarray(frame)[..., 3], np.asarray(a)[..., 3])  # the copying binary: a's coverage
    assert interpolate.calls == 2

