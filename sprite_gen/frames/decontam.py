# SPDX-License-Identifier: Apache-2.0
"""Edge decontamination: give a keyed edge the subject's own colour back (`decontam: "palette"`).

The chroma engine splits a key/subject blend using the key-tint score alone. That
assumes the subject colour carries no tint of its own, which holds for grey and
white and fails for saturated colours. A red hair strand blended a third of the
way into green scores as barely tinted, so it stays opaque and olive, and when
`despill_color` does reach it the recovery overshoots into orange (the warm bias
of the `1/(1-k)` gain). Video adds a second loss: 4:2:0 chroma subsampling has
already averaged the key into the colour of every strand one or two pixels wide,
so no amount of unmixing that pixel alone can find the red again. What survives
is the luma, which the codec keeps at full resolution.

This pass explains each edge pixel as a mix of the key background, measured
locally, with one colour the subject actually owns:

    observed = alpha * P + (1 - alpha) * B

P ranges over a small palette learned from the subject's confident interior.
The pixel takes the P whose line through B passes closest to the observation;
alpha is the projection onto that line. The colour written is P's chroma at the
observation's luma: the hue comes from the subject, the light and shade from
the pixel. With a key-free palette, the output cannot carry the key's hue at
all, and no channel gain is applied, so nothing can drift warm.

Guards, in order of the decision they make:

- *Background flank (still fit).* Keyed pixels within `FLANK_PX` of the subject may
  regain coverage (the faint sides of a strand the hard cut erased), above a noise
  floor measured on the background itself, and only when chained to the subject.
  The video fit gives the keyed flank nothing: a decoded frame's blur reads as a
  faint blend on every keyed pixel near the subject (*Frame edge*), and the codec's
  noise carried those across the floor and back from one frame to the next, so the
  dark gaps between strands filled with a faint smear that came and went.
- *Frame edge.* Within `edge_band` of the frame's top, left and right edges (the
  band `video-frames` reads as the subject touching the frame or the key surviving
  the matte), a pixel the matte left transparent stays transparent. A decoded
  frame's blur runs the subject on into the key as a smooth tail that reads as a
  faint blend at every distance, the background past the flank as well, so a head
  a few pixels under the frame edge would get a faint flank there that no floor
  tells from the tail, and that flank alone would fail a clip the matte passes.
- *Unexplained.* A pixel no palette colour explains (a small gem of a colour the
  interior never shows) keeps the engine's bytes. In the still fit the blend must
  also reproduce the observed luma, so outline ink darker than any mix of a
  palette colour with the key stays as the engine left it.
- *Opaque.* A band pixel that is closer to some palette colour as it is than the
  mix explains it (by `OPAQUE_MARGIN`, or three noise sigmas when the source is
  noisier) is ordinary shading, not a blend: it keeps the engine's bytes. Only
  when the subject owns no key-coloured material is a residual key hue on such a
  pixel capped (`KEY_HUE_TINT`), which lowers the keyed channels and never raises
  one.
- *Own colour (still fit).* Gold sits between red and green, so a gold a little
  darker or yellower than the palette's own also reads as a warmer palette gold
  with some green mixed in, and came out orange and see-through. The still fit
  therefore also counts a palette colour at the pixel's own luma (the freedom the
  written colour has too) when it comes within the subject's own spread: how far
  its confident interior sits from the palette (`MATERIAL_SPREAD_QUANTILE`), which
  also raises the margin a blend has to win by. And a still's antialiased edge is
  at most `_IN_BAND_UNMIX_KEY_DEPTH` pixels deep, so deeper in the band a key share
  shows only as the key's hue: a pixel without it is the subject's own colour, and
  so is an edge pixel without it that has the colour of the plain material right
  behind it. Such pixels are never blends or tints. Where the matte changed one of
  them although it carries no key hue, the source colour and coverage come back:
  the RGB matte scores tint on the channel average, which calls yellow green (and
  red or blue magenta), so it unmixes gold as if it were a blend. The video fit
  does none of this, since a decoded frame's chroma is blurred and an edge pixel's
  colour is no evidence of what the subject is made of there.
- *Key-coloured parts (video fit).* A subject can own a key-coloured part too small
  for the palette to keep (a green charm under a green key, under
  `KEY_MATERIAL_MAX_SHARE` of the interior). A key-free palette then has no colour that
  explains the part's edge, and the fit repainted it in the nearest key-free colour: a
  green charm's rim came out brown. Band pixels within `KEY_PART_GUARD_PX` of
  such a part (opaque, deeper than `BAND_PX`, key-hued past `KEY_HUE_TINT`) keep the
  engine's bytes, and the key-hue cap leaves them alone.
- *Material behind (video fit).* A band pixel that has the colour, as it is or at its
  own luma, of the subject right behind it (the mean of subject pixels deeper than
  `_IN_BAND_UNMIX_KEY_DEPTH` within `VIDEO_BEHIND_PX`), to within the opaque margin, is
  that material carried on to the edge, not a blend or a tint: it keeps the engine's
  bytes, the key-hue cap still applying. Unlike the still fit's test it does not ask
  the pixel to be free of the key's hue, which a decoded frame's blurred chroma gives
  every edge a little of.
- *Tint.* Between the two: a pixel pushed from its colour toward the key by at
  least `TINT_SHIFT`, and `TINT_RATIO` times more than the fit misses by, is
  recoloured with its coverage left alone (a faint cast, not proven coverage).
- *Depth.* Coverage is refitted only within `alpha_depth` of the keyed region
  (the engine's unmix reach, same meaning). Deeper band pixels get colour only,
  so a key reflection on a solid surface is recoloured, not turned see-through.
- *Hold (video fit, with a clip's `history`).* Codec noise moves an edge pixel back
  and forth across the tests above from one frame to the next, and each crossing
  changes the colour written by far more than the noise did (the matte follows the
  noise smoothly, so with the pass off the edge stays calm). So each test keeps the
  outcome it had at that pixel in the last frame until this frame's evidence passes
  its bar the other way by `VIDEO_HOLD`, and the palette colour picked last frame
  stays unless another one's line passes closer by as much. A pixel that moves
  changes its evidence by far more than that and is decided afresh.
- *Blend (video fit, with a clip's `history`).* A call the hold does let go still
  changed what the pixel showed by the whole gap between the matte's colour and the
  fit's in one frame (kept to written, a tint to a blend and so the matte's coverage
  to the fit's, one palette colour to another): on a still edge, the rare large jump
  the matte does not have. So an examined pixel shows both renderings, the matte's
  and the fit's, made from this frame's evidence and mixed, and the mix moves toward
  this frame's call by at most `VIDEO_BLEND` a frame, and less where that would move
  the output by more than `VIDEO_STEP` plus `VIDEO_STEP_MOTION` per unit its source
  moved (premultiplied, any channel or coverage). A moving pixel follows its subject
  at once, since both renderings do; only the change of call is spread over frames.
  A still pixel that came into or left the band, or that is still on its way to its
  call, moves at most `VIDEO_STEP` a frame from what it showed; a pixel the hard cut
  keyed stays transparent.
- *Settle (video fit, with a clip's `history`).* A pixel written in this frame and the
  last in the same palette colour, both at their call, whose source did not move by
  more than `VIDEO_STILL` anywhere in its 3x3 box, moves `VIDEO_SETTLE` of the way from
  what it was toward this frame's fit, never farther than `VIDEO_SETTLE_REACH` from
  that fit, and catches up past that by at most `VIDEO_STEP` a frame: what is left
  changing there is the codec's noise.

A subject that owns key-coloured material (more than `KEY_MATERIAL_MAX_SHARE` of
its interior carries the key hue, the same test `video-frames --spill auto` runs
on its reference still) keeps that material in the palette; its edges are then
repainted with whatever explains them best, key colour included.

`fit="video"` chooses P on a 3x3 mean of the observation and averages the
projection alpha with a luma-only alpha where P and B differ in luma, because a
decoded video frame's chroma is blurred while its luma is not.

Everything here is integer-exact or a fixed sequence of float64 operations: no
random initialisation, no iteration-order dependence, the same input gives the
same bytes. The one learned component, the palette, is a histogram k-means with
a deterministic seed (greedy weighted farthest-point) and a fixed iteration count.
With a clip's `history` the input is the clip so far: a frame's bytes depend on the
frames before it, in the order they were given, and on nothing else.
"""

from __future__ import annotations

from typing import Any

from sprite_gen._deps import np
from sprite_gen.frames.extract import (_IN_BAND_UNMIX_KEY_DEPTH, DECONTAM_MODES, OFF, PALETTE, _grow_chebyshev,
                                       _grow_into, _key_channel_split, _key_excess_field)

DECONTAM_FITS = ("still", "video")

BAND_PX = 6  # subject pixels this close (Chebyshev) to the keyed background are re-examined
FLANK_PX = 3  # keyed pixels this close to the subject may regain coverage
BACKGROUND_RADIUS = 12  # local background = mean of keyed pixels in this box radius
PALETTE_SIZE = 32
PALETTE_BITS = 5  # histogram quantisation for the palette k-means
PALETTE_ITERATIONS = 10
KEY_HUE_TINT = 8.0  # the key-hue bar of the full spill pass (extract._SPILL_FULL_MIN_TINT)
KEY_MATERIAL_MAX_SHARE = 0.005  # the key-material share `video-frames --spill auto` tolerates
OPAQUE_MARGIN = 10.0  # a blend must explain the pixel this much better than "opaque as observed"
# ... and better than the subject's own interior sits from its palette: material this far from every
# palette colour is still material (the quantile of the confident interior's distance to the palette)
MATERIAL_SPREAD_QUANTILE = 99.0
MATERIAL_SPREAD_SAMPLES = 1 << 16  # interior pixels the spread is measured on, at a fixed stride
NOISE_SIGMAS = 3.0
MIN_RECOVERED_ALPHA = 0.06
LUMA_TRUST_ALPHA = 0.35  # below this coverage the observed luma is noise; lean on the palette
UNEXPLAINED_RATIO = 0.5
UNEXPLAINED_SLACK = 8.0
LUMA_SEPARATION = 24.0  # video fit: luma alpha only where P and B differ this much in luma
LUMA_MISS_SLACK = 12.0  # still fit: a blend must reproduce the observed luma this closely (plus 3 sigma)
TINT_SHIFT = 8.0  # a tint moves the colour at least this far toward the key ...
TINT_RATIO = 2.0  # ... and at least this many times farther than the fit misses by
KEY_PART_GUARD_PX = BAND_PX  # video fit: edge pixels this close to a key-coloured part keep the engine's bytes
VIDEO_BEHIND_PX = 3  # video fit: the material behind an edge pixel is the subject's mean within this box radius
VIDEO_HOLD = 16.0  # video fit: how far (RGB) a test must pass the other way, or a colour fit closer, to undo last frame's call
VIDEO_SETTLE = 0.1  # video fit: the share of the way a written pixel that stood still moves from last frame's value
VIDEO_SETTLE_REACH = 12.0  # video fit: ... and never farther than this (per channel) from what this frame's fit gives
VIDEO_STILL = 8  # video fit: stood still = no source channel within the pixel's 3x3 box moved more than this
VIDEO_BLEND = 0.2  # video fit: the most a pixel's mix of two calls moves toward this frame's call in a frame (*Blend*)
VIDEO_STEP = 4.0  # video fit: ... and the most that moves its output, premultiplied (any channel or coverage, 0-255)
VIDEO_STEP_MOTION = 0.25  # ... plus this much per unit its source moved (the largest channel change in its 3x3 box)
LUMA = np.array([0.299, 0.587, 0.114])
_CHUNK = 1 << 16
# what the video fit keeps of each pixel it examined (`history["tests"]`): that it saw it, and each held test's outcome
_SEEN, _EXPLAINED, _BEHIND, _BLEND, _TINT_SHIFT, _TINT_RATIO = 1, 2, 4, 8, 16, 32


def validate(mode: str, fit: str = "still") -> None:
    if mode not in DECONTAM_MODES:
        raise SystemExit(f"decontam: unknown mode {mode!r}; expected one of {', '.join(DECONTAM_MODES)}")
    if fit not in DECONTAM_FITS:
        raise SystemExit(f"decontam: unknown fit {fit!r}; expected one of {', '.join(DECONTAM_FITS)}")


def _ring_depth(seed: np.ndarray, rings: int) -> np.ndarray:
    """Chebyshev distance to `seed`, counted ring by ring: 0 on it, rings + 1 beyond the last ring."""
    depth = np.full(seed.shape, rings + 1, dtype=np.uint8)
    depth[seed] = 0
    reached = seed
    for ring in range(1, rings + 1):
        grown = _grow_chebyshev(reached)
        depth[grown & ~reached] = ring
        reached = grown
    return depth


def _box_sum(values: np.ndarray, radius: int) -> np.ndarray:
    """Sum over a (2r+1)^2 window, clipped at the image edge. Integer-valued input stays exact."""
    height, width = values.shape[:2]
    table = np.zeros((height + 1, width + 1) + values.shape[2:], dtype=np.float64)
    table[1:, 1:] = values.cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(height) - radius, 0, height)
    y1 = np.clip(np.arange(height) + radius + 1, 0, height)
    x0 = np.clip(np.arange(width) - radius, 0, width)
    x1 = np.clip(np.arange(width) + radius + 1, 0, width)
    return table[y1][:, x1] - table[y0][:, x1] - table[y1][:, x0] + table[y0][:, x0]


def _box_sum_at(values: np.ndarray, radius: int, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """`_box_sum` of a 2-D array, at the given pixels only: one summed-area table, no full-size result."""
    height, width = values.shape
    table = np.zeros((height + 1, width + 1), dtype=np.float64)
    table[1:, 1:] = values.cumsum(0).cumsum(1)
    y0 = np.clip(rows - radius, 0, height)
    y1 = np.clip(rows + radius + 1, 0, height)
    x0 = np.clip(cols - radius, 0, width)
    x1 = np.clip(cols + radius + 1, 0, width)
    return table[y1, x1] - table[y0, x1] - table[y1, x0] + table[y0, x0]


def local_background(rgb: np.ndarray, preferred: np.ndarray, fallback: np.ndarray, radius: int) -> np.ndarray:
    """Mean keyed colour within `radius`, from `preferred` pixels where any, else from `fallback`."""
    out = np.zeros(rgb.shape, dtype=np.float64)
    found = np.zeros(rgb.shape[:2], dtype=bool)
    for mask in (preferred, fallback):
        weight = _box_sum(mask.astype(np.float64), radius)
        total = _box_sum(rgb * mask[..., None], radius)
        fill = (weight > 0) & ~found
        out[fill] = total[fill] / weight[fill][:, None]
        found |= fill
    return out


def learn_palette(samples: np.ndarray, size: int = PALETTE_SIZE) -> np.ndarray:
    """Deterministic weighted k-means over a 5-bit colour histogram of `samples` (N, 3)."""
    samples = np.asarray(samples, dtype=np.int64).reshape(-1, 3)
    shift = 8 - PALETTE_BITS
    q = samples >> shift
    code = (q[:, 0] << (2 * PALETTE_BITS)) | (q[:, 1] << PALETTE_BITS) | q[:, 2]
    bins = 1 << (3 * PALETTE_BITS)
    counts = np.bincount(code, minlength=bins)
    used = np.flatnonzero(counts)
    weight = counts[used].astype(np.float64)
    points = np.stack([np.bincount(code, weights=samples[:, c].astype(np.float64), minlength=bins)[used]
                       for c in range(3)], axis=-1) / weight[:, None]
    centres = [(points * weight[:, None]).sum(0) / weight.sum()]
    nearest = ((points - centres[0]) ** 2).sum(-1)
    for _ in range(1, min(size, len(points))):
        pick = int(np.argmax(weight * nearest))  # greedy weighted farthest point; ties -> lowest bin
        if weight[pick] * nearest[pick] <= 0:
            break
        centres.append(points[pick])
        nearest = np.minimum(nearest, ((points - points[pick]) ** 2).sum(-1))
    centres = np.array(centres)
    for _ in range(PALETTE_ITERATIONS):
        label = ((points[:, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
        for j in range(len(centres)):
            member = label == j
            if member.any():
                centres[j] = (points[member] * weight[member, None]).sum(0) / weight[member].sum()
    return centres


def _cap_key_hue(rgb: np.ndarray, keyed_channels: list[int], unkeyed_channels: list[int], tint: float) -> np.ndarray:
    """Take the key hue out of the colours that carry it and leave every other colour alone.

    A colour carries the key hue by the engine's own test (`_key_excess_field`: G - max(R, B)
    for green, min(R, B) - G for magenta). Where that excess passes `tint`, the part beyond it
    comes off every keyed channel alike: for green that is G <= max(R, B) + tint, for magenta
    R and B come down together and whichever led keeps its lead. A red, a blue or a skin tone
    under a magenta key has no key hue and is returned as it is. Never raises a channel.
    """
    out = np.array(rgb, dtype=np.float64, copy=True)
    over = np.maximum(_key_excess_field(out, keyed_channels, unkeyed_channels) - tint, 0.0)
    for channel in keyed_channels:
        out[..., channel] -= over
    return out


def recolor_to_luma(colour: np.ndarray, luma: np.ndarray) -> np.ndarray:
    """`colour` moved to `luma`: darkened by scaling, brightened toward white. Hue is kept either way."""
    own = colour @ LUMA
    target = np.clip(luma, 0, 255)
    darker = target <= own
    scale = np.where(darker, target / np.maximum(own, 1e-6), 0.0)
    lift = np.where(darker, 0.0, (target - own) / np.maximum(255 - own, 1e-6))
    return np.where(darker[:, None], colour * scale[:, None], colour + lift[:, None] * (255 - colour))


def material_distance(obs: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """How far each observation (N, 3) is from being the subject's own material: the nearest
    palette colour as it is, or at the observation's luma (the shading the colour written by
    `decontaminate` is granted too)."""
    obs = np.asarray(obs, dtype=np.float64)
    light = obs @ LUMA
    nearest = np.full(len(obs), np.inf)
    for colour in np.asarray(palette, dtype=np.float64):
        shaded = recolor_to_luma(np.broadcast_to(colour, obs.shape), light)
        nearest = np.minimum(nearest, np.minimum(np.linalg.norm(obs - colour, axis=-1),
                                                 np.linalg.norm(obs - shaded, axis=-1)))
    return nearest


def _box3(rgb: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """3x3 mean (edge-clamped) of `rgb` at the given pixels."""
    height, width = rgb.shape[:2]
    acc = np.zeros((len(rows), 3), dtype=np.float64)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            acc += rgb[np.clip(rows + dy, 0, height - 1), np.clip(cols + dx, 0, width - 1)]
    return acc / 9.0


def _line_miss(towards: np.ndarray, line: np.ndarray) -> np.ndarray:
    """How far `towards` (observation minus background) passes from the segment from 0 to `line` (colour minus
    background), per pixel."""
    length = np.maximum((line * line).sum(-1), 1e-9)
    along = np.clip((towards * line).sum(-1) / length, 0.0, 1.0)
    return np.linalg.norm(towards - along[:, None] * line, axis=-1)


def _fit_alpha(offset: np.ndarray, line: np.ndarray, fit: str) -> np.ndarray:
    """The coverage that explains `offset` (observation minus background) along `line` (palette colour minus
    background), per pixel: the projection, which the video fit averages with a luma-only alpha where the two
    differ in luma (a decoded frame's chroma is blurred, its luma is not)."""
    alpha = np.clip((offset * line).sum(-1) / np.maximum((line * line).sum(-1), 1e-9), 0.0, 1.0)
    if fit == "video":
        luma_gap = line @ LUMA
        separated = np.abs(luma_gap) >= LUMA_SEPARATION
        luma_alpha = np.clip((offset @ LUMA) / np.where(separated, luma_gap, 1.0), 0.0, 1.0)
        alpha = np.where(separated, 0.5 * (alpha + luma_alpha), alpha)
    return alpha


def _fit_colour(obs: np.ndarray, bg: np.ndarray, chosen: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """The colour written for palette colour `chosen` at coverage `alpha`: its chroma at the observation's light with
    the fitted key share taken out, leaning on the palette colour where coverage is too thin for the observed luma to
    mean much."""
    observed_luma = (obs @ LUMA - (1 - alpha) * (bg @ LUMA)) / np.maximum(alpha, 1e-3)
    trust = np.clip(alpha / LUMA_TRUST_ALPHA, 0.0, 1.0)
    target = trust * observed_luma + (1 - trust) * (chosen @ LUMA)
    return recolor_to_luma(chosen, target)


def _hold(value: np.ndarray, bar: Any, before: np.ndarray, bit: int, *, strict: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """`value <= bar` (`<` when `strict`), with the outcome the last frame recorded under `bit` in `before` (*Hold*):
    a pixel it passed passes up to bar + VIDEO_HOLD, one it failed needs bar - VIDEO_HOLD, and a pixel it did not
    examine takes the plain test. Returns (outcome, held), held where the outcome is not this frame's alone."""
    fresh = value < bar if strict else value <= bar
    seen = (before & _SEEN) != 0
    shifted = bar + np.where(seen, np.where((before & bit) != 0, VIDEO_HOLD, -VIDEO_HOLD), 0.0)
    outcome = value < shifted if strict else value <= shifted
    return outcome, outcome != fresh


def _box_max3(values: np.ndarray) -> np.ndarray:
    """Maximum over each pixel's 3x3 box (edge-clamped) of a 2-D array."""
    pad = np.pad(values, 1, mode="edge")
    height, width = values.shape
    return np.max([pad[1 + dy:1 + dy + height, 1 + dx:1 + dx + width] for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)


def _premultiplied(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Rows of colour (0-255) and coverage (0-1) as what they add over any background: colour times coverage, and
    the coverage, all on 0-255."""
    return np.concatenate([rgb * alpha[:, None], 255.0 * alpha[:, None]], axis=1)


def _toward(was: np.ndarray, fresh: np.ndarray, step: Any) -> np.ndarray:
    """RGBA rows (0-255): `was` moved toward `fresh` along their premultiplied crossfade, by at most `step` (per row,
    or one value) in any premultiplied channel or in coverage; `fresh` itself where it lies within that."""
    was_a, fresh_a = was[:, 3] / 255.0, fresh[:, 3] / 255.0
    gap = np.abs(_premultiplied(fresh[:, :3], fresh_a) - _premultiplied(was[:, :3], was_a)).max(axis=1)
    lam = np.minimum(1.0, np.asarray(step, dtype=np.float64) / np.maximum(gap, 1e-9))
    alpha = was_a + lam * (fresh_a - was_a)
    paint = was[:, :3] * was_a[:, None] + lam[:, None] * (fresh[:, :3] * fresh_a[:, None] - was[:, :3] * was_a[:, None])
    rgb = np.where(alpha[:, None] > 0, paint / np.maximum(alpha, 1e-9)[:, None], fresh[:, :3])
    moved = np.concatenate([rgb, 255.0 * alpha[:, None]], axis=1)
    return np.where((lam >= 1.0)[:, None], fresh, moved)


def _last_frame(history: dict[str, Any] | None, shape: tuple[int, ...], palette: np.ndarray) -> dict[str, Any] | None:
    """The previous frame's record in a clip's `history`, or None where it cannot speak for this frame: no record
    yet, a frame of another size, or another palette (picks are indices into it)."""
    if not history or history["shape"] != shape or not np.array_equal(history["palette"], palette):
        return None
    return history


def _blend_calls(last: dict[str, Any] | None, rows: np.ndarray, cols: np.ndarray, motion: np.ndarray, *,
                 written: np.ndarray, refits: np.ndarray, picks: np.ndarray, matte: np.ndarray, fit_alpha: np.ndarray,
                 colour: np.ndarray, palette_rgb: np.ndarray, obs: np.ndarray, bg: np.ndarray
                 ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """*Blend*: what each examined pixel (`rows`, `cols`) shows, between the matte's rendering and the fit's.

    Both come from this frame's evidence: K is the matte (`matte`, RGBA rows, the key hue capped as a kept pixel's
    is), W the fit (`colour`, the palette colour at the pixel's light, with the fit's coverage `fit_alpha` where the
    call refits coverage and the matte's elsewhere). While the palette pick changes, W is the two picks' renderings
    mixed by mu; a new pick waits until the change in hand is done, and going back to the pick it came from turns the
    change around. The weights (colour: written; coverage: written with coverage refitted; mu: 1) move toward this
    frame's call by at most VIDEO_BLEND a frame, and less where that would move the output by more than
    VIDEO_STEP + VIDEO_STEP_MOTION * `motion` (premultiplied, any channel or coverage). A pixel new to the band starts
    at its call when it was transparent last frame, otherwise from the matte. Returns the RGBA rows shown (float)
    and the state: w_col, w_cov, pick_from, pick_to, mu, settled (at this frame's call), moving (weights moved)."""
    t_col = written.astype(np.float64)
    t_cov = (written & refits).astype(np.float64)
    now = picks.astype(np.int64)
    if last is None:
        w_col0, w_cov0, to0, from0, mu0 = t_col, t_cov, now, now, np.ones(len(rows))
    else:
        seen = last["examined"][rows, cols]
        at_call = ~seen & (last["out"][rows, cols, 3] == 0)  # nothing was shown there to move from
        w_col0 = np.where(seen, last["w_col"][rows, cols], np.where(at_call, t_col, 0.0))
        w_cov0 = np.where(seen, last["w_cov"][rows, cols], np.where(at_call, t_cov, 0.0))
        to0 = np.where(seen, last["pick_to"][rows, cols], now).astype(np.int64)
        from0 = np.where(seen, last["pick_from"][rows, cols], now).astype(np.int64)
        mu0 = np.where(seen, last["mu"][rows, cols], 1.0)
    back = (now == from0) & (from0 != to0) & (mu0 < 1)
    new = (now != to0) & ~back & (mu0 >= 1)
    pick_to = np.where(back, from0, np.where(new, now, to0))
    mu_start = np.where(back, 1.0 - mu0, np.where(new, 0.0, mu0))
    pick_from = np.where(mu_start >= 1, pick_to, np.where(back | new, to0, from0))

    def render(picked, which, rgb_rows, alpha_rows):
        if which.any():
            chosen = palette_rgb[picked[which]]
            alpha_rows[which] = _fit_alpha(obs[which] - bg[which], chosen - bg[which], "video")
            rgb_rows[which] = _fit_colour(obs[which], bg[which], chosen, alpha_rows[which])

    to_rgb, to_a = colour.copy(), fit_alpha.copy()
    render(pick_to, pick_to != now, to_rgb, to_a)  # a new pick waiting its turn: the one in hand
    from_rgb, from_a = to_rgb.copy(), to_a.copy()
    render(pick_from, pick_from != pick_to, from_rgb, from_a)
    matte_rgb, matte_a = matte[:, :3], matte[:, 3] / 255.0

    def shown(w_col, w_cov, mu):
        fit_rgb = (1 - mu)[:, None] * from_rgb + mu[:, None] * to_rgb
        fit_a = (1 - mu) * from_a + mu * to_a
        a = np.where(w_cov >= 1, fit_a, matte_a + w_cov * (fit_a - matte_a))
        rgb = np.where(w_col[:, None] >= 1, fit_rgb, matte_rgb + w_col[:, None] * (fit_rgb - matte_rgb))
        return rgb, a

    full_col = w_col0 + np.clip(t_col - w_col0, -VIDEO_BLEND, VIDEO_BLEND)
    full_cov = w_cov0 + np.clip(t_cov - w_cov0, -VIDEO_BLEND, VIDEO_BLEND)
    full_mu = np.minimum(1.0, mu_start + VIDEO_BLEND)
    change = np.abs(_premultiplied(*shown(full_col, full_cov, full_mu))
                    - _premultiplied(*shown(w_col0, w_cov0, mu_start))).max(axis=1)
    lam = np.minimum(1.0, (VIDEO_STEP + VIDEO_STEP_MOTION * motion) / np.maximum(change, 1e-9))
    w_col = w_col0 + lam * (full_col - w_col0)
    w_col = np.where(np.abs(w_col - t_col) < 1e-9, t_col, w_col)
    w_cov = w_cov0 + lam * (full_cov - w_cov0)
    w_cov = np.where(np.abs(w_cov - t_cov) < 1e-9, t_cov, w_cov)
    mu = mu_start + lam * (full_mu - mu_start)
    mu = np.where(mu > 1 - 1e-9, 1.0, mu)
    rgb, a = shown(w_col, w_cov, mu)
    state = {"w_col": w_col, "w_cov": w_cov, "pick_from": pick_from, "pick_to": pick_to, "mu": mu,
             "settled": (w_col == t_col) & (w_cov == t_cov) & (mu >= 1) & (pick_to == now),
             "moving": (w_col != w_col0) | (w_cov != w_cov0) | (mu != mu0) | (pick_to != to0)}
    return np.concatenate([rgb, 255.0 * a[:, None]], axis=1), state


def _interior(keyed_mask: np.ndarray, not_subject: np.ndarray | None) -> np.ndarray:
    """The subject's interior: deeper than `BAND_PX` inside the keyed silhouette, `not_subject` left out."""
    interior = ~keyed_mask & (_ring_depth(keyed_mask, BAND_PX) > BAND_PX)
    return interior if not_subject is None else interior & ~not_subject


def not_applicable(keyed: np.ndarray, keyed_mask: np.ndarray, chroma_key: tuple[int, int, int],
                   not_subject: np.ndarray | None = None) -> str | None:
    """Why the pass cannot run on this image, or None when it can."""
    keyed_channels, _ = _key_channel_split(chroma_key)
    if not keyed_channels:
        return (f"key {tuple(chroma_key)} has no key hue (needs saturated and dark channels); "
                "edge decontamination only applies to chroma keys such as green or magenta")
    interior = _interior(keyed_mask, not_subject)
    if not np.count_nonzero(interior & (keyed[..., 3] == 255)):
        return (f"no subject pixel lies deeper than {BAND_PX} px inside the keyed silhouette, "
                "so there is no interior to learn the subject's colours from")
    return None


def subject_palette(keyed: np.ndarray, keyed_mask: np.ndarray, chroma_key: tuple[int, int, int],
                    not_subject: np.ndarray | None = None) -> dict[str, Any]:
    """The subject's colours, learned from its confident interior (opaque, deeper than `BAND_PX`), the pixels
    `not_subject` marks left out (a prop plate standing as opaque as the subject: `video-frames --prop-plate`).

    Returns {"palette": (k, 3) float64, "keyfree": bool, "key_material_share": float,
    "material_spread": float}, the last being how far the interior itself sits from the
    palette (`material_distance`, its `MATERIAL_SPREAD_QUANTILE`). A clip can learn this once
    and hand it to every frame, so the colours an edge may take do not change from frame to
    frame. Raises SystemExit when `not_applicable` says why it cannot.
    """
    reason = not_applicable(keyed, keyed_mask, chroma_key, not_subject)
    if reason is not None:
        raise SystemExit(f"decontam: {reason}; run without decontam")
    keyed_channels, unkeyed_channels = _key_channel_split(chroma_key)
    confident = _interior(keyed_mask, not_subject) & (keyed[..., 3] == 255)
    confident_count = int(np.count_nonzero(confident))
    excess = _key_excess_field(keyed[..., :3].astype(np.int32), keyed_channels, unkeyed_channels)
    key_material = float(np.count_nonzero(confident & (excess > KEY_HUE_TINT))) / confident_count
    keyfree = key_material <= KEY_MATERIAL_MAX_SHARE
    samples = keyed[..., :3][confident & (excess <= KEY_HUE_TINT)] if keyfree else keyed[..., :3][confident]
    # whole RGB values: a palette is a set of colours, and integers survive a JSON report exactly,
    # so a clip can hand frame 1's palette to frame 97 through its report with no drift
    palette = np.round(learn_palette(samples))
    if keyfree:
        palette = _cap_key_hue(palette, keyed_channels, unkeyed_channels, KEY_HUE_TINT)
    measured = samples[::max(1, len(samples) // MATERIAL_SPREAD_SAMPLES)]
    spread = round(float(np.percentile(material_distance(measured, palette), MATERIAL_SPREAD_QUANTILE)), 3)
    return {"palette": palette, "keyfree": keyfree, "key_material_share": key_material, "material_spread": spread}


def palette_from_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """The `subject_palette` a `decontaminate` call used, rebuilt from its stats."""
    return {"palette": np.asarray(stats["palette"], dtype=np.float64), "keyfree": bool(stats["palette_keyfree"]),
            "key_material_share": float(stats["key_material_share"]),
            "material_spread": float(stats["material_spread"])}


def decontaminate(source_rgb: np.ndarray, keyed: np.ndarray, keyed_mask: np.ndarray,
                  chroma_key: tuple[int, int, int], *, fit: str = "still", alpha_depth: int = 4,
                  palette: dict[str, Any] | None = None, mode: str = PALETTE,
                  source_alpha: np.ndarray | None = None, edge_band: int = 0,
                  history: dict[str, Any] | None = None,
                  not_subject: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Re-explain the edge of an already keyed image. Returns (RGBA uint8, stats).

    `source_rgb` (H, W, 3) is the frame as generated and `source_alpha` (H, W) its own
    coverage (None: opaque), `keyed` (H, W, 4) the engine's output for it and `keyed_mask`
    the engine's hard-cut background (transparent input included). Interior pixels
    (deeper than `BAND_PX`) are returned byte-identical.
    `palette` is a `subject_palette` result to reuse (a clip's); None learns this frame's, without the
    pixels `not_subject` marks (None: every pixel the matte kept is the subject's).
    `edge_band` (px) keeps what the matte left transparent along the top, left and right
    edges transparent (*Frame edge*); 0 leaves the frame edge to the pass like anywhere else.
    `history` (video fit) is a dict a clip hands to every frame in order: the pass reads the
    previous frame's record from it (*Hold*, *Blend*, *Settle*) and leaves this frame's; empty, it
    starts from this frame alone. The still fit ignores it.
    `mode="auto"` returns the input unchanged, with the reason in the stats, where the
    pass does not apply; `mode="palette"` raises SystemExit there instead.
    """
    validate(mode, fit)
    if mode == OFF:
        raise SystemExit("decontam: decontaminate() called with mode 'off'")
    if palette is None:
        reason = not_applicable(keyed, keyed_mask, chroma_key, not_subject)
        if reason is not None:
            if mode == PALETTE:
                raise SystemExit(f"decontam: {reason}; run without decontam")
            if history is not None:
                history.clear()  # a frame left as keyed: the next one has no last frame to hold to
            return np.array(keyed, dtype=np.uint8, copy=True), {"mode": mode, "applied": False, "reason": reason}
    keyed_channels, unkeyed_channels = _key_channel_split(chroma_key)
    rgb = np.asarray(source_rgb, dtype=np.float64)[..., :3]
    out = np.array(keyed, dtype=np.uint8, copy=True)
    subject = ~keyed_mask
    depth = _ring_depth(keyed_mask, BAND_PX)
    band = subject & (depth <= BAND_PX)
    flank = keyed_mask & (_ring_depth(subject, FLANK_PX) <= FLANK_PX)
    learned = subject_palette(out, keyed_mask, chroma_key, not_subject) if palette is None else palette
    palette_rgb = np.asarray(learned["palette"], dtype=np.float64)
    keyfree = bool(learned["keyfree"])
    key_material = float(learned["key_material_share"])
    spread = float(learned["material_spread"])
    # the still fit reads an edge pixel's colour as evidence of the subject's own material; a decoded
    # video frame's chroma is blurred, so there the colour is no such evidence and the fit decides alone
    material_test = fit == "still"
    # *Frame edge*: what the matte left transparent along the top, left and right edges stays so
    frame_edge = np.zeros(keyed_mask.shape, dtype=bool)
    if edge_band > 0:
        frame_edge[:edge_band] = frame_edge[:, :edge_band] = frame_edge[:, -edge_band:] = True
        frame_edge &= out[..., 3] == 0

    far_background = keyed_mask & ~flank
    background = local_background(rgb, far_background, keyed_mask, BACKGROUND_RADIUS)
    if np.count_nonzero(far_background):
        residual = rgb[far_background] - background[far_background]
        sigma_luma = 1.4826 * float(np.median(np.abs(residual @ LUMA)))
        sigma_rgb = 1.4826 * float(np.median(np.linalg.norm(residual, axis=-1)))
    else:
        sigma_luma = sigma_rgb = 0.0
    margin = max(OPAQUE_MARGIN, NOISE_SIGMAS * sigma_rgb, spread if material_test else 0.0)

    # the video fit gives no coverage back to the keyed flank (*Background flank*): it examines the band only
    rows, cols = np.nonzero(band if fit == "video" else band | flank)
    last = _last_frame(history, keyed_mask.shape, palette_rgb) if fit == "video" else None
    edge = _IN_BAND_UNMIX_KEY_DEPTH  # a still's antialiased edge: the engine's in-band unmix depth
    if material_test:
        # the subject's plain material right behind the edge: deeper than it and without the key's hue
        source_int = np.asarray(source_rgb)[..., :3].astype(np.int32)
        behind = subject & (depth > edge) & (_key_excess_field(source_int, keyed_channels, unkeyed_channels) <= 0)
        reach = edge
    else:
        # *Material behind*: a decoded frame's edge carries the key's blurred hue, so no hue test
        behind = subject & (depth > edge)
        reach = VIDEO_BEHIND_PX
    behind_weight = _box_sum_at(behind.astype(np.float64), reach, rows, cols)
    behind_mean = np.stack([_box_sum_at(rgb[..., channel] * behind, reach, rows, cols) for channel in range(3)],
                           axis=-1) / np.maximum(behind_weight, 1.0)[:, None]
    guarded = np.zeros(len(rows), dtype=bool)
    if fit == "video" and keyfree:
        # *Key-coloured parts*: the palette left them out, so no colour of it explains their edge
        excess = _key_excess_field(out[..., :3].astype(np.int32), keyed_channels, unkeyed_channels)
        part = subject & (depth > BAND_PX) & (out[..., 3] == 255) & (excess > KEY_HUE_TINT)
        if part.any():
            guarded = (_ring_depth(part, KEY_PART_GUARD_PX) <= KEY_PART_GUARD_PX)[rows, cols]
    alpha_new = np.zeros(len(rows))
    fit_alpha = np.zeros(len(rows))  # the fit's own coverage, refitted or not (*Blend* mixes it with the matte's)
    refits = np.zeros(len(rows), dtype=bool)
    colour_new = np.zeros((len(rows), 3))
    blend = np.zeros(len(rows), dtype=bool)
    tinted = np.zeros(len(rows), dtype=bool)
    unexplained = np.zeros(len(rows), dtype=bool)
    own = np.zeros(len(rows), dtype=bool)
    behind_kept = np.zeros(len(rows), dtype=bool)
    picks = np.zeros(len(rows), dtype=np.uint8)
    tests = np.zeros(len(rows), dtype=np.uint8)  # this frame's record for the next (*Hold*)
    held_calls = np.zeros(len(rows), dtype=bool)
    for start in range(0, len(rows), _CHUNK):
        r = rows[start:start + _CHUNK]
        c = cols[start:start + _CHUNK]
        obs = rgb[r, c]
        bg = background[r, c]
        if fit == "video":
            choose_obs = _box3(rgb, r, c)
            choose_bg = _box3(background, r, c)
        else:
            choose_obs, choose_bg = obs, bg
        towards = choose_obs - choose_bg
        best = np.full(len(r), np.inf)
        pick = np.zeros(len(r), dtype=np.int64)
        for index, colour in enumerate(palette_rgb):
            miss = _line_miss(towards, colour - choose_bg)
            closer = miss < best
            best[closer] = miss[closer]
            pick[closer] = index
        before = np.zeros(len(r), dtype=np.uint8) if last is None else last["tests"][r, c]
        held_here = np.zeros(len(r), dtype=bool)
        if last is not None:
            # *Hold*: last frame's colour stays unless another one's line passes closer by VIDEO_HOLD
            kept = last["pick"][r, c].astype(np.int64)
            seen = (before & _SEEN) != 0
            kept_miss = _line_miss(towards, palette_rgb[np.where(seen, kept, 0)] - choose_bg)
            stay = seen & (kept_miss <= best + VIDEO_HOLD)
            held_here |= stay & (kept != pick)
            pick = np.where(stay, kept, pick)
        near = np.min([np.linalg.norm(obs - colour, axis=-1) for colour in palette_rgb], axis=0)
        if material_test:
            # the subject's own colour at its own light, as closely as its interior sits to the palette
            lit = material_distance(obs, palette_rgb)
            own_colour = lit <= spread
            material = np.where(own_colour, lit, near)
        else:
            own_colour = np.zeros(len(r), dtype=bool)
            material = near
        chosen = palette_rgb[pick]
        line = chosen - bg
        offset = obs - bg
        alpha = _fit_alpha(offset, line, fit)
        residual = offset - alpha[:, None] * line
        miss = np.linalg.norm(residual, axis=-1)
        fits, held = _hold(miss, UNEXPLAINED_RATIO * np.linalg.norm(offset, axis=-1) + UNEXPLAINED_SLACK, before,
                           _EXPLAINED)
        held_here |= held
        explained = fits
        if fit == "still":
            # a blend's luma lies between its colour's and the key's; a still keeps luma exact, so a
            # pixel darker than the line allows (outline ink the interior never shows) is not a blend
            explained &= np.abs(residual @ LUMA) <= LUMA_MISS_SLACK + NOISE_SIGMAS * sigma_luma
        in_flank = flank[r, c]
        mean = behind_mean[start:start + len(r)]
        behind_distance = np.minimum(np.linalg.norm(obs - mean, axis=-1),
                                     np.linalg.norm(obs - recolor_to_luma(mean, obs @ LUMA), axis=-1))
        like_behind, held = _hold(behind_distance, margin, before, _BEHIND)
        held_here |= held
        behind_near = (behind_weight[start:start + len(r)] > 0) & like_behind
        if material_test:
            # Deeper than a still's antialiased edge, a key share shows only as the key's hue: a pixel
            # without it is the subject's own colour. On the edge, so is a pixel without it that has
            # the colour (at its own luma) of the plain material right behind it.
            hueless = ~in_flank & (_key_excess_field(obs, keyed_channels, unkeyed_channels) <= 0)
            deep = depth[r, c] > edge
            settled = hueless & (deep | behind_near)
        else:
            settled = ~in_flank & behind_near
        kept_as_is = settled | guarded[start:start + len(r)] | frame_edge[r, c]
        closer_as_blend, held = _hold(miss + margin, material, before, _BLEND, strict=True)
        held_here |= held
        is_blend = explained & (alpha < 1.0) & (in_flank | closer_as_blend) & ~kept_as_is
        # a tint: displaced from its colour toward the key clearly more than the fit misses by, but
        # not by enough to prove partial coverage, and by more than shading any palette colour
        # accounts for. Recoloured, coverage untouched.
        shift = (1.0 - alpha) * np.sqrt((line * line).sum(-1))
        moved_far, held = _hold(TINT_SHIFT, shift, before, _TINT_SHIFT)
        held_here |= held
        moved_more, held = _hold(TINT_RATIO * miss, shift, before, _TINT_RATIO)
        held_here |= held
        is_tint = explained & ~is_blend & ~in_flank & moved_far & moved_more & ~own_colour & ~kept_as_is
        # coverage: refit within the unmix reach and on the flank; colour only deeper in the band
        refit = (in_flank | (depth[r, c] <= alpha_depth)) & ~is_tint
        floor = np.maximum(MIN_RECOVERED_ALPHA, NOISE_SIGMAS * sigma_luma / np.maximum(np.abs((chosen - bg) @ LUMA), 1e-6))
        alpha = np.where(in_flank & (alpha < floor), 0.0, alpha)
        coverage = np.where(refit, alpha, out[r, c, 3] / 255.0)
        alpha_new[start:start + len(r)] = coverage
        fit_alpha[start:start + len(r)] = alpha
        refits[start:start + len(r)] = refit
        colour_new[start:start + len(r)] = _fit_colour(obs, bg, chosen, alpha)
        blend[start:start + len(r)] = is_blend | is_tint
        tinted[start:start + len(r)] = is_tint
        unexplained[start:start + len(r)] = ~explained
        if not material_test:
            behind_kept[start:start + len(r)] = settled
        own[start:start + len(r)] = (material_test & ~is_blend & ~is_tint & ~in_flank & ~frame_edge[r, c]
                                     & ((material <= margin) | settled))
        picks[start:start + len(r)] = pick
        tests[start:start + len(r)] = (_SEEN | np.where(fits, _EXPLAINED, 0) | np.where(like_behind, _BEHIND, 0)
                                       | np.where(closer_as_blend, _BLEND, 0) | np.where(moved_far, _TINT_SHIFT, 0)
                                       | np.where(moved_more, _TINT_RATIO, 0))
        held_calls[start:start + len(r)] = held_here

    alpha8 = np.clip(np.round(alpha_new * 255), 0, 255).astype(np.uint8)
    colour8 = np.clip(np.round(colour_new), 0, 255).astype(np.uint8)
    br, bc = rows[blend], cols[blend]
    out[br, bc, :3] = colour8[blend]
    out[br, bc, 3] = alpha8[blend]
    # the subject's own material, with no key hue, that the matte changed anyway (its channel-average
    # tint calls yellow green): the source pixel and its coverage come back
    source8 = np.asarray(source_rgb)[rows, cols, :3].astype(np.int32)
    source_a = np.full(len(rows), 255, dtype=np.int32) if source_alpha is None else np.asarray(source_alpha)[rows, cols].astype(np.int32)
    back = (own & (_key_excess_field(source8, keyed_channels, unkeyed_channels) <= 0)
            & ((out[rows, cols, :3] != source8).any(-1) | (out[rows, cols, 3] != source_a)))
    out[rows[back], cols[back], :3] = source8[back].astype(np.uint8)
    out[rows[back], cols[back], 3] = source_a[back].astype(np.uint8)
    restored = int(np.count_nonzero(back))
    clamped = 0
    if keyfree:
        kept = ~blend & ~guarded & band[rows, cols] & (out[rows, cols, 3] > 0)
        kr, kc = rows[kept], cols[kept]
        kept_rgb = out[kr, kc, :3].astype(np.int32)
        hued = _key_excess_field(kept_rgb, keyed_channels, unkeyed_channels) > KEY_HUE_TINT
        capped = _cap_key_hue(kept_rgb[hued], keyed_channels, unkeyed_channels, KEY_HUE_TINT)
        out[kr[hued], kc[hued], :3] = np.clip(np.round(capped), 0, 255).astype(np.uint8)
        clamped = int(np.count_nonzero(hued))
    blended: dict[str, np.ndarray] = {}
    if fit == "video" and history is not None:
        # *Blend*: each examined pixel shows the matte's rendering and the fit's mixed, the mix moving toward this
        # frame's call a step a frame; the matte as a kept pixel shows it, its key hue capped
        source_now = np.asarray(source_rgb)[..., :3].astype(np.uint8)
        motion = (np.zeros(keyed_mask.shape, dtype=np.int16) if last is None else
                  _box_max3(np.abs(source_now.astype(np.int16) - last["source"].astype(np.int16)).max(-1)))
        matte = np.asarray(keyed)[rows, cols].astype(np.float64)
        if keyfree:
            hued = ~guarded & (matte[:, 3] > 0) & (_key_excess_field(matte[:, :3].astype(np.int32), keyed_channels,
                                                                     unkeyed_channels) > KEY_HUE_TINT)
            matte[hued, :3] = np.clip(np.round(_cap_key_hue(matte[hued, :3].astype(np.int32), keyed_channels,
                                                            unkeyed_channels, KEY_HUE_TINT)), 0, 255)
        shown, blended = _blend_calls(last, rows, cols, motion[rows, cols].astype(np.float64), written=blend,
                                      refits=refits, picks=picks, matte=matte, fit_alpha=fit_alpha, colour=colour_new,
                                      palette_rgb=palette_rgb, obs=rgb[rows, cols], bg=background[rows, cols])
        out[rows, cols] = np.clip(np.round(shown), 0, 255).astype(np.uint8)
    # recovered background coverage must chain to the subject
    visible = out[..., 3] > 0
    attached = _grow_into(subject & visible, visible)
    detached = flank & visible & ~attached
    out[detached] = 0
    out[out[..., 3] == 0] = 0
    settled_px = 0
    if fit == "video" and history is not None:
        def as_map(values: np.ndarray, fill: Any, dtype: Any) -> np.ndarray:
            plane = np.full(keyed_mask.shape, fill, dtype=dtype)
            plane[rows, cols] = values
            return plane
        examined = as_map(True, False, bool)
        written = as_map(blend, False, bool)
        pick_map = as_map(picks, 255, np.uint8)
        mix = {"w_col": as_map(blended["w_col"], 0.0, np.float32), "w_cov": as_map(blended["w_cov"], 0.0, np.float32),
               "pick_from": as_map(blended["pick_from"], 255, np.uint8),
               "pick_to": as_map(blended["pick_to"], 255, np.uint8),
               "mu": as_map(blended["mu"], 1.0, np.float32), "settled": as_map(blended["settled"], False, bool)}
        unsmoothed = out.copy()  # what this frame shows before the steps below
        if last is not None:
            still = motion <= VIDEO_STILL
            # *Settle*: a pixel written in this frame and the last in the same colour, both at their call, whose
            # source stood still, moves VIDEO_SETTLE of the way from what it was, never farther than
            # VIDEO_SETTLE_REACH from this fit, and catches up past that by at most VIDEO_STEP a frame
            calm = (written & last["written"] & (pick_map == last["pick"]) & still & (out[..., 3] > 0)
                    & mix["settled"] & last["settled"])
            fresh = out[calm].astype(np.float64)
            was = last["out"][calm].astype(np.float64)
            eased = np.clip(was + VIDEO_SETTLE * (fresh - was), fresh - VIDEO_SETTLE_REACH, fresh + VIDEO_SETTLE_REACH)
            out[calm] = np.clip(np.round(_toward(was, eased, VIDEO_STEP)), 0, 255).astype(np.uint8)
            # a still pixel whose mix moved, that is still on its way to its call, or that came into or left the band
            # moves at most VIDEO_STEP a frame from what it showed; what the hard cut keyed stays transparent
            stepped = (still & (examined | last["examined"]) & ~calm & ~keyed_mask
                       & (as_map(blended["moving"], False, bool) | last["on_way"] | (examined != last["examined"])))
            stepped_out = _toward(last["out"][stepped].astype(np.float64), out[stepped].astype(np.float64), VIDEO_STEP)
            out[stepped] = np.clip(np.round(stepped_out), 0, 255).astype(np.uint8)
            out[out[..., 3] == 0] = 0
            settled_px = int(np.count_nonzero(calm | stepped))
        tests_map = as_map(tests, 0, np.uint8)
        history.clear()
        history.update(shape=keyed_mask.shape, palette=palette_rgb.copy(), tests=tests_map, pick=pick_map,
                       written=written, out=out.copy(), source=source_now, on_way=(out != unsmoothed).any(-1),
                       examined=examined, **mix)
    recovered = int(np.count_nonzero(flank & (out[..., 3] > 0)))
    changed = int(np.count_nonzero((out != np.asarray(keyed, dtype=np.uint8)).any(axis=-1)))
    stats = {
        "mode": mode,
        "applied": True,
        "fit": fit,
        "band_px": BAND_PX,
        "alpha_depth": int(alpha_depth),
        "flank_px": FLANK_PX,
        "edge_band": int(edge_band),
        "palette_size": int(len(palette_rgb)),
        "palette_source": "frame" if palette is None else "given",
        "palette": [[int(v) for v in colour] for colour in palette_rgb],
        "palette_keyfree": bool(keyfree),
        "key_material_share": round(key_material, 5),
        "noise_sigma_rgb": round(sigma_rgb, 3),
        "material_spread": round(spread, 3),
        "opaque_margin": round(margin, 3),
        "changed_px": changed,
        "refit_px": int(np.count_nonzero(blend & ~tinted)),
        "tint_px": int(np.count_nonzero(tinted)),
        "recovered_px": recovered,
        "unexplained_px": int(np.count_nonzero(unexplained & band[rows, cols])),
        "restored_px": restored,
        "key_hue_capped_px": clamped,
    }
    if fit == "video":
        stats["key_part_kept_px"] = int(np.count_nonzero(guarded))
        stats["behind_kept_px"] = int(np.count_nonzero(behind_kept))
        stats["held_px"] = int(np.count_nonzero(held_calls))
        stats["settled_px"] = settled_px
        stats["blending_px"] = int(np.count_nonzero(~blended["settled"])) if blended else 0
    return out, stats
