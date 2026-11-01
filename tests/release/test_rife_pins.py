# SPDX-License-Identifier: Apache-2.0
"""`as_pinned` (tests/conftest.py), how a test that reaches the binary holds its count of the binary's calls: rife-v4.6's
exactly, rife-v4.25's from its floor, the calls of a cut where no frame is refused, to its top, the most an Apple GPU and
llvmpipe made. A cut that asks the binary for a frame fewer than its floor, as one whose frame was made without RIFE
does, fails its pin; so does one that asks for none."""
import pytest

import conftest
from conftest import as_pinned
from sprite_gen.video import rife

PIN = {"rife-v4.25": (16, 24), "rife-v4.6": 16}


@pytest.fixture
def runs(monkeypatch):
    """The binary located here, running the model named."""
    monkeypatch.setattr(conftest, "rife_located", lambda: True)

    def model(name):
        monkeypatch.setattr(rife, "model_name", lambda: name)

    return model


def test_rife_v4_25_is_held_from_its_floor_to_its_top(runs):
    runs("rife-v4.25")
    assert [as_pinned(called, PIN) for called in (0, 14, 16, 20, 24, 26)] == [False, False, True, True, True, False]


def test_rife_v4_6_is_held_exactly(runs):
    runs("rife-v4.6")
    assert [as_pinned(called, PIN) for called in (14, 16, 18, 24)] == [False, True, False, False]


def test_a_pin_not_by_model_holds_every_model_exactly(runs):
    for model in ("rife-v4.25", "rife-v4.6"):
        runs(model)
        assert [as_pinned(called, 20) for called in (18, 20, 22)] == [False, True, False]


def test_a_range_for_rife_v4_6_is_refused(runs):
    runs("rife-v4.6")
    with pytest.raises(ValueError, match="rife-v4.6's calls are pinned exactly"):
        as_pinned(16, {"rife-v4.25": (16, 24), "rife-v4.6": (14, 16)})


def test_where_no_binary_is_found_none_is_called(monkeypatch):
    monkeypatch.setattr(conftest, "rife_located", lambda: False)
    assert [as_pinned(called, PIN) for called in (0, 2, 16)] == [True, False, False]
