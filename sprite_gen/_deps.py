# SPDX-License-Identifier: Apache-2.0
"""The one module that imports NumPy — and the one place its absence is reported.

NumPy is a declared runtime dependency (`pyproject.toml`), materialized by the
repo-root `.venv` that README quickstart and CI build. An interpreter without it
is not a degraded environment this package works around: the extraction path
carries a byte-identity contract, so a second implementation of the same pixel
math would be two answers to one question (principle 6). There is therefore no
pure-Python fallback, and there is no `numpy = None` sentinel for callers to
branch on — importing this module either yields NumPy or raises.

A bare `import numpy` would already fail loudly, but `No module named 'numpy'`
tells an agent nothing about *which* interpreter it just used or how to get a
working one, and that is the failure this repo actually hit (homebrew `python3`
carried Pillow but not NumPy, so the surface looked provisioned). This module
re-raises with the interpreter it ran under and the exact install command.

Every other shipped module reaches NumPy through here, by importing `np` from
this module, and `sprite_gen/__init__.py` imports this module, so the gate fires
at package import — before any entrypoint gets far enough to do partial work.
`tests/test_numpy_dependency_gate.py` locks both halves.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def _missing_numpy_message() -> str:
    """The install path, spelled out for whoever is reading the traceback.

    Paths are resolved from this file rather than quoted from SKILL.md's
    `$ALEX_EXTENSIONS_DIR/sprite-gen/...` register so the command is
    copy-pasteable in the shell that just failed; in a canonical deployment the
    two name the same directory.
    """
    return (
        "sprite-gen requires NumPy and this interpreter cannot import it.\n"
        f"  interpreter: {sys.executable}\n"
        f"  sprite-gen:  {REPO_ROOT}\n"
        "Install the declared dependencies into the skill venv, then run the skill with "
        "that interpreter:\n"
        f"  {VENV_PYTHON} -m pip install -e {REPO_ROOT}\n"
        f"  {VENV_PYTHON} <script>.py ...\n"
        "There is no pure-Python fallback — the extraction path carries a byte-identity "
        "contract and will not run twice as fast-and-loose scalar code "
        "(SKILL.md '실행 인터프리터', SECURITY.md 'Current Dependency Surface')."
    )


try:
    import numpy
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in a subprocess
    raise ModuleNotFoundError(_missing_numpy_message()) from exc

np = numpy

__all__ = ["np", "numpy", "REPO_ROOT", "VENV_PYTHON"]
