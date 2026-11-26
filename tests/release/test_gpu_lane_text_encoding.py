# SPDX-License-Identifier: Apache-2.0
"""The tests the GPU lane runs read and write text in an encoding they name, as the package does.

The GPU lane (`scripts/cuda_lane.py`) runs the tests marked with its marks (`cuda_lane.GATE_TESTS`) on Windows, where a
text read or write that names no encoding takes the code page (cp949 on a Korean one), not the UTF-8 the package writes:
a test reading a report the package wrote stops at its first character the code page cannot decode. No other lane runs
them on Windows, so the Linux lane, which runs this, finds such a call before the GPU lane does. The modules: each one
holding one of those marks, each module of tests/ those import, on and on, and tests/conftest.py. The scan is the
package's (`tests/packaging/test_text_encoding.py`, `locale_calls`), read the same way."""
from __future__ import annotations

import ast
import functools
import sys
from pathlib import Path

import pytest

from tests.packaging.test_text_encoding import locale_calls

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import cuda_lane  # noqa: E402

GPU_LANE_MARKS = frozenset(cuda_lane.GATE_TESTS.split(" or "))

# The calls allowed to read in the locale's encoding: the module, the call as written, and why.
LOCALE_READS = {
    ("tests/video/test_rife_loader_layers.py", "real_run(*args, **kwargs)"):
        "stands in for the package's own run of the binary (tests/packaging/test_text_encoding.py, RIFE_CALL), handing "
        "on the arguments the package gives it: the encoding is the package's, read as the package reads it",
}


def _marked(tree: ast.AST) -> bool:
    """Whether the module names one of the GPU lane's marks as a mark (`pytest.mark.real_rife`, `marks=mark.real_rife`)."""
    return any(isinstance(node, ast.Attribute) and node.attr in GPU_LANE_MARKS
               and isinstance(node.value, ast.Attribute) and node.value.attr == "mark" for node in ast.walk(tree))


def _imported(tree: ast.AST) -> set[Path]:
    """The modules of tests/ the module imports: `from conftest import`, `from tests.x.y import`, `from tests.x import
    test_y`, `import tests.x.y`."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "conftest":
                found.add(ROOT / "tests" / "conftest.py")
            elif node.module.split(".")[0] == "tests":
                base = ROOT.joinpath(*node.module.split("."))
                found |= ({base.with_suffix(".py")} if base.with_suffix(".py").is_file()
                          else {base / f"{alias.name}.py" for alias in node.names})
        elif isinstance(node, ast.Import):
            found |= {ROOT.joinpath(*alias.name.split(".")).with_suffix(".py") for alias in node.names
                      if alias.name.split(".")[0] == "tests"}
    return {path for path in found if path.is_file()}


def _found() -> list[tuple[str, int, str, str]]:
    return [(module, line, call, why) for module in gpu_lane_modules()
            for line, call, why in locale_calls((ROOT / module).read_text(encoding="utf-8"))]


@functools.cache
def gpu_lane_modules() -> tuple[str, ...]:
    """The modules whose code the GPU lane's tests run, as paths from the root."""
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "tests").rglob("*.py"))}
    todo = [path for path, tree in trees.items() if _marked(tree)] + [ROOT / "tests" / "conftest.py"]
    seen: set[Path] = set()
    while todo:
        path = todo.pop()
        if path not in seen and path in trees:
            seen.add(path)
            todo.extend(_imported(trees[path]))
    return tuple(sorted(path.relative_to(ROOT).as_posix() for path in seen))


@pytest.mark.slow
def test_the_tests_the_gpu_lane_runs_name_the_encoding_of_every_text_they_read_or_write():
    found = _found()
    unexplained = [f"{module}:{line}: {call} — {why}" for module, line, call, why in found
                   if (module, call) not in LOCALE_READS]
    assert not unexplained, "text the GPU lane's tests read or write in the locale's encoding:\n" + "\n".join(unexplained)
    assert [key for key in LOCALE_READS if key not in {(module, call) for module, _, call, _ in found}] == []


def test_the_modules_are_those_marked_what_they_import_and_the_conftest():
    modules = gpu_lane_modules()
    assert {"tests/conftest.py", "tests/video/test_walk_steps.py", "tests/video/test_rife_cuts.py",
            "tests/video/test_rife_torch.py", "tests/video/test_video_pipeline.py"} <= set(modules)
    assert GPU_LANE_MARKS == {"real_rife", "torch_rife", "rife_pinned"}
    assert "tests/release/test_gpu_lane_text_encoding.py" not in modules


def test_a_module_is_found_by_its_mark_and_followed_into_what_it_imports(tmp_path):
    tree = ast.parse("import pytest\nfrom conftest import as_pinned\nfrom tests.video import test_rife as tr\n"
                     "from tests.video.test_loop_repair import _no_rife\n\n\n"
                     "@pytest.mark.rife_pinned\ndef test_x():\n    pass\n")
    assert _marked(tree)
    assert _imported(tree) == {ROOT / "tests" / "conftest.py", ROOT / "tests" / "video" / "test_rife.py",
                               ROOT / "tests" / "video" / "test_loop_repair.py"}
    assert not _marked(ast.parse("def test_x():\n    assert 'pytest.mark.real_rife' in 'words'\n"))
    assert _marked(ast.parse("import pytest\nB = [pytest.param('ncnn', marks=pytest.mark.real_rife)]\n"))
