"""`video-follow-inspect`: where a follow-through carries its regions in every cell, on a synthetic figure."""
import argparse
import hashlib
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen import cli
from sprite_gen.video import follow, follow_inspect

W, H, N = 120, 160, 24
HEAD, TORSO, PART, LEG = (210, 150, 60, 255), (20, 90, 180, 255), (230, 90, 120, 255), (240, 200, 40, 255)
CHEST = (60.0, 72.0, 18.0, 16.0)  # over the soft part, as `figure` draws it in cell 0
GROUND = (128, 128, 128, 255)
FILES = ('follow-inspect.json', 'follow-inspect.source.png', 'follow-inspect.moved.png')


def sway(k, n=N):
    """How far cell k's body is drawn from cell 0's, across and down, in whole pixels: once a cycle from side to
    side, twice up and down."""
    return round(3*math.sin(2*math.pi*k/n)), round(4*math.sin(4*math.pi*k/n))


def figure(k, n=N, arm=None, speck=None):
    """A body from the front: a head and a torso that sway as one, a soft part on the chest, thin legs to the
    ground. In the cells `arm` (first, last) a forearm of the torso's own colour lies across the chest, out past
    both sides. `speck` is a cell that has one more pixel in its corner."""
    sx, sy = sway(k, n)
    im = Image.new('RGBA', (W, H))
    d = ImageDraw.Draw(im)
    d.rectangle((45+sx, 20+sy, 75+sx, 45+sy), fill=HEAD)
    d.rectangle((35+sx, 46+sy, 85+sx, 110+sy), fill=TORSO)
    d.ellipse((46+sx, 60+sy, 74+sx, 84+sy), fill=PART)
    d.rectangle((44+sx, 111+sy, 50+sx, 150), fill=LEG)
    d.rectangle((70+sx, 111+sy, 76+sx, 150), fill=LEG)
    if arm and arm[0] <= k <= arm[1]:
        d.rectangle((25+sx, 68+sy, 95+sx, 74+sy), fill=TORSO)
    if k == speck:
        d.point((2, 2), fill=(255, 255, 255, 255))
    return im


def strip_dir(path, draw=figure, n=N, **meta):
    """A loop directory holding one cycle of `draw(k)` as a strip, as video-loop leaves one."""
    strip = Image.new('RGBA', (W*n, H))
    for k in range(n):
        strip.paste(draw(k), (k*W, 0))
    path.mkdir()
    strip.save(path/'walk.strip.png')
    (path/'walk.strip.json').write_text(json.dumps({'kind': 'periodic', 'frames': n, 'w': W, 'h': H, 'delay_ms': 1000/24} | meta))
    return path


def snapshot(path):
    """Everything in a directory: each name with its bytes and the time it was last written, and the directory's own."""
    inside = {str(p.relative_to(path)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns) for p in sorted(path.rglob('*'))}
    return inside | {'.': (None, path.stat().st_mtime_ns)}


def inspect(loop_dir, regions, out, **kw):
    """The record of an inspection, as written."""
    returned = follow_inspect.inspect_loop(loop_dir, regions, out, **kw)
    assert sorted(p.name for p in out.iterdir()) == sorted(FILES)
    record = json.loads((out/FILES[0]).read_text())
    assert record == returned
    return record


def cells(path):
    strip = np.asarray(Image.open(path).convert('RGBA'))
    return [np.ascontiguousarray(strip[:, k*W:(k+1)*W]) for k in range(strip.shape[1]//W)]


def picture(path):
    return np.asarray(Image.open(path).convert('RGB'))


def tiles(out, record, name):
    """A board's cells, each cut out at the box the record gives it."""
    board = picture(out/record['boards'][name]['file'])
    assert hashlib.sha256(board.tobytes()).hexdigest() == record['boards'][name]['pixels_sha256']
    assert hashlib.sha256((out/record['boards'][name]['file']).read_bytes()).hexdigest() == record['boards'][name]['sha256']
    assert [board.shape[1], board.shape[0]] == record['boards']['size']
    return [board[y0:y1, x0:x1] for x0, y0, x1, y1 in (cell['box'] for cell in record['cells'])]


def covered(ellipse):
    """The cell's pixels inside an ellipse cx, cy, rx, ry."""
    cx, cy, rx, ry = ellipse
    yy, xx = np.mgrid[0:H, 0:W]
    return np.sqrt(((xx-cx)/rx)**2+((yy-cy)/ry)**2) < 1


def grown(mask):
    out = mask.copy()
    out[1:] |= mask[:-1]
    out[:-1] |= mask[1:]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def enlarged(a, scale):
    return np.repeat(np.repeat(a, scale, axis=0), scale, axis=1)


def on_ground(cell):
    ground = Image.new('RGBA', (W, H), GROUND)
    ground.alpha_composite(Image.fromarray(cell))
    return np.asarray(ground.convert('RGB'))


def unit(tmp_path):
    """How far the figure's soft part moves at gain 1, px."""
    return inspect(strip_dir(tmp_path/'unit'), [CHEST], tmp_path/'unit-out')['follow']['fold']['reach_per_gain_px']


def folding_at(limit, reach_per_gain):
    """The radius of a region that folds at the gain `limit`."""
    return limit*reach_per_gain*math.pi/2


def test_the_loop_directory_is_read_and_not_written(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop')
    as_cut = snapshot(loop_dir)
    record = inspect(loop_dir, [CHEST], tmp_path/'out')
    # Not the strip, not its meta, and not the copy of the strip video-follow keeps beside the loop on its first run.
    assert snapshot(loop_dir) == as_cut and not (loop_dir/follow.SOURCE).exists()
    assert record['inputs']['strip']['file'] == 'walk.strip.png' and record['inputs']['meta']['follow_recorded'] is False
    follow.follow_loop(loop_dir, [CHEST])
    followed = snapshot(loop_dir)
    again = inspect(loop_dir, [CHEST], tmp_path/'again')
    assert snapshot(loop_dir) == followed
    assert again['inputs']['strip']['file'] == follow.SOURCE and again['inputs']['meta']['follow_recorded'] is True
    # Nothing of an inspection goes into the loop directory, or under it.
    for inside in (loop_dir, loop_dir/'inspect'/'deep'):
        with pytest.raises(SystemExit, match=r'^video-follow-inspect: --out-dir .* is in the loop directory; an inspection writes nothing there$'):
            follow_inspect.inspect_loop(loop_dir, [CHEST], inside)
    assert snapshot(loop_dir) == followed


def test_inspecting_first_changes_nothing_video_follow_writes(tmp_path):
    inspected, plain = strip_dir(tmp_path/'inspected'), strip_dir(tmp_path/'plain')
    inspect(inspected, [CHEST], tmp_path/'out', on_fold='lower')
    for loop_dir in (inspected, plain):
        follow.follow_loop(loop_dir, [CHEST], on_fold='lower')
    names = ['follow.source.png', 'walk.gif', 'walk.strip.json', 'walk.strip.png', 'walk.webp']
    assert sorted(p.name for p in inspected.iterdir()) == sorted(p.name for p in plain.iterdir()) == names
    for name in names:
        assert (inspected/name).read_bytes() == (plain/name).read_bytes(), name


def test_carry_is_the_bodys_own_motion_in_every_cell(tmp_path):
    record = inspect(strip_dir(tmp_path/'loop'), [CHEST], tmp_path/'out')
    assert [cell['index'] for cell in record['cells']] == list(range(N))
    assert record['inputs']['cut'] == {'frames': N, 'w': W, 'h': H, 'delay_ms': 1000/24}
    across, down = zip(*(sway(k) for k in range(N)))
    assert max(across) > 0 > min(across) and max(down) > 0 > min(down) and across != down
    for k, cell in enumerate(record['cells']):
        # Across and down, as drawn: the two are different motions, so one read as the other fails here.
        assert cell['carry_px'] == [across[k], down[k]], k
        (entry,) = cell['regions']
        assert entry['index'] == 0 and entry['ellipse'] == [CHEST[0]+across[k], CHEST[1]+down[k], CHEST[2], CHEST[3]], k
    assert record['follow']['body_bob_px'] == [max(across)-min(across), max(down)-min(down)]
    # The offsets are the strip's own, to more places than its record keeps.
    assert [cell['regions'][0]['offset_px'][0] for cell in record['cells']] == pytest.approx(record['follow']['dx_px'], abs=0.0051)
    assert [cell['regions'][0]['offset_px'][1] for cell in record['cells']] == pytest.approx(record['follow']['dy_px'], abs=0.0051)
    assert record['regions'][0]['reach_px'] == pytest.approx(record['follow']['reach_px'], abs=0.006)


def test_the_record_is_what_video_follow_then_writes(tmp_path):
    loop_dir, out = strip_dir(tmp_path/'loop'), tmp_path/'out'
    record = inspect(loop_dir, [CHEST], out, on_fold='lower')
    follow.follow_loop(loop_dir, [CHEST], on_fold='lower')
    written = json.loads((loop_dir/'walk.strip.json').read_text())['follow']
    assert record['follow'] == {key: value for key, value in written.items() if key not in ('gif', 'webp')}
    source, moved = cells(loop_dir/follow.SOURCE), cells(loop_dir/'walk.strip.png')
    changed = [(a != b).any(axis=-1) for a, b in zip(source, moved)]
    assert sum(int(c.sum()) for c in changed) == record['regions'][0]['changed_px'] > 0
    scale = record['boards']['scale']
    boards = [(tiles(out, record, 'source'), source), (tiles(out, record, 'moved'), moved)]
    for k, cell in enumerate(record['cells']):
        (entry,) = cell['regions']
        inside = covered(entry['ellipse'])
        # The carried ellipse the record gives holds every pixel video-follow changed in the cell.
        assert not (changed[k] & ~inside).any(), k
        assert cell['changed_px'] == entry['changed_px'] == int(changed[k].sum()), k
        assert entry['ellipse_px'] == int(inside.sum()) and entry['solid_px'] == int((inside & (source[k][..., 3] >= 128)).sum()), k
        for shown, strip in boards:
            # The cell at its box, over the ground, as cut on one board and as video-follow wrote it on the other;
            # the ring is on the board pixels just outside the ellipse, and inside it the cell shows whole.
            assert shown[k].shape[:2] == (H*scale, W*scale)
            ring = (shown[k] != enlarged(on_ground(strip[k]), scale)).any(axis=-1)
            within = enlarged(inside, scale)
            assert ring.any() and not (ring & ~(grown(within) & ~within)).any(), k
            assert {tuple(int(v) for v in colour) for colour in shown[k][ring]} <= {tuple(record['regions'][0]['ring']), (0, 0, 0)}, k


def test_each_region_has_its_gain_and_one_held_is_still_carried(tmp_path):
    reach = unit(tmp_path)
    crown = (66.0, 20.0, folding_at(1.755, reach), folding_at(1.755, reach))  # on the head's top edge; folds under the default gain
    ear = (45.0, 32.0, folding_at(0.755, reach), folding_at(0.755, reach))  # folds under gain 1: too small to move
    regions = [CHEST, crown, ear]
    loop_dir, out = strip_dir(tmp_path/'loop'), tmp_path/'out'
    record = inspect(loop_dir, regions, out, on_fold='lower')
    large, small, held = record['regions']
    assert [region['index'] for region in record['regions']] == [0, 1, 2]
    assert [region['ellipse'] for region in record['regions']] == [list(region) for region in regions]
    assert large['gain'] == follow.GAIN_DEFAULT > small['gain'] >= follow.GAIN_MEASURED and large['held'] is small['held'] is False
    assert 0.99 <= small['fold_ratio'] < 1 and 0 < small['jacobian_min'] < large['jacobian_min'] < 1
    assert held['held'] is True and (held['gain'], held['reach_px'], held['fold_ratio'], held['changed_px']) == (0, 0, 0, 0)
    assert held['gain_limit'] < follow.GAIN_MEASURED <= small['gain'] < small['gain_limit'] < follow.GAIN_DEFAULT < large['gain_limit']
    assert small['changed_px'] > 0 and large['changed_px'] > 0
    assert len({tuple(region['ring']) for region in record['regions']}) == 3
    source = tiles(out, record, 'source')
    for k, cell in enumerate(record['cells']):
        sx, sy = sway(k)
        assert [entry['index'] for entry in cell['regions']] == [0, 1, 2]
        for entry, (cx, cy, rx, ry) in zip(cell['regions'], regions):
            assert entry['ellipse'] == [cx+sx, cy+sy, rx, ry], k
        chest, top, still = cell['regions']
        # One motion, each region by its own gain; the held one by none, and ringed where it is carried all the same.
        assert top['offset_px'] == pytest.approx([v*small['gain']/large['gain'] for v in chest['offset_px']], abs=2e-4)
        assert still['offset_px'] == [0, 0] and still['changed_px'] == 0 and still['jacobian_min'] == 1
        assert cell['changed_px'] == chest['changed_px']+top['changed_px']
        assert cell['jacobian_min'] == min(chest['jacobian_min'], top['jacobian_min'])
        assert (source[k] == held['ring']).all(axis=-1).any(), k
    follow.follow_loop(loop_dir, regions, on_fold='lower')
    written = json.loads((loop_dir/'walk.strip.json').read_text())['follow']
    assert record['follow'] == {key: value for key, value in written.items() if key not in ('gif', 'webp')}
    assert [entry['held'] for entry in written['fold']['regions']] == [False, False, True]
    changed = [(a != b).any(axis=-1) for a, b in zip(cells(loop_dir/follow.SOURCE), cells(loop_dir/'walk.strip.png'))]
    assert [int(c.sum()) for c in changed] == [cell['changed_px'] for cell in record['cells']]


def test_regions_are_recorded_in_the_order_given_and_cells_in_the_strips(tmp_path):
    reach = unit(tmp_path)
    crown = (66.0, 20.0, folding_at(1.755, reach), folding_at(1.755, reach))
    loop_dir = strip_dir(tmp_path/'loop')
    one = inspect(loop_dir, [CHEST, crown], tmp_path/'one', on_fold='lower')
    other = inspect(loop_dir, [crown, CHEST], tmp_path/'other', on_fold='lower')
    assert one['input_id'] != other['input_id']
    without = lambda entry: {key: value for key, value in entry.items() if key not in ('index', 'ring')}
    # A region's numbers are its own wherever it is given; its index and its ring's colour are its place.
    assert [without(region) for region in one['regions']] == [without(region) for region in reversed(other['regions'])]
    assert [region['ring'] for region in one['regions']] == [region['ring'] for region in other['regions']]
    for a, b in zip(one['cells'], other['cells']):
        assert [without(entry) for entry in a['regions']] == [without(entry) for entry in reversed(b['regions'])]
        assert (a['index'], a['carry_px'], a['changed_px'], a['box']) == (b['index'], b['carry_px'], b['changed_px'], b['box'])
    # Each box holds its own cell of the strip and no other's: every cell is drawn differently here.
    strip = [on_ground(cell) for cell in cells(loop_dir/'walk.strip.png')]
    scale = one['boards']['scale']
    for k, shown in enumerate(tiles(tmp_path/'one', one, 'source')):
        match = [float((shown == enlarged(cell, scale)).all(axis=-1).mean()) for cell in strip]
        assert match[k] > 0.97 and all(m < match[k] for j, m in enumerate(match) if sway(j) != sway(k)), k
    boxes = [cell['box'] for cell in one['cells']]
    assert len({tuple(box) for box in boxes}) == N and boxes[0][0] < boxes[1][0] and boxes[0][1] == boxes[1][1] < boxes[one['boards']['columns']][1]


def test_what_was_read_names_the_evidence_and_another_cut_is_another(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop')
    first = inspect(loop_dir, [CHEST], tmp_path/'a')
    # No path, no time: the same loop asked the same way writes the same three files anywhere.
    inspect(loop_dir, [CHEST], tmp_path/'b')
    for name in FILES:
        assert (tmp_path/'a'/name).read_bytes() == (tmp_path/'b'/name).read_bytes(), name
    assert str(tmp_path) not in (tmp_path/'a'/FILES[0]).read_text()
    assert first['inputs']['strip']['sha256'] == hashlib.sha256((loop_dir/'walk.strip.png').read_bytes()).hexdigest()
    assert first['inputs']['meta']['sha256'] == hashlib.sha256((loop_dir/'walk.strip.json').read_bytes()).hexdigest()
    # video-follow moves the strip and records itself in the meta; what it was read from is the same cut.
    follow.follow_loop(loop_dir, [CHEST])
    followed = inspect(loop_dir, [CHEST], tmp_path/'c')
    assert followed['input_id'] == first['input_id']
    assert followed['inputs']['strip']['sha256'] == first['inputs']['strip']['sha256']
    assert followed['inputs']['meta']['sha256'] != first['inputs']['meta']['sha256']
    assert followed['inputs']['meta']['cut_sha256'] == first['inputs']['meta']['cut_sha256']
    assert (followed['cells'], followed['regions'], followed['follow'], followed['boards']) == (first['cells'], first['regions'], first['follow'], first['boards'])
    # Another cut, another id: the cycle turned to start one cell on (an alignment); one pixel of one cell;
    # the cut's own record with another delay, or with any other key changed.
    turned = inspect(strip_dir(tmp_path/'turned', lambda k: figure((k+1) % N)), [CHEST], tmp_path/'d')
    specked = inspect(strip_dir(tmp_path/'specked', lambda k: figure(k, speck=5)), [CHEST], tmp_path/'e')
    slower = inspect(strip_dir(tmp_path/'slower', delay_ms=50), [CHEST], tmp_path/'f')
    noted = inspect(strip_dir(tmp_path/'noted', cycle_frames=N), [CHEST], tmp_path/'g')
    assert len({r['input_id'] for r in (first, turned, specked, slower, noted)}) == 5
    assert len({r['inputs']['strip']['sha256'] for r in (first, turned, specked)}) == 3
    # One pixel in a corner moves no body: the same carry and offsets, and still not the cut the evidence was read on.
    assert [cell['carry_px'] for cell in specked['cells']] == [cell['carry_px'] for cell in first['cells']]
    assert specked['follow'] == first['follow'] and specked['boards']['source']['pixels_sha256'] != first['boards']['source']['pixels_sha256']
    for meta_only in (slower, noted):
        assert meta_only['inputs']['strip']['sha256'] == first['inputs']['strip']['sha256']
        assert meta_only['inputs']['meta']['cut_sha256'] != first['inputs']['meta']['cut_sha256']
    assert noted['cells'] == first['cells'] and slower['follow']['dy_px'] != first['follow']['dy_px']


def test_what_the_answer_is_asked_with_is_in_the_id_and_how_the_boards_are_drawn_is_not(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop')
    base = inspect(loop_dir, [CHEST], tmp_path/'base')
    asked = [dict(gain=2.0), dict(freq=2.5), dict(zeta=0.5), dict(on_fold='lower')]
    regions = [(61.0, 72.0, 18.0, 16.0), (60.0, 71.0, 18.0, 16.0), (60.0, 72.0, 17.0, 16.0), (60.0, 72.0, 18.0, 15.0), CHEST+CHEST]
    others = [inspect(loop_dir, [CHEST], tmp_path/f'asked-{i}', **kw) for i, kw in enumerate(asked)]
    others += [inspect(loop_dir, [r[:4], r[4:]] if len(r) == 8 else [r], tmp_path/f'region-{i}') for i, r in enumerate(regions)]
    assert len({record['input_id'] for record in (base, *others)}) == 1+len(asked)+len(regions)
    # Asked to lower where nothing folds: every number and both boards as before, under another id.
    lower = others[3]
    assert (lower['cells'], lower['regions'], lower['boards']) == (base['cells'], base['regions'], base['boards'])
    assert lower['inputs']['settings'] == base['inputs']['settings'] | {'on_fold': 'lower'}
    assert base['inputs']['settings'] == {'gain': 2.5, 'on_fold': 'refuse', 'freq_hz': 2.4, 'zeta': 0.6, 'read_on': None, 'stretch_floor': None}
    assert base['inputs']['policy'] == {'name': follow.POLICY, 'harmonics': 6, 'alpha_solid': 128, 'worn': [0.25, 0.5],
                                        'gain_measured': 1.0, 'gain_step': 0.01, 'weight': 'cos2',
                                        'transport': follow.TRANSPORT_POLICY, 'stretch_floor': follow.STRETCH_POLICY}
    # Numbers from a caller are read as the command line's.
    for kind in (np.int64, np.float32, int):
        given = follow_inspect.inspect_loop(loop_dir, np.asarray([CHEST], dtype=kind), tmp_path/f'kind-{kind.__name__}', gain=kind(2.5) if kind is np.float32 else 2.5)
        assert given == base, kind
    # Drawn larger and five to a row: other boards and boxes, the same id and the same numbers.
    drawn = inspect(loop_dir, [CHEST], tmp_path/'drawn', scale=3, columns=5)
    unboxed = lambda record: [{key: value for key, value in cell.items() if key != 'box'} for cell in record['cells']]
    assert (drawn['input_id'], drawn['inputs'], drawn['follow'], drawn['regions'], unboxed(drawn)) == (
        base['input_id'], base['inputs'], base['follow'], base['regions'], unboxed(base))
    assert (drawn['boards']['scale'], drawn['boards']['columns']) == (3, 5) != (base['boards']['scale'], base['boards']['columns'])
    assert drawn['boards']['source']['pixels_sha256'] != base['boards']['source']['pixels_sha256']
    assert all(shown.shape[:2] == (H*3, W*3) for shown in tiles(tmp_path/'drawn', drawn, 'source'))
    assert drawn['cells'][5]['box'][1] > drawn['cells'][4]['box'][1] == drawn['cells'][0]['box'][1]
    for bad in (dict(scale=0), dict(columns=0)):
        with pytest.raises(SystemExit, match=r'^video-follow-inspect: --scale and --columns must be 1 or more$'):
            follow_inspect.inspect_loop(loop_dir, [CHEST], tmp_path/'bad', **bad)
    assert not (tmp_path/'bad').exists()


def test_what_video_follow_refuses_the_inspection_refuses_and_writes_nothing(tmp_path):
    loop_dir, out = strip_dir(tmp_path/'loop'), tmp_path/'out'
    as_cut = snapshot(loop_dir)
    reach = unit(tmp_path)
    tiny = (60.0, 72.0, folding_at(0.755, reach), folding_at(0.755, reach))
    refusals = [
        (dict(regions=[(60.0, 72.0, 2.0, 2.0)], gain=50), r'a move of \d+\.\d px folds a region of radius 2 px over itself; lower --gain or give the region larger radii'),
        (dict(regions=[tiny], on_fold='lower'), r'a move of \d+\.\d px folds a region of radius [\d.]+ px over itself, and --on-fold lower would have to go under --gain 1 .*'),
        (dict(regions=[(999.0, 72.0, 5.0, 5.0)]), r'--region centre 999,72 is outside the 120x160 cell'),
        (dict(regions=[]), r'at least one --region cx,cy,rx,ry'),
        (dict(regions=[CHEST], on_fold='quietly'), r"unknown --on-fold 'quietly'; expected one of refuse, lower"),
    ]
    for i, (kw, why) in enumerate(refusals):
        regions = kw.pop('regions')
        with pytest.raises(SystemExit, match=rf'^video-follow-inspect: {why}$') as refused:
            follow_inspect.inspect_loop(loop_dir, regions, out, **kw)
        assert not out.exists() and snapshot(loop_dir) == as_cut
        # video-follow refuses the same request in the same words, under its own name.
        with pytest.raises(SystemExit) as followed:
            follow.follow_loop(strip_dir(tmp_path/f'follow-{i}'), regions, **kw)
        assert str(followed.value) == str(refused.value).replace('video-follow-inspect:', 'video-follow:', 1)
    with pytest.raises(SystemExit, match=r"^video-follow-inspect: a one-shot plays once; the follow-through is a loop's steady state$"):
        follow_inspect.inspect_loop(strip_dir(tmp_path/'once', kind='one-shot'), [CHEST], out)
    with pytest.raises(SystemExit, match=r'^video-follow-inspect: walk\.strip\.png is 2880x160, the strip meta says 2760x160; cut the loop again \(video-loop\)$'):
        follow_inspect.inspect_loop(strip_dir(tmp_path/'short', frames=N-1), [CHEST], out)
    with pytest.raises(SystemExit, match=r'^video-follow-inspect: .*: expected one <name>\.strip\.json from video-loop, found 0$'):
        follow_inspect.inspect_loop(tmp_path, [CHEST], out)
    assert not out.exists()


def test_the_least_area_says_how_near_the_picture_is_to_stopping(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop')
    record = inspect(loop_dir, [CHEST], tmp_path/'out')
    assert record['follow']['fold']['lowered'] is False and 0 < record['regions'][0]['jacobian_min'] < 1
    moving = 0
    for cell in record['cells']:
        (entry,) = cell['regions']
        dx, dy = entry['offset_px']
        # On an ellipse the weight falls fastest half way out, along the move: there a pixel of the moved cell
        # takes this much of the source, and no less anywhere.
        steepest = 1-math.pi/2*math.hypot(dx/CHEST[2], dy/CHEST[3])
        assert entry['jacobian_min'] == pytest.approx(steepest, abs=0.05) and cell['jacobian_min'] == entry['jacobian_min']
        assert entry['jacobian_min'] >= 1-entry['fold_ratio']-1e-4 and entry['fold_ratio'] == pytest.approx(entry['reach_px']*math.pi/(2*16), abs=1e-4)
        if entry['reach_px'] >= 1:
            moving += 1
            x, y = entry['jacobian_min_at']
            cx, cy, rx, ry = entry['ellipse']
            assert 0.3 < math.hypot((x-cx)/rx, (y-cy)/ry) < 0.7, cell['index']
    assert moving > N/2
    assert record['regions'][0]['jacobian_min'] == min(cell['jacobian_min'] for cell in record['cells'])
    assert record['regions'][0]['fold_ratio'] == max(cell['regions'][0]['fold_ratio'] for cell in record['cells'])
    # Lowered to the largest gain that does not fold it, a region is drawn nearly stopped where it moves most:
    # not folded, and stretched.
    lowered = inspect(loop_dir, [CHEST], tmp_path/'lowered', gain=50, on_fold='lower')
    region = lowered['regions'][0]
    assert lowered['follow']['fold']['lowered'] is True and follow.GAIN_MEASURED <= region['gain'] < 50
    assert 0.99 <= region['fold_ratio'] < 1 and 0 < region['jacobian_min'] < 0.15
    # At gain 0 nothing moves and nothing is stretched.
    still = inspect(loop_dir, [CHEST], tmp_path/'still', gain=0)
    assert {(cell['changed_px'], cell['jacobian_min'], *cell['regions'][0]['offset_px']) for cell in still['cells']} == {(0, 1, 0, 0)}
    assert still['boards']['source']['pixels_sha256'] == still['boards']['moved']['pixels_sha256'] == record['boards']['source']['pixels_sha256']
    assert record['boards']['moved']['pixels_sha256'] != record['boards']['source']['pixels_sha256']


def test_a_part_that_crosses_an_ellipse_is_shown_and_not_judged(tmp_path):
    crossing = range(9, 14)
    bare = inspect(strip_dir(tmp_path/'bare'), [CHEST], tmp_path/'bare-out')
    crossed = inspect(strip_dir(tmp_path/'crossed', lambda k: figure(k, arm=(crossing[0], crossing[-1]))), [CHEST], tmp_path/'crossed-out')
    # A forearm of the torso's own colour, thinner than the body: the motion read off the cells is the bare figure's,
    # so the ellipse is carried over the forearm as it is over the chest.
    assert [cell['carry_px'] for cell in crossed['cells']] == [cell['carry_px'] for cell in bare['cells']]
    assert crossed['follow'] == bare['follow'] and crossed['input_id'] != bare['input_id']
    # The engine says of no cell what is inside its ellipse: the cells the forearm crosses are written as every other.
    entries = [entry for cell in crossed['cells'] for entry in cell['regions']]
    assert {entry['ownership'] for entry in entries} == {'unknown'} and len({tuple(sorted(entry)) for entry in entries}) == 1
    assert 'ownership' not in crossed['follow'] and all('ownership' not in cell for cell in crossed['cells'])
    scale = crossed['boards']['scale']
    seen = [tiles(tmp_path/name, record, 'source') for name, record in (('bare-out', bare), ('crossed-out', crossed))]
    moved = tiles(tmp_path/'crossed-out', crossed, 'moved')
    taken = 0
    for k, cell in enumerate(crossed['cells']):
        differs = (seen[0][k] != seen[1][k]).any(axis=-1)
        within = enlarged(covered(cell['regions'][0]['ellipse']), scale)
        if k in crossing:
            # It is on the board of the cells as cut, inside the ring, for whoever reads that board;
            forearm = differs & within
            assert forearm.any(), k
            # and on the other board it has moved with the chest.
            taken += int(((seen[1][k] != moved[k]).any(axis=-1) & forearm).sum())
        else:
            assert not differs.any(), k
    assert taken > 0


def test_the_command_takes_what_video_follow_takes(tmp_path, capsys):
    description, add_arguments, run = cli.COMMANDS['video-follow-inspect']
    assert run is follow_inspect.run and 'nothing moved' in description
    loop_dir, out = strip_dir(tmp_path/'loop'), tmp_path/'out'
    asked = ['--loop-dir', str(loop_dir), '--region', '60,72,18,16', '--region', '66,20,9,9', '--gain', '50', '--on-fold', 'lower',
             '--freq', '2.5', '--zeta', '0.5']
    parsers = [argparse.ArgumentParser(), argparse.ArgumentParser()]
    cli.COMMANDS['video-follow'][1](parsers[0])
    add_arguments(parsers[1])
    followed, inspected = vars(parsers[0].parse_args(asked)), vars(parsers[1].parse_args([*asked, '--out-dir', str(out)]))
    assert set(inspected)-set(followed) == {'out_dir', 'scale', 'columns'} and set(followed)-set(inspected) == {'board'}
    assert {key: inspected[key] for key in followed if key != 'board'} == {key: value for key, value in followed.items() if key != 'board'}
    capsys.readouterr()
    assert cli.main(['video-follow-inspect', *asked, '--out-dir', str(out), '--columns', '6']) == 0
    printed, said = capsys.readouterr()
    printed, record = json.loads(printed), json.loads((out/FILES[0]).read_text())
    assert (out/FILES[0]).samefile(printed['record']) and printed['input_id'] == record['input_id'] and printed['cells'] == N
    assert printed['ownership'] == 'unknown' and printed['held'] == [] and printed['region_gains'] == [region['gain'] for region in record['regions']]
    assert [(out/record['boards'][name]['file']).samefile(printed['boards'][name]['path']) for name in ('source', 'moved')] == [True, True]
    assert record['boards']['columns'] == 6 and record['inputs']['settings'] == {'gain': 50.0, 'on_fold': 'lower', 'freq_hz': 2.5, 'zeta': 0.5,
                                                                                'read_on': None, 'stretch_floor': None}
    lines = said.strip().splitlines()
    assert lines and all(line.startswith('video-follow-inspect: --gain 50 folds ') and 'lowered to --gain ' in line for line in lines)
    assert follow_inspect.main([*asked, '--out-dir', str(tmp_path/'direct')]) == 0
    assert json.loads((tmp_path/'direct'/FILES[0]).read_text())['input_id'] == record['input_id']
