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

REGION_WEIGHTS = (0.7, 0.3)
SEARCH_RADIUS = (14, 28)
COARSE_SEARCH = 64
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
    if anchor != "motion":
        if regions is not None:
            raise ValueError("--anchor-region requires --anchor motion")
        return
    if cycle_mode != "fixed" or length is None or length < 6:
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


def _register(references: list[tuple], moving: np.ndarray, center: tuple[int, int]) -> dict:
    cx, cy = center
    rx, ry = SEARCH_RADIUS
    candidates = []
    for dy in range(cy - ry, cy + ry + 1):
        for dx in range(cx - rx, cx + rx + 1):
            costs = []
            for (x0, y0, x1, y1), mask, reference, norm in references:
                if x0 - dx < 0 or y0 - dy < 0 or x1 - dx > moving.shape[1] or y1 - dy > moving.shape[0]:
                    break
                patch = moving[y0 - dy:y1 - dy, x0 - dx:x1 - dx][mask]
                if not np.ptp(patch, axis=0).any():
                    break
                patch = patch - patch.mean(axis=0)
                denominator = norm * float(np.sqrt(np.sum(patch * patch)))
                if denominator <= 1e-12:
                    break
                costs.append(1 - float(np.sum(reference * patch) / denominator))
            if len(costs) == 2:
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
    corrected = []
    for frame, (dx, dy) in zip(frames, shifts.tolist()):
        canvas = Image.new("RGBA", (width + left + right, height + top + bottom))
        canvas.paste(frame, (left + dx, top + dy))
        corrected.append(canvas)
    return corrected, {
        "method": "masked-ncc-boundary-velocity-v1", "regions": [list(b) for b in regions],
        "region_weights": list(REGION_WEIGHTS), "search_radius_xy": list(SEARCH_RADIUS),
        "coarse_dx_px": coarse_dx, "boundary_measurements": measurements,
        "raw_start_velocity_xy": v_start.tolist(), "raw_end_velocity_xy": v_end.tolist(),
        "endpoint_float_xy": endpoint_float.tolist(), "endpoint_xy": endpoint.tolist(),
        "shifts_xy": shifts.tolist(), "padding_ltrb": [left, top, right, bottom],
    }
