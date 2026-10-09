# SPDX-License-Identifier: Apache-2.0
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Generator
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


# Tests that skip where no RIFE is reachable for a reason other than RIFE: an input the lane does not
# give. Named without their parameters. In the lane's RIFE-less run (`pytest --rife-unmeasured`) these
# and the tests marked real_rife may skip; any other skip fails (docs/release.md).
NOT_RIFE_SKIPS = {
    # SPRITE_GEN_CHROMA_REAL_STILLS names model stills, which the repository does not hold.
    "tests/frames/test_chroma_key_relative.py::test_real_still_keys_out_its_painted_border",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--rife-unmeasured",
        action="store_true",
        help="the RIFE-less run of scripts/linux_lane.sh: a skip fails unless its test is marked real_rife "
        "or named in NOT_RIFE_SKIPS (tests/conftest.py)",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A test marked `real_rife` runs the real binary: skipped where `rife.locate()` finds none, and run
    by `scripts/linux_lane.sh --rife-only` (`-m real_rife`)."""
    marked = [item for item in items if item.get_closest_marker("real_rife")]
    if not marked:
        return
    from sprite_gen.video import rife

    try:
        rife.locate()
    except rife.RifeUnavailable:
        for item in marked:
            item.add_marker(pytest.mark.skip(reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH / sprite-gen rife install)"))


def _fail_unmeasured_skip(report: pytest.TestReport | pytest.CollectReport, node: pytest.Item | pytest.Collector) -> None:
    """In the lane's RIFE-less run a skip that `--rife-only` will not run fails, by name: neither run would
    measure it. `--rife-only` runs the tests marked real_rife; a skip of any other test, or of a whole
    file, stands only where NOT_RIFE_SKIPS names it. Whatever skipped it: a skip mark, a fixture, the
    test body, the file at import."""
    if not report.skipped or hasattr(report, "wasxfail") or not node.config.getoption("rife_unmeasured", False):
        return
    if node.get_closest_marker("real_rife") or report.nodeid.partition("[")[0] in NOT_RIFE_SKIPS:
        return
    path, line, reason = report.longrepr
    report.outcome = "failed"
    report.longrepr = (
        "skipped where no RIFE is reachable, but not marked real_rife: `scripts/linux_lane.sh --rife-only` "
        f"runs only the marked tests, so neither run measures it\n{path}:{line}: {reason}\n"
        "A test that needs the real rife-ncnn-vulkan is marked `real_rife`; one that skips for another "
        "reason is named in NOT_RIFE_SKIPS (tests/conftest.py)."
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    _fail_unmeasured_skip(report, item)
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    report = yield
    _fail_unmeasured_skip(report, collector)
    return report


@pytest.fixture
def fixture_run_dir(tmp_path: Path) -> Path:
    """A throwaway copy of the golden fixture run dir."""
    run_dir = tmp_path / "run"
    shutil.copytree(FIXTURE_RUN, run_dir)
    return run_dir
