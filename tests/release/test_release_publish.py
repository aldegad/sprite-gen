# SPDX-License-Identifier: Apache-2.0
"""The release publisher's decision: publish once, never twice, never on a guess.

`gh` is replaced by a stub that records every call and keeps its "is there a release?"
answer in a file, so a second run sees exactly what a real re-run would see. Fixtures are
synthetic: a throwaway changelog, a throwaway repository name, one-pixel GIFs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLISH = ROOT / "scripts" / "release_publish.py"
GIF_BYTES = bytes.fromhex("474946383961010001008000000000ffffff21f90401000000002c00000000010001000002024401003b")

CHANGELOG = """\
# Changelog

## v1.3.0 - Showcase

- One line, and a clip: `docs/assets/wave-cube.gif`.
"""

GH_STUB = '''\
#!/usr/bin/env python3
"""Stand-in for `gh`: answers from a state file and records what it was asked."""
import json, os, sys
from pathlib import Path

state = Path(os.environ["GH_STUB_STATE"])
Path(os.environ["GH_STUB_LOG"]).open("a", encoding="utf-8").write(" ".join(sys.argv[1:]) + "\\n")
verb = sys.argv[1:3]

if verb == ["release", "view"]:
    if os.environ.get("GH_STUB_UNREACHABLE"):
        print("HTTP 503: could not reach the API", file=sys.stderr)
        sys.exit(1)
    if state.exists():
        print(json.dumps({"tagName": sys.argv[3]}))
        sys.exit(0)
    print("release not found", file=sys.stderr)
    sys.exit(1)

if verb == ["release", "create"]:
    state.write_text("published", encoding="utf-8")
    print("https://github.com/example/example/releases/tag/" + sys.argv[3])
    sys.exit(0)

print("the publisher called an unexpected gh verb: " + " ".join(sys.argv[1:]), file=sys.stderr)
sys.exit(64)
'''


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.checkout = tmp_path / "checkout"
        (self.checkout / "docs" / "assets").mkdir(parents=True)
        (self.checkout / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        (self.checkout / "docs" / "assets" / "wave-cube.gif").write_bytes(GIF_BYTES)
        self.state = tmp_path / "release-exists"
        self.log = tmp_path / "gh-calls.log"
        self.log.write_text("", encoding="utf-8")
        self.gh = tmp_path / "gh"
        self.gh.write_text(GH_STUB, encoding="utf-8")
        self.gh.chmod(0o755)

    def run(self, *args: str, unreachable: bool = False) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "GH_STUB_STATE": str(self.state), "GH_STUB_LOG": str(self.log)}
        env.pop("GITHUB_STEP_SUMMARY", None)
        if unreachable:
            env["GH_STUB_UNREACHABLE"] = "1"
        return subprocess.run(
            [sys.executable, str(PUBLISH), "--tag", "v1.3.0", "--repo", "example/example",
             "--checkout", str(self.checkout), "--gh", str(self.gh), "--skip-build", *args],
            capture_output=True, text=True, env=env)

    @property
    def calls(self) -> list[str]:
        return [line for line in self.log.read_text(encoding="utf-8").splitlines() if line]

    def published(self) -> bool:
        return self.state.exists()


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_a_missing_release_is_published_with_its_notes_and_gif(harness: Harness) -> None:
    proc = harness.run()
    assert proc.returncode == 0, proc.stderr
    created = [c for c in harness.calls if c.startswith("release create")]
    assert len(created) == 1, harness.calls
    assert "v1.3.0 - Showcase" in created[0]
    assert "wave-cube.gif" in created[0], "the showcase clip is attached, not only linked"
    assert harness.published()


def test_a_second_run_changes_nothing(harness: Harness) -> None:
    """Idempotency: re-running a tag — a retried job, a re-pushed tag — must not rewrite
    a published page. The first run creates, the second one only looks."""
    assert harness.run().returncode == 0
    harness.log.write_text("", encoding="utf-8")

    second = harness.run()

    assert second.returncode == 0, second.stderr
    assert "already has a release page" in second.stdout
    assert [c.split()[1] for c in harness.calls] == ["view"], harness.calls
    assert not [c for c in harness.calls if "create" in c or "upload" in c or "edit" in c]


def test_a_dry_run_creates_nothing_and_shows_what_it_would_create(harness: Harness) -> None:
    proc = harness.run("--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "DRY RUN" in proc.stdout and "v1.3.0 - Showcase" in proc.stdout
    assert "wave-cube.gif" in proc.stdout
    assert [c.split()[1] for c in harness.calls] == ["view"], harness.calls
    assert not harness.published()


def test_a_dry_run_on_a_released_tag_reports_the_no_op(harness: Harness) -> None:
    harness.state.write_text("published", encoding="utf-8")
    proc = harness.run("--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "already has a release page" in proc.stdout
    assert [c.split()[1] for c in harness.calls] == ["view"]


def test_an_unreadable_answer_is_not_read_as_a_missing_release(harness: Harness) -> None:
    """No silent fallback: a 503 must not become "no release yet" and then a create."""
    proc = harness.run(unreachable=True)
    assert proc.returncode == 2
    assert "could not tell whether" in proc.stderr
    assert not [c for c in harness.calls if "create" in c]
    assert not harness.published()


def test_a_missing_changelog_section_stops_before_gh_is_called(harness: Harness) -> None:
    proc = harness.run("--tag", "v9.9.9")
    assert proc.returncode == 2
    assert "v9.9.9" in proc.stderr
    assert harness.calls == [], "the notes are rendered before anything is asked of gh"


def test_a_gif_named_in_the_notes_but_absent_stops_the_release(harness: Harness) -> None:
    (harness.checkout / "docs" / "assets" / "wave-cube.gif").unlink()
    proc = harness.run()
    assert proc.returncode == 2
    assert "wave-cube.gif" in proc.stderr
    assert not harness.published()


def test_a_dist_directory_with_stale_archives_is_refused(harness: Harness, tmp_path: Path) -> None:
    """Everything in the dist dir is attached, so a leftover archive would ride along."""
    stale = tmp_path / "dist"
    stale.mkdir()
    (stale / "sprite_gen-0.0.1-py3-none-any.whl").write_bytes(b"stale")
    proc = subprocess.run(
        [sys.executable, str(PUBLISH), "--tag", "v1.3.0", "--repo", "example/example",
         "--checkout", str(harness.checkout), "--gh", str(harness.gh), "--dist-dir", str(stale)],
        capture_output=True, text=True,
        env={**os.environ, "GH_STUB_STATE": str(harness.state), "GH_STUB_LOG": str(harness.log)})
    assert proc.returncode == 2
    assert "not empty" in proc.stderr
    assert not harness.published()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _tagged(harness: Harness) -> str:
    """Make the throwaway checkout a repository whose v1.3.0 tag is its only commit."""
    _git(harness.checkout, "init", "-q")
    _git(harness.checkout, "add", "-A")
    _git(harness.checkout, "commit", "-q", "-m", "release")
    _git(harness.checkout, "tag", "v1.3.0")
    return _git(harness.checkout, "rev-parse", "HEAD")


def _lane_artifact(directory: Path, source_sha: str) -> Path:
    """What `scripts/linux_lane.sh --artifact` leaves: a wheel, an sdist, SHA256SUMS, SOURCE_SHA."""
    import hashlib

    directory.mkdir()
    lines = []
    for name, body in (("sprite_gen-1.3.0-py3-none-any.whl", b"wheel"),
                       ("sprite_gen-1.3.0.tar.gz", b"sdist")):
        (directory / name).write_bytes(body)
        lines.append(f"{hashlib.sha256(body).hexdigest()}  {name}\n")
    (directory / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
    (directory / "SOURCE_SHA").write_text(source_sha + "\n", encoding="utf-8")
    return directory


def _run_prebuilt(harness: Harness, prebuilt: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PUBLISH), "--tag", "v1.3.0", "--repo", "example/example",
         "--checkout", str(harness.checkout), "--gh", str(harness.gh), "--prebuilt", str(prebuilt),
         *args],
        capture_output=True, text=True,
        env={**os.environ, "GH_STUB_STATE": str(harness.state), "GH_STUB_LOG": str(harness.log)})


def test_a_lane_build_of_the_tag_commit_is_attached_without_building(harness: Harness, tmp_path: Path) -> None:
    prebuilt = _lane_artifact(tmp_path / "lane", _tagged(harness))
    proc = _run_prebuilt(harness, prebuilt)
    assert proc.returncode == 0, proc.stderr
    created = [c for c in harness.calls if c.startswith("release create")]
    assert len(created) == 1, harness.calls
    for name in ("sprite_gen-1.3.0-py3-none-any.whl", "sprite_gen-1.3.0.tar.gz", "SHA256SUMS", "wave-cube.gif"):
        assert name in created[0], name
    assert not (harness.checkout / "dist").exists(), "nothing is built where the release is published"


def test_a_lane_build_of_another_commit_is_refused(harness: Harness, tmp_path: Path) -> None:
    _tagged(harness)
    prebuilt = _lane_artifact(tmp_path / "lane", "0" * 40)
    proc = _run_prebuilt(harness, prebuilt)
    assert proc.returncode == 2
    assert "was built from" in proc.stderr
    assert not harness.published()


def test_a_lane_build_whose_archive_changed_is_refused(harness: Harness, tmp_path: Path) -> None:
    prebuilt = _lane_artifact(tmp_path / "lane", _tagged(harness))
    (prebuilt / "sprite_gen-1.3.0.tar.gz").write_bytes(b"sdist, changed after the lane")
    proc = _run_prebuilt(harness, prebuilt)
    assert proc.returncode == 2
    assert "does not match" in proc.stderr
    assert not harness.published()


def test_a_lane_artifact_with_a_stray_file_is_refused(harness: Harness, tmp_path: Path) -> None:
    """Everything in the directory would be attached, so it must hold exactly the lane's four files."""
    prebuilt = _lane_artifact(tmp_path / "lane", _tagged(harness))
    (prebuilt / "sprite_gen-0.0.1-py3-none-any.whl").write_bytes(b"stale")
    proc = _run_prebuilt(harness, prebuilt)
    assert proc.returncode == 2
    assert "exactly one wheel" in proc.stderr
    assert not harness.published()


def test_prebuilt_does_not_combine_with_a_build_here(harness: Harness, tmp_path: Path) -> None:
    prebuilt = _lane_artifact(tmp_path / "lane", _tagged(harness))
    proc = _run_prebuilt(harness, prebuilt, "--skip-build")
    assert proc.returncode == 2
    assert "--prebuilt" in proc.stderr
    assert harness.calls == []
