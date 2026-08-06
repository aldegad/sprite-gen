# SPDX-License-Identifier: Apache-2.0
"""Importable core for the sprite-gen pipeline."""

# The declared NumPy dependency is gated here, at package import, so that every
# entrypoint — the 22 `scripts/*.py` wrappers, `-m sprite_gen.cli`, and any
# downstream importer — fails on a NumPy-less interpreter with the install path
# instead of a bare ModuleNotFoundError deeper in. See `sprite_gen/_deps.py`.
from sprite_gen import _deps as _deps  # noqa: F401

# Domain subpackages (physical taxonomy). Module domains: sprite_gen/_modules.py.
__all__ = [
    "spec",     # run dir IO, request/layout, migrations
    "gen",      # provider-backed image generation
    "frames",   # sheet -> frames extraction (+ inverse unpack)
    "curate",   # curation decisions / sidecar / anchors
    "compose",  # bake atlas / cycle / gif / layers / export
    "effects",  # per-frame transforms: breathe, recolor, interpolate, reroll
    "qa",       # inspect / score / correction-loop / preview
    "serve",    # webview servers (curation, compose) + SPAs
    "util",     # shared helpers
]
