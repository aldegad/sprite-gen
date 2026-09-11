# SPDX-License-Identifier: Apache-2.0
"""`video-canvas` normalizes an off-key flat background to the exact key, and `video-frames`
tells leftover key background at the edge apart from a subject that left the frame
(plan sprite-gen/chroma-key-relative-threshold, step 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import canvas as canvas_mod
from sprite_gen.video import frames as frames_mod

GREEN = (0, 255, 0)
MAGENTA = (255, 0, 255)
DARK_GREEN = (8, 162, 24)  # 96.38 from pure green: the 2026-09-11 cliff colour
DARK_MAGENTA = (170, 8, 180)
IVORY = (248, 247, 242)
SUBJECT = (200, 40, 40)
SIZE = (120, 160)
SUBJECT_BOX = (40, 40, 80, 150)  # x0, y0, x1, y1


def _still(tmp_path: Path, bg: tuple[int, int, int], name: str = "still.png") -> Path:
    im = Image.new("RGB", SIZE, bg)
    x0, y0, x1, y1 = SUBJECT_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            im.putpixel((x, y), SUBJECT)
    p = tmp_path / name
    im.save(p)
    return p


def _corners(im: Image.Image) -> list[tuple[int, ...]]:
    w, h = im.size
    return [im.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]


@pytest.mark.parametrize("bg,kind,key", [(DARK_GREEN, "green", GREEN), ((10, 150, 30), "green", GREEN), (DARK_MAGENTA, "magenta", MAGENTA)])
def test_dark_key_still_is_repainted_to_the_exact_key_and_not_refused(tmp_path: Path, bg, kind, key) -> None:
    still = Image.open(_still(tmp_path, bg))
    out, rep = canvas_mod.pad_canvas(still, canvas_mod.profile_for("jump"))  # tall: real padding above
    assert _corners(out) == [key] * 4 and rep["key_rgb"] == list(key) and rep["key"] == kind
    assert rep["corner_rgb"] == list(bg) and rep["key_painted"] == list(bg)
    ox, oy = rep["offset"]
    data = np.array(out)
    inner = data[oy:oy + SIZE[1], ox:ox + SIZE[0]]
    x0, y0, x1, y1 = SUBJECT_BOX
    subject = np.zeros(inner.shape[:2], dtype=bool)
    subject[y0:y1, x0:x1] = True
    assert (inner[subject] == SUBJECT).all(), "subject pixels must stay byte-identical"
    assert (inner[~subject] == key).all(), "every background pixel of the still becomes the exact key"
    assert rep["normalized_px"] == int((~subject).sum())
    # the padding outside the still is the exact key too
    outside = np.ones(data.shape[:2], dtype=bool)
    outside[oy:oy + SIZE[1], ox:ox + SIZE[0]] = False
    assert (data[outside] == key).all()


def test_exact_key_still_is_unchanged_and_reports_zero_painted_drift(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path, GREEN))
    out, rep = canvas_mod.pad_canvas(still, canvas_mod.profile_for("walk"))
    assert rep["key"] == "green" and rep["key_painted"] == list(GREEN) and _corners(out) == [GREEN] * 4
    ox, oy = rep["offset"]
    assert (np.array(out)[oy:oy + SIZE[1], ox:ox + SIZE[0]] == np.array(still)).all()


def test_non_key_base_keeps_the_corner_colour_padding(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path, IVORY))
    out, rep = canvas_mod.pad_canvas(still, canvas_mod.profile_for("jump"))
    assert rep["key"] is None and rep["key_rgb"] == list(IVORY) and rep["normalized_px"] == 0
    assert _corners(out) == [IVORY] * 4


def test_explicit_key_that_the_corners_are_not_is_refused(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path, IVORY))
    with pytest.raises(SystemExit, match="not a green key family colour"):
        canvas_mod.pad_canvas(still, canvas_mod.profile_for("walk"), key="green")
    with pytest.raises(SystemExit, match="unknown --key"):
        canvas_mod.pad_canvas(still, canvas_mod.profile_for("walk"), key="blue")
    # --key white opts out of normalization even on a green base
    out, rep = canvas_mod.pad_canvas(Image.open(_still(tmp_path, DARK_GREEN, "g.png")), canvas_mod.profile_for("walk"), key="white")
    assert rep["key"] is None and _corners(out) == [DARK_GREEN] * 4


def test_run_canvas_accepts_key_and_records_it(tmp_path: Path) -> None:
    rep = canvas_mod.run_canvas(_still(tmp_path, DARK_GREEN), tmp_path / "o" / "c.png", state="walk", shape=None, facing="right", headroom=None, lead=None, report_path=None, key="green")
    assert rep["key"] == "green" and rep["key_painted"] == list(DARK_GREEN)
    assert _corners(Image.open(tmp_path / "o" / "c.png")) == [GREEN] * 4


def test_normalized_dark_still_keys_like_the_pure_key_still(tmp_path: Path) -> None:
    """The canvas output of a dark-green still and of the same still on pure green must key to the
    same alpha in `video-frames` (the double safety: normalization + relative key both close it)."""
    dark, _ = canvas_mod.pad_canvas(Image.open(_still(tmp_path, DARK_GREEN, "d.png")), canvas_mod.profile_for("walk"))
    pure, _ = canvas_mod.pad_canvas(Image.open(_still(tmp_path, GREEN, "p.png")), canvas_mod.profile_for("walk"))
    (tmp_path / "raw").mkdir()
    dark.save(tmp_path / "raw" / "frame-0000.png")
    pure.save(tmp_path / "raw" / "frame-0001.png")
    rep = frames_mod.key_frames(sorted((tmp_path / "raw").glob("*.png")), tmp_path / "keyed", key="green")
    assert rep["edge_contacts"] == [] and rep["alpha_zero_pct_min"] == rep["alpha_zero_pct_max"]
    a = np.array(Image.open(tmp_path / "keyed" / "frame-0000.png").convert("RGBA"))[..., 3]
    b = np.array(Image.open(tmp_path / "keyed" / "frame-0001.png").convert("RGBA"))[..., 3]
    assert (a == b).all()


# --- frames: residual background vs subject at the edge ---------------------------


def _frames(tmp_path: Path, *, residual: bool, touch_top: bool) -> list[Path]:
    d = tmp_path / "raw"
    d.mkdir(exist_ok=True)
    files = []
    for k in range(3):
        im = Image.new("RGB", (80, 100), GREEN)
        top = 0 if touch_top else 20
        for y in range(top, 90):
            for x in range(25 + k, 55 + k):
                im.putpixel((x, y), (180, 60, 30))
        if residual:  # a dark-green patch of leftover background hugging the left edge
            for y in range(40, 60):
                for x in range(0, 10):
                    im.putpixel((x, y), DARK_GREEN)
        p = d / f"frame-{k:04d}.png"
        im.save(p)
        files.append(p)
    return files


def test_leftover_key_background_at_the_edge_is_reported_as_residual_not_framing(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=True, touch_top=False)
    with pytest.raises(SystemExit, match="leftover chroma background") as info:
        frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    assert "framed too tight" not in str(info.value) and "video-canvas" in str(info.value)
    rep = frames_mod.key_frames(files, tmp_path / "keyed2", key="green", check_edges=False)
    assert rep["frames"] == 3


def test_subject_contact_is_still_framing_and_carries_the_split(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=False, touch_top=True)
    with pytest.raises(SystemExit, match="framed too tight") as info:
        frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    assert "leftover" not in str(info.value)
    raw = Image.open(files[0])
    keyed = Image.open(tmp_path / "keyed" / files[0].name)
    split = frames_mod.classify_edge_contact(raw, keyed, GREEN)
    assert split["subject"] > 0 and split["residual"] == 0


def test_mixed_contact_names_both(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=True, touch_top=True)
    with pytest.raises(SystemExit, match="framed too tight") as info:
        frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    assert "also carry leftover chroma background" in str(info.value)
    raw = Image.open(files[0])
    keyed = Image.open(tmp_path / "keyed" / files[0].name)
    split = frames_mod.classify_edge_contact(raw, keyed, GREEN)
    assert split["subject"] > 0 and split["residual"] > 0
