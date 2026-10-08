"""`video-follow --read-on`: regions read on a cut are carried to the loop aligned from it, cell for cell, on a
synthetic runner. And what a reading of outlines cannot say about whose pixels a region holds."""
import hashlib
import json
import math
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen import cli
from sprite_gen.video import align, follow, follow_inspect
from sprite_gen.video import loop as loop_mod

N, W, H = 13, 160, 220
HEAD, TORSO, PATCH, LINE, NEAR, FAR, MARK = ((210, 150, 60, 255), (40, 70, 160, 255), (230, 90, 120, 255), (20, 10, 30, 255),
                                              (240, 200, 40, 255), (150, 110, 20, 255), (255, 255, 255, 255))
OTHER, OTHER_PATCH = (190, 90, 40, 255), (90, 200, 120, 255)
SHIFT = 5  # the cut starts this far into the stride, so the alignment turns it to start elsewhere


def runner(k, n=N, *, torso=TORSO, patch=PATCH, wide=0, arm_out=False):
    """A runner from the side: head and torso bob twice a cycle and sway once, as one; a striped soft part on the
    chest; two legs that swing across, the near one lighter, so a heel lands once a cycle on each. A white mark
    walks down the torso, so no two cells are alike. `arm_out`: an arm held out past every other cell's outline."""
    phase = 2*math.pi*(k+SHIFT)/n
    sx, sy = round(3*math.sin(phase)), round(5*math.cos(2*phase))
    im = Image.new('RGBA', (W, H))
    d = ImageDraw.Draw(im)
    d.ellipse((62+sx, 14+sy, 98+sx, 50+sy), fill=HEAD)
    d.rounded_rectangle((48+sx-wide, 52+sy, 112+sx+wide, 140+sy), radius=14, fill=torso)
    cx, cy = 80+sx, 92+sy
    d.ellipse((cx-18, cy-18, cx+18, cy+18), fill=patch)
    for y in range(cy-15, cy+16, 3):
        d.line((cx-14, y, cx+14, y), fill=LINE)
    for sign, colour in ((1, NEAR), (-1, FAR)):
        foot = (80+sign*round(22*math.cos(phase)), 206-round(6*max(0.0, sign*math.sin(phase))))
        d.line(((80+sx+sign*12, 138+sy), foot), fill=colour, width=8)
        d.rectangle((foot[0]-6, foot[1]-3, foot[0]+6, foot[1]+3), fill=colour)
    d.rectangle((52+sx, 60+sy+k, 53+sx, 61+sy+k), fill=MARK)
    if arm_out:
        d.rectangle((112+sx, 70+sy, 152, 76+sy), fill=torso)
    return im


def cut(path, frames, name='run'):
    """A loop directory as `video-loop` leaves one: the cycle's frames, and the strip built from them."""
    strip, meta = loop_mod.build_strip(frames, max_height=520, cycle_seconds=len(frames)/24)
    (path/'cycle').mkdir(parents=True)
    for k, frame in enumerate(frames):
        frame.save(path/'cycle'/f'frame-{k:03d}.png')
    strip.save(path/f'{name}.strip.png')
    (path/f'{name}.strip.json').write_text(json.dumps(meta, indent=2)+'\n')
    return path


def copy(src, dst):
    shutil.copytree(src, dst)
    return dst


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def meta_of(loop_dir):
    return json.loads(next(loop_dir.glob('*.strip.json')).read_text())


def cells_of(loop_dir):
    meta = meta_of(loop_dir)
    strip = np.asarray(Image.open(next(loop_dir.glob('*.strip.png'))).convert('RGBA'))
    return [np.ascontiguousarray(strip[:, k*meta['w']:(k+1)*meta['w']]) for k in range(meta['frames'])]


def files(loop_dir):
    """The loop's own files, by name and bytes; `follow.source.png`, which video-follow keeps on any refusal, apart."""
    return {p.name: p.read_bytes() for p in sorted(loop_dir.iterdir()) if p.is_file() and p.name != follow.SOURCE}


def chest(loop_dir):
    """The soft part's ellipse in the strip's first cell, from where the cut put the frame."""
    meta = meta_of(loop_dir)
    left, top = meta['source_rect'][:2]
    phase = 2*math.pi*SHIFT/N
    return (80+round(3*math.sin(phase))-left, 92+round(5*math.cos(2*phase))-top, 17.0, 17.0)


class Blend:
    """A made frame of its own: an even mix of its two neighbours, never a copy of either."""

    def __call__(self, a, b, t):
        return Image.blend(a, b, t)


@pytest.fixture
def origin(tmp_path):
    """The cut every case starts from, followed as cut (the reference), and its strip's sha256 — what an app keeps
    as `read_on` beside the regions it read on the first cell."""
    o = cut(tmp_path/'cut', [runner(k) for k in range(N)])
    read_on = sha(o/'run.strip.png')
    followed = copy(o, tmp_path/'followed')
    follow.follow_loop(followed, [chest(o)], on_fold='lower')
    return o, read_on, followed


def aligned(o, path, **kw):
    """A copy of the cut, aligned on its own (`video-cycle-align`) as asked."""
    loop_dir = copy(o, path)
    align.align_set([loop_dir], multi_cycle='warn', **({'between': 'nearest'} | kw))
    return loop_dir


# ---------------------------------------------------------------- C3: the alignment records where every cell is from

def test_the_first_alignment_records_the_cut_it_was_made_from_and_later_ones_keep_it(origin, tmp_path):
    o, read_on, _ = origin
    as_cut = meta_of(o)
    loop_dir = aligned(o, tmp_path/'turned')
    record = meta_of(loop_dir)['cycle_align']
    source = record['origin']
    assert source['strip_sha256'] == read_on
    assert (source['frames'], source['w'], source['h'], source['scale']) == (as_cut['frames'], as_cut['w'], as_cut['h'], as_cut['scale'])
    assert source['crop_origin'] == as_cut['source_rect'][:2] and source['sample_indices'] == list(range(N))
    rows, cols, height0, worn = follow.body_motion([Image.fromarray(c) for c in cells_of(o)])
    assert source['reading'] == {'policy': follow.POLICY, 'carry_px': [[c, r] for c, r in zip(cols.tolist(), rows.tolist())],
                                 'body_px': height0, 'body_worn_px': worn}
    assert source['cells_sha256'] == [hashlib.sha256(c.tobytes()).hexdigest() for c in cells_of(o)]
    turned = record['turned_by']
    assert turned != 0, 'the runner must be turned for this case to say anything'
    assert record['cells_from'] == [{'source': (k+turned) % N} for k in range(N)]
    # Every cell is the cut's cell it says it is, byte for byte: a turn moves no pixel.
    for k, cell in enumerate(cells_of(loop_dir)):
        assert np.array_equal(cell, cells_of(o)[record['cells_from'][k]['source']]), k
    # Aligned again (another length), the origin is the cut's still, not the first alignment's strip.
    align.align_set([loop_dir], length=17, between='nearest', multi_cycle='warn')
    again = meta_of(loop_dir)['cycle_align']
    assert again['origin'] == source and len(again['cells_from']) == 17


def test_a_follow_through_on_the_cut_is_not_the_origin_the_cut_as_cut_is(origin, tmp_path):
    o, read_on, followed = origin
    loop_dir = aligned(followed, tmp_path/'followed-then-aligned')
    assert meta_of(loop_dir)['cycle_align']['origin']['strip_sha256'] == read_on


# ---------------------------------------------------------------- T1: a turn is carried and the result is the cut's

def test_t1_a_turned_loop_followed_on_the_cuts_reading_is_the_cuts_follow_through_cell_for_cell(origin, tmp_path):
    o, read_on, followed = origin
    loop_dir = aligned(o, tmp_path/'turned')
    turned = meta_of(loop_dir)['cycle_align']['turned_by']
    result = follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)
    rec, ref = meta_of(loop_dir)['follow'], meta_of(followed)['follow']
    assert rec['read_on'] == {'sha256': read_on, 'relation': 'transported', 'policy': follow.TRANSPORT_POLICY,
                              'cut_cells': [(k+turned) % N for k in range(N)]}
    assert result['read_on']['relation'] == 'transported'
    assert rec['carry_basis'] == follow.CARRY_BASIS
    assert (rec['gain'], rec['gain_requested']) == (ref['gain'], ref['gain_requested'])
    assert [r.get('gain') for r in rec['fold']['regions']] == [r.get('gain') for r in ref['fold']['regions']]
    assert (rec['body_px'], rec['body_worn_px']) == (ref['body_px'], ref['body_worn_px'])
    for k in range(N):
        assert abs(rec['dx_px'][k]-ref['dx_px'][(k+turned) % N]) <= 0.01 and abs(rec['dy_px'][k]-ref['dy_px'][(k+turned) % N]) <= 0.01
    moved, reference = cells_of(loop_dir), cells_of(followed)
    assert all(np.array_equal(moved[k], reference[(k+turned) % N]) for k in range(N))


def test_t1_a_cut_that_did_not_record_its_crop_is_carried_by_its_cells_pixels(origin, tmp_path):
    # A cut made before the strip recorded its crop and frames (no `source_rect`, no `sample_indices`): its alignment
    # records both, and nothing says the crop changed — every cell is still the cut's, pixel for pixel.
    o, read_on, followed = origin
    legacy = copy(o, tmp_path/'legacy-cut')
    meta = meta_of(legacy)
    for key in ('source_rect', 'sample_indices', 'source_cut'):
        meta.pop(key, None)
    (legacy/'run.strip.json').write_text(json.dumps(meta, indent=2)+'\n')
    loop_dir = aligned(legacy, tmp_path/'legacy-turned')
    record = meta_of(loop_dir)['cycle_align']
    assert record['origin']['crop_origin'] is None and meta_of(loop_dir)['source_rect'] is not None
    follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)
    assert meta_of(loop_dir)['follow']['read_on']['relation'] == 'transported'
    turned, moved, reference = record['turned_by'], cells_of(loop_dir), cells_of(followed)
    assert all(np.array_equal(moved[k], reference[(k+turned) % N]) for k in range(N))
    # And a cell that is not the cut's is still caught by its pixels.
    touched = aligned(legacy, tmp_path/'legacy-touched')
    strip = Image.open(touched/'run.strip.png').convert('RGBA')
    ImageDraw.Draw(strip).point((meta_of(touched)['w']*2+3, 3), fill=MARK)
    strip.save(touched/'run.strip.png')
    with pytest.raises(SystemExit, match='uncertain.*cell 2'):
        follow.follow_loop(touched, [chest(o)], on_fold='lower', read_on=read_on)


def test_t1_the_same_turned_loop_followed_as_read_today_moves_another_place(origin, tmp_path):
    # The app gives the regions read on the cut's first cell to the turned loop, whose first cell is another.
    o, _, followed = origin
    loop_dir = aligned(o, tmp_path/'stale')
    turned = meta_of(loop_dir)['cycle_align']['turned_by']
    follow.follow_loop(loop_dir, [chest(o)], on_fold='lower')
    moved, reference = cells_of(loop_dir), cells_of(followed)
    assert 'read_on' not in meta_of(loop_dir)['follow']
    assert sum(not np.array_equal(moved[k], reference[(k+turned) % N]) for k in range(N)) >= N//2


def test_t1_the_inspection_carries_every_cell_by_the_cuts_reading(origin, tmp_path):
    o, read_on, _ = origin
    loop_dir = aligned(o, tmp_path/'turned')
    turned = meta_of(loop_dir)['cycle_align']['turned_by']
    reading = meta_of(loop_dir)['cycle_align']['origin']['reading']['carry_px']
    record = follow_inspect.inspect_loop(loop_dir, [chest(o)], tmp_path/'out', on_fold='lower', read_on=read_on)
    assert [c['carry_px'] for c in record['cells']] == [reading[(k+turned) % N] for k in range(N)]
    assert record['inputs']['settings']['read_on'] == read_on
    as_cut = follow_inspect.inspect_loop(o, [chest(o)], tmp_path/'cut-out', on_fold='lower')
    assert [c['carry_px'] for c in as_cut['cells']] == reading


# ---------------------------------------------------------------- T2: a stretch by the nearer cell repeats cells

def test_t2_a_loop_stretched_by_the_nearer_cell_is_carried_cell_for_cell_duplicates_and_all(origin, tmp_path):
    o, read_on, _ = origin
    loop_dir = aligned(o, tmp_path/'stretched', length=17)
    record = meta_of(loop_dir)['cycle_align']
    sources = [entry['source'] for entry in record['cells_from']]
    assert len(sources) == 17 and len(set(sources)) < len(sources)
    for k, cell in enumerate(cells_of(loop_dir)):
        assert np.array_equal(cell, cells_of(o)[sources[k]]), k
    reading = record['origin']['reading']['carry_px']
    inspected = follow_inspect.inspect_loop(loop_dir, [chest(o)], tmp_path/'out', on_fold='lower', read_on=read_on)
    assert [c['carry_px'] for c in inspected['cells']] == [reading[i] for i in sources]
    follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)
    assert meta_of(loop_dir)['follow']['read_on']['cut_cells'] == sources


# ---------------------------------------------------------------- T3: a made cell has no cell of the cut

def test_t3_a_loop_with_made_cells_is_refused_uncertain_and_left_as_it_was(origin, tmp_path):
    o, read_on, _ = origin
    loop_dir = aligned(o, tmp_path/'made', length=2*N, between='rife', interpolate=Blend())
    cells_from = meta_of(loop_dir)['cycle_align']['cells_from']
    made = [k for k, entry in enumerate(cells_from) if 'between' in entry]
    assert made and all(cells_from[k]['t'] == 0.5 and cells_from[k]['between'][1] == (cells_from[k]['between'][0]+1) % N for k in made)
    assert made[0] in (0, 1)  # the first cell is made, or the second
    before = files(loop_dir)
    with pytest.raises(SystemExit, match=rf'uncertain.*cell {made[0]} is made between'):
        follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)
    assert files(loop_dir) == before
    with pytest.raises(SystemExit, match='uncertain'):
        follow_inspect.inspect_loop(loop_dir, [chest(o)], tmp_path/'out', on_fold='lower', read_on=read_on)
    assert not (tmp_path/'out').exists()


# ---------------------------------------------------------------- T4: no correspondence at all

def test_t4_another_clip_or_an_unknown_read_on_is_refused_no_match(origin, tmp_path):
    o, read_on, _ = origin
    other = cut(tmp_path/'other-cut', [runner(k, torso=OTHER, patch=OTHER_PATCH, wide=4) for k in range(N)])
    loop_dir = aligned(other, tmp_path/'other')
    before = files(loop_dir)
    with pytest.raises(SystemExit, match='no-match'):
        follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)
    assert files(loop_dir) == before
    turned = aligned(o, tmp_path/'turned')
    with pytest.raises(SystemExit, match='no-match'):
        follow.follow_loop(turned, [chest(o)], on_fold='lower', read_on='ab'*32)
    # The strip the regions were read on is a strip nothing here was cut or aligned from.
    with pytest.raises(SystemExit, match='no-match'):
        follow.follow_loop(copy(o, tmp_path/'plain'), [chest(o)], on_fold='lower', read_on=sha(turned/'run.strip.png'))


@pytest.mark.parametrize('given', ['AB'*32, 'ab'*31+'a', 'zz'*32, ''])
def test_a_read_on_that_is_no_sha256_is_refused_before_anything_is_read(origin, tmp_path, given):
    o, _, _ = origin
    with pytest.raises(SystemExit, match='--read-on'):
        follow.follow_loop(copy(o, tmp_path/'loop'), [chest(o)], read_on=given)


# ---------------------------------------------------------------- T5: the cut's geometry changed and nothing says how

def test_t5_a_loop_whose_cells_were_cropped_again_is_refused_uncertain(tmp_path):
    frames = [runner(k, arm_out=k == 1) for k in range(N)]  # only cell 1 reaches this far out
    o = cut(tmp_path/'cut', frames)
    read_on = sha(o/'run.strip.png')
    loop_dir = aligned(o, tmp_path/'short', length=6)  # cell 1 is not among the six: the crop narrows
    assert meta_of(loop_dir)['w'] < meta_of(o)['w']
    assert all('source' in entry for entry in meta_of(loop_dir)['cycle_align']['cells_from'])
    before = files(loop_dir)
    with pytest.raises(SystemExit, match='uncertain.*crop or scale'):
        follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)
    assert files(loop_dir) == before


def test_t5_a_cell_drawn_again_after_the_alignment_is_refused_uncertain(origin, tmp_path):
    o, read_on, _ = origin
    loop_dir = aligned(o, tmp_path/'touched')
    strip = Image.open(loop_dir/'run.strip.png').convert('RGBA')
    ImageDraw.Draw(strip).point((meta_of(loop_dir)['w']*4+3, 3), fill=MARK)  # cell 4 is no longer the cut's cell
    strip.save(loop_dir/'run.strip.png')
    with pytest.raises(SystemExit, match='uncertain.*cell 4'):
        follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)


# ---------------------------------------------------------------- T6: nothing asked, nothing changes

LEGACY_KEYS = {'regions', 'gain', 'freq_hz', 'zeta', 'harmonics', 'source', 'body_px', 'body_worn_px', 'body_bob_px', 'dx_px', 'dy_px',
               'reach_px', 'gain_requested', 'on_fold', 'fold', 'gif', 'webp'}


def test_t6_without_read_on_the_record_holds_nothing_new(origin):
    _, _, followed = origin
    assert set(meta_of(followed)['follow']) == LEGACY_KEYS


def test_t6_read_on_the_strip_as_cut_moves_exactly_what_it_moves_without(origin, tmp_path):
    o, read_on, followed = origin
    loop_dir = copy(o, tmp_path/'same')
    follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)
    for name in ('run.strip.png', 'run.gif', 'run.webp', follow.SOURCE):
        assert (loop_dir/name).read_bytes() == (followed/name).read_bytes(), name
    rec = meta_of(loop_dir)['follow']
    assert rec.pop('read_on') == {'sha256': read_on, 'relation': 'same'} and rec.pop('carry_basis') == follow.CARRY_BASIS
    assert rec == meta_of(followed)['follow']
    # Once followed, the strip as cut is read from follow.source.png, so the same read_on is still `same`.
    follow.follow_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on)
    assert meta_of(loop_dir)['follow']['read_on']['relation'] == 'same'


def test_t6_an_alignment_adds_two_keys_and_no_pixel(origin, tmp_path):
    o, _, _ = origin
    loop_dir = aligned(o, tmp_path/'turned')
    record = meta_of(loop_dir)['cycle_align']
    assert {'origin', 'cells_from'} <= set(record)
    report = align.align_set([copy(o, tmp_path/'again')], between='nearest', multi_cycle='warn')
    assert 'origin' not in report['loops'][0]  # the report names each cell's source; the cut's record stays in the meta


# ---------------------------------------------------------------- T7: the floor decides on the cut's numbers

def test_t7_a_floor_on_a_turned_loop_decides_as_on_the_cut(origin, tmp_path):
    o, read_on, _ = origin
    cx, cy, rx, ry = chest(o)
    regions = [(cx, cy, rx, ry), (cx, cy+8, 7.0, 7.0)]  # the soft part, and a small one on it
    turned = aligned(o, tmp_path/'turned')
    for floor in (0.2, 0.35, 0.5):
        outcomes = []
        for label, loop_dir, extra in (('cut', copy(o, tmp_path/f'cut-{floor}'), {}),
                                       ('turned', copy(turned, tmp_path/f'turned-{floor}'), {'read_on': read_on})):
            try:
                follow.follow_loop(loop_dir, regions, on_fold='lower', stretch_floor=floor, **extra)
            except SystemExit as exc:
                outcomes.append(('refused', str(exc)))
                continue
            rec = meta_of(loop_dir)['follow']
            outcomes.append(('moved', rec['gain'], [(r['gain'], r['held']) for r in rec['stretch']['regions']]))
        assert outcomes[0] == outcomes[1], floor


# ---------------------------------------------------------------- C7: the carry is the outline's lay; whose pixels, unread

def x5(k, *, patch_moves):
    """S1/X5: 13 cells of a body that bobs; in cells 8 and 9 the torso's outline lies 6 px to the right while the head
    and legs stay. The chest patch stays with the head (an outline that turns) or moves with the outline."""
    shift = 6 if k in (8, 9) else 0
    dx, dy = round(3*math.sin(2*math.pi*k/N)), round(6*math.cos(4*math.pi*k/N))
    im = Image.new('RGBA', (160, 300))
    d = ImageDraw.Draw(im)
    d.rectangle((55+dx, 188+dy, 75+dx, 285+dy), fill=NEAR)
    d.rectangle((85+dx, 188+dy, 105+dx, 285+dy), fill=NEAR)
    d.rounded_rectangle((45+dx+shift, 70+dy, 115+dx+shift, 190+dy), radius=18, fill=TORSO)
    d.ellipse((60+dx, 25+dy, 100+dx, 65+dy), fill=HEAD)
    d.rectangle((72+dx, 60+dy, 88+dx, 75+dy), fill=HEAD)
    px = 80+dx+(shift if patch_moves else 0)
    d.ellipse((px-18, 94+dy, px+18, 130+dy), fill=PATCH)
    return im


def strip_dir(path, frames):
    """A loop directory holding one cycle as a strip, as video-loop leaves one (no cycle frames: never aligned)."""
    w, h = frames[0].size
    strip = Image.new('RGBA', (w*len(frames), h))
    for k, frame in enumerate(frames):
        strip.paste(frame, (k*w, 0))
    path.mkdir()
    strip.save(path/'walk.strip.png')
    (path/'walk.strip.json').write_text(json.dumps({'kind': 'periodic', 'frames': len(frames), 'w': w, 'h': h, 'delay_ms': 1000/24}))
    return path


def test_o1_two_cells_alike_in_alpha_are_carried_alike_and_the_record_says_whose_pixels_is_unread(tmp_path):
    a = [x5(k, patch_moves=False) for k in range(N)]
    b = [x5(k, patch_moves=True) for k in range(N)]
    assert all(np.array_equal(np.asarray(p.getchannel('A')), np.asarray(q.getchannel('A'))) for p, q in zip(a, b))
    assert any(not np.array_equal(np.asarray(p), np.asarray(q)) for p, q in zip(a, b))
    region = (80.0, 112.0+round(6*math.cos(0)), 16.0, 16.0)
    records = [follow_inspect.inspect_loop(strip_dir(tmp_path/name, frames), [region], tmp_path/f'{name}-out', gain=1, on_fold='lower')
               for name, frames in (('a', a), ('b', b))]
    assert [c['carry_px'] for c in records[0]['cells']] == [c['carry_px'] for c in records[1]['cells']]
    for record in records:
        assert record['carry_basis'] == follow.CARRY_BASIS == {'reading': 'outline-lay', 'region': 'unverified'}
        assert all(r['ownership'] == 'unknown' for c in record['cells'] for r in c['regions'])
    # In b the patch moved 6 px with the outline in cells 8 and 9; the carry cannot say whether it did.
    assert records[0]['cells'][8]['carry_px'] == records[1]['cells'][8]['carry_px']


def syn01_cell(state, t):
    """syn-01: head, torso and legs at one place in every cell; only the near arm changes. A hangs beside the torso
    with a gap, B against its side, C across the front inside the outline, D across the front with the fist out."""
    im = Image.new('RGBA', (160, 300))
    d = ImageDraw.Draw(im)
    sleeve = (40, 60, 120, 255)
    d.rounded_rectangle((45, 70, 115, 190), radius=18, fill=sleeve)
    d.ellipse((60, 25, 100, 65), fill=HEAD)
    d.rectangle((72, 60, 88, 75), fill=HEAD)
    d.rectangle((55, 188, 75, 290), fill=NEAR)
    d.rectangle((85, 188, 105, 290), fill=NEAR)
    d.ellipse((102, 72, 122, 92), fill=sleeve)
    if state == 'A':
        d.rectangle((121, 84, 121+t, 175), fill=sleeve)
    elif state == 'B':
        d.rectangle((115, 84, 115+t, 175), fill=sleeve)
    elif state == 'C':
        d.rectangle((113-t, 84, 113, 130), fill=sleeve)
        d.rectangle((60, 128, 113, 128+t), fill=sleeve)
    elif state == 'D':
        d.rectangle((113-t, 84, 113, 125), fill=sleeve)
        d.rectangle((60, 118, 132, 118+t), fill=sleeve)
        d.ellipse((124, 104, 146, 126), fill=HEAD)
    return im


@pytest.mark.parametrize('arm', [10, 16, 22])
def test_o2_an_arm_added_to_the_outline_does_not_move_the_body(arm):
    rows, cols, _, _ = follow.body_motion([syn01_cell(s, arm) for s in 'AAAABBCCDDAAA'])
    assert rows.tolist() == [0.0]*N and cols.tolist() == [0.0]*N


# ---------------------------------------------------------------- the command

def test_the_commands_take_read_on_and_stretch_floor(origin, tmp_path, capsys):
    o, read_on, _ = origin
    loop_dir = aligned(o, tmp_path/'turned')
    cx, cy, rx, ry = chest(o)
    region = f'{cx:g},{cy:g},{rx:g},{ry:g}'
    assert cli.main(['video-follow-inspect', '--loop-dir', str(loop_dir), '--region', region, '--on-fold', 'lower',
                     '--read-on', read_on, '--stretch-floor', '0.2', '--out-dir', str(tmp_path/'out')]) == 0
    inspected = json.loads((tmp_path/'out'/follow_inspect.RECORD).read_text())
    assert inspected['inputs']['settings']['stretch_floor'] == 0.2 and inspected['inputs']['settings']['read_on'] == read_on
    capsys.readouterr()
    assert cli.main(['video-follow', '--loop-dir', str(loop_dir), '--region', region, '--on-fold', 'lower',
                     '--read-on', read_on, '--stretch-floor', '0.2']) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['read_on'] == 'transported' and printed['stretch_floor'] == 0.2
    assert meta_of(loop_dir)['follow']['stretch']['floor'] == 0.2
    with pytest.raises(SystemExit):
        cli.main(['video-follow', '--loop-dir', str(loop_dir), '--region', region, '--read-on', 'AB'*32])
