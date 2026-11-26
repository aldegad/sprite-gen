# SPDX-License-Identifier: Apache-2.0
"""A test that pins the RIFE binary's calls (`as_pinned`, tests/conftest.py) passes where no binary is found, pinned to
none, as it does in the Linux lane, which has no RIFE. Its binary's calls are measured only where a binary runs: the GPU
lane (`scripts/cuda_lane.py`), which runs it by its mark, rife_pinned (GATE_TESTS). So every test that reaches
`as_pinned`, in its body or through a helper or fixture of its module, is marked rife_pinned, and every test so marked
reaches it: a test that pins the calls without the mark would have its binary's calls measured by neither lane."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cuda_lane  # noqa: E402

# Pins `as_pinned`'s own reading with the binary stood in (`rife_located` patched): it never runs one.
STOOD_IN = {"tests/release/test_rife_pins.py"}


def _marked(decorators: list[ast.expr]) -> bool:
    return any("rife_pinned" in ast.unparse(d) for d in decorators)


def _tests(path: Path) -> dict[str, tuple[bool, bool]]:
    """Each test function of the module at path: (whether it reaches `as_pinned`, whether it is marked rife_pinned)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_marked = any(isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "pytestmark" for t in node.targets)
                        and "rife_pinned" in ast.unparse(node.value) for node in tree.body)
    funcs = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    names = {name: {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
             | {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
             | {a.arg for a in fn.args.args}  # the fixtures it asks for
             for name, fn in funcs.items()}
    reach = {name for name, used in names.items() if "as_pinned" in used}
    grew = True
    while grew:
        grew = False
        for name, used in names.items():
            if name not in reach and used & reach:
                reach.add(name)
                grew = True
    return {name: (name in reach, module_marked or _marked(fn.decorator_list))
            for name, fn in funcs.items() if name.startswith("test")}


def _all() -> dict[str, tuple[bool, bool]]:
    found = {}
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel not in STOOD_IN:
            found.update({f"{rel}::{name}": seen for name, seen in _tests(path).items()})
    return found


def test_every_test_that_pins_rife_s_calls_is_marked_rife_pinned():
    tests = _all()
    unmarked = [nodeid for nodeid, (pins, marked) in tests.items() if pins and not marked]
    assert not unmarked, ("these reach as_pinned but are not marked rife_pinned, so neither lane measures their "
                          f"binary's calls: {unmarked}")
    assert sum(pins for pins, _ in tests.values()) >= 17


def test_each_test_marked_rife_pinned_pins_rife_s_calls():
    stray = [nodeid for nodeid, (pins, marked) in _all().items() if marked and not pins]
    assert not stray, f"marked rife_pinned but reaching no as_pinned: {stray}"


def test_the_gpu_lane_runs_the_tests_that_pin_rife_s_calls():
    assert cuda_lane.GATE_TESTS == "real_rife or torch_rife or rife_pinned"


def test_the_rule_reads_a_pin_through_a_helper_and_a_fixture_and_a_module_mark(tmp_path):
    module = tmp_path / "test_new.py"
    module.write_text(
        "import pytest\nfrom conftest import as_pinned\n\n\n"
        "def _cut():\n    assert as_pinned(3, 3)\n\n\n"
        "@pytest.fixture\ndef pinned_cut():\n    _cut()\n\n\n"
        "@pytest.mark.rife_pinned\ndef test_helper():\n    _cut()\n\n\n"
        "def test_fixture(pinned_cut):\n    pass\n\n\n"
        "def test_plain():\n    assert 'as_pinned' in 'a test of the words'\n", encoding="utf-8")
    assert _tests(module) == {"test_helper": (True, True), "test_fixture": (True, False), "test_plain": (False, False)}
    module.write_text("import pytest\n\npytestmark = [pytest.mark.slow, pytest.mark.rife_pinned]\n\n\n"
                      "def test_x():\n    assert as_pinned(0, 0)\n", encoding="utf-8")
    assert _tests(module) == {"test_x": (True, True)}
