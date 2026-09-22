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
    args = ['--frames-dir', str(keyed), '--out-dir', str(output), '--state', 'walk', '--anchor', 'motion-auto']
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
