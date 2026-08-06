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


def test_ci_matrix_covers_declared_minimum_python() -> None:
    minimum = _declared_min_python()
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    matrix = re.search(r"python-version:\s*\[(?P<versions>[^\]]+)\]", workflow)
    assert matrix, "CI must declare the setup-python matrix on one line"
    versions = re.findall(r'"(\d+\.\d+)"', matrix.group("versions"))
    assert versions, "CI Python matrix must include at least one version"

    minimum_tuple = _version_tuple(minimum)
    assert minimum in versions
    assert min(_version_tuple(version) for version in versions) == minimum_tuple
    assert all(_version_tuple(version) >= minimum_tuple for version in versions)


def test_readme_names_declared_python_support_and_venv_requirement() -> None:
    minimum = _declared_min_python()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert f"CPython {minimum}+" in readme
    assert "`venv`/`ensurepip`" in readme
