# SPDX-License-Identifier: Apache-2.0
"""What RIFE is given for a frame between `a` and `b` (`rife.inputs`: each one's colour as `bleed` makes it and its
coverage) is read of each frame alone, and every try of a cell, and every cell between the same two frames, asks for the
same `a` and `b`: each frame is read once, kept by its pixels (`sprite_gen.video.per_frame`). What RIFE is handed is the
same byte for byte whether it was kept or read again, and the caller's to write into, as before.

The frames are the public knight's walk (tests/fixtures/rife-cuts), a band of it of this test's own, so no other test's
frames are kept for it; no RIFE runs."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from sprite_gen.video import rife

CUT = Path(__file__).resolve().parents[1] / "fixtures" / "rife-cuts" / "knight-walk"


def _frames(top: int, n: int) -> list[Image.Image]:
    out = []
    for path in sorted(CUT.glob("*.webp"))[:n]:
        im = Image.open(path).convert("RGBA")
        out.append(im.crop((0, top, im.width, im.height)))
    return out


def test_what_rife_is_given_reads_each_frame_once_however_many_frames_between_it_are_asked_for(monkeypatch):
    a, b, c = _frames(top=455, n=3)
    named = {id(a): "a", id(b): "b", id(c): "c"}
    bled: list[str] = []
    bleed = rife.bleed

    def spied(x):
        bled.append("frame")
        return bleed(x)

    monkeypatch.setattr(rife, "bleed", spied)
    asked = [(a, b), (a, b), (b, c), (a, b)]  # two tries of a cell, the next cell, the first cell again
    for x, y in asked:
        rife.inputs(x, y)
    assert len(bled) == len(named), f"{len(bled)} reads of {len(named)} frames over {len(asked)} asks"


def test_what_rife_is_handed_is_the_same_byte_for_byte_whether_kept_or_read_again_and_the_caller_s(monkeypatch):
    frames = _frames(top=470, n=4)
    pairs = [(frames[0], frames[1]), (frames[0], frames[1]), (frames[1], frames[2]), (frames[2], frames[3])]

    def handed() -> list[tuple[str, tuple[int, ...], bytes]]:
        out = []
        for x, y in pairs:
            colour, cover = rife.inputs(x, y)
            for arr in (*colour, *cover):
                assert arr.flags.writeable, "the caller may write into what it is handed, as before"
                out.append((str(arr.dtype), arr.shape, arr.tobytes()))
        return out

    kept = handed()
    monkeypatch.setattr(rife._INPUT, "size", 0)
    assert handed() == kept
