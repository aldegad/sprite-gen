# SPDX-License-Identifier: Apache-2.0
"""docs/README.md is the documentation index: every leaf doc appears there exactly once,
grouped under the taxonomy, and no markdown link in the repo's docs points at a file that
does not exist. A doc added without an index line — or a link left behind by a rename —
fails here instead of surfacing as a dead link for a reader."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")


def _leaf_docs() -> set[str]:
    return {p.name for p in DOCS.glob("*.md") if p.name != "README.md"}


def test_docs_index_lists_every_leaf_doc_exactly_once() -> None:
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    linked = re.findall(r"\]\(([a-z0-9-]+\.md)\)", index)
    # the taxonomy sections start at the first "## " heading; the pipeline table above them
    # may name an entry doc a second time, the sections list each doc exactly once
    sections = index[index.index("\n## "):]
    per_section = re.findall(r"\]\(([a-z0-9-]+\.md)\)", sections)
    counts = {name: per_section.count(name) for name in set(per_section)}
    missing = sorted(_leaf_docs() - set(per_section))
    extra = sorted(set(linked) - _leaf_docs())
    dupes = sorted(n for n, c in counts.items() if c > 1)
    assert not missing, f"docs without an index line: {missing}"
    assert not extra, f"index lines for docs that do not exist: {extra}"
    assert not dupes, f"docs indexed more than once: {dupes}"


def _markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "SKILL.md", ROOT / "CHANGELOG.md", *DOCS.glob("*.md"), ROOT / "scripts" / "dev" / "README.md"]
    return [f for f in files if f.is_file()]


def test_relative_markdown_links_resolve() -> None:
    broken: list[str] = []
    for md in _markdown_files():
        text = md.read_text(encoding="utf-8")
        for target in LINK.findall(text):
            if re.match(r"^[a-z]+:", target) or target.startswith("/"):
                continue  # URLs, mailto, absolute paths are not repo links
            resolved = (md.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, "broken relative links:\n" + "\n".join(broken)
