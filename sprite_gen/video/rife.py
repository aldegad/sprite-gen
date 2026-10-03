# SPDX-License-Identifier: Apache-2.0
"""RIFE in-betweens for RGBA sprite frames, through the external `rife-ncnn-vulkan` binary.

Where it runs, what it costs and why this build: docs/loop-repair.md section 1. RIFE reads
three colour channels and no alpha, so a frame is interpolated as two images — its colour
premultiplied over black and its coverage as a grey image — and put back together
unpremultiplied. Interpolating the straight colour instead would drag the key's black under
alpha 0 into the edge; interpolating RGBA as RGB would lose the coverage outright.

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

from PIL import Image

from sprite_gen._deps import np

BINARY = "rife-ncnn-vulkan"
MODEL = "rife-v4.6"
RELEASE = "20221029"
# Coverage RIFE leaves under this is its own blur, not body: dropped so no halo is invented.
ALPHA_FLOOR = 2 / 255
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


def between(a: Image.Image, b: Image.Image, t: float, *, binary: Path, model: Path, tmp: Path) -> Image.Image:
    """The RGBA frame at fraction `t` (0..1) of the way from `a` to `b`."""
    if a.size != b.size:
        raise ValueError(f"rife: frames differ in size ({a.size} vs {b.size})")
    fa, fb = (np.asarray(f.convert("RGBA"), dtype=np.float32) / 255.0 for f in (a, b))
    prem = [Image.fromarray(np.uint8(np.clip(x[..., :3] * x[..., 3:4], 0, 1) * 255 + 0.5), "RGB") for x in (fa, fb)]
    cov = [Image.fromarray(np.uint8(x[..., 3] * 255 + 0.5), "L").convert("RGB") for x in (fa, fb)]
    color = np.asarray(_call(binary, model, prem[0], prem[1], t, tmp), dtype=np.float32) / 255.0
    alpha = np.asarray(_call(binary, model, cov[0], cov[1], t, tmp).convert("L"), dtype=np.float32) / 255.0
    alpha = np.where(alpha < ALPHA_FLOOR, 0.0, alpha)
    rgb = np.where(alpha[..., None] > 0, color / np.maximum(alpha[..., None], 1e-6), 0.0)
    out = np.dstack([np.clip(rgb, 0, 1), alpha])
    return Image.fromarray(np.uint8(out * 255 + 0.5), "RGBA")


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
