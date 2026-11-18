# SPDX-License-Identifier: Apache-2.0
"""A cut's kinds and a loop's anchors are each named once (`sprite_gen.video.loop_vocab`), and every set made of them
is its own module's, for its own question.

The values are what a caller writes (`video-loop --cycle`, `--anchor`) and reads back (a report's and a strip's
`kind`, the strip's `foot_anchor`), so they are written out here as a caller writes them: a set built from the names
agrees with itself whatever the names say.
"""

from __future__ import annotations

import ast
import inspect

from sprite_gen.video import local_cycle, loop, loop_vocab, nonloop

CUT_KINDS = ("periodic", "one-shot", "fixed", "pinned", "once", "lifetime", "once-return")


def _declared(prefix: str) -> list[str]:
    return [value for name, value in vars(loop_vocab).items() if name.startswith(prefix)]


def test_every_cycle_kind_and_anchor_is_spelt_as_a_caller_spells_it():
    assert loop.CYCLE_MODES == ("auto", *CUT_KINDS)
    assert loop.ANCHOR_MODES == ("none", "feet", "body", "motion", "motion-auto")
    assert sorted(_declared("CYCLE_")) == sorted(loop.CYCLE_MODES), "a cycle kind named outside --cycle's choices"
    assert sorted(_declared("ANCHOR_")) == sorted(loop.ANCHOR_MODES), "an anchor named outside --anchor's choices"


def test_each_set_of_cycle_kinds_keeps_its_own_members():
    assert nonloop.KINDS == ("once", "lifetime", "once-return")
    assert loop.SEARCHED == ("auto", "periodic")
    assert loop.PLAYED_ONCE == ("one-shot", "once", "lifetime", "once-return")
    assert loop.NO_SEAM_GATE == ("pinned", "once", "lifetime", "once-return")
    assert loop.MOTION_ANCHORS == ("motion", "motion-auto")


def test_the_cuts_local_cycle_hands_back_are_cut_kinds():
    """`local_cycle` still spells the kind of the cut it hands back (until it reads `loop_vocab`, after PR #168): a
    misspelt one is no cut kind at all, and every reader of `cycle["kind"]` would take it for none of its own."""
    written = [value.value for node in ast.walk(ast.parse(inspect.getsource(local_cycle)))
               if isinstance(node, ast.Dict) for key, value in zip(node.keys, node.values)
               if isinstance(key, ast.Constant) and key.value == "kind" and isinstance(value, ast.Constant)]
    assert written, "local_cycle names no cut kind: read its cuts' kinds through loop_vocab here"
    assert set(written) <= set(CUT_KINDS), f"local_cycle hands back a cut of no kind: {sorted(set(written) - set(CUT_KINDS))}"
