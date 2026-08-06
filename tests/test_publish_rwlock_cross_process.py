# SPDX-License-Identifier: Apache-2.0
"""Cross-process behaviour of the publish rwlock (`read_guard` / `publish_guard`).

The isolation tests in `test_curation_view_contract.py` drive the guards from threads of
one process. That covers the call sites, but it cannot distinguish a real cross-process
file lock from a same-process one, and it never asserts that the *shared* mode is actually
shared. Both matter for the backend choice:

- Cross-process is the whole point — the readers being excluded are a separate
  `serve_curation` process, not a thread.
- Shared-must-be-shared is what rules out `msvcrt.locking` on Windows, which has no shared
  mode: under it two readers would serialize and `read_guard` would silently become a
  mutex. `LockFileEx` (and `flock`) grant concurrent shared locks, so these tests fail
  loudly if the backend is ever swapped for an exclusive-only one.

Both tests are platform-agnostic — they assert the contract, not the implementation.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sprite_gen import runio

# Timing budget. The asserts only need "did B happen before or after A released", so the
# windows are generous relative to process spawn jitter rather than tight.
_SPAWN_TIMEOUT = 30.0
_HOLD_WINDOW = 0.6

_CHILD = """
import json, sys, time
from pathlib import Path
from sprite_gen import runio

run, mode, hold = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
guard = runio.read_guard if mode == "read" else runio.publish_guard
print(json.dumps({"phase": "start", "t": time.time()}), flush=True)
with guard(run):
    print(json.dumps({"phase": "acquired", "t": time.time()}), flush=True)
    time.sleep(hold)
print(json.dumps({"phase": "released", "t": time.time()}), flush=True)
"""


class _Child:
    """A subprocess holding one guard, with its JSON progress lines drained off-thread."""

    def __init__(self, run: Path, mode: str, hold: float) -> None:
        self.events: list[dict] = []
        self._proc = subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(run), mode, str(hold)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if line:
                self.events.append(json.loads(line))

    def at(self, phase: str) -> float | None:
        for event in self.events:
            if event["phase"] == phase:
                return event["t"]
        return None

    def wait_for(self, phase: str, timeout: float) -> float:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hit = self.at(phase)
            if hit is not None:
                return hit
            if self._proc.poll() is not None and self.at(phase) is None:
                self._thread.join(1)
                if self.at(phase) is None:
                    raise AssertionError(
                        f"child exited (rc={self._proc.returncode}) before reaching "
                        f"{phase!r}\nstderr:\n{self._proc.stderr.read()}"
                    )
            time.sleep(0.01)
        raise AssertionError(f"child never reached {phase!r} within {timeout}s")

    def finish(self) -> None:
        try:
            self._proc.wait(timeout=_SPAWN_TIMEOUT)
        finally:
            if self._proc.poll() is None:  # pragma: no cover - only on a hung child
                self._proc.kill()
            self._thread.join(2)


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    return run


def test_reader_in_another_process_blocks_until_publish_releases(run_dir: Path) -> None:
    """A separate process entering read_guard must not acquire while this process holds
    publish_guard — the exclusion is cross-process, not merely cross-thread."""
    child = _Child(run_dir, "read", hold=0.0)
    try:
        with runio.publish_guard(run_dir):
            child.wait_for("start", _SPAWN_TIMEOUT)  # interpreter up, about to request the lock
            time.sleep(_HOLD_WINDOW)
            assert child.at("acquired") is None, (
                "reader acquired the shared lock while a publish held the exclusive lock — "
                "no cross-process isolation"
            )
            released_at = time.time()
        acquired_at = child.wait_for("acquired", _SPAWN_TIMEOUT)
        assert acquired_at >= released_at, (
            f"reader acquired at {acquired_at}, before the publish released at {released_at}"
        )
    finally:
        child.finish()


def test_two_readers_hold_the_shared_lock_concurrently(run_dir: Path) -> None:
    """Shared must mean shared. While another process holds read_guard, this process must
    also acquire read_guard — promptly, not after the first reader lets go.

    This is the test an exclusive-only backend (`msvcrt.locking`) fails: it would turn
    reader isolation into reader serialization, which is a silent performance and
    correctness change rather than a loud failure.
    """
    hold = 3.0
    child = _Child(run_dir, "read", hold=hold)
    try:
        child.wait_for("acquired", _SPAWN_TIMEOUT)
        started = time.monotonic()
        with runio.read_guard(run_dir):
            waited = time.monotonic() - started
            assert child.at("released") is None, (
                "the first reader had already released — the overlap this test needs "
                "never happened, so shared-mode concurrency was not exercised"
            )
            assert waited < hold / 2, (
                f"second reader waited {waited:.2f}s for a *shared* lock the other process "
                f"already held — the backend is serializing readers"
            )
    finally:
        child.finish()
