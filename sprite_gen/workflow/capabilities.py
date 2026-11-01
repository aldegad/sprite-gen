# SPDX-License-Identifier: Apache-2.0
"""What this engine can do, for a machine to read (docs/capabilities.md).

Every verb and every argument is read off the CLI's own parser (`cli.COMMANDS` and `cli._build_parser`), so a verb
or flag added there is listed with nothing else written. The one fact the parser does not hold is which providers
a verb may call and be billed by (`PROVIDER_CALLS`). Beside the verbs: where each provider is by default and the
variables that move it (`gen.address`), its API key's variable, and the variable that turns subscription logins
off (`gen.base.logins_on`). Printed as one compact JSON line, so an agent reads it once instead of every `--help`.
"""
from __future__ import annotations

import argparse
import json
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import PurePath
from typing import Any

from sprite_gen.gen import address, anthropic_api, openai_provider, xai
from sprite_gen.gen.base import LOGINS_ENV
from sprite_gen.util.console import utf8_streams

KIND = "sprite-gen-capabilities"

# The providers each verb may call over HTTP (`address.PROVIDERS`); a verb not named calls none. `gen`, `gen-set`
# and `logo` reach OpenAI by `--provider openai` and xAI by `--provider grok` (codex runs on its own login,
# `base.logins_on`); `curation` reaches xAI by its reroll and in-between buttons (codex or grok); `video-set` makes
# each clip with `video` and checks a side view's facing on Grok.
PROVIDER_CALLS: dict[str, tuple[str, ...]] = {
    "gen": (address.OPENAI, address.XAI),
    "gen-set": (address.OPENAI, address.XAI),
    "logo": (address.OPENAI, address.XAI),
    "pixel-draw": (address.ANTHROPIC,),
    "curation": (address.XAI,),
    "video": (address.XAI,),
    "video-extend": (address.XAI,),
    "video-edit": (address.XAI,),
    "video-resume": (address.XAI,),
    "video-set": (address.XAI,),
}
# Verbs that call a provider and buy nothing: `video-resume` polls and downloads a clip an earlier run bought.
NO_PURCHASE = frozenset({"video-resume"})
KEY_ENVS = {address.OPENAI: openai_provider.AUTH_ENV, address.XAI: xai.AUTH_ENV,
            address.ANTHROPIC: anthropic_api.AUTH_ENV}
# Argument types by name; any other is its parsing function's name without `_` and `parse_` (`_parse_grid`: grid).
TYPES = {str: None, int: "int", float: "float"}


def _version() -> str:
    try:
        return package_version("sprite-gen")
    except PackageNotFoundError:  # pragma: no cover - bare checkout
        return "unknown"


def _json(value: Any) -> Any:
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _type(action: argparse.Action) -> str | None:
    if action.nargs == 0:
        return "flag"
    if action.type is None or action.type in TYPES:
        return TYPES.get(action.type)
    if isinstance(action.type, type) and issubclass(action.type, PurePath):
        return "path"
    name = getattr(action.type, "__name__", type(action.type).__name__).lstrip("_")
    return name.removeprefix("parse_")


def _spelled(action: argparse.Action) -> list[str]:
    """Its spellings, the first long one first: `--out`, then `-o`."""
    return sorted(action.option_strings, key=lambda s: not s.startswith("--")) or [action.dest]


def _argument(action: argparse.Action) -> dict[str, Any]:
    name, *aliases = _spelled(action)
    entry: dict[str, Any] = {"name": name}
    if aliases:
        entry["aliases"] = aliases
    if not action.option_strings:
        entry["positional"] = True
    kind = _type(action)
    if kind:
        entry["type"] = kind
    if action.nargs not in (None, 0):
        entry["nargs"] = action.nargs
    if isinstance(action, argparse._AppendAction):
        entry["repeat"] = True
    # A flag is off unless given; say its default only when it is not.
    if action.default not in (None, argparse.SUPPRESS) and not (action.nargs == 0 and action.default is False):
        entry["default"] = _json(action.default)
    if action.required and action.option_strings:
        entry["required"] = True
    if action.choices is not None:
        entry["choices"] = _json(list(action.choices))
    return entry


def _parser(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """Its arguments as `--help` shows them, the groups of which at most one is given, and its own sub-verbs."""
    shown = [a for a in parser._actions if a.help != argparse.SUPPRESS]
    entry: dict[str, Any] = {"args": [_argument(a) for a in shown
                                      if not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))]}
    exclusive = [{"one_of": [_spelled(a)[0] for a in group._group_actions],
                  **({"required": True} if group.required else {})} for group in parser._mutually_exclusive_groups]
    if exclusive:
        entry["exclusive"] = exclusive
    for action in shown:
        if isinstance(action, argparse._SubParsersAction):
            entry["commands"] = {name: {**({"description": sub.description} if sub.description else {}), **_parser(sub)}
                                 for name, sub in action.choices.items()}
    return entry


def document() -> dict[str, Any]:
    """The whole document: every verb of `cli.COMMANDS`, in its order, from the parser the CLI runs."""
    from sprite_gen import cli  # the CLI imports this module to register the verb

    parsed = _parser(cli._build_parser())["commands"]
    domains = {verb: domain for domain, verbs in cli.command_domains().items() for verb in verbs}
    old_names: dict[str, list[str]] = {}
    for old, (new, _run) in cli.VERB_ALIASES.items():
        old_names.setdefault(new, []).append(old)
    verbs: dict[str, Any] = {}
    for name, (description, _add_arguments, _run) in cli.COMMANDS.items():
        providers = PROVIDER_CALLS.get(name, ())
        entry: dict[str, Any] = {"domain": domains[name], "description": description}
        if providers:
            entry["providers"] = list(providers)
        entry["paid"] = bool(providers) and name not in NO_PURCHASE
        if name in old_names:
            entry["old_names"] = old_names[name]
        verbs[name] = {**entry, **parsed[name]}
    return {
        "kind": KIND,
        "version": _version(),
        "providers": {name: {"base_url_env": provider.env, "base_url": provider.default, "key_env": KEY_ENVS[name]}
                      for name, provider in address.PROVIDERS.items()},
        "logins_env": LOGINS_ENV,
        "verbs": verbs,
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """No arguments: the document is the whole engine."""


def run(**_kwargs: object) -> int:
    utf8_streams()
    print(json.dumps(document(), ensure_ascii=False, separators=(",", ":")))
    return 0
