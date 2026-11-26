# SPDX-License-Identifier: Apache-2.0
"""The face layer: the lower face of a talking clip laid over any body motion, frame by frame.

A body motion (an idle loop, a greeting, a reaction) and the face that talks are two layers. The face layer is the
whole lower face — mouth, jaw line, cheeks and chin down to the neck line — cut from a talking clip, so the body's own
jaw, which a talking clip moves, is never seen under a closed mouth. Everything is read in the coordinates of one
reference cell (the body's rest pose, the still the clips were made from), on which the request names the face: the
mouth, both eyes, the lower face's top edge across the cheeks, the box a lower face can be in and the skin points it is
grown from (`Geometry`).

- **Anchor** (`fit`): per frame, the similarity — place, size, tilt — that lays the reference cell's face on the frame.
  It is read on the band round the reference's own lower face (cheeks under the eyes, the nose, the hair's edge, the
  headset; not the jaw, which moves as the face talks, and nothing below `band_bottom`, where the neck and choker are
  the body's): the engine's masked-NCC match (`auto_motion._match_costs`, `_best`) reads the place within `SEARCH` px,
  and again within twice that where the best sits on the search's rim; every size and tilt of a grid is then laid
  through it and the place read again near it, the least cost wins, a parabola through its neighbours refines size,
  tilt and place, and a best on the grid's rim is read again on a grid twice as wide. A reading further from its
  window's median than three scaled MADs is a misreading and takes that median (`hampel`). A body frame whose own
  mouth is closed has its place read again on the ring of skin round the mouth (`Band.ring`, `place_on`), its size and
  tilt the band's: a head that turns moves its hair, headset and cheeks further than its mouth. A frame whose mouth is
  open takes the moves of the read frames round it (`on_ring`).
- **Weight** (`weight`): the lower faces of the reference and of every frame of every clip, read into the reference
  through their anchors, joined (`union`; a frame adds nothing past `REACH` px of the reference's own lower face, so a
  hand or sleeve the skin grows into stays out), and holes filled; flat 1 over it grown by `MARGIN`, a cos² feather
  over `FEATHER` px outside it, 0 above the top edge rising to 1 over `RAMP`, the eyes cut out.
- **Sprite** (`face_sprite`): a talking frame's lower face at the clip's own resolution, its alpha the weight (less
  its outermost EDGE px, which the resampling would spread past it) less what must stay the body's where the face
  meets it — the dark grey and black that reach in from the boundary (the headset's boom, the choker) and the lines
  drawn in the feather — its colour moved onto the reference's by the median over the feather.
- **Keep** (`body_keep`): per body frame, what of the body stays on top of the layer: its own dark grey and black
  reaching in from the boundary (but not its own mouth, `own_mouth`, which a dark outline can join to the outside),
  its lines in the feather, and a hand (`hand_reach`): what differs from the reference face and reaches in from
  outside the layer far into the face above the chin.
- **Lay** (`lay`): the body frame, the sprite over it through the frame's anchor (one `resample.transform_cell`,
  after one `resample.resize_cell` where the sprite is shrunk), then the keep over that. Both are plain source-over,
  so a live player draws the same with two `drawImage` calls; nothing outside the layer's weight changes.

docs/face-parts.md owns the rules; the numbers below are read at a 960 px reference on which the lower face is about
190 px across.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.util import resample
from sprite_gen.video import auto_motion, motion_anchor
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import parts as parts_mod
from sprite_gen.video.stop import SEAM_MAX

LUMA = np.array([0.299, 0.587, 0.114], np.float32)
SKIN_LUMA = 175          # skin is opaque and lighter than this
RAMP = 14                # the weight rises from 0 at the top edge to 1 this far below it
MARGIN, FEATHER = 6, 10  # the union grown by MARGIN px is flat; the weight falls to 0 over FEATHER px outside it
EDGE = 2                 # a sprite leaves out the feather's outermost px (weight under 0.1): mapped to the clip's
                         # pixels and back, its edge spreads a pixel, and nothing may change outside the weight
EYE_GROW = 6
REACH = 12               # a frame's lower face counts this far past the reference's own: a dropped jaw, not a hand
BAND = 30                # the anchor band: this far outside the flat part, above band_bottom, less the eyes
SEARCH = 48              # px each way the place is searched; a best on the rim is searched again at twice this
FINE = 3                 # px each way the place is read again at each size and tilt
RECENTRE_ROUNDS = 4      # times the grid is read again round a place further than FINE - 1 from the last
SCALES = np.array([0.96, 0.98, 1.0, 1.02, 1.04])
THETAS = np.arange(-4.0, 4.01, 1.0)
WIDE_SCALES = np.round(np.arange(0.92, 1.0801, 0.02), 3)
WIDE_THETAS = np.arange(-8.0, 8.01, 1.0)
HAMPEL_WINDOW = 5
HAMPEL_FLOOR = {"tx": 0.5, "ty": 0.5, "s": 0.01, "th": 0.3}
RING_IN, RING_OUT = (30, 14), (60, 42)  # px half-axes of the ellipses round the mouth the ring lies between
RING_SEARCH = 16         # px each way a body frame's place is read again on the ring; twice this where the best is on the rim
RING_OPAQUE = 200 / 255  # the ring reads the reference's lower face where it is this opaque
DARK_LUMA, DARK_CHROMA, DARK_GROW = 110, 45, 3
LINE_LUMA, LINE_GROW = 150, 2
FOREIGN = 48             # a body pixel this far (any channel, 0..255) from the reference face is foreign
HAND_MIN = 400           # px of a hand's skin on the flat weight above the chin before it is a hand
HAND_GROW = 3
SKIN_WARM = 12           # a hand's skin is red over blue by this much (0..255)
SHRINK_FROM = 0.9        # a sprite laid smaller than this is resized first (resample.resize_cell), then mapped
JUMP_LINE = SEAM_MAX     # a step over this many of the body's median steps is a jump
# the mouth reading (`mouth_reading`): darker than the box's skin by DROP, off the headset's grey
DROP = 18
CLOSED_HEIGHT = 5.0      # a mouth this tall or less is closed
SMILE_FROM = 0.35        # px of lift from which a closed mouth smiles
OPEN_LUMA = 40           # the open mouth's area: pixels round the mouth this much off the reference


@dataclass(frozen=True)
class Geometry:
    """The face on the reference cell (its pixel coordinates); docs/face-parts.md "The request"."""

    size: tuple[int, int]
    mouth: tuple[float, float]
    eyes: tuple[tuple[int, int, int, int], ...]
    top: int
    box: tuple[int, int, int, int]
    seeds: tuple[tuple[int, int], ...]
    band_bottom: int

    @classmethod
    def read(cls, value: dict[str, Any], size: tuple[int, int]) -> Geometry:
        def ints(v: Any, n: int, what: str) -> tuple[int, ...]:
            if not isinstance(v, (list, tuple)) or len(v) != n or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v):
                raise ValueError(f"face.{what} must be {n} numbers")
            return tuple(int(round(x)) for x in v)

        if not isinstance(value, dict):
            raise ValueError("the request needs a face object")
        lower = value.get("lower_face")
        if not isinstance(lower, dict):
            raise ValueError("face.lower_face must be an object with top, box and seeds")
        eyes = value.get("eyes")
        seeds = lower.get("seeds")
        if not isinstance(eyes, list) or len(eyes) != 2:
            raise ValueError("face.eyes must be two boxes x0,y0,x1,y1")
        if not isinstance(seeds, list) or not seeds:
            raise ValueError("face.lower_face.seeds must name at least one skin point")
        geom = cls(size=size, mouth=tuple(float(v) for v in ints(value.get("mouth"), 2, "mouth")),
                   eyes=tuple(ints(e, 4, "eyes[]") for e in eyes), top=int(lower.get("top", -1)),
                   box=ints(lower.get("box"), 4, "lower_face.box"), seeds=tuple(ints(s, 2, "lower_face.seeds[]") for s in seeds),
                   band_bottom=int(value.get("band_bottom", -1)))
        w, h = size
        x0, y0, x1, y1 = geom.box
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise ValueError(f"face.lower_face.box {geom.box} is not inside the {w}x{h} reference")
        if not y0 <= geom.top < y1:
            raise ValueError("face.lower_face.top must lie inside its box")
        if not geom.top < geom.band_bottom < h:
            raise ValueError("face.band_bottom must lie below the lower face's top edge")
        if not all(x0 <= x < x1 and y0 <= y < y1 for x, y in geom.seeds):
            raise ValueError("face.lower_face.seeds must lie inside its box")
        return geom

    def around_mouth(self, left: int, up: int, right: int, down: int) -> tuple[int, int, int, int]:
        mx, my = (int(round(v)) for v in self.mouth)
        w, h = self.size
        return max(mx - left, 0), max(my - up, 0), min(mx + right, w), min(my + down, h)

    @property
    def mouth_box(self) -> tuple[int, int, int, int]:
        """Where the mouth line can be: the corners and the lift are read here (`mouth_reading`)."""
        return self.around_mouth(46, 31, 49, 39)

    @property
    def open_zone(self) -> tuple[int, int, int, int]:
        """Where an open mouth differs from the reference's closed one (`open_area`)."""
        return self.around_mouth(45, 30, 45, 45)

    @property
    def step_box(self) -> tuple[int, int, int, int]:
        """The face round the mouth on which a step between two frames is read (`step_features`)."""
        return self.around_mouth(58, 41, 64, 79)


# --- geometry -----------------------------------------------------------------------------------------------------

def luma(a: np.ndarray) -> np.ndarray:
    return a[..., :3] @ LUMA


def sim(tx: float, ty: float, s: float, th: float, centre: tuple[float, float]) -> np.ndarray:
    """A(p) = c + T + s R(th) (p - c): the reference's pixel p to the frame's (pixel indices, centres on integers)."""
    a = math.radians(th)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]) * s
    c = np.asarray(centre, float)
    m = np.eye(3)
    m[:2, :2] = rot
    m[:2, 2] = c + np.array([tx, ty]) - rot @ c
    return m


def shift(dx: float, dy: float) -> np.ndarray:
    m = np.eye(3)
    m[:2, 2] = (dx, dy)
    return m


def scale(k: float) -> np.ndarray:
    m = np.eye(3)
    m[0, 0] = m[1, 1] = k
    return m


def pil_data(m: np.ndarray) -> tuple[float, ...]:
    """Pillow's AFFINE data (output -> input, read on continuous coordinates, pixel centres at +0.5) for a map `m`
    given on pixel indices."""
    a, b = m[:2, :2], m[:2, 2]
    b = b + 0.5 - a @ np.array([0.5, 0.5])
    return (float(a[0, 0]), float(a[0, 1]), float(b[0]), float(a[1, 0]), float(a[1, 1]), float(b[1]))


def warp(arr: np.ndarray, m: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Float channels of `arr` sampled at m(p) for every output pixel p (output -> input), bilinear, 0 outside."""
    data = pil_data(m)
    flat = arr[..., None] if arr.ndim == 2 else arr
    out = np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(flat[..., k], np.float32), "F").transform(
        size, Image.Transform.AFFINE, data, resample=Image.Resampling.BILINEAR), np.float32) for k in range(flat.shape[2])], -1)
    return out[..., 0] if arr.ndim == 2 else out


def placement(place: tuple[int, int, int], native: tuple[int, int]) -> np.ndarray:
    """The map from a clip frame laid on the reference (`to_reference`) to the clip's own pixels: a clip that shows the
    reference's square at (x, y), `side` px across, at its own size."""
    x, y, side = place
    return scale(native[0] / side) @ shift(-x, -y)


def to_reference(frame: Image.Image, place: tuple[int, int, int], size: tuple[int, int]) -> Image.Image:
    """A clip frame on the reference's pixels: scaled to the square it shows (`resample.resize_cell`) and put there.
    A clip of the whole reference (place 0, 0, its width) is the frame as it is."""
    x, y, side = place
    if (x, y) == (0, 0) and frame.size == (side, side) == size:
        return frame
    out = Image.new("RGBA", size)
    out.paste(resample.resize_cell(frame, (side, side)), (x, y))
    return out


def features(image: Image.Image) -> np.ndarray:
    return motion_anchor._features(image).astype(np.float64)


def step_features(arr: np.ndarray, geom: Geometry) -> np.ndarray:
    """The engine's analysis features (`loop._small_features`) of a picture in reference coordinates, on the face
    round the mouth (`Geometry.step_box`): what a step between two frames is read on."""
    x0, y0, x1, y1 = geom.step_box
    crop = np.clip(arr[y0:y1, x0:x1] + 0.5, 0, 255).astype(np.uint8)
    return loop_mod._small_features(Image.fromarray(crop, "RGBA")).astype(np.float64)


# --- masks --------------------------------------------------------------------------------------------------------

def reach(mask: np.ndarray, seeds: np.ndarray) -> np.ndarray:
    """The pieces of `mask` (4-connected) that hold a seed."""
    lab, sizes = parts_mod._pieces(mask)
    if not sizes.size:
        return np.zeros_like(mask)
    ids = np.unique(lab[seeds & (lab >= 0)])
    return np.isin(lab, ids) if ids.size else np.zeros_like(mask)


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """`mask` with every hole filled: what the outside of the picture cannot reach without crossing it."""
    border = np.zeros_like(mask)
    border[[0, -1], :] = True
    border[:, [0, -1]] = True
    return ~reach(~mask, border & ~mask)


def soft_grow(mask: np.ndarray, grow: int) -> np.ndarray:
    """1 on `mask` grown by `grow` px, then cos² to 0 over as many px again."""
    grown = parts_mod._dilate(mask, grow)
    soft = grown.astype(np.float32)
    outer = grown
    for k in range(1, grow + 1):
        ring_ = parts_mod._dilate(grown, k)
        soft[ring_ & ~outer] = math.cos(k / (grow + 1) * math.pi / 2) ** 2
        outer = ring_
    return soft


def eye_mask(geom: Geometry) -> np.ndarray:
    w, h = geom.size
    yy, xx = np.mgrid[0:h, 0:w]
    m = np.zeros((h, w), bool)
    for ex0, ey0, ex1, ey1 in geom.eyes:
        m |= (xx >= ex0 - EYE_GROW) & (xx < ex1 + EYE_GROW) & (yy >= ey0 - EYE_GROW) & (yy < ey1 + EYE_GROW)
    return m


def lower_face(arr: np.ndarray, geom: Geometry) -> np.ndarray:
    """The lower face of a picture already in reference coordinates: opaque skin grown from the seeds inside the box
    below the top edge; the face's outline and the mouth's lines stop it."""
    x0, y0, x1, y1 = geom.box
    sub = arr[y0:y1, x0:x1]
    skin = (luma(sub) > SKIN_LUMA) & (sub[..., 3] > 200)
    seeds = np.zeros_like(skin)
    for x, y in geom.seeds:
        seeds[y - y0, x - x0] = True
    out = np.zeros(arr.shape[:2], bool)
    out[y0:y1, x0:x1] = reach(skin, seeds & skin)
    return out


def union(reference_face: np.ndarray, faces: list[np.ndarray]) -> np.ndarray:
    """The lower faces joined, each held within REACH px of the reference's own, the holes (open mouths, lines)
    filled."""
    room = parts_mod._dilate(reference_face, REACH)
    out = reference_face.copy()
    for face in faces:
        out |= face & room
    return fill_holes(out)


def weight(face_union: np.ndarray, geom: Geometry) -> np.ndarray:
    """1 on the union grown by MARGIN px, cos² to 0 over FEATHER px outside it; 0 above the top edge rising to 1 over
    RAMP px; 0 on the eyes, feathered over half the feather."""
    flat = parts_mod._dilate(face_union, MARGIN)
    w = flat.astype(np.float32)
    inner = flat
    for k in range(1, FEATHER + 1):
        ring_ = parts_mod._dilate(flat, k)
        w[ring_ & ~inner] = math.cos(k / (FEATHER + 1) * math.pi / 2) ** 2
        inner = ring_
    h = geom.size[1]
    rise = np.clip((np.arange(h, dtype=np.float32) - geom.top) / RAMP, 0, 1)
    w *= (np.sin(rise * math.pi / 2) ** 2)[:, None]
    w *= 1 - soft_grow(eye_mask(geom), FEATHER // 2)
    return w.astype(np.float32)


def dark(arr: np.ndarray) -> np.ndarray:
    """Dark grey and black: a headset, its boom, a choker (not a dark red mouth)."""
    rgb = arr[..., :3]
    return (luma(arr) < DARK_LUMA) & (arr[..., 3] > 128) & (rgb.max(-1) - rgb.min(-1) < DARK_CHROMA)


def dark_reach(arr: np.ndarray, w: np.ndarray) -> np.ndarray:
    """The dark grey and black of a picture that reaches in from where its weight `w` is under 0.5, grown DARK_GROW px
    and feathered as far: the headset's boom and the choker cross the layer's boundary and stay; a dark mouth inside
    the face does not reach it."""
    d = dark(arr)
    m = reach(d, d & (w < 0.5)) & parts_mod._dilate(w > 0, DARK_GROW + 1)
    return soft_grow(m, DARK_GROW) if m.any() else np.zeros(w.shape, np.float32)


def line_band(arr: np.ndarray, w: np.ndarray) -> np.ndarray:
    """The lines a picture draws in the feather (0 < w < 0.98: the jaw line, the nose, hair), grown LINE_GROW px: a
    line blended across the feather over the other picture's would read as a second, fainter line."""
    band = (w > 0) & (w < 0.98)
    m = (luma(arr) < LINE_LUMA) & (arr[..., 3] > 128) & band
    return soft_grow(m, LINE_GROW) if m.any() else np.zeros(w.shape, np.float32)


def own_mouth(body_ref: np.ndarray, geom: Geometry) -> np.ndarray:
    """A body frame's own mouth in reference coordinates: its mouth line or opening (`mouth_line`), filled (a tongue,
    teeth) and grown 2 px. The layer replaces it: nothing of it stays on top but where a hand meets it."""
    mouth = np.zeros(body_ref.shape[:2], bool)
    line = mouth_line(body_ref, geom)
    if line is not None:
        x0, y0, x1, y1 = geom.mouth_box
        mouth[y0:y1, x0:x1] = parts_mod._dilate(fill_holes(line), 2)
    return mouth


def hand_reach(body_ref: np.ndarray, reference: np.ndarray, w0: np.ndarray, geom: Geometry, mouth: np.ndarray) -> np.ndarray:
    """A hand over the face, in reference coordinates. What differs from the reference face (FOREIGN) is cut into
    pieces, and a piece that holds a pixel outside the layer reaches in from outside. The frame's own skin (light and
    warm: teeth and white streaks are not skin) is cut into pieces too, its drawn lines the walls: the piece that lies
    most on the reference's own lower face is the face; any other skin piece a reaching piece touches is the hand's (a
    palm, a finger, the sleeve under it), with the lines round it (HAND_GROW px of the reaching piece). It is a hand
    where HAND_MIN px or more of those skin pieces lie on the layer's flat part above the chin (`band_bottom`); grown
    HAND_GROW px and feathered. A body's own mouth (`own_mouth`) is not skin and is kept only where it meets a hand,
    and a skin piece lying half or more in it (a tongue) is not a hand's; the face's own skin, however far its outline
    moves off the reference's, is never a hand's."""
    near = parts_mod._dilate(w0 > 0, HAND_GROW + FEATHER)
    ys, xs = np.nonzero(near)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    b, r = body_ref[y0:y1, x0:x1], reference[y0:y1, x0:x1]
    solid = b[..., 3] > 128
    differ = ((np.abs(b[..., :3] - r[..., :3]).max(-1) > FOREIGN) | (np.abs(b[..., 3] - r[..., 3]) > 64)) & solid
    differ &= near[y0:y1, x0:x1]
    sub_w = w0[y0:y1, x0:x1]
    lab, sizes = parts_mod._pieces(differ)
    if not sizes.size:
        return np.zeros(w0.shape, np.float32)
    reaching = np.isin(lab, np.unique(lab[differ & (sub_w <= 0) & (lab >= 0)]))
    warm = (b[..., 0] - b[..., 2] >= SKIN_WARM) & (luma(b) > SKIN_LUMA) & solid
    skin_lab, skin_sizes = parts_mod._pieces(warm)
    if not skin_sizes.size or not reaching.any():
        return np.zeros(w0.shape, np.float32)
    own = lower_face(reference, geom)[y0:y1, x0:x1]
    face = int(np.argmax(np.bincount(skin_lab[own & (skin_lab >= 0)], minlength=skin_sizes.size))) if (own & (skin_lab >= 0)).any() else -1
    in_mouth = mouth[y0:y1, x0:x1]  # a tongue is light and warm too
    touched = [j for j in np.unique(skin_lab[parts_mod._dilate(reaching, 2) & (skin_lab >= 0)])
               if j != face and (in_mouth & (skin_lab == j)).sum() * 2 < skin_sizes[j]]
    hand_skin = np.isin(skin_lab, touched)
    inside = (sub_w > 0.98) & (np.arange(y0, y1)[:, None] < geom.band_bottom)
    if int((hand_skin & inside).sum()) < HAND_MIN:
        return np.zeros(w0.shape, np.float32)
    hand = hand_skin | (reaching & parts_mod._dilate(hand_skin, HAND_GROW))
    out = np.zeros(w0.shape, bool)
    out[y0:y1, x0:x1] = hand & (sub_w > 0)
    return soft_grow(out, HAND_GROW)


# --- anchors ------------------------------------------------------------------------------------------------------

@dataclass
class Band:
    """An anchor reference: auto_motion's masked-NCC reference tuple on a region of the reference (the band round
    the layer, or the ring round the mouth), and the reference's features on the region's box."""

    reference: tuple
    features: np.ndarray

    @classmethod
    def read(cls, reference: Image.Image, w0: np.ndarray, geom: Geometry) -> Band:
        """The band round the layer, on which a frame's place, size and tilt are read (`fit`)."""
        rows = np.arange(geom.size[1])[:, None] < geom.band_bottom
        return cls.on(features(reference), parts_mod._dilate(w0 > 0, BAND) & ~(w0 > 0.98) & rows & ~eye_mask(geom), 0.05)

    @classmethod
    def ring(cls, reference: Image.Image, w0: np.ndarray, geom: Geometry) -> Band:
        """The ring round the mouth, on which a body frame's place is read again (`place_on`): the reference's own
        lower face (`w0`, its weight) between the ellipses RING_IN and RING_OUT round the mouth, where it is opaque.
        The mouth inside it is left out: a body's closed mouth smiles more or less than the reference's."""
        h, w = w0.shape
        yy, xx = np.mgrid[0:h, 0:w]
        mx, my = geom.mouth

        def inside(rx: float, ry: float) -> np.ndarray:
            return ((xx - mx) / rx) ** 2 + ((yy - my) / ry) ** 2 < 1

        return cls.on(features(reference), (w0 > 0) & inside(*RING_OUT) & ~inside(*RING_IN), RING_OPAQUE)

    @classmethod
    def on(cls, ref: np.ndarray, region: np.ndarray, opaque: float) -> Band:
        ys, xs = np.nonzero(region)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        patch = ref[y0:y1, x0:x1]
        mask = region[y0:y1, x0:x1] & (patch[:, :, 3] > opaque)
        flat = patch[mask]
        c = flat - flat.mean(axis=0)
        return cls(((x0, y0, x1, y1), mask, c, float(np.sqrt((c * c).sum()))), patch.copy())


def register(reference: tuple, feat: np.ndarray, centre: tuple[int, int] = (0, 0), radius: int = SEARCH,
             match: Callable[..., tuple[np.ndarray, int, int]] | None = None) -> dict[str, Any]:
    """The shift (dx, dy) that lays `feat` over the reference: the engine's integer match (`auto_motion._match_costs`,
    `auto_motion._best`) and a parabola through the cost at the best offset and its neighbours on each axis. `match`,
    where given, is `auto_motion._matcher(reference)`, kept by a caller that reads many pictures against it."""
    (x, y, _, _), _, _, _ = reference
    costs, xs, ys = (match if match is not None else auto_motion._matcher(reference))(feat, centre, radius)
    iy, ix = auto_motion._best(costs, xs, ys, reference, centre)
    dx, dy = x - (xs + ix), y - (ys + iy)

    def vertex(a: float, b: float, c: float) -> float:
        den = a - 2 * b + c
        return 0.5 * (a - c) / den if den > 1e-12 else 0.0

    fx = vertex(costs[iy, ix - 1], costs[iy, ix], costs[iy, ix + 1]) if 0 < ix < costs.shape[1] - 1 else 0.0
    fy = vertex(costs[iy - 1, ix], costs[iy, ix], costs[iy + 1, ix]) if 0 < iy < costs.shape[0] - 1 else 0.0
    return {"sx": float(dx - fx), "sy": float(dy - fy), "cost": float(costs[iy, ix]),
            "edge": bool(abs(dx - centre[0]) == radius or abs(dy - centre[1]) == radius)}


def _local(reference: tuple, pad: int) -> tuple[tuple, tuple[int, int], tuple[int, int]]:
    (x0, y0, x1, y1), mask, c, norm = reference
    return ((pad, pad, pad + x1 - x0, pad + y1 - y0), mask, c, norm), (x0 - pad, y0 - pad), (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad)


def matrix(a: dict[str, float], geom: Geometry) -> np.ndarray:
    return sim(a["tx"], a["ty"], a["s"], a["th"], geom.mouth)


def fit(band: Band, feat: np.ndarray, geom: Geometry) -> dict[str, Any]:
    """The anchor of one frame, read on the band (module docstring "Anchor")."""
    first = register(band.reference, feat)
    search = SEARCH
    if first["edge"]:
        search = 2 * SEARCH
        first = register(band.reference, feat, (0, 0), search)
    loc, origin, size = _local(band.reference, FINE + 1)
    fine = auto_motion._matcher(loc)  # every size and tilt is read against it: its transforms are made once
    c = np.asarray(geom.mouth)

    def at(t: np.ndarray, s: float, th: float) -> tuple[float, np.ndarray, bool]:
        m = sim(t[0], t[1], s, th, geom.mouth)
        wf = warp(feat, m @ shift(*origin), size)
        rr = register(loc, wf.astype(np.float64), (0, 0), FINE, fine)
        return rr["cost"], m @ shift(-rr["sx"], -rr["sy"]), rr["edge"]

    def best_on(t: np.ndarray, scales: np.ndarray, thetas: np.ndarray) -> dict[str, Any]:
        costs = np.array([[at(t, s, th)[0] for th in thetas] for s in scales])
        i, j = np.unravel_index(np.argmin(costs), costs.shape)

        def vertex(a: float, b: float, c: float) -> float:
            den = a - 2 * b + c
            return float(np.clip(0.5 * (a - c) / den, -0.5, 0.5)) if den > 1e-12 else 0.0

        ds = vertex(costs[i - 1, j], costs[i, j], costs[i + 1, j]) if 0 < i < len(scales) - 1 else 0.0
        dt = vertex(costs[i, j - 1], costs[i, j], costs[i, j + 1]) if 0 < j < len(thetas) - 1 else 0.0
        s = float(scales[i] + ds * (scales[1] - scales[0]))
        th = float(thetas[j] + dt * (thetas[1] - thetas[0]))
        cost, m, edge = at(t, s, th)
        tt = m[:2, 2] - c + m[:2, :2] @ c
        return {"tx": float(tt[0]), "ty": float(tt[1]), "s": s, "th": th, "cost": float(cost), "fine_edge": bool(edge),
                "grid_edge": bool(i in (0, len(scales) - 1) or j in (0, len(thetas) - 1))}

    t0 = np.array([-first["sx"], -first["sy"]])
    rec = best_on(t0, SCALES, THETAS)
    rec["wide_grid"] = False
    if rec["grid_edge"]:
        rec = {**best_on(t0, WIDE_SCALES, WIDE_THETAS), "wide_grid": True}
    # The place a size and tilt need can lie further from the first, place-only reading than FINE: the place-only
    # match settles where the band's tilted edges agree best, not where the tilt turns about. The grid is read again
    # round the place, size and tilt it found until the place stays.
    def moved(r: dict[str, Any], t: np.ndarray) -> bool:
        return math.hypot(r["tx"] - t[0], r["ty"] - t[1]) > FINE - 1

    t, rounds, again = t0, 0, rec["fine_edge"] or moved(rec, t0)
    while again and rounds < RECENTRE_ROUNDS:
        t = np.array([rec["tx"], rec["ty"]])
        local = best_on(t, rec["s"] + np.array([-1.0, 0.0, 1.0]) * (SCALES[1] - SCALES[0]),
                        rec["th"] + np.array([-1.0, 0.0, 1.0]) * (THETAS[1] - THETAS[0]))
        again = local["fine_edge"] or local["grid_edge"] or moved(local, t)
        rec = {**local, "grid_edge": rec["grid_edge"], "wide_grid": rec["wide_grid"]}
        rounds += 1
    rec["recentred"] = rounds
    rec["search_px"] = search
    rec["edge"] = bool(first["edge"])
    rec["place_only"] = [float(t0[0]), float(t0[1])]
    return rec


def place_on(ring: Band, feat: np.ndarray, a: dict[str, Any], geom: Geometry) -> dict[str, Any]:
    """Anchor `a` with its place read again on the ring round the mouth (`Band.ring`), its size and tilt kept: the
    frame laid through `a` is matched to the ring within RING_SEARCH px (`register`), and within twice that where the
    best sits on the search's rim."""
    m = matrix(a, geom)
    for radius in (RING_SEARCH, 2 * RING_SEARCH):
        loc, origin, size = _local(ring.reference, radius + 1)
        rr = register(loc, warp(feat, m @ shift(*origin), size).astype(np.float64), (0, 0), radius)
        if not rr["edge"]:
            break
    moved = m @ shift(-rr["sx"], -rr["sy"])
    c = np.asarray(geom.mouth)
    t = moved[:2, 2] - c + moved[:2, :2] @ c
    return {**a, "tx": float(t[0]), "ty": float(t[1])}


def on_ring(used: list[dict[str, Any]], placed: list[dict[str, Any] | None], cyclic: bool) -> list[dict[str, Any]]:
    """Each anchor with the place read on the ring round the mouth: `placed` is `place_on`'s anchor where the frame's
    own mouth is closed and None where it is not (an open mouth moves the jaw and the skin round it). A read frame's
    move from band to ring is checked as `hampel` checks an anchor, against the moves read among the HAMPEL_WINDOW
    frames round it (three or more), and a misreading takes their median. A frame read on the ring takes its move; any
    other takes the moves of the read frames round it, linear between the nearest on each side (wrapping on a loop),
    the nearest one's past the first or the last. A clip with no frame read keeps its places. Each anchor records its
    move (`ring`) and whether it was read (`ring_read`)."""
    n, h = len(used), HAMPEL_WINDOW // 2
    read_moves = {k: np.array([p["tx"] - used[k]["tx"], p["ty"] - used[k]["ty"]]) for k, p in enumerate(placed) if p is not None}
    moves = {}
    for k, d in read_moves.items():
        near = [read_moves[j % n] for j in range(k - h, k + h + 1) if (cyclic or 0 <= j < n) and j % n in read_moves]
        if len(near) >= 3:
            v = np.stack(near)
            med = np.median(v, 0)
            mad = 1.4826 * np.median(np.abs(v - med), 0)
            floor = np.array([HAMPEL_FLOOR["tx"], HAMPEL_FLOOR["ty"]])
            d = med if (np.abs(d - med) > np.maximum(3 * mad, floor)).any() else d
        moves[k] = d
    read = sorted(moves)
    out = []
    for k, a in enumerate(used):
        if k in moves or not read:
            d = moves.get(k, np.zeros(2))
        else:
            before = [j for j in read if j < k] or ([j - n for j in read] if cyclic else [])
            after = [j for j in read if j > k] or ([j + n for j in read] if cyclic else [])
            if before and after:
                j0, j1 = max(before), min(after)
                t = (k - j0) / (j1 - j0)
                d = (1 - t) * moves[j0 % n] + t * moves[j1 % n]
            else:
                d = moves[(max(before) if before else min(after)) % n]
        out.append({**a, "tx": a["tx"] + float(d[0]), "ty": a["ty"] + float(d[1]),
                    "ring": [round(float(d[0]), 3), round(float(d[1]), 3)], "ring_read": k in moves})
    return out


def band_residual(band: Band, feat: np.ndarray, m: np.ndarray) -> float:
    """Mean |frame - reference| (0..255, colour matched by the median) on the band where both are opaque, the frame
    read through `m`: how well the anchor lays the reference's face on the frame."""
    (x0, y0, x1, y1), mask, _, _ = band.reference
    w = warp(feat, m @ shift(x0, y0), (x1 - x0, y1 - y0))
    r = band.features
    keep = mask & (w[..., 3] > 0.99) & (r[..., 3] > 0.99)
    d = (w[keep][:, :3] - r[keep][:, :3]) * 255
    return float(np.abs(d - np.median(d, 0)).mean()) if keep.any() else float("nan")


def hampel(rows: list[dict[str, Any]], cyclic: bool) -> list[dict[str, Any]]:
    """Each anchor number against the median of HAMPEL_WINDOW frames round it (wrapping on a loop): a reading further
    from it than three scaled MADs (and than HAMPEL_FLOOR) is a misreading, and the frame takes the window's medians
    for all four numbers (its place was read at the misread size and tilt). Every other reading stays as read: a head's
    real bounce is not flattened."""
    n, h = len(rows), HAMPEL_WINDOW // 2
    out = []
    for t in range(n):
        idx = [(t + k) % n for k in range(-h, h + 1)] if cyclic else list(range(max(0, t - h), min(n, t + h + 1)))
        med, bad = {}, False
        for k in ("tx", "ty", "s", "th"):
            v = np.array([rows[q][k] for q in idx])
            med[k] = float(np.median(v))
            mad = 1.4826 * float(np.median(np.abs(v - med[k])))
            bad |= abs(rows[t][k] - med[k]) > max(3 * mad, HAMPEL_FLOOR[k])
        out.append({**(med if bad else {k: float(rows[t][k]) for k in ("tx", "ty", "s", "th")}), "misread": bool(bad)})
    return out


# --- the mouth ----------------------------------------------------------------------------------------------------

def mouth_line(arr: np.ndarray, geom: Geometry) -> np.ndarray | None:
    """The mouth of a picture in reference coordinates, on `Geometry.mouth_box`: the dark line or opening nearest the
    mouth point (DROP under the box's skin, off the headset's grey). None where there is none."""
    x0, y0, x1, y1 = geom.mouth_box
    a = arr[y0:y1, x0:x1]
    lum = luma(a)
    solid = a[..., 3] > 200
    if not solid.any():
        return None
    skin = float(np.median(lum[solid]))
    grey = (a[..., :3].max(-1) - a[..., :3].min(-1) < DARK_CHROMA) & (lum < 120)
    m = (lum < skin - DROP) & solid & ~parts_mod._dilate(grey, 2)
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    cx, cy = geom.mouth[0] - x0, geom.mouth[1] - y0
    near = m & ((xx - cx) ** 2 / 30 ** 2 + (yy - cy) ** 2 / 16 ** 2 < 1)
    return reach(m, near) if near.any() else None


def mouth_reading(arr: np.ndarray, geom: Geometry) -> dict[str, Any] | None:
    """How a mouth in reference coordinates smiles (`mouth_line`), its corners its leftmost and rightmost pixels.
    `lift` is how far its top edge in the middle third sits below the chord between the corners (px; + the corners
    turned up: a smile, - turned down: an O or a pout); `height` the mean of its bottom less top there, plus one. None
    where no mouth line is found."""
    g = mouth_line(arr, geom)
    if g is None:
        return None
    ys, xs = np.nonzero(g)
    if len(xs) < 6:
        return None
    left, right = int(xs.min()), int(xs.max())
    width = right - left + 1
    if width < 8:
        return None
    yl, yr = float(ys[xs <= left + 1].mean()), float(ys[xs >= right - 1].mean())
    cols = [x for x in range(int(left + width / 3), int(right - width / 3) + 1) if (xs == x).any()]
    if not cols:
        return None
    top = np.array([ys[xs == x].min() for x in cols], float)
    bottom = np.array([ys[xs == x].max() for x in cols], float)
    chord = yl + (np.array(cols, float) - left) / max(right - left, 1) * (yr - yl)
    return {"lift": round(float((top - chord).mean()), 2), "width": int(width),
            "height": round(float(bottom.mean() - top.mean() + 1), 2), "px": int(g.sum())}


def expression(reading: dict[str, Any] | None) -> str | None:
    """A closed mouth's expression: 'smile' with SMILE_FROM px of lift or more, 'neutral' under it; None for an open
    mouth or none read. One line, not a gap between two: a closed frame left out between two of the same expression
    would cut a run of that expression into two turns."""
    if reading is None or reading["height"] > CLOSED_HEIGHT:
        return None
    return "smile" if reading["lift"] >= SMILE_FROM else "neutral"


def open_area(arr: np.ndarray, reference: np.ndarray, geom: Geometry) -> int:
    """How big a mouth reads: pixels round the mouth (`Geometry.open_zone`) that differ from the reference's closed
    mouth by more than OPEN_LUMA."""
    x0, y0, x1, y1 = geom.open_zone
    return int((np.abs(luma(arr[y0:y1, x0:x1]) - luma(reference[y0:y1, x0:x1])) > OPEN_LUMA).sum())


# --- laying -------------------------------------------------------------------------------------------------------

@dataclass
class Sprite:
    """A talking frame's lower face at the clip's own resolution: `image` (RGBA, alpha = the weight less the holes)
    and `to_ref`, the map from its pixels to the reference's."""

    image: Image.Image
    to_ref: np.ndarray


def colour_offset(reference: np.ndarray, picture: np.ndarray, w: np.ndarray) -> np.ndarray:
    """The median of reference - picture over the feather (0.02 < w < 0.98) where both are opaque."""
    band = (w > 0.02) & (w < 0.98) & (picture[..., 3] > 254) & (reference[..., 3] > 254)
    return np.median(reference[band][:, :3] - picture[band][:, :3], 0) if band.any() else np.zeros(3)


def face_sprite(native: Image.Image, to_native: np.ndarray, w0: np.ndarray, reference: np.ndarray) -> tuple[Sprite, dict[str, Any]]:
    """The sprite of one talking frame. `to_native` maps the reference's pixels to the frame's own (the frame's
    anchor, then its placement)."""
    nat = np.asarray(native.convert("RGBA"), np.float32)
    size = (w0.shape[1], w0.shape[0])
    face_ref = warp(nat, to_native, size)
    hole = np.maximum(dark_reach(face_ref, w0), line_band(face_ref, w0))
    w_face = w0 * (1 - hole) * parts_mod._erode(w0 > 0, EDGE)
    off = colour_offset(reference, face_ref, w_face)
    back = np.linalg.inv(to_native)
    w_nat = warp(w_face, back, native.size)
    w_nat[w_nat < 1.0 / 512] = 0
    ys, xs = np.nonzero(w_nat > 0)
    x0, y0 = max(int(xs.min()) - 2, 0), max(int(ys.min()) - 2, 0)
    x1, y1 = min(int(xs.max()) + 3, native.size[0]), min(int(ys.max()) + 3, native.size[1])
    crop = nat[y0:y1, x0:x1].copy()
    crop[..., :3] = np.clip(crop[..., :3] + off, 0, 255)
    crop[..., 3] *= w_nat[y0:y1, x0:x1]
    image = Image.fromarray(np.clip(crop + 0.5, 0, 255).astype(np.uint8), "RGBA")
    return Sprite(image, back @ shift(x0, y0)), {"colour_offset": [round(float(v), 2) for v in off],
                                                 "hole_px": int((hole > 0.5).sum()), "origin": [x0, y0], "size": [x1 - x0, y1 - y0]}


def body_keep(body: np.ndarray, to_body: np.ndarray, w0: np.ndarray, reference: np.ndarray, geom: Geometry) -> tuple[np.ndarray, dict[str, Any]]:
    """What of a body frame stays on top of the layer (module docstring "Keep"), in the frame's own pixels, and how
    much of it each part is. `to_body` maps the reference's pixels to the frame's (its anchor and placement)."""
    size = (body.shape[1], body.shape[0])
    back = np.linalg.inv(to_body)
    w_ell = warp(w0, back, size)
    w_ell[w_ell < 1e-4] = 0
    body_ref = warp(body, to_body, (w0.shape[1], w0.shape[0]))
    mouth_ref = own_mouth(body_ref, geom)
    mouth = warp(mouth_ref.astype(np.float32), back, size) if mouth_ref.any() else np.zeros(w_ell.shape, np.float32)
    held = dark_reach(body, w_ell) * (1 - mouth)
    lines = line_band(body, w_ell)
    hand_ref = hand_reach(body_ref, reference, w0, geom, mouth_ref)
    hand = warp(hand_ref, back, size) if hand_ref.any() else np.zeros(w_ell.shape, np.float32)
    keep = np.maximum(np.maximum(held, lines), hand) * (w_ell > 0)
    return keep.astype(np.float32), {"dark_px": int((held > 0.5).sum()), "line_px": int((lines > 0.5).sum()),
                                     "hand_px": int((hand > 0.5).sum())}


def keep_overlay(body: np.ndarray, keep: np.ndarray) -> tuple[Image.Image, tuple[int, int]] | None:
    """The body's own pixels where they stay on top, as a picture to draw over the layer, and where it goes."""
    alpha = body[..., 3] * keep
    ys, xs = np.nonzero(np.clip(alpha + 0.5, 0, 255) >= 1)
    if not len(xs):
        return None
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    crop = body[y0:y1, x0:x1].copy()
    crop[..., 3] = alpha[y0:y1, x0:x1]
    return Image.fromarray(np.clip(crop + 0.5, 0, 255).astype(np.uint8), "RGBA"), (x0, y0)


def over(dst: np.ndarray, src: np.ndarray, at: tuple[int, int] = (0, 0)) -> np.ndarray:
    """Source-over (straight alpha) of `src` on `dst` with its corner at `at`; float RGBA 0..255, in place. Where
    `src` is clear, `dst` is left as it is, its colour under no coverage included."""
    x0, y0 = at
    h, w = src.shape[:2]
    d = dst[y0:y0 + h, x0:x0 + w]
    sa, da = src[..., 3:4] / 255, d[..., 3:4] / 255
    a = sa + da * (1 - sa)
    rgb = np.where(a > 0, (src[..., :3] * sa + d[..., :3] * da * (1 - sa)) / np.maximum(a, 1e-6), 0)
    covered = sa > 0
    d[..., :3] = np.where(covered, rgb, d[..., :3])
    d[..., 3:4] = np.where(covered, a * 255, d[..., 3:4])
    return dst


def placed_sprite(sprite: Sprite, to_body: np.ndarray, size: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]]:
    """The sprite on a body frame's pixels, and where its box goes: one `resample.transform_cell` through
    to_body . to_ref, after one `resample.resize_cell` where it is laid smaller than SHRINK_FROM."""
    m = to_body @ sprite.to_ref
    k = math.sqrt(abs(float(np.linalg.det(m[:2, :2]))))
    image = sprite.image
    if k < SHRINK_FROM:
        w, h = image.size
        nw, nh = max(1, round(w * k)), max(1, round(h * k))
        image = resample.resize_cell(image, (nw, nh))
        m = m @ np.diag([w / nw, h / nh, 1.0])
    w, h = image.size
    corners = m @ np.array([[-1, w, -1, w], [-1, -1, h, h], [1, 1, 1, 1]], float)
    x0, y0 = max(int(math.floor(corners[0].min())) - 1, 0), max(int(math.floor(corners[1].min())) - 1, 0)
    x1, y1 = min(int(math.ceil(corners[0].max())) + 2, size[0]), min(int(math.ceil(corners[1].max())) + 2, size[1])
    return resample.transform_cell(image, (x1 - x0, y1 - y0), pil_data(np.linalg.inv(m) @ shift(x0, y0))), (x0, y0)


def lay(body: Image.Image, sprite: Sprite | None, to_body: np.ndarray, keep: tuple[Image.Image, tuple[int, int]] | None) -> Image.Image:
    """A body frame with the face layer: the sprite over it, then what of the body stays on top over that. No sprite
    is the body frame as it is."""
    if sprite is None:
        return body.convert("RGBA")
    out = np.asarray(body.convert("RGBA"), np.float32).copy()
    placed, at = placed_sprite(sprite, to_body, body.size)
    over(out, np.asarray(placed, np.float32), at)
    if keep is not None:
        over(out, np.asarray(keep[0], np.float32), keep[1])
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA")
