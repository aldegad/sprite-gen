# SPDX-License-Identifier: Apache-2.0
"""Parts catalog — the declaration half of the parts rig.

A catalog names every body part of one base image: its draw order, its box on
the base, its pivot, its group, and the prompt that generates it alone. The
catalog is the numeric SSoT that `gen`, `match` and `rig` all read; nothing
downstream infers a part from pixels. This module is filesystem-free and
deterministic: the same catalog always yields the same error list in the same
order, so the CLI can refuse a bad catalog before any generation is paid for.

Behavior contract: `docs/parts-rig.md` §2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

KIND = "sprite-gen-parts-catalog"
VERSION = 1
PART_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
DEFAULT_VARIANT = "default"
DEFAULT_TOLERANCE = 0.06
DEFAULT_AGREE_FLOOR = 0.85
DEFAULT_GROUP = "none"

# Known variant vocabularies are documentation, not a restriction: any id that
# matches PART_ID is accepted. The runtime looks these names up by convention.
MOUTH_VARIANTS = ("closed", "half", "open", "o")
EYELID_VARIANTS = ("open", "half", "closed")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bbox_ok(bbox: Any) -> bool:
    return (isinstance(bbox, list) and len(bbox) == 4 and all(_is_int(v) for v in bbox)
            and bbox[2] > 0 and bbox[3] > 0 and bbox[0] >= 0 and bbox[1] >= 0)


def _point_ok(point: Any) -> bool:
    return isinstance(point, list) and len(point) == 2 and all(_is_int(v) for v in point)


def validate_catalog(catalog: Any) -> list[str]:
    """Return every violation, in declaration order. Empty list means valid."""
    errors: list[str] = []
    if not isinstance(catalog, dict):
        return ["catalog must be a JSON object"]
    if catalog.get("kind") != KIND:
        errors.append(f"kind must be {KIND!r}")
    if catalog.get("version") != VERSION:
        errors.append(f"version must be {VERSION}")
    canvas = catalog.get("canvas")
    cw = ch = None
    if not (isinstance(canvas, dict) and _is_int(canvas.get("width")) and _is_int(canvas.get("height"))
            and canvas["width"] > 0 and canvas["height"] > 0):
        errors.append("canvas.width / canvas.height must be positive integers")
    else:
        cw, ch = canvas["width"], canvas["height"]
    base = catalog.get("base")
    if not (isinstance(base, str) and base):
        errors.append("base must be a non-empty path string (relative to the catalog)")
    chroma = catalog.get("chroma_key")
    if chroma is not None and not (isinstance(chroma, str) and chroma in ("green", "magenta")):
        errors.append("chroma_key must be 'green' or 'magenta' when declared")
    groups = catalog.get("groups", {})
    if not isinstance(groups, dict):
        errors.append("groups must be an object")
        groups = {}
    for name, group in groups.items():
        if not PART_ID.match(str(name)):
            errors.append(f"groups.{name}: invalid group id")
        if not (isinstance(group, dict) and _point_ok(group.get("pivot"))):
            errors.append(f"groups.{name}.pivot must be [x, y] integers")
    parts = catalog.get("parts")
    if not isinstance(parts, list) or not parts:
        errors.append("parts must be a non-empty list")
        return errors
    seen_ids: set[str] = set()
    seen_z: dict[int, str] = {}
    for index, part in enumerate(parts):
        where = f"parts[{index}]"
        if not isinstance(part, dict):
            errors.append(f"{where}: must be an object")
            continue
        pid = part.get("id")
        if not (isinstance(pid, str) and PART_ID.match(pid)):
            errors.append(f"{where}.id: must match {PART_ID.pattern}")
        elif pid in seen_ids:
            errors.append(f"{where}.id: duplicate part id {pid!r}")
        else:
            seen_ids.add(pid)
        where = f"parts.{pid}" if isinstance(pid, str) else where
        z = part.get("z")
        if not _is_int(z):
            errors.append(f"{where}.z: must be an integer")
        elif z in seen_z:
            errors.append(f"{where}.z: duplicate draw order {z} (also {seen_z[z]!r})")
        else:
            seen_z[z] = pid
        bbox = part.get("bbox")
        if not _bbox_ok(bbox):
            errors.append(f"{where}.bbox: must be [x, y, w, h] integers with w,h > 0")
        elif cw is not None and (bbox[0] + bbox[2] > cw or bbox[1] + bbox[3] > ch):
            errors.append(f"{where}.bbox: exceeds canvas {cw}x{ch}")
        pivot = part.get("pivot")
        if not _point_ok(pivot):
            errors.append(f"{where}.pivot: must be [x, y] integers")
        elif _bbox_ok(bbox) and not (bbox[0] <= pivot[0] <= bbox[0] + bbox[2]
                                     and bbox[1] <= pivot[1] <= bbox[1] + bbox[3]):
            errors.append(f"{where}.pivot: must lie inside bbox")
        group = part.get("group", DEFAULT_GROUP)
        if group != DEFAULT_GROUP and group not in groups:
            errors.append(f"{where}.group: unknown group {group!r}")
        prompt = part.get("prompt")
        if not (isinstance(prompt, str) and prompt.strip()):
            errors.append(f"{where}.prompt: must be a non-empty string")
        variants = part.get("variants", {DEFAULT_VARIANT: ""})
        if not isinstance(variants, dict) or not variants:
            errors.append(f"{where}.variants: must be a non-empty object of variant -> prompt suffix")
        else:
            if DEFAULT_VARIANT not in variants:
                errors.append(f"{where}.variants: must include {DEFAULT_VARIANT!r}")
            for vname, suffix in variants.items():
                if not PART_ID.match(str(vname)):
                    errors.append(f"{where}.variants.{vname}: invalid variant id")
                if not isinstance(suffix, str):
                    errors.append(f"{where}.variants.{vname}: prompt suffix must be a string")
        tolerance = part.get("tolerance", DEFAULT_TOLERANCE)
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not (0 < tolerance < 1):
            errors.append(f"{where}.tolerance: must be a number in (0, 1)")
        floor = part.get("agree_floor", DEFAULT_AGREE_FLOOR)
        if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not (0 < floor <= 1):
            errors.append(f"{where}.agree_floor: must be a number in (0, 1]")
    return errors


def load_catalog(path: Path | str) -> dict[str, Any]:
    """Read + validate. Raises ValueError with every violation listed."""
    path = Path(path)
    catalog = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_catalog(catalog)
    if errors:
        raise ValueError("invalid parts catalog " + str(path) + ":\n  " + "\n  ".join(errors))
    return catalog


def parts_by_z(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Parts in draw order (bottom first)."""
    return sorted(catalog["parts"], key=lambda part: part["z"])


def jobs(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Every (part, variant) generation job, in draw order then declaration order."""
    out: list[dict[str, Any]] = []
    for part in parts_by_z(catalog):
        variants = part.get("variants", {DEFAULT_VARIANT: ""})
        for vname, suffix in variants.items():
            out.append({
                "part": part["id"], "variant": vname, "z": part["z"], "bbox": list(part["bbox"]),
                "prompt": part["prompt"] + ((" " + suffix.strip()) if suffix.strip() else ""),
                "tolerance": float(part.get("tolerance", DEFAULT_TOLERANCE)),
            })
    return out


def job_name(part: str, variant: str) -> str:
    return part if variant == DEFAULT_VARIANT else f"{part}__{variant}"
