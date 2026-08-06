# SPDX-License-Identifier: Apache-2.0
"""Importable core for the sprite-gen pipeline."""

# The declared NumPy dependency is gated here, at package import, so that every
# entrypoint — the 22 `scripts/*.py` wrappers, `-m sprite_gen.cli`, and any
# downstream importer — fails on a NumPy-less interpreter with the install path
# instead of a bare ModuleNotFoundError deeper in. See `sprite_gen/_deps.py`.
from . import _deps as _deps  # noqa: F401

# Every entrypoint prints a JSON report that echoes the prompt, and this codebase's
# own strings are full of non-ASCII (em dashes, Korean). On Windows, `sys.stdout`
# defaults to the ANSI codepage — cp949 on a Korean host — which cannot encode `—`,
# so a *successful* generation still dies at the print:
#   UnicodeEncodeError: 'cp949' codec can't encode character '—'
# The PNG is already on disk at that point, so the failure is pure reporting loss,
# but it breaks every caller that reads the report (correction loop, heal, curation
# server). Pin UTF-8 at package import, where the NumPy gate already lives.
# Deliberately conservative: an explicit PYTHONIOENCODING wins, streams already on
# UTF-8 are untouched, and anything that is not a reconfigurable text stream
# (pytest capture, a pipe wrapper) is left exactly as-is.
def _pin_utf8_stdio() -> None:
    import os
    import sys

    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None) or ""
        if encoding.lower().replace("-", "") == "utf8":
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass  # not a reconfigurable text stream — leave the caller's stream alone


_pin_utf8_stdio()

__all__ = [
    "anchor",
    "check_visible_magenta",
    "compose_atlas",
    "compose_cycle",
    "compose_gif",
    "correction_loop",
    "curation",
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
