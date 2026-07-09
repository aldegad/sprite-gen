# SPDX-License-Identifier: Apache-2.0
"""Import-surface contract checks for downstream package consumers."""

from __future__ import annotations

import importlib
import re
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PACKAGE_RUN_MODULES = [
    "compose_atlas",
    "compose_cycle",
    "compose_gif",
    "export_pngs",
    "extract",
    "generate_image",
    "prepare",
    "preview",
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


def test_installed_distribution_version_matches_skill_frontmatter() -> None:
    try:
        installed_version = metadata.version("sprite-gen")
    except metadata.PackageNotFoundError:
        pytest.skip("sprite-gen distribution metadata is only available after editable/package install")

    assert installed_version == _read_skill_version()
