# SPDX-License-Identifier: Apache-2.0
"""A made frame that cross-fades two drawings inside one silhouette (docs/loop-repair.md section 4):
the near and far boots of two drawings both at part strength where the flow found nothing to follow,
the coverage whole — so no part-covered band for the ghost screen, and an outline the outline rule
reads only where the art outlines in near-black. `rife.crossfade` reads it relative to the source
frames' own edges; it is a reading to look at, named in a warning, never a fault: on these legs a
clean drawing between two far-apart drawings outlined in grey reads over the line too.

The fixtures are synthetic: outlined legs with boots (tests/video/test_rife.py `_walker`, three times
the size, a palette per case — the outline's and the boots' lightness varied), and RIFE stand-ins
whose colour run returns the plain blend of the two images while the coverage run returns the
drawing between, or the two coverages' union: a cross-fade inside a whole silhouette. The one
real-RIFE case runs where RIFE is installed."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import align, repair, rife
from sprite_gen.video.interpolation_quality import CROSSFADE_LOOK, faults, looks
from tests.video.test_ghost_screen import _band, _block_walker, _cut
from tests.video.test_rife import _real_rife_available

GREY, BLACK = (125, 118, 112), (20, 20, 20)
LIGHT_BOOTS = (225, 210, 180)


def _walker(front: float, back: float, *, outline=GREY, boot=LIGHT_BOOTS, s: int = 3) -> Image.Image:
    """A body over two legs swung from the hip (degrees, 0 straight down), each a capsule outlined
    2·s px in `outline`, a boot on its lower third (none where `boot` is None); the far leg and boot
    shaded and drawn first."""
    w, h = 128 * s, 192 * s
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    hip, length, r = (w // 2, int(h * 0.55)), int(h * 0.36), int(w * 0.09)

    def part(p, q, radius, fill):
        for rad, colour in ((radius, outline), (radius - 2 * s, fill)):
            d.line([p, q], fill=colour + (255,), width=2 * rad)
            for c in (p, q):
                d.ellipse([c[0] - rad, c[1] - rad, c[0] + rad, c[1] + rad], fill=colour + (255,))

    for angle, fill, shade in ((back, (215, 205, 200), 0.8), (front, (250, 240, 235), 1.0)):
        a = np.radians(angle)
        foot = (hip[0] + length * np.sin(a), hip[1] + length * np.cos(a))
        part(hip, foot, r, fill)
        if boot is not None:
            knee = (hip[0] + 0.62 * length * np.sin(a), hip[1] + 0.62 * length * np.cos(a))
            part(knee, foot, r + s, tuple(int(c * shade) for c in boot))
            part(foot, (foot[0] + 0.5 * r, foot[1]), r + s, tuple(int(c * shade) for c in boot))
    body = [w * 0.25, h * 0.15, w * 0.75, h * 0.62]
    d.ellipse(body, fill=outline + (255,))
    d.ellipse([body[0] + 2 * s, body[1] + 2 * s, body[2] - 2 * s, body[3] - 2 * s], fill=(250, 240, 235, 255))
    return im


def _fade(x: Image.Image, y: Image.Image, t: float) -> Image.Image:
    a, b = (np.asarray(v, dtype=np.float32) for v in (x, y))
    return Image.fromarray(np.uint8((1 - t) * a + t * b + 0.5), "RGB")


def _made(a: Image.Image, b: Image.Image, t: float, coverage) -> Image.Image:
    """RIFE's frame where the colour run found nothing to follow (the plain blend) and the coverage
    run returns `coverage(x, y, t)` of the two coverage images."""
    runs = []

    def call(binary, model, x, y, tt, tmp):
        runs.append(1)
        return _fade(x, y, tt) if len(runs) % 2 else coverage(x, y, tt)

    real = rife._call
    rife._call = call
    try:
        with tempfile.TemporaryDirectory() as td:
            return rife.between(a, b, t, binary=Path("x"), model=Path("x"), tmp=Path(td))
    finally:
        rife._call = real


def _inside(sa: tuple[float, float], sb: tuple[float, float], t: float, **kw) -> tuple[Image.Image, Image.Image, Image.Image, Image.Image]:
    """(a, b, the drawing between at t, the cross-fade inside it): the colour blended, the coverage the drawing's."""
    a, b = _walker(*sa, **kw), _walker(*sb, **kw)
    drawn = _walker(*(x + t * (y - x) for x, y in zip(sa, sb)), **kw)
    cover = drawn.getchannel("A").convert("RGB")
    return a, b, drawn, _made(a, b, t, lambda x, y, tt: cover)


def _union(x: Image.Image, y: Image.Image, t: float) -> Image.Image:
    return Image.fromarray(np.maximum(np.asarray(x), np.asarray(y)), "RGB")


def test_crossfade_reads_two_drawings_blended_inside_a_whole_silhouette_whatever_the_outline():
    """Grey outlines: the outline rule does not count them, and the cross-fade passes every fault.
    Near-black outlines: the same cross-fade loses them, so the outline rule catches it — by the
    palette's accident. The cross-fade reads over the line in both; the drawing between does not."""
    a, b, drawn, made = _inside((10, -10), (5, -5), 0.5)
    measure = {**rife.smear(made, a, b), "ghost": rife.ghost(made), "crossfade": rife.crossfade(made, a, b, 0.5)}
    assert faults(measure) == [] and measure["ghost"] == 0
    assert measure["crossfade"] > 3 * CROSSFADE_LOOK and looks(measure) == ["crossfade"]
    assert rife.crossfade(drawn, a, b, 0.5) < CROSSFADE_LOOK / 2
    a, b, drawn, made = _inside((10, -10), (5, -5), 0.5, outline=BLACK, boot=None)
    assert "outline" in faults(rife.smear(made, a, b))
    assert rife.crossfade(made, a, b, 0.5) > 3 * CROSSFADE_LOOK
    assert rife.crossfade(drawn, a, b, 0.5) < CROSSFADE_LOOK / 2


def test_a_frame_with_no_blend_reads_zero_and_the_reading_is_no_fault():
    a, b = _walker(10, -10), _walker(5, -5)
    assert rife.crossfade(a, a, b, 0.5) == 0 and rife.crossfade(b, a, b, 0.5) == 0  # a source frame is no blend
    assert looks({"crossfade": None}) == [] and looks({"crossfade": CROSSFADE_LOOK}) == []
    assert faults({"dark_excess": 0.0, "outline_loss": 0.0, "crossfade": 1.0}) == []


def test_a_clean_drawing_between_far_drawings_outlined_in_grey_reads_over_the_line_which_is_why_it_is_no_fault():
    """Legs closing from 16 to 3 degrees, drawn half way: nothing blended, yet its grey outlines at
    their new places count as lost where the far-apart drawings had theirs (section 4)."""
    a, b, drawn, _ = _inside((16, -16), (3, -3), 0.5)
    assert rife.crossfade(drawn, a, b, 0.5) > CROSSFADE_LOOK
    assert faults({**rife.smear(drawn, a, b), "ghost": rife.ghost(drawn)}) == []


def test_auto_keeps_a_crossfaded_frame_and_names_it_to_look_at():
    sa, sb = (10, -10), (5, -5)
    a, b, _, there = _inside(sa, sb, 0.5)
    _, _, _, back = _inside(sb, sa, 0.5)
    made = {(id(a), id(b)): there, (id(b), id(a)): back}
    for between in ("auto", "rife"):
        out, facts = align.resample([a, b], 4, lambda x, y, t: made[(id(x), id(y))], between=between)  # times 0, .5, 1, 1.5
        assert [m["at"] for m in facts["smear"]] == [1, 3]
        assert all(m["method"] == "rife" and m["faults"] == [] and m["look"] == ["crossfade"] for m in facts["smear"])
        assert all(m["crossfade"] > CROSSFADE_LOOK for m in facts["smear"])
        assert out[1] is there and out[3] is back and facts["made_by_rife"] == 2
    a, b, _, there = _inside(sa, sb, 0.5, outline=BLACK, boot=None)
    _, _, _, back = _inside(sb, sa, 0.5, outline=BLACK, boot=None)
    made = {(id(a), id(b)): there, (id(b), id(a)): back}
    out, facts = align.resample([a, b], 4, lambda x, y, t: made[(id(x), id(y))])
    assert all(m["method"] == "nearest" and "outline" in m["faults"] and m["look"] == ["crossfade"] for m in facts["smear"])
    assert out[1] is b and out[3] is a  # the nearer source frame, half way rounding up


def _keyed(tmp_path: Path, name: str, length: int) -> Path:
    """Keyed frames of a stride on twos and threes' scale: the legs swing ±14 degrees over `length` frames."""
    keyed = tmp_path / f"{name}-keyed"
    keyed.mkdir()
    for k in range(length + 6):
        swing = 14 * np.cos(2 * np.pi * k / length)
        _walker(swing, -swing).save(keyed / f"{k:04d}.png")
    return keyed


def test_alignment_warns_on_a_crossfaded_frame_it_keeps(tmp_path, capsys):
    """Resampled 8 to 12 through a RIFE whose colour run blends and whose coverage run is the union:
    the frames it makes across the legs' long steps are kept, each named in a warning line."""
    _cut(_keyed(tmp_path, "E", 8), tmp_path / "E", 8)
    report = align.align_set([tmp_path / "E"], length=12, interpolate=lambda a, b, t: _made(a, b, t, _union), multi_cycle="warn")
    row = report["loops"][0]
    looked = [m for m in row["smear"] if m["look"]]
    assert looked and all(m["look"] == ["crossfade"] and m["crossfade"] > CROSSFADE_LOOK for m in looked)
    assert all(m["method"] == "rife" for m in looked if not m["faults"])  # the reading gives nothing way
    kept = [m for m in looked if m["method"] == "rife"]
    named = [w for w in report["warnings"] if "may cross-fade two drawings inside its outline" in w]
    assert kept and len(named) == len(kept) and all(any(f"frame {m['at']} (made by RIFE)" in w for w in named) for m in kept)


def test_jump_repair_takes_a_crossfaded_proposal_and_names_it(monkeypatch):
    sa, sb = (10, -10), (5, -5)
    a, b, drawn, made = _inside(sa, sb, 0.5)
    frames = [a, _walker(20, -20), b, _walker(8, -8)]
    scores = np.array([3., 2., 1., 1.])
    monkeypatch.setattr(repair, "jump_scores", lambda *x, **kw: {k: scores for k in ("whole", "hair", "score")})
    out, record = repair.repair_jumps(frames, lambda x, y, t: made)
    first = record["rounds"][0]
    assert first["outcome"] == "accepted" and first["faults"] == [] and first["look"] == ["crossfade"]
    assert first["proposal"]["crossfade"] > CROSSFADE_LOOK and out[1] is made


@pytest.mark.skipif(not _real_rife_available(), reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH / sprite-gen rife install)")
def test_real_rife_through_a_short_step_reads_no_crossfade():
    """A short step the flow follows: RIFE's frames are softer than their sources, never a cross-fade."""
    interpolate = rife.Rife()
    for outline, boot in ((GREY, LIGHT_BOOTS), (BLACK, None)):
        a, b = _walker(10, -10, outline=outline, boot=boot), _walker(5, -5, outline=outline, boot=boot)
        for t in (0.33, 0.5, 0.67):
            assert rife.crossfade(interpolate(a, b, t), a, b, t) < CROSSFADE_LOOK / 2


def _keyed_cape(tmp_path: Path, name: str, length: int) -> Path:
    """Keyed frames of a walk drawn part-covered in every frame (a translucent cape over the legs)."""
    keyed = tmp_path / f"{name}-keyed"
    keyed.mkdir()
    for k in range(length + 6):
        _band(_block_walker(2 * math.pi * k / length), box=(0.25, 0.80, 0.75, 0.95)).save(keyed / f"{k:04d}.png")
    return keyed


def test_a_loop_the_ghost_screen_does_not_read_is_named_on_stderr_not_only_in_the_report(tmp_path, capsys):
    """A cut drawn part-covered in every frame is not judged by the screen (`reads` false) — and says
    so on stderr in video-loop, and in the alignment's warnings, which video-cycle-align prints."""
    report = _cut(_keyed_cape(tmp_path, "S", 20), tmp_path / "S", 20)
    assert report["ghost_screen"]["reads"] is False
    assert "the ghost screen does not read this cut" in capsys.readouterr().err
    _cut(_keyed_cape(tmp_path, "E", 24), tmp_path / "E", 24)
    report = align.align_set([tmp_path / "S", tmp_path / "E"], interpolate=lambda a, b, t: a.copy())
    named = [w for w in report["warnings"] if "the ghost screen does not read it" in w]
    assert len(named) == 2 and all("drawn part-covered" in w for w in named)
