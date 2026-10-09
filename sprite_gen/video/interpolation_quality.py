# SPDX-License-Identifier: Apache-2.0
"""The shared acceptance policy for the frames repair and cycle alignment deliver.

Measurements live in ``rife`` (``smear``, ``ghost``); these are the existing alignment bounds,
also applied before replacing a filmed middle frame during jump repair, and the ghost screen
every frame a loop path delivers passes, filmed or made (docs/loop-repair.md section 4).
"""

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import rife

SMEAR_WARN = 0.001
OUTLINE_WARN = 0.05
# A frame whose part-covered regions at least rife.GHOST_THICK pixels thick come to more than this
# share of its solid pixels is a ghost: set by eye on drawn walks, between the half-drawn frames a video
# model leaves between two held drawings (and the frames made beside them) and the frames seen as clean
# (docs/loop-repair.md section 4).
GHOST_WARN = 0.003


def faults(measure: dict[str, float | None]) -> list[str]:
    """Smear inside the body or outline loss beyond both neighbours, and a ghost on the frame's
    own coverage where the measure carries its reading (`ghost`; None where the screen does not
    read the loop, `ghost_screen`)."""
    return ([*(["smear"] if measure["dark_excess"] > SMEAR_WARN else []),
             *(["outline"] if measure["outline_loss"] > OUTLINE_WARN else []),
             *(["ghost"] if (measure.get("ghost") or 0.0) > GHOST_WARN else [])])


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
