# SPDX-License-Identifier: Apache-2.0
"""Regression tests for OS-neutral paths in machine-readable manifests."""

from pathlib import Path, PureWindowsPath

from sprite_gen.runio import relative_posix


def test_relative_posix_reproduces_windows_backslash_bug_without_windows() -> None:
    # PureWindowsPath is a pure stdlib path flavor: it performs Windows path
    # parsing on any host without touching the OS, so macOS can reproduce this.
    root = PureWindowsPath("C:/sprite/run")
    frame = root / "frames" / "walk" / "frame-0.png"

    assert str(frame.relative_to(root)) == r"frames\walk\frame-0.png"
    assert relative_posix(frame, root) == "frames/walk/frame-0.png"


def test_relative_posix_is_noop_for_posix_paths() -> None:
    root = Path("/sprite/run")
    frame = root / "frames" / "walk" / "frame-0.png"

    assert relative_posix(frame, root) == str(frame.relative_to(root))
