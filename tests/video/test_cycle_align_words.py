# SPDX-License-Identifier: Apache-2.0
"""The words a retake and a cycle alignment are reported in, pinned as a reader of the reports spells them.

Each is declared once and every writer and reader names it: a retake's `remedy` (`loop.REMEDIES`, which
`video-cycle-align` says as well), what became of a loop the two-cycle screen suspects (`status` under `suspects`)
and the refusal it stops a set with (`align`), and the reason `video-set` gives a set left unaligned for want of
RIFE (`batch`). A name agrees with itself whatever its value says, so the values are written out here.
"""

from __future__ import annotations

from sprite_gen.video import align, batch, loop


def test_a_retake_asks_for_a_new_take_a_new_cut_or_either():
    assert loop.REMEDIES == ("retake", "recut", "recut-or-retake")
    assert (loop.REMEDY_RETAKE, loop.REMEDY_RECUT, loop.REMEDY_RECUT_OR_RETAKE) == loop.REMEDIES
    assert align.REMEDIES is loop.REMEDIES


def test_a_suspected_loop_is_counted_stepped_stopped_or_warned_and_a_stop_is_refused_as_cycle_suspects():
    assert (align.SUSPECT_COUNTED, align.SUSPECT_STEPS, align.SUSPECT_STOPPED, align.SUSPECT_WARNED) == (
        "counted", "steps", "stopped", "warned")
    assert align.SUSPECTS_REFUSED == "cycle-suspects"


def test_a_set_left_unaligned_without_rife_says_so():
    assert batch.ALIGN_NO_RIFE == "rife-not-installed"
