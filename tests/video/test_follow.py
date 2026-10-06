"""`video-follow`: a region of a walk loop follows the body's bob, on a synthetic walker."""
import json
import math
import re

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen.video import align, follow, loop


def walker(k, period=24):
    """A front walker that bobs up and down twice a cycle, with a soft blob on its chest."""
    im = Image.new('RGBA', (160, 200))
    d = ImageDraw.Draw(im)
    bob = round(3*math.sin(4*math.pi*k/period))
    lift = 8*math.sin(2*math.pi*k/period)
    d.rectangle((60, 20+bob, 100, 55+bob), fill=(210, 150, 60, 255))  # head
    d.rectangle((50, 58+bob, 110, 120+bob), fill=(20, 90, 180, 255))  # torso
    d.ellipse((62, 70+bob, 98, 96+bob), fill=(230, 90, 120, 255))  # the soft part
    d.rectangle((55, 121+bob+max(0, lift), 75, 190-max(0, lift)/2), fill=(240, 200, 40, 255))
    d.rectangle((85, 121+bob+max(0, -lift), 105, 190-max(0, -lift)/2), fill=(30, 60, 90, 255))
    d.rectangle((52, 60+bob+(k % period), 54, 62+bob+(k % period)), fill=(255, 255, 255, 255))  # no two frames alike
    return im


@pytest.fixture
def loop_dir(tmp_path):
    keyed = tmp_path/'keyed'
    keyed.mkdir()
    for k in range(73):
        walker(k).save(keyed/f'{k:03}.png')
    out = tmp_path/'loop'
    code = loop.main(['--frames-dir', str(keyed), '--out-dir', str(out), '--state', 'walk', '--anchor', 'motion-auto',
                      '--repair', 'off', '--report', str(tmp_path/'loop.json'), '--name', 'walk'])
    assert code == 0
    return out


def cells(path, meta):
    strip = Image.open(path).convert('RGBA')
    return [np.asarray(strip.crop((k*meta['w'], 0, (k+1)*meta['w'], meta['h']))) for k in range(meta['frames'])]


def chest_region(loop_dir):
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    first = cells(loop_dir/'walk.strip.png', meta)[0]
    pink = np.all(np.abs(first[..., :3].astype(int)-(230, 90, 120)) < 40, axis=-1) & (first[..., 3] > 200)
    ys, xs = np.nonzero(pink)
    return ((xs.min()+xs.max())/2, (ys.min()+ys.max())/2, (xs.max()-xs.min())/2+4, (ys.max()-ys.min())/2+4)


def test_the_region_moves_with_the_bob_and_nothing_else_does(loop_dir):
    before = json.loads((loop_dir/'walk.strip.json').read_text())
    old = cells(loop_dir/'walk.strip.png', before)
    region = chest_region(loop_dir)
    result = follow.follow_loop(loop_dir, [region])
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    new = cells(loop_dir/'walk.strip.png', meta)
    rec = meta['follow']
    # The body bobs twice a cycle; the part answers it.
    assert rec['body_bob_px'][1] >= 4
    assert max(abs(v) for v in rec['dy_px']) > 1
    cx, cy, rx, ry = region
    h, w = new[0].shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    rows = np.asarray([np.nonzero((c[..., 3] >= 128).any(axis=1))[0][0] for c in old], float)
    for k, (a, b) in enumerate(zip(old, new)):
        # Outside the region, carried with the bob, every pixel is as it was.
        r = np.sqrt(((xx-cx)/rx)**2+((yy-cy-(rows[k]-rows[0]))/ry)**2)
        assert np.array_equal(a[r >= 1.05], b[r >= 1.05]), k
    assert any(not np.array_equal(a, b) for a, b in zip(old, new))
    assert result['gif']['n_frames'] == before['frames'] and result['webp']['n_frames'] == before['frames']
    assert (loop_dir/follow.SOURCE).exists()


def with_top(k, kind, period=24):
    """The walker with something thin swinging over its head: a tail from the hip whose tip rises above
    the crown for part of the cycle, or a sword held up and swung over the head from side to side."""
    im = walker(k, period)
    d = ImageDraw.Draw(im)
    bob = round(3*math.sin(4*math.pi*k/period))
    phase = 2*math.pi*k/period
    if kind == 'tail':
        d.line((108, 100+bob, 125+25*math.cos(phase), 12+14*math.sin(phase)), fill=(120, 70, 30, 255), width=3)
    else:
        d.line((102, 40+bob, 80+45*math.sin(phase), 4+6*(1-math.cos(phase))), fill=(200, 200, 220, 255), width=4)
    return im


def strip_dir(path, draw, n=24):
    """A loop directory holding one cycle of `draw(k)` as a strip, as video-loop leaves one."""
    cells = [draw(k) for k in range(n)]
    w, h = cells[0].size
    strip = Image.new('RGBA', (w*n, h))
    for k, c in enumerate(cells):
        strip.paste(c, (k*w, 0))
    path.mkdir()
    strip.save(path/'walk.strip.png')
    (path/'walk.strip.json').write_text(json.dumps({'kind': 'periodic', 'frames': n, 'w': w, 'h': h, 'delay_ms': 1000/24}))
    return path


@pytest.mark.parametrize('kind', ['tail', 'sword'])
def test_what_swings_over_the_head_does_not_move_the_body(tmp_path, kind):
    # The motion was read off the top of the body, so whatever came to the top was the body: a tail tip
    # rising over the head made the read jump across the cell, a sword held up made the body follow its tip.
    chest = (80.0, 83.0, 22.0, 17.0)  # the soft part, as walker draws it in cell 0
    bare = follow.follow_loop(strip_dir(tmp_path/'bare', walker), [chest], on_fold='lower')
    topped = follow.follow_loop(strip_dir(tmp_path/kind, lambda k: with_top(k, kind)), [chest], on_fold='lower')
    # The head and torso bob 3 px up and down twice a cycle and do not move across.
    assert bare['body_bob_px'] == [0.0, 6.0]
    for key in ('body_bob_px', 'dx_px', 'dy_px', 'reach_px', 'gain', 'fold'):
        assert topped[key] == bare[key], key
    # The chest moves alike under both: what the follow-through changed in the chest is the same pixels.
    moved = [np.asarray(Image.open(p/'walk.strip.png').convert('RGBA')) for p in (tmp_path/'bare', tmp_path/kind)]
    source = [np.asarray(Image.open(p/follow.SOURCE).convert('RGBA')) for p in (tmp_path/'bare', tmp_path/kind)]
    changed = [(m != s).any(axis=-1) for m, s in zip(moved, source)]
    assert changed[0].any() and np.array_equal(changed[0], changed[1])
    assert np.array_equal(moved[0][changed[0]], moved[1][changed[1]])


def test_a_cell_with_no_core_is_refused(tmp_path):
    # A cell whose body is nowhere a quarter as deep as cell 0's has nothing to lay on cell 0's.
    def draw(k):
        if k != 5:
            return walker(k)
        im = Image.new('RGBA', (160, 200))
        ImageDraw.Draw(im).line((80, 20, 80, 190), fill=(20, 90, 180, 255), width=5)
        return im
    loop_dir = strip_dir(tmp_path/'thin', draw)
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    with pytest.raises(SystemExit, match=r"^video-follow: cell 5 has no body as deep as a quarter of cell 0's \(\d+ px\)$"):
        follow.follow_loop(loop_dir, [(80.0, 83.0, 22.0, 17.0)])
    assert (loop_dir/'walk.strip.png').read_bytes() == as_cut
    assert 'follow' not in json.loads((loop_dir/'walk.strip.json').read_text())


def bunny(k, ears, period=24):
    """A front bunny, its head, torso and legs one column that bobs twice a cycle, with or without lop ears out
    to both sides of its head: each more than half as thick as the column is wide and nearly as long, swinging
    down and back up once a cycle."""
    im = Image.new('RGBA', (210, 240))
    d = ImageDraw.Draw(im)
    bob = round(3*math.sin(4*math.pi*k/period))
    d.rectangle((70, 30+bob, 140, 225+bob), fill=(250, 250, 250, 255))  # head, torso and legs
    d.ellipse((85, 110+bob, 125, 150+bob), fill=(230, 90, 120, 255))  # the soft part
    if ears:
        droop = round(60*(1-math.cos(2*math.pi*k/period)))
        d.rectangle((10, 32+bob+droop, 70, 72+bob+droop), fill=(235, 235, 240, 255))
        d.rectangle((140, 32+bob+droop, 200, 72+bob+droop), fill=(235, 235, 240, 255))
    return im


def test_ears_as_thick_as_the_body_do_not_move_it(tmp_path):
    # Ears this thick outlast the wearing, and a cell laid on cell 0 alone lies ears on ears: in the cells where
    # the ears had swung less than their own thickness from where cell 0 has them the body was read as dropping
    # with them, by up to 30 px, and as jumping back in the next cell. Laid again on what the cells have in
    # common, the ears are at one place in too few cells to count.
    chest = (105.0, 130.0, 24.0, 24.0)  # the soft part, as bunny draws it in cell 0
    bare = follow.follow_loop(strip_dir(tmp_path/'bare', lambda k: bunny(k, ears=False)), [chest], on_fold='lower')
    eared = follow.follow_loop(strip_dir(tmp_path/'eared', lambda k: bunny(k, ears=True)), [chest], on_fold='lower')
    assert bare['body_bob_px'] == [0.0, 6.0]
    for key in ('body_bob_px', 'dx_px', 'dy_px', 'reach_px', 'gain', 'fold'):
        assert eared[key] == bare[key], key
    moved = [np.asarray(Image.open(p/'walk.strip.png').convert('RGBA')) for p in (tmp_path/'bare', tmp_path/'eared')]
    source = [np.asarray(Image.open(p/follow.SOURCE).convert('RGBA')) for p in (tmp_path/'bare', tmp_path/'eared')]
    changed = [(m != s).any(axis=-1) for m, s in zip(moved, source)]
    assert changed[0].any() and np.array_equal(changed[0], changed[1])
    assert np.array_equal(moved[0][changed[0]], moved[1][changed[1]])


def test_cells_with_no_body_in_common_are_refused(tmp_path):
    # What the cells have in common is what more than half of them have at one place. A bar in cell 0 and a
    # block at a different place along it in each other cell: no place is in more than two of the four.
    def draw(k):
        im = Image.new('RGBA', (160, 200))
        box = (20, 80, 140, 120) if k == 0 else (20+40*(k-1), 80, 60+40*(k-1), 120)
        ImageDraw.Draw(im).rectangle(box, fill=(20, 90, 180, 255))
        return im
    loop_dir = strip_dir(tmp_path/'apart', draw, n=4)
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    with pytest.raises(SystemExit, match=r'^video-follow: no part of the body is at one place in more than half of the cells$'):
        follow.follow_loop(loop_dir, [(80.0, 100.0, 22.0, 17.0)])
    assert (loop_dir/'walk.strip.png').read_bytes() == as_cut
    assert 'follow' not in json.loads((loop_dir/'walk.strip.json').read_text())


def test_the_part_lags_and_settles_without_a_kick():
    # One cycle of a smooth bob: the answer is as smooth (no frame-to-frame jump far beyond the rest)
    # and closes on itself, since it is the loop's steady state.
    n, fps = 36, 24.0
    bob = 6*np.sin(4*np.pi*np.arange(n)/n)
    dy = follow.follow_offsets(bob, fps, freq=2.4, zeta=0.6, gain=1.0)
    steps = np.abs(np.diff(np.r_[dy, dy[0]]))
    assert steps.max() <= 2*np.median(steps)+1e-9
    assert abs(dy.mean()) < 1e-9
    # A body that does not move moves nothing.
    assert np.allclose(follow.follow_offsets(np.zeros(n), fps, freq=2.4, zeta=0.6, gain=2.5), 0)


def test_running_it_again_reads_the_strip_as_cut(loop_dir):
    region = chest_region(loop_dir)
    follow.follow_loop(loop_dir, [region])
    once = (loop_dir/'walk.strip.png').read_bytes()
    follow.follow_loop(loop_dir, [region])
    assert (loop_dir/'walk.strip.png').read_bytes() == once
    follow.follow_loop(loop_dir, [region], gain=0)
    assert (loop_dir/'walk.strip.png').read_bytes() == (loop_dir/follow.SOURCE).read_bytes()


def test_an_alignment_clears_the_follow_through_and_says_so(loop_dir):
    region = chest_region(loop_dir)
    follow.follow_loop(loop_dir, [region])
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    report = align.align_set([loop_dir], length=meta['cycle_frames'])
    assert report['loops'][0]['follow_cleared'] is True
    after = json.loads((loop_dir/'walk.strip.json').read_text())
    assert 'follow' not in after
    assert not (loop_dir/follow.SOURCE).exists()


def test_a_new_cut_removes_the_old_follow_source(loop_dir, tmp_path):
    follow.follow_loop(loop_dir, [chest_region(loop_dir)])
    code = loop.main(['--frames-dir', str(tmp_path/'keyed'), '--out-dir', str(loop_dir), '--state', 'walk', '--anchor', 'motion-auto',
                      '--repair', 'off', '--report', str(tmp_path/'loop2.json'), '--name', 'walk'])
    assert code == 0
    assert not (loop_dir/follow.SOURCE).exists()
    assert 'follow' not in json.loads((loop_dir/'walk.strip.json').read_text())


def test_a_move_that_would_fold_the_region_is_refused(loop_dir):
    cx, cy, _, _ = chest_region(loop_dir)
    with pytest.raises(SystemExit, match='folds a region'):
        follow.follow_loop(loop_dir, [(cx, cy, 2, 2)], gain=50)


def test_the_default_refusal_reads_as_it_did(loop_dir):
    cx, cy, _, _ = chest_region(loop_dir)
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    with pytest.raises(SystemExit) as refused:
        follow.follow_loop(loop_dir, [(cx, cy, 2, 2)], gain=50)
    assert re.fullmatch(r'video-follow: a move of \d+\.\d px folds a region of radius 2 px over itself; '
                        r'lower --gain or give the region larger radii', str(refused.value))
    assert (loop_dir/'walk.strip.png').read_bytes() == as_cut
    assert 'follow' not in json.loads((loop_dir/'walk.strip.json').read_text())


def small_region(loop_dir, share):
    """A region on the chest whose smaller radius folds at `share` of the default gain."""
    cx, cy, rx, _ = chest_region(loop_dir)
    reach = follow.follow_loop(loop_dir, [(cx, cy, 40, 40)])['reach_px']
    return (cx, cy, rx, share*reach*math.pi/2)


def head_region(loop_dir, share):
    """A round region on the crown, clear of the chest, whose radius folds at `share` of the default gain.

    On the head's top edge, so its move changes pixels: inside the flat head nothing would."""
    radius = small_region(loop_dir, share)[3]
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    first = cells(loop_dir/'walk.strip.png', meta)[0]
    head = np.all(np.abs(first[..., :3].astype(int)-(210, 150, 60)) < 40, axis=-1) & (first[..., 3] > 200)
    ys, xs = np.nonzero(head)
    return ((xs.min()+xs.max())/2, float(ys.min()), radius, radius)


def folds_nowhere(rec, meta):
    """Every cell's picture runs forwards: along the move, the sample point never turns back."""
    h, w = meta['h'], meta['w']
    yy, xx = np.mgrid[0:h, 0:w].astype(float)
    worst = 0.0
    for dx, dy in zip(rec['dx_px'], rec['dy_px']):
        weight = np.zeros((h, w))
        for (cx, cy, rx, ry), entry in zip(rec['regions'], rec['fold']['regions']):
            # A region that took a gain of its own moves by that share of the strip's move.
            share = entry.get('gain', rec['gain'])/rec['gain']
            r = np.sqrt(((xx-cx)/rx)**2+((yy-cy)/ry)**2)
            weight = np.maximum(weight, share*np.where(r < 1, np.cos(r*math.pi/2)**2, 0))
        gy, gx = np.gradient(weight)
        worst = max(worst, float(np.max(dx*gx+dy*gy)))
    return worst < 1, worst


def test_lower_takes_the_largest_gain_that_does_not_fold(loop_dir):
    region = small_region(loop_dir, 0.7)  # folds at 0.7 of the default gain: 1.75
    with pytest.raises(SystemExit, match='folds a region'):
        follow.follow_loop(loop_dir, [region])
    result = follow.follow_loop(loop_dir, [region], on_fold='lower')
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    rec = meta['follow']
    assert rec['gain_requested'] == follow.GAIN_DEFAULT and rec['on_fold'] == 'lower' and rec['fold']['lowered'] is True
    assert result['gain'] == rec['gain']
    # Under the request, not under the mass as measured, and the largest that passes: the next step folds.
    limit = rec['fold']['regions'][0]['gain_limit']
    assert 1.70 < limit < 1.80 and follow.GAIN_MEASURED <= rec['gain'] < limit <= rec['gain']+follow.GAIN_STEP+1e-9
    assert 0.99 <= rec['fold']['ratio'] < 1
    assert rec['fold']['regions'][0]['radius_px'] == pytest.approx(region[3])
    assert rec['reach_px'] < rec['fold']['reach_requested_px']
    assert rec['reach_px'] == pytest.approx(rec['gain']*rec['fold']['reach_per_gain_px'], abs=0.02)
    ok, worst = folds_nowhere(rec, meta)
    assert ok, worst
    lowered = (loop_dir/'walk.strip.png').read_bytes()
    assert lowered != (loop_dir/follow.SOURCE).read_bytes()
    # The report's gain is the --gain that gives this strip; one step more is refused.
    follow.follow_loop(loop_dir, [region], gain=rec['gain'])
    assert (loop_dir/'walk.strip.png').read_bytes() == lowered
    assert json.loads((loop_dir/'walk.strip.json').read_text())['follow']['fold']['lowered'] is False
    with pytest.raises(SystemExit, match='folds a region'):
        follow.follow_loop(loop_dir, [region], gain=rec['gain']+follow.GAIN_STEP)


def moved_alone(loop_dir, region, **kw):
    """The strip with `region` moved by itself, and the pixels that moved."""
    follow.follow_loop(loop_dir, [region], on_fold='lower', **kw)
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    strip = np.asarray(Image.open(loop_dir/'walk.strip.png').convert('RGBA'))
    source = np.asarray(Image.open(loop_dir/follow.SOURCE).convert('RGBA'))
    return strip, (strip != source).any(axis=-1), meta['follow']


def test_each_region_takes_its_own_gain(loop_dir):
    small = head_region(loop_dir, 0.7)  # folds at 1.75
    chest = chest_region(loop_dir)  # does not fold at the default gain
    little_alone, little_moved, alone = moved_alone(loop_dir, small)
    chest_alone, chest_moved, chest_rec = moved_alone(loop_dir, chest)
    assert alone['fold']['lowered'] is True and follow.GAIN_MEASURED <= alone['gain'] < follow.GAIN_DEFAULT
    assert little_moved.any() and chest_moved.any() and not (little_moved & chest_moved).any()
    both = follow.follow_loop(loop_dir, [chest, small], on_fold='lower')
    large, little = both['fold']['regions']
    # The chest moves at the gain asked for; the small region at the gain it takes alone.
    assert both['gain'] == large['gain'] == both['gain_requested'] == follow.GAIN_DEFAULT
    assert little['gain'] == alone['gain'] and large['held'] is little['held'] is False
    assert both['fold']['lowered'] is True
    assert large['gain_limit'] > follow.GAIN_DEFAULT > little['gain_limit']
    assert little['ratio'] == alone['fold']['ratio'] and 0.99 <= little['ratio'] < 1 and large['ratio'] < 1
    assert both['fold']['ratio'] == max(large['ratio'], little['ratio'])
    # The strip's move is the chest's, as when the chest moves alone.
    assert (both['dx_px'], both['dy_px'], both['reach_px']) == (chest_rec['dx_px'], chest_rec['dy_px'], chest_rec['reach_px'])
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    strip = np.asarray(Image.open(loop_dir/'walk.strip.png').convert('RGBA'))
    source = np.asarray(Image.open(loop_dir/follow.SOURCE).convert('RGBA'))
    # Each region's pixels are as when it moves alone at its own gain; nothing else changed.
    assert np.array_equal(strip[little_moved], little_alone[little_moved])
    assert np.array_equal(strip[chest_moved], chest_alone[chest_moved])
    assert np.array_equal(strip[~(little_moved | chest_moved)], source[~(little_moved | chest_moved)])
    ok, worst = folds_nowhere(meta['follow'], meta)
    assert ok, worst


def test_overlapping_regions_of_different_gains_fold_nowhere(loop_dir):
    # A small region on the chest takes a lower gain than a large one over it: where they overlap the
    # larger move wins, and the move is nowhere steeper than either region's own.
    small = small_region(loop_dir, 0.7)
    cx, cy, rx, ry = chest_region(loop_dir)
    result = follow.follow_loop(loop_dir, [(cx, cy+6, rx+6, ry+6), small], on_fold='lower')
    large, little = result['fold']['regions']
    assert large['gain'] == follow.GAIN_DEFAULT > little['gain'] >= follow.GAIN_MEASURED
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    ok, worst = folds_nowhere(meta['follow'], meta)
    assert ok, worst


def test_a_region_too_small_to_move_is_held_and_the_rest_move(loop_dir):
    # One gain for the strip, set by its smallest region, refused the whole strip for a region under the
    # mass as measured: the chest did not move because of an ear.
    chest = chest_region(loop_dir)
    ear = head_region(loop_dir, 0.3)  # folds at 0.75: under the mass as measured
    chest_alone, chest_moved, _ = moved_alone(loop_dir, chest)
    result = follow.follow_loop(loop_dir, [chest, ear], on_fold='lower')
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    rec = meta['follow']
    moving, held = rec['fold']['regions']
    assert held['held'] is True and held['gain'] == 0 and held['ratio'] == 0 and held['gain_limit'] < follow.GAIN_MEASURED
    assert moving['held'] is False and moving['gain'] == rec['gain'] == follow.GAIN_DEFAULT
    assert rec['fold']['lowered'] is True and result['gain'] == rec['gain']
    # The held region does not move: the strip is the chest's alone, byte for byte.
    assert np.array_equal(np.asarray(Image.open(loop_dir/'walk.strip.png').convert('RGBA')), chest_alone)
    # A gain asked for under 1 that does not fold the chest still moves it; only the ear is held.
    low = follow.follow_loop(loop_dir, [chest, ear], gain=0.9, on_fold='lower')
    assert [e['gain'] for e in low['fold']['regions']] == [0.9, 0] and [e['held'] for e in low['fold']['regions']] == [False, True]
    # Refusing is still the default: the ear folds at the gain asked for.
    with pytest.raises(SystemExit, match='folds a region of radius'):
        follow.follow_loop(loop_dir, [chest, ear])


def test_a_strip_whose_every_region_is_held_is_refused(loop_dir):
    ear, other = head_region(loop_dir, 0.3), small_region(loop_dir, 0.2)
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    meta = (loop_dir/'walk.strip.json').read_text()
    with pytest.raises(SystemExit) as refused:
        follow.follow_loop(loop_dir, [other, ear], on_fold='lower')
    # Named by the largest region, the last to fold: if it is held, so is every other.
    assert re.fullmatch(rf'video-follow: a move of \d+\.\d px folds a region of radius {ear[3]:g} px over itself '
                        r'\(the largest of 2 regions: every one folds\), and --on-fold lower would have to go under --gain 1 '
                        r'\(the mass as measured moves \d+\.\d px; the largest gain that does not fold is 0\.\d+\); give the region larger radii',
                        str(refused.value))
    assert (loop_dir/'walk.strip.png').read_bytes() == as_cut
    assert (loop_dir/'walk.strip.json').read_text() == meta


def test_regions_that_take_one_gain_are_written_as_one_gain(loop_dir):
    # Two regions of the same smaller radius fold at the same gain: the record is the one-gain record,
    # with no gain of its own per region, and the strip is the one that gain asked for gives.
    a, b = small_region(loop_dir, 0.7), head_region(loop_dir, 0.7)
    result = follow.follow_loop(loop_dir, [a, b], on_fold='lower')
    rec = json.loads((loop_dir/'walk.strip.json').read_text())['follow']
    assert rec['fold']['lowered'] is True and follow.GAIN_MEASURED <= rec['gain'] < follow.GAIN_DEFAULT
    assert [sorted(e) for e in rec['fold']['regions']] == [['gain_limit', 'radius_px', 'ratio']]*2
    assert sorted(rec) == ['body_bob_px', 'body_px', 'body_worn_px', 'dx_px', 'dy_px', 'fold', 'freq_hz', 'gain', 'gain_requested',
                           'gif', 'harmonics', 'on_fold', 'reach_px', 'regions', 'source', 'webp', 'zeta']
    assert sorted(rec['fold']) == ['lowered', 'ratio', 'reach_per_gain_px', 'reach_requested_px', 'regions']
    lowered = (loop_dir/'walk.strip.png').read_bytes()
    follow.follow_loop(loop_dir, [a, b], gain=result['gain'])
    assert (loop_dir/'walk.strip.png').read_bytes() == lowered


def test_lower_does_not_go_under_the_mass_as_measured(loop_dir):
    region = small_region(loop_dir, 0.3)  # folds at 0.75: under gain 1
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    meta = (loop_dir/'walk.strip.json').read_text()
    with pytest.raises(SystemExit) as refused:
        follow.follow_loop(loop_dir, [region], on_fold='lower')
    assert 'folds a region' in str(refused.value) and 'under --gain 1' in str(refused.value)
    assert (loop_dir/'walk.strip.png').read_bytes() == as_cut
    assert (loop_dir/'walk.strip.json').read_text() == meta
    # A request already under 1 that folds is refused too: lowering only goes down.
    with pytest.raises(SystemExit, match='under --gain 1'):
        follow.follow_loop(loop_dir, [small_region(loop_dir, 0.3)], gain=0.9, on_fold='lower')


def test_lower_changes_nothing_when_nothing_folds(loop_dir):
    region = chest_region(loop_dir)
    refuse = follow.follow_loop(loop_dir, [region])
    strip = (loop_dir/'walk.strip.png').read_bytes()
    lower = follow.follow_loop(loop_dir, [region], on_fold='lower')
    assert (loop_dir/'walk.strip.png').read_bytes() == strip
    assert lower['gain'] == lower['gain_requested'] == refuse['gain'] == follow.GAIN_DEFAULT
    assert lower['fold']['lowered'] is False and lower['dy_px'] == refuse['dy_px']
    assert lower['fold']['ratio'] < 1 and lower['fold']['reach_requested_px'] == lower['reach_px']


def test_the_command_takes_on_fold_and_says_what_it_lowered_to(loop_dir, capsys):
    region = small_region(loop_dir, 0.7)
    text = ','.join(f'{v:.3f}' for v in region)
    with pytest.raises(SystemExit, match='folds a region'):
        follow.main(['--loop-dir', str(loop_dir), '--region', text])
    capsys.readouterr()
    assert follow.main(['--loop-dir', str(loop_dir), '--region', text, '--on-fold', 'lower']) == 0
    out, err = capsys.readouterr()
    printed = json.loads(out)
    assert printed['gain_requested'] == 2.5 and printed['on_fold'] == 'lower' and 1 <= printed['gain'] < 2.5
    assert f"lowered to --gain {printed['gain']:g}" in err and 'region_gains' not in printed
    with pytest.raises(SystemExit, match='unknown --on-fold'):
        follow.follow_loop(loop_dir, [region], on_fold='quietly')


def test_the_command_names_the_regions_it_lowered_and_held(loop_dir, capsys):
    regions = [chest_region(loop_dir), small_region(loop_dir, 0.7), head_region(loop_dir, 0.3)]
    texts = [','.join(f'{v:.3f}' for v in r) for r in regions]
    names = ['--region '+','.join(f'{float(v):g}' for v in t.split(',')) for t in texts]
    capsys.readouterr()
    assert follow.main(['--loop-dir', str(loop_dir), '--on-fold', 'lower', *(x for t in texts for x in ('--region', t))]) == 0
    out, err = capsys.readouterr()
    printed = json.loads(out)
    chest, small, ear = printed['region_gains']
    assert printed['gain'] == chest == 2.5 and 1 <= small < 2.5 and ear == 0
    lowered, held = err.strip().splitlines()
    assert lowered.startswith(f'video-follow: --gain 2.5 folds {names[1]} (a move of ') and lowered.endswith(f'lowered to --gain {small:g} for it')
    assert held.startswith(f'video-follow: {names[2]} folds at --gain 0.') and held.endswith('under 1 (the mass as measured): it is held, and does not move')


def outputs(loop_dir):
    """What a follow-through writes over the loop, by name."""
    return {name: (loop_dir/name).read_bytes() for name in ('walk.strip.png', 'walk.gif', 'walk.webp', 'walk.strip.json')}


def test_numpy_numbers_write_what_the_command_line_writes(loop_dir):
    # The command line gives floats. Numpy numbers from a caller wrote the strip, the GIF and the WebP and then
    # failed on the record (an int64 has no JSON form): a moved strip beside a record that does not say so.
    chest = tuple(float(round(v)) for v in chest_region(loop_dir))
    head = tuple(float(round(v)) for v in head_region(loop_dir, 0.7))  # folds at the default gain; the chest does not
    given = follow.follow_loop(loop_dir, [chest, head], on_fold='lower')
    assert 'held' in given['fold']['regions'][0]  # the regions took gains of their own
    written = outputs(loop_dir)
    for kind in (np.int64, np.int32, np.float32, np.float64, int):
        result = follow.follow_loop(loop_dir, [tuple(kind(v) for v in r) for r in (chest, head)], on_fold='lower')
        assert outputs(loop_dir) == written, kind
        assert json.dumps(result) == json.dumps(given), kind
    # The regions as the rows of an array, and the settings as numpy numbers.
    result = follow.follow_loop(loop_dir, np.asarray([chest, head], dtype=np.int64), gain=np.float32(follow.GAIN_DEFAULT),
                                freq=np.float64(follow.FREQ_DEFAULT), zeta=np.float64(follow.ZETA_DEFAULT), on_fold='lower')
    assert outputs(loop_dir) == written and json.dumps(result) == json.dumps(given)


@pytest.mark.parametrize('failure', ['record', 'animation'])
@pytest.mark.parametrize('followed', [False, True])
def test_a_run_that_fails_leaves_the_loop_as_it_was(loop_dir, monkeypatch, failure, followed):
    # The strip and its animations were written over the loop's before the record was: a record that did not
    # serialise, or an animation that failed its check, left them moved beside a record that does not say so.
    region = chest_region(loop_dir)
    if followed:
        follow.follow_loop(loop_dir, [region])
    before, names = outputs(loop_dir), sorted(p.name for p in loop_dir.iterdir())
    as_cut = (loop_dir/follow.SOURCE).read_bytes() if followed else before['walk.strip.png']
    check = loop.verify_animation

    def failing(path, **kw):
        report = check(path, **kw)
        if failure == 'animation' and path.suffix == '.webp':
            raise SystemExit(f'video-loop: {path.name} failed verification: forced')
        return report | ({'forced': object()} if failure == 'record' else {})

    monkeypatch.setattr(loop, 'verify_animation', failing)
    with pytest.raises(TypeError if failure == 'record' else SystemExit):
        follow.follow_loop(loop_dir, [region], gain=1.5)
    assert outputs(loop_dir) == before
    # Nothing is left beside the loop but the strip as cut, kept as on any refusal.
    assert sorted(p.name for p in loop_dir.iterdir()) == sorted({*names, follow.SOURCE})
    assert (loop_dir/follow.SOURCE).read_bytes() == as_cut


def test_bad_regions_are_refused(loop_dir):
    with pytest.raises(SystemExit, match='outside'):
        follow.follow_loop(loop_dir, [(9999, 10, 5, 5)])
    with pytest.raises(Exception):
        follow.parse_region('1,2,3')
    with pytest.raises(Exception):
        follow.parse_region('1,2,0,3')


def test_a_region_over_the_soft_outline_leaves_no_colour_under_clear_pixels(tmp_path):
    # The part on the outline: its edge is partly transparent, and moving it must not leave colour
    # where the coverage rounds to nothing (the WebP check refuses that).
    keyed = tmp_path/'soft'
    keyed.mkdir()
    for k in range(73):
        big = walker(k).resize((640, 800), Image.Resampling.NEAREST)
        ImageDraw.Draw(big).ellipse((400, 260, 520, 400), fill=(230, 90, 120, 255))  # bulges past the torso's side
        big.convert('RGBa').resize((160, 200), Image.Resampling.BOX).convert('RGBA').save(keyed/f'{k:03}.png')
    out = tmp_path/'soft-loop'
    assert loop.main(['--frames-dir', str(keyed), '--out-dir', str(out), '--state', 'walk', '--anchor', 'motion-auto',
                      '--repair', 'off', '--report', str(tmp_path/'soft.json'), '--name', 'walk']) == 0
    meta = json.loads((out/'walk.strip.json').read_text())
    first = cells(out/'walk.strip.png', meta)[0]
    pink = np.all(np.abs(first[..., :3].astype(int)-(230, 90, 120)) < 40, axis=-1) & (first[..., 3] > 0)
    ys, xs = np.nonzero(pink)
    region = (float(xs.max()-6), (ys.min()+ys.max())/2, 14.0, (ys.max()-ys.min())/2+4)
    result = follow.follow_loop(out, [region], gain=3)
    assert result['webp']['stale_rgb_under_alpha0'] == 0
    for c in cells(out/'walk.strip.png', meta):
        assert not np.any((c[..., 3] == 0) & np.any(c[..., :3] > 0, axis=-1))

