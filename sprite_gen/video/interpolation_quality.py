# SPDX-License-Identifier: Apache-2.0
"""The shared acceptance policy for the frames repair and cycle alignment deliver.

Measurements live in ``rife`` (``smear``, ``outline_shift``, ``ghost``, ``crossfade``, ``torn``, ``hole``, ``doubled``);
these are the existing alignment bounds, also applied before replacing a filmed middle frame during jump repair, the ghost
screen every frame a loop path delivers passes, filmed or made, the clean frame a filmed ghost gives way
to, and the cross-fade a made frame is named for, kept; the torn part a frame of the overlapped wrap
is refused for; and the hole through a limb and the double outline a frame the alignment makes gives way for
(docs/loop-repair.md section 4).
"""

import argparse
import math
from collections.abc import Container

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import rife

# The size a game shows the cells at, a share of the frames' own (`--judge-scale`): 1 reads a frame made between two
# frames as cut and nothing else. Below 1, a frame refused for its outline as cut is taken where its line keeps its ink
# as seen at that size (`rife.outline_faded`: the line's ink beside each edge pixel summed across rife.OUTLINE_REACH
# pixels of that size, against the two it is made between, on the frames as cut): a thin line an interpolator softened
# reads lost at the frames' own size and as kept where a game shows it smaller, while a line gone to the fill or legs
# melted into one shape stay lost at any scale. The size takes back outline refusals only: every reading that names a
# broken frame (smear, ghost, hole, doubled, and the held fill's contact) is read as at 1, its limits set in pixels of
# the frames as cut, and the frames delivered are the size they were cut at (docs/loop-repair.md section 7).
JUDGE_SCALE = 1.0

SMEAR_WARN = 0.001
OUTLINE_WARN = 0.05
# A frame whose part-covered regions at least rife.GHOST_THICK pixels thick come to more than this
# share of its solid pixels is a ghost: set by eye on drawn walks, between the half-drawn frames a video
# model leaves between two held drawings (and the frames made beside them) and the frames seen as clean
# (docs/loop-repair.md section 4).
GHOST_WARN = 0.003
# A made frame whose ghost band, read in windows rife.GHOST_BAND of its body high (`rife.ghost_window`), is over this share
# of a window's solid pixels is a ghost too, though the whole figure's share stays under GHOST_WARN: a band through one
# limb, where one neighbour's gap beside the limb and the other's limb there are blended and the background shows through.
# Set from the made frames of the cycle alignment, clean and seen through (docs/loop-repair.md section 4). Read only
# where the measure carries it — the frames the cycle alignment makes.
GHOST_WINDOW_WARN = 0.014
# A made frame whose `rife.crossfade` is over this is named to look at, never given way for: on drawn
# walks only the frame seen cross-faded read over it, but on synthetic legs a clean drawing between two
# far-apart drawings outlined in grey reads over it too, and a cross-fade drawn without outlines mostly
# under it — the reading does not part the two, so it is no fault (docs/loop-repair.md section 4).
CROSSFADE_LOOK = 0.015
# A made frame whose single passes disagree (`rife.torn`) over more than this share of a window a tenth of the body
# high is torn: set by eye on drawn walks, between the frames seen torn (a hand dragged apart beside a tail swung far,
# legs melted into one shape, a part doubled) and the frames seen as clean. Read only where a frame is read with its
# passes — the overlapped wrap's frames (docs/loop-repair.md sections 2 and 4).
TORN_WARN = 0.21
# A made frame whose `rife.hole` is over this has a hole through a limb: a transparent hole inside its silhouette at
# least rife.HOLE_THICK pixels thick that neither neighbour has there, in place, carried by the whole figure's motion, or
# as one hole of each, alike and on its way. Any is one: a gap a step opens between two legs lies where the neighbour that
# shows it open has it, a gap the two have moved with the body lies on its way between them, and a hole each has that a
# part carried, turned or grown, is one hole of each; all are theirs. Read only where the measure carries it — the frames
# the cycle alignment makes (docs/loop-repair.md section 4).
HOLE_WARN = 0.0
# A made frame whose `rife.doubled` is over this shows a double outline: a line one neighbour draws, left at part strength
# in its own place, over a run longer than this share of its body height. Set by eye on drawn walks, between the frames
# seen with a line doubled (a boot's top faded out where one neighbour tilts it, beside the top the frame drew), which read
# over it, and the frames seen as clean or only soft, which read under it. Read only where the measure carries it — the
# frames the cycle alignment makes (docs/loop-repair.md section 4).
DOUBLED_WARN = 0.03


def judge_scale(text: str) -> float:
    """`--judge-scale`: a share over 0 and at most 1 of the frames' own size (`JUDGE_SCALE`)."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and 0 < value <= 1):
        raise argparse.ArgumentTypeError(f"--judge-scale {text}: a share of the frames' size over 0 and at most 1 "
                                         "(the size the game shows them at; 1, as cut)")
    return value


def faults(measure: dict[str, float | None]) -> list[str]:
    """Smear inside the body or outline loss beyond both neighbours, and a ghost on the frame's
    own coverage where the measure carries its reading (`ghost`; None where the screen does not
    read the loop, `ghost_screen`). Where the measure also carries an `outline_shift` (the frames the
    jump repair and the cycle alignment make between outlined neighbours, `rife.outlined`), an outline
    is lost only where both readings say so: a line outlined in a mid tone, which the dark line
    (`rife.DARK_LUMA`) reads as lost wherever the flow softened it, is kept unless its edge went
    lighter than the neighbours' by more than `rife.OUTLINE_SHIFT` (docs/loop-repair.md section 4).
    Where it is None (art painted without a line) and in the versioned measures of
    `video-loop-compare` and `video-loop-repair`, which carry `rife.smear` alone, the dark line
    judges alone. Where it also carries an `outline_faded` (how much of the edge's line faded as a game shows the
    frames smaller, `--judge-scale`), an outline lost as cut is lost only where the line faded there too
    (docs/loop-repair.md section 7). A part torn where the measure carries how far the frame's passes disagree
    (`torn`, the overlapped wrap's frames: `rife.passes`, `rife.torn`). A hole through a limb where
    it carries the holes the frame opened that neither neighbour has (`hole`, the cycle alignment's
    frames: `rife.hole`). A double outline where it carries how far a line one neighbour draws runs at
    part strength in its own place (`doubled`, the cycle alignment's frames: `rife.doubled`). A ghost too
    where it carries the band read in windows of the body (`ghost_window`, the cycle alignment's frames:
    `rife.ghost_window`) and that is over GHOST_WINDOW_WARN."""
    shift = measure.get("outline_shift")
    outline = measure["outline_loss"] if shift is None else min(measure["outline_loss"], shift)
    faded = measure.get("outline_faded")
    if faded is not None:
        outline = min(outline, faded)
    return ([*(["smear"] if measure["dark_excess"] > SMEAR_WARN else []),
             *(["outline"] if outline > OUTLINE_WARN else []),
             *(["ghost"] if ((measure.get("ghost") or 0.0) > GHOST_WARN
                             or (measure.get("ghost_window") or 0.0) > GHOST_WINDOW_WARN) else []),
             *(["torn"] if (measure.get("torn") or 0.0) > TORN_WARN else []),
             *(["hole"] if (measure.get("hole") or 0.0) > HOLE_WARN else []),
             *(["doubled"] if (measure.get("doubled") or 0.0) > DOUBLED_WARN else [])])


def looks(measure: dict[str, float | None]) -> list[str]:
    """What a person should look at in a made frame, beyond its faults: two drawings cross-faded
    inside its silhouette (`crossfade` over CROSSFADE_LOOK, where the measure carries it). Named in a
    warning, the frame kept — never a reason to give it way."""
    return ["crossfade"] if (measure.get("crossfade") or 0.0) > CROSSFADE_LOOK else []


def ghost_screen(frames: list[Image.Image]) -> dict:
    """The ghost screen of a loop as filmed: its frames whose `rife.ghost` is over GHOST_WARN
    (`ghosts`, each `frame` and its reading) and the loop's own level, the median reading. A loop
    whose level is over it is drawn part-covered — a glow, a translucent cape — and the screen does
    not read it (`reads` false, with `why`): its ghosts cannot be told from its drawing."""
    readings = [rife.ghost(f) for f in frames]
    level = round(float(np.median(readings)), 5) if readings else 0.0
    record = {"limit": GHOST_WARN, "thick_px": rife.GHOST_THICK, "level": level, "reads": level <= GHOST_WARN}
    if not record["reads"]:
        return {**record, "ghosts": [], "why": f"the loop is drawn part-covered: its median frame reads {level:.5f}, over the "
                                               f"{GHOST_WARN} that names a ghost"}
    return {**record, "ghosts": [{"frame": i, "ghost": v} for i, v in enumerate(readings) if v > GHOST_WARN]}


def clean_beside(frame: int, ghosts: Container[int], count: int) -> int | None:
    """The frame of a loop of `count` frames a filmed ghost at `frame` gives way to: the frame after
    it where the screen passes it (`ghosts` are the frames it names), else the one before it; None
    where both are ghosts too, and the ghost is kept. The cycle alignment takes it at a time on a
    ghost, and the jump repair of `video-loop` where its frame between the two is not taken."""
    after, before = (frame + 1) % count, (frame - 1) % count
    return after if after not in ghosts else before if before not in ghosts else None
