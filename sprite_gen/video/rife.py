# SPDX-License-Identifier: Apache-2.0
"""RIFE in-betweens for RGBA sprite frames, through the external `rife-ncnn-vulkan` binary or, where SPRITE_GEN_RIFE_BACKEND
names it, the same rife-v4.6 in PyTorch (`rife_torch`).

Where it runs, what it costs and why this build: docs/loop-repair.md section 1. RIFE reads
three colour channels and no alpha, so a frame is interpolated as two images — its colour and
its coverage as a grey image — and put back together. The two are two runs of the flow, and
where legs cross they do not agree: the coverage says body where the colour run still carries
what lay around the body. So around the body the colour image holds the body's own colour,
pushed out from inside its outline (`bleed`): a disagreement there reads as the body, not as the
black a frame premultiplied over black put under it (2.24 and before: a black smear between
crossing legs). Inside the coverage the colour is the frame's own, outline included.
`smear` measures what a made frame has that neither neighbour has, and the outline it lost where
the flow failed and melted a limb into the fill; `outline_shift` reads that edge against the
neighbours' rank by rank, so a mid-tone line the flow only softened is not read as lost — where the
neighbours are outlined (`outlined`), and not on art painted without a line; `ghost` reads a band of
part coverage on a frame alone, filmed or made; `crossfade` reads two drawings cross-faded inside a
whole silhouette, a reading to look at, not a fault; `hole` reads a transparent hole a made frame opened
inside its silhouette that neither neighbour has there — in place, carried by the whole figure's motion,
or, whatever carried it, one of each neighbour's holes, alike and on its way — where the two runs
disagree inside a limb; `doubled` reads a line one neighbour draws that a made frame left at part strength in its own
place, a double outline where the flow faded a line out instead of moving it (docs/loop-repair.md section 4). `passes` runs
the sixteen single passes the binary's augmentation combines inside one call, one at a time, and
`torn` reads where they disagree: a small part RIFE drags apart beside a large one moving far.

The binary's own CPU path (`-g -1`) returns a wrong frame with rife-v4.6 on both macOS and
Linux (measured 2026-10-03: mean error 39 against 3.3 through Vulkan), so it is never passed;
a machine without a GPU runs the Vulkan path on Mesa's llvmpipe, which gives the GPU's frame.
Every call asks for the binary's test-time augmentation, spatial and temporal (`-x -z`, `TTA`;
docs/loop-repair.md section 1, "Which setting"); SPRITE_GEN_RIFE_TTA names another (`tta()`).

The binary is found by SPRITE_GEN_RIFE, then on PATH, then where `sprite-gen rife install`
puts it (`install_root()`, docs/loop-repair.md section 1). A RIFE that cannot be found is
`RifeNotInstalled`, which a caller may answer by cutting the loop as filmed with a warning; a
RIFE that is found and then fails is `RifeUnavailable` and always an error.

SPRITE_GEN_RIFE_BACKEND=torch runs the frame in PyTorch instead (`backend()`, `rife_torch`), on the device
SPRITE_GEN_RIFE_DEVICE names (`device()`: cuda, the default, or cpu), from the same model files the binary runs
(`locate_model()`, checked against MODEL_SHA256). Nothing falls back: with torch chosen the binary is neither looked for
nor run, and a device that cannot be had is not traded for another. PyTorch or the model not installed is
`RifeNotInstalled`, as the binary not installed is; the line that installs it is `install_command()`.

SPRITE_GEN_RIFE_EXECUTOR runs the torch backend on a GPU elsewhere (`executor()`, `RemoteRunner`): it names the host's send,
a function from a request's bytes to its answer's, which reaches the same sprite-gen there (`rife_torch.serve`, `answer`).
A frame is one call, a read one call, and a RIFE asks the executor what it is once, as it is made, holds that for its life
and refuses an executor that is not it — so a frame it hands on or keeps is that executor's. The host needs no PyTorch.

Wherever it runs, a frame is the same two runs of RIFE and the read the same sixteen passes (`frame_runs`, `pass_runs`):
what turns a frame into RIFE's input (`inputs`) and its two runs back into a frame (`_joined`) is done here, and the runs
are a runner's (`Binary`, `rife_torch.TorchRunner`), which `Rife` hands each frame whole.

The interpolator is a plain callable `(a, b, t) -> frame`, so a caller can be handed a stand-in
(`Interpolate`), and a test needs no binary. A caller that reads the passes takes a callable
`(a, b, t) -> (frame, disagreement)` (`Read`, `Rife.read`).

A frame made between two frames can be kept and taken again (`Cache`): an alignment run twice on
the same source frames — again with the feet told — makes the same frames a second time, two RIFE
calls each (section 1 of docs/loop-repair.md has their cost; section 4 the cache). It is kept under
everything it is made from, the interpolator's `identity()` included — read again for every frame,
as the files a RIFE runs from can be written over under it — so it is taken only where making it
again would give the same bytes.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import PIL
from PIL import Image, ImageFilter, PngImagePlugin

from sprite_gen._deps import np
from sprite_gen.video.frames import _components, _near

BINARY = "rife-ncnn-vulkan"
MODEL = "rife-v4.6"
RELEASE = "20221029"
# What runs RIFE (`backend()`): the binary, the default, or rife-v4.6 in PyTorch (`rife_torch`); and the torch devices
# it runs on (`device()`), the first the default.
BACKENDS = ("ncnn", "torch")
DEVICES = ("cuda", "cpu")
# The model files of rife-v4.6 as the release zips ship them, the macOS and the Ubuntu zip alike: `sprite-gen rife install`
# checks them, and the torch backend reads its weights out of exactly these (`model_bytes`), as it reads them by their
# layout (docs/loop-repair.md section 1).
MODEL_SHA256 = {
    "flownet.param": "724569596bcd1e7b9fa50455c604777ebed99746d2ef40aa86e31b5725f1053c",
    "flownet.bin": "f334ed2260149ce0188a6dcf049844e8b0cdd912e01cbcfb63553157d2508958",
}
TORCH_EXTRA = 'pip install "sprite-gen[torch]"'
# `RemoteRunner`: the version of the requests and answers a host and its executor exchange (`pack`); another is refused.
PROTOCOL = 1
# The binary's test-time augmentation, passed on every call: `-x` runs the pair as its eight flips and
# transposes, `-z` runs it both ways in time as well, and what comes back is averaged — so a frame made
# from a to b is the one made from b to a (docs/loop-repair.md section 1, "Which setting"). On a machine
# without a GPU it costs 15 to 19 times the CPU of a plain call. SPRITE_GEN_RIFE_TTA names another
# (`tta()`): either mode, or none.
TTA = ("-x", "-z")
TTA_MODES = ("-x", "-z")
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
# `outline_shift`: a pixel of the made frame's edge is lost where its darkest solid pixel within OUTLINE_REACH is
# lighter than the lighter neighbour's at the same rank by more than this (luma, 0..1). A line the flow keeps
# and softens comes out lighter by less; a line gone to the fill beside it, by the line's own contrast.
OUTLINE_SHIFT = 50 / 255
# `outlined`: a frame's fill is read this far inside its solid edge (a share of the body's height, at least a
# pixel) and pushed out over the edge, as `bleed` pushes the colour from BLEED_DEPTH: twice as deep, so the fill
# read beside a line is past it where the line is twice as wide as the one `bleed` reads past.
LINE_DEPTH = 2 * BLEED_DEPTH
# `outline_shift` reads a pair only where both neighbours are outlined on at least this share of their coverage
# edge (`outlined`). Under it — art painted without a line, or lined in a tone near its fill — a line gone to fill
# is lighter by less than OUTLINE_SHIFT, or there is no line to lose, and the dark line judges alone: a frame
# refused there is replaced by a filmed one, a melted frame kept would be delivered (docs/loop-repair.md section 4).
OUTLINED = 0.75
# `outline_faded`, read where a game shows the cells at a share of the frames' size (`--judge-scale`): a pixel of a made
# frame's edge has its line where the line's ink beside it, summed across OUTLINE_REACH pixels of the size seen, is at
# least this share of the weaker neighbour's at the same rank. A thin line the flow softened spreads its ink over a few
# pixels and keeps it; a line gone to the fill, or legs melted into one shape of fill, has little or none.
LINE_SEEN = 0.6
# `ghost`: part-covered pixels count where they make a region at least this many pixels thick. An
# antialiased edge or a keyed strand of hair is a pixel or two wide and erodes away; the band a video
# model leaves between two drawings, or a flow carries from it, does not.
GHOST_THICK = 5
# `ghost_window`: the same band read in windows this share of the body high, each against the solid pixels in it, so a
# band on one limb counts against that limb's part of the body and not the whole figure's (docs/loop-repair.md section 4).
GHOST_BAND = 0.10
# `hole`: a pixel under solid (alpha under 0.5) that the outside of the picture cannot reach without crossing the solid
# body is a hole; a made frame's is its neighbours' where either of them is under solid within HOLE_REACH pixels of where
# it lay at that neighbour's time — in place, or carried with the whole figure (a gap between an arm and the body or
# between two legs, kept, opened by a step or moved with them) — and counts where it makes a region at least HOLE_THICK
# pixels thick: a pinhole a pixel or two wide, an antialiased crevice, is opened away. The figure's motion is the one the
# two neighbours alone show (`_travel`), never the flow's, so a frame the flow got wrong is not judged by the flow that
# made it; it is followed up to HOLE_TRAVEL of the body's height, searched whole on the level pooled coarsely enough that
# this reach is at most HOLE_COARSE pixels each way, then refined down to the pixel.
HOLE_REACH = 4
HOLE_THICK = 5
HOLE_TRAVEL = 0.2
HOLE_COARSE = 24
# A hole those two leave is still the neighbours' where it is one hole of each, whatever carried it — a part moving over a
# body that stays, turning, growing, sheared, swinging: told by what it is, not by a motion. Each of the made frame's
# enclosed holes the first two readings leave (a piece of them keeping a pixel opened by HOLE_THICK) is paired with one
# of each neighbour's (keeping a pixel opened by HOLE_SOURCE_THICK, a gap a little thinner at a neighbour's time) where
# the three are alike and lie on one way (`_paired`); a hole those readings give the neighbours takes no pair, being
# theirs already. The neighbours' two: areas within HOLE_PAIR_AREA times of each other, shapes within HOLE_PAIR_SHAPE
# times (`_holes`: second moments over area, which turning and growing keep), the colour round them within HOLE_COLOUR,
# and no farther apart than the figure's motion reaches, diagonally, and HOLE_REACH. The made frame's: its area within
# HOLE_AREA of their range, its shape within HOLE_SHAPE times of it, the colour round it within HOLE_COLOUR of each, and
# its centroid within HOLE_REACH and HOLE_PATH of their distance of the point at its time on the line from one to the
# other. Each neighbour's hole explains one of the holes paired: of the sets of such triples that can stand together,
# the one taken pairs the most, then is the alikest and nearest (`_taken`), so alike holes moving together each keep
# their own two, however near their ways run. An exact search finds it, each group of triples sharing holes apart: first
# whether one set pairs every hole of the group (`_whole`), then, where none does, which set pairs the most
# (`_searched`), each in at most HOLE_PAIR_STEPS steps (a row or grid of alike holes that all pair takes about a step a
# hole); where the first runs past them the second answers, and a group the second runs past pairs none of its holes.
HOLE_SOURCE_THICK = 3
HOLE_PAIR_AREA = 4.0
HOLE_PAIR_SHAPE = 2.0
HOLE_AREA = 0.25
HOLE_SHAPE = 1.3
HOLE_PATH = 0.1
HOLE_COLOUR = 0.12
HOLE_PAIR_STEPS = 5000
# `doubled`: a line one neighbour draws, left at part strength in its own place in a made frame — where the flow could not
# follow a line and faded it out instead, the frame shows it beside the line it drew: a double outline (a boot's top as one
# neighbour tilts it and the other plants it). A frame's line is its solid pixels darker by more than OUTLINE_SHIFT than its
# own luma, white off its solid body, closed by a square DOUBLED_WIDE of the made frame's body height wide: a dark stroke
# narrower than that goes in the closing, a dark fill wider stays and is no line. A pixel of one neighbour's line is left
# where the three frames are solid, the other neighbour draws no line within DOUBLED_SHARED px of it (a line both draw is
# theirs, at any strength) and is lighter there by more than OUTLINE_SHIFT, and the made frame is darker than the other
# there by more than OUTLINE_SHIFT (a line still seen) yet by no more than DOUBLED_PART of the line's contrast, there and
# at its darkest within OUTLINE_REACH (no line at full strength beside it, as at the soft edge of a line the flow moved).
# Such pixels count where they make a region DOUBLED_THICK px thick; widened by DOUBLED_THICK px, those that touch make one
# run, and a run's length is sqrt(12) times the root of its pixels' larger second moment (a straight run's own length).
DOUBLED_WIDE = 0.025
DOUBLED_SHARED = 4
DOUBLED_PART = 0.8
DOUBLED_THICK = 3
# `crossfade`: an edge's local range is read over this many pixels, wider than RIFE's blur, so a
# softened edge keeps its height and a cross-faded one does not; strong edges are counted in windows
# this share of the body's height; a window that kept under CROSSFADE_KEEP of its source frames'
# strong edges lost them.
CROSSFADE_RANGE = 7
CROSSFADE_WINDOW = 0.08
CROSSFADE_KEEP = 0.6
# `passes`: the binary's augmentation runs the pair turned eight ways (`-x`: every flip and transpose) and both ways in
# time (`-z`), combines the sixteen inside, and gives back one frame — where the passes disagree, the frame does not say
# so. Run one at a time with no augmentation, each turned back, they can be read apart. Each pass is two calls, colour
# and coverage, as `between` makes them.
PASS_GEOMETRIES = tuple((transpose, flips) for transpose in (False, True) for flips in range(4))  # flips: 1 left-right, 2 up-down
PASS_CALLS = 2 * 2 * len(PASS_GEOMETRIES)
# `torn`: the forward passes' mean and the backward passes' mean put a different colour or coverage at a pixel where
# they are more than this apart (premultiplied RGBA, 0..1); an edge they place a pixel or two apart is thinner than
# TORN_THICK pixels and opened away; a part torn is read in windows TORN_WINDOW of the body's height on a side, the
# size of a hand on a walk.
TORN_LEVEL = 0.05
TORN_THICK = 3
TORN_WINDOW = 0.10
# One call past this is stopped and reported as a RIFE that failed. TTA's slowest call measured, on a
# 960 x 960 frame on Modal's CPU, took about 30 s, and Modal's hosts differ by up to 2.5 times on one frame
# (2026-10-15, docs/loop-repair.md section 1).
CALL_TIMEOUT_SECONDS = 600
# `Cache`: the first thing a kept frame's key is made from — another way of keying or keeping frames is another
# name, so no frame kept the old way is taken — and the PNG text a kept frame names its own pixels' sha256 under.
CACHE_FORMAT = "sprite-gen frame between, 1"
PIXELS_TEXT = "sprite-gen-pixels-sha256"
# The engine code that makes a frame between, as this process loaded it (`Rife.identity`): read once, at import —
# rife.py written over on disk later changes no code that runs here, so a frame made here is kept under the code that
# made it, and a process that loads other code makes it again.
CODE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

Interpolate = Callable[[Image.Image, Image.Image, float], Image.Image]
Read = Callable[[Image.Image, Image.Image, float], tuple[Image.Image, np.ndarray]]

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


def tta() -> tuple[str, ...]:
    """The test-time augmentation every call asks for: SPRITE_GEN_RIFE_TTA when it is set (`-x`, `-z`
    or both, space-separated; empty for none), else TTA. Anything else is refused: a flag passed
    through unread could be the binary's CPU path, which is wrong with this model."""
    named = os.environ.get("SPRITE_GEN_RIFE_TTA")
    if named is None:
        return TTA
    modes = tuple(named.split())
    if any(m not in TTA_MODES for m in modes) or len(set(modes)) < len(modes):
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_TTA={named!r}: name -x, -z or both, space-separated, or leave it empty for none")
    return modes


def backend() -> str:
    """What runs RIFE: SPRITE_GEN_RIFE_BACKEND, `ncnn` (the binary) where it is unset or empty, or `torch` (rife-v4.6 in
    PyTorch, `rife_torch`). Anything else is refused, not read as the default."""
    named = os.environ.get("SPRITE_GEN_RIFE_BACKEND") or "ncnn"
    if named not in BACKENDS:
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_BACKEND={named!r}: name ncnn ({BINARY}, the default) or torch (rife-v4.6 in "
                              f"PyTorch), or leave it unset")
    return named


def device() -> str:
    """The torch device the torch backend runs on: SPRITE_GEN_RIFE_DEVICE, `cuda` where it is unset or empty, or `cpu`.
    Nothing is chosen for it: a CUDA that is not there is refused (`rife_torch.TorchRunner`), never traded for the CPU.
    Apple's GPU (`mps`) is not opened: no release lane measures it, and on a Mac the binary already runs on the GPU."""
    named = os.environ.get("SPRITE_GEN_RIFE_DEVICE") or DEVICES[0]
    if named == "mps":
        raise RifeUnavailable("SPRITE_GEN_RIFE_DEVICE=mps: the torch backend does not run on Apple's GPU — no release lane "
                              f"measures it there; on a Mac the default backend ({BINARY}) runs on the GPU, or name cpu")
    if named not in DEVICES:
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_DEVICE={named!r}: name cuda (the default) or cpu")
    return named


def executor() -> str | None:
    """The send that carries the torch backend's frames to a GPU elsewhere (`RemoteRunner`): SPRITE_GEN_RIFE_EXECUTOR,
    `module:attr` of the host's function from a request's bytes to its answer's; None where it is unset or empty. It runs
    the torch backend only, on the executor's own device, which its identity names: named with another backend, or with
    SPRITE_GEN_RIFE_DEVICE here as well, it is refused."""
    named = os.environ.get("SPRITE_GEN_RIFE_EXECUTOR")
    if not named:
        return None
    if backend() != "torch":
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_EXECUTOR={named!r}: an executor runs the torch backend; name "
                              "SPRITE_GEN_RIFE_BACKEND=torch with it, or unset it")
    on = os.environ.get("SPRITE_GEN_RIFE_DEVICE")
    if on:
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_DEVICE={on!r} with SPRITE_GEN_RIFE_EXECUTOR={named!r}: the device is the "
                              "executor's own, named where it runs and by its identity; unset SPRITE_GEN_RIFE_DEVICE here")
    return named


def executor_send(named: str) -> Callable[[bytes], bytes]:
    """The send `executor()` names, imported: refused by name where it is not `module:attr`, its module cannot be imported
    or it has no such function."""
    module, colon, attr = named.partition(":")
    if not (module and colon and attr):
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_EXECUTOR={named!r}: name the host's send as module:attr, a function from a "
                              "request's bytes to its answer's")
    try:
        found = importlib.import_module(module)
    except ImportError as exc:
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_EXECUTOR={named!r}: {module} could not be imported: {exc}") from exc
    send = getattr(found, attr, None)
    if not callable(send):
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_EXECUTOR={named!r}: {module} has no function {attr}")
    return send


def install_command() -> str:
    """The line that installs the RIFE `backend()` names, quoted wherever a missing RIFE is reported: INSTALL_COMMAND for the
    binary; for torch the extra and the model, `sprite-gen rife install --backend torch`. A backend refused names the
    binary's, as `Rife()` refuses it before anything is looked for."""
    try:
        chosen = backend()
    except RifeUnavailable:
        chosen = "ncnn"
    return INSTALL_COMMAND if chosen == "ncnn" else f"{TORCH_EXTRA} && {INSTALL_COMMAND} --backend torch"


def locate_model() -> Path:
    """The model directory the torch backend reads, with no binary looked for: SPRITE_GEN_RIFE_MODEL, else where
    `sprite-gen rife install` puts the model (`<install_root()>/rife-ncnn-vulkan-<release>-<platform>/rife-v4.6`)."""
    named = os.environ.get("SPRITE_GEN_RIFE_MODEL")
    model = Path(named).expanduser() if named else installed_binary().parent / MODEL
    if not (model / "flownet.param").is_file() or not (model / "flownet.bin").is_file():
        where = "SPRITE_GEN_RIFE_MODEL" if named else "SPRITE_GEN_RIFE_MODEL is not set, and the install root"
        raise RifeNotInstalled(f"RIFE model {model} has no flownet.param / flownet.bin ({where}); run `{install_command()}` "
                               f"(the model {MODEL}, sha256-checked, into the user data directory), or set "
                               f"SPRITE_GEN_RIFE_MODEL to a {MODEL} directory; see docs/loop-repair.md")
    return model.resolve()


def model_bytes(model: Path) -> dict[str, bytes]:
    """The two files of the model in `model`, each read once and checked against MODEL_SHA256, so the bytes handed on are
    the bytes checked: another model, or one cut short or written over, is refused by name, never read
    (`RifeUnavailable`)."""
    files = {}
    for name, pinned in MODEL_SHA256.items():
        try:
            data = (model / name).read_bytes()
        except OSError as exc:
            raise RifeUnavailable(f"RIFE model {model}: {name} could not be read: {exc}") from exc
        got = hashlib.sha256(data).hexdigest()
        if got != pinned:
            raise RifeUnavailable(f"RIFE model {model}: {name} has sha256 {got}, expected {pinned} ({MODEL} of "
                                  f"{BINARY} {RELEASE}); install it again (`{INSTALL_COMMAND} --force`), or point "
                                  f"SPRITE_GEN_RIFE_MODEL at that {MODEL}")
        files[name] = data
    return files


def _call(binary: Path, model: Path, a: Image.Image, b: Image.Image, t: float, tmp: Path,
          modes: tuple[str, ...] | None = None) -> Image.Image:
    """One run of the binary from `a` to `b` at `t`, with the augmentation `tta()` names, or `modes` (`passes`: none)."""
    modes = tta() if modes is None else modes
    a.save(tmp / "a.png")
    b.save(tmp / "b.png")
    out = tmp / "o.png"
    out.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [str(binary), "-0", str(tmp / "a.png"), "-1", str(tmp / "b.png"), "-o", str(out), "-s", f"{t:.4f}", "-m", str(model), *modes],
            capture_output=True, text=True, timeout=CALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RifeUnavailable(f"{binary.name} made no frame in {CALL_TIMEOUT_SECONDS} s and was stopped "
                              f"(augmentation {' '.join(modes) or 'none'}, SPRITE_GEN_RIFE_TTA)") from exc
    except OSError as exc:
        # A binary that is found but cannot be started (a damaged or wrong-platform build:
        # WinError 193 on Windows, ENOEXEC elsewhere) is a RIFE that fails, not a crash.
        raise RifeUnavailable(f"{binary.name} could not be started: {exc}") from exc
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


def _core_fill(frame: np.ndarray, depth: float) -> np.ndarray | None:
    """The colour of an RGBA frame (0..1) from `depth` (a share of its solid body's height, at least a pixel)
    inside its solid edge, pushed out over every pixel outside that core; None where nothing is solid."""
    solid = frame[..., 3] >= 0.5
    rows = np.nonzero(solid.any(axis=1))[0]
    if rows.size == 0:
        return None
    px = max(1, round((rows[-1] - rows[0] + 1) * depth))
    core = _square_min(solid, px, mode="edge")  # PIL's MinFilter, which repeats the edge pixels outward
    if not core.any():  # a body thinner than its outline: read at its edge
        core = solid
    return _push_pull(frame[..., :3], core.astype(np.float32))


def bleed(frame: np.ndarray) -> np.ndarray:
    """The colour image of an RGBA frame (0..1) RIFE is given: the frame's own colour wherever it
    has coverage, and around it the colour from BLEED_DEPTH inside its solid edge, pushed out."""
    fill = _core_fill(frame, BLEED_DEPTH)
    return np.where(frame[..., 3:] > 0, frame[..., :3], 0.0 if fill is None else fill)


# One run of RIFE, wherever it runs: from the first image to the second (H x W x 3, uint8) at a fraction, with the
# augmentation named (None: `tta()`'s, read for the run), to the image it makes (H x W x 3, uint8).
Run = Callable[[np.ndarray, np.ndarray, float, tuple[str, ...] | None], np.ndarray]


def inputs(a: Image.Image, b: Image.Image) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """What RIFE is given for a frame between `a` and `b`: their colour images as `bleed` makes them (H x W x 3) and their
    coverage (H x W), uint8 — the same wherever RIFE runs."""
    if a.size != b.size:
        raise ValueError(f"rife: frames differ in size ({a.size} vs {b.size})")
    fa, fb = (np.asarray(f.convert("RGBA"), dtype=np.float32) / 255.0 for f in (a, b))
    return ([np.uint8(np.clip(bleed(x), 0, 1) * 255 + 0.5) for x in (fa, fb)],
            [np.uint8(x[..., 3] * 255 + 0.5) for x in (fa, fb)])


def _grey(cover: np.ndarray) -> np.ndarray:
    """Coverage (H x W) as the grey image RIFE runs on."""
    return np.repeat(cover[..., None], 3, axis=-1)


def _coverage(made: np.ndarray) -> np.ndarray:
    """The coverage RIFE made from two grey images, read back to one channel as Pillow reads a grey (`convert("L")`)."""
    return np.asarray(Image.fromarray(made, "RGB").convert("L"))


def frame_runs(run: Run, colour: list[np.ndarray], cover: list[np.ndarray], t: float) -> tuple[np.ndarray, np.ndarray]:
    """A frame's two runs (`inputs`): the colour, then the coverage, from the first to the second at `t` with the
    augmentation every call asks for — (colour H x W x 3, coverage H x W), uint8, which `_joined` makes a frame."""
    return run(colour[0], colour[1], t, None), _coverage(run(_grey(cover[0]), _grey(cover[1]), t, None))


def pass_runs(run: Run, colour: list[np.ndarray], cover: list[np.ndarray], t: float) -> np.ndarray:
    """How far the sixteen single passes of a frame (`inputs`) disagree, per pixel (`passes`): PASS_CALLS runs."""
    grey = [_grey(c) for c in cover]
    rgbs, alphas = [], []
    for geometry in PASS_GEOMETRIES:
        for first, second, at in ((0, 1, t), (1, 0, 1 - t)):
            made = [run(_turned(pair[first], geometry), _turned(pair[second], geometry), at, ()) for pair in (colour, grey)]
            rgbs.append(_turned_back(made[0].astype(np.float32) / 255.0, geometry))
            alphas.append(_turned_back(_coverage(made[1]).astype(np.float32)[..., None] / 255.0, geometry)[..., 0])
    rgb, alpha = np.stack(rgbs), np.stack(alphas)
    pre = np.concatenate([rgb * alpha[..., None], alpha[..., None]], axis=-1)
    apart = np.sqrt(((pre[0::2].mean(axis=0) - pre[1::2].mean(axis=0)) ** 2).sum(axis=-1) / 4.0)
    return apart.astype(np.float32)


def _binary_run(binary: Path, model: Path, tmp: Path) -> Run:
    """A run of the binary (`_call`, its images written in `tmp`)."""
    def run(x: np.ndarray, y: np.ndarray, t: float, modes: tuple[str, ...] | None) -> np.ndarray:
        a, b = Image.fromarray(x, "RGB"), Image.fromarray(y, "RGB")
        return np.asarray(_call(binary, model, a, b, t, tmp) if modes is None else _call(binary, model, a, b, t, tmp, modes))
    return run


def between(a: Image.Image, b: Image.Image, t: float, *, binary: Path, model: Path, tmp: Path) -> Image.Image:
    """The RGBA frame at fraction `t` (0..1) of the way from `a` to `b`, through the binary."""
    colour, cover = inputs(a, b)
    return _joined(*frame_runs(_binary_run(binary, model, tmp), colour, cover, t))


def _joined(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    """The RGBA frame of a colour run and a coverage run (uint8): coverage under ALPHA_FLOOR dropped, the colour kept
    wherever there is coverage."""
    rgb, alpha = rgb.astype(np.float32) / 255.0, alpha.astype(np.float32) / 255.0
    alpha = np.where(alpha < ALPHA_FLOOR, 0.0, alpha)
    out = np.dstack([np.where(alpha[..., None] > 0, rgb, 0.0), alpha])
    return Image.fromarray(np.uint8(np.clip(out, 0, 1) * 255 + 0.5), "RGBA")


def _turned(x: np.ndarray, geometry: tuple[bool, int]) -> np.ndarray:
    """An image (H x W x C) turned one of PASS_GEOMETRIES' ways: transposed, then flipped."""
    transpose, flips = geometry
    if transpose:
        x = x.transpose(1, 0, 2)
    if flips & 1:
        x = x[:, ::-1]
    if flips & 2:
        x = x[::-1]
    return np.ascontiguousarray(x)


def _turned_back(x: np.ndarray, geometry: tuple[bool, int]) -> np.ndarray:
    """`_turned` undone."""
    transpose, flips = geometry
    if flips & 2:
        x = x[::-1]
    if flips & 1:
        x = x[:, ::-1]
    if transpose:
        x = x.transpose(1, 0, 2)
    return np.ascontiguousarray(x)


def passes(a: Image.Image, b: Image.Image, t: float, *, binary: Path, model: Path, tmp: Path) -> np.ndarray:
    """How far the binary's sixteen single passes from `a` to `b` at fraction `t` disagree, per pixel (docs/loop-repair.md
    section 4).

    Each pass is the pair turned one of PASS_GEOMETRIES' ways and run with no augmentation — from `a` to `b` at `t`, and
    from `b` to `a` at 1 - t — colour and coverage as `between` runs them, and turned back. The disagreement is how far
    apart the mean of the eight forward passes and the mean of the eight backward ones put the premultiplied colour and
    coverage (0..1, `torn` reads it). Their mean is not the frame: where they disagree it blends their guesses part-covered,
    which the augmentation, combining them inside the binary, does not. PASS_CALLS calls of the binary."""
    colour, cover = inputs(a, b)
    return pass_runs(_binary_run(binary, model, tmp), colour, cover, t)


def _mask(mask: np.ndarray, size: int, op: type) -> np.ndarray:
    return np.asarray(Image.fromarray(np.uint8(mask) * 255).filter(op(size))) > 0


def _square_min(a: np.ndarray, r: int, **pad) -> np.ndarray:
    """Per pixel, the minimum over the square 2r+1 wide around it, one axis at a time, outside the frame as
    `np.pad` fills it with `pad`."""
    out = np.pad(a, r, **pad)
    for axis in (0, 1):
        out = np.lib.stride_tricks.sliding_window_view(out, 2 * r + 1, axis=axis).min(axis=-1)
    return out


def _luma(x: np.ndarray) -> np.ndarray:
    return x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114


def _smear_counts(frame: Image.Image) -> tuple[int, int, int, int, int]:
    """(dark pixels inside the solid body, one pixel in from its edge; solid pixels; part-covered
    pixels; pixels on the coverage's edge; those of them with no outline within OUTLINE_REACH)."""
    x = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    solid = x[..., 3] >= 0.5
    inner = _mask(solid, 3, ImageFilter.MinFilter)
    luma = _luma(x)
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


def _edge_line(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The coverage edge of an RGBA frame (0..1; alpha from PARTIAL_ALPHA's floor, as `smear` reads it) and, per
    pixel, its line: the luma of the darkest solid pixel within OUTLINE_REACH, 1 where there is none."""
    covered = x[..., 3] >= PARTIAL_ALPHA[0]
    edge = covered & ~_mask(covered, 3, ImageFilter.MinFilter)
    return edge, _square_min(np.where(x[..., 3] >= 0.5, _luma(x), 1.0), OUTLINE_REACH, constant_values=1.0)


def _edge_darkness(frame: Image.Image) -> np.ndarray:
    """Per pixel of the coverage edge, its line (`_edge_line`)."""
    edge, line = _edge_line(np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0)
    return line[edge]


def outlined(frame: Image.Image) -> float:
    """The share of a frame's coverage edge outlined against its fill: where its line, the darkest solid pixel
    within OUTLINE_REACH, is darker than the colour from LINE_DEPTH inside, pushed out over the edge, by more than
    OUTLINE_SHIFT — so a line there gone to that fill reads lost rank by rank (`outline_shift`). Art outlined all
    round reads most of its edge; art painted without a line, whose edge is its fill, or lined in a tone near
    it, little. 0 where it has no edge (docs/loop-repair.md section 4)."""
    x = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    edge, line = _edge_line(x)
    fill = _core_fill(x, LINE_DEPTH)
    if fill is None or not edge.any():
        return 0.0
    return round(float(np.mean((_luma(fill) - line)[edge] > OUTLINE_SHIFT)), 5)


def _line_ink(frame: Image.Image, reach: int) -> np.ndarray:
    """Per pixel of a frame's coverage edge (alpha from PARTIAL_ALPHA's floor, as `smear` reads it), the ink of its line:
    how far its solid pixels in the square `reach` pixels round it are darker than the colour from LINE_DEPTH inside,
    pushed out over the edge (luma, 0..1, nothing where lighter), summed and divided by the square's side. A line
    crossing the square gives its darkness times its width, however far the flow spread it inside the square. Empty
    where the frame has no edge or nothing solid."""
    x = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    covered = x[..., 3] >= PARTIAL_ALPHA[0]
    edge = covered & ~_mask(covered, 3, ImageFilter.MinFilter)
    fill = _core_fill(x, LINE_DEPTH)
    if fill is None or not edge.any():
        return np.zeros(0, dtype=np.float32)
    dark = np.where(x[..., 3] >= 0.5, np.clip(_luma(fill) - _luma(x), 0.0, None), 0.0)
    side = 2 * reach + 1
    summed = np.pad(np.pad(dark, reach).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    h, w = dark.shape
    box = summed[side:side + h, side:side + w] - summed[:h, side:side + w] - summed[side:side + h, :w] + summed[:h, :w]
    return (box / side)[edge]


def outline_faded(made: Image.Image, a: Image.Image, b: Image.Image, scale: float) -> float | None:
    """The edge of a made frame whose line faded as a game shows the frames at `scale` of their size (`--judge-scale`),
    read on the frames as given against the two neighbours rank by rank: each pixel of its coverage edge by its line's
    ink (`_line_ink`) across OUTLINE_REACH pixels of the size seen (OUTLINE_REACH / `scale` here, rounded up), in order,
    against the weaker of the two neighbours' at the same rank (their quantiles), counted where it is under LINE_SEEN of
    that, as a fraction of its edge. A thin line the flow softened keeps its ink and is not counted; a line gone to the
    fill, or legs melted into one shape of fill, is, at any scale. None where one of the three has no edge or nothing
    solid to read the line against: nothing taken back (docs/loop-repair.md section 7)."""
    reach = math.ceil(OUTLINE_REACH / scale - 1e-9)
    im, ia, ib = (_line_ink(f, reach) for f in (made, a, b))
    if not (im.size and ia.size and ib.size):
        return None
    rank = (np.arange(im.size) + 0.5) / im.size
    weaker = np.minimum(np.quantile(ia, rank), np.quantile(ib, rank))
    return round(float(np.mean(np.sort(im) < LINE_SEEN * weaker)), 5)


def outline_shift(made: Image.Image, a: Image.Image, b: Image.Image) -> float | None:
    """The edge of a made frame that lost its line, read against its two neighbours rank by rank: each pixel
    of its coverage edge by how dark the darkest solid pixel within OUTLINE_REACH is, in order, against the
    lighter of the two neighbours' edges at the same rank (their quantiles), and counted where it is lighter
    by more than OUTLINE_SHIFT — as a fraction of its edge. A line drawn in a mid tone and softened by the
    flow reads lighter by less at every rank and is not counted, whatever tone the art outlines in; a line
    gone to the fill beside it (legs melted into one shape, a pale ghost limb) is. 0 where nothing is lighter.
    None where either neighbour is outlined on less than OUTLINED of its edge (`outlined`): there a part can
    melt with no line to lose, lighter by less at every rank, and the dark line (`smear`'s `outline_loss`)
    judges alone (docs/loop-repair.md section 4)."""
    if min(outlined(a), outlined(b)) < OUTLINED:
        return None
    dm, da, db = (_edge_darkness(f) for f in (made, a, b))
    if not (dm.size and da.size and db.size):
        return 0.0
    rank = (np.arange(dm.size) + 0.5) / dm.size
    lighter = np.maximum(np.quantile(da, rank), np.quantile(db, rank))
    return round(float(np.mean(np.sort(dm) > lighter + OUTLINE_SHIFT)), 5)


def ghost(frame: Image.Image) -> float:
    """Part-covered pixels (PARTIAL_ALPHA) inside regions at least GHOST_THICK pixels thick, as a
    fraction of the frame's solid pixels: a ghost band, read on the frame's own coverage and not
    against its neighbours, so a band a filmed frame carries reads the same in a frame made beside
    it (docs/loop-repair.md section 4)."""
    alpha = np.asarray(frame.convert("RGBA").getchannel("A"), dtype=np.float32) / 255.0
    lo, hi = PARTIAL_ALPHA
    thick = _mask((alpha > lo) & (alpha < hi), GHOST_THICK, ImageFilter.MinFilter)
    return round(float(thick.sum()) / max(1, int((alpha >= 0.5).sum())), 5)


def ghost_window(frame: Image.Image) -> tuple[float, tuple[float, float]]:
    """The ghost band (`ghost`: part-covered pixels in regions at least GHOST_THICK pixels thick) read in windows
    GHOST_BAND of the body high, sliding a quarter window at a time down the body: the largest share of a window's solid
    pixels, and where that window lies, as fractions of the body from its top. A band through one limb — the gap one
    neighbour has beside a limb blended with the limb the other has there, so the background shows through it — is a
    small share of a whole figure and a large one of the rows it lies in (docs/loop-repair.md section 4)."""
    alpha = np.asarray(frame.convert("RGBA").getchannel("A"), dtype=np.float32) / 255.0
    lo, hi = PARTIAL_ALPHA
    thick = _mask((alpha > lo) & (alpha < hi), GHOST_THICK, ImageFilter.MinFilter)
    solid = alpha >= 0.5
    rows = np.nonzero(solid.any(axis=1))[0]
    if not rows.size:
        return 0.0, (0.0, 0.0)
    top, bottom = int(rows[0]), int(rows[-1])
    height = bottom - top + 1
    win = max(8, round(height * GHOST_BAND))
    thick_rows, solid_rows = thick.sum(axis=1), solid.sum(axis=1)
    best, at = 0.0, top
    for y in range(top, bottom - win + 2, max(1, win // 4)):
        s = int(solid_rows[y:y + win].sum())
        if s and thick_rows[y:y + win].sum() / s > best:
            best, at = float(thick_rows[y:y + win].sum()) / s, y
    return round(best, 5), (round((at - top) / height, 3), round((at + win - top) / height, 3))


def _enclosed(solid: np.ndarray) -> np.ndarray:
    """The pixels off `solid` that the outside of the picture cannot reach without crossing it (8-connected)."""
    ys, starts, stops, pieces, _ = _components(~solid, diagonal=True)
    h, w = solid.shape
    inside = ~np.isin(pieces, pieces[(ys == 0) | (ys == h - 1) | (starts == 0) | (stops == w)])
    out = np.zeros_like(solid)
    for y, x0, x1 in zip(ys[inside], starts[inside], stops[inside]):
        out[y, x0:x1] = True
    return out


def _opened(mask: np.ndarray, size: int) -> np.ndarray:
    """`mask` opened by a square `size` pixels wide: what is thinner than that goes."""
    return _mask(_mask(mask, size, ImageFilter.MinFilter), size, ImageFilter.MaxFilter)


def _premultiplied(frame: Image.Image) -> np.ndarray:
    """An RGBA frame as its colour premultiplied by its coverage and the coverage (0..1, H x W x 4)."""
    x = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    return np.concatenate([x[..., :3] * x[..., 3:], x[..., 3:]], axis=-1)


def _pooled(x: np.ndarray, f: int) -> np.ndarray:
    """`x` (H x W x C) averaged over blocks f pixels on a side, padded with zeros to whole blocks."""
    if f == 1:
        return x
    x = np.pad(x, ((0, -x.shape[0] % f), (0, -x.shape[1] % f), (0, 0)))
    return x.reshape(x.shape[0] // f, f, x.shape[1] // f, f, x.shape[2]).mean(axis=(1, 3))


def _apart(v: int, t: float) -> tuple[int, int]:
    """Along one axis, for a motion of v pixels from a to b: where a pixel of the frame at fraction t lay in a and lies
    in b, from it — t of the motion back and 1 - t of it on, rounded so the two are v apart."""
    back = int(np.floor(t * v + 0.5))
    return -back, v - back


def _match(pa: np.ndarray, pb: np.ndarray, t: float, box: tuple[int, int, int, int], centre: tuple[int, int],
           radius: int) -> tuple[int, int]:
    """The motion (rows, columns) from a to b, within `radius` of `centre` each way, under which the two agree best over
    `box` (y0, y1, x0, x1) of the frame at fraction t: each pixel of the box read in a where it lay and in b where it lies
    (`_apart`), the mean absolute difference of their premultiplied colour and coverage (`_premultiplied`), a tie to the
    smaller motion. Past the picture both are empty."""
    y0, y1, x0, x1 = box
    pad = max(abs(centre[0]), abs(centre[1])) + radius + 1
    h, w = y1 - y0, x1 - x0
    crops = []
    for x in (pa, pb):
        crop = np.zeros((h + 2 * pad, w + 2 * pad, x.shape[2]), dtype=x.dtype)
        sy0, sy1, sx0, sx1 = max(0, y0 - pad), min(x.shape[0], y1 + pad), max(0, x0 - pad), min(x.shape[1], x1 + pad)
        crop[sy0 - y0 + pad:sy1 - y0 + pad, sx0 - x0 + pad:sx1 - x0 + pad] = x[sy0:sy1, sx0:sx1]
        crops.append(crop)
    best: tuple[float, int, int, int] | None = None
    for vy in range(centre[0] - radius, centre[0] + radius + 1):
        ay, by = _apart(vy, t)
        for vx in range(centre[1] - radius, centre[1] + radius + 1):
            ax, bx = _apart(vx, t)
            cost = float(np.abs(crops[0][pad + ay:pad + ay + h, pad + ax:pad + ax + w]
                                - crops[1][pad + by:pad + by + h, pad + bx:pad + bx + w]).mean(dtype=np.float64))
            key = (cost, vy * vy + vx * vx, vy, vx)
            if best is None or key < best:
                best = key
    return best[2], best[3]


def _travel(pa: np.ndarray, pb: np.ndarray, t: float, box: tuple[int, int, int, int], reach: int) -> tuple[int, int]:
    """The motion from a to b over `box` of the frame at fraction t (`_match`), up to `reach` pixels each way: searched
    whole on the level pooled in blocks of the smallest power of two that brings the reach to at most HOLE_COARSE pixels
    each way, then within two pixels of twice the coarser motion on each level down to the pixel."""
    f = 1
    while reach > HOLE_COARSE * f:
        f *= 2
    v, radius = (0, 0), -(-reach // f)
    y0, y1, x0, x1 = box
    while True:
        qa, qb = _pooled(pa, f), _pooled(pb, f)
        v = _match(qa, qb, t, (y0 // f, min(qa.shape[0], -(-y1 // f)), x0 // f, min(qa.shape[1], -(-x1 // f))), v, radius)
        if f == 1:
            return v
        f, v, radius = f // 2, (2 * v[0], 2 * v[1]), 2


def _moved(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """`mask` moved dy rows down and dx columns right; what comes in is empty."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    if abs(dy) < h and abs(dx) < w:
        out[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = mask[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
    return out


def _bounds(mask: np.ndarray) -> tuple[int, int, int, int]:
    """The rows and columns `mask` spans (y0, y1, x0, x1)."""
    ys, xs = np.nonzero(mask)
    return int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1


def _holes(pre: np.ndarray, thick: int) -> tuple[list[tuple[tuple[slice, slice], np.ndarray]], np.ndarray]:
    """A frame's enclosed holes (`_enclosed`, 8-connected pieces of them) that keep a pixel opened by a square `thick`
    pixels wide, its premultiplied colour and coverage given (`_premultiplied`): each hole's window, the hole's pixels
    in it and 2 HOLE_REACH + 1 pixels round them, and a row of what `_paired` reads of it — its area (pixels), its
    centroid (rows, columns), its shape (the logarithms of its pixels' two principal second moments, larger first, each
    plus a pixel's own 1/12 and over the area: turning and growing keep it) and the colour round it (the mean
    premultiplied colour of the frame's solid pixels from HOLE_REACH + 1 to 2 HOLE_REACH out, 0 where there are none)."""
    solid = pre[..., 3] >= 0.5
    enclosed = _enclosed(solid)
    ys, starts, stops, pieces, _ = _components(enclosed, diagonal=True)
    opened = np.pad(np.cumsum(_opened(enclosed, thick), axis=1, dtype=np.int64), ((0, 0), (1, 0)))
    order = np.argsort(pieces, kind="stable")
    margin = 2 * HOLE_REACH + 1
    windows, rows = [], []
    for runs in np.split(order, np.unique(pieces[order], return_index=True)[1][1:]):
        if not (opened[ys[runs], stops[runs]] > opened[ys[runs], starts[runs]]).any():
            continue
        y0, y1 = max(0, int(ys[runs].min()) - margin), min(solid.shape[0], int(ys[runs].max()) + 1 + margin)
        x0, x1 = max(0, int(starts[runs].min()) - margin), min(solid.shape[1], int(stops[runs].max()) + margin)
        mask = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        for y, s0, s1 in zip(ys[runs], starts[runs], stops[runs]):
            mask[y - y0, s0 - x0:s1 - x0] = True
        window = (slice(y0, y1), slice(x0, x1))
        py, px = np.nonzero(mask)
        py, px, n = py + y0, px + x0, len(py)
        moments = (np.sort(np.linalg.eigvalsh(np.cov(np.vstack([py, px]).astype(np.float64))))[::-1] if n > 2
                   else np.zeros(2))
        ring = _near(mask, 2 * HOLE_REACH) & ~_near(mask, HOLE_REACH) & solid[window]
        colour = pre[window][ring][:, :3].mean(axis=0) if ring.any() else np.zeros(3)
        windows.append((window, mask))
        rows.append([n, py.mean(), px.mean(), *np.log((moments + 1 / 12) / n), *colour])
    return windows, np.array(rows, dtype=np.float64).reshape(-1, 8)


def _paired(made: np.ndarray, a: np.ndarray, b: np.ndarray, t: float, reach: int) -> list[int]:
    """Which of the made frame's holes (rows of `_holes`: those the first two readings leave; the frame at fraction `t`
    from `a` to `b`, the neighbours' holes) are one hole of each neighbour's. A triple is one of the made frame's holes
    and one of each neighbour's where the neighbours' two have areas within HOLE_PAIR_AREA times of each other, shapes
    within HOLE_PAIR_SHAPE times (on each moment) and the colours round them within HOLE_COLOUR (on each channel), their
    centroids at most sqrt(2) `reach` + HOLE_REACH apart (`reach`: how far the figure's motion is followed each way);
    and the made frame's has its area within HOLE_AREA of their range, its shape within HOLE_SHAPE times of theirs on
    each moment, the colour round it within HOLE_COLOUR of each, and its centroid within HOLE_REACH + HOLE_PATH of
    their distance of the point at `t` on the line from theirs in a to theirs in b. Its cost, how far the three are
    from alike and near: the logarithm of how far the made frame's area is out of their range, its distance from that
    point over what it may be, how far its shape is out of theirs, the two colour differences over HOLE_COLOUR and the
    neighbours' two holes' distance over what it may be, summed. The triples taken are one to one (`_taken`)."""
    if not (len(made) and len(a) and len(b)):
        return []
    A, B = a[:, None], b[None, :]
    apart = np.hypot(B[..., 1] - A[..., 1], B[..., 2] - A[..., 2])
    near = apart / (np.sqrt(2) * reach + HOLE_REACH)
    colour = np.abs(A[..., 5:] - B[..., 5:]).max(axis=-1)
    j, k = np.nonzero((np.maximum(A[..., 0], B[..., 0]) <= HOLE_PAIR_AREA * np.minimum(A[..., 0], B[..., 0]))
                      & (np.abs(A[..., 3:5] - B[..., 3:5]).max(axis=-1) <= np.log(HOLE_PAIR_SHAPE))
                      & (colour <= HOLE_COLOUR) & (near <= 1))
    A, B, apart, near, colour = a[j], b[k], apart[j, k], near[j, k], colour[j, k]
    lo, hi = np.minimum(A[:, 0], B[:, 0]), np.maximum(A[:, 0], B[:, 0])
    narrow, wide = np.minimum(A[:, 3:5], B[:, 3:5]), np.maximum(A[:, 3:5], B[:, 3:5])
    at = (1 - t) * A[:, 1:3] + t * B[:, 1:3]
    triples = []
    for i, c in enumerate(made):
        area = np.where(c[0] < lo, c[0] / lo, np.where(c[0] > hi, c[0] / hi, 1.0))
        path = np.hypot(c[1] - at[:, 0], c[2] - at[:, 1]) / (HOLE_REACH + HOLE_PATH * apart)
        shape = np.maximum(0.0, np.maximum(narrow - c[3:5], c[3:5] - wide)).max(axis=-1)
        alike = np.maximum(np.abs(c[5:] - A[:, 5:]).max(axis=-1), np.abs(c[5:] - B[:, 5:]).max(axis=-1))
        cost = np.abs(np.log(area)) + path + shape + alike / HOLE_COLOUR + colour / HOLE_COLOUR + near
        for p in np.nonzero((area >= 1 - HOLE_AREA) & (area <= 1 + HOLE_AREA) & (path <= 1)
                            & (shape <= np.log(HOLE_SHAPE)) & (alike <= HOLE_COLOUR))[0]:
            triples.append((i, int(j[p]), int(k[p]), float(cost[p])))
    return _taken(triples)


def _taken(triples: list[tuple[int, int, int, float]]) -> list[int]:
    """The made frame's holes the triples taken pair, one to one: no hole of any frame in two of them. Of the sets of
    `triples` (the made frame's hole, a's, b's and the cost; `_paired`) that can stand together, the one taken pairs the
    most holes, then has the least summed cost, so alike holes moving together each keep their own two, however near
    their ways run. Triples sharing a hole, directly or through others, make one group, read apart from the rest: all
    its holes pair where one set pairs them all (`_whole`), and otherwise those of the set `_searched` finds."""
    owner: dict[tuple[int, int], tuple[int, int]] = {}

    def root(x: tuple[int, int]) -> tuple[int, int]:
        while owner.get(x, x) != x:
            x = owner[x]
        return x

    for i, j, k, _ in triples:
        for x in ((1, j), (2, k)):
            if (r := root(x)) != (s := root((0, i))):
                owner[r] = s
    groups: dict[tuple[int, int], dict[int, list[tuple[float, int, int]]]] = {}
    for i, j, k, cost in sorted(triples, key=lambda x: (x[3], x[1], x[2], x[0])):
        groups.setdefault(root((0, i)), {}).setdefault(i, []).append((cost, 1 << j, 1 << k))
    taken: list[int] = []
    for cands in groups.values():
        taken += list(cands) if _whole(cands) else _searched(cands)
    return sorted(taken)


def _whole(cands: dict[int, list[tuple[float, int, int]]]) -> bool | None:
    """Whether one set of a group's triples (each of its made frame's holes with its triples, cheapest first: the cost,
    and a's hole and b's as bits; `_taken`), one to one, pairs every hole of the group: True or False, or None where the
    search runs past HOLE_PAIR_STEPS steps. Depth first from the made frame's hole with the fewest triples still free
    (holding no hole a triple taken holds), each of those cheapest first, no hole left unpaired; a branch is cut where a
    hole left has no free triple, or where the holes left cannot each keep a hole of a, or of b, of its own among those
    its free triples hold (`_matched`)."""
    stack = [(tuple(sorted(cands)), 0, 0)]
    for _ in range(HOLE_PAIR_STEPS):
        if not stack:
            return False
        holes, used_a, used_b = stack.pop()
        if not holes:
            return True
        free = {i: [x for x in cands[i] if not (used_a & x[1] or used_b & x[2])] for i in holes}
        if not all(free.values()) or not (_matched(free, 1) and _matched(free, 2)):
            continue
        h = min(holes, key=lambda i: (len(free[i]), i))
        others = tuple(i for i in holes if i != h)
        stack.extend((others, used_a | ja, used_b | kb) for _, ja, kb in reversed(free[h]))
    return None if stack else False


def _matched(free: dict[int, list[tuple[float, int, int]]], side: int) -> bool:
    """Whether each of the made frame's holes `free` names can keep a hole of one neighbour (`side` 1, a; 2, b) of its
    own among those its free triples hold there: an augmenting path for each in turn (Kuhn's matching)."""
    held = dict.fromkeys(free, 0)
    for i, f in free.items():
        for x in f:
            held[i] |= x[side]
    owner: dict[int, int] = {}  # a neighbour's hole, as its bit: the made frame's hole keeping it
    for start in free:
        seen, path = 0, [[start, held[start], 0]]  # a made frame's hole, the holes it has yet to try, the one it keeps
        while path:
            step = path[-1]
            untried = step[1] & ~seen
            if not untried:
                path.pop()
                continue
            x = untried & -untried
            step[1], seen = untried ^ x, seen | x
            if x in owner:
                path.append([owner[x], held[owner[x]], x])
                continue
            for i, _, kept in reversed(path):  # the last takes x, and each before it the one the next gives up
                owner[x], x = i, kept
            break
        else:
            return False
    return True


def _searched(cands: dict[int, list[tuple[float, int, int]]]) -> list[int]:
    """Of one group of triples (each of its made frame's holes with its triples, cheapest first: the cost, and a's hole
    and b's as bits; `_taken`), the holes the set taken pairs, by an exact search, depth first: from the made frame's
    hole with the fewest triples still free (holding no hole a triple taken holds), each of those, cheapest first, then
    the hole left unpaired. The most a branch could still pair: each hole left with a free triple, and in each neighbour
    no more than the holes the free triples hold there. A branch is cut where that pairs fewer holes than the best set
    found, or as many with no less cost than the best's, even were each of as many as it takes, the cheapest, paired by
    its cheapest free triple. The search ends at a set pairing every hole, since every such set pairs the same holes.
    Where it runs past HOLE_PAIR_STEPS steps none of the group's holes is paired (docs/loop-repair.md section 4)."""
    best: tuple[int, float, tuple[int, ...]] = (-1, 0.0, ())
    stack = [(tuple(sorted(cands)), 0, 0, 0, 0.0, ())]
    for _ in range(HOLE_PAIR_STEPS):
        if not stack:
            break
        holes, used_a, used_b, got, cost, pairs = stack.pop()
        free, cheapest, held_a, held_b = {}, [], 0, 0
        for i in holes:
            if f := [x for x in cands[i] if not (used_a & x[1] or used_b & x[2])]:
                free[i] = f
                cheapest.append(f[0][0])
                for _, ja, kb in f:
                    held_a |= ja
                    held_b |= kb
        more = min(len(cheapest), held_a.bit_count(), held_b.bit_count())
        if got + more < best[0] or got + more == best[0] and cost + sum(sorted(cheapest)[:more]) >= best[1]:
            continue
        if not free:
            best = (got, cost, pairs)
            if got == len(cands):
                break
            continue
        h = min(free, key=lambda i: (len(free[i]), i))
        others = tuple(i for i in free if i != h)
        stack.append((others, used_a, used_b, got, cost, pairs))
        stack.extend((others, used_a | ja, used_b | kb, got + 1, cost + c, pairs + (h,))
                     for c, ja, kb in reversed(free[h]))
    else:
        if stack:
            return []
    return list(best[2])


def hole(made: Image.Image, a: Image.Image, b: Image.Image, t: float) -> float:
    """A hole a made frame at fraction `t` from `a` to `b` opened inside its silhouette that neither neighbour has there:
    of its pixels under solid that the outside cannot reach without crossing its solid body, those where neither
    neighbour is under solid within HOLE_REACH of where the pixel lay at that neighbour's time — in place, or carried
    with the whole figure along the motion the two neighbours alone show (`_travel`, over the box round all three
    frames' solid bodies, followed up to HOLE_TRAVEL of the body's height) — and in no hole of the made frame that is
    one of each neighbour's, whatever carried it (`_holes`, `_paired`), in regions at least HOLE_THICK pixels thick
    (opened by a square that wide), as a fraction of its solid pixels.
    The coverage run can carry a gap one neighbour has between two legs into a leg the colour run keeps whole: the frame
    shows the leg's fill on both sides of a transparent band, with no line round it, and nothing added and no part
    coverage to read — and wherever the band's pixels lay, both neighbours show the leg there, and neither has a hole
    like it on its way. A gap the neighbours have — between an arm and the body or between two legs, kept, opened by a
    step, or moved with the body — is theirs, and so is a hole each has, carried by a part however it moves within
    reach. 0 where the frame has no solid body (docs/loop-repair.md section 4)."""
    m, fa, fb = (np.asarray(f.convert("RGBA").getchannel("A"), dtype=np.float32) / 255.0 for f in (made, a, b))
    solid = m >= 0.5
    if not solid.any():
        return 0.0
    near_a, near_b = _near(fa < 0.5, HOLE_REACH), _near(fb < 0.5, HOLE_REACH)
    left = _opened(_enclosed(solid) & ~near_a & ~near_b, HOLE_THICK)
    if left.any():
        pa, pb = _premultiplied(a), _premultiplied(b)
        rows = np.nonzero(solid.any(axis=1))[0]
        reach = max(1, int(round(HOLE_TRAVEL * int(rows[-1] - rows[0] + 1))))
        (ay, by), (ax, bx) = (_apart(v, t) for v in _travel(pa, pb, t, _bounds(solid | (fa >= 0.5) | (fb >= 0.5)), reach))
        left = _opened(left & ~(_moved(near_a, -ay, -ax) | _moved(near_b, -by, -bx)), HOLE_THICK)
        if left.any():
            holes, own = _holes(_premultiplied(made), HOLE_THICK)
            rest = [i for i, (window, mask) in enumerate(holes) if left[window][mask].any()]
            for p in _paired(own[rest], _holes(pa, HOLE_SOURCE_THICK)[1], _holes(pb, HOLE_SOURCE_THICK)[1], t, reach):
                window, mask = holes[rest[p]]
                left[window] &= ~mask
            left = _opened(left, HOLE_THICK)
    return round(float(left.sum()) / int(solid.sum()), 5)


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
    fraction of the made frame's solid pixels — the windows step by half their size, so one lost pixel
    is counted in up to four of them (nine where the window is an odd number of pixels wide); 0 where
    it shows no blend. Relative to the source frames' own edges, so a palette's lightness does not
    decide it (docs/loop-repair.md section 4)."""
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


def _lines(x: np.ndarray, r: int) -> np.ndarray:
    """An RGBA frame's (0..1) lines (`doubled`): its solid pixels darker by more than OUTLINE_SHIFT than its luma, white off
    its solid body, closed by a square 2 r + 1 wide."""
    solid = x[..., 3] >= 0.5
    luma = np.where(solid, _luma(x), 1.0)
    closed = _square_min(-_square_min(-luma, r, mode="edge"), r, mode="edge")
    return solid & (closed - luma > OUTLINE_SHIFT)


def doubled(made: Image.Image, a: Image.Image, b: Image.Image) -> float:
    """A line one neighbour draws, left at part strength in its own place in a made frame: the longest run of such pixels
    (DOUBLED_* above), as a share of the made frame's body height. RIFE can fade out a line its flow does not follow — the
    top of a boot one neighbour tilts and the other plants — and the frame shows it, lighter, beside the line it drew: a
    double outline, its coverage whole (no part coverage for `ghost`), nothing darker added (`smear`) and its edge outlined
    (`outline_shift`). Read on the made frame's pixels against each neighbour's: a line both neighbours draw is theirs at
    any strength, a line the made frame carries at its neighbour's strength is that neighbour's drawing, and the soft edge
    of a line the flow moved lies beside the line at full strength. 0 where the frame has no solid body, or where the
    neighbours nowhere differ by a line's contrast (docs/loop-repair.md section 4)."""
    m, fa, fb = (np.asarray(f.convert("RGBA"), dtype=np.float32) / 255.0 for f in (made, a, b))
    rows = np.nonzero((m[..., 3] >= 0.5).any(axis=1))[0]
    if rows.size == 0:
        return 0.0
    height = int(rows[-1] - rows[0] + 1)
    lm, la, lb = (_luma(x) for x in (m, fa, fb))
    both = (m[..., 3] >= 0.5) & (fa[..., 3] >= 0.5) & (fb[..., 3] >= 0.5)
    differ = both & (np.abs(la - lb) > OUTLINE_SHIFT)
    if not differ.any():
        return 0.0
    r = max(1, round(DOUBLED_WIDE * height / 2))
    # read in the box round where they differ, wide enough that every pixel there reads as in the whole frame
    y0, y1, x0, x1 = _bounds(differ)
    pad = 2 * r + DOUBLED_SHARED + OUTLINE_REACH + DOUBLED_THICK
    box = (slice(max(0, y0 - pad), y1 + pad), slice(max(0, x0 - pad), x1 + pad))
    m, fa, fb, lm, la, lb, both = (v[box] for v in (m, fa, fb, lm, la, lb, both))
    line_a, line_b = _lines(fa, r), _lines(fb, r)
    darkest = _square_min(np.where(m[..., 3] >= 0.5, lm, 1.0), OUTLINE_REACH, constant_values=1.0)
    left = np.zeros(both.shape, dtype=bool)
    for line, other, own, there in ((line_a, line_b, la, lb), (line_b, line_a, lb, la)):
        part = DOUBLED_PART * (there - own)
        left |= (both & line & ~_near(other, DOUBLED_SHARED) & (there - own > OUTLINE_SHIFT)
                 & (there - lm > OUTLINE_SHIFT) & (there - lm <= part) & (there - darkest <= part))
    left = _opened(left, DOUBLED_THICK)
    if not left.any():
        return 0.0
    ys, starts, stops, pieces, _ = _components(_near(left, DOUBLED_THICK), diagonal=True)
    run = np.zeros(left.shape, dtype=np.int64)
    for y, s0, s1, p in zip(ys, starts, stops, pieces):
        run[y, s0:s1] = p
    py, px = np.nonzero(left)
    ids = run[py, px]
    longest = max(float(np.linalg.eigvalsh(np.cov(np.vstack([py[ids == i], px[ids == i]]).astype(np.float64)))[-1])
                  for i in np.unique(ids))
    return round(float(np.sqrt(12 * max(longest, 0.0))) / height, 5)


def torn(apart: np.ndarray, made: Image.Image) -> float:
    """A part torn in a made frame, read on how far its single passes disagree (`passes`): of the pixels where they are
    more than TORN_LEVEL apart, those in regions at least TORN_THICK pixels thick, and of them the largest share of a
    window TORN_WINDOW of the made frame's body height on a side (8 px at least; the windows step by a quarter of it). A
    hand the flow drags apart beside a tail swung far fills its window; an edge the passes place a pixel apart, or hair
    they redraw strand by strand, leaves it nearly empty. Read in a window, not over the body, so a small part torn on a
    large body is not lost in it. 0 where the frame has no solid body (docs/loop-repair.md section 4)."""
    alpha = np.asarray(made.convert("RGBA").getchannel("A"), dtype=np.float32) / 255.0
    rows = np.nonzero((alpha >= 0.5).any(axis=1))[0]
    if rows.size == 0:
        return 0.0
    side = max(8, int(round((rows[-1] - rows[0] + 1) * TORN_WINDOW)))
    thick = _mask(_mask(apart > TORN_LEVEL, TORN_THICK, ImageFilter.MinFilter), TORN_THICK, ImageFilter.MaxFilter)
    h, w = thick.shape
    if side >= min(h, w):
        return round(float(thick.sum()) / (side * side), 5)
    sums = np.pad(thick.astype(np.float64).cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)))
    step = max(1, side // 4)
    ys, xs = np.arange(0, h - side + 1, step)[:, None], np.arange(0, w - side + 1, step)[None, :]
    covered = sums[ys + side, xs + side] - sums[ys, xs + side] - sums[ys + side, xs] + sums[ys, xs]
    return round(float(covered.max()) / (side * side), 5)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Binary:
    """rife-ncnn-vulkan as the runner of a `Rife`: each run one call of the binary, its images written in a directory of
    its own for the frame. What its frames depend on is read anew every time it is asked (`identity`): the binary runs
    from its files on disk, and reads its model from them, every call."""

    def __init__(self, binary: Path, model: Path):
        self.binary, self.model = binary, model

    def frame(self, colour: list[np.ndarray], cover: list[np.ndarray], t: float) -> tuple[np.ndarray, np.ndarray]:
        with tempfile.TemporaryDirectory(prefix="sprite-gen-rife-") as td:
            return frame_runs(_binary_run(self.binary, self.model, Path(td)), colour, cover, t)

    def read(self, colour: list[np.ndarray], cover: list[np.ndarray], t: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with tempfile.TemporaryDirectory(prefix="sprite-gen-rife-") as td:
            run = _binary_run(self.binary, self.model, Path(td))
            return (*frame_runs(run, colour, cover, t), pass_runs(run, colour, cover, t))

    def describe(self) -> dict[str, str]:
        return {"kind": BINARY, "binary": str(self.binary), "model": self.model.name}

    def identity(self) -> dict[str, object]:
        """The binary's and each of the model's files by sha256, and the model's path as the binary is handed it (the
        binary reads the model's version from its name), read now. Files that cannot be read are a RIFE that fails
        (`RifeUnavailable`)."""
        try:
            model = sorted(p for p in self.model.iterdir() if p.is_file())
            return {"backend": "ncnn", "binary": _file_sha256(self.binary), "model": str(self.model),
                    "model_files": {p.name: _file_sha256(p) for p in model}}
        except OSError as exc:
            raise RifeUnavailable(f"{self.binary.name}: the binary or model it runs from could not be read: {exc}") from exc


class Unreadable(ValueError):
    """Bytes that are not a request or an answer between a `RemoteRunner` and its executor (`unpack`), or a request or an
    answer that is not what it is to be (`_asked`, `RemoteRunner`): the message names why, and where reading the bytes
    raised, what it raised is the cause."""


def pack(header: dict, arrays: dict[str, np.ndarray] | None = None) -> bytes:
    """A request or an answer between a `RemoteRunner` and its executor as bytes: `header` as UTF-8 JSON and `arrays`, in one
    compressed NumPy archive."""
    out = io.BytesIO()
    np.savez_compressed(out, header=np.frombuffer(json.dumps(header).encode(), np.uint8), **(arrays or {}))
    return out.getvalue()


def unpack(data: bytes) -> tuple[dict, dict[str, np.ndarray]]:
    """`pack` read back: (header, arrays). Anything else is `Unreadable`, naming why — and whatever reading the bytes raises
    on the way is that: a member cut short, a block that does not decompress, a CRC-32 that does not match, a header that
    does not parse, pickled objects (which are never loaded); what it raised is named and kept as the cause. Only where
    `pack`'s own bytes do not read either is it the reading that fails, not the bytes: a fault of the code, raised as it
    is."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise Unreadable(f"{type(data).__name__}, not bytes")
    try:
        return _unpacked(bytes(data))
    except Unreadable:
        raise
    except Exception as exc:
        if not _reads_pack():
            raise
        kind = type(exc)
        named = kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"
        raise Unreadable(f"{named}: {exc}" if str(exc) else named) from exc


def _unpacked(data: bytes) -> tuple[dict, dict[str, np.ndarray]]:
    """`unpack`'s reading: the bytes opened as a zip archive, each member read whole — so its CRC-32 is checked — and only
    then read as a NumPy array, pickled objects refused; the header as JSON. NumPy's own archive reader stops where a
    member's header says its array ends: a header damaged to a shorter length gives the array read from the wrong place,
    its CRC-32 never reached."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    arrays = {}
    for name, member in members:
        if not name.endswith(".npy"):
            raise Unreadable(f"{name} is not an array")
        arrays[name.removesuffix(".npy")] = np.lib.format.read_array(io.BytesIO(member), allow_pickle=False)
    if "header" not in arrays:
        raise Unreadable("it has no header")
    header = json.loads(arrays.pop("header").tobytes().decode("utf-8"))
    if not isinstance(header, dict):
        raise Unreadable("its header is not an object")
    return header, arrays


def _reads_pack() -> bool:
    """Whether `unpack`'s reading reads what `pack` writes."""
    try:
        _unpacked(pack({}))
    except Exception:
        return False
    return True


def _differs(held: dict, other: object) -> str:
    """Where another identity is not the one held, field by field (`gpu: Tesla T4 -> NVIDIA L4`)."""
    other = other if isinstance(other, dict) else {}
    return ", ".join(f"{k}: {held.get(k)} -> {other.get(k)}" for k in sorted(set(held) | set(other)) if held.get(k) != other.get(k))


def _asked(header: dict, arrays: dict[str, np.ndarray]) -> tuple[list[np.ndarray], list[np.ndarray], float, tuple[str, ...], bool]:
    """A frame's or a read's request, as `RemoteRunner` sends it: (colour, coverage, t, the augmentation, whether the passes
    are read), or `Unreadable` naming what is not so."""
    t, modes, read = header.get("t"), header.get("modes"), header.get("read")
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        raise Unreadable(f"t is {t!r}, not a number")
    if (not isinstance(modes, list) or any(not isinstance(m, str) or m not in TTA_MODES for m in modes)
            or len(set(modes)) < len(modes)):
        raise Unreadable(f"the augmentation is {modes!r}, not a list of {' or '.join(TTA_MODES)}, each once")
    if not isinstance(read, bool):
        raise Unreadable(f"read is {read!r}, not true or false")
    cover = arrays.get("cover_a")
    if cover is None or cover.ndim != 2:
        raise Unreadable("cover_a is not an image of one channel")
    h, w = cover.shape
    for name, shape in (("colour_a", (h, w, 3)), ("colour_b", (h, w, 3)), ("cover_a", (h, w)), ("cover_b", (h, w))):
        got = arrays.get(name)
        if got is None or got.shape != shape or got.dtype != np.uint8:
            raise Unreadable(f"{name} is {'missing' if got is None else f'{got.shape} {got.dtype}'}, not {shape} uint8")
    return [arrays["colour_a"], arrays["colour_b"]], [arrays["cover_a"], arrays["cover_b"]], float(t), tuple(modes), read


def answer(request: bytes, runner: Callable[[], object]) -> bytes:
    """The executor's answer to a request of a `RemoteRunner`, as bytes (`rife_torch.serve`): the frame or the read made by
    the runner that `runner()` loads, the torch backend. In order, before anything runs: a request that does not read,
    another protocol or another engine code (this rife.py, by sha256) is answered so, nothing loaded; the probe is answered
    by the runner's identity and the protocol, as it is loaded; a frame or a read that does not `expect` that identity is
    answered by it, nothing made. Then the frame's two runs, and a read's sixteen passes and their disagreement as well
    (`frame_runs`, `pass_runs`), with the augmentation the request names. A RIFE that fails here, loading or running, is
    answered in its words; anything else is raised, a fault of the code."""
    try:
        header, arrays = unpack(request)
    except Unreadable as exc:
        return pack({"error": "failed", "message": f"the request does not read: {exc}"})
    if header.get("protocol") != PROTOCOL:
        return pack({"error": "protocol", "protocol": PROTOCOL})
    if header.get("code") != CODE_SHA256:
        return pack({"error": "code", "code": CODE_SHA256})
    try:
        loaded = runner()
        own = json.loads(json.dumps({"protocol": PROTOCOL, **loaded.identity()}))  # type: ignore[attr-defined]
    except RifeUnavailable as exc:
        return pack({"error": "failed", "message": str(exc)})
    if header.get("probe") is True:
        return pack({"identity": own})
    if header.get("expect") != own:
        return pack({"error": "identity", "identity": own})
    try:
        colour, cover, t, modes, read = _asked(header, arrays)
    except Unreadable as exc:
        return pack({"error": "failed", "message": f"the request does not read: {exc}"})

    def run(x: np.ndarray, y: np.ndarray, at: float, named: tuple[str, ...] | None) -> np.ndarray:
        return loaded.run(x, y, at, modes if named is None else named)  # type: ignore[attr-defined]

    try:
        rgb, alpha = frame_runs(run, colour, cover, t)
        made = {"rgb": rgb, "alpha": alpha}
        if read:
            made["apart"] = pass_runs(run, colour, cover, t)
    except RifeUnavailable as exc:
        return pack({"error": "failed", "message": str(exc)})
    return pack({"identity": own}, made)


class RemoteRunner:
    """The torch backend on a GPU elsewhere, as the runner of a `Rife` (SPRITE_GEN_RIFE_EXECUTOR): `send`, the host's, from a
    request's bytes to its answer's (`pack`), reaches the same sprite-gen there (`rife_torch.serve`, `answer`). A frame is
    one call, its two runs made there; a read is one call, its two runs and sixteen passes made, and the passes reduced to
    their disagreement, there. It needs no PyTorch here.

    Made, it asks the executor what it is (the probe) and holds the answer for its life: the identity of the torch backend
    it runs (`rife_torch.TorchRunner.identity`) and the protocol, the executor named besides (`identity`). Every request
    carries it (`expect`): an executor that is not it any more makes nothing and says what differs, and an answer from
    another is not handed on — so every frame handed on, and every frame a `Cache` keeps under its identity, is that
    executor's. An executor whose protocol or rife.py is another is refused as it is made. Nothing is asked again or run
    elsewhere: a send that fails, an answer that does not read — however it does not (`_read`) — and the executor's own
    failure are each a RIFE that fails (`RifeUnavailable`). Counts its calls of the send (`calls`)."""

    def __init__(self, send: Callable[[bytes], bytes], name: str):
        self.send, self.name = send, name
        self.calls = 0
        self.remote: dict = {}
        self.remote = self._sent({"probe": True}, {}, {})[0]

    def frame(self, colour: list[np.ndarray], cover: list[np.ndarray], t: float) -> tuple[np.ndarray, np.ndarray]:
        made = self._made(colour, cover, t, read=False)
        return made["rgb"], made["alpha"]

    def read(self, colour: list[np.ndarray], cover: list[np.ndarray], t: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        made = self._made(colour, cover, t, read=True)
        return made["rgb"], made["alpha"], made["apart"]

    def describe(self) -> dict[str, str]:
        return {"kind": "rife-torch", "executor": self.name, "device": str(self.remote.get("device")),
                **({"gpu": str(self.remote["gpu"])} if "gpu" in self.remote else {})}

    def identity(self) -> dict[str, object]:
        return {**self.remote, "executor": self.name}

    def _made(self, colour: list[np.ndarray], cover: list[np.ndarray], t: float, *, read: bool) -> dict[str, np.ndarray]:
        """A frame, or a read, made by the executor in one call: the request names the identity held, the fraction, the
        augmentation this host asks for now (`tta()`) and whether the passes are read; the answer is that identity's, at
        the frame's size, or it is not handed on."""
        h, w = cover[0].shape
        want = {"rgb": ((h, w, 3), np.uint8), "alpha": ((h, w), np.uint8), **({"apart": ((h, w), np.float32)} if read else {})}
        return self._sent({"expect": self.remote, "t": t, "modes": list(tta()), "read": read},
                          {"colour_a": colour[0], "colour_b": colour[1], "cover_a": cover[0], "cover_b": cover[1]}, want)[1]

    def _sent(self, header: dict, arrays: dict[str, np.ndarray],
              want: dict[str, tuple[tuple[int, ...], type]]) -> tuple[dict, dict[str, np.ndarray]]:
        """One call of the send, this engine's protocol and code (CODE_SHA256) heading the request, and its answer read
        (`_read`: the identity it names and its arrays, `want`'s at their shapes and dtypes). An answer that does not read,
        however it does not, is a RIFE that fails here, naming why in the reading's words, what reading it raised the
        cause."""
        request = pack({"protocol": PROTOCOL, "code": CODE_SHA256, **header}, arrays)
        self.calls += 1
        try:
            data = self.send(request)
        except Exception as exc:  # what carries the bytes is the host's, failing in its own words
            raise RifeUnavailable(f"{self.name}: sending to the executor failed: {exc}") from exc
        try:
            return self._read(data, header.get("expect"), want)
        except Unreadable as exc:
            raise RifeUnavailable(f"{self.name}: the executor's answer does not read: {exc}") from (exc.__cause__ or exc)

    def _read(self, data: bytes, expect: dict | None,
              want: dict[str, tuple[tuple[int, ...], type]]) -> tuple[dict, dict[str, np.ndarray]]:
        """The executor's answer read whole (`unpack`): the identity it names — the one the request `expect`s, where it
        names one — and its arrays, each `want` names there at its shape and dtype. One that is not so is `Unreadable`;
        the refusal or the failure it names, and an answer from another identity, are each a RIFE that fails."""
        answered, got = unpack(data)
        error = answered.get("error")
        if error == "protocol":
            raise RifeUnavailable(f"{self.name}: the executor speaks protocol {answered.get('protocol')}, this one {PROTOCOL}: "
                                  "run the same sprite-gen on both sides")
        if error == "code":
            raise RifeUnavailable(f"{self.name}: the executor's sprite-gen rife.py is {answered.get('code')}, this one's "
                                  f"{CODE_SHA256}: run the same sprite-gen on both sides")
        if error == "identity":
            raise RifeUnavailable(f"{self.name}: the executor is not the one this RIFE was made with "
                                  f"({_differs(self.remote, answered.get('identity'))}) and made nothing; a RIFE made now "
                                  "runs as it is")
        if error is not None:
            raise RifeUnavailable(f"{self.name}: the executor failed: {answered.get('message', error)}")
        identity = answered.get("identity")
        if not isinstance(identity, dict):
            raise Unreadable("it names no identity")
        if expect is not None and identity != expect:
            raise RifeUnavailable(f"{self.name}: the executor answered as another than this RIFE runs as "
                                  f"({_differs(expect, identity)}); its frame is not handed on")
        for name, (shape, dtype) in want.items():
            found = got.get(name)
            if found is None or found.shape != shape or found.dtype != dtype:
                raise Unreadable(f"{name} is {'missing' if found is None else f'{found.shape} {found.dtype}'}, not {shape} "
                                 f"{np.dtype(dtype)}")
        return identity, got


def torch_runner(model: Path | None = None) -> object:
    """The torch backend's runner (`rife_torch.TorchRunner`) on `device()`, from `model` (else `locate_model()`): the device
    named is refused before anything is imported, PyTorch not installed is `RifeNotInstalled` with the extra that installs
    it."""
    named = device()
    try:
        rife_torch = importlib.import_module("sprite_gen.video.rife_torch")
    except ImportError as exc:
        raise RifeNotInstalled(f"SPRITE_GEN_RIFE_BACKEND=torch, and PyTorch cannot be imported ({exc}); install it and the "
                               f"model {MODEL} with `{install_command()}`; see docs/loop-repair.md") from exc
    except OSError as exc:  # installed, and its libraries do not load: a RIFE found that fails
        raise RifeUnavailable(f"SPRITE_GEN_RIFE_BACKEND=torch, and PyTorch failed to load: {exc}") from exc
    return rife_torch.TorchRunner(model or locate_model(), named)


class Rife:
    """A located RIFE as an `Interpolate` callable, and as a `Read` (`read`). Counts the frames it made (`made`) and the
    times it ran RIFE (`calls`): the binary's calls, or the torch backend's runs.

    What runs it is its runner, which makes a frame whole from what `inputs` gives it (`frame`: two runs; `read`: two
    runs and the sixteen passes) and names what its frames depend on (`identity`): `Binary` for the binary,
    `rife_torch.TorchRunner`, or `RemoteRunner` for the torch backend on a GPU elsewhere. `Rife()` takes the one
    `backend()` and `executor()` name; `Rife(binary, model)` is the binary at those paths; `Rife(runner=...)` any other."""

    def __init__(self, binary: Path | None = None, model: Path | None = None, *, runner: object | None = None):
        if runner is None:
            if binary is not None and model is not None:
                runner = Binary(binary, model)
            elif (named := executor()) is not None:
                runner = RemoteRunner(executor_send(named), named)
            elif backend() == "ncnn":
                runner = Binary(*locate())
            else:
                runner = torch_runner()
        self.runner = runner
        self.binary, self.model = getattr(runner, "binary", None), getattr(runner, "model", None)
        self.tta = tta()  # refused here, before a frame is asked for
        self.made = 0
        self.calls = 0

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        frame = _joined(*self.runner.frame(*inputs(a, b), t))  # type: ignore[attr-defined]
        self.made += 1
        self.calls += 2
        return frame

    def read(self, a: Image.Image, b: Image.Image, t: float) -> tuple[Image.Image, np.ndarray]:
        """The frame at `t`, made as every call makes it (`between`), and how far the sixteen single passes disagree
        there (`passes`): 2 + PASS_CALLS runs of RIFE."""
        rgb, alpha, apart = self.runner.read(*inputs(a, b), t)  # type: ignore[attr-defined]
        self.made += 1
        self.calls += 2 + PASS_CALLS
        return _joined(rgb, alpha), apart

    def describe(self) -> dict[str, str]:
        """What made the frames, for a report: its `kind` (`rife-ncnn-vulkan`, `rife-torch`), where it runs from, and the
        augmentation."""
        return {**self.runner.describe(), "tta": " ".join(self.tta)}  # type: ignore[attr-defined]

    def counts(self) -> dict[str, int]:
        """The runs it made, as a report names them: the binary's calls (`binary_calls`), or the torch backend's runs
        (`runs`) and, on an executor, the calls of its send (`executor_calls`, the probe one of them)."""
        counted = {"binary_calls" if isinstance(self.runner, Binary) else "runs": self.calls}
        if isinstance(self.runner, RemoteRunner):
            counted["executor_calls"] = self.runner.calls
        return counted

    def identity(self) -> str:
        """What a frame this RIFE makes depends on besides its two frames and t, as one string (`Cache` keys
        on it): its runner's (`identity()` of the runner — for the binary its files, read again on every call, so a
        binary or model changed under this very RIFE is another identity; for the torch backend what it loaded and runs
        on, the same for its life; on an executor what the executor answered as this RIFE was made, held for its life and
        enforced on every frame it makes, and the executor's name) and this host's, read as it is now on every call: the augmentation every call asks
        for now (`tta()`: SPRITE_GEN_RIFE_TTA is read on every call, and another is another frame); the engine code this
        process loaded (`CODE_SHA256`: it turns a frame into RIFE's two runs and back) and NumPy's and
        Pillow's versions; and the machine — the OS, and `uname`'s host name, OS release and build and
        architecture (platform.uname() keeps its first answer) — as the Apple GPU and llvmpipe are only measured
        to agree to the third decimal of an error (docs/loop-repair.md section 1), so a frame made on one is not
        taken on another."""
        return json.dumps({
            **self.runner.identity(), "tta": list(tta()), "code": CODE_SHA256, "numpy": np.__version__,  # type: ignore[attr-defined]
            "pillow": PIL.__version__, "machine": [sys.platform, *(os.uname() if hasattr(os, "uname") else platform.uname()[:5])]},
            sort_keys=True)


def _kept(path: Path, size: tuple[int, int]) -> Image.Image | None:
    """The frame kept at `path`, or None where it is not one: not a PNG, not RGBA at `size`, cut short, or
    its pixels not the ones its PIXELS_TEXT names."""
    try:
        with Image.open(path) as im:
            if im.format != "PNG" or im.mode != "RGBA" or im.size != size:
                return None
            im.load()
            data, named = im.tobytes(), im.info.get(PIXELS_TEXT)
    except (OSError, SyntaxError, ValueError, EOFError):
        return None
    if named != hashlib.sha256(data).hexdigest():
        return None
    return Image.frombytes("RGBA", size, data)


class Cache:
    """Frames made between two frames, kept in `directory` and taken from there when the same frame is
    asked for again (`video-cycle-align --between-cache`, docs/loop-repair.md section 4).

    A frame is kept under the sha256 of everything it is made from: CACHE_FORMAT, the interpolator's
    `identity()`, each of the two frames' mode, size and bytes, and `t` exactly. Another frame, fraction,
    RIFE or machine is another key, so a frame kept for other inputs is never taken. The key is read
    for every frame asked for, and once more after a frame is made: a frame made while what it is made
    from changed (a binary or model written over between RIFE's two runs) is handed on as made but not
    kept, as no key names what made it, counted `not_kept`. Frames are kept for RGBA frames only: their
    mode, size and bytes are all of them, where a palette or a transparent colour is not in an image's
    bytes. A kept frame is a PNG naming its own pixels' sha256, written whole or not at all; a file that
    does not read back as that frame (cut short, written over) is made again and kept over it, counted
    `unreadable`. A frame taken is a new image of the kept pixels, as the interpolator's own frame is.

    Nothing is kept unless a caller names the directory, and nothing here removes it: it belongs to the
    caller, who gives each alignment its own. It is created readable by this user only. Counts the frames
    taken (`hits`) and made (`misses`)."""

    def __init__(self, directory: Path):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.hits = self.misses = self.unreadable = self.not_kept = 0

    def key(self, interpolate: Interpolate, a: Image.Image, b: Image.Image, t: float) -> str:
        for f in (a, b):
            if f.mode != "RGBA":
                raise ValueError(f"rife: a frame between is kept for RGBA frames only, not {f.mode}: a palette or a "
                                 "transparent colour is not in an image's bytes, which are what it is kept under")
        h = hashlib.sha256()
        for part in (CACHE_FORMAT, interpolate.identity(), a.mode, *map(str, a.size), b.mode, *map(str, b.size), float(t).hex()):  # type: ignore[attr-defined]
            h.update(part.encode() + b"\0")
        h.update(a.tobytes())
        h.update(b.tobytes())
        return h.hexdigest()

    def frame(self, interpolate: Interpolate, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        """The frame at `t` between `a` and `b`: the one kept for exactly these, else `interpolate`'s — kept where
        its key reads the same once it is made."""
        key = self.key(interpolate, a, b, t)
        path = self.directory / f"{key}.png"
        if path.exists():
            kept = _kept(path, a.size)
            if kept is not None:
                self.hits += 1
                return kept
            self.unreadable += 1
        made = interpolate(a, b, t)
        if made.mode != "RGBA" or made.size != a.size:
            raise ValueError(f"rife: a frame between is kept as RGBA at its frames' size {a.size}, not {made.mode} at {made.size}")
        self.misses += 1
        if self.key(interpolate, a, b, t) != key:
            self.not_kept += 1
            return made
        # saved from a copy of its pixels, so the frame handed on carries nothing of this save
        pixels = made.tobytes()
        info = PngImagePlugin.PngInfo()
        info.add_text(PIXELS_TEXT, hashlib.sha256(pixels).hexdigest())
        fd, staged = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as out:
                Image.frombytes("RGBA", made.size, pixels).save(out, format="PNG", pnginfo=info)
            os.replace(staged, path)
        except BaseException:
            Path(staged).unlink(missing_ok=True)
            raise
        return made

    def record(self) -> dict[str, object]:
        return {"dir": str(self.directory), "hits": self.hits, "misses": self.misses, "unreadable": self.unreadable,
                "not_kept": self.not_kept}
