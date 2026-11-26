# SPDX-License-Identifier: Apache-2.0
"""The places that start work side by side size themselves by the CPUs this process may use (`cpus.usable`), so a
caller that already runs several such processes side by side gives each its share; and a face pack's readers start
no more processes or threads than there are frames to read."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from sprite_gen.util import cpus
from sprite_gen.video import face_pack, rife

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.version_info < (3, 13), reason="PYTHON_CPU_COUNT is Python 3.13's")
def test_a_process_given_one_cpu_by_its_caller_uses_one():
    """PYTHON_CPU_COUNT=1, as a caller running a process for each of its CPUs sets it: the process counts one CPU, and
    starts the RIFE binary's runs one at a time."""
    probe = "from sprite_gen.util import cpus; from sprite_gen.video import rife; print(cpus.usable(), rife.runs_at_once())"
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env={**os.environ, "PYTHON_CPU_COUNT": "1"},
                          capture_output=True, text=True, encoding="utf-8", check=True)
    assert done.stdout.split() == ["1", "1"]


def test_the_count_is_at_least_one():
    assert cpus.usable() >= 1
    assert 1 <= rife.runs_at_once() <= rife.RUNS_AT_ONCE


class _Recording:
    """A pool stand-in that records how many workers it was asked for and runs the work here, in order."""
    asked: list[int] = []

    def __init__(self, max_workers, initializer=None, initargs=()):
        type(self).asked.append(max_workers)
        if initializer is not None:
            initializer(*initargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, items, chunksize=1):
        return [fn(x) for x in items]


def _double(x: int) -> int:
    return 2 * x


def test_a_pack_s_readers_start_no_more_workers_than_frames(monkeypatch):
    """Eight CPUs, three frames: three processes and three threads; one frame is read here, with no pool."""
    monkeypatch.setattr(face_pack, "ProcessPoolExecutor", _Recording)
    monkeypatch.setattr(face_pack, "ThreadPoolExecutor", _Recording)
    _Recording.asked = []
    assert face_pack._pool_map(_double, [1, 2, 3], {}, 8) == [2, 4, 6]
    assert face_pack.each(_double, [1, 2, 3], 8) == [2, 4, 6]
    assert _Recording.asked == [3, 3]
    assert face_pack._pool_map(_double, [5], {}, 8) == [10]
    assert face_pack.each(_double, [5], 8) == [10]
    assert _Recording.asked == [3, 3]
