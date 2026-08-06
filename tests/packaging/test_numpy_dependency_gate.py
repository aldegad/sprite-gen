# SPDX-License-Identifier: Apache-2.0
"""Missing-NumPy contract for the agent-facing entrypoints.

`tests/test_entrypoint_interpreter.py` locks the other half of the deployment
question — that documented commands name the venv interpreter. This file locks
what happens when someone runs one anyway on an interpreter that cannot import
NumPy: it fails, before it does any work, naming the interpreter it ran under
and the command that fixes it. A pure-Python fallback is not permitted
(principle 6, SECURITY.md "Current Dependency Surface"), so "kept running,
slower" is a failure of this contract rather than an outcome it tolerates.

The absence is simulated with a `sitecustomize.py` on `PYTHONPATH` that installs
a meta-path finder raising `ModuleNotFoundError` for `numpy`, so the real
documented command line runs on the real interpreter and only the one import
disappears. Every failing case is paired with the same command unblocked, so a
green run cannot come from an entrypoint that was already broken.

Two assertions carry this past the point where the vectorized code actually
lands. The runtime ones hold whatever `sprite_gen/frames/extract.py` does, because the
gate fires at package import, before any submodule runs. The static one keeps it
that way: a module that imports NumPy directly would fail with a bare
`No module named 'numpy'`, which names neither the interpreter nor the fix.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = Path("sprite_gen/_deps.py")

# Shipped code only. The test suite and the plan harnesses may import NumPy
# directly — they are not what an agent runs.
_SHIPPED_TREES = (ROOT / "sprite_gen", ROOT / "scripts")

# Documented pipeline entrypoints that must be in the scanned population below;
# an AST scan that quietly returned nothing would parametrize into zero cases.
_CORE_ENTRYPOINTS = {
    "extract_sprite_row_frames.py",
    "prepare_sprite_run.py",
    "compose_sprite_atlas.py",
    "serve_curation.py",
}

_BLOCKER = """\
import sys


class _NoNumpy:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "numpy" or fullname.startswith("numpy."):
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


sys.meta_path.insert(0, _NoNumpy())
"""


def _module_level(tree: ast.Module):
    """Statements that run on import — function and class bodies do not."""
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _imports_at_load(path: Path, package: str) -> bool:
    for node in _module_level(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            if any(a.name == package or a.name.startswith(f"{package}.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (module == package or module.startswith(f"{package}.")):
                return True
    return False


def _imports_numpy_anywhere(path: Path) -> bool:
    """Direct NumPy imports, including the dynamic spellings, at any nesting depth."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            if any(a.name == "numpy" or a.name.startswith("numpy.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and (module == "numpy" or module.startswith("numpy.")):
                return True
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in {"import_module", "__import__"} and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.split(".")[0] == "numpy":
                        return True
    return False


def _entry_scripts() -> list[Path]:
    """The `scripts/*.py` that load the package — i.e. the ones that reach the gate.

    Derived rather than listed: a script that stops importing `sprite_gen` also
    stops needing NumPy, and one that starts importing it joins this population
    without anyone remembering to add it here.
    """
    return sorted(p for p in (ROOT / "scripts").glob("*.py") if _imports_at_load(p, "sprite_gen"))


# The registers SKILL.md documents, plus a bare package import for downstream importers.
COMMANDS = {"package import": ["-c", "import sprite_gen"], "cli module": ["-m", "sprite_gen.cli", "--help"]}
COMMANDS.update({path.name: [str(path), "--help"] for path in _entry_scripts()})


def _run(args: list[str], *, numpy_blocked: bool, tmp_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if numpy_blocked:
        (tmp_path / "sitecustomize.py").write_text(_BLOCKER, encoding="utf-8")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(tmp_path), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)


def test_the_scanned_entrypoints_are_the_real_ones() -> None:
    """Anti-vacuity: the parametrization below is derived, so it must be checked."""
    found = {path.name for path in _entry_scripts()}

    assert _CORE_ENTRYPOINTS <= found, (
        f"documented entrypoints missing from the scan: {sorted(_CORE_ENTRYPOINTS - found)}")


@pytest.mark.parametrize("register", sorted(COMMANDS))
def test_entrypoint_without_numpy_fails_loudly_with_the_install_path(
    register: str, tmp_path: Path
) -> None:
    proc = _run(COMMANDS[register], numpy_blocked=True, tmp_path=tmp_path)

    assert proc.returncode != 0, (
        f"{register} kept running without NumPy — there is no pure-Python fallback for it to "
        f"keep running on:\n{proc.stdout}")
    assert "usage:" not in proc.stdout, (
        f"{register} produced entrypoint output before failing; the gate must fire at import, "
        f"not partway through a run:\n{proc.stdout}")
    stderr = proc.stderr
    assert "NumPy" in stderr, f"{register} failure never names NumPy:\n{stderr}"
    assert sys.executable in stderr, (
        f"{register} failure does not name the interpreter it ran under, which is the thing the "
        f"reader has to change:\n{stderr}")
    assert f"{ROOT}/.venv/bin/python -m pip install -e {ROOT}" in stderr, (
        f"{register} failure does not spell out the install command for the skill venv:\n{stderr}")


@pytest.mark.parametrize("register", sorted(COMMANDS))
def test_the_same_entrypoint_succeeds_when_numpy_is_importable(
    register: str, tmp_path: Path
) -> None:
    """Control: the failures above come from the blocked import, not a broken command."""
    proc = _run(COMMANDS[register], numpy_blocked=False, tmp_path=tmp_path)

    assert proc.returncode == 0, f"{register} fails even with NumPy present:\n{proc.stderr}"


def test_numpy_is_imported_in_exactly_one_shipped_module() -> None:
    """The gate cannot be bypassed. A direct `import numpy` elsewhere still fails loudly, but
    with a bare `No module named 'numpy'` that names neither interpreter nor install command."""
    importers = sorted(
        path.relative_to(ROOT)
        for tree in _SHIPPED_TREES
        for path in tree.rglob("*.py")
        if _imports_numpy_anywhere(path))

    assert importers == [GATE], (
        f"shipped code must reach NumPy through {GATE} (`from sprite_gen._deps import np`), not "
        f"by importing it directly; found: {[str(p) for p in importers]}")


def test_the_gate_hands_back_the_real_numpy() -> None:
    """No sentinel, no shim — `np` is NumPy itself, or the import raised."""
    from sprite_gen import _deps

    assert _deps.np is _deps.numpy
    assert getattr(_deps.np, "__name__", None) == "numpy"


def test_skill_md_documents_the_missing_numpy_behavior() -> None:
    """An agent that hits the traceback reads SKILL.md next; the rule lives there."""
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "NumPy 가 없는 인터프리터" in text, (
        "SKILL.md lost the clause telling agents what a NumPy-less interpreter does")
