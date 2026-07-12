# SPDX-License-Identifier: Apache-2.0
"""Shared curation sidecar logic for sprite-gen.

`curation.json` is an optional, non-destructive sidecar in a run directory. It
records which extracted frames a human selected (and in what order), which
frames were logically deleted, plus a per-frame affine transform. The original
`frames/<state>/frame-N.png` files are never rewritten — the atlas/GIF compose
steps read this sidecar and apply the transform at compose time, so a curation
decision is always reversible by editing `curation.json`.

This module is the single source of truth for the curation schema and for how a
transform is applied, so the webview server and the compose scripts can never
drift apart.

Schema (`curation.json`):

    {
      "version": 1,
      "kind": "sprite-gen-curation",
      "run_revision": "9f3c1a0b7e2d4c58",    # stamped at write = the frame generation this
                                              #   curation was made for. If the frames are
                                              #   later regenerated (re-import/re-extract),
                                              #   load_curation detects the mismatch and
                                              #   ignores this sidecar (all-frames default)
                                              #   so stale edits never apply to new frames.
      "pixel_perfect": true,                 # optional; false -> compose reads the
                                              #   frame-N.plain.png variant (pre-
                                              #   pixel-perfect). absent/true -> the
                                              #   canonical frame-N.png. Only
                                              #   meaningful when extraction saved
                                              #   both variants (fit.pixel_perfect).
      "states": {
        "<state>": {
          "selected": [0, 1, 2, 3],          # 0-based frame indices, in play order
          "deleted": [4],                    # optional 0-based frame indices
                                               #   excluded from UI rows and bake.
          "order": [0, 1, 2, 3, 4, 5],        # optional, webview-owned; full display
                                               #   order (sequence then candidate pool).
                                               #   Restores the row arrangement on reload.
                                               #   Consumers key off `selected`; ignored here.
          "transforms": {                      # keyed by 0-based frame index (string)
            "0": {"rotate": 0.0, "scale": 1.0, "dx": 0, "dy": 0}
          }
        }
      }
    }

Defaults when absent (explicit, not a silent fallback):
- no `curation.json`           -> every state uses all extracted frames in order, identity transform.
- state missing from sidecar   -> same all-frames default for that state.
- `selected` missing/empty     -> all non-deleted frames in extraction order.
- `deleted` missing             -> no frames are deleted.
- `order` missing               -> webview rebuilds arrangement from `selected`; bake is unaffected (state_plan reads `selected`, never `order`).
- frame missing from transforms -> identity transform.

`rotate` is in degrees, counter-clockwise positive (PIL convention).
`scale` is a multiplier about the frame center.
`dx`/`dy` are pixel offsets inside the cell, +x right, +y down.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image

CURATION_FILENAME = "curation.json"
SCHEMA_VERSION = 1
IDENTITY = {"rotate": 0.0, "scale": 1.0, "dx": 0, "dy": 0, "shx": 0.0, "shy": 0.0, "flipX": 0}

# Generation-material chip roles for imported `_refs/<role>-<name>.png` files
# (run-contract.md §4). Single source shared by the importer (fail-loud on an
# unknown role) and the webview server (render), so neither silently invents a role.
IMPORTED_REF_ROLES = ("anchor", "basis", "guide")


def imported_ref_role(filename: str) -> str | None:
    """Role for an imported `_refs` filename `<role>-<name>.png`, or None if the
    prefix is not a known role. Callers must handle None explicitly (the importer
    fails loud, the server skips) — never silently relabel an unknown role."""
    stem = Path(filename).stem
    prefix = stem.split("-", 1)[0] if "-" in stem else ""
    return prefix if prefix in IMPORTED_REF_ROLES else None


def curation_path(run_dir: Path) -> Path:
    return run_dir / CURATION_FILENAME


def frame_variant(curation: dict[str, Any] | None) -> str:
    """Which extracted frame variant consumers read: 'pixel' or 'plain'.

    'plain' only when the sidecar explicitly opts out of pixel-perfect
    (`pixel_perfect: false` — the curator's top-right checkbox). Absent sidecar
    or absent/true field -> the canonical pixel-perfected frames."""
    if curation and curation.get("pixel_perfect") is False:
        return "plain"
    return "pixel"


def frame_filename(index: int, variant: str = "pixel") -> str:
    """Frame file name for a variant. 'pixel' = canonical frame-N.png; 'plain'
    = the pre-pixel-perfect twin saved by extraction when fit.pixel_perfect."""
    if variant == "plain":
        return f"frame-{index}.plain.png"
    return f"frame-{index}.png"


def run_revision(run_dir: Path) -> str:
    """Frame-content fingerprint of the run's current generation: the request + frames
    manifest bytes plus each canonical frame file's name/size/mtime. It changes whenever
    the frames are (re)written (`--force` re-import, re-extract), so a curation sidecar
    stamped for a prior generation is detected as stale. Single source of run identity for
    the server, compose, export, and the webview."""
    h = hashlib.sha256()
    for name in ("sprite-request.json", "frames/frames-manifest.json"):
        try:
            h.update((run_dir / name).read_bytes())
        except OSError:
            h.update(b"\0")
    frames_root = run_dir / "frames"
    if frames_root.is_dir():
        for state_dir in sorted(d for d in frames_root.iterdir() if d.is_dir()):
            for frame in sorted(state_dir.glob("frame-*.png")):
                if frame.name.endswith(".plain.png"):
                    continue
                try:
                    st = frame.stat()
                    h.update(f"{state_dir.name}/{frame.name}:{st.st_size}:{st.st_mtime_ns}".encode())
                except OSError:
                    pass
    return h.hexdigest()[:16]


def load_curation(run_dir: Path) -> dict[str, Any] | None:
    """Return the parsed sidecar, or None when there is no curation.json OR when the stored
    curation is **stale** — its `run_revision` stamp no longer matches the current frame
    generation (a re-import/re-extract regenerated the frames under it). A stale sidecar is
    ignored (the all-frames identity default) rather than silently applied to frames it was
    not made for; the skip is reported on stderr (observable, not a silent fallback). This is
    the single gate every consumer (server, compose, export, GIF) passes through."""
    path = curation_path(run_dir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != "sprite-gen-curation":
        raise SystemExit(f"{path} is not a sprite-gen-curation file")
    stamped = data.get("run_revision")
    if stamped is not None and stamped != run_revision(run_dir):
        print(f"[curation] ignoring stale {CURATION_FILENAME} (run_revision {stamped} != current "
              f"frame generation — frames were regenerated under it): {run_dir}", file=sys.stderr)
        return None
    return data


def normalize_transform(raw: Any) -> dict[str, float]:
    """Coerce a stored transform into a full {rotate, scale, dx, dy, shx, shy, flipX} dict."""
    if not isinstance(raw, dict):
        return dict(IDENTITY)
    return {
        "rotate": float(raw.get("rotate", 0.0)),
        "scale": float(raw.get("scale", 1.0)),
        "dx": float(raw.get("dx", 0)),
        "dy": float(raw.get("dy", 0)),
        "shx": float(raw.get("shx", 0.0)),
        "shy": float(raw.get("shy", 0.0)),
        # (Alex 2026-05-28) flipX: 0 | 1 — horizontal mirror. Image-gen 결과가 좌우
        # 반대로 나올 때 frame 별로 거울 반전. matrix 마지막에 diag(-1, 1) 곱.
        "flipX": 1 if raw.get("flipX") else 0,
    }


def is_identity(transform: dict[str, float]) -> bool:
    return (
        abs(transform["rotate"]) < 1e-6
        and abs(transform["scale"] - 1.0) < 1e-6
        and abs(transform["dx"]) < 1e-6
        and abs(transform["dy"]) < 1e-6
        and abs(transform.get("shx", 0.0)) < 1e-6
        and abs(transform.get("shy", 0.0)) < 1e-6
        and not transform.get("flipX", 0)
    )


def normalize_frame_indices(raw: Any, default_count: int) -> list[int]:
    """Return unique 0-based frame indices that are valid for this state."""
    if not isinstance(raw, list):
        return []
    indices: list[int] = []
    seen: set[int] = set()
    for value in raw:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < default_count and index not in seen:
            indices.append(index)
            seen.add(index)
    return indices


def transform_matrix(t: dict[str, float]) -> tuple[float, float, float, float]:
    """Forward 2x2 linear matrix (M00, M01, M10, M11) = Rotate · Shear · Scale · FlipX.

    Screen y-down. Positive `rotate` is counter-clockwise. This exact matrix is
    mirrored in the webview (CSS `matrix()` + canvas), so what the user aligns to
    the ground grid is what bakes — no preview/bake drift. flipX (when set)
    multiplies the right-most diag(-1, 1) so column-0 의 부호가 반전된다.
    """
    rr = math.radians(t["rotate"])
    c, sn = math.cos(rr), math.sin(rr)
    s, shx, shy = t["scale"], t.get("shx", 0.0), t.get("shy", 0.0)
    m00 = s * (c + sn * shy)
    m01 = s * (c * shx + sn)
    m10 = s * (-sn + c * shy)
    m11 = s * (c - sn * shx)
    if t.get("flipX"):
        m00, m10 = -m00, -m10
    return m00, m01, m10, m11


def state_plan(
    curation: dict[str, Any] | None,
    state: str,
    default_count: int,
) -> tuple[list[int], dict[int, dict[str, float]]]:
    """Resolve the ordered frame indices and per-frame transforms for a state.

    Returns (ordered_zero_based_indices, {frame_index: transform}).
    """
    default_order = list(range(default_count))
    if not curation:
        return default_order, {}
    entry = curation.get("states", {}).get(state)
    if not isinstance(entry, dict):
        return default_order, {}

    deleted = set(normalize_frame_indices(entry.get("deleted"), default_count))
    default_visible = [index for index in default_order if index not in deleted]
    selected = entry.get("selected")
    if isinstance(selected, list) and selected:
        # tolerate a hand-edited / corrupt sidecar: skip non-integer,
        # out-of-range, duplicate, or deleted entries instead of crashing.
        ordered = [
            index
            for index in normalize_frame_indices(selected, default_count)
            if index not in deleted
        ]
        if not ordered:
            ordered = default_visible
    else:
        ordered = default_visible

    transforms_raw = entry.get("transforms", {})
    transforms: dict[int, dict[str, float]] = {}
    if isinstance(transforms_raw, dict):
        for key, value in transforms_raw.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            transform = normalize_transform(value)
            if not is_identity(transform):
                transforms[index] = transform
    return ordered, transforms


def apply_transform(
    frame: Image.Image,
    transform: dict[str, float] | None,
    cell_size: tuple[int, int],
) -> Image.Image:
    """Apply scale/shear/rotate (about center) + translate, into a fresh cell.

    Rendered with one inverse-affine `Image.transform` into the cell, so cell
    size is preserved and the atlas layout never changes. Non-destructive: the
    source frame is not modified. The forward matrix matches `transform_matrix`,
    which the webview uses for its preview, so alignment to the ground grid is
    faithful to the bake.
    """
    transform = normalize_transform(transform) if transform else dict(IDENTITY)
    if is_identity(transform) and frame.size == cell_size:
        return frame.convert("RGBA")

    src = frame.convert("RGBA")
    cw, ch = cell_size
    m00, m01, m10, m11 = transform_matrix(transform)
    det = m00 * m11 - m01 * m10
    if abs(det) < 1e-6:
        det = 1e-6 if det >= 0 else -1e-6
    # inverse 2x2 (output -> input)
    ia, ib = m11 / det, -m01 / det
    id_, ie = -m10 / det, m00 / det
    cin_x, cin_y = src.width / 2, src.height / 2
    cout_x, cout_y = cw / 2 + transform["dx"], ch / 2 + transform["dy"]
    c = -(ia * cout_x + ib * cout_y) + cin_x
    f = -(id_ * cout_x + ie * cout_y) + cin_y
    return src.transform((cw, ch), Image.AFFINE, (ia, ib, c, id_, ie, f), resample=Image.BICUBIC)


def empty_curation() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "kind": "sprite-gen-curation", "states": {}}
