# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen capabilities` (docs/capabilities.md): every verb and argument as the parser declares them, read off
the parser itself, so a verb or flag added to the CLI is in it with nothing else written; which providers each verb
may call and be billed by; the provider address and login variables. One compact JSON line, a fraction of every
`--help` together."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sprite_gen import cli
from sprite_gen.gen import address

ROOT = Path(__file__).resolve().parents[2]


def _document() -> dict:
    from sprite_gen.workflow import capabilities
    return capabilities.document()


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices


def _visible(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [a for a in parser._actions if a.help != argparse.SUPPRESS
            and not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))]


def _printed(cmd: list[str]) -> str:
    proc = subprocess.run([sys.executable, "-m", "sprite_gen.cli", *cmd], capture_output=True, encoding="utf-8",
                          cwd=ROOT, env={**os.environ, "PYTHON_COLORS": "0", "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_every_verb_and_old_name_is_listed_once() -> None:
    verbs = _document()["verbs"]
    assert list(verbs) == list(cli.COMMANDS)
    old = {name: verb for verb, entry in verbs.items() for name in entry.get("old_names", [])}
    assert old == {name: new for name, (new, _run) in cli.VERB_ALIASES.items()}
    assert len(verbs) + len(old) == len(_subparsers(cli._build_parser()))


def test_each_verb_is_its_parser_argument_for_argument() -> None:
    """Name, other spellings, type, how many values, default, required and choices: what the parser holds."""
    verbs = _document()["verbs"]
    for name, sub in _subparsers(cli._build_parser()).items():
        if name in cli.VERB_ALIASES:
            continue
        entry = verbs[name]
        assert entry["description"] == sub.description
        assert entry["domain"] == next(d for d, vs in cli.command_domains().items() if name in vs)
        args = entry["args"]
        actions = _visible(sub)
        assert len(args) == len(actions), name
        for arg, action in zip(args, actions):
            spelled = action.option_strings or [action.dest]
            assert [arg["name"], *arg.get("aliases", [])] == [*sorted(spelled, key=lambda s: not s.startswith("--"))]
            assert arg.get("positional", False) is (not action.option_strings)
            assert arg.get("required", False) is bool(action.required and action.option_strings)
            assert arg.get("choices") == (list(action.choices) if action.choices is not None else None)
            assert arg.get("nargs") == (action.nargs if action.nargs not in (None, 0) else None)
            assert arg.get("repeat", False) is isinstance(action, argparse._AppendAction)


def test_every_flag_a_verbs_help_prints_is_listed(monkeypatch) -> None:
    """Read off another source than the parser's actions: the help text a person reads (wide, so no flag wraps)."""
    monkeypatch.setenv("COLUMNS", "1000")
    verbs = _document()["verbs"]
    for name, sub in _subparsers(cli._build_parser()).items():
        entry = verbs[cli.VERB_ALIASES[name][0] if name in cli.VERB_ALIASES else name]
        listed = {spelled for arg in entry["args"] for spelled in (arg["name"], *arg.get("aliases", []))}
        # An option's line opens with its spellings, then two spaces and its help (which may name other verbs' flags).
        invocations = [re.split(r"\s{2,}", line.strip())[0] for line in sub.format_help().splitlines()
                       if line.startswith("  -")]
        printed = {flag for line in invocations for flag in re.findall(r"--[a-z][\w-]*", line)} - {"--help"}
        assert printed == {s for s in listed if s.startswith("--")}, name


def test_types_defaults_and_flags_are_written_as_json() -> None:
    gen = {arg["name"]: arg for arg in _document()["verbs"]["gen"]["args"]}
    assert gen["--out"]["type"] == "path" and gen["--out"]["required"] is True
    assert gen["--provider"]["choices"] == ["codex", "grok", "openai"]
    slicing = {arg["name"]: arg for arg in _document()["verbs"]["slice-sheet"]["args"]}
    assert slicing["--grid"] == {"name": "--grid", "type": "grid", "default": [3, 2]}
    assert slicing["--cell-width"] == {"name": "--cell-width", "type": "int", "default": 512}
    inputs = {arg["name"]: arg for arg in _document()["verbs"]["compose-gif"]["args"]}["inputs"]
    assert inputs == {"name": "inputs", "positional": True, "type": "path", "nargs": "*"}
    prepare = {arg["name"]: arg for arg in _document()["verbs"]["prepare"]["args"]}
    assert prepare["--fit-ground-frames"] == {"name": "--fit-ground-frames", "aliases": ["--no-fit-ground-frames"],
                                              "type": "flag"}
    video, video_prompt = ({arg["name"]: arg for arg in _document()["verbs"][verb]["args"]} for verb in ("video", "video-prompt"))
    assert video["--model"]["default"] == video_prompt["--model"]["default"] == "grok-imagine-video-1.5"
    assert _document()["verbs"]["video-sway"]["exclusive"] == [{"one_of": ["--loop-dir", "--frames"], "required": True}]
    rife = _document()["verbs"]["rife"]
    assert rife["args"] == [] and list(rife["commands"]) == ["install"]
    assert {arg["name"] for arg in rife["commands"]["install"]["args"]} == {"--dir", "--zip", "--force", "--no-check",
                                                                             "--backend"}


def test_a_verb_or_flag_added_to_the_cli_is_listed_with_nothing_else_written(monkeypatch) -> None:
    def add(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--depth", type=int, default=3, choices=(1, 2, 3))
        parser.add_argument("--note", required=True)

    commands = {**cli.COMMANDS, "probe-verb": ("A verb this test adds.", add, lambda **_: 0)}
    monkeypatch.setattr(cli, "COMMANDS", commands)
    monkeypatch.setitem(sys.modules["sprite_gen._modules"].MODULE_DOMAIN, "test_capabilities", "workflow")
    entry = _document()["verbs"]["probe-verb"]
    assert entry["description"] == "A verb this test adds."
    assert entry["args"] == [{"name": "--depth", "type": "int", "default": 3, "choices": [1, 2, 3]},
                             {"name": "--note", "required": True}]
    assert "providers" not in entry and entry["paid"] is False


def test_the_providers_of_each_verb_are_known_providers_of_real_verbs() -> None:
    from sprite_gen.workflow import capabilities
    assert set(capabilities.PROVIDER_CALLS) <= set(cli.COMMANDS)
    assert {p for providers in capabilities.PROVIDER_CALLS.values() for p in providers} <= set(address.PROVIDERS)
    assert capabilities.NO_PURCHASE <= set(capabilities.PROVIDER_CALLS)


def test_what_each_paid_verb_calls() -> None:
    verbs = _document()["verbs"]
    paid = {name: entry["providers"] for name, entry in verbs.items() if entry["paid"]}
    assert paid == {
        "gen": ["openai", "xai"], "gen-set": ["openai", "xai"], "logo": ["openai", "xai"],
        "pixel-draw": ["anthropic"], "curation": ["xai"],
        "video": ["xai"], "video-extend": ["xai"], "video-edit": ["xai"], "video-set": ["xai"],
    }
    assert verbs["video-resume"]["providers"] == ["xai"] and verbs["video-resume"]["paid"] is False
    assert "providers" not in verbs["video-loop"] and verbs["video-loop"]["paid"] is False


def test_the_provider_section_is_the_address_registry_and_the_key_names() -> None:
    document = _document()
    assert document["kind"] == "sprite-gen-capabilities"
    assert document["logins_env"] == "SPRITE_GEN_LOGINS"
    assert document["providers"] == {
        "openai": {"base_url_env": "SPRITE_GEN_OPENAI_BASE_URL", "base_url": "https://api.openai.com/v1",
                   "key_env": "OPENAI_API_KEY"},
        "xai": {"base_url_env": "SPRITE_GEN_XAI_BASE_URL", "base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY"},
        "anthropic": {"base_url_env": "SPRITE_GEN_ANTHROPIC_BASE_URL", "base_url": "https://api.anthropic.com/v1",
                      "key_env": "ANTHROPIC_API_KEY"},
    }


def test_the_document_ignores_the_address_variables(monkeypatch) -> None:
    """It says where each provider is by default; what this process has set is not part of what the engine can do."""
    plain = _document()
    monkeypatch.setenv("SPRITE_GEN_XAI_BASE_URL", "http://127.0.0.1:9/xai")
    assert _document() == plain


def test_the_verb_prints_one_compact_json_line_a_fraction_of_every_help() -> None:
    printed = _printed(["capabilities"])
    assert printed.count("\n") == 1 and printed.endswith("\n")
    assert json.loads(printed)["verbs"] == json.loads(json.dumps(_document()))["verbs"]
    parser = cli._build_parser()
    helps = len(parser.format_help().encode()) + sum(len(sub.format_help().encode())
                                                     for sub in _subparsers(parser).values())
    assert len(printed.encode()) * 2 < helps
