# SPDX-License-Identifier: Apache-2.0
"""Deterministic composite bake — stack curated rows by integer pivot translation.

The execution half of the layer feature (`docs/layer-tracks.md` §4/§5). Given a
request that declares a `rig`, per-row `track`s and a `layers` stack, this bakes
each composite into the `layers/` sibling artifact tree:

    layers/<name>.png             composed sheet
    layers/<name>.manifest.json   runtime manifest, same key set as manifest.json
    layers/layers.report.json     provenance: stack, source revisions, offsets, clips

Determinism is the whole point: composition is integer translation plus alpha
compositing over the curated instances, with no resampling, rotation or scale of
its own, so the same run bakes the same bytes every time. Rotation and scale are
curation's job and are already inside the source instance by the time this runs.

Boundary (SoC): declaration truth is `sprite_gen.compose.layers` (filesystem-free —
profiles, tracks, stack shape). Everything here needs the run dir: does the
declared frame exist, is the mask the cell's size, does an aligned element clip,
was the source row re-rolled since its landmarks were declared.

A curated instance is assembled from the shared primitives in `curation` /
`layout` / `breathe`, in the same order as every other consumer of a finished
generation (`compose_atlas`, `compose_gif`, `export_pngs`, `anchor`): the
primitives are the single source of that math, and each consumer composes them
for its own output.

Atomicity: every composite is baked in memory first. One violation anywhere
fails the whole call with the complete diagnostic list and writes nothing, so
`layers/` never holds a half-baked set (No Silent Fallback: no composite is
skipped quietly, none is written from partly-valid input). The write is held to
the same rule — sheets, manifests and the report go out through one
`runio.atomic_write_set`, so an IO failure partway through cannot leave a
published sheet that no report names.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.effects.breathe import fit_breathe_pattern, phase_frame, resolve_anatomy
from sprite_gen.compose.compose_atlas import cell_geometry
from sprite_gen.curate.curation import (apply_pixel_edits, apply_transform, edit_index, frame_variant,
                                 load_curation, pixel_snap_scale, source_frame_index,
                                 state_breathe, state_pixel_ops, state_plan, state_revision)
from sprite_gen.frames.extract import heal_run, require_frames_manifest
from sprite_gen.compose.layers import (has_layer_contract, landmark_map, require_valid_layer_request,
                               resolve_stack, state_track)
from sprite_gen.spec.layout import row_frame_rel, state_frame_total
from sprite_gen.spec.runio import acquire_run_dir_lock, atomic_write_set, load_request

# Where a composite bake puts its output and what it names the report. The one
# place these strings live (`docs/layer-tracks.md` §5 documents the same tree;
# `docs/run-contract.md` §2 owns the run-dir tree it belongs to).
LAYERS_DIRNAME = "layers"
REPORT_NAME = "layers.report.json"
REPORT_KIND = "sprite-gen-layers-report"

# "A pixel" for the clip check. Matches the alpha floor recolor and compose-gif
# already use, so the three agree on what counts as art rather than as the soft
# key's edge bleed: a translated element that pushes only sub-threshold edge
# pixels off the cell is not a clip.
CLIP_ALPHA_THRESHOLD = 8


@dataclass
class _Source:
    """One source row of a stack: its curated play sequence and baked instances.

    `ordered[i]` is the instance index played at position `i` (pool index, or a
    curated clone index outside the pool), `phases[i]` its breathe phase.
    """

    state: str
    track: str
    row: dict[str, Any]
    total: int
    ordered: list[int]
    phases: list[float]
    transforms: dict[int, dict[str, float]]
    variant: str
    breathe: dict[str, Any] | None
    revision: list[str] | None
    anatomy: Any = None
    baked: dict[int, Image.Image] = field(default_factory=dict)


def _resolve_source(run_dir: Path, request: dict[str, Any], curation: dict[str, Any] | None,
                    row: dict[str, Any], state: str) -> _Source:
    total = state_frame_total(request, state)
    ordered, transforms = state_plan(curation, state, total)
    cfg = state_breathe(curation, state)
    phases = fit_breathe_pattern(len(ordered), cfg) if (cfg and ordered) else [0.0] * len(ordered)
    return _Source(
        state=state,
        track=state_track(request, state),
        row=row,
        total=total,
        ordered=ordered,
        phases=phases,
        transforms=transforms,
        variant=frame_variant(curation, state),
        breathe=cfg,
        revision=state_revision(run_dir, state, request=request, row=row),
    )


def _instance(run_dir: Path, request: dict[str, Any], curation: dict[str, Any] | None,
              source: _Source, position: int, cell_size: tuple[int, int],
              snap_scale: int | None, errors: list[str]) -> Image.Image | None:
    """The curated, breathing instance played at `position` — what the atlas bakes."""
    if position in source.baked:
        return source.baked[position]
    instance = source.ordered[position]
    src_index = source_frame_index(curation, source.state, instance, source.total)
    try:
        rel = row_frame_rel(source.row, src_index, source.variant)
    except SystemExit as exc:
        errors.append(f"{source.state}: {exc}")
        return None
    path = run_dir / rel
    if not path.is_file():
        errors.append(
            f"{source.state} instance {instance}: missing frame ({source.variant} variant) "
            f"{rel} — the declared source is not in the published generation")
        return None
    edit_idx = edit_index(curation, source.state, instance)
    with Image.open(path) as opened:
        art = apply_pixel_edits(opened.convert("RGBA"),
                                state_pixel_ops(curation, source.state).get(edit_idx))
    if art.size != cell_size:
        errors.append(
            f"{source.state} instance {instance}: frame {rel} is {art.width}x{art.height}; "
            f"expected the run's {cell_size[0]}x{cell_size[1]} cell")
        return None
    frame = apply_transform(art, source.transforms.get(edit_idx), cell_size,
                            snap_scale=snap_scale if source.variant == "pixel" else None)
    if source.breathe:
        if source.anatomy is None:
            # One anatomy per row, resolved from its first played instance —
            # same rule as compose_atlas (anatomy is a character property).
            source.anatomy = resolve_anatomy(frame, source.breathe)
        frame = phase_frame(frame, source.breathe, source.phases[position], source.anatomy)
    source.baked[position] = frame
    return frame


def _load_mask(run_dir: Path, rel: Any, cell_size: tuple[int, int], where: str,
               errors: list[str]) -> Image.Image | None:
    """An arbitrary alpha mask as an `L` channel of the cell's size.

    Any PNG shape is legal — that is the point of "arbitrary mask" — but its
    geometry is not: a mask of another size cannot be applied without resampling,
    and resampling is exactly what this bake refuses to do.
    """
    if not isinstance(rel, str) or not rel:
        errors.append(f"{where}: mask must be a run-relative path string (got {rel!r})")
        return None
    candidate = Path(rel)
    resolved = (run_dir / candidate).resolve()
    if candidate.is_absolute() or run_dir.resolve() not in resolved.parents:
        errors.append(f"{where}: mask path {rel!r} must stay inside the run dir")
        return None
    if not resolved.is_file():
        errors.append(f"{where}: mask file {rel} not found")
        return None
    try:
        with Image.open(resolved) as opened:
            opened.load()
            mask = opened.getchannel("A") if "A" in opened.getbands() else opened.convert("L")
    except OSError as exc:
        errors.append(f"{where}: mask file {rel} is unreadable ({exc})")
        return None
    if mask.size != cell_size:
        errors.append(
            f"{where}: mask {rel} is {mask.width}x{mask.height}; expected the run's "
            f"{cell_size[0]}x{cell_size[1]} cell (a mask is never resampled)")
        return None
    return mask


def apply_alpha_mask(frame: Image.Image, mask: Image.Image) -> Image.Image:
    """`a' = (a * m + 127) // 255` — integer, rounding half up (contract §4).

    Spelled out rather than delegated to an imaging multiply: the library
    rounding a blend uses is an implementation detail that can differ by
    version, and this arithmetic is a published clause of the composite
    contract, not an internal choice.
    """
    alpha = np.asarray(frame.getchannel("A"), dtype=np.uint32)
    values = np.asarray(mask, dtype=np.uint32)
    masked = frame.copy()
    masked.putalpha(Image.fromarray(((alpha * values + 127) // 255).astype(np.uint8), "L"))
    return masked


def _clipped_pixels(frame: Image.Image, offset: tuple[int, int],
                    cell_size: tuple[int, int]) -> int:
    """Art pixels this offset would push outside the cell (0 = nothing is lost)."""
    dx, dy = offset
    cell_w, cell_h = cell_size
    alpha = frame.getchannel("A")
    total = sum(alpha.histogram()[CLIP_ALPHA_THRESHOLD + 1:])
    left, top = max(0, -dx), max(0, -dy)
    right, bottom = min(frame.width, cell_w - dx), min(frame.height, cell_h - dy)
    if right <= left or bottom <= top:
        return total
    inside = sum(alpha.crop((left, top, right, bottom)).histogram()[CLIP_ALPHA_THRESHOLD + 1:])
    return total - inside


def _element_offset(request: dict[str, Any], name: str, element: dict[str, Any],
                    body: _Source, source: _Source, body_instance: int, instance: int,
                    errors: list[str]) -> tuple[int, int] | None:
    """`to_point - from_point`, both declared, neither inferred."""
    offset: list[int] = []
    for role, state, wanted, index in (
        ("to", body.state, str(element["to"]), body_instance),
        ("from", source.state, str(element["from"]), instance),
    ):
        points = landmark_map(request, state, index)
        if points is None:
            errors.append(
                f"layers.{name}: element {source.state!r} aligns {role}={wanted!r} against "
                f"{state!r} instance {index}, which declares no landmarks — a curated clone "
                f"instance carries its own transform, so its pivots cannot be borrowed from "
                f"the frame it copies. Drop the clone from this row or bake without it")
            return None
        point = points.get(wanted)
        if point is None:
            errors.append(
                f"layers.{name}: element {source.state!r} aligns {role}={wanted!r} but "
                f"{state!r} instance {index} does not declare it")
            return None
        offset.append(point[0])
        offset.append(point[1])
    return (offset[0] - offset[2], offset[1] - offset[3])


def _compose_one(run_dir: Path, request: dict[str, Any], curation: dict[str, Any] | None,
                 rows_by_state: dict[str, Any], name: str, cell_size: tuple[int, int],
                 snap_scale: int | None, errors: list[str]) -> dict[str, Any] | None:
    spec = (request.get("layers") or {})[name]
    stack = resolve_stack(request, name)
    sources: list[_Source] = []
    for element in stack:
        state = str(element["state"])
        row = rows_by_state.get(state)
        if row is None:
            errors.append(
                f"layers.{name}: element state {state!r} has no row in the published "
                f"generation — extract it before composing")
            return None
        sources.append(_resolve_source(run_dir, request, curation, row, state))

    # The body element is stack[0] — the validator has already established that,
    # so the composer reads the declaration instead of re-deriving it.
    body = sources[0]
    length = len(body.ordered)
    if length == 0:
        errors.append(
            f"layers.{name}: body row {body.state!r} plays no frames (every instance is "
            f"deleted in curation) — a composite has nothing to stack onto")
        return None

    for element, source in zip(stack, sources):
        pin = element.get("revision")
        if pin is not None:
            current = source.revision
            if not (current and list(pin) == list(current[:len(pin)])):
                errors.append(
                    f"layers.{name}: element {source.state!r} pins revision {list(pin)} but the "
                    f"row is now {current} — the source was re-rolled since its landmarks were "
                    f"declared; re-declare them and re-pin (pivots are never re-fitted)")
        if source is body:
            continue
        # A held pose is a row whose POOL is one frame. A row that merely got
        # curated down to fewer instances than the body is a mismatch, not a
        # hold: pairing its shortened sequence with the body's would silently
        # pick which body frames go unaccompanied.
        wanted = 1 if source.total == 1 else length
        if len(source.ordered) != wanted:
            errors.append(
                f"layers.{name}: element {source.state!r} plays {len(source.ordered)} frames "
                f"against a body of {length} — an overlay plays the body's sequence, and only "
                f"a single-frame row holds one pose")

    masks: dict[int, Image.Image | None] = {}
    for position, element in enumerate(stack):
        if element.get("mask") is not None:
            masks[position] = _load_mask(run_dir, element["mask"], cell_size,
                                         f"layers.{name}.stack[{position}]", errors)
    if errors:
        return None

    element_reports = [{
        "state": source.state,
        "track": source.track,
        "from": element["from"],
        "to": element["to"],
        "mask": element.get("mask"),
        "allow_clip": bool(element.get("allow_clip")),
        "frame_variant": source.variant,
        "state_revision": source.revision,
        "pool_frames": source.total,
        "played_frames": len(source.ordered),
        "offsets": [],
        "clipped_pixels": 0,
    } for element, source in zip(stack, sources)]

    cells: dict[bytes, int] = {}
    order: list[int] = []          # play position -> unique cell index
    unique: list[Image.Image] = []
    for position in range(length):
        cell = Image.new("RGBA", cell_size, (0, 0, 0, 0))
        for slot, (element, source) in enumerate(zip(stack, sources)):
            at = 0 if (source is not body and source.total == 1) else position
            frame = _instance(run_dir, request, curation, source, at, cell_size,
                              snap_scale, errors)
            if frame is None:
                continue
            mask = masks.get(slot)
            if mask is not None:
                frame = apply_alpha_mask(frame, mask)
            if source is body:
                offset = (0, 0)
            else:
                offset = _element_offset(request, name, element, body, source,
                                         body.ordered[position], source.ordered[at], errors)
                if offset is None:
                    continue
            element_reports[slot]["offsets"].append(list(offset))
            clipped = _clipped_pixels(frame, offset, cell_size)
            if clipped:
                element_reports[slot]["clipped_pixels"] += clipped
                if not element["allow_clip"]:
                    errors.append(
                        f"layers.{name} frame {position}: element {source.state!r} offset "
                        f"{list(offset)} pushes {clipped} art pixel(s) outside the "
                        f"{cell_size[0]}x{cell_size[1]} cell — declare allow_clip to accept "
                        f"the loss, or move the landmarks")
            layer = Image.new("RGBA", cell_size, (0, 0, 0, 0))
            layer.paste(frame, offset)
            cell.alpha_composite(layer)
        # Identical cells share one column, exactly like the atlas: the rect list
        # is indexed by play position and repeats (docs/layer-tracks.md B7).
        key = cell.tobytes()
        index = cells.get(key)
        if index is None:
            index = len(unique)
            cells[key] = index
            unique.append(cell)
        order.append(index)
    if errors:
        return None

    return {
        "name": name,
        "spec": spec,
        "body": body,
        "sources": sources,
        "elements": element_reports,
        "cells": unique,
        "order": order,
    }


def _render(run_dir: Path, request: dict[str, Any], curation: dict[str, Any] | None,
            composed: dict[str, Any], cell_size: tuple[int, int]) -> dict[str, Any]:
    """One composite's payloads + its report entry — rendered, not yet published.

    Nothing here touches the run dir: the caller collects every composite's
    payloads and publishes them as one set, so a bake either replaces all the
    sheets it named or none of them.
    """
    name = composed["name"]
    cell_w, cell_h = cell_size
    unique, order = composed["cells"], composed["order"]
    sheet = Image.new("RGBA", (max(1, len(unique)) * cell_w, cell_h), (0, 0, 0, 0))
    for column, cell in enumerate(unique):
        sheet.alpha_composite(cell, (column * cell_w, 0))
    rects = [{"x": index * cell_w, "y": 0, "w": cell_w, "h": cell_h} for index in order]

    body_entry = request["states"][composed["body"].state]
    spec = composed["spec"]
    fps = int(spec.get("fps", body_entry.get("fps", 6)))
    loop = bool(spec.get("loop", body_entry.get("loop", True)))
    variants = {source.variant for source in composed["sources"]}
    variant_summary = variants.pop() if len(variants) == 1 else "mixed"

    sheet_name = f"{name}.png"
    manifest_name = f"{name}.manifest.json"
    out_dir = run_dir / LAYERS_DIRNAME

    frame_layout = {
        "sheetWidth": sheet.width,
        "sheetHeight": sheet.height,
        "cellWidth": cell_w,
        "cellHeight": cell_h,
        "rows": {name: rects},
    }
    duration_ms = max(1, round(1000.0 / (float(fps) or 6.0)))
    manifest = {
        "characterId": request["character"]["id"],
        "engine": "component-row",
        "game_input": sheet_name,
        "degraded_static_fallback": False,
        "curation_applied": curation is not None,
        "frame_variant": variant_summary,
        "sprite_sheet_alpha": sheet_name,
        # The alpha/extraction report of THIS sheet — and a composite has none.
        # The field keeps the base manifest's key set (§5) and states the absence
        # rather than pointing at a document of another kind: `layers.report.json`
        # is the bake's provenance record for every composite, not this sheet's
        # alpha report, so a consumer resolving this field would open the wrong
        # document. The provenance pointer lives in the `layers` block below.
        "sprite_sheet_alpha_report": None,
        "base_image": request["character"].get("base_image"),
        "cell": request["cell"],
        "chroma_key": request.get("chroma_key"),
        "animation": {
            "cellWidth": cell_w,
            "cellHeight": cell_h,
            "columns": len(unique),
            "rows": {name: {
                "row": 0,
                "frames": len(order),
                "fps": fps,
                "durations_ms": [duration_ms] * len(order),
                "loop": loop,
                "frame_variant": variant_summary,
                "track": "composite",
            }},
        },
        "frame_layout": frame_layout,
        # Provenance a runtime can read without the report: what this sheet is a
        # combination of. The runtime still plays it as an ordinary one-row atlas.
        "layers": {
            "name": name,
            "report": REPORT_NAME,
            "stack": [{k: element[k] for k in ("state", "track", "from", "to", "mask",
                                               "allow_clip", "state_revision")}
                      for element in composed["elements"]],
        },
    }

    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    return {
        "payloads": {
            out_dir / sheet_name: buffer.getvalue(),
            out_dir / manifest_name: json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        },
        "entry": {
            "name": name,
            "sheet": sheet_name,
            "manifest": manifest_name,
            "frames": len(order),
            "cells": len(unique),
            "fps": fps,
            "loop": loop,
            "frame_variant": variant_summary,
            "body": composed["body"].state,
            "elements": composed["elements"],
            "frame_cells": order,
        },
    }


def require_distinct_names(names: list[str], *, origin: str) -> None:
    """A selection names each composite at most once. The one place that rule lives.

    Both entry forms run it: `parse_names` so a CLI typo is diagnosed before the
    run dir is touched, and `bake` so a direct `bake(names=[...])` call cannot
    reach the bake without it. Both ways of passing over a repeat lie about the
    result — de-duplicating answers a selection nobody asked for, and keeping the
    repeat writes one sheet while `layers.report.json` counts two composites, so
    `composite_count` no longer matches the sheets on disk (§5).
    """
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise SystemExit(
            f"{origin}: composite(s) {repeated} named more than once; a selection "
            f"names each composite at most once")


def bake(run_dir: Path | str, *, names: list[str] | None = None) -> dict[str, Any]:
    """Bake every declared composite (or just `names`) into `<run-dir>/layers/`.

    `names`, when given, selects declared composites and names each at most once
    (`require_distinct_names`). Returns the report that was written. Raises
    `SystemExit` listing every violation at once, having written nothing, when any
    composite cannot be baked as declared.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    # Derived-cache self-heal before consuming frames — the same real-time
    # contract compose_atlas follows, and for the same reason: a composite is
    # baked from the current engine's frames or not at all.
    heal_report = heal_run(run_dir)
    for note in heal_report["notes"]:
        print(f"[heal] {note}", file=sys.stderr)
    if heal_report["healed"]:
        print(f"[heal] re-derived stale rows: {', '.join(heal_report['healed'])}", file=sys.stderr)
    acquire_run_dir_lock(run_dir, "compose_layers")
    request = load_request(run_dir)
    if not has_layer_contract(request):
        raise SystemExit(
            f"{run_dir} is not a layer run: sprite-request.json declares no rig / track / "
            f"layers. Nothing was written (docs/layer-tracks.md §1)")
    require_valid_layer_request(request)

    declared = request.get("layers") or {}
    if not declared:
        raise SystemExit(
            f"{run_dir}: sprite-request.json declares a rig but no `layers` composite to bake")
    wanted = sorted(declared) if names is None else list(names)
    if not wanted:
        raise SystemExit(f"{run_dir}: no composite selected; declared: {sorted(declared)}")
    require_distinct_names(wanted, origin=f"{run_dir}: names=")
    unknown = [name for name in wanted if name not in declared]
    if unknown:
        raise SystemExit(
            f"{run_dir}: no such composite(s) {unknown}; declared: {sorted(declared)}")

    frames_manifest = require_frames_manifest(run_dir)
    rows_by_state = {row["state"]: row for row in frames_manifest.get("rows", [])}
    curation = load_curation(run_dir)
    cell_size = cell_geometry(request["cell"])
    snap_scale = pixel_snap_scale(request)

    errors: list[str] = []
    composed = []
    for name in wanted:
        result = _compose_one(run_dir, request, curation, rows_by_state, name, cell_size,
                              snap_scale, errors)
        if result is not None:
            composed.append(result)
    if errors:
        raise SystemExit(
            f"layer composition failed in {run_dir} (nothing written):\n  - "
            + "\n  - ".join(errors))

    rendered = [_render(run_dir, request, curation, result, cell_size) for result in composed]
    report = {
        "ok": True,
        "kind": REPORT_KIND,
        "engine": "component-row",
        "curation_applied": curation is not None,
        "cell": request["cell"],
        "rig_profile": (request.get("rig") or {}).get("profile"),
        "composite_count": len(rendered),
        "composites": [item["entry"] for item in rendered],
    }
    # One publish for the whole bake. The report is what says which sheets are
    # current (§5), so it lands with them or not at all — a sheet on disk that no
    # report names is exactly the leftover state this transaction prevents.
    payloads: dict[Path, bytes | str] = {}
    for item in rendered:
        payloads.update(item["payloads"])
    payloads[run_dir / LAYERS_DIRNAME / REPORT_NAME] = (
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (run_dir / LAYERS_DIRNAME).mkdir(parents=True, exist_ok=True)
    atomic_write_set(payloads)
    return report


def load_report(run_dir: Path | str) -> dict[str, Any] | None:
    """The composite bake report of a run dir, or `None` when nothing was baked.

    `None` means exactly one thing — no report file — so a consumer can treat it
    as "this run has no composites" without guessing. A file that exists but is
    not a layers report is an error, never an empty result.
    """
    path = Path(run_dir) / LAYERS_DIRNAME / REPORT_NAME
    if not path.is_file():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("kind") != REPORT_KIND:
        raise SystemExit(f"{path} is not a {REPORT_KIND} ({report.get('kind')!r})")
    return report


# --- CLI -------------------------------------------------------------------
# `sprite-gen compose-layers`, `python -m sprite_gen.compose.compose_layers` and
# `scripts/compose_layers.py` are three entry forms of ONE declaration: the CLI
# table points at `add_arguments` / `run` below rather than restating the flags,
# so a new option cannot reach one launch form and miss another.


def parse_names(value: str | None) -> list[str] | None:
    """`--names a,b` -> ["a", "b"]; unset -> None (every declared composite).

    An empty entry is a typo (`a,,b`, a trailing comma), never "all": silently
    dropping it would bake a different set than the one that was asked for. The
    repeat rule is `require_distinct_names`, run here so a CLI typo fails before
    the run dir is touched — `bake` runs the same check for every other caller.
    """
    if value is None:
        return None
    names = [part.strip() for part in value.split(",")]
    if not names or any(not name for name in names):
        raise SystemExit(
            f"--names expects a comma-separated list of composite names (got {value!r})")
    require_distinct_names(names, origin=f"--names {value!r}")
    return names


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True, type=Path,
                   help="run dir whose sprite-request.json declares rig / track / layers")
    p.add_argument("--names",
                   help="comma-separated composite names to bake (default: every declared one)")


def run(*, run_dir: Path, names: str | None = None) -> int:
    report = bake(run_dir, names=parse_names(names))
    print(json.dumps({
        "ok": report["ok"],
        "run_dir": str(Path(run_dir).expanduser().resolve()),
        "rig_profile": report["rig_profile"],
        "composites": [{
            "name": entry["name"],
            "sheet": f"{LAYERS_DIRNAME}/{entry['sheet']}",
            "manifest": f"{LAYERS_DIRNAME}/{entry['manifest']}",
            "frames": entry["frames"],
            "cells": entry["cells"],
            "clipped_pixels": sum(element["clipped_pixels"] for element in entry["elements"]),
        } for entry in report["composites"]],
        "report": f"{LAYERS_DIRNAME}/{REPORT_NAME}",
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bake a rig run's declared composite stacks into <run-dir>/layers/.")
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
