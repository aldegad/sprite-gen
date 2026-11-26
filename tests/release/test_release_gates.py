# SPDX-License-Identifier: Apache-2.0
"""The one table of what a change reaches (`scripts/release_gates.py`, docs/release.md): RIFE's tests run in the GPU lane
only where a change reaches them, so the table must name every way in. Each module of the package that runs RIFE or asks
it for frames (it imports rife, rife_torch or rife_install) is named, and each module of the package the three import
at their top; a test file reaches it by its marks, read on both
sides of the change, so a test newly marked and a mark taken off both count; a change elsewhere does not reach it. The
record a release page ends with is tests/release/test_release_publish.py's."""

from __future__ import annotations

import ast
import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import release_gates  # noqa: E402

RIFE_MODULES = {"rife", "rife_torch", "rife_install"}


def _imports_rife(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import) and any(a.name.split(".")[-1] in RIFE_MODULES and "video" in a.name for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[-1] in RIFE_MODULES:
                return True
            if (module.endswith("video") or (node.level and module == "")) and {a.name for a in node.names} & RIFE_MODULES:
                return True
    return False


def test_every_module_that_runs_rife_is_in_the_table():
    callers = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "sprite_gen").rglob("*.py") if _imports_rife(p))
    assert "sprite_gen/video/loop.py" in callers and "sprite_gen/cli.py" in callers, callers  # the reading reads
    missing = [p for p in callers if not release_gates.reaches(p, None)]
    assert not missing, f"these modules run RIFE or ask it for frames, and a change to them does not reach the GPU lane: {missing}"
    for name in ("rife.py", "rife_torch.py", "rife_install.py"):
        assert release_gates.reaches(f"sprite_gen/video/{name}", None)


def _top_imports(path: Path) -> set[str]:
    """The package's modules a file imports at its top, as paths from the root."""
    found = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "sprite_gen":
            base = node.module.replace(".", "/")
            for alias in node.names:
                as_module = f"{base}/{alias.name}.py"
                found.add(as_module if (ROOT / as_module).is_file() else f"{base}.py")
        elif isinstance(node, ast.Import):
            found.update(a.name.replace(".", "/") + ".py" for a in node.names if a.name.split(".")[0] == "sprite_gen")
    return found


def test_every_module_rife_imports_at_its_top_is_in_the_table():
    imported = set().union(*(_top_imports(ROOT / "sprite_gen" / "video" / f"{m}.py") for m in sorted(RIFE_MODULES)))
    assert "sprite_gen/video/frames.py" in imported, imported  # the reading reads
    missing = sorted(p for p in imported if (ROOT / p).is_file() and not release_gates.reaches(p, None))
    assert not missing, f"RIFE imports these and a change to them does not reach the GPU lane: {missing}"


def test_each_pattern_of_the_table_names_a_file_that_is_there():
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, encoding="utf-8", check=True).stdout.split()
    for pattern in release_gates.GPU_LANE:
        assert any(fnmatch.fnmatchcase(f, pattern) for f in files), pattern


def test_a_test_file_reaches_it_by_its_marks():
    marked = "import pytest\n\n\n@pytest.mark.real_rife\ndef test_x():\n    pass\n"
    torched = "import pytest\n\npytestmark = pytest.mark.torch_rife\n"
    plain = "def test_x():\n    assert 'real_rife' in 'a test of the words'\n"
    assert release_gates.reaches("tests/video/test_new.py", marked)
    assert release_gates.reaches("tests/video/test_new.py", torched)
    assert not release_gates.reaches("tests/video/test_new.py", plain)
    assert not release_gates.reaches("tests/video/test_new.py", None)
    for elsewhere in ("README.md", "docs/release.md", "sprite_gen/video/sway.py", "tests/video/test_sway_region.py"):
        assert not release_gates.reaches(elsewhere, "text"), elsewhere


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
         "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd, check=True, capture_output=True, encoding="utf-8").stdout.strip()


def _commit(repo: Path, files: dict[str, str | None]) -> str:
    for name, text in files.items():
        path = repo / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "a change")
    return _git(repo, "rev-parse", "HEAD")


def test_the_files_a_change_reaches_are_read_on_both_sides(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    marked = "import pytest\n\n\n@pytest.mark.real_rife\ndef test_x():\n    pass\n"
    first = _commit(repo, {"README.md": "a tree\n", "tests/video/test_a.py": marked, "sprite_gen/video/sway.py": "A = 1\n"})
    elsewhere = _commit(repo, {"README.md": "a tree, again\n", "sprite_gen/video/sway.py": "A = 2\n"})
    assert release_gates.reached(repo, first, elsewhere) == []
    unmarked = _commit(repo, {"tests/video/test_a.py": "def test_x():\n    pass\n"})
    assert release_gates.reached(repo, elsewhere, unmarked) == ["tests/video/test_a.py"]  # the mark taken off
    cut = _commit(repo, {"sprite_gen/video/loop.py": "CUT = 1\n", "tests/video/test_a.py": None})
    assert release_gates.reached(repo, unmarked, cut) == ["sprite_gen/video/loop.py"]
    assert release_gates.reached(repo, first, cut) == ["sprite_gen/video/loop.py", "tests/video/test_a.py"]
