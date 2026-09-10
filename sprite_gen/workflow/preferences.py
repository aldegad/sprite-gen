# SPDX-License-Identifier: Apache-2.0
"""One preferences file; read-only queries, atomic compare-and-swap writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from sprite_gen.spec.runio import atomic_write_text, publish_guard
from .catalog import FIELDS, FLOWS, validate_choices


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
        return {"path": str(path), "revision": "missing", "profiles": {}}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"version", "profiles"} or type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("expected preferences version 1")
        if not isinstance(data["profiles"], dict):
            raise ValueError("profiles must be an object")
        for kind, choices in data["profiles"].items():
            validate_choices(kind, choices)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid preferences at {path}: {exc}; repair or explicitly remove the file") from exc
    return {"path": str(path), "revision": hashlib.sha256(raw).hexdigest(), "profiles": data["profiles"]}


def save_settings(kind: str, choices: dict, *, expected_revision: str, clear: bool = False) -> dict:
    """Only explicit user-approved calls write. First matching revision wins a race."""
    choices = validate_choices(kind, choices)
    if not expected_revision:
        raise ValueError("expected_revision is required; read current settings first")
    if not clear and not choices:
        raise ValueError("provide at least one default to save")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reuse the package's cross-platform lock backend. The stable sidecar is for
    # this file (not the replaceable JSON inode); atomic replace keeps reads pure.
    with publish_guard(path):
        before = read_settings()
        if before["revision"] != expected_revision:
            raise ValueError("preferences changed since they were read; read again and reconfirm the intended change")
        profiles = before["profiles"]
        if clear:
            profiles.pop(kind, None)
        else:
            profiles[kind] = {**profiles.get(kind, {}), **choices}
        payload = json.dumps({"version": 1, "profiles": profiles}, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != payload:
            atomic_write_text(path, payload)
        return read_settings()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=("show", "save", "clear"))
    parser.add_argument("--kind", choices=FLOWS)
    parser.add_argument("--expected-revision")
    for key, field in FIELDS.items():
        parser.add_argument("--" + key.replace("_", "-"), choices=field["options"])


def run(**kwargs) -> int:
    args = argparse.Namespace(**kwargs)
    choices = {key: getattr(args, key, None) for key in FIELDS if getattr(args, key, None) is not None}
    try:
        if args.action == "show":
            if choices or args.kind or args.expected_revision:
                raise ValueError("show accepts no mutation arguments")
            result = read_settings()
        else:
            if args.action == "clear" and choices:
                raise ValueError("clear does not accept choices")
            result = save_settings(args.kind, choices, expected_revision=args.expected_revision, clear=args.action == "clear")
    except (ValueError, OSError) as exc:
        raise SystemExit(f"defaults: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
