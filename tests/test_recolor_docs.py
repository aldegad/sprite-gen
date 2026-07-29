# SPDX-License-Identifier: Apache-2.0
"""Doc surface lock for the recolor feature.

An external user who only reads the docs must be able to bake colourways. The
implementation is already covered by `test_recolor_bake` / `test_recolor_curation_view`;
this file only asserts that the hub and the leaf teach the same commands, that the
curation sidecar field is named, and that the changelog records the feature.
Internal project names and absolute personal paths must not appear in the new leaf
(open-source vocabulary rule for this repo).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A denylist is itself published. Anything that would only be secret until this
# file is read (an operator's login name, a private address, an unpublished
# project id) is expressed as a *class* below, never as a literal — otherwise the
# guard leaks exactly what it guards.
#
# Literals here are limited to names this repository already prints in its own
# tracked tree (CHANGELOG / docs / other tests), so naming them again costs
# nothing new.
FORBIDDEN_LITERALS_IN_RECOLOR_LEAF = (
    "solvell",
    "kuma-studio",
    "discord",
)

# Classes of operator context that must never reach a published doc.
FORBIDDEN_PATTERNS_IN_RECOLOR_LEAF = (
    # An absolute path inside somebody's home directory — leaks a login name and
    # is unusable for every reader who is not that person.
    (re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+"), "an absolute personal home path"),
    # A contact address of any kind.
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "an e-mail address"),
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_recolor_leaf_doc_exists_and_teaches_both_commands() -> None:
    text = _read("docs/recolor.md")
    assert "sprite-gen recolor" in text
    assert "sprite-gen recolor-palette" in text
    assert "sprite-gen-recolor" in text  # spec kind
    assert "sprite-gen-recolor-report" in text  # report kind
    assert "variants/" in text
    assert "recolor.picked" in text
    assert "curation.json" in text
    # Exact is the default; tolerance is opt-in — both must be named.
    assert "exact" in text
    assert "tolerance" in text
    assert "No Silent Fallback" in text or "passthrough" in text


def test_skill_hub_routes_recolor_triggers_and_points_at_leaf() -> None:
    text = _read("SKILL.md")
    # Frontmatter description carries the trigger vocabulary for skill routers.
    assert "palette swap" in text or "팔레트 스왑" in text
    assert "recolor" in text
    assert "docs/recolor.md" in text
    assert "sprite-gen recolor" in text
    assert "recolor-palette" in text
    # Workflow step after compose.
    assert "4.5" in text
    # Docs topology branch.
    assert "COLOURWAYS" in text or "docs/recolor.md" in text
    # Wrapper listed among required scripts.
    assert "scripts/recolor.py" in text


def test_curation_and_run_contract_name_the_sidecar_and_folder() -> None:
    curation = _read("docs/curation.md")
    assert "recolor" in curation
    assert "picked" in curation
    assert "recolor.md" in curation

    run_contract = _read("docs/run-contract.md")
    assert "variants/" in run_contract
    assert "recolor" in run_contract


def test_readme_and_changelog_surface_the_feature() -> None:
    readme = _read("README.md")
    assert "recolor" in readme
    assert "docs/recolor.md" in readme

    changelog = _read("CHANGELOG.md")
    # The Unreleased recolor section must exist before a version pin is cut.
    assert "palette-swap" in changelog or "palette swap" in changelog.lower() or "recolor" in changelog
    assert "recolor-palette" in changelog
    assert "recolor.picked" in changelog or "colourway" in changelog.lower() or "colorway" in changelog.lower()


# Every file this feature adds to the published tree. The leak found in review was
# in a *test* file, not in the leaf — so the vocabulary lock covers all of them.
RECOLOR_SURFACE = (
    "docs/recolor.md",
    "scripts/recolor.py",
    "sprite_gen/recolor.py",
    "sprite_gen/curator/src/recolor.js",
    "tests/test_recolor_bake.py",
    "tests/test_recolor_cli_entrypoint.py",
    "tests/test_recolor_curation_view.py",
    "tests/test_recolor_docs.py",
)


def test_recolor_leaf_stays_open_source_vocabulary() -> None:
    lowered = _read("docs/recolor.md").lower()
    for token in FORBIDDEN_LITERALS_IN_RECOLOR_LEAF:
        assert token.lower() not in lowered, f"docs/recolor.md must not mention {token!r}"


def test_recolor_surface_carries_no_operator_context() -> None:
    for rel in RECOLOR_SURFACE:
        raw = _read(rel)
        for pattern, what in FORBIDDEN_PATTERNS_IN_RECOLOR_LEAF:
            hit = pattern.search(raw)
            assert hit is None, f"{rel} must not carry {what} (found at offset {hit.start()})"
