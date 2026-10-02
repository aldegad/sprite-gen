"""`video-set --base` accepts only the directions the prompt table knows.

An undocumented key such as `left=` used to be accepted silently and then
animated with the default facing, so the caller's intent was inverted without
a word. The parser now names the allowed directions and refuses the rest.
"""
from __future__ import annotations

import pytest

from sprite_gen.video import batch


def test_known_directions_parse(tmp_path):
    still = tmp_path / "s.png"
    still.write_bytes(b"")
    bases = batch._parse_bases([f"side={still}", f"front={still}", f"back={still}"])
    assert set(bases) == {"side", "front", "back"}


@pytest.mark.parametrize("key", ["left", "right", "Side", "profile"])
def test_unknown_direction_is_refused_by_name(key, tmp_path):
    with pytest.raises(SystemExit) as exc:
        batch._parse_bases([f"{key}={tmp_path / 's.png'}"])
    message = str(exc.value)
    assert repr(key) in message and "back, back_diagonal, front, front_diagonal, side" in message


def test_missing_equals_still_refused():
    with pytest.raises(SystemExit):
        batch._parse_bases(["side"])
