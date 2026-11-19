"""Integer XY drift correction from explicitly selected head and torso regions.

Match the first/last three poses, then apply ONE linear translation ramp. This
preserves the original motion instead of pinning the subject in every frame.
Region coordinates belong to the first frame of an already selected cycle.
"""
from __future__ import annotations

import argparse
from numbers import Integral

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video.loop_vocab import ANCHOR_MOTION, CYCLE_FIXED

REGION_WEIGHTS = (0.7, 0.3)
SEARCH_RADIUS = (14, 28)
COARSE_SEARCH = 64
ROW_BLOCK = 1024  # mask pixels a block of one row of shifts takes at a time (`_row_costs`)
Box = tuple[int, int, int, int]


class FineSearchBoundaryError(ValueError):
    """A measured candidate is outside the trusted fine-registration interior."""

    def __init__(self, measurement: dict):
        super().__init__("motion anchor match reached search boundary; inspect the cut and regions")
        self.measurement = measurement


def parse_region(value: str) -> Box:
    try:
        coordinates = tuple(int(v.strip()) for v in value.split(","))
        if len(coordinates) != 4:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("region must be x0,y0,x1,y1 (four integers)") from None
    return coordinates


def validate_request(anchor: str, cycle_mode: str, length: int | None, regions: list[Box] | None) -> None:
    if anchor != ANCHOR_MOTION:
        if regions is not None:
            raise ValueError("--anchor-region requires --anchor motion")
        return
    if cycle_mode != CYCLE_FIXED or length is None or length < 6:
        raise ValueError("--anchor motion requires --cycle fixed and --length >= 6")
    if regions is None or len(regions) != 2:
        raise ValueError("--anchor motion needs two --anchor-region values: head, then torso")


def _features(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image, dtype=np.float32) / 255
    return np.concatenate((rgba[:, :, :3] * rgba[:, :, 3:4], rgba[:, :, 3:4]), axis=2)


def _references(image: np.ndarray, regions: list[Box]) -> list[tuple]:
    height, width = image.shape[:2]
    references = []
    if len(regions) != 2:
        raise ValueError("motion anchor needs exactly two regions: head, then torso")
    for box in regions:
        if len(box) != 4 or any(not isinstance(v, Integral) or isinstance(v, bool) for v in box):
            raise ValueError("motion anchor regions must have four integer coordinates")
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ValueError(f"motion anchor region outside first frame: {box}")
        patch = image[y0:y1, x0:x1]
        mask = patch[:, :, 3] > 0.05
        if not mask.any():
            raise ValueError(f"motion anchor region contains no foreground: {box}")
        for _ in range(2):
            padded = np.pad(mask, 1)
            mask = np.logical_or.reduce([
                padded[y:y + mask.shape[0], x:x + mask.shape[1]]
                for x, y in ((1, 1), (0, 1), (2, 1), (1, 0), (1, 2))
            ])
        flat = patch[mask]
        if not np.ptp(flat, axis=0).any():
            raise ValueError(f"motion anchor region has no measurable texture or contour: {box}")
        centered = flat - flat.mean(axis=0)
        norm = float(np.sqrt(np.sum(centered ** 2)))
        if norm <= 1e-12:
            raise ValueError(f"motion anchor region has no measurable texture or contour: {box}")
        references.append((box, mask, centered, norm))
    return references


def _shift_blocks(moving: np.ndarray, count: int) -> np.ndarray:
    """Every pixel of `moving` with the `count - 1` after it on the flat image, a view (nothing copied): the block of a
    region's pixel holds it under `count` side-by-side shifts, the last shift first, and stays in the pixel's row while
    no shift lays the region past the frame."""
    height, width, channels = moving.shape
    _, pixel, channel = moving.strides
    return np.lib.stride_tricks.as_strided(moving, (height * width - count + 1, count, channels), (pixel, pixel, channel),
                                           writeable=False)


def _row_costs(reference: tuple, blocks: np.ndarray, at: np.ndarray, scratch: tuple) -> list[float | None]:
    """The masked NCC cost of the reference at each of a row of side-by-side shifts, the last shift first, None where it
    reads none (a patch of one value, or of no energy).

    `blocks[at]` (`_shift_blocks`) is every shift's patch at once, a mask pixel at a time. Each cost is the one a patch
    read alone has, value for value: the patch's mean sums its pixels one after the other in the mask's row order (a
    reduce over the leading axis, as `patch.mean(axis=0)` does on one patch, carried from block to block), and its two
    other sums each run over that patch alone, laid out as one patch is. A block of ROW_BLOCK pixels at a time keeps the
    work in cache, and the scratch arrays are `_register`'s, made once.
    """
    _, _, feature, norm = reference
    block, laid, squares, products = scratch
    n, count = len(at), blocks.shape[1]
    total = high = low = None
    for s in range(0, n, ROW_BLOCK):
        c = min(ROW_BLOCK, n - s)
        part = block[1:c + 1]
        part[...] = blocks[at[s:s + c]]
        high = part.max(axis=0) if high is None else np.maximum(high, part.max(axis=0))
        low = part.min(axis=0) if low is None else np.minimum(low, part.min(axis=0))
        if total is None:
            total = np.add.reduce(part, axis=0)
        else:
            block[0] = total
            total = np.add.reduce(block[:c + 1], axis=0)
    mean = np.true_divide(total, np.intp(n), out=total, casting="unsafe")
    # The centered patches one after the other, a pixel copied as its bytes.
    pixels = laid[:count * n].reshape(count, n)
    for s in range(0, n, ROW_BLOCK):
        c = min(ROW_BLOCK, n - s)
        part = block[:c]
        part[...] = blocks[at[s:s + c]]
        np.subtract(part, mean, out=part)
        pixels[:, s:s + c] = part.view(laid.dtype)[:, :, 0].T
    patches = pixels.view(block.dtype).reshape(count, n, block.shape[2])
    squares, products = squares[:n], products[:n]
    textured = (high - low).any(axis=1)
    costs = []
    for k in range(count):
        if not textured[k]:
            costs.append(None)
            continue
        patch = patches[k]
        denominator = norm * float(np.sqrt(np.sum(np.multiply(patch, patch, out=squares))))
        costs.append(1 - float(np.sum(np.multiply(feature, patch, out=products)) / denominator)
                     if denominator > 1e-12 else None)
    return costs


def _register(references: list[tuple], moving: np.ndarray, center: tuple[int, int]) -> dict:
    cx, cy = center
    rx, ry = SEARCH_RADIUS
    moving = np.ascontiguousarray(moving)
    height, width, channels = moving.shape
    # The shifts that lay every region inside the frame; any other reads nothing.
    lo = max(cx - rx, *(x1 - width for (_, _, x1, _), *_ in references))
    hi = min(cx + rx, *(x0 for (x0, _, _, _), *_ in references))
    candidates = []
    if lo <= hi:
        count = hi - lo + 1
        blocks = _shift_blocks(moving, count)
        # Each region's mask pixels in row order, as offsets on the flat image from its box's corner.
        offsets = [rows * width + cols for rows, cols in (np.nonzero(mask) for _, mask, _, _ in references)]
        most = max(len(at) for at in offsets)
        scratch = (np.empty((ROW_BLOCK + 1, count, channels), moving.dtype),
                   np.empty(count * most, np.dtype((np.void, moving.strides[1]))),
                   np.empty((most, channels), moving.dtype), np.empty((most, channels), moving.dtype))
        for dy in range(cy - ry, cy + ry + 1):
            if any(y0 - dy < 0 or y1 - dy > height for (_, y0, _, y1), *_ in references):
                continue
            rows = []
            for reference, at in zip(references, offsets):
                x0, y0, _, _ = reference[0]
                rows.append(_row_costs(reference, blocks, (y0 - dy) * width + x0 - hi + at, scratch)[::-1])
            for dx, costs in zip(range(lo, hi + 1), zip(*rows)):
                if None not in costs:
                    costs = list(costs)
                    candidates.append((sum(w * c for w, c in zip(REGION_WEIGHTS, costs)), dx, dy, costs))
    if not candidates:
        raise ValueError("motion anchor found no measurable match inside the search window")
    candidates.sort()
    score, dx, dy, costs = candidates[0]
    if abs(dx - cx) == rx or abs(dy - cy) == ry:
        raise FineSearchBoundaryError({
            "dx": dx, "dy": dy, "cost": score, "region_costs": costs,
            "center_xy": [cx, cy], "search_radius_xy": [rx, ry],
        })
    return {"dx": dx, "dy": dy, "cost": score, "region_costs": costs}


def correct_motion(frames: list[Image.Image], regions: list[Box], *, coarse_dx: int) -> tuple[list[Image.Image], dict]:
    """Correct drift without changing frame order, RGBA pixels or the local bob.

    ``coarse_dx`` is the horizontal body wrap measurement (search=COARSE_SEARCH).
    It centers the bounded XY search, not the resulting correction.
    """
    if len(frames) < 6:
        raise ValueError("motion anchor requires at least six cycle frames")
    if any(im.mode != "RGBA" or im.size != frames[0].size for im in frames):
        raise ValueError("motion anchor needs RGBA frames with identical dimensions")
    if abs(coarse_dx) >= COARSE_SEARCH:
        raise ValueError("motion anchor coarse match reached search boundary")
    references = _references(_features(frames[0]), regions)
    length = len(frames)
    measurements = []
    for k in (0, 1, 2, length - 3, length - 2, length - 1):
        center = (round(coarse_dx * k / (length - 1)), 0)
        try:
            measurements.append({"k": k, **_register(references, _features(frames[k]), center)})
        except FineSearchBoundaryError as exc:
            exc.measurement["k"] = k
            raise
    positions = -np.array([[r["dx"], r["dy"]] for r in measurements], dtype=float)
    v_start = (positions[2] - positions[0]) / 2
    v_end = (positions[5] - positions[3]) / 2
    velocity = (v_start + v_end) / 2
    match = np.array([measurements[-1]["dx"], measurements[-1]["dy"]], dtype=float)
    # Equalize the wrap's displacement with the mean adjacent displacement:
    # -p_last-d = velocity+d/(length-1).
    endpoint_float = (match - velocity) * (length - 1) / length
    endpoint = np.rint(endpoint_float).astype(int)
    shifts = np.rint(np.linspace((0, 0), endpoint, length)).astype(int)
    # Common origin + enough room for every ORIGINAL canvas, not just its bbox.
    left, top = (-np.minimum(shifts.min(axis=0), 0)).tolist()
    right, bottom = np.maximum(shifts.max(axis=0), 0).tolist()
    width, height = frames[0].size
    record = {
        "method": "masked-ncc-boundary-velocity-v1", "regions": [list(b) for b in regions],
        "region_weights": list(REGION_WEIGHTS), "search_radius_xy": list(SEARCH_RADIUS),
        "coarse_dx_px": coarse_dx, "boundary_measurements": measurements,
        "raw_start_velocity_xy": v_start.tolist(), "raw_end_velocity_xy": v_end.tolist(),
        "endpoint_float_xy": endpoint_float.tolist(), "endpoint_xy": endpoint.tolist(),
        "shifts_xy": shifts.tolist(), "padding_ltrb": [left, top, right, bottom],
    }
    corrected = []
    for k, frame in enumerate(frames):
        canvas = Image.new("RGBA", (width + left + right, height + top + bottom))
        canvas.paste(frame, laid_at(record, k))
        corrected.append(canvas)
    return corrected, record


def laid_at(record: dict, k: int) -> tuple[int, int]:
    """Where `correct_motion` laid frame k of its cycle on the canvas it gives every frame (its `record`): the padding's
    left and top, and that frame's shift."""
    left, top = record["padding_ltrb"][:2]
    dx, dy = record["shifts_xy"][k]
    return left + dx, top + dy
