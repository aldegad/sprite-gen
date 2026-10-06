# SPDX-License-Identifier: Apache-2.0
"""A fleck far from the body is not the subject touching the edge.

* `video-frames` erases a speck — a small piece far from the body — before the edge check reads the frame.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import align, canvas, frames as frames_mod, gait_fallback, loop
from tests.video import test_gait_fallback as walks

MAGENTA = (255, 0, 255)
BODY = (180, 60, 30)
FLECK = (255, 180, 220)  # a pale pink the magenta matte keeps, not the key's hue


# --- the key stage's specks ---------------------------------------------------------------------------------


def _raw(tmp_path: Path, n: int, paint) -> list[Path]:
    """`n` frames of a 30 x 70 body on magenta, `paint(k, image)` adding to frame k."""
    d = tmp_path / "raw"
    d.mkdir()
    files = []
    for k in range(n):
        image = Image.new("RGB", (120, 100), MAGENTA)
        image.paste(BODY, (20, 20, 50, 90))
        paint(k, image)
        files.append(d / f"frame-{k:04d}.png")
        image.save(files[-1])
    return files


def test_a_fleck_far_from_the_body_is_erased_before_the_edge_check(tmp_path):
    # Five pale pixels crossing the right edge band in one frame, 66 px from a body 70 px tall: the edge
    # check read them as the subject framed too tight, and the clip was sent back for a wider canvas.
    def fleck(k, image):
        if k == 1:
            image.paste(FLECK, (116, 50, 119, 51))
            image.paste(FLECK, (117, 51, 119, 52))
    files = _raw(tmp_path, 3, fleck)
    report = frames_mod.key_frames(files, tmp_path / "keyed", key="magenta")
    assert report["edge_contacts"] == [] and report["edge_policy"] == "refuse"
    assert [row["specks"] for row in report["rows"]] == [0, 1, 0]
    assert report["specks"]["dropped"] == 1 and report["specks"]["frames"] == 1
    keyed = Image.open(tmp_path / "keyed" / "frame-0001.png").convert("RGBA")
    assert keyed.getpixel((117, 50))[3] == 0 and keyed.getpixel((35, 50))[3] == 255
    assert all(row["edge"] == {"top": 0, "left": 0, "right": 0} for row in report["rows"])


def test_small_pieces_near_the_body_and_a_large_one_far_from_it_are_the_subjects(tmp_path):
    # A small shadow drawn 2 px under the feet, a loose pixel one off the outline (both under the speck
    # size, 21 px here), and a block far from the body but too large to be a speck: all kept.
    def pieces(k, image):
        image.paste((40, 40, 40), (30, 92, 40, 93))
        image.putpixel((51, 40), (200, 50, 50))
        image.paste((60, 160, 60), (80, 30, 100, 60))
    files = _raw(tmp_path, 2, pieces)
    report = frames_mod.key_frames(files, tmp_path / "keyed", key="magenta")
    assert [row["specks"] for row in report["rows"]] == [0, 0] and report["specks"]["dropped"] == 0
    keyed = Image.open(tmp_path / "keyed" / "frame-0000.png").convert("RGBA")
    assert keyed.getpixel((35, 92))[3] > 0 and keyed.getpixel((51, 40))[3] > 0 and keyed.getpixel((90, 45))[3] > 0


def test_the_loop_cut_reads_its_loose_pieces_as_before():
    """`video-loop`'s own pass on a cut that is not motion-reviewed: every island of solid pixels (alpha over
    16, side by side) under the speck size, wherever it is — the outline's islands and a drawn shadow too."""
    image = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    image.paste((200, 0, 0, 255), (10, 5, 40, 45))  # 1200 px: a speck is under 12
    image.paste((40, 40, 40, 255), (12, 48, 22, 49))  # a 10 px shadow, 3 px under the body
    image.putpixel((41, 20), (200, 0, 0, 255))  # one transparent column off the body
    image.putpixel((42, 21), (200, 0, 0, 255))  # ... joined to the pixel above only at a corner
    cut, dropped = frames_mod.drop_specks(image.copy(), alpha_over=16, diagonal=False, apart=0)
    assert dropped == 3 and all(cut.getpixel(xy)[3] == 0 for xy in ((15, 48), (41, 20), (42, 21)))
    assert cut.getpixel((20, 20))[3] == 255
    # The key stage keeps all three: within a tenth of the body's height of it.
    keyed, kept = frames_mod.drop_specks(image)
    assert kept == 0 and keyed is image


def test_a_motion_auto_loop_of_a_clip_with_a_fleck_is_cut_without_it(tmp_path):
    # The fleck drifts across the background far from the walker, never into the edge band: keyed and cut
    # with `--anchor motion-auto`, which never cleans a frame, the cells are only as wide as the walker.
    raw = tmp_path / "raw"
    raw.mkdir()
    files = []
    for k in range(73):
        image = Image.new("RGBA", (280, 180), MAGENTA + (255,))
        image.alpha_composite(walks.walker(k))
        x = 250 - 2 * (k % 25)
        image = image.convert("RGB")
        image.paste(FLECK, (x, 40, x + 3, 42))
        files.append(raw / f"frame-{k:04d}.png")
        image.save(files[-1])
    report = frames_mod.key_frames(files, tmp_path / "keyed", key="magenta")
    assert all(row["specks"] == 1 for row in report["rows"])
    out = loop.run_loop(tmp_path / "keyed", tmp_path / "out", fps=24.0, state="walk", min_len=None, max_len=None,
                        n_out=None, seam_max=loop.SEAM_RATIO_MAX, name="w", report_path=None, anchor="motion-auto",
                        repair="off")
    walker_width = walks.walker(0).getchannel("A").getbbox()
    assert out["strip"]["w"] <= walker_width[2] - walker_width[0] + 2 * 8 + 8  # the walker, its margins, a little sway
