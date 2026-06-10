# SPDX-License-Identifier: Apache-2.0
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURE_RUN = Path(__file__).resolve().parent / "fixtures" / "run"


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke a pipeline script exactly as the quickstart does."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def fixture_run_dir(tmp_path: Path) -> Path:
    """A throwaway copy of the golden fixture run dir."""
    run_dir = tmp_path / "run"
    shutil.copytree(FIXTURE_RUN, run_dir)
    return run_dir
