# SPDX-License-Identifier: Apache-2.0
"""The names of a cut's kind and of a loop's anchor, each declared once.

A cycle kind is what `video-loop --cycle` asks for and what a cut says it is: a cut's `kind` in its report and its
strip. `auto` only asks: the search hands back a `periodic` or `one-shot` cut, and a state that plays once turns
it into `once` or `lifetime`. An anchor mode is how a cut's cells are held in place (`video-loop --anchor`, the
strip's `foot_anchor`).

The sets made of these names answer different questions and stay apart, each in its own module:
`loop.CYCLE_MODES` (what `--cycle` takes), `nonloop.KINDS` (the cuts played once, ended as the clip's ending says),
`root_motion.KINDS` (the cuts played once that carry a body: not an effect's lifetime) and `loop.ANCHOR_MODES`
(what `--anchor` takes). `play.MODES` is a request's play mode, another vocabulary: `batch.cycle_for` turns one
into the other, and a value the two spell alike is still two names.

This module imports nothing, so every module of the cut, `loop` and the modules `loop` imports alike, can import it.
"""

from __future__ import annotations

CYCLE_AUTO = "auto"
CYCLE_PERIODIC = "periodic"
CYCLE_ONE_SHOT = "one-shot"
CYCLE_FIXED = "fixed"
CYCLE_PINNED = "pinned"
CYCLE_ONCE = "once"
CYCLE_LIFETIME = "lifetime"
CYCLE_ONCE_RETURN = "once-return"

ANCHOR_NONE = "none"
ANCHOR_FEET = "feet"
ANCHOR_BODY = "body"
ANCHOR_MOTION = "motion"
ANCHOR_MOTION_AUTO = "motion-auto"
