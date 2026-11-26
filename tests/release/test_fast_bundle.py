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


def _collected(tmp_path: Path, *args: str) -> tuple[list[str], str]:
    # This repository's configuration, and tests/conftest.py as the one conftest (-p), over a file outside the suite.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-c", str(ROOT / "pyproject.toml"), "--rootdir", str(tmp_path),
         "--noconftest", "-p", "conftest", "-p", "no:cacheprovider", *args, str(tmp_path / "test_probe.py")],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    return re.findall(r"<Function (\S+)>", proc.stdout), proc.stdout


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


def test_the_shares_of_a_run_hold_every_test_once(tmp_path):
    """The lane's jobs each run one share (`--shard K/N`); together they run what one run would, each test once."""
    (tmp_path / "test_probe.py").write_text(PROBE + "".join(
        f"\n\n@pytest.mark.parametrize('case', range(7))\ndef test_many_{i}(case):\n    pass\n" for i in range(5)))
    every, said = _collected(tmp_path, "--full")
    assert len(every) == 41, said
    for n in (1, 3, 4):
        shares = [_collected(tmp_path, "--full", "--shard", f"{k}/{n}")[0] for k in range(1, n + 1)]
        assert sorted(sum(shares, [])) == sorted(every), n
        assert all(shares), f"a share of {n} holds no test: {shares}"
    fast = _collected(tmp_path)[0]
    assert sorted(sum((_collected(tmp_path, "--shard", f"{k}/2")[0] for k in (1, 2)), [])) == sorted(fast)


def test_a_share_is_named_k_of_n(tmp_path):
    (tmp_path / "test_probe.py").write_text(PROBE)
    for wrong in ("0/2", "3/2", "2", "a/b"):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-c", str(ROOT / "pyproject.toml"), "--rootdir",
             str(tmp_path), "--noconftest", "-p", "conftest", "-p", "no:cacheprovider", "--shard", wrong,
             str(tmp_path / "test_probe.py")],
            cwd=ROOT, capture_output=True, encoding="utf-8")
        assert proc.returncode == 4 and "--shard takes K/N with 1 <= K <= N" in proc.stderr, (wrong, proc.stderr)


def test_every_pytest_call_of_the_lane_runs_every_test():
    lane = (ROOT / "scripts" / "linux_lane.sh").read_text(encoding="utf-8")
    calls = [line for line in lane.splitlines() if "-m pytest" in line and not line.lstrip().startswith("#")]
    assert calls, "the lane runs no pytest"
    assert all(re.search(r"\s--full(\s|$)", line) for line in calls), calls
