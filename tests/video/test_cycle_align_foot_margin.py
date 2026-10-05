# SPDX-License-Identifier: Apache-2.0
"""A foot the view's cue names by a small margin is not named (docs/loop-repair.md section 4): the
loop is listed under `unnamed_feet` with `foot_why` starting `low-margin`, so whoever looks — a
person or a vision call — names it with `--foot`, and a cue that clears the margin names it as before.

The figures are the drawn walker of test_cycle_align seen from the side, its far leg drawn
`far_darker` levels darker than the near one: 3 levels part the two strikes by 0.0235 in luma (under
the margin), 4 levels by 0.0314 (over it), the default 60 by far more."""

from __future__ import annotations

import math

from sprite_gen.video import align

from tests.video.test_cycle_align import _cycle
from tests.video.test_cycle_align_foot import _cut, _rows

FAINT, CLEAR = 3, 4  # levels darker the far leg is drawn: a margin under SHADE_MARGIN, and just over it


def test_a_shade_that_parts_the_strikes_by_less_than_the_margin_names_no_foot():
    found = align.strike_foot(_cycle(view="side", far_darker=FAINT), (0, 6), "side", "right")
    assert found["cue"] == "shade" and found["rhythm"] >= align.CUE_RHYTHM  # it follows the step
    assert 0.015 < found["margin"] < align.SHADE_MARGIN  # named before (0.015); not now
    assert found["feet"] is None and found["why"].startswith("low-margin: ")
    assert f"{found['margin']:.4f}" in found["why"] and f"{align.SHADE_MARGIN:g}" in found["why"]


def test_a_shade_that_clears_the_margin_names_the_foot_as_before():
    shaded = align.strike_foot(_cycle(view="side"), (0, 6), "side", "right")
    clear = align.strike_foot(_cycle(view="side", far_darker=CLEAR), (0, 6), "side", "right")
    assert clear["margin"] >= align.SHADE_MARGIN
    # its right heel lands at phase 0: the near (right) leg is in front, drawn light
    assert clear["feet"] == shaded["feet"] == ["right", "left"]


def test_a_low_margin_loop_is_listed_for_whoever_looks_and_the_others_stay_named(tmp_path):
    dirs = [_cut(tmp_path, "S", view="front"), _cut(tmp_path, "E", view="side"),
            _cut(tmp_path, "L", view="side", far_darker=FAINT), _cut(tmp_path, "C", view="side", far_darker=CLEAR)]
    views = ["front", "side@right", "side@right", "side@right"]
    report = align.align_set(dirs, views=views)
    rows = _rows(report)
    assert [rows[k]["start_foot_source"] for k in "SECL"] == ["engine", "engine", "engine", None]
    assert [rows[k]["start_foot"] for k in "SEC"] == ["right"] * 3
    low = rows["L"]
    assert low["foot_why"].startswith("low-margin: ") and low["foot"]["margin"] < align.SHADE_MARGIN
    [entry] = report["unnamed_feet"]
    assert entry["dir"] == str(dirs[2]) and entry["foot_why"] == low["foot_why"]
    assert [(c["strike"], c["frame"]) for c in entry["candidates"]] == [(0, 0), (1, 6)]
    assert any(w.startswith(f"{dirs[2]}: starts on its larger strike, foot not named — low-margin: ") for w in report["warnings"])
    # the answer comes back as for any unnamed loop, and the reason it was unnamed is kept
    told = _rows(align.align_set(dirs, views=views, feet={"L": "right"}))["L"]
    assert told["start_foot_source"] == "given" and told["foot_unnamed_why"].startswith("low-margin: ")


def test_the_figure_shades_its_far_leg_by_the_levels_asked():
    """The fixture itself: the margins the tests above rest on."""
    margins = {d: align.strike_foot(_cycle(view="side", far_darker=d), (0, 6), "side", "right")["margin"] for d in (FAINT, CLEAR)}
    assert math.isclose(margins[FAINT], 0.0235, abs_tol=0.0005) and math.isclose(margins[CLEAR], 0.0314, abs_tol=0.0005)
