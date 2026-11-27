# SPDX-License-Identifier: Apache-2.0
"""A frame made between two frames of a cut is judged (`held_fill.judge`) once a try, and every try of a cell, and every
cell between the same two frames, is judged against the same `a` and `b`: what judge reads of a frame of the cut alone,
or of the two together, it reads once, kept by the frames' pixels (`sprite_gen.video.per_frame`): how much of each one's
edge is outlined and how dark its edge's line is (`rife.outline_shift`), its dark, solid, part-covered and unlined edge
pixels (`rife.smear`), its clear pixels and what lies near them with its premultiplied pixels (`rife.hole`), its
thumbnail and premultiplied pixels (`held_fill.same_picture`), the outline both stand still on (`held_fill.still_kept`),
and how far apart the two are with where they differ (`rife.crossfade`). The made frame is read each time. Kept or read again, the fill is the same, its
record and its pictures byte for byte.

The frames are the public knight's walk (tests/fixtures/rife-cuts), its lower half cut out (outlined enough that
`outline_shift` reads its edges), and a made frame is the plain blend of the two (no RIFE): each test cuts its own band,
so no other test's frames are kept for it."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import held_fill, loop, rife

CUT = Path(__file__).resolve().parents[1] / "fixtures" / "rife-cuts" / "knight-walk"


def _frames(top: int) -> list[Image.Image]:
    """The cut's frames from row `top` down (its feet band, as wide as the cut)."""
    out = []
    for path in sorted(CUT.glob("*.webp")):
        im = Image.open(path).convert("RGBA")
        out.append(im.crop((0, top, im.width, im.height)))
    return out


def _digest(im: Image.Image) -> str:
    return hashlib.sha256(f"{im.mode} {im.size}".encode() + im.tobytes()).hexdigest()


def test_judging_frames_between_one_pair_reads_each_frame_of_the_cut_and_the_pair_once(monkeypatch):
    frames = _frames(top=380)
    a, b, before = frames[0], frames[1], frames[-1]
    assert min(rife.outlined(a), rife.outlined(b)) >= rife.OUTLINED, "both outlined, so outline_shift reads their edges"
    named = {id(a): "a", id(b): "b", id(before): "before"}
    read: Counter = Counter()

    def spied(owner, name, label, which=lambda *args: named.get(id(args[0]))):
        fn = getattr(owner, name)

        def wrapper(*args, **kwargs):
            tag = which(*args)
            if tag:
                read[(label, tag)] += 1
            return fn(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapper)

    clear = {"a": np.asarray(a.getchannel("A"), dtype=np.float32) / 255.0 < 0.5,
             "b": np.asarray(b.getchannel("A"), dtype=np.float32) / 255.0 < 0.5}
    spied(rife, "outlined", "outlined")
    spied(rife, "_smear_counts", "smear counts")
    spied(rife, "_premultiplied", "premultiplied (hole)")
    spied(rife, "_near", "near the clear pixels",  # hole's reach of a's and b's clear pixels, not of the made frame's holes
          which=lambda *args: next((k for k, v in clear.items() if args[1] == rife.HOLE_REACH and np.array_equal(args[0], v)), None))
    spied(rife, "_edge_darkness", "edge darkness")
    spied(loop, "_small_features", "thumbnail")
    spied(held_fill, "premultiplied", "premultiplied")
    spied(held_fill, "_edge", "still outline", which=lambda *args: "pair")  # still_kept reads a's and b's outlines
    spied(rife, "_mask", "where they differ", which=lambda *args: "pair" if args[1] == rife.CROSSFADE_RANGE else None)
    dstack = np.dstack
    monkeypatch.setattr(np, "dstack", lambda *args, **kwargs: (read.update([("premultiplied, crossfade", "any")]),
                                                                dstack(*args, **kwargs))[1])
    tries = (0.3, 0.5, 0.7)
    found = [held_fill.judge(Image.blend(a, b, t), a, b, t, reads=True, before=before)[0] for t in tries]
    assert all(m["crossfade"] > 0 for m in found), "each blend reads as a cross-fade, so crossfade reads the pair's spread"
    once = {(label, tag): 1 for label in ("outlined", "edge darkness", "smear counts", "near the clear pixels")
            for tag in ("a", "b")}
    once |= {("thumbnail", tag): 1 for tag in ("a", "b", "before")}
    assert {k: v for k, v in read.items() if k in once} == once
    assert all(read[("premultiplied", tag)] <= 1 for tag in ("a", "b", "before"))
    assert all(read[("premultiplied (hole)", tag)] <= 1 for tag in ("a", "b"))
    assert read[("still outline", "pair")] == 2  # a's and b's, once
    assert read[("where they differ", "pair")] == 1
    assert read[("premultiplied, crossfade", "any")] == len(tries) + 2  # each made frame, and a and b once


@pytest.mark.slow  # the fill twice, about forty tries each
def test_a_fill_is_the_same_byte_for_byte_whether_what_judge_reads_of_the_cut_is_kept_or_read_again(monkeypatch):
    from sprite_gen.video import per_frame

    frames = _frames(top=360)
    at = held_fill.times(len(frames), 24, 60, ring=True)[:9]

    def fill() -> tuple[list[str], str]:
        out, record = held_fill.at_times(frames, lambda a, b, t: Image.blend(a, b, t), at, ring=True, spacing=24 / 60)
        return [_digest(im) for im in out], json.dumps(record, sort_keys=True)

    kept = fill()
    with monkeypatch.context() as m:
        for memo in per_frame.MEMOS:
            m.setattr(memo, "size", 0)
        again = fill()
    assert kept[0] == again[0]
    assert kept[1] == again[1]
    assert json.loads(kept[1])["frames"], "the cells between frames were made and judged"
