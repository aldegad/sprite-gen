# SPDX-License-Identifier: Apache-2.0
"""`runio.atomic_write_set` — publish a set of files, or publish none of them.

The single-file writers make each artifact appear whole, which is enough for a
stage that writes one file. A stage that publishes a set (the composite bake:
sheets, their manifests, and the report naming which sheets are current) needs
the set to appear together — a write error partway through must not leave a
published sheet that no report mentions.

What is pinned here is the observable failure: staging allocates everything
first, so a payload that cannot be written publishes nothing and leaves no temp
files behind. A crash between renames is out of reach of any userspace writer and
is documented as such, not tested as if it were prevented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sprite_gen.spec.runio import atomic_write_set


def _names(directory: Path) -> set[str]:
    return {path.name for path in directory.iterdir()}


def test_a_set_lands_together(tmp_path: Path) -> None:
    atomic_write_set({
        tmp_path / "sheet.png": b"\x89PNG-ish",
        tmp_path / "sheet.manifest.json": '{"ok": true}\n',
    })

    assert (tmp_path / "sheet.png").read_bytes() == b"\x89PNG-ish"
    assert (tmp_path / "sheet.manifest.json").read_text(encoding="utf-8") == '{"ok": true}\n'
    assert _names(tmp_path) == {"sheet.png", "sheet.manifest.json"}


def test_a_failing_payload_publishes_nothing_and_leaves_no_temp_files(tmp_path: Path) -> None:
    """The second payload cannot be staged, so the first must not appear either."""
    (tmp_path / "report.json").write_text("previous\n", encoding="utf-8")

    with pytest.raises(OSError):
        atomic_write_set({
            tmp_path / "sheet.png": b"new",
            tmp_path / "missing-dir" / "other.png": b"new",
            tmp_path / "report.json": "new\n",
        })

    assert _names(tmp_path) == {"report.json"}
    assert (tmp_path / "report.json").read_text(encoding="utf-8") == "previous\n"


def test_an_existing_target_is_replaced_not_appended(tmp_path: Path) -> None:
    (tmp_path / "sheet.png").write_bytes(b"old-and-longer")

    atomic_write_set({tmp_path / "sheet.png": b"new"})

    assert (tmp_path / "sheet.png").read_bytes() == b"new"
