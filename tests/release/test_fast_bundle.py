# SPDX-License-Identifier: Apache-2.0
"""A plain `pytest` is the fast bundle: tests/conftest.py deselects the tests marked `slow`, and `pytest --full` runs
every test. The release lane measures a commit on every test, so each pytest call of `scripts/linux_lane.sh` carries
`--full` (docs/release.md)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROBE = '''
import pytest


@pytest.mark.slow
def test_marked_slow():
    pass


@pytest.mark.slow
@pytest.mark.parametrize("case", [1, 2])
def test_every_case_marked_slow(case):
    pass


@pytest.mark.parametrize("case", [1, pytest.param(2, marks=pytest.mark.slow)])
def test_one_case_marked_slow(case):
    pass


def test_unmarked():
    pass
'''


def _collected(tmp_path: Path, *args: str, empty: bool = False) -> tuple[list[str], str]:
    # This repository's configuration, and tests/conftest.py as the one conftest (-p), over a file outside the suite.
    # `empty`: a share may hold no test, which pytest ends with exit 5.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-c", str(ROOT / "pyproject.toml"), "--rootdir", str(tmp_path),
         "--noconftest", "-p", "conftest", "-p", "no:cacheprovider", "-p", "no:xdist", *args, str(tmp_path / "test_probe.py")],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
    )
    assert proc.returncode in ((0, 5) if empty else (0,)), f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    return re.findall(r"<Function (\S+)>", proc.stdout), proc.stdout


@pytest.mark.slow  # two runs of pytest
def test_a_plain_run_deselects_the_tests_marked_slow_and_full_runs_every_test(tmp_path):
    (tmp_path / "test_probe.py").write_text(PROBE)
    fast, said = _collected(tmp_path)
    assert fast == ["test_one_case_marked_slow[1]", "test_unmarked"], said
    assert "fast bundle: the tests marked slow are deselected; `pytest --full` runs every test" in said
    assert "(4 deselected)" in said
    every, said = _collected(tmp_path, "--full")
    assert every == ["test_marked_slow", "test_every_case_marked_slow[1]", "test_every_case_marked_slow[2]",
                     "test_one_case_marked_slow[1]", "test_one_case_marked_slow[2]", "test_unmarked"], said
    assert "fast bundle" not in said and "deselected" not in said


@pytest.mark.slow  # four runs of pytest
def test_the_shares_of_a_run_hold_every_test_once(tmp_path):
    """The lane's jobs each run one share (`--shard K/N`); together they run what one run would, each test once."""
    (tmp_path / "test_probe.py").write_text(PROBE + "".join(
        f"\n\n@pytest.mark.parametrize('case', range(7))\ndef test_many_{i}(case):\n    pass\n" for i in range(5)))
    every, said = _collected(tmp_path, "--full")
    assert len(every) == 41, said
    shares = [_collected(tmp_path, "--full", "--shard", f"{k}/3")[0] for k in (1, 2, 3)]
    assert sorted(sum(shares, [])) == sorted(every)
    assert all(shares), f"a share holds no test: {shares}"


@pytest.mark.slow  # four runs of pytest
def test_a_module_marked_one_share_runs_whole_in_one_share(tmp_path):
    """A module whose tests read what it makes once (`one_share`) is placed by its file, not test by test: one share
    holds all of it, so the lane makes that once a run."""
    (tmp_path / "test_probe.py").write_text("import pytest\n\npytestmark = pytest.mark.one_share\n" + "".join(
        f"\n\n@pytest.mark.parametrize('case', range(7))\ndef test_many_{i}(case):\n    pass\n" for i in range(5)))
    every, said = _collected(tmp_path, "--full")
    assert len(every) == 35, said
    shares = [_collected(tmp_path, "--full", "--shard", f"{k}/3", empty=True)[0] for k in (1, 2, 3)]
    assert sorted(len(s) for s in shares) == [0, 0, 35], shares


def test_a_share_is_named_k_of_n(tmp_path):
    (tmp_path / "test_probe.py").write_text(PROBE)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-c", str(ROOT / "pyproject.toml"), "--rootdir",
         str(tmp_path), "--noconftest", "-p", "conftest", "-p", "no:cacheprovider", "-p", "no:xdist", "--shard", "3/2",
         str(tmp_path / "test_probe.py")],
        cwd=ROOT, capture_output=True, encoding="utf-8")
    assert proc.returncode == 4 and "--shard takes K/N with 1 <= K <= N, not '3/2'" in proc.stderr, proc.stderr


def test_every_pytest_call_of_the_lane_runs_every_test():
    lane = (ROOT / "scripts" / "linux_lane.sh").read_text(encoding="utf-8")
    calls = [line for line in lane.splitlines() if "-m pytest" in line and not line.lstrip().startswith("#")]
    assert calls, "the lane runs no pytest"
    assert all(re.search(r"\s--full(\s|$)", line) for line in calls), calls
