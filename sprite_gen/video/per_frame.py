# SPDX-License-Identifier: Apache-2.0
"""What is read of one frame of a cut, or of two together, kept by the frames' pixels.

A frame made between two frames of a cut is judged once a try (`held_fill.judge`), and every try of a cell, and every
cell between the same two frames, is judged against the same two: what the judge reads of them alone is the same each
time. A `Kept` holds such values, each under the frames' pixels (their mode, size and a digest of their bytes, so a
frame with other pixels is read again whatever object it is), the latest `size` of them; a size of 0 keeps none. The
values are those the reading function returns, its arrays made read-only so no caller changes one another reads.
`MEMOS` names every `Kept`."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from PIL import Image

from sprite_gen._deps import np

T = TypeVar("T")
MEMOS: list[Kept] = []


def pixels(frame: Image.Image) -> bytes:
    """A key for a frame's pixels: its mode and size and a digest of its bytes."""
    return f"{frame.mode} {frame.size} ".encode() + hashlib.blake2b(frame.tobytes(), digest_size=16).digest()


def _read_only(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        value.setflags(write=False)
    elif isinstance(value, tuple):
        for item in value:
            _read_only(item)
    return value


class Kept:
    """Values read of frames, under the frames' pixels: the latest `size`."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._values: OrderedDict[tuple[bytes, ...], Any] = OrderedDict()
        MEMOS.append(self)

    def __call__(self, frames: Sequence[Image.Image], read: Callable[[], T]) -> T:
        """`read()`, the value of `frames`: kept where these pixels were read before, read and kept where not."""
        if self.size <= 0:
            return read()
        key = tuple(pixels(f) for f in frames)
        if key in self._values:
            self._values.move_to_end(key)
            return self._values[key]
        value = _read_only(read())
        self._values[key] = value
        while len(self._values) > self.size:
            self._values.popitem(last=False)
        return value

    def clear(self) -> None:
        self._values.clear()
