# SPDX-License-Identifier: Apache-2.0
"""Import-surface contract checks for downstream package consumers."""

from __future__ import annotations

import ast
import importlib
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "sprite_gen"

PACKAGE_RUN_MODULES = [
    "anchor",
    "compose_atlas",
    "compose_cycle",
    "compose_gif",
    "compose_layers",
    "correction_loop",
    "export_pngs",
    "extract",
    "gen",
    "generate_image",
    "inspect",
    "prepare",
    "preview",
    "score",
    "serve_curation",
    "slice_sheet",
    "unpack_atlas",
]

CLI_HELPERS = [
    "_parse_frame_order",
    "_parse_frames",
    "_parse_grid",
]


def _read_skill_version() -> str:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"(?m)^version:\s*([^\s#]+)\s*$", text)
    assert match, "SKILL.md frontmatter is missing version:"
    return match.group(1)


@pytest.mark.parametrize("module_name", PACKAGE_RUN_MODULES)
def test_mcp_import_surface_modules_expose_callable_run(module_name: str) -> None:
    module = importlib.import_module(f"sprite_gen.{module_name}")

    assert callable(getattr(module, "run", None)), f"sprite_gen.{module_name}.run must be callable"


@pytest.mark.parametrize("helper_name", CLI_HELPERS)
def test_cli_parser_helpers_exist(helper_name: str) -> None:
    cli = importlib.import_module("sprite_gen.cli")

    assert callable(getattr(cli, helper_name, None)), f"sprite_gen.cli.{helper_name} must be callable"


def _sibling_module_names() -> set[str]:
    modules = {path.stem for path in PACKAGE_DIR.glob("*.py")}
    modules |= {path.name for path in PACKAGE_DIR.iterdir() if (path / "__init__.py").is_file()}
    return modules - {"__init__"}


def _bare_sibling_imports(path: Path, siblings: set[str]) -> list[tuple[int, str]]:
    """Imports of a sibling module by bare name, at any nesting depth.

    Names that also exist in the stdlib (`inspect`) are left alone: inside the package an
    absolute `import inspect` reaches the stdlib, which is what the caller means.
    """
    hits = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            if root in siblings and root not in sys.stdlib_module_names:
                hits.append((node.lineno, name))
    return hits


def test_package_modules_import_siblings_by_package_path() -> None:
    """`from sprite_gen.curate.curation import …`, never `from curation import …`.

    A bare sibling import only resolves when `scripts/` happens to be on `sys.path`, so it
    survives `python scripts/<tool>.py` and dies under `python -m sprite_gen.<tool>` or any
    downstream importer. The lazy ones die at request time, not import time — the curation
    webview served its SPA and 500'd on `/api/run` with `No module named 'extract'`.
    """
    siblings = _sibling_module_names()
    offenders = {
        str(path.relative_to(ROOT)): _bare_sibling_imports(path, siblings)
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        if _bare_sibling_imports(path, siblings)
    }

    assert not offenders, f"bare sibling imports inside the package: {offenders}"


def test_scripts_wrappers_reexport_their_package_module() -> None:
    """The `scripts/*.py` call path documented in the READMEs keeps working after a module
    moves into the package: the wrapper re-exports the implementation, entrypoints included."""
    probe = (
        "import runpy, sys;"
        f"m = runpy.run_path({str(ROOT / 'scripts' / 'serve_curation.py')!r});"
        "sys.exit(0 if callable(m.get('main')) and callable(m.get('build_run_state')) else 1)"
    )
    proc = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True)

    assert proc.returncode == 0, f"scripts/serve_curation.py stopped re-exporting the impl:\n{proc.stderr}"

    helped = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "serve_curation.py"), "--help"],
        cwd=ROOT, capture_output=True, text=True)

    assert helped.returncode == 0, helped.stderr
    assert "--run-dir" in helped.stdout, (
        f"the wrapper no longer runs the entrypoint — `python scripts/serve_curation.py` is a "
        f"documented launch command:\n{helped.stdout}")


def test_installed_distribution_version_matches_skill_frontmatter() -> None:
    try:
        installed_version = metadata.version("sprite-gen")
    except metadata.PackageNotFoundError:
        pytest.skip("sprite-gen distribution metadata is only available after editable/package install")

    assert installed_version == _read_skill_version()
