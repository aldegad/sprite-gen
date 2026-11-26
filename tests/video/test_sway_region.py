"""`video-sway --region-mask`: the region given as a mask, every visible pixel outside it held.

A mask drawn on the cell the regions were read on is the region instead of the ellipse's weight. It is carried into every
cell by its own pixels — round where the cell before had them first, round the lay second, and where neither settles it
keeps the cell before's carry and says so — and there every visible pixel outside it and its soft edge keeps its bytes,
at any gain, while it moves into the air beside it. Its weight falls to 0 toward the held pixels over a rim read off the
move (sin² of the distance in an ellipse Lx across and Ly down, widened until the move takes at most RIM_SLOPE of a
pixel's area), so the picture does not fold and no read leans half on a held pixel; `--band-up` yields above. A mask
drawn a little large is worn in (`--region-shrink`), and its lower edge may be moved to the crease under it
(`--region-crease`) before it is worn in. Synthetic figures only: a body whose striped part, round above and cut straight
below, lies over its torso's right side and past it, over the air, soft against the air, a dark crease under it on the
torso; the band test's part on cloth with no pattern; the read-on test's runner, cut and turned.

Each case first reads what a sway writes with the mask (its cells, whether it refuses, what it says), so on a release
without the mask the same input fails on what comes out; the record and the ledger are read after.
"""
import hashlib
import io
import json
import math
import shutil

import numpy as np
import pytest
from PIL import Image
from sprite_gen import cli
from sprite_gen.video import sway

from tests.video.test_follow_inspect import CHEST, HEAD, LEG, N, TORSO, W, H, covered, strip_dir, sway as body_sway
from tests.video.test_follow_read_on import aligned, cells_of as cut_cells, chest as cut_chest, meta_of, origin, origin_once  # noqa: F401 (fixtures)
from tests.video.test_sway_band import ramp, slide, sliding, swayed, whole_move

POLICY = 'own-pixels-mask/held-outside/rim-sin2/1'
PART = (71.0, 76.0, 21.0, 22.0)  # the part in cell 0: round above its centre, straight down its sides below
BOTTOM = 92  # its last row in cell 0; under it on the torso (x 35 to 85), a dark crease
STRIPES = [(230, 90, 120), (250, 170, 90), (200, 60, 150)]
CREASE = (25, 15, 40)
SOFT = 90  # the alpha of the soft edges against the air: the part's own, and the torso's right side
GAINS = (2.5, 1.25, 6.0)  # the default, its half, and more than twice it
SEARCH = 9
FILES = [f'frame-{k:03d}.png' for k in range(N)]


def text(region):
    return ','.join(f'{v:g}' for v in region)


def still(k):
    return 0, 0


def out_of_part(xx, yy):
    """How far out of the part a place is, as the part lies in cell 0: in its ellipse's radii above the centre, in its
    width below it; 1 on its edge."""
    dx, dy = (xx-PART[0])/PART[2], (yy-PART[1])/PART[3]
    return np.where(dy < 0, np.hypot(dx, dy), np.abs(dx))


def part_figure(k, n=N, slide=still, lift=still):
    """A body: a head and a torso that sway as one (and `lift(k)` further), legs to the ground, the torso's right side soft
    against the air. On it, a further `slide(k)` whole pixels from where the torso carries it, a part striped across both
    ways, over the torso's right side and past it, soft where it meets the air; under it on the torso, a dark crease."""
    sx, sy = body_sway(k, n)
    lx, ly = lift(k)
    sx, sy = sx+lx, sy+ly
    ex, ey = slide(k)
    yy, xx = np.mgrid[0:H, 0:W]
    a = np.zeros((H, W, 4), np.uint8)
    a[(xx >= 45+sx) & (xx <= 75+sx) & (yy >= 20+sy) & (yy <= 45+sy)] = HEAD
    a[(xx >= 35+sx) & (xx <= 85+sx) & (yy >= 46+sy) & (yy <= 110+sy)] = TORSO
    a[(xx == 86+sx) & (yy >= 46+sy) & (yy <= 110+sy)] = (*TORSO[:3], SOFT)
    for x0 in (44, 70):
        a[(xx >= x0+sx) & (xx <= x0+6+sx) & (yy >= 111+sy) & (yy <= 150)] = LEG
    px, py = xx-sx-ex, yy-sy-ey  # where each pixel lies on the part, as in cell 0
    r = out_of_part(px, py)
    part = (r <= 1) & (py <= BOTTOM)
    stripes = np.array([(*c, 255) for c in STRIPES], np.uint8)
    a[part] = stripes[((px+2*py)//3 % 3)[part]]
    a[(py == BOTTOM+1) & (r <= 1) & (a[..., 3] == 255)] = (*CREASE, 255)  # on the torso only
    a[(r > 1) & (r <= 1.06) & (py <= BOTTOM) & (a[..., 3] == 0)] = (*STRIPES[0], SOFT)
    return Image.fromarray(a, 'RGBA')


def part_at(k, slide=still):
    """The part's opaque pixels in cell k."""
    sx, sy = body_sway(k)
    ex, ey = slide(k)
    yy, xx = np.mgrid[0:H, 0:W]
    return (out_of_part(xx-sx-ex, yy-sy-ey) <= 1) & (yy-sy-ey <= BOTTOM)


def ours(k, slide=still):
    """The part's pixels in cell k: its opaque ones, and its soft edge against the air."""
    sx, sy = body_sway(k)
    ex, ey = slide(k)
    yy, xx = np.mgrid[0:H, 0:W]
    r = out_of_part(xx-sx-ex, yy-sy-ey)
    return part_at(k, slide) | ((r > 1) & (r <= 1.06) & (yy-sy-ey <= BOTTOM) & (xx > 86+sx))


def mask_png(path, mask):
    """A region mask drawn on cell 0: opaque white over `mask`, clear elsewhere."""
    Image.fromarray(np.where(mask[..., None], 255, 0).astype(np.uint8).repeat(4, axis=2), 'RGBA').save(path)
    return path


def answer_of(loop_dir, regions, **kw):
    """What `video-sway` asked the same way comes to on the loop (`sway.solve`), with nothing written."""
    request = sway.read_request(loop_dir, regions, **kw)
    strip = request.source_path if request.source_path.exists() else request.strip_path
    data = strip.read_bytes()
    return sway.solve(request, Image.open(io.BytesIO(data)).convert('RGBA'), strip_name=strip.name,
                      strip_sha256=hashlib.sha256(data).hexdigest())


def cells_of(loop_dir):
    strip = np.asarray(Image.open(loop_dir/'walk.strip.png').convert('RGBA'))
    return [np.ascontiguousarray(strip[:, k*W:(k+1)*W]) for k in range(strip.shape[1]//W)]


def part_swayed(root, **kw):
    """A strip of the part figure swayed with a mask of the part, drawn on cell 0, as asked (`swayed`), in `root`: its loop
    directory, its record and the answer it moved the loop by."""
    loop_dir = strip_dir(root/'loop', part_figure)
    return (loop_dir, *swayed(loop_dir, [PART], region_mask=mask_png(root/'mask.png', part_at(0)), **kw))


@pytest.fixture(scope='module')
def masked_once(tmp_path_factory):
    """The part figure swayed with a mask of the part as `--region-mask` alone asks (`part_swayed`), made once for the
    module: four cases read that same sway, none writes its loop."""
    return part_swayed(tmp_path_factory.mktemp('masked'))


@pytest.fixture(scope='module')
def flush_once(tmp_path_factory):
    """The same with the mask as drawn, not worn in (`--region-shrink 0`), made once for the module for three cases."""
    return part_swayed(tmp_path_factory.mktemp('flush'), region_shrink=0)


def grow4(mask, px):
    for _ in range(px):
        p = np.pad(mask, 1)
        mask = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return mask


def shrink4(mask, px):
    for _ in range(px):
        p = np.pad(mask, 1)
        mask = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return mask


def chebyshev_within(mask, px):
    """The pixels `px` 8-neighbour steps or fewer from `mask`."""
    for _ in range(px):
        p = np.pad(mask, 1)
        mask = np.logical_or.reduce([p[dy:dy+H, dx:dx+W] for dy in range(3) for dx in range(3)])
    return mask


def distance_by_offsets(held, lx, ly):
    """The distance to the nearest held pixel in rim units, Lx across and Ly down, 1 past it: every offset inside the
    ellipse tried in turn, apart from the engine's rows-then-columns."""
    d = np.ones(held.shape)
    p = np.pad(held, ((ly, ly), (lx, lx)))
    for oy in range(-ly, ly+1):
        for ox in range(-lx, lx+1):
            r = math.hypot(ox/lx, oy/ly)
            if r < 1:
                d = np.where(p[ly+oy:ly+oy+H, lx+ox:lx+ox+W] & (r < d), r, d)
    return d


def rim_for(dx, dy, scale=1.0):
    """The rim the move asks: the fold rule's least band each way times `scale`, widened until the slope is 0.9 or less."""
    least = [math.ceil((math.ceil(float(np.max(np.abs(v)))*math.pi/2)+1)*scale) for v in (dx, dy)]
    lx, ly = least
    while float(np.max(0.5*math.pi*np.hypot(dx/lx, dy/ly))) > 0.9:
        lx, ly = lx+1, ly+1
    return lx, ly, least


def weights(answer, k):
    """Per pixel of cell k, the share of the cell's offset it moves by, and what the mask moves and holds there."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    field, sets = answer.mask_field(k, xx, yy)
    offset = math.hypot(answer.dx[k], answer.dy[k])
    return (np.hypot(field[1], field[2])/offset if offset else np.zeros((H, W))), sets


def held_kept(cells, source, slid=still, lift=still):
    """Every visible pixel outside the part and its soft edge keeps its bytes in every cell."""
    for k in range(N):
        outside = (source[k][..., 3] > 0) & ~np.roll(ours(k, slid), lift(k)[1], axis=0)
        assert np.array_equal(cells[k][outside], source[k][outside]), k


# ---------------------------------------------------------------- off, nothing of it

@pytest.mark.slow
def test_without_a_region_mask_the_sway_is_as_ever(tmp_path):
    for name, kw, args in (('plain', {}, []), ('band', {'band': 'auto'}, ['--band', 'auto'])):
        by_cli, by_api = strip_dir(tmp_path/f'{name}-cli', part_figure), strip_dir(tmp_path/f'{name}-api', part_figure)
        assert cli.main(['video-sway', '--loop-dir', str(by_cli), '--region', text(PART), *args]) == 0
        sway.sway_loop(by_api, [PART], region_mask=None, region_rim=None, region_shrink=None, region_crease=False, **kw)
        assert (by_cli/'walk.strip.png').read_bytes() == (by_api/'walk.strip.png').read_bytes(), name
        record = json.loads((by_cli/'walk.strip.json').read_text())['follow']
        assert 'region_mask' not in record and all(entry['radius_px'] is not None for entry in record['fold']['regions'])


# ---------------------------------------------------------------- what is held, what moves

@pytest.mark.slow
def test_every_visible_pixel_outside_the_part_keeps_its_bytes_at_any_gain(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    source = cells_of(strip_dir(tmp_path/'source', part_figure))
    for gain in GAINS:
        plain_dir, masked_dir = strip_dir(tmp_path/f'plain-{gain}', part_figure), strip_dir(tmp_path/f'masked-{gain}', part_figure)
        sway.sway_loop(plain_dir, [PART], gain=gain, on_fold='lower')
        record = sway.sway_loop(masked_dir, [PART], gain=gain, on_fold='lower', region_mask=mask)
        plain, masked = cells_of(plain_dir), cells_of(masked_dir)
        held_kept(masked, source)
        plain_outside = moved = 0
        for k in range(N):
            outside = (source[k][..., 3] > 0) & ~ours(k)
            plain_outside += int(np.any(plain[k][outside] != source[k][outside], axis=-1).sum())
            moved += int(np.any(masked[k] != source[k], axis=-1).sum())
        assert plain_outside > 0 and moved > 0, gain  # the ellipse's weight moves the crease under the part; the mask does not
        ledger = record['region_mask']['ledger']
        assert ledger['held_changed_px_max'] == 0 and min(ledger['held_px']) > 0 and record['gain'] == gain


def test_inside_its_rim_the_part_moves_whole_and_it_moves_into_the_air_and_no_further(flush_once, tmp_path):
    loop_dir, record, answer = flush_once  # the mask as drawn, and the answer the loop was moved by
    source, moved = cells_of(strip_dir(tmp_path/'source', part_figure)), cells_of(loop_dir)
    lx, ly = record['region_mask']['rim']['across_px'], record['region_mask']['rim']['down_px']
    air = math.ceil(answer.reach) + 2
    into_air = whole = 0
    for k in range(N):
        clear = source[k][..., 3] == 0
        # Past the move's reach and 2 px of the part, the air stays as it was; nearer, the part moves into it.
        far = clear & ~chebyshev_within(part_at(k), air)
        assert np.array_equal(moved[k][far], source[k][far]), k
        into_air += int((clear & (moved[k][..., 3] > 0)).sum())
        # Where no held pixel lies within the rim and two px more, a pixel of the part is the cell moved by the cell's
        # offset itself.
        _, sets = weights(answer, k)
        core = part_at(k) & (distance_by_offsets(sets['held'], lx+2, ly+2) >= 1)
        assert np.array_equal(moved[k][core], whole_move(answer.cells[k], answer.dx[k], answer.dy[k])[core]), k
        whole += int(core.sum()) if math.hypot(answer.dx[k], answer.dy[k]) > 0 else 0
    assert into_air > 0 and whole > 0
    assert record['region_mask']['air_px'] == air and record['region_mask']['soft_px'] == 2


def test_the_parts_soft_edge_moves_with_it_and_a_soft_pixel_beside_the_torso_is_held(flush_once, tmp_path):
    loop_dir, _, _ = flush_once  # the mask as drawn
    source, moved = cells_of(strip_dir(tmp_path/'source', part_figure)), cells_of(loop_dir)
    near = edge_moved = 0
    for k in range(N):
        solid, part = source[k][..., 3] >= 128, part_at(k)
        # The torso's soft side is held where a solid pixel of the torso lies beside it, however near the part.
        beside = (source[k][..., 3] == SOFT) & ~part & grow4(solid & ~part, 1)
        assert beside.any() and np.array_equal(moved[k][beside], source[k][beside]), k
        near += int((beside & grow4(part, 2)).sum())
        edge = (source[k][..., 3] == SOFT) & ours(k) & ~part
        edge_moved += int(np.any(moved[k][edge] != source[k][edge], axis=-1).sum())
    assert near > 0 and edge_moved > 0


# ---------------------------------------------------------------- carried by its own pixels

@pytest.mark.slow
def test_the_mask_is_carried_round_where_the_cell_before_had_it_first_and_the_record_says_so(tmp_path):
    for name, slid in (('slide', slide), ('ramp', ramp)):  # the ramp lies 12 px from the lay, past the search
        figure = lambda k, n=N, s=slid: part_figure(k, n, slide=s)
        mask = mask_png(tmp_path/f'{name}.png', part_at(0, slid))
        loop_dir = strip_dir(tmp_path/name, figure)
        sway.sway_loop(loop_dir, [PART], region_mask=mask)
        held_kept(cells_of(loop_dir), cells_of(strip_dir(tmp_path/f'{name}-source', figure)), slid)
        masked = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['region_mask']
        assert masked['kept'] == [] and masked['carry']['search_px'] == SEARCH
        cells = masked['carry']['cells']
        assert cells[0] == {'px': [0.0, 0.0], 'from': 'base'}
        for k in range(1, N):
            (sx, sy), (ex, ey) = body_sway(k), slid(k)
            entry = cells[k]
            assert entry['from'] == 'pixels' and abs(entry['px'][0]-(sx+ex)) < 0.1 and abs(entry['px'][1]-(sy+ey)) < 0.1, (name, k)
            assert entry['curvature'][0] > 0 and entry['curvature'][1] > 0, (name, k)
            if abs(ey) >= 10:  # past the search from the lay
                assert entry['guess'] == 'before', (name, k)


def test_a_part_whose_body_jumps_past_the_search_is_found_round_the_lay(tmp_path):
    lift = lambda k: (0, 12) if k == 5 else (0, 0)
    figure = lambda k, n=N: part_figure(k, n, lift=lift)
    loop_dir = strip_dir(tmp_path/'loop', figure)
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    sway.sway_loop(loop_dir, [PART], gain=1.0, region_mask=mask)
    held_kept(cells_of(loop_dir), cells_of(strip_dir(tmp_path/'source', figure)), lift=lift)
    masked = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['region_mask']
    assert masked['kept'] == []
    for k in (5, 6):  # 12 px from where the cell before had it: found round its own lay
        sx, sy = body_sway(k)
        entry = masked['carry']['cells'][k]
        assert entry['from'] == 'pixels' and entry['guess'] == 'lay', (k, entry)
        assert abs(entry['px'][0]-sx) < 0.1 and abs(entry['px'][1]-(sy+lift(k)[1])) < 0.1, (k, entry)


def test_where_neither_guess_settles_the_carry_of_the_cell_before_is_kept_never_the_lay(tmp_path, capsys):
    figure = sliding(lambda k: (0, 0), part=TORSO)  # cloth with no pattern
    loop_dir = strip_dir(tmp_path/'loop', figure)
    patch = (60.0, 72.0, 14.0, 12.0)
    mask = mask_png(tmp_path/'mask.png', covered(patch) & (np.asarray(figure(0))[..., 3] >= 128))
    as_cut = cells_of(loop_dir)
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--region-mask', str(mask)]) == 0
    out = capsys.readouterr()
    moved = cells_of(loop_dir)
    for k in range(N):  # the mask stays where cell 0 had it, and nothing past it changes
        assert np.array_equal(moved[k][~covered(patch)], as_cut[k][~covered(patch)]), k
    masked = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['region_mask']
    cells, kept = masked['carry']['cells'], masked['kept']
    assert [entry['cell'] for entry in kept] == list(range(1, N))
    for k in range(1, N):
        assert cells[k]['from'] == 'before' and cells[k]['px'] == [0.0, 0.0] and 'guess' not in cells[k], k
        before = f"round where cell {k-1} had it: the least is on the search's edge, {SEARCH} px from where cell {k-1} had it"
        assert cells[k]['why'].startswith(before) and kept[k-1] == {'cell': k, 'why': cells[k]['why']}, k
    whys = '; '.join(f"cell {e['cell']}: {e['why']}" for e in kept)
    assert (f"--region-mask keeps the carry of the cell before in {N-1} cells, its own pixels found surely neither round where "
            f"that cell had them nor round the lay ({whys}) — follow.region_mask.kept") in out.err
    assert json.loads(out.out)['region_mask']['kept_cells'] == list(range(1, N))


# ---------------------------------------------------------------- the rim

@pytest.mark.parametrize('gain', [2.5, pytest.param(6.0, marks=pytest.mark.slow)])
def test_the_rim_is_read_off_the_move_and_the_weight_is_sin2_of_the_distance_to_the_held_pixels(masked_once, tmp_path, gain):
    # At the default gain (2.5, what `read_ask` reads for a gain left out) the sway is the one the module made once.
    loop_dir, record, answer = masked_once if gain == sway.GAIN_DEFAULT else part_swayed(tmp_path, gain=gain)
    held_kept(cells_of(loop_dir), cells_of(strip_dir(tmp_path/'source', part_figure)))
    lx, ly, least = rim_for(answer.dx, answer.dy)
    rim = record['region_mask']['rim']
    assert (rim['across_px'], rim['down_px'], rim['least_px']) == (lx, ly, least) and rim['slope'] <= 0.9 == rim['slope_bound']
    assert record['fold']['regions'] == [{'radius_px': None, 'ratio': 0.0, 'gain_limit': None}] and not record['fold']['lowered']
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    for k in range(0, N, 3):
        (inside, move_x, move_y), sets = answer.mask_field(k, xx, yy)
        moving = sets['region'] | sets['soft'] | sets['air']
        weight = np.where(moving, np.sin(0.5*math.pi*distance_by_offsets(sets['held'], lx, ly))**2, 0.0)
        assert np.allclose(move_x, answer.dx[k]*weight, rtol=0, atol=1e-9) and np.allclose(move_y, answer.dy[k]*weight, rtol=0, atol=1e-9), k
        assert not inside[sets['held']].any()
    ledger = record['region_mask']['ledger']
    assert ledger['folded_px_max'] == 0 and min(ledger['jacobian_min']) > 0
    assert ledger['walked_px_max'] == 0 and ledger['read_held_half_px_max'] == 0
    assert max(spot[0] for spot in ledger['read_held_weight'] if spot is not None) < 0.5
    assert ledger['renormalised_px_max'] > 0  # reads that touch a held pixel are read from the others


@pytest.mark.slow
def test_a_wider_rim_moves_less_of_the_part_whole(masked_once, tmp_path):
    narrow_dir, narrow, narrow_answer = masked_once  # the rim as ever, and the answer the loop was moved by
    wide_dir, wide, wide_answer = part_swayed(tmp_path, region_rim=1.5)
    held_kept(cells_of(wide_dir), cells_of(strip_dir(tmp_path/'source', part_figure)))
    assert any(not np.array_equal(a, b) for a, b in zip(cells_of(narrow_dir), cells_of(wide_dir)))
    lx, ly, least = rim_for(wide_answer.dx, wide_answer.dy, 1.5)
    rim, was = wide['region_mask']['rim'], narrow['region_mask']['rim']
    assert (rim['across_px'], rim['down_px'], rim['least_px']) == (lx, ly, least) and wide['region_mask']['asked']['rim'] == 1.5
    assert rim['across_px'] >= was['across_px'] and rim['down_px'] >= was['down_px'] and rim != was
    whole_narrow = whole_wide = 0
    for k in range(N):
        a, sets = weights(narrow_answer, k)
        b, _ = weights(wide_answer, k)
        assert np.all(b <= a + 1e-12), k  # nearer the held pixels in rim units, everywhere
        whole_narrow += int((sets['region'] & (a >= 1 - 1e-12)).sum())
        whole_wide += int((sets['region'] & (b >= 1 - 1e-12)).sum())
    assert whole_wide < whole_narrow


@pytest.mark.slow
def test_band_up_yields_above_the_centre(tmp_path):
    loop_dir, record, answer = part_swayed(tmp_path, on_fold='lower', band_up=(0.2, 'auto'))  # and the answer it moved by
    held_kept(cells_of(loop_dir), cells_of(strip_dir(tmp_path/'source', part_figure)))
    up = answer.mask.up
    lx, ly = record['region_mask']['rim']['across_px'], record['region_mask']['rim']['down_px']
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    binds = 0
    for k in range(N):
        (_, move_x, move_y), sets = answer.mask_field(k, xx, yy)
        carried = up.carried[k][0]['px']
        yielded = sway.band_weight(xx, yy, (PART[0]+carried[0], PART[1]+carried[1]), *up.shapes[0])
        rim = np.where(sets['region'] | sets['soft'] | sets['air'],
                       np.sin(0.5*math.pi*distance_by_offsets(sets['held'], lx, ly))**2, 0.0)
        weight = np.minimum(rim, yielded)  # the lesser of the rim and the upper yield
        assert np.allclose(move_x, answer.dx[k]*weight, rtol=0, atol=1e-9) and np.allclose(move_y, answer.dy[k]*weight, rtol=0, atol=1e-9), k
        binds += int((sets['region'] & (yielded < rim - 1e-9)).sum())
        assert not (sets['region'] & (yielded < rim - 1e-9) & (yy > PART[1]+carried[1])).any(), k  # below the centre the rim's
    assert binds > 0
    masked = record['region_mask']
    assert masked['asked']['up'] == {'flat': 0.2, 'band': 'auto'}
    band, margin = math.ceil(answer.reach_requested*math.pi/2)+1, math.ceil(answer.reach_requested)+1
    assert (masked['up']['band_px'], masked['up']['margin_px']) == (band, margin) and len(masked['up']['carry']['cells']) == N
    assert masked['up']['regions'][0]['flat_px'] == [PART[2]+margin, round(0.2*PART[3], 4), PART[3]+margin]
    assert masked['up']['regions'][0]['band_px'] == [band, round(max(band, 0.8*PART[3]+3), 4), band]


# ---------------------------------------------------------------- the mask as given: worn in, its crease

@pytest.mark.slow
def test_a_mask_drawn_2_px_large_holds_the_belly_once_it_is_worn_in(tmp_path):
    big = grow4(part_at(0), 2) & (np.asarray(part_figure(0))[..., 3] >= 128)  # past the part into the crease and the torso
    mask = mask_png(tmp_path/'big.png', big)
    source = cells_of(strip_dir(tmp_path/'source', part_figure))
    worn_dir, given_dir = strip_dir(tmp_path/'worn', part_figure), strip_dir(tmp_path/'given', part_figure)
    record = sway.sway_loop(worn_dir, [PART], region_mask=mask)
    sway.sway_loop(given_dir, [PART], region_mask=mask, region_shrink=0)
    worn, given = cells_of(worn_dir), cells_of(given_dir)
    yy = np.mgrid[0:H, 0:W][0]
    belly_given = 0
    for k in range(N):
        belly = (source[k][..., 3] > 0) & ~ours(k) & (yy > BOTTOM + body_sway(k)[1])  # the crease and the torso under it
        assert np.array_equal(worn[k][belly], source[k][belly]), k
        belly_given += int(np.any(given[k][belly] != source[k][belly], axis=-1).sum())
    assert belly_given > 0  # as given, the mask moves the belly's top rows
    masked = record['region_mask']
    assert masked['asked']['shrink_px'] == 2 and masked['mask']['carried_by_px'] == int(big.sum())
    assert masked['mask']['px'] == int(shrink4(big, 2).sum())


@pytest.mark.slow
def test_the_crease_ends_the_mask_on_the_darkest_row_under_it_before_it_is_worn_in(flush_once, tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    source = cells_of(strip_dir(tmp_path/'source', part_figure))
    creased_dir = strip_dir(tmp_path/'creased', part_figure)
    record = sway.sway_loop(creased_dir, [PART], region_mask=mask, region_shrink=0, region_crease=True)
    plain_dir, _, _ = flush_once  # the same mask as drawn, without the crease
    creased, plain = cells_of(creased_dir), cells_of(plain_dir)
    yy = np.mgrid[0:H, 0:W][0]
    crease_moved = 0
    for k in range(N):
        crease = np.all(source[k][..., :3] == CREASE, axis=-1) & (source[k][..., 3] == 255)
        below = (source[k][..., 3] >= 128) & ~ours(k) & ~crease & (yy > BOTTOM + body_sway(k)[1])
        assert crease.any() and np.array_equal(plain[k][crease], source[k][crease]), k  # without it the crease is held
        for cells in (creased, plain):
            assert np.array_equal(cells[k][below], source[k][below]), k
        crease_moved += int(np.any(creased[k][crease] != source[k][crease], axis=-1).sum())
    assert crease_moved > 0  # with it the crease is the region's last row, and moves with it
    part0 = part_at(0)
    over_torso = [x for x in range(W) if part0[:, x].any() and x <= 85]
    over_air = [x for x in range(W) if part0[:, x].any() and x > 85]
    assert record['region_mask']['crease'] == [{'rows': {str(x): BOTTOM+1 for x in over_torso}, 'free_columns': len(over_air)}]
    worn = sway.sway_loop(strip_dir(tmp_path/'worn', part_figure), [PART], region_mask=mask, region_crease=True)
    assert worn['region_mask']['mask']['box'][3] == BOTTOM + 1 - 2  # the crease, then worn in by 2
    assert worn['region_mask']['mask']['carried_by_px'] == int(part0.sum()) + len(over_torso)


# ---------------------------------------------------------------- the same region on every path

def test_a_folder_of_the_masks_carried_moves_as_the_one_mask(masked_once, tmp_path):
    one_dir, one, answer = masked_once  # the one mask, carried, and the answer the loop was moved by
    folder_dir = strip_dir(tmp_path/'folder', part_figure)
    held_kept(cells_of(one_dir), cells_of(strip_dir(tmp_path/'source', part_figure)))
    masks = tmp_path/'masks'
    masks.mkdir()
    for k in range(N):
        region = answer.mask.region_in(k, np.asarray(answer.cells[k])[..., 3] >= 128)
        Image.fromarray(np.where(region, 255, 0).astype(np.uint8), 'L').save(masks/f'cell-{k:03d}.png')
    given = sway.sway_loop(folder_dir, [PART], region_mask=masks, region_shrink=0)
    assert (one_dir/'walk.strip.png').read_bytes() == (folder_dir/'walk.strip.png').read_bytes()
    masked = given['region_mask']
    assert masked['asked']['per_cell'] and len(masked['asked']['sha256']) == N
    assert masked['carry'] is None and masked['mask'] is None and masked['kept'] == []
    assert masked['ledger'] == one['region_mask']['ledger'] and masked['rim'] == one['region_mask']['rim']


@pytest.mark.slow
def test_the_frames_are_moved_as_the_strip(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    frames = tmp_path/'frames'
    frames.mkdir()
    for k, name in enumerate(FILES):
        part_figure(k).save(frames/name)
    loop_dir = strip_dir(tmp_path/'loop', part_figure, delay_ms=41.67)  # a frame at --fps 24, as a strip's meta writes it
    assert cli.main(['video-sway', '--frames', str(frames), '--out', str(tmp_path/'out'), '--fps', '24', '--region', text(PART),
                     '--region-mask', str(mask)]) == 0
    strip_record = sway.sway_loop(loop_dir, [PART], region_mask=mask)
    strip = cells_of(loop_dir)
    out = [np.asarray(Image.open(tmp_path/'out'/name).convert('RGBA')) for name in FILES]
    held_kept(out, [np.asarray(part_figure(k)) for k in range(N)])
    assert all(np.array_equal(out[k], strip[k]) for k in range(N))
    assert json.loads((tmp_path/'out'/'sway.json').read_text())['region_mask'] == strip_record['region_mask']


@pytest.mark.slow
def test_a_turned_loop_moved_on_the_cuts_reading_is_the_cuts_cell_for_cell(origin, tmp_path):
    # The mask is drawn on the cut's first cell, where the regions were read; the turned loop carries it from there.
    o, read_on, _ = origin
    cell0 = cut_cells(o)[0]
    cx, cy, rx, ry = cut_chest(o)
    yy, xx = np.mgrid[0:cell0.shape[0], 0:cell0.shape[1]]
    mask = tmp_path/'mask.png'
    Image.fromarray(np.where((np.hypot((xx-cx)/rx, (yy-cy)/ry) < 1) & (cell0[..., 3] >= 128), 255, 0).astype(np.uint8), 'L').save(mask)
    sway.sway_loop(shutil.copytree(o, tmp_path/'plain'), [cut_chest(o)], on_fold='lower')
    masked = sway.sway_loop(shutil.copytree(o, tmp_path/'masked'), [cut_chest(o)], on_fold='lower', region_mask=mask)
    loop_dir = aligned(o, tmp_path/'turned')
    turned = meta_of(loop_dir)['cycle_align']['turned_by']
    result = sway.sway_loop(loop_dir, [cut_chest(o)], on_fold='lower', read_on=read_on, region_mask=mask)
    moved, reference, ellipse = cut_cells(loop_dir), cut_cells(tmp_path/'masked'), cut_cells(tmp_path/'plain')
    n = len(reference)
    assert sum(not np.array_equal(reference[k], ellipse[k]) for k in range(n)) > n//2  # the mask moves the part otherwise
    assert all(np.array_equal(moved[k], reference[(k+turned) % n]) for k in range(n))
    assert result['read_on']['relation'] == 'transported'
    cut_carry, turned_carry = masked['region_mask']['carry']['cells'], result['region_mask']['carry']['cells']
    assert [entry['px'] for entry in turned_carry] == [cut_carry[(k+turned) % n]['px'] for k in range(n)]


# ---------------------------------------------------------------- refused, and said

def test_what_a_region_mask_is_refused_for_leaves_the_loop_as_it_was(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop', part_figure)
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    small = tmp_path/'small.png'
    Image.new('RGBA', (W-1, H), (255, 255, 255, 255)).save(small)
    empty = tmp_path/'empty.png'
    Image.new('RGBA', (W, H)).save(empty)
    dot = np.zeros((H, W), bool)
    dot[70:73, 70:73] = True
    tiny = mask_png(tmp_path/'tiny.png', dot)
    air = np.zeros((H, W), bool)
    air[2:6, 2:6] = True
    clear = mask_png(tmp_path/'clear.png', air)
    few = tmp_path/'few'
    few.mkdir()
    for k in range(3):
        Image.new('L', (W, H), 255).save(few/f'{k}.png')
    for region, args, said in (
            (text(PART), ['--region-mask', str(mask), '--band', 'auto'],
             r"--region-mask moves the region's own pixels and holds every visible pixel round it, the weight falling over a rim "
             r"inside it; --band moves an ellipse over a band outside it: give one of them$"),
            (text(PART), ['--region-mask', str(mask), '--band-margin', '2'], r'--band-margin shapes a band; with --region-mask the rim is read off'),
            (f'{text(PART)},0.5', ['--region-mask', str(mask)], r"--region-mask gives the region as a mask, and a region's fifth number \(flat, 0.5"),
            (text(PART), ['--region-mask', str(mask), '--stretch-floor', '0.5'], r"--stretch-floor reads the weight that falls from a region's centre; with --region-mask"),
            (text(PART), ['--region-mask', str(mask), '--hold-mask', str(mask)], r'--region-mask holds every visible pixel outside the region already'),
            (text(PART), ['--region-mask', str(mask), '--region-rim', '0.5'], r"--region-rim is how many times the fold rule's least band the rim starts at, 1 or more; got 0.5$"),
            (text(PART), ['--region-mask', str(mask), '--region-shrink', '-1'], r'--region-shrink is the px the mask is worn in by, a whole number, 0 or more; got -1$'),
            (text(PART), ['--region-rim', '1.5'], r'--region-rim, --region-shrink and --region-crease shape a region mask; they need --region-mask$'),
            (text(PART), ['--region-crease'], r'they need --region-mask$'),
            (text(PART), ['--region-mask', str(small)], rf'--region-mask is {W-1}x{H}, the cells are {W}x{H}: a mask is drawn on a cell'),
            (text(PART), ['--region-mask', str(empty)], r'holds no pixel \(alpha 128 or more'),
            (text(PART), ['--region-mask', str(few)], rf'--region-mask holds 3 masks and there are {N} cells: a folder gives one mask per cell'),
            (text(PART), ['--region-mask', str(tiny)], r'--region-shrink 2 wears the region mask away: no pixel of it is left to move$'),
            (text(PART), ['--region-mask', str(clear)], r'--region-mask holds no solid pixel of the cell it was drawn on')):
        with pytest.raises(SystemExit, match=said) as refused:
            cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', region, *args])
        assert refused.value.code not in (0, None), args
        assert (loop_dir/'walk.strip.png').read_bytes() == as_cut and 'follow' not in json.loads((loop_dir/'walk.strip.json').read_text())


# ---------------------------------------------------------------- the ledger

@pytest.mark.slow
def test_the_ledger_counts_each_cell_and_a_warning_says_what_is_in_it(masked_once, tmp_path, capsys):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    loop_dir = strip_dir(tmp_path/'loop', part_figure)
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(PART), '--region-mask', str(mask)]) == 0
    out = capsys.readouterr()
    held_kept(cells_of(loop_dir), cells_of(strip_dir(tmp_path/'source', part_figure)))
    record = json.loads((loop_dir/'walk.strip.json').read_text())['follow']
    masked = record['region_mask']
    assert masked['policy'] == POLICY and record['carry_basis'] == {'reading': 'outline-lay', 'region': 'own-pixels'}
    assert masked['asked'] == {'per_cell': False, 'sha256': hashlib.sha256(mask.read_bytes()).hexdigest(), 'rim': 1.0,
                               'shrink_px': 2, 'crease': False, 'up': None}
    assert masked['mask']['px'] == int(shrink4(part_at(0), 2).sum()) and masked['crease'] is None and masked['up'] is None
    ledger = masked['ledger']
    for key in ('region_px', 'soft_px', 'air_px', 'held_px', 'held_changed_px', 'pressed_px', 'stretched_px', 'folded_px',
                'read_held_half_px', 'renormalised_px', 'walked_px'):
        assert len(ledger[key]) == N and ledger[f'{key}_max'] == max(ledger[key]), key
    for key in ('move_most', 'pressed_box', 'area_least', 'stretched_box', 'area_most', 'jacobian_min', 'read_held_weight'):
        assert len(ledger[key]) == N, key
    answer = masked_once[2]  # the answer to the same ask, the module's sway's
    for k in range(N):  # the most a pixel of the part moves, and where
        offset = math.hypot(answer.dx[k], answer.dy[k])
        most = ledger['move_most'][k]
        assert (most is None) == (offset == 0), k
        if most is not None:
            share, sets = weights(answer, k)
            assert abs(most[0] - offset*float(share[sets['region']].max())) < 1e-4 and sets['region'][most[2], most[1]], k
    assert ledger['move_most_max'] == max(m[0] for m in ledger['move_most'] if m)
    for key, what in (('pressed_px', 'moved pixels are left under 0.85 of their area'),
                      ('renormalised_px', 'reads took a held pixel and were read from the others'),
                      ('walked_px', 'reads took a held pixel and were walked back'),
                      ('held_changed_px', 'held pixels do not keep their bytes')):
        assert (f'--region-mask: {what}' in out.err) == (ledger[f'{key}_max'] > 0), key
        assert (f'follow.region_mask.ledger.{key}' in out.err) == (ledger[f'{key}_max'] > 0), key
    summary = json.loads(out.out)['region_mask']
    assert summary == {'kept_cells': [], 'rim_px': [masked['rim']['across_px'], masked['rim']['down_px']],
                       'move_most_px': ledger['move_most_max'], 'walked_px_max': 0}
