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
    assert sorted(rec) == ['body_bob_px', 'body_px', 'dx_px', 'dy_px', 'fold', 'freq_hz', 'gain', 'gain_requested', 'gif',
                           'harmonics', 'on_fold', 'reach_px', 'regions', 'source', 'webp', 'zeta']
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

