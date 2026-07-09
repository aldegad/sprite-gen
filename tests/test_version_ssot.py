# SPDX-License-Identifier: Apache-2.0
"""Release metadata version parity checks."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
    assert match, "pyproject.toml is missing [project] version"
    return match.group(1)


def _read_skill_version() -> str:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"(?m)^version:\s*([^\s#]+)\s*$", text)
    assert match, "SKILL.md frontmatter is missing version:"
    return match.group(1)


def test_pyproject_version_matches_skill_frontmatter_version() -> None:
    assert _read_pyproject_version() == _read_skill_version()
