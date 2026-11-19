# SPDX-License-Identifier: Apache-2.0
"""Tests for the matte-based `cutout` background remover (imported-image utility).

Synthetic fixtures are built in-process (no binary assets): an ivory canvas with
a centered green square, optionally carrying a pure-white highlight *inside* the
square. The interior highlight is the crux — a colour-only key would punch a hole
in it, but the corner flood-fill keys by position so it must survive.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.frames.cutout import cutout, estimate_background

IVORY = (248, 247, 242)
GREEN = (86, 133, 39)


def _make_icon(with_interior_white: bool = False) -> Image.Image:
    """128x128 ivory bg, centered 64x64 green square, optional white dot inside it."""
    img = Image.new("RGBA", (128, 128), IVORY + (255,))
    px = img.load()
    for y in range(32, 96):
        for x in range(32, 96):
            px[x, y] = GREEN + (255,)
    if with_interior_white:
        for y in range(56, 72):
            for x in range(56, 72):
                px[x, y] = (255, 255, 255, 255)  # pure white highlight buried in the object
    return img


def test_uniform_background_removed(tmp_path: Path) -> None:
    src = tmp_path / "icon.png"
    _make_icon().save(src)
    out = tmp_path / "icon_cutout.png"
    stats = cutout(src, out)

    result = Image.open(out).convert("RGBA")
    px = result.load()
    # corners (background) fully transparent
    for cx, cy in [(0, 0), (127, 0), (0, 127), (127, 127)]:
        assert px[cx, cy][3] == 0
    # object center fully opaque and still green
    r, g, b, a = px[64, 64]
    assert a == 255
    assert (r, g, b) == GREEN
    assert stats["keyed_pixels"] > 0


def test_interior_white_highlight_preserved(tmp_path: Path) -> None:
    """A pure-white region *inside* the object must not be keyed out (no pinhole)."""
    src = tmp_path / "icon_hl.png"
    _make_icon(with_interior_white=True).save(src)
    out = tmp_path / "icon_hl_cutout.png"
    cutout(src, out)

    px = Image.open(out).convert("RGBA").load()
    # the interior white dot stays opaque despite matching the ivory background colour
    r, g, b, a = px[64, 64]
    assert a == 255
    assert (r, g, b) == (255, 255, 255)


def test_white_check_composites_written(tmp_path: Path) -> None:
    src = tmp_path / "icon.png"
    _make_icon().save(src)
    out = tmp_path / "icon_cutout.png"
    check_dir = tmp_path / "checks"
    stats = cutout(src, out, white_check_dir=check_dir)
    assert "white_check" in stats
    for name in ("cyan", "magenta", "yellow"):
        assert (check_dir / f"icon_cutout_{name}.png").exists()


def test_the_cutout_command_writes_the_cut_beside_its_input_and_prints_what_it_wrote(tmp_path: Path, capsys) -> None:
    """`sprite-gen cutout` from its argv to its files: the cut under the default name beside the input, the three
    check composites beside the cut, and the printed stats naming them."""
    src = tmp_path / "icon.png"
    _make_icon().save(src)
    assert cli.main(["cutout", str(src), "--white-check"]) == 0
    out = tmp_path / "icon_cutout.png"
    checks = [tmp_path / f"icon_cutout_{name}.png" for name in ("cyan", "magenta", "yellow")]
    assert out.is_file() and all(check.is_file() for check in checks)
    px = Image.open(out).convert("RGBA").load()
    assert px[0, 0][3] == 0 and px[64, 64] == GREEN + (255,)
    printed = json.loads(capsys.readouterr().out)
    assert printed["out"] == str(out) and printed["route"] == "matte"
    assert printed["white_check"] == [str(check) for check in checks]


def test_missing_background_raises(tmp_path: Path) -> None:
    """A fully object-filled image (no locatable background corners) fails loudly."""
    src = tmp_path / "full.png"
    Image.new("RGBA", (64, 64), GREEN + (255,)).save(src)
    out = tmp_path / "full_cutout.png"
    with pytest.raises(SystemExit):
        cutout(src, out)


def test_estimate_background_from_corners(tmp_path: Path) -> None:
    img = _make_icon()
    assert estimate_background(img) == IVORY


# --- key-colour routing (imported images already on a magenta/green key) ------
# The routes themselves are checked where the keying is: `test_chroma_key_relative.py`
# (auto and explicit keys, the shared family rule) and `test_chroma_decontam.py` (a white
# background stays on the matte).


def test_unknown_key_raises(tmp_path: Path) -> None:
    src = tmp_path / "icon.png"
    _make_icon().save(src)
    with pytest.raises(SystemExit):
        cutout(src, tmp_path / "o.png", key="blue")


# --- the key colours and key names: one declaration each ------------------------

def _key_choices(verb: str) -> object:
    import argparse

    verbs = next(a for a in cli._build_parser()._actions if isinstance(a, argparse._SubParsersAction)).choices
    return next(a for a in verbs[verb]._actions if "--key" in a.option_strings).choices


@pytest.mark.parametrize("name", ["magenta", "green"])
def test_every_key_table_is_the_one_table(name: str) -> None:
    """The logo matte, slice-sheet and gen key a colour by `cutout.KEY_TARGETS`' own tuple, not a copy of it."""
    from sprite_gen.frames import cutout as cutout_mod, logo_matte, slice_sheet
    from sprite_gen.gen import chroma

    assert logo_matte.KEYS is cutout_mod.KEY_TARGETS
    assert slice_sheet.parse_chroma_key(name) is cutout_mod.KEY_TARGETS[name]
    assert chroma.KEYS[name]["target"] is cutout_mod.KEY_TARGETS[name]


def test_the_tables_kept_by_key_name_name_the_keys() -> None:
    from sprite_gen.frames import cutout as cutout_mod
    from sprite_gen.gen import chroma, logo

    assert set(chroma.KEY_BACKGROUND_TEXT) == set(logo.KEY_COLOUR_WORDS) == set(cutout_mod.KEY_TARGETS)


def test_no_table_keys_a_key_colour_by_its_spelling() -> None:
    """A table of the two key colours keyed on their text stays behind when `cutout.MAGENTA` or `GREEN` moves, and a
    reader that looks a key up in it by the name fails. (`WHITE_CHECK_COLORS` is the three primaries, not the keys.)"""
    from sprite_gen.frames import cutout as cutout_mod

    root = Path(cli.__file__).parent
    spelt = [f"{path.relative_to(root).as_posix()}:{node.lineno}" for path in sorted(root.rglob("*.py"))
             for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.Dict)
             if set(cutout_mod.KEY_TARGETS) <= {key.value for key in node.keys if isinstance(key, ast.Constant)}]
    assert spelt == []


def test_every_video_key_flag_reads_its_declaration() -> None:
    """video-canvas, video-set and video-frames (which keys a clip filmed from a video-canvas still) take
    `canvas.KEYS`, video-prompt the keys its redraw sentence is written for."""
    from sprite_gen.video import canvas, clip_prompt

    declared = {"video-canvas": canvas.KEYS, "video-set": canvas.KEYS, "video-frames": canvas.KEYS,
                "video-prompt": clip_prompt.START_STILL_KEYS}
    assert [verb for verb, keys in declared.items() if _key_choices(verb) is not keys] == []


def test_the_video_key_sets_are_the_cutout_key_names() -> None:
    """Spelt out, so a key name changed in `cutout` shows here: what a still is padded on (`canvas.KEYS`) and
    what a clip's frames are read as keyed on (`frames.KEYS_READ`; brightness is the alpha mode with no key)."""
    from sprite_gen.video import canvas, frames

    assert canvas.KEYS == ("auto", "green", "magenta", "white")
    assert frames.KEYS_READ == ("green", "magenta", "white", "brightness")


def test_the_cutout_key_flag_reads_cutout_keys() -> None:
    from sprite_gen.frames import cutout as cutout_mod

    assert _key_choices("cutout") is cutout_mod.KEYS
    assert cutout_mod.KEYS == ("auto", "white", "magenta", "green", "measured")  # spelt out: the routes take these
