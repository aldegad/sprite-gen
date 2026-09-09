# SPDX-License-Identifier: Apache-2.0
"""The CLI's grouped help is derived from `sprite_gen._modules` — one taxonomy table.

Adding a verb whose module is not in MODULE_DOMAIN must fail loudly here, not land in an
unnamed bucket; every verb must appear exactly once in `sprite-gen --help`, under its
domain heading, and every domain heading used must be a declared domain.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from sprite_gen import _modules, cli


def test_every_verb_has_exactly_one_domain() -> None:
    groups = cli.command_domains()
    seen: list[str] = [v for verbs in groups.values() for v in verbs]
    assert sorted(seen) == sorted(cli.COMMANDS), "every verb is grouped once"
    assert set(groups) <= set(_modules.DOMAIN_ORDER), "groups are declared domains"
    assert list(groups) == [d for d in _modules.DOMAIN_ORDER if d in groups], "groups follow the declared order"


def test_domain_of_refuses_unknown_modules() -> None:
    assert _modules.domain_of("sprite_gen.video.loop") == "video"
    assert _modules.domain_of("sprite_gen.gen") == "gen"  # the domain package is its own domain
    with pytest.raises(KeyError, match="MODULE_DOMAIN"):
        _modules.domain_of("sprite_gen.nowhere.mystery")


def test_help_lists_each_verb_once_under_its_domain() -> None:
    proc = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for domain, verbs in cli.command_domains().items():
        assert f"[{domain}]" in out
        for verb in verbs:
            assert len(re.findall(rf"(?m)^\s+{re.escape(verb)}\s{{2,}}", out)) == 1, verb
    for pipe in _modules.PIPELINES:
        assert f"{pipe['key']}  {pipe['name']}" in out and str(pipe["doc"]) in out


def test_pipeline_catalog_names_real_verbs_and_docs() -> None:
    """Validator finding 2026-09-09: the pipeline list was a hand-written tuple in cli.py.
    It is now a catalog in _modules; every verb it names is a real verb, every doc exists,
    and both README pipeline tables mention each pipeline's doc."""
    from pathlib import Path

    root = Path(cli.__file__).resolve().parents[1]
    keys = [p["key"] for p in _modules.PIPELINES]
    assert keys == ["A", "B", "C", "D"]
    for pipe in _modules.PIPELINES:
        for verb in pipe["verbs"]:
            assert verb in cli.COMMANDS, (pipe["key"], verb)
        assert (root / str(pipe["doc"])).is_file(), pipe["doc"]
    index = (root / "docs" / "README.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for pipe in _modules.PIPELINES:
        doc_name = str(pipe["doc"]).split("/")[-1]
        row = next((l for l in index.splitlines() if l.startswith(f"| **{pipe['key']} ·")), None)
        assert row is not None, f"docs/README.md has no pipeline row for {pipe['key']}"
        assert doc_name in row, (pipe["key"], "entry doc missing from its index row")
        for verb in pipe["verbs"]:
            assert f"`{verb}`" in row, (pipe["key"], verb, "missing from the index pipeline row")
        assert any(l.startswith(f"| **{pipe['key']} ·") and doc_name in l for l in readme.splitlines()), (pipe["key"], "README pipeline row")
