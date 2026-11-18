# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-sway` — the Sway layer: a soft part of a looping body sways with the body's motion.

Its old name, `video-follow` (`sprite_gen.video.follow`, `follow_loop`), still runs it and gives the same
result. The loop's record of it is read under `sway` or `follow`; this release still writes it under
`follow`, and the strip as cut is still `follow.source.png`, the names a consumer reads them by.

A clip model draws a walk's body moving, but a soft part that hangs off it (a chest, a belly,
a pouch on a strap) mostly moves with the body as one piece, even when the prompt asks it to
bounce: at sprite size it reads as rigid. This puts that follow-through back after the loop is
cut.

Each frame of the strip is a cell of one cycle. The body's motion is read off the cells: how far
each cell's body lies from the first cell's. The silhouette is worn down from a quarter to half
of its depth in the first cell, so what swings on the body — a tail, a ponytail, an ear, a sword
or a rod held up, the legs and arms — is worn away and the head and torso are left; each cell is
laid where it overlaps the first cell most, worn alike, a pixel counting once for every level it
is still in, and then laid again on what the cells so laid have in common: what more than half of
them have at one place, so a part as thick as the body that is elsewhere in another cell (ears as
wide as the head, up in one cell and on the face in the next) does not pull that cell onto itself.
(Read off the top of the body instead, the motion was whatever came to the top: a
tail tip or a raised sword made it jump by the part's whole swing from one cell to the next.) A part
hung on the body is a damped mass: its offset x from where the body carries it answers the body's own
acceleration, x'' + 2·ζ·ω·x' + ω²·x = −body''. The loop repeats, so the answer is the periodic
steady state, solved per harmonic of the cycle: no start-up, no kick at a foot strike, and the
last frame leads into the first. `--gain` scales that physical answer and nothing else; it is
not normalised to a target size.

The part is an ellipse in the first cell (`--region cx,cy,rx,ry`, cell pixels), carried with
the body's bob. Inside it every pixel is moved by the offset times a weight that is 1 at the
centre and falls to 0 at the rim (cos²); outside it no pixel changes. The move is sampled as
premultiplied bilinear colour, so an edge never picks up the colour under a transparent pixel.
A move so large that the weight's slope folds the picture over (offset × π / (2 · radius) ≥ 1)
is refused. `--on-fold lower` gives each region the largest gain that does not fold it instead
(the offset grows in a straight line with the gain, so that gain is known without a search), and
never one under 1, the mass as measured: a region too small for that is held — it does not move,
and the record names it — and the strip is refused only when every region is.

The strip as it was before is kept as `follow.source.png` beside it; running the command again
reads from there, so a second follow-through never moves a moved strip. `video-loop` (a new
cut) and `video-cycle-align` (a new cycle) remove it, and the alignment records that the
follow-through was cleared. docs/video-pipeline.md section 6.

The regions are where somebody looked: in the first cell of the strip they were read on. An alignment
turns a loop to start elsewhere, and its first cell is then another; read again there, the motion is
taken from another cell 0 and the regions land on another place of the body. `--read-on <sha256>` names
the strip (as cut, before any follow-through) the regions were read on. That strip is this loop's own —
`same`, read as ever — or the cut this loop was aligned from (`video-cycle-align` records it, with its
reading and where every new cell is from): then the cut's reading is carried to each cell from the cut's
cell it is, the regions stay where they were read, and the damped mass is solved on the new order, length
and frame time (`transported`). A cell the alignment made between two of the cut's, or one that is not
the cut's cell pixel for pixel (a crop or a scale changed, and nothing records how), has no reading to
carry: refused `uncertain`, never carried by the nearest whole cell. Another cut of this loop's own clip,
named by `--read-on-cut STRIP` with its record beside it, is `recut`: paired by the source frame its cell 0
is, its regions and masks moved onto this loop's cell that is that frame by the whole px shift the two
crops set it apart by on the frame as filmed — each crop less where its cut's anchor moved that frame —
never scaled (`recut_pair`). Each cut is read by its record of its cells alone, as `video-loop` writes it
in the strip's record, bound to the strip's PNG by its sha256 (`cell_record`): the source frame each cell
is, where the anchor moved it, the factor the size hold and the gait fallback scaled it by, and whether its
pixels are that frame as filmed but for that move (on a loop aligned from its cut, the cut's own record as
the alignment kept it beside the cut's frames). A cut with no such record (one video-loop made before it
wrote one), a record of another strip, a cell scaled or not as filmed, and two paired cells that are not
the same pixels where they overlap at the shift (`recut_same`) are refused `no-match`, saying which. Any
other strip is refused `no-match`. Without `--read-on` nothing of this is read or written.

`--stretch-floor D` asks every region's move to leave each pixel at least D of its area. The weight's
steepest slope is π/2 over each radius, so a move (ox, oy) leaves 1 − (π/2)·hypot(ox/rx, oy/ry) of it —
by the way the region moves, where the fold rule reads the smaller radius whichever way it moves. Each
region takes the largest gain, in steps of GAIN_STEP, that keeps the floor in every cell, and never one
above what the fold rule gave it: the floor only lowers. Under it the fold rule's handling stands (under 1
held, every region held refused; `--on-fold refuse` refuses). The record names the floor and its policy.

What carries a region is the lay of the body's outline (`carry_basis`): whose pixels are inside the
carried ellipse is not read, and nothing here moves a region on a guess of it.

A strip holds 64 cells and 32,000 px across at most, so a cycle of large frames is cut down to fit it. `--frames
<folder> --out <new folder>` sways a cycle's frames themselves — a loop's `cycle/`, or what `sprite-gen breathe` wrote
from it — at their own size and count, into a new folder of the same frames under the same names and `sway.json`, as
`sprite-gen breathe` writes one. It is the same ask answered by the same code, cell for cell (`solve_cells`): a strip
cut from those frames at scale 1 with none left out, its regions moved by its crop origin, moves its cells as these
are moved, at the frame time its meta writes — rounded to hundredths of a ms, as `--fps` is read here.

A breathing idle's head stays where it is and its body moves a pixel or two: read off the outline, that motion is
whole-pixel steps, and the damped mass answers their jerks, not the breath. `--breathe <breathe.json>` sways the part
on the breath instead: the Breathe layer's record of the breath the frames were given (`sprite-gen breathe` writes it
beside them), its phases checked against those its config gives. The chest rises lift × w, w the breath of the record's
in-breath share (its config's `inhale`, the phase the in-breath peaks at); the part rises as far, late by `--lag` of a
breath (the time warp the Breathe layer's shoulders are late by), and lies the difference behind:
offset = gain × lift × (w − w late), against the way the chest rises. Where the breath rests the offset is 0 — the first
cell, and where the loop closes — and a cell whose every offset is 0 is the cell as it was, byte for byte.

A loop set's way in or out of its loop carries on the loop's breath from a cell of it: its record says the loop's
length and the cell it starts at (`sprite-gen breathe --loop-cells N --start-cell K`), and its cell i has the phase of
the loop's cell (K + i) mod N. An offset is its phase's alone, so that cell is offset as the loop's cell is, from the
stretch's first cell on — there is nothing to wind up. The fold rule and the floor read the loop's whole breath, every
cell of it, so the stretch moves by the gain the loop moves by and the record's reach is the loop's. Its regions were
read on the loop's first cell, which is not one of its frames: `--reference` names that cell, the body is read from it
and the regions are carried from there, as on the loop's own cells.

A region's fifth number, flat, is the share of the way from its centre to its rim that moves in full — the Breathe
layer's chest weight (`breathe.flat_cos2`) — so a large part moves as one piece instead of pressed in at its middle;
the fold rule and the floor read it by the radius left (1 − flat of it). `--gain-across` moves across by a gain of its
own; a gain the fold rule or the floor lowers lowers both in proportion.

`--band PX|auto` moves a region as one mass instead (`BAND_POLICY`; off by default, and off nothing of it is read or
written). The weight is 1 over the region's ellipse grown by `--band-margin` every way and falls to 0 (cos², along the
ray from the centre) over the band past it: the whole part moves, and only what is round it gives. A pixel of the part
lands where the weight is still 1 only if the flat part reaches as far as the part goes, so the margin left auto is the
move's reach and a pixel (ceil(reach) + 1), and the band left auto is the narrowest the move does not fold
(ceil(reach × π / 2) + 1): the reach is read off the move first, at the gain asked, and the band and margin from it.
The fold rule reads a region by its narrowest band, `--on-fold lower` as ever, and then the steepest its weight falls
along the move (`band_steepest`): where the flat ellipse and the outer one differ in shape the band is narrower across
its ring than its px. `--band-up FLAT[,PX|auto]` gives the
part above the centre its own: the flat part stops FLAT of the region's height over the centre and the weight falls
over PX from there — left auto, the band or as far as HANG_PAST px over the region's top, whichever is further — so the
upper part gives and the rest moves whole. A region's flat share is the other way of moving it whole and is refused
with it. A band carries each region by its own pixels, not by the body's lay: the solid pixels under its ellipse in
the cell it was read on are looked for in each cell within CARRY_SEARCH px of where the cell before had them, or of the
lay (`carry_match`, `carry_own`), and a cell where that is uncertain is carried by the lay, the record saying which and
why. Outside every region's band no pixel changes. The record holds the band, the carry of each region in every cell
and a ledger of what the move did in each cell, and what is in the ledger is said in a warning line.

`--hold-mask` (with `--band`; off by default, and off nothing of it is read or written) holds pixels the band moves
round: an arm that hangs over the part, a dress below it. The mask is a PNG drawn on the cell the regions were read on,
each of its 8-connected pieces carried into every cell by its own solid pixels as a band carries a region (`carry_mask`;
by the lay where that is uncertain, named) and grown by HOLD_GROW px for what that carry may be off by; or a folder of
one mask per cell, held as given. The move is not changed. In every cell the mask, the pixels not opaque within the
move's reach and HOLD_EDGE px of it (its soft edge, the half-clear rim of a gap beside it) and the clear pixels as near
(the air) keep their bytes. Every other pixel is read as without the mask, but a read headed into what is kept stops
short of it (`free_back`): it goes back f = min(s, h) px of its move s, h HOLD_SLOPE of how far it could go, lowered
until it rises by at most HOLD_SLOPE a px every way, and the gap the part leaves is drawn from the part stretched in
order, never read backwards: a read takes at least the move's own least area or 1 − HOLD_SLOPE of the source's,
whichever is less (`read_share`; read between neighbours, 1 − HOLD_SLOPE·√2). A read that still takes a kept pixel is
read from the others, or walked back further. The record holds the mask, the carry of each piece in every cell and a
ledger per cell (`hold_ledger`): what was kept, what was read short, and the pixels whose read takes the source
backwards, read off the move each read took.

`--region-mask` gives the region as a mask instead of the ellipse's weight (off by default, and off nothing of it is read
or written): a PNG drawn on the cell the regions were read on, or a folder of one PNG per cell. Its lower edge may be moved
to the crease under it (`--region-crease`, `crease_of`) and it is worn in by `--region-shrink` px (SHRINK_DEFAULT), so a
mask drawn a little large holds what is outside it; then it is carried into every cell by its own pixels (`carry_region`):
round where the cell before had them first and round the lay second, the lesser difference of the two that settle, and
where neither does, where the cell before had them, the record saying which and why — never the lay alone. In every cell
each visible pixel outside the mask and its soft edge keeps its bytes, and the clear pixels within the move's reach and
REGION_AIR px of it are the air it moves into. The weight is sin² of the distance to the nearest held pixel over a rim, an
ellipse of Lx across and Ly down (`rim_bands`: the fold rule's least band each way for the largest move that way, times
`--region-rim`, widened together until (π/2)·hypot(dx/Lx, dy/Ly) is at most RIM_SLOPE in every cell), so the move leaves a
pixel at least 1 − RIM_SLOPE of its area and its read stays clear of the held pixels; with `--band-up`, the lesser of that
and the upper yield's. The ellipses still say each region's gain and where `--band-up`'s shape sits. A read whose four
pixels take a held one is read from the others, and walked back only where they are half its weight, which the rim keeps
from happening. The record holds the mask, its carry in every cell and a ledger per cell (`region_ledger`). Not with
`--band`, a region's flat share, `--stretch-floor` nor `--hold-mask` (but under `--region-over`). The crease is the
picture's own edge: under `--region-over` the region reaches down to it again in every cell where the wear lifted it
(`crease_reach`).

`--region-over` (with `--region-mask`; off by default, and off nothing of it is read or written) lays the region over the
figure under it (`layer_sets`, `lay_over`): the held pixels under its lower edge, solid or not — in its columns and the
air's reach past them — are no anchor of the rim, so the lower edge moves with the rest, and the band of them within the
air's reach of it moves too and is drawn over: the region's own pixels (with its soft edge, which goes with it beside the
figure too, and its outline against the air) read at the move and laid by their alpha over the band's own pixel, which
keeps its bytes where they do not land; the air and where the region lifts are drawn by the move, the figure under it
showing there. With `--region-membrane` what lies under the lower edge within its reach moves as a membrane between
the region's move and none (`membrane_move`), drawn by that move. `--hold-mask` names the pixels it
leaves out (an arm), carried as a band's are: where the mask does not cover them never the region's, never drawn over, an
anchor of the rim; where it does, the region keeps them and they move with it. `--region-shear
S` (off by default) widens the rim so a move one way rises across it the other way by at most S px a px (`shear_floor`):
the fold rule reads each way by the move along it alone, and a move down rose all at once across a rim sized for a small
move across. With either the ledger reads the region's edges too: the steps the drawn move takes (`move_steps`), the
strip a region drawn short of its side leaves standing (`still_strip`), and its tip's shape and move (`tip_shape`).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.curate.curation import BREATHE_PIXEL, BREATHE_SMOOTH, BREATHE_SMOOTH_SHAPE_RANGES, state_breathe
from sprite_gen.effects import breathe as breathe_mod
from sprite_gen.effects.anatomy import Anatomy
from sprite_gen.frames import folder as folder_mod
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.video import loop as loop_mod
from sprite_gen.video.loop_vocab import CYCLE_ONE_SHOT
from sprite_gen.util.console import utf8_streams

VERB = "video-sway"
OLD_VERB = "video-follow"  # the old name, kept: it runs this and says its own name
# How the motion is read and a region moved, by name. `video-sway-inspect` writes it beside what it read, so
# what somebody saw under one way of reading is not taken for another's: a change that gives the same strip,
# regions and settings another carry, offset or picture takes a new name.
POLICY = "common-lay/damped-mass/cos2-bilinear/1"
BREATH_POLICY = "common-lay/breath-late/cos2-bilinear/1"  # the offset from the breath (`--breathe`), not the body's motion
FREQ_DEFAULT = 2.4  # Hz: a slow, soft part
ZETA_DEFAULT = 0.6  # damping ratio: it lags and settles, with no ringing
GAIN_DEFAULT = 2.5  # times the physical answer
BREATH_GAIN_DEFAULT = 1.0  # times the breath's rise less the part's own, late by --lag: under the breath's own rise
LAG_DEFAULT = 0.15  # of one breath: the part peaks this much of a breath after the chest
LAG_MAX = 1 / math.pi  # at it the late breath's time stops; past it, it runs backwards (breathe.smooth_shoulder_wave)
FLAT_RANGE = BREATHE_SMOOTH_SHAPE_RANGES["chest_flat"]  # a region's flat share: the Breathe layer's chest_flat, one weight
FRAMES_RECORD = "sway.json"  # beside the frames `--frames ... --out` writes
GAIN_MEASURED = 1.0  # the mass as measured: `--on-fold lower` does not go under it
GAIN_STEP = 0.01  # a lowered gain is a whole number of these, so the report's `gain` is the `--gain` to pass
ON_FOLD_MODES = ("refuse", "lower")
HARMONICS = 6  # of the cycle, for the body's motion: a step's shape, not its noise
ALPHA_SOLID = 128
WORN_FROM, WORN_TO = 0.25, 0.5  # of the body's depth in the first cell: the levels its silhouette is worn down to
SOURCE = loop_mod.FOLLOW_SOURCE
# How a reading made on the cut is carried to a loop aligned from it (`--read-on`): per whole cell of the cut, by the
# alignment's record of where each cell is from, and only to a cell that is that cell pixel for pixel.
TRANSPORT_POLICY = "cut-reading/whole-cell/1"
# How regions read on another cut of the same clip are paired with this loop's cut (`--read-on` with `--read-on-cut`): by
# the source frame the cut's cell 0 is, onto the cell of this loop's cut that is that frame, moved by the whole px the two
# crops set that frame's pixels apart, each crop taken on the frame as filmed (less where its cut's anchor moved it); never
# scaled or grown, only where each cut's record of its cells calls that cell the frame as filmed (`cell_record`), and only
# where the two cells are the same pixels where they overlap at the shift (`recut_same`).
RECUT_POLICY = "same-clip/source-frame/whole-px-shift/2"
RECUT_DRIFT = 1.0  # px: the most a pixel of the one cut may lie off the whole shift from the same pixel of the other
# How `--stretch-floor` reads a region's least area: the cos² weight's closed form, by direction, never above the
# fold rule's gain.
STRETCH_POLICY = "stretch-floor/cos2-direction/1"
# What carries a region into a cell: the lay of the body's outline. Whose pixels the carried ellipse holds is not read.
CARRY_BASIS = {"reading": "outline-lay", "region": "unverified"}
READ_ON = re.compile(r"[0-9a-f]{64}")  # a strip's sha256, as `--read-on` names it
# A region moved as one mass (`--band`): flat over its ellipse grown by a margin, cos² to 0 over a band past it,
# carried by its own pixels. The record names it, as POLICY names the way without it.
BAND_POLICY = "own-pixels/margin-band-cos2/1"
AUTO = "auto"  # a band, margin or band above read off the move
HANG_PAST = 3.0  # px over a region's top the band above runs at least, left auto: all of the ellipse moves some, nothing over it
# Under a band, a region is carried by its own pixels; a cell where they are not found surely is carried by the lay.
CARRY_BASIS_BAND = {"reading": "outline-lay", "region": "own-pixels"}
CARRY_SEARCH = 9  # px either way a region's pixels are looked for in a cell (whole pixels), around where the cell before had them
PRESSED, STRETCHED = 0.85, 1.15  # of a pixel's area: the ledger counts a moved pixel left with less, or more
MOVED_PX = 0.5  # px: a pixel outside every region moved this far or more is counted
# A hold mask (`--hold-mask`) under a band: the pixels it holds, carried by their own pixels, keep their bytes with their
# soft edge and the air beside them; a read headed into them takes a share of its move that rises no faster than HOLD_SLOPE.
HOLD_POLICY = "own-pixels-grown/kept-edge-air/read-short-sloped/1"
HOLD_GROW = 3  # px (4-neighbour steps) a mask carried from the cell it was drawn on is grown by: what its carry may be off by
HOLD_EDGE = 2  # px past the move's reach (4-neighbour steps) round the held pixels: what is not opaque there is held, what is clear kept
HOLD_MARGIN = 1  # px (8-neighbour steps): a read stops short of the kept pixels by as much, unless it is already that near
HOLD_SLOPE = 0.5  # of a px: how much more of its move a read takes a px further any way (across, down or both), at most
HOLD_ROUND = 1  # px each way the share of a read is taken at its least over, then averaged over (0: as found)
HOLD_STEPS = 16  # a read whose four pixels still take a kept one walks back a 1/HOLD_STEPS of its move at a time
# A region given as a mask (`--region-mask`): its own pixels carried, every visible pixel outside it held, the weight falling
# to 0 toward the held pixels over a rim inside it (sin², read off the move), the upper yield (`--band-up`) the lesser.
REGION_POLICY = "own-pixels-mask/held-outside/rim-sin2/1"
RIM_SLOPE = 0.9  # the rim is widened until a move takes at most this much of a pixel's area along the way it moves
RIM_DEFAULT = 1.0  # times the fold rule's least band, across and down, the rim starts at (`--region-rim`)
SHRINK_DEFAULT = 2  # px (4-neighbour steps) a region mask is worn in by (`--region-shrink`): a mask drawn a little large holds nothing outside
REGION_SOFT = 2  # px (4-neighbour steps) from the region a pixel not opaque moves with it, unless it is beside another solid one
REGION_AIR = 2  # px past the move's reach (8-neighbour steps) round the region of clear pixels it may move into
CREASE_UP, CREASE_DOWN = 5, 3  # rows above and below a region's lowest pixel its lower edge is looked for (`--region-crease`)
CREASE_SPAN = 2  # columns either way the crease's rows are taken the median over
# A region laid over the figure under it (`--region-over`): what lies under its lower edge no anchor of the rim, the band of
# it within the air's reach drawn over by the region's own pixels by their alpha and kept where they do not land; the air
# and where it lifts drawn by the move; with `--region-crease` its lower edge reaching down to the crease in every cell.
OVER_POLICY = "under-edge-free/band-laid-by-alpha/air-and-lift-by-move/crease-reach/2"
# Laid over with `--region-over-solid`: of what lies under its lower edge only the solid pixels are free of the rim and laid
# over, the rest stays and holds the rim, and its soft edge goes with it even where it touches the figure under it.
OVER_SOLID_POLICY = "under-edge-solid-free/soft-edge-with/1"
# Laid over with `--region-rim-round`: the held pixels beside the region (in the columns it does not reach, above its lower
# edge) hold the rim round, the wider of its two widths each way; the rest of the anchors hold it as ever.
RIM_ROUND_POLICY = "beside-above-floor/round-wider-width/1"
# Laid over the figure under it, a membrane (`--region-membrane N`): what lies under its lower edge within N + 1 px of it
# moves, starting at the region's nearest move falling as cos² over N + 1 px, then each pixel's move the mean of its figure
# 4-neighbours', between the region's own and none on the rest of the figure, pass after pass.
MEMBRANE_POLICY = "under-edge-within-n/spread-cos2-start/figure-4-mean-passes/2"
MEMBRANE_STEP = 1e-4  # px: the membrane's passes stop at the first that moves no pixel this far
MEMBRANE_PASSES = 1200  # the most passes the membrane takes
TIP_COLS = 10  # the region's columns at its tip the ledger reads the tip's shape and move over (`tip_shape`)
TIP_AIR = 8  # px of the air round the tip's columns and rows the tip's window takes
TIP_RING = 3  # px round the source's outline against the air the tip's shape is read over
TIP_OFF = 2  # px round the other solid pixels the tip's ring leaves out
TIP_SEARCH = 6  # px either way the tip's outline is looked for once its move is taken away


def parse_region(text: str) -> tuple[float, ...]:
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--region wants cx,cy,rx,ry[,flat] (four or five numbers), got {text!r}") from exc
    if len(values) not in (4, 5):
        raise argparse.ArgumentTypeError(f"--region wants cx,cy,rx,ry[,flat] (four or five numbers), got {text!r}")
    try:
        return region_of(values)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--region {text!r}: {exc}") from exc


def region_of(values) -> tuple[float, ...]:
    """A region as the engine reads it: cx, cy, rx, ry, and its flat share where it has one. A flat share of 0 is no
    flat share: the region is read, moved and recorded as one of four numbers."""
    values = [float(v) for v in values]
    if values[2] <= 0 or values[3] <= 0:
        raise ValueError(f"radii must be positive, got {values[2]:g},{values[3]:g}")
    if len(values) == 5:
        if not FLAT_RANGE[0] <= values[4] <= FLAT_RANGE[1]:  # a NaN is not
            raise ValueError(f"flat is the share of the way from the centre to the rim that moves in full, "
                             f"{FLAT_RANGE[0]:g} to {FLAT_RANGE[1]:g}; got {values[4]:g}")
        if values[4] == 0:
            values = values[:4]
    return tuple(values)


def flat_of(region: tuple[float, ...]) -> float:
    """A region's flat share: the share of the way from its centre to its rim that moves in full (0: none)."""
    return region[4] if len(region) > 4 else 0.0


def region_radius(region: tuple[float, ...]) -> float:
    """The radius the fold rule reads a region by: its smaller radius, less its flat share — the weight falls from 1 to 0
    over what is left, and steepest there."""
    return min(region[2], region[3]) * (1 - flat_of(region))


def parse_read_on(text: str) -> str:
    if not READ_ON.fullmatch(text):
        raise argparse.ArgumentTypeError(f"--read-on wants the sha256 of the strip the regions were read on (64 lowercase "
                                         f"hexadecimal digits), got {text!r}")
    return text


def _px_or_auto(flag: str, text: str, *, least: float, above: bool) -> float | str:
    """A px number (over `least` when `above`, else at least it), or AUTO."""
    if text == AUTO:
        return AUTO
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (value > least if above else value >= least) or not math.isfinite(value):  # a NaN is neither
        raise argparse.ArgumentTypeError(f"{flag} wants px ({'over' if above else 'at least'} {least:g}) or {AUTO}, got {text!r}")
    return value


def parse_band(text: str) -> float | str:
    return _px_or_auto("--band", text, least=0.0, above=True)


def parse_band_margin(text: str) -> float | str:
    return _px_or_auto("--band-margin", text, least=0.0, above=False)


def parse_band_up(text: str) -> tuple[float, float | str]:
    """`--band-up FLAT[,PX|auto]`: the share of the region's height over its centre that stays flat (over 0, at most 1),
    and the band above it (AUTO when left out)."""
    flat, _, band = text.partition(",")
    try:
        share = float(flat)
    except ValueError:
        share = math.nan
    if not 0 < share <= 1:  # a NaN is not
        raise argparse.ArgumentTypeError(f"--band-up wants FLAT[,PX|auto], FLAT the share of the region's height over its "
                                         f"centre that stays flat (over 0, at most 1), got {text!r}")
    return share, _px_or_auto("--band-up", band or AUTO, least=0.0, above=True)


def _run_depth(solid: np.ndarray, axis: int) -> np.ndarray:
    """Per solid pixel, how far along `axis` the nearest clear pixel is (1 beside one; past the cell's edge is clear)."""
    m = np.moveaxis(solid, axis, -1)
    n = m.shape[-1]
    at = np.broadcast_to(np.arange(n), m.shape)
    before = np.maximum.accumulate(np.where(m, -1, at), axis=-1)
    after = np.minimum.accumulate(np.where(m, n, at)[..., ::-1], axis=-1)[..., ::-1]
    return np.moveaxis(np.where(m, np.minimum(at - before, after - at), 0), -1, axis)


def wear(solid: np.ndarray, r: int) -> np.ndarray:
    """`solid` worn down by r: the pixels whose square of side 2·r + 1 around them is solid."""
    return _run_depth(_run_depth(solid, 1) > r, 0) > r


def depth(solid: np.ndarray) -> int:
    """How deep the body is at its deepest: the most it is worn down by and still has a pixel, plus one."""
    lo, hi = 0, (min(solid.shape) + 1) // 2  # worn by lo something is left; worn by hi nothing is
    if not solid.any():
        return 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if wear(solid, mid).any() else (lo, mid)
    return lo + 1


def _levels(solid: np.ndarray, worn: range) -> tuple[np.ndarray, int, int] | None:
    """`solid` worn down by each of `worn`, as one number per pixel: how many of those levels the pixel is still
    in. Each level lies inside the one before, so level i is where that number is over i. Cut to the box of the
    first level, with that box's top and left; None when nothing is left of `solid` worn down by the first."""
    level = wear(solid, worn[0])
    if not level.any():
        return None
    ys, xs = np.nonzero(level.any(axis=1))[0], np.nonzero(level.any(axis=0))[0]
    level = level[ys[0]: ys[-1] + 1, xs[0]: xs[-1] + 1]
    inside = level.astype(np.int32)
    for _ in worn[1:]:
        # Worn down by one more pixel: a square of side 2·r + 3 is one of side 2·r + 1 grown by one each way.
        across = np.pad(level, 1)
        level = across[1:-1, :-2] & across[1:-1, 1:-1] & across[1:-1, 2:]
        down = np.pad(level, 1)
        level = down[:-2, 1:-1] & down[1:-1, 1:-1] & down[2:, 1:-1]
        inside += level
    return inside, int(ys[0]), int(xs[0])


def _fft_length(n: int) -> int:
    """The first length from n on with no prime factor over 5: an FFT of it is fast."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


def _overlay(onto: tuple[list[np.ndarray], int, int], levels: tuple[np.ndarray, int, int]) -> tuple[int, int]:
    """The shift (dx, dy) of a cell from what it is laid on: where `onto` — per level a whole number at every
    pixel, with its top and left — moved by it, lands on the cell's own levels most, the numbers that land on
    the cell's pixels summed at every level and the levels summed."""
    (a, ya, xa), (b, yb, xb) = onto, levels
    # Long enough that no move wraps round (a.h + b.h - 1 each way, and so on), and fast.
    height, width = a[0].shape[0] + b.shape[0] - 1, a[0].shape[1] + b.shape[1] - 1
    shape = (_fft_length(height), _fft_length(width))
    # overlap[i, j]: what of `onto` lands on the cell's same level moved by (j - (a.w - 1), i - (a.h - 1)),
    # summed over the levels, for every move that meets.
    spectrum = sum(np.fft.rfft2((b > i).astype(np.float64), shape) * np.fft.rfft2(la[::-1, ::-1].astype(np.float64), shape)
                   for i, la in enumerate(a))
    # Sums of whole numbers are whole numbers: rounded, the FFT's own rounding (not the same on every machine)
    # is gone, so a tie is a tie on every machine, and it goes to the move nearest none.
    overlap = np.rint(np.fft.irfft2(spectrum, shape)[:height, :width]).astype(np.int64)
    shifts = [(int(j) - (a[0].shape[1] - 1) + xb - xa, int(i) - (a[0].shape[0] - 1) + yb - ya)
              for i, j in np.argwhere(overlap == overlap.max())]
    return min(shifts, key=lambda s: (s[0] ** 2 + s[1] ** 2, s[1], s[0]))


def _common(levels: list[tuple[np.ndarray, int, int]], lays: list[tuple[int, int]],
            count: int) -> tuple[list[np.ndarray], int, int] | None:
    """What the cells' bodies have in common, each cell moved back by its lay: per level (`count` of them) and
    pixel, how many more of the cells are in that level there than are not (none, where no more are), cut to
    the box of what is left, with that box's top and left; None when no pixel is in more than half of the cells.

    The head and torso are in every cell at one place and count the whole number of cells. What swings and is
    thick enough to outlast the wearing — an ear as wide as the head, up in some cells and flopped onto the
    face in others; a leg forward and back — is at any one place in half of the cells or fewer, and counts
    nothing. A thick part that keeps one place in most of the cells counts as the body does: outlines alone
    do not tell the two apart."""
    tops = [top - dy for (_, top, _), (_, dy) in zip(levels, lays)]
    lefts = [left - dx for (_, _, left), (dx, _) in zip(levels, lays)]
    top, left = min(tops), min(lefts)
    height = max(t + inside.shape[0] for t, (inside, _, _) in zip(tops, levels)) - top
    width = max(l + inside.shape[1] for l, (inside, _, _) in zip(lefts, levels)) - left
    cells_in = np.zeros((count, height, width), np.int32)
    level = np.arange(count)[:, None, None]
    for (inside, _, _), t, l in zip(levels, tops, lefts):
        cells_in[:, t - top: t - top + inside.shape[0], l - left: l - left + inside.shape[1]] += inside > level
    more = np.maximum(0, 2 * cells_in - len(levels))
    # Each level lies inside the one before, in every cell: the first level's box holds them all.
    ys, xs = np.nonzero(more[0].any(axis=1))[0], np.nonzero(more[0].any(axis=0))[0]
    if not len(ys):
        return None
    return list(more[:, ys[0]: ys[-1] + 1, xs[0]: xs[-1] + 1]), top + int(ys[0]), left + int(xs[0])


def body_motion(cells: list[Image.Image]) -> tuple[np.ndarray, np.ndarray, int, list[int]]:
    """Per cell: how far its body lies from cell 0's, down and across (px); the body's height in cell 0,
    and the first and last level its silhouette is worn down to.

    The top of the body is whatever comes to the top — a tail tip, a ponytail, an ear, a sword held
    up — and read there the motion jumps from cell to cell. What swings on the body is thinner than
    the body it swings on: worn down by a quarter of the body's depth it is gone, and what is left
    is the head and torso. Each cell is laid where its silhouette, worn down by every whole number
    of pixels from a quarter to half of that depth, overlaps cell 0's, worn alike, most, the
    overlaps of every level summed: a pixel counts once for each level it is still in, so the
    deepest of the body counts most and nothing that comes and goes outweighs it, and a torso that
    is shallower in one cell (an arm swung away from it) still counts at its shallower levels.

    That lay answers to everything left in cell 0, and a part as thick as the body is left: where
    ears as wide as the head stand up in cell 0 and flop onto the face in another cell, the cell
    is laid ears on ears, the body tens of pixels off. So the cells are laid a second time, on what
    they have in common as first laid (`_common`), and that lay, less cell 0's own, is the motion:
    the ears are at one place in too few cells to count, and the cell lies head on head and torso
    on torso."""
    solids = []
    for k, cell in enumerate(cells):
        solid = np.asarray(cell.getchannel("A")) >= ALPHA_SOLID
        if not solid.any():
            raise ValueError(f"cell {k} has no solid body")
        solids.append(solid)
    ys = np.nonzero(solids[0].any(axis=1))[0]
    height0 = int(ys[-1] - ys[0])
    deepest = depth(solids[0])
    first = int(WORN_FROM * deepest)
    worn = range(first, max(first + 1, int(WORN_TO * deepest)))
    levels = []
    for k, solid in enumerate(solids):
        level = _levels(solid, worn)
        if level is None:
            raise ValueError(f"cell {k} has no body as deep as a quarter of cell 0's ({worn[0]} px)")
        levels.append(level)
    inside0, top0, left0 = levels[0]
    lays = [_overlay(([inside0 > i for i in range(len(worn))], top0, left0), level) for level in levels]
    common = _common(levels, lays, len(worn))
    if common is None:
        raise ValueError("no part of the body is at one place in more than half of the cells")
    lays = [_overlay(common, level) for level in levels]
    dx0, dy0 = lays[0]
    return (np.asarray([dy - dy0 for _, dy in lays], float), np.asarray([dx - dx0 for dx, _ in lays], float),
            height0, [worn[0], worn[-1]])


def _cells(strip: Image.Image, n: int, w: int, h: int) -> list[Image.Image]:
    return [strip.crop((k * w, 0, (k + 1) * w, h)) for k in range(n)]


def _cell_sha256(cell: Image.Image) -> str:
    """One cell's pixels, RGBA row by row: a cell drawn alike is the same one, whatever wrote its file."""
    return hashlib.sha256(cell.tobytes()).hexdigest()


def cut_origin(strip_bytes: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    """What `video-cycle-align` keeps of a loop's strip as cut when it first aligns it (`cycle_align.origin`), so a
    follow-through whose regions were read on that cut can be carried to the aligned loop (`--read-on`): the strip's
    sha256 (the file's bytes, as `--read-on` names it), its cell size, scale and crop origin, which frame of the
    cycle each of its cells is (`sample_indices`), each cell's pixels by sha256, and the body's motion as read on
    it (`reading`: per cell how far its body lies from cell 0's, across and down, by `POLICY`). A strip whose body
    cannot be read keeps `reading` None and says why. Reads the bytes given and nothing else."""
    strip = Image.open(io.BytesIO(strip_bytes)).convert("RGBA")
    n, w, h = int(meta["frames"]), int(meta["w"]), int(meta["h"])
    rect, samples = meta.get("source_rect"), meta.get("sample_indices")
    origin: dict[str, Any] = {
        "strip_sha256": hashlib.sha256(strip_bytes).hexdigest(), "frames": n, "w": w, "h": h, "scale": meta.get("scale"),
        "crop_origin": None if rect is None else list(rect[:2]),
        # A cut made before its strip recorded the frames it took holds every frame of its cycle, or it was subsampled
        # and which ones is not known.
        "sample_indices": list(samples) if samples is not None else (list(range(n)) if n == meta.get("cycle_frames") else None),
    }
    if strip.size != (w * n, h):
        return origin | {"cells_sha256": None, "reading": None,
                         "reading_why": f"the strip is {strip.size[0]}x{strip.size[1]}, its meta says {w * n}x{h}"}
    cells = _cells(strip, n, w, h)
    origin["cells_sha256"] = [_cell_sha256(cell) for cell in cells]
    try:
        rows, cols, height0, worn = body_motion(cells)
    except ValueError as exc:
        return origin | {"reading": None, "reading_why": str(exc)}
    origin["reading"] = {"policy": POLICY, "carry_px": [[c, r] for c, r in zip(cols.tolist(), rows.tolist())],
                         "body_px": height0, "body_worn_px": worn}
    return origin


def read_on_relation(meta: dict[str, Any], strip_sha256: str, read_on: str, *, verb: str = VERB, cut: bool = False) -> str:
    """How the strip the regions were read on (`read_on`) stands to this loop: its own strip as cut (`same`), or the
    cut it was aligned from (`transported`); with that strip's record given (`cut`, `--read-on-cut`), another cut to pair
    with this loop's by the source frame (`recut`, `recut_pair`); refused `no-match` otherwise."""
    if read_on == strip_sha256:
        return "same"
    origin = (meta.get("cycle_align") or {}).get("origin")
    if origin is not None and origin.get("strip_sha256") == read_on:
        return "transported"
    if cut:
        return "recut"
    aligned_from = f"the cut it was aligned from ({origin['strip_sha256']})" if origin else "a cut it was aligned from (none is recorded)"
    raise SystemExit(f"{verb}: --read-on {read_on}: no-match — the regions were read on a strip that is neither this loop's "
                     f"strip as cut ({strip_sha256}) nor {aligned_from}, and no record of it says which source frame its cell 0 "
                     "is (--read-on-cut names that strip); locate them again on this strip")


def _clip(cut: dict[str, Any]) -> str:
    source = cut.get("source")
    return str(source.get("sha256", source)) if isinstance(source, dict) else str(source)


def cell_record(record: dict[str, Any], strip_sha256: str) -> list[dict[str, Any]]:
    """Each cell of a cut's strip as `video-loop` records it in the strip's record (`record`; `loop.CELL_RECORD`): the
    source frame it is, where its cut's anchor moved that frame before the crop, the factor each step that holds a clip's
    frames to one size scaled it by, and whether it is that frame as filmed but for that move. Read only where the
    record is of the strip whose PNG is `strip_sha256`. A ValueError says what it does not give: none (a cut made before
    video-loop wrote one), another strip's, or a field of it missing or not of its kind, naming it."""
    key = loop_mod.CELL_RECORD
    if key not in record:
        raise ValueError(f"it holds no record of them ({key}), as a cut video-loop made before it wrote one does")
    said = record[key]
    if not isinstance(said, dict):
        raise ValueError(f"its record of them is not one ({key})")
    if said.get("strip_sha256") != strip_sha256:
        raise ValueError(f"its record of them is another strip's ({key}.strip_sha256 {said.get('strip_sha256')}; the strip is "
                         f"{strip_sha256})")
    cells, n = said.get("cells"), record.get("frames")
    if not isinstance(cells, list) or len(cells) != n:
        raise ValueError(f"its record of them names {len(cells) if isinstance(cells, list) else 'no'} cells, and the strip "
                         f"has {n} ({key}.cells)")

    def number(v: Any) -> bool:
        return type(v) in (int, float) and math.isfinite(v) and v > 0

    for c, cell in enumerate(cells):
        at = f"{key}.cells[{c}]"
        if not isinstance(cell, dict):
            raise ValueError(f"its record of cell {c} is not one ({at})")
        move, scale = cell.get("anchor_move_px"), cell.get("scale")
        if type(cell.get("source_frame")) is not int:
            raise ValueError(f"its record of cell {c} names no whole source frame ({at}.source_frame)")
        if not (isinstance(move, list) and len(move) == 2 and all(type(v) is int for v in move)):
            raise ValueError(f"its record of cell {c} gives no whole move of its anchor ({at}.anchor_move_px)")
        if not (isinstance(scale, dict) and all(number(scale.get(step)) for step in loop_mod.CELL_SCALE_STEPS)):
            raise ValueError(f"its record of cell {c} does not give the factor each hold scaled it by ({at}.scale: "
                             f"{', '.join(loop_mod.CELL_SCALE_STEPS)})")
        if type(cell.get("as_filmed")) is not bool:
            raise ValueError(f"its record of cell {c} does not say whether it is that frame as filmed ({at}.as_filmed)")
    return cells


def _not_as_filmed(cell: dict[str, Any], c: int) -> str | None:
    """Why cell `c`, as its cut's record gives it (`cell_record`), is not its source frame as filmed but for its anchor's
    move: a step scaled it, or video-loop did not cut it as filmed. None where it is."""
    at = f"{loop_mod.CELL_RECORD}.cells[{c}]"
    for step, words in loop_mod.CELL_SCALE_STEPS.items():
        if cell["scale"][step] != 1:
            return (f"{words} scaled it by {cell['scale'][step]:g} about its feet before the cut ({at}.scale.{step}), and "
                    "no whole px shift undoes a scale")
    if not cell["as_filmed"]:
        return (f"video-loop did not cut it as filmed ({at}.as_filmed): its pixels at the end of the cut are not its pixels "
                "anchored, before any repair, or a step held the clip's frames to one size")
    return None


def aligned_from(loop_dir: Path, meta_path: Path, meta: dict[str, Any], verb: str = VERB) -> dict[str, Any] | None:
    """On a loop aligned from its cut (`cycle_align`), the cut's own strip record, as the first alignment kept it beside
    the cut's frames as filmed (`cycle.source/<name>.strip.json`, `align._rebuild`); None on a loop never aligned, and
    where none is kept."""
    if (meta.get("cycle_align") or {}).get("origin") is None:
        return None
    path = loop_dir / loop_mod.CYCLE_SOURCE_DIR / meta_path.name
    if not path.is_file():
        return None
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{verb}: {path}: not a strip record ({exc})") from exc
    if not isinstance(kept, dict):
        raise SystemExit(f"{verb}: {path}: not a strip record")
    return kept


def recut_pair(meta: dict[str, Any], cut: dict[str, Any], read_on: str, *, strip_sha256: str,
               kept: dict[str, Any] | None = None, verb: str = VERB) -> dict[str, Any]:
    """How the cut the regions were read on (`cut`: its record, `--read-on-cut`; `read_on` its strip's sha256) pairs with
    this loop's cut, both cut from one source clip, by each cut's record of its cells (`cell_record`) and nothing else:
    the source frame the cut's cell 0 is, the cell of this loop's cut that is that frame, and the whole px across and down
    a pixel of that frame lies from where it lies in the cut — their crops and scales (`source_rect`) taken on the frame
    as filmed, each less where its cut's anchor moved that frame before the crop (`anchor_move_px`); a pixel lies at
    most RECUT_DRIFT px off that shift anywhere in the cell. This loop's cut is its own record (`meta`, of its strip as
    cut, `strip_sha256`), or on a loop aligned from its cut, the cut's own record the alignment kept (`kept`,
    `aligned_from`), whose strip the alignment's origin names. Refused `no-match` where the strip named is a loop
    aligned from a cut, a record of the cells is not there, is another strip's or does not give them as video-loop
    writes it, the two are not one clip's, this loop's cut does not hold the frame, or either cell is not that frame as
    filmed (`_not_as_filmed`), saying which; `uncertain` where their crops do not set the frame apart by one whole shift.
    Whether the two cells are that frame's pixels at the shift is `recut_same`'s."""

    def no_match(why: str) -> SystemExit:
        return SystemExit(f"{verb}: --read-on {read_on}: no-match — the regions were read on another cut, {why}; locate them "
                          "again on this strip")

    if cut.get("cycle_align") is not None:
        raise no_match("but the strip --read-on-cut names is a loop aligned from its cut (cycle_align), whose cells are not "
                       "the cut's frames in order")
    origin = (meta.get("cycle_align") or {}).get("origin")
    own, own_sha256 = (meta, strip_sha256) if origin is None else (kept, origin["strip_sha256"])
    cells = []
    for whose, record, sha in (("the cut's (--read-on-cut)", cut, read_on), ("this loop's", own, own_sha256)):
        if record is None:
            raise no_match("and this loop is aligned from its cut (cycle_align), whose strip record is not kept beside its "
                           f"frames as filmed ({loop_mod.CYCLE_SOURCE_DIR}/<name>.strip.json)")
        try:
            cells.append(cell_record(record, sha))
        except ValueError as exc:
            raise no_match(f"but {whose} strip record does not give its cells: {exc}") from None
    then, now = cut.get("source_cut"), own.get("source_cut")
    for whose, record in (("the cut's (--read-on-cut)", then), ("this loop's", now)):
        if not isinstance(record, dict) or "source" not in record:
            raise no_match(f"and {whose} record names no clip it is cut from (source_cut)")
    if then["source"] != now["source"]:
        raise no_match(f"of another clip ({_clip(then)}; this loop's is {_clip(now)})")
    frame, held = cells[0][0]["source_frame"], [c["source_frame"] for c in cells[1]]
    if frame not in held:
        raise no_match(f"whose cell 0 is source frame {frame}, which this loop's cut does not hold (it holds source frames "
                       f"{min(held)} to {max(held)})")
    cell = held.index(frame)
    for whose, record, at in (("the cut's (--read-on-cut)", cells[0], 0), ("this loop's", cells[1], cell)):
        why = _not_as_filmed(record[at], at)
        if why is not None:
            raise no_match(f"whose cell 0 is source frame {frame} (this loop's cut's cell {cell}), but {whose} cell {at} is "
                           f"not that frame as filmed: {why}")

    def uncertain(why: str) -> SystemExit:
        return SystemExit(f"{verb}: --read-on {read_on} is a cut of this loop's clip, paired at source frame {frame} (this "
                          f"loop's cut's cell {cell}), but uncertain: {why}; the regions are not moved — only by a whole px "
                          "shift, never scaled")

    rects = cut.get("source_rect"), own.get("source_rect")
    if rects[0] is None or rects[1] is None:
        raise uncertain("a cut records no crop (laid by its feet, each frame is placed on its own)")
    moves = cells[0][0]["anchor_move_px"], cells[1][cell]["anchor_move_px"]
    sizes = (int(cut["w"]), int(cut["h"])), (int(own["w"]), int(own["h"]))
    shift, drift = [], []
    for axis in (0, 1):
        # each crop on the frame as filmed: where it lies on the frame its anchor moved, less that move
        (lo0, hi0), (lo1, hi1) = ((rect[axis] - move[axis], rect[axis + 2] - move[axis]) for rect, move in zip(rects, moves))
        per0, per1 = sizes[0][axis] / (hi0 - lo0), sizes[1][axis] / (hi1 - lo1)  # cell px a source px, then and now
        # where the first and the last pixel of the cut's cell lie in this loop's cell, less where they lay
        ends = [(lo0 + (x + 0.5) / per0 - lo1) * per1 - 0.5 - x for x in (0, sizes[0][axis] - 1)]
        whole = int(math.floor(0.5 * (ends[0] + ends[1]) + 0.5))
        shift.append(whole)
        drift.append(round(max(abs(end - whole) for end in ends), 4))
    if max(drift) > RECUT_DRIFT:
        raise uncertain(f"their crops, scales and anchors set a pixel up to {max(drift):g} px off one whole shift ({shift[0]}, "
                        f"{shift[1]}) across the cell, more than {RECUT_DRIFT:g}")
    return {"pair_policy": RECUT_POLICY, "source_frame": frame, "cut_cell": cell, "shift_px": shift, "drift_px": drift,
            "cut": {"sha256": read_on, "source": then.get("source"), "start": then.get("start"), "length": then.get("length"),
                    "w": sizes[0][0], "h": sizes[0][1], "source_rect": list(rects[0])}}


def recut_same(pair: dict[str, Any], cut_cell: Image.Image, cell: Image.Image, read_on: str, *, verb: str = VERB) -> None:
    """Whether the cut's cell 0 (`cut_cell`) and this loop's cell paired with it (`cell`, `recut_pair`) are the same
    pixels where they overlap, the one laid on the other at the pair's whole shift: refused `no-match` where they are
    not, saying how many differ: a record that calls a cell the frame as filmed is held to that frame's pixels, and two
    cells whose crops and scales set a pixel off the whole shift (`drift_px`) are resampled apart and refused alike."""
    a, b = np.asarray(cut_cell.convert("RGBA")), np.asarray(cell.convert("RGBA"))
    sx, sy = pair["shift_px"]
    x0, x1 = max(0, -sx), min(a.shape[1], b.shape[1] - sx)
    y0, y1 = max(0, -sy), min(a.shape[0], b.shape[0] - sy)
    seen = max(0, x1 - x0) * max(0, y1 - y0)
    differ = int(np.any(a[y0:y1, x0:x1] != b[y0 + sy:y1 + sy, x0 + sx:x1 + sx], axis=-1).sum()) if seen else 0
    if seen and not differ:
        return
    off = max(pair["drift_px"])
    raise SystemExit(f"{verb}: --read-on {read_on}: no-match — the regions were read on another cut, whose cell 0 is source "
                     f"frame {pair['source_frame']} (this loop's cut's cell {pair['cut_cell']}), but the two cells are not "
                     f"the same pixels where they overlap at the shift ({sx}, {sy}): "
                     + (f"{differ} of {seen} px differ" if seen else "they do not overlap")
                     + (f" (their crops and scales set a pixel up to {off:g} px off the shift)" if off else "")
                     + "; locate them again on this strip")


def _recut_mask(mask: np.ndarray, shift: list[int], size: tuple[int, int], what: str, verb: str) -> np.ndarray:
    """A mask drawn on another cut's cell, moved by the whole px `shift` onto a cell of `size`: the same pixels; refused
    where any would leave the cell."""
    w, h = size
    out = np.zeros((h, w), bool)
    ys, xs = np.nonzero(mask)
    ys, xs = ys + shift[1], xs + shift[0]
    inside = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    if not inside.all():
        raise SystemExit(f"{verb}: {what} moved by ({shift[0]}, {shift[1]}) px onto this loop's {w}x{h} cell leaves "
                         f"{int((~inside).sum())} px past its edge")
    out[ys, xs] = True
    return out


def _cut_cell(cut: dict[str, Any], pair: dict[str, Any], read_on: str, verb: str = VERB) -> Image.Image:
    """The cut's cell 0, from its strip (`read_cut`) at the size its record gives it (`recut_pair`); refused `no-match`
    where the strip is not that record's size."""
    w, h, n = pair["cut"]["w"], pair["cut"]["h"], int(cut["meta"]["frames"])
    if cut["strip"].size != (w * n, h):
        raise SystemExit(f"{verb}: --read-on {read_on}: no-match — the regions were read on another cut, but its strip is "
                         f"{cut['strip'].size[0]}x{cut['strip'].size[1]} and its record says {n} cells of {w}x{h}; locate "
                         "them again on this strip")
    return cut["strip"].crop((0, 0, w, h))


def recut_ask(ask: "Ask", pair: dict[str, Any], size: tuple[int, int], *, verb: str = VERB) -> "Ask":
    """`ask`, read on another cut of the clip (`recut_pair`), moved onto this loop's cell that is the same source frame:
    each region's centre by the whole shift, a region mask and a hold mask drawn on that cell by the shift, pixel for
    pixel. A folder of masks, one per cell, is drawn on this loop's cells already and is refused."""
    sx, sy = pair["shift_px"]
    regions = [(cx + sx, cy + sy, *rest) for cx, cy, *rest in ask.regions]
    mask, hold = ask.mask, ask.hold
    if mask is not None:
        if mask.per_cell:
            raise SystemExit(f"{verb}: --region-mask is a folder, one mask per cell of this loop; regions read on another cut "
                             "(--read-on-cut) are drawn on its cell 0 alone: give one PNG")
        if mask.hold is not None and not mask.hold.per_cell:
            mask = dataclasses.replace(mask, hold=dataclasses.replace(
                mask.hold, masks=[_recut_mask(mask.hold.masks[0], [sx, sy], size, "--hold-mask", verb)]))
        mask = dataclasses.replace(mask, masks=[_recut_mask(mask.masks[0], [sx, sy], size, "--region-mask", verb)])
    if hold is not None and not hold.per_cell:
        hold = dataclasses.replace(hold, masks=[_recut_mask(hold.masks[0], [sx, sy], size, "--hold-mask", verb)])
    return dataclasses.replace(ask, regions=regions, mask=mask, hold=hold)


def transported_motion(meta: dict[str, Any], cells: list[Image.Image], read_on: str, *, verb: str = VERB,
                       paired: dict[str, Any] | None = None) -> tuple[np.ndarray, np.ndarray, int, list[int], list[int]]:
    """The cut's reading carried to `cells`, the strip of a loop aligned from the cut `read_on` names: per cell how far
    the body of the cut's cell it is lies from the cut's cell 0 (down, then across, as `body_motion` gives them), the
    body's height and wear as read on the cut, and which cell of the cut each cell is.

    The alignment records where each cell is from (`cycle_align.cells_from`). A cell made between two of the cut's
    has no reading, and is not given its nearer one's; a cell the record calls the cut's must be that cell pixel for
    pixel, so a crop, a scale or a drawing changed since — and not recorded as a transform — is caught here. Either
    is refused `uncertain`. With `paired` (`recut_pair`) the regions were read on another cut of the clip, paired with
    the cut this loop was aligned from, and the refusal says so."""
    record = meta.get("cycle_align") or {}
    origin, cells_from = record["origin"], record.get("cells_from")
    stands = ("the cut this loop was aligned from" if paired is None else
              f"a cut of this loop's clip, paired at source frame {paired['source_frame']} with the cut this loop was aligned "
              f"from ({origin['strip_sha256']})")

    def uncertain(why: str) -> SystemExit:
        return SystemExit(f"{verb}: --read-on {read_on} is {stands}, but uncertain: {why}; the reading is not carried — only "
                          "a whole cell of the cut carries it, and none is taken by rounding")

    reading = origin.get("reading")
    if reading is None:
        raise uncertain(f"the cut's reading was not recorded ({origin.get('reading_why')})")
    if reading.get("policy") != POLICY:
        raise uncertain(f"the cut was read as {reading.get('policy')}, and this engine reads as {POLICY}")
    if not isinstance(cells_from, list) or len(cells_from) != len(cells):
        raise uncertain(f"the alignment records where {len(cells_from) if isinstance(cells_from, list) else 'no'} cells are "
                        f"from, and the strip has {len(cells)}")
    for k, entry in enumerate(cells_from):
        if "source" not in entry:
            i, j = entry["between"]
            raise uncertain(f"cell {k} is made between the cut's frames {i} and {j} (t {entry['t']:g})")
    (w, h), rect = cells[0].size, meta.get("source_rect")
    now = (w, h, meta.get("scale"), None if rect is None else list(rect[:2]))
    then = (origin["w"], origin["h"], origin["scale"], origin["crop_origin"])
    # A cut made before the strip recorded its crop says nothing of where it was cropped: there the cells' pixels
    # below are the whole test, as they are for every cell anyway.
    if now[:3] != then[:3] or (then[3] is not None and now[3] != then[3]):
        raise uncertain(f"the cut's crop or scale changed (cells {then[0]}x{then[1]} at scale {then[2]} from {then[3]}, now "
                        f"{w}x{h} at scale {now[2]} from {now[3]}) and no transform is recorded")
    frames, digests = origin["sample_indices"], origin["cells_sha256"]
    cut_cells = []
    for k, (cell, entry) in enumerate(zip(cells, cells_from)):
        i = entry["source"]
        if frames is None or i not in frames:
            raise uncertain(f"cell {k} is frame {i} of the cut's cycle, which the cut's strip does not hold")
        j = frames.index(i)
        if _cell_sha256(cell) != digests[j]:
            raise uncertain(f"cell {k} is not the cut's cell {j} as drawn: its pixels differ")
        cut_cells.append(j)
    carry = reading["carry_px"]
    return (np.asarray([carry[j][1] for j in cut_cells], float), np.asarray([carry[j][0] for j in cut_cells], float),
            int(reading["body_px"]), list(reading["body_worn_px"]), cut_cells)


def sway_offsets(motion: np.ndarray, fps: float, *, freq: float, zeta: float, gain: float) -> np.ndarray:
    """The part's offset from where the body carries it, per cell, in the cycle's steady state."""
    n = len(motion)
    spectrum = np.fft.rfft(motion - motion.mean())
    k = np.arange(len(spectrum))
    spectrum[k > HARMONICS] = 0
    omega = 2 * math.pi * fps / n * k  # each harmonic's angular frequency
    w = 2 * math.pi * freq
    # x = H·y with y the body's position: x'' + 2ζw x' + w² x = −y''  ⇒  H = Ω² / (w² − Ω² + 2iζwΩ)
    response = omega ** 2 / (w ** 2 - omega ** 2 + 2j * zeta * w * omega)
    return gain * np.fft.irfft(spectrum * response, n)


def fold_ratio(reach: float, radius: float) -> float:
    """How near a move of `reach` px is to folding a region whose smaller radius is `radius`: at 1
    the steepest part of the weight (cos², slope π / (2 · radius)) stops the picture, past it the
    picture runs backwards."""
    return reach * math.pi / (2 * radius)


def gain_limit(reach_per_gain: float, radius: float) -> float | None:
    """The gain at which a region of that radius folds; no gain folds it when the body is still."""
    return 2 * radius / (math.pi * reach_per_gain) if reach_per_gain > 0 else None


def lowered_gain(gain: float, reach_per_gain: float, radius: float) -> float:
    """The largest gain, in steps of GAIN_STEP and no more than `gain`, that `fold_ratio` passes."""
    limit = gain_limit(reach_per_gain, radius)
    if limit is None:
        return gain
    steps = math.ceil(limit / GAIN_STEP) - 1  # under the limit, never on it
    # The step count is rounded from floats: the one test that decides is `fold_ratio` itself.
    while steps > 0 and fold_ratio(steps * GAIN_STEP * reach_per_gain, radius) >= 1:
        steps -= 1
    return min(gain, round(steps * GAIN_STEP, 2))


def stretch_per_gain(radii: tuple[float, float], across: np.ndarray, down: np.ndarray, flat: float = 0.0) -> tuple[float, int]:
    """How much of a pixel's area a region's move takes per unit of gain, at its worst cell, and that cell: the cos²
    weight is steepest at π/2 over each radius, so a move (ox, oy) leaves 1 − (π/2)·hypot(ox/rx, oy/ry) of the area
    (`across`, `down`: the move at gain 1, per cell); a region with a flat share falls over what is left of each radius,
    1 − flat of it, and leaves 1 − (π/2)/(1 − flat)·hypot(ox/rx, oy/ry)."""
    rx, ry = radii
    per_cell = (math.pi / 2) / (1 - flat) * np.hypot(across / rx, down / ry)
    k = int(np.argmax(per_cell))
    return float(per_cell[k]), k


def floor_gain(gain: float, per_gain: float, floor: float) -> float:
    """The largest gain, in steps of GAIN_STEP and no more than `gain`, whose move leaves `floor` of a pixel's area or
    more (1 − gain · `per_gain` ≥ `floor`)."""
    if per_gain <= 0 or 1 - gain * per_gain >= floor:
        return gain
    steps = math.floor((1 - floor) / per_gain / GAIN_STEP) + 1
    # The step count is rounded from floats: the one test that decides is the area left at the gain written.
    while steps > 0 and 1 - round(steps * GAIN_STEP, 2) * per_gain < floor:
        steps -= 1
    return min(gain, round(steps * GAIN_STEP, 2))


def _sample(premultiplied: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    h, w = premultiplied.shape[:2]
    x0 = np.clip(np.floor(sx).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, h - 2)
    fx = np.clip(sx - x0, 0, 1)[..., None]
    fy = np.clip(sy - y0, 0, 1)[..., None]
    a, b = premultiplied[y0, x0], premultiplied[y0, x0 + 1]
    c, d = premultiplied[y0 + 1, x0], premultiplied[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def region_weight(xx: np.ndarray, yy: np.ndarray, region: tuple[float, ...],
                  shift: tuple[float, float]) -> np.ndarray:
    """Per pixel of a cell, how much of a region's move it takes: 1 at the centre of the ellipse carried by
    `shift` — and over its flat share of the way to the rim, where it has one — 0 at its rim (cos²) and outside it:
    the Breathe layer's chest weight (`breathe.flat_cos2`). The pixels a region covers are those where this is over 0."""
    cx, cy, rx, ry = region[:4]
    r = np.sqrt(((xx - cx - shift[0]) / rx) ** 2 + ((yy - cy - shift[1]) / ry) ** 2)
    return breathe_mod.flat_cos2(r, flat_of(region))


def move_field(xx: np.ndarray, yy: np.ndarray, moves: list[tuple[list[tuple[float, float, float, float]], tuple[float, float]]],
               shift: tuple[float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Per pixel of a cell: whether a region of `moves`, carried by `shift`, covers it, and how far it is moved
    across and down. None when no region covers a pixel of the cell."""
    weights = []
    for regions, _ in moves:
        weight = np.zeros(xx.shape, np.float32)
        for region in regions:
            weight = np.maximum(weight, region_weight(xx, yy, region, shift))
        weights.append(weight)
    return _largest_move(weights, [offset for _, offset in moves])


def _largest_move(weights: list[np.ndarray], offsets: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Per pixel, whether a group's weight (one per offset) is over 0 there, and the largest of the groups' weighted
    moves, across and down. None when no weight is over 0 anywhere."""
    inside = np.logical_or.reduce([weight > 0 for weight in weights])
    if not inside.any():
        return None
    largest = np.argmax(np.stack([math.hypot(*offset) * weight for offset, weight in zip(offsets, weights)]), axis=0)[None]
    move_x = np.take_along_axis(np.stack([offset[0] * weight for offset, weight in zip(offsets, weights)]), largest, 0)[0]
    move_y = np.take_along_axis(np.stack([offset[1] * weight for offset, weight in zip(offsets, weights)]), largest, 0)[0]
    return inside, move_x, move_y


def band_weight(xx: np.ndarray, yy: np.ndarray, centre: tuple[float, float], flat: tuple[float, float, float],
                band: tuple[float, float, float]) -> np.ndarray:
    """Per pixel, how much of a banded region's move it takes (`--band`): 1 inside the flat ellipse about `centre` — its
    radii across, above and below (`flat`) — 0 on and past the outer one, whose radii are those and `band`'s (across,
    above, below), and between them cos² of the share of the way across, along the ray from the centre. The pixels the
    region covers are those where this is over 0."""
    (cx, cy), (ax, up, down), (bx, band_up, band_down) = centre, flat, band
    dx, dy = np.asarray(xx, np.float64) - cx, np.asarray(yy, np.float64) - cy
    above = dy < 0
    ay, by = np.where(above, up, down), np.where(above, band_up, band_down)
    inner = np.hypot(dx / ax, dy / ay)
    outer = np.hypot(dx / (ax + bx), dy / (ay + by))
    with np.errstate(divide="ignore", invalid="ignore"):
        # Along the ray, inner/outer is the outer radius over the flat one: the share across is linear in the distance.
        share = np.clip((inner - 1) * outer / (inner - outer), 0.0, 1.0)
        return np.where(inner <= 1, 1.0, np.where(outer < 1, np.cos(0.5 * math.pi * share) ** 2, 0.0))


def move_regions(cell: Image.Image, moves: list[tuple[list[tuple[float, float, float, float]], tuple[float, float]]],
                 shift: tuple[float, float]) -> Image.Image:
    """`cell` with each group of regions moved by its own offset (dx, dy), their centres carried by `shift`.

    The groups' offsets are one motion times their gains, so where regions of different groups
    overlap the larger move wins: the move is the largest of the groups' weighted moves, which is
    nowhere steeper than the steepest group's own. Where every offset is 0 the cell is as it was, byte for
    byte — the colour under its clear pixels too, which a move clears."""
    if all(dx == 0 and dy == 0 for _, (dx, dy) in moves):
        return cell.copy()
    return _draw(cell, lambda xx, yy: move_field(xx, yy, moves, shift))


def _draw(cell: Image.Image, field_of: Any) -> Image.Image:
    """`cell` moved by the field `field_of(xx, yy)` gives (whether a pixel is covered, its move across and down; None:
    nothing is), sampled as premultiplied bilinear colour; a pixel no region covers keeps its bytes."""
    src = np.asarray(cell.convert("RGBA"), dtype=np.float32)
    h, w = src.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    field = field_of(xx, yy)
    if field is None:
        return cell.copy()
    inside, move_x, move_y = field
    pm = src.copy()
    pm[..., :3] *= pm[..., 3:4] / 255
    moved = _sample(pm, xx - move_x, yy - move_y)
    alpha = np.clip(np.round(moved[..., 3:4]), 0, 255)
    # Colour is read back from the premultiplied mix, and none is kept where the coverage
    # rounds to nothing (the WebP check refuses colour under alpha 0).
    moved[..., :3] = np.where(alpha > 0, moved[..., :3] * 255 / np.maximum(moved[..., 3:4], 1e-3), 0)
    moved[..., 3:4] = alpha
    out = np.where(inside[..., None], np.clip(np.round(moved), 0, 255), src).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def jacobian(move_x: np.ndarray, move_y: np.ndarray) -> np.ndarray:
    """Per pixel of a moved cell, but for those on its edge, the area of the source it takes. The moved cell's
    pixel p is read from the source at p less its move, so the area is read between p's neighbours on either
    side, across and down, as half of how far apart those are read: 1 where nothing moves, 0 where the picture
    stops, under 0 where it runs backwards. Between the neighbours on either side, not from p to the next pixel
    one way: read one way, a round region a few pixels across reads steeper on the side it bulges to than its
    weight is anywhere. One row and one column shorter than the cell at each edge."""
    across_x, across_y = (move_x[1:-1, 2:] - move_x[1:-1, :-2]) / 2, (move_y[1:-1, 2:] - move_y[1:-1, :-2]) / 2
    down_x, down_y = (move_x[2:, 1:-1] - move_x[:-2, 1:-1]) / 2, (move_y[2:, 1:-1] - move_y[:-2, 1:-1]) / 2
    return (1 - across_x) * (1 - down_y) - down_x * across_y


def _whole_premultiplied(cell: Image.Image) -> np.ndarray:
    """A cell's premultiplied RGBA as whole numbers, 255 times it (each colour times the alpha, and the alpha times 255):
    a squared difference of two of them, summed, is a whole number, the same on every machine."""
    rgba = np.asarray(cell.convert("RGBA"), np.int64)
    return np.concatenate([rgba[..., :3] * rgba[..., 3:4], rgba[..., 3:4] * 255], axis=-1)


@dataclass(frozen=True)
class Carried:
    """Where a region's own pixels are in a cell (`carry_match`): their shift from the cell they were read on, or None
    and why that is uncertain; the least of the masked squared difference, and its second difference across and down
    there, each per pixel of the mask in premultiplied colour (0 to 255) squared."""

    shift: tuple[float, float] | None
    why: str | None
    least: float
    curvature: tuple[float, float] | None = None


def carry_match(base: np.ndarray, cell: np.ndarray, mask: np.ndarray, guess: tuple[float, float],
                search: int = CARRY_SEARCH, around: str = "the lay") -> Carried:
    """Where the pixels of `base` under `mask` are in `cell` (both `_whole_premultiplied`): the whole-pixel move, within
    `search` px of `guess` each way (`around` says what it is: the body's lay, or where the cell before had them), with
    the least squared difference over the mask — past the cell's edge is clear — and a parabola through it and its
    neighbours each way for the part of a pixel. Uncertain where the least is on the search's edge (the pixels may be
    further), where it is tied with a place more than a pixel away, or where the difference does not curve up across or
    down (nothing there tells one place from the next)."""
    ys, xs = np.nonzero(mask)
    gx, gy = int(round(guess[0])), int(round(guess[1]))
    pad = search + math.ceil(max(abs(guess[0]), abs(guess[1]))) + 2
    padded = np.pad(cell, ((pad, pad), (pad, pad), (0, 0)))
    steps = np.arange(-search, search + 1)
    rows = ys[None, None, :] + (gy + steps)[:, None, None] + pad
    cols = xs[None, None, :] + (gx + steps)[None, :, None] + pad
    grid = ((padded[rows, cols] - base[ys, xs]) ** 2).sum(axis=(2, 3))  # [down, across], whole numbers
    unit = len(ys) * 255 ** 2
    iy, ix = np.unravel_index(int(np.argmin(grid)), grid.shape)
    least = int(grid[iy, ix])
    edge = 2 * search
    if iy in (0, edge) or ix in (0, edge):
        return Carried(None, f"the least is on the search's edge, {search} px from {around}", least / unit)
    if (np.abs(np.argwhere(grid == least) - [iy, ix]).max(axis=1) > 1).any():
        return Carried(None, "the least is tied with a place more than a pixel away", least / unit)
    across = int(grid[iy, ix - 1]) - 2 * least + int(grid[iy, ix + 1])
    down = int(grid[iy - 1, ix]) - 2 * least + int(grid[iy + 1, ix])
    curvature = (across / unit, down / unit)
    if across <= 0 or down <= 0:
        way = "across" if across <= 0 else "down"
        return Carried(None, f"the difference does not curve up {way}", least / unit, curvature)
    fx = float(np.clip(0.5 * (int(grid[iy, ix - 1]) - int(grid[iy, ix + 1])) / across, -0.5, 0.5))
    fy = float(np.clip(0.5 * (int(grid[iy - 1, ix]) - int(grid[iy + 1, ix])) / down, -0.5, 0.5))
    return Carried((gx - search + int(ix) + fx, gy - search + int(iy) + fy), None, least / unit, curvature)


def carry_own(base: Image.Image, cells: list[Image.Image], regions: list[tuple[float, ...]], base_shift: tuple[float, float],
              lays: list[tuple[float, float]], base_index: int | None) -> list[list[dict[str, Any]]]:
    """Per cell and region, where a band carries the region (`--band`): by its own pixels — the solid pixels under its
    ellipse in `base`, the cell it was read on or carried to by `base_shift`, found in the cell (`carry_match`) — or by
    the lay where that is uncertain, saying why. The cell `base_index` is `base` itself. The cells are looked at in the
    loop's order from it (from cell 0 where `base` is none of them), each around where the cell before had the pixels:
    a part moves little from one cell to the next, and the outline's lay can jump where the outline changes (an arm
    swung out from the body); around the cell's own lay where the cell before had them by its lay, or is `base`."""
    w, h = base.size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    solid = np.asarray(base.getchannel("A")) >= ALPHA_SOLID
    whole = _whole_premultiplied(base)
    cell_wholes: dict[int, np.ndarray] = {}
    out: list[list[dict[str, Any]]] = [[] for _ in cells]
    for region in regions:
        mask = (region_weight(xx, yy, region, base_shift) > 0) & solid
        entries = carry_mask(whole, cells, cell_wholes, mask, base_shift, lays, base_index,
                             "no solid pixel of the cell it was read on is under it")
        for k in range(len(cells)):
            out[k].append(entries[k])
    return out


def carry_mask(whole: np.ndarray, cells: list[Image.Image], cell_wholes: dict[int, np.ndarray], mask: np.ndarray,
               base_shift: tuple[float, float], lays: list[tuple[float, float]], base_index: int | None,
               empty: str) -> dict[int, dict[str, Any]]:
    """Per cell, where the pixels of `whole` (`_whole_premultiplied` of the cell they were read on, carried there by
    `base_shift`) under `mask` are (`carry_match`), as `carry_own` carries a region: in the loop's order from `base_index`,
    each cell around where the cell before had them, or around its lay; by the lay where that is uncertain, or where the
    mask holds no pixel (`empty` says why). `cell_wholes` keeps each cell's `_whole_premultiplied` once read."""
    n = len(cells)
    order = list(range(n)) if base_index is None else [(base_index + i) % n for i in range(1, n)]
    entries: dict[int, dict[str, Any]] = {}
    if base_index is not None:
        entries[base_index] = {"px": [float(base_shift[0]), float(base_shift[1])], "from": "base"}
    before: tuple[int, tuple[float, float]] | None = None  # the cell before and where its own pixels were found
    for k in order:
        lay = (float(lays[k][0]), float(lays[k][1]))
        if not mask.any():
            entries[k] = {"px": list(lay), "from": "lay", "why": empty}
            continue
        if k not in cell_wholes:
            cell_wholes[k] = _whole_premultiplied(cells[k])
        if before is None:
            guess, around = (lay[0] - base_shift[0], lay[1] - base_shift[1]), "the lay"
        else:
            guess, around = before[1], f"where cell {before[0]} had it"
        found = carry_match(whole, cell_wholes[k], mask, guess, around=around)
        entry: dict[str, Any] = {"least": found.least, "curvature": None if found.curvature is None else list(found.curvature)}
        if found.shift is None:
            entries[k] = {"px": list(lay), "from": "lay", "why": found.why} | entry
            before = None
        else:
            entries[k] = {"px": [base_shift[0] + found.shift[0], base_shift[1] + found.shift[1]], "from": "pixels"} | entry
            before = (k, found.shift)
    return entries


@dataclass(frozen=True)
class Band:
    """A band asked (`--band`): its px, the margin's (`--band-margin`) and, above the centre, the share of the region's
    height that stays flat and the band from there (`--band-up`; None: above as below). Each px is a number or AUTO, read
    off the move (`band_shapes`)."""

    band: float | str
    margin: float | str = AUTO
    up: tuple[float, float | str] | None = None

    def asked(self) -> dict[str, Any]:
        return {"band": self.band, "margin": self.margin, "up": None if self.up is None else {"flat": self.up[0], "band": self.up[1]}}


def band_shapes(band: Band, regions: list[tuple[float, ...]], reach: float) -> tuple[float, float, list[tuple[tuple[float, float, float], tuple[float, float, float]]]]:
    """The band's px and the margin's, AUTO read off the move's reach at the gain asked — the margin the reach and a
    pixel, ceil(reach) + 1, so the part is still on the flat where it goes, and the band the narrowest the move does
    not fold, ceil(reach × π / 2) + 1 — and per region its flat radii and its bands, each across, above and below."""
    band_px = float(math.ceil(reach * math.pi / 2) + 1) if band.band == AUTO else float(band.band)
    margin_px = float(math.ceil(reach) + 1) if band.margin == AUTO else float(band.margin)
    shapes = []
    for _, _, rx, ry in regions:
        flat, bands = [rx + margin_px, ry + margin_px, ry + margin_px], [band_px, band_px, band_px]
        if band.up is not None:
            share, up = band.up
            flat[1] = share * ry
            bands[1] = max(band_px, (1 - share) * ry + HANG_PAST) if up == AUTO else float(up)
        shapes.append((tuple(flat), tuple(bands)))
    return band_px, margin_px, shapes


@dataclass(frozen=True, eq=False)
class Banded:
    """What a band comes to on the cells: its px and the margin's, read off `reach` (the move's at the gain asked), each
    region's flat radii and bands, where each region is carried in every cell (`carry_own`), the steepest its weight falls
    along the move (`band_steepest`), and the ledger of what the move does in each cell (`band_ledger`)."""

    asked: Band
    reach: float
    band: float
    margin: float
    shapes: list[tuple[tuple[float, float, float], tuple[float, float, float]]]
    carried: list[list[dict[str, Any]]]  # per cell, per region
    steepest: list[tuple[float, int]]  # per region, u·∇w at its steepest per unit of gain, and the cell (`band_steepest`)
    ledger: dict[str, Any] | None = None


@dataclass(frozen=True, eq=False)
class Breath:
    """The breath a sway follows (`--breathe`): the Breathe layer's record of it — `sprite-gen breathe` writes
    breathe.json beside the frames it breathed — read and checked, and how late the part follows it (`--lag`)."""

    path: Path
    sha256: str  # of the record's bytes
    phases: list[float]  # per cell, the breath's phase
    beat: dict[str, Any]  # the breath count, or the cell of each in-breath's peak
    lift: float  # px the chest rises at the top of an in-breath (breathe.smooth_lift)
    heading: float  # degrees from straight up the chest rises toward (its chest_dir)
    inhale: float  # share of one breath the in-breath takes (its inhale): the phase the chest peaks at
    lag: float  # of one breath
    start_cell: int = 0  # the loop's cell the cells start at (its start_cell)
    loop_cells: int | None = None  # the cells of the loop they are a stretch of (its loop_cells); None: they are the loop
    loop: list[float] | None = None  # the loop's phases (breathe.breathe_loop); None: the cells' own

    def unit(self, phases: list[float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Per cell (or per phase of `phases`), the part's offset from where the breath carries it at gain 1, across and
        down: the chest rises `lift` × w (breathe.smooth_wave, of the record's in-breath share), the part as far late by
        `lag` (the time warp the Breathe layer's shoulders are late by, breathe.smooth_shoulder_wave), and the part lies
        the difference behind, against the way the chest rises. 0 where the breath rests — the loop's first cell, and
        where it closes. A phase's offset is the phase's alone, so a stretch's cell is offset as the loop's cell of its
        phase is, from its first cell on: nothing is wound up before it."""
        wave, late = breathe_mod.smooth_wave, breathe_mod.smooth_shoulder_wave
        share = np.asarray([wave(p, self.inhale) - late(p, self.lag, self.inhale)
                            for p in (self.phases if phases is None else phases)])
        heading = math.radians(self.heading)
        return self.lift * share * -math.sin(heading) + 0.0, self.lift * share * math.cos(heading)

    def whole(self) -> tuple[np.ndarray, np.ndarray]:
        """The offset at gain 1 over the whole breath the cells are swayed on, per phase: the loop's every cell for a
        stretch of a loop, so the fold rule and the floor read the stretch as they read its loop; else the cells'."""
        return self.unit(self.loop)

    def record(self) -> dict[str, Any]:
        return {"kind": "breath", "breathe_sha256": self.sha256, "lag": self.lag, "lift_px": round(self.lift, 4),
                "heading_deg": self.heading, "inhale": self.inhale, **self.beat, "start_cell": self.start_cell,
                "loop_cells": self.loop_cells}


def read_breath(path: Path, lag: float | None, *, verb: str = VERB) -> Breath:
    """The breath of a Breathe layer's record (breathe.json): a smooth breath, whose phases are the ones its config
    gives after the sidecar's checks (`state_breathe`, `breathe.fit_breathe_pattern`), whose chest rises as the anatomy it
    was baked with says, on the curve of its in-breath share (`inhale`; a record written before the key was there says
    the default by leaving it out, as the sidecar does). A record of a stretch of a loop says the loop's length and the
    cell it starts at (`loop_cells`, `start_cell`; one written before the keys were there is the loop itself from its
    first cell). A record written by hand for a breath made elsewhere is read alike and must say the same — its in-breath
    share and its place in the loop among it."""
    lag = LAG_DEFAULT if lag is None else float(lag)
    if not 0 < lag < LAG_MAX:  # a NaN is not
        raise SystemExit(f"{verb}: --lag is the share of a breath the part is late by, above 0 and under 1/π ({LAG_MAX:.4f}): at "
                         f"1/π the late breath stops, past it it runs backwards; got {lag:g}")
    path = Path(path).expanduser()
    try:
        data = path.read_bytes()
        report = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{verb}: --breathe {path}: not read ({exc})") from exc
    if not (isinstance(report, dict) and isinstance(report.get("config"), dict) and isinstance(report.get("phases"), list)
            and isinstance((report.get("resolved") or {}).get("anatomy"), dict)):
        raise SystemExit(f"{verb}: --breathe {path}: not a Breathe layer's record — sprite-gen breathe writes config, phases "
                         "and resolved.anatomy into breathe.json")
    mode = report["config"].get("mode") or BREATHE_PIXEL
    if mode != BREATHE_SMOOTH:
        raise SystemExit(f"{verb}: --breathe {path}: a {mode} breath — the part follows a smooth breath, which rests at the "
                         "first cell (a pixel breath's travelling wave moves it)")
    try:
        cfg = state_breathe({"states": {"frames": {"breathe": report["config"]}}}, "frames")
    except SystemExit as exc:
        raise SystemExit(f"{verb}: --breathe {path}: its config is refused as a sidecar's would be — {exc}") from exc
    phases = report["phases"]
    if report.get("frames") != len(phases):
        raise SystemExit(f"{verb}: --breathe {path}: it says {report.get('frames')} frames and holds {len(phases)} phases")
    if breathe_mod.fit_breathe_pattern(len(phases), cfg) != phases:
        raise SystemExit(f"{verb}: --breathe {path}: its phases are not the ones its config gives for {len(phases)} cells "
                         "(breathe.fit_breathe_pattern) — the record was changed after it was written")
    try:
        anatomy = Anatomy.from_dict(report["resolved"]["anatomy"])
    except (KeyError, TypeError) as exc:
        raise SystemExit(f"{verb}: --breathe {path}: its resolved.anatomy is not one sprite-gen breathe writes ({exc})") from exc
    beat = {"breath_peaks": list(cfg["breath_peaks"])} if cfg.get("breath_peaks") else {"breaths": cfg["breaths"]}
    loop = breathe_mod.breathe_loop(len(phases), cfg)
    return Breath(path, hashlib.sha256(data).hexdigest(), [float(p) for p in phases], beat,
                  breathe_mod.smooth_lift(anatomy, cfg), float(cfg["chest_dir"]), float(cfg["inhale"]), lag,
                  cfg["start_cell"], cfg["loop_cells"], None if loop == phases else loop)


@dataclass(frozen=True, eq=False)
class Ask:
    """What a sway is asked, read and checked: the regions and how they move. What it is asked of — a loop's strip
    (`Request`) or a folder of a cycle's frames (`sway_frames`) — is apart from it."""

    regions: list[tuple[float, ...]]
    gain: float
    freq: float | None  # the damped mass's; None on the breath
    zeta: float | None
    on_fold: str
    read_on: str | None = None  # the sha256 of the strip as cut the regions were read on; None: this strip's, as ever
    stretch_floor: float | None = None  # the least area a moved pixel keeps; None: the fold rule alone
    gain_across: float | None = None  # the gain across asked; None: --gain, across as down
    breath: Breath | None = None  # the breath the part follows; None: the body's motion, answered by the damped mass
    band: Band | None = None  # each region moved as one mass over a band (`--band`); None: the weight falls from the centre
    hold: Hold | None = None  # pixels a band moves round and leaves as they are (`--hold-mask`); None: none
    mask: RegionMask | None = None  # the region as a mask, held round (`--region-mask`); None: the ellipse's weight

    @property
    def across(self) -> float:
        """The across move's gain over the down move's: `--gain-across` over `--gain`, 1 without it."""
        return 1.0 if self.gain_across is None or self.gain == 0 else self.gain_across / self.gain

    @property
    def policy(self) -> str:
        return POLICY if self.breath is None else BREATH_POLICY


def read_ask(regions: list[tuple[float, ...]], *, gain: float | None = None, freq: float | None = None,
             zeta: float | None = None, on_fold: str = "refuse", read_on: str | None = None,
             stretch_floor: float | None = None, breathe: Path | None = None, lag: float | None = None,
             gain_across: float | None = None, band: float | str | None = None, band_margin: float | str | None = None,
             band_up: tuple[float, float | str] | str | None = None, hold_mask: Path | None = None,
             region_mask: Path | None = None, region_rim: float | None = None, region_shrink: int | None = None,
             region_crease: bool = False, region_over: bool = False, region_shear: float | None = None,
             region_membrane: int | None = None, region_over_solid: bool = False, region_rim_round: bool = False,
             region_membrane_unpin: bool = False, region_clear_alpha: int | None = None,
             region_floor_beside: int | None = None, region_own_outline: int | None = None, verb: str = VERB) -> Ask:
    """A sway's ask, read and checked. A number left out (None) is its default: the damped mass's gain, freq and zeta,
    or, on the breath (`breathe`), the breath's gain and lag. `band` (px or AUTO; None: off) moves each region as one
    mass, `band_margin` and `band_up` shaping it, and `hold_mask` (a PNG or a folder of them; None: off) holds pixels
    the band moves round. `region_mask` (a PNG or a folder of them; None: off) gives the region as a mask instead,
    `region_rim`, `region_shrink`, `region_crease`, `region_over` (with `hold_mask` the pixels its layer leaves out,
    `region_over_solid` which pixels under it it lays over, `region_membrane` the figure under it a membrane moves and,
    with it, `region_membrane_unpin`, `region_clear_alpha`, `region_floor_beside` and `region_own_outline`, and
    `region_rim_round` the rim held round beside it), `region_shear` and `band_up` shaping it."""
    # The numbers go into the record, which is JSON, and into the cells' arithmetic: a numpy number from a
    # caller (an int64 has no JSON form) is read as the float the command line gives, so every caller of the
    # same numbers writes the same strip and record.
    try:
        regions = [region_of(region) for region in regions]
    except ValueError as exc:
        raise SystemExit(f"{verb}: --region {exc}") from exc
    if not regions:
        raise SystemExit(f"{verb}: at least one --region cx,cy,rx,ry")
    if breathe is None and lag is not None:
        raise SystemExit(f"{verb}: --lag is how late the part follows the breath; it needs --breathe")
    if breathe is not None and (freq is not None or zeta is not None):
        raise SystemExit(f"{verb}: --freq and --zeta are the damped mass's; on the breath (--breathe) the part is late by --lag")
    gain = (GAIN_DEFAULT if breathe is None else BREATH_GAIN_DEFAULT) if gain is None else float(gain)
    if breathe is None:
        freq = FREQ_DEFAULT if freq is None else float(freq)
        zeta = ZETA_DEFAULT if zeta is None else float(zeta)
    if gain < 0 or (breathe is None and (freq <= 0 or not 0 < zeta)):
        raise SystemExit(f"{verb}: --gain must be 0 or more, --freq and --zeta above 0")
    if gain_across is not None:
        gain_across = float(gain_across)
        if not gain_across >= 0:  # a NaN is not
            raise SystemExit(f"{verb}: --gain-across must be 0 or more")
        if gain == 0 and gain_across != 0:
            raise SystemExit(f"{verb}: --gain 0 leaves the cells as they were, and --gain-across is read against it")
    if on_fold not in ON_FOLD_MODES:
        raise SystemExit(f"{verb}: unknown --on-fold {on_fold!r}; expected one of {', '.join(ON_FOLD_MODES)}")
    if read_on is not None and not READ_ON.fullmatch(str(read_on)):
        raise SystemExit(f"{verb}: --read-on wants the sha256 of the strip the regions were read on (64 lowercase "
                         f"hexadecimal digits), got {read_on!r}")
    if stretch_floor is not None:
        stretch_floor = float(stretch_floor)
        if not 0 <= stretch_floor < 1:  # a NaN is neither
            raise SystemExit(f"{verb}: --stretch-floor is the least area a moved pixel keeps, at least 0 and under 1; got {stretch_floor:g}")
    breath = None if breathe is None else read_breath(breathe, lag, verb=verb)
    if region_mask is not None:
        masked = _read_region_mask(regions, region_mask, region_rim, region_shrink, region_crease, band, band_margin, band_up,
                                   stretch_floor, hold_mask, verb, over=region_over, shear=region_shear,
                                   membrane=region_membrane, over_solid=region_over_solid, rim_round=region_rim_round,
                                   unpin=region_membrane_unpin, clear_alpha=region_clear_alpha,
                                   floor_beside=region_floor_beside, own_outline=region_own_outline)
        return Ask(regions, gain, freq, zeta, on_fold, None if read_on is None else str(read_on), stretch_floor, gain_across,
                   breath, mask=masked)
    if region_rim is not None or region_shrink is not None or region_crease:
        raise SystemExit(f"{verb}: --region-rim, --region-shrink and --region-crease shape a region mask; they need --region-mask")
    if region_over or region_shear is not None or region_membrane is not None or region_over_solid or region_rim_round:
        raise SystemExit(f"{verb}: --region-over, --region-over-solid, --region-rim-round, --region-shear and --region-membrane "
                         "shape a region mask; they need --region-mask")
    if region_membrane_unpin or region_clear_alpha is not None or region_floor_beside is not None or region_own_outline is not None:
        raise SystemExit(f"{verb}: --region-membrane-unpin, --region-clear-alpha, --region-floor-beside and --region-own-outline "
                         "shape a region mask's membrane; they need --region-mask")
    shaped = _read_band(regions, band, band_margin, band_up, stretch_floor, verb)
    if hold_mask is not None and shaped is None:
        raise SystemExit(f"{verb}: --hold-mask keeps pixels as they are while a band moves its regions round them; it needs --band")
    return Ask(regions, gain, freq, zeta, on_fold, None if read_on is None else str(read_on), stretch_floor, gain_across,
               breath, shaped, None if hold_mask is None else read_hold(hold_mask, verb))


def _read_band(regions: list[tuple[float, ...]], band: float | str | None, margin: float | str | None,
               up: tuple[float, float | str] | str | None, stretch_floor: float | None, verb: str) -> Band | None:
    """The band asked (`--band`, `--band-margin`, `--band-up`), checked as the command line checks it; None when off."""
    if band is None:
        if margin is not None or up is not None:
            raise SystemExit(f"{verb}: --band-margin and --band-up shape a band; they need --band")
        return None
    flat = [region for region in regions if len(region) > 4]
    if flat:
        raise SystemExit(f"{verb}: --band moves a region whole over its ellipse grown by the margin, and a region's fifth number "
                         f"(flat, {flat[0][4]:g} on --region {','.join(f'{v:g}' for v in flat[0])}) is the other way of moving it "
                         "whole: give the region as cx,cy,rx,ry with --band")
    if stretch_floor is not None:
        raise SystemExit(f"{verb}: --stretch-floor reads the weight that falls from a region's centre; with --band the fold "
                         "rule reads the band")
    try:
        return Band(parse_band(str(band)), AUTO if margin is None else parse_band_margin(str(margin)),
                    None if up is None else parse_band_up(up if isinstance(up, str) else ",".join(str(v) for v in up)))
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(f"{verb}: {exc}") from exc


def _read_region_mask(regions: list[tuple[float, ...]], path: Path, rim: float | None, shrink: int | None, crease: bool,
                      band: float | str | None, margin: float | str | None, up: tuple[float, float | str] | str | None,
                      stretch_floor: float | None, hold_mask: Path | None, verb: str, *, over: bool = False,
                      shear: float | None = None, membrane: int | None = None, over_solid: bool = False,
                      rim_round: bool = False, unpin: bool = False, clear_alpha: int | None = None,
                      floor_beside: int | None = None, own_outline: int | None = None) -> RegionMask:
    """The region mask asked (`--region-mask`, `--region-rim`, `--region-shrink`, `--region-crease`, `--region-over` and,
    with it, `--hold-mask`, `--region-over-solid`, `--region-rim-round` and `--region-membrane`, `--region-shear`,
    `--band-up`; the membrane's `--region-membrane-unpin`, `--region-clear-alpha`, `--region-floor-beside` and
    `--region-own-outline`, which shape nothing without one), checked as the command line checks it, and its PNGs read."""

    def whole(given: object) -> int:  # a whole number as given; -1 where it is none (a NaN, an infinity, not a number)
        try:
            return int(given) if not isinstance(given, bool) and float(given) == int(given) else -1
        except (TypeError, ValueError, OverflowError):
            return -1

    if band is not None:
        raise SystemExit(f"{verb}: --region-mask moves the region's own pixels and holds every visible pixel round it, the weight "
                         "falling over a rim inside it; --band moves an ellipse over a band outside it: give one of them")
    if margin is not None:
        raise SystemExit(f"{verb}: --band-margin shapes a band; with --region-mask the rim is read off the move (--region-rim)")
    flat = [region for region in regions if len(region) > 4]
    if flat:
        raise SystemExit(f"{verb}: --region-mask gives the region as a mask, and a region's fifth number (flat, {flat[0][4]:g} on "
                         f"--region {','.join(f'{v:g}' for v in flat[0])}) shapes the ellipse's weight: give the region as "
                         "cx,cy,rx,ry with --region-mask")
    if stretch_floor is not None:
        raise SystemExit(f"{verb}: --stretch-floor reads the weight that falls from a region's centre; with --region-mask the rim "
                         "is widened with the move")
    if hold_mask is not None and not over:
        raise SystemExit(f"{verb}: --region-mask holds every visible pixel outside the region already; --hold-mask is a band's, "
                         "or with --region-over the pixels its layer leaves out")
    if shear is not None:
        try:
            shear = float(shear)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"{verb}: --region-shear is px a px, over 0; got {shear!r}") from exc
        if not (shear > 0 and math.isfinite(shear)):  # a NaN is not
            raise SystemExit(f"{verb}: --region-shear is the most a move one way may rise across the rim the other way, px a "
                             f"px, over 0; got {shear:g}")
    if over_solid and not over:
        raise SystemExit(f"{verb}: --region-over-solid says which pixels under a region laid over the figure are laid over; it "
                         "needs --region-over")
    if rim_round and not over:
        raise SystemExit(f"{verb}: --region-rim-round holds the rim round beside a region laid over the figure, where what lies "
                         "under its lower edge holds none of it; it needs --region-over")
    if membrane is not None:
        if not over:
            raise SystemExit(f"{verb}: --region-membrane moves the figure under a region laid over it; it needs --region-over")
        given, membrane = membrane, whole(membrane)
        if membrane < 0:
            raise SystemExit(f"{verb}: --region-membrane is how far under the region's lower edge the membrane reaches, a "
                             f"whole number of px past the first, 0 or more; got {given!r}")
    rim = RIM_DEFAULT if rim is None else float(rim)
    if not rim >= 1 or not math.isfinite(rim):  # a NaN is not
        raise SystemExit(f"{verb}: --region-rim is how many times the fold rule's least band the rim starts at, 1 or more; got {rim:g}")
    asked = SHRINK_DEFAULT if shrink is None else shrink
    shrink = whole(asked)
    if shrink < 0:
        raise SystemExit(f"{verb}: --region-shrink is the px the mask is worn in by, a whole number, 0 or more; got {asked!r}")
    if clear_alpha is not None:
        given, clear_alpha = clear_alpha, whole(clear_alpha)
        if not 1 <= clear_alpha < ALPHA_SOLID:
            raise SystemExit(f"{verb}: --region-clear-alpha is the alpha under which a pixel is read as clear, a whole number "
                             f"from 1 to {ALPHA_SOLID - 1} (under a solid pixel's); got {given!r}")
    for flag, given in (("--region-floor-beside", floor_beside), ("--region-own-outline", own_outline)):
        if given is not None and whole(given) < 0:
            raise SystemExit(f"{verb}: {flag} is px, a whole number, 0 or more; got {given!r}")
    floor_beside = None if floor_beside is None else whole(floor_beside)
    own_outline = None if own_outline is None else whole(own_outline)
    try:
        shaped = None if up is None else parse_band_up(up if isinstance(up, str) else ",".join(str(v) for v in up))
    except argparse.ArgumentTypeError as exc:
        raise SystemExit(f"{verb}: {exc}") from exc
    masks, digests, per_cell = _read_masks(path, "--region-mask", verb)
    return RegionMask(masks, digests, per_cell, rim, shrink, bool(crease), shaped, bool(over), shear,
                      None if hold_mask is None else read_hold(hold_mask, verb), membrane, bool(over_solid), bool(rim_round),
                      bool(unpin), clear_alpha, floor_beside or 0, own_outline or 0)


@dataclass(frozen=True, eq=False)
class Request:
    """A sway asked of a loop directory, read and checked; nothing is written by reading it."""

    loop_dir: Path
    name: str
    meta_path: Path
    meta_bytes: bytes
    meta: dict[str, Any]
    ask: Ask
    read_on_cut: dict[str, Any] | None = None  # the strip the regions were read on and its record (`--read-on-cut`)
    aligned_from: dict[str, Any] | None = None  # with it, on a loop aligned from its cut, the cut's record (`aligned_from`)

    @property
    def strip_path(self) -> Path:
        return self.loop_dir / f"{self.name}.strip.png"

    @property
    def source_path(self) -> Path:
        return self.loop_dir / SOURCE


def read_request(loop_dir: Path, regions: list[tuple[float, ...]], *, gain: float | None = None,
                 freq: float | None = None, zeta: float | None = None, on_fold: str = "refuse", verb: str = VERB,
                 read_on: str | None = None, stretch_floor: float | None = None, breathe: Path | None = None,
                 lag: float | None = None, gain_across: float | None = None, band: float | str | None = None,
                 band_margin: float | str | None = None, band_up: tuple[float, float | str] | str | None = None,
                 hold_mask: Path | None = None, region_mask: Path | None = None, region_rim: float | None = None,
                 region_shrink: int | None = None, region_crease: bool = False, region_over: bool = False,
                 region_shear: float | None = None, region_membrane: int | None = None,
                 region_over_solid: bool = False, region_rim_round: bool = False, region_membrane_unpin: bool = False,
                 region_clear_alpha: int | None = None, region_floor_beside: int | None = None,
                 region_own_outline: int | None = None, read_on_cut: Path | None = None) -> Request:
    loop_dir = loop_dir.expanduser().resolve()
    metas = sorted(loop_dir.glob("*.strip.json"))
    if len(metas) != 1:
        raise SystemExit(f"{verb}: {loop_dir}: expected one <name>.strip.json from video-loop, found {len(metas)}")
    meta_path = metas[0]
    name = meta_path.name[: -len(".strip.json")]
    meta_bytes = meta_path.read_bytes()
    meta = json.loads(meta_bytes.decode("utf-8"))
    if meta.get("kind") == CYCLE_ONE_SHOT or meta.get("loop") is False:
        raise SystemExit(f"{verb}: a one-shot plays once; the follow-through is a loop's steady state")
    ask = read_ask(regions, gain=gain, freq=freq, zeta=zeta, on_fold=on_fold, read_on=read_on, stretch_floor=stretch_floor,
                   breathe=breathe, lag=lag, gain_across=gain_across, band=band, band_margin=band_margin, band_up=band_up,
                   hold_mask=hold_mask, region_mask=region_mask, region_rim=region_rim, region_shrink=region_shrink,
                   region_crease=region_crease, region_over=region_over, region_shear=region_shear,
                   region_membrane=region_membrane, region_over_solid=region_over_solid, region_rim_round=region_rim_round,
                   region_membrane_unpin=region_membrane_unpin, region_clear_alpha=region_clear_alpha,
                   region_floor_beside=region_floor_beside, region_own_outline=region_own_outline, verb=verb)
    if read_on_cut is None:
        return Request(loop_dir, name, meta_path, meta_bytes, meta, ask)
    return Request(loop_dir, name, meta_path, meta_bytes, meta, ask, read_cut(read_on_cut, ask.read_on, verb),
                   aligned_from(loop_dir, meta_path, meta, verb))


def read_cut(path: Path, read_on: str | None, verb: str = VERB) -> dict[str, Any]:
    """The strip the regions were read on (`--read-on-cut`, with `--read-on`): its PNG, whose bytes are the sha256
    `--read-on` names, and its record beside it as `video-loop` writes one (`<name>.strip.json` by `<name>.strip.png`)."""
    if read_on is None:
        raise SystemExit(f"{verb}: --read-on-cut names the strip --read-on names by its sha256; it needs --read-on")
    path = Path(path).expanduser()
    record = path.with_suffix(".json")
    if not path.is_file() or not record.is_file():
        raise SystemExit(f"{verb}: --read-on-cut {path}: wants the strip's PNG and its record beside it ({record.name})")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != read_on:
        raise SystemExit(f"{verb}: --read-on-cut {path} is {digest}, not the strip --read-on names ({read_on})")
    try:
        meta = json.loads(record.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{verb}: --read-on-cut {record}: not a strip record ({exc})") from exc
    if not isinstance(meta, dict):
        raise SystemExit(f"{verb}: --read-on-cut {record}: not a strip record")
    with Image.open(io.BytesIO(data)) as im:
        strip = im.convert("RGBA")
    return {"path": str(path), "sha256": digest, "meta": meta, "strip": strip}


@dataclass(frozen=True, eq=False)
class Answer:
    """What an ask comes to on a cycle's cells: the body's motion, each region's gain, and every cell's move.

    `video-sway` writes it over the loop or into a new folder of frames; `video-sway-inspect` writes it down and
    changes nothing. All of them read the carry, the offsets and the moved cells here, and nowhere else."""

    ask: Ask
    cells: list[Image.Image]
    cols: np.ndarray  # per cell, how far its body lies from cell 0's (or the reference frame's): across
    rows: np.ndarray  # and down
    height0: int
    worn: list[int]
    requested: float
    reach_requested: float
    reach_per_gain: float
    radii: list[float]
    gains: list[float]  # per region; 0 where it is held
    held: list[bool]
    ratios: list[float]
    moves: dict[float, tuple[np.ndarray, np.ndarray]]  # per gain a region moves by, the offset per cell: across, down
    reaches: dict[float, float]
    gain: float  # the largest any region moves by; dx, dy and reach are that gain's
    dx: np.ndarray
    dy: np.ndarray
    reach: float
    groups: list[tuple[list[tuple[float, ...]], tuple[np.ndarray, np.ndarray]]]
    read_on: dict[str, Any] | None = None  # how the strip the regions were read on stands to this one (`--read-on`)
    stretch: dict[str, Any] | None = None  # the floor asked, and what it did to each region (`--stretch-floor`)
    reference: dict[str, Any] | None = None  # the frame the regions were read on, when not cell 0 (`--reference`)
    band: Banded | None = None  # what the band comes to (`--band`); None: the weight falls from the centre
    hold: Held | None = None  # what the hold mask comes to (`--hold-mask`); None: none
    mask: Masked | None = None  # what the region mask comes to (`--region-mask`); None: the ellipse's weight

    def carry(self, k: int) -> tuple[float, float]:
        """How far cell k's body lies from cell 0's, across and down: every region's ellipse is carried by it. Carried
        from the cut, the cells' motion is already from the cut's cell 0, where the regions were read; read from a
        reference frame, it is already from that frame; read on another cut of the clip, it is from the cell paired with
        that cut's cell 0 (`recut_pair`), where the regions were moved."""
        if self.read_on is not None and self.read_on["relation"] == "recut":
            base = self.read_on["base_cell"]
            return self.cols[k] - self.cols[base], self.rows[k] - self.rows[base]
        if self.reference is not None or (self.read_on is not None and self.read_on["relation"] == "transported"):
            return self.cols[k], self.rows[k]
        return self.cols[k] - self.cols[0], self.rows[k] - self.rows[0]

    def moves_in(self, k: int) -> list[tuple[list[tuple[float, ...]], tuple[float, float]]]:
        """Cell k's groups of regions, each with its offset in that cell."""
        return [(group, (mx[k], my[k])) for group, (mx, my) in self.groups]

    def moved(self, k: int) -> Image.Image:
        if self.mask is not None:
            if all(dx == 0 and dy == 0 for _, (dx, dy) in self.moves_in(k)):
                return self.cells[k].copy()
            return (self.mask.drawn[k] if k in self.mask.drawn else self.masked_cell(k)[0]).copy()
        if self.band is None:
            return move_regions(self.cells[k], self.moves_in(k), self.carry(k))
        if all(dx == 0 and dy == 0 for _, (dx, dy) in self.moves_in(k)):
            return self.cells[k].copy()
        if self.hold is not None:
            return (self.hold.drawn[k] if k in self.hold.drawn else self.held_cell(k)[0]).copy()
        return _draw(self.cells[k], lambda xx, yy: self.band_field(k, xx, yy)[0])

    def held_cell(self, k: int) -> tuple[Image.Image, dict[str, np.ndarray]]:
        """Under a hold mask, cell k drawn round it (`draw_held`), what it keeps there — the mask's pixels (`mask`), the
        held pixels and the air (`hold_sets`) — and how each pixel was read. Kept in `Held.drawn`."""
        assert self.hold is not None
        cell = self.cells[k]
        w, h = cell.size
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        mask = self.hold.mask_in(k)
        held, air = hold_sets(mask, cell, self.hold.edge)
        field = self.band_field(k, xx, yy)[0]
        if field is None or all(dx == 0 and dy == 0 for _, (dx, dy) in self.moves_in(k)):
            still = np.zeros((h, w))
            drawn, read = cell.copy(), {"share": np.ones((h, w)), "readers": still > 0, "renormalised": still > 0,
                                        "walked": still > 0, "read_x": still, "read_y": still}
        else:
            drawn, read = draw_held(cell, field, held, air)
        self.hold.drawn.setdefault(k, drawn)
        return drawn, {"mask": mask, "held": held, "air": air, **read}

    def band_field(self, k: int, xx: np.ndarray, yy: np.ndarray) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray] | None, list[np.ndarray]]:
        """Under a band, cell k's move per pixel (`move_field`'s: covered, across, down; None where no region covers a
        pixel) and each region's weight, every region carried by its own carry in the cell (`Banded.carried`)."""
        assert self.band is not None
        weights = [band_weight(xx, yy, (region[0] + carried["px"][0], region[1] + carried["px"][1]), *shape)
                   for region, shape, carried in zip(self.ask.regions, self.band.shapes, self.band.carried[k])]
        # The groups as `solve_cells` makes them: one per gain a region moves by, in the order of `moves`.
        groups = [[i for i, (rg, hold) in enumerate(zip(self.gains, self.held)) if rg == g and not hold] for g in self.moves]
        group_weights = [np.max([weights[i] for i in members], axis=0) if members else np.zeros(np.shape(xx)) for members in groups]
        offsets = [(mx[k], my[k]) for mx, my in self.moves.values()]
        return _largest_move(group_weights, offsets), weights

    def mask_field(self, k: int, xx: np.ndarray, yy: np.ndarray) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray] | None, dict[str, np.ndarray]]:
        """Under a region mask, cell k's move per pixel (`move_field`'s: covered, across, down; None where nothing moves)
        and what it moves and holds there (`region_sets`: the region, its soft edge, the air it may move into, the held
        pixels). Over the region, its soft edge and the air the weight is the rim's, sin² of the distance to the nearest
        held pixel (`rim_distance`, over `Masked.rim`; with `--region-rim-round` round, the wider of its two widths each
        way, from the held pixels beside the region), and with `--band-up` the lesser of that and each region's upper
        yield, the ellipse carried by its own pixels as a band carries it (`Masked.up`); 0 elsewhere. Laid over the figure
        under it (`--region-over`, `layer_sets`), what lies under its lower edge is no anchor of the rim and the band of it
        moves too, the pixels `--hold-mask` names left out of its soft edge and of the band (the region keeps its own); with
        `--region-crease` the region reaches down to the crease carried into the cell (`crease_reach`). With a membrane
        (`--region-membrane`) the region grows over the outline its mask leaves out (`--region-own-outline`, `own_outline`),
        the lower edge beside it is set by the lowest pixel near each side (`--region-floor-beside`), what the membrane
        moves is no anchor of the rim (`--region-membrane-unpin`), and a pixel under `--region-clear-alpha` is read as
        clear (`RegionMask.alpha_of`)."""
        assert self.mask is not None
        masked = self.mask
        cell = self.cells[k]
        solid = np.asarray(cell.getchannel("A")) >= ALPHA_SOLID
        alpha = masked.asked.alpha_of(cell)
        region = masked.region_in(k, solid)
        if masked.asked.over:
            left_out = masked.left_out_in(k)
            if masked.creases and masked.creases[0] and masked.carried is not None:
                px = masked.carried[k]["px"]
                region = crease_reach(region, solid, masked.creases[0]["rows"],
                                      (px[0] - masked.base_shift[0], px[1] - masked.base_shift[1]))
            membrane = masked.asked.membrane is not None
            if membrane and masked.asked.own_outline:
                region = own_outline(region, solid, alpha == 0, left_out, masked.asked.own_outline)
            sets = layer_sets(region, alpha, masked.air, left_out, masked.asked.over_solid,
                              masked.asked.floor_beside if membrane else 0)
            if membrane and masked.asked.unpin:
                sets["anchors"] = sets["anchors"] & ~membrane_sets(sets, masked.asked.membrane)
            anchors, moving = sets["anchors"], sets["region"] | sets["soft"] | sets["air"] | sets["band"]
            sets["left_out"] = region & left_out
        else:
            soft, held, air = region_sets(region, alpha, masked.air)
            sets = {"region": region, "soft": soft, "air": air, "held": held}
            anchors, moving = held, region | soft | air
        across, down = masked.rim[:2]
        if masked.asked.rim_round:
            # Beside the region (in the columns it does not reach, above its lower edge) the rim is round: a corner where
            # those pixels stop over what lies under its lower edge, which holds none of it, rises down as gently as across.
            r = max(across, down)
            beside = anchors & ~region.any(axis=0)[None, :] & (np.arange(region.shape[0])[:, None] < sets["floor"][None, :])
            distance = np.minimum(rim_distance(anchors & ~beside, across, down), rim_distance(beside, r, r))
        else:
            distance = rim_distance(anchors, across, down)
        rim = np.where(moving, np.sin(0.5 * math.pi * distance) ** 2, 0.0)
        # The groups as `solve_cells` makes them: one per gain a region moves by, in the order of `moves`.
        groups = [[i for i, (rg, hold) in enumerate(zip(self.gains, self.held)) if rg == g and not hold] for g in self.moves]
        if masked.up is None:
            group_weights = [rim if members else np.zeros(rim.shape) for members in groups]
        else:
            yields = [band_weight(xx, yy, (region_[0] + carried["px"][0], region_[1] + carried["px"][1]), *shape)
                      for region_, shape, carried in zip(self.ask.regions, masked.up.shapes, masked.up.carried[k])]
            group_weights = [np.minimum(rim, np.max([yields[i] for i in members], axis=0)) if members else np.zeros(rim.shape)
                             for members in groups]
        offsets = [(mx[k], my[k]) for mx, my in self.moves.values()]
        return _largest_move(group_weights, offsets), sets

    def masked_cell(self, k: int) -> tuple[Image.Image, dict[str, Any]]:
        """Under a region mask, cell k drawn by its move (`mask_field`) as a hold draws it (`draw_held`), every read its
        whole move and no held pixel read; what it moved and held, the move, and how each pixel was read. Laid over the
        figure under it (`--region-over`), the band is drawn as the layer lands on it (`lay_over`), not read by
        `draw_held`, which draws the air and where the region lifts by the move: the held pixels are then those it keeps,
        the move 0 on them; with a membrane (`--region-membrane`), what lies under its lower edge within its reach moves by
        it (`membrane_sets`, `membrane_move`) and is drawn by `draw_held`, never laid over. Kept in `Masked.drawn`."""
        assert self.mask is not None
        cell = self.cells[k]
        w, h = cell.size
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        field, sets = self.mask_field(k, xx, yy)
        if field is None or all(dx == 0 and dy == 0 for _, (dx, dy) in self.moves_in(k)):
            still = np.zeros((h, w))
            field, drawn = None, cell.copy()
            read = {"share": np.ones((h, w)), "readers": still > 0, "renormalised": still > 0, "walked": still > 0,
                    "read_x": still, "read_y": still}
            if self.mask.asked.over:
                sets = {**sets, "over": still > 0, "laid": still > 0, "lifted": still > 0} \
                    | ({} if self.mask.asked.membrane is None else {"membrane": still > 0})
        elif self.mask.asked.over:
            band, free = sets["band"], np.zeros((h, w), bool)
            reach = self.mask.asked.membrane
            if reach is not None:
                # The membrane under the lower edge: its move hangs between the region's own (its soft edge and its outline
                # over the band too) and none on the rest of the figure, and it is drawn by that move, never laid over.
                free = membrane_sets(sets, reach)
                field = (field[0] | free, *membrane_move(free, self.mask.asked.alpha_of(cell) > 0, sets["region"] | sets["soft"],
                                                         sets["edge"], (field[1], field[2]), reach))
                band = band & ~free
            # The air and where the region lifts are drawn by the move (`draw_held`, the band under it read there), the band
            # by the layer (`lay_over`, every pixel of it): no read of `draw_held`'s is taken on the band.
            drawn, read = draw_held(cell, (field[0] & ~band, field[1], field[2]), sets["held"] & ~sets["band"] & ~free,
                                    np.zeros((h, w), bool), short=False)
            drawn, laid, over, lifted = lay_over(cell, drawn, field, {**sets, "band": band})
            kept = band & ~laid
            field = (field[0], np.where(kept, 0.0, field[1]), np.where(kept, 0.0, field[2]))
            # On the band the read is the layer's: its whole move, none where the band keeps its bytes.
            read = {**read, "read_x": np.where(band, field[1], read["read_x"]), "read_y": np.where(band, field[2], read["read_y"])}
            # What it keeps: the held pixels less the band the layer lands on, the outline that goes with it and what the
            # membrane moves; what no read takes: the held pixels but the band, which the region reads where it lifts.
            sets = {**sets, "held": sets["held"] & ~laid & ~free, "unread": sets["held"] & ~sets["band"] & ~free, "over": over,
                    "laid": laid, "lifted": lifted} | ({} if self.mask.asked.membrane is None else {"membrane": free})
        else:
            drawn, read = draw_held(cell, field, sets["held"], np.zeros((h, w), bool), short=False)
        self.mask.drawn.setdefault(k, drawn)
        return drawn, {**sets, "field": field, **read}

    def record(self, source: str = SOURCE) -> dict[str, Any]:
        """The sway as the strip's meta records it, `source` the strip as cut kept beside it (`video-sway` adds its
        animations' checks); a frames folder's records it alike, the folder its source."""
        ask, requested = self.ask, self.requested
        drive = ({"freq_hz": ask.freq, "zeta": ask.zeta, "harmonics": HARMONICS} if ask.breath is None
                 else {"drive": ask.breath.record()})
        across = ({} if ask.gain_across is None else
                  {"gain_across": round(self.gain * ask.across, 4), "gain_across_requested": ask.gain_across})
        # Read from a reference frame, the body's bob spans that frame too (at 0): it is where the regions were read.
        cols, rows = (self.cols, self.rows) if self.reference is None else (np.append(self.cols, 0.0), np.append(self.rows, 0.0))
        return {
            "regions": [list(r) for r in ask.regions], "gain": self.gain, **across, **drive,
            "source": source, "body_px": self.height0, "body_worn_px": self.worn,
            "body_bob_px": [round(float(np.ptp(cols)), 2), round(float(np.ptp(rows)), 2)],
            "dx_px": np.round(self.dx, 2).tolist(), "dy_px": np.round(self.dy, 2).tolist(),
            "reach_px": round(self.reach, 2),
            "gain_requested": requested, "on_fold": ask.on_fold,
            "fold": {
                "lowered": any(g != requested for g in self.gains), "ratio": round(max(self.ratios), 3),
                "reach_requested_px": round(self.reach_requested, 2), "reach_per_gain_px": round(self.reach_per_gain, 3),
                # Where the regions took gains of their own, each says the gain it moved by and whether it
                # was held still (gain 0); where they all took one, the strip's `gain` is every region's.
                # A region mask's rim is read off the move (`rim_bands`): the fold rule reads no radius, and no gain folds it.
                "regions": [{"radius_px": radius if math.isfinite(radius) else None, "ratio": round(ratio, 3),
                             "gain_limit": None if not math.isfinite(radius) or (limit := gain_limit(self.reach_per_gain, radius)) is None
                             else round(limit, 3)}
                            | ({} if len(set(self.gains)) == 1 else {"gain": g, "held": hold})
                            for radius, ratio, g, hold in zip(self.radii, self.ratios, self.gains, self.held)],
            },
        } | ({} if self.read_on is None else {"read_on": self.read_on}) | ({} if self.stretch is None else {"stretch": self.stretch}) \
          | ({} if self.read_on is None and self.stretch is None and self.band is None and self.mask is None else
             {"carry_basis": dict(CARRY_BASIS if self.band is None and self.mask is None else CARRY_BASIS_BAND)}) \
          | ({} if self.reference is None else {"reference": self.reference}) \
          | ({} if self.band is None else {"band": self.band_record()}) \
          | ({} if self.hold is None else {"hold": self.hold_record()}) \
          | ({} if self.mask is None else {"region_mask": self.mask_record()})

    def band_record(self) -> dict[str, Any]:
        """The band as the record holds it (`--band`): what was asked, the reach it was read off, its px and the margin's,
        each region's flat radii and bands (across, above, below), where each region is carried in every cell and from
        what, the cells carried by the lay and why, and the ledger."""
        assert self.band is not None
        band = self.band
        return {"policy": BAND_POLICY, "asked": band.asked.asked(), **_shaped_record(band, self.gains), "ledger": band.ledger}

    def hold_record(self) -> dict[str, Any]:
        """The hold mask as the record holds it (`--hold-mask`): what was asked, how its pieces are grown, kept round and
        read round, each piece's size and box on the cell it was drawn on, where each is carried in every cell and from
        what (None where a mask is given for each cell), the cells carried by the lay and why, and the ledger."""
        assert self.hold is not None
        hold, per_cell = self.hold, self.hold.asked.per_cell
        return {
            "policy": HOLD_POLICY, "asked": hold.asked.asked(), "grow_px": None if per_cell else HOLD_GROW, "edge_px": hold.edge,
            "margin_px": HOLD_MARGIN, "slope": HOLD_SLOPE, "round_px": HOLD_ROUND, "steps": HOLD_STEPS,
            "pieces": [{"px": int(piece.sum()), "box": _box(piece)} for piece in hold.pieces],
            "carry": None if per_cell else {"search_px": CARRY_SEARCH,
                                            "cells": [[_carried_record(entry) for entry in cell] for cell in hold.carried]},
            "lay": [{"cell": k, "piece": i, "why": entry["why"]} for k, cell in enumerate(hold.carried)
                    for i, entry in enumerate(cell) if entry["from"] == "lay"],
            "ledger": hold.ledger,
        }

    def mask_record(self) -> dict[str, Any]:
        """The region mask as the record holds it (`--region-mask`): what was asked; how far round the region its soft edge
        and the air reach, and the rim (`rim_bands`); the mask on the cell it was drawn on, worn in, with the solid pixels
        it was carried by, and the crease where asked (`crease_of`); where it is carried in every cell and from what, and
        the cells that kept the carry of the cell before and why (None and none where a mask is given for each cell); the
        upper yield's bands and carry (`--band-up`; None without it); and the ledger (`region_ledger`). The rim `--region-
        shear` asks at least (`rim.shear_px`) and the layer (`over`: how deep its band reaches, the membrane under it
        (`--region-membrane`: how far it reaches and how its passes stop), and the pixels `--hold-mask` leaves out, carried
        as a hold mask is) only where they are asked."""
        assert self.mask is not None
        masked = self.mask
        across, down, slope, least = masked.rim
        one = None if masked.asked.per_cell else masked.worn[0]
        left_out = masked.left_out
        return {
            "policy": REGION_POLICY, "asked": masked.asked.asked(), "soft_px": REGION_SOFT, "air_px": masked.air,
            "rim": {"across_px": across, "down_px": down, "least_px": list(least), "slope": slope, "slope_bound": RIM_SLOPE}
            | ({} if masked.sheared is None else {"shear_px": list(masked.sheared)})
            | ({} if not masked.asked.rim_round else {"round": {"policy": RIM_ROUND_POLICY, "px": max(across, down)}}),
            "mask": None if one is None else {"px": int(one.sum()), "box": _box(one), "carried_by_px": masked.matched},
            "crease": masked.creases,
            "carry": None if masked.carried is None else {"search_px": CARRY_SEARCH,
                                                         "cells": [_carried_record(entry) for entry in masked.carried]},
            "kept": [{"cell": k, "why": entry["why"]} for k, entry in enumerate(masked.carried or []) if entry["from"] == "before"],
            "up": None if masked.up is None else _shaped_record(masked.up, self.gains),
            "ledger": masked.ledger,
        } | ({} if not masked.asked.over else {"over": {"policy": OVER_POLICY, "band_px": masked.air} | (
            {} if not masked.asked.over_solid else {"solid": OVER_SOLID_POLICY}) | (
            {} if masked.asked.membrane is None else
            {"membrane": {"policy": MEMBRANE_POLICY, "reach_px": masked.asked.membrane + 1, "step_px": MEMBRANE_STEP,
                          "passes_most": MEMBRANE_PASSES}}) | {
            "left_out": None if left_out is None else {
                "grow_px": None if left_out.asked.per_cell else HOLD_GROW,
                "pieces": [{"px": int(piece.sum()), "box": _box(piece)} for piece in left_out.pieces],
                "carry": None if left_out.asked.per_cell else {
                    "search_px": CARRY_SEARCH, "cells": [[_carried_record(entry) for entry in cell] for cell in left_out.carried]},
                "lay": [{"cell": k, "piece": i, "why": entry["why"]} for k, cell in enumerate(left_out.carried)
                        for i, entry in enumerate(cell) if entry["from"] == "lay"]}}})


def _shaped_record(band: Banded, gains: list[float]) -> dict[str, Any]:
    """A band's shape as the record holds it (`--band`, and `--band-up` under a region mask): the reach it was read off,
    its px and the margin's, each region's flat radii and bands (across, above, below) and the steepest its weight falls,
    where each region is carried in every cell and from what, and the cells carried by the lay and why."""
    return {
        "reach_px": round(band.reach, 4), "band_px": band.band, "margin_px": band.margin,
        "regions": [{"flat_px": [round(v, 4) for v in flat], "band_px": [round(v, 4) for v in bands],
                     "steepest": {"per_gain": round(per_gain, 4), "cell": k, "ratio": round(g * per_gain, 3)}}
                    for (flat, bands), (per_gain, k), g in zip(band.shapes, band.steepest, gains)],
        "carry": {"search_px": CARRY_SEARCH, "cells": [[_carried_record(entry) for entry in cell] for cell in band.carried]},
        "lay": [{"cell": k, "region": i, "why": entry["why"]} for k, cell in enumerate(band.carried)
                for i, entry in enumerate(cell) if entry["from"] == "lay"],
    }


def _carried_record(entry: dict[str, Any]) -> dict[str, Any]:
    """A carry (`carry_mask`'s or `carry_region`'s entry) as the record holds it."""
    out = {"px": [round(v, 4) + 0.0 for v in entry["px"]], "from": entry["from"]}
    if "guess" in entry:
        out["guess"] = entry["guess"]
    if "why" in entry:
        out["why"] = entry["why"]
    if "least" in entry:
        out["least"] = round(entry["least"], 4)
        out["curvature"] = None if entry["curvature"] is None else [round(v, 4) for v in entry["curvature"]]
    return out


def solve(request: Request, strip: Image.Image, *, strip_name: str, verb: str = VERB,
          strip_sha256: str | None = None) -> Answer:
    """The answer to `request` on `strip`, the loop's strip as cut (an RGBA picture; `strip_name` names its file
    in a refusal, `strip_sha256` is its file's, read where the request names the strip its regions were read on): its
    cells answered as any cycle's cells are (`solve_cells`), at the frame time its meta records. Reads nothing else and
    writes nothing."""
    meta, ask = request.meta, request.ask
    n, w, h = int(meta["frames"]), int(meta["w"]), int(meta["h"])
    if strip.size != (w * n, h):
        raise SystemExit(f"{verb}: {strip_name} is {strip.size[0]}x{strip.size[1]}, the strip meta says {w * n}x{h}; "
                         "cut the loop again (video-loop)")
    cells = _cells(strip, n, w, h)
    read_on = None
    if ask.read_on is not None:
        if strip_sha256 is None:
            raise ValueError("solve: a request with read_on needs the strip's sha256")
        read_on = {"sha256": ask.read_on, "relation": read_on_relation(meta, strip_sha256, ask.read_on, verb=verb,
                                                                       cut=request.read_on_cut is not None)}
    aligned = (meta.get("cycle_align") or {}).get("origin") is not None
    if read_on is not None and read_on["relation"] == "recut":
        # Read on another cut of the clip: paired by the source frame its cell 0 is, the regions moved onto this loop's
        # cut's cell that is that frame, and every cell carried from there (`Answer.carry`).
        pair = recut_pair(meta, request.read_on_cut["meta"], ask.read_on, strip_sha256=strip_sha256,
                          kept=request.aligned_from, verb=verb)
        cut_cell = _cut_cell(request.read_on_cut, pair, ask.read_on, verb)
        read_on.update(pair)
        if not aligned:  # carried from that cell by its own pixels; on an aligned loop, as the alignment carries the cut's
            recut_same(pair, cut_cell, cells[pair["cut_cell"]], ask.read_on, verb=verb)
            read_on.update(policy=RECUT_POLICY, base_cell=pair["cut_cell"])
        ask = recut_ask(ask, pair, (w, h), verb=verb)

    def transported() -> tuple[np.ndarray, np.ndarray, int, list[int]]:
        # The cut's own reading, cell for cell: the regions are where they were read, on the cut's cell 0 (or, read on
        # another cut, on the cut's cell paired with it), and the damped mass answers that motion in this loop's order,
        # length and frame time.
        paired = read_on if read_on["relation"] == "recut" else None
        rows, cols, height0, worn, cut_cells = transported_motion(meta, cells, ask.read_on, verb=verb, paired=paired)
        if paired is not None:
            if paired["cut_cell"] not in cut_cells:
                raise SystemExit(f"{verb}: --read-on {ask.read_on} is a cut of this loop's clip, paired at source frame "
                                 f"{paired['source_frame']} with the cut this loop was aligned from, but uncertain: no cell "
                                 f"of this loop is that cut's cell {paired['cut_cell']}; the regions are not moved")
            read_on["base_cell"] = cut_cells.index(paired["cut_cell"])
            # that cell is the cut's cell as drawn (`transported_motion`), so the cut's frame as filmed
            recut_same(paired, cut_cell, cells[read_on["base_cell"]], ask.read_on, verb=verb)
        read_on.update(policy=TRANSPORT_POLICY, cut_cells=cut_cells)
        return rows, cols, height0, worn

    carried = read_on is not None and (read_on["relation"] == "transported" or read_on["relation"] == "recut" and aligned)
    return solve_cells(ask, cells, float(meta["delay_ms"]), verb=verb, read_on=read_on,
                       read_body=transported if carried else None)


def _unit_move(ask: Ask, rows: np.ndarray, cols: np.ndarray, frame_ms: float | None) -> tuple[np.ndarray, np.ndarray]:
    """Per cell, the part's offset at gain 1, across and down, the across times its share of the gain (`Ask.across`):
    on the breath its offset from the breath (`Breath.unit`), or else the damped mass's answer to the body's motion at
    the cells' frame time."""
    if ask.breath is not None:
        across, down = ask.breath.unit()
    else:
        fps = 1000.0 / frame_ms
        across = sway_offsets(cols, fps, freq=ask.freq, zeta=ask.zeta, gain=1.0)
        down = sway_offsets(rows, fps, freq=ask.freq, zeta=ask.zeta, gain=1.0)
    return ask.across * across, down


def _whole_move(ask: Ask, move: tuple[np.ndarray, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """The move at gain 1 the fold rule and the floor read: on the breath of a stretch of a loop (the record's
    start_cell and loop_cells — a loop set's way in or out), the loop's whole breath (`Breath.whole`), so the stretch
    moves by the gain its loop moves by and their seams do not jump; else the cells' own `move`."""
    if ask.breath is None or ask.breath.loop is None:
        return move
    across, down = ask.breath.whole()
    return ask.across * across, down


def solve_cells(ask: Ask, cells: list[Image.Image], frame_ms: float | None, *, verb: str = VERB,
                read_on: dict[str, Any] | None = None, read_body: Any = None,
                reference: tuple[Image.Image, dict[str, Any]] | None = None) -> Answer:
    """The answer to `ask` on a cycle's cells (RGBA pictures of one size, in order; `frame_ms` one frame's time, which
    the damped mass alone reads): a loop's strip cut into its cells (`solve`), or a folder of the cycle's frames
    (`sway_frames`) — one answer, whatever the cells came from. The body's motion is read on the cells (`body_motion`)
    unless `read_body` gives it (a reading carried from the cut, `--read-on`); with a `reference` (the frame the regions
    were read on and its record, `--reference`) it is read from that frame, laid first. Reads nothing else and writes
    nothing."""
    regions, gain, on_fold = ask.regions, ask.gain, ask.on_fold
    w, h = cells[0].size
    for cx, cy, *_ in regions:
        if not (0 <= cx < w and 0 <= cy < h):
            raise SystemExit(f"{verb}: --region centre {cx:g},{cy:g} is outside the {w}x{h} cell")
    if ask.breath is not None and len(ask.breath.phases) != len(cells):
        raise SystemExit(f"{verb}: --breathe {ask.breath.path} breathes {len(ask.breath.phases)} cells and there are "
                         f"{len(cells)} to sway: the part follows the breath cell for cell")
    if read_body is not None:
        rows, cols, height0, worn = read_body()
    else:
        try:
            rows, cols, height0, worn = body_motion(cells if reference is None else [reference[0], *cells])
        except ValueError as exc:
            raise SystemExit(f"{verb}: {exc}") from exc
        if reference is not None:  # each cell from the reference, which is not swayed
            rows, cols = rows[1:], cols[1:]
    across, down = _unit_move(ask, rows, cols, frame_ms)
    whole_across, whole_down = _whole_move(ask, (across, down))

    def move(g: float) -> tuple[np.ndarray, np.ndarray]:
        """The offset per cell at gain g, across and down: g times the move at gain 1."""
        return g * across, g * down

    def reach_at(g: float) -> float:
        """The largest offset at gain g over the whole move the fold rule reads (`_whole_move`)."""
        return float(np.max(np.hypot(g * whole_across, g * whole_down)))

    reach = reach_at(gain)
    shaped = None
    up = None if ask.mask is None else ask.mask.up  # a region mask's upper yield: shaped as a band's, its band and margin auto
    if ask.band is not None or up is not None:
        # The reach first, at the gain asked: the band and the margin are read off it, and the fold rule reads each
        # region by its narrowest band.
        shaped = band_shapes(ask.band if ask.band is not None else Band(AUTO, AUTO, up), regions, reach)
        radii, sized = [min(bands) for _, bands in shaped[2]], "a region's band of"
        larger = "give a wider --band" if ask.band is not None else "give --band-up a wider band"
    elif ask.mask is not None:
        # A region mask's rim is read off the move at the gain it moves by (`rim_bands`): the fold rule reads no radius.
        radii, sized, larger = [math.inf] * len(regions), "", ""
    else:
        radii, sized, larger = [region_radius(region) for region in regions], "a region of radius", "give the region larger radii"
    smallest = min(radii)
    requested, reach_requested = gain, reach
    # The offset is the gain times the answer at gain 1, so one move decides every gain.
    reach_per_gain = float(np.max(np.hypot(whole_across, whole_down)))
    gains, held = [requested] * len(regions), [False] * len(regions)
    if fold_ratio(reach, smallest) >= 1:
        if on_fold == "refuse":
            raise SystemExit(f"{verb}: a move of {reach:.1f} px folds {sized} {smallest:g} px over itself; "
                             f"lower --gain or {larger}")
        # A gain for each region: every part hangs on the same body and answers the same motion, but
        # how far it moves before it folds is its own size, so a small part (an ear) does not hold a
        # large one (a chest) back. A region that does not fold keeps the gain asked for.
        folds = [fold_ratio(reach, radius) >= 1 for radius in radii]
        gains = [lowered_gain(requested, reach_per_gain, radius) if fold else requested for radius, fold in zip(radii, folds)]
        # One that would have to go under the mass as measured does not move at all; the strip is
        # refused only when that is every region.
        held = [fold and g < GAIN_MEASURED for fold, g in zip(folds, gains)]
        if all(held):
            k = radii.index(max(radii))  # the largest folds at the largest gain: if it is held, every region is
            every = f" (the largest of {len(regions)} regions: every one folds)" if len(regions) > 1 else ""
            raise SystemExit(f"{verb}: a move of {reach:.1f} px folds {sized} {radii[k]:g} px over itself{every}, and "
                             f"--on-fold lower would have to go under --gain {GAIN_MEASURED:g} (the mass as measured moves "
                             f"{reach_per_gain:.1f} px; the largest gain that does not fold is {gains[k]:g}); {larger}")
        gains = [0.0 if hold else g for g, hold in zip(gains, held)]
    stretch = None
    if ask.stretch_floor is not None:
        gains, held, stretch = _keep_floor(ask, radii, gains, held, (whole_across, whole_down), verb)

    def answer_at(gains: list[float], held: list[bool]) -> Answer:
        # One move per gain: a region's offset is its gain times the answer at gain 1. The strip's `gain`
        # is the largest any region moves by, and `dx_px`/`dy_px` and `reach_px` are that gain's (the reach,
        # like the fold, over the whole move the fold rule reads).
        moves = {g: move(g) for g, hold in zip(gains, held) if not hold}
        reaches = {g: reach_at(g) for g in moves}
        gain = max(moves)
        dx, dy = moves[gain]
        ratios = [0.0 if hold else fold_ratio(reaches[g], radius) for radius, g, hold in zip(radii, gains, held)]
        groups = [([r for r, rg, hold in zip(regions, gains, held) if rg == g and not hold], offset) for g, offset in moves.items()]
        return Answer(ask, cells, cols, rows, height0, worn, requested, reach_requested, reach_per_gain, radii, gains, held,
                      ratios, moves, reaches, gain, dx, dy, reaches[gain], groups, read_on, stretch,
                      None if reference is None else reference[1])

    answer = answer_at(gains, held)
    if shaped is None:
        if ask.mask is None:
            return answer
        return _masked(answer, None, (whole_across, whole_down), None if reference is None else reference[0], verb)
    # Under a band the regions are carried by their own pixels, whatever the gain; and where the flat ellipse and the
    # outer one differ in shape (above the centre under --band-up) the band is narrower across its ring than its px,
    # so the weight falls steeper than the fold rule reads. A pixel moved by the gain times u (the move at gain 1)
    # keeps 1 − gain · u·∇w of its area: each region's steepest u·∇w over the cells may lower its gain further.
    carried = _carry_band(answer, None if reference is None else reference[0])
    steepest = band_steepest(regions, shaped[2], carried, move(1.0), (w, h))
    over = [not hold and g * per_gain >= 1 for g, hold, (per_gain, _) in zip(gains, held, steepest)]
    if any(over):
        i = over.index(True)
        per_gain, k = steepest[i]
        flag, wider = (("--band", "a wider --band" + (" or --band-up" if ask.band.up else "")) if ask.band is not None
                       else ("--band-up", "--band-up a wider band"))
        why = (f"{flag}'s weight falls steeper than its {shaped[0]:g} px where the band is narrower across its ring (cell {k}: "
               f"at --gain {gains[i]:g} a pixel keeps {1 - gains[i] * per_gain:.3f} of its area)")
        if on_fold == "refuse":
            raise SystemExit(f"{verb}: {why}; lower --gain or give {wider}")
        lowered = [steepest_gain(g, per_gain) if fold else g for g, fold, (per_gain, _) in zip(gains, over, steepest)]
        held = [hold or (fold and g < GAIN_MEASURED) for g, hold, fold in zip(lowered, held, over)]
        if all(held):
            raise SystemExit(f"{verb}: {why}, and --on-fold lower would have to go under --gain {GAIN_MEASURED:g}; give {wider}")
        answer = answer_at([0.0 if hold else g for g, hold in zip(lowered, held)], held)
    if ask.mask is not None:
        # The upper yield's shape and carry, as a band's: its ledger is the region mask's (`region_ledger`).
        up_band = Banded(Band(AUTO, AUTO, up), reach_requested, shaped[0], shaped[1], shaped[2], carried, steepest)
        return _masked(answer, up_band, (whole_across, whole_down), None if reference is None else reference[0], verb)
    banded = _banded(answer, shaped, reach_requested, carried, steepest, verb)
    return banded if ask.hold is None else _held(banded, None if reference is None else reference[0], verb)


def _carry_band(answer: Answer, reference: Image.Image | None) -> list[list[dict[str, Any]]]:
    """Each region carried by its own pixels (`carry_own`) from the cell it was read on: the reference frame; on a loop
    carried from its cut (`--read-on`, transported), the cut's cell 0 where the loop holds it, so the loop is carried as
    the cut is, cell for cell; else cell 0, where the lay or the cut's reading carried the regions."""
    base, base_shift, base_index, lays = _band_base(answer, reference)
    return carry_own(base, answer.cells, answer.ask.regions, base_shift, lays, base_index=base_index)


def _band_base(answer: Answer, reference: Image.Image | None) -> tuple[Image.Image, tuple[float, float], int | None, list[tuple[float, float]]]:
    """The cell the regions were read on, as a band carries from it (`_carry_band`): the picture, how it lies from cell 0,
    which of the cells it is (None: the reference frame, none of them), and every cell's lay."""
    cells = answer.cells
    lays = [answer.carry(k) for k in range(len(cells))]
    if reference is not None:
        return reference, (0.0, 0.0), None, lays
    if answer.read_on is not None and answer.read_on["relation"] == "recut":
        base = answer.read_on["base_cell"]
        return cells[base], lays[base], base, lays
    cut_cells = (answer.read_on or {}).get("cut_cells") or []
    base = cut_cells.index(0) if 0 in cut_cells else 0
    return cells[base], lays[base], base, lays


def band_steepest(regions: list[tuple[float, ...]], shapes: list[Any], carried: list[list[dict[str, Any]]],
                  unit: tuple[np.ndarray, np.ndarray], size: tuple[int, int]) -> list[tuple[float, int]]:
    """Per region, the steepest its band's weight falls along the move at gain 1 (`unit`, per cell), u·∇w over every
    pixel of every cell (∇ read between each pixel's neighbours, as `jacobian` reads it), and that cell: a pixel moved
    by gain g keeps 1 − g · u·∇w of its area, so the region folds at g · this ≥ 1."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    out = []
    for i, (region, shape) in enumerate(zip(regions, shapes)):
        per_cell = []
        for k, cell in enumerate(carried):
            px = cell[i]["px"]
            weight = band_weight(xx, yy, (region[0] + px[0], region[1] + px[1]), *shape)
            across = (weight[1:-1, 2:] - weight[1:-1, :-2]) / 2
            down = (weight[2:, 1:-1] - weight[:-2, 1:-1]) / 2
            per_cell.append(float(np.max(unit[0][k] * across + unit[1][k] * down)))
        k = int(np.argmax(per_cell))
        out.append((per_cell[k], k))
    return out


def steepest_gain(gain: float, per_gain: float) -> float:
    """The largest gain, in steps of GAIN_STEP and no more than `gain`, at which a slope of `per_gain` per unit of gain
    (`band_steepest`) stays under 1."""
    if per_gain <= 0:
        return gain
    steps = math.ceil(1 / per_gain / GAIN_STEP) - 1  # under the limit, never on it
    while steps > 0 and round(steps * GAIN_STEP, 2) * per_gain >= 1:
        steps -= 1
    return min(gain, round(steps * GAIN_STEP, 2))


def _banded(answer: Answer, shaped: tuple[float, float, list[Any]], reach: float, carried: list[list[dict[str, Any]]],
            steepest: list[tuple[float, int]], verb: str) -> Answer:
    """`answer` under its band, each region carried as `_carry_band` carried it, the steepest its weight falls
    (`band_steepest`), and the ledger of every cell (`band_ledger`). Refused where the move folds the picture all the
    same."""
    band_px, margin_px, shapes = shaped
    ask = answer.ask
    assert ask.band is not None
    banded = dataclasses.replace(answer, band=Banded(ask.band, reach, band_px, margin_px, shapes, carried, steepest))
    ledger = band_ledger(banded)
    if min(ledger["jacobian_min"]) <= 0:
        k = int(np.argmin(ledger["jacobian_min"]))
        raise SystemExit(f"{verb}: --band {band_px:g} px passes the fold rule (a move of {reach:.1f} px), and cell {k}'s move "
                         f"folds the picture all the same (a pixel takes {ledger['jacobian_min'][k]:g} of its area); this is "
                         "the engine's fault — nothing was written")
    return dataclasses.replace(banded, band=dataclasses.replace(banded.band, ledger=ledger))


def _box(mask: np.ndarray) -> list[int] | None:
    """The box (x0, y0, x1, y1, the last pixel's included) of a mask's pixels; None when it has none."""
    ys, xs = np.nonzero(mask)
    return None if not len(ys) else [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def band_ledger(answer: Answer) -> dict[str, Any]:
    """Per cell, what a band's move does (`--band`), each count a list over the cells with its largest beside it:
    - `band_on_regions_px`: solid pixels under a region's ellipse, carried, that the weight takes less than 1 of — the
      band lies over the part;
    - `pressed_px`, `stretched_px`: pixels of the moved cell, solid and moved, left with under PRESSED or over STRETCHED
      of their area (the area of the source a pixel takes, `jacobian`, is 1 over it), the box they lie in, and the least
      and the most area with where it is (`area_least`, `area_most`: area, x, y);
    - `outside_moved_px`: solid pixels under no region's ellipse that the move takes MOVED_PX or more;
    - `jacobian_min`: the least area of the source any pixel of the moved cell takes (0 or under: the picture folds)."""
    assert answer.band is not None
    w, h = answer.cells[0].size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    keys = ("band_on_regions_px", "pressed_px", "pressed_box", "area_least", "stretched_px", "stretched_box", "area_most",
            "outside_moved_px", "jacobian_min")
    ledger: dict[str, list[Any]] = {key: [] for key in keys}
    for k, cell in enumerate(answer.cells):
        alpha = np.asarray(cell.getchannel("A"), np.float32)
        solid = alpha >= ALPHA_SOLID
        field, weights = answer.band_field(k, xx, yy)
        regions = np.logical_or.reduce([region_weight(xx, yy, region, entry["px"]) > 0
                                        for region, entry in zip(answer.ask.regions, answer.band.carried[k])])
        ledger["band_on_regions_px"].append(int((solid & regions & (np.max(weights, axis=0) < 1)).sum()))
        moving = field is not None and any(dx != 0 or dy != 0 for _, (dx, dy) in answer.moves_in(k))
        if not moving:
            for key, empty in (("pressed_px", 0), ("pressed_box", None), ("area_least", None), ("stretched_px", 0),
                               ("stretched_box", None), ("area_most", None), ("outside_moved_px", 0), ("jacobian_min", 1.0)):
                ledger[key].append(empty)
            continue
        inside, move_x, move_y = field
        # The moved cell's alpha as `_draw` draws it: the alpha channel read alone, bilinear, is the premultiplied mix's.
        drawn = np.where(inside, np.clip(np.round(_sample(alpha[..., None], xx - move_x, yy - move_y)[..., 0]), 0, 255), alpha)
        area_of = jacobian(move_x, move_y)
        counted = np.zeros((h, w), bool)
        counted[1:-1, 1:-1] = (inside & (drawn >= ALPHA_SOLID))[1:-1, 1:-1] & (area_of > 0)
        area = np.ones((h, w))
        area[1:-1, 1:-1] = 1 / np.where(area_of > 0, area_of, 1.0)  # each pixel's area: 1 over the source's it takes
        pressed, stretched = counted & (area < PRESSED), counted & (area > STRETCHED)
        for key, mask in (("pressed", pressed), ("stretched", stretched)):
            ledger[f"{key}_px"].append(int(mask.sum()))
            ledger[f"{key}_box"].append(_box(mask))
        for key, pick, empty in (("area_least", np.argmin, np.inf), ("area_most", np.argmax, -np.inf)):
            if counted.any():
                y, x = divmod(int(pick(np.where(counted, area, empty))), w)
                ledger[key].append([round(float(area[y, x]), 4), x, y])
            else:
                ledger[key].append(None)
        ledger["outside_moved_px"].append(int((solid & ~regions & (np.hypot(move_x, move_y) >= MOVED_PX)).sum()))
        ledger["jacobian_min"].append(round(float(area_of.min()), 4))
    out: dict[str, Any] = {}
    for key in keys:
        out[key] = ledger[key]
        if key.endswith("_px"):
            out[f"{key}_max"] = max(ledger[key], default=0)
    return out


def band_notes(record: dict[str, Any], verb: str = VERB, at: str = f"{loop_mod.SWAY_RECORD}.band") -> list[str]:
    """What to say of a band's record (`--band`), a line each, as warnings: the cells a region was carried by the lay
    in and why, and every count of the ledger that is not 0 in some cell — in how many cells, up to how many pixels and
    in which cell, and where in the record (`at`: the band's key in the strip's meta, or in a frames folder's record)."""
    band, notes = record["band"], []
    by_region: dict[int, list[dict[str, Any]]] = {}
    for entry in band["lay"]:
        by_region.setdefault(entry["region"], []).append(entry)
    for i, entries in sorted(by_region.items()):
        name = "--region " + ",".join(f"{v:g}" for v in record["regions"][i])
        whys = "; ".join(f"cell {e['cell']}: {e['why']}" for e in entries)
        notes.append(f"{verb}: warning: --band carries {name} by the body's lay in {len(entries)} cells, its own pixels not "
                     f"found surely ({whys}) — {at}.lay")
    said = {"band_on_regions_px": "the band lies over solid pixels of the regions",
            "pressed_px": f"moved pixels are left under {PRESSED:g} of their area",
            "stretched_px": f"moved pixels are left over {STRETCHED:g} of their area",
            "outside_moved_px": f"solid pixels outside every region move {MOVED_PX:g} px or more"}
    return notes + _ledger_notes(band["ledger"], said, "--band", verb, at)


def _ledger_notes(ledger: dict[str, Any], said: dict[str, str], flag: str, verb: str, at: str) -> list[str]:
    """A warning line for each count of `ledger` that `said` names and that is not 0 in some cell: in how many cells, up
    to how many pixels and in which cell (and for the pressed and the stretched, the least or the most area there and
    where), and where in the record (`at`)."""
    notes = []
    for key, what in said.items():
        counts = ledger[key]
        n = len(counts)
        cells = [k for k, count in enumerate(counts) if count]
        if not cells:
            continue
        top = max(range(n), key=lambda k: counts[k])
        extra = ""
        if key in ("pressed_px", "stretched_px"):
            spot = ledger["area_least" if key == "pressed_px" else "area_most"][top]
            extra = f", {'least' if key == 'pressed_px' else 'most'} {spot[0]:g} at {spot[1]},{spot[2]}"
        notes.append(f"{verb}: warning: {flag}: {what} in {len(cells)} of {n} cells, up to {ledger[key + '_max']} px (cell "
                     f"{top}{extra}) — {at}.ledger.{key}")
    return notes


@dataclass(frozen=True, eq=False)
class Hold:
    """A hold mask asked (`--hold-mask`): one PNG drawn on the cell the regions were read on, or a folder of PNGs, one per
    cell in name order; a pixel is held where the PNG's alpha is ALPHA_SOLID or more — where it has no alpha, where it is
    that light. The sha256 of each file's bytes."""

    masks: list[np.ndarray]
    sha256: list[str]
    per_cell: bool

    def asked(self) -> dict[str, Any]:
        return {"per_cell": self.per_cell, "sha256": self.sha256 if self.per_cell else self.sha256[0]}


def _mask_of(image: Image.Image) -> np.ndarray:
    """The pixels a hold mask's PNG holds: alpha ALPHA_SOLID or more, or, where the PNG has no alpha, that light."""
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        return np.asarray(image.convert("RGBA").getchannel("A")) >= ALPHA_SOLID
    return np.asarray(image.convert("L")) >= ALPHA_SOLID


def read_hold(path: Path, verb: str = VERB) -> Hold:
    """A hold mask (`--hold-mask`) read: a PNG, or a folder of PNGs, one per cell, in name order. A single mask that holds
    no pixel is refused; a cell of a folder may hold none."""
    return Hold(*_read_masks(path, "--hold-mask", verb))


def _read_masks(path: Path, flag: str, verb: str) -> tuple[list[np.ndarray], list[str], bool]:
    """A mask's PNG, or a folder of PNGs, one per cell, in name order, read for `flag` (`_mask_of`): the masks, the sha256
    of each file's bytes, and whether it is a folder. Masks that hold no pixel between them are refused."""
    path = Path(path).expanduser()
    files = sorted(path.glob("*.png")) if path.is_dir() else [path]
    if not files:
        raise SystemExit(f"{verb}: {flag} {path}: a folder of masks with no PNG in it")
    masks, digests = [], []
    for file in files:
        try:
            data = file.read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                masks.append(_mask_of(image))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"{verb}: {flag} {file}: not read ({exc})") from exc
        digests.append(hashlib.sha256(data).hexdigest())
    if not any(mask.any() for mask in masks):
        raise SystemExit(f"{verb}: {flag} {path}: holds no pixel (alpha {ALPHA_SOLID} or more, or that light where it has "
                         "no alpha)")
    return masks, digests, path.is_dir()


def _grow4(mask: np.ndarray, steps: int) -> np.ndarray:
    """`mask` grown by `steps` 4-neighbour steps (a diamond), nothing carried round the cell's edge."""
    out = mask.copy()
    for _ in range(steps):
        p = np.pad(out, 1)
        out = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return out


def _grow8(mask: np.ndarray, steps: int = 1) -> np.ndarray:
    """`mask` grown by `steps` 8-neighbour steps (a square), nothing carried round the cell's edge."""
    h, w = mask.shape
    for _ in range(steps):
        p, out = np.pad(mask, 1), mask.copy()
        for dy in range(3):
            for dx in range(3):
                out |= p[dy:dy + h, dx:dx + w]
        mask = out
    return mask


def _pieces(mask: np.ndarray) -> list[np.ndarray]:
    """`mask`'s 8-connected pieces, in the order of their first pixel row by row."""
    left, pieces = mask.copy(), []
    while left.any():
        y, x = divmod(int(np.argmax(left)), mask.shape[1])
        piece = np.zeros_like(mask)
        piece[y, x] = True
        while True:
            grown = _grow8(piece) & left
            if not (grown & ~piece).any():
                break
            piece = grown
        pieces.append(piece)
        left &= ~piece
    return pieces


def _shifted(mask: np.ndarray, shift: tuple[float, float]) -> np.ndarray:
    """`mask` moved by `shift` (across, down): every pixel the moved mask covers some of, the whole-pixel moves on either
    side of it joined, nothing carried round the cell's edge."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    for sx in {math.floor(shift[0]), math.ceil(shift[0])}:
        for sy in {math.floor(shift[1]), math.ceil(shift[1])}:
            if abs(sx) >= w or abs(sy) >= h:
                continue
            src = mask[max(0, -sy):h - max(0, sy), max(0, -sx):w - max(0, sx)]
            out[max(0, sy):max(0, sy) + src.shape[0], max(0, sx):max(0, sx) + src.shape[1]] |= src
    return out


@dataclass(frozen=True, eq=False)
class Held:
    """What a hold mask comes to on the cells (`--hold-mask`): the mask in its 8-connected pieces on the cell it was drawn
    on, which lies `base_shift` from cell 0, each piece carried into every cell by its own pixels (`carried`, per cell and
    piece, as `carry_mask` carries it; none where a mask is given for each cell), how far round the held pixels their soft
    edge and the air beside them reach (`edge`, 4-neighbour steps), and the ledger (`hold_ledger`). Each cell is drawn once
    (`drawn`)."""

    asked: Hold
    pieces: list[np.ndarray]
    base_shift: tuple[float, float]
    carried: list[list[dict[str, Any]]]
    edge: int
    ledger: dict[str, Any] | None = None
    drawn: dict[int, Image.Image] = dataclasses.field(default_factory=dict)

    def mask_in(self, k: int) -> np.ndarray:
        """The mask's pixels in cell k: the mask given for it, or each piece carried there and the whole grown by HOLD_GROW
        px (4-neighbour steps), what a piece's carry may be off by where the part it holds changes its shape."""
        if self.asked.per_cell:
            return self.asked.masks[k]
        carried = np.zeros_like(self.asked.masks[0])
        for piece, entry in zip(self.pieces, self.carried[k]):
            carried |= _shifted(piece, (entry["px"][0] - self.base_shift[0], entry["px"][1] - self.base_shift[1]))
        return _grow4(carried, HOLD_GROW)


def hold_sets(mask: np.ndarray, cell: Image.Image, edge: int) -> tuple[np.ndarray, np.ndarray]:
    """What a hold keeps in a cell: the held pixels — the mask and every pixel not opaque (alpha over 0, under 255) within
    `edge` 4-neighbour steps of it, its soft edge and the half-clear rim of a gap beside it — and the air, the clear pixels
    within as far. Both keep their bytes; no pixel solid in the cell reads either, no clear one the held."""
    alpha = np.asarray(cell.getchannel("A"))
    near = _grow4(mask, edge) & ~mask
    return mask | (near & (alpha > 0) & (alpha < 255)), near & (alpha == 0)


def _bilinear(a: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """`a` (one value a pixel) read at (sx, sy) as `_sample` reads a cell."""
    h, w = a.shape
    x0 = np.clip(np.floor(sx).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, h - 2)
    fx, fy = np.clip(sx - x0, 0, 1), np.clip(sy - y0, 0, 1)
    return (a[y0, x0] * (1 - fx) + a[y0, x0 + 1] * fx) * (1 - fy) + (a[y0 + 1, x0] * (1 - fx) + a[y0 + 1, x0 + 1] * fx) * fy


def _mean_along(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """The mean over 2·r + 1 along `axis`, the edge value carried past the cell's edge."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
    if axis == 0:
        return (c[2 * r + 1:] - c[:-(2 * r + 1)]) / (2 * r + 1)
    return (c[:, 2 * r + 1:] - c[:, :-(2 * r + 1)]) / (2 * r + 1)


def _least_round(a: np.ndarray, r: int) -> np.ndarray:
    """The least of `a` over each pixel's square of side 2·r + 1 (the edge value past the cell's edge)."""
    out = a
    for axis in (0, 1):
        p = np.pad(out, [(r, r) if i == axis else (0, 0) for i in range(2)], mode="edge")
        n = out.shape[axis]
        out = np.min([np.take(p, range(i, i + n), axis=axis) for i in range(2 * r + 1)], axis=0)
    return out


def free_back(kept: np.ndarray, way: tuple[float, float], far: float) -> np.ndarray:
    """Per pixel, how far (px) a read can go back against `way` (the unit vector the cell's part moves along) before the
    four pixels it reads (`_sample`'s) take a `kept` pixel, or one within HOLD_MARGIN px of them (8-neighbour steps) where
    the pixel is not itself that near: followed an eighth of a pixel at a time, `far` where it does not come to one within
    `far`. Along a line the way the part moves, it rises by at most a px a px and falls past each kept pixel."""
    h, w = kept.shape
    near = _grow8(kept, HOLD_MARGIN)
    free = np.full((h, w), float(far))
    # Only a pixel `far` or less along `way` from the margin can come to it; between two whole steps a read's four pixels
    # may pair the one step's column with the other's row, a pixel off either.
    swept = near.copy()
    for i in range(1, math.ceil(far) + 1):
        swept |= _shifted(near, (i * way[0], i * way[1]))
    ys, xs = np.nonzero(_grow8(swept) & ~kept)
    if not len(ys):
        return free
    hard, soft = kept.astype(np.float64), near.astype(np.float64)
    own = soft[ys, xs]  # 1 where the pixel is itself within the margin: then only the kept pixels stop it
    found = np.full(len(ys), float(far))
    left = np.arange(len(ys))
    for i in range(1, math.ceil(8 * far) + 1):
        tau = min(i / 8, far)
        sx, sy = xs[left] - tau * way[0], ys[left] - tau * way[1]
        stops = (_bilinear(hard, sx, sy) > 0) | (_bilinear(soft, sx, sy) > own[left] + 1e-9)
        found[left[stops]] = (i - 1) / 8
        left = left[~stops]
        if not len(left):
            break
    free[ys, xs] = found
    return free


def _sloped(a: np.ndarray, slope: float) -> np.ndarray:
    """`a` lowered until no pixel is more than `slope` over any of its 8 neighbours: per pixel the least of a(q) + slope
    times q's distance in whole steps across or down, whichever is more (the edge value carried past the cell's edge)."""
    for _ in range(sum(a.shape)):
        p = np.pad(a, 1, mode="edge")
        rows = np.minimum(np.minimum(p[:-2], p[1:-1]), p[2:])
        lowered = np.minimum(a, np.minimum(np.minimum(rows[:, :-2], rows[:, 1:-1]), rows[:, 2:]) + slope)
        if np.array_equal(lowered, a):
            break
        a = lowered
    return a


def read_share(free: np.ndarray, size: np.ndarray) -> np.ndarray:
    """Per pixel, the share of its move (`size` px long) a read takes round a hold: the read goes back f = min(size, h) px,
    h HOLD_SLOPE of `free` (`free_back`), taken at its least within HOLD_ROUND px each way and then averaged over as far,
    and lowered until it is nowhere more than HOLD_SLOPE over a neighbour (`_sloped`); 1 where f is the whole move, so
    those pixels read as without the hold."""
    # Within a cell every move is s·ô, one way ô (the groups' offsets are one motion times their gains), so the read
    # p − f·ô takes 1 − ∂ô f of the source's area, and 1 − (ôx·Dx f + ôy·Dy f) read between neighbours as `jacobian` reads
    # it: h rises by at most κ (HOLD_SLOPE) a px every way, so where f = h a read takes at least 1 − κ (between
    # neighbours 1 − κ(|ôx| + |ôy|) ≥ 1 − κ√2), and where f = s the move's own least area — over 0 wherever the move does
    # not fold; where f turns from the one to the other the two mix between neighbours, and the ledger reads those
    # (`read_turn_folded_px`, `read_turn_least`).
    reach = HOLD_SLOPE * free
    if HOLD_ROUND:
        r = HOLD_ROUND
        reach = _mean_along(_mean_along(_least_round(reach, r), r, 0), r, 1)
    reach = _sloped(reach, HOLD_SLOPE)
    return np.where(reach >= size, 1.0, reach / np.maximum(size, 1e-12))


def draw_held(cell: Image.Image, field: tuple[np.ndarray, np.ndarray, np.ndarray], held: np.ndarray,
              air: np.ndarray, short: bool = True) -> tuple[Image.Image, dict[str, np.ndarray]]:
    """`cell` moved by `field` (`_draw`'s: covered, across, down) round what a hold keeps (`hold_sets`: the held pixels and
    the air beside them). The field is as it is; the held pixels and the air keep their bytes; every other pixel is read
    as `_draw` reads it, but only as far back along its move as `read_share` lets it, which stops short of every kept
    pixel (with `short` false, its whole move: a region mask's rim keeps its reads clear of the held pixels). A read whose
    four pixels still take one it may not (a solid pixel a kept one, a clear pixel a held one) is read from the others
    where they are half its weight or more (`renormalised`), else walked back a 1/HOLD_STEPS of its move at a time until
    they are (`walked`). Where every read takes its whole move and no kept pixel the cell is `_draw`'s, byte for byte.
    Also returns, per pixel, the share of its move each read took (`share`), the pixels read (`readers`) and the move each
    read took (`read_x`, `read_y`; 0 on what is kept and on what does not move)."""
    src = np.asarray(cell.convert("RGBA"), dtype=np.float32)
    h, w = src.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    inside, move_x, move_y = field
    size = np.hypot(move_x, move_y)
    pm = src.copy()
    pm[..., :3] *= pm[..., 3:4] / 255
    kept = held | air
    solid = src[..., 3] >= ALPHA_SOLID
    readers = inside & ~kept & (size > 0)
    share = np.ones((h, w))
    if short and readers.any():
        y, x = divmod(int(np.argmax(np.where(readers, size, -1))), w)
        way = (float(move_x[y, x] / size[y, x]), float(move_y[y, x] / size[y, x]))
        share = np.where(readers, read_share(free_back(kept, way, float(size[y, x]) / HOLD_SLOPE + 1), size), 1.0)
    sx, sy = xx - share * move_x, yy - share * move_y
    moved = _sample(pm, sx, sy)
    x0 = np.clip(np.floor(sx).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, h - 2)
    fx, fy = np.clip(sx - x0, 0, 1), np.clip(sy - y0, 0, 1)
    off = np.zeros((h, w), bool)
    for oy, ox, weight in ((0, 0, (1 - fx) * (1 - fy)), (0, 1, fx * (1 - fy)), (1, 0, (1 - fx) * fy), (1, 1, fx * fy)):
        off |= (weight > 0) & np.where(solid, kept[y0 + oy, x0 + ox], held[y0 + oy, x0 + ox])
    off &= readers
    renormalised, walked = np.zeros((h, w), bool), np.zeros((h, w), bool)
    if off.any():
        ys, xs = np.nonzero(off)
        of_solid = solid[ys, xs]
        t = share[ys, xs].copy()
        left, first = np.arange(len(ys)), True
        while len(left):
            ly, lx = ys[left], xs[left]
            px, py = lx - t[left] * move_x[ly, lx], ly - t[left] * move_y[ly, lx]
            ax0 = np.clip(np.floor(px).astype(int), 0, w - 2)
            ay0 = np.clip(np.floor(py).astype(int), 0, h - 2)
            ax, ay = np.clip(px - ax0, 0, 1), np.clip(py - ay0, 0, 1)
            total, taken = np.zeros((len(left), 4)), np.zeros(len(left))
            for oy, ox, weight in ((0, 0, (1 - ax) * (1 - ay)), (0, 1, ax * (1 - ay)), (1, 0, (1 - ax) * ay), (1, 1, ax * ay)):
                tap_y, tap_x = ay0 + oy, ax0 + ox
                ok = np.where(of_solid[left], ~kept[tap_y, tap_x], ~held[tap_y, tap_x]) * weight
                total += pm[tap_y, tap_x] * ok[:, None]
                taken += ok
            done = taken >= 0.5
            moved[ly[done], lx[done]] = total[done] / taken[done][:, None]
            (renormalised if first else walked)[ly[done], lx[done]] = True
            share[ly[done], lx[done]] = t[left[done]]
            left, first = left[~done], False
            t[left] = np.maximum(t[left] - 1 / HOLD_STEPS, 0.0)  # at 0 a pixel reads itself, which it may
    alpha = np.clip(np.round(moved[..., 3:4]), 0, 255)
    moved[..., :3] = np.where(alpha > 0, moved[..., :3] * 255 / np.maximum(moved[..., 3:4], 1e-3), 0)
    moved[..., 3:4] = alpha
    out = np.where((inside & ~kept)[..., None], np.clip(np.round(moved), 0, 255), src).astype(np.uint8)
    taken_x, taken_y = np.where(readers, share * move_x, 0.0), np.where(readers, share * move_y, 0.0)
    return Image.fromarray(out, "RGBA"), {"share": share, "readers": readers, "renormalised": renormalised, "walked": walked,
                                          "read_x": taken_x, "read_y": taken_y}


def _held(answer: Answer, reference: Image.Image | None, verb: str) -> Answer:
    """`answer` round its hold mask (`--hold-mask`): one mask, drawn on the cell the regions were read on (`_band_base`),
    in its pieces, each carried into every cell by its own solid pixels there as a band carries a region (`carry_mask`),
    or a mask for each cell as given; and the ledger of every cell (`hold_ledger`)."""
    assert answer.ask.hold is not None
    held = _hold_of(answer, answer.ask.hold, reference, math.ceil(answer.reach) + HOLD_EDGE, verb)
    answer = dataclasses.replace(answer, hold=held)
    return dataclasses.replace(answer, hold=dataclasses.replace(held, ledger=hold_ledger(answer)))


def _hold_of(answer: Answer, hold: Hold, reference: Image.Image | None, edge: int, verb: str) -> Held:
    """A hold mask on `answer`'s cells, `edge` px round it its soft edge and air (`Held`): one mask, drawn on the cell the
    regions were read on (`_band_base`), in its pieces, each carried into every cell by its own solid pixels there
    (`carry_mask`); or a mask for each cell as given. A band's (`--hold-mask`), or the pixels a region's layer leaves
    out (`--region-over`)."""
    cells = answer.cells
    w, h = cells[0].size
    for mask in hold.masks:
        if mask.shape != (h, w):
            raise SystemExit(f"{verb}: --hold-mask is {mask.shape[1]}x{mask.shape[0]}, the cells are {w}x{h}: a mask is drawn on "
                             "a cell, pixel for pixel")
    if hold.per_cell and len(hold.masks) != len(cells):
        raise SystemExit(f"{verb}: --hold-mask holds {len(hold.masks)} masks and there are {len(cells)} cells: a folder gives "
                         "one mask per cell, in name order")
    if hold.per_cell:
        return Held(hold, [], (0.0, 0.0), [], edge)
    base, base_shift, base_index, lays = _band_base(answer, reference)
    solid = np.asarray(base.getchannel("A")) >= ALPHA_SOLID
    whole, cell_wholes = _whole_premultiplied(base), {}
    pieces = _pieces(hold.masks[0])
    carried: list[list[dict[str, Any]]] = [[] for _ in cells]
    for piece in pieces:
        entries = carry_mask(whole, cells, cell_wholes, piece & solid, base_shift, lays, base_index,
                             "no solid pixel of the cell it was drawn on is under it")
        for k in range(len(cells)):
            carried[k].append(entries[k])
    return Held(hold, pieces, (float(base_shift[0]), float(base_shift[1])), carried, edge)


def hold_ledger(answer: Answer) -> dict[str, Any]:
    """Per cell, what a hold kept and how the reads round it went (`--hold-mask`), each count a list over the cells with its
    largest beside it:
    - `held_px`, `soft_px`, `air_px`: the mask's pixels, the pixels not opaque held round it, the clear ones kept round it;
    - `short_px`: pixels read short of their move; `renormalised_px`, `walked_px`: those whose four pixels still took a kept
      one, read from the others or walked back further (`draw_held`);
    - `read_folded_px`: opaque pixels of the drawn cell, not kept nor beside a kept one (4-neighbours), whose read takes 0
      or less of the source's area (`jacobian` of the move each read took: the source read backwards), and
      `read_folded_edge_px` those beside a kept one, where the part moves under it and the read is meant to jump;
    - `read_least`: the least area a read of the first kind takes, and where (area, x, y; None where none moves);
    - `read_turn_folded_px` and `read_turn_least`: the same for those of the first kind where the read turns from the
      whole move to the share round the hold (`read_share`): some of the pixel and its 4-neighbours read the whole move
      and some less, the two mixed between neighbours, where the bound on the read's area does not hold."""
    keys = ("held_px", "soft_px", "air_px", "short_px", "renormalised_px", "walked_px", "read_folded_px", "read_folded_edge_px",
            "read_least", "read_turn_folded_px", "read_turn_least")
    ledger: dict[str, list[Any]] = {key: [] for key in keys}
    for k in range(len(answer.cells)):
        drawn, read = answer.held_cell(k)
        mask, held, air = read["mask"], read["held"], read["air"]
        kept = held | air
        h, w = mask.shape
        area = np.ones((h, w))
        area[1:-1, 1:-1] = jacobian(read["read_x"], read["read_y"])
        counted = np.zeros((h, w), bool)
        counted[1:-1, 1:-1] = True
        counted &= (np.asarray(drawn.getchannel("A")) >= ALPHA_SOLID) & ~kept
        beside = _grow4(kept, 1)
        folded = counted & (area <= 0)
        ledger["held_px"].append(int(mask.sum()))
        ledger["soft_px"].append(int((held & ~mask).sum()))
        ledger["air_px"].append(int(air.sum()))
        ledger["short_px"].append(int((read["readers"] & (read["share"] < 1)).sum()))
        ledger["renormalised_px"].append(int(read["renormalised"].sum()))
        ledger["walked_px"].append(int(read["walked"].sum()))
        ledger["read_folded_px"].append(int((folded & ~beside).sum()))
        ledger["read_folded_edge_px"].append(int((folded & beside).sum()))
        live = counted & ~beside & read["readers"]
        sp = np.pad(read["share"], 1, mode="edge")
        around = [read["share"], sp[:-2, 1:-1], sp[2:, 1:-1], sp[1:-1, :-2], sp[1:-1, 2:]]
        turn = live & (np.minimum.reduce(around) < 1) & (np.maximum.reduce(around) >= 1)
        ledger["read_turn_folded_px"].append(int((folded & turn).sum()))
        for key, where in (("read_least", live), ("read_turn_least", turn)):
            if where.any():
                y, x = divmod(int(np.argmin(np.where(where, area, np.inf))), w)
                ledger[key].append([round(float(area[y, x]), 4), x, y])
            else:
                ledger[key].append(None)
    out: dict[str, Any] = {}
    for key in keys:
        out[key] = ledger[key]
        if key.endswith("_px"):
            out[f"{key}_max"] = max(ledger[key], default=0)
    return out


def hold_notes(record: dict[str, Any], verb: str = VERB, at: str = f"{loop_mod.SWAY_RECORD}.hold") -> list[str]:
    """What to say of a hold's record (`--hold-mask`), a line each, as warnings: the cells a piece of the mask was carried
    by the lay in and why, and the reads round it that took a kept pixel or turned back — in how many cells, up to how many
    pixels and in which cell, and where in the record (`at`)."""
    hold, notes = record["hold"], []
    by_piece: dict[int, list[dict[str, Any]]] = {}
    for entry in hold["lay"]:
        by_piece.setdefault(entry["piece"], []).append(entry)
    for i, entries in sorted(by_piece.items()):
        whys = "; ".join(f"cell {e['cell']}: {e['why']}" for e in entries)
        notes.append(f"{verb}: warning: --hold-mask carries its piece {i} by the body's lay in {len(entries)} cells, its own "
                     f"pixels not found surely ({whys}) — {at}.lay")
    said = {"renormalised_px": "reads took a kept pixel and were read from the others",
            "walked_px": "reads took a kept pixel and were walked back further",
            "read_folded_px": "reads take the source backwards (0 or less of its area)"}
    return notes + _ledger_notes(hold["ledger"], said, "--hold-mask", verb, at)


@dataclass(frozen=True, eq=False)
class RegionMask:
    """A region given as a mask (`--region-mask`): one PNG drawn on the cell the regions were read on, or a folder of PNGs,
    one per cell in name order, read as a hold mask is (`_mask_of`), and the sha256 of each file's bytes; how many times
    the fold rule's least band the rim starts at (`--region-rim`), the px the mask is worn in by (`--region-shrink`),
    whether its lower edge is moved to the crease under it (`--region-crease`), the upper yield (`--band-up`'s flat
    share and band; None: none), whether it is laid over the figure under it (`--region-over`) and the pixels its layer
    leaves out (`--hold-mask`; None: none), the most a move one way may rise across the rim the other way
    (`--region-shear`, px a px; None: the rim by the move along it alone), how far under its lower edge a membrane
    moves the figure under it (`--region-membrane`, px; None: none, the layer slides over it), whether only the solid
    pixels under it are laid over (`--region-over-solid`), and whether the held pixels beside it hold the rim round
    (`--region-rim-round`); with a membrane, whether what it moves is no anchor of the rim (`--region-membrane-unpin`),
    the alpha under which a pixel is read as clear (`--region-clear-alpha`; None: 0's alone), how far into the region's
    columns from each side the lowest pixel sets the lower edge beside it (`--region-floor-beside`, px; 0: the nearest
    column's), and how far the region grows over the outline its mask leaves out (`--region-own-outline`, px; 0: none).
    The record names the last ten only where they are asked, the membrane's four only with one."""

    masks: list[np.ndarray]
    sha256: list[str]
    per_cell: bool
    rim: float = RIM_DEFAULT
    shrink: int = SHRINK_DEFAULT
    crease: bool = False
    up: tuple[float, float | str] | None = None
    over: bool = False
    shear: float | None = None
    hold: Hold | None = None
    membrane: int | None = None
    over_solid: bool = False
    rim_round: bool = False
    unpin: bool = False
    clear_alpha: int | None = None
    floor_beside: int = 0
    own_outline: int = 0

    def asked(self) -> dict[str, Any]:
        return {"per_cell": self.per_cell, "sha256": self.sha256 if self.per_cell else self.sha256[0], "rim": self.rim,
                "shrink_px": self.shrink, "crease": self.crease,
                "up": None if self.up is None else {"flat": self.up[0], "band": self.up[1]}} \
            | ({} if not self.over else {"over": True, "hold": None if self.hold is None else self.hold.asked()}) \
            | ({} if not self.over_solid else {"over_solid": True}) \
            | ({} if not self.rim_round else {"rim_round": True}) \
            | ({} if self.shear is None else {"shear": self.shear}) \
            | ({} if self.membrane is None else {"membrane_px": self.membrane} | self.membrane_asked())

    def alpha_of(self, cell: Image.Image) -> np.ndarray:
        """The cell's alpha as the sets and a membrane read it: under `--region-clear-alpha` (with a membrane) 0, clear;
        the cell's bytes are its own."""
        alpha = np.asarray(cell.getchannel("A"))
        return alpha if self.membrane is None or self.clear_alpha is None else np.where(alpha < self.clear_alpha, 0, alpha)

    def membrane_asked(self) -> dict[str, Any]:
        """The membrane's own keys as the record names them: those asked, with a membrane; none without one."""
        if self.membrane is None:
            return {}
        return ({} if not self.unpin else {"membrane_unpin": True}) \
            | ({} if self.clear_alpha is None else {"clear_alpha": self.clear_alpha}) \
            | ({} if not self.floor_beside else {"floor_beside_px": self.floor_beside}) \
            | ({} if not self.own_outline else {"own_outline_px": self.own_outline})

    def edges(self) -> bool:
        """Whether the ledger reads the region's edges (`still_strip`, `tip_shape`, `move_steps`): with `--region-over`
        or `--region-shear`."""
        return self.over or self.shear is not None


@dataclass(frozen=True, eq=False)
class Masked:
    """What a region mask comes to on the cells (`--region-mask`): the mask — one, drawn on the cell the regions were read
    on, which lies `base_shift` from cell 0, or one per cell — trimmed to its crease where asked and worn in (`worn`), each
    one's crease (`creases`; None where not asked), how many solid pixels the one mask was carried by (`matched`), its
    carry in every cell (`carried`, as `carry_region` carries it; None where a mask is given for each cell), how far round
    the region the air it may move into reaches (`air`, 8-neighbour steps), the rim (`rim_bands`: across and down, its
    slope at the steepest cell and the least bands it started at), the upper yield shaped and carried as a band's (`up`,
    `--band-up`; None: none), the rim `--region-shear` asks at least, across and down (`sheared`; None: not asked), the
    pixels a layer leaves out carried as a hold mask's (`left_out`, `--region-over` with `--hold-mask`; None: none), and
    the ledger (`region_ledger`). Each cell is drawn once (`drawn`)."""

    asked: RegionMask
    worn: list[np.ndarray]
    creases: list[dict[str, Any]] | None
    base_shift: tuple[float, float]
    matched: int
    carried: list[dict[str, Any]] | None
    air: int
    rim: tuple[int, int, float, tuple[int, int]]
    up: Banded | None = None
    ledger: dict[str, Any] | None = None
    drawn: dict[int, Image.Image] = dataclasses.field(default_factory=dict)
    sheared: tuple[int, int] | None = None
    left_out: Held | None = None

    def region_in(self, k: int, solid: np.ndarray) -> np.ndarray:
        """The region's pixels in cell k (`solid`: the cell's solid pixels): the mask given for it, or the one mask carried
        there (`_carried`), where the cell is solid."""
        if self.asked.per_cell:
            return self.worn[k] & solid
        px = self.carried[k]["px"]
        return _carried(self.worn[0], (px[0] - self.base_shift[0], px[1] - self.base_shift[1])) & solid

    def left_out_in(self, k: int) -> np.ndarray:
        """The pixels a layer leaves out in cell k (`--region-over` with `--hold-mask`, `Held.mask_in`): none without one."""
        if self.left_out is None:
            return np.zeros(self.worn[0].shape, bool)
        return self.left_out.mask_in(k)


def _shrink4(mask: np.ndarray, steps: int) -> np.ndarray:
    """`mask` worn in by `steps` 4-neighbour steps: a pixel stays where it and its 4 neighbours are all in it (past the
    cell's edge none is)."""
    out = mask.copy()
    for _ in range(steps):
        p = np.pad(out, 1)
        out = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return out


def _carried(mask: np.ndarray, shift: tuple[float, float]) -> np.ndarray:
    """`mask` moved by `shift` (across, down), read bilinear as `_sample` reads a cell: the pixels it covers half or more of."""
    h, w = mask.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    return _bilinear(mask.astype(np.float64), xx - shift[0], yy - shift[1]) >= 0.5


def crease_of(cell: Image.Image, mask: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """`mask` ending at the crease under it (`--region-crease`): in each column of its solid pixels in `cell` whose lowest
    has a solid pixel under it (the part lies on more of the figure there), the darkest solid pixel (by luma) from
    CREASE_UP rows above that lowest one to CREASE_DOWN rows below it, the rows then the median over CREASE_SPAN columns
    either way, rounded down; the mask is cut under that row and filled down to it with the column's solid pixels. A column
    whose lowest pixel has a clear one under it (the part's free edge, over the air) is left as it is. Also the record of
    it: each column's row, and how many columns were left free."""
    rgba = np.asarray(cell.convert("RGBA"))
    solid = rgba[..., 3] >= ALPHA_SOLID
    luma = rgba[..., :3].astype(np.float64) @ np.array([0.299, 0.587, 0.114])
    on = mask & solid
    h = mask.shape[0]
    darkest: dict[int, int] = {}
    free = 0
    for x in (int(x) for x in np.nonzero(on.any(axis=0))[0]):
        lowest = int(np.nonzero(on[:, x])[0].max())
        if lowest + 1 >= h or not solid[lowest + 1, x]:
            free += 1
            continue
        window = [y for y in range(lowest - CREASE_UP, lowest + CREASE_DOWN + 1) if 0 <= y < h and solid[y, x]]
        darkest[x] = min(window, key=lambda y: luma[y, x])
    rows = {x: int(np.floor(np.median([darkest[q] for q in range(x - CREASE_SPAN, x + CREASE_SPAN + 1) if q in darkest])))
            for x in darkest}
    out = mask.copy()
    for x, y in rows.items():
        lowest = int(np.nonzero(on[:, x])[0].max())
        out[y + 1:, x] = False
        out[lowest + 1:y + 1, x] |= solid[lowest + 1:y + 1, x]
    return out, {"rows": {str(x): y for x, y in sorted(rows.items())}, "free_columns": free}


def crease_reach(region: np.ndarray, solid: np.ndarray, rows: dict[str, int], shift: tuple[float, float]) -> np.ndarray:
    """`region` — a cell's, its mask carried `shift` from the cell it was drawn on — reaching down to its crease again
    where the wear lifted it (`--region-over` with `--region-crease`): the crease's rows (`crease_of`, columns and rows on
    that cell; a column between two of them takes the nearer's row, the left one's on a tie) carried by `shift` (the column
    rounded to the nearest, half to even), and in each column the crease reaches, where the region holds a pixel, the cell's
    solid pixels from its lowest one down to the crease's row. The crease is the picture's own edge, not one drawn a little
    large, so the wear leaves it where it is; it still wears the mask in everywhere else."""
    h, w = region.shape
    columns = sorted(int(x) for x in rows)
    if not columns:
        return region.copy()
    line: dict[int, float] = {}
    for x in range(max(0, columns[0]), min(w, columns[-1] + 1)):
        at = int(round(x + shift[0]))
        if 0 <= at < w:
            line[at] = rows[str(min(columns, key=lambda q: abs(q - x)))] + shift[1]
    out = region.copy()
    for x, y in line.items():
        if out[:, x].any():
            lowest, crease = int(np.nonzero(out[:, x])[0].max()), int(math.floor(y))
            out[lowest + 1:crease + 1, x] |= solid[lowest + 1:crease + 1, x]
    return out


def carry_region(whole: np.ndarray, cells: list[Image.Image], mask: np.ndarray, base_shift: tuple[float, float],
                 lays: list[tuple[float, float]], base_index: int | None) -> list[dict[str, Any]]:
    """Per cell, where a region mask's pixels — those of `whole` (`_whole_premultiplied` of the cell it was drawn on, which
    lies `base_shift` from cell 0) under `mask` — are (`carry_match`), in the loop's order from `base_index` (from cell 0
    where the cell it was drawn on is none of them): looked for round where the cell before had them first and round the
    cell's lay second, the lesser difference of the two that settle (where the cell before had them, on a tie); where
    neither settles, where the cell before had them, saying why. A part moves little from one cell to the next, and the
    lay can lie far off it where the outline changes; never the lay alone."""
    n = len(cells)
    order = list(range(n)) if base_index is None else [(base_index + i) % n for i in range(1, n)]
    entries: dict[int, dict[str, Any]] = {}
    if base_index is not None:
        entries[base_index] = {"px": [float(base_shift[0]), float(base_shift[1])], "from": "base"}
    before_cell, before = base_index, (0.0, 0.0)  # the cell before and its carry from the cell the mask was drawn on
    for k in order:
        cell = _whole_premultiplied(cells[k])
        lay = (float(lays[k][0]) - base_shift[0], float(lays[k][1]) - base_shift[1])
        named = "where the frame it was drawn on had it" if before_cell is None else f"where cell {before_cell} had it"
        tries = [("before", named, carry_match(whole, cell, mask, before, around=named))]
        if (round(lay[0]), round(lay[1])) != (round(before[0]), round(before[1])):  # else the same search
            tries.append(("lay", "the lay", carry_match(whole, cell, mask, lay)))
        settled = [(guess, found) for guess, _, found in tries if found.shift is not None]
        if settled:
            guess, found = min(settled, key=lambda t: t[1].least)  # the first on a tie
            before = found.shift
            entries[k] = {"px": [base_shift[0] + before[0], base_shift[1] + before[1]], "from": "pixels", "guess": guess,
                          "least": found.least, "curvature": None if found.curvature is None else list(found.curvature)}
        else:
            found = tries[0][2]
            entries[k] = {"px": [base_shift[0] + before[0], base_shift[1] + before[1]], "from": "before",
                          "why": "; ".join(f"round {name}: {miss.why}" for _, name, miss in tries),
                          "least": found.least, "curvature": None if found.curvature is None else list(found.curvature)}
        before_cell = k
    return [entries[k] for k in range(n)]


def rim_bands(across: np.ndarray, down: np.ndarray, scale: float,
              floor: tuple[int, int] | None = None) -> tuple[int, int, float, tuple[int, int]]:
    """A region mask's rim for a move (`across`, `down`: px per cell): it starts at the fold rule's least band for the
    largest move each way, ceil(most × π / 2) + 1, times `scale` and rounded up, and both are widened a px at a time until
    (π/2)·hypot(dx/Lx, dy/Ly) is at most RIM_SLOPE in every cell. sin²(π/2·d) rises at most π/2 a unit of d, and d (the
    distance in rim units, `rim_distance`) at most 1/Lx a px across and 1/Ly down, so a move takes at most that much of a
    pixel's area along its way and goes less far than the held pixels lie. Then each is at least `floor`'s (`shear_floor`;
    None: as it is), which only lowers the slope. The rim across and down, that slope at its steepest cell, and the least
    bands it started at."""

    def least(most: float) -> int:
        return math.ceil((math.ceil(most * math.pi / 2) + 1) * scale)

    started = (least(float(np.max(np.abs(across)))), least(float(np.max(np.abs(down)))))
    lx, ly = started

    def steepest() -> float:
        return float(np.max(0.5 * math.pi * np.hypot(across / lx, down / ly)))

    while steepest() > RIM_SLOPE:
        lx, ly = lx + 1, ly + 1
    if floor is not None:
        lx, ly = max(lx, floor[0]), max(ly, floor[1])
    return lx, ly, round(steepest(), 4), started


def shear_floor(across: np.ndarray, down: np.ndarray, shear: float) -> tuple[int, int]:
    """The rim `--region-shear` asks at least for a move (`across`, `down`: px per cell): across, ceil(π/2 × the most move
    down / `shear`); down, ceil(π/2 × the most move across / `shear`). The fold rule reads each way by the move along it
    alone, so a move down rises all at once across a rim sized for a small move across: sin²(π/2·d) rises at most π/2 a
    rim's width, so with these a move down rises across the rim by at most `shear` px a px, and a move across down it."""
    return (math.ceil(0.5 * math.pi * float(np.max(np.abs(down))) / shear),
            math.ceil(0.5 * math.pi * float(np.max(np.abs(across))) / shear))


def rim_distance(held: np.ndarray, across: int, down: int) -> np.ndarray:
    """Per pixel, how far the nearest held pixel is in rim units, an ellipse `across` px wide each way and `down` px high —
    d = the least of hypot((x − qx)/across, (y − qy)/down) over the held pixels q — and 1 where none is nearer: the least
    along each row, then down each column, which is exact (past the cell's edge none is held)."""
    h, w = held.shape
    row = np.full((h, w), np.inf)
    p = np.pad(held, ((0, 0), (across, across)))
    for ox in range(-across, across + 1):
        row = np.where(p[:, across + ox:across + ox + w], np.minimum(row, (ox / across) ** 2), row)
    q = np.pad(row, ((down, down), (0, 0)), constant_values=np.inf)
    both = np.full((h, w), np.inf)
    for oy in range(-down, down + 1):
        both = np.minimum(both, q[down + oy:down + oy + h] + (oy / down) ** 2)
    return np.sqrt(np.minimum(both, 1.0))


def region_sets(region: np.ndarray, alpha: np.ndarray, air: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """What a region mask moves and holds in a cell (`alpha`: the cell's alpha): the region's soft edge — the pixels not
    opaque (alpha over 0, under ALPHA_SOLID) within REGION_SOFT 4-neighbour steps of it and not beside another solid
    pixel — which moves with it; the held pixels, every other one not clear; and the clear pixels within `air`
    8-neighbour steps of it, the air it may move into."""
    solid = alpha >= ALPHA_SOLID
    soft = (alpha > 0) & ~solid & _grow4(region, REGION_SOFT) & ~region & ~_grow4(solid & ~region, 1)
    return soft, (alpha > 0) & ~region & ~soft, (alpha == 0) & _grow8(region, air)


def layer_sets(region: np.ndarray, alpha: np.ndarray, air: int, left_out: np.ndarray,
               solid_only: bool = False, beside: int = 0) -> dict[str, np.ndarray]:
    """What a region laid over the figure under it moves, draws over and holds in a cell (`--region-over`; `alpha`: the
    cell's alpha, `air`: the move's reach and REGION_AIR px, `left_out`: the pixels `--hold-mask` names there, which the
    region keeps where it holds them and which are left out of all else; `solid_only`: `--region-over-solid`, the changes
    it makes said where they fall; `beside`: `--region-floor-beside`, the lower edge beside its columns):
    - `soft`: its soft edge, the pixels not opaque within REGION_SOFT 4-neighbour steps of it, beside another solid pixel
      or not (left beside the figure under it, it would stay behind as a ghost of its outline), none left out; under its
      lower edge (`floor`) only those touching no solid pixel outside it (8-neighbour): one that does is the figure's own
      soft side, which stays where it is — with `solid_only`, under its lower edge too, whatever it touches;
    - `held`: every other pixel not clear; `air`: the clear pixels within `air` 8-neighbour steps of it (`region_sets`);
    - `under`: the held pixels under its lower edge, solid or not — in its columns and `air` px past them, under the lowest
      pixel of the region's nearest column (the leftmost on a tie) — none left out; no anchor of the rim, so the lower edge
      moves with the rest, beside the figure's soft side under it too (an anchor there would hold the outline over the air
      beside it short of a px, and part of it would stay where it was); with `solid_only`, only the solid ones: the rest of the
      figure there, not opaque, stays, an anchor of the rim, and is never drawn over;
    - `below`: the held pixels under its lower edge, solid or not, none left out (with `solid_only`, more than `under`): what a
      membrane may move (`membrane_sets`);
    - `band`: those of them within `air` 8-neighbour steps of it and its soft edge, which move too, and which the layer is
      drawn over (`lay_over`);
    - `edge`: the band's solid pixels that are its own outline against the air — beside a pixel neither solid nor left out
      (4-neighbour), within 2 px of it, touching no other solid pixel outside it (4-neighbour) — which go with the layer and
      leave the air behind; an outline pixel that touches the figure under it or a pixel left out is the figure's, kept;
    - `anchors`: the held pixels the rim falls to: all but those under its lower edge;
    - `floor`: per column, the first row under its lower edge (that of its nearest column; with `beside` N, past its
      columns that of the lowest pixel of its columns within N of that side), the cell's height where none."""
    solid = alpha >= ALPHA_SOLID
    h, w = region.shape
    under = np.zeros((h, w), bool)
    floor = np.full(w, h)
    xs = np.nonzero(region.any(axis=0))[0]
    if len(xs):
        lowest = {int(x): int(np.nonzero(region[:, x])[0].max()) for x in xs}
        left, right = int(xs.min()), int(xs.max())
        sides = (max(lowest[q] for q in lowest if q <= left + beside), max(lowest[q] for q in lowest if q >= right - beside))
        for x in range(max(0, left - air), min(w, right + air + 1)):
            floor[x] = (sides[0] if beside and x < left else sides[1] if beside and x > right
                        else lowest[min(lowest, key=lambda q: abs(q - x))]) + 1
            under[floor[x]:, x] = True
    soft = (alpha > 0) & ~solid & _grow4(region, REGION_SOFT) & ~region & ~left_out
    if not solid_only:
        soft &= ~(under & _grow8(solid & ~region, 1))
    held = (alpha > 0) & ~region & ~soft
    under &= held & ~left_out
    below = under
    if solid_only:
        under = under & solid
    band = under & _grow8(region | soft, air)
    beside = band & solid & _grow4(~solid & ~left_out, 1) & _grow8(region | soft, 2)
    edge = beside & ~_grow4(solid & ~region & ~beside, 1)
    return {"region": region, "soft": soft, "air": (alpha == 0) & _grow8(region, air), "held": held, "under": under,
            "below": below, "band": band, "edge": edge, "anchors": held & ~under, "floor": floor}


def membrane_sets(sets: dict[str, np.ndarray], reach: int) -> np.ndarray:
    """What a membrane moves under a region laid over the figure under it (`--region-membrane N`, `layer_sets`): the pixels
    under its lower edge, solid or not (`below`: with `--region-over-solid` the pixels not opaque there too, which the
    layer leaves where they are), within N + 1 px of it and its soft edge (`reach` N; straight-line px, as `rim_distance`
    reads a round rim) — the band and past it — none `--hold-mask` leaves out, none its outline over the band (which goes
    with the layer)."""
    return sets["below"] & ~sets["edge"] & (rim_distance(sets["region"] | sets["soft"], reach + 1, reach + 1) < 1)


def own_outline(region: np.ndarray, solid: np.ndarray, clear: np.ndarray, left_out: np.ndarray, steps: int) -> np.ndarray:
    """`region` grown over the outline its mask leaves out (`--region-own-outline G`, with a membrane): G 4-neighbour steps
    through the cell's solid pixels within G 4-neighbour steps of a clear one (`clear`), never under its lower edge's line
    (in every column, under the lowest pixel of the region's nearest column as it stands before), never a pixel
    `--hold-mask` leaves out (`left_out`)."""
    h, w = region.shape
    xs = np.nonzero(region.any(axis=0))[0]
    if not len(xs):
        return region.copy()
    lowest = {int(x): int(np.nonzero(region[:, x])[0].max()) for x in xs}
    under = np.arange(h)[:, None] > np.array([lowest[min(lowest, key=lambda q: abs(q - x))] for x in range(w)])[None, :]
    zone = solid & ~left_out & ~under & ~region & _grow4(clear, steps)
    grown = region.copy()
    for _ in range(steps):
        grown = grown | (_grow4(grown, 1) & zone)
    return grown


def membrane_move(free: np.ndarray, figure: np.ndarray, own: np.ndarray, outline: np.ndarray,
                  move: tuple[np.ndarray, np.ndarray], reach: int) -> tuple[np.ndarray, np.ndarray]:
    """The move of the pixels `free` as a membrane stretched between the region's own move (`move`, across and down, on
    `own` — the region and its soft edge — and on its outline over the band, `outline`) and none on the rest of the figure
    (`figure`: the pixels not clear), the air none of them. Each free pixel starts at the move of the nearest pixels of
    `own` (`_spread`, over reach + 1 steps; its own move where none reaches it), falling as cos² of its distance to them
    over reach + 1 px (`rim_distance`), and is then the mean of its 4 neighbours' that are the figure, all of them at
    once, pass after pass, until a pass moves none of them MEMBRANE_STEP px or more (MEMBRANE_PASSES at most). The move
    across and down: the membrane's on the free pixels, `move`'s elsewhere."""
    h, w = free.shape
    falls = np.cos(0.5 * math.pi * rim_distance(own, reach + 1, reach + 1)) ** 2
    start = [np.where(free, _spread(way, own, free, reach + 1) * falls, way) for way in move]
    if not free.any():
        return start[0], start[1]
    fixed = own | outline
    ux, uy = (np.where(free, way, np.where(fixed, way, 0.0)) for way in start)
    # The passes read only the free pixels and their neighbours: the box round them, a px past it.
    ys, xs = np.nonzero(free)
    y0, y1, x0, x1 = max(int(ys.min()) - 1, 0), min(int(ys.max()) + 2, h), max(int(xs.min()) - 1, 0), min(int(xs.max()) + 2, w)
    box = (slice(y0, y1), slice(x0, x1))
    inside, bx, by = free[box], ux[box], uy[box]
    bh, bw = inside.shape
    four = ((-1, 0), (1, 0), (0, -1), (0, 1))
    near = np.pad(figure, 1)[y0:y1 + 2, x0:x1 + 2]
    near = [near[1 + oy:1 + oy + bh, 1 + ox:1 + ox + bw] for oy, ox in four]
    count = np.maximum(sum(n.astype(float) for n in near), 1.0)

    def mean(u: np.ndarray) -> np.ndarray:  # each pixel's figure 4-neighbours' mean
        p = np.pad(u, 1)
        return sum(np.where(n, p[1 + oy:1 + oy + bh, 1 + ox:1 + ox + bw], 0.0) for n, (oy, ox) in zip(near, four)) / count

    for _ in range(MEMBRANE_PASSES):
        nx, ny = np.where(inside, mean(bx), bx), np.where(inside, mean(by), by)
        done = max(float(np.abs(nx - bx).max()), float(np.abs(ny - by).max())) < MEMBRANE_STEP
        bx, by = nx, ny
        if done:
            break
    ux, uy = ux.copy(), uy.copy()
    ux[box], uy[box] = bx, by
    return np.where(free, ux, start[0]), np.where(free, uy, start[1])


def _spread(value: np.ndarray, start: np.ndarray, into: np.ndarray, steps: int) -> np.ndarray:
    """`value` carried from the pixels `start` into those of `into`, nearest first: `steps` + 1 times over, each pixel of
    `into` not yet reached beside one that is (8-neighbour) takes the mean of their values; one never reached keeps its
    own."""
    h, w = value.shape
    out, got = value.copy(), start.copy()
    for _ in range(steps + 1):
        total, count = np.zeros((h, w)), np.zeros((h, w))
        pv, pg = np.pad(out, 1), np.pad(got, 1)
        for oy in (-1, 0, 1):
            for ox in (-1, 0, 1):
                if oy or ox:
                    near = pg[1 + oy:1 + oy + h, 1 + ox:1 + ox + w]
                    total += np.where(near, pv[1 + oy:1 + oy + h, 1 + ox:1 + ox + w], 0.0)
                    count += near
        new = into & ~got & (count > 0)
        if not new.any():
            break
        out, got = np.where(new, total / np.maximum(count, 1), out), got | new
    return out


def lay_over(cell: Image.Image, drawn: Image.Image, field: tuple[np.ndarray, np.ndarray, np.ndarray],
             sets: dict[str, np.ndarray]) -> tuple[Image.Image, np.ndarray, np.ndarray, np.ndarray]:
    """`drawn` — the cell moved by `field` round the held pixels but the band (`draw_held`: the air and where the region
    lifts off the figure under it are read as the move reads them, the band under it showing there) — with the region laid
    over the band (`--region-over`, `layer_sets`): the region's own pixels (it, its soft edge and its outline against the
    air, premultiplied) read at the move, over what they leave of the band's own pixel (the air, under the outline). Where
    the layer lands on a band pixel (alpha over 0), and on its outline, that pixel is the layer; where it does not, the
    band pixel keeps its bytes. Also the pixels of the band the layer lands on (`laid`), the band's it covers half or more
    (`over`, its outline not counted) and the region's pixels whose read falls under half on it and its soft edge and half
    or more on the figure under its lower edge (`lifted`)."""
    as_cut = np.asarray(cell.convert("RGBA"))
    src = as_cut.astype(np.float64)
    out = np.asarray(drawn.convert("RGBA")).astype(np.float64)
    h, w = src.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    _, move_x, move_y = field
    sx, sy = xx - move_x, yy - move_y
    region, soft, band, edge = sets["region"], sets["soft"], sets["band"], sets["edge"]
    pm = src.copy()
    pm[..., :3] *= pm[..., 3:4] / 255
    layer = pm * (region | soft | edge)[..., None]
    read = np.stack([_bilinear(layer[..., i], sx, sy) for i in range(4)], axis=-1)
    laid = (band & (read[..., 3] > 0)) | edge
    over = band & ~edge & (read[..., 3] / 255 >= 0.5)
    both = read + (1 - read[..., 3:4] / 255) * pm * (~edge)[..., None]
    alpha = np.clip(both[..., 3:4], 0, 255)
    both[..., :3] = np.where(alpha > 0, both[..., :3] * 255 / np.maximum(alpha, 1e-3), 0)
    out[laid] = both[laid]
    out = np.clip(np.round(out), 0, 255).astype(np.uint8)
    kept = band & ~laid
    out[kept] = as_cut[kept]
    ours = np.clip(_bilinear((region | soft).astype(np.float64), sx, sy), 0, 1)
    lifted = region & (ours < 0.5) & (_bilinear(sets["under"].astype(np.float64), sx, sy) >= 0.5)
    return Image.fromarray(out, "RGBA"), laid, over, lifted


def cover_depth(over: np.ndarray, mine: np.ndarray) -> float:
    """How far past the region's own pixels (`mine`: it and its soft edge as cut) the farthest pixel its layer covers
    (`over`) lies, straight-line px: how deep the layer goes over the figure under it, not how many rows of a column (a
    steep lower edge moved sideways covers many rows of one column but little depth). 0 where it covers none."""
    if not over.any() or not mine.any():
        return 0.0
    oy, ox = np.nonzero(over)
    ry, rx = np.nonzero(mine)
    return round(float(np.sqrt((oy[:, None] - ry[None, :]) ** 2 + (ox[:, None] - rx[None, :]) ** 2).min(axis=1).max()), 3)


def still_strip(region: np.ndarray, alpha: np.ndarray, left_out: np.ndarray) -> list[int]:
    """Per row of the region, at each end of each run of it, how many solid pixels lie outward before the figure's side
    there — the air (a pixel not solid) or a pixel `--hold-mask` names (an arm) — none of them the region's: the strip a
    region drawn short of its side leaves standing beside it (0: it reaches its side). An end whose way out meets the
    region again (between two parts of it) or the cell's edge has no side and is not read."""
    solid = alpha >= ALPHA_SOLID
    w = region.shape[1]
    widths = []
    for y in np.nonzero(region.any(axis=1))[0]:
        row = region[y]
        for x in np.nonzero(row)[0]:
            for way in (-1, 1):
                if 0 <= x + way < w and row[x + way]:
                    continue
                q = x + way
                while 0 <= q < w and solid[y, q] and not left_out[y, q] and not row[q]:
                    q += way
                if 0 <= q < w and not row[q]:
                    widths.append(int(abs(q - x)) - 1)
    return widths


def tip_shape(source: np.ndarray, drawn: np.ndarray, mine: np.ndarray, move: tuple[np.ndarray, np.ndarray]) -> dict[str, Any] | None:
    """The region's tip in a cell as the ledger reads it (`source`, `drawn`: the cell as cut and as drawn, RGBA; `mine`: the
    region and its soft edge; `move`: the field, across and down): the side — the TIP_COLS outermost columns of `mine`,
    right or left — more of whose pixels touch the air (the right on a tie), and the window of those columns and their
    rows with TIP_AIR px of the air round them. On the ring of pixels within TIP_RING px of the source's outline against
    the air in the window, none within TIP_OFF px of a solid pixel not `mine`, how far the drawn outline is from the
    source's moved by the shift that fits it best: Σ|α_drawn(x) − α_source(x − t)| over the ring at its least over t
    (whole px within TIP_SEARCH, then a quarter and a twentieth of a px round the best), over Σ|∇α_source| on it — the mean
    px the tip's outline is off its own shape once its move is taken away (`shape_px`; 0: the tip moved whole); that t
    (`shift`); and the mean move of the tip's own solid pixels in the window (`move_mean`, the field's: a shift fits a
    straight outline anywhere along it, so it is no measure of the move). None where `mine` holds no pixel."""
    xs = np.nonzero(mine.any(axis=0))[0]
    if not len(xs):
        return None
    h, w = mine.shape
    a_src, a_out = source[..., 3] / 255.0, drawn[..., 3] / 255.0
    touching = mine & _grow8(source[..., 3] == 0, 1)
    cols = {"right": np.arange(w) >= xs.max() - TIP_COLS + 1, "left": np.arange(w) <= xs.min() + TIP_COLS - 1}
    side = max(("right", "left"), key=lambda s: int((touching & cols[s][None, :]).sum()))  # the right on a tie
    rows = np.nonzero((mine & cols[side][None, :]).any(axis=1))[0]
    x0, x1 = ((int(xs.max()) - TIP_COLS + 1, int(xs.max()) + 1 + TIP_AIR) if side == "right"
              else (int(xs.min()) - TIP_AIR, int(xs.min()) + TIP_COLS))
    box = [x0, int(rows.min()) - TIP_AIR, x1, int(rows.max()) + 1 + TIP_AIR]
    window = np.zeros((h, w), bool)
    window[max(0, box[1]):min(h, box[3]), max(0, box[0]):min(w, box[2])] = True
    solid = a_src >= 0.5
    ring = _grow8(solid & _grow8(~solid, 1), TIP_RING) & window & ~_grow8(solid & ~mine, TIP_OFF)
    own = window & mine & (source[..., 3] >= ALPHA_SOLID)
    record = {"side": side, "box": box, "ring_px": int(ring.sum()), "shape_px": None, "shift": None,
              "move_mean": round(float(np.hypot(*move)[own].mean()), 4) if own.any() else 0.0}
    ys, xs_ = np.nonzero(ring)
    if not len(ys):
        return record
    gy, gx = np.gradient(a_src)
    length = float(np.hypot(gx, gy)[ys, xs_].sum())
    best: tuple[float, float, float] | None = None
    for step, span, centre in ((1.0, TIP_SEARCH, (0.0, 0.0)), (0.25, 1.0, None), (0.05, 0.25, None)):
        cu, cv = centre if centre is not None else best[:2]
        for u in np.arange(cu - span, cu + span + 1e-9, step):
            for v in np.arange(cv - span, cv + span + 1e-9, step):
                e = float(np.abs(a_out[ys, xs_] - _bilinear(a_src, xs_ - u, ys - v)).sum())
                if best is None or e < best[2]:
                    best = (float(u), float(v), e)
    return {**record, "shape_px": round(best[2] / max(length, 1e-6), 4), "shift": [round(best[0], 2), round(best[1], 2)]}


def move_steps(move_x: np.ndarray, move_y: np.ndarray, shown: np.ndarray) -> tuple[list[Any] | None, list[Any] | None]:
    """The most the drawn move (`move_x`, `move_y`: the move each pixel's read took, 0 on what is kept) steps between two
    pixels side by side, and between two one over the other, both shown in the cell as cut (`shown`): |m(p) − m(q)| px a
    px, and where (the left, the upper of the two); None where no two are. What `--region-shear` bounds, read off what is
    drawn."""
    found: list[list[Any] | None] = []
    for axis in (1, 0):
        step = np.hypot(np.diff(move_x, axis=axis), np.diff(move_y, axis=axis))
        both = (shown[:, 1:] & shown[:, :-1]) if axis == 1 else (shown[1:] & shown[:-1])
        if not both.any():
            found.append(None)
            continue
        y, x = np.unravel_index(int(np.argmax(np.where(both, step, -1.0))), step.shape)
        found.append([round(float(step[y, x]), 4), int(x), int(y)])
    return found[0], found[1]


def _masked(answer: Answer, up: Banded | None, whole: tuple[np.ndarray, np.ndarray], reference: Image.Image | None,
            verb: str) -> Answer:
    """`answer` with its region given as a mask (`--region-mask`): one mask, drawn on the cell the regions were read on
    (`_band_base`), trimmed to its crease where asked (`crease_of`), worn in by `--region-shrink` px (`_shrink4`) and
    carried into every cell by the solid pixels of the mask as trimmed (`carry_region`); or a mask for each cell, trimmed
    and worn in on its own cell. The rim is read off the move the fold rule reads (`whole`, at gain 1) at the gain the
    regions move by (`rim_bands`); `up` is the upper yield (`--band-up`; None: none); and the ledger of every cell
    (`region_ledger`). Refused where the masks leave nothing to move, and where the move folds the picture all the same."""
    mask, cells = answer.ask.mask, answer.cells
    assert mask is not None
    w, h = cells[0].size
    for given in mask.masks:
        if given.shape != (h, w):
            raise SystemExit(f"{verb}: --region-mask is {given.shape[1]}x{given.shape[0]}, the cells are {w}x{h}: a mask is "
                             "drawn on a cell, pixel for pixel")
    if mask.per_cell and len(mask.masks) != len(cells):
        raise SystemExit(f"{verb}: --region-mask holds {len(mask.masks)} masks and there are {len(cells)} cells: a folder gives "
                         "one mask per cell, in name order")

    def shaped(given: np.ndarray, cell: Image.Image) -> tuple[np.ndarray, np.ndarray, dict[str, Any] | None]:
        trimmed, crease = crease_of(cell, given) if mask.crease else (given, None)
        return trimmed, _shrink4(trimmed, mask.shrink), crease

    if mask.per_cell:
        made = [shaped(given, cell) for given, cell in zip(mask.masks, cells)]
        base_shift, matched, carried = (0.0, 0.0), 0, None
    else:
        base, base_shift, base_index, lays = _band_base(answer, reference)
        made = [shaped(mask.masks[0], base)]
        by = made[0][0] & (np.asarray(base.getchannel("A")) >= ALPHA_SOLID)
        if not by.any():
            raise SystemExit(f"{verb}: --region-mask holds no solid pixel of the cell it was drawn on: nothing of the region to "
                             "carry")
        matched = int(by.sum())
        carried = carry_region(_whole_premultiplied(base), cells, by, base_shift, lays, base_index)
    worn = [one for _, one, _ in made]
    if not any(one.any() for one in worn):
        raise SystemExit(f"{verb}: --region-shrink {mask.shrink} wears the region mask away: no pixel of it is left to move")
    move = (answer.gain * whole[0], answer.gain * whole[1])
    sheared = None if mask.shear is None else shear_floor(*move, mask.shear)
    rim = rim_bands(*move, mask.rim, sheared)
    left_out = None if not mask.over or mask.hold is None else _hold_of(answer, mask.hold, reference, 0, verb)
    masked = Masked(mask, worn, None if not mask.crease else [crease for _, _, crease in made],
                    (float(base_shift[0]), float(base_shift[1])), matched, carried, math.ceil(answer.reach) + REGION_AIR, rim, up,
                    sheared=sheared, left_out=left_out)
    answer = dataclasses.replace(answer, mask=masked)
    ledger = region_ledger(answer)
    if min(ledger["jacobian_min"]) <= 0:
        k = int(np.argmin(ledger["jacobian_min"]))
        if mask.membrane is not None and ledger["membrane_folded_px"][k]:
            raise SystemExit(f"{verb}: --region-membrane {mask.membrane} hangs a move of {answer.reach:.1f} px over "
                             f"{mask.membrane + 1} px under the region's lower edge, and cell {k}'s move folds the picture there "
                             f"(a pixel takes {ledger['jacobian_min'][k]:g} of its area): a longer membrane, or a smaller move — "
                             f"nothing was written")
        raise SystemExit(f"{verb}: --region-mask's rim ({rim[0]} px across, {rim[1]} down) keeps a move of {answer.reach:.1f} px "
                         f"taking at most {RIM_SLOPE:g} of a pixel's area, and cell {k}'s move folds the picture all the same (a "
                         f"pixel takes {ledger['jacobian_min'][k]:g} of its area); this is the engine's fault — nothing was written")
    return dataclasses.replace(answer, mask=dataclasses.replace(masked, ledger=ledger))


def region_ledger(answer: Answer) -> dict[str, Any]:
    """Per cell, what a region mask moved and held and how its move went (`--region-mask`), each count a list over the
    cells with its largest beside it:
    - `region_px`, `soft_px`, `air_px`, `held_px`: the region's pixels, its soft edge, the clear pixels it may move into and
      the visible pixels held (`region_sets`); `held_changed_px`: held pixels the drawn cell does not keep byte for byte;
    - `move_most`: the most a pixel of the region moves, and where (px, x, y; None where none moves), and `move_most_max`;
    - `pressed_px`, `stretched_px`: pixels of the drawn cell, solid, moved and not held, left with under PRESSED or over
      STRETCHED of their area, the box they lie in, and the least and the most area with where it is (`area_least`,
      `area_most`), as `band_ledger` counts them; `folded_px`: those whose move takes 0 or less of the source's area
      (`jacobian`: the picture folds), and `jacobian_min`, the least area any of them takes — every read takes its whole
      move, so the move and the read are one field;
    - `read_held_weight`: the most weight a read's four pixels put on held pixels (laid over the figure under it, held
      pixels but the band), and where (weight, x, y; None where no read takes one), and `read_held_half_px`: reads that put
      half or more on them;
    - `renormalised_px`, `walked_px`: reads that took a held pixel and were read from the others, and those walked back
      (`draw_held`), which the rim keeps at 0 — laid over the figure under it, but in the air, which is read too: a read
      there may take the held figure beside it past the band.
    Laid over the figure under it (`--region-over`, `layer_sets`, `lay_over`), also:
    - `band_px`, `over_px`, `edge_px`, `lifted_px`, `left_out_px`: the band under its lower edge, the band's pixels the
      layer covers half or more, the outline that goes with the layer, the region's pixels whose read falls on the figure
      under it where it lifts (`lay_over`), and the mask's pixels `--hold-mask` names, which the region keeps;
      `cover_depth_px`: how deep the layer covers the band
      (`cover_depth`); the held pixels are those the drawn cell keeps, the band the layer does not land on among them;
    - `edge_folded_px`: folds within a px (4-neighbour) of a held pixel, where the layer stops and the band keeps its
      bytes — the layer's edge, not the move's, so they are counted apart and the rest is read as ever.
    With a membrane (`--region-membrane`), also `membrane_px`: the pixels under its lower edge it moves (`membrane_sets`),
    `membrane_most`: the most one of them moves, and where (px, x, y; None where none moves), and `membrane_folded_px`:
    the folds on it or a px (8-neighbour) from it — a membrane too short for the move it hangs.
    With `--region-over` or `--region-shear` (`RegionMask.edges`), the region's edges:
    - `step_across_most`, `step_down_most`: the most the drawn move steps between two pixels side by side and one over the
      other, px a px, and where (`move_steps`; laid over the figure under it, the band not read: the layer slides over it),
      and their largest (`step_across_max`, `step_down_max`);
    - `still_strip`: the strip a region short of its side leaves standing (`still_strip`): the median and the most of its
      widths over the rows' ends, how many ends are read and how many of them are no wider than the rim across
      (`covered`); None where no end is read;
    - `tip`: its tip's side, window, shape and move (`tip_shape`), and `tip_shape_max` (px, cell) and `tip_move_max`
      (px, cell): the most of each over the cells."""
    assert answer.mask is not None
    w, h = answer.cells[0].size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    inner = np.zeros((h, w), bool)
    inner[1:-1, 1:-1] = True
    over, edges, membrane = answer.mask.asked.over, answer.mask.asked.edges(), answer.mask.asked.membrane is not None
    keys = ("region_px", "soft_px", "air_px", "held_px", "held_changed_px", "move_most", "pressed_px", "pressed_box",
            "area_least", "stretched_px", "stretched_box", "area_most", "folded_px", "jacobian_min", "read_held_weight",
            "read_held_half_px", "renormalised_px", "walked_px") \
        + (("band_px", "over_px", "edge_px", "lifted_px", "left_out_px", "cover_depth_px", "edge_folded_px") if over else ()) \
        + (("membrane_px", "membrane_most", "membrane_folded_px") if membrane else ()) \
        + (("step_across_most", "step_down_most", "still_strip", "tip") if edges else ())
    ledger: dict[str, list[Any]] = {key: [] for key in keys}
    for k, cell in enumerate(answer.cells):
        drawn, read = answer.masked_cell(k)
        region, held, field = read["region"], read["held"], read["field"]
        for key in ("region", "soft", "air", "held"):
            ledger[f"{key}_px"].append(int(read[key].sum()))
        source, out = np.asarray(cell.convert("RGBA")), np.asarray(drawn.convert("RGBA"))
        ledger["held_changed_px"].append(int((held & np.any(source != out, axis=-1)).sum()))
        ledger["renormalised_px"].append(int(read["renormalised"].sum()))
        ledger["walked_px"].append(int(read["walked"].sum()))
        if over:
            for key, where in (("band", read["band"]), ("over", read["over"]), ("edge", read["edge"]),
                               ("lifted", read["lifted"]), ("left_out", read["left_out"])):
                ledger[f"{key}_px"].append(int(where.sum()))
            ledger["cover_depth_px"].append(cover_depth(read["over"], region | read["soft"]))
        if membrane:
            moved_by = read["membrane"]
            ledger["membrane_px"].append(int(moved_by.sum()))
            if moved_by.any() and field is not None:
                size = np.where(moved_by, np.hypot(field[1], field[2]), -1.0)
                y, x = divmod(int(np.argmax(size)), w)
                ledger["membrane_most"].append([round(float(size[y, x]), 4), x, y])
            else:
                ledger["membrane_most"].append(None)
        if edges:
            # Laid over the figure under it, the layer slides over the band: what steps there is where it covers it, not
            # a shear, so the band's pixels are not read.
            shown, mine = (source[..., 3] > 0) & ~(read["band"] if over else np.zeros((h, w), bool)), region | read["soft"]
            across, down = move_steps(read["read_x"], read["read_y"], shown)
            ledger["step_across_most"].append(across)
            ledger["step_down_most"].append(down)
            widths = still_strip(region, source[..., 3], answer.mask.left_out_in(k))
            ledger["still_strip"].append(None if not widths else {
                "median": float(np.median(widths)), "most": max(widths), "ends": len(widths),
                "covered": sum(width <= answer.mask.rim[0] for width in widths)})
            moving = (np.zeros((h, w)), np.zeros((h, w))) if field is None else field[1:]
            ledger["tip"].append(tip_shape(source, out, mine, moving))
        if field is None:
            if over:
                ledger["edge_folded_px"].append(0)
            if membrane:
                ledger["membrane_folded_px"].append(0)
            for key, empty in (("move_most", None), ("pressed_px", 0), ("pressed_box", None), ("area_least", None),
                               ("stretched_px", 0), ("stretched_box", None), ("area_most", None), ("folded_px", 0),
                               ("jacobian_min", 1.0), ("read_held_weight", None), ("read_held_half_px", 0)):
                ledger[key].append(empty)
            continue
        inside, move_x, move_y = field
        size = np.hypot(move_x, move_y)
        if region.any():
            y, x = divmod(int(np.argmax(np.where(region, size, -1.0))), w)
            ledger["move_most"].append([round(float(size[y, x]), 4), x, y])
        else:
            ledger["move_most"].append(None)
        taken = np.ones((h, w))
        taken[1:-1, 1:-1] = jacobian(move_x, move_y)
        moved = inner & inside & ~held & (out[..., 3] >= ALPHA_SOLID)
        if over:
            # Where the layer stops and the band keeps its bytes the move is 0 beside a pixel it moves: the layer's
            # edge, not the move's.
            at_edge = moved & _grow4(held, 1)
            ledger["edge_folded_px"].append(int((at_edge & (taken <= 0)).sum()))
            moved &= ~at_edge
        counted = moved & (taken > 0)
        area = 1 / np.where(taken > 0, taken, 1.0)  # each pixel's area: 1 over the source's it takes
        pressed, stretched = counted & (area < PRESSED), counted & (area > STRETCHED)
        for key, where in (("pressed", pressed), ("stretched", stretched)):
            ledger[f"{key}_px"].append(int(where.sum()))
            ledger[f"{key}_box"].append(_box(where))
        for key, pick, empty in (("area_least", np.argmin, np.inf), ("area_most", np.argmax, -np.inf)):
            if counted.any():
                y, x = divmod(int(pick(np.where(counted, area, empty))), w)
                ledger[key].append([round(float(area[y, x]), 4), x, y])
            else:
                ledger[key].append(None)
        ledger["folded_px"].append(int((moved & (taken <= 0)).sum()))
        ledger["jacobian_min"].append(round(float(taken[moved].min()), 4) if moved.any() else 1.0)
        if membrane:
            ledger["membrane_folded_px"].append(int((moved & (taken <= 0) & _grow8(read["membrane"], 1)).sum()))
        # The weight a read puts on held pixels, as `_sample` reads them: the rim keeps it under a half. Laid over the
        # figure under it, the region reads the band where it lifts: the held pixels but the band.
        unread = read.get("unread", held)
        on_held = np.where(read["readers"], _bilinear(unread.astype(np.float64), xx - move_x, yy - move_y), 0.0)
        if (on_held > 0).any():
            y, x = divmod(int(np.argmax(on_held)), w)
            ledger["read_held_weight"].append([round(float(on_held[y, x]), 4), x, y])
        else:
            ledger["read_held_weight"].append(None)
        ledger["read_held_half_px"].append(int((on_held >= 0.5).sum()))
    out: dict[str, Any] = {}
    for key in keys:
        out[key] = ledger[key]
        if key.endswith("_px"):
            out[f"{key}_max"] = max(ledger[key], default=0)
    out["move_most_max"] = max((most[0] for most in ledger["move_most"] if most is not None), default=0.0)
    if edges:
        for way in ("across", "down"):
            out[f"step_{way}_max"] = max((most[0] for most in ledger[f"step_{way}_most"] if most is not None), default=0.0)
        for key, of in (("tip_shape_max", "shape_px"), ("tip_move_max", "move_mean")):
            found = [(tip[of], k) for k, tip in enumerate(ledger["tip"]) if tip is not None and tip[of] is not None]
            out[key] = None if not found else list(max(found, key=lambda t: t[0]))  # the first cell on a tie
    return out


def region_notes(record: dict[str, Any], verb: str = VERB, at: str = f"{loop_mod.SWAY_RECORD}.region_mask") -> list[str]:
    """What to say of a region mask's record (`--region-mask`), a line each, as warnings: the cells it kept the carry of
    the cell before in and why, the cells `--band-up` carried a region's ellipse by the lay in and why (and, laid over the
    figure under it, the pixels `--hold-mask` leaves out of it, `--region-over`), and every count of
    the ledger that is not 0 in some cell — in how many cells, up to how many pixels and in which cell, and where in the
    record (`at`)."""
    masked, notes = record["region_mask"], []
    if masked["kept"]:
        whys = "; ".join(f"cell {e['cell']}: {e['why']}" for e in masked["kept"])
        notes.append(f"{verb}: warning: --region-mask keeps the carry of the cell before in {len(masked['kept'])} cells, its own "
                     f"pixels found surely neither round where that cell had them nor round the lay ({whys}) — {at}.kept")
    by_region: dict[int, list[dict[str, Any]]] = {}
    for entry in [] if masked["up"] is None else masked["up"]["lay"]:
        by_region.setdefault(entry["region"], []).append(entry)
    for i, entries in sorted(by_region.items()):
        name = "--region " + ",".join(f"{v:g}" for v in record["regions"][i])
        whys = "; ".join(f"cell {e['cell']}: {e['why']}" for e in entries)
        notes.append(f"{verb}: warning: --band-up carries {name} by the body's lay in {len(entries)} cells, its own pixels not "
                     f"found surely ({whys}) — {at}.up.lay")
    said = {"held_changed_px": "held pixels do not keep their bytes (the engine's fault)",
            "pressed_px": f"moved pixels are left under {PRESSED:g} of their area",
            "stretched_px": f"moved pixels are left over {STRETCHED:g} of their area",
            "read_held_half_px": "reads put half their weight or more on held pixels",
            "renormalised_px": "reads took a held pixel and were read from the others",
            "walked_px": "reads took a held pixel and were walked back"}
    if "over" in masked:
        left_out = masked["over"]["left_out"]
        if left_out is not None and left_out["lay"]:
            whys = "; ".join(f"cell {e['cell']}, piece {e['piece']}: {e['why']}" for e in left_out["lay"])
            notes.append(f"{verb}: warning: --hold-mask (what --region-over leaves out) is carried by the body's lay in "
                         f"{len({e['cell'] for e in left_out['lay']})} cells, its own pixels not found surely ({whys}) — "
                         f"{at}.over.left_out.lay")
    return notes + _ledger_notes(masked["ledger"], said, "--region-mask", verb, at)


def _keep_floor(ask: Ask, radii: list[float], gains: list[float], held: list[bool],
                move: tuple[np.ndarray, np.ndarray], verb: str) -> tuple[list[float], list[bool], dict[str, Any]]:
    """The fold rule's gains (`gains`, 0 where `held`) lowered where a region's move would leave a pixel less than
    `--stretch-floor` of its area (`move`: across and down per cell at gain 1), and the record of it. A region is never
    raised; one the floor takes under GAIN_MEASURED is held as the fold rule holds one, and the strip is refused when
    every region is — or, under `--on-fold refuse`, when the floor lowers any."""
    floor, requested = ask.stretch_floor, ask.gain
    per = [stretch_per_gain((region[2], region[3]), *move, flat=flat_of(region)) for region in ask.regions]
    floored = [floor_gain(requested, q, floor) for q, _ in per]
    lowered = [g if hold else min(g, f) for g, f, hold in zip(gains, floored, held)]
    by_floor = [not hold and g < rule for g, rule, hold in zip(lowered, gains, held)]
    if any(by_floor) and ask.on_fold == "refuse":
        least = min(1 - requested * q for (q, _), by in zip(per, by_floor) if by)
        raise SystemExit(f"{verb}: --gain {requested:g} leaves a moved pixel {least:.3f} of its area, under --stretch-floor {floor:g}; "
                         "lower --gain, pass --on-fold lower or give the region larger radii")
    now_held = [hold or (by and g < GAIN_MEASURED) for hold, by, g in zip(held, by_floor, lowered)]
    if all(now_held):
        k = radii.index(max(radii))
        raise SystemExit(f"{verb}: --stretch-floor {floor:g} leaves every region held: to keep it each would move under --gain "
                         f"{GAIN_MEASURED:g} (the mass as measured; the largest region at {lowered[k]:g}); lower the floor or give "
                         "the regions larger radii")
    final = [0.0 if hold else g for g, hold in zip(lowered, now_held)]
    return final, now_held, {
        "policy": STRETCH_POLICY, "floor": floor,
        "regions": [{"per_gain": round(q, 6), "least_cell": cell, "rule_gain": rule, "floor_gain": f, "gain": g, "held": hold,
                     "least_area": 1.0 if hold else round(1 - g * q, 4), "under_measured": not hold and g < GAIN_MEASURED}
                    for (q, cell), rule, f, g, hold in zip(per, gains, floored, final, now_held)],
    }


def sway_loop(loop_dir: Path, regions: list[tuple[float, ...]], *, gain: float | None = None,
              freq: float | None = None, zeta: float | None = None, board: Path | None = None,
              on_fold: str = "refuse", read_on: str | None = None, stretch_floor: float | None = None,
              breathe: Path | None = None, lag: float | None = None, gain_across: float | None = None,
              band: float | str | None = None, band_margin: float | str | None = None,
              band_up: tuple[float, float | str] | str | None = None, hold_mask: Path | None = None,
              region_mask: Path | None = None, region_rim: float | None = None, region_shrink: int | None = None,
              region_crease: bool = False, region_over: bool = False, region_shear: float | None = None,
              region_membrane: int | None = None, region_over_solid: bool = False, region_rim_round: bool = False,
              region_membrane_unpin: bool = False, region_clear_alpha: int | None = None,
              region_floor_beside: int | None = None, region_own_outline: int | None = None,
              read_on_cut: Path | None = None, verb: str = VERB) -> dict[str, Any]:
    request = read_request(loop_dir, regions, gain=gain, freq=freq, zeta=zeta, on_fold=on_fold, read_on=read_on,
                           stretch_floor=stretch_floor, breathe=breathe, lag=lag, gain_across=gain_across, band=band,
                           band_margin=band_margin, band_up=band_up, hold_mask=hold_mask, region_mask=region_mask,
                           region_rim=region_rim, region_shrink=region_shrink, region_crease=region_crease,
                           region_over=region_over, region_shear=region_shear, region_membrane=region_membrane,
                           region_over_solid=region_over_solid, region_rim_round=region_rim_round,
                           region_membrane_unpin=region_membrane_unpin, region_clear_alpha=region_clear_alpha,
                           region_floor_beside=region_floor_beside, region_own_outline=region_own_outline,
                           read_on_cut=read_on_cut, verb=verb)
    loop_dir, name, meta_path, meta = request.loop_dir, request.name, request.meta_path, request.meta
    strip_path = request.strip_path
    source = request.source_path
    if not source.exists():
        shutil.copyfile(strip_path, source)
    data = source.read_bytes()
    answer = solve(request, Image.open(io.BytesIO(data)).convert("RGBA"), strip_name=source.name,
                   strip_sha256=hashlib.sha256(data).hexdigest(), verb=verb)
    cells = answer.cells
    n, (w, h) = len(cells), cells[0].size
    out = [answer.moved(k) for k in range(n)]
    joined = Image.new("RGBA", (w * n, h), (0, 0, 0, 0))
    for k, c in enumerate(out):
        joined.alpha_composite(c, (k * w, 0))
    delay_ms = max(20, round(float(meta["delay_ms"])))
    gif_path, webp_path = loop_dir / f"{name}.gif", loop_dir / f"{name}.webp"
    # Written and checked beside the loop first, each under its own name, and moved over the loop's files
    # only once the record is written too: a run that fails on the way (an animation that fails its check, a
    # record that does not serialise) leaves the strip, its animations and its record as they were. Staged as
    # files, not as payloads in memory (runio.atomic_write_set): img2webp writes a file, and the check reads one.
    stage = Path(tempfile.mkdtemp(prefix=".follow.", dir=loop_dir))
    try:
        staged = {path: stage / path.name for path in (strip_path, gif_path, webp_path, meta_path)}
        joined.save(staged[strip_path])
        save_clean_gif(out, staged[gif_path], duration_ms=delay_ms, loop=0, alpha_threshold=128)
        loop_mod.write_webp(out, staged[webp_path], delay_ms=delay_ms)
        # a cell left as it was beside its twin (a filmed ghost given way to the frame beside it) is one frame of both
        shown = loop_mod.shown_runs(out)
        record = {
            **answer.record(),
            "gif": loop_mod.verify_animation(staged[gif_path], expect_frames=shown, check_stale=False),
            "webp": loop_mod.verify_animation(staged[webp_path], expect_frames=shown, check_stale=True),
        }
        # One record, under the key consumers read it by (loop_mod.SWAY_RECORD); one read under the other key goes.
        kept = {k: v for k, v in meta.items() if k not in loop_mod.SWAY_RECORD_KEYS or k == loop_mod.SWAY_RECORD}
        atomic_write_text(staged[meta_path], json.dumps({**kept, loop_mod.SWAY_RECORD: record}, indent=2) + "\n")
        for path, written in staged.items():  # the record last, as it was written last
            os.replace(written, path)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if board is not None:
        _board(cells, out, answer.dy, board)
        record["board"] = str(board)
    return {"strip": str(strip_path), **record}


def sway_frames(frames: Path, out: Path, regions: list[tuple[float, ...]], *, fps: float | None = None,
                gain: float | None = None, freq: float | None = None, zeta: float | None = None,
                board: Path | None = None, on_fold: str = "refuse", read_on: str | None = None,
                stretch_floor: float | None = None, breathe: Path | None = None, lag: float | None = None,
                gain_across: float | None = None, reference: Path | None = None, band: float | str | None = None,
                band_margin: float | str | None = None, band_up: tuple[float, float | str] | str | None = None,
                hold_mask: Path | None = None, region_mask: Path | None = None, region_rim: float | None = None,
                region_shrink: int | None = None, region_crease: bool = False, region_over: bool = False,
                region_shear: float | None = None, region_membrane: int | None = None, region_over_solid: bool = False,
                region_rim_round: bool = False, region_membrane_unpin: bool = False, region_clear_alpha: int | None = None,
                region_floor_beside: int | None = None, region_own_outline: int | None = None,
                read_on_cut: Path | None = None, verb: str = VERB) -> dict[str, Any]:
    """The sway of a folder of a cycle's frames — a loop's `cycle/`, or what `sprite-gen breathe` wrote from it — at their
    own size and count, past the strip's caps on cells and width: the same frames under the same names, the regions
    (in frame pixels) moved, and `sway.json` beside them, written whole into a new folder (`frames.folder`); the frames
    folder is read and not written. Cell for cell it is the strip's sway (`solve_cells`): a strip cut from these frames
    at scale 1, none left out, its regions moved by its crop origin, at the frame time its meta writes (`frame_ms` of
    `--fps`), moves its cells as these are moved.

    With `reference` (a loop set's first cell, for a stretch of the loop whose regions were read on that cell) the
    regions are read on it and not on the first frame: each frame's body is read from it and the regions carried
    from there, as they are on the loop's own cells."""
    ask = read_ask(regions, gain=gain, freq=freq, zeta=zeta, on_fold=on_fold, read_on=read_on, stretch_floor=stretch_floor,
                   breathe=breathe, lag=lag, gain_across=gain_across, band=band, band_margin=band_margin, band_up=band_up,
                   hold_mask=hold_mask, region_mask=region_mask, region_rim=region_rim, region_shrink=region_shrink,
                   region_crease=region_crease, region_over=region_over, region_shear=region_shear,
                   region_membrane=region_membrane, region_over_solid=region_over_solid, region_rim_round=region_rim_round,
                   region_membrane_unpin=region_membrane_unpin, region_clear_alpha=region_clear_alpha,
                   region_floor_beside=region_floor_beside, region_own_outline=region_own_outline, verb=verb)
    if ask.read_on is not None or read_on_cut is not None:
        raise SystemExit(f"{verb}: --read-on names the strip as cut that the regions were read on; frames are read as they are")
    if ask.breath is not None and fps is not None:
        raise SystemExit(f"{verb}: --fps is the damped mass's frame time; on the breath (--breathe) the part keeps the breath's beat")
    frames = Path(frames)
    folder = folder_mod.open_frames(frames, Path(out), verb)
    cells = folder.images
    frame_ms = None
    if ask.breath is None:
        frame_ms = folder_mod.cycle_frame_ms(frames, len(cells), fps, verb)
        if frame_ms is None:
            raise SystemExit(f"{verb}: --frames {frames}: the damped mass answers the body's motion in time — give --fps (a "
                             "video-loop cycle/ folder has its own in the loop's strip meta), or sway on the breath (--breathe)")
    basis, held = folder_mod.reference_frame(reference, folder, verb)
    answer = solve_cells(ask, cells, frame_ms, verb=verb, reference=None if held is None else (basis, held))
    moved = [answer.moved(k) for k in range(len(cells))]
    record = {"frames": len(cells), "frame_ms": frame_ms, **answer.record(source=str(frames))}
    folder_mod.publish(folder, moved, FRAMES_RECORD, record)
    if board is not None:
        _board(cells, moved, answer.dy, board)
        record["board"] = str(board)
    return {"out": str(folder.out), **record}


def _board(before: list[Image.Image], after: list[Image.Image], dy: np.ndarray, path: Path) -> None:
    """Before and after, side by side on white, at the frames where the part sits lowest and highest."""
    picks = [int(np.argmax(dy)), int(np.argmin(dy))]
    w, h = before[0].size
    board = Image.new("RGB", (w * 4 + 30, h), (128, 128, 128))
    x = 0
    for k in picks:
        for cell in (before[k], after[k]):
            ground = Image.new("RGBA", (w, h), (255, 255, 255, 255))
            ground.alpha_composite(cell)
            board.paste(ground.convert("RGB"), (x, 0))
            x += w + 10
    path.parent.mkdir(parents=True, exist_ok=True)
    board.save(path)


LOOP_DIR_HELP = "a video-loop output directory (after video-cycle-align, if the set is aligned)"


def add_request_arguments(parser: argparse.ArgumentParser, *, loop_dir: bool = True) -> None:
    """What a sway is asked with; `video-sway-inspect` takes the same of a loop directory and is asked alike
    (`video-sway` puts `--loop-dir` beside `--frames` itself)."""
    if loop_dir:
        parser.add_argument("--loop-dir", required=True, type=Path, help=LOOP_DIR_HELP)
    parser.add_argument("--region", action="append", type=parse_region, required=True,
                        help="cx,cy,rx,ry[,flat]: an ellipse over the soft part in the first cell, in cell pixels (repeatable); flat, "
                             f"{FLAT_RANGE[0]:g} to {FLAT_RANGE[1]:g}, is the share of the way from its centre to its rim that moves in "
                             "full, the Breathe layer's chest_flat (0 or left out: the weight falls from the centre)")
    parser.add_argument("--gain", type=float, default=None,
                        help=f"times the physical answer (default {GAIN_DEFAULT:g}; 1 is the mass as measured, 0 leaves the strip as it "
                             f"was); on the breath (--breathe), times the breath's rise less the part's own (default {BREATH_GAIN_DEFAULT:g})")
    parser.add_argument("--gain-across", type=float, default=None,
                        help="the gain across, apart from --gain, which is then the gain down (default: --gain); a region whose gain "
                             "--on-fold lower or --stretch-floor lowers moves across lowered in the same proportion")
    parser.add_argument("--on-fold", choices=ON_FOLD_MODES, default="refuse",
                        help=f"a move that folds a region over itself: refuse (default), or lower — each region takes the largest gain that does "
                             f"not fold it, never under {GAIN_MEASURED:g} (the mass as measured): a region that would have to is held still and "
                             "named in the record, and the strip is refused only when every region is")
    parser.add_argument("--freq", type=float, default=None, help=f"the part's own frequency in Hz (default {FREQ_DEFAULT:g})")
    parser.add_argument("--zeta", type=float, default=None, help=f"damping ratio (default {ZETA_DEFAULT:g}: lags and settles, no ringing)")
    parser.add_argument("--breathe", type=Path, default=None, metavar="BREATHE.JSON",
                        help="sway on the breath: the record sprite-gen breathe writes beside the frames it breathed (smooth). The part "
                             "follows the chest's rise late by --lag instead of answering the body's motion as a damped mass: its offset is "
                             "gain x the chest's rise x (w - w late), w the breath of the record's in-breath share (its config's "
                             "inhale), against the way the chest rises, 0 where the breath rests (the loop's first cell). The record's "
                             "phases must be the ones its config gives, one for each cell swayed; a stretch of a loop (its loop_cells, "
                             "start_cell) is folded as its whole loop is")
    parser.add_argument("--lag", type=float, default=None,
                        help=f"on the breath: the share of a breath the part peaks after the chest, above 0 and under 1/π (default {LAG_DEFAULT:g})")
    parser.add_argument("--read-on", type=parse_read_on, default=None, metavar="SHA256",
                        help="the sha256 of the strip as cut (before any follow-through) the regions were read on: this loop's own is "
                             "read as without it (relation same); the cut this loop was aligned from (video-cycle-align records it) has "
                             "its reading carried cell for cell (transported); a made cell or a cell geometry nothing records is "
                             "refused uncertain, any other strip no-match. Without it nothing of this is read or written")
    parser.add_argument("--stretch-floor", type=float, default=None, metavar="D",
                        help="the least area, 0 to under 1, a moved pixel keeps, read by the way each region moves: each region takes "
                             "the largest gain that keeps it, never above the fold rule's; under it the fold rule's handling stands "
                             f"(held under {GAIN_MEASURED:g}, every region held refused; --on-fold refuse refuses). No default: without "
                             "it the fold rule alone")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--loop-dir", type=Path, help=LOOP_DIR_HELP)
    where.add_argument("--frames", type=Path,
                       help="a folder of a cycle's frames (*.png in name order, one size: a video-loop cycle/, or what sprite-gen "
                            "breathe wrote from it), swayed at their own size and count into --out — past the strip's caps on cells "
                            "and width; the regions are in frame pixels")
    parser.add_argument("--out", type=Path, help=f"with --frames: a new folder for the swayed frames (same names) and {FRAMES_RECORD}")
    parser.add_argument("--fps", type=float, default=None,
                        help="with --frames: their rate, for the damped mass (a video-loop cycle/ folder has its own in the loop's strip "
                             "meta); a frame's time is rounded to hundredths of a ms, as a strip's meta writes it")
    parser.add_argument("--reference", type=Path, default=None, metavar="FRAME.png",
                        help="with --frames: the frame the regions were read on, when it is not the first of them — a loop set's "
                             "first cell, for a way in or out of the loop (sprite-gen breathe --loop-cells --start-cell): each "
                             "frame's body is read from it and the regions carried from there (one size with the frames)")
    add_request_arguments(parser, loop_dir=False)
    parser.add_argument("--read-on-cut", type=Path, default=None, metavar="STRIP",
                        help="with --read-on: the strip it names (its PNG, its record beside it as video-loop writes one), when "
                             "it is another cut of this loop's source clip (a cut again from the same clip, not a new take): "
                             "paired by the source frame its cell 0 is with the cell of this loop's cut that is that frame, the "
                             "regions and the region mask moved onto that cell by the whole px the two crops set it apart, each "
                             "taken on the frame as filmed (less where its cut's anchor moved that frame), pixel for pixel, "
                             "and carried from there (relation recut), each cut read by its record of its cells as video-loop "
                             "writes it (cell_record, bound to its strip's PNG); refused no-match where a cut has no such "
                             "record (made before video-loop wrote one) or it is another strip's, the clip differs, this "
                             "loop's cut does not hold that frame, either cell is not that frame as filmed (the size hold or "
                             "the gait fallback scaled it, or video-loop did not cut it as filmed), the two cells are not the "
                             "same pixels where they overlap at the shift, or the strip is a loop aligned from its cut, "
                             "uncertain where the crops do not set it apart by one whole shift. Off by default")
    parser.add_argument("--band", type=parse_band, default=None, metavar=f"PX|{AUTO}",
                        help="move each region as one mass: 1 over its ellipse grown by --band-margin, falling to 0 (cos²) over PX px "
                             f"past it ({AUTO}: ceil(reach x π/2) + 1, the narrowest the move does not fold), carried into each cell by "
                             "its own pixels (the body's lay where they are not found surely, named in the record and a warning); "
                             "the fold rule reads the band. Off by default: the weight falls from the centre. Not with a region's "
                             "flat share, nor --stretch-floor")
    parser.add_argument("--band-margin", type=parse_band_margin, default=None, metavar=f"PX|{AUTO}",
                        help=f"with --band: how far past the ellipse the weight stays 1, every way (default {AUTO}: the move's reach "
                             "and a pixel, ceil(reach) + 1, so the part is still on the flat where it goes)")
    parser.add_argument("--band-up", type=parse_band_up, default=None, metavar=f"FLAT[,PX|{AUTO}]",
                        help="with --band: above the centre the flat part stops FLAT (over 0, at most 1) of the region's height over "
                             f"it and the weight falls over PX from there ({AUTO}, the default: the band, or to {HANG_PAST:g} px over the "
                             "region's top where that is further): the upper part gives, the rest moves whole. Default: above as below. "
                             "With --region-mask: that upper yield, the band and margin auto, and the rim's weight the lesser")
    parser.add_argument("--hold-mask", type=Path, default=None, metavar="MASK.png|DIR",
                        help="with --band: pixels the band moves round and leaves as they are (an arm hanging over the part, a dress "
                             "below it), a PNG drawn on the cell the regions were read on (alpha, or light where it has none, "
                             f"{ALPHA_SOLID} or more holds), each piece carried into every cell by its own pixels and grown by "
                             f"{HOLD_GROW} px; or a folder of one PNG per cell in name order, held as given. The pixels not opaque round "
                             "it and the air beside it are kept too, and a read headed into them stops short, its share of the move "
                             f"rising by {HOLD_SLOPE:g} a px from them. With --region-mask --region-over: the pixels the region's "
                             "layer leaves out, carried alike. Off by default")
    parser.add_argument("--region-mask", type=Path, default=None, metavar="MASK.png|DIR",
                        help="the region as a mask instead of the ellipse's weight: a PNG drawn on the cell the regions were read on "
                             f"(alpha, or light where it has none, {ALPHA_SOLID} or more), carried into every cell by its own pixels "
                             "(round where the cell before had them, then round the lay; where neither settles, where the cell before "
                             "had them, named in the record and a warning), or a folder of one PNG per cell in name order. Every "
                             f"visible pixel outside it and its soft edge keeps its bytes; it moves into the air within the move's reach "
                             f"and {REGION_AIR} px; the weight falls to 0 toward the held pixels over a rim (sin², the fold rule's least "
                             f"band across and down widened until a move takes at most {RIM_SLOPE:g} of a pixel's area), and with "
                             "--band-up the lesser of that and its upper yield. The ellipses still say each region's gain. Off by "
                             "default. Not with --band, a region's flat share, --stretch-floor, nor --hold-mask but with "
                             "--region-over")
    parser.add_argument("--region-rim", type=float, default=None, metavar="SCALE",
                        help=f"with --region-mask: the rim starts at SCALE (1 or more) times the fold rule's least band (default "
                             f"{RIM_DEFAULT:g}; 1.5 a wider, gentler rim, a smaller part moving whole)")
    parser.add_argument("--region-shrink", type=int, default=None, metavar="PX",
                        help=f"with --region-mask: wear the mask in by PX px (4-neighbour steps) before it moves, so a mask drawn a "
                             f"little large holds what is outside the part (default {SHRINK_DEFAULT}; 0: as given)")
    parser.add_argument("--region-crease", action="store_true",
                        help=f"with --region-mask: before it is worn in, end the mask at the crease under it — in each column whose "
                             f"lowest pixel lies on more of the figure, the darkest solid pixel {CREASE_UP} px above to {CREASE_DOWN} "
                             f"below it, the median over {2 * CREASE_SPAN + 1} columns; a column over the air is left as it is. "
                             "--region-shrink then wears it in; with --region-over the region reaches down to the crease again in "
                             "every cell. Off by default")
    parser.add_argument("--region-over", action="store_true",
                        help="with --region-mask: lay the region over the figure under it — what lies under its lower edge (in its "
                             f"columns and the move's reach and {REGION_AIR} px past them) is no anchor of the rim, so the lower "
                             "edge moves with the rest, and the band of it within as far is drawn over by the region's own pixels "
                             "by their alpha and keeps its bytes where they do not land; the air and where the region lifts are "
                             "drawn by the move, the figure under it showing there. Its soft edge goes with it beside the figure "
                             "too, and its outline against the air with the layer; with --region-crease it reaches down to the "
                             "crease in every cell. --hold-mask names pixels it leaves out (an arm): where the mask does not cover "
                             "them never the region's, never drawn over, an anchor of the rim; where it does, they move with the "
                             "region. Off by default")
    parser.add_argument("--region-over-solid", action="store_true",
                        help="with --region-over: of what lies under the region's lower edge only the solid pixels (alpha "
                             f"{ALPHA_SOLID} or more) are no anchor of the rim and drawn over; the rest of the figure there, not "
                             "opaque, stays where it is and holds the rim, never drawn over, and the region's soft edge goes with "
                             "it under its lower edge too, beside the figure under it. Off by default")
    parser.add_argument("--region-rim-round", action="store_true",
                        help="with --region-over: the held pixels beside the region (in the columns it does not reach, above "
                             "its lower edge) hold the rim round, the wider of its two widths each way, so where they stop over "
                             "what lies under its lower edge (which holds none of it) the move rises down as gently as across, "
                             "not within the few rows the rim's height gives it. Off by default")
    parser.add_argument("--region-shear", type=float, default=None, metavar="S",
                        help="with --region-mask: the most a move one way may rise across the rim the other way, px a px (over 0): "
                             "the rim across at least ceil(π/2 x the most move down / S) and down at least ceil(π/2 x the most "
                             "move across / S). Without it the rim reads each way by the move along it alone. The ledger reads the "
                             "steps the drawn move takes. No default")
    parser.add_argument("--region-membrane", type=int, default=None, metavar="N",
                        help="with --region-over: what lies under the region's lower edge within N + 1 px of it and its soft edge "
                             "(the band and past it, solid or not; not what --hold-mask leaves out, not its outline over the band) "
                             "moves as a membrane, each pixel's move the mean of its 4 neighbours' that are the figure, between the "
                             "region's own and none on the rest of the figure, and is drawn by that move instead of laid over: the "
                             "figure under the region stretches with it instead of the region sliding over it. Off by default")
    parser.add_argument("--region-membrane-unpin", action="store_true",
                        help="with --region-membrane: what the membrane moves is no anchor of the rim either, so the region's "
                             "move does not fall to none at its first rows under the lower edge. Nothing without a membrane. "
                             "Off by default")
    parser.add_argument("--region-clear-alpha", type=int, default=None, metavar="A",
                        help=f"with --region-membrane: a pixel under alpha A (1 to {ALPHA_SOLID - 1}) is read as clear by what "
                             "the region moves and holds and by the membrane, its bytes kept: a speck of dust beside the figure "
                             "holds nothing. Nothing without a membrane. No default")
    parser.add_argument("--region-floor-beside", type=int, default=None, metavar="N",
                        help="with --region-membrane: beside the region's columns its lower edge is the lowest pixel of its "
                             "columns within N px of that side, not that of its nearest column alone. Nothing without a membrane. "
                             "No default")
    parser.add_argument("--region-own-outline", type=int, default=None, metavar="G",
                        help="with --region-membrane: the region grows G 4-neighbour steps over the cell's solid pixels within G "
                             "px of a clear one, above its lower edge's line and never what --hold-mask leaves out: the outline its "
                             "mask, carried into the cell, leaves out moves with it. Nothing without a membrane. No default")
    parser.add_argument("--board", type=Path, help="write a before/after picture at the frames where the part sits lowest and highest")


def fold_notes(record: dict[str, Any], verb: str = VERB) -> list[str]:
    """What to say of a record whose gain was lowered or whose regions were held, a line each."""
    fold, notes = record["fold"], []
    if "stretch" in record:  # a floor was asked: each region says what lowered it, the fold or the floor
        floor = record["stretch"]["floor"]
        for region, entry, folded in zip(record["regions"], record["stretch"]["regions"], fold["regions"]):
            name = "--region " + ",".join(f"{v:g}" for v in region)
            if entry["held"]:
                why = (f"folds at --gain {folded['gain_limit']:g}" if entry["rule_gain"] == 0 else
                       f"keeps --stretch-floor {floor:g} only at --gain {entry['floor_gain']:g}")
                notes.append(f"{verb}: {name} {why}, under {GAIN_MEASURED:g} (the mass as measured): it is held, and does not move")
            elif entry["gain"] != record["gain_requested"]:
                by = f"--stretch-floor {floor:g}" if entry["gain"] < entry["rule_gain"] else "the fold"
                notes.append(f"{verb}: {name} moves at --gain {entry['gain']:g}, lowered from {record['gain_requested']:g} by {by}")
        return notes
    if "held" in fold["regions"][0]:  # the regions took gains of their own
        for region, entry in zip(record["regions"], fold["regions"]):
            name = "--region " + ",".join(f"{v:g}" for v in region)
            if entry["held"]:
                notes.append(f"{verb}: {name} folds at --gain {entry['gain_limit']:g}, under {GAIN_MEASURED:g} (the mass as measured): "
                             "it is held, and does not move")
            elif entry["gain"] != record["gain_requested"]:
                notes.append(f"{verb}: --gain {record['gain_requested']:g} folds {name} (a move of {fold['reach_requested_px']:g} px); "
                             f"lowered to --gain {entry['gain']:g} for it")
    elif fold["lowered"]:
        notes.append(f"{verb}: --gain {record['gain_requested']:g} folds a region (a move of {fold['reach_requested_px']:g} px); "
                     f"lowered to --gain {record['gain']:g}")
    return notes


def run(**kwargs: object) -> int:
    verb = str(kwargs.get("verb") or VERB)
    asked = {"gain": kwargs.get("gain"), "freq": kwargs.get("freq"), "zeta": kwargs.get("zeta"), "board": kwargs.get("board"),
             "on_fold": str(kwargs.get("on_fold") or "refuse"), "read_on": kwargs.get("read_on"),
             "stretch_floor": kwargs.get("stretch_floor"), "breathe": kwargs.get("breathe"), "lag": kwargs.get("lag"),
             "gain_across": kwargs.get("gain_across"), "band": kwargs.get("band"), "band_margin": kwargs.get("band_margin"),
             "band_up": kwargs.get("band_up"), "hold_mask": kwargs.get("hold_mask"), "region_mask": kwargs.get("region_mask"),
             "region_rim": kwargs.get("region_rim"), "region_shrink": kwargs.get("region_shrink"),
             "region_crease": bool(kwargs.get("region_crease")), "region_over": bool(kwargs.get("region_over")),
             "region_shear": kwargs.get("region_shear"), "region_membrane": kwargs.get("region_membrane"),
             "region_over_solid": bool(kwargs.get("region_over_solid")), "region_rim_round": bool(kwargs.get("region_rim_round")),
             "region_membrane_unpin": bool(kwargs.get("region_membrane_unpin")),
             "region_clear_alpha": kwargs.get("region_clear_alpha"), "region_floor_beside": kwargs.get("region_floor_beside"),
             "region_own_outline": kwargs.get("region_own_outline"), "read_on_cut": kwargs.get("read_on_cut")}
    if kwargs.get("frames") is not None:
        if kwargs.get("out") is None:
            raise SystemExit(f"{verb}: --frames needs --out, a new folder for the swayed frames")
        result = sway_frames(Path(str(kwargs["frames"])), Path(str(kwargs["out"])), list(kwargs["region"]),  # type: ignore[arg-type]
                             fps=kwargs.get("fps"), reference=kwargs.get("reference"), verb=verb, **asked)  # type: ignore[arg-type]
    else:
        for flag in ("out", "fps", "reference"):
            if kwargs.get(flag) is not None:
                raise SystemExit(f"{verb}: --{flag} goes with --frames; a loop directory is swayed where it is")
        result = sway_loop(Path(str(kwargs["loop_dir"])), list(kwargs["region"]), verb=verb, **asked)  # type: ignore[arg-type]
    fold = result["fold"]
    own = "held" in fold["regions"][0]  # the regions took gains of their own
    for note in fold_notes(result, verb):
        print(note, file=sys.stderr)
    where = "out" if "out" in result else "strip"
    if "band" in result:
        for note in band_notes(result, verb, f"{FRAMES_RECORD} band" if where == "out" else f"{loop_mod.SWAY_RECORD}.band"):
            print(note, file=sys.stderr)
    if "hold" in result:
        for note in hold_notes(result, verb, f"{FRAMES_RECORD} hold" if where == "out" else f"{loop_mod.SWAY_RECORD}.hold"):
            print(note, file=sys.stderr)
    if "region_mask" in result:
        for note in region_notes(result, verb, f"{FRAMES_RECORD} region_mask" if where == "out" else f"{loop_mod.SWAY_RECORD}.region_mask"):
            print(note, file=sys.stderr)
    print(json.dumps({k: result[k] for k in (where, "regions", "gain", "gain_requested", "on_fold", "body_bob_px", "reach_px")}
                     | ({"region_gains": [entry["gain"] for entry in fold["regions"]]} if own else {})
                     | ({"drive": result["drive"]["kind"]} if "drive" in result else {})
                     | ({"read_on": result["read_on"]["relation"]} if "read_on" in result else {})
                     | ({"stretch_floor": result["stretch"]["floor"]} if "stretch" in result else {})
                     | ({"band": {"band_px": result["band"]["band_px"], "margin_px": result["band"]["margin_px"],
                                  "lay_cells": sorted({entry["cell"] for entry in result["band"]["lay"]})}} if "band" in result else {})
                     | ({"hold": {"pieces": len(result["hold"]["pieces"]),
                                  "lay_cells": sorted({entry["cell"] for entry in result["hold"]["lay"]}),
                                  "read_folded_px_max": result["hold"]["ledger"]["read_folded_px_max"]}} if "hold" in result else {})
                     | ({"region_mask": {"kept_cells": [entry["cell"] for entry in result["region_mask"]["kept"]],
                                         "rim_px": [result["region_mask"]["rim"]["across_px"], result["region_mask"]["rim"]["down_px"]],
                                         "move_most_px": result["region_mask"]["ledger"]["move_most_max"],
                                         "walked_px_max": result["region_mask"]["ledger"]["walked_px_max"]}}
                        if "region_mask" in result else {})
                     | ({"board": result["board"]} if "board" in result else {}), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None, verb: str = VERB) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog=f"sprite-gen {verb}", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)), verb=verb)


# The old names, kept: the same functions, called as they always were (the old verb in what they say).
def follow_loop(loop_dir: Path, regions: list[tuple[float, float, float, float]], **kwargs: Any) -> dict[str, Any]:
    return sway_loop(loop_dir, regions, **{"verb": OLD_VERB, **kwargs})


follow_offsets = sway_offsets


if __name__ == "__main__":
    raise SystemExit(main())
