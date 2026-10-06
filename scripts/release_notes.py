# SPDX-License-Identifier: Apache-2.0
"""Render the GitHub release body for one tag out of `CHANGELOG.md`.

`scripts/release_publish.py` calls this after a `v*` tag is pushed, so the release
page is built from the file that already describes the release instead of from whatever
the person cutting the tag remembers to paste. A tag with no `## vX.Y.Z` section — or a
section with no content — is a failure here, not an empty release page.

A release body is not rendered relative to the repository, so a relative
`docs/assets/<name>.gif` link target 404s on the release page. Link targets are
rewritten to the raw URL on the release branch, and the GIFs the section names are also
reported as assets to attach, so the page keeps a frozen copy of what it shows while the
raw URL follows the branch. Two ways of naming one, because the CHANGELOG uses both:

* `docs/assets/<name>.gif` — an explicit path. Attached, and a path that is not in the
  checkout fails the run rather than publishing a page with a dead image.
* a bare `<name>.gif` — attached only when `docs/assets/<name>.gif` exists here. A bare
  name that resolves to nothing is prose, not a missing asset.

Standard library only: this runs on a bare CI checkout, before the package is installed.

    .venv/bin/python scripts/release_notes.py --tag v2.5.3 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "aldegad/sprite-gen"
DEFAULT_BRANCH = "main"

# Every GIF the section names, with or without its `docs/assets/` prefix. The optional
# prefix is greedy, so an explicit path is one match and not a bare name in disguise.
ASSET_REF = re.compile(r"(?P<prefix>docs/assets/)?(?P<name>[\w.-]+\.gif)")
# The same path where it is actually a link target: `](path)`, `src="path"`, `href='path'`.
LINK_TARGET = re.compile(r"""(?P<pre>\]\(|(?:src|href)=["'])(?P<path>docs/assets/[\w.-]+\.gif)""")
# A code fence, either spelling. Only the character that opened one can close it.
FENCE = re.compile(r"^(?P<marker>`{3,}|~{3,})")


class ReleaseNotesError(RuntimeError):
    """The CHANGELOG cannot answer for this tag. Never recoverable by guessing."""


def version_of(tag: str) -> str:
    """`v2.5.3` -> `2.5.3`. The tag policy is a bare `vX.Y.Z`, no codename prefix."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+[\w.-]*", tag):
        raise ReleaseNotesError(f"tag {tag!r} is not a release tag (expected vX.Y.Z)")
    return tag[1:]


def _heading_pattern(version: str) -> re.Pattern[str]:
    # `(?![\w.-])` so v2.5.3 matches neither the v2.5.30 heading sitting above it nor a
    # v2.5.3-rc1 pre-release section: `version_of` accepts a `-` suffix as a tag, so the
    # heading guard has to refuse one too, or the rc notes become the release page.
    return re.compile(rf"^##\s+v{re.escape(version)}(?![\w.-])")


def _heading_lines(lines: list[str]) -> list[int]:
    """Indices of real `## ` headings: a `## …` inside a fenced block is code, not a section.

    Markdown fences both ways, and a block is closed only by the character that opened it:
    a ``` line inside a `~~~` block is the block's content. Reading one spelling as a fence
    and the other as prose would keep the rule for quoted shell and drop it for quoted
    markdown — which is the CHANGELOG entry most likely to contain a `## vX.Y.Z` line.
    """
    fence: str | None = None
    found: list[int] = []
    for i, line in enumerate(lines):
        opened = FENCE.match(line)
        if opened:
            marker = opened.group("marker")[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
        elif fence is None and line.startswith("## "):
            found.append(i)
    return found


def extract_section(changelog: str, version: str) -> tuple[str, str]:
    """Return (heading text, section body) for `## v<version> …`.

    The body runs to the next `## ` heading or to the end of the file, so the newest
    section and the oldest one are read the same way.
    """
    pattern = _heading_pattern(version)
    lines = changelog.splitlines()
    headings = _heading_lines(lines)
    start = next((i for i in headings if pattern.match(lines[i])), None)
    if start is None:
        known = [m.group(1) for m in (re.match(r"##\s+(v[\w.-]+)", lines[i]) for i in headings) if m]
        raise ReleaseNotesError(
            f"CHANGELOG.md has no `## v{version}` section. "
            f"Newest sections: {', '.join(known[:5]) or '(none)'}"
        )
    end = next((j for j in headings if j > start), len(lines))
    heading = lines[start].lstrip("#").strip()
    body = "\n".join(lines[start + 1:end]).strip("\n")
    if not body.strip():
        raise ReleaseNotesError(f"the `## v{version}` section is empty; a release page needs notes")
    return heading, body


def referenced_assets(body: str, assets_root: Path) -> list[str]:
    """Repo-relative GIF paths to attach, first mention first, deduplicated.

    `assets_root` is the checkout the release is built from: it decides whether a bare
    filename is one of this repository's clips or just a word ending in `.gif`.
    """
    seen: dict[str, None] = {}
    missing: list[str] = []
    for match in ASSET_REF.finditer(body):
        path = f"docs/assets/{match.group('name')}"
        if (assets_root / path).is_file():
            seen.setdefault(path, None)
        elif match.group("prefix"):
            missing.append(path)
    if missing:
        raise ReleaseNotesError(
            "the release notes name GIFs that are not in this checkout: "
            + ", ".join(dict.fromkeys(missing))
        )
    return list(seen)


def rewrite_asset_links(body: str, repo: str, branch: str) -> str:
    """Point link targets at raw.githubusercontent.com; leave prose paths as paths."""
    base = f"https://raw.githubusercontent.com/{repo}/{branch}/"
    return LINK_TARGET.sub(lambda m: m.group("pre") + base + m.group("path"), body)


def changelog_anchor(heading: str) -> str:
    """GitHub's heading slug: lowercase, punctuation dropped, spaces to hyphens."""
    slug = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return slug.replace(" ", "-")


def footer(tag: str, heading: str, repo: str, branch: str) -> str:
    """The closing block every release page has carried: how to install, where the rest is."""
    return (
        "Install or update:\n"
        "\n"
        "```sh\n"
        f'pip install --upgrade "sprite-gen @ git+https://github.com/{repo}.git@{tag}"\n'
        "```\n"
        "\n"
        "The wheel, the source archive and `SHA256SUMS` are attached. Full notes in "
        f"[CHANGELOG.md](https://github.com/{repo}/blob/{branch}/CHANGELOG.md#{changelog_anchor(heading)})."
    )


def render(
    changelog_text: str,
    tag: str,
    assets_root: Path,
    *,
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
    with_footer: bool = True,
) -> dict[str, object]:
    """The whole release page as data: title, body, and the GIFs to attach."""
    heading, section = extract_section(changelog_text, version_of(tag))
    assets = referenced_assets(section, assets_root)
    body = rewrite_asset_links(section, repo, branch)
    if with_footer:
        body = f"{body}\n\n{footer(tag, heading, repo, branch)}"
    return {"tag": tag, "title": heading, "body": body, "assets": assets}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True, help="release tag, e.g. v2.5.3")
    parser.add_argument("--changelog", type=Path, default=REPO_ROOT / "CHANGELOG.md")
    parser.add_argument("--assets-root", type=Path, default=None,
                        help="where docs/assets/ is resolved (default: the changelog's directory)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name for the raw and blob URLs")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="branch the raw asset URLs point at")
    parser.add_argument("--no-footer", action="store_true", help="body is the CHANGELOG section alone")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="write title.txt, body.md and assets.txt here")
    parser.add_argument("--json", action="store_true", help="print the rendered page as JSON")
    args = parser.parse_args(argv)

    try:
        text = args.changelog.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 2
    try:
        page = render(text, args.tag, args.assets_root or args.changelog.resolve().parent,
                      repo=args.repo, branch=args.branch, with_footer=not args.no_footer)
    except ReleaseNotesError as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 2

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "title.txt").write_text(str(page["title"]) + "\n", encoding="utf-8")
        (args.out_dir / "body.md").write_text(str(page["body"]) + "\n", encoding="utf-8")
        (args.out_dir / "assets.txt").write_text(
            "".join(f"{path}\n" for path in page["assets"]), encoding="utf-8")
    if args.json:
        print(json.dumps(page, ensure_ascii=False, indent=2))
    elif not args.out_dir:
        print(page["body"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
