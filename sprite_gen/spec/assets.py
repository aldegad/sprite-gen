# SPDX-License-Identifier: Apache-2.0
"""Read-only adapters for external frames, loop strips and runtime atlases.

The returned sequence is an in-memory snapshot, never another editable asset store.
Durations are seconds; the anchor is in source pixels, defaulting to bottom centre.

Every source file is read from disk exactly once. Frames are decoded and
descriptors parsed from those bytes, and ``fingerprints()`` hashes the very same
bytes, so a file replaced on disk after loading can neither change the frames of
an existing sequence nor make its fingerprints agree with the new file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path

from PIL import Image


def finite(value, name: str, *, positive=False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be {'positive and ' if positive else ''}finite")
    return result


def sequence_identity(sequence) -> dict:
    """The facts a measurement of ``sequence`` is bound to.

    A consumer that applies a report (for example a scene stride) must find the
    same source, atlas state, frame-rate override, frame count, canvas size,
    anchor and per-frame durations in the sequence it selected, in addition to
    matching ``fingerprints()``. Two states of one atlas share files and hashes
    but not this identity; so do one sequence loaded at two frame rates.
    """
    metadata = getattr(sequence, "metadata", None) or {}
    size = sequence.frames[0].size
    return {
        "source": metadata.get("source"),
        "state": metadata.get("state"),
        "fps_override": metadata.get("fps_override"),
        "frame_count": len(sequence.frames),
        "size": [int(size[0]), int(size[1])],
        "anchor_px": [float(v) for v in sequence.anchor],
        "durations_seconds": [float(v) for v in sequence.durations],
    }


def same_identity(reported, sequence) -> bool:
    """True when ``reported`` (a JSON round trip of ``sequence_identity``) names ``sequence``.

    The effective per-frame durations are compared, not how they were specified:
    a frame-rate override that reproduces the native timing measured the same
    motion. ``fps_override`` stays in the record for readers.
    """
    if not isinstance(reported, dict):
        return False
    expected = sequence_identity(sequence)
    if set(reported) != set(expected):
        return False
    for key in ("source", "state", "frame_count", "size"):
        if reported[key] != expected[key]:
            return False
    for key in ("anchor_px", "durations_seconds"):
        a, b = reported[key], expected[key]
        if not isinstance(a, list) or len(a) != len(b) or not all(_close(x, y) for x, y in zip(a, b)):
            return False
    return True


def _close(a, b) -> bool:
    try:
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class FrameSequence:
    """An in-memory frame sequence: ``frames, durations, anchor, source_files, metadata``.

    ``anchor`` defaults to the bottom centre of the frame canvas. A sequence
    built from files also carries, keyword-only, the SHA-256 of the bytes each
    source file contributed (``source_digests``, one per ``source_files`` entry);
    a manually constructed sequence that names source files without those
    digests is refused, so nothing unverifiable can pose as file-backed.
    """
    frames: tuple[Image.Image, ...]
    durations: tuple[float, ...]
    anchor: tuple[float, float] | None = None
    source_files: tuple[Path, ...] = ()
    metadata: dict = field(default_factory=dict)
    source_digests: tuple[str, ...] = field(default=(), kw_only=True)

    def __post_init__(self):
        if not self.frames or len(self.frames) != len(self.durations):
            raise ValueError("asset needs one positive duration per frame")
        if any(f.size != self.frames[0].size for f in self.frames):
            raise ValueError("asset frames must share a canvas size")
        for d in self.durations:
            finite(d, "frame duration", positive=True)
        if self.anchor is None:
            object.__setattr__(self, "anchor", (self.frames[0].width / 2, self.frames[0].height))
        if not isinstance(self.anchor, (tuple, list)) or len(self.anchor) != 2:
            raise ValueError("asset anchor needs [x, y]")
        object.__setattr__(self, "anchor", tuple(finite(v, "anchor") for v in self.anchor))
        if len(self.source_files) != len(self.source_digests):
            raise ValueError("each source file needs the SHA-256 of the bytes decoded from it")

    @property
    def size(self):
        return self.frames[0].size

    @property
    def duration(self):
        return sum(self.durations)

    def frame_at(self, t: float, loop=True):
        t = finite(t, "frame time")
        if loop:
            t %= self.duration
        elif t >= self.duration:
            return self.frames[-1]
        t = max(0, t)
        end = 0.0
        for frame, duration in zip(self.frames, self.durations):
            end += duration
            if t < end - 1e-10:
                return frame
        return self.frames[-1]

    def fingerprints(self):
        """SHA-256 of the exact bytes this sequence was decoded from, keyed by resolved path.

        The digests were taken from the bytes read at load time, never from a
        later read of the files, so they describe these frames and nothing else.
        """
        return {str(path): digest for path, digest in zip(self.source_files, self.source_digests)}

    def identity(self) -> dict:
        return sequence_identity(self)


def _read_bytes(path: Path) -> bytes:
    """The single place a source file is read; every decode and hash uses its result."""
    return Path(path).read_bytes()


# One decoded source image (a frame, a loop strip or an atlas sheet) may hold at
# most this many pixels: 256 MB of RGBA, below Pillow's decompression-bomb
# warning, so a hostile header is refused before any pixel data is decoded.
MAX_SOURCE_PIXELS = 67_108_864


def _decode_image(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("animated image input requires an explicit frame descriptor")
        width, height = image.size
        if width < 1 or height < 1 or width * height > MAX_SOURCE_PIXELS:
            raise ValueError(f"source image {width}x{height} exceeds the supported {MAX_SOURCE_PIXELS} pixels")
        return image.convert("RGBA")


class _Snapshot:
    """Bytes of every file touched by one load, each read once, with their digests."""

    def __init__(self):
        self.digests: dict[Path, str] = {}
        self._bytes: dict[Path, bytes] = {}

    def read(self, path: Path) -> bytes:
        if path not in self._bytes:
            data = _read_bytes(path)
            self._bytes[path] = data
            self.digests[path] = hashlib.sha256(data).hexdigest()
        return self._bytes[path]

    def image(self, path: Path) -> Image.Image:
        return _decode_image(self.read(path))

    def json(self, path: Path):
        return json.loads(self.read(path).decode("utf-8"))


def _choose(rows, state):
    if not isinstance(rows, dict) or not rows:
        raise ValueError("asset has no animation states")
    if state is None:
        if len(rows) != 1:
            raise ValueError("--state is required for a multi-state atlas")
        state = next(iter(rows))
    if state not in rows:
        raise ValueError(f"unknown asset state: {state}")
    return state


def _integer(value, name):
    number = finite(value, name, positive=True)
    if number != int(number):
        raise ValueError(f"{name} must be an integer")
    return int(number)


def load_asset(source: Path, *, state=None, fps=None, anchor=None) -> FrameSequence:
    source = Path(source).expanduser().resolve()
    snapshot = _Snapshot()
    meta = {"source": str(source)}
    declared_anchor = None
    if source.suffix.lower() != ".json":
        if state is not None:
            raise ValueError("--state is only valid for atlas input")
        frames = [snapshot.image(source)]
        durations = [1.0]
    else:
        data = snapshot.json(source)
        if not isinstance(data, dict):
            raise ValueError("asset descriptor must be an object")
        declared_anchor = data.get("anchor")
        if data.get("kind") == "sprite-gen-asset":
            if data.get("version") != 1 or state is not None:
                raise ValueError("external asset requires version 1 and no --state")
            entries = data.get("frames")
            if not isinstance(entries, list) or not entries:
                raise ValueError("asset frames must be a nonempty ordered list")
            frames, durations = [], []
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                    raise ValueError("each asset frame requires file and duration")
                frames.append(snapshot.image((source.parent / entry["file"]).resolve()))
                durations.append(finite(entry.get("duration"), "frame duration", positive=True))
        elif "frame_layout" in data:
            layout = data["frame_layout"]
            state = _choose(layout.get("rows"), state)
            sheet = snapshot.image((source.parent / data["sprite_sheet_alpha"]).resolve())
            frames = []
            for rect in layout["rows"][state]:
                x, y = finite(rect["x"], "rect x"), finite(rect["y"], "rect y")
                w, h = _integer(rect["w"], "rect width"), _integer(rect["h"], "rect height")
                if int(x) != x or int(y) != y or min(x, y) < 0 or x+w > sheet.width or y+h > sheet.height:
                    raise ValueError("atlas frame rectangle outside sheet")
                frames.append(sheet.crop((int(x), int(y), int(x)+w, int(y)+h)))
            anim = data["animation"]["rows"][state]
            if "durations_ms" in anim:
                durations = [finite(v, "duration_ms", positive=True) / 1000 for v in anim["durations_ms"]]
            else:
                durations = [1 / finite(anim.get("fps"), "atlas fps", positive=True)] * len(frames)
            meta.update(state=state, loop=bool(anim.get("loop", True)))
        elif all(k in data for k in ("frames", "w", "h", "delay_ms")):
            if state is not None:
                raise ValueError("--state is only valid for atlas input")
            sheet = snapshot.image(source.with_suffix(".png"))
            n, w, h = (_integer(data[k], k) for k in ("frames", "w", "h"))
            if sheet.size != (n * w, h):
                raise ValueError("loop strip size disagrees with its sidecar")
            frames = [sheet.crop((i*w, 0, (i+1)*w, h)) for i in range(n)]
            durations = [finite(data["delay_ms"], "delay_ms", positive=True)/1000] * n
            meta["loop"] = True
        else:
            raise ValueError("unsupported asset descriptor; use sprite-gen-asset, loop strip sidecar or atlas manifest")
    if not frames:
        raise ValueError("asset requires at least one frame")
    if fps is not None:
        durations = [1 / finite(fps, "fps", positive=True)] * len(frames)
        meta["fps_override"] = float(fps)
    point = anchor if anchor is not None else declared_anchor
    if point is None:
        point = (frames[0].width / 2, frames[0].height)
        meta["anchor_source"] = "bottom-center default"
    else:
        if not isinstance(point, (tuple, list)) or len(point) != 2:
            raise ValueError("anchor must be [x, y]")
        point = tuple(finite(v, "anchor") for v in point)
        meta["anchor_source"] = "explicit override" if anchor is not None else "asset metadata"
    return FrameSequence(tuple(frames), tuple(durations), tuple(point), tuple(snapshot.digests), meta,
                         source_digests=tuple(snapshot.digests.values()))
