# SPDX-License-Identifier: Apache-2.0
"""The outline read rank by rank (docs/loop-repair.md section 4): `rife.outline_shift` reads a made frame's
coverage edge against its two neighbours' at the same rank, and the fault `outline` of the frames the jump
repair and the cycle alignment make needs it over the line as well as `outline_loss`. A line outlined in a
mid tone, either side of the dark line (70/255), and drawn a little lighter by the flow is kept; a line gone
to the fill is lost, whatever tone the art outlines in. It is read only where both neighbours are outlined
(`rife.outlined`): art painted without a line is judged on the dark line alone, as the versioned measures,
which carry `rife.smear` alone, judge every frame.

The fixtures are synthetic: the walker of tests/video/test_rife.py twice the size, outlined in bands of two
browns either side of 70/255 (luma 57 and 77), the frame between drawn with its line lighter or its legs'
line turned to fill; and the same walker painted without a line, the frame between with a boot washed
lighter. The one real-RIFE case runs where RIFE is installed."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.video import align, repair, rife
from sprite_gen.video.interpolation_quality import OUTLINE_WARN, faults
from tests.video.test_loop_repair import forced_flow_group
from tests.video.test_rife import _walker

TONES = ((80, 50, 35), (105, 68, 48))  # luma 57 and 77: the dark line falls between them
FAR, NEAR = ((20, -20), (-5, 5)), ((10, -10), (4, -4))
SKIN, BOOT, BODY = (240, 205, 185), (70, 40, 30), (130, 120, 80)  # luma 213, 48 and 118, no line between them


def _two_tone(front: float, back: float, *, lift: int = 0, s: int = 2) -> Image.Image:
    """A body over two legs swung from the hip (degrees, 0 straight down), each a capsule outlined 2·s px,
    the line in bands 6·s px tall of the two TONES, each `lift` lighter; the far leg shaded."""
    w, h = 128 * s, 192 * s
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    line = (1, 2, 3, 255)  # a placeholder, coloured by band below
    hip, length, r = (w // 2, int(h * 0.55)), int(h * 0.36), int(w * 0.09)
    for angle, fill in ((back, (215, 205, 200, 255)), (front, (250, 240, 235, 255))):
        a = np.radians(angle)
        foot = (hip[0] + length * np.sin(a), hip[1] + length * np.cos(a))
        for radius, colour in ((r, line), (r - 2 * s, fill)):
            d.line([hip, foot], fill=colour, width=2 * radius)
            d.ellipse([foot[0] - radius, foot[1] - radius, foot[0] + radius, foot[1] + radius], fill=colour)
    body = [w * 0.25, h * 0.15, w * 0.75, h * 0.62]
    d.ellipse(body, fill=line)
    d.ellipse([body[0] + 2 * s, body[1] + 2 * s, body[2] - 2 * s, body[3] - 2 * s], fill=(250, 240, 235, 255))
    x = np.asarray(im).copy()
    on_line = (x == line).all(axis=-1)
    band = (np.arange(h)[:, None] // (6 * s) % 2).repeat(w, axis=1)
    x[on_line, :3] = np.minimum(np.asarray(TONES)[band[on_line]] + lift, 255)
    return Image.fromarray(x, "RGBA")


def _painted(front: float, back: float, *, wash: int = 0, s: int = 2) -> Image.Image:
    """The walker painted without a line: skin legs swung from the hip, a dark boot over the last third of
    each and a body of one mid tone; the near boot `wash` tones lighter."""
    w, h = 128 * s, 192 * s
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    hip, length, r = (w // 2, int(h * 0.55)), int(h * 0.36), int(w * 0.09)
    for angle, lift in ((back, 0), (front, wash)):
        a = np.radians(angle)
        knee, foot = ((hip[0] + k * length * np.sin(a), hip[1] + k * length * np.cos(a)) for k in (0.65, 1.0))
        boot = tuple(min(255, c + lift) for c in BOOT) + (255,)
        d.line([hip, foot], fill=SKIN + (255,), width=2 * r)
        d.line([knee, foot], fill=boot, width=2 * r)
        d.ellipse([foot[0] - r, foot[1] - r, foot[0] + r, foot[1] + r], fill=boot)
    d.ellipse([w * 0.25, h * 0.15, w * 0.75, h * 0.62], fill=BODY + (255,))
    return im


def _legs_to_fill(frame: Image.Image, below: float = 0.57) -> Image.Image:
    """`frame` with the line of everything below this share of its height turned to the leg's fill."""
    x = np.asarray(frame).copy()
    legs = x[int(below * x.shape[0]):]
    line = (legs[..., 3] > 0) & (legs[..., :3] @ np.array([0.299, 0.587, 0.114]) < 140)
    legs[line] = (240, 230, 225, 255)
    return Image.fromarray(x, "RGBA")


def _measure(made: Image.Image, a: Image.Image, b: Image.Image) -> dict[str, float]:
    return {**rife.smear(made, a, b), "outline_shift": rife.outline_shift(made, a, b)}


def test_a_mid_tone_line_drawn_lighter_is_kept_and_one_gone_to_fill_is_lost():
    """Rank by rank the lighter line is no further from its neighbours' than 50/255, so nothing is lost; the
    dark line reads half the edge and more as lost, the line being now all on its far side. The legs' line
    gone to fill is lost on both."""
    a, b = (_two_tone(*p) for p in FAR)
    drawn = _two_tone(8, -8)
    assert rife.outline_shift(drawn, a, b) == 0 and faults(_measure(drawn, a, b)) == []
    for lift in (20, 45):
        lighter = _two_tone(8, -8, lift=lift)
        m = _measure(lighter, a, b)
        assert m["outline_loss"] > 0.5 and m["outline_shift"] == 0 and faults(m) == []
    melted = _measure(_legs_to_fill(drawn), a, b)
    assert melted["outline_loss"] > 0.18 and melted["outline_shift"] > 0.4 and faults(melted) == ["outline"]


def test_the_versioned_measures_judge_the_outline_on_the_dark_line_alone():
    """`rife.smear` alone — what `video-loop-compare` and `video-loop-repair` read — carries no
    `outline_shift`, and the lighter line is the fault `outline` there, as before; so is a measure whose
    `outline_shift` was not read (None, neighbours not outlined)."""
    a, b = (_two_tone(*p) for p in FAR)
    assert faults(rife.smear(_two_tone(8, -8, lift=20), a, b)) == ["outline"]
    assert faults({"dark_excess": 0.0, "outline_loss": 0.3, "outline_shift": 0.0}) == []
    assert faults({"dark_excess": 0.0, "outline_loss": 0.3, "outline_shift": None}) == ["outline"]
    assert faults({"dark_excess": 0.0, "outline_loss": 0.3, "outline_shift": 0.3}) == ["outline"]
    assert faults({"dark_excess": 0.0, "outline_loss": 0.0, "outline_shift": 0.3}) == []


def test_art_outlined_near_black_reads_the_same_on_both():
    """The walker outlined in black: its melted frame is lost on both readings, its drawing between on
    neither, and a black line drawn lighter but still under the dark line is lost on neither."""
    a, b = _walker(20, -20), _walker(-5, 5)
    melted = np.asarray(_walker(8, -8)).copy()
    legs = melted[110:, :]
    legs[(legs[..., 3] > 0) & (legs[..., :3].max(axis=-1) < 70)] = (240, 230, 225, 255)
    m = _measure(Image.fromarray(melted, "RGBA"), a, b)
    assert m["outline_loss"] > OUTLINE_WARN and m["outline_shift"] > OUTLINE_WARN and faults(m) == ["outline"]
    assert faults(_measure(_walker(8, -8), a, b)) == []
    grey = np.asarray(_walker(8, -8)).copy()
    grey[(grey[..., 3] > 0) & (grey[..., :3].max(axis=-1) < 70), :3] = 60
    m = _measure(Image.fromarray(grey, "RGBA"), a, b)
    assert m["outline_loss"] <= 0.01 and m["outline_shift"] == 0


def test_a_frame_with_no_edge_reads_zero_and_neighbours_without_one_are_not_read():
    blank = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    a, b = (_two_tone(*p, s=1) for p in FAR)
    assert rife.outline_shift(blank, a, b) == 0 and rife.outlined(blank) == 0 and rife.outline_shift(a, blank, blank) is None


def test_jump_repair_takes_a_lighter_line_and_refuses_one_gone_to_fill(monkeypatch):
    """A flow group whose RIFE frame keeps a mid-tone line, drawn lighter, is accepted, its proposal
    carrying both readings; the same frame with its legs' line gone to fill is refused."""
    frames = [_two_tone(20, -20), _two_tone(30, -30), _two_tone(-5, 5), _two_tone(10, -10)]
    lighter = _two_tone(8, -8, lift=30)
    forced_flow_group(monkeypatch, [1], (0, 2), [0.5])
    out, record = repair.repair_jumps(frames, lambda x, y, t: lighter)
    made = record["rounds"][0]["made"][0]["proposal"]
    assert record["rounds"][0]["outcome"] == "accepted" and out[1] is lighter
    assert made["outline_loss"] > OUTLINE_WARN and made["outline_shift"] == 0
    melted = _legs_to_fill(_two_tone(8, -8))
    out, record = repair.repair_jumps(frames, lambda x, y, t: melted)
    assert record["rounds"][0]["outcome"] == "rejected" and record["rounds"][0]["faults"] == ["outline"]
    assert out[1] is frames[1]


@pytest.mark.slow
def test_alignment_keeps_a_lighter_line_and_takes_the_nearer_frame_for_one_gone_to_fill():
    a, b = (_two_tone(*p) for p in FAR)
    lighter, melted = _two_tone(8, -8, lift=30), _legs_to_fill(_two_tone(8, -8))
    out, facts = align.resample([a, b], 4, lambda x, y, t: lighter)  # times 0, .5, 1, 1.5: both made at t=0.5
    assert facts["made_at"] == [1, 3] and all(m["faults"] == [] and m["outline_shift"] == 0 for m in facts["smear"])
    out, facts = align.resample([a, b], 4, lambda x, y, t: melted)
    assert facts["nearest_at"] == [1, 3] and all(m["faults"] == ["outline"] for m in facts["smear"])


@pytest.mark.slow
def test_a_boot_washed_lighter_on_art_painted_without_a_line_is_refused_on_the_dark_line(monkeypatch):
    """Painted without a line, the walker's edge is its fill, and its neighbours are not outlined: the frame
    between with its near boot washed 40 tones lighter — no further from the boot than a softened line at any
    rank, so read rank by rank nothing is lost — is the dark line's fault `outline`. The jump repair refuses it
    and the alignment takes the nearer frame; the frame between drawn whole is kept. The two-tone and the black
    walker are outlined on all their edge and the painted one on a few % of it. The washed frame's own reading
    between a and b is the alignment's (`align.measure_made` at cell 1: `rife.smear(washed, a, b)`), read once."""
    frames = [_painted(20, -20), _painted(30, -30), _painted(-5, 5), _painted(10, -10)]
    washed = _painted(8, -8, wash=40)
    forced_flow_group(monkeypatch, [1], (0, 2), [0.5])
    out, record = repair.repair_jumps(frames, lambda x, y, t: washed)
    assert record["rounds"][0]["outcome"] == "rejected" and record["rounds"][0]["faults"] == ["outline"] and out[1] is frames[1]
    a, b = (_painted(*p) for p in FAR)
    out, facts = align.resample([a, b], 4, lambda x, y, t: washed)
    assert facts["nearest_at"] == [1, 3] and all(m["outline_shift"] is None and m["faults"] == ["outline"] for m in facts["smear"])
    washed_between = next(m for m in facts["smear"] if m["at"] == 1)  # made from a to b at t 0.5
    assert faults(_measure(_painted(8, -8), a, b)) == [] and washed_between["outline_loss"] > OUTLINE_WARN
    with monkeypatch.context() as patched:  # read as outlined art, its edge is lighter by less than 50/255 everywhere
        patched.setattr(rife, "OUTLINED", 0.0)
        assert rife.outline_shift(washed, a, b) == 0
    assert max(rife.outlined(a), rife.outlined(b)) < 0.1 and min(rife.outlined(_two_tone(*FAR[0])), rife.outlined(_walker(20, -20))) > 0.99


@pytest.mark.slow
@pytest.mark.real_rife
def test_real_rife_keeps_a_mid_tone_line_through_a_short_step_and_loses_it_crossing_far():
    """Through RIFE, the two-tone legs of a short step come out outlined on the rank reading at every
    fraction; legs crossing far melt, and the fault is read on both. At the size of the real-RIFE cases
    of tests/video/test_rife.py, where the 2 px line is thick enough for the flow to melt."""
    interpolate = rife.Rife()
    near = tuple(_two_tone(*p, s=1) for p in NEAR)
    for t in (0.25, 0.5, 0.75):
        assert "outline" not in faults(_measure(interpolate(*near, t), *near))
    far = tuple(_two_tone(*p, s=1) for p in FAR)
    m = _measure(interpolate(*far, 0.5), *far)
    assert m["outline_loss"] > OUTLINE_WARN and m["outline_shift"] > OUTLINE_WARN and "outline" in faults(m)
