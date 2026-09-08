# SPDX-License-Identifier: Apache-2.0
"""FFT template registration for parts — every offset of one scale in a handful of FFTs.

For a candidate part `t` (RGB) with mask `m` (alpha), a base window `b`, a `free`
mask (pixels not yet claimed by higher parts) and an owned `region` (base pixels of
the part's own colours it is expected to cover), the per-offset cost is

    cost(u,v) = [ SSD(u,v)/(3*255^2) - REWARD * W(u,v) + MISS_WEIGHT * miss(u,v) ] / area

where SSD = sum m·free·(t-b)^2 over channels, W = sum m·free (matched pixel mass),
miss = sum region - corr(region, m) (owned pixels left uncovered). Each term is a
cross-correlation, so one scale costs four rfft2 round-trips regardless of how many
offsets are evaluated. Deterministic: same inputs, same argmin (ties → first).
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from sprite_gen._deps import np

REWARD = 0.03        # per matched pixel: an exact match contributes -REWARD, so covering is rewarded
MISS_WEIGHT = 0.06   # per owned pixel left uncovered


def _corr(f: np.ndarray, g: np.ndarray) -> np.ndarray:
    """corr(f, g)[u, v] = sum_xy f[u+x, v+y] * g[x, y] for u in [0, Hf-Hg], v in [0, Wf-Wg]."""
    Hf, Wf = f.shape
    Hg, Wg = g.shape
    F = np.fft.rfft2(f, s=(Hf, Wf))
    G = np.fft.rfft2(g, s=(Hf, Wf))
    full = np.fft.irfft2(F * np.conj(G), s=(Hf, Wf))
    return full[: Hf - Hg + 1, : Wf - Wg + 1]


def cost_map(part_rgb: np.ndarray, part_alpha: np.ndarray, base_rgb: np.ndarray, base_alpha: np.ndarray,
             free: np.ndarray, region: np.ndarray, area: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (cost, ssd_norm, W) maps over every offset where the part fits inside the window."""
    m = part_alpha.astype(np.float64) / 255.0
    ssd = np.zeros((base_rgb.shape[0] - m.shape[0] + 1, base_rgb.shape[1] - m.shape[1] + 1))
    W = _corr(free.astype(np.float64), m)
    # a part pixel over transparent base is a disagreement: fold base alpha into "free" for the colour terms
    vis = free.astype(np.float64) * (base_alpha.astype(np.float64) / 255.0)
    Wvis = _corr(vis, m)
    for c in range(3):
        t = part_rgb[..., c].astype(np.float64)
        b = base_rgb[..., c].astype(np.float64)
        ssd += _corr(vis, m * t * t) - 2.0 * _corr(vis * b, m * t) + _corr(vis * b * b, m)
    # pixels placed over empty base count as fully wrong
    ssd += (W - Wvis) * 3.0 * 255.0 * 255.0
    ssd_norm = ssd / (3.0 * 255.0 * 255.0)
    miss = float(region.sum()) - _corr(region.astype(np.float64), m)
    cost = (ssd_norm - REWARD * W + MISS_WEIGHT * miss) / max(area, 1.0)
    return cost, ssd_norm, W


def best_offset(cost: np.ndarray) -> tuple[int, int, float]:
    idx = int(np.argmin(cost))
    u, v = divmod(idx, cost.shape[1])
    return u, v, float(cost[u, v])
