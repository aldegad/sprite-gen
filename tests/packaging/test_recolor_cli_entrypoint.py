# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen recolor` / `recolor-palette` are the same program as the wrappers.

The console script is only safe if the CLI table points at the module's own
argument declaration and run function — a copied flag list would pass a smoke
test on the day it is written and drift on the next option.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from sprite_gen import cli
from sprite_gen.effects import recolor

ROOT = Path(__file__).resolve().parents[2]


def _help_options(*argv: str) -> set[str]:
    proc = subprocess.run(
        [sys.executable, *argv, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return set(re.findall(r"(?<![\w-])--[a-z][\w-]*", proc.stdout))


def test_recolor_subcommand_reuses_module_declaration() -> None:
    _description, add_args, run_fn = cli.COMMANDS["recolor"]
    assert add_args is recolor.add_recolor_arguments
    assert run_fn is recolor.run


def test_recolor_palette_subcommand_reuses_module_declaration() -> None:
    _description, add_args, run_fn = cli.COMMANDS["recolor-palette"]
    assert add_args is recolor.add_palette_arguments
    assert run_fn is recolor.run_palette


def test_recolor_launch_forms_expose_the_same_flags() -> None:
    subcommand = _help_options("-m", "sprite_gen.cli", "recolor")
    wrapper = _help_options(str(ROOT / "scripts" / "recolor.py"))

    expected = {
        "--base",
        "--run-dir",
        "--atlas",
        "--spec",
        "--out-dir",
        "--manifest",
        "--alpha-threshold",
        "--report",
        "--help",
    }
    assert subcommand == expected
    assert subcommand == wrapper


def test_recolor_palette_cli_exposes_authoring_flags() -> None:
    flags = _help_options("-m", "sprite_gen.cli", "recolor-palette")
    assert flags == {"--base", "--out", "--alpha-threshold", "--max-colors", "--help"}
