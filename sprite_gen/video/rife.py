# SPDX-License-Identifier: Apache-2.0
"""RIFE in-betweens for RGBA sprite frames, through the external `rife-ncnn-vulkan` binary.

Where it runs, what it costs and why this build: docs/loop-repair.md section 1. RIFE reads
three colour channels and no alpha, so a frame is interpolated as two images — its colour and
its coverage as a grey image — and put back together. The two are two runs of the flow, and
where legs cross they do not agree: the coverage says body where the colour run still carries
what lay around the body. So around the body the colour image holds the body's own colour,
pushed out from inside its outline (`bleed`): a disagreement there reads as the body, not as the
black a frame premultiplied over black put under it (2.24 and before: a black smear between
crossing legs). Inside the coverage the colour is the frame's own, outline included.
`smear` measures what a made frame has that neither neighbour has, and the outline it lost where
the flow failed and melted a limb into the fill; `ghost` reads a band of part coverage on a frame
alone, filmed or made; `crossfade` reads two drawings cross-faded inside a whole silhouette, a
reading to look at, not a fault (docs/loop-repair.md section 4).

The binary's own CPU path (`-g -1`) returns a wrong frame with rife-v4.6 on both macOS and
Linux (measured 2026-10-03: mean error 39 against 3.3 through Vulkan), so it is never passed;
a machine without a GPU runs the Vulkan path on Mesa's llvmpipe, which gives the GPU's frame.

The binary is found by SPRITE_GEN_RIFE, then on PATH, then where `sprite-gen rife install`
puts it (`install_root()`, docs/loop-repair.md section 1). A RIFE that cannot be found is
`RifeNotInstalled`, which a caller may answer by cutting the loop as filmed with a warning; a
RIFE that is found and then fails is `RifeUnavailable` and always an error.

The interpolator is a plain callable `(a, b, t) -> frame`, so a caller can be handed a stand-in
(`Interpolate`), and a test needs no binary.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter

from sprite_gen._deps import np

BINARY = "rife-ncnn-vulkan"
MODEL = "rife-v4.6"
RELEASE = "20221029"
# Coverage RIFE leaves under this is its own blur, not body: dropped so no halo is invented.
ALPHA_FLOOR = 2 / 255
# The colour pushed out around the body is read this far inside its solid edge (a fraction of the
# body's height, at least a pixel): past a drawn outline, so a disagreement reads as the body's
# fill, not its outline: 0.008 is 6 px on an 800 px body, past an outline a few pixels wide.
BLEED_DEPTH = 0.008
# `smear`: a pixel is dark under this luma (0..1), and part-covered between these alphas.
DARK_LUMA = 70 / 255
PARTIAL_ALPHA = (0.1, 0.9)
# `smear`'s outline: a pixel of the coverage's edge (alpha from PARTIAL_ALPHA's floor) is outlined
# when a dark solid pixel lies within this many pixels of it. A drawn outline sits on the edge, under
# an antialiased pixel or two, whatever the frame's size.
OUTLINE_REACH = 2
# `ghost`: part-covered pixels count where they make a region at least this many pixels thick. An
# antialiased edge or a keyed strand of hair is a pixel or two wide and erodes away; the band a video
# model leaves between two drawings, or a flow carries from it, does not.
GHOST_THICK = 5
# `crossfade`: an edge's local range is read over this many pixels, wider than RIFE's blur, so a
# softened edge keeps its height and a cross-faded one does not; strong edges are counted in windows
# this share of the body's height; a window that kept under CROSSFADE_KEEP of its source frames'
# strong edges lost them.
CROSSFADE_RANGE = 7
CROSSFADE_WINDOW = 0.08
CROSSFADE_KEEP = 0.6
CALL_TIMEOUT_SECONDS = 120

Interpolate = Callable[[Image.Image, Image.Image, float], Image.Image]

# The one line that installs it, quoted wherever a missing RIFE is reported.
INSTALL_COMMAND = "sprite-gen rife install"
INSTALL_HINT = (
    f"run `{INSTALL_COMMAND}` (rife-ncnn-vulkan {RELEASE} with the model {MODEL}, sha256-checked, into the "
    f"user data directory), or put {BINARY} on PATH or set SPRITE_GEN_RIFE to the binary — the model {MODEL}/ "
    f"ships beside it (SPRITE_GEN_RIFE_MODEL overrides); on Linux without a GPU also `libvulkan1 mesa-vulkan-drivers`; "
    f"see docs/loop-repair.md"
)


class RifeUnavailable(RuntimeError):
    """No usable RIFE: not installed, or found and failed. The message names what went wrong."""


class RifeNotInstalled(RifeUnavailable):
    """No RIFE binary or model where the engine looks. The message carries the install line."""


def platform_release() -> str:
    """The release zip's platform name for this machine (`macos`, `ubuntu`, `windows`), or '' when it ships none."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "ubuntu"
    if sys.platform == "win32":
        return "windows"
    return ""


def data_dir() -> Path:
    """sprite-gen's user data directory: SPRITE_GEN_DATA_DIR, else %LOCALAPPDATA%\\sprite-gen on
    Windows, else $XDG_DATA_HOME/sprite-gen or ~/.local/share/sprite-gen."""
    named = os.environ.get("SPRITE_GEN_DATA_DIR")
    if named:
        return Path(named).expanduser()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "sprite-gen"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share").expanduser() / "sprite-gen"


def install_root() -> Path:
    """Where `sprite-gen rife install` puts RIFE by default, and the last place `locate()` looks."""
    return data_dir() / "rife"


def installed_binary(root: Path | None = None, platform: str | None = None) -> Path:
    """The binary inside an install root: `<root>/rife-ncnn-vulkan-<release>-<platform>/rife-ncnn-vulkan[.exe]`."""
    platform = platform or platform_release()
    return (root or install_root()) / f"{BINARY}-{RELEASE}-{platform}" / (BINARY + (".exe" if platform == "windows" else ""))


def locate() -> tuple[Path, Path]:
    """(binary, model directory): the binary from SPRITE_GEN_RIFE, else PATH, else `install_root()`;
    the model from SPRITE_GEN_RIFE_MODEL, else beside the binary."""
    named = os.environ.get("SPRITE_GEN_RIFE")
    installed = installed_binary() if platform_release() else None
    found = named or shutil.which(BINARY) or (str(installed) if installed and installed.is_file() else None)
    if not found:
        raise RifeNotInstalled(f"{BINARY} not found (SPRITE_GEN_RIFE is not set, it is not on PATH, and "
                               f"{installed or install_root()} does not exist); {INSTALL_HINT}")
    binary = Path(found).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RifeNotInstalled(f"{binary} is not an executable file; {INSTALL_HINT}")
    binary = binary.resolve()
    model = Path(os.environ.get("SPRITE_GEN_RIFE_MODEL") or binary.parent / MODEL).expanduser()
    if not (model / "flownet.param").is_file() or not (model / "flownet.bin").is_file():
        raise RifeNotInstalled(f"RIFE model {model} has no flownet.param / flownet.bin; {INSTALL_HINT}")
    return binary, model


def _call(binary: Path, model: Path, a: Image.Image, b: Image.Image, t: float, tmp: Path) -> Image.Image:
    a.save(tmp / "a.png")
    b.save(tmp / "b.png")
    out = tmp / "o.png"
    out.unlink(missing_ok=True)
    proc = subprocess.run(
        [str(binary), "-0", str(tmp / "a.png"), "-1", str(tmp / "b.png"), "-o", str(out), "-s", f"{t:.4f}", "-m", str(model)],
        capture_output=True, text=True, timeout=CALL_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0 or not out.is_file():
        raise RifeUnavailable(f"{binary.name} failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[-300:]}")
    with Image.open(out) as im:
        return im.convert("RGB")


def _push_pull(rgb: np.ndarray, known: np.ndarray) -> np.ndarray:
    """`rgb` with every pixel where `known` is 0 filled by the weighted colour around it, coarse to
    fine (each level averages 2x2 blocks of the known colour; an unknown pixel takes its block's)."""
    if known.min() > 0 or min(known.shape) <= 1:
        return rgb
    h, w = known.shape
    pad = ((0, h % 2), (0, w % 2))
    weighted, weight = np.pad(rgb * known[..., None], pad + ((0, 0),)), np.pad(known, pad)
    wsum, csum = (x[0::2, 0::2] + x[1::2, 0::2] + x[0::2, 1::2] + x[1::2, 1::2] for x in (weight, weighted))
    coarse = _push_pull(np.where(wsum[..., None] > 0, csum / np.maximum(wsum[..., None], 1e-9), 0.0), np.minimum(wsum, 1.0))
    up = coarse.repeat(2, axis=0).repeat(2, axis=1)[:h, :w]
    return np.where(known[..., None] > 0, rgb, up)


def bleed(frame: np.ndarray) -> np.ndarray:
    """The colour image of an RGBA frame (0..1) RIFE is given: the frame's own colour wherever it
    has coverage, and around it the colour from BLEED_DEPTH inside its solid edge, pushed out."""
    alpha = frame[..., 3]
    solid = alpha >= 0.5
    rows = np.nonzero(solid.any(axis=1))[0]
    if rows.size == 0:
        return np.where(alpha[..., None] > 0, frame[..., :3], 0.0)
    depth = max(1, round((rows[-1] - rows[0] + 1) * BLEED_DEPTH))
    core = np.asarray(Image.fromarray(np.uint8(solid) * 255).filter(ImageFilter.MinFilter(2 * depth + 1))) > 0
    if not core.any():  # a body thinner than its outline: read at its edge
        core = solid
    fill = _push_pull(frame[..., :3], core.astype(np.float32))
    return np.where(alpha[..., None] > 0, frame[..., :3], fill)


def between(a: Image.Image, b: Image.Image, t: float, *, binary: Path, model: Path, tmp: Path) -> Image.Image:
    """The RGBA frame at fraction `t` (0..1) of the way from `a` to `b`."""
    if a.size != b.size:
        raise ValueError(f"rife: frames differ in size ({a.size} vs {b.size})")
    fa, fb = (np.asarray(f.convert("RGBA"), dtype=np.float32) / 255.0 for f in (a, b))
    colour = [Image.fromarray(np.uint8(np.clip(bleed(x), 0, 1) * 255 + 0.5), "RGB") for x in (fa, fb)]
    cov = [Image.fromarray(np.uint8(x[..., 3] * 255 + 0.5), "L").convert("RGB") for x in (fa, fb)]
    rgb = np.asarray(_call(binary, model, colour[0], colour[1], t, tmp), dtype=np.float32) / 255.0
    alpha = np.asarray(_call(binary, model, cov[0], cov[1], t, tmp).convert("L"), dtype=np.float32) / 255.0
    alpha = np.where(alpha < ALPHA_FLOOR, 0.0, alpha)
    out = np.dstack([np.where(alpha[..., None] > 0, rgb, 0.0), alpha])
    return Image.fromarray(np.uint8(np.clip(out, 0, 1) * 255 + 0.5), "RGBA")


def _mask(mask: np.ndarray, size: int, op: type) -> np.ndarray:
    return np.asarray(Image.fromarray(np.uint8(mask) * 255).filter(op(size))) > 0


def _smear_counts(frame: Image.Image) -> tuple[int, int, int, int, int]:
    """(dark pixels inside the solid body, one pixel in from its edge; solid pixels; part-covered
    pixels; pixels on the coverage's edge; those of them with no outline within OUTLINE_REACH)."""
    x = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    solid = x[..., 3] >= 0.5
    inner = _mask(solid, 3, ImageFilter.MinFilter)
    luma = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
    lo, hi = PARTIAL_ALPHA
    covered = x[..., 3] >= lo
    edge = covered & ~_mask(covered, 3, ImageFilter.MinFilter)
    outlined = _mask(solid & (luma < DARK_LUMA), 2 * OUTLINE_REACH + 1, ImageFilter.MaxFilter)
    return (int((inner & (luma < DARK_LUMA)).sum()), int(solid.sum()), int(((x[..., 3] > lo) & (x[..., 3] < hi)).sum()),
            int(edge.sum()), int((edge & ~outlined).sum()))


def smear(made: Image.Image, a: Image.Image, b: Image.Image) -> dict[str, float]:
    """What a made frame has that neither of its two neighbours has: `dark_excess`, dark pixels
    inside the body beyond the darker neighbour's count (a black smear raises it; a moved dark
    part — a hat, a watch — keeps its count), and `partial_excess`, part-covered pixels beyond the
    more ragged neighbour's, both as fractions of its solid pixels; and `outline_loss`, the edge of
    its coverage that has no outline beyond the less outlined neighbour's, as a fraction of its edge
    (where legs crossing too far for the flow melt into one shape of fill, or a limb is left a pale
    ghost, the outline is gone; a frame drawn without outlines loses none against neighbours that
    have none). Zero or less: nothing added, no outline lost."""
    dm, sm, pm, em, um = _smear_counts(made)
    (da, _, pa, _, ua), (db, _, pb, _, ub) = _smear_counts(a), _smear_counts(b)
    solid = max(1, sm)
    return {"dark_excess": round((dm - max(da, db)) / solid, 5), "partial_excess": round((pm - max(pa, pb)) / solid, 5),
            "outline_loss": round((um - max(ua, ub)) / max(1, em), 5)}


def ghost(frame: Image.Image) -> float:
    """Part-covered pixels (PARTIAL_ALPHA) inside regions at least GHOST_THICK pixels thick, as a
    fraction of the frame's solid pixels: a ghost band, read on the frame's own coverage and not
    against its neighbours, so a band a filmed frame carries reads the same in a frame made beside
    it (docs/loop-repair.md section 4)."""
    alpha = np.asarray(frame.convert("RGBA").getchannel("A"), dtype=np.float32) / 255.0
    lo, hi = PARTIAL_ALPHA
    thick = _mask((alpha > lo) & (alpha < hi), GHOST_THICK, ImageFilter.MinFilter)
    return round(float(thick.sum()) / max(1, int((alpha >= 0.5).sum())), 5)


def _local_range(x: np.ndarray) -> np.ndarray:
    """Per pixel, the largest over the channels of max - min within CROSSFADE_RANGE pixels."""
    out = np.zeros(x.shape[:2], dtype=np.float32)
    for c in range(x.shape[2]):
        channel = Image.fromarray(np.uint8(np.clip(x[..., c], 0, 1) * 255 + 0.5), "L")
        hi = np.asarray(channel.filter(ImageFilter.MaxFilter(CROSSFADE_RANGE)), dtype=np.float32)
        lo = np.asarray(channel.filter(ImageFilter.MinFilter(CROSSFADE_RANGE)), dtype=np.float32)
        out = np.maximum(out, (hi - lo) / 255.0)
    return out


def crossfade(made: Image.Image, a: Image.Image, b: Image.Image, t: float) -> float:
    """A cross-fade of two drawings inside the silhouette, the coverage whole: the near and far boots
    of `a` and `b` both shown at part strength where a flow found nothing to follow. Read where two
    things meet in one window of the frame where `a` and `b` differ: pixels of the made frame's solid
    body at the plain blend of `a` and `b` at its own fraction `t` and well away from each (in patches
    GHOST_THICK pixels thick, past an edge's soft rim), and the made frame keeping under CROSSFADE_KEEP
    of the source frames' strong edges there — a blend shows each edge at part height, a flow that
    moved the part shows it whole, only softer. The strong-edge pixels lost in such windows, as a
    fraction of the made frame's solid pixels; 0 where it shows no blend. Relative to the source
    frames' own edges, so a palette's lightness does not decide it (docs/loop-repair.md section 4)."""
    m, fa, fb = (np.asarray(f.convert("RGBA"), dtype=np.float32) / 255.0 for f in (made, a, b))
    pm, pa, pb = (np.dstack([x[..., :3] * x[..., 3:], x[..., 3:]]) for x in (m, fa, fb))

    def dist(x: np.ndarray, y: np.ndarray) -> np.ndarray:  # premultiplied RGBA, 0..1
        return np.sqrt(((x - y) ** 2).sum(-1) / 4.0)

    d = dist(pa, pb)
    solid = m[..., 3] >= 0.5
    blend = (solid & (d > 0.15) & (dist(pm, (1 - t) * pa + t * pb) < 0.35 * d)
             & (dist(pm, pa) > 0.25 * d) & (dist(pm, pb) > 0.25 * d))
    blend &= _mask(_mask(blend, GHOST_THICK, ImageFilter.MinFilter), GHOST_THICK, ImageFilter.MaxFilter)
    if not blend.any():
        return 0.0
    rows = np.nonzero((np.maximum.reduce([fa[..., 3], fb[..., 3], m[..., 3]]) >= 0.5).any(axis=1))[0]
    w = max(16, round(CROSSFADE_WINDOW * (rows[-1] - rows[0] + 1)))
    changed = _mask(d > 0.08, CROSSFADE_RANGE, ImageFilter.MaxFilter)
    ys, xs = np.nonzero(blend)
    y0, y1, x0, x1 = max(0, ys.min() - w), ys.max() + w, max(0, xs.min() - w), xs.max() + w
    rm, ra, rb = (np.zeros(d.shape, np.float32) for _ in range(3))
    for into, x in ((rm, pm), (ra, pa), (rb, pb)):
        into[y0:y1, x0:x1] = _local_range(x[y0:y1, x0:x1])
    lost, step = 0.0, w // 2
    for y in range((y0 // step) * step, y1, step):
        for x in range((x0 // step) * step, x1, step):
            win = (slice(y, y + w), slice(x, x + w))
            near = changed[win]
            if blend[win].sum() < 0.01 * w * w or near.sum() < 0.1 * w * w:
                continue
            strong = max(np.percentile(ra[win][near], 95), np.percentile(rb[win][near], 95))
            if strong < 0.15:  # no edge to keep here
                continue
            ka, kb, km = (int((r[win][near] > 0.5 * strong).sum()) for r in (ra, rb, rm))
            kept = (ka + kb) / 2
            if kept >= 0.02 * w * w and km < CROSSFADE_KEEP * kept:
                lost += kept - km
    return round(lost / max(1, int(solid.sum())), 5)


class Rife:
    """A located RIFE as an `Interpolate` callable. Counts the frames it made (`made`)."""

    def __init__(self, binary: Path | None = None, model: Path | None = None):
        if binary is None or model is None:
            binary, model = locate()
        self.binary, self.model = binary, model
        self.made = 0

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        with tempfile.TemporaryDirectory(prefix="sprite-gen-rife-") as td:
            frame = between(a, b, t, binary=self.binary, model=self.model, tmp=Path(td))
        self.made += 1
        return frame

    def describe(self) -> dict[str, str]:
        return {"binary": str(self.binary), "model": self.model.name}
