# SPDX-License-Identifier: Apache-2.0
"""Read delivered animation bytes and prove the strip-to-playback mapping."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.video import evidence
from sprite_gen.util.gif_utils import _prepare_transparent_frame


def visible_bytes(frame: Image.Image) -> bytes:
    """GIF has no RGB under transparency; compare its displayed RGBA instead."""
    out = Image.new("RGBA", frame.size)
    out.paste(frame, mask=frame.getchannel("A").point(lambda a: 255 if a else 0))
    return out.tobytes()


@dataclass
class Playback:
    frames: list[Image.Image]
    durations: list[int]
    loop: int | None
    format: str
    artifact: dict[str, Any]

    @classmethod
    def read(cls, path: Path, format: str) -> Playback:
        blob = path.read_bytes()
        with Image.open(io.BytesIO(blob)) as im:
            if im.format != format:
                raise ValueError(f"playback must be {format}")
            repeat = im.info.get("loop")
            frames, durations = [], []
            for k in range(getattr(im, "n_frames", 1)):
                im.seek(k)
                frame = im.convert("RGBA")  # WebP publishes duration when loaded.
                duration = im.info.get("duration")
                if not isinstance(duration, int) or duration <= 0:
                    raise ValueError("playback has no positive integer duration")
                frames.append(frame)
                durations.append(duration)
        return cls(frames, durations, repeat, format,
                   {"sha256": evidence.digest(blob), "bytes": len(blob)})

    def describe(self) -> dict[str, Any]:
        return {"frames": len(self.frames), "durations_ms": self.durations, "loop": self.loop,
                "format": self.format, "pixels_sha256": [evidence.digest(visible_bytes(f)) for f in self.frames]}


def mapping(loop: Any) -> tuple[list[int], int] | None:
    """The recorded uniform export recipe, also used by legacy video-loop."""
    n = len(loop.frames)
    count, delay = (loop.source_report.get(k) for k in ("n_out", "delay_ms"))
    if (type(count) is not int or not 2 <= count <= n or type(delay) is not int or delay < 20
            or loop.delay is None or loop.timing_issue()):
        return None
    if delay != max(20, round(1000 * loop.meta["cycle_seconds"] / count)):
        return None
    return [min(n - 1, round(k * n / count)) for k in range(count)], delay


def verify(loop: Any, animations: dict[str, Playback]) -> tuple[dict[str, Any], str | None]:
    recipe = mapping(loop)
    if recipe is None:
        return {}, "playback-schedule-unverified"
    indices, delay = recipe
    records = {}
    for key, fmt in (("gif", "GIF"), ("webp", "WEBP")):
        if key not in animations:
            return records, "playback-missing"
        animation = animations[key]
        expected_delay = delay // 10 * 10 if fmt == "GIF" else delay
        if (animation.format != fmt or animation.loop != 0 or len(animation.frames) != len(indices)
                or animation.durations != [expected_delay] * len(indices)):
            return records, f"{key}:playback-schedule-mismatch"
        for frame, index in zip(animation.frames, indices):
            cell = loop.frames[index]
            expected = _prepare_transparent_frame(cell, 128).convert("RGBA") if fmt == "GIF" else cell
            if frame.size != cell.size or visible_bytes(frame) != visible_bytes(expected):
                return records, f"{key}:playback-pixels-mismatch"
        records[key] = {**animation.describe(), "strip_indices": indices, "exact_export": True}
    return records, None
