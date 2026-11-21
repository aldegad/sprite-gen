# SPDX-License-Identifier: Apache-2.0
"""Regression checks for support-version documentation drift."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _declared_min_python() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*">=(\d+\.\d+)"', pyproject, flags=re.MULTILINE)
    assert match, "pyproject.toml must declare requires-python as a >= major.minor floor"
    return match.group(1)


def _version_tuple(version: str) -> tuple[int, int]:
    major, minor = version.split(".")
    return int(major), int(minor)


def _ci_python_versions() -> list[str]:
    """The Python the gates run on: the Linux lane's pin (no CI runs on push or pull request)."""
    lane = (ROOT / "scripts" / "linux_lane.sh").read_text(encoding="utf-8")

    pinned = re.findall(r"^PINNED_PYTHON=(\d+\.\d+)$", lane, re.MULTILINE)
    assert pinned, "the lane must pin its Python on a PINNED_PYTHON=<major>.<minor> line"

    return pinned


def test_ci_versions_are_supported_and_documented() -> None:
    minimum = _declared_min_python()
    minimum_tuple = _version_tuple(minimum)
    versions = _ci_python_versions()
    assert all(_version_tuple(version) >= minimum_tuple for version in versions)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(versions) == 1, "CI intentionally tests a single Python version"
    assert f"CI runs only {versions[0]}" in readme
    # The README also names the declared floor and the venv requirement.
    assert f"CPython {minimum}+" in readme
    assert "`venv`/`ensurepip`" in readme


def test_the_test_timeout_leaves_its_method_to_the_platform(pytestconfig) -> None:
    """`timeout_method = "signal"` stopped pytest on Windows, which has no SIGALRM, before its first
    test. Left unset, pytest-timeout takes signal where SIGALRM exists and thread elsewhere."""
    assert pytestconfig.getini("timeout")
    assert not pytestconfig.getini("timeout_method")
