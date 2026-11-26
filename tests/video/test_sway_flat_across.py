"""Two of the Sway layer's gaps: a region moved whole, and across apart from down.

A region's weight is 1 at its centre and falls to 0 at its rim, so a large region moves its middle and presses the rest
in. Its fifth number, flat, is the share of the way from its centre to its rim that moves in full — the Breathe layer's
chest weight, one formula — and the fold rule and the floor read it by the radius left. `--gain-across` moves across by
a gain of its own; a gain lowered for a region lowers both alike. Neither changes anything when left out. On the
synthetic figure of the inspection's tests, which sways once and bobs twice a cycle.
"""
import json
import math
import shutil

import numpy as np
import pytest
from sprite_gen import cli
from sprite_gen.effects import breathe
from sprite_gen.video import sway, sway_inspect

from tests.video.test_follow_inspect import CHEST, strip_dir

LOOP_FILES = ('walk.strip.png', 'walk.strip.json', 'walk.gif', 'walk.webp')


def files(directory):
    return {name: (directory/name).read_bytes() for name in LOOP_FILES}


def swayed(tmp_path, name, *args):
    directory = strip_dir(tmp_path/name)
    assert cli.main(['video-sway', '--loop-dir', str(directory), *args]) == 0
    return directory, json.loads((directory/'walk.strip.json').read_text())['follow']


def text(region):
    return ','.join(f'{v:g}' for v in region)


@pytest.mark.slow
def test_a_flat_share_of_0_is_the_region_of_four_numbers(tmp_path):
    four, four_record = swayed(tmp_path, 'four', '--region', text(CHEST))
    five, five_record = swayed(tmp_path, 'five', '--region', text(CHEST) + ',0')
    assert files(four) == files(five) and five_record['regions'] == [list(CHEST)]
    inspected = [sway_inspect.inspect_loop(strip_dir(tmp_path/f'loop-{name}'), [region], tmp_path/f'out-{name}')
                 for name, region in (('four', CHEST), ('five', (*CHEST, 0.0)))]
    assert inspected[0] == inspected[1]


def test_a_flat_region_moves_its_middle_whole():
    region = (60.0, 72.0, 24.0, 20.0, 0.6)
    yy, xx = np.mgrid[0:160, 0:120].astype(np.float32)
    inside, move_x, move_y = sway.move_field(xx, yy, [([region], (2.0, 3.0))], (1.0, -2.0))
    r = np.hypot((xx-61)/24, (yy-70)/20)
    assert np.all(move_x[r <= 0.59] == 2.0) and np.all(move_y[r <= 0.59] == 3.0)
    falling = (r > 0.61) & (r < 1)
    weight = breathe.flat_cos2(r.astype(np.float64), 0.6)
    assert np.allclose(move_y[falling], 3.0*weight[falling], atol=1e-5) and not inside[r >= 1.001].any()  # the rim, read in float32
    assert np.all(move_y[falling] < 3.0) and np.all(np.diff(move_y[70, 61:85]) <= 0)


def test_the_fold_and_the_floor_read_a_flat_region_by_the_radius_left_and_stay_under_the_numbers(tmp_path):
    flat = 0.5
    region = (*CHEST, flat)
    record = sway_inspect.inspect_loop(strip_dir(tmp_path/'loop'), [region], tmp_path/'out', gain=1.0, on_fold='lower')
    radius = min(CHEST[2:])*(1-flat)
    assert record['follow']['fold']['regions'][0]['radius_px'] == radius
    for cell in record['cells']:
        entry = cell['regions'][0]
        ox, oy = entry['offset_px']
        assert entry['ellipse'][4] == flat
        assert entry['bend'] == pytest.approx(math.hypot(ox, oy)*math.pi/(2*radius), abs=2e-4)
        closed = 1-(math.pi/2)/(1-flat)*math.hypot(ox/CHEST[2], oy/CHEST[3])  # the floor's least area for this move
        assert closed <= entry['jacobian_min'] + 1e-4, cell['index']
    floored = sway_inspect.inspect_loop(strip_dir(tmp_path/'loop2'), [region], tmp_path/'out2', gain=1.0, on_fold='lower',
                                        stretch_floor=0.0)
    least = floored['follow']['stretch']['regions'][0]['least_area']
    assert least <= min(cell['regions'][0]['jacobian_min'] for cell in floored['cells']) + 1e-4
    unflat = sway_inspect.inspect_loop(strip_dir(tmp_path/'loop3'), [CHEST], tmp_path/'out3', gain=1.0, on_fold='lower',
                                       stretch_floor=0.0)
    assert least < unflat['follow']['stretch']['regions'][0]['least_area']  # the same move, the steeper fall


def test_a_flat_share_out_of_the_breathe_layers_range_is_refused(tmp_path):
    directory = strip_dir(tmp_path/'loop')
    for bad in ('0.95', '-0.1', 'nan'):
        with pytest.raises(SystemExit):
            cli.main(['video-sway', '--loop-dir', str(directory), '--region', f'{text(CHEST)},{bad}'])
    with pytest.raises(SystemExit, match='flat is the share'):
        sway.sway_loop(directory, [(*CHEST, 0.95)])


def test_gain_across_left_out_or_equal_to_the_gain_moves_as_ever(tmp_path):
    ever, ever_record = swayed(tmp_path, 'ever', '--region', text(CHEST), '--gain', '2')
    equal, equal_record = swayed(tmp_path, 'equal', '--region', text(CHEST), '--gain', '2', '--gain-across', '2')
    assert files(ever)['walk.strip.png'] == files(equal)['walk.strip.png']
    assert 'gain_across' not in ever_record and (equal_record['gain_across'], equal_record['gain_across_requested']) == (2.0, 2.0)
    assert {k: v for k, v in equal_record.items() if not k.startswith('gain_across')} == ever_record


def test_gain_across_moves_across_by_its_own_gain(tmp_path):
    _, ever = swayed(tmp_path, 'ever', '--region', text(CHEST), '--gain', '2')
    _, apart = swayed(tmp_path, 'apart', '--region', text(CHEST), '--gain', '2', '--gain-across', '0.5')
    assert apart['dy_px'] == ever['dy_px'] and any(ever['dx_px'])
    assert np.allclose(apart['dx_px'], np.asarray(ever['dx_px'])/4, atol=0.007)
    with pytest.raises(SystemExit, match='--gain 0 leaves the cells as they were'):
        sway.sway_loop(strip_dir(tmp_path/'zero'), [CHEST], gain=0, gain_across=1)


def test_a_gain_lowered_for_a_region_lowers_across_in_proportion(tmp_path):
    small = (60.0, 72.0, 10.0, 10.0)
    _, record = swayed(tmp_path, 'lowered', '--region', text(small), '--gain', '40', '--gain-across', '20', '--on-fold', 'lower')
    assert record['fold']['lowered'] is True and record['gain'] < 40
    assert record['gain_across'] == round(record['gain']/2, 4) and record['gain_across_requested'] == 20
    unit = sway.read_ask([small], gain=1.0, gain_across=0.5)
    assert unit.across == 0.5
