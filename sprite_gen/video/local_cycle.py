# SPDX-License-Identifier: Apache-2.0
"""Select an observed local repeat after camera/subject drift analysis.

A changing cadence can contain a good cycle without one period fitting the
whole clip. Require a local lag minimum, repeat depth, motion context, and the
same two-step duration prior; an isolated matching endpoint is insufficient.
The local period taken is screened for two or three cycles and the finding
recorded (`fundamental`, sprite_gen/video/period.py), never cut shorter; the
legs are read off `signals` (`legs.strike_signals` of the analysed frames).
The cut taken is screened the other way too, for one step of a cycle twice as
long that the window could not hold (`step_screen`), never cut longer on it:
only a count of the steps cuts again — given (`video-loop --steps`), or read off
the clip's own crossings (`by_steps`, sprite_gen/video/crossings.py): where the
clip shows its steps, the cut is two of them, joined at the walk's own point,
the smoothest seam first. A cut under the gait floor is the exception: the
clip as filmed is read two lengths on about the doubled cut's wrap
(`loop._two_lengths`).
"""
from __future__ import annotations
import math
from sprite_gen._deps import np
from sprite_gen.video import period as period_mod
from sprite_gen.video import crossings as crossings_mod
from sprite_gen.video import legs as legs_mod
from sprite_gen.video.crossings import HIDE_SHARE

REFUSED_SHOWN = 12  # refused windows a failed search reports, as many as a found cycle's candidates
# A two-step cut's seam is as smooth as the smoothest in-phase one's within this much more flow off (`_rank`): about
# what an ordinary frame of a walk lies off the line between its neighbours, in its window's usual steps.
SEAM_FLOW_BAND = 0.5
REPEAT_OVER_STEP_MAX = 2  # `detect` refuses a repeat further off than two playback steps ('repeat error over two steps')
SEAM_TRIES = 4  # two-step cuts, best first, whose seam is read as the seam gate reads it before one is taken (`by_steps`)
# Whether the crossings mark cycles (`read_cycles`): the clip one interval between crossings on is nearer than half an
# interval on and as near as two intervals on within CYCLE_SLACK of the clip's mean step (each read at a whole lag, up
# to half a frame off the interval: about half a step).
CYCLE_SLACK = 0.5
# The view's foot cue (`legs.shade_cue` from a diagonal or the side, `legs.depth_cue` from the front or back) can only
# refuse cycles (`cue_word`): turned over to CUE_SWAPPED or under one interval on (`crossings.correlation_on`), the other
# leg leads there and the interval is a step. Its turn half an interval on witnesses nothing: a light fixed on the screen
# turns a cue bound to how far apart the feet are drawn there too, whichever leg leads.
CUE_SWAPPED = -0.2
# A cue whose range over the clip is under CUE_FLOOR of the step it is drawn in (`legs.TONE` of luma, a pixel row of the
# body's height) rounds to one value: the legs are drawn alike to it, and its correlation is the arithmetic's (not read).
CUE_FLOOR = 0.5
# The body's bob witnesses that the crossings mark cycles (`body_word`), read without telling the legs apart: the body's
# top line (`legs.strike_signals` `top`) rises and falls once a step (a body rising and falling twice a step on its own
# legs reads as two steps an interval of one, and nothing that reads no leg tells it apart). It is read less its flow
# (`off_flow`): the picture drifting, swaying or zooming up and down slower than the interval, or a lean changing, moves
# the line where it pleases, while a rise and fall once or twice an interval comes to nothing over one and is left
# whole. Where the interval holds two steps the line left is as it was half an interval on and turned over a quarter on,
# each by BOB_TWICE (correlated BOB_TWICE or over there, -BOB_TWICE or under here); where it holds one it is turned over
# half an interval on, and a flow left over is as it was at both. A line left whose range is under BOB_FLOOR of the
# body's median height, or under BOB_ROWS pixel rows (a line read in whole rows rounds a row up and down as the picture
# drifts), does not rise and fall, and witnesses nothing. A line reads so in two places (CUT_LINES), on the screen and over
# the body's feet (`over_feet`), and it witnesses only where it says so in both (`body_word`), the crossing interval and
# a walk's cut alike. On the screen alone a head rising twice in one step reads as two steps; the feet leave no such rise
# and fall over them. Over the feet alone the two feet's heights, taken in by turns, read as the body's; the screen line
# has none of them. A walk whose rise and fall shows in one place only is therefore not witnessed, as a picture swaying
# once a cut, left whole by the flow taken out at the cut's length, hides the rise and fall on the screen, or a walk
# leaning further on its feet as it goes tilts the ground under them. The line that says so in both is read as one
# (TOP_LINES, never one line on the screen and the other over the feet): the top line, or the first solid row (`legs.body_signals`
# `first_row`), since a tail or a cape as wide as half the widest row takes the top line off the head.
# The picture moving up and down moves the line as the body does, on any beat — shaken twice an interval it is pixel for
# pixel a body carried up and down whole, feet and all — and the picture zoomed in and out lifts and lowers it too. So
# the line's rise and fall twice an interval (`twice_an_interval`) is read against the body's own lowest row and upper
# body (`own_beat`): less the lowest solid row's (`legs.strike_signals` `reach`, the planted foot, which a body rising
# on its legs leaves on the ground and the picture carries along), and that left is the top line's own: at least BOB_OWN
# of it is on the screen, or the rise and fall left is the lowest row's, on a line that rises and falls on some other
# beat (read every four frames, a line on a beat of twelve leaves nothing of it twice an interval of 24, and still
# correlates there). Then less that again of the zoom each band of the upper body reads (`legs.body_signals` `bands`: a
# body rising on its legs carries them whole and leaves their widths; a
# zoom widens each as it lifts the line, as far as the band's outline lets it — one whose sides run straight down by the
# zoom's whole part, one under a point, as of a pointed head, an ear or a hat, by none). How far a zoom moves each band
# is measured on the body as it is drawn, not assumed of its outline: the frame of its median height zoomed in as far as
# the line's rise and fall against the lowest row lengthens the body (`legs.zoomed`). A band such a zoom moves
# BAND_FLOOR pixels or more is seen; under a pixel, how far a band's width moves is set as much by how its outline sits
# on the pixel grid (its first solid row rounded, a point's edges blurred) as by the zoom — a pointed head's top band,
# zoomed in once, may move most of a pixel and, zoomed so on the beat, a quarter of one — and one moved less reads none.
# A body rising and falling so little that no band would move a pixel is not told from a zoom. A band seen reads the
# zoom as far as its width rises and falls twice an interval, whatever its turn (`own_beat` `reads`): a zoom moves it in
# step with the line it lifts, but its parts swinging across it move it too, in step with the line, against it or a
# quarter of the beat off, and the zoom's part is not told from theirs. It holds still on the beat where it reads no
# more than 1 - BOB_SCALE. One band holding still says only that no zoom shows in it (a head drawn narrower as the
# picture zooms in holds still so), and a band its parts swing across on the beat, as arms do, may hide a zoom under the
# swing at any turn: a walk's arms read so, whatever the picture does. So where it can, the witness reads what a zoom
# does and arms do not, the rows down the middle of the body (`body_depth`, `legs.body_signals` `strips`), at the top of
# the rise against the bottom: a body rising whole carries them as they are, and a zoom as far as the rise draws them
# longer. They read the body's own where that zoom fits them no better than they fit as they stand (BOB_RIGID), and,
# read at all, they decide, and the bands' widths are recorded and left out. They are read where such a zoom would show:
# it moves the strip STRIP_FLOOR or more; drawn plain, the strip fits a zoom as well as anything. Where it is not read,
# the bands decide, and the witness holds by default: where any band seen moves on the beat, nothing tells the picture
# zooming from the body's own, and nothing is witnessed. A picture zoomed with every band seen drawn against it, each
# holding still, and a strip as plain, is read as the body's own: its upper body is that of one rising whole on its
# legs. What is left of the line's rise and fall is the body's own where, against the lowest row, it is at least BOB_OWN
# of it and over the floor above, the top line's own is at least BOB_OWN of that, a band would show the zoom, and either
# the strip reads the body's own or, less as much of the zoom as each band seen reads, the line's rise and fall is left
# as above and no less than BOB_SCALE of what is left against the lowest row. Else the picture moves or zooms with the
# line, or the body does not rise and fall enough of its own to tell from it, and nothing is witnessed. Where the lowest
# solid row is the frame's last row in any frame (`legs.body_signals` `edge`) it is the frame's, not the feet's, and
# nothing is witnessed either.
BOB_TWICE = 0.28
BOB_FLOOR = 0.005
BOB_ROWS = 2
BOB_OWN = 0.3
BOB_SCALE = 0.6
BAND_FLOOR = 1.0  # pixels of a band's mean width a zoom moves: under it the pixel grid moves the band as much
BOB_RIGID = 1.0  # a zoom as far as the rise fits the strip no better than it fits as it stands: a body rising whole
# How far a zoom as far as the rise moves the strip (`body_depth` `moved`, the mean of its colour and cover over its rows,
# each in 0..1) for it to be read: half the step its colours are drawn in (`legs.TONE`), as CUE_FLOOR reads a cue. Under
# it the strip is as good as plain to that zoom, and which of the two fits it better is the rounding of its colours.
STRIP_FLOOR = CUE_FLOOR*legs_mod.TONE
# The strip's fits (`body_depth`) are each moved as they fit best, in steps of a twelfth of this and at most half a row
# or more: STRIP_SHIFT of the body's height either way, at least STRIP_SHIFT_MIN rows. Its rows are read from the first
# solid row, which a body's outline rounds and a nod moves a little over the rise.
STRIP_SHIFT = 0.03
STRIP_SHIFT_MIN = 3.0
BEAT_FRAMES = 8  # a rise and fall twice an interval is fitted (`twice_an_interval`) to no fewer frames
# A line that does not rise and fall fits to round-off, not to nothing: exactly 0 under one machine's numpy, about 1e-30
# rows under another's, and two lines that move alike leave about 1e-14 rows of each other. A line read in whole rows
# moves a row or not at all, and a row in one frame reads about 1e-5 rows up and down at the least, on a clip of a
# thousand frames. Under BEAT_STILL rows there is no rise and fall to take a share of (`_share`, `bob_word` `share` and
# `scale`). What the witness says does not read it: its floors are whole rows.
BEAT_STILL = 1e-9
# Where a line is read (`body_word`): on the screen, and over the body's feet (`over_feet`). It witnesses where it says so
# in both.
CUT_LINES = {'screen': 'on the screen', 'feet': 'over its feet'}
# The lines read so (`signals` keys), one at a time: the top line first, as `legs.strike_signals` reads it, then the first
# solid row (`legs.body_signals`).
TOP_LINES = {'top': 'its top line', 'first_row': 'its first solid row'}
SIDE_VIEW = 'side'  # from the side the legs a step on are one silhouette, as near as a cycle on: asked only where a pass hides
# The order the feet are drawn in witnesses as the body does (`order_word`), for the step count and the cut alike: from
# the front, the back or a diagonal the foot nearer the viewer is drawn lower (`legs.feet_order`), and the nearer foot
# changes sides twice a cycle, once a step. Where the clip shows the two sides of its foot band are two legs, the
# changes counted over a crossing interval, or over a cut, say how many steps it holds. The view's label is not taken
# for it (a generated walk's label may be wrong): the clip shows it, or nothing is witnessed. Two feet are seen apart
# (`legs.feet_order`) in at least `legs.FEET_APART_SHARE` of the frames; the lower foot's side swings ORDER_ROWS rows
# or more and `legs.REACH_MIN` of the body's height (p10 to p90 of `depth`), a side read where one foot is drawn an
# eighth of that swing (at least a row) lower; no foot planted on the ground passes under the body to the other side,
# as one does seen from the side, the two sides then its front and back, not its two legs — no change of sides whose
# nearer foot's contact before it and the other side's after it share or touch a column, and nothing standing on the
# lowest row slides along it ORDER_TRAVEL of the feet's distance apart or more (`_travel`; a foot seen from the side
# slides the whole of it, one seen from the front or the back stands, or steps toward or away from the viewer); and the
# upper body sways under ORDER_SWAY of what a tilt about the feet would carry it to draw the lower foot so (`sway`, p10
# to p90, against the swing over the feet's distance apart, at ORDER_REACH of the body's height over them): a picture
# rocking on its feet draws one foot lower and then the other. Then the changes' rate over the span, within
# ORDER_SLACK of two, four or one, no two changes further apart than two of their mean, says two steps, four or one;
# from the side (SIDE_VIEW) it is not read. Before all of it, a foot is drawn over the ground the walk stands on: a
# frame either of whose two sides is drawn over no column anything stands on the lowest row of in any frame of the clip
# (`ground`) is not read apart (`_off_ground`) — a tail's tip dipping into the foot band, read as a foot, would change
# the side drawn lower as no foot does; a foot lifted where none ever stands is left out with it. A tail hanging over
# the columns the feet stand on is still read as a foot: the gates above are what hold it.
ORDER_ROWS = 2
ORDER_TRAVEL = 0.8
ORDER_SWAY = 0.5
ORDER_REACH = 0.75
ORDER_SLACK = 0.2
ORDER_WORDS = (('two steps', 2), ('four steps', 4), ('one step', 1))
# A loop whose clip one interval on is the same picture, as a walk whose feet meet once a cycle draws it, with nothing
# to witness whether they do (`read_cycles`): its crossings stay on steps, so its two steps may be two cycles. Named on
# its step count (`cycles.unwitnessed`), and `video-set` leaves it out of its state's cycle alignment under this reason.
# So is a walk's cut that nothing witnesses whole cycles as it is cut, whichever way it was chosen (`cut_word`, loop
# `_witnessed`; `cut_witness.held`): it may be one step of a walk whose legs are drawn alike, or, the body rising and
# falling once in it, a whole cycle of a body that does so once a cycle (`unwitnessed`).
UNWITNESSED = 'cycle-unwitnessed'
# A walk's cut its body rises and falls four times in, or its foot drawn nearer changes sides four times in (`cut_word`
# says four steps, loop `_witnessed`): two cycles, and it stands. Its strip still counts the two steps the crossings counted (`steps: 2`), which `video-cycle-align` reads as one
# cycle, a return half way its second step: aligned so, it would play twice as fast as the rest. `video-set` leaves it
# out of its state's cycle alignment under this reason (`four_steps`).
FOUR_STEPS = 'cycle-four-steps'


def _around(start, length):
    """The frames whose repeats are compared for a cut at `start`: a quarter of the cut either side."""
    radius = max(2, length//4)
    return radius, np.arange(max(0, start-radius), start+radius+1)


def _profile(distances, js, radius, lo, hi):
    n = len(distances)
    profile = {}
    for lag in range(max(2, lo//2), hi+2):
        observed = js[js+lag < n]
        if len(observed) >= radius+1:
            profile[lag] = float(distances[observed, observed+lag].mean())
    return profile


def _measure(distances, trajectory, start, length, profile):
    """The parts of a window's score, for a window the search refused (`refused`): the same sums as
    a candidate's, so a refused window and a candidate read alike."""
    step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
    if step <= 1e-10 or length not in profile:
        return None
    ratio = float(distances[start+length-1, start])/step
    x = np.arange(length)
    segment = trajectory[start:start+length]
    residual = float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean())
    error = profile[length]
    return {'start': start, 'length': length, 'score': error/step + .3*abs(math.log(max(.01, ratio))) + residual,
            'ratio': ratio, 'context_repeat_over_step': error/step, 'drift_line_residual_analysis_px': residual}


def _closure(distances, start, length):
    """How far a cut's wrap is from the clip's own way on there: its last frame against the clip's frame
    before its first, and the frame before its last against the one before that (the speed into the
    wrap), over the cut's mean playback step. None for a cut with fewer than two clip frames before it."""
    step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
    if start < 2 or step <= 1e-10:
        return None
    return float(distances[start+length-1, start-1] + distances[start+length-2, start-2])/2/step


def _read(measured):
    """A cut's pop as `cycle.seam_pop` records it: the jump, or why its top band could not be read."""
    return {'skipped': measured['skipped']} if 'skipped' in measured else {'pop': measured['pop']}


def _mark(row, measured):
    """A window row's pop: `seam_pop`, or None with `seam_pop_skipped` (why) when its top band could
    not be read — an unread window is neither taken as closing nor as popping."""
    row['seam_pop'] = measured.get('pop')
    if 'skipped' in measured:
        row['seam_pop_skipped'] = measured['skipped']


def _held(measured):
    """A window's held side as `cycle.seam_pop.held_edge` records it: its seam, or why it could not be read."""
    return {'skipped': measured['skipped']} if 'skipped' in measured else {'seam': measured['seam'], 'closes': measured['closes']}


def _held_rank(candidates, calm, chosen, wrap_pop, held_edge, length_cap=None, double=True, turn=None):
    """Below the top band (`held_edge`, repair.held_edge on the side the popping first choice holds its
    part): among the calm candidates, those whose held side closes, the same score tolerance, then the
    smallest held seam. When no candidate closes it, windows twice a candidate's length: the candidate
    was one step of a walk whose arm, and the part it holds, swing once a cycle — two steps — and close
    only there (`steps` 2: the window is one cycle, `step_lengths` the candidates it is two of — each one
    step). Such a window is two lengths of a repeat the search confirmed, not itself seen repeating; it
    is taken when its held side closes and its top does not pop, the smallest held seam first. A window longer than `length_cap`
    (an explicit --max-len) is not one, and none is read without `double` (the steps were counted,
    `video-loop --steps`), nor one `turn` refuses (`detect`). Returns (picked or None, record)."""
    side = held_edge.side(chosen['start'], chosen['length'])
    if 'skipped' in side:
        return None, {'method': 'held-side-edge-v1', 'skipped': side['skipped']}
    side = side['side']
    record = {'method': 'held-side-edge-v1', 'side': side, 'reference': held_edge.reference}
    closing = []
    unread = 0
    for row in calm:
        measured = held_edge.seam(row['start'], row['length'], side)
        row['held_edge'] = measured.get('seam')
        if 'skipped' in measured:
            row['held_edge_skipped'] = measured['skipped']
            unread += 1
        elif measured['closes']:
            closing.append(row)
    record.update(measured=len(calm)-unread, unread=unread)
    if closing:
        cutoff = min(row['score'] for row in closing)*1.15+1e-8
        picked = min((row for row in closing if row['score'] <= cutoff),
                     key=lambda row: (row['held_edge'], row['seam_pop'], row['score'], row['start'], row['length']))
        record.update(doubled=False, chosen={'start': picked['start'], 'length': picked['length'], 'seam': picked['held_edge'], 'closes': True})
        return picked, record
    if not double:
        record['two_step'] = {'read': False, 'why': 'the steps were counted (--steps): no window twice as long is read'}
        return None, record
    lengths = sorted({2*row['length']+d for row in candidates for d in (-1, 0, 1)})
    if length_cap is not None:
        over = [length for length in lengths if length > length_cap]
        lengths = [length for length in lengths if length <= length_cap]
        if over:
            record['two_step_capped'] = {'max_len': length_cap, 'lengths_over': [over[0], over[-1]]}
        if not lengths:
            return None, record
    windows = []
    counts = {'measured': 0, 'unread': 0, 'top_pops': 0}
    for length in lengths:
        for start in range(held_edge.frames-length+1):
            if turn is not None and turn(start, length) is not None:
                continue
            measured = held_edge.seam(start, length, side)
            if 'skipped' in measured:
                counts['unread'] += 1
                continue
            counts['measured'] += 1
            if not measured['closes']:
                continue
            top = wrap_pop(start, length)
            if 'skipped' in top or top['pops']:
                counts['top_pops'] += 1  # a top that pops, or one that could not be read, is not taken
                continue
            windows.append({'start': start, 'length': length, 'period_local': length,
                            'steps': {'count': 2, 'by': 'held-edge',
                                      'step_lengths': sorted({row['length'] for row in candidates if abs(2*row['length']-length) <= 1})},
                            'held_edge': measured['seam'], 'seam_pop': top['pop'],
                            'repeat': 'two lengths of a confirmed repeat (two steps, one cycle), not itself seen repeating'})
    record['two_step'] = {'read': True, 'lengths': [lengths[0], lengths[-1]], **counts, 'closing': len(windows)}
    if windows:
        picked = min(windows, key=lambda row: (row['held_edge'], row['seam_pop'], row['start'], row['length']))
        record.update(doubled=True, chosen={'start': picked['start'], 'length': picked['length'], 'seam': picked['held_edge'], 'closes': True})
        return picked, record
    return None, record


def _shape_rank(candidates, chosen, wrap_pop, held_edge=None, length_cap=None, double=True, turn=None):
    """Choose again when the chosen cut's top pops at the wrap (`wrap_pop`, repair.seam_pop on the
    analysed frames): among the candidates whose top was read and does not pop, the same score
    tolerance, then the smallest pop. Nothing is measured past the first choice when it does not pop,
    or when its top could not be read (kept, `skipped`), so a cut without a thin part swinging on its
    own beat is chosen exactly as before. A candidate whose top could not be read is not chosen.

    With `held_edge` (loop.HeldEdge), a popping first choice has its held part read below the top too
    (`_held_rank`): a cut whose held side closes is taken first, one or two lengths long. Where none
    closes, the choice is the top band's, and `held_edge.chosen` records that it does not close (the
    jolt warns, repair.held_edge_verdict). A window two lengths long is never longer than `length_cap`,
    nor read without `double`."""
    first = wrap_pop(chosen['start'], chosen['length'])
    record = {'method': 'top-band-wrap-v1', 'reference': first['reference'],
              'first_choice': {'start': chosen['start'], 'length': chosen['length'], **_read(first)},
              'applied': False}
    if 'skipped' in first or not first['pops']:
        return chosen, record
    calm = []
    unread = 0
    for row in candidates:
        measured = wrap_pop(row['start'], row['length'])
        _mark(row, measured)
        if 'skipped' in measured:
            unread += 1
        elif not measured['pops']:
            calm.append(row)
    record.update(measured=len(candidates)-unread, unread=unread)
    held = None
    if held_edge is not None:
        picked, held = _held_rank(candidates, calm, chosen, wrap_pop, held_edge, length_cap, double, turn)
        record['held_edge'] = held
        if picked is not None:
            record.update(applied=True, chosen={'start': picked['start'], 'length': picked['length'], 'pop': picked['seam_pop'],
                                                **({'steps': 2} if 'steps' in picked else {})})
            return picked, record
    if not calm:
        record['why'] = 'every candidate whose top was read pops at the wrap; the first choice is kept'
        picked = chosen
    else:
        cutoff = min(row['score'] for row in calm)*1.15+1e-8
        picked = min((row for row in calm if row['score'] <= cutoff),
                     key=lambda row: (row['seam_pop'], row['score'], row['start'], row['length']))
        record.update(applied=True, chosen={'start': picked['start'], 'length': picked['length'], 'pop': picked['seam_pop']})
    if held is not None and 'side' in held:
        measured = held_edge.seam(picked['start'], picked['length'], held['side'])
        held['chosen'] = {'start': picked['start'], 'length': picked['length'], **_held(measured)}
    return picked, record


def _pop_said(read):
    """A cut's top band at the wrap as the seam pop gate reads it before the cut (`pop_gate`), for the record: the pop,
    and the gate's word — `refused` over its bound, `passed`, or `unreadable` (`pop_skipped`, why), which it does not
    refuse."""
    if 'skipped' in read:
        return {'pop': None, 'pop_skipped': read['skipped'], 'pop_gate': 'unreadable'}
    return {'pop': read['pop'], 'pop_gate': 'refused' if read['over'] else 'passed'}


def _pop_gate(chosen, rest, pop_gate):
    """The seam pop gate before the cut. `pop_gate(start, length)` reads a cut's top band at the wrap as the gate reads
    the loop — its frames corrected as the cut will be (loop.CutOnCells): `{'pop', 'over', 'max'}`, or `{'skipped',
    'max'}` where the top band cannot be read, which the gate does not refuse. The chosen cut is read first; where the
    gate would refuse it, the next of `rest` (in the caller's order), and the first of SEAM_TRIES the gate does not
    refuse is taken — as `by_steps` takes the first two-step cut its seam gate passes. Where it would refuse them all,
    the first choice stays, and the gate refuses it on the loop. Returns (the cut, the record: `max`, `tried`,
    `applied`, and `why` where none passed)."""
    tried = []
    picked = None
    bound = None
    for row in [chosen, *rest][:SEAM_TRIES]:
        read = pop_gate(row['start'], row['length'])
        bound = read['max']
        tried.append({'start': row['start'], 'length': row['length'], **_pop_said(read)})
        if tried[-1]['pop_gate'] != 'refused':
            picked = row
            break
    record = {'max': bound, 'tried': tried, 'applied': picked is not None and picked is not chosen}
    if picked is None:
        record['why'] = (f"the gate refuses each of the {len(tried)} cuts read: the first choice is kept, and the gate "
                         "refuses it on the loop")
        picked = chosen
    return picked, record


def counted(distances, trajectory, *, min_len, max_len, periodicity_min, signals=None, pop_gate=None, turn=None):
    """A cut of a length someone counted (`video-loop --steps`) where the clip is too short to see a
    window that long repeat (`detect` found none): the counted length stands for the repeat. Every
    window [min_len, max_len] long with two clip frames before it is read, and the one whose wrap is
    nearest the clip's own way on there is taken (`_closure`, the earliest on a tie); whether it closes
    is the seam gate's word. With `pop_gate`, a window the seam pop gate would refuse gives way to the next nearest
    (`_pop_gate`, `seam_pop_gate`), and none `turn` refuses is read (`detect`). Raises ValueError where no window has
    room."""
    n = len(distances)
    rows = []
    for length in range(min_len, max_len+1):
        for start in range(2, n-length+1):
            if turn is not None and turn(start, length) is not None:
                continue
            measured = _closure(distances, start, length)
            if measured is None:
                continue
            step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
            x = np.arange(length)
            segment = trajectory[start:start+length]
            rows.append({'start': start, 'length': length, 'period_local': length, 'closure': measured,
                         'ratio': float(distances[start+length-1, start])/step,
                         'drift_line_residual_analysis_px': float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean()),
                         'half_period_guard': {'applied': False}})
    if not rows:
        raise ValueError(f"no window {min_len}-{max_len} frames long has two clip frames before it in {n}")
    rows.sort(key=lambda row: (row['closure'], row['start'], row['length']))
    chosen, gate = _pop_gate(rows[0], rows[1:], pop_gate) if pop_gate is not None else (rows[0], None)
    return {**chosen, 'kind': 'periodic', 'method': 'counted-window-v1',
            **({'seam_pop_gate': gate} if gate is not None else {}),
            'repeat': 'not seen in the clip: the counted length stands for it', 'choice': 'closure',
            'period_global': None, 'periodicity_min': periodicity_min, 'review_recommended': True,
            'candidate_count': len(rows), 'candidates': rows[:REFUSED_SHOWN],
            'step_screen': period_mod.step_screen(distances, start=chosen['start'], length=chosen['length'],
                                                  periodicity_min=periodicity_min, signals=signals)}


def _screens(distances, chosen, *, lo, hi, gait_floor, periodicity_min, signals):
    """The cut's two screens, as `detect` records them: below it for two or three cycles (`fundamental`,
    period.screen on the local profile about it) and above it for one step of a cycle twice as long
    (`step_screen`, period.step_screen over the whole clip)."""
    radius, js = _around(chosen['start'], chosen['length'])
    profile = _profile(distances, js, radius, lo, hi)
    fundamental = period_mod.screen(
        chosen['period_local'], D=distances, prof=profile, minima=period_mod.local_minima(profile),
        mean=float(np.mean(list(profile.values()))), lowest=max(lo, gait_floor), periodicity_min=periodicity_min,
        signals=signals, gait=True, js=js, floor_why='the gait floor' if gait_floor >= lo else 'the window')
    steps = period_mod.step_screen(distances, start=chosen['start'], length=chosen['length'],
                                   periodicity_min=periodicity_min, signals=signals)
    return fundamental, steps


def judged(rows, judge):
    """Each window row's steps as the clip's crossings count them (`step_count`: verdict, code, k, phase)."""
    for row in rows:
        counted = judge(row['start'], row['length'])
        row['step_count'] = {k: counted[k] for k in ('verdict', 'code', 'k', 'phase') if k in counted}
    return rows


def detect(distances, trajectory, *, min_len, max_len, gait_floor,
           periodicity_min, double_tolerance, double_search, max_fraction=.5, signals=None, wrap_pop=None,
           held_edge=None, length_cap=None, double=True, closure=False, judge=None, pop_gate=None, turn=None):
    """`wrap_pop(start, length)`, when given, is the top band's jump at that cut's wrap
    (repair.seam_pop): a chosen cut that pops is chosen again (`_shape_rank`), and a search that finds
    nothing raises with the windows it refused, popping ones last (`ValueError.diagnostics`).

    `pop_gate(start, length)`, when given, is the seam pop gate's reading of a cut before it is cut (`_pop_gate`): the
    cut chosen — after `_shape_rank` — that the gate would refuse gives way to the next of the candidates in the
    search's order (the equivalent ones earliest first, then the rest best first), the first of SEAM_TRIES the gate
    does not refuse (`seam_pop_gate` in the result).
    `held_edge` (loop.HeldEdge) reads the held side below the top of a cut chosen again; the cut it
    takes may be two lengths long (`steps` in the result: two steps, one cycle), but never longer than
    `length_cap`, nor with `double` false.

    `length_cap` is the caller's own ceiling on the cut — an explicit --max-len, or the window a count
    of steps sets (`video-loop --steps`) — which `max_len` alone does not mark (the state's default
    window is one too). It is the window's top as given: the share of the clip (`max_fraction`) bounds
    only a window the caller did not set. Each window still needs its repeat seen in the clip.

    Among the equivalent candidates the earliest is taken; with `closure` (a length the caller counted,
    `video-loop --steps`), the one whose wrap is nearest the clip's own way on there (`_closure`). The
    cut taken is screened for one step of a cycle twice as long (`step_screen`, period.step_screen,
    over the whole clip); nothing is cut on it.

    `judge(start, length)` (crossings.Reading.judge), when given, refuses a window whose steps the clip's
    crossings count as other than two ('one step', 'three steps': `refuted`), and a search that finds
    nothing names each refused window's count (`step_count`), those the crossings refute last.

    `turn(start, length)` (body_turn.Turn), when given, refuses a window whose cut holds a frame of a body turn, where
    the judge would read it, with its words ('the body turns (clip frames a-b)'), and so does the held side's window
    two lengths long (`_held_rank`); every candidate holds none."""
    n = len(distances)
    # A cycle has to be seen repeating: half the clip by default, more for the gait fallback, and as
    # long as the caller's own ceiling allows.
    lo = max(6, min_len)
    hi = min(max_len, length_cap) if length_cap is not None else min(max_len, int(n*max_fraction))
    if hi < lo:
        raise ValueError(f"automatic motion cycle window [{lo}, {hi}] has no repeated cycle")
    candidates = []
    refused = []
    for length in range(max(lo, gait_floor), hi+1):
        radius = max(2, length//4)
        for start in range(n-length-radius-1):
            _, js = _around(start, length)
            profile = _profile(distances, js, radius, lo, hi)

            def refuse(why, start=start, length=length, profile=profile):
                row = _measure(distances, trajectory, start, length, profile)
                if row is not None:
                    refused.append({**row, 'refused': why})
            minima = [lag for lag in profile if lag-1 in profile and lag+1 in profile
                      and profile[lag] <= min(profile[lag-1], profile[lag+1]) and lag <= hi]
            if not minima:
                refuse('no local repeat minimum')
                continue
            deepest = min(profile[lag] for lag in minima)
            period = min(lag for lag in minima if profile[lag] <= deepest*1.15+1e-4)
            guard = {'applied': False}
            if period < gait_floor:
                doubles = [lag for lag in minima if abs(lag-2*period) <= double_search and lag >= gait_floor]
                if not doubles:
                    refuse('one step: no repeat near twice it')
                    continue
                doubled = min(doubles, key=profile.get)
                if profile[doubled] > profile[period]*(1+double_tolerance)+1e-4:
                    refuse('one step: twice it repeats worse')
                    continue
                guard = {'applied': True, 'from': period, 'to': doubled,
                         'gait_floor': gait_floor, 'reason': 'local-two-step-repeat'}
                period = doubled
            # Allow one frame of cut quantization around the measured local lag.
            if abs(length-period) > 1 or profile.get(length, math.inf) > profile[period]*1.15+1e-4:
                refuse(f'the local repeat is {period} frames')
                continue
            baseline = float(np.mean(list(profile.values())))
            error = profile[length]
            depth = 1-error/baseline if baseline > 0 else 0
            step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
            if depth < periodicity_min or step <= 1e-10 or error/step > 2:
                refuse('repeat too shallow' if depth < periodicity_min else 'repeat error over two steps')
                continue
            # A still/rest segment matching another still is not a repeated gait.
            first_motion = float(distances[js[:-1], js[1:]].mean())
            second_motion = float(distances[js[:-1]+length, js[1:]+length].mean())
            if min(first_motion, second_motion) < .25*step:
                refuse('a still segment')
                continue
            if turn is not None:
                why = turn(start, length)
                if why is not None:
                    refuse(why)
                    continue
            if judge is not None:
                counted = judge(start, length)
                if counted['verdict'] == 'refuted':
                    refuse(f"{counted['why']} in the clip's crossings")
                    continue
            ratio = float(distances[start+length-1, start])/step
            x = np.arange(length)
            segment = trajectory[start:start+length]
            residual = float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean())
            score = error/step + .3*abs(math.log(max(.01, ratio))) + residual
            candidates.append({
                'start': start, 'length': length, 'period_local': period, 'score': score,
                'ratio': ratio, 'periodicity': depth, 'context_repeat_over_step': error/step,
                'context_pair_range': [int(js[0]), int(js[-1])+1],
                'context_motion_over_step': [first_motion/step, second_motion/step],
                'drift_line_residual_analysis_px': residual, 'half_period_guard': guard,
            })
    if not candidates:
        exc = ValueError("no periodic cycle found — no supported local two-step repeat after drift analysis")
        # The windows the search measured and refused, best first: a caller that must deliver anyway
        # (a forced cut) takes one the engine measured instead of the whole clip.
        refused.sort(key=lambda row: (row['score'], row['start'], row['length']))
        shown = refused[:REFUSED_SHOWN]
        if judge is not None:
            judged(shown, judge)
        if wrap_pop is not None:
            # Those whose top closes first, then those whose top could not be read, then those that pop.
            rank = {}
            for row in shown:
                measured = wrap_pop(row['start'], row['length'])
                _mark(row, measured)
                rank[row['start'], row['length']] = 1 if 'skipped' in measured else 2 if measured['pops'] else 0
            shown.sort(key=lambda row: (rank[row['start'], row['length']], row['score'], row['start'], row['length']))
        if judge is not None:
            shown.sort(key=lambda row: row['step_count']['verdict'] == 'refuted')
        exc.diagnostics = {'kind': 'periodic', 'method': 'local-repeat-drift-v1', 'status': 'refused',
                           'window': [lo, hi], 'refused_count': len(refused),
                           'order': 'score' + (', windows whose top could not be read after those whose top closes,'
                                               ' windows whose top pops at the wrap last' if wrap_pop is not None else '')
                                    + (', windows whose steps the crossings refute after the rest' if judge is not None else ''),
                           'candidates': shown}
        raise exc
    candidates.sort(key=lambda row: (row['score'], row['start'], row['length']))
    # Equivalent-quality candidates: prefer the earliest demonstrated repeat.
    cutoff = candidates[0]['score']*1.15+1e-8
    equivalent = [r for r in candidates if r['score'] <= cutoff]
    if closure:
        # A length counted, not found: its window was never the search's, and the earliest of the
        # equivalent cuts need not be the one that closes — the one whose wrap the clip itself plays is.
        for row in equivalent:
            row['closure'] = _closure(distances, row['start'], row['length'])
        chosen = min(equivalent, key=lambda row: (row['closure'] is None, row['closure'] or 0.0, row['start'], row['score'], row['length']))
    else:
        chosen = min(equivalent, key=lambda row: (row['start'], row['score'], row['length']))
    shape = None
    if wrap_pop is not None:
        chosen, shape = _shape_rank(candidates, chosen, wrap_pop, held_edge, length_cap, double, turn)
    gate = None
    if pop_gate is not None:
        rest = sorted((row for row in candidates if (row['start'], row['length']) != (chosen['start'], chosen['length'])),
                      key=lambda row: ((0, row['start'], row['score'], row['length']) if row['score'] <= cutoff
                                       else (1, row['score'], row['start'], row['length'])))
        chosen, gate = _pop_gate(chosen, rest, pop_gate)
    if 'ratio' not in chosen:
        # A window two lengths long (`_held_rank`) was not a candidate: its wrap over its step, as a candidate's.
        s, L = chosen['start'], chosen['length']
        chosen['ratio'] = float(distances[s+L-1, s])/float(np.diag(distances[s:s+L, s:s+L], 1).mean())
    fundamental, steps = _screens(distances, chosen, lo=lo, hi=hi, gait_floor=gait_floor,
                                  periodicity_min=periodicity_min, signals=signals)
    return {
        **chosen, 'fundamental': fundamental, 'kind': 'periodic', 'method': 'local-repeat-drift-v1',
        'period_global': None, 'periodicity_min': periodicity_min,
        'review_recommended': True, 'candidate_count': len(candidates),
        'best_score': candidates[0]['score'], 'equivalent_score_tolerance': .15,
        'candidates': candidates[:12],
        **({'choice': 'closure'} if closure else {}),
        **({'seam_pop': shape} if shape is not None else {}),
        **({'seam_pop_gate': gate} if gate is not None else {}),
        'step_screen': steps,
    }


def _worse(row):
    return max(row['seam_flow']['off'])


def _calm(row):
    """The cut's top band was read at the wrap and does not pop (`wrap_pop`)."""
    return 'pops' in row['top'] and not row['top']['pops']


def repeat_at(distances, span):
    """How far the clip lies from itself `span` frames on, the nearer at the two whole lags about it: the mean distance of
    every frame to the one a lag on, over the clip's mean step from one frame to the next. None where the clip holds
    under half a lag of frames past it, or does not move."""
    n = len(distances)
    step = float(np.diag(distances, 1).mean()) if n > 1 else 0.0
    found = [float(np.diag(distances, lag).mean())/step for lag in sorted({math.floor(span), math.ceil(span)})
             if lag >= 1 and n-lag >= max(2, lag/2) and step > 1e-10]
    return min(found) if found else None


def cue_word(cue, interval, *, step=None):
    """What the view's foot cue (`cue`, per frame) says of the legs `interval` frames on (`read_cycles`): `says` `alike`
    where its range over the clip is under CUE_FLOOR of the `step` it is drawn in (`legs.TONE` of luma, a pixel row of
    the body's height) — it does not move: the legs are drawn alike to it, and its correlation (the arithmetic's) is not
    read. Else `steps` where it turns over one interval on (`crossings.correlation_on` at CUE_SWAPPED or under: the
    other leg leads there), None where it does not — it cannot say the same leg leads. Returns the record."""
    values = np.asarray(cue, dtype=float)
    spread = float(values.max()-values.min()) if values.size else 0.0
    word = {'range': float(f'{spread:.3g}'), **({'floor': float(f'{CUE_FLOOR*step:.3g}')} if step is not None else {}),
            'swapped': CUE_SWAPPED}
    if step is not None and spread < CUE_FLOOR*step:
        return {**word, 'says': 'alike', 'why': f'its range over the clip ({spread:.2g}) is under half the step it is drawn in '
                                                f'({step:.2g}): it does not move'}
    one = crossings_mod.correlation_on(values, interval)
    word['one'] = None if one is None else round(one, 3)
    if one is not None and one <= CUE_SWAPPED:
        return {**word, 'says': 'steps'}
    return {**word, 'says': None, 'why': f"it does not turn over one interval on ({'not read' if one is None else f'{one:+.2f}'}, "
                                         f"over {CUE_SWAPPED:+g})"}


def off_flow(values, interval):
    """`values` (per frame) less their flow: each frame less the mean of the `interval` about it (a moving average one
    interval long, its two end frames weighted by the part of a frame the interval covers), over the frames the interval
    fits about, and what is left less its straight line. A rise and fall once or twice an interval comes to nothing over
    one and is left whole; a flow slower than the interval is taken out, wholly where it bends no more than a cubic."""
    x = np.asarray(values, dtype=float)
    reach = math.ceil(interval/2 - 0.5)
    if x.size < 2*reach + 3:
        return x[:0]
    weights = np.clip(interval/2 + 0.5 - np.abs(np.arange(-reach, reach+1)), 0.0, 1.0)
    left = x[reach:x.size-reach] - np.convolve(x, weights/weights.sum(), mode='valid')
    t = np.arange(left.size)
    return left - np.polyval(np.polyfit(t, left, 1), t)


def twice_an_interval(values, interval):
    """How `values` (per frame) rise and fall twice an interval `interval` frames long, less their flow (`off_flow`): the
    harmonic of what is left fitted by least squares with a mean and the harmonic once an interval (cos and sin of
    4 pi k / `interval` and of 2 pi k / `interval`, k the frames left from the first), its cos less i times its sin — a
    complex amplitude, in the values' units: a line rising and falling a rows either way twice an interval reads a, its
    phase at the first frame left. Lines read so over the same frames and interval subtract as they move. None where
    under BEAT_FRAMES frames are left."""
    x = off_flow(values, interval)
    if x.size < BEAT_FRAMES:
        return None
    k = np.arange(x.size, dtype=float)
    basis = np.stack([np.ones_like(k), np.cos(4*np.pi*k/interval), np.sin(4*np.pi*k/interval), np.cos(2*np.pi*k/interval),
                      np.sin(2*np.pi*k/interval)], axis=1)
    coef = np.linalg.lstsq(basis, x, rcond=None)[0]
    return complex(coef[1], -coef[2])


def _strip_off(tall, short, stretch, shift):
    """How far the strip `tall` (`legs.body_signals` `strips`, rows by channels) lies from `short` stretched down by
    1 + `stretch` and moved `shift` rows down: the mean absolute difference over the rows and channels it covers."""
    rows = np.arange(tall.shape[0], dtype=float)
    at = (rows - shift)/(1 + stretch)
    keep = (at >= 0) & (at <= rows[-1])
    made = np.stack([np.interp(at, rows, short[:, c]) for c in range(short.shape[1])], axis=1)
    return float(np.abs(tall - made)[keep].mean())


def body_depth(signals, interval):
    """The rows down the middle of the body at the top of its rise against the bottom (`signals`: `strips`,
    `legs.body_signals`): the frames where the rise and fall twice an interval `interval` frames long of the line over
    its lowest row (`over_feet`, as `own_beat` takes it) stands a half or more of the way to the top and to the bottom,
    each set's strips in a median. Returns, each fit (`_strip_off`) moved as it fits best: `need`, the zoom those frames'
    rise would be, of the body's height, and `risen`, in rows; `rigid`, how far the strip at the top lies from the one at
    the bottom as it stands; `zoom`, from that one zoomed by `need`; `ratio`, `zoom` of `rigid`, 1 or more where the zoom
    fits no better; `moved`, how far the zoom moves the strip at the bottom from itself, the most `zoom` could tell; and
    `frames`, how many frames stand in each set. None where no strip is read, the rise and fall is none, a set holds
    under two frames or the frames at the top are not the taller."""
    strips = signals.get('strips')
    if strips is None:
        return None
    line = over_feet(signals, interval)[0]
    rise = twice_an_interval(line, interval)
    if rise is None or rise == 0:
        return None
    reach = math.ceil(interval/2 - 0.5)
    at = np.arange(reach, line.size-reach)
    wave = (rise*np.exp(4j*np.pi*(at-reach)/interval)).real
    tall, short = at[wave <= -abs(rise)/2], at[wave >= abs(rise)/2]
    if tall.size < 2 or short.size < 2:
        return None
    height = float(np.median(signals['height']))
    risen = float(np.median(line[short]) - np.median(line[tall]))
    if risen <= 0:
        return None
    need = risen/height
    top, bottom = np.median(strips[tall], axis=0), np.median(strips[short], axis=0)
    most = max(STRIP_SHIFT_MIN, STRIP_SHIFT*height)
    shifts = np.arange(-most, most + 0.01, max(0.5, most/12))
    rigid, zoom = (min(_strip_off(top, bottom, stretch, c) for c in shifts) for stretch in (0.0, need))
    moved = min(_strip_off(bottom, bottom, need, c) for c in shifts)
    return {'need': need, 'risen': risen, 'rigid': rigid, 'zoom': zoom, 'ratio': zoom/max(rigid, 1e-6), 'moved': moved,
            'frames': [int(tall.size), int(short.size)]}


def own_beat(signals, interval):
    """The body's own rise and fall twice an interval `interval` frames long (`bob_word`), read off `signals`
    (`legs.strike_signals` `top`, `reach` and `height`, `legs.body_signals`) as `twice_an_interval` reads each: the top
    line's, less the lowest row's (the picture moving taken out) — the lowest solid row's or the lowest opaque row's,
    whichever leaves less: a shadow drawn translucent under the feet and fixed on the screen is the lowest solid row and
    does not move with the picture, the feet do — and, band by band of the upper body (`legs.body_signals` `bands`),
    less the zoom that band's width reads (the picture zooming taken out). A band reads a zoom as far as one moves it:
    the frame of the body's median height (`median_frame`) zoomed in as far as the line's rise and fall left against
    the lowest row lengthens the body (`legs.zoomed`) moves the band's width `zoom` pixels; a band it moves under
    BAND_FLOOR pixels is not seen, and reads nothing. A band seen reads, whatever its turn, the share of the zoom its
    width's rise and fall twice an interval makes: the zoom's part of it is not told from its parts' own swing, in step
    with the line, against it or a quarter of the beat off, and as much of the zoom as it reads, up to the whole of the
    line's, is taken out. A band seen holds still on the beat where it reads no more than 1 - BOB_SCALE: less it, no
    less than BOB_SCALE of the line's rise and fall is left. Returns, in rows up and down: `beat` (the top line's),
    `lowest`, `sole`, `less_lowest`, `bands` (per band: its `rows` below the first solid row, its `width` in the median
    frame and `zoom`, in pixels; `beat`, its width's rise and fall twice an interval, in pixels up and down; where it is
    seen, `reads`, `beat` of `zoom` — the share of the zoom making the line's rise and fall it reads, whatever its turn
    — and `less`, what is left of the line's rise and fall less as much of that zoom, else None), `less_bands`, the
    least `less` (None where no band is seen), `still_bands`, the bands seen that hold still on the beat, `depth`
    (`body_depth`), `whole` (True where the strip reads a body rising whole, False where it reads a zoom, None where it
    is not read: no `depth`, or `moved` under STRIP_FLOOR) and `own`: `less_lowest` where `whole` is True or no band is
    seen, else the smaller of `less_lowest` and `less_bands`. None where a line is not read."""
    height = float(np.median(signals['height']))
    top, lowest, sole = (twice_an_interval(signals[k], interval) for k in ('top', 'reach', 'sole'))
    if top is None or lowest is None or sole is None:
        return None
    left = min(top - lowest, top - sole, key=abs)
    rows, still = signals['band_rows'], signals['median_frame']
    widths = legs_mod.band_widths(still, rows)
    zoom = [z - w for z, w in zip(legs_mod.band_widths(legs_mod.zoomed(still, 1 + 2*abs(left)/height), rows), widths)]
    beats = [twice_an_interval([f[b] for f in signals['bands']], interval) for b in range(len(rows))]
    seen = [abs(z) >= BAND_FLOOR for z in zoom]
    reads = [2*abs(beat)/abs(z) if s else None for z, beat, s in zip(zoom, beats, seen)]
    less = [2*abs(left)*max(0.0, 1 - r) if r is not None else None for r in reads]
    less_bands = min((v for v in less if v is not None), default=None)
    depth = body_depth(signals, interval)
    whole = None if depth is None or depth['moved'] < STRIP_FLOOR else bool(depth['ratio'] >= BOB_RIGID)
    return {'beat': 2*abs(top), 'lowest': 2*abs(lowest), 'sole': 2*abs(sole), 'less_lowest': 2*abs(left),
            'bands': {'rows': [list(r) for r in rows], 'width': widths, 'zoom': zoom, 'beat': [2*abs(b) for b in beats],
                      'reads': reads, 'less': less},
            'less_bands': less_bands, 'still_bands': sum(1 for r in reads if r is not None and r <= 1 - BOB_SCALE),
            'depth': depth, 'whole': whole, 'own': 2*abs(left) if less_bands is None or whole else min(2*abs(left), less_bands)}


def over_feet(signals, interval):
    """The body's top line over its lowest row, per frame, and which row (`reach`, the lowest solid one, or `sole`, the
    lowest opaque one): whichever leaves less of the top line's rise and fall twice an interval, as `own_beat` takes it
    (the solid one where that is not read). The picture moving carries the body and its feet alike, on any beat, and
    the line over the feet holds still for it; the body rising and falling on its legs is left."""
    top = np.asarray(signals['top'], dtype=float)
    lines = {k: top - np.asarray(signals[k], dtype=float) for k in ('reach', 'sole')}
    left = {k: twice_an_interval(line, interval) for k, line in lines.items()}
    over = 'sole' if None not in left.values() and abs(left['sole']) < abs(left['reach']) else 'reach'
    return lines[over], over


def _share(rows, beat, name):
    """`rows` as a share of the rise and fall twice an interval of a line (`beat`, `own_beat`) named `name`, for a why:
    or, where that is none (under BEAT_STILL), the line holding still on the screen."""
    return f"{rows/beat:.0%}" if beat >= BEAT_STILL else f"{name} holding still"

def bob_word(signals, interval, *, feet=False, line='top'):
    """Whether one line of the body rises and falls twice an interval `interval` frames long in one place, read off
    `signals` (`legs.strike_signals` with `legs.body_signals`) without telling the legs apart: `line` (TOP_LINES, in
    pixel rows) on the screen, or with `feet` over the lowest row (`over_feet`, the picture's moving taken out first: the
    picture moving once a cut, which `off_flow` leaves whole, would hide the rise and fall twice it). The line less its
    flow (`off_flow`) ranges BOB_FLOOR of the body's median `height` or more and BOB_ROWS rows or more, and it is as it
    was half an interval on and turned over a quarter on (`crossings.correlation_on`, BOB_TWICE or over there and
    -BOB_TWICE or under here): it rises and falls twice an interval. And that rise and fall is the body's own
    (`own_beat`): what is left of it against the lowest row (solid or opaque, whichever leaves less) is at least BOB_OWN
    of it and over the floor; the line's own on the screen is at least BOB_OWN of what is left against the lowest row; a
    band of the upper body would show the picture zooming as far as that (seen, BAND_FLOOR pixels or more); the strip
    down the middle of the body reads a body rising whole (`whole`), or where the strip is not read, less as much of the
    zoom as each band seen reads, whatever its turn, as much is left again, and no less than BOB_SCALE of what is left
    against the lowest row — every band seen holds still on the beat; and no frame's lowest row is its last (`edge`).
    Held by default: where no band is seen, where the strip reads a zoom, or, it not read, where any band seen moves on
    the beat (its parts swinging across it, as arms do, or the picture zooming), the body's own is not told from a zoom,
    and nothing is witnessed. Then `says` `cycles` — a step each rise, two steps an interval; None otherwise, with why.
    This is one line in one place: the body's word on an interval is `body_word`'s. Returns the record: `swing` (the line
    left's range, of the body's height), `rows` (in rows), `floor`, `twice`, `over` (with `feet`: the row the line is read
    over), `half` and `quarter` (where read), `edge` (frames whose lowest row is their last), `body` (`own_beat`, in rows
    up and down, with `share`, `own` of `beat`, and `scale`, `less_bands` of `less_lowest`, None where no band is seen;
    `depth` as `own_beat` reads it, its measures only, `whole` being no part of the record: a hold is not told from a hold
    by it; where read), `says`."""
    if line != 'top':
        signals = {**signals, 'top': signals[line]}
    height = float(np.median(signals['height']))
    name = TOP_LINES[line]
    what, extent, word, drawn = name, f"{name}'s range", {}, signals['top']
    if feet:
        drawn, over = over_feet(signals, interval)
        what = f"{name} over its lowest {'opaque' if over == 'sole' else 'solid'} row"
        extent, word = f"the range of {what}", {'over': over}
    values = off_flow(drawn, interval)
    rows = float(values.max()-values.min()) if values.size else 0.0
    swing = rows/height if height > 0 else 0.0
    word = {'swing': round(swing, 4), 'rows': round(rows, 2), 'floor': BOB_FLOOR, 'twice': BOB_TWICE, **word}
    if not values.size:
        return {**word, 'says': None, 'why': f"{what} less its flow is not read: the clip is under an interval long"}
    if swing < BOB_FLOOR or rows < BOB_ROWS:
        return {**word, 'says': None, 'why': f"{extent} less its flow ({swing:.2%} of the body's height, {rows:.1f} "
                                             f"rows) is under {BOB_FLOOR:.1%} or {BOB_ROWS} rows: it does not rise and fall"}
    half, quarter = (crossings_mod.correlation_on(values, interval/k) for k in (2, 4))
    edge = sum(1 for e in signals['edge'] if e)
    beat = own_beat(signals, interval)
    word.update(half=None if half is None else round(half, 3), quarter=None if quarter is None else round(quarter, 3), edge=edge)
    if beat is not None:
        bands, depth = beat['bands'], beat['depth']
        word['body'] = {**{k: None if v is None else round(v, 2) for k, v in beat.items() if k not in ('bands', 'depth', 'whole')},
                        'bands': {'rows': bands['rows'], **{k: [None if v is None else round(v, 2) for v in bands[k]]
                                                            for k in ('width', 'zoom', 'beat', 'reads', 'less')}},
                        'share': round(beat['own']/beat['beat'], 3) if beat['beat'] >= BEAT_STILL else 0.0,
                        'scale': (None if beat['less_bands'] is None else
                                  round(beat['less_bands']/beat['less_lowest'], 3) if beat['less_lowest'] >= BEAT_STILL else 0.0),
                        'depth': None if depth is None else {'need': round(depth['need'], 5), 'risen': round(depth['risen'], 2),
                                                             **{k: round(depth[k], 5) for k in ('rigid', 'zoom', 'moved')},
                                                             'ratio': round(depth['ratio'], 3), 'frames': depth['frames']},
                        'least': BOB_OWN, 'scale_least': BOB_SCALE, 'band_floor': BAND_FLOOR}
    if half is None or quarter is None or half < BOB_TWICE or quarter > -BOB_TWICE:
        return {**word, 'says': None, 'why': f"{what} less its flow does not rise and fall twice an interval "
                                             f"({'not read' if half is None else f'{half:+.2f}'} half an interval on, "
                                             f"{'not read' if quarter is None else f'{quarter:+.2f}'} a quarter on: not at least "
                                             f"{BOB_TWICE:+g} and at most {-BOB_TWICE:+g})"}
    if edge:
        return {**word, 'says': None, 'why': f"its lowest row is the frame's last row in {edge} of {len(signals['edge'])} frames: "
                                             "the feet may run off the frame, and the picture moving is not read off them"}
    if beat is None:
        return {**word, 'says': None, 'why': f"{name}'s rise and fall twice an interval is not read against its lowest row "
                                             f"and upper body: under {BEAT_FRAMES} frames are left less the flow"}
    floor = max(BOB_ROWS, BOB_FLOOR*height)
    if beat['less_lowest'] < max(BOB_OWN*beat['beat'], floor):
        return {**word, 'says': None, 'why': f"its lowest row rises and falls with it: less the lowest row's, {name}'s rise "
                                             f"and fall twice an interval is {beat['less_lowest']:.1f} rows of {beat['beat']:.1f} "
                                             f"({_share(beat['less_lowest'], beat['beat'], name)}; at least {BOB_OWN:.0%} and {floor:.1f} "
                                             "rows): the picture moves with the body, and the body's own is not told from it"}
    if beat['beat'] < BOB_OWN*beat['less_lowest']:
        return {**word, 'says': None, 'why': f"its lowest row does the rising and falling: {name} does so twice an interval "
                                             f"{beat['beat']:.1f} rows on the screen, {_share(beat['beat'], beat['less_lowest'], name)} of "
                                             f"the {beat['less_lowest']:.1f} left against its lowest row (at least {BOB_OWN:.0%}), "
                                             "and the body's own is not told from it"}
    if beat['less_bands'] is None:
        return {**word, 'says': None, 'why': f"no band of its upper body would show the picture zooming: zoomed in as far as "
                                             f"{name} rises and falls twice an interval against its lowest row "
                                             f"({beat['less_lowest']:.1f} rows of {height:.0f}), no band's width moves "
                                             f"{BAND_FLOOR:g} px (at most {max(abs(z) for z in beat['bands']['zoom']):.2f}): "
                                             "the picture zooming with the body is not told from the body's own"}
    if beat['whole'] is False:
        depth = beat['depth']
        return {**word, 'says': None, 'why': f"the rows down the middle of its body fit a zoom as well as they fit standing: "
                                             f"at the top of its rise they lie {depth['zoom']:.4f} off those at the bottom zoomed "
                                             f"{depth['need']:.2%}, the rise of {depth['risen']:.1f} rows, and {depth['rigid']:.4f} "
                                             f"off them as they stand ({depth['ratio']:.2f} of it, at least {BOB_RIGID:g}): the "
                                             "picture may zoom with the body, and the body's own is not told from it"}
    if beat['whole'] is None and beat['less_bands'] < max(BOB_OWN*beat['beat'], floor, BOB_SCALE*beat['less_lowest']):
        a, b = legs_mod.BANDS[beat['bands']['less'].index(beat['less_bands'])]
        return {**word, 'says': None, 'why': f"its upper body's width changes with it: less as much of the zoom as its band "
                                             f"{a:.0%} to {b:.0%} of its height below its top reads, {name}'s rise and fall "
                                             f"twice an interval is {beat['less_bands']:.1f} rows of {beat['beat']:.1f} "
                                             f"({_share(beat['less_bands'], beat['beat'], name)}), {beat['less_bands']/beat['less_lowest']:.2f} "
                                             f"of the {beat['less_lowest']:.1f} left against the lowest row (at least "
                                             f"{BOB_OWN:.0%}, {floor:.1f} rows and {BOB_SCALE:g}), and the rows down the middle of its "
                                             f"body do not tell (a zoom as far as that moves them {_strip_moved(beat['depth'])}): the "
                                             "picture may zoom with the body, and the body's own is not told from it"}
    return {**word, 'says': 'cycles'}


def _strip_moved(depth):
    """How far a zoom as far as the rise moves the strip (`body_depth`), for a why: or that it is not read."""
    return "they are not read" if depth is None else f"{depth['moved']:.4f}, under {STRIP_FLOOR:g}"


def body_word(signals, interval):
    """The body's word on an interval `interval` frames long, a crossing interval (`read_cycles`) or a cut (`cut_word`):
    `says` `cycles` where one of its lines (TOP_LINES, the first that does) rises and falls twice an interval both on the
    screen and over its feet (`bob_word`, CUT_LINES), the same line in both; None otherwise. Returns the screen's record
    with `says`, where it says `top_line`, the line, and `feet`, the record over its feet, its `says` and `top_line` the
    same; where it says nothing, the top line's records, each with `why`: each line's reading in that place, or that it
    said so only in the other, a reading both lines give once."""
    read = []
    for line in TOP_LINES:
        if line in signals:
            words = {place: bob_word(signals, interval, feet=place == 'feet', line=line) for place in CUT_LINES}
            if all(word['says'] == 'cycles' for word in words.values()):
                return {**words['screen'], 'top_line': line, 'feet': {**words['feet'], 'top_line': line}}
            read.append((line, words))

    def why(place, line, words):
        other = next(p for p in CUT_LINES if p != place)
        return words[place].get('why') or (f"{TOP_LINES[line]} rises and falls twice an interval {CUT_LINES[place]}, "
                                           f"but not {CUT_LINES[other]}")
    said = {place: {**read[0][1][place], 'says': None, 'why': '; '.join(dict.fromkeys(why(place, *each) for each in read))}
            for place in CUT_LINES}
    return {**said['screen'], 'feet': said['feet']}


def _touch(a, b):
    """Whether two contacts ([first, last] column) share or touch a column."""
    return max(a[0], b[0]) <= min(a[1], b[1]) + 1


def _travel(order):
    """How far, in columns, the furthest anything standing on the lowest row (`ground`) slides along it while it stands
    there: each run there followed from frame to frame while it shares or touches a column with one run, and only one,
    of the next frame (two runs that meet, or one that parts, start afresh: two feet standing side by side), its
    middle's range over the frames it is followed."""
    chains, widest = [], 0.0
    for o in order:
        nxt = []
        for part in o['ground']:
            middle = (part[0]+part[1])/2
            on = [c for c in chains if _touch(c['part'], part)]
            go = on[0] if len(on) == 1 and sum(1 for p in o['ground'] if _touch(on[0]['part'], p)) == 1 else None
            lo, hi = (min(go['lo'], middle), max(go['hi'], middle)) if go else (middle, middle)
            nxt.append({'part': part, 'lo': lo, 'hi': hi})
            widest = max(widest, hi-lo)
        chains = nxt
    return widest


def _off_ground(order):
    """The frames whose feet are read apart but either of whose two sides is drawn over no column anything stands on
    the lowest row of (`ground`) in any frame of the clip: a tail's tip dipping into the foot band, say."""
    ground = [part for o in order for part in o['ground']]
    return [k for k, o in enumerate(order)
            if o['apart'] and not all(any(_touch(o[side], part) for part in ground) for side in ('left', 'right'))]


def order_word(order, span):
    """What the order the feet are drawn in (`order`, per frame: `legs.feet_order`) says of `span` frames — a crossing
    interval (`read_cycles`) or a cut (`cut_word`): `says` `two steps`, `four steps` or `one step` where the clip shows
    its two sides of feet are two legs and the side drawn lower changes at that rate (ORDER_WORDS, within ORDER_SLACK),
    else None with why (module notes before ORDER_ROWS). Returns the record: `apart` (the share of frames whose feet are
    seen apart, less `off_ground`: where any are, the frames read apart with a side drawn over no ground the clip stands
    on, `_off_ground`), `swing` (the depth's p10 to p90, in rows), `floor`, and where read `changes` (frames between
    which the lower side changes), `slides` (those whose contacts touch), `spacing` (the feet's median distance apart)
    and `travel` (`_travel`), in px, `sway` and `tilt` (the sway a tilt would need), `per` (changes per span)."""
    off = _off_ground(order)
    order = [{**o, 'apart': False} if k in off else o for k, o in enumerate(order)]
    seen = [o for o in order if o['apart']]
    apart = len(seen)/len(order) if order else 0.0
    word = {'apart': round(apart, 3), **({'off_ground': off} if off else {})}
    if apart < legs_mod.FEET_APART_SHARE or len(seen) < 3:
        return {**word, 'says': None, 'why': f'the feet are seen apart in {apart:.0%} of the frames (from '
                                              f'{legs_mod.FEET_APART_SHARE:.0%}): the order they are drawn in is not read'}
    height = float(np.median([o['height'] for o in seen]))
    depth = np.asarray([o['depth'] for o in seen], dtype=float)
    swing = float(np.percentile(depth, 90) - np.percentile(depth, 10))
    floor = max(ORDER_ROWS, legs_mod.REACH_MIN*height)
    word.update(swing=round(swing, 2), floor=round(floor, 2))
    if swing < floor:
        return {**word, 'says': None, 'why': f'one foot is drawn lower than the other by {swing:.1f} rows either way, under '
                                              f'{floor:.1f}: the feet are drawn level'}
    least = max(1.0, swing/8)
    sides, last, changes = [], None, []
    for k, o in enumerate(order):
        side = None if not o['apart'] or abs(o['depth']) < least else ('right' if o['depth'] > 0 else 'left')
        if side is not None:
            if last is not None and side != sides[last]:
                changes.append((last, k))
            last = k
        sides.append(side)
    slides = [[a, b] for a, b in changes if _touch(order[a][sides[a]], order[b][sides[b]])]
    spacing = float(np.median([abs(sum(o['right'])-sum(o['left']))/2 for o in seen]))
    travel = _travel(order)
    word.update(changes=[[a, b] for a, b in changes], slides=slides, spacing=round(spacing, 1), travel=round(travel, 1))
    if slides:
        a, b = slides[0]
        return {**word, 'says': None, 'why': f'the foot drawn lower on the {sides[a]} at frame {a} and the one on the '
                                              f'{sides[b]} at frame {b} touch at the ground ({len(slides)} of '
                                              f'{len(changes)} changes): one foot slides under the body from one side to '
                                              'the other, as seen from the side, and the sides are not two legs'}
    if travel >= ORDER_TRAVEL*spacing:
        return {**word, 'says': None, 'why': f'a foot standing on the lowest row slides {travel:.0f} px along it, '
                                              f'{travel/spacing:.0%} of the {spacing:.0f} px the feet are apart (from '
                                              f'{ORDER_TRAVEL:.0%}): a planted foot passes under the body to the other '
                                              'side, as seen from the side, and the sides are not two legs'}
    sway = float(np.percentile([o['sway'] for o in seen], 90) - np.percentile([o['sway'] for o in seen], 10))
    tilt = swing*ORDER_REACH*height/max(spacing, 1.0)
    word.update(sway=round(sway, 1), tilt=round(tilt, 1))
    if sway >= ORDER_SWAY*tilt:
        return {**word, 'says': None, 'why': f'its upper body sways {sway:.0f} px, {sway/tilt:.0%} of the {tilt:.0f} px a tilt '
                                              f'about its feet would carry it to draw one foot {swing:.0f} rows lower than '
                                              f'the other (from {ORDER_SWAY:.0%}): the picture may rock on its feet'}
    times = [(a+b)/2 for a, b in changes]
    if len(times) < 3:
        return {**word, 'says': None, 'why': f'the foot drawn lower changes sides {len(times)} times: too few to count'}
    mean = (times[-1]-times[0])/(len(times)-1)
    per = span/mean
    word['per'] = round(per, 3)
    widest = max(b-a for a, b in zip(times, times[1:]))
    for says, k in ORDER_WORDS:
        if abs(per/k-1) <= ORDER_SLACK and widest <= 2*mean:
            return {**word, 'says': says}
    return {**word, 'says': None, 'why': f'the foot drawn lower changes sides {per:.2f} times in {span:.1f} frames '
                                          f'(every {mean:.1f}, at most {widest:g} apart): not within {ORDER_SLACK:.0%} of '
                                          'two, four or one, each change at most two of their mean apart'}


def _order_why(feet):
    """Why the order of the feet (`order_word`) witnesses nothing: its own why, or the word it says instead."""
    return feet.get('why') or f"the foot drawn nearer changes sides {feet['per']:.2f} times: {feet['says']}"


def read_cycles(reading, distances, *, view=None, cue=None, cue_step=None, signals=None, order=None):
    """Whether the crossings the clip shows (`reading`, gap mode) mark its cycles, not its steps, read and kept on the
    reading (`Reading.mark_cycles`; crossings.py, "Crossings that mark cycles"). The feet need not meet on the screen
    at every pass: the far foot can pass hidden behind the near one, or a foot step toward or away from the camera and
    pass the other apart. Then they meet once a cycle, and the clip one interval between crossings on (`repeat_at`) has
    the same leg leading, not the other: it is nearer than half an interval on (the pose a step on) and as near as two
    intervals on within CYCLE_SLACK. Asked where a pass can hide (`reading.hides`, the far foot behind the near one) or
    where the `view` is given and is not the side.

    The picture is the same a step on as a cycle on where the legs are drawn alike, and so is everything else that does
    not tell the legs apart (a foot cue under a light fixed on the screen, how far apart the feet are drawn): it marks
    cycles only where a witness that reads no leg says the interval holds two steps — a line of the body (`signals`,
    `legs.strike_signals` with `legs.body_signals`) rising and falling twice an interval on the screen and over its feet,
    less its flow and of its own (`body_word`, `bob_word`: the picture drifting, swaying, shaking or zooming up and down
    as it pleases witnesses nothing, read against the lowest row and the rows down the middle of the body or the widths
    of the upper body; a lowest row on the frame's edge holds the word) — or the
    order the feet are drawn in (`order`, per frame: `legs.feet_order`; `order_word`), the foot drawn nearer changing
    sides twice an interval where the clip shows its two sides of feet are its two legs, not read from the side. The
    view's foot cue (`cue`, per frame, drawn in steps of `cue_step`; `cue_word`) only refuses: where it turns over one
    interval on, the other leg leads there, a step. With no witness the crossings stay on steps, as from the side, and
    the record names it (`unwitnessed`): where the feet do meet once a cycle, each step counted is a cycle. Returns
    what was read, None where it is not asked."""
    if reading.mode != 'gap' or not reading.readable:
        return None
    overlap, hides = reading.overlap, reading.hides
    if not hides and view in (None, SIDE_VIEW):
        return None
    d = reading.interval
    near = {name: repeat_at(distances, span) for name, span in (('half', d/2), ('one', d), ('two', 2*d))}
    said = {'asked': (f'the feet together {overlap:.2f} of an interval at a pass, over {HIDE_SHARE:g}: a pass can hide there'
                      if hides else f'the {view} view, the legs side by side'),
            'interval': round(d, 2), 'overlap': None if overlap is None else round(overlap, 3),
            **{k: None if v is None else round(v, 3) for k, v in near.items()}, 'slack': CYCLE_SLACK}
    if None in near.values():
        said.update(cycles=False, why='the clip is too short to be read two intervals on')
    elif not (near['one'] < near['half'] and near['one'] <= near['two']+CYCLE_SLACK):
        said.update(cycles=False, why=f"the clip one interval on ({near['one']:.2f} steps off) is not the same picture as half "
                                      f"an interval on ({near['half']:.2f}) and two on ({near['two']:.2f}, +{CYCLE_SLACK:g}) "
                                      "would make it: the crossings mark steps")
    else:
        picture = (f"the clip one interval on ({near['one']:.2f} steps off) is nearer than half an interval on ({near['half']:.2f}) "
                   f"and as near as two on ({near['two']:.2f}, +{CYCLE_SLACK:g})")
        word = None if cue is None or view is None else cue_word(cue, d, step=cue_step)
        bob = None if not signals else body_word(signals, d)
        feet = None if order is None or view == SIDE_VIEW else order_word(order, d)
        said.update({**({'cue': word} if word is not None else {}), **({'bob': bob} if bob is not None else {}),
                     **({'order': feet} if feet is not None else {})})
        if word is not None and word['says'] == 'steps':
            said.update(cycles=False, why=f"{picture}, but the view's foot cue turns over there ({word['one']:+.2f}, at most "
                                          f"{CUE_SWAPPED:+g}): the other leg leads, a step")
        elif bob is not None and bob['says'] == 'cycles':
            said.update(cycles=True, why=f"{picture}, and {TOP_LINES[bob['top_line']]} less its flow rises and falls twice an "
                                         f"interval on the screen and over its feet ({bob['half']:+.2f} half an interval on, at least "
                                         f"{BOB_TWICE:+g}; {bob['quarter']:+.2f} a quarter on, at most {-BOB_TWICE:+g}, on the "
                                         f"screen), the body's own ({bob['body']['own']:.1f} rows of {bob['body']['beat']:.1f} left "
                                         f"against its lowest row and upper body, {bob['body']['share']:.0%}; at least "
                                         f"{BOB_OWN:.0%}): two steps an interval, the feet meet once a cycle")
        elif feet is not None and feet['says'] == 'two steps':
            said.update(cycles=True, why=f"{picture}, and the foot drawn nearer changes sides {feet['per']:.2f} times an "
                                         f"interval, the clip's two sides of feet its two legs (none slides from one to the "
                                         f"other, the upper body sways {feet['sway']:.0f} px of the {feet['tilt']:.0f} a tilt "
                                         "would need): two steps an interval, the feet meet once a cycle")
        else:
            said.update(cycles=False, unwitnessed=True,
                        why=f"{picture}, but nothing that reads no leg witnesses two steps an interval "
                            f"({'no top line read' if bob is None else bob['why']}; "
                            f"{'the order of the feet is not read from the side' if feet is None else _order_why(feet)}): "
                            "a step on draws as a cycle on, as from the side, and the crossings mark steps")
    reading.mark_cycles(said)
    return said


def _cut_lines(signals, length):
    """Each place's word on a walk's cut `length` frames long (`cut_word`, CUT_LINES), the body's (`body_word`) read on
    the cut, on half of it and on twice it — the first it rises and falls twice on says two steps, four steps or one
    step; None where it does on none, with why (the reading on the cut). Returns, per place, {'says', 'bob': the reading
    on the cut, 'on': the reading that said four steps or one step, with its `interval`, 'why' where None}."""
    bob = body_word(signals, length)
    cut = {'screen': {k: v for k, v in bob.items() if k != 'feet'}, 'feet': bob['feet']}
    says, on = ('two steps' if bob['says'] == 'cycles' else None), None
    if says is None:
        for word, span in (('four steps', length/2), ('one step', 2*length)):
            read = body_word(signals, span)
            if read['says'] == 'cycles':
                says = word
                on = {'screen': {k: v for k, v in read.items() if k != 'feet'}, 'feet': read['feet']}
                on = {place: {'interval': round(span, 2), **read} for place, read in on.items()}
                break
    return {place: {'says': says, 'bob': cut[place], **({'on': on[place]} if on else {}),
                    **({'why': cut[place]['why']} if says is None else {})} for place in CUT_LINES}


def _feet_mark(signals, length, over):
    """Whether a walk's cut `length` frames long that stands (`cut_word`) is marked: its lowest row — the one its line
    over the feet is read over (`over`) — rises and falls twice the cut more than its top line does on the screen
    (`own_beat`, `signals` as the line that stood is read). The line over the feet is then the feet's as much as the
    body's: a foot hidden behind the other shakes it so once a cycle. A mark, not a hold. Returns {'over', 'feet', 'beat'},
    in rows up and down to two places (as `bob_word`'s `body`), where it is so; None where not, or not read."""
    beat = own_beat(signals, length)
    if beat is None:
        return None
    feet, top = round(beat['sole' if over == 'sole' else 'lowest'], 2), round(beat['beat'], 2)
    return {'over': over, 'feet': feet, 'beat': top} if feet > top else None


def cut_word(signals, length, *, order=None, declined=None):
    """The body's word on a walk's cut `length` frames long as it is about to be cut, whichever way it was chosen — the
    pixel search's, the step count's crossings read or not read (loop `_witnessed`). Legs drawn alike loop as well on
    one step as on two, and crossings read off feet that slide, or off a shadow under them on a picture that moves, can
    count half steps: the legs cannot say the cut is a cycle, and the body's own rise and fall, once a step, is read
    instead (`body_word`, off `signals`: `legs.strike_signals` with `legs.body_signals`): one of its lines saying so both
    on the screen and over its feet (CUT_LINES, `_cut_lines`). A picture moving once a cut, as a slow sway or drift whose
    turn is a cycle long does, is left whole by the flow taken out at the cut's length and hides the rise and fall twice
    it on the screen; a walk leaning further on its feet as it goes tilts the ground under them, and the line over its
    feet takes in the two feet's heights by turns. Either way the rise and fall shows in one place only, and neither is
    witnessed, as a head rising twice in one step is not. The body says:

    - `two steps` where it rises and falls twice the cut (`body_word` on the cut says cycles): the cut is a cycle;
    - `four steps` where it does so twice half the cut: the cut is two cycles;
    - `one step` where it does so twice two cuts — once the cut: the cut is one step of a walk, or a whole cycle of a
      body that rises and falls once a cycle (a limp, a body with no legs that bounces as it goes); the body cannot
      tell which, and it witnesses no cycle;
    - None where it reads none of these: nothing witnesses the cut whole cycles (`why`, each place's reading on the
      cut).

    Where it does not say the cut is a cycle or two, the order the feet are drawn in is read on it (`order`, per
    frame: `legs.feet_order`; `order_word`, as the step count reads it, None from the side): two steps or four, where
    the clip shows its two sides of feet are its two legs, and the cut stands on it (`by` ['order']); else the lines'
    word stands, the order's record kept (`order`). The order counts the changes in the cut, not whether its length
    closes them (a cut a sixth short of a cycle still reads two steps): where the cut is the search's, kept because none
    of the clip's two-step cuts closes (`declined`, the step count's record of the best of them), it does not stand on
    the order — a length the closing refused — and the order's word is kept as `read`.

    Where the cut stands (two steps or four) and its lowest row rises and falls twice it more than the line that stood
    does on the screen, the word carries a mark (`mark`, `_feet_mark`), and nothing more. Returns {'says', 'by': the
    places it was said in (where it says), 'lines': each place's reading (`_cut_lines`), 'on': the reading that said four
    steps or one step, on the screen, with its `line` and `interval`, 'why' where None, 'mark' where marked, 'order'
    where read}."""
    if not signals:
        return {'says': None, 'why': 'no top line read: a frame has no solid body'}
    lines = _cut_lines(signals, length)
    says = lines['screen']['says']
    if says not in ('two steps', 'four steps') and order is not None:
        feet = order_word(order, length)
        if feet['says'] in ('two steps', 'four steps') and declined:
            feet = {**feet, 'says': None, 'read': feet['says'], 'why': (
                f"the foot drawn nearer changes sides {feet['per']:.2f} times in the cut ({feet['says']}), but the cut is "
                f"the search's, kept where none of the clip's two-step cuts closes (the best, {declined['cut'][0]}/"
                f"{declined['cut'][1]}): the order counts its steps, not whether its length closes, and does not stand a "
                "length the closing refused")}
        if feet['says'] in ('two steps', 'four steps'):
            return {'says': feet['says'], 'by': ['order'], 'lines': lines, 'order': feet}
        if says is None:
            return {'says': None, 'lines': lines, 'order': feet,
                    'why': '; '.join([*dict.fromkeys(word['why'] for word in lines.values()), _order_why(feet)])}
    if says is None:
        return {'says': None, 'lines': lines, 'why': '; '.join(dict.fromkeys(word['why'] for word in lines.values()))}
    said = {'says': says, 'by': list(CUT_LINES), 'lines': lines}
    if says != 'two steps':
        said['on'] = {'line': 'screen', **lines['screen']['on']}
    if says == 'one step' and order is not None:
        said['order'] = feet
    stood = (said.get('on') or lines['screen']['bob'])['top_line']
    mark = None if says == 'one step' else _feet_mark({**signals, 'top': signals[stood]}, length, lines['feet']['bob']['over'])
    return {**said, **({'mark': mark} if mark else {})}


def unwitnessed(cycle):
    """Whether a walk's loop leaves with nothing to witness it whole cycles (UNWITNESSED): its crossings left on steps
    (`read_cycles`, `step_count.cycles.unwitnessed`), or its cut held (`cut_word`, loop `_witnessed`,
    `cut_witness.held`). `video-set` leaves it out of its state's cycle alignment."""
    return bool(((cycle.get('step_count') or {}).get('cycles') or {}).get('unwitnessed')
                or (cycle.get('cut_witness') or {}).get('held'))


def four_steps(cycle):
    """Whether a walk's loop is cut two cycles long, four steps read in it (FOUR_STEPS:
    `cut_word`, loop `_witnessed`, `cut_witness.says`). It stands; `video-set` leaves it out of its state's cycle
    alignment, its strip counting two steps."""
    return (cycle.get('cut_witness') or {}).get('says') == 'four steps'


def _rank(rows, *, flow_limit, seam_max, in_phase):
    """Two-step cuts, best first, and how many of the first passed the gates: in phase (`in_phase`), its top band calm
    at the wrap (`_calm`: a thin part the flow hardly sees, a staff, does not jump), its wrap over its step (`ratio`) at
    most `seam_max` (what the seam gate holds a loop to) and both seam frames' flow off under `flow_limit`, the worse
    within SEAM_FLOW_BAND of the smoothest such cut's, then the wrap nearest the clip's own way on (`_closure`); after
    those the rest, in phase first, then calm, then under the flow limit, then under `seam_max`, then by the worse flow."""
    gated = [row for row in rows if in_phase(row) and _calm(row) and row['ratio'] <= seam_max and _worse(row) < flow_limit]
    best = min((_worse(row) for row in gated), default=None)
    band = sorted((row for row in gated if _worse(row) <= best + SEAM_FLOW_BAND),
                  key=lambda row: (row['closure'] is None, row['closure'] or 0.0, _worse(row), row['start'], row['length']))
    rest = sorted((row for row in rows if row not in band),
                  key=lambda row: (not in_phase(row), not _calm(row), _worse(row) >= flow_limit, row['ratio'] > seam_max, _worse(row),
                                   row['start'], row['length']))
    return band + rest, len(band)


def _gates(tried, seam_max):
    """A cut read before it is cut passes its seam gate (`seam` at most `seam_max`) and the seam pop gate does not
    refuse it (`pop_gate`, where it was read)."""
    return tried['seam'] <= seam_max and tried.get('pop_gate') != 'refused'


def by_steps(distances, trajectory, reading, *, found, refused, min_len, max_len, seam_flow, wrap_pop, seam_gate, flow_limit,
             seam_max, gait_floor, periodicity_min, signals, pop_gate=None, turn=None):
    """The cut the clip's own steps choose (`reading`, crossings.Reading, whose crossings mark steps or cycles as
    `read_cycles` read them), after the pixel search: `found`, its cut, or `refused`, why it found none. Returns (the
    cut, or None where the search's word stands and it found none; `step_count`, the record).

    - The clip does not show its steps (`reading.readable` false: no feet seen, a lean that is not feet stepping,
      under two steps): the search's cut, as it chose it.
    - The search's cut covers two steps (`two`) and its wrap joins the walk at its own point (`reading.in_phase`):
      that cut, as it chose it.
    - Else the cuts [min_len, max_len] frames long that cover two steps, any start, however long — the window that
      binds the search binds no count; none, and the search's word stands. Those in phase, if any, else all, are
      read at the wrap (`seam_flow(start, length)`: the flow off of the cut's last and first frames; `wrap_pop`, the
      top band's jump, repair.seam_pop) and ranked (`_rank`). Of the first SEAM_TRIES the first whose seam the seam
      gate passes (`seam_gate`: its wrap over its step as the gate will read the cut, at most `seam_max`) is taken,
      where none the first, and the gate says whether it closes (`method: observed-two-steps-v1`). The pixel
      search's own repeat rule is not asked: a part off the legs' beat (a tail) repeats on its own and its seam is
      the flow repair's; `repeat` records it.
    - A search's cut the count does not refute (`two` out of phase, `uncertain`) whose own seam the seam gate passes
      is replaced only by a two-step cut that closes: one of the cuts that passed the gates (`_rank`) whose seam the
      seam gate passes. Where none does, the search's cut, as it chose it (`declined`: the best two-step cut and why
      it does not close). A search's cut the gate refuses too gives way as before: the two-step cut's wrap may still
      be closed by the overlapped wrap (repair.overlap_wrap), and the gate says whether it is.

    With `pop_gate` (the seam pop gate's reading before the cut, `_pop_gate`), a cut the seam gate passes is read for
    its top band too, and passes only where the seam pop gate would not refuse it: of the two-step cuts the first
    that passes both is taken, and the search's cut stands only where both pass it (`pop`, `pop_gate` beside `seam`).

    A cut taken on two steps records `steps` (count 2, by crossings): `video-cycle-align` reads a return half way
    as its step, and the step screen's pixel suspicion of one step (`step_screen`) is settled, kept as
    `pixel_suspect`.

    With `turn` (body_turn.Turn), a two-step cut that holds a frame of a body turn is not one of them (`turned_out`
    counts those left out), as the search refused such a window."""
    record = reading.record()
    record['search'] = ({'cut': [found['start'], found['length']]} if found is not None else {'refused': str(refused)})
    if not reading.readable:
        record['chosen_by'] = 'search'
        if found is not None:
            record['cut'] = reading.judge(found['start'], found['length'])
        return found, record
    searched = None
    if found is not None:
        searched = reading.judge(found['start'], found['length'])
        record['search'].update({k: searched[k] for k in ('verdict', 'code', 'k', 'phase') if k in searched})
        if reading.in_phase(searched):
            record.update(chosen_by='search', cut=searched)
            return _two_steps(found, record), record
    twos = reading.twos(min_len, max_len)
    if turn is not None:
        kept = [counted for counted in twos if turn(*counted['cut']) is None]
        record['turned_out'] = len(twos)-len(kept)
        twos = kept
    record['two_cuts'] = len(twos)
    if not twos:
        record['chosen_by'] = 'search'
        if found is not None:
            record['cut'] = reading.judge(found['start'], found['length'])
        return found, record
    pool = [row for row in twos if reading.in_phase(row)]
    record['in_phase'] = len(pool)
    rows = []
    for counted in pool or twos:
        start, length = counted['cut']
        step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
        rows.append({'start': start, 'length': length, 'phase': counted['phase'], 'counted': counted,
                     'seam_flow': seam_flow(start, length), 'top': wrap_pop(start, length), 'closure': _closure(distances, start, length),
                     'ratio': float(distances[start+length-1, start])/step if step > 1e-10 else math.inf})
    ranked, band = _rank(rows, flow_limit=flow_limit, seam_max=seam_max, in_phase=lambda row: reading.in_phase(row['counted']))
    tried = []
    for row in ranked[:SEAM_TRIES]:
        tried.append({'start': row['start'], 'length': row['length'], 'seam': round(seam_gate(row['start'], row['length']), 3)})
        if pop_gate is not None and tried[-1]['seam'] <= seam_max:
            tried[-1].update(_pop_said(pop_gate(row['start'], row['length'])))
        if _gates(tried[-1], seam_max):
            break
    passed = [t for t in tried if _gates(t, seam_max)]
    picked = next(row for row in ranked if (row['start'], row['length']) == ((passed[0]['start'], passed[0]['length']) if passed
                                                                           else (tried[0]['start'], tried[0]['length'])))
    record['seam_gate'] = {'max': seam_max, 'tried': tried}
    start, length = picked['start'], picked['length']
    if searched is not None and searched['verdict'] != 'refuted' and not (passed and ranked.index(picked) < band):
        # A cut the count does not refute, and the seam gate passes, stands until a two-step cut closes in its place.
        own = round(seam_gate(found['start'], found['length']), 3)
        record['search']['seam'] = own
        if pop_gate is not None and own <= seam_max:
            record['search'].update(_pop_said(pop_gate(found['start'], found['length'])))
        if _gates(record['search'], seam_max):
            record.update(chosen_by='search', cut=searched, flow_limit=flow_limit, seam_max=seam_max, declined={
                'cut': [start, length], 'phase': picked['phase'], 'gated': ranked.index(picked) < band,
                'seam': next(t['seam'] for t in tried if (t['start'], t['length']) == (start, length))})
            return (_two_steps(found, record) if searched['verdict'] == 'two' else found), record
    record.update(chosen_by='two steps', cut=picked['counted'], gated=bool(band), flow_limit=flow_limit, seam_max=seam_max)
    # The pixel search's own word on this cut: its repeat, read as `detect` reads a window it refused.
    radius, js = _around(start, length)
    measured = _measure(distances, trajectory, start, length, _profile(distances, js, radius, max(6, min_len), length))
    if measured is not None:
        over = measured['context_repeat_over_step']
        record['repeat'] = {'over_step': round(over, 3), 'max': REPEAT_OVER_STEP_MAX,
                            **({'why': f'about its start the clip a cut length on is {over:.2f} playback steps off, over '
                                       f'{REPEAT_OVER_STEP_MAX}: the pixel search refuses it (repeat error over two steps) — a part '
                                       'off the legs\' beat repeats on its own'} if over > REPEAT_OVER_STEP_MAX else {})}
    cycle = {'start': start, 'length': length, 'period_local': length, 'ratio': picked['ratio'],
             'closure': picked['closure'], 'seam_flow': picked['seam_flow'], 'top': picked['top'], 'kind': 'periodic', 'method': 'observed-two-steps-v1',
             'repeat': 'two steps seen in the clip, joined at the walk\'s own point',
             'period_global': None, 'periodicity_min': periodicity_min, 'review_recommended': not band,
             'candidate_count': len(rows),
             'candidates': [{'start': row['start'], 'length': row['length'], 'phase': row['phase'], 'seam_flow': row['seam_flow']['off'],
                             'seam_pop': row['top'].get('pop'), 'ratio': round(row['ratio'], 3),
                             'closure': None if row['closure'] is None else round(row['closure'], 3)}
                            for row in ranked[:REFUSED_SHOWN]]}
    cycle['fundamental'], cycle['step_screen'] = _screens(distances, cycle, lo=max(6, min_len), hi=max(length, min_len),
                                                          gait_floor=gait_floor, periodicity_min=periodicity_min, signals=signals)
    return _two_steps(cycle, record), record


def _two_steps(cycle, record):
    """A cut the crossings count two steps: `steps` recorded, the step screen's pixel suspicion settled."""
    cycle.setdefault('steps', {'count': 2, 'by': 'crossings'})
    screen = cycle.get('step_screen')
    if screen and screen.get('suspect'):
        screen.update(suspect=False, pixel_suspect=True, settled_by='crossings',
                      why=f"two steps counted on the clip's crossings (cycle.step_count); the pixels alone: {screen['why']}")
    return cycle


def step_verdict(cycle):
    """The step count's warnings, in words: crossings left on steps because nothing witnesses two steps an interval
    where the clip one interval on is the same picture (`read_cycles`, `unwitnessed`), a cut taken on two steps whose
    seam the pixel search refuses (a part off the legs' beat) or that passed no gate, a search's cut kept where no
    two-step cut closes, and a clip that shows its steps but no cut of two — and the body's word where it holds the cut
    (`held_verdict`), crossings read or not."""
    counted = cycle.get('step_count')
    if not counted or not counted.get('readable'):
        return held_verdict(cycle)
    s, L = cycle['start'], cycle['length']
    out = []
    said = counted.get('cycles') or {}
    if said.get('unwitnessed'):
        bob = said.get('bob')
        out.append(f"the clip one interval on is the same picture, as a walk whose feet meet once a cycle draws it, but nothing "
                   f"that reads no leg witnesses it ({'no top line read' if bob is None else bob['why']}): its crossings are "
                   f"counted as steps, as from the side — where its feet do meet once a cycle, each step counted in the cut "
                   f"{s}/{L} is a cycle ({UNWITNESSED})")
    if counted.get('chosen_by') == 'two steps':
        if not counted.get('gated'):
            out.append(f"the cut {s}/{L} covers two steps by the clip's crossings, but no two-step cut joins the walk within "
                       f"its phase, its top calm, its wrap within {counted['seam_max']:g} of its step and its seam under the flow "
                       f"limit {counted['flow_limit']:g} (phase {counted['cut']['phase']:+.1f} frames, wrap {cycle['ratio']:.2f}, "
                       f"seam flow {cycle['seam_flow']['off']}, top {cycle['top'].get('pop', cycle['top'].get('skipped'))}): "
                       "a part on a beat of its own closes on no cut of two steps — look at the wrap")
        if 'why' in counted.get('repeat', {}):
            worst = int(np.argmax(cycle['seam_flow']['off']))
            out.append(f"the cut {s}/{L} covers two steps by the clip's crossings, and {counted['repeat']['why']}; the seam's "
                       f"worst window is {cycle['seam_flow']['window'][worst]} of the body's box, turning "
                       f"{cycle['seam_flow']['turn'][worst]:+.2f} there — the flow repair reads it")
    elif 'declined' in counted:
        declined = counted['declined']
        out.append(f"the clip shows cuts of two steps, but none closes — in phase, its top calm, its wrap within "
                   f"{counted['seam_max']:g} of its step, its seam under the flow limit {counted['flow_limit']:g} and the seam "
                   f"gate passed (the best, {declined['cut'][0]}/{declined['cut'][1]}: phase {declined['phase']:+.1f} frames, "
                   f"seam {declined['seam']:g}): the search's cut {s}/{L} stands, {counted['cut']['verdict']} by the count "
                   f"({counted['cut']['why']})")
    elif 'two_cuts' in counted and counted['two_cuts'] == 0:
        turned = f" that leaves the body's turn out ({counted['turned_out']} hold it)" if counted.get('turned_out') else ''
        out.append(f"the clip shows its steps (crossings {counted['crossings']}) but no cut of two{turned}: the cut {s}/{L} is "
                   f"{counted['cut']['verdict']} ({counted['cut']['why']}) — count its steps")
    return out + held_verdict(cycle)


def held_verdict(cycle):
    """The body's word on the cut, where it holds it (`cut_witness`, loop `_witnessed`), in words: a cut nothing
    witnesses whole cycles, or one the body rises and falls once in — one step of a walk, or a whole cycle of a body
    that does so once a cycle — kept as it was chosen, each line's reading named (CUT_LINES). Each names UNWITNESSED.
    A cut held on one step whose record names no line it was read on (no `by` or `lines`) is refused by name
    (ValueError): the line it rose and fell once on cannot be named."""
    said = cycle.get('cut_witness') or {}
    if said.get('held') != 'unwitnessed':
        return []
    s, L = said['cut']
    counted = cycle.get('step_count') or {}
    basis = (f"the step count reads no feet ({counted.get('why', 'no reading')})" if not counted.get('readable')
             else "the clip's crossings cannot say it either: read off feet that slide, or off a shadow under them on a "
                  "picture that moves, they count half steps")
    if said['says'] is None:
        return [f"nothing that reads no leg witnesses the cut {s}/{L} whole cycles ({said['why']}): {basis} — where the "
                f"walk's legs are drawn alike, a step loops as a cycle does, and the loop may be half its cycle "
                f"({UNWITNESSED})"]
    if 'by' not in said or 'lines' not in said:
        raise ValueError(f"the cut witness of {s}/{L} says one step and names no line it was read on (no `by` or "
                         "`lines`): held_verdict names the line a cut held on one step rose and fell once on, as "
                         "cut_word records it (CUT_LINES); cut the loop again to have its witness so recorded")
    on = said['on']
    where = ' and '.join(CUT_LINES[line] for line in said['by'])
    return [f"nothing that reads no leg witnesses the cut {s}/{L} whole cycles: {TOP_LINES[on.get('top_line', 'top')]} {where} rises and "
            f"falls of its own once in it ({on['half']:+.2f} a cut on, {on['quarter']:+.2f} half a cut on) — one step of a "
            f"walk whose legs are drawn alike, half its cycle, or a whole cycle of a body that rises and falls once a cycle "
            f"(a limp, a body with no legs that bounces as it goes) — and {basis}. The cut is kept as chosen: cut again two "
            f"long, it would loop two of such a body's cycles ({UNWITNESSED})"]
