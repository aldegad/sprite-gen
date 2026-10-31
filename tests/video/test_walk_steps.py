# SPDX-License-Identifier: Apache-2.0
"""A walk is cut two steps long — the left foot forward once and the right once — wherever the clip shows its steps
(docs/loop-repair.md section 3, "The step count"; sprite_gen/video/crossings.py, local_cycle.by_steps).

Each walker is drawn so the pixel search alone makes one of the faults a walk loop is cut with:

- a diagonal walk with its legs drawn alike and a tail on its own beat, cut one step long (a half step loop:
  one foot goes forward);
- a walk slower than the search's window, cut one step long;
- a front walk with a big tail on its own beat, cut three steps long by the gait fallback (the wrap puts the
  nearer foot back: a stomp in place);
- a slow front walk in a short clip, refused outright (no periodic cycle found);
- a walk that stalls, its cut playing the stalled step.

Two whose feet meet on the screen once a cycle, read as cycles (`local_cycle.read_cycles`) and cut one cycle, their body
rising and falling twice an interval between crossings on legs whose feet stay on the ground — the witness that reads no
leg, read against the lowest row and the rows down the middle of the body or the widths of its upper body, the same
line saying so on the screen and over the feet, its arms swinging within the body's sides or past them: a walk seen from
behind whose far foot passes hidden behind the near one, on ones, twos or threes, a cycle a whole number of frames or
not, its legs drawn apart or alike (the parent engine counted the cycle one step and cut two cycles, or kept the search's
cut unwritten); and a walk on a front diagonal whose near foot steps out toward the camera and passes the far one apart,
read so where its view is given. The guards: from the side, and where the view's foot cue says the other leg leads an
interval on, the count stands however alike a step on draws; and where nothing that reads no leg witnesses two steps an
interval — legs drawn alike from any view, alike and wide as a hidden pass is, alike and wide under a light fixed on the
screen, the swing foot lifted under such a light, a far leg a few tones off with no view, a walk whose feet do meet once
a cycle but whose body does not bob, the same walk carried up and down whole, feet and all (the picture moved on its
beat), a body held still on a picture shaken twice an interval, and feet that run off the frame — the crossings stay on
steps, named in a warning. Read off drawn frames, no walk that does not rise and fall twice an interval on its legs is
witnessed however the whole picture moves or zooms, on any beat, whatever the top of its head (flat, round, a point, two
ears' tips) or however its head or a cape is drawn against the zoom, nor a run whose feet leave the ground, nor a planted
foot sliding down the screen, nor a line on a beat of its own that a frame rate folds onto twice an interval over feet
that do the rising and falling, nor a head rising twice in one step on feet that do not. And one the count alone gets
wrong, where the search's cut stands: a walk on threes whose tail closes on no cut of two steps.

A walk seen from the front whose feet stay apart and meet once a cycle, its body still, is witnessed by the order its
feet are drawn in (`local_cycle.order_word`): the foot drawn nearer, lower, changes sides twice an interval, and the clip
shows its two sides of feet are its legs — no foot planted on the ground slides under the body to the other side, the
upper body does not rock over them — so the crossings mark cycles and the cut stands on the same word; labelled the
side view, the order is not read. A side walk lifting its swing foot draws one foot lower and then the other too, but
its planted foot passes under the body: never read so, whatever its label; nor a picture rocked on its feet, nor feet
drawn level. Nor does the order stand a cut the search kept where none of the clip's two-step cuts closes. A tail whose
tip dips into the foot band beside the feet is never read as a foot: drawn over no column anything stands on, the
frames whose one side it is are left out, and the order says what the walker drawn without it says — two steps a cycle
of the walker whose feet meet once a cycle, one step an interval of one whose feet meet once a step (never cut one step
unnamed), nothing of a side walk.

A walk the search already cuts two steps long, joined at the walk's own point, is cut exactly as before; a body
whose feet cannot be read is cut as before. Synthetic frames only."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from conftest import feet_unseen, rife_located
from sprite_gen.video import crossings, legs, local_cycle, loop

FRAMES = 73
GROUND = 166  # the row the walkers' soles stand on: a body rising and falling on its legs leaves its feet there


def _side(k: float, *, cycle: float, phase: float = 0, tail_cycle: float = 1, tail: int = 0, arms: bool = True) -> Image.Image:
    """A side or diagonal walker: legs drawn alike, half a cycle apart and seen apart for most of a step; arms opposite,
    the far one darker (`arms`); a tail swinging up and down behind on its own beat (`tail_cycle`, `tail` px long)."""
    ph = 2 * math.pi * (k + phase) / cycle
    im = Image.new("RGBA", (300, 220))
    d = ImageDraw.Draw(im)
    x, y = 90, 40 + round(3 * math.cos(2 * ph))
    sw = 16 * math.sin(ph)
    if arms:
        d.rectangle((x + 14 - sw - 4, y + 34, x + 14 - sw + 4, y + 72), fill=(150, 150, 150, 255))
    for sign in (1, -1):
        lx = x + 14 + 14 * sign * math.sin(ph)
        d.rectangle((lx - 5, y + 80, lx + 5, y + 126), fill=(60, 60, 70, 255))
    if tail:
        a = 0.8 * math.sin(2 * math.pi * k / tail_cycle)
        d.line((x, y + 50, x - tail * math.cos(a), y + 50 - tail * math.sin(a)), fill=(180, 120, 40, 255), width=12)
    d.rectangle((x, y + 30, x + 28, y + 84), fill=(200, 80, 80, 255))
    d.rectangle((x + 2, y, x + 26, y + 28), fill=(230, 200, 170, 255))
    if arms:
        d.rectangle((x + 14 + sw - 4, y + 34, x + 14 + sw + 4, y + 72), fill=(225, 225, 225, 255))
    d.point((x + 6, y + 40 + int(k) % 11), fill=(250, 250, 250, 255))  # no two frames the same picture
    return im


def _front(k: float, *, cycle: float, phase: float = 0, tail_cycle: float = 1, tail: int = 0) -> Image.Image:
    """A walker seen from the front: legs side by side and touching (never seen apart), the nearer foot drawn lower
    each step; arms swinging opposite; a tail swinging sideways at the hips on its own beat."""
    ph = 2 * math.pi * (k + phase) / cycle
    im = Image.new("RGBA", (260, 220))
    d = ImageDraw.Draw(im)
    cx, y = 130, 40 + round(2 * math.cos(2 * ph))
    drop = round(6 * math.sin(ph))
    if tail:
        a = math.sin(2 * math.pi * k / tail_cycle)
        d.line((cx, y + 72, cx + tail * a, y + 90), fill=(180, 120, 40, 255), width=14)
    d.rectangle((cx - 12, y + 80, cx - 1, y + 120 + drop), fill=(60, 60, 70, 255))
    d.rectangle((cx, y + 80, cx + 11, y + 120 - drop), fill=(60, 60, 70, 255))
    d.rectangle((cx - 16, y + 30, cx + 16, y + 84), fill=(200, 80, 80, 255))
    d.rectangle((cx - 12, y, cx + 12, y + 28), fill=(230, 200, 170, 255))
    sw = round(8 * math.sin(ph))
    d.rectangle((cx - 22, y + 34 + sw, cx - 16, y + 70 + sw), fill=(225, 225, 225, 255))
    d.rectangle((cx + 16, y + 34 - sw, cx + 22, y + 70 - sw), fill=(225, 225, 225, 255))
    d.point((cx - 6, y + 40 + int(k) % 11), fill=(250, 250, 250, 255))
    return im


ARMS_IN = 8  # an arm's swing either way of the body's middle that keeps it within the body's sides (28 px apart)


def _behind(k: int, *, cycle: float, hold: int, alike: bool = False, bob: int = 3, whole: bool = False,
            top: str = "flat", arm: float = 16) -> Image.Image:
    """A walker seen from behind on a diagonal, drawn on twos or threes (`hold`): the near leg swings ahead and back,
    and the far one is seen beside it only while it is behind — ahead, it stands hidden behind the near leg. The feet
    are seen apart once a cycle and together through the other step, its pass hidden among them: one crossing shows a
    cycle. The body rises and falls `bob` px once a step on its legs, the feet on the ground (`whole`: carried up and
    down with it, feet and all — the picture moved, not a walk); the far leg darker (`alike`: drawn as the near one);
    arms opposite, the far one darker, swinging `arm` px either way of the body's middle (at 16 past its sides, its
    upper body widening and narrowing once a step; at ARMS_IN within them); each repeat of a drawing redrawn a little (a
    pixel aside, a few tones). Its head's top is `top` (`_head`)."""
    redraw = k % hold
    ph = 2 * math.pi * (k - redraw) / cycle
    im = Image.new("RGBA", (300, 220))
    d = ImageDraw.Draw(im)
    x, y = 90 + redraw, 40 + round(bob * math.cos(2 * ph))
    sole = y + 126 if whole else GROUND
    sw, swing = 16 * math.sin(ph), arm * math.sin(ph)
    near = x + 14 + sw
    far = x + 14 - sw if sw > 0 else near
    d.rectangle((x + 14 - swing - 4, y + 34, x + 14 - swing + 4, y + 72), fill=(150, 150, 150, 255))
    d.rectangle((far - 5, y + 80, far + 5, sole), fill=(70, 70, 80, 255) if alike else (50, 50, 60, 255))
    d.rectangle((near - 5, y + 80, near + 5, sole), fill=(70, 70, 80, 255))
    d.rectangle((x, y + 30, x + 28, y + 84), fill=(200 + 12 * redraw, 80, 80, 255))
    _head(d, x, y, top)
    d.rectangle((x + 14 + swing - 4, y + 34, x + 14 + swing + 4, y + 72), fill=(225, 225, 225, 255))
    return im


def _outward(k: float, *, cycle: float, bob: int = 3, whole: bool = False, arm: float = 16) -> Image.Image:
    """A walker on a front diagonal whose near foot steps out toward the camera: it swings wide ahead and back, and the
    far foot hardly moves, so the feet meet on the screen once a cycle, where the near foot passes the far one on its
    way back; its pass on the way out is made apart. The body rises and falls `bob` px once a step on its legs, the feet
    on the ground (`whole`: carried with it); arms opposite, the far one darker, swinging `arm` px either way (as
    `_behind`'s); the far leg a tone off the near."""
    ph = 2 * math.pi * k / cycle
    im = Image.new("RGBA", (300, 220))
    d = ImageDraw.Draw(im)
    x, y = 90, 40 + round(bob * math.cos(2 * ph))
    sole = y + 126 if whole else GROUND
    sw, swing = 16 * math.sin(ph), arm * math.sin(ph)
    near, far = x + 14 + sw, x + 34 - 4 * math.sin(ph)
    d.rectangle((x + 14 - swing - 4, y + 34, x + 14 - swing + 4, y + 72), fill=(150, 150, 150, 255))
    d.rectangle((far - 5, y + 80, far + 5, sole), fill=(61, 61, 71, 255))
    d.rectangle((near - 5, y + 80, near + 5, sole), fill=(60, 60, 70, 255))
    d.rectangle((x, y + 30, x + 28, y + 84), fill=(200, 80, 80, 255))
    d.rectangle((x + 2, y, x + 26, y + 28), fill=(230, 200, 170, 255))
    d.rectangle((x + 14 + swing - 4, y + 34, x + 14 + swing + 4, y + 72), fill=(225, 225, 225, 255))
    return im


def _apart(k: float, *, cycle: float, rock: float = 0, slide: bool = False, each_step: bool = False) -> Image.Image:
    """A walker seen from the front whose feet stay apart: the foot stepping toward the camera is drawn lower, up to 3 px
    under the ground and the other as far over it, so the foot drawn nearer changes sides once a step; the right foot,
    drawn higher, swings in to meet the left over it and back, so the feet meet on the screen once a cycle, briefly, and
    no foot on the ground slides along it. Its body does not rise and fall, its arms swinging within its sides
    (ARMS_IN): nothing that reads no leg but the order its feet are drawn in tells a step on from a cycle on.
    `rock`: its feet drawn level and still, the whole picture rocked that many degrees either way about its feet once a
    cycle, one foot drawn lower and then the other with no step. `slide`: the right foot swings in to meet the left as
    both stand level on the ground and back as it is drawn lower, planted, sliding along the ground as only a foot seen
    from the side does. `each_step`: the left foot too swings in to meet the right while it is drawn higher, so the
    feet meet once a step."""
    ph = 2 * math.pi * k / cycle
    im = Image.new("RGBA", (300, 220))
    d = ImageDraw.Draw(im)
    x, y = 90, 40
    drop = 0 if rock else round(3 * math.sin(ph))
    inward = 0 if rock else 10 * max(0.0, -math.cos(ph)) ** 4 if slide else 10 * max(0.0, math.sin(ph)) ** 2
    outward = 10 * max(0.0, -math.sin(ph)) ** 2 if each_step else 0
    swing = ARMS_IN * math.sin(ph)
    d.rectangle((x + 14 - swing - 4, y + 34, x + 14 - swing + 4, y + 72), fill=(150, 150, 150, 255))
    for lx, sole, t in ((x + 6 + outward, GROUND + drop, 0), (x + 22 - inward, GROUND - drop, 1)):
        d.rectangle((lx - 5, y + 80, lx + 5, sole), fill=(60 + t, 60 + t, 70 + t, 255))
    d.rectangle((x, y + 30, x + 28, y + 84), fill=(200, 80, 80, 255))
    d.rectangle((x + 2, y, x + 26, y + 28), fill=(230, 200, 170, 255))
    d.rectangle((x + 14 + swing - 4, y + 34, x + 14 + swing + 4, y + 72), fill=(225, 225, 225, 255))
    return im.rotate(rock * math.sin(ph), resample=Image.Resampling.NEAREST, center=(x + 14, GROUND)) if rock else im



def _dipping_tail(draw, *, hip: tuple[int, int], reach: int, beat: float = 17):
    """`draw` with a tail from `hip`, on the body, to a tip `reach` px left of it, rising and falling 8 px either way on
    a beat of its own (`beat` frames): the tip dips into the foot band a third of the time or so, never as low as the
    feet nor over a column they stand on."""
    def drawn(k: float) -> Image.Image:
        im = draw(k)
        tip = (hip[0] - reach, GROUND - 12 + round(8 * math.sin(2 * math.pi * k / beat)))
        ImageDraw.Draw(im).line((hip, tip), fill=(180, 120, 40, 255), width=6)
        return im
    return drawn


def _alike(k: float, *, cycle: float, phase: float = 0, tone: int = 1, lean: float = 0, leg_tone: int | None = None,
           leg_half: int = 5, lift: int = 0, light: tuple[str, int] | None = None, bob: int = 3, whole: bool = False,
           air: int = 0, slide: int = 0, limp: int = 0, top: str = "flat", arm: float = 16) -> Image.Image:
    """A side walker whose two steps are the same silhouette: the far leg and arm `tone` tones lighter than the near
    ones (the far leg `leg_tone`, where given: 0 draws the legs alike), the arms swinging `arm` px either way of the
    body's middle, behind it (at ARMS_IN hidden within its sides), and `lean` px further back than forward — nothing
    else tells a step from the next. Each leg is drawn `leg_half` px either side of its line: at 10
    the feet are seen together over half the interval at a pass, as where the far foot passes hidden. The leg swinging
    forward is lifted up to `lift` px, most at its pass; `light` lays a light fixed on the screen over the legs
    (`_lit`). The body rises and falls `bob` px once a step on its legs, the feet on the ground (`whole`: carried with
    it). A run lifts both soles up to `air` px off the ground while the body is high; a view from behind or a diagonal
    draws each sole up to `slide` px lower the further back its foot is (the planted foot sliding down the screen
    through its step). A limp sinks the body `limp` px either way once a cycle, low on one leg's step, high on the
    other's. Its head's top is `top` (`_head`)."""
    ph = 2 * math.pi * (k + phase) / cycle
    im = Image.new("RGBA", (300, 220))
    d = ImageDraw.Draw(im)
    drawn = Image.new("L", im.size)
    x, y = 90, 40 + round(bob * math.cos(2 * ph) + limp * math.cos(ph))
    for sign, t in ((1, 0), (-1, tone)):
        sw = sign * (arm * math.sin(ph) + lean)
        d.rectangle((x + 14 - sw - 4, y + 34, x + 14 - sw + 4, y + 72), fill=(190 + t, 190 + t, 190 + t, 255))
    for sign, t in ((1, 0), (-1, tone if leg_tone is None else leg_tone)):
        lx = x + 14 + 14 * sign * math.sin(ph)
        up = round(lift * max(0.0, sign * math.cos(ph)))
        ground = y + 126 if whole else GROUND - round(air * max(0.0, -math.cos(2 * ph)))
        box = (lx - leg_half, y + 80 - up, lx + leg_half, ground + round(slide * (1 - sign * math.sin(ph)) / 2) - up)
        d.rectangle(box, fill=(60 + t, 60 + t, 70 + t, 255))
        ImageDraw.Draw(drawn).rectangle(box, fill=255)
    d.rectangle((x, y + 30, x + 28, y + 84), fill=(200, 80, 80, 255))
    ImageDraw.Draw(drawn).rectangle((x, y + 30, x + 28, y + 84), fill=0)
    _head(d, x, y, top)
    return _lit(im, drawn, light)


TOPS = ("flat", "round", "pointed", "ears")


def _head(d: ImageDraw.ImageDraw, x: int, y: int, top: str = "flat") -> None:
    """The head of a walker whose body's box starts at (x, y), its top `top` (TOPS): `flat`, the box every walker here
    draws; `round`, an ellipse in that box; `pointed`, a triangle from a point over the box's middle down to a base
    wider than the body; `ears`, the box from a third of the way down with an ear's tip over each corner. A zoom widens
    a band across a flat top by its whole part, across a round one by part of it, and across a point or an ear's tip by
    nothing."""
    skin = (230, 200, 170, 255)
    if top == "flat":
        d.rectangle((x + 2, y, x + 26, y + 28), fill=skin)
    elif top == "round":
        d.ellipse((x + 2, y, x + 26, y + 28), fill=skin)
    elif top == "pointed":
        d.polygon(((x + 14, y), (x - 21, y + 28), (x + 49, y + 28)), fill=skin)
    elif top == "ears":
        d.rectangle((x + 2, y + 10, x + 26, y + 28), fill=skin)
        d.polygon(((x + 4, y), (x + 2, y + 10), (x + 11, y + 10)), fill=skin)
        d.polygon(((x + 24, y), (x + 17, y + 10), (x + 26, y + 10)), fill=skin)
    else:
        raise ValueError(top)


def _lit(im: Image.Image, legs_drawn: Image.Image, light: tuple[str, int] | None) -> Image.Image:
    """`im` under a light fixed on the screen, laid over the pixels drawn as legs (`legs_drawn`), both legs alike:
    `("x", n)` up to n tones lighter to the right across the feet, `("y", n)` up to n tones lighter lower down the legs.
    It lights a foot by where it is drawn on the screen, not by which leg it is."""
    if light is None:
        return im
    axis, tones = light
    a = np.array(im)
    h, w = a.shape[:2]
    field = (np.clip((np.arange(w) - 80) * tones // 48, 0, tones)[None, :].repeat(h, 0) if axis == "x" else
             np.clip((np.arange(h) - 120) * tones // 46, 0, tones)[:, None].repeat(w, 1))
    on = np.asarray(legs_drawn) > 0
    a[..., :3][on] = np.clip(a[..., :3][on].astype(int) + field[on][:, None], 0, 255).astype(np.uint8)
    return Image.fromarray(a)


def _carried(draw, motion, room: int = 60):
    """`draw`'s walker carried up and down the screen with the whole picture (a motion of the picture, not the walk's)
    on a canvas `room` px taller either side: `"arc"` along a smooth arc, 24 px down at either end of the 73 frames
    and level in the middle; `"cubic"` from 48 px up at the start to 48 down at the end, still in the middle;
    `"sway"` 12 px down, level, 12 up and back every 92 frames; `"ramp"` 24 px down over the clip, a row every three
    frames; or `motion(k)` px down in frame k."""
    move = motion if callable(motion) else {
        "arc": lambda k: round(24 * ((k - 36) / 36) ** 2), "cubic": lambda k: round(48 * ((k - 36) / 36) ** 3),
        "sway": lambda k: round(12 * math.sin(2 * math.pi * k / 92)), "ramp": lambda k: round(24 * k / 72)}[motion]

    def frame(k: int) -> Image.Image:
        im = draw(k)
        out = Image.new("RGBA", (im.width, im.height + 2 * room))
        out.paste(im, (0, room + move(k)))
        return out
    return frame


def _tailed(k: int) -> Image.Image:
    """A side walker on threes, 30 frames a cycle, its legs hurrying through each pass and slowing apart, with a big
    tail swinging on a beat of its own (34 frames); each repeat of a drawing redrawn a few tones."""
    redraw = k % 3
    t = k - redraw
    u = 2 * math.pi * t / 30
    ph = u - 0.25 * math.sin(u)
    im = Image.new("RGBA", (300, 220))
    d = ImageDraw.Draw(im)
    x, y = 90, 40 + round(3 * math.cos(2 * ph))
    sw = 16 * math.sin(ph)
    d.rectangle((x + 14 - sw - 4, y + 34, x + 14 - sw + 4, y + 72), fill=(150, 150, 150, 255))
    for sign in (1, -1):
        lx = x + 14 + 14 * sign * math.sin(ph)
        d.rectangle((lx - 5, y + 80, lx + 5, y + 126), fill=(60, 60, 70, 255))
    a = 0.8 * math.sin(2 * math.pi * t / 34)
    d.line((x, y + 50, x - 70 * math.cos(a), y + 50 - 70 * math.sin(a)), fill=(180, 120, 40, 255), width=12)
    d.rectangle((x, y + 30, x + 28, y + 84), fill=(200 + 3 * redraw, 80, 80, 255))
    d.rectangle((x + 2, y, x + 26, y + 28), fill=(230, 200, 170, 255))
    d.rectangle((x + 14 + sw - 4, y + 34, x + 14 + sw + 4, y + 72), fill=(225, 225, 225, 255))
    return im


def _blob(k: int) -> Image.Image:
    """A legless body bouncing every 16 frames: one foot band, nothing to step."""
    im = Image.new("RGBA", (200, 200))
    h = 60 + round(8 * math.cos(2 * math.pi * k / 16))
    ImageDraw.Draw(im).ellipse((70, 170 - h, 130, 170), fill=(90, 160, 220, 255))
    ImageDraw.Draw(im).point((90, 150 - k % 9), fill=(250, 250, 250, 255))
    return im


def _keyed(root: Path, draw, frames: int = FRAMES, hold: tuple[int, int] | None = None) -> Path:
    """The walker's frames; `hold` (from, frames) holds the walk still that long at `from`."""
    keyed = root / "keyed"
    keyed.mkdir(parents=True)
    for k in range(frames):
        t = k if hold is None or k < hold[0] else hold[0] if k < hold[0] + hold[1] else k - hold[1]
        draw(t).save(keyed / f"{k:03}.png")
    return keyed


def _cut(out: Path, keyed: Path, *extra: str) -> dict:
    assert loop.main(["--frames-dir", str(keyed), "--out-dir", str(out), "--state", "walk",
                      "--anchor", "motion-auto", "--repair", "off", *extra]) == 0
    return json.loads((out / "loop.loop.report.json").read_text())


def _two_steps(report: dict, cycle: float) -> dict:
    """The cut covers two steps by the clip's crossings — one cycle of the walker, `cycle` frames — and says so."""
    c = report["cycle"]
    counted = c["step_count"]
    assert counted["readable"] and counted["chosen_by"] == "two steps" and c["method"] == "observed-two-steps-v1"
    assert counted["cut"]["verdict"] == "two" and counted["cut"]["k"] == [2] and counted["cut"]["ring"] == 2
    assert abs(counted["cut"]["phase"]) <= crossings.PHASE_TOLERANCE
    assert abs(c["length"] - cycle) <= 1.5
    assert c["steps"] == {"count": 2, "by": "crossings"}
    assert report["status"] == "passed"
    return counted


@pytest.mark.slow
def test_a_half_step_diagonal_walk_is_cut_two_steps(tmp_path):
    """Legs drawn alike, 14.5 frames a step, a tail on a 45-frame beat: one step on, the legs are swapped and the
    tail is nearer where it was than a cycle on, so the search takes 14 frames — one foot goes forward. The
    crossings count it one step; the cut is two."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _side(k, cycle=29, tail_cycle=45, tail=60, arms=False)))
    counted = _two_steps(report, 29)
    assert counted["mode"] == "gap" and counted["step_range"][1] == 14.5
    assert counted["search"]["verdict"] == "refuted" and counted["search"]["k"] == [1] and counted["search"]["cut"][1] in (14, 15)
    # Its feet pass briefly and no view is given: whether its crossings mark cycles is not asked.
    assert "cycles" not in counted
    strip = json.loads((tmp_path / "out" / "loop.strip.json").read_text())
    assert strip["steps"] == 2 and strip["cycle_frames"] == report["cycle"]["length"]
    # The step screen's pixel suspicion of a cycle twice as long is settled by the count.
    screen = report["cycle"]["step_screen"]
    assert screen["suspect"] is False and (screen.get("settled_by") == "crossings" or "pixel_suspect" not in screen)


@pytest.mark.slow
def test_a_walk_slower_than_the_window_is_cut_two_steps(tmp_path):
    """A 46-frame cycle (1.92 s) in a 73-frame clip: past the window's 36, so the search cut a 23-frame step. Two
    steps are 46 frames, and the clip holds 0.59 of a cycle past them."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _side(k, cycle=46, phase=13)))
    counted = _two_steps(report, 46)
    assert report["window"][1] < report["cycle"]["length"]
    assert counted["search"]["k"] == [1]
    assert report["cycle"]["coverage"] == round((FRAMES - report["cycle"]["length"]) / report["cycle"]["length"], 4) > loop.COVERAGE_MIN


@pytest.mark.slow
def test_a_tail_on_its_own_beat_no_longer_cuts_three_steps(tmp_path):
    """A front walk, 13.5 frames a step, its big tail on a 41-frame beat: the search refuses the walk's 27 frames
    (repeat error over two steps), and the gait fallback takes the tail's 41 — three steps, the wrap putting the
    nearer foot back: a stomp in place. The crossings count three and cut two."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _front(k, cycle=27, phase=5, tail_cycle=41, tail=70)))
    counted = _two_steps(report, 27)
    assert counted["mode"] == "sign" and counted["alternation"] <= crossings.ALTERNATION_MAX
    assert "gait_fallback" in report and counted["search"]["k"] == [3] and counted["search"]["cut"][1] in (40, 41, 42)


@pytest.mark.slow
def test_a_slow_walk_in_a_short_clip_the_search_refuses_is_cut_two_steps(tmp_path):
    """A 34-frame front walk with a tail on a 52-frame beat: no window repeats within two steps of its own, so the
    search and the gait fallback find no cycle. The crossings show two."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _front(k, cycle=34, phase=5, tail_cycle=52, tail=70)))
    counted = _two_steps(report, 34)
    assert counted["search"]["refused"].startswith("no periodic cycle found")


@pytest.mark.slow
def test_a_walk_that_stalls_is_not_cut_across_the_stalled_step(tmp_path):
    """A side walk held still 20 frames: the step the hold is in is far longer than the walk's own, and no frame of
    it is played (the search's cut played its last frames, and its seam did not close)."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _side(k, cycle=29, phase=3), frames=96, hold=(30, 20)))
    counted = _two_steps(report, 29)
    c = [x if isinstance(x, (int, float)) else x[1] for x in counted["crossings"]]
    hi = counted["step_range"][2]
    stalled = [(a, b) for a, b in zip(c, c[1:]) if b - a > hi]
    assert stalled == [(26.0, 60.5)]
    s, L = report["cycle"]["start"], report["cycle"]["length"]
    assert s + L - 1 < 26 or s > 60.5
    assert counted["search"]["code"] == "step-long"


def _one_cycle(out: Path, cycle: float) -> dict:
    """The cut in `out` is one cycle of the walker, `cycle` frames, two steps by its crossings read as cycles, and says
    so — in its strip too."""
    report = json.loads((out / "loop.loop.report.json").read_text())
    c = report["cycle"]
    counted = c["step_count"]
    assert abs(c["length"] - cycle) <= 1
    assert c["steps"] == {"count": 2, "by": "crossings"}
    assert counted["mode"] == "gap" and counted["cycles"]["cycles"] is True
    assert abs(counted["step_range"][1] - cycle / 2) <= 1
    assert counted["cut"]["verdict"] == "two" and counted["cut"]["k"] == [2] and counted["cut"]["ring"] == 2
    assert abs(counted["cut"]["phase"]) <= crossings.PHASE_TOLERANCE
    assert json.loads((out / "loop.strip.json").read_text())["steps"] == 2
    assert report["status"] == "passed"
    return counted


@pytest.mark.slow
@pytest.mark.parametrize("cycle, hold", [(20, 2), (24, 3), (17.5, 1), (18.5, 1)])
def test_a_walk_whose_far_foot_hides_is_cut_one_cycle(tmp_path, cycle, hold):
    """Seen from behind on a diagonal, the far foot passes hidden behind the near one: the feet are seen together over
    more than half the interval between two crossings, one crossing a cycle. The clip one interval on is the same
    picture, a cycle on, and the body rises and falls twice an interval — a step each rise — its arms swinging within
    its sides, every band of its upper body seen still on the beat: the count reads the crossings as cycles, two steps
    an interval. The cut is one cycle, `steps: 2` (the parent engine counted the cycle one step: on twos and threes it
    kept the search's cut unwritten, and on ones, a cycle a whole number of frames or not, cut two cycles, 35 and 37
    frames). With no view, no foot cue is read."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _behind(k, cycle=cycle, hold=hold, arm=ARMS_IN)))
    counted = _one_cycle(tmp_path / "out", cycle)
    said = counted["cycles"]
    assert report["drawings"]["hold"] == hold
    assert said["asked"].startswith("the feet together") and said["overlap"] > crossings.HIDE_SHARE
    assert said["one"] < said["half"] and "cue" not in said and "unwitnessed" not in said
    assert said["bob"]["says"] == "cycles" and said["bob"]["half"] >= local_cycle.BOB_TWICE and said["bob"]["swing"] >= local_cycle.BOB_FLOOR
    assert said["bob"]["body"]["share"] >= local_cycle.BOB_OWN and said["bob"]["body"]["scale"] >= local_cycle.BOB_SCALE
    assert said["bob"]["edge"] == 0 and "rises and falls twice an interval" in said["why"] and "the body's own" in said["why"]
    reads = [r for r in said["bob"]["body"]["bands"]["reads"] if r is not None]
    assert reads and said["bob"]["body"]["still_bands"] == len(reads)


@pytest.mark.slow
@pytest.mark.parametrize("view", [None, "back_diagonal"])
@pytest.mark.parametrize("cycle, hold", [(17.5, 1), (20, 2)])
def test_a_walk_whose_far_foot_hides_is_cut_one_cycle_however_alike_its_legs(tmp_path, view, cycle, hold):
    """The same walker with its far leg drawn as the near one: the view's shade of the feet does not move (`alike`) and
    says nothing, and the body's bob, which reads no leg, still witnesses two steps an interval: one cycle (the parent
    engine left legs drawn alike to the cue on steps and cut two cycles, with a warning)."""
    _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _behind(k, cycle=cycle, hold=hold, alike=True, arm=ARMS_IN)),
         *(("--view", view) if view else ()))
    said = _one_cycle(tmp_path / "out", cycle)["cycles"]
    assert said["bob"]["says"] == "cycles" and (view is None) == ("cue" not in said)
    assert view is None or said["cue"]["says"] == "alike"


def _unwitnessed(report: dict, interval: float) -> dict:
    """The cut is two crossing intervals long, `interval` frames each, two steps by its crossings left on steps because
    nothing that reads no leg witnesses two steps an interval (`cycles.unwitnessed`), and a warning names it."""
    c = report["cycle"]
    counted = c["step_count"]
    said = counted["cycles"]
    assert abs(c["length"] - 2 * interval) <= 1.5 and c["steps"] == {"count": 2, "by": "crossings"}
    assert counted["cut"]["verdict"] == "two" and report["status"] == "passed"
    assert said["one"] < said["half"] and said["one"] <= said["two"] + local_cycle.CYCLE_SLACK
    assert said["cycles"] is False and said["unwitnessed"] is True and said["bob"]["says"] is None
    assert "nothing that reads no leg witnesses two steps an interval" in said["why"]
    assert any(local_cycle.UNWITNESSED in w and f"the cut {c['start']}/{c['length']}" in w for w in report["jolt"]["warnings"])
    return said


@pytest.mark.slow
@pytest.mark.timeout(900)  # up to 12 calls of the binary at 300 x 220 (the held fill's frames between): seconds on a GPU, minutes on a CPU's Vulkan driver
@pytest.mark.parametrize("walker, view, interval, runs", [("behind", None, 17.5, 12), ("behind", "back_diagonal", 17.5, 12),
                                                          ("outward", "front_diagonal", 16, 0)])
def test_a_walk_whose_feet_meet_once_a_cycle_but_whose_body_does_not_bob_is_cut_two_intervals_with_a_warning(tmp_path, walker, view,
                                                                                                         interval, runs, rife_runs):
    """The two walkers whose feet meet once a cycle, their body held still: its top line does not rise and fall, and
    nothing else that reads no leg can tell a step on from a cycle on. The crossings stay on steps: two intervals, two
    cycles, named in a warning — never one interval read as two steps on a word that cannot tell (the parent engine cut
    one cycle on the picture where a pass hid, and on the foot cue's turn half an interval on from the view)."""
    draw = (lambda k: _behind(k, cycle=interval, hold=1, bob=0)) if walker == "behind" else (lambda k: _outward(k, cycle=interval, bob=0))
    report = _cut(tmp_path / "out", _keyed(tmp_path, draw), *(("--view", view) if view else ()))
    assert rife_runs() == (runs if rife_located() else 0)
    said = _unwitnessed(report, interval)
    assert said["bob"]["swing"] < local_cycle.BOB_FLOOR and "does not rise and fall" in said["bob"]["why"]


@pytest.mark.slow
@pytest.mark.parametrize("walker, view, interval", [("behind", None, 17.5), ("outward", "front_diagonal", 16)])
def test_a_walk_whose_feet_meet_once_a_cycle_its_arms_swinging_past_its_sides_is_cut_one_cycle(tmp_path, walker, view, interval):
    """The two walkers whose feet meet once a cycle, rising and falling on their legs twice an interval, their arms
    swinging 16 px either way of the body's middle, past its sides: the upper body widens and narrows twice an interval
    across the bands the arms swing over, and a zoom's part there is not told from theirs. The rows down the middle of
    the body tell it: a body rising whole carries them as they are, and a zoom as far as the rise draws them longer.
    They fit as they stand better than they fit that zoom, and they decide: two steps an interval, one cycle (the parent
    engine read the bands alone, left the crossings on steps and cut two intervals, two cycles, named)."""
    draw = (lambda k: _behind(k, cycle=interval, hold=1)) if walker == "behind" else (lambda k: _outward(k, cycle=interval))
    _cut(tmp_path / "out", _keyed(tmp_path, draw), *(("--view", view) if view else ()))
    bob = _one_cycle(tmp_path / "out", interval)["cycles"]["bob"]
    assert bob["says"] == "cycles" and bob["half"] >= local_cycle.BOB_TWICE and bob["quarter"] <= -local_cycle.BOB_TWICE
    assert bob["top_line"] == "top" and bob["feet"]["says"] == "cycles" and bob["feet"]["top_line"] == "top"
    body = bob["body"]
    assert "whole" not in body and body["depth"]["ratio"] >= local_cycle.BOB_RIGID
    assert body["depth"]["moved"] >= local_cycle.STRIP_FLOOR and body["scale"] < local_cycle.BOB_SCALE
    assert max(r for r in body["bands"]["reads"] if r is not None) > 1 - local_cycle.BOB_SCALE


@pytest.mark.slow
@pytest.mark.timeout(900)  # up to 10 calls of the binary at 300 x 220 (the held fill's frames between, both cuts): seconds on a GPU, minutes on a CPU's Vulkan driver
@pytest.mark.parametrize("cycle, runs", [(16, 0), (18, 10)])
def test_a_diagonal_walk_whose_near_foot_steps_out_is_cut_one_cycle_from_its_view(tmp_path, cycle, runs, rife_runs):
    """On a front diagonal the near foot steps out toward the camera and passes the far one apart: the feet meet on the
    screen once a cycle, and briefly — no pass hides. Given the view, the count asks the picture: the clip one interval
    on is the same picture, the legs as they were, the view's foot cue does not turn over there, and the body rises and
    falls twice an interval, its arms swinging within its sides. One cycle, `steps: 2` (the parent engine counted each
    meeting a step and cut two cycles, 32 and 36 frames). Without the view it is not asked, and cuts as before, two
    cycles: its body rises and falls four times in the cut, which stands, its strip counting two steps, and `video-set`
    leaves it out of its cycle alignment (`local_cycle.four_steps`)."""
    keyed = _keyed(tmp_path, lambda k: _outward(k, cycle=cycle, arm=ARMS_IN))
    _cut(tmp_path / "view", keyed, "--view", "front_diagonal")
    counted = _one_cycle(tmp_path / "view", cycle)
    assert counted["cycles"]["overlap"] <= crossings.HIDE_SHARE and counted["cycles"]["asked"] == "the front_diagonal view, the legs side by side"
    # The far leg a tone off the near one moves the cue a tone where the feet part: over the floor, and it does not turn
    # over one interval on — it does not refuse. Its turn half an interval on is no witness (a light fixed on the screen
    # turns a cue so too); the body's bob is.
    cue = counted["cycles"]["cue"]
    assert cue["says"] is None and cue["range"] >= cue["floor"] == pytest.approx(local_cycle.CUE_FLOOR * legs.TONE, rel=0.01)
    assert cue["one"] > local_cycle.CUE_SWAPPED and "half" not in cue
    assert counted["cycles"]["bob"]["says"] == "cycles"
    plain = _cut(tmp_path / "plain", keyed)
    assert "cycles" not in plain["cycle"]["step_count"] and abs(plain["cycle"]["length"] - 2 * cycle) <= 1
    said = plain["cycle"]["cut_witness"]
    assert said["says"] == "four steps" and local_cycle.four_steps(plain["cycle"]) and plain["cycle"]["steps"]["count"] == 2
    assert rife_runs() == (runs if rife_located() else 0)


@pytest.mark.slow
@pytest.mark.timeout(900)  # up to 12 calls of the binary at 300 x 220 (the held fill's frames between, both cuts): seconds on a GPU, minutes on a CPU's Vulkan driver
@pytest.mark.parametrize("cycle, runs", [(20, 10), (24, 12)])
def test_a_walk_whose_feet_stay_apart_and_meet_once_a_cycle_is_cut_one_cycle_on_the_order_its_feet_are_drawn_in(tmp_path, cycle, runs, rife_runs):
    """Seen from the front, the feet apart, the foot stepping toward the camera drawn lower: the feet meet on the screen
    once a cycle, and the body does not rise and fall. The clip shows its two sides of feet are its two legs — seen apart
    most of the time, one drawn lower and then the other by 6 rows either way, no foot planted on the ground sliding
    along it or passing under the body to the other side, the upper body still over them — and the foot drawn nearer
    changes sides twice an interval: the count reads the crossings as cycles, one cycle, `steps: 2`, and the cut stands
    on the same word (the parent engine left the crossings on steps, nothing that reads no leg witnessing them, and cut
    two cycles with a warning). Labelled the side view, the order is not read: two intervals, named."""
    keyed = _keyed(tmp_path, lambda k: _apart(k, cycle=cycle))
    _cut(tmp_path / "front", keyed, "--view", "front")
    said = _one_cycle(tmp_path / "front", cycle)["cycles"]
    assert said["bob"]["says"] is None and said["order"]["says"] == "two steps" and "unwitnessed" not in said
    assert said["order"]["slides"] == [] and said["order"]["travel"] < local_cycle.ORDER_TRAVEL * said["order"]["spacing"]
    assert "the foot drawn nearer changes sides" in said["why"]
    witness = json.loads((tmp_path / "front" / "loop.loop.report.json").read_text())["cycle"]["cut_witness"]
    assert witness["says"] == "two steps" and witness["by"] == ["order"] and "held" not in witness
    side = _cut(tmp_path / "side", keyed, "--view", "side")
    assert abs(side["cycle"]["length"] - 2 * cycle) <= 1.5 and local_cycle.unwitnessed(side["cycle"])
    assert "order" not in (side["cycle"]["step_count"].get("cycles") or {}) and "order" not in side["cycle"]["cut_witness"]
    assert rife_runs() == (runs if rife_located() else 0)


@pytest.mark.slow
@pytest.mark.parametrize("view", [None, "front", "front_diagonal", "back", "back_diagonal"])
def test_a_side_walk_whose_swing_foot_lifts_is_never_cut_on_the_order_its_feet_are_drawn_in_whatever_its_label(tmp_path, view):
    """The side walker with its legs drawn alike, the leg swinging forward lifted 6 px at its pass: one foot is drawn
    higher and then the other, and its label, where given, says a view the order is read from. The planted foot slides
    along the ground under the body from one side to the other, as only a foot seen from the side does: the clip's two
    sides of feet are its front and its back, not its two legs, and the order witnesses nothing — two steps, 46 frames,
    never one interval read as two steps on a wrong label."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, lift=6)),
                  *(("--view", view) if view else ()))
    _two_steps(report, 46)
    said = report["cycle"]["step_count"].get("cycles")
    if said is not None:
        assert said["cycles"] is False and said["order"]["says"] is None and "under the body" in said["order"]["why"]
    assert report["cycle"]["cut_witness"].get("by") != ["order"]


@pytest.mark.slow
def test_a_front_walk_whose_feet_meet_once_a_step_its_tail_dipping_into_the_foot_band_is_never_cut_one_step_unnamed(tmp_path):
    """Seen from the front, the feet apart, each swinging in to meet the other while it is drawn higher: the feet meet
    on the screen once a step, every 15 frames of a 30-frame cycle, and the foot drawn nearer changes sides once an
    interval. A tail's tip dips into the foot band beside the feet on a beat of its own. Labelled the front view, its
    crossings are never read as cycles, the order never stands a cut shorter than the cycle, and no cut under a cycle is
    handed out unnamed."""
    walker = _dipping_tail(lambda k: _apart(k, cycle=30, each_step=True), hip=(92, 100), reach=28)
    report = _cut(tmp_path / "out", _keyed(tmp_path, walker), "--view", "front")
    _not_silent(report, 30)
    c = report["cycle"]
    assert (c["step_count"].get("cycles") or {}).get("cycles") is not True
    assert c["cut_witness"].get("by") != ["order"] or abs(c["length"] - 30) <= 1.5, c["cut_witness"]


@pytest.mark.slow
@pytest.mark.parametrize("tone, lean", [(1, 0), (4, 1)])
def test_a_side_walk_whose_two_steps_look_alike_is_cut_two_steps_however_near_a_step_on(tmp_path, tone, lean):
    """The case the count was made for, guarded: from the side the two steps are one silhouette, the near leg and arm
    a tone or a few apart from the far ones. The walk is slower than the window, so the search cuts one step, and at
    one tone the clip a step on draws nearer than the next frame (0.89 playback steps). Its feet pass briefly — no pass
    can hide — and from the side the picture is not asked: the count stands, two steps, a cycle long. Its arms swing
    past its sides on the beat, across the bands of its upper body; the rows down the middle of the body read a body
    rising whole, and the cut stands witnessed (the parent engine held it, named)."""
    keyed = _keyed(tmp_path, lambda k: _alike(k, cycle=46, phase=13, tone=tone, lean=lean))
    for out, extra in (("plain", ()), ("side", ("--view", "side"))):
        report = _cut(tmp_path / out, keyed, *extra)
        counted = _two_steps(report, 46)
        assert counted["search"]["verdict"] == "refuted" and counted["search"]["k"] == [1]
        assert "cycles" not in counted and _strip_says_whole(_witnessed(report))


@pytest.mark.slow
def test_a_diagonal_walk_whose_foot_cue_turns_over_a_step_on_is_cut_two_steps(tmp_path):
    """The same walker filmed as a front diagonal: its legs a step on are one silhouette, so the clip one interval on is
    as near as a cycle on, but its far leg is drawn a few tones off the near one, and the view's foot cue turns over one
    interval on — the other leg leads. The count stands: two steps, a cycle long, its arms swinging past its sides and
    the rows down the middle of its body witnessing the cut."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1)),
                  "--view", "front_diagonal")
    counted = _two_steps(report, 46)
    said = counted["cycles"]
    assert said["cycles"] is False and said["one"] <= said["two"] + local_cycle.CYCLE_SLACK
    assert said["cue"]["one"] <= local_cycle.CUE_SWAPPED and said["cue"]["says"] == "steps" and "the other leg leads" in said["why"]
    assert "unwitnessed" not in said and _strip_says_whole(_witnessed(report))


@pytest.mark.slow
@pytest.mark.parametrize("view", ["front", "front_diagonal", "back", "back_diagonal"])
def test_a_walk_whose_legs_are_drawn_alike_is_cut_two_steps_from_any_view(tmp_path, view):
    """The same walker with its legs drawn alike, its arms still four tones apart, filmed from the front, the back or a
    diagonal: one interval on (a step on) is the same picture, as near as two on, and the view's foot cue does not move
    — the shade of legs drawn alike, feet drawn on one row — its range far under half the step it is drawn in. The body
    rises and falls once an interval, a step: nothing witnesses two steps an interval, and the crossings stay on steps,
    as from the side: two steps, 46 frames, and a warning naming why (the parent engine read the still cue's float
    roundings, or no correlation, as the same leg leading and cut 23 frames, one step, counted two)."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0)),
                  "--view", view)
    _two_steps(report, 46)
    said = _unwitnessed(report, 23)
    assert said["asked"] == f"the {view} view, the legs side by side" and said["overlap"] <= crossings.HIDE_SHARE
    assert said["cue"]["says"] == "alike" and said["cue"]["range"] < said["cue"]["floor"] and "one" not in said["cue"]
    assert said["bob"]["half"] < -local_cycle.BOB_TWICE


@pytest.mark.slow
@pytest.mark.parametrize("view", [None, "side", "front_diagonal", "back"])
def test_a_walk_whose_legs_are_drawn_alike_and_wide_is_cut_two_steps_where_a_pass_could_hide(tmp_path, view):
    """The same walker with its legs drawn alike and twice as wide: the feet are seen together over half the interval at
    each pass, as where the far foot passes hidden, so the count asks the picture with or without a view — and one
    interval on (a step on) is the same picture, as near as two on. The view's foot cue does not move (with no view
    none is read), and the body rises and falls once an interval: nothing witnesses two steps an interval, and the
    crossings stay on steps: two steps, 46 frames, and a warning naming why (the parent engine let the picture mark
    cycles and cut 23 frames, one step, counted two)."""
    keyed = _keyed(tmp_path, lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, leg_half=10))
    report = _cut(tmp_path / "out", keyed, *(("--view", view) if view else ()))
    _two_steps(report, 46)
    said = _unwitnessed(report, 23)
    assert said["asked"].startswith("the feet together") and said["overlap"] > crossings.HIDE_SHARE
    assert (said["cue"]["says"] == "alike" and said["cue"]["range"] < said["cue"]["floor"]) if view else "cue" not in said


@pytest.mark.slow
@pytest.mark.parametrize("view", [None, "side", "front_diagonal", "back_diagonal"])
def test_a_walk_whose_legs_are_drawn_alike_under_a_light_fixed_on_the_screen_is_cut_two_steps(tmp_path, view):
    """The wide walker with its legs drawn alike, lit from the right by a light fixed on the screen (up to 12 tones
    across the feet): the shade of the feet moves with how far apart they are drawn, whichever leg leads, and so turns
    over half an interval on. The body rises and falls once an interval: nothing that reads no leg witnesses two steps
    an interval, and the crossings stay on steps — 46 frames, a warning (the parent engine let the picture mark cycles
    where a pass could hide, and cut 23 frames, one step, counted two, from every one of these views)."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, leg_half=10,
                                                                       light=("x", 12))), *(("--view", view) if view else ()))
    _two_steps(report, 46)
    said = _unwitnessed(report, 23)
    assert view is None or (said["cue"]["says"] is None and said["cue"]["range"] >= said["cue"]["floor"])


@pytest.mark.slow
@pytest.mark.parametrize("view", ["front", "front_diagonal", "back", "back_diagonal"])
def test_a_walk_whose_swing_foot_lifts_under_a_light_fixed_on_the_screen_is_cut_two_steps(tmp_path, view):
    """The walker with its legs drawn alike, the leg swinging forward lifted 6 px at its pass, lit lower down by a light
    fixed on the screen: the depth and the shade of the feet move with the swing, whichever leg swings, and turn over
    half an interval on. Nothing that reads no leg witnesses two steps an interval: 46 frames, a warning (the parent
    engine read the cue's turn half an interval on as the other leg leading a step on, and cut 23 frames)."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, lift=6,
                                                                       light=("y", 12))), "--view", view)
    _two_steps(report, 46)
    assert _unwitnessed(report, 23)["cue"]["says"] in (None, "alike")


@pytest.mark.slow
@pytest.mark.parametrize("view", [None, "side", "front_diagonal"])
def test_a_wide_walk_whose_far_leg_is_drawn_a_few_tones_off_is_cut_two_steps(tmp_path, view):
    """The wide walker with its far leg four tones off the near one, its arms swinging within its sides: a pass can
    hide. From the side or a diagonal the view's shade turns over one interval on — the other leg leads — and refuses
    cycles; with no view no cue is read, and the body rising and falling once an interval witnesses nothing: two steps,
    46 frames either way, a warning only where nothing refused (the parent engine let the picture mark cycles with no
    view and cut 23 frames)."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_half=10,
                                                                       arm=ARMS_IN)),
                  *(("--view", view) if view else ()))
    counted = _two_steps(report, 46)
    if view is None:
        _unwitnessed(report, 23)
    else:
        said = counted["cycles"]
        assert said["cycles"] is False and said["cue"]["says"] == "steps" and "unwitnessed" not in said
        assert not any(local_cycle.UNWITNESSED in w for w in report["jolt"]["warnings"])


@pytest.mark.slow
@pytest.mark.timeout(900)  # up to 22 calls of the binary at about 300 x 340 (the held fill's frames between): seconds on a GPU, minutes on a CPU's Vulkan driver
@pytest.mark.parametrize("motion, bob, room, runs", [("arc", 3, 0, 8), ("arc", 0, 60, 22), ("sway", 0, 60, 6), ("ramp", 0, 60, 16)])
def test_a_walk_whose_crossings_are_its_steps_carried_up_and_down_is_cut_two_steps(tmp_path, motion, bob, room, runs, rife_runs):
    """The wide walker with its legs drawn alike, carried up and down the screen with the whole picture (`_carried`;
    its legs, their steps and their crossings every 23 frames as they were): bobbing, along a smooth arc 24 px deep;
    its body held still, along the same arc, a sway 12 px either way four intervals long, and a ramp 24 px down a row
    every three frames. None of it is the body rising and falling twice an interval: less its flow the bobbing
    walker's top line rises and falls once an interval, turned over half an interval on, and the still one's moves
    under BOB_ROWS (its rows rounding up and down as the picture drifts). Nothing witnesses two steps an interval: two
    steps, 46 frames, a warning (the parent engine read the flow's own likeness half an interval on as the body rising
    and falling twice an interval and cut each one step, counted two, and no warning said so)."""
    walker = lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, leg_half=10, bob=bob)  # noqa: E731
    report = _cut(tmp_path / "out", _keyed(tmp_path, _carried(walker, motion, room)))
    assert rife_runs() == (runs if rife_located() else 0)
    _two_steps(report, 46)
    said = _unwitnessed(report, 23)
    assert said["asked"].startswith("the feet together")
    if bob:
        assert said["bob"]["half"] <= -local_cycle.BOB_TWICE
    else:
        assert said["bob"]["rows"] < local_cycle.BOB_ROWS and "does not rise and fall" in said["bob"]["why"]


@pytest.mark.slow
@pytest.mark.parametrize("motion, room", [("arc", 0), ("cubic", 60)])
def test_a_walk_whose_feet_meet_once_a_cycle_carried_up_and_down_is_cut_one_cycle(tmp_path, motion, room):
    """The walker whose far foot passes hidden, its legs drawn alike, its arms swinging within its sides, carried along
    the arc or the cubic with the whole picture: less its flow its top line rises and falls twice an interval as it did
    with the picture still, one cycle."""
    walker = lambda k: _behind(k, cycle=17.5, hold=1, alike=True, arm=ARMS_IN)  # noqa: E731
    _cut(tmp_path / "out", _keyed(tmp_path, _carried(walker, motion, room)))
    said = _one_cycle(tmp_path / "out", 17.5)["cycles"]
    assert said["bob"]["says"] == "cycles" and said["bob"]["quarter"] <= -local_cycle.BOB_TWICE


@pytest.mark.slow
@pytest.mark.parametrize("walker, view, interval", [("behind", None, 17.5), ("outward", "front_diagonal", 16)])
def test_a_walk_carried_up_and_down_whole_feet_and_all_is_cut_two_intervals_with_a_warning(tmp_path, walker, view, interval):
    """The two walkers whose feet meet once a cycle, their body rising and falling twice an interval with the feet
    carried along (`whole`): the whole figure moved up and down on the walk's beat, pixel for pixel a picture shaken
    twice an interval over a body held still. Its top line alone rises and falls twice an interval, and the lowest row
    with it: nothing of it is the body's own, and nothing that reads no leg tells a step on from a cycle on. Two
    intervals, a warning naming why (the parent engine read the top line alone and cut one cycle)."""
    draw = ((lambda k: _behind(k, cycle=interval, hold=1, whole=True)) if walker == "behind" else
            (lambda k: _outward(k, cycle=interval, whole=True)))
    said = _unwitnessed(_cut(tmp_path / "out", _keyed(tmp_path, draw), *(("--view", view) if view else ())), interval)
    assert said["bob"]["half"] >= local_cycle.BOB_TWICE and said["bob"]["quarter"] <= -local_cycle.BOB_TWICE
    assert said["bob"]["body"]["share"] < local_cycle.BOB_OWN and "its lowest row rises and falls with it" in said["bob"]["why"]


@pytest.mark.slow
@pytest.mark.timeout(900)  # up to 8 calls of the binary at 300 x 340 (the held fill's frames between): seconds on a GPU, minutes on a CPU's Vulkan driver
@pytest.mark.parametrize("bob, runs", [(0, 8), (3, 4)])
def test_a_walk_on_a_picture_shaken_twice_an_interval_is_cut_two_steps_with_a_warning(tmp_path, bob, runs, rife_runs):
    """The wide walker with its legs drawn alike, its crossings its steps every 23 frames, held still (or rising and
    falling on its legs once a step), on a picture shaken 3 px up and down twice an interval — dy = round(3 sin(4 pi k /
    23)), a camera's shake, not the walk. Its top line on the screen rises and falls twice an interval (the parent engine
    read it so, as the body, and cut 23 frames, one step, counted two, and no warning said so), but its lowest row rises
    and falls with it, and the body's own rise and fall twice an interval is nothing: two steps, 46 frames, a
    warning."""
    walker = lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, leg_half=10, bob=bob)  # noqa: E731
    report = _cut(tmp_path / "out", _keyed(tmp_path, _carried(walker, lambda k: round(3 * math.sin(4 * math.pi * k / 23)))))
    assert rife_runs() == (runs if rife_located() else 0)
    said = _unwitnessed(report, 23)
    assert said["asked"].startswith("the feet together")
    if not bob:
        assert said["bob"]["half"] >= local_cycle.BOB_TWICE and said["bob"]["quarter"] <= -local_cycle.BOB_TWICE
        assert "its lowest row rises and falls with it" in said["bob"]["why"]


@pytest.mark.slow
def test_a_walk_whose_feet_run_off_the_frame_is_cut_two_intervals_with_a_warning(tmp_path):
    """The walker seen from behind whose feet meet once a cycle, rising and falling on its legs twice an interval, its
    frames cut at the soles' row: the lowest row is each frame's last. The feet may run off the frame there, so the
    lowest row is the frame's, not theirs, and the picture's moving cannot be read off it: nothing is witnessed, two
    intervals, a warning naming why."""
    walker = lambda k: _behind(k, cycle=17.5, hold=1).crop((0, 0, 300, GROUND + 1))  # noqa: E731
    said = _unwitnessed(_cut(tmp_path / "out", _keyed(tmp_path, walker)), 17.5)
    assert said["bob"]["edge"] > 0 and "the frame's last row" in said["bob"]["why"]


@pytest.mark.slow
def test_a_cut_of_two_steps_that_does_not_close_leaves_the_searchs_cut(tmp_path):
    """On threes, a big tail on its own beat: the search's cut is one the count does not refute (it plays feet held
    together past a pass: uncertain), and no cut of two steps joins the walk at its own point. The seam gate reads the
    first tried between its drawings and passes it (read frame by frame, every one tried was over the gate), but out
    of phase it is no two-step cut that closes. The search's cut stands and the loop passes; the record and a warning
    line name the two-step cut declined (the parent engine took it, and the seam gate failed the loop)."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, _tailed))
    c = report["cycle"]
    counted = c["step_count"]
    assert report["status"] == "passed" and report["drawings"]["hold"] == 3
    assert counted["chosen_by"] == "search" and counted["search"]["verdict"] == "uncertain"
    assert [c["start"], c["length"]] == counted["search"]["cut"] and "steps" not in c
    declined = counted["declined"]
    assert declined["gated"] is False and abs(declined["phase"]) > crossings.PHASE_TOLERANCE
    assert [t["seam"] for t in counted["seam_gate"]["tried"]] == [declined["seam"]] and declined["seam"] <= report["seam_max"]
    assert any("the search's cut" in w and "stands" in w for w in report["jolt"]["warnings"])


def _same_cut(a: Path, b: Path) -> None:
    for name in ("loop.strip.png", "loop.gif", "loop.webp"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    frames = sorted(p.name for p in (a / "cycle").glob("*.png"))
    assert frames and frames == sorted(p.name for p in (b / "cycle").glob("*.png"))
    assert all((a / "cycle" / f).read_bytes() == (b / "cycle" / f).read_bytes() for f in frames)


@pytest.mark.slow
def test_a_walk_the_search_cuts_two_steps_in_phase_is_cut_as_before(tmp_path):
    """The same front walk with a small tail: the search cuts its 27 frames, two steps joined at the walk's own point,
    and the cut is kept — every image as the search alone cut it; the strip says `steps: 2`."""
    keyed = _keyed(tmp_path, lambda k: _front(k, cycle=27, phase=5, tail_cycle=41, tail=40))
    report = _cut(tmp_path / "read", keyed)
    counted = report["cycle"]["step_count"]
    assert counted["chosen_by"] == "search" and counted["cut"]["verdict"] == "two"
    assert report["cycle"]["method"] == "local-repeat-drift-v1" and report["cycle"]["steps"] == {"count": 2, "by": "crossings"}
    with feet_unseen():
        plain = _cut(tmp_path / "unread", keyed)
    assert (plain["cycle"]["start"], plain["cycle"]["length"]) == (report["cycle"]["start"], report["cycle"]["length"])
    _same_cut(tmp_path / "read", tmp_path / "unread")
    strips = [json.loads((tmp_path / d / "loop.strip.json").read_text()) for d in ("read", "unread")]
    assert strips[0].pop("steps") == 2 and "steps" not in strips[1] and strips[0] == strips[1]


@pytest.mark.slow
def test_a_body_with_no_feet_to_read_is_cut_as_the_search_cuts_it(tmp_path):
    """A legless body bouncing every 16 frames — a walk the window admits (`walk`'s floor): no feet to read, and the
    search's cut stands as it cut it, never cut again two long. The search cuts one bounce, and the body rises and falls
    once in it, once a cycle; but it squashes as it bounces, and its upper body's widths, read on rows below its top,
    change with it on that beat: its own rise and fall is not told from the picture zooming, and nothing witnesses the
    cut — held and named, not cut again (two bounces). (The parent engine read the bounce in the cut as one step, which
    held it so too: to the body's word one step and a whole cycle of a body that rises and falls once a cycle are
    alike.)"""
    keyed = _keyed(tmp_path, _blob, frames=64)
    report = _cut(tmp_path / "out", keyed)
    c = report["cycle"]
    counted = c["step_count"]
    assert counted["readable"] is False and counted["chosen_by"] == "search" and counted["why"]
    assert counted["search"]["cut"] == [c["start"], c["length"]]
    assert "steps" not in report["cycle"] and "steps" not in json.loads((tmp_path / "out" / "loop.strip.json").read_text())
    said = _held(report)
    assert said["cut"] == [c["start"], c["length"]] and said["says"] is None and abs(c["length"] - 16) <= 1
    frames = [_blob(k) for k in range(64)]
    once = local_cycle.bob_word({**legs.strike_signals(frames), **legs.body_signals(frames)}, 2 * c["length"])
    assert once["half"] >= local_cycle.BOB_TWICE and once["quarter"] <= -local_cycle.BOB_TWICE
    assert once["says"] is None and "its upper body's width changes with it" in once["why"]


@pytest.mark.slow
def test_a_count_given_is_the_word_on_the_searchs_cut(tmp_path):
    """--steps counts the cut the search takes: the crossings are not asked."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _side(k, cycle=46, phase=13)), "--steps", "2")
    assert "step_count" not in report["cycle"] and report["cycle"]["steps"]["by"] == "given"
    assert report["cycle"]["length"] in (22, 23, 24)  # the search's one step, kept on the count


def _distances(period: int = 13, frames: int = 120) -> np.ndarray:
    t = np.arange(frames)
    angle = t * 2 * np.pi / period
    points = np.column_stack([np.cos(angle), np.sin(angle)])
    return np.abs(points[:, None] - points[None, :]).mean(axis=2).astype(np.float32)


@pytest.mark.parametrize("uncertain_from", [None, 5])
def test_the_search_refuses_what_the_crossings_refute_and_names_their_count(uncertain_from):
    """`detect` with `judge`: a window the crossings count other than two is refused, and a search that finds nothing
    lists each refused window's count, those the crossings refute after the rest."""
    def judge(s, L):
        if uncertain_from is not None and s >= uncertain_from:
            return {"verdict": "uncertain", "code": "partial", "k": [], "why": "plays frames no crossing confirms"}
        return {"verdict": "refuted", "code": "refuted", "k": [1], "why": "one step, each seen"}

    D = _distances()
    if uncertain_from is not None:
        found = local_cycle.detect(D, np.zeros(len(D)), min_len=12, max_len=14, gait_floor=8, periodicity_min=loop.PERIODICITY_MIN,
                                   double_tolerance=loop.GAIT_DOUBLE_TOL, double_search=loop.GAIT_DOUBLE_SEARCH, judge=judge)
        assert found["start"] >= uncertain_from
        return
    with pytest.raises(ValueError, match="no periodic cycle found") as refused:
        local_cycle.detect(D, np.zeros(len(D)), min_len=12, max_len=14, gait_floor=8, periodicity_min=loop.PERIODICITY_MIN,
                           double_tolerance=loop.GAIT_DOUBLE_TOL, double_search=loop.GAIT_DOUBLE_SEARCH, judge=judge)
    shown = refused.value.diagnostics["candidates"]
    assert shown and all(row["step_count"]["verdict"] == "refuted" for row in shown)
    assert any("one step, each seen in the clip's crossings" == row["refused"] for row in shown)
    assert "refute after the rest" in refused.value.diagnostics["order"]


def _reading(run: int) -> crossings.Reading:
    """A clip whose feet meet every 15 frames (crossings at 8, 23 … 68), seen together `run` frames at each meeting
    (9: over half the interval, a pass can hide there; 3: brief)."""
    middles = (8, 23, 38, 53, 68)
    return crossings.Reading(FRAMES, "gap", [(float(c), float(c)) for c in middles],
                             runs=[(c - run // 2, c + run // 2) for c in middles])


def _column(outline: str = "straight") -> np.ndarray:
    """The alpha of a body 100 rows tall from row 40: its sides running straight down 20 px apart (`straight`), or a
    cone from a point at its top, 20 px wide at its middle (`cone`)."""
    frame = np.zeros((200, 120), np.uint8)
    for r in range(100):
        half = 10 if outline == "straight" else max(1, round(r / 5))
        frame[40 + r, 60 - half:60 + half] = 255
    return frame


def _body(top, *, lowest=None, width=None, edge=None, sole=None, outline="straight") -> dict:
    """Leg signals (`legs.strike_signals`, `legs.body_signals`) of a body 100 rows tall whose top line is `top` (per
    frame), its lowest row `lowest` (140 throughout: the feet on the ground), its lowest opaque row `sole` (the lowest
    row's: nothing translucent under the feet) and each band of its upper body `width` wide (px; 20 throughout), drawn
    as `_column` draws its `outline`, no lowest row on the frame's last row unless `edge` says where."""
    n = len(top)
    reach = [140.0] * n if lowest is None else [float(v) for v in lowest]
    rows = legs.band_rows(100)
    widths = [20.0] * n if width is None else [float(v) for v in width]
    return {"top": [float(v) for v in top], "reach": reach, "sole": list(reach) if sole is None else [float(v) for v in sole],
            "bands": [[w] * len(rows) for w in widths], "band_rows": rows, "median_frame": _column(outline),
            "edge": [0.0] * n if edge is None else [float(v) for v in edge], "height": [100.0] * n}


def _bob(beat: float | None) -> dict:
    """Leg signals whose body's top line rises and falls 3 rows either way every `beat` frames on legs whose feet stay
    on the ground (None: held still), the body 100 rows tall: every 7.5 frames it rises and falls twice an interval of
    `_reading`, every 15 once."""
    t = np.arange(FRAMES)
    return _body(40 + (np.zeros(FRAMES) if beat is None else 3 * np.cos(2 * np.pi * (t - 8) / beat)))


def _choose(found: dict, *, period: int, seam: float, run: int = 9, search_seam: float = 1.0, view: str | None = None,
            cue: list[float] | None = None, cue_step: float | None = None, bob: float | None = None) -> tuple[dict, dict]:
    """`by_steps` on a clip whose picture goes round every `period` frames and whose feet meet every 15 (`_reading`),
    its crossings read as `read_cycles` reads them from `view`, its foot `cue` (drawn in steps of `cue_step`, where
    given) and its body's bob (`_bob`, every `bob` frames), with the search's cut `found`: every two-step cut's seam
    reads `seam` at the gate, its top calm and its flow off 1.0 either side, and the search's own cut `search_seam`."""
    D = _distances(period=period, frames=FRAMES)
    reading = _reading(run)
    local_cycle.read_cycles(reading, D, view=view, cue=cue, signals=_bob(bob), **({} if cue_step is None else {"cue_step": cue_step}))
    return local_cycle.by_steps(
        D, np.zeros(FRAMES), reading, found=dict(found), refused=None, min_len=12, max_len=FRAMES,
        seam_flow=lambda s, L: {"off": [1.0, 1.0], "turn": [0.0, 0.0], "window": [[0, 0, 1, 1], [0, 0, 1, 1]]},
        wrap_pop=lambda s, L: {"pops": False, "pop": 0.1},
        seam_gate=lambda s, L: search_seam if (s, L) == (found["start"], found["length"]) else seam, flow_limit=3.0, seam_max=2.0,
        gait_floor=12, periodicity_min=loop.PERIODICITY_MIN, signals=None)


@pytest.mark.parametrize("seam, search_seam, kept", [(3.0, 1.0, True), (1.0, 1.0, False), (3.0, 3.0, False)])
def test_a_cut_the_count_does_not_refute_gives_way_only_to_a_two_step_cut_that_closes(seam, search_seam, kept):
    """The search's cut 10/33 covers two steps a fifth of a step off (out of phase, not refuted), and the seam gate
    passes it. A two-step cut in phase replaces it only where the seam gate passes that too; where none passes, the
    search's cut stands and the record names the two-step cut it declined, with a warning line (the parent engine took
    the two-step cut either way). A search's cut the gate refuses as well gives way: the two-step cut's wrap may yet be
    closed by the overlapped wrap."""
    picked, record = _choose({"start": 10, "length": 33}, period=30, seam=seam, search_seam=search_seam)
    assert record["search"]["verdict"] == "two" and abs(record["search"]["phase"]) > crossings.PHASE_TOLERANCE
    if kept:
        assert (picked["start"], picked["length"]) == (10, 33) and record["chosen_by"] == "search"
        assert record["declined"]["seam"] == seam and record["declined"]["gated"] is True
        assert picked["steps"] == {"count": 2, "by": "crossings"}
        warned = local_cycle.step_verdict({**picked, "step_count": record})
        assert len(warned) == 1 and "the search's cut 10/33 stands" in warned[0]
    else:
        assert record["chosen_by"] == "two steps" and "declined" not in record and picked["length"] != 33


def test_a_cut_the_count_refutes_gives_way_to_two_steps_closing_or_not():
    """A search's cut the count refutes, one step seen, is a fault of its own: the two-step cut is taken though its seam
    fails the gate, and the gate says whether the loop passes. The picture goes round every 30 frames: one interval on
    it is half a cycle off, and the crossings mark steps."""
    picked, record = _choose({"start": 10, "length": 15}, period=30, seam=3.0)
    assert record["cycles"]["cycles"] is False and record["cycles"]["one"] > record["cycles"]["half"]
    assert record["search"]["verdict"] == "refuted" and record["search"]["k"] == [1]
    assert record["chosen_by"] == "two steps" and picked["steps"] == {"count": 2, "by": "crossings"} and abs(picked["length"] - 30) <= 1


@pytest.mark.parametrize("bob", [7.5, 15, None])
def test_where_a_pass_can_hide_and_the_picture_repeats_each_interval_the_body_says_whether_the_crossings_mark_cycles(bob):
    """The same meetings on a clip whose picture goes round every 15 frames, the feet together 9 frames at each: a pass
    can hide there, and one interval on the clip is the same picture as two on. Where the body rises and falls twice an
    interval, the crossings mark cycles: the step is half the interval, and the search's cut 10/15 is two steps joined at
    the walk's own point — it stands, `steps` written. Where it rises and falls once an interval, or is held still, the
    picture alone cannot tell a step on from a cycle on (legs drawn alike are the same picture a step on): the crossings
    stay on steps, two steps of 15, `unwitnessed`, a warning naming it (the parent engine let the picture mark cycles)."""
    picked, record = _choose({"start": 10, "length": 15}, period=15, seam=1.0, bob=bob)
    said = record["cycles"]
    assert said["asked"].startswith("the feet together") and said["one"] <= said["two"] + local_cycle.CYCLE_SLACK and "cue" not in said
    if bob == 7.5:
        assert said["cycles"] is True and said["bob"]["says"] == "cycles" and said["bob"]["half"] >= local_cycle.BOB_TWICE
        assert record["step_range"][1] == 7.5 and "unwitnessed" not in said
        assert (picked["start"], picked["length"]) == (10, 15) and picked["steps"] == {"count": 2, "by": "crossings"}
        assert record["chosen_by"] == "search" and record["cut"]["verdict"] == "two"
        assert local_cycle.step_verdict({**picked, "step_count": record}) == []
    else:
        assert said["cycles"] is False and said["unwitnessed"] is True and said["bob"]["says"] is None
        assert ("does not rise and fall" if bob is None else "does not rise and fall twice an interval") in said["bob"]["why"]
        assert ("half" not in said["bob"]) if bob is None else said["bob"]["half"] < -local_cycle.BOB_TWICE
        assert record["chosen_by"] == "two steps" and abs(picked["length"] - 30) <= 1
        warned = [w for w in local_cycle.step_verdict({**picked, "step_count": record}) if local_cycle.UNWITNESSED in w]
        assert len(warned) == 1 and said["bob"]["why"] in warned[0]


@pytest.mark.parametrize("view", [None, "side"])
def test_where_the_feet_meet_briefly_the_count_stands_from_the_side_or_no_view(view):
    """The same clip, its feet seen together only at the moment they meet: no pass can hide, and from the side (or with
    no view given) a step on drawing as near as a cycle on is what legs drawn alike do. Not asked: two steps."""
    picked, record = _choose({"start": 10, "length": 15}, period=15, seam=1.0, run=3, view=view, bob=7.5)
    assert "cycles" not in record
    assert record["search"]["verdict"] == "refuted" and record["chosen_by"] == "two steps" and abs(picked["length"] - 30) <= 1


@pytest.mark.parametrize("cue, bob, cycles", [("one", 7.5, False), ("half", 15, False), ("half", 7.5, True)])
def test_from_a_diagonal_the_foot_cue_only_refuses_cycles(cue, bob, cycles):
    """The same brief meetings from a front diagonal: asked, and one interval on is the same picture. Where the view's
    foot cue turns over one interval on (the near foot's shade leading one interval, the far one's the next) the other
    leg leads there: the count stands, two steps of 15, whatever the body says. A cue that turns over half an interval
    on and holds one on is no witness — a light fixed on the screen turns the shade of feet drawn apart so, whichever
    leg leads: with the body rising and falling once an interval, the crossings stay on steps with a warning (the
    parent engine read the turn as the other leg leading a step on and kept 15 frames as two steps); rising and falling
    twice, they mark cycles and the search's 15 frames are the cycle."""
    t = np.arange(FRAMES)
    values = list(np.sin(2 * np.pi * (t - 8) / (30 if cue == "one" else 15)))
    picked, record = _choose({"start": 10, "length": 15}, period=15, seam=1.0, run=3, view="front_diagonal", cue=values,
                             cue_step=legs.TONE, bob=bob)
    said = record["cycles"]
    assert said["asked"] == "the front_diagonal view, the legs side by side" and said["cycles"] is cycles
    assert said["cue"]["says"] == ("steps" if cue == "one" else None) and "half" not in said["cue"]
    assert (said["cue"]["one"] <= local_cycle.CUE_SWAPPED) is (cue == "one")
    assert said.get("unwitnessed", False) is (cue == "half" and not cycles)
    assert abs(picked["length"] - (15 if cycles else 30)) <= (0 if cycles else 1)
    warned = local_cycle.step_verdict({**picked, "step_count": record})
    assert any(local_cycle.UNWITNESSED in w for w in warned) is (cue == "half" and not cycles)


@pytest.mark.parametrize("view, run", [("front_diagonal", 3), ("back_diagonal", 9), (None, 9)])
@pytest.mark.parametrize("cue", ["still", "own beat"])
@pytest.mark.parametrize("bob", [7.5, None])
def test_a_cue_that_does_not_tell_the_legs_apart_leaves_the_word_to_the_body(view, run, cue, bob):
    """The same meetings, the clip one interval on the same picture, from a diagonal (passes brief or long) or with no
    view where a pass can hide, and a foot cue that does not tell the legs apart: legs drawn alike leave it still — a
    few float roundings, its range far under half a tone (`legs.TONE`), not read, however well the roundings, which
    repeat with the legs, correlate — or it flickers on a beat of its own (every 4 frames) and does not turn over one
    interval on. It refuses nothing; with no view it is not read at all. The body says: rising and falling twice an
    interval, the crossings mark cycles, one cycle of 15 (the parent engine left a still cue on steps, two cycles);
    held still, they stay on steps, two steps of 15, with a warning."""
    t = np.arange(FRAMES)
    values = 1e-8 * np.sin(2 * np.pi * (t - 8) / 15) if cue == "still" else 0.05 * np.cos(2 * np.pi * t / 4)
    picked, record = _choose({"start": 10, "length": 15}, period=15, seam=1.0, run=run, view=view, cue=list(values),
                             bob=bob, **({"cue_step": legs.TONE} if cue == "still" else {}))
    said = record["cycles"]
    assert said["one"] < said["half"] and said["one"] <= said["two"] + local_cycle.CYCLE_SLACK
    if view is None:
        assert "cue" not in said
    else:
        assert said["cue"]["says"] == ("alike" if cue == "still" else None)
        assert ("one" not in said["cue"]) if cue == "still" else said["cue"]["one"] > local_cycle.CUE_SWAPPED
    assert said["cycles"] is (bob is not None) and said.get("unwitnessed", False) is (bob is None)
    assert abs(picked["length"] - (15 if bob else 30)) <= (0 if bob else 1)
    assert any(local_cycle.UNWITNESSED in w for w in local_cycle.step_verdict({**picked, "step_count": record})) is (bob is None)


@pytest.mark.parametrize("swing, beat, says", [(3, 7.5, "cycles"), (3, 15, None), (0.2, 7.5, None), (3, 30, None)])
def test_the_body_witnesses_two_steps_an_interval_where_its_top_line_rises_and_falls_twice(swing, beat, says):
    """`bob_word` on an interval of 15 frames, the body 100 rows tall: a top line rising and falling 3 rows either way
    every 7.5 frames (twice an interval: as it was half an interval on, turned over a quarter on) witnesses two steps
    an interval; every 15 (once: a step an interval, turned over half an interval on) or every 30 (once a cycle of two
    intervals: a quarter turn half an interval on, and much of it taken out as flow) does not; 0.2 rows either way, its
    range under BOB_FLOOR of the body's height and under BOB_ROWS rows, does not rise and fall, however it correlates."""
    t = np.arange(FRAMES)
    line = list(40 + swing * np.cos(2 * np.pi * t / beat))
    word = local_cycle.bob_word(_body(line), 15)
    assert word["says"] == says and word["floor"] == local_cycle.BOB_FLOOR and word["twice"] == local_cycle.BOB_TWICE
    left = local_cycle.off_flow(line, 15)
    assert word["swing"] == pytest.approx(np.ptp(left) / 100, abs=1e-4)
    assert word["rows"] == pytest.approx(np.ptp(left), abs=0.01)
    if swing < 1:
        assert "half" not in word and "does not rise and fall" in word["why"]
    elif beat < 30:
        twice = word["half"] >= local_cycle.BOB_TWICE and word["quarter"] <= -local_cycle.BOB_TWICE
        assert twice is (says == "cycles")


@pytest.mark.parametrize("flow", ["still", "arc", "cubic", "sway", "ramp"])
@pytest.mark.parametrize("beat", [7.5, 15, None])
def test_the_body_is_read_less_the_flow_of_the_picture(beat, flow):
    """`bob_word` on an interval of 15 frames, the body 100 rows tall, its top line read in whole rows: rising and
    falling 3 rows either way twice an interval (every 7.5 frames), once (every 15) or not at all on legs whose feet
    stay on the ground, on a picture held still or carried up and down with the body, feet and all: along an arc 24 rows
    deep, a cubic 48 rows either way, a sway 12 rows either way four intervals long, a ramp of 6 rows over the clip.
    Less its flow the line is the body's own: twice an interval witnesses two steps an interval wherever the picture
    goes, and once or not at all never does; a line that does not rise and fall is left under BOB_ROWS by the picture
    drifting in whole rows."""
    t = np.arange(FRAMES)
    flows = {"still": 0 * t, "arc": 24 * ((t - 36) / 36) ** 2, "cubic": 48 * ((t - 36) / 36) ** 3,
             "sway": 12 * np.sin(2 * np.pi * t / 60), "ramp": 6 * t / 72}
    top = np.round(40 + (0 if beat is None else 3 * np.cos(2 * np.pi * t / beat)) + flows[flow])
    word = local_cycle.bob_word(_body(top, lowest=np.round(140 + flows[flow])), 15)
    assert (word["says"] == "cycles") is (beat == 7.5)
    if beat is None and flow != "sway":
        assert word["rows"] < local_cycle.BOB_ROWS and "does not rise and fall" in word["why"]


def test_the_flow_taken_out_leaves_a_rise_and_fall_once_or_twice_an_interval_whole():
    """`off_flow` over an interval of 15 frames (and of 17.5, its end frames a quarter weighted): a cubic flow is taken
    out wholly, and a rise and fall once or twice an interval laid on it is left as it was, over the frames the
    interval fits about (7 either end; 9 at 17.5), less its straight line."""
    t = np.arange(FRAMES)
    flow = 48 * ((t - 36) / 36) ** 3 - 5 * t / 72 + 40
    for interval, reach in ((15, 7), (17.5, 9)):
        for beat in (interval, interval / 2):
            rise = 3 * np.cos(2 * np.pi * t / beat)
            left = local_cycle.off_flow(list(flow + rise), interval)
            assert left.size == FRAMES - 2 * reach
            inner, u = rise[reach:FRAMES - reach], np.arange(FRAMES - 2 * reach)
            whole = inner - np.polyval(np.polyfit(u, inner, 1), u)
            assert np.max(np.abs(left - whole)) < (1e-9 if interval == 15 else 0.05)
        assert np.max(np.abs(local_cycle.off_flow(list(flow), interval))) < 1e-9


def test_the_rise_and_fall_twice_an_interval_reads_as_far_as_the_line_moves():
    """`twice_an_interval` over an interval of 15 frames (and 17.5): a line rising and falling 3 rows either way twice an
    interval, with a rise and fall once an interval and a cubic flow laid on it, reads 3 rows; two lines read over the
    same frames subtract as they move — the same rise and fall on another flow leaves next to nothing. Under BEAT_FRAMES
    frames left less the flow, nothing is read."""
    t = np.arange(FRAMES)
    for interval in (15, 17.5):
        flow = 48 * ((t - 36) / 36) ** 3 + 2 * np.cos(2 * np.pi * t / interval)
        rise = 3 * np.cos(4 * np.pi * t / interval + 1)
        a = local_cycle.twice_an_interval(list(flow + rise), interval)
        assert abs(a) == pytest.approx(3, abs=0.15)
        b = local_cycle.twice_an_interval(list(5 + rise), interval)
        assert abs(a - b) < 0.15
    assert local_cycle.twice_an_interval(list(range(14)), 15) is None


@pytest.mark.parametrize("motion", ["planted", "moved", "planted and shaken"])
def test_a_rise_and_fall_the_lowest_row_shares_is_the_picture_moving(motion):
    """`bob_word` on an interval of 15 frames, the body 100 rows tall, its lines read in whole rows. A top line rising
    and falling 3 rows either way twice an interval on legs whose feet stay on the ground (`planted`) is the body's own,
    all of it left against the lowest row: two steps an interval. The same rise and fall with the lowest row rising and
    falling with it (`moved`) — the body carried up and down whole, feet and all, or a body held still on a picture
    shaken twice an interval, the same lines either way — is the picture moving: the line alone rises and falls twice
    an interval, and nothing of it is left against the lowest row. A body's own rise and fall of 3 rows on a picture
    shaken 12 rows with it (`planted and shaken`) is a fifth of the line's, under BOB_OWN: not told from the picture.
    Neither witnesses anything."""
    t = np.arange(FRAMES)
    rise = 3 * np.cos(2 * np.pi * t / 7.5)
    own, moved = {"planted": (rise, 0 * t), "moved": (0 * t, rise), "planted and shaken": (rise, 4 * rise)}[motion]
    word = local_cycle.bob_word(_body(np.round(40 + own + moved), lowest=np.round(140 + moved)), 15)
    assert word["half"] >= local_cycle.BOB_TWICE and word["quarter"] <= -local_cycle.BOB_TWICE and word["edge"] == 0
    body = word["body"]
    assert body["least"] == local_cycle.BOB_OWN and body["scale_least"] == local_cycle.BOB_SCALE
    if motion == "planted":
        assert word["says"] == "cycles" and body["share"] > 0.9 and body["scale"] == pytest.approx(1)
    else:
        assert word["says"] is None and body["share"] < local_cycle.BOB_OWN
        assert "its lowest row rises and falls with it" in word["why"] and "the picture moves with the body" in word["why"]


@pytest.mark.parametrize("about", ["feet", "middle"])
def test_a_rise_and_fall_the_upper_bodys_width_shares_is_the_picture_zooming(about):
    """`bob_word` on an interval of 15 frames: a body 100 rows tall, its sides running straight down, zoomed in and out
    with the picture twice an interval about its feet (3 % either way: its top line rising and falling 3 rows, its
    lowest row still) or about its middle (6 %: the top line and the lowest row 3 rows either way, turned over from each
    other). Against the lowest row the top line rises and falls as a body on its legs would, but every band of its upper
    body widens as the line lifts, as much as a zoom so far would widen it: less the zoom each reads nothing is left.
    The picture zooms; nothing is witnessed."""
    t = np.arange(FRAMES)
    f = 1 + (0.03 if about == "feet" else 0.06) * np.sin(2 * np.pi * t / 7.5)
    at = 140 if about == "feet" else 90
    top, lowest = np.round(at - (at - 40) * f), np.round(at + (140 - at) * f)
    word = local_cycle.bob_word({**_body(top, lowest=lowest, width=20 * f), "height": list(100 * f)}, 15)
    assert word["half"] >= local_cycle.BOB_TWICE and word["quarter"] <= -local_cycle.BOB_TWICE
    assert word["says"] is None and word["body"]["scale"] < local_cycle.BOB_SCALE
    assert all(z >= local_cycle.BAND_FLOOR for z in word["body"]["bands"]["zoom"])
    assert "its upper body's width changes with it" in word["why"] and "the picture may zoom with the body" in word["why"]


def test_a_body_no_band_of_which_would_show_a_zoom_is_never_witnessed():
    """A top line rising and falling twice an interval on legs whose feet stay on the ground witnesses two steps an
    interval on a body whose sides run straight down; on a cone from a point at its top, the same line does not: a zoom
    as far as the line rises and falls would move no band of it a pixel (each band's width at its depth is the same
    zoomed), so its width could not tell the picture zooming from the body's own. Held by default, the word says why."""
    t = np.arange(FRAMES)
    top = np.round(40 + 3 * np.cos(2 * np.pi * t / 7.5))
    straight = local_cycle.bob_word(_body(top), 15)
    assert straight["says"] == "cycles" and straight["body"]["scale"] == pytest.approx(1)
    rows = legs.band_rows(100)
    cone = local_cycle.bob_word({**_body(top, outline="cone"), "bands": [legs.band_widths(_column("cone"), rows)] * FRAMES}, 15)
    assert cone["says"] is None and cone["body"]["less_bands"] is None and cone["body"]["scale"] is None
    assert all(abs(z) < local_cycle.BAND_FLOOR for z in cone["body"]["bands"]["zoom"])
    assert "no band of its upper body would show the picture zooming" in cone["why"]


def test_a_body_any_band_of_whose_upper_body_moves_on_the_beat_is_never_witnessed():
    """A top line rising and falling 3 rows either way twice an interval on legs whose feet stay on the ground, the
    body's sides running straight down, every band of its upper body widening and narrowing 2 px either way twice an
    interval a quarter of the beat on — as arms swinging across them do, or a zoom whose turn the line hides: more than
    a zoom so far would move them, and the zoom's part is not told from the swing at any turn. Held by default, the
    word says why. So with the band across its head's top holding still, the rest swinging so: a head drawn narrower as
    the picture zooms in holds still too, and one band holding still says only that no zoom shows in it (the parent
    engine took it as the witness that nothing zooms). With every band holding still, the body's own is told: two steps
    an interval."""
    t = np.arange(FRAMES)
    top = np.round(40 + 3 * np.cos(2 * np.pi * t / 7.5))
    swing = list(20 + 2 * np.cos(2 * np.pi * t / 7.5 + np.pi / 2))
    rows = legs.band_rows(100)
    for still in (0, 1):
        word = local_cycle.bob_word({**_body(top), "bands": [[20.0] * still + [w] * (len(rows) - still) for w in swing]}, 15)
        assert word["says"] is None and word["body"]["still_bands"] == still, still
        assert word["body"]["scale"] < local_cycle.BOB_SCALE and max(word["body"]["bands"]["reads"]) > 1 - local_cycle.BOB_SCALE
        assert "its upper body's width changes with it" in word["why"] and "the picture may zoom with the body" in word["why"]
    assert local_cycle.bob_word(_body(top), 15)["says"] == "cycles"


def _shrugging(k: int) -> Image.Image:
    """A column 20 px wide on feet planted at row 140, its top rising and falling 3 rows either way twice an interval of
    16 frames; its crown (11 rows) and the column under its shoulders hold still, and its shoulders (the 41 rows under
    the crown) widen and narrow 2 px either way a quarter of the beat on."""
    phase = 4 * np.pi * k / 16
    first, half = 40 + round(3 * np.cos(phase)), 10 + round(2 * np.sin(phase))
    alpha = np.zeros((200, 120), np.uint8)
    alpha[first:141, 50:70] = 255
    alpha[first + 11:first + 52] = 0
    alpha[first + 11:first + 52, 60 - half:60 + half] = 255
    rgba = np.zeros((200, 120, 4), np.uint8)
    rgba[:, :, :3] = (160, 100, 60)
    rgba[:, :, 3] = alpha
    return Image.fromarray(rgba)


def test_a_body_whose_shoulders_swing_on_the_beat_is_never_witnessed_however_its_crown_holds_still():
    """`bob_word` off drawn frames (128) at an interval of 16: a column on planted feet rising and falling twice an
    interval, its crown and the column under its shoulders holding still, its shoulders swinging a quarter of the beat
    on (`_shrugging`). Nothing zooms, but band by band it reads as a picture zoomed with the body whose crown and column
    are drawn against the zoom and whose shoulders swing over it: five bands are seen, one holds still, four move on the
    beat. Drawn in one flat colour down its middle, the rows there would not show such a zoom and do not tell, so the
    bands decide. The parent engine witnessed it on the one holding still; held, named."""
    frames = [_shrugging(k) for k in range(128)]
    word = local_cycle.bob_word({**legs.strike_signals(frames), **legs.body_signals(frames)}, 16)
    assert word["half"] >= local_cycle.BOB_TWICE and word["quarter"] <= -local_cycle.BOB_TWICE
    assert sum(r is not None for r in word["body"]["bands"]["reads"]) == 5 and word["body"]["still_bands"] == 1
    assert word["body"]["depth"]["moved"] < local_cycle.STRIP_FLOOR
    assert word["says"] is None and "its upper body's width changes with it" in word["why"]
    assert "the rows down the middle of its body do not tell" in word["why"]


def test_a_lowest_row_on_the_frames_last_row_holds_the_word():
    """A top line rising and falling twice an interval on legs whose feet stay on the ground witnesses two steps an
    interval; the same with the lowest row on the frame's last row in one frame does not: the feet may run off the
    frame there, and the lowest row is the frame's, not theirs. The word says why."""
    t = np.arange(FRAMES)
    top = np.round(40 + 3 * np.cos(2 * np.pi * t / 7.5))
    assert local_cycle.bob_word(_body(top), 15)["says"] == "cycles"
    word = local_cycle.bob_word(_body(top, edge=(t == 30)), 15)
    assert word["says"] is None and word["edge"] == 1 and f"the frame's last row in 1 of {FRAMES} frames" in word["why"]


def test_a_cut_of_a_body_whose_top_line_holds_still_says_why_whatever_its_length():
    """The walker whose feet stay apart, its body still, a cycle 30 frames: its top line holds on the screen, and its
    lowest row (the foot drawn nearer, lower) rises and falls once a step. Read over its feet on cuts of 10 to 46
    frames, the line rises and falls as the lowest row does, and none of it is the top line's own: each cut says why
    (the parent engine stopped there, dividing by that none, on cuts of 13, 17, 25, 26, 34 and 35 frames)."""
    frames = [_apart(k, cycle=30) for k in range(FRAMES)]
    signals = {**legs.strike_signals(frames), **legs.body_signals(frames)}
    words = {length: local_cycle.cut_word(signals, length) for length in range(10, 47)}
    still = [length for length, word in words.items() if "its top line holding still" in (word.get("why") or "")]
    assert still == [25, 26, 34, 35] and all(words[length]["says"] is None for length in still)


@pytest.mark.parametrize("off", [0, 1e-13, -1e-11])
def test_a_top_line_that_holds_still_reads_so_whatever_round_off_its_fit_leaves(off):
    """The same walker on a cut of 25 frames. Its top line fits to round-off, not to nothing, and what round-off leaves
    differs from machine to machine: exactly 0 under one numpy, a little over it under another. Laid over the line, as
    much as round-off leaves or more, it reads as the line reads with none: holding still, with no share of a rise and
    fall (the parent engine read it as the rise and fall and took a share of it, under a why and in the record alike,
    where its fit did not come to exactly 0). A row in one frame is a rise and fall."""
    frames = [_apart(k, cycle=30) for k in range(FRAMES)]
    signals = {**legs.strike_signals(frames), **legs.body_signals(frames)}
    top = np.asarray(signals["top"], dtype=float) + off * np.cos(4 * np.pi * np.arange(FRAMES) / 25)
    word = local_cycle.bob_word({**signals, "top": top}, 25, feet=True)
    assert word["says"] is None and "its top line holding still" in word["why"] and word["body"]["share"] == 0.0
    top[36] += 1
    word = local_cycle.bob_word({**signals, "top": top}, 25, feet=True)
    assert word["says"] is None and "its top line holding still" not in word["why"]
    assert word["body"]["beat"] > 0 and word["body"]["share"] > 0


# Walkers moved or zoomed with the whole picture, by their interval between crossings d: the side walker whose legs are
# drawn alike and wide, its crossings its steps (23 frames), and the walker seen from behind whose feet meet once a cycle
# (17.5); held still, carried up and down whole (feet and all, on the walk's beat), or rising and falling on its legs
# once a step. None rises and falls twice an interval on its legs.
_STEPS = {"cycle": 46, "phase": 13, "tone": 4, "lean": 1, "leg_tone": 0, "leg_half": 10}
NOT_TWICE = {
    "steps-still": (23, lambda k: _alike(k, **_STEPS, bob=0)),
    "steps-whole": (23, lambda k: _alike(k, **_STEPS, whole=True)),
    "steps-on-its-legs": (23, lambda k: _alike(k, **_STEPS)),
    "hidden-still": (17.5, lambda k: _behind(k, cycle=17.5, hold=1, alike=True, bob=0)),
    "hidden-whole": (17.5, lambda k: _behind(k, cycle=17.5, hold=1, alike=True, whole=True)),
}


def _zoomed(draw, scale, about: str):
    """`draw`'s walker zoomed in and out with the whole picture, `scale(k)` in frame k about its feet, its middle or its
    head (bilinear), on a canvas 60 px taller either side."""
    ax, ay = 104, {"feet": GROUND, "middle": 103, "head": 40}[about]

    def frame(k: int) -> Image.Image:
        im, f = draw(k), scale(k)
        big = im.resize((round(im.width * f), round(im.height * f)), Image.BILINEAR)
        out = Image.new("RGBA", (im.width, im.height + 120))
        out.paste(big, (round(ax - ax * f), round(60 + ay - ay * f)))
        return out
    return frame


def _word_of(draw, interval: float) -> dict:
    frames = [draw(k) for k in range(FRAMES)]
    return local_cycle.bob_word({**legs.strike_signals(frames), **legs.body_signals(frames)}, interval)


@pytest.mark.slow
@pytest.mark.parametrize("period", [0.25, 0.5, 1, 2])
@pytest.mark.parametrize("walker", list(NOT_TWICE))
def test_a_walk_moved_up_and_down_with_the_whole_picture_on_any_beat_is_never_witnessed(walker, period):
    """`bob_word` off the drawn frames (`legs.strike_signals`, `legs.body_signals`) at the walker's interval d, the
    whole picture moved up and down 3, 6 or 12 px either way every d/4, d/2, d or 2d frames, at four phases
    (`_carried`). The top line on the screen may rise and fall twice an interval with the picture (the parent engine
    read it so, and witnessed a body held still on a picture shaken twice an interval), but the lowest row moves with
    it, and what is left against it does not rise and fall twice an interval: none is witnessed."""
    d, draw = NOT_TWICE[walker]
    frames = [draw(k) for k in range(FRAMES)]
    for rows in (3, 6, 12):
        for phase in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
            shake = lambda k, m=rows, f=phase: round(m * math.sin(2 * math.pi * k / (period * d) + f))  # noqa: E731
            word = _word_of(_carried(lambda k: frames[k], shake), d)
            assert word["says"] is None, (walker, period, rows, phase, word)
            if (walker, period, rows, phase) == ("steps-still", 0.5, 3, 0):
                assert word["half"] >= local_cycle.BOB_TWICE and word["quarter"] <= -local_cycle.BOB_TWICE
                assert "its lowest row rises and falls with it" in word["why"]


@pytest.mark.slow
@pytest.mark.parametrize("period", [0.25, 0.5, 1])
@pytest.mark.parametrize("walker", ["steps-still", "steps-on-its-legs", "hidden-still"])
def test_a_walk_zoomed_in_and_out_with_the_whole_picture_on_any_beat_is_never_witnessed(walker, period):
    """The same walkers, held still or rising and falling on their legs once a step, zoomed in and out with the picture
    0.5 to 4 % either way every d/4, d/2 or d frames about their feet, middle or head, at two phases (`_zoomed`): where
    the top line rises and falls twice an interval against the lowest row, the bands of the upper body widen with it,
    and less the zoom they read nothing of the body's own is left. None is witnessed."""
    d, draw = NOT_TWICE[walker]
    frames = [draw(k) for k in range(FRAMES)]
    for amount in (0.005, 0.01, 0.02, 0.04):
        for about in ("feet", "middle", "head"):
            for phase in (0, math.pi / 2):
                scale = lambda k, a=amount, f=phase: 1 + a * math.sin(2 * math.pi * k / (period * d) + f)  # noqa: E731
                word = _word_of(_zoomed(lambda k: frames[k], scale, about), d)
                assert word["says"] is None, (walker, period, amount, about, phase, word)


# The walker zoomed with the picture whatever its head's top (`_head`): the wide walker with its legs drawn alike, no tone
# between them, its feet together over half the interval at a pass (46 frames a cycle, its crossings its steps every
# 23), held still or rising and falling on its legs once a step.
_TOPPED = {"cycle": 46, "phase": 11, "tone": 0, "leg_half": 10}
_ZOOM_FITS = "the rows down the middle of its body fit a zoom as well as they fit standing"


def _zoom_every(period: float, amount: float = 0.04):
    return lambda k: 1 + amount * math.sin(2 * math.pi * k / period)


@pytest.mark.slow
def test_a_still_walk_with_a_pointed_head_zoomed_about_its_feet_twice_a_step_is_never_cut_half_a_cycle_unnamed(tmp_path):
    """The wide walker held still on its legs, its head a triangle from a point down to a base wider than its body,
    zoomed in and out 4 % either way about its feet twice a step (every 11.5 frames, `_zoomed`): against its lowest
    row, which the zoom about the feet leaves where it is, its top line rises and falls twice an interval of a step,
    and the band across its head's top widens with it by nothing — a zoom moves a band under a point not at all. The
    step count's crossings count its steps, and the parent engine cut one of them, 23 frames, half a cycle, as two
    steps, nothing named. The bands lower down its upper body, whose sides run straight down, widen with the zoom, and
    the rows down the middle of its body fit that zoom better than they fit as they stand. Not witnessed half a cycle;
    held, named."""
    draw = _zoomed(lambda k: _alike(k, **_TOPPED, bob=0, top="pointed"), _zoom_every(11.5), "feet")
    report = _cut(tmp_path / "out", _keyed(tmp_path, draw))
    _not_silent(report, 46)
    word = _word_of(draw, 23)
    assert word["half"] >= local_cycle.BOB_TWICE and word["quarter"] <= -local_cycle.BOB_TWICE
    assert word["says"] is None and word["body"]["depth"]["ratio"] < local_cycle.BOB_RIGID and _ZOOM_FITS in word["why"]
    bands = word["body"]["bands"]
    assert abs(bands["zoom"][0]) < local_cycle.BAND_FLOOR and max(bands["zoom"]) >= local_cycle.BAND_FLOOR


@pytest.mark.slow
@pytest.mark.parametrize("about", ["feet", "middle", "head"])
@pytest.mark.parametrize("top", TOPS)
def test_a_walk_zoomed_with_the_picture_whatever_its_heads_top_is_never_witnessed_half_a_cycle_or_two(top, about):
    """The wide walker (`_TOPPED`), its head's top flat, round, pointed or two ears' tips (`_head`), held still or rising
    and falling on its legs once a step, zoomed in and out 4 % either way with the picture about its feet, middle or
    head every quarter, half or whole cycle (11.5, 23 or 46 frames), 140 frames drawn. However far a zoom widens the
    band across its head's top — wholly, in part or not at all — the bands lower down its upper body widen with it, and
    where the zoom lifts the top line twice an interval nothing of it is left less the zoom they read. Never two steps
    an interval of a step (23 frames), nor a cut of a step said to be a cycle or two, nor a cut of two cycles (92) said
    to be one."""
    for bob in (0, 3):
        for period in (11.5, 23, 46):
            draw = _zoomed(lambda k, b=bob: _alike(k, **_TOPPED, bob=b, top=top), _zoom_every(period), about)
            frames = [draw(k) for k in range(140)]
            signals = {**legs.strike_signals(frames), **legs.body_signals(frames)}
            case = (top, about, bob, period)
            assert local_cycle.bob_word(signals, 23)["says"] is None, case
            assert local_cycle.cut_word(signals, 23)["says"] not in ("two steps", "four steps"), case
            assert local_cycle.cut_word(signals, 92)["says"] != "two steps", case


@pytest.mark.slow
@pytest.mark.parametrize("bob", [0, 3])
@pytest.mark.parametrize("about", ["feet", "middle", "head"])
@pytest.mark.parametrize("top", ["pointed", "ears"])
def test_a_walk_with_a_pointed_head_or_ears_zoomed_every_quarter_cycle_is_never_cut_half_a_cycle_unnamed(tmp_path, top, about,
                                                                                                         bob):
    """The wide walker (`_TOPPED`), its head a point or two ears' tips (`_head`), held still or rising and falling on its
    legs once a step, zoomed in and out 4 % either way with the picture about its feet, middle or head every quarter
    cycle (11.5 frames): twice an interval of a step. The parent engine read the band across the head's top alone,
    which such a zoom widens by nothing, and handed some of these out cut one step, half a cycle, as two steps, nothing
    named. Through the loop none is."""
    draw = _zoomed(lambda k: _alike(k, **_TOPPED, bob=bob, top=top), _zoom_every(11.5), about)
    _not_silent(_cut(tmp_path / "out", _keyed(tmp_path, draw)), 46)


def _drawn_against(turn: str):
    """The wide walker (`_TOPPED`) held still on its legs, its upper body redrawn: its head a box 36 px wide drawn
    narrower as the picture zooms in (36/s), its torso a box under it whose half width swings 4 px either way twice a
    step, a quarter of the beat off the zoom (`quarter`), in step with it or against it; zoomed in and out 4 % either
    way about its feet twice a step (every 11.5 frames, `_zoomed`)."""
    swing = {"quarter": math.cos, "in step": math.sin, "against": lambda u: -math.sin(u)}[turn]
    scale = _zoom_every(11.5)

    def frame(k: int) -> Image.Image:
        im = _alike(k, **_TOPPED, bob=0)
        d = ImageDraw.Draw(im)
        d.rectangle((60, 40, 148, 109), fill=(0, 0, 0, 0))
        d.rectangle((104 - 18 / scale(k), 40, 104 + 18 / scale(k), 69), fill=(230, 200, 170, 255))
        half = 17 + 4 * swing(4 * math.pi * k / 23)
        d.rectangle((104 - half, 70, 104 + half, 109), fill=(200, 80, 80, 255))
        return im
    return _zoomed(frame, scale, "feet")


def _caped():
    """The wide walker (`_TOPPED`) held still on its legs, the 65 rows from its head's top redrawn, their edges drawn to
    the part of a pixel they cover: a crown 13 rows deep and 25 px wide drawn narrower as the picture zooms in (25/s),
    over a cape 29 px wide widening and narrowing 4 px either way twice a step, a quarter of the beat before the zoom;
    zoomed in and out 4 % either way about its feet twice a step (every 11.5 frames, `_zoomed`)."""
    scale = _zoom_every(11.5)

    def frame(k: int) -> Image.Image:
        a = np.array(_alike(k, **_TOPPED, bob=0))
        cols = np.arange(a.shape[1])
        a[40:105] = 0
        for rows, width, rgb in ((slice(40, 53), 25 / scale(k), (230, 200, 170)),
                                 (slice(53, 105), 29 + 4 * math.cos(2 * math.pi * k / 11.5), (120, 60, 160))):
            cover = np.clip(np.minimum(cols + 1, 104.5 + width / 2) - np.maximum(cols, 104.5 - width / 2), 0, 1)
            a[rows, :, :3] = rgb
            a[rows, :, 3] = np.round(255 * cover).astype(np.uint8)
        return Image.fromarray(a)
    return _zoomed(frame, scale, "feet")


@pytest.mark.slow
@pytest.mark.parametrize("form", ["quarter", "in step", "against", "cape"])
def test_a_still_walk_zoomed_twice_a_step_its_head_drawn_against_the_zoom_is_never_cut_half_a_cycle_unnamed(tmp_path, form):
    """The wide walker held still on its legs, zoomed about its feet twice a step, its head drawn narrower as the
    picture zooms in and the body under it swinging on the beat: its torso a quarter of the beat off the zoom, in step
    with it or against it (`_drawn_against`), or a cape a quarter of the beat before it under a crown (`_caped`).
    Against its lowest row its top line rises and falls twice an interval of a step; the bands across its head hold
    still on the screen, and those across the body under it move. The parent engine took a band holding still as the
    witness that nothing zooms and read the swing under it as the body's own, and cut 23 frames, half a cycle, as two
    steps, nothing named. The swing may hide the zoom at any turn, and the rows down the middle of the body do not hide
    it from the strip: they fit the zoom better than they fit as they stand. Never witnessed half a cycle; held, named."""
    draw = _caped() if form == "cape" else _drawn_against(form)
    report = _cut(tmp_path / "out", _keyed(tmp_path, draw))
    _not_silent(report, 46)
    assert local_cycle.unwitnessed(report["cycle"])
    word = _word_of(draw, 23)
    assert word["half"] >= local_cycle.BOB_TWICE and word["quarter"] <= -local_cycle.BOB_TWICE
    assert word["says"] is None and word["body"]["still_bands"] >= 1 and word["body"]["depth"]["ratio"] < local_cycle.BOB_RIGID
    assert _ZOOM_FITS in word["why"]


# The picture moved up and down (of the interval d, px either way, phase), or held still (None).
SHAKES = [None, (0.5, 6, 0), (0.5, 6, math.pi / 2), (1, 6, 0)]
SLIDE_SHAKES = [None, (0.5, 3, 0), *((0.5, 6, f) for f in (0, math.pi / 2, math.pi, 3 * math.pi / 2)), (0.5, 12, 0), (1, 6, 0)]


def _shake(shake, d: float):
    return (lambda k: 0) if shake is None else (lambda k: round(shake[1] * math.sin(2 * math.pi * k / (shake[0] * d) + shake[2])))


@pytest.mark.slow
@pytest.mark.parametrize("air", [1, 2, 3, 6])
def test_a_run_whose_feet_leave_the_ground_as_its_body_rises_is_never_witnessed_two_steps_an_interval_of_steps(air):
    """The side walker rising and falling on its legs once a step, its crossings its steps (23 frames), running: both
    soles lifted up to `air` px off the ground while the body is high, so the lowest row rises with the body for half of
    each step. Still or on a picture moved 6 px either way every d/2 or d frames: what is left of the top line against
    the lowest row rises and falls once an interval, never twice — not witnessed."""
    frames = [_alike(k, **_STEPS, air=air) for k in range(FRAMES)]
    for shake in SHAKES:
        assert _word_of(_carried(lambda k: frames[k], _shake(shake, 23)), 23)["says"] is None, (air, shake)


@pytest.mark.slow
@pytest.mark.parametrize("slide", [6, 12, 24])
def test_a_walk_whose_planted_foot_slides_down_the_screen_is_never_witnessed_two_steps_an_interval_of_steps(slide):
    """The side walker rising and falling on its legs once a step, its crossings its steps, drawn as a view from behind
    or a diagonal draws feet: each sole up to `slide` px lower the further back its foot is, the foot moving forward
    lifted 8 px — the lowest row is the planted foot, sliding down through its step and jumping up when the other lands.
    Still or on a picture moved 3, 6 (at four phases) or 12 px either way every d/2 frames, or 6 every d: not
    witnessed."""
    frames = [_alike(k, **_STEPS, lift=8, slide=slide) for k in range(FRAMES)]
    for shake in SLIDE_SHAKES:
        assert _word_of(_carried(lambda k: frames[k], _shake(shake, 23)), 23)["says"] is None, (slide, shake)


@pytest.mark.parametrize("beat, says", [(8, "two steps"), (4, "four steps"), (16, "one step"), (None, None)])
def test_a_cut_is_witnessed_by_how_often_the_body_rises_and_falls_in_it(beat, says):
    """`cut_word` on a cut 16 frames long, the body 100 rows tall on feet that stay on the ground, its top line rising
    and falling 3 rows either way every `beat` frames: twice the cut is two steps, a cycle; four times, two cycles;
    once, one step — or a whole cycle of a body that rises and falls once a cycle, which the word cannot tell from it;
    held still, nothing witnesses the cut whole cycles. The feet on the ground, the top line reads alike on the screen
    and over the feet: both lines say the word, and where neither says one, each says why."""
    word = local_cycle.cut_word(_bob(beat), 16)
    assert word["says"] == says and [line["says"] for line in word["lines"].values()] == [says, says]
    if says is None:
        assert "by" not in word and all(line["why"] in word["why"] for line in word["lines"].values())
    else:
        assert word["by"] == list(local_cycle.CUT_LINES)
    if says in ("four steps", "one step"):
        assert word["on"]["says"] == "cycles" and word["on"]["interval"] == {"four steps": 8, "one step": 32}[says]


def _on_legs(top, feet):
    """A column on two legs, one flat colour: its top `top(k)` rows off row 40 in frame k, its soles `feet(k)` off row 180."""

    def frame(k: int) -> Image.Image:
        y, sole = 40 + top(k), 180 + feet(k)
        im = Image.new("RGBA", (300, 220))
        d = ImageDraw.Draw(im)
        d.rectangle((80, y, 140, y + 85), fill=(180, 80, 80, 255))
        d.rectangle((84, y + 80, 99, sole), fill=(60, 60, 70, 255))
        d.rectangle((121, y + 80, 136, sole), fill=(60, 60, 70, 255))
        return im
    return frame


def _signals(draw, frames: int) -> dict:
    drawn = [draw(k) for k in range(frames)]
    return {**legs.strike_signals(drawn), **legs.body_signals(drawn)}


def _quick(interval: int, turns: int | None):
    """A top line on a beat of its own, 4 rows either way: `turns` times an interval of `interval` frames, or (None) by
    the four rows 4, -4, -4, 4 over and over. Read in whole frames, half an interval on and a quarter on it correlates
    as a rise and fall twice an interval does, and it has next to nothing of one."""
    return (lambda k: (4, -4, -4, 4)[k % 4]) if turns is None else (lambda k: round(4 * math.sin(2 * math.pi * k * turns / interval)))


@pytest.mark.parametrize("interval, turns", [(24, None), (18, 6), (24, 10), (30, 6), (36, 14)])
def test_a_top_line_on_a_quick_beat_of_its_own_over_feet_rising_twice_an_interval_is_never_witnessed(interval, turns):
    """A column whose feet rise and fall 6 rows twice an interval and whose top runs on a quick beat of its own (`_quick`).
    Its two correlations pass, and against its lowest row a rise and fall twice an interval is left: all of it the
    lowest row's, the top line's own on the screen next to none. The lowest row does the rising and falling, and the
    parent engine read that as the body's own, two steps an interval, and cut it so. Never witnessed, the word says
    why, and the crossings stay on steps."""
    feet = lambda k: round(6 * math.cos(4 * math.pi * k / interval))  # noqa: E731
    signals = _signals(_on_legs(_quick(interval, turns), feet), 120)
    word = local_cycle.bob_word(signals, interval)
    assert word["half"] >= local_cycle.BOB_TWICE and word["quarter"] <= -local_cycle.BOB_TWICE
    assert word["says"] is None and "its lowest row does the rising and falling" in word["why"]
    assert word["body"]["beat"] < local_cycle.BOB_OWN * word["body"]["less_lowest"]
    assert local_cycle.cut_word(signals, interval)["says"] != "two steps"
    middles = range(interval // 2, 120, interval)
    reading = crossings.Reading(120, "gap", [(float(c), float(c)) for c in middles],
                                runs=[(c - interval // 3, min(c + interval // 3, 119)) for c in middles])
    said = local_cycle.read_cycles(reading, _distances(period=interval, frames=120), signals=signals)
    assert said["cycles"] is False and said["unwitnessed"] is True and "its lowest row does the rising and falling" in said["bob"]["why"]


@pytest.mark.parametrize("form", ["signals", "drawn"])
def test_a_head_rising_twice_in_one_step_is_never_cut_half_a_cycle_unnamed(form):
    """`cut_word` on a cut 16 frames long, one step of a walk 32 frames a cycle, read off signals (`_body`) and off a
    drawn column: its head rises and falls 3 rows every 8 frames, twice the cut, and its feet 8 rows every 16, once the
    cut. On the screen alone the line reads two steps, half a cycle cut as a whole one, and the parent engine stood it
    so, nothing named. Over its feet the line rises and falls as the feet do, once the cut. A line witnesses only where
    it says so in both places: nothing witnesses the cut, and the word says why."""
    head = lambda k: round(3 * math.cos(2 * math.pi * k / 8))  # noqa: E731
    feet = lambda k: round(8 * math.cos(2 * math.pi * k / 16))  # noqa: E731
    signals = _signals(_on_legs(head, feet), 96) if form == "drawn" else _body(
        [40.0 + head(k) for k in range(96)], lowest=[140.0 + feet(k) for k in range(96)])
    assert local_cycle.bob_word(signals, 16)["says"] == "cycles" and local_cycle.bob_word(signals, 16, feet=True)["says"] is None
    word = local_cycle.cut_word(signals, 16)
    assert word["says"] is None and "by" not in word and [line["says"] for line in word["lines"].values()] == [None, None]
    assert "its top line rises and falls twice an interval on the screen, but not over its feet" in word["why"]
    assert "its top line less its flow does not rise and fall" not in word["lines"]["screen"]["why"]
    assert local_cycle.body_word(signals, 16)["says"] is None


def _lifted_cape(k: int) -> Image.Image:
    """A body rising and falling 3 rows every 8 frames on feet that stay on the ground, its head a narrow column and a
    cape as wide as its widest row over its shoulders, lifted 8 rows either way on a beat of its own (every 11 frames):
    `top`, which passes over what is narrower than half the widest row, reads the cape and not the head."""
    y = 40 + round(3 * math.cos(2 * math.pi * k / 8))
    lift = round(8 * math.sin(2 * math.pi * k / 11))
    im = Image.new("RGBA", (300, 220))
    d = ImageDraw.Draw(im)
    d.rectangle((102, y, 118, y + 30), fill=(230, 200, 170, 255))
    d.rectangle((90, y + 30, 130, y + 100), fill=(200, 80, 80, 255))
    d.rectangle((70, y + 24 + lift, 150, y + 60), fill=(90, 120, 200, 255))
    d.rectangle((92, y + 100, 105, GROUND), fill=(60, 60, 70, 255))
    d.rectangle((115, y + 100, 128, GROUND), fill=(60, 60, 70, 255))
    return im


def test_a_cape_that_takes_the_top_line_off_the_head_leaves_the_word_to_the_first_solid_row():
    """The top line and the first solid row are each read, one at a time, both on the screen and over the feet. A cape
    lifted on its own beat takes the top line off the head (`_lifted_cape`), where the first solid row stays on it and
    rises and falls twice an interval: it witnesses, and the word names it (the parent engine read the top line alone
    and held the cut)."""
    signals = _signals(_lifted_cape, FRAMES)
    assert local_cycle.bob_word(signals, 16)["says"] is None
    word = local_cycle.body_word(signals, 16)
    assert word["says"] == "cycles" and word["top_line"] == "first_row" and word["feet"]["top_line"] == "first_row"
    cut = local_cycle.cut_word(signals, 16)
    assert cut["says"] == "two steps" and cut["by"] == list(local_cycle.CUT_LINES) and cut["lines"]["feet"]["bob"]["top_line"] == "first_row"
    assert local_cycle.body_word({k: v for k, v in signals.items() if k != "first_row"}, 16)["says"] is None


def test_a_line_on_the_screen_and_another_over_the_feet_witness_nothing():
    """`body_word` reads one line in both places. The top line rises and falls twice an interval on the screen and not
    over its feet, which rise and fall once an interval; the first solid row, which also moves with the feet, does so
    over them and not on the screen. Each says so in one place: nothing is witnessed, and each line's why is in the word."""
    t = np.arange(FRAMES)
    head, feet = 3 * np.cos(2 * np.pi * t / 8), 8 * np.cos(2 * np.pi * t / 16)
    word = local_cycle.body_word({**_body(40 + head, lowest=140 + feet), "first_row": list(40 + head + feet)}, 16)
    assert word["says"] is None and "top_line" not in word
    assert "its top line rises and falls twice an interval on the screen, but not over its feet" in word["why"]
    assert "its first solid row rises and falls twice an interval over its feet, but not on the screen" in word["feet"]["why"]


def _marked(k: int, zoom: bool, marked: bool) -> Image.Image:
    """A column on planted feet, rising 3 rows either way twice an interval of 24 frames, or (`zoom`) zoomed about its
    feet as far as that; `marked`, with a band across it every 12 rows down its middle, else one flat colour."""
    wave = math.cos(4 * math.pi * k / 24)
    scale = 1 + 3 * wave / 100 if zoom else 1
    y = (lambda row: round(GROUND - (GROUND - row) * scale) - (0 if zoom else round(3 * wave)))
    im = Image.new("RGBA", (300, 220))
    d = ImageDraw.Draw(im)
    d.rectangle((90, y(40), 130, y(150)), fill=(180, 80, 80, 255))
    for row in range(44, 140, 12 if marked else 1000):
        d.rectangle((90, y(row), 130, y(row + 4)), fill=(240, 220, 90, 255))
    d.rectangle((94, y(146), 108, GROUND), fill=(60, 60, 70, 255))
    d.rectangle((112, y(146), 126, GROUND), fill=(60, 60, 70, 255))
    return im


def test_the_rows_down_the_middle_of_a_body_are_read_where_a_zoom_would_show_in_them():
    """`body_depth`, `own_beat` `whole`: a body with marks down its middle, rising 3 rows either way twice an interval on
    planted feet, carries them as they are, and zoomed about its feet as far as that it draws them longer (`_marked`).
    Those fits decide; a body one flat colour down its middle has none to read, a zoom moves its strip under
    STRIP_FLOOR, and the bands decide. That is `own_beat`'s to read; the report keeps the measures and no such word, and
    a hold is not told from a hold by it."""
    walker = _marked
    read = {(zoom, marked): local_cycle.own_beat(_signals(lambda k: walker(k, zoom, marked), 96), 24)
            for zoom in (False, True) for marked in (False, True)}
    assert read[False, True]["whole"] is True and read[True, True]["whole"] is False
    assert read[False, True]["depth"]["ratio"] >= local_cycle.BOB_RIGID > read[True, True]["depth"]["ratio"]
    assert read[False, False]["whole"] is None and read[False, False]["depth"]["moved"] < local_cycle.STRIP_FLOOR
    signals = _signals(lambda k: walker(k, False, True), 96)
    body = local_cycle.bob_word(signals, 24)["body"]
    assert "whole" not in body and "whole_least" not in body and "depth" in body


def test_a_reason_both_lines_give_is_said_once():
    """The column zoomed on the beat, marks down its middle (`_marked`): its top line and its first solid row are one
    row, and each is held for the same reason, the rows down the middle of the body fitting the zoom as well as they fit
    standing, a reason that names no line. The body's word, read on a crossing interval and on a cut, says it once (the
    first reading of this engine said it once for each line, joined)."""
    signals = _signals(lambda k: _marked(k, True, True), 96)
    assert list(signals["first_row"]) == list(signals["top"])
    reason = "fit a zoom as well as they fit standing"
    word = local_cycle.body_word(signals, 24)
    assert word["says"] is None and word["why"].count(reason) == 1
    assert local_cycle.cut_word(signals, 24)["why"].count(reason) == 1


@pytest.mark.parametrize("span, says", [(30, "two steps"), (60, "four steps"), (15, "one step"), (22, None)])
def test_the_order_the_feet_are_drawn_in_says_how_many_steps_a_span_holds(span, says):
    """`order_word` on the walker whose feet stay apart, a cycle 30 frames: the foot drawn nearer changes sides every 15
    frames, twice in 30 — two steps; four times in 60, two cycles; once in 15, one step; in 22, none of those. No foot
    on the ground slides along it, and its upper body is still."""
    word = local_cycle.order_word([legs.feet_order(_apart(k, cycle=30)) for k in range(FRAMES)], span)
    assert word["says"] == says and word["apart"] >= legs.FEET_APART_SHARE and word["swing"] >= word["floor"]
    assert word["slides"] == [] and word["travel"] < local_cycle.ORDER_TRAVEL * word["spacing"]
    assert word["sway"] < local_cycle.ORDER_SWAY * word["tilt"] and word["per"] == pytest.approx(span / 15, rel=0.05)


def test_the_order_does_not_stand_a_cut_the_search_kept_where_none_of_the_two_step_cuts_closes():
    """The walker whose feet stay apart, its body still, cut 20 frames, a cycle (loop `_witnessed`): the order says two
    steps and the cut stands on it. Where the step count read the clip but none of its two-step cuts closed, and the
    search's cut was kept (`declined`), the order counts the same steps but does not stand a length the closing refused:
    the cut is held, named, the order's word kept as `read`."""
    frames = [_apart(k, cycle=20) for k in range(FRAMES)]
    cut = {"start": 4, "length": 20, "step_count": {"readable": True, "chosen_by": "search"}}
    stood = loop._witnessed(json.loads(json.dumps(cut)), frames=frames, view="front")["cut_witness"]
    assert stood["says"] == "two steps" and stood["by"] == ["order"] and "held" not in stood
    cut["step_count"]["declined"] = {"cut": [6, 40], "phase": 0.0, "gated": True, "seam": 2.6}
    held = loop._witnessed(cut, frames=frames, view="front")
    said = held["cut_witness"]
    assert said["says"] is None and said["held"] == "unwitnessed" and local_cycle.unwitnessed(held)
    assert said["order"]["read"] == "two steps" and "does not stand a length the closing refused" in said["why"]


@pytest.mark.parametrize("frames, why", [
    (lambda k: _apart(k, cycle=30, rock=10), "may rock on its feet"),
    (lambda k: _apart(k, cycle=30, slide=True), "slides"),
    (lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, lift=6), "under the body"),
    (lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, leg_half=10, lift=6), "under the body"),
    (lambda k: _behind(k, cycle=20, hold=2, arm=ARMS_IN), "drawn level"),
])
def test_the_order_the_feet_are_drawn_in_witnesses_nothing_where_the_clip_does_not_show_its_sides_are_two_legs(frames, why):
    """A picture rocked on its feet draws one foot lower and then the other with its upper body swaying as far as the
    tilt carries it; the walker whose feet stay apart, its right foot sliding along the ground planted to meet the left
    and back, and a side walk lifting its swing foot draw one foot lower and then the other with a foot on the ground
    passing under the body; feet drawn level say no order. None says a word, each says why."""
    word = local_cycle.order_word([legs.feet_order(frames(k)) for k in range(FRAMES)], 23)
    assert word["says"] is None and why in word["why"], word


@pytest.mark.parametrize("each_step, span, says", [(False, 30, "two steps"), (False, 15, "one step"),
                                                    (True, 15, "one step")])
def test_a_tail_whose_tip_dips_into_the_foot_band_is_never_read_as_a_foot(each_step, span, says):
    """The walker whose feet stay apart, a cycle 30 frames, its feet meeting once a cycle or once a step (`each_step`),
    with a tail whose tip dips into the foot band beside them: there the band's widest gap parts the tail from both
    feet, and the tail read as a foot puts the foot drawn lower on the side it is not, then back (the parent engine
    read it so, and the order said nothing of the walker whose feet meet once a cycle). The tail is drawn over no column
    anything stands on: the frames whose one side it is are not read apart (`off_ground`), no change is counted on
    them, and the order says what the walker drawn without it says — two steps in a cycle, one step in an interval of
    the walker whose feet meet once a step, never two."""
    walker = lambda k: _apart(k, cycle=30, each_step=each_step)  # noqa: E731
    order = [legs.feet_order(_dipping_tail(walker, hip=(92, 100), reach=28)(k)) for k in range(FRAMES)]
    tail = [k for k, o in enumerate(order) if o["apart"] and o["left"][1] < 80]
    word = local_cycle.order_word(order, span)
    assert (word["says"] == "two steps") == (says == "two steps"), word
    assert tail and word["off_ground"] == tail and not {k for change in word["changes"] for k in change} & set(tail)
    plain = local_cycle.order_word([legs.feet_order(walker(k)) for k in range(FRAMES)], span)
    assert word["says"] == plain["says"] == says and "off_ground" not in plain, (word, plain)


def test_a_side_walk_whose_tail_dips_into_the_foot_band_is_never_read_on_its_tail():
    """The side walk lifting its swing foot, its planted foot passing under the body, with a tail whose tip dips into
    the foot band behind it: the tail read as a foot moves the band's parting and the feet's distance apart with it
    (the parent engine said nothing of it all the same). Its frames are not read apart (`off_ground`), and the order
    says nothing on the planted foot passing under the body, as of the walk drawn without the tail."""
    walker = lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_tone=0, lift=6)  # noqa: E731
    order = [legs.feet_order(_dipping_tail(walker, hip=(92, 90), reach=40)(k)) for k in range(FRAMES)]
    tail = [k for k, o in enumerate(order) if o["apart"] and o["left"][1] < 70]
    word = local_cycle.order_word(order, 23)
    assert tail and word["off_ground"] == tail and word["says"] is None and "under the body" in word["why"], word


@pytest.mark.parametrize("verdict", ["held_verdict", "step_verdict"])
def test_a_cut_held_on_one_step_whose_record_names_no_line_is_refused_by_name(verdict):
    """`held_verdict` (a public caller; `step_verdict` says it) names the line a cut held on one step rose and fell
    once on (`by`, `lines`). A record held on one step that names no line it was read on, its reading over the feet
    kept at its top (`bob`, `on`), is refused by name (ValueError); one held with nothing read names its own why as
    before."""
    once, still = local_cycle.cut_word(_bob(16), 16), local_cycle.cut_word(_bob(None), 16)
    feet = once["lines"]["feet"]
    one_step = {"cut": [0, 16], "says": "one step", "bob": feet["bob"], "on": feet["on"], "held": "unwitnessed"}
    with pytest.raises(ValueError, match="names no line it was read on"):
        getattr(local_cycle, verdict)({"start": 0, "length": 16, "cut_witness": one_step})
    why = still["lines"]["feet"]["why"]
    nothing = {"cut": [0, 16], "says": None, "bob": still["lines"]["feet"]["bob"], "why": why, "held": "unwitnessed"}
    said = getattr(local_cycle, verdict)({"start": 0, "length": 16, "cut_witness": nothing})
    assert len(said) == 1 and why in said[0] and local_cycle.UNWITNESSED in said[0]


def test_a_shadow_on_the_screen_under_the_feet_does_not_hold_a_shaken_picture_still_for_the_witness():
    """`bob_word` on an interval of 15 frames, the body 100 rows tall on a picture moved 3 rows up and down twice an
    interval: its top line and its feet move with the picture, but a shadow drawn translucent under the feet and fixed
    on the screen is its lowest solid row, and holds still. Read against that row alone the picture's moving is the
    body's own (it witnessed two steps an interval so); against the lowest opaque row, the feet's, nothing is left."""
    t = np.arange(FRAMES)
    moved = np.round(3 * np.cos(4 * np.pi * (t - 8) / 15))
    shadowed = _body(40 + moved, lowest=[150.0] * FRAMES, sole=140 + moved)
    word = local_cycle.bob_word(shadowed, 15)
    assert word["says"] is None and "its lowest row rises and falls with it" in word["why"]
    assert word["body"]["lowest"] == 0 and word["body"]["sole"] == pytest.approx(word["body"]["beat"])
    assert local_cycle.bob_word({**shadowed, "sole": shadowed["reach"]}, 15)["says"] == "cycles"


# A shadow drawn dark and translucent (alpha 200) under the walker's feet, 36 px wide (narrower than the body's widest
# run, so the top line stays on the body): apart (6..12 px under the soles) or touching (over the soles' last 3 rows).
SHADOWS = {"apart": (86, GROUND + 6, 122, GROUND + 12), "touch": (86, GROUND - 3, 122, GROUND + 5)}


def _shadowed(draw, motion, *, shadow: str, screen: bool, room: int = 60):
    """`draw`'s walker over a SHADOWS `shadow`, the picture moved `motion(k)` px down in frame k on a canvas `room` px
    taller either side; the shadow drawn on the screen (`screen`: it stays while the picture moves) or with the walker.
    The shadow is solid to the legs' reading: the feet are not seen apart over it."""
    box = SHADOWS[shadow]

    def frame(k: int) -> Image.Image:
        walker = draw(k)
        out = Image.new("RGBA", (walker.width, walker.height + 2 * room))
        if screen:
            ImageDraw.Draw(out).ellipse((box[0], box[1] + room, box[2], box[3] + room), fill=(40, 40, 40, 200))
            out.alpha_composite(walker, (0, room + motion(k)))
        else:
            layer = Image.new("RGBA", walker.size)
            ImageDraw.Draw(layer).ellipse(box, fill=(40, 40, 40, 200))
            layer.alpha_composite(walker)
            out.paste(layer, (0, room + motion(k)))
        return out
    return frame


def _not_silent(report: dict, cycle: float) -> None:
    """The plan's count of a silent half cycle, whichever way the cut was chosen: a loop handed out (passed) shorter
    than three quarters of the walk's `cycle`, with no warning naming UNWITNESSED and no retake reason. None is."""
    c = report["cycle"]
    named = any(local_cycle.UNWITNESSED in w for w in report["jolt"]["warnings"]) or (report.get("retake") or {}).get("reasons")
    assert report["status"] != "passed" or c["length"] >= 0.75 * cycle or named, (
        f"the cut {c['start']}/{c['length']} of a walk {cycle} frames a cycle is handed out, nothing named")


def _held(report: dict) -> dict:
    """Nothing that reads no leg witnesses the cut handed out whole cycles — the body's rise and fall not read twice
    in it, or once (one step, or a whole cycle of a body that rises and falls once a cycle): it is held as chosen
    (`cut_witness.held`), never cut again, a warning names it (UNWITNESSED), and `video-set` leaves it out of its cycle
    alignment (`local_cycle.unwitnessed`)."""
    c = report["cycle"]
    said = c["cut_witness"]
    assert report["status"] == "passed" and said["held"] == "unwitnessed" and local_cycle.unwitnessed(c)
    assert said["cut"] == [c["start"], c["length"]] and said["says"] in (None, "one step") and "cut_again" not in said
    assert (c.get("steps") or {}).get("by") != "body"
    assert any(local_cycle.UNWITNESSED in w and f"the cut {c['start']}/{c['length']} whole cycles" in w
               for w in report["jolt"]["warnings"])
    return said


def _witnessed(report: dict) -> dict:
    """The cut handed out is witnessed: a line of the body rises and falls twice it on the screen and over its feet,
    two steps, nothing held and nothing named."""
    c = report["cycle"]
    said = c["cut_witness"]
    assert report["status"] == "passed" and said["cut"] == [c["start"], c["length"]]
    assert said["says"] == "two steps" and said["by"] == list(local_cycle.CUT_LINES) and "held" not in said
    assert not local_cycle.unwitnessed(c) and not any(local_cycle.UNWITNESSED in w for w in report["jolt"]["warnings"])
    return said


def _strip_says_whole(said: dict) -> bool:
    """The rows down the middle of the body, read on the cut, fit as they stand no worse than a zoom as far as the rise,
    and such a zoom would show in them."""
    depth = said["lines"]["screen"]["bob"]["body"]["depth"]
    return depth["ratio"] >= local_cycle.BOB_RIGID and depth["moved"] >= local_cycle.STRIP_FLOOR


@pytest.mark.slow
@pytest.mark.parametrize("body", ["on-its-legs", "whole"])
def test_a_walk_whose_feet_a_shadow_hides_from_the_step_count_is_never_cut_one_step_unnamed(tmp_path, body):
    """The wide walker with its legs drawn alike, its crossings its steps every 23 frames, over a shadow drawn with it
    apart under its feet: the step count reads no feet, and the search cuts 23 frames — one step, which loops as a cycle
    does where the legs are drawn alike (the parent engine handed it out so, nothing named). Rising and falling on its
    legs once a step, the body rises and falls once the cut — one step, or a whole cycle of a body that does so once a
    cycle; carried up and down whole, feet and all, it does not of its own. Nothing says the cut is a cycle: held as cut,
    named. Its arms swinging past its sides or within them, the word is the same, read off its frames. (Drawn with its
    arms within its sides, or held still on its legs, the loop is refused: its wrap jumps.)"""
    drawn = {"on-its-legs": {}, "whole": {"whole": True}}[body]
    report = _cut(tmp_path / "out", _keyed(tmp_path, _shadowed(lambda k: _alike(k, **_STEPS, **drawn), lambda k: 0,
                                                                 shadow="apart", screen=False)))
    _not_silent(report, 46)
    counted = report["cycle"]["step_count"]
    assert counted["readable"] is False and counted["chosen_by"] == "search"
    said = _held(report)
    says = {"on-its-legs": "one step", "whole": None}[body]
    assert said["says"] == says and said["cut"] == counted["search"]["cut"] and abs(said["cut"][1] - 23) <= 1
    within = _shadowed(lambda k: _alike(k, **_STEPS, arm=ARMS_IN, **drawn), lambda k: 0, shadow="apart", screen=False)
    frames = [within(k) for k in range(FRAMES)]
    assert local_cycle.cut_word({**legs.strike_signals(frames), **legs.body_signals(frames)}, said["cut"][1])["says"] == says


@pytest.mark.slow
def test_a_walk_whose_planted_foot_slides_is_never_cut_one_step_unnamed(tmp_path):
    """The wide walker rising and falling on its legs once a step, its arms swinging within its sides, its crossings its
    steps every 23 frames, each sole drawn up to 12 px lower the further back its foot is (as a view from behind or a
    diagonal draws it) and the foot moving forward lifted 8 px: the step count reads a crossing every half step and
    counts the search's 23 frames — one step — two (the parent engine handed it out so, nothing named). The body rises
    and falls once the cut: one step, or a whole cycle of a body that does so once a cycle, and the crossings cannot say
    which: held as cut, named."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _alike(k, **_STEPS, lift=8, slide=12, arm=ARMS_IN)))
    _not_silent(report, 46)
    counted = report["cycle"]["step_count"]
    assert counted["readable"] and counted["chosen_by"] == "search" and counted["cut"]["verdict"] == "two"
    said = _held(report)
    assert said["says"] == "one step" and said["cut"] == counted["search"]["cut"] and abs(said["cut"][1] - 23) <= 1


@pytest.mark.slow
def test_a_limping_walk_is_never_cut_two_cycles_long(tmp_path):
    """A side walker whose legs a tone or a few apart tell its steps apart, its arms swinging within its sides, 30
    frames a cycle, filmed 100 frames (long enough to read its body's rise and fall over twice the cut), whose body
    sinks on one leg's step and not the other's: it rises and falls once a cycle, as a body with no legs that bounces
    does. The search cuts it a cycle, two steps by its crossings. To the body's word the cut is one step, and it cannot
    tell that from one cycle of such a body: the cut stands as it was cut, held, named — never cut again two long, two
    cycles."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, lambda k: _alike(k, cycle=30, phase=8, tone=4, lean=1, bob=0, limp=3, arm=ARMS_IN),
                                           frames=100))
    c = report["cycle"]
    assert report["status"] == "passed" and abs(c["length"] - 30) <= 1.5
    assert c["step_count"]["chosen_by"] in ("search", "two steps") and c["steps"] == {"count": 2, "by": "crossings"}
    said = _held(report)
    assert said["says"] == "one step" and said["on"]["interval"] == 2 * c["length"]


@pytest.mark.slow
def test_a_walk_over_a_shadow_on_the_screen_on_a_shaken_picture_is_never_cut_a_quarter_cycle_unnamed(tmp_path):
    """The wide walker rising and falling on its legs once a step (23 frames), over a shadow drawn on the screen apart
    under its feet, on a picture shaken 12 px up and down every half step: the feet move in and out over the shadow with
    the picture, and the step count reads a crossing every quarter step and cuts two of them — 12 frames, a quarter of
    a cycle (the parent engine handed it out so, nothing named). Against the lowest opaque row, the feet's, moving with
    the picture where the shadow holds still, the body does not rise and fall of its own twice the cut, nor once: held,
    named."""
    shaken = _shadowed(lambda k: _alike(k, **_STEPS), _shake((0.5, 12, 0), 23), shadow="apart", screen=True)
    report = _cut(tmp_path / "out", _keyed(tmp_path, shaken))
    _not_silent(report, 46)
    assert report["cycle"]["step_count"]["readable"] and report["cycle"]["step_count"]["chosen_by"] == "two steps"
    assert _held(report)["cut"][1] <= 14


@pytest.mark.slow
def test_a_walk_on_a_picture_moved_every_two_thirds_of_a_step_is_never_cut_a_third_of_a_cycle_unnamed(tmp_path):
    """The wide walker rising and falling on its legs once a step (23 frames), on a picture moved 12 px up and down
    every two thirds of a step (dy = round(12 sin(2 pi k / (2/3 x 23) + pi/2))): the search cuts the picture's own 15
    frames, a third of a cycle, and the step count keeps it (the parent engine handed it out so, nothing named).
    Against its feet, moving with the picture, the body does not rise and fall twice the cut, nor once: held, named."""
    report = _cut(tmp_path / "out", _keyed(tmp_path, _carried(lambda k: _alike(k, **_STEPS), _shake((2 / 3, 12, math.pi / 2), 23))))
    _not_silent(report, 46)
    assert report["cycle"]["step_count"]["chosen_by"] == "search" and _held(report)["cut"][1] <= 16


@pytest.mark.slow
def test_a_still_walk_over_a_shadow_on_the_screen_on_a_picture_shaken_twice_a_step_is_never_witnessed(tmp_path):
    """The wide walker held still on its legs (crossings its steps, 23 frames), its arms swinging within its sides, over
    a shadow drawn on the screen touching its soles, on a picture shaken 6 px up and down twice a step: the step count
    reads no feet, and the search cuts 23 frames — one step. On the screen the top line rises and falls twice the cut
    with the picture, and the lowest opaque row, the feet's, with it: the picture's moving, not the body's own. The
    lowest solid row is the shadow, holding still: read over it alone, the picture's moving is the body's own and
    witnesses two steps, every band of its upper body holding still on the beat. Over the lowest opaque row the top line
    holds still. Neither line witnesses the cut: held, named."""
    shaken = _shadowed(lambda k: _alike(k, **_STEPS, bob=0, arm=ARMS_IN), _shake((0.5, 6, math.pi / 2), 23), shadow="touch",
                       screen=True)
    report = _cut(tmp_path / "out", _keyed(tmp_path, shaken))
    _not_silent(report, 46)
    assert report["cycle"]["step_count"]["readable"] is False
    said = _held(report)
    screen, feet = (said["lines"][line]["bob"] for line in ("screen", "feet"))
    assert abs(said["cut"][1] - 23) <= 1 and said["says"] is None
    assert screen["half"] >= local_cycle.BOB_TWICE and screen["quarter"] <= -local_cycle.BOB_TWICE
    assert screen["body"]["sole"] > screen["body"]["lowest"] and "its lowest row rises and falls with it" in said["why"]
    assert feet["over"] == "sole" and feet["rows"] < local_cycle.BOB_ROWS and "does not rise and fall" in said["why"]
    frames = [shaken(k) for k in range(FRAMES)]
    signals = {**legs.strike_signals(frames), **legs.body_signals(frames)}
    assert local_cycle.cut_word({**signals, "sole": signals["reach"]}, said["cut"][1])["says"] == "two steps"


@pytest.mark.slow
@pytest.mark.parametrize("walker, cycle, sway", [("steps", 46, (2, 12, 0)), ("steps", 46, (3, 24, math.pi / 2)),
                                                 ("behind", 20, (2, 24, math.pi))])
def test_a_walk_rising_on_its_legs_on_a_picture_swaying_once_a_cycle_or_slower_is_held_as_cut_named(tmp_path, walker, cycle, sway):
    """A walk rising and falling on its legs once a step, its feet on the ground, its arms swinging within its sides, on
    a picture swaying up and down once a cycle or slower — 12 px either way every 46 frames or 24 px every 69 (the wide
    walker with its legs drawn alike, its crossings its steps every 23 frames), 24 px every 40, up first (the walker
    whose far foot passes hidden, 20 frames a cycle on twos). The cut is a cycle. Taken out at the cut's length, the flow
    leaves the sway whole, and the top line on the screen rises and falls with it once the cut, hiding the body's rise
    and fall twice it. Over its feet, which move with the picture, the line does rise and fall twice the cut, but a head
    rising twice in one step says the same in one place: a line witnesses only where it says so in both. The cycle is
    held as cut, named, the price of that guard (the parent engine witnessed it over its feet alone)."""
    draw, d = {"steps": (lambda k: _alike(k, **_STEPS, arm=ARMS_IN), 23),
               "behind": (lambda k: _behind(k, cycle=20, hold=2, arm=ARMS_IN), 20)}[walker]
    report = _cut(tmp_path / "out", _keyed(tmp_path, _carried(draw, _shake(sway, d))))
    _not_silent(report, cycle)
    said = _held(report)
    assert abs(said["cut"][1] - cycle) <= 1.5 and said["says"] is None
    feet = said["lines"]["feet"]["bob"]
    assert feet["over"] in ("reach", "sole") and feet["half"] >= local_cycle.BOB_TWICE and feet["quarter"] <= -local_cycle.BOB_TWICE
    assert "its top line rises and falls twice an interval over its feet, but not on the screen" in said["why"]


def _leaning(draw, degrees: float):
    """`draw`'s walker turned about its feet `degrees` over the clip, k/72 of it in frame k (bilinear): a lean
    changing slowly on its feet, on a canvas 60 px taller either side."""

    def frame(k: int) -> Image.Image:
        im = draw(k)
        out = Image.new("RGBA", (im.width, im.height + 120))
        out.paste(im.rotate(degrees * k / (FRAMES - 1), resample=Image.BILINEAR, center=(104, GROUND + 3)), (0, 60))
        return out
    return frame


@pytest.mark.slow
def test_a_walk_leaning_further_on_its_feet_as_it_goes_is_witnessed_one_cycle(tmp_path):
    """The walker whose far foot passes hidden behind the near one, its legs drawn alike, its feet meeting once a cycle
    (17.5 frames), rising and falling 3 px on its legs once a step, its arms swinging within its sides, turned 15
    degrees about its feet over the clip: a lean changing slowly, on its feet. The count reads its crossings as cycles
    and cuts one cycle. On the screen the top line, less its flow, rises and falls of its own twice the cut, every band
    of its upper body a zoom so far would move a pixel holding still on the beat, and over its feet it does so too: two
    steps, witnessed, nothing named. The lean tilts the ground under the feet, and the line over them takes in the two
    feet's heights by turns: read off its frames on a cut of 18, a frame longer, it does not rise and fall twice the cut
    over them, nor four times, nor once, where it does on the screen, and one place alone witnesses nothing."""
    leaning = _leaning(lambda k: _behind(k, cycle=17.5, hold=1, alike=True, arm=ARMS_IN), -15)
    report = _cut(tmp_path / "out", _keyed(tmp_path, leaning))
    _not_silent(report, 17.5)
    c = report["cycle"]
    said = c["cut_witness"]
    assert report["status"] == "passed" and abs(c["length"] - 17.5) <= 1 and said["cut"] == [c["start"], c["length"]]
    assert c["step_count"]["cycles"]["cycles"] is True and c["steps"] == {"count": 2, "by": "crossings"}
    body = _witnessed(report)["lines"]["screen"]["bob"]["body"]
    assert body["still_bands"] == sum(r is not None for r in body["bands"]["reads"]) >= 1
    frames = [leaning(k) for k in range(FRAMES)]
    word = local_cycle.cut_word({**legs.strike_signals(frames), **legs.body_signals(frames)}, 18)
    assert word["says"] is None and "does not rise and fall twice an interval" in word["lines"]["feet"]["why"]
    assert "rises and falls twice an interval on the screen, but not over its feet" in word["lines"]["screen"]["why"]


# The walkers the tests above draw with their arms swinging within their sides (ARMS_IN), drawn with their arms swinging
# 16 px either way of the body's middle, past its sides, as the parent engine's tests drew them: (draw, frames, view, the
# walk's cycle, the cut: "one cycle" and the frames it is cut, "stands" and the frames of the cut witnessed, or "held",
# the frames of the cut held as chosen and the word it holds on).
PAST_ITS_SIDES = {
    "hidden-18.5-ones": (lambda k: _behind(k, cycle=18.5, hold=1), FRAMES, None, 18.5, ("one cycle", 18.5)),
    "hidden-20-twos": (lambda k: _behind(k, cycle=20, hold=2), FRAMES, None, 20, ("one cycle", 20)),
    "hidden-24-threes": (lambda k: _behind(k, cycle=24, hold=3), FRAMES, None, 24, ("one cycle", 24)),
    "alike-17.5": (lambda k: _behind(k, cycle=17.5, hold=1, alike=True), FRAMES, None, 17.5, ("one cycle", 17.5)),
    "alike-17.5-back_diagonal": (lambda k: _behind(k, cycle=17.5, hold=1, alike=True), FRAMES, "back_diagonal", 17.5,
                                 ("one cycle", 17.5)),
    "alike-20": (lambda k: _behind(k, cycle=20, hold=2, alike=True), FRAMES, None, 20, ("one cycle", 20)),
    "alike-20-back_diagonal": (lambda k: _behind(k, cycle=20, hold=2, alike=True), FRAMES, "back_diagonal", 20, ("one cycle", 20)),
    "outward-18": (lambda k: _outward(k, cycle=18), FRAMES, "front_diagonal", 18, ("one cycle", 18)),
    "carried-arc": (_carried(lambda k: _behind(k, cycle=17.5, hold=1, alike=True), "arc", 0), FRAMES, None, 17.5,
                    ("one cycle", 17.5)),
    "carried-cubic": (_carried(lambda k: _behind(k, cycle=17.5, hold=1, alike=True), "cubic", 60), FRAMES, None, 17.5,
                      ("one cycle", 17.5)),
    "leaning": (_leaning(lambda k: _behind(k, cycle=17.5, hold=1, alike=True), -15), FRAMES, None, 17.5, ("one cycle", 17.5)),
    "far-leg-off-side": (lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_half=10), FRAMES, "side", 46,
                         ("stands", 46)),
    "far-leg-off-front_diagonal": (lambda k: _alike(k, cycle=46, phase=13, tone=4, lean=1, leg_half=10), FRAMES,
                                   "front_diagonal", 46, ("stands", 46)),
    "slides": (lambda k: _alike(k, **_STEPS, lift=8, slide=12), FRAMES, None, 46, ("held", 23, "one step")),
    "limps": (lambda k: _alike(k, cycle=30, phase=8, tone=4, lean=1, bob=0, limp=3), 100, None, 30, ("held", 30, "one step")),
    "shaken-over-a-shadow": (_shadowed(lambda k: _alike(k, **_STEPS, bob=0), _shake((0.5, 6, math.pi / 2), 23), shadow="touch",
                                       screen=True), FRAMES, None, 46, ("held", 23, None)),
    "swaying-12-every-46": (_carried(lambda k: _alike(k, **_STEPS), _shake((2, 12, 0), 23)), FRAMES, None, 46, ("held", 46, None)),
    "swaying-24-every-69": (_carried(lambda k: _alike(k, **_STEPS), _shake((3, 24, math.pi / 2), 23)), FRAMES, None, 46,
                            ("held", 46, None)),
    "hidden-swaying-24-every-40": (_carried(lambda k: _behind(k, cycle=20, hold=2), _shake((2, 24, 0), 20)), FRAMES, None, 20,
                                   ("held", 20, None)),
}


@pytest.mark.slow
@pytest.mark.parametrize("form", list(PAST_ITS_SIDES))
def test_a_walk_whose_arms_swing_past_its_sides_reads_as_one_whose_arms_swing_within_them(tmp_path, form):
    """The walkers witnessed above, or held, drawn as the parent engine's tests drew them, their arms swinging past
    their sides (`PAST_ITS_SIDES`): the bands the arms swing across widen and narrow on the beat, and a zoom's part there
    is not told from theirs, but the rows down the middle of the body are told from it, and the arms do not cross them.
    A walk that says two steps a cycle with its arms within its sides says so with them past: a walk whose feet meet once
    a cycle is cut one cycle, and a cut of two steps stands witnessed. A walk held so is held so, on the same word, one
    step or none (the parent engine left the crossings on steps, two intervals, named, and held the rest, its word null).
    Never a silent half cycle."""
    draw, frames, view, cycle, (kind, length, *word) = PAST_ITS_SIDES[form]
    report = _cut(tmp_path / "out", _keyed(tmp_path, draw, frames=frames), *(("--view", view) if view else ()))
    _not_silent(report, cycle)
    if kind == "one cycle":
        assert _one_cycle(tmp_path / "out", length)["cycles"]["bob"]["body"]["depth"]["ratio"] >= local_cycle.BOB_RIGID
    elif kind == "stands":
        said = _witnessed(report)
        assert abs(said["cut"][1] - length) <= 1.5 and _strip_says_whole(said)
    else:
        said = _held(report)
        assert said["says"] == word[0] and abs(said["cut"][1] - length) <= 1.5


@pytest.mark.parametrize("verdict, named", [("uncertain", True), ("two", False)])
def test_a_walk_that_starts_and_stops_in_the_take_is_named_to_film_again(verdict, named):
    """Feet together 15 frames at the head and 14 at the tail, longer than any pass the clip shows whole (8, 3): the
    walk stands still there. Where the cut kept is not two steps, no cut of two closes between the stands and the loop
    names the take to film again (`start-stop`), its stands, crossings and cut; a cut counted two steps is a walk
    looped, and nothing is named. Feet that only pass name nothing."""
    reading = crossings.Reading(FRAMES, "gap", [(10.0, 10.0), (20.0, 20.0), (40.5, 40.5)],
                                runs=[(3, 17), (19, 21), (37, 44), (59, 72)], unconfirmed=[(59, 72)])
    assert reading.stands == [(3, 17), (59, 72)] and reading.run_limit == pytest.approx(8 * math.sqrt(2))
    record = reading.record()
    assert record["stands"] == [[3, 17], [59, 72]] and record["run_limit"] == round(8 * math.sqrt(2), 2)
    cut = {"verdict": verdict, "code": "tail-stall" if verdict == "uncertain" else "two", "why": "plays the clip's tail"}
    retake = loop.stand_retake({"start": 37, "length": 19, "step_count": {**record, "cut": cut}})
    if named:
        assert retake["reason"] == "start-stop" and retake["reasons"] == ["start-stop"] and retake["remedy"] == "retake"
        assert retake["start_stop"]["stands"] == [[3, 17], [59, 72]] and retake["start_stop"]["cut"] == [37, 19]
        assert retake["start_stop"]["code"] == "tail-stall"
        assert loop.retakes(None, retake) == retake
        assert loop.retakes({"reason": "part-pop", "reasons": ["part-pop"], "part_pop": {}, "remedy": "retake"}, retake)["reasons"] == \
            ["part-pop", "start-stop"]
    else:
        assert retake is None
    passing = _reading(3)
    assert passing.stands == [] and "stands" not in passing.record()
    assert loop.stand_retake({"start": 10, "length": 15, "step_count": {**passing.record(), "cut": {"verdict": "uncertain"}}}) is None
