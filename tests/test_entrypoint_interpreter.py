# SPDX-License-Identifier: Apache-2.0
"""Deployment-interpreter contract for the agent-facing entrypoints.

The skill's dependencies (Pillow, NumPy) are declared in `pyproject.toml` and
materialized into the repo-root `.venv` — the same venv the README quickstart
and CI create. A global `python3` is whatever `$PATH` points at that day; on
macOS that is a PEP 668 externally-managed homebrew CPython whose packages were
installed by hand, so declaration and reality drift there (observed: Pillow
present, NumPy absent — one dependency installed made the surface *look*
provisioned).

Agent-facing commands are written with an absolute skill path (they run in
shells that never activated the venv), so those must name the venv interpreter
explicitly. Repo-relative examples (`python3 scripts/...`) belong to the README
quickstart register, where `source .venv/bin/activate` already resolved the same
interpreter — they are deliberately out of scope here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# An invocation is "agent-register" when it either targets a path anchored
# outside the current directory (`$ALEX_EXTENSIONS_DIR/...`, the `$SG` shorthand
# docs/curation.md defines, the elliptical `.../<script>.py` form) or runs this
# package via `-m sprite_gen...` — SKILL.md and docs/ are read by agents in
# shells that never activated the venv, so both forms need the explicit
# interpreter. Only the README quickstart may assume an activated shell.
_BARE_INTERPRETER = r"(?<![\w./-])python3?\s+"
_AGENT_REGISTER_BARE = re.compile(
    _BARE_INTERPRETER
    + r"(?:"
    + r"[\"']?(?:\$\{?(?:ALEX_EXTENSIONS_DIR|SG)\b|\.\.\./)"   # skill-absolute path
    + r"|-m\s+sprite_gen"                                       # or the package CLI
    + r")"
)


def _agent_facing_docs() -> list[Path]:
    return [ROOT / "SKILL.md", *sorted((ROOT / "docs").glob("*.md"))]


def test_agent_facing_docs_never_invoke_a_global_python() -> None:
    offenders: list[str] = []
    for path in _agent_facing_docs():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _AGENT_REGISTER_BARE.search(line):
                rel = path.relative_to(ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "agent-facing commands must name the venv interpreter "
        "($ALEX_EXTENSIONS_DIR/sprite-gen/.venv/bin/python), not a global python3:\n"
        + "\n".join(offenders)
    )


def test_skill_md_owns_the_interpreter_rule() -> None:
    """The rule has to live in SKILL.md, or the commands above are unexplained."""
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert ".venv/bin/python" in text, (
        "SKILL.md must name the venv interpreter as the skill's runtime")
    assert "실행 인터프리터" in text, (
        "SKILL.md lost its runtime-interpreter gate section")
    # No silent fallback (principle 6): there is exactly one interpreter, and a
    # missing venv is created or fails loudly — never quietly swapped for python3.
    assert "폴백 금지" in text, (
        "SKILL.md must keep the no-fallback clause for the interpreter rule")
