# SPDX-License-Identifier: Apache-2.0
"""Importable core for the sprite-gen pipeline."""

# The declared NumPy dependency is gated here, at package import, so that every
# entrypoint — the 22 `scripts/*.py` wrappers, `-m sprite_gen.cli`, and any
# downstream importer — fails on a NumPy-less interpreter with the install path
# instead of a bare ModuleNotFoundError deeper in. See `sprite_gen/_deps.py`.
from . import _deps as _deps  # noqa: F401

__all__ = [
    "anchor",
    "check_visible_magenta",
    "compose_atlas",
    "compose_cycle",
    "compose_gif",
    "correction_loop",
    "curation",
    "export_aseprite",
    "export_pngs",
    "extract",
    "gen",
    "generate_image",
    "gif_utils",
    "inspect",
    "prepare",
    "preview",
    "runio",
    "score",
    "slice_sheet",
    "migrate_breathe",
    "unpack_atlas",
]
