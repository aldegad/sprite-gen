# SPDX-License-Identifier: Apache-2.0
"""The curation SPA ships inside the package, from one location.

`sprite-gen curation` finds its assets relative to `sprite_gen/serve_curation.py`, so an
asset that `pyproject.toml` does not declare as package data is simply absent from an
installed wheel — the server still boots and still serves `index.html`, and every
`<script src>` in it 404s. Nothing fails at build time, so the declaration is checked here
against what is actually on disk instead.

The same reasoning covers the subprocess routes (`/api/compose`, `/api/interpolate`,
`/api/reroll`, `/api/export`, `/api/export-gif`): naming `scripts/<tool>.py` there would
work in a checkout and 500 in an install, because the `scripts/` wrappers are not
installed. Those routes go through `-m sprite_gen.<module>`, and the last test holds them
to it.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

import pytest

from sprite_gen.serve_curation import CURATOR_DIR

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "sprite_gen"
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

# `scripts/<anything>.py` named as a path, not as prose.
SCRIPT_PATH = re.compile(r"(?:^|/)scripts/[^/]+\.py$")


def _declared_package_data() -> list[str]:
    """The `sprite_gen = [...]` patterns under `[tool.setuptools.package-data]`.

    Read with a regex for the same reason `test_version_ssot.py` does: `tomllib` is 3.11+
    and this suite runs on the 3.10 floor `requires-python` promises.
    """
    section = re.search(
        r"(?ms)^\[tool\.setuptools\.package-data\]\s*$(.*?)(?=^\[|\Z)", PYPROJECT)
    assert section, "pyproject.toml is missing [tool.setuptools.package-data]"
    entry = re.search(r"(?ms)^sprite_gen\s*=\s*\[(.*?)\]", section.group(1))
    assert entry, "[tool.setuptools.package-data] declares nothing for sprite_gen"
    return re.findall(r'"([^"]+)"', entry.group(1))


def _curator_assets() -> list[Path]:
    return sorted(p for p in CURATOR_DIR.rglob("*") if p.is_file())


def _string_constants(tree: ast.AST) -> list[str]:
    """Every string literal except docstrings.

    Docstrings are prose about the repo (`python3 scripts/interpolate_frames.py …` is a
    documented call form) — the contract is about paths the code actually builds.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


def test_curator_dir_lives_inside_the_installed_package() -> None:
    assert CURATOR_DIR.parent == PACKAGE_DIR, (
        f"the SPA has to travel with the package; CURATOR_DIR points outside it: {CURATOR_DIR}")
    assert (CURATOR_DIR / "index.html").is_file()


def test_the_spa_has_exactly_one_home() -> None:
    """No `scripts/curator/` left behind to shadow it (No Silent Fallback)."""
    assert not (ROOT / "scripts" / "curator").exists(), (
        "two curator trees exist — whichever one the server does not read will rot silently")


@pytest.mark.parametrize("asset", [p.relative_to(CURATOR_DIR).as_posix() for p in _curator_assets()])
def test_every_curator_asset_is_declared_as_package_data(asset: str) -> None:
    patterns = _declared_package_data()
    rel = f"curator/{asset}"
    assert any(fnmatch.fnmatch(rel, pat) for pat in patterns), (
        f"{rel} matches no package-data pattern {patterns} — it would be missing from the "
        f"wheel and 404 for anyone who installed sprite-gen instead of cloning it")


def test_the_spa_asks_only_for_assets_that_exist() -> None:
    """Every `src=`/`href=` in index.html is a real file under the SPA root.

    The load order in index.html is the SSoT for the split `src/*.js` modules, so a
    renamed or dropped file shows up as a blank page, not as an error.
    """
    html = (CURATOR_DIR / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="(/[^"]+)"', html)
    assert refs, "index.html loads no assets — the SPA cannot be blank by accident"
    missing = [r for r in refs if not (CURATOR_DIR / r.lstrip("/")).is_file()]

    assert not missing, f"index.html references assets that are not in the SPA tree: {missing}"


@pytest.mark.parametrize("module", sorted(p.relative_to(ROOT).as_posix()
                                          for p in PACKAGE_DIR.rglob("*.py")))
def test_the_package_never_reaches_into_the_scripts_wrappers(module: str) -> None:
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    offenders = [s for s in _string_constants(tree) if s == "scripts" or SCRIPT_PATH.search(s)]

    assert not offenders, (
        f"{module} builds a path into scripts/ ({offenders}) — those wrappers are not "
        f"installed, so this works in a checkout and fails in an install. Shell out with "
        f"`-m sprite_gen.<module>` instead.")
