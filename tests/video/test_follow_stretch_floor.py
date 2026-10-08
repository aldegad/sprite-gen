"""`video-follow --stretch-floor`: a least area each region's move must leave, by the way the region moves, never a
gain above the fold rule's; and what `video-follow-inspect` (schema 2) says a move does to the picture besides
stretching it — the bend of a line, and where two regions meet, the seam. On a synthetic chest that bobs or sways."""
import json
import math
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen.video import follow, follow_inspect

W, H, N, DELAY = 160, 300, 13, 41.67
SKIN, TORSO, LEG, PATCH, LINE = (200, 150, 120, 255), (90, 120, 180, 255), (190, 170, 120, 255), (120, 160, 220, 255), (10, 10, 20, 255)
CX, CY = 80, 112
FLOORS = (0.03, 0.1, 0.2, 0.35)


def figure(dx, dy, *, lines):
    """A runner from the front at (dx, dy): legs, a torso, a head, and on the chest a patch with 1 px dark lines
    every third pixel, across (`h`) or up and down (`v`)."""
    im = Image.new('RGBA', (W, H))
    d = ImageDraw.Draw(im)
    d.rectangle((55+dx, 188+dy, 75+dx, 285+dy), fill=LEG)
    d.rectangle((85+dx, 188+dy, 105+dx, 285+dy), fill=LEG)
    d.rounded_rectangle((45+dx, 70+dy, 115+dx, 190+dy), radius=18, fill=TORSO)
    d.ellipse((60+dx, 25+dy, 100+dx, 65+dy), fill=SKIN)
    d.rectangle((72+dx, 60+dy, 88+dx, 75+dy), fill=SKIN)
    px, py = CX+dx, CY+dy
    d.ellipse((px-18, py-18, px+18, py+18), fill=PATCH)
    for v in range(-15, 16, 3):
        d.line((px-15, py+v, px+15, py+v) if lines == 'h' else (px+v, py-15, px+v, py+15), fill=LINE)
    return im


def step(k):
    """Twice a cycle, as a run's steps are: 6 px each way."""
    return round(6*math.cos(4*math.pi*k/N))


# X1: the chest bobs up and down under a round region. X2: it sways across under a region twice as wide as tall,
# along its long axis — where the fold rule, which reads the smaller radius whatever way the region moves, is the
# stricter of the two.
X1 = ([figure(0, step(k), lines='h') for k in range(N)], (CX, CY+step(0), 16.0, 16.0))
X2 = ([figure(step(k), 0, lines='v') for k in range(N)], (CX+step(0), CY, 24.0, 12.0))


def strip_dir(path, frames):
    strip = Image.new('RGBA', (W*len(frames), H))
    for k, frame in enumerate(frames):
        strip.paste(frame, (k*W, 0))
    path.mkdir()
    strip.save(path/'run.strip.png')
    (path/'run.strip.json').write_text(json.dumps({'kind': 'periodic', 'frames': len(frames), 'w': W, 'h': H, 'delay_ms': DELAY}))
    return path


@pytest.fixture
def probes(tmp_path):
    return {'X1': strip_dir(tmp_path/'x1', X1[0]), 'X2': strip_dir(tmp_path/'x2', X2[0])}


def followed(src, path, regions, **kw):
    loop_dir = shutil.copytree(src, path)
    follow.follow_loop(loop_dir, regions, on_fold='lower', **kw)
    return loop_dir, json.loads((loop_dir/'run.strip.json').read_text())['follow']


def drawn(loop_dir):
    return {name: (loop_dir/name).read_bytes() for name in ('run.strip.png', 'run.gif', 'run.webp')}


# ---------------------------------------------------------------- F1: the floor is kept in every cell

@pytest.mark.parametrize('floor', FLOORS)
def test_f1_every_cell_of_every_region_keeps_the_floor(probes, tmp_path, floor):
    for name, (_, region) in (('X1', X1), ('X2', X2)):
        _, rec = followed(probes[name], tmp_path/f'{name}', [region], stretch_floor=floor)
        stretch = rec['stretch']
        assert stretch['policy'] == follow.STRETCH_POLICY and stretch['floor'] == floor
        entry = stretch['regions'][0]
        assert not entry['held'] and entry['least_area'] >= floor and entry['gain'] == min(entry['rule_gain'], entry['floor_gain'])
        record = follow_inspect.inspect_loop(probes[name], [region], tmp_path/f'{name}-out', on_fold='lower', stretch_floor=floor)
        assert record['follow']['stretch'] == stretch
        assert all(cell['regions'][0]['jacobian_min'] >= floor-1e-4 for cell in record['cells']), name
        assert record['inputs']['settings']['stretch_floor'] == floor


def test_f1_a_floor_no_gain_of_one_or_more_keeps_holds_the_region_and_one_held_alone_is_refused(probes, tmp_path):
    _, region = X1
    loop_dir = shutil.copytree(probes['X1'], tmp_path/'x1')
    before = drawn(loop_dir)
    with pytest.raises(SystemExit, match=r'--stretch-floor 0\.5.*held'):
        follow.follow_loop(loop_dir, [region], on_fold='lower', stretch_floor=0.5)
    assert drawn(loop_dir) == before
    # Beside a larger region that keeps it at 1 or more, the small one is held and named, and the large one moves.
    cx, cy, _, _ = region
    _, rec = followed(probes['X1'], tmp_path/'two', [(cx, cy, 30.0, 30.0), region], stretch_floor=0.5)
    entries = rec['stretch']['regions']
    assert not entries[0]['held'] and entries[0]['gain'] >= follow.GAIN_MEASURED
    assert entries[1]['held'] and entries[1]['gain'] == 0.0 and entries[1]['least_area'] == 1.0
    assert rec['fold']['regions'][1]['held'] is True


def test_f1_under_refuse_a_move_under_the_floor_is_refused(probes, tmp_path):
    _, region = X2
    with pytest.raises(SystemExit, match='--stretch-floor'):
        follow.follow_loop(shutil.copytree(probes['X2'], tmp_path/'x2'), [region], gain=1.0, stretch_floor=0.9)


# ---------------------------------------------------------------- F2: never above the fold rule's gain

def test_f2_where_the_fold_rule_is_stricter_its_gain_stands(probes, tmp_path):
    _, region = X2
    _, legacy = followed(probes['X2'], tmp_path/'legacy', [region])
    _, rec = followed(probes['X2'], tmp_path/'floor', [region], stretch_floor=0.2)
    entry = rec['stretch']['regions'][0]
    assert entry['floor_gain'] > entry['rule_gain'] == legacy['gain'] == rec['gain']


def test_f2_where_the_floor_is_stricter_its_gain_stands(probes, tmp_path):
    _, region = X1
    _, legacy = followed(probes['X1'], tmp_path/'legacy', [region])
    _, rec = followed(probes['X1'], tmp_path/'floor', [region], stretch_floor=0.03)
    entry = rec['stretch']['regions'][0]
    assert entry['rule_gain'] == legacy['gain'] and rec['gain'] == entry['floor_gain'] < legacy['gain']
    assert rec['fold']['lowered'] is True


# ---------------------------------------------------------------- F3: a floor the move already keeps changes no pixel

def test_f3_a_floor_under_the_least_area_already_kept_draws_the_same_strip(probes, tmp_path):
    _, region = X2
    legacy_dir, legacy = followed(probes['X2'], tmp_path/'legacy', [region])
    _, zero = followed(probes['X2'], tmp_path/'zero', [region], stretch_floor=0.0)
    kept = zero['stretch']['regions'][0]['least_area']
    assert 0.1 < kept < 1
    under_dir, under = followed(probes['X2'], tmp_path/'under', [region], stretch_floor=round(kept-0.003, 4))
    assert drawn(under_dir) == drawn(legacy_dir)
    assert {k: v for k, v in under.items() if k not in ('stretch', 'carry_basis')} == legacy
    _, over = followed(probes['X2'], tmp_path/'over', [region], stretch_floor=round(kept+0.03, 4))
    assert over['gain'] < legacy['gain']


# ---------------------------------------------------------------- F4: the floor alone, by direction, is not shipped

def test_f4_a_floor_of_nothing_is_the_fold_rule_not_the_direction_alone(probes, tmp_path):
    # Read by direction alone, X2's region may move twice as far before its picture stops; that stretches a
    # pixel to a twentieth of its area where the fold rule leaves it half. The floor never goes past the rule.
    _, region = X2
    legacy_dir, legacy = followed(probes['X2'], tmp_path/'legacy', [region])
    zero_dir, zero = followed(probes['X2'], tmp_path/'zero', [region], stretch_floor=0.0)
    entry = zero['stretch']['regions'][0]
    assert entry['floor_gain'] > 1.5*legacy['gain'] and zero['gain'] == legacy['gain']
    assert drawn(zero_dir) == drawn(legacy_dir)
    as_legacy = follow_inspect.inspect_loop(probes['X2'], [region], tmp_path/'legacy-out', on_fold='lower')
    as_zero = follow_inspect.inspect_loop(probes['X2'], [region], tmp_path/'zero-out', on_fold='lower', stretch_floor=0.0)
    assert as_zero['regions'][0]['jacobian_min'] == as_legacy['regions'][0]['jacobian_min'] > 0.4


def test_the_floor_is_read_between_0_and_1(probes, tmp_path):
    _, region = X1
    for bad in (-0.1, 1.0, 1.5, float('nan')):
        with pytest.raises(SystemExit, match='--stretch-floor'):
            follow.follow_loop(shutil.copytree(probes['X1'], tmp_path/f'x1-{bad}'), [region], stretch_floor=bad)


# ---------------------------------------------------------------- Q1: the bend and the seam are written down

def test_q1_the_bend_is_the_moves_steepest_tilt_and_a_lowered_region_moves_just_under_45_degrees(probes, tmp_path):
    _, region = X1
    record = follow_inspect.inspect_loop(probes['X1'], [region], tmp_path/'out', on_fold='lower')
    assert record['schema_version'] == 2
    radius = min(region[2:])
    for cell in record['cells']:
        entry = cell['regions'][0]
        bend = math.hypot(*entry['offset_px'])*math.pi/(2*radius)
        assert entry['bend'] == pytest.approx(bend, abs=2e-4) and entry['bend'] == entry['fold_ratio']
        assert entry['bend_deg'] == pytest.approx(math.degrees(math.atan(entry['bend'])), abs=0.01)
    top = record['regions'][0]
    assert top['bend'] == max(cell['regions'][0]['bend'] for cell in record['cells'])
    # --on-fold lower takes the largest gain that does not fold: the move then tilts a line by just under 45°.
    assert 44 < top['bend_deg'] < 45
    assert record['seams'] == [] and all(cell['seams'] == [] for cell in record['cells'])


def weight_and_gradient(x, y, ellipse):
    cx, cy, rx, ry = ellipse
    u, v = (x-cx)/rx, (y-cy)/ry
    r = math.hypot(u, v)
    if r >= 1:
        return 0.0, 0.0, 0.0
    k = 0.0 if r == 0 else -(math.pi/2)*math.sin(math.pi*r)/r
    return math.cos(r*math.pi/2)**2, k*u/rx, k*v/ry


def test_q1_where_two_regions_meet_the_seam_is_where_the_larger_move_changes_hands(probes, tmp_path):
    _, (cx, cy, _, _) = X1
    regions = [(cx-8, cy, 14.0, 14.0), (cx+8, cy, 14.0, 14.0)]
    record = follow_inspect.inspect_loop(probes['X1'], regions, tmp_path/'out', gain=1.0, on_fold='lower')
    seams = [(cell['index'], seam) for cell in record['cells'] for seam in cell['seams']]
    assert seams and all(seam['regions'] == [0, 1] for _, seam in seams)
    for k, seam in seams:
        cell = record['cells'][k]
        x, y = seam['at']
        carried = [cell['regions'][i]['ellipse'] for i in (0, 1)]
        (wa, ax, ay), (wb, bx, by) = (weight_and_gradient(x, y, e) for e in carried)
        assert wa > 0 and wb > 0 and wa == pytest.approx(wb, abs=1e-3)  # one gain: the larger weight wins
        oa, ob = (cell['regions'][i]['offset_px'] for i in (0, 1))
        jump = math.sqrt((oa[0]*ax-ob[0]*bx)**2+(oa[0]*ay-ob[0]*by)**2+(oa[1]*ax-ob[1]*bx)**2+(oa[1]*ay-ob[1]*by)**2)
        assert seam['jump'] == pytest.approx(jump, abs=0.01)
        assert seam['deg'] == pytest.approx(math.degrees(math.atan(seam['jump'])), abs=0.01)
    largest = max(seams, key=lambda s: s[1]['jump'])
    assert record['seams'] == [{'regions': [0, 1], 'jump': largest[1]['jump'], 'deg': largest[1]['deg'], 'cell': largest[0],
                                'at': largest[1]['at']}]
    assert record['seams'][0]['deg'] > 5


def test_q1_regions_apart_or_held_have_no_seam(probes, tmp_path):
    _, (cx, cy, _, _) = X1
    apart = follow_inspect.inspect_loop(probes['X1'], [(cx-25, cy, 12.0, 12.0), (cx+25, cy, 12.0, 12.0)], tmp_path/'apart', gain=1.0)
    assert apart['seams'] == []
    held = follow_inspect.inspect_loop(probes['X1'], [(cx, cy, 16.0, 16.0), (cx, cy, 3.0, 3.0)], tmp_path/'held', on_fold='lower')
    assert [r['held'] for r in held['regions']] == [False, True] and held['seams'] == []


def test_the_inspection_names_the_floor_and_the_read_on_in_its_id(probes, tmp_path):
    _, region = X1
    ids = {follow_inspect.inspect_loop(probes['X1'], [region], tmp_path/f'out-{floor}', on_fold='lower', stretch_floor=floor)['input_id']
           for floor in (None, 0.1, 0.2)}
    assert len(ids) == 3
