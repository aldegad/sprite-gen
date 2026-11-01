# SPDX-License-Identifier: Apache-2.0
"""The curation SPA ships inside the package, from one location.

`sprite-gen curation` finds its assets relative to `sprite_gen/serve/serve_curation.py`, so an
asset that `pyproject.toml` does not declare as package data is simply absent from an
installed wheel — the server still boots and still serves `index.html`, and every
`<script src>` in it 404s. Nothing fails at build time, so what lands in the build is
checked here against what is actually on disk instead.

Checked against the build, not against the declaration string: reading the patterns and
asking "does this asset look like one of them" is how the first version of this guard passed
while the wheel was already dropping files (`fnmatch`'s `*` crosses `/`, setuptools' does
not). The tests below therefore read a real wheel built from this tree. setuptools is a
declared test dependency, so the wheel fixture skipping fails the lane rather than passing it.

The same reasoning covers the subprocess routes (`/api/compose`, `/api/interpolate`,
`/api/reroll`, `/api/export`, `/api/export-gif`): naming `scripts/<tool>.py` there would
work in a checkout and 500 in an install, because the `scripts/` wrappers are not
installed. Those routes go through `-m sprite_gen.<module>`, and the last test holds them
to it.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from sprite_gen.serve.serve_curation import CURATOR_DIR

ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE_DIR = ROOT / "sprite_gen"

# `scripts/<anything>.py` named as a path, not as prose.
SCRIPT_PATH = re.compile(r"(?:^|/)scripts/[^/]+\.py$")


def _curator_assets() -> list[Path]:
    return sorted(p for p in CURATOR_DIR.rglob("*") if p.is_file())


_BUILD_WHEEL = (
    "import sys\n"
    "from setuptools import build_meta\n"
    "sys.stdout.write(build_meta.build_wheel(sys.argv[1]))\n"
)


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory: pytest.TempPathFactory) -> set[str]:
    """Every path inside a wheel built from this tree — the packaging ground truth.

    Built from a copy so the build's own scratch (`build/`, `*.egg-info`) never lands in the
    checkout, and with the backend called directly (no build isolation) so the test needs no
    network.
    """
    if importlib.util.find_spec("setuptools") is None:
        pytest.skip("setuptools — the build backend pyproject.toml declares — is not importable "
                    "in this interpreter, so no wheel can be built here to inspect")
    project = tmp_path_factory.mktemp("project")
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, project / name)
    shutil.copytree(PACKAGE_DIR, project / PACKAGE_DIR.name,
                    ignore=shutil.ignore_patterns("__pycache__"))
    out = tmp_path_factory.mktemp("wheel")
    built_by = subprocess.run([sys.executable, "-c", _BUILD_WHEEL, str(out)],
                              cwd=project, capture_output=True, encoding="utf-8")
    assert built_by.returncode == 0, (
        f"the declared build backend could not build this project:\n{built_by.stderr}")
    built = list(out.glob("*.whl"))
    assert len(built) == 1, f"expected exactly one wheel, got {built}"
    with zipfile.ZipFile(built[0]) as zf:
        return set(zf.namelist())


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
    # The SPA lives under a domain subpackage (serve/curator), so it is nested
    # inside the package rather than a direct child — what matters is that it
    # travels with the package (is importable-package-relative), not its depth.
    assert CURATOR_DIR.is_relative_to(PACKAGE_DIR), (
        f"the SPA has to travel with the package; CURATOR_DIR points outside it: {CURATOR_DIR}")
    assert (CURATOR_DIR / "index.html").is_file()


def test_the_spa_has_exactly_one_home() -> None:
    """No `scripts/curator/` left behind to shadow it (No Silent Fallback)."""
    assert not (ROOT / "scripts" / "curator").exists(), (
        "two curator trees exist — whichever one the server does not read will rot silently")


def test_the_built_wheel_carries_every_curator_asset(wheel_names: set[str]) -> None:
    """The declaration is a claim; the wheel is the fact. Compare the fact to the disk."""
    on_disk = {f"sprite_gen/serve/curator/{p.relative_to(CURATOR_DIR).as_posix()}"
               for p in _curator_assets()}
    in_wheel = {n for n in wheel_names if n.startswith("sprite_gen/serve/curator/")}

    assert not sorted(on_disk - in_wheel), (
        f"the built wheel is missing {sorted(on_disk - in_wheel)} — the SPA loads them from "
        f"an installed sprite_gen, so an install serves a shell whose scripts 404")
    assert not sorted(in_wheel - on_disk), (
        f"the wheel carries curator files that are not in the tree: {sorted(in_wheel - on_disk)}")


def test_the_built_wheel_carries_every_package_module(wheel_names: set[str]) -> None:
    """Same fact-vs-claim check one level up: a subpackage may not go missing either.

    A hand-listed `packages = ["sprite_gen"]` once dropped all of `sprite_gen/gen/`, and
    `sprite-gen --help` (cli.py imports `gen` at module scope) died on every non-editable
    install while the whole suite stayed green — editable installs map the source tree, so
    they cannot see this class of defect at all.
    """
    on_disk = {p.relative_to(ROOT).as_posix() for p in PACKAGE_DIR.rglob("*.py")
               if "__pycache__" not in p.parts}
    missing = sorted(on_disk - wheel_names)

    assert not missing, (
        f"{len(missing)} module(s) are in the tree but not in the wheel: {missing[:5]} — "
        f"an install imports what shipped, not what the checkout has")


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


def test_the_package_never_reaches_into_the_scripts_wrappers() -> None:
    modules = sorted(PACKAGE_DIR.rglob("*.py"))
    assert modules, f"no package modules found under {PACKAGE_DIR} — the scan would pass vacuously"
    offenders: list[str] = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = [s for s in _string_constants(tree) if s == "scripts" or SCRIPT_PATH.search(s)]
        if hits:
            offenders.append(f"{path.relative_to(ROOT).as_posix()}: {hits}")

    assert not offenders, (
        "these modules build a path into scripts/ — those wrappers are not installed, so this "
        "works in a checkout and fails in an install. Shell out with `-m sprite_gen.<module>` "
        "instead:\n" + "\n".join(offenders))


def test_built_wheel_carries_the_face_player(wheel_names: set[str]) -> None:
    # `face-bake` runs the face pack's player with node from the installed package (`video/*.mjs` package data)
    assert 'sprite_gen/video/sprite-gen-face-v1.mjs' in wheel_names
