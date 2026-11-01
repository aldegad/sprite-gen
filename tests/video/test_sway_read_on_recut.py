"""`video-sway --read-on` with `--read-on-cut`: regions read on one cut of a clip, moved to another cut of the same clip.

A clip cut twice (a cut, and a cut again from the same clip at another start and crop — not a new take) holds the same
source frames in other cells and at other places. The regions read on the first cut's cell 0, with the region mask drawn
there, are paired by the source frame that cell is with the cell of the loop's cut that is that frame, moved onto it by
the whole px the two crops set that frame apart, the mask pixel for pixel, and carried from there; on a loop aligned from
its cut, through the alignment's record of where each cell is from. Each crop is taken on the frame as filmed: less where
its cut's anchor moved that frame (the body anchor's ramp, the motion anchor's correction).

Each cut is read by its record of its cells alone, as video-loop writes it in the strip's record, bound to the strip's
PNG by its sha256 (`cell_record`): the source frame each cell is, where its anchor moved it, the factor the size hold and
the gait fallback scaled it by, and whether its pixels are that frame as filmed but for that move; on a loop aligned from
its cut, the cut's own record, which the alignment keeps beside the cut's frames. A cut with no such record (one
video-loop cut before it wrote one), a record of another strip or one that does not give its cells as video-loop writes
them (naming the field), a cut whose frame the loop's cut does not hold, a cut of another clip, a loop aligned from its
cut named by --read-on-cut, a cell scaled or not cut as filmed, and two paired cells that are not the same pixels where
they overlap at the shift are refused no-match, saying which, and leave the loop as it was: a record changed to call a
cell the frame as filmed is held to that frame's pixels. Synthetic figures only: the read-on test's runner, cut from a
clip of two strides at two starts and two crops, by hand or by video-loop; the walk away from the camera of the gait
fallback's tests, cut by video-loop as a walk; and the ghost walk of the ghost repair's tests.

Each case first reads what a sway writes with the new key (its cells, its record, whether it refuses and what it says),
so on a release without it the same input fails on what comes out.
"""
import json
import math

import numpy as np
import pytest
from PIL import Image
from sprite_gen import cli
from sprite_gen.video import align, loop, motion_anchor, sway

from tests.video.test_follow_read_on import (Blend, H, N, PATCH, SHIFT, W, aligned, cells_of, copy, cut, files, meta_of, runner,
                                             sha)
from tests.video.test_gait_fallback import walking_away
from tests.video.test_ghost_given_way import _cut as ghost_cut, _keyed as ghost_keyed
from tests.video.test_loop_repair import _no_rife
from tests.video.test_sway_region import mask_png

CLIP = 2*N  # the clip's frames: the runner's stride twice
FPS = 24
CR = 'cell_record'  # the strip record's record of its cells (`loop.CELL_RECORD`)


def clip():
    return [runner(k) for k in range(CLIP)]


def bbox(frames):
    """The union of the frames' visible pixels, as video-loop crops a cut: left, top, right, bottom (last excluded)."""
    seen = np.zeros(np.asarray(frames[0]).shape[:2], bool)
    for frame in frames:
        seen |= np.asarray(frame)[..., 3] > 0
    ys, xs = np.nonzero(seen)
    return int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1


def as_cut(loop_dir, start, *, source='clip-a', made=(), cells=True, name='run'):
    """A loop's record as video-loop writes it of a cut from source frame `start` of the clip `source`: the clip and the
    cut (`source_cut`, `cycle_drawings`), and where `cells`, the record of each cell (`cell_record`, bound to the strip's
    PNG): the source frame it is, moved by no anchor, scaled by no hold, as filmed but where it is in `made`."""
    path = loop_dir/f'{name}.strip.json'
    meta = json.loads(path.read_text())
    n = meta['frames']
    meta['source_cut'] = {'source': {'sha256': source, 'frames': CLIP, 'fps': FPS}, 'start': start, 'length': n,
                          'samples': [start+k for k in range(n)]}
    meta['cycle_drawings'] = {'start': start, 'length': n}
    if cells:
        meta[CR] = {'strip_sha256': sha(loop_dir/f'{name}.strip.png'), 'cells': [
            {'source_frame': start+k, 'anchor_move_px': [0, 0], 'scale': {'size_hold': 1.0, 'gait_fallback': 1.0},
             'as_filmed': k not in made} for k in range(n)]}
    path.write_text(json.dumps(meta, indent=2)+'\n')
    return loop_dir


def cut_of(path, start, rect, *, source='clip-a', record=True, name='run', anchor='none', made=(), cells=True):
    """A loop directory as video-loop leaves one, cut from the clip at `start` with the crop `rect` at scale 1: its strip,
    and its record with how its frames were anchored (`foot_anchor`; None: not said), and where `record`, the source
    frame each cell is and the record of each cell (`as_cut`, none where not `cells`). A cell in `made` is drawn between
    the frames either side of its own, half of each, as an interpolator makes it, and its record says it is not the
    frame as filmed."""
    frames = clip()
    x0, y0, x1, y1 = rect
    w, h = x1-x0, y1-y0
    strip = Image.new('RGBA', (w*N, h))
    for k in range(N):
        cell = (Image.blend(frames[(start+k-1) % CLIP].crop(rect), frames[(start+k+1) % CLIP].crop(rect), 0.5) if k in made
                else frames[(start+k) % CLIP].crop(rect))
        strip.alpha_composite(cell, (k*w, 0))
    meta = {'frames': N, 'sample_indices': list(range(N)), 'source_rect': list(rect), 'w': w, 'h': h, 'kind': 'periodic',
            'loop': True, 'scale': 1.0, 'delay_ms': round(1000/FPS, 2), 'cycle_frames': N, 'cycle_seconds': N/FPS,
            'subsampled': False} | ({} if anchor is None else {'foot_anchor': anchor})
    path.mkdir(parents=True)
    strip.save(path/f'{name}.strip.png')
    (path/f'{name}.strip.json').write_text(json.dumps(meta, indent=2)+'\n')
    return as_cut(path, start, source=source, made=made, cells=cells, name=name) if record else path


GONE = object()  # what a record does not hold (`recorded`)


def recorded(loop_dir, *at, to, name='run'):
    """The loop's record changed at the keys `at` (the record's, then each one's within it) to `to` (GONE: taken out; a
    function: of what is there)."""
    path = loop_dir/f'{name}.strip.json'
    meta = json.loads(path.read_text())
    *within, last = at
    holder = meta
    for key in within:
        holder = holder[key]
    if to is GONE:
        del holder[last]
    else:
        holder[last] = to(holder[last]) if callable(to) else to
    path.write_text(json.dumps(meta, indent=2)+'\n')
    return loop_dir


def patch_at(frame_index, rect):
    """The runner's chest patch in the cell of source frame `frame_index` cut with `rect`: its centre, and its pixels."""
    phase = 2*math.pi*(frame_index+SHIFT)/N
    cx, cy = 80+round(3*math.sin(phase))-rect[0], 92+round(5*math.cos(2*phase))-rect[1]
    h, w = rect[3]-rect[1], rect[2]-rect[0]
    yy, xx = np.mgrid[0:h, 0:w]
    return (cx, cy), (xx-cx)**2+(yy-cy)**2 <= 17**2


def moved(mask, shift, rect):
    """`mask` moved by the whole px `shift` onto a cell cut with `rect`."""
    out = np.zeros((rect[3]-rect[1], rect[2]-rect[0]), bool)
    ys, xs = np.nonzero(mask)
    out[ys+shift[1], xs+shift[0]] = True
    return out


def walked(path, step, size):
    """The clip keyed as `video-frames` leaves it, one PNG a frame: the runner of frame k laid `step` px further (across,
    down) each frame, on a canvas of `size`."""
    path.mkdir(parents=True)
    for k in range(CLIP):
        frame = Image.new('RGBA', size)
        frame.paste(runner(k), (step[0]*k, step[1]*k))
        frame.save(path/f'frame-{k:03d}.png')
    return path


def loop_cut(clip_dir, path, start, anchor, *, length=N, state=None, size_hold='off', **kw):
    """`video-loop` on the keyed clip: the cut given (--cycle fixed from `start`, `length` frames long), anchored by
    `anchor`, of the `state` given (a walk held to one size where `size_hold` is auto), its report beside its strip."""
    loop.run_loop(clip_dir, path, fps=FPS, state=state, min_len=None, max_len=None, n_out=None, seam_max=100, name='run',
                  report_path=None, cycle_mode='fixed', start=start, length=length, anchor=anchor, repair='off',
                  size_hold=size_hold, seam_pop_max=None, wrap_blend='off', strip_height=520, **kw)
    return path


def patch_in(loop_dir, cell):
    """The runner's chest patch, its own colour's pixels, in a cell of a loop's strip as drawn."""
    return np.all(cells_of(loop_dir)[cell] == PATCH, axis=2)


def read_on(a, drawn=None):
    """A region on the chest patch of `a`'s cell 0 (or on the pixels `drawn` there) and its mask, those pixels, asked
    with `--read-on-cut a`: the region and the request's keywords."""
    drawn = patch_in(a, 0) if drawn is None else drawn
    ys, xs = np.nonzero(drawn)
    return (float(xs.mean()), float(ys.mean()), 18.0, 18.0), dict(
        region_mask=mask_png(a.parent/f'{a.name}-mask.png', drawn), read_on=sha(a/'run.strip.png'),
        read_on_cut=a/'run.strip.png', **KW)


def read_on_cut(a, b):
    """A region on the chest patch of `a`'s cell 0 and its mask, those pixels, asked on `b` with `--read-on-cut a`: the
    region, the request's keywords, and the answer solved on `b`'s strip as cut."""
    region, kw = read_on(a)
    answer = sway.solve(sway.read_request(b, [region], **kw), Image.open(b/'run.strip.png').convert('RGBA'),
                        strip_name='run.strip.png', strip_sha256=sha(b/'run.strip.png'))
    return region, kw, answer


def refused(loop_dir, kw, said, region=None):
    """`video-sway` on `loop_dir` asked `kw` (`read_on`), refused with `said` (the refusal after the regions were read on
    another cut, to its end), the loop as it was."""
    region = region or (0.0, 0.0, 18.0, 18.0)
    before = files(loop_dir)
    with pytest.raises(SystemExit, match=r"no-match — the regions were read on another cut, " + said
                                         + r"; locate them again on this strip$"):
        sway.sway_loop(loop_dir, [region], **kw)
    assert files(loop_dir) == before


@pytest.fixture
def cuts(tmp_path):
    """The first cut (`a`, at frame 4, cropped to the clip's frames) and its region mask on its cell 0, the patch."""
    frames = clip()
    tight = bbox(frames)
    a = cut_of(tmp_path/'a', 4, tight)
    (cx, cy), patch = patch_at(4, tight)
    return a, tight, (cx, cy, 17.0, 17.0), mask_png(tmp_path/'mask.png', patch)


def wider(rect):
    """`rect` cropped again 3 px wider left, 2 px higher, 2 px wider right and 1 px lower."""
    return (rect[0]-3, rect[1]-2, rect[2]+2, rect[3]+1)


KW = dict(gain=1.0, on_fold='lower', region_shrink=0)
PAIRED = r"whose cell 0 is source frame 4 \(this loop's cut's cell 3\), "  # the read cut from 4, this loop's from 1
NOT_ANCHORED = (r": its pixels at the end of the cut are not its pixels anchored, before any repair, or a step held the "
                r"clip's frames to one size")


# ---------------------------------------------------------------- moved by the source frame

def test_on_a_cut_again_at_the_same_frame_the_regions_are_the_ones_drawn_there_moved_by_the_crop(cuts, tmp_path):
    a, tight, region, mask = cuts
    wide = wider(tight)
    loop_dir = cut_of(tmp_path/'b', 4, wide)
    record = sway.sway_loop(loop_dir, [region], region_mask=mask, read_on=sha(a/'run.strip.png'),
                            read_on_cut=a/'run.strip.png', **KW)
    read_on = record['read_on']
    assert read_on['relation'] == 'recut' and read_on['policy'] == read_on['pair_policy'] == sway.RECUT_POLICY
    assert (read_on['source_frame'], read_on['cut_cell'], read_on['base_cell'], read_on['shift_px']) == (4, 0, 0, [3, 2])
    assert read_on['cut']['sha256'] == sha(a/'run.strip.png') and read_on['drift_px'] == [0.0, 0.0]
    # The same sway asked on the loop's own cell 0 with the mask and the region moved there by hand: byte for byte.
    by_hand = cut_of(tmp_path/'by-hand', 4, wide)
    shifted = mask_png(tmp_path/'shifted.png', moved(np.asarray(Image.open(mask).getchannel('A')) >= 128, [3, 2], wide))
    plain = sway.sway_loop(by_hand, [(region[0]+3, region[1]+2, *region[2:])], region_mask=shifted, **KW)
    assert (loop_dir/'run.strip.png').read_bytes() == (by_hand/'run.strip.png').read_bytes()
    assert record['dx_px'] == plain['dx_px'] and record['dy_px'] == plain['dy_px']


def test_on_a_cut_again_at_another_start_the_mask_is_moved_onto_the_cell_that_is_that_frame(cuts, tmp_path):
    a, tight, region, mask = cuts
    wide = wider(tight)
    loop_dir = cut_of(tmp_path/'b', 1, wide)  # holds source frames 1 to N: frame 4 is its cell 3
    source = cells_of(loop_dir)
    request = sway.read_request(loop_dir, [region], region_mask=mask, read_on=sha(a/'run.strip.png'),
                                read_on_cut=a/'run.strip.png', **KW)
    data = (loop_dir/'run.strip.png').read_bytes()
    answer = sway.solve(request, Image.open(loop_dir/'run.strip.png').convert('RGBA'), strip_name='run.strip.png',
                        strip_sha256=sha(loop_dir/'run.strip.png'))
    assert (answer.read_on['cut_cell'], answer.read_on['base_cell'], answer.read_on['shift_px']) == (3, 3, [3, 2])
    drawn = np.asarray(Image.open(mask).getchannel('A')) >= 128
    # The mask on the cell it was moved to: the pixels drawn, moved by the whole shift, none grown or lost.
    assert np.array_equal(answer.mask.worn[0], moved(drawn, [3, 2], wide))
    assert answer.carry(3) == (0.0, 0.0) and answer.ask.regions[0][:2] == (region[0]+3, region[1]+2)
    record = sway.sway_loop(loop_dir, [region], region_mask=mask, read_on=sha(a/'run.strip.png'),
                            read_on_cut=a/'run.strip.png', **KW)
    assert record['read_on']['base_cell'] == 3 and (loop_dir/'run.strip.png').read_bytes() != data
    # Outside the region and its soft edge every visible pixel keeps its bytes, in every cell.
    out = cells_of(loop_dir)
    for k in range(N):
        _, read = answer.masked_cell(k)
        outside = (source[k][..., 3] > 0) & ~read['region'] & ~read['soft']
        assert np.array_equal(out[k][outside], source[k][outside]), k


def test_a_cell_its_record_calls_scaled_or_not_as_filmed_leaves_another_cell_paired(cuts, tmp_path):
    # Only the two cells paired are read for the frame as filmed: this loop's cut's cell 5 scaled and not as filmed, and
    # the cut's cell 2 not as filmed, leave frame 4 paired as ever.
    a, tight, region, mask = cuts
    read = recorded(cut_of(tmp_path/'read', 4, tight), CR, 'cells', 2, 'as_filmed', to=False)
    loop_dir = cut_of(tmp_path/'b', 1, wider(tight))
    recorded(loop_dir, CR, 'cells', 5, 'scale', 'size_hold', to=0.9)
    recorded(loop_dir, CR, 'cells', 5, 'as_filmed', to=False)
    _, _, answer = read_on_cut(read, loop_dir)
    assert (answer.read_on['relation'], answer.read_on['cut_cell'], answer.read_on['shift_px']) == ('recut', 3, [3, 2])
    assert np.array_equal(answer.mask.worn[0], patch_in(loop_dir, 3))


def aligned_cut(tmp_path, made=(), read_made=()):
    """The loop's cut as video-loop cuts one (its own crop), from frame 1, with its record of its cells (a cell in `made`
    drawn between the frames either side, recorded so), aligned on its own; and the regions read on a cut from frame 4
    cropped 3 px in from its left and 2 px from its top (its cells in `read_made` drawn so): the cut, the cut the regions
    were read on, the aligned loop, the region and its mask."""
    frames = clip()[1:1+N]
    for k in made:
        frames[k] = Image.blend(clip()[k], clip()[k+2], 0.5)
    b = as_cut(cut(tmp_path/'b', frames), 1, made=made)
    meta = meta_of(b)
    assert meta['scale'] == 1.0 and meta['sample_indices'] == list(range(N))
    left, top, right, bottom = meta['source_rect']
    inner = (left+3, top+2, right, bottom)
    a = cut_of(tmp_path/'a', 4, inner, made=read_made)
    (cx, cy), patch = patch_at(4, inner)
    return b, a, aligned(b, tmp_path/'turned'), (cx, cy, 17.0, 17.0), mask_png(tmp_path/'mask.png', patch)


def test_on_a_loop_aligned_from_the_cut_again_the_pair_is_carried_through_the_alignment(tmp_path):
    b, a, loop_dir, region, mask = aligned_cut(tmp_path)
    record = meta_of(loop_dir)['cycle_align']
    assert record['origin']['strip_sha256'] == sha(b/'run.strip.png') and record['turned_by'] != 0
    # The cut's own record, kept beside its frames as filmed as it was, byte for byte, bound to the strip the origin names;
    # the aligned strip is another, and its record does not carry the cut's record of its cells. Aligned again, the cut's
    # is kept as the first alignment kept it.
    kept = loop_dir/loop.CYCLE_SOURCE_DIR/'run.strip.json'
    assert kept.read_bytes() == (b/'run.strip.json').read_bytes() and CR not in meta_of(loop_dir)
    assert json.loads(kept.read_text())[CR]['strip_sha256'] == record['origin']['strip_sha256']
    again = copy(loop_dir, tmp_path/'again')
    align.align_set([again], length=17, between='nearest', multi_cycle='warn')
    assert (again/loop.CYCLE_SOURCE_DIR/'run.strip.json').read_bytes() == kept.read_bytes()
    assert CR not in meta_of(again)
    result = sway.sway_loop(loop_dir, [region], region_mask=mask, read_on=sha(a/'run.strip.png'),
                            read_on_cut=a/'run.strip.png', **KW)
    read_on = result['read_on']
    assert read_on['shift_px'] == [3, 2]
    cut_cells = [entry['source'] for entry in record['cells_from']]
    assert read_on['relation'] == 'recut' and read_on['cut_cells'] == cut_cells and read_on['policy'] == sway.TRANSPORT_POLICY
    assert read_on['pair_policy'] == sway.RECUT_POLICY
    assert read_on['cut_cell'] == 3 and read_on['base_cell'] == cut_cells.index(3) and read_on['source_frame'] == 4


ALIGNED = {  # what the alignment kept of the cut's record, or what its record says of the cell paired, and the refusal
    'kept-gone': (lambda kept, a: kept.unlink(),
                  r"and this loop is aligned from its cut \(cycle_align\), whose strip record is not kept beside its frames "
                  r"as filmed \(cycle\.source/<name>\.strip\.json\)"),
    'kept-no-cells': (lambda kept, a: recorded(kept.parent, CR, to=GONE),
                      r"but this loop's strip record does not give its cells: it holds no record of them \(cell_record\), "
                      r"as a cut video-loop made before it wrote one does"),
    # bound to another strip than the one the alignment's origin names: here the aligned strip's
    'kept-another-strip': (lambda kept, a: recorded(kept.parent, CR, 'strip_sha256',
                                                    to=sha(kept.parent.parent/'run.strip.png')),
                           r"but this loop's strip record does not give its cells: its record of them is another strip's "
                           r"\(cell_record\.strip_sha256 [0-9a-f]{64}; the strip is <origin>\)"),
    'made': (None, PAIRED + r"but this loop's cell 3 is not that frame as filmed: video-loop did not cut it as filmed "
                            r"\(cell_record\.cells\[3\]\.as_filmed\)" + NOT_ANCHORED),
    # the cut the regions were read on drew its cell 0 between the frames either side, its record changed to say not
    'read-made-said-as-filmed': (lambda kept, a: recorded(a, CR, 'cells', 0, 'as_filmed', to=True),
                            PAIRED + r"but the two cells are not the same pixels where they overlap at the shift \(3, 2\): "
                                     r"\d+ of \d+ px differ")}


@pytest.mark.parametrize('case', list(ALIGNED))
def test_a_loop_aligned_from_a_cut_whose_kept_record_does_not_give_the_frame_as_filmed_is_refused_no_match(tmp_path, case):
    change, said = ALIGNED[case]
    b, a, loop_dir, region, mask = aligned_cut(tmp_path, made=(3,) if case == 'made' else (),
                                               read_made=(0,) if case.startswith('read-made') else ())
    said = said.replace('<origin>', meta_of(loop_dir)['cycle_align']['origin']['strip_sha256'])
    if change is not None:
        change(loop_dir/loop.CYCLE_SOURCE_DIR/'run.strip.json', a)
    refused(loop_dir, dict(region_mask=mask, read_on=sha(a/'run.strip.png'), read_on_cut=a/'run.strip.png', **KW), said,
            region)


def test_a_loop_aligned_to_more_cells_says_where_each_is_from_and_read_on_cut_reads_the_cut_s_own_record(tmp_path):
    # video-cycle-align stretching the cut (13 cells to 17, as a set stretches a loop of 18 to its 24). The aligned strip is
    # another strip of another length: its record holds no record of the cut's cells to go stale; the alignment's record
    # gives each of its cells, the frame of the cut it is or the two it is made between, and the cut's own record is kept
    # as it was beside the frames as filmed, 13 cells, for --read-on-cut to read.
    b, a, _, region, mask = aligned_cut(tmp_path)
    kw = dict(region_mask=mask, read_on=sha(a/'run.strip.png'), read_on_cut=a/'run.strip.png', **KW)
    made = aligned(b, tmp_path/'made', length=17, between='rife', interpolate=Blend())
    meta, cells = meta_of(made), cells_of(made)
    cells_from, cut_cells = meta['cycle_align']['cells_from'], cells_of(b)
    kept = json.loads((made/loop.CYCLE_SOURCE_DIR/'run.strip.json').read_text())
    assert len(cells) == len(cells_from) == 17 and CR not in meta
    assert kept == meta_of(b) and [c['source_frame'] for c in kept[CR]['cells']] == list(range(1, 1+N))
    between = [k for k, entry in enumerate(cells_from) if 'between' in entry]
    assert 0 < len(between) < 17
    for k, entry in enumerate(cells_from):
        if k in between:  # a frame of its own, a copy of no cell of the cut
            assert not any(np.array_equal(cells[k], cell) for cell in cut_cells)
        else:
            assert np.array_equal(cells[k], cut_cells[entry['source']])
    # A cell made between has no cell of the cut, and the reading is not carried: refused on the pair the kept record gives.
    before = files(made)
    with pytest.raises(SystemExit, match=rf"paired at source frame 4 with the cut this loop was aligned from \(.*\), but "
                                         rf"uncertain: cell {between[0]} is made between the cut's frames"):
        sway.sway_loop(made, [region], **kw)
    assert files(made) == before
    # Stretched by the cut's own cells, shown again, every cell is one of the cut's, and the pair is carried through.
    shown = aligned(b, tmp_path/'shown', length=17, fill_held='off')  # the cells shown again stay so (--fill-held off)
    entries = meta_of(shown)['cycle_align']['cells_from']
    assert len(entries) == 17 and all('source' in entry for entry in entries) and CR not in meta_of(shown)
    read_on = sway.sway_loop(shown, [region], **kw)['read_on']
    assert read_on['relation'] == 'recut' and read_on['shift_px'] == [3, 2]
    assert read_on['source_frame'] == 4 and read_on['cut_cell'] == 3
    assert read_on['cut_cells'] == [entry['source'] for entry in entries]
    assert read_on['base_cell'] == read_on['cut_cells'].index(3)


# ---------------------------------------------------------------- the frame where each cut's anchor moved it

def test_two_cuts_at_anchor_body_pair_the_frame_where_each_ramp_moved_it(tmp_path):
    # The runner walking 2 px a frame, cut by video-loop at --anchor body from frame 4 and from frame 1: each cut's ramp
    # moves its frame k by round(wrap_dx·k/N), so frame 4 — the first cut's cell 0 — is moved by nothing there, and by
    # round(-24·3/13) = -6 px as the second cut's cell 3. The crops alone set it 6 px apart; on the frames as filmed, none.
    clip_dir = walked(tmp_path/'clip', (2, 0), (220, 220))
    a, b = loop_cut(clip_dir, tmp_path/'a', 4, 'body'), loop_cut(clip_dir, tmp_path/'b', 1, 'body')
    ma, mb = meta_of(a), meta_of(b)
    assert ma['wrap_dx_px'] == mb['wrap_dx_px'] == -24 and ma['source_cut']['samples'][0] == mb['source_cut']['samples'][3] == 4
    assert ma['source_rect'][0] - mb['source_rect'][0] == 6 and ma['source_rect'][1] == mb['source_rect'][1]
    assert mb[CR]['cells'][3]['anchor_move_px'] == [-6, 0] and ma[CR]['cells'][0]['anchor_move_px'] == [0, 0]
    there = patch_in(b, 3)  # frame 4's patch where the second cut drew it
    region, kw, answer = read_on_cut(a, b)
    pair = answer.read_on
    assert (pair['relation'], pair['source_frame'], pair['cut_cell'], pair['base_cell']) == ('recut', 4, 3, 3)
    assert pair['shift_px'] == [0, 0] and pair['drift_px'] == [0.0, 0.0]
    assert there.sum() == patch_in(a, 0).sum() > 100 and np.array_equal(answer.mask.worn[0], there)
    record = sway.sway_loop(b, [region], **kw)
    assert (record['read_on']['relation'], record['read_on']['shift_px']) == ('recut', [0, 0])


def test_two_cuts_at_anchor_body_whose_ramp_moves_nothing_pair_by_their_crops_alone(tmp_path):
    # The runner laid 1 px lower each frame, cut at --anchor body from frame 12 and from frame 5, where the last frame's
    # head and torso lie over the first's (the across of their sway is the same): both wrap offsets are nothing, the ramp
    # moves no frame, and frame 12 — the first cut's cell 0, the second's cell 7 — is moved by the crops alone, as ever.
    clip_dir = walked(tmp_path/'clip', (0, 1), (W, H+CLIP))
    a, b = loop_cut(clip_dir, tmp_path/'a', 12, 'body'), loop_cut(clip_dir, tmp_path/'b', 5, 'body')
    ma, mb = meta_of(a), meta_of(b)
    assert ma['wrap_dx_px'] == mb['wrap_dx_px'] == 0 and ma['source_cut']['samples'][0] == mb['source_cut']['samples'][7] == 12
    crops = [ma['source_rect'][axis] - mb['source_rect'][axis] for axis in (0, 1)]
    assert crops[0] == 0 and crops[1] > 0
    there = patch_in(b, 7)
    region, kw, answer = read_on_cut(a, b)
    assert (answer.read_on['cut_cell'], answer.read_on['base_cell'], answer.read_on['shift_px']) == (7, 7, crops)
    assert there.sum() > 100 and np.array_equal(answer.mask.worn[0], there)


def test_two_cuts_at_anchor_motion_pair_the_frame_where_each_correction_laid_it(tmp_path):
    # The runner walking 2 px a frame, cut by video-loop at --anchor motion from frame 4 and from frame 1 on its head and
    # torso in each cut's first frame: each correction lays its frame k on a canvas padded for every frame, at the padding
    # plus that frame's shift, so the crops alone do not say where frame 4 lies in the second cut; the corrections do, and
    # each cell's record gives it.
    clip_dir = walked(tmp_path/'clip', (2, 0), (220, 220))

    def regions(start):
        phase = 2*math.pi*(start+SHIFT)/N
        x, y = round(3*math.sin(phase)) + 2*start, round(5*math.cos(2*phase))
        return [(60+x, 12+y, 101+x, 52+y), (46+x, 52+y, 115+x, 136+y)]  # head, torso

    a = loop_cut(clip_dir, tmp_path/'a', 4, 'motion', anchor_regions=regions(4))
    b = loop_cut(clip_dir, tmp_path/'b', 1, 'motion', anchor_regions=regions(1))
    ma, mb = meta_of(a), meta_of(b)
    assert ma['source_cut']['samples'][0] == mb['source_cut']['samples'][3] == 4
    for m in (ma, mb):
        assert [c['anchor_move_px'] for c in m[CR]['cells']] == [list(motion_anchor.laid_at(m['motion_anchor'], k))
                                                                  for k in range(N)]
        assert all(c['as_filmed'] for c in m[CR]['cells'])
    report = json.loads((b/'run.loop.report.json').read_text())
    assert report['source_projection']['recipe'] == loop.MOTION_RECIPE
    laid = [*ma[CR]['cells'][0]['anchor_move_px'], *mb[CR]['cells'][3]['anchor_move_px']]
    crops = [ma['source_rect'][axis] - mb['source_rect'][axis] for axis in (0, 1)]
    assert laid[:2] != laid[2:]  # the two corrections lay frame 4 apart
    there = patch_in(b, 3)
    region, kw, answer = read_on_cut(a, b)
    pair = answer.read_on
    assert (pair['relation'], pair['cut_cell'], pair['base_cell']) == ('recut', 3, 3)
    assert pair['shift_px'] == [crops[axis] - laid[axis] + laid[2+axis] for axis in (0, 1)] != crops
    assert there.sum() == patch_in(a, 0).sum() > 100 and np.array_equal(answer.mask.worn[0], there)


# ---------------------------------------------------------------- what video-loop records of each cell

def test_video_loop_records_each_cell_of_its_strip_in_the_strips_record_bound_to_its_png(tmp_path):
    # --anchor body: each cell the source frame it is, moved across by its ramp, scaled by no hold, as filmed.
    clip_dir = walked(tmp_path/'clip', (2, 0), (220, 220))
    b = loop_cut(clip_dir, tmp_path/'b', 1, 'body')
    meta = meta_of(b)
    record = meta[CR]
    assert loop.CELL_RECORD == CR and set(record) == {'strip_sha256', 'cells'}
    assert record['strip_sha256'] == sha(b/'run.strip.png')
    assert [set(c) for c in record['cells']] == [{'source_frame', 'anchor_move_px', 'scale', 'as_filmed'}]*N
    assert [c['source_frame'] for c in record['cells']] == meta['source_cut']['samples'] == [1+k for k in range(N)]
    assert [c['anchor_move_px'] for c in record['cells']] == [[loop.ramp_offset(-24, k, N), 0] for k in range(N)]
    assert all(c['scale'] == {'size_hold': 1, 'gait_fallback': 1} and c['as_filmed'] is True for c in record['cells'])
    # Written beside the strip and not into the report, which keeps each frame's receipt the record is read against.
    report = json.loads((b/'run.loop.report.json').read_text())
    assert CR not in report and CR not in report['strip']
    assert len(report['source_projection']['before_repair_pixels_sha256']) == N


def test_a_cell_the_jump_repair_gave_way_is_recorded_not_as_filmed(tmp_path, monkeypatch):
    # The ghost walk with a filmed ghost at frame 10, no RIFE: frame 10 gives way to frame 11, shown twice in a row. Its
    # cell's pixels are not its pixels before the repair; every other cell's are.
    _no_rife(monkeypatch, tmp_path)
    report = ghost_cut(ghost_keyed(tmp_path, 'E', (10,)), tmp_path/'E')
    assert report['jump_repair']['replaced'] == [10]
    meta = json.loads((tmp_path/'E'/'walk.strip.json').read_text())
    assert 10 in meta['sample_indices']
    assert [c['as_filmed'] for c in meta[CR]['cells']] == [k != 10 for k in meta['sample_indices']]
    assert meta[CR]['strip_sha256'] == sha(tmp_path/'E'/'walk.strip.png')


# ---------------------------------------------------------------- refused, saying why

def test_a_cut_whose_frame_the_loop_does_not_hold_or_that_names_no_clip_is_refused_no_match(cuts, tmp_path):
    a, tight, region, mask = cuts
    kw = dict(region_mask=mask, read_on=sha(a/'run.strip.png'), read_on_cut=a/'run.strip.png', **KW)
    later = cut_of(tmp_path/'later', 7, tight)  # source frames 7 to N+6: frame 4 is not among them
    refused(later, kw, r"whose cell 0 is source frame 4, which this loop's cut does not hold \(it holds source frames 7 to "
                       r"19\)", region)
    nameless = recorded(cut_of(tmp_path/'nameless', 5, tight, name='walk'), 'source_cut', to=GONE, name='walk')
    refused(cut_of(tmp_path/'b', 4, tight), dict(kw, read_on=sha(nameless/'walk.strip.png'),
                                                 read_on_cut=nameless/'walk.strip.png'),
            r"and the cut's \(--read-on-cut\) record names no clip it is cut from \(source_cut\)", region)
    # Without the cut's record nothing says which frame its cell 0 is: refused as ever, saying so.
    before = files(later)
    with pytest.raises(SystemExit, match=r'no-match — .*no record of it says which source frame its cell 0 is \(--read-on-cut'):
        sway.sway_loop(later, [region], region_mask=mask, read_on=sha(a/'run.strip.png'), **KW)
    assert files(later) == before


WHOSE = {'read-on-cut': r"the cut's \(--read-on-cut\)", 'this-loop': "this loop's"}
CELLS = r"its record of cell 2 "
UNGIVEN = {  # a cut's record of its cells changed (`recorded`: where, to what), on the cut the regions were read on or on
    # this loop's, so that it does not give them as video-loop writes it; the refusal's words
    'none': ('read-on-cut', (CR,), GONE,
             r"it holds no record of them \(cell_record\), as a cut video-loop made before it wrote one does"),
    'none-here': ('this-loop', (CR,), GONE,
                  r"it holds no record of them \(cell_record\), as a cut video-loop made before it wrote one does"),
    'null': ('this-loop', (CR,), None, r"its record of them is not one \(cell_record\)"),
    'list': ('read-on-cut', (CR,), [], r"its record of them is not one \(cell_record\)"),
    'another-strip': ('read-on-cut', (CR, 'strip_sha256'), 'ab'*32,
                      r"its record of them is another strip's \(cell_record\.strip_sha256 (ab){32}; the strip is "
                      r"[0-9a-f]{64}\)"),
    'another-strip-here': ('this-loop', (CR, 'strip_sha256'), 'cd'*32,
                           r"its record of them is another strip's \(cell_record\.strip_sha256 (cd){32}; the strip is "
                           r"[0-9a-f]{64}\)"),
    'unbound': ('this-loop', (CR, 'strip_sha256'), GONE,
                r"its record of them is another strip's \(cell_record\.strip_sha256 None; the strip is [0-9a-f]{64}\)"),
    'cells-gone': ('read-on-cut', (CR, 'cells'), GONE,
                   r"its record of them names no cells, and the strip has 13 \(cell_record\.cells\)"),
    'cells-short': ('this-loop', (CR, 'cells'), lambda cells: cells[:-1],
                    r"its record of them names 12 cells, and the strip has 13 \(cell_record\.cells\)"),
    'cell-null': ('read-on-cut', (CR, 'cells', 2), None, CELLS + r"is not one \(cell_record\.cells\[2\]\)"),
    'frame-gone': ('this-loop', (CR, 'cells', 2, 'source_frame'), GONE,
                   CELLS + r"names no whole source frame \(cell_record\.cells\[2\]\.source_frame\)"),
    'frame-float': ('read-on-cut', (CR, 'cells', 2, 'source_frame'), 6.0,
                    CELLS + r"names no whole source frame \(cell_record\.cells\[2\]\.source_frame\)"),
    'frame-text': ('this-loop', (CR, 'cells', 2, 'source_frame'), '3',
                   CELLS + r"names no whole source frame \(cell_record\.cells\[2\]\.source_frame\)"),
    'frame-bool': ('read-on-cut', (CR, 'cells', 2, 'source_frame'), True,
                   CELLS + r"names no whole source frame \(cell_record\.cells\[2\]\.source_frame\)"),
    'move-gone': ('this-loop', (CR, 'cells', 2, 'anchor_move_px'), GONE,
                  CELLS + r"gives no whole move of its anchor \(cell_record\.cells\[2\]\.anchor_move_px\)"),
    'move-short': ('read-on-cut', (CR, 'cells', 2, 'anchor_move_px'), [0],
                   CELLS + r"gives no whole move of its anchor \(cell_record\.cells\[2\]\.anchor_move_px\)"),
    'move-float': ('this-loop', (CR, 'cells', 2, 'anchor_move_px'), [0.5, 0],
                   CELLS + r"gives no whole move of its anchor \(cell_record\.cells\[2\]\.anchor_move_px\)"),
    'move-text': ('read-on-cut', (CR, 'cells', 2, 'anchor_move_px'), '0,0',
                  CELLS + r"gives no whole move of its anchor \(cell_record\.cells\[2\]\.anchor_move_px\)"),
    'scale-gone': ('this-loop', (CR, 'cells', 2, 'scale'), GONE,
                   CELLS + r"does not give the factor each hold scaled it by \(cell_record\.cells\[2\]\.scale: size_hold, "
                           r"gait_fallback\)"),
    'scale-step-gone': ('read-on-cut', (CR, 'cells', 2, 'scale', 'gait_fallback'), GONE,
                        CELLS + r"does not give the factor each hold scaled it by \(cell_record\.cells\[2\]\.scale: "
                                r"size_hold, gait_fallback\)"),
    'scale-zero': ('this-loop', (CR, 'cells', 2, 'scale', 'size_hold'), 0,
                   CELLS + r"does not give the factor each hold scaled it by \(cell_record\.cells\[2\]\.scale: size_hold, "
                           r"gait_fallback\)"),
    'scale-text': ('read-on-cut', (CR, 'cells', 2, 'scale', 'size_hold'), '1',
                   CELLS + r"does not give the factor each hold scaled it by \(cell_record\.cells\[2\]\.scale: size_hold, "
                           r"gait_fallback\)"),
    'scale-bool': ('this-loop', (CR, 'cells', 2, 'scale', 'gait_fallback'), True,
                   CELLS + r"does not give the factor each hold scaled it by \(cell_record\.cells\[2\]\.scale: size_hold, "
                           r"gait_fallback\)"),
    'scale-nan': ('read-on-cut', (CR, 'cells', 2, 'scale', 'size_hold'), float('nan'),
                  CELLS + r"does not give the factor each hold scaled it by \(cell_record\.cells\[2\]\.scale: size_hold, "
                          r"gait_fallback\)"),
    'filmed-gone': ('this-loop', (CR, 'cells', 2, 'as_filmed'), GONE,
                    CELLS + r"does not say whether it is that frame as filmed \(cell_record\.cells\[2\]\.as_filmed\)"),
    'filmed-null': ('read-on-cut', (CR, 'cells', 2, 'as_filmed'), None,
                    CELLS + r"does not say whether it is that frame as filmed \(cell_record\.cells\[2\]\.as_filmed\)"),
    'filmed-int': ('this-loop', (CR, 'cells', 2, 'as_filmed'), 1,
                   CELLS + r"does not say whether it is that frame as filmed \(cell_record\.cells\[2\]\.as_filmed\)"),
    'filmed-text': ('read-on-cut', (CR, 'cells', 2, 'as_filmed'), 'true',
                    CELLS + r"does not say whether it is that frame as filmed \(cell_record\.cells\[2\]\.as_filmed\)")}


@pytest.mark.parametrize('case', list(UNGIVEN))
def test_a_record_of_its_cells_not_there_or_not_as_video_loop_writes_it_is_refused_no_match_naming_it(cuts, tmp_path, case):
    # The frames are as filmed, and the record does not say so as video-loop writes it: none (as a cut made before
    # video-loop wrote one), another strip's, or a field of it, of any cell, missing or not of its kind. Not read as off.
    a, tight, region, mask = cuts
    side, at, to, said = UNGIVEN[case]
    read = cut_of(tmp_path/'read', 4, tight)
    loop_dir = cut_of(tmp_path/'b', 1, wider(tight))
    recorded(read if side == 'read-on-cut' else loop_dir, *at, to=to)
    region, kw = read_on(read)
    refused(loop_dir, kw, rf"but {WHOSE[side]} strip record does not give its cells: {said}", region)


NOT_FILMED = {  # a cut's record of the cell paired, on the cut the regions were read on (cell 0) or on this loop's
    # (cell 3): the field changed and to what, and the refusal's words
    'held': ('read-on-cut', ('scale', 'size_hold'), 0.85,
             r"the size hold scaled it by 0\.85 about its feet before the cut \(cell_record\.cells\[0\]\.scale\.size_hold\), "
             r"and no whole px shift undoes a scale"),
    'held-here': ('this-loop', ('scale', 'size_hold'), 1.25,
                  r"the size hold scaled it by 1\.25 about its feet before the cut \(cell_record\.cells\[3\]\.scale\."
                  r"size_hold\), and no whole px shift undoes a scale"),
    'fallback': ('read-on-cut', ('scale', 'gait_fallback'), 1.1,
                 r"the gait fallback scaled it by 1\.1 about its feet before the cut \(cell_record\.cells\[0\]\.scale\."
                 r"gait_fallback\), and no whole px shift undoes a scale"),
    'fallback-here': ('this-loop', ('scale', 'gait_fallback'), 0.95,
                      r"the gait fallback scaled it by 0\.95 about its feet before the cut \(cell_record\.cells\[3\]\.scale\."
                      r"gait_fallback\), and no whole px shift undoes a scale"),
    'not-filmed': ('read-on-cut', ('as_filmed',), False,
                   r"video-loop did not cut it as filmed \(cell_record\.cells\[0\]\.as_filmed\)"),
    'not-filmed-here': ('this-loop', ('as_filmed',), False,
                        r"video-loop did not cut it as filmed \(cell_record\.cells\[3\]\.as_filmed\)")}


@pytest.mark.parametrize('case', list(NOT_FILMED))
def test_a_cell_paired_its_record_calls_scaled_or_not_as_filmed_is_refused_no_match_saying_which(cuts, tmp_path, case):
    a, tight, region, mask = cuts
    side, field, to, said = NOT_FILMED[case]
    read = cut_of(tmp_path/'read', 4, tight)
    loop_dir = cut_of(tmp_path/'b', 1, wider(tight))
    cell = 0 if side == 'read-on-cut' else 3
    recorded(read if side == 'read-on-cut' else loop_dir, CR, 'cells', cell, *field, to=to)
    region, kw = read_on(read)
    refused(loop_dir, kw, PAIRED + rf"but {WHOSE[side]} cell {cell} is not that frame as filmed: {said}"
            + (NOT_ANCHORED if field == ('as_filmed',) else ''), region)


MADE = {  # a cell an interpolator made, frame 4 as the cut the regions were read on (cell 0) or this loop's cut (cell 3)
    # draws it: its record as video-loop writes it, or changed to call it the frame as filmed; the refusal's words
    'read-on-cut': ('read-on-cut', False, r"but the cut's \(--read-on-cut\) cell 0 is not that frame as filmed: video-loop "
                                          r"did not cut it as filmed \(cell_record\.cells\[0\]\.as_filmed\)" + NOT_ANCHORED),
    'this-loop': ('this-loop', False, r"but this loop's cell 3 is not that frame as filmed: video-loop did not cut it as "
                                      r"filmed \(cell_record\.cells\[3\]\.as_filmed\)" + NOT_ANCHORED),
    'read-on-cut-said-as-filmed': ('read-on-cut', True, r"but the two cells are not the same pixels where they overlap at "
                                                        r"the shift \(3, 2\): \d+ of \d+ px differ"),
    'this-loop-said-as-filmed': ('this-loop', True, r"but the two cells are not the same pixels where they overlap at the "
                                                    r"shift \(3, 2\): \d+ of \d+ px differ")}


@pytest.mark.parametrize('case', list(MADE))
def test_a_cell_an_interpolator_made_is_refused_no_match_whatever_its_record_says(cuts, tmp_path, case):
    # Frame 4, drawn between frames 3 and 5 in one cut: not the patch drawn on the other cut's cell 0 moved by the crops.
    # Its record says so as video-loop writes it; changed to say it is the frame as filmed, its pixels say it is not.
    a, tight, region, mask = cuts
    side, said_as_filmed, said = MADE[case]
    wide = wider(tight)
    read = cut_of(tmp_path/'read', 4, tight, made=(0,) if side == 'read-on-cut' else ())
    loop_dir = cut_of(tmp_path/'b', 1, wide, made=(3,) if side == 'this-loop' else ())
    assert not np.array_equal(patch_in(loop_dir, 3), moved(patch_in(read, 0), [3, 2], wide))
    if said_as_filmed:
        recorded(read if side == 'read-on-cut' else loop_dir, CR, 'cells', 0 if side == 'read-on-cut' else 3, 'as_filmed',
                 to=True)
    region, kw = read_on(read, patch_at(4, tight)[1])  # the patch where frame 4 as filmed draws it
    refused(loop_dir, kw, PAIRED + said, region)


def test_a_loop_aligned_from_its_cut_named_by_read_on_cut_is_refused_no_match(tmp_path):
    # The cut from frame 1 aligned on its own (video-cycle-align): its record keeps the cut's, source frames 1 to N in
    # order, but its cell 0 is another of them, so the frame the record's cut starts on is not where the regions were read.
    o = as_cut(cut(tmp_path/'o', clip()[1:1+N]), 1)
    turned = aligned(o, tmp_path/'turned')
    assert meta_of(turned)['source_cut']['samples'][0] == 1 and meta_of(turned)['cycle_align']['cells_from'][0]['source'] != 0
    drawn = patch_in(turned, 0)
    ys, xs = np.nonzero(drawn)
    loop_dir = cut_of(tmp_path/'b', 1, bbox(clip()))
    refused(loop_dir, dict(region_mask=mask_png(tmp_path/'mask.png', drawn), read_on=sha(turned/'run.strip.png'),
                           read_on_cut=turned/'run.strip.png', **KW),
            r"but the strip --read-on-cut names is a loop aligned from its cut \(cycle_align\), whose cells are not the "
            r"cut.s frames in order", (float(xs.mean()), float(ys.mean()), 18.0, 18.0))


def test_a_cut_of_another_clip_is_refused_no_match(cuts, tmp_path):
    a, tight, region, mask = cuts
    other = cut_of(tmp_path/'other', 4, wider(tight), source='clip-b')
    refused(other, dict(region_mask=mask, read_on=sha(a/'run.strip.png'), read_on_cut=a/'run.strip.png', **KW),
            r"of another clip \(clip-a; this loop's is clip-b\)", region)


def test_what_the_key_is_refused_for_leaves_the_loop_as_it_was(cuts, tmp_path):
    a, tight, region, mask = cuts
    loop_dir = cut_of(tmp_path/'b', 1, wider(tight))
    before = files(loop_dir)
    text = ','.join(f'{v:g}' for v in region)
    for args, said in (
            (['--read-on-cut', str(a/'run.strip.png')], r'--read-on-cut names the strip --read-on names by its sha256; it needs --read-on$'),
            (['--read-on', 'ab'*32, '--read-on-cut', str(a/'run.strip.png')], r'is [0-9a-f]{64}, not the strip --read-on names'),
            (['--read-on', sha(a/'run.strip.png'), '--read-on-cut', str(tmp_path/'none.png')], r'wants the strip.s PNG and its record')):
        with pytest.raises(SystemExit, match=said) as refusal:
            cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text, '--region-mask', str(mask), *args])
        assert refusal.value.code not in (0, None), args
        assert files(loop_dir) == before
    # And on the command line it is taken as the call takes it.
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text, '--region-mask', str(mask), '--region-shrink',
                     '0', '--gain', '1', '--on-fold', 'lower', '--read-on', sha(a/'run.strip.png'), '--read-on-cut',
                     str(a/'run.strip.png')]) == 0
    assert meta_of(loop_dir)['follow']['read_on']['relation'] == 'recut'


# ---------------------------------------------------------------- a walk the size hold held to one size

WALK_TORSO = (20, 90, 180)  # the walker's torso (tests/video/test_gait_fallback.py)


def torso_in(loop_dir, cell):
    """The walker's torso, its own colour's opaque pixels (within 12 of it: a frame scaled is resampled), in a cell of a
    loop's strip as drawn."""
    rgba = cells_of(loop_dir)[cell].astype(int)
    return (np.abs(rgba[..., :3] - WALK_TORSO).max(axis=-1) <= 12) & (rgba[..., 3] == 255)


@pytest.fixture(scope='module')
def walk_cuts(tmp_path_factory):
    """The walk away from the camera of tests/video/test_gait_fallback.py (15 % smaller by its last frame, its feet 20 px
    higher), keyed as video-frames leaves it and cut by video-loop as a walk at --anchor body, a stride from frame 24 and
    from frame 20: each held to one size (--size-hold auto) and as filmed (off)."""
    root = tmp_path_factory.mktemp('walk')
    keyed = root/'keyed'
    keyed.mkdir()
    for k in range(73):
        walking_away(k).save(keyed/f'frame-{k:03d}.png')
    return {(start, hold): loop_cut(keyed, root/f'{start}-{hold}', start, 'body', length=24, state='walk', size_hold=hold)
            for start in (24, 20) for hold in ('auto', 'off')}


@pytest.mark.slow
@pytest.mark.parametrize('start', [24, 20])
def test_video_loop_records_the_factor_the_size_hold_scaled_each_cell_by(walk_cuts, start):
    held, filmed = meta_of(walk_cuts[start, 'auto'])[CR]['cells'], meta_of(walk_cuts[start, 'off'])[CR]['cells']
    report = json.loads((walk_cuts[start, 'auto']/'run.loop.report.json').read_text())
    assert report['size_hold']['applied'] is True
    assert [c['scale']['size_hold'] for c in held] == [f['scale'] for f in report['size_hold']['cut_frames']]
    assert all(c['scale']['size_hold'] != 1 and c['as_filmed'] is False for c in held)
    assert all(c['scale'] == {'size_hold': 1, 'gait_fallback': 1} and c['as_filmed'] is True for c in filmed)
    assert [c['source_frame'] for c in held] == [c['source_frame'] for c in filmed] == list(range(start, start+24))


HELD = r"the size hold scaled it by [0-9.]+ about its feet before the cut \(cell_record\.cells\[{}\]\.scale\.size_hold\), " \
       r"and no whole px shift undoes a scale"
HOLDS = {  # how each cut was held (--size-hold): the cut the regions were read on, then this loop's; whose record says it
    'read-on-cut-held': ('auto', 'off', 'read-on-cut'),
    'this-loop-held': ('off', 'auto', 'this-loop'),
    'both-held': ('auto', 'auto', 'read-on-cut')}
WALK_PAIRED = r"whose cell 0 is source frame 24 \(this loop's cut's cell 4\), "


@pytest.mark.slow
@pytest.mark.parametrize('case', list(HOLDS))
def test_a_walk_cut_the_size_hold_held_to_one_size_is_refused_no_match(walk_cuts, tmp_path, case):
    # Frame 24 is the first cut's cell 0 and the second's cell 4. A cut held to one size was cut from frames scaled about
    # their feet, and its record of its cells says by how much. Held in one cut and not in the other, frame 24's torso is
    # not the same size in the two, and no whole px shift carries the one onto the other.
    read_hold, loop_hold, side = HOLDS[case]
    a, b = copy(walk_cuts[24, read_hold], tmp_path/'a'), copy(walk_cuts[20, loop_hold], tmp_path/'b')
    assert meta_of(a)['source_cut']['samples'][0] == meta_of(b)['source_cut']['samples'][4] == 24
    drawn = torso_in(a, 0)
    assert drawn.sum() > 100
    if read_hold != loop_hold:
        assert drawn.sum() != torso_in(b, 4).sum()
    region, kw = read_on(a, drawn)
    cell = 0 if side == 'read-on-cut' else 4
    refused(b, kw, WALK_PAIRED + rf"but {WHOSE[side]} cell {cell} is not that frame as filmed: " + HELD.format(cell), region)


def erase_trace(loop_dir):
    """Every trace of the size hold and of the walk in a cut's report and its strip's record taken out: the report's
    size hold, the state its strip's record, the report and the report's strip say."""
    report_path = loop_dir/'run.loop.report.json'
    report = json.loads(report_path.read_text())
    for holder, key in ((report, 'size_hold'), (report, 'state'), (report['strip'], 'state')):
        holder.pop(key, None)
    report_path.write_text(json.dumps(report, indent=2)+'\n')
    return recorded(loop_dir, 'state', to=GONE)


def said_as_filmed(loop_dir):
    """A cut's record of its cells changed to call every cell the frame as filmed, scaled by no hold."""
    return recorded(loop_dir, CR, 'cells', to=lambda cells: [
        c | {'scale': {'size_hold': 1.0, 'gait_fallback': 1.0}, 'as_filmed': True} for c in cells])


def erase_scale(loop_dir):
    """A cut's record of its cells with neither the factor each hold scaled a cell by nor whether it is as filmed."""
    return recorded(loop_dir, CR, 'cells', to=lambda cells: [
        {k: v for k, v in c.items() if k not in ('scale', 'as_filmed')} for c in cells])


NOT_SAME = r"but the two cells are not the same pixels where they overlap at the shift \(-?\d+, -?\d+\): \d+ of \d+ px differ"
ERASED = {  # a walk cut the size hold held to one size, the other cut as filmed: whose cut it is, what is taken out of its
    # records or changed in them, and the refusal's words
    'trace': ('read-on-cut', (erase_trace,), WALK_PAIRED + r"but the cut's \(--read-on-cut\) cell 0 is not that frame as "
                                                           r"filmed: " + HELD.format(0)),
    'trace-here': ('this-loop', (erase_trace,), WALK_PAIRED + r"but this loop's cell 4 is not that frame as filmed: "
                                                             + HELD.format(4)),
    'trace-and-scale': ('read-on-cut', (erase_trace, erase_scale),
                        r"but the cut's \(--read-on-cut\) strip record does not give its cells: its record of cell 0 does "
                        r"not give the factor each hold scaled it by \(cell_record\.cells\[0\]\.scale: size_hold, "
                        r"gait_fallback\)"),
    'trace-and-record': ('read-on-cut', (erase_trace, lambda d: recorded(d, CR, to=GONE)),
                         r"but the cut's \(--read-on-cut\) strip record does not give its cells: it holds no record of "
                         r"them \(cell_record\), as a cut video-loop made before it wrote one does"),
    'said-as-filmed': ('read-on-cut', (erase_trace, said_as_filmed), WALK_PAIRED + NOT_SAME),
    'said-as-filmed-here': ('this-loop', (erase_trace, said_as_filmed), WALK_PAIRED + NOT_SAME)}


@pytest.mark.slow
@pytest.mark.parametrize('case', list(ERASED))
def test_a_held_walk_cut_whose_records_no_longer_say_it_was_held_is_refused_no_match(walk_cuts, tmp_path, case):
    # Frame 24 is the first cut's cell 0 and the second's cell 4, held to one size in one cut and as filmed in the other:
    # its torso is not the same size in the two. The report and the strip's record are no longer read for it: with every
    # trace of the hold and of the walk taken out of them, the record of each cell still says the hold scaled it; with
    # that taken out too, the record does not give the cells; changed to say each cell is as filmed, the pixels say not.
    side, changes, said = ERASED[case]
    read_hold, loop_hold = ('auto', 'off') if side == 'read-on-cut' else ('off', 'auto')
    a, b = copy(walk_cuts[24, read_hold], tmp_path/'a'), copy(walk_cuts[20, loop_hold], tmp_path/'b')
    held = a if side == 'read-on-cut' else b
    for change in changes:
        change(held)
    report = json.loads((held/'run.loop.report.json').read_text())
    assert 'size_hold' not in report and 'state' not in report and 'state' not in report['strip']
    assert 'state' not in meta_of(held) and 'size_hold' not in meta_of(held)
    drawn = torso_in(a, 0)
    assert drawn.sum() > 100 and drawn.sum() != torso_in(b, 4).sum()
    region, kw = read_on(a, drawn)
    refused(b, kw, said, region)
