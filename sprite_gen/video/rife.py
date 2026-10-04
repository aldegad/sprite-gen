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
`smear` measures what a made frame has that neither neighbour has (docs/loop-repair.md section 4).

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


def _smear_counts(frame: Image.Image) -> tuple[int, int, int]:
    """(dark pixels inside the solid body, one pixel in from its edge; solid pixels; part-covered pixels)."""
    x = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    solid = x[..., 3] >= 0.5
    inner = np.asarray(Image.fromarray(np.uint8(solid) * 255).filter(ImageFilter.MinFilter(3))) > 0
    luma = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
    lo, hi = PARTIAL_ALPHA
    return int((inner & (luma < DARK_LUMA)).sum()), int(solid.sum()), int(((x[..., 3] > lo) & (x[..., 3] < hi)).sum())


def smear(made: Image.Image, a: Image.Image, b: Image.Image) -> dict[str, float]:
    """What a made frame has that neither of its two neighbours has, as fractions of its solid
    pixels: `dark_excess`, dark pixels inside the body beyond the darker neighbour's count (a black
    smear raises it; a moved dark part — a hat, a watch — keeps its count), and `partial_excess`,
    part-covered pixels beyond the more ragged neighbour's (a limb RIFE could not follow, drawn as
    a pale ghost). Zero or less: nothing added."""
    dm, sm, pm = _smear_counts(made)
    (da, _, pa), (db, _, pb) = _smear_counts(a), _smear_counts(b)
    solid = max(1, sm)
    return {"dark_excess": round((dm - max(da, db)) / solid, 5), "partial_excess": round((pm - max(pa, pb)) / solid, 5)}


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
