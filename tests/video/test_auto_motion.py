"""Automatic postprocessing exercised with known synthetic movement, no private assets."""
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen.video import auto_motion, batch, local_cycle, loop


def walker(k, *, repeat=True):
    im = Image.new('RGBA', (280, 180))
    d = ImageDraw.Draw(im)
    x = 45+k
    y = 18+round(k*.2)+round(3*math.sin(k*2*math.pi/24))
    d.rectangle((x, y, x+28, y+30), fill=(210, 150, 60, 255))
    d.rectangle((x+20, y+8, x+24, y+12), fill=(10, 30, 50, 255))
    d.rectangle((x-3, y+32, x+30, y+78), fill=(20, 90, 180, 255))
    d.rectangle((x+10, y+35, x+15, y+73), fill=(210, 190, 40, 255))
    leg = round(12*math.sin(k*2*math.pi/24)) if repeat else 0
    d.rectangle((x+leg, y+79, x+6+leg, y+119), fill=(80, 30, 60, 255))
    d.rectangle((x+24-leg, y+79, x+30-leg, y+119), fill=(30, 60, 90, 255))
    d.point((x+leg+2, y+85+k%12), fill=(240, 240, 240, 255))
    return im


def detect(D, trajectory=None, **kwargs):
    return local_cycle.detect(D, np.zeros(len(D)) if trajectory is None else trajectory,
                              min_len=12, max_len=36, gait_floor=14,
                              periodicity_min=.15, double_tolerance=.25, double_search=3, **kwargs)


def test_local_repeat_refuses_static_and_single_excursion():
    for values in [np.zeros(80), np.array([10 if 40 <= k <= 44 else 0 for k in range(80)])]:
        D = np.abs(values[:, None]-values[None, :]).astype(np.float32)
        with pytest.raises(ValueError, match='no periodic cycle'):
            detect(D)


def test_symmetric_steps_keep_two_phase_occurrences_and_respect_window():
    values = np.array([[math.sin(k*2*math.pi/12), math.cos(k*2*math.pi/12)] for k in range(80)])
    D = np.abs(values[:, None]-values[None, :]).mean(axis=2)
    result = detect(D)
    assert result['length'] == 24
    assert result['half_period_guard']['from'] == 12
    assert result['half_period_guard']['to'] == 24
    # A hard ceiling below the doubled recurrence must not be silently widened.
    with pytest.raises(ValueError, match='no periodic cycle'):
        local_cycle.detect(D, np.zeros(80), min_len=12, max_len=18, gait_floor=14,
                           periodicity_min=.15, double_tolerance=.25, double_search=3)


def gathered_match(reference, moving, center, radius):
    """The masked NCC written out directly: one gathered masked patch per offset."""
    (x, y, _, _), mask, feature, norm = reference
    cx, cy = center
    height, width = mask.shape
    xs, xe = max(0, x-cx-radius), min(moving.shape[1]-width, x-cx+radius)
    ys, ye = max(0, y-cy-radius), min(moving.shape[0]-height, y-cy+radius)
    windows = np.lib.stride_tricks.sliding_window_view(
        moving[ys:ye+height, xs:xe+width], (height, width), axis=(0, 1))
    patches = windows.transpose(0, 1, 3, 4, 2)[:, :, mask, :]
    centered = patches - patches.mean(axis=2, keepdims=True)
    denominator = np.sqrt((centered * centered).sum(axis=(2, 3))) * norm
    costs = 1 - (centered * feature).sum(axis=(2, 3)) / np.maximum(1e-10, denominator)
    return {(x-(xs+ix), y-(ys+iy)): float(costs[iy, ix]) for iy in range(costs.shape[0]) for ix in range(costs.shape[1])}


def test_match_equals_the_gathered_masked_ncc():
    rng = np.random.default_rng(7)
    unique = tied = 0
    for k in range(0, 40, 3):
        a = auto_motion.motion_anchor._features(walker(0))
        moving = auto_motion.motion_anchor._features(walker(k))
        for box in [(48, 30, 70, 50), (44, 60, 72, 84), (50, 100, 66, 126)]:
            reference = auto_motion._reference(a, box)
            if reference is None:
                continue
            center = tuple(int(v) for v in rng.integers(-3, 4, size=2) + np.array([-k, -round(k*.2)]))
            try:
                got = auto_motion._match(reference, moving, center, 12)
            except ValueError:
                continue
            expected = gathered_match(reference, moving, center, 12)
            best = min(expected.values())
            # The flat synthetic torso matches itself at several vertical shifts. The direct form
            # rounds in float32, so its tie is read loosely; the FFT form takes, among its own tie
            # (MATCH_TIE), the offset nearest the centre it searched around.
            ties = [offset for offset, cost in expected.items() if cost <= best + 1e-5]
            assert tuple(got[0].tolist()) in ties
            assert abs(got[1]-expected[tuple(got[0].tolist())]) < 1e-6
            if len(ties) == 1:
                unique += 1
            else:
                tied += 1
    assert unique >= 12 and tied >= 1


def _rounding(monkeypatch, seed):
    """FFT rounding as another machine might do it: every correlation off by up to 1e-12 of its
    largest value — more than numpy's FFTs differ by between machines (~1e-14), far under MATCH_TIE."""
    rng = np.random.default_rng(seed)
    correlate = auto_motion._correlate

    def noisy(region, kernel, rows, cols):
        out = correlate(region, kernel, rows, cols)
        return out + rng.uniform(-1e-12, 1e-12, out.shape)*max(1.0, float(np.abs(out).max()))
    monkeypatch.setattr(auto_motion, '_correlate', noisy)


def test_match_takes_the_tied_offset_nearest_the_centre_whatever_the_rounding(monkeypatch):
    """The flat torso slides: several offsets match it alike. Which one is taken is the prior's word
    (nearest the centre searched around), never the rounding's — on v2.36.0 the rounding picked."""
    a = auto_motion.motion_anchor._features(walker(0))
    moving = auto_motion.motion_anchor._features(walker(9))
    reference = auto_motion._reference(a, (44, 60, 72, 84))
    center = (-9, -1)
    expected = gathered_match(reference, moving, center, 12)
    best = min(expected.values())
    ties = [offset for offset, cost in expected.items() if cost <= best + 1e-5]
    assert len(ties) >= 3
    nearest = min(ties, key=lambda o: ((o[0]-center[0])**2+(o[1]-center[1])**2, -o[1], -o[0]))
    taken = set()
    for seed in range(8):
        _rounding(monkeypatch, seed)
        taken.add(tuple(auto_motion._match(reference, moving, center, 12)[0].tolist()))
        monkeypatch.undo()
    assert taken == {nearest}


def test_region_discovery_is_the_same_whatever_the_rounding(monkeypatch):
    """Regions that track the synthetic walker exactly tie on quality but for the rounding: the
    regions, their order and every tracked offset are the same under any of it."""
    frames = [walker(k) for k in range(40)]
    seen = set()
    for seed in range(4):
        _rounding(monkeypatch, seed)
        regions, report = auto_motion.discover(frames, reference_index=20)
        seen.add((tuple(map(tuple, regions)), tuple(map(tuple, report['tracking_xy']))))
        monkeypatch.undo()
    assert len(seen) == 1


def test_line_is_the_least_squares_line_and_the_same_in_any_order():
    """analyse's drift line: np.polyfit's, but written out with exactly rounded sums, so the order the
    points are summed in — which differs between machines' LAPACK — cannot reach its last bit."""
    rng = np.random.default_rng(3)
    t = np.arange(-12, 13)
    values = rng.normal(40, 9, len(t)).round(1)*0.7
    slope, intercept = auto_motion._line(t, values)
    np.testing.assert_allclose([slope, intercept], np.polyfit(t, values, 1), rtol=0, atol=1e-12)
    for seed in range(20):
        order = np.random.default_rng(seed).permutation(len(t))
        assert auto_motion._line(t[order], values[order]) == (slope, intercept)


def test_region_discovery_refuses_blank_input():
    with pytest.raises(ValueError, match='visible foreground'):
        auto_motion.discover([Image.new('RGBA', (100, 100)) for _ in range(6)], reference_index=0)


def test_actual_cli_auto_selects_corrects_and_verifies_animation(tmp_path):
    source = [walker(k) for k in range(73)]
    keyed = tmp_path/'keyed'
    keyed.mkdir()
    for k, image in enumerate(source):
        image.save(keyed/f'{k:03}.png')
    output = tmp_path/'out'
    args = ['--frames-dir', str(keyed), '--out-dir', str(output), '--state', 'walk', '--anchor', 'motion-auto', '--repair', 'off']
    assert loop.main(args) == 0
    report = json.loads((output/'loop.loop.report.json').read_text())
    start, length = report['cycle']['start'], report['cycle']['length']
    assert length == 24
    assert report['anchor'] == 'motion-auto' and report['seam_measurement'] == 'rendered-cells'
    assert report['automatic_motion_analysis']['analysis_only'] is True
    assert report['automatic_motion_regions']['reference_index'] == 0
    assert report['gif']['n_frames'] == report['webp']['n_frames'] == length
    motion = report['motion_anchor']
    assert abs(motion['endpoint_xy'][0]+23) <= 1
    assert abs(motion['endpoint_xy'][1]+5) <= 1
    left, top, _, _ = motion['padding_ltrb']
    for k, path in enumerate(sorted((output/'cycle').glob('*.png'))):
        image = Image.open(path)
        dx, dy = motion['shifts_xy'][k]
        restored = image.crop((left+dx, top+dy, left+dx+280, top+dy+180))
        np.testing.assert_array_equal(np.asarray(restored), np.asarray(source[start+k]))
    with pytest.raises(SystemExit, match='loop seam ratio'):
        loop.main(args+['--seam-max', '.001'])
    assert json.loads((output/'loop.loop.report.json').read_text())['status'] == 'failed'


def test_automatic_contract_refuses_manual_inputs_and_non_gaits_before_io(tmp_path):
    args = ['--frames-dir', '/missing', '--out-dir', str(tmp_path), '--anchor', 'motion-auto']
    for extra in [[], ['--state', 'idle'], ['--state', 'walk', '--cycle', 'fixed', '--start', '0', '--length', '24']]:
        with pytest.raises(SystemExit, match='requires walk/run'):
            loop.main(args+extra)
    with pytest.raises(SystemExit, match='requires --anchor motion'):
        loop.main(args+['--state', 'walk', '--anchor-region', '1,2,3,4'])
    with pytest.raises(SystemExit, match='requires walk/run'):
        batch.run_set(bases={}, states=['walk', 'idle'], root=tmp_path/'batch', character=None,
                      duration=3, resolution='720p', key='auto', concurrency=1,
                      force=False, gap=0, anchor='motion-auto')
    assert not (tmp_path/'batch').exists()
