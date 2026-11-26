# SPDX-License-Identifier: Apache-2.0
"""One preferences file; read-only queries, atomic compare-and-swap writes.

Version 2 holds the per-journey `profiles` and the `general` settings every profile shares
(`catalog.GENERAL_FIELDS`); a version 1 file (profiles only) reads with no general settings and is
written as version 2 by the next save."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from sprite_gen.gen.codex_provider import driver_from
from sprite_gen.spec.runio import atomic_write_text, publish_guard
from .catalog import FIELDS, FLOWS, GENERAL_FIELDS, validate_choices, validate_general

VERSION = 2


def settings_path() -> Path:
    configured = os.environ.get("SPRITE_GEN_CONFIG_DIR")
    if configured is not None and not configured.strip():
        raise ValueError("SPRITE_GEN_CONFIG_DIR is empty")
    root = Path(configured).expanduser() if configured is not None else Path.home() / ".config" / "sprite-gen"
    return root / "preferences.json"


def read_settings() -> dict:
    path = settings_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"path": str(path), "revision": "missing", "profiles": {}, "general": {}}
    try:
        data = json.loads(raw)
        shapes = {1: {"version", "profiles"}, VERSION: {"version", "profiles", "general"}}
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or shapes.get(data["version"]) != set(data)):
            raise ValueError(f"expected preferences version 1 or {VERSION}")
        if not isinstance(data["profiles"], dict):
            raise ValueError("profiles must be an object")
        for kind, choices in data["profiles"].items():
            validate_choices(kind, choices)
        general = validate_general(data.get("general", {}))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid preferences at {path}: {exc}; repair or explicitly remove the file") from exc
    return {"path": str(path), "revision": hashlib.sha256(raw).hexdigest(), "profiles": data["profiles"],
            "general": general}


def save_settings(kind: str | None, choices: dict, *, expected_revision: str, clear: bool = False,
                  general: dict | None = None, clear_general: tuple[str, ...] = ()) -> dict:
    """Only explicit user-approved calls write. First matching revision wins a race. `kind` and `choices` save or
    `clear` one profile; `general` saves settings every profile shares (no `kind` needed for those alone), and
    `clear` with `clear_general` removes the named ones (with or without a `kind`)."""
    general = validate_general(general or {})
    unknown = [key for key in clear_general if key not in GENERAL_FIELDS]
    if unknown:
        raise ValueError(f"unknown general setting: {', '.join(unknown)}")
    if clear_general and not clear:
        raise ValueError("general settings are removed only by clear")
    if kind is not None or choices or (clear and not clear_general):
        choices = validate_choices(kind, choices)
    if not expected_revision:
        raise ValueError("expected_revision is required; read current settings first")
    if not clear and not choices and not general:
        raise ValueError("provide at least one default to save")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reuse the package's cross-platform lock backend. The stable sidecar is for
    # this file (not the replaceable JSON inode); atomic replace keeps reads pure.
    with publish_guard(path):
        before = read_settings()
        if before["revision"] != expected_revision:
            raise ValueError("preferences changed since they were read; read again and reconfirm the intended change")
        profiles, kept = before["profiles"], before["general"]
        if clear:
            if kind is not None:
                profiles.pop(kind, None)
            for key in clear_general:
                kept.pop(key, None)
        elif choices:
            profiles[kind] = {**profiles.get(kind, {}), **choices}
        payload = json.dumps({"version": VERSION, "profiles": profiles, "general": {**kept, **general}},
                             ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != payload:
            atomic_write_text(path, payload)
        return read_settings()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=("show", "save", "clear"))
    parser.add_argument("--kind", choices=FLOWS)
    parser.add_argument("--expected-revision")
    for key, field in FIELDS.items():
        parser.add_argument("--" + key.replace("_", "-"), choices=field["options"])
    for key, field in GENERAL_FIELDS.items():
        parser.add_argument("--" + key.replace("_", "-"), choices=field["options"], metavar=field.get("metavar"),
                            help="shared by every profile; saved without --kind" + (
                                f"; {field['help']}" if field.get("help") else ""))
    parser.add_argument("--setting", action="append", choices=tuple(GENERAL_FIELDS), dest="clear_general",
                        help="with clear: remove this shared setting (repeatable)")


def run(**kwargs) -> int:
    args = argparse.Namespace(**kwargs)
    choices = {key: getattr(args, key, None) for key in FIELDS if getattr(args, key, None) is not None}
    general = {key: getattr(args, key, None) for key in GENERAL_FIELDS if getattr(args, key, None) is not None}
    clear_general = tuple(getattr(args, "clear_general", None) or ())
    try:
        if args.action == "show":
            if choices or general or clear_general or args.kind or args.expected_revision:
                raise ValueError("show accepts no mutation arguments")
            result = read_settings()
        else:
            if args.action == "clear" and (choices or general):
                raise ValueError("clear does not accept choices")
            if args.action == "save" and clear_general:
                raise ValueError("--setting names what clear removes")
            result = save_settings(args.kind, choices, expected_revision=args.expected_revision,
                                   clear=args.action == "clear", general=general, clear_general=clear_general)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"defaults: {exc}") from exc
    # What a codex generation runs on with these settings and no --model (docs/gen.md, "The codex driver model").
    result["codex_driver"] = driver_from(result["general"]).report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
