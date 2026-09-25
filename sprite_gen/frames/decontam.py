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

- *Background flank.* Keyed pixels within `FLANK_PX` of the subject may regain
  coverage (the faint sides of a strand the hard cut erased), above a noise
  floor measured on the background itself, and only when chained to the subject.
- *Unexplained.* A pixel no palette colour explains (a small gem of a colour the
  interior never shows) keeps the engine's bytes. In the still fit the blend must
  also reproduce the observed luma, so outline ink darker than any mix of a
  palette colour with the key stays as the engine left it.
- *Opaque.* A band pixel that is closer to some palette colour as it is than the
  mix explains it (by `OPAQUE_MARGIN`, or three noise sigmas when the source is
  noisier) is ordinary shading, not a blend: it keeps the engine's bytes. Only
  when the subject owns no key-coloured material is a residual key hue on such a
  pixel capped (`KEY_HUE_TINT`), which lowers the keyed channel and never raises
  another.
- *Tint.* Between the two: a pixel pushed from its colour toward the key by at
  least `TINT_SHIFT`, and `TINT_RATIO` times more than the fit misses by, is
  recoloured with its coverage left alone (a faint cast, not proven coverage).
- *Depth.* Coverage is refitted only within `alpha_depth` of the keyed region
  (the engine's unmix reach, same meaning). Deeper band pixels get colour only,
  so a key reflection on a solid surface is recoloured, not turned see-through.

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
"""

from __future__ import annotations

from typing import Any

from sprite_gen._deps import np
from sprite_gen.frames.extract import _grow_chebyshev, _grow_into, _key_channel_split, _key_excess_field

# "palette" runs the pass and fails loud when it cannot; "auto" runs it wherever it applies (a
# chroma key, a subject interior to learn from) and otherwise reports why it did not.
DECONTAM_MODES = ("off", "auto", "palette")
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
NOISE_SIGMAS = 3.0
MIN_RECOVERED_ALPHA = 0.06
LUMA_TRUST_ALPHA = 0.35  # below this coverage the observed luma is noise; lean on the palette
UNEXPLAINED_RATIO = 0.5
UNEXPLAINED_SLACK = 8.0
LUMA_SEPARATION = 24.0  # video fit: luma alpha only where P and B differ this much in luma
LUMA_MISS_SLACK = 12.0  # still fit: a blend must reproduce the observed luma this closely (plus 3 sigma)
TINT_SHIFT = 8.0  # a tint moves the colour at least this far toward the key ...
TINT_RATIO = 2.0  # ... and at least this many times farther than the fit misses by
LUMA = np.array([0.299, 0.587, 0.114])
_CHUNK = 1 << 16


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
    """Lower every keyed channel to at most (max unkeyed channel + tint). Never raises a channel."""
    out = np.array(rgb, dtype=np.float64, copy=True)
    ceiling = out[..., unkeyed_channels].max(axis=-1) + tint
    for channel in keyed_channels:
        out[..., channel] = np.minimum(out[..., channel], ceiling)
    return out


def recolor_to_luma(colour: np.ndarray, luma: np.ndarray) -> np.ndarray:
    """`colour` moved to `luma`: darkened by scaling, brightened toward white. Hue is kept either way."""
    own = colour @ LUMA
    target = np.clip(luma, 0, 255)
    darker = target <= own
    scale = np.where(darker, target / np.maximum(own, 1e-6), 0.0)
    lift = np.where(darker, 0.0, (target - own) / np.maximum(255 - own, 1e-6))
    return np.where(darker[:, None], colour * scale[:, None], colour + lift[:, None] * (255 - colour))


def _box3(rgb: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """3x3 mean (edge-clamped) of `rgb` at the given pixels."""
    height, width = rgb.shape[:2]
    acc = np.zeros((len(rows), 3), dtype=np.float64)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            acc += rgb[np.clip(rows + dy, 0, height - 1), np.clip(cols + dx, 0, width - 1)]
    return acc / 9.0


def not_applicable(keyed: np.ndarray, keyed_mask: np.ndarray, chroma_key: tuple[int, int, int]) -> str | None:
    """Why the pass cannot run on this image, or None when it can."""
    keyed_channels, _ = _key_channel_split(chroma_key)
    if not keyed_channels:
        return (f"key {tuple(chroma_key)} has no key hue (needs saturated and dark channels); "
                "edge decontamination only applies to chroma keys such as green or magenta")
    interior = ~keyed_mask & (_ring_depth(keyed_mask, BAND_PX) > BAND_PX)
    if not np.count_nonzero(interior & (keyed[..., 3] == 255)):
        return (f"no subject pixel lies deeper than {BAND_PX} px inside the keyed silhouette, "
                "so there is no interior to learn the subject's colours from")
    return None


def subject_palette(keyed: np.ndarray, keyed_mask: np.ndarray, chroma_key: tuple[int, int, int]) -> dict[str, Any]:
    """The subject's colours, learned from its confident interior (opaque, deeper than `BAND_PX`).

    Returns {"palette": (k, 3) float64, "keyfree": bool, "key_material_share": float}. A clip
    can learn this once and hand it to every frame, so the colours an edge may take do not
    change from frame to frame. Raises SystemExit when `not_applicable` says why it cannot.
    """
    reason = not_applicable(keyed, keyed_mask, chroma_key)
    if reason is not None:
        raise SystemExit(f"decontam: {reason}; run without decontam")
    keyed_channels, unkeyed_channels = _key_channel_split(chroma_key)
    subject = ~keyed_mask
    interior = subject & (_ring_depth(keyed_mask, BAND_PX) > BAND_PX)
    confident = interior & (keyed[..., 3] == 255)
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
    return {"palette": palette, "keyfree": keyfree, "key_material_share": key_material}


def palette_from_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """The `subject_palette` a `decontaminate` call used, rebuilt from its stats."""
    return {"palette": np.asarray(stats["palette"], dtype=np.float64), "keyfree": bool(stats["palette_keyfree"]),
            "key_material_share": float(stats["key_material_share"])}


def decontaminate(source_rgb: np.ndarray, keyed: np.ndarray, keyed_mask: np.ndarray,
                  chroma_key: tuple[int, int, int], *, fit: str = "still", alpha_depth: int = 4,
                  palette: dict[str, Any] | None = None, mode: str = "palette") -> tuple[np.ndarray, dict[str, Any]]:
    """Re-explain the edge of an already keyed image. Returns (RGBA uint8, stats).

    `source_rgb` (H, W, 3) is the frame as generated, `keyed` (H, W, 4) the engine's
    output for it and `keyed_mask` the engine's hard-cut background (transparent input
    included). Interior pixels (deeper than `BAND_PX`) are returned byte-identical.
    `palette` is a `subject_palette` result to reuse (a clip's); None learns this frame's.
    `mode="auto"` returns the input unchanged, with the reason in the stats, where the
    pass does not apply; `mode="palette"` raises SystemExit there instead.
    """
    validate(mode, fit)
    if mode == "off":
        raise SystemExit("decontam: decontaminate() called with mode 'off'")
    if palette is None:
        reason = not_applicable(keyed, keyed_mask, chroma_key)
        if reason is not None:
            if mode == "palette":
                raise SystemExit(f"decontam: {reason}; run without decontam")
            return np.array(keyed, dtype=np.uint8, copy=True), {"mode": mode, "applied": False, "reason": reason}
    keyed_channels, unkeyed_channels = _key_channel_split(chroma_key)
    rgb = np.asarray(source_rgb, dtype=np.float64)[..., :3]
    out = np.array(keyed, dtype=np.uint8, copy=True)
    subject = ~keyed_mask
    depth = _ring_depth(keyed_mask, BAND_PX)
    band = subject & (depth <= BAND_PX)
    flank = keyed_mask & (_ring_depth(subject, FLANK_PX) <= FLANK_PX)
    learned = subject_palette(out, keyed_mask, chroma_key) if palette is None else palette
    palette_rgb = np.asarray(learned["palette"], dtype=np.float64)
    keyfree = bool(learned["keyfree"])
    key_material = float(learned["key_material_share"])

    far_background = keyed_mask & ~flank
    background = local_background(rgb, far_background, keyed_mask, BACKGROUND_RADIUS)
    if np.count_nonzero(far_background):
        residual = rgb[far_background] - background[far_background]
        sigma_luma = 1.4826 * float(np.median(np.abs(residual @ LUMA)))
        sigma_rgb = 1.4826 * float(np.median(np.linalg.norm(residual, axis=-1)))
    else:
        sigma_luma = sigma_rgb = 0.0
    margin = max(OPAQUE_MARGIN, NOISE_SIGMAS * sigma_rgb)

    rows, cols = np.nonzero(band | flank)
    alpha_new = np.zeros(len(rows))
    colour_new = np.zeros((len(rows), 3))
    blend = np.zeros(len(rows), dtype=bool)
    tinted = np.zeros(len(rows), dtype=bool)
    unexplained = np.zeros(len(rows), dtype=bool)
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
        opaque_distance = np.full(len(r), np.inf)
        for index, colour in enumerate(palette_rgb):
            line = colour - choose_bg
            length = np.maximum((line * line).sum(-1), 1e-9)
            along = np.clip((towards * line).sum(-1) / length, 0.0, 1.0)
            miss = np.linalg.norm(towards - along[:, None] * line, axis=-1)
            closer = miss < best
            best[closer] = miss[closer]
            pick[closer] = index
            opaque_distance = np.minimum(opaque_distance, np.linalg.norm(obs - colour, axis=-1))
        chosen = palette_rgb[pick]
        line = chosen - bg
        offset = obs - bg
        alpha = np.clip((offset * line).sum(-1) / np.maximum((line * line).sum(-1), 1e-9), 0.0, 1.0)
        if fit == "video":
            luma_gap = (chosen - bg) @ LUMA
            separated = np.abs(luma_gap) >= LUMA_SEPARATION
            luma_alpha = np.clip((offset @ LUMA) / np.where(separated, luma_gap, 1.0), 0.0, 1.0)
            alpha = np.where(separated, 0.5 * (alpha + luma_alpha), alpha)
        residual = offset - alpha[:, None] * line
        miss = np.linalg.norm(residual, axis=-1)
        explained = miss <= UNEXPLAINED_RATIO * np.linalg.norm(offset, axis=-1) + UNEXPLAINED_SLACK
        if fit == "still":
            # a blend's luma lies between its colour's and the key's; a still keeps luma exact, so a
            # pixel darker than the line allows (outline ink the interior never shows) is not a blend
            explained &= np.abs(residual @ LUMA) <= LUMA_MISS_SLACK + NOISE_SIGMAS * sigma_luma
        in_flank = flank[r, c]
        is_blend = explained & (alpha < 1.0) & (in_flank | (miss + margin < opaque_distance))
        # a tint: displaced from its colour toward the key clearly more than the fit misses by, but
        # not by enough to prove partial coverage. Recoloured, coverage untouched.
        shift = (1.0 - alpha) * np.sqrt((line * line).sum(-1))
        is_tint = explained & ~is_blend & ~in_flank & (shift >= TINT_SHIFT) & (shift >= TINT_RATIO * miss)
        # coverage: refit within the unmix reach and on the flank; colour only deeper in the band
        refit = (in_flank | (depth[r, c] <= alpha_depth)) & ~is_tint
        floor = np.maximum(MIN_RECOVERED_ALPHA, NOISE_SIGMAS * sigma_luma / np.maximum(np.abs((chosen - bg) @ LUMA), 1e-6))
        alpha = np.where(in_flank & (alpha < floor), 0.0, alpha)
        coverage = np.where(refit, alpha, out[r, c, 3] / 255.0)
        # the colour's light: the observation with the fitted key share taken out, leaning on the
        # palette colour where coverage is too thin for the observed luma to mean much
        observed_luma = (obs @ LUMA - (1 - alpha) * (bg @ LUMA)) / np.maximum(alpha, 1e-3)
        trust = np.clip(alpha / LUMA_TRUST_ALPHA, 0.0, 1.0)
        target = trust * observed_luma + (1 - trust) * (chosen @ LUMA)
        alpha_new[start:start + len(r)] = coverage
        colour_new[start:start + len(r)] = recolor_to_luma(chosen, target)
        blend[start:start + len(r)] = is_blend | is_tint
        tinted[start:start + len(r)] = is_tint
        unexplained[start:start + len(r)] = ~explained

    alpha8 = np.clip(np.round(alpha_new * 255), 0, 255).astype(np.uint8)
    colour8 = np.clip(np.round(colour_new), 0, 255).astype(np.uint8)
    br, bc = rows[blend], cols[blend]
    out[br, bc, :3] = colour8[blend]
    out[br, bc, 3] = alpha8[blend]
    clamped = 0
    if keyfree:
        kept = ~blend & band[rows, cols] & (out[rows, cols, 3] > 0)
        kr, kc = rows[kept], cols[kept]
        kept_rgb = out[kr, kc, :3].astype(np.int32)
        hued = _key_excess_field(kept_rgb, keyed_channels, unkeyed_channels) > KEY_HUE_TINT
        capped = _cap_key_hue(kept_rgb[hued], keyed_channels, unkeyed_channels, KEY_HUE_TINT)
        out[kr[hued], kc[hued], :3] = np.clip(np.round(capped), 0, 255).astype(np.uint8)
        clamped = int(np.count_nonzero(hued))
    # recovered background coverage must chain to the subject
    visible = out[..., 3] > 0
    attached = _grow_into(subject & visible, visible)
    detached = flank & visible & ~attached
    out[detached] = 0
    out[out[..., 3] == 0] = 0
    recovered = int(np.count_nonzero(flank & (out[..., 3] > 0)))
    changed = int(np.count_nonzero((out != np.asarray(keyed, dtype=np.uint8)).any(axis=-1)))
    stats = {
        "mode": mode,
        "applied": True,
        "fit": fit,
        "band_px": BAND_PX,
        "alpha_depth": int(alpha_depth),
        "flank_px": FLANK_PX,
        "palette_size": int(len(palette_rgb)),
        "palette_source": "frame" if palette is None else "given",
        "palette": [[int(v) for v in colour] for colour in palette_rgb],
        "palette_keyfree": bool(keyfree),
        "key_material_share": round(key_material, 5),
        "noise_sigma_rgb": round(sigma_rgb, 3),
        "opaque_margin": round(margin, 3),
        "changed_px": changed,
        "refit_px": int(np.count_nonzero(blend & ~tinted)),
        "tint_px": int(np.count_nonzero(tinted)),
        "recovered_px": recovered,
        "unexplained_px": int(np.count_nonzero(unexplained & band[rows, cols])),
        "key_hue_capped_px": clamped,
    }
    return out, stats
