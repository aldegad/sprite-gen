# SPDX-License-Identifier: Apache-2.0
"""A part on its own beat told apart from the body (sprite_gen/video/parts.py): the tail's mask read from the clip's
frames a cycle apart, on frames filmed and on frames made between two; whether it lies in front of the body or behind
it; the tail of another drawing laid behind the body in place of a frame's own without changing a pixel of the body; and
what is not laid — a tail in front of the body, a frame or a tail with no mask, an order read from nothing; and the
loops a mask runs, taken in place, answering as they did when every step made new arrays.

Three walkers are drawn here, 26 frames a cycle with a bob, legs and arms swinging, over a clip of three cycles. Their
sleeves and hands are the tail's orange; every part is outlined in a darker shade of its colour, as cel art is, and
drawn at four times the size and averaged, so every edge is as soft as a keyed frame's. The tail, five segments from a
root at the back, sways on its own beat of 41 frames. Seen from the side (`side`) it hangs behind the body and the near
hand swings over its root. Seen from behind it hangs over the legs and swings over the hands, in front of them: a thin
tail rooted at the small of the back (`back`), which on three frames of the clip lies wholly over a leg, and a bushy one
rooted lower with the hands hanging lower (`back_bushy`), which crosses a hand on a third of the frames and a leg on
all of them. Each pixel's share of the tail is known, and the mask is read against the pixels the tail shows in. A frame
made between two filmed ones is the walker drawn at the time between them, as an optical-flow in-between draws it.
Synthetic frames only."""

from __future__ import annotations

import functools
import math

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import auto_motion, parts

N, BEAT = 26, 41  # frames a walk cycle; frames the tail takes to sway and back
CLIP = range(-N, 2 * N)  # the clip on the loop's clock: the loop is 0..N-1, a cycle of the clip either side of it
READ = range(-N, 2 * N, 2)  # the clip's frames the order is read from
SS, H, W = 4, 240, 200
ORANGE, CREAM = (0.9, 0.5, 0.15), (0.95, 0.9, 0.75)
LINE = 1.5  # px: every part's outline, a darker shade of its colour
# The tail on each walker: its first segment's angle (degrees) and how far it sways, its root, its segments' radii.
TAILS = {"side": dict(base=160, sway=38, root=(120, 88), radii=(9, 8, 7, 6, 5)),
         "back": dict(base=270, sway=68, root=(118, 100), radii=(9, 8, 7, 6, 5)),
         "back_bushy": dict(base=270, sway=68, root=(138, 100), radii=(10, 12, 12, 11, 8))}
REACH = {"back": 54, "back_bushy": 72}  # px from the shoulder to the hand, seen from behind
MADE = (2.5, 6.5, 9.4, 17.6, 21.5)  # times of frames made between two filmed ones
HIDDEN = (-18, 3, 44)  # the frames the thin tail seen from behind lies wholly over a leg on
# The mask against the tail as drawn, over the solid pixels: each frame's overlap with it (IoU) and the solid pixels it
# takes that are not the tail's (leak) or leaves that are (miss), shares of the tail's. Where the tail meets the hand of
# its own colour a pixel or two of either may go to the other side. Measured on the walker seen from the side, every
# frame of the clip and every frame made: IoU 0.865 at least, leak 0.095 at most, miss 0.066.
IOU_MIN, IOU_MEDIAN = 0.83, 0.87
LEAK_MAX, MISS_MAX = 0.16, 0.1
OFF = 0.15  # premultiplied RGBA distance: a pixel of the frame laid this far from the drawing is off it

_yy, _xx = np.mgrid[0:H * SS, 0:W * SS].astype(np.float32)
_yy, _xx = (_yy + 0.5) / SS, (_xx + 0.5) / SS


def _window(points, reach: float) -> tuple[slice, slice]:
    """The whole pixels (rows, columns) of the box round `points` grown by `reach` px and a pixel more: every point of
    the supersampled canvas within `reach` of a segment between them lies inside, so a shape of that radius is drawn
    there alone (the frames are the same bytes as drawn over the whole canvas, in a fraction of the time)."""
    ys, xs = [pt[0] for pt in points], [pt[1] for pt in points]
    return (slice(max(0, math.floor(min(ys) - reach) - 1), min(H, math.ceil(max(ys) + reach) + 1)),
            slice(max(0, math.floor(min(xs) - reach) - 1), min(W, math.ceil(max(xs) + reach) + 1)))


def _seg(p: tuple[float, float], q: tuple[float, float], win: tuple[slice, slice]) -> np.ndarray:
    rows, cols = win
    fine = slice(rows.start * SS, rows.stop * SS), slice(cols.start * SS, cols.stop * SS)
    yy, xx = _yy[fine], _xx[fine]
    (py, px), (qy, qx) = p, q
    vy, vx = qy - py, qx - px
    t = np.clip(((yy - py) * vy + (xx - px) * vx) / max(vy * vy + vx * vx, 1e-9), 0, 1)
    return np.hypot(yy - (py + t * vy), xx - (px + t * vx))


def _cover(inside: np.ndarray) -> np.ndarray:
    return inside.astype(np.float32).reshape(inside.shape[0] // SS, SS, inside.shape[1] // SS, SS).mean(axis=(1, 3))


def _shape(e: np.ndarray, win: tuple[slice, slice], rgb: np.ndarray | tuple, line: tuple | None = None
           ) -> tuple[np.ndarray, np.ndarray]:
    """A shape (its signed distance `e` over the window `win`, inside at or below 0) filled with `rgb`, outlined LINE px
    in `line` (a darker shade of its colour where none is given): its coverage and colour per pixel of the frame, the
    outline's colour and no coverage outside the window."""
    a = np.zeros((H, W), np.float32)
    a[win] = _cover(e <= 0)
    fill = np.clip(_cover(e <= -LINE) / np.maximum(a[win], 1e-6), 0, 1)[..., None]
    rgb = np.broadcast_to(np.asarray(rgb, np.float32), (H, W, 3))
    line = np.broadcast_to(0.35 * np.asarray(rgb if line is None else line, np.float32), (H, W, 3))
    colour = np.array(line)
    colour[win] = fill * rgb[win] + (1 - fill) * line[win]
    return a, colour


def _capsule(p, q, r, rgb):
    win = _window((p, q), r)
    return _shape(_seg(p, q, win) - r, win, rgb)


def _tail(at: float, bob: float, walker: str) -> tuple[np.ndarray, np.ndarray]:
    """The tail at time `at` of its beat, its root on a body bobbed `bob` px: five segments, a wave running down them
    from the root, orange to a cream tip."""
    shape = TAILS[walker]
    phase = 2 * math.pi * at / BEAT
    angles = [shape["base"] + shape["sway"] * math.sin(phase)]
    for k in range(1, 5):
        angles.append(angles[-1] + 14 * math.sin(phase - 0.7 * k))
    joints = [(shape["root"][0] + bob, shape["root"][1])]
    for angle in angles:
        p = joints[-1]
        joints.append((p[0] - 13 * math.sin(math.radians(angle)), p[1] + 13 * math.cos(math.radians(angle))))
    win = _window(joints, max(shape["radii"]))
    e, tip = np.inf, None
    for k, r in enumerate(shape["radii"]):
        ek = _seg(joints[k], joints[k + 1], win) - r
        if k == 4:
            tip = ek < e
        e = np.minimum(e, ek)
    tipped = np.clip(_cover(tip & (e <= 0)) / np.maximum(_cover(e <= 0), 1e-6), 0, 1)[..., None]
    rgb = np.array(np.broadcast_to(np.array(ORANGE, np.float32), (H, W, 3)))
    rgb[win] = (1 - tipped) * np.array(ORANGE, np.float32) + tipped * np.array(CREAM, np.float32)
    return _shape(e, win, rgb, (0.3, 0.2, 0.1))


def _bob(body: float) -> float:
    return 2.5 * math.cos(4 * math.pi * body / N)


def _arm(shoulder, hand, r, hand_r):
    win = _window((shoulder, hand), max(r, hand_r))
    return _shape(np.minimum(_seg(shoulder, hand, win) - r, _seg(hand, hand, win) - hand_r), win, ORANGE)


def _layers(body: float, tail: float | None, walker: str, tail_bob: float | None = None
            ) -> list[tuple[bool, np.ndarray, np.ndarray]]:
    """The walker's layers back to front, (is the tail, coverage, colour): its body at time `body`, its tail at `tail`
    on a root bobbed as the body is (or `tail_bob`), or none."""
    phase = 2 * math.pi * body / N
    bob = _bob(body)
    if walker == "side":
        leg, arm = 0.42 * math.sin(phase), 0.5 * math.sin(phase)
        hip, shoulder = (128 + bob, 100), (78 + bob, 104)
        foot = lambda a: (hip[0] + 80 * math.cos(a), hip[1] + 80 * math.sin(a))  # noqa: E731
        hand = lambda a: (shoulder[0] + 52 * math.cos(a), shoulder[1] + 52 * math.sin(a))  # noqa: E731
        far_hand, near_hand, far_foot, near_foot = hand(arm), hand(-arm), foot(-leg), foot(leg)
        body_ = [_arm(shoulder, far_hand, 5, 6.5),
                 _capsule(hip, far_foot, 7, (0.15, 0.16, 0.26)), _capsule(far_foot, (far_foot[0], far_foot[1] + 11), 5, (0.12, 0.12, 0.2)),
                 _capsule((70 + bob, 104), (126 + bob, 100), 16, (0.25, 0.4, 0.75)),
                 _capsule((44 + bob, 112), (44 + bob, 112), 18, (0.95, 0.8, 0.65)),
                 _capsule((40 + bob, 120), (40 + bob, 120), 3, (0.1, 0.1, 0.15)),
                 _capsule((96 + bob, 90), (96 + bob, 118), 2.5, (0.1, 0.15, 0.3)),
                 _capsule(hip, near_foot, 7, (0.2, 0.22, 0.35)), _capsule(near_foot, (near_foot[0], near_foot[1] + 11), 5, (0.16, 0.16, 0.25)),
                 _arm(shoulder, near_hand, 5, 6.5)]
    else:  # seen from behind: the legs, the back and the head, the arms
        lift = (7 * max(0.0, math.sin(phase)), 7 * max(0.0, -math.sin(phase)))
        body_ = []
        for x, up, rgb in ((86, lift[0], (0.15, 0.16, 0.26)), (114, lift[1], (0.2, 0.22, 0.35))):
            hip, foot = (130 + bob, x), (210 - up, x + 2 * math.sin(phase))
            body_ += [_capsule(hip, foot, 8, rgb), _capsule(foot, foot, 7, (0.12, 0.12, 0.2))]
        body_ += [_capsule((72 + bob, 100), (124 + bob, 100), 24, (0.25, 0.4, 0.75)),
                  _capsule((44 + bob, 100), (44 + bob, 100), 19, (0.95, 0.8, 0.65)),
                  _capsule((112 + bob, 80), (112 + bob, 120), 2.5, (0.1, 0.15, 0.3))]
        for x, out, sign in ((72, -10, 1), (128, 10, -1)):
            shoulder = (66 + bob, x)
            hand = (shoulder[0] + REACH[walker] - 5 * sign * math.sin(phase), x + out + 3 * sign * math.sin(phase))
            body_.append(_arm(shoulder, hand, 5.5, 7))
    rest = [(False, a, c) for a, c in body_]
    if tail is None:
        return rest
    tail_ = [(True, *_tail(tail, bob if tail_bob is None else tail_bob, walker))]
    return tail_ + rest if walker == "side" else rest + tail_


def _compose(layers) -> tuple[np.ndarray, np.ndarray]:
    """Straight RGBA, and each pixel's coverage by the tail as it shows."""
    premul, alpha, tail = np.zeros((H, W, 3), np.float32), np.zeros((H, W), np.float32), np.zeros((H, W), np.float32)
    for is_tail, a, rgb in layers:
        premul = rgb * a[..., None] + premul * (1 - a[..., None])
        tail = (a if is_tail else 0) + tail * (1 - a)
        alpha = a + alpha * (1 - a)
    return np.concatenate([premul / np.maximum(alpha, 1e-6)[..., None], alpha[..., None]], -1), tail


def _image(rgba: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(rgba * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA")


@functools.lru_cache(maxsize=None)
def _drawn(body: float, tail: float | None, walker: str, tail_bob: float | None = None
           ) -> tuple[Image.Image, np.ndarray, np.ndarray]:
    """The walker drawn: (the frame, the pixels the tail shows in — its share of their colour over half — and the solid
    pixels)."""
    rgba, tail_share = _compose(_layers(body, tail, walker, tail_bob))
    alpha = rgba[..., 3]
    return _image(rgba), (tail_share > 0.5 * alpha) & (alpha > parts.SOFT), alpha > parts.SOLID_GROW


def _frame(t: float, walker: str) -> Image.Image:
    return _drawn(t, t, walker)[0]


@functools.lru_cache(maxsize=None)
def _regions(walker: str) -> list:
    regions, _ = auto_motion.discover([_frame(t, walker) for t in range(N)], reference_index=0)
    return regions


@functools.lru_cache(maxsize=None)
def _masker(walker: str) -> parts.Masker:
    return parts.Masker({t: _frame(t, walker) for t in CLIP}, N, _regions(walker))


def _read(mask: np.ndarray, t: float, walker: str) -> dict[str, float]:
    _, tail, solid = _drawn(t, t, walker)
    tail, mask = tail & solid, mask & solid
    return {"iou": (mask & tail).sum() / (mask | tail).sum(), "leak": (mask & ~tail).sum() / tail.sum(),
            "miss": (tail & ~mask).sum() / tail.sum()}


def _crossed(t: float) -> bool:
    """Whether the near hand and the tail of the walker seen from the side, each drawn alone, overlap at time t."""
    a = -0.5 * math.sin(2 * math.pi * t / N)
    hand = (78 + _bob(t) + 52 * math.cos(a), 104 + 52 * math.sin(a))
    win = _window((hand, hand), 6.5)
    return bool(((_cover(_seg(hand, hand, win) <= 6.5) > 0.5) & (_tail(t, _bob(t), "side")[0][win] > 0.5)).any())


def _bytes(frames) -> list[bytes]:
    return [np.asarray(f).tobytes() for f in frames]


@pytest.mark.slow
def test_a_tail_behind_the_body_is_read_where_it_is_drawn_on_every_frame():
    # Every frame of the clip, and frames made between two read at the time they show: none left out of the bounds.
    masker = _masker("side")
    reads = [_read(masker.clip_mask(t), t, "side") for t in CLIP]
    reads += [_read(masker.mask(_frame(t, "side"), t), t, "side") for t in MADE]
    iou, leak, miss = (np.array([r[k] for r in reads]) for k in ("iou", "leak", "miss"))
    assert iou.size == 83
    assert iou.min() >= IOU_MIN and np.median(iou) >= IOU_MEDIAN, iou.round(3)
    assert leak.max() <= LEAK_MAX and miss.max() <= MISS_MAX, (leak.round(3), miss.round(3))
    # and the near hand, the tail's colour, swings over the tail's root on some frames of the loop
    assert sum(_crossed(t) for t in range(N)) >= 4


# parts.py's three hot loops as first written, making new arrays every step: what they take each step in place now must
# give, byte for byte.
def _shift_as_written(m, dy, dx, fill=0):
    h, w = m.shape[:2]
    out = np.full_like(m, fill)
    out[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = m[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
    return out


def _least_as_written(seed, cost_to):
    d = np.where(seed, 0.0, np.inf).astype(np.float32)
    for k in range(parts.ITERATIONS):
        old = d
        for c, (dy, dx) in zip(cost_to, parts.NB):
            d = np.minimum(d, _shift_as_written(d, dy, dx, np.inf) + c)
        if k % 25 == 24 and np.array_equal(d, old):
            break
    return d


def _feet_as_written(self, a, b):
    top = int(parts.FEET_ROWS * a.shape[0])
    best = math.inf
    r = self.feet_shift
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            bb = _shift_as_written(b, dy, dx)
            d = np.abs(a - bb).max(-1)[top:]
            on = ((a[..., 3] > 0.05) | (bb[..., 3] > 0.05))[top:]
            v = np.sort(d[on])
            if v.size:
                best = min(best, float(v[: max(1, int(0.75 * v.size))].mean()))
    return best


def _far_as_written(self, a, b):
    best = np.full(a.shape[:2], np.inf, np.float32)
    for dy, dx in parts._disk(self.search):
        best = np.minimum(best, np.abs(a - _shift_as_written(b, dy, dx)).max(-1))
    return best


@pytest.mark.slow
def test_each_loop_a_mask_runs_gives_the_same_bytes_in_place_as_with_new_arrays_every_step(monkeypatch):
    # Every call of the three loops a frame's mask makes, of a frame filmed and of one made between two, is answered as
    # the loops first written answer it: the same dtype, the same bytes. The second least-cost reading's costs are
    # float64 (each side's own colours), and its distances are read in that precision, as they were.
    masker = _masker("side")
    calls = []

    def kept(name, loop):
        def call(*args):
            calls.append((name, args, loop(*args)))
            return calls[-1][2]
        return call

    monkeypatch.setattr(parts, "_least", kept("least", parts._least))
    monkeypatch.setattr(parts.Masker, "_feet", kept("feet", parts.Masker._feet))
    monkeypatch.setattr(parts.Masker, "_far", kept("far", parts.Masker._far))
    for t in (0, MADE[0]):
        part, _, _ = masker.mask_read(masker._read(_frame(t, "side")), t)
        assert part.any(), t
    as_written = {"least": _least_as_written, "feet": _feet_as_written, "far": _far_as_written}
    for name, args, got in calls:
        want = as_written[name](*args)
        if name == "feet":
            assert got == want
        else:
            assert (got.dtype, got.shape, got.tobytes()) == (want.dtype, want.shape, want.tobytes()), name
    assert {name for name, _, _ in calls} == {"least", "feet", "far"}
    assert {args[1][0].dtype.name for name, args, _ in calls if name == "least"} == {"float32", "float64"}


@pytest.mark.slow
@pytest.mark.parametrize("reading", ["clip", "wrap"])
@pytest.mark.parametrize("walker, rare", [("side", parts.RARE), ("back", parts.RARE), ("back_bushy", parts.RARE),
                                          ("side", parts.RARE / 2), ("side", 2 * parts.RARE),
                                          ("back_bushy", parts.RARE / 2), ("back_bushy", 2 * parts.RARE)])
def test_in_front_of_the_body_or_behind_it_is_read_from_the_clip(walker, rare, reading):
    # The order's mask seeds the body with the colours the rims of the frames read rarely show; read from every other
    # frame of the clip or from the frames beside the wrap alone, and with half the share or twice it, each walker reads
    # the same way.
    masker = _masker(walker)
    read = masker.order(READ if reading == "clip" else masker.near_wrap(), rare)
    if walker == "side":
        assert read["order"] == "behind" and read["front_share"] < parts.FRONT - 0.1, read
    else:
        assert read["order"] == "front" and read["front_share"] > parts.FRONT + 0.05, read


@pytest.mark.slow
def test_a_lay_reads_the_order_from_the_frames_beside_the_wrap_alone(monkeypatch):
    # A lay reads the rims of the clip's frames within WRAP of the wrap, on either side and in every cycle, and of no
    # other frame.
    clip = {t: _frame(t, "back_bushy") for t in CLIP}
    masker = parts.Masker(clip, N, _regions("back_bushy"))
    read = []
    rim = masker.rim
    monkeypatch.setattr(masker, "rim", lambda a, at: read.append(at) or rim(a, at))
    _, record = parts.lay_behind(masker, [(clip[0], 0, clip[N], N)])
    near = [t for t in CLIP if t % N < parts.WRAP or t % N >= N - parts.WRAP]
    assert record["refused"] == "front" and record["times"] == near and sorted(read) == near
    assert len(near) == 3 * 2 * parts.WRAP


@pytest.mark.slow
def test_a_tail_laid_behind_the_body_changes_none_of_its_solid_pixels():
    # The eased retime's case: the tail of a frame made a frame on, laid behind the body of the frame at its own time.
    masker = _masker("side")
    times = range(0, N, 3)
    lays = [(_frame(t, "side"), t, _frame(t + 1.0, "side"), t + 1.0) for t in times]
    made, record = parts.lay_behind(masker, lays)
    assert record["refused"] is None and record["order"]["order"] == "behind" and len(made) == len(lays)
    for (frame, t, source, at), laid, done in zip(lays, made, record["lays"]):
        mask, source_mask = masker.clip_mask(t), masker.mask(source, at)
        f, out = np.asarray(frame), np.asarray(laid)
        body = ~mask & (f[..., 3] > 0)
        solid = body & (f[..., 3] >= round(parts.SOLID * 255))
        assert (out[solid] == f[solid]).all(), t
        # a body pixel less than solid shows the new tail through it, and is itself as drawn: laid over the tail's layer
        layer = np.asarray(source).copy()
        layer[~source_mask] = 0
        under = np.asarray(Image.alpha_composite(Image.fromarray(layer), frame))
        assert (out[body & ~solid] == under[body & ~solid]).all(), t
        assert done["refused"] is None and done["laid_px"] == int((layer[..., 3] > 0).sum())
        # and what it makes is the walker with its body at t and its tail a frame on, but where the masks miss or overreach
        drawn = _drawn(t, at, "side", _bob(at))[0]
        assert _off(laid, drawn).sum() <= LEAK_MAX * _drawn(t, t, "side")[1].sum(), t


@pytest.mark.slow
@pytest.mark.parametrize("walker", ["back", "back_bushy"])
def test_a_tail_in_front_of_the_body_is_not_laid_and_no_frame_is_read_for_it(walker, monkeypatch):
    # Laid behind the body, the tail would go under the legs and the hands it hangs over. The order is read first; no
    # frame's mask is read, and every frame is as it was given.
    masker = _masker(walker)
    monkeypatch.setattr(masker, "mask", lambda frame, at: pytest.fail(f"a mask read at {at} after the order"))
    lays = [(_frame(t, walker), t, _frame(t + 0.5, walker), t + 0.5) for t in range(0, N, 5)]
    made, record = parts.lay_behind(masker, lays)
    assert record["refused"] == "front" and record["order"]["order"] == "front" and record["lays"] == []
    assert _bytes(made) == _bytes(frame for frame, _, _, _ in lays)


def test_a_tail_lying_wholly_over_a_leg_has_no_mask_and_nothing_is_laid_with_it():
    # Where the thin tail lies wholly over a leg, no frame of its walk phase a cycle away leaves clear what it covers:
    # there is no rim to read it from, and its mask is empty. Laid on with that mask, the frame would keep its own tail
    # beside the one laid; laid from it, the frame would lose its own and get none. Nothing is laid, whichever the order.
    masker = _masker("back")
    for t in HIDDEN:
        frame, tail, solid = _drawn(t, t, "back")
        assert (tail & solid).sum() > 1000 and not masker.clip_mask(t).any(), t
        other = t + N if t + N in CLIP else t - N
        source, source_mask = _frame(other, "back"), masker.clip_mask(other)
        for order in ("behind", "front"):
            for args in ((frame, masker.clip_mask(t), source, source_mask), (source, source_mask, frame, masker.clip_mask(t))):
                made, record = parts.lay(*args, order)
                assert record["refused"] == "empty" and record["laid_px"] == 0, (t, order)
                assert made.tobytes() == args[0].tobytes()


def test_a_tail_with_no_mask_refuses_every_frame_laid_with_it():
    # A tail hidden wholly behind the body shows nothing of itself to read: laid from such a frame, the frame laid on
    # would lose its own tail and get none. The frames laid before it are not laid either.
    masker = _masker("side")
    hidden = _drawn(18.0, None, "side")[0]
    lays = [(_frame(t, "side"), t, _frame(t + 1.0, "side"), t + 1.0) for t in (0, 9)] + [(_frame(18, "side"), 18, hidden, 18.0)]
    made, record = parts.lay_behind(masker, lays)
    assert record["refused"] == "empty" and record["order"]["order"] == "behind"
    assert [(done["refused"], done["source_px"] > 0) for done in record["lays"]] == [(None, True), (None, True), ("empty", False)]
    assert _bytes(made) == _bytes(frame for frame, _, _, _ in lays)


def test_frames_none_of_which_has_one_of_its_walk_phase_a_cycle_away_read_no_order():
    # One cycle of the clip has no frame a cycle from another, so nothing reads the part in front of the body or behind
    # it; read as behind, a tail in front would be laid under the body. Nothing is laid.
    masker = _masker("side")
    assert masker.order(range(N)) == {"order": None, "front_share": None, "part_px": 0}
    lays = [(_frame(t, "side"), t, _frame(t + 1.0, "side"), t + 1.0) for t in (0, 9)]
    made, record = parts.lay_behind(masker, lays, times=range(N))
    assert record["refused"] == "no pairs" and record["lays"] == []
    assert _bytes(made) == _bytes(frame for frame, _, _, _ in lays)


def _straight(im: Image.Image) -> np.ndarray:
    return np.asarray(im).astype(np.float32) / 255


def _off(made: Image.Image, drawn: Image.Image) -> np.ndarray:
    """The pixels of `made` off `drawn` (premultiplied)."""
    a, b = _straight(made), _straight(drawn)
    a[..., :3] *= a[..., 3:]
    b[..., :3] *= b[..., 3:]
    return np.abs(a - b).max(-1) > OFF


@pytest.mark.parametrize("walker", ["side", "back_bushy"])
def test_with_the_tails_own_masks_the_frame_laid_is_the_drawing(walker):
    # Laying alone: given the pixels each tail shows in, the frame laid is the walker drawn with the frame's body and the
    # other drawing's tail, but at the edges where a soft pixel of the one met the other, and behind the body where the
    # other drawing's own hand hid some of its tail and the frame's hand does not. In front of the body, the frame's own
    # tail's place is taken from the frames of its walk phase a cycle or two away.
    order = "behind" if walker == "side" else "front"
    for t in range(0, N, 3):
        frame, mask, _ = _drawn(t, t, walker)
        other = t + 1.0 if order == "behind" else t + N
        source, source_mask, _ = _drawn(other, other, walker)
        plates = [_drawn(s, s, walker)[:2] for s in (t - N, t + N, t + 2 * N)] if order == "front" else []
        laid, record = parts.lay(frame, mask, source, source_mask, order, plates=plates)
        assert record["refused"] is None
        drawn = _drawn(t, other, walker, _bob(other))[0]
        assert _off(laid, drawn).sum() <= 0.1 * mask.sum(), t


def test_an_order_neither_in_front_nor_behind_is_refused():
    frame = _frame(0, "side")
    none = np.zeros((H, W), bool)
    with pytest.raises(ValueError, match="front"):
        parts.lay(frame, none, frame, none, "over")
