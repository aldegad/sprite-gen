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
    assert "pipelines:" in out and "A  atlas rows" in out and "B  video" in out
