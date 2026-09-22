# SPDX-License-Identifier: Apache-2.0
"""Discover stable texture regions and remove drift for cycle ANALYSIS only.

No semantic body labels, character profiles, learned weights or external models.
Output correction uses one integer ramp, or explicitly abstains from an uncertain
fine match. The caller must still gate the rendered, uncorrected output.
"""
from __future__ import annotations

from PIL import Image
from sprite_gen._deps import np
from sprite_gen.video import motion_anchor

ANALYSIS_EDGE = 184
SAMPLE_COUNT = 9


def correct_cycle(frames, regions, *, coarse_dx):
    """Do not turn an uncertain fine match into a potentially damaging shift.

    Returning unchanged pixels is not a pass: the loop owner always runs the
    rendered-cell seam gate and animation verification before accepting output.
    Other registration/discovery errors retain their failure contract.
    """
    try:
        return motion_anchor.correct_motion(frames, regions, coarse_dx=coarse_dx)
    except motion_anchor.FineSearchBoundaryError as exc:
        return frames, {
            "method": "uncorrected-after-uncertain-fine-match-v1",
            "applied": False, "reason": "fine-match-at-search-boundary",
            "registration_error": str(exc), "rejected_measurement": exc.measurement,
            "requires_rendered_seam_gate": True,
            "regions": [list(box) for box in regions], "coarse_dx_px": coarse_dx,
            "endpoint_xy": [0, 0], "shifts_xy": [[0, 0] for _ in frames],
            "padding_ltrb": [0, 0, 0, 0],
        }


def _center(a):
    y, x = np.nonzero(a[:, :, 3] > .1)
    if not len(x):
        raise ValueError("automatic motion anchor needs visible foreground in every frame")
    return np.array([x.mean(), y.mean()])


def _reference(a, box):
    x, y, right, bottom = box
    patch = a[y:bottom, x:right]
    mask = patch[:, :, 3] > .05
    if mask.mean() < .45:
        return None
    z = np.pad(mask, 1)
    mask = np.logical_or.reduce([z[dy:dy+mask.shape[0], dx:dx+mask.shape[1]]
                                 for dx, dy in ((1, 1), (0, 1), (2, 1), (1, 0), (1, 2))])
    flat = patch[mask]
    centered = flat - flat.mean(axis=0)
    norm = float(np.sqrt((centered * centered).sum()))
    if norm < .5:
        return None
    return box, mask, centered, norm


def _match(reference, moving, center, radius):
    """Bounded masked NCC; vectorized across translations, never wrap pixels."""
    (x, y, right, bottom), mask, feature, norm = reference
    cx, cy = center
    height, width = mask.shape
    xs, xe = max(0, x-cx-radius), min(moving.shape[1]-width, x-cx+radius)
    ys, ye = max(0, y-cy-radius), min(moving.shape[0]-height, y-cy+radius)
    if xs > xe or ys > ye:
        raise ValueError("automatic motion anchor has no search overlap")
    windows = np.lib.stride_tricks.sliding_window_view(
        moving[ys:ye+height, xs:xe+width], (height, width), axis=(0, 1))
    patches = windows.transpose(0, 1, 3, 4, 2)[:, :, mask, :]
    centered = patches - patches.mean(axis=2, keepdims=True)
    denominator = np.sqrt((centered * centered).sum(axis=(2, 3))) * norm
    costs = 1 - (centered * feature).sum(axis=(2, 3)) / np.maximum(1e-10, denominator)
    iy, ix = np.unravel_index(costs.argmin(), costs.shape)
    dx, dy = x-(xs+int(ix)), y-(ys+int(iy))
    if abs(dx-cx) == radius or abs(dy-cy) == radius:
        raise ValueError("automatic motion anchor match reached search boundary")
    return np.array([dx, dy]), float(costs[iy, ix])


def discover(frames: list[Image.Image], *, reference_index: int) -> tuple[list, dict]:
    if len(frames) < 6 or any(im.mode != 'RGBA' or im.size != frames[0].size for im in frames):
        raise ValueError("automatic motion anchor needs at least six equally sized RGBA frames")
    width, height = frames[0].size
    # Keep small sprites measurable and bound memory for high resolution input.
    divisor = max(1, int(np.ceil(max(width, height) / ANALYSIS_EDGE)))
    size = (max(1, width // divisor), max(1, height // divisor))
    scale = np.array([width/size[0], height/size[1]])
    arrays = [motion_anchor._features(im.resize(size, Image.Resampling.BOX)) for im in frames]
    centers = [_center(a) for a in arrays]
    a = arrays[reference_index]
    yy, xx = np.nonzero(a[:, :, 3] > .1)
    x0, x1, y0, y1 = int(xx.min()), int(xx.max()+1), int(yy.min()), int(yy.max()+1)
    body_h, body_w = y1-y0, x1-x0
    pw, ph = max(5, round(min(body_w*.5, body_h*.25))), max(5, round(body_h*.20))
    radius = max(8, round(body_h*.16))
    samples = np.unique(np.linspace(0, len(frames)-1, SAMPLE_COUNT).astype(int))
    candidates = []
    rejected = 0
    for fraction in np.linspace(.08, .5, 6):
        y = int(y0+fraction*body_h)
        for x in range(x0, max(x0+1, x1-pw+1), max(2, pw//3)):
            box = (x, y, x+pw, y+ph)
            if box[2] > size[0] or box[3] > size[1]:
                continue
            reference = _reference(a, box)
            if reference is None:
                continue
            positions, errors = [], []
            try:
                for k in samples:
                    center = np.rint(centers[reference_index]-centers[k]).astype(int)
                    position, error = _match(reference, arrays[k], center, radius)
                    positions.append(position)
                    errors.append(error)
            except ValueError:
                rejected += 1
                continue  # A rejected patch is not used as an alternative motion result.
            smoothness = float(np.abs(np.diff(positions, n=2, axis=0)).mean()/body_h)
            quality = float(np.mean(errors)+.25*np.quantile(errors, .9)+.05*smoothness)
            candidates.append({'box': box, 'quality': quality, 'sample_costs': errors})
    candidates.sort(key=lambda row: (row['quality'], row['box']))
    if not candidates or candidates[0]['quality'] > .8:
        raise ValueError("automatic motion anchor found no stable textured region")
    chosen = []
    validated = []
    for row in candidates:
        if row['quality'] > .8:
            break
        if chosen and abs(row['box'][1]-chosen[0]['box'][1]) < ph*.75:
            continue
        reference = _reference(a, row['box'])
        tracking = []
        try:
            for k, moving in enumerate(arrays):
                center = np.rint(centers[reference_index]-centers[k]).astype(int)
                tracking.append(_match(reference, moving, center, radius))
        except ValueError:
            rejected += 1
            continue
        chosen.append(row)
        validated.append(tracking)
        if len(chosen) == 2:
            break
    if len(chosen) != 2:
        raise ValueError("automatic motion anchor needs two separated regions trackable throughout the clip")
    ordered = sorted(zip(chosen, validated), key=lambda pair: pair[0]['box'][1])
    chosen = [pair[0] for pair in ordered]
    validated = [pair[1] for pair in ordered]
    positions, costs = [], []
    for k in range(len(frames)):
        matches = [tracking[k] for tracking in validated]
        positions.append(sum(w * match[0] for w, match in zip(motion_anchor.REGION_WEIGHTS, matches)))
        costs.append([match[1] for match in matches])
    regions = [tuple(int(round(v * scale[j % 2])) for j, v in enumerate(row['box'])) for row in chosen]
    report = {
        'method': 'stable-texture-grid-v1', 'reference_index': reference_index,
        'analysis_size': list(size), 'scale_xy': scale.tolist(), 'regions': [list(b) for b in regions],
        'region_quality': [r['quality'] for r in chosen], 'candidate_count': len(candidates),
        'rejected_search_candidates': rejected, 'sample_indices': samples.tolist(),
        'tracking_xy': (np.asarray(positions)*scale).tolist(), 'tracking_costs': costs,
        'semantic_labels': False,
    }
    return regions, report


def analyse(frames: list[Image.Image], *, fps: float) -> tuple[np.ndarray, np.ndarray, dict]:
    _, report = discover(frames, reference_index=len(frames)//2)
    size = tuple(report['analysis_size'])
    scale = np.asarray(report['scale_xy'])
    positions = np.asarray(report['tracking_xy']) / scale
    n = len(frames)
    # Local linear X trend follows slow drift changes; vertical trend is global
    # so the periodic hop/bob remains evidence. These images are NEVER emitted.
    radius = max(3, round(fps/2))
    trajectory = []
    for k in range(n):
        js = np.arange(max(0, k-radius), min(n, k+radius+1))
        trajectory.append(float(np.polyfit(js-k, positions[js, 0], 1)[1]))
    trajectory = np.asarray(trajectory)
    trajectory -= trajectory[n//2]
    dy_slope = float(np.polyfit(np.arange(n), positions[:, 1], 1)[0])
    shifts = np.column_stack((trajectory, dy_slope*(np.arange(n)-n//2)))
    pad = int(np.ceil(np.abs(shifts).max()))+4
    normalized = []
    for im, (dx, dy) in zip(frames, shifts):
        small = im.resize(size, Image.Resampling.BOX)
        canvas = Image.new('RGBA', (size[0]+2*pad, size[1]+2*pad))
        canvas.paste(small, (pad, pad))
        canvas = canvas.transform(canvas.size, Image.Transform.AFFINE,
                                  (1, 0, -float(dx), 0, 1, -float(dy)), Image.Resampling.BILINEAR)
        normalized.append(motion_anchor._features(canvas).reshape(-1))
    flat = np.stack(normalized)
    distances = np.empty((n, n), dtype=np.float32)
    for k in range(n):
        distances[k] = np.abs(flat-flat[k]).mean(axis=1)
    report['analysis_only_translation_xy'] = (shifts*scale).tolist()
    report['analysis_only'] = True
    return distances, trajectory, report
