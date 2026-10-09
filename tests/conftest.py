# SPDX-License-Identifier: Apache-2.0
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURE_RUN = Path(__file__).resolve().parent / "fixtures" / "run"


def help_options(*argv: str) -> set[str]:
    """Return flags advertised by ``<argv> --help`` without ANSI styling.

    Python 3.14 colorizes argparse output when the surrounding environment asks
    for color. These tests compare the declared option surface, so they pin the
    interpreter-specific override rather than depending on the invoking shell.
    """
    env = {**os.environ, "PYTHON_COLORS": "0"}
    proc = subprocess.run(
        [sys.executable, *argv, "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return set(re.findall(r"(?<![\w-])--[a-z][\w-]*", proc.stdout))


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke a pipeline script exactly as the quickstart does."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), *args],
        capture_output=True,
        text=True,
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A test marked `real_rife` runs the real binary: skipped where `rife.locate()` finds none. The
    mark is the only RIFE guard, so the tests it skips are the tests `scripts/linux_lane.sh
    --rife-only` runs (`-m real_rife`); tests/release/test_lane_rife_collection.py holds the suite to it."""
    marked = [item for item in items if item.get_closest_marker("real_rife")]
    if not marked:
        return
    from sprite_gen.video import rife

    try:
        rife.locate()
    except rife.RifeUnavailable:
        for item in marked:
            item.add_marker(pytest.mark.skip(reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH / sprite-gen rife install)"))


@pytest.fixture
def fixture_run_dir(tmp_path: Path) -> Path:
    """A throwaway copy of the golden fixture run dir."""
    run_dir = tmp_path / "run"
    shutil.copytree(FIXTURE_RUN, run_dir)
    return run_dir
