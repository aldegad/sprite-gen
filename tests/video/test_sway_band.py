"""`video-sway --band`: a region moved as one mass.

Without it a region's weight falls from its centre, so a large part moves its middle and presses the rest in. With it the
weight is 1 over the region's ellipse grown by a margin and falls to 0 over a band past it, each "auto" read off the
move's reach; the fold rule reads the band, and the steepest the weight falls where the band is narrower across its ring
than its px, and `--band-up` gives the part above the centre its own flat share and band.
Each region is carried into a cell by its own pixels, and by the body's lay where they are not found surely, the record
saying which. Past every band no byte changes, and a ledger says, cell by cell, what the move did. Synthetic figures only:
the inspection's body (a soft part on a torso that sways once and bobs twice a cycle), the frames test's noise-textured
body, the read-on test's runner, cloth with no pattern and cloth dotted every 4 px.

Each case first reads what a sway writes with or without the band (its cells, its fold, whether it refuses, what it
says), so on a release without the band the same input fails on what comes out; the band's own record is read after.
"""
import hashlib
import io
import json
import math
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen import cli
from sprite_gen.video import sway

from tests.video.test_follow_inspect import CHEST, HEAD, LEG, N, PART, W, H, cells, sway as body_sway, strip_dir
from tests.video.test_follow_inspect import TORSO as TORSO_COLOUR
from tests.video.test_follow_read_on import aligned, cells_of, chest, meta_of, origin, origin_once  # noqa: F401 (fixtures)
from tests.video.test_sway_frames import TORSO, as_strip_cell, cut, frame, frames_dir, rgba

FILES = [f'frame-{k:03d}.png' for k in range(N)]
POLICY = 'own-pixels/margin-band-cos2/1'
OWN_PIXELS = {'reading': 'outline-lay', 'region': 'own-pixels'}
SEARCH = 9  # px either way of the lay a region's pixels are looked for
EDGE = f"the least is on the search's edge, {SEARCH} px from the lay"
TIE = 'the least is tied with a place more than a pixel away'


def text(region):
    return ','.join(f'{v:g}' for v in region)


def answer_of(loop_dir, regions, **kw):
    """What `video-sway` asked the same way comes to on the loop (`sway.solve`), with nothing written."""
    request = sway.read_request(loop_dir, regions, **kw)
    strip = request.source_path if request.source_path.exists() else request.strip_path
    data = strip.read_bytes()
    return sway.solve(request, Image.open(io.BytesIO(data)).convert('RGBA'), strip_name=strip.name,
                      strip_sha256=hashlib.sha256(data).hexdigest())


def swayed(loop_dir, regions, **kw):
    """`sway.sway_loop` on the loop as asked, and the answer it moved the loop by (`sway.solve`'s, kept as it returned):
    what `answer_of` asked the same way comes to on the loop's strip as cut, or on a strip of the same figure (the same
    request on the same bytes), solved once and not again."""
    answers = []
    solve = sway.solve

    def kept(*args, **kwargs):
        answers.append(solve(*args, **kwargs))
        return answers[-1]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sway, 'solve', kept)
        record = sway.sway_loop(loop_dir, regions, **kw)
    (answer,) = answers
    return record, answer


@pytest.fixture(scope='module')
def banded_once(tmp_path_factory):
    """The inspection's figure swayed with --band auto, made once for the module (three cases read that same sway): its
    loop directory, which no case writes, and its record."""
    loop_dir = strip_dir(tmp_path_factory.mktemp('banded')/'loop')
    return loop_dir, sway.sway_loop(loop_dir, [CHEST], band='auto')


def auto(reach):
    """The margin and band `auto` reads off a move's reach: the reach and a pixel, and the narrowest band it does not fold."""
    return math.ceil(reach)+1, math.ceil(reach*math.pi/2)+1


def shapes(record):
    """Each region's flat radii and bands as the band's record holds them."""
    return [{key: region[key] for key in ('flat_px', 'band_px')} for region in record['band']['regions']]


def whole_move(cell, dx, dy):
    """`cell` moved by (dx, dy) everywhere, as the engine draws a move (one region flat over far more than the cell)."""
    w, h = cell.size
    return np.asarray(sway.move_regions(cell, [([(w/2, h/2, 1e4, 1e4, 0.9)], (dx, dy))], (0.0, 0.0)))


def rings(shape, centre, flat, band):
    """Per pixel, how far out it is, in the flat ellipse's radii and in the outer one's (1 on each rim): the flat
    ellipse about `centre` with radii across, above and below, the outer one those and the band's."""
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float64)
    dx, dy = xx-centre[0], yy-centre[1]
    above = dy < 0
    ay, by = np.where(above, flat[1], flat[2]), np.where(above, band[1], band[2])
    return np.hypot(dx/flat[0], dy/ay), np.hypot(dx/(flat[0]+band[0]), dy/(ay+by))


def sliding(slide, part=PART, dots=False):
    """The inspection's figure, its soft part drawn a further `slide(k)` whole pixels from where the torso carries it:
    the part slides on the body, and the body's outline does not show it. `part` the part's colour (the torso's: cloth
    with no pattern); `dots`: the torso dotted every 4 px each way, the dots carried with it, and no part."""
    def draw(k):
        sx, sy = body_sway(k)
        ex, ey = slide(k)
        im = Image.new('RGBA', (W, H))
        d = ImageDraw.Draw(im)
        d.rectangle((45+sx, 20+sy, 75+sx, 45+sy), fill=HEAD)
        d.rectangle((35+sx, 46+sy, 85+sx, 110+sy), fill=TORSO_COLOUR)
        if dots:
            for y in range(48, 109, 4):
                for x in range(37, 84, 4):
                    d.point((x+sx, y+sy), fill=PART)
        else:
            d.ellipse((46+sx+ex, 60+sy+ey, 74+sx+ex, 84+sy+ey), fill=part)
        d.rectangle((44+sx, 111+sy, 50+sx, 150), fill=LEG)
        d.rectangle((70+sx, 111+sy, 76+sx, 150), fill=LEG)
        return im
    return draw


def slide(k):
    return round(2*math.sin(4*math.pi*k/N)), round(2*math.cos(2*math.pi*k/N))-2


def ramp(k):
    """3 px further down each cell, to 12 px from where the torso carries it, held there, and back by the loop's end."""
    return 0, 3*min(k, N-k, 4)


def lay_warning(region, entries):
    whys = '; '.join(f'cell {k}: {why}' for k, why in entries)
    return (f"--band carries --region {text(region)} by the body's lay in {len(entries)} cells, its own pixels not found "
            f"surely ({whys}) — follow.band.lay")


# ---------------------------------------------------------------- the band's shape, read off the move

def test_without_a_band_the_record_and_the_strip_are_as_ever(tmp_path):
    plain = strip_dir(tmp_path/'plain')
    asked = strip_dir(tmp_path/'asked')
    assert cli.main(['video-sway', '--loop-dir', str(plain), '--region', text(CHEST)]) == 0
    sway.sway_loop(asked, [CHEST], band=None, band_margin=None, band_up=None)
    assert (plain/'walk.strip.png').read_bytes() == (asked/'walk.strip.png').read_bytes()
    record = json.loads((plain/'walk.strip.json').read_text())['follow']
    assert 'band' not in record and 'carry_basis' not in record


def test_auto_reads_the_margin_and_the_band_off_the_moves_reach(banded_once, tmp_path):
    reach = answer_of(strip_dir(tmp_path/'loop'), [CHEST]).reach_requested  # the move at the gain asked, before any band
    margin, band = auto(reach)
    _, record = banded_once  # the same figure swayed with --band auto
    # The fold rule reads the band: auto is the narrowest the move at the gain asked does not fold.
    assert [r['radius_px'] for r in record['fold']['regions']] == [band] and not record['fold']['lowered']
    assert record['fold']['ratio'] == round(reach*math.pi/(2*band), 3) < 1
    assert record.get('carry_basis') == OWN_PIXELS
    banded = record['band']
    assert (banded['policy'], banded['asked'], banded['reach_px'], banded['band_px'], banded['margin_px']) == \
           (POLICY, {'band': 'auto', 'margin': 'auto', 'up': None}, round(reach, 4), band, margin)
    assert shapes(record) == [{'flat_px': [CHEST[2]+margin, CHEST[3]+margin, CHEST[3]+margin], 'band_px': [band]*3}]
    assert min(banded['ledger']['jacobian_min']) > 0
    given = sway.sway_loop(strip_dir(tmp_path/'given'), [CHEST], band=40, band_margin=0)
    assert [r['radius_px'] for r in given['fold']['regions']] == [40]
    assert shapes(given) == [{'flat_px': [CHEST[2], CHEST[3], CHEST[3]], 'band_px': [40, 40, 40]}]


def test_the_fold_rule_reads_a_narrow_band_and_lowers_or_refuses_as_ever(tmp_path):
    per_gain = answer_of(strip_dir(tmp_path/'unit'), [CHEST], gain=1.0).reach_per_gain
    band = math.ceil(1.3*per_gain*math.pi/2)  # the move at gain 1.3 does not fold it, the move at 2.5 does
    assert 2.5*per_gain*math.pi/(2*band) >= 1
    with pytest.raises(SystemExit, match=rf"folds a region's band of {band} px over itself; lower --gain or give a wider --band$"):
        sway.sway_loop(strip_dir(tmp_path/'refused'), [CHEST], band=band)
    lowered = sway.sway_loop(strip_dir(tmp_path/'lowered'), [CHEST], band=band, on_fold='lower')
    assert lowered['fold']['lowered'] and 1 <= lowered['gain'] < 2.5
    assert lowered['reach_px']*math.pi/(2*band) < 1 and lowered['fold']['regions'][0]['radius_px'] == band


def test_a_band_narrower_across_its_ring_than_its_px_lowers_or_refuses_by_its_steepest_slope(banded_once, tmp_path):
    # Flat to a tenth of the radius above the centre, the band above runs out from a flat ellipse of another shape than
    # the outer one: across its ring it is narrower than its px, and the move at the gain asked, which the band's px
    # does not fold, folds the picture there.
    _, band = auto(answer_of(strip_dir(tmp_path/'unit'), [CHEST]).reach_requested)
    with pytest.raises(SystemExit, match=rf"--band's weight falls steeper than its {band} px where the band is narrower across its "
                                         r"ring \(cell \d+: at --gain 2.5 a pixel keeps -[\d.]+ of its area\); lower --gain or give a "
                                         r"wider --band or --band-up$"):
        sway.sway_loop(strip_dir(tmp_path/'refused'), [CHEST], band='auto', band_up='0.1')
    lowered = sway.sway_loop(strip_dir(tmp_path/'lowered'), [CHEST], band='auto', band_up='0.1', on_fold='lower')
    assert lowered['fold']['lowered'] and 1 <= lowered['gain'] < 2.5
    assert lowered['fold']['regions'][0]['radius_px'] == band and lowered['fold']['ratio'] < 1  # the band's px passes
    steepest = lowered['band']['regions'][0]['steepest']
    # The largest gain in steps of 0.01 whose steepest slope leaves every pixel some of its area.
    assert steepest['ratio'] == round(lowered['gain']*steepest['per_gain'], 3) < 1 <= round((lowered['gain']+0.01)*steepest['per_gain'], 2)
    assert min(lowered['band']['ledger']['jacobian_min']) > 0
    _, whole = banded_once  # the same figure swayed with --band auto, no --band-up
    assert whole['gain'] == 2.5 and whole['band']['regions'][0]['steepest']['ratio'] < 1


def test_band_up_stops_the_flat_part_over_the_centre_and_runs_its_band_past_the_top(banded_once, tmp_path):
    whole_dir, whole = banded_once  # the same figure swayed with --band auto
    up_dir = strip_dir(tmp_path/'up')
    up = sway.sway_loop(up_dir, [CHEST], band='auto', band_up=(0.4, 'auto'))
    moved_whole, moved_up = cells(whole_dir/'walk.strip.png'), cells(up_dir/'walk.strip.png')
    # Below the centre the two are one move; above it the upper part gives.
    upper_gives = 0
    for k in range(N):
        centre = math.ceil(CHEST[1]+body_sway(k)[1]+1)
        assert np.array_equal(moved_whole[k][centre:], moved_up[k][centre:]), k
        upper_gives += not np.array_equal(moved_whole[k][:centre], moved_up[k][:centre])
    assert upper_gives > N//2
    margin, band = whole['band']['margin_px'], whole['band']['band_px']
    assert shapes(up) == [{'flat_px': [CHEST[2]+margin, round(0.4*CHEST[3], 4), CHEST[3]+margin],
                           'band_px': [band, round(max(band, 0.6*CHEST[3]+3), 4), band]}]
    assert up['band']['asked']['up'] == {'flat': 0.4, 'band': 'auto'}
    # The band lies over the part only where it is asked to.
    assert up['band']['ledger']['band_on_regions_px_max'] > 0 and whole['band']['ledger']['band_on_regions_px_max'] == 0
    given = sway.sway_loop(strip_dir(tmp_path/'given'), [CHEST], band=40, band_up='0.4,20')
    assert given['band']['regions'][0]['band_px'] == [40, 20, 40]
    assert given['fold']['regions'][0]['radius_px'] == 20  # the fold rule reads the narrowest


# ---------------------------------------------------------------- refused

@pytest.mark.parametrize('args, said', [
    (['--region', f'{text(CHEST)},0.5', '--band', 'auto'], r'a region\'s fifth number \(flat, 0.5 on --region 60,72,18,16,0.5\) is '
                                                          r'the other way of moving it whole'),
    (['--region', text(CHEST), '--band', 'auto', '--stretch-floor', '0.5'], r'--stretch-floor reads the weight that falls'),
    (['--region', text(CHEST), '--band-margin', '3'], r'--band-margin and --band-up shape a band; they need --band$'),
    (['--region', text(CHEST), '--band-up', '0.4'], r'--band-margin and --band-up shape a band; they need --band$'),
])
def test_what_a_band_cannot_be_asked_with_is_refused_and_says_why(tmp_path, args, said):
    loop_dir = strip_dir(tmp_path/'loop')
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    with pytest.raises(SystemExit, match=said) as refused:
        cli.main(['video-sway', '--loop-dir', str(loop_dir), *args])
    assert refused.value.code not in (0, None)
    assert (loop_dir/'walk.strip.png').read_bytes() == as_cut and 'follow' not in json.loads((loop_dir/'walk.strip.json').read_text())


def test_a_flat_share_of_0_is_four_numbers_and_takes_a_band(tmp_path):
    four, five = strip_dir(tmp_path/'four'), strip_dir(tmp_path/'five')
    assert cli.main(['video-sway', '--loop-dir', str(four), '--region', text(CHEST), '--band', 'auto']) == 0
    assert cli.main(['video-sway', '--loop-dir', str(five), '--region', f'{text(CHEST)},0', '--band', 'auto']) == 0
    assert (four/'walk.strip.png').read_bytes() == (five/'walk.strip.png').read_bytes()


@pytest.mark.parametrize('flag, value, said', [
    ('--band', '0', "--band wants px (over 0) or auto, got '0'"), ('--band', '-3', "--band wants px (over 0) or auto"),
    ('--band', 'wide', "--band wants px (over 0) or auto, got 'wide'"), ('--band', 'nan', "--band wants px (over 0) or auto"),
    ('--band-margin', '-1', "--band-margin wants px (at least 0) or auto, got '-1'"),
    ('--band-up', '0', "--band-up wants FLAT[,PX|auto]"), ('--band-up', '1.5', "--band-up wants FLAT[,PX|auto]"),
    ('--band-up', '0.4,0', "--band-up wants px (over 0) or auto, got '0'"), ('--band-up', 'top', "--band-up wants FLAT[,PX|auto]")])
def test_a_band_out_of_range_is_refused_by_the_command_line(tmp_path, capsys, flag, value, said):
    loop_dir = strip_dir(tmp_path/'loop')
    with pytest.raises(SystemExit) as refused:
        cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--band', 'auto', flag, value])
    assert refused.value.code == 2
    assert f'argument {flag}: {said}' in capsys.readouterr().err


# ---------------------------------------------------------------- what moves and what does not

def test_the_flat_part_moves_whole_and_past_the_band_no_byte_changes(tmp_path):
    loop_dir, meta = cut(tmp_path, [frame(k) for k in range(N)])
    left, top = meta['source_rect'][:2]
    region = (TORSO[0]-left, TORSO[1]-top, 16.0, 20.0)
    record, answer = swayed(loop_dir, [region], band='auto', band_margin=2, on_fold='lower')  # the answer it moved the loop by
    strip, w = rgba(loop_dir/'walk.strip.png'), meta['w']
    _, band = auto(answer.reach_requested)
    flat, bands = (region[2]+2, region[3]+2, region[3]+2), (band, band, band)
    moved = 0
    for k in range(N):
        src, out = np.asarray(answer.cells[k]), strip[:, k*w:(k+1)*w]
        dx, dy = answer.dx[k], answer.dy[k]
        lay = answer.carry(k)
        inner, outer = rings(src.shape[:2], (region[0]+lay[0], region[1]+lay[1]), flat, bands)
        # Well inside the flat part, the region grown by the margin, the cell moves by its offset itself; past the band
        # (and a tenth of it, for the carry) every byte is as it was, the colour under clear pixels too.
        core, far = inner <= 0.9, outer >= 1.1
        assert np.array_equal(out[core], whole_move(answer.cells[k], dx, dy)[core]), k
        assert np.array_equal(out[far], src[far]), k
        moved += math.hypot(dx, dy) > 0
    assert moved > N//2
    banded = record['band']
    assert shapes(record) == [{'flat_px': list(flat), 'band_px': list(bands)}] and banded['margin_px'] == 2
    yy, xx = np.mgrid[0:meta['h'], 0:w].astype(np.float32)
    just_outside = just_inside_changed = 0
    for k in range(N):
        src, out = np.asarray(answer.cells[k]), strip[:, k*w:(k+1)*w]
        shift = banded['carry']['cells'][k][0]['px']
        inner, outer = rings(src.shape[:2], (region[0]+shift[0], region[1]+shift[1]), flat, bands)
        # Past the band's own rim, where the region is carried, no byte changes; inside it the weight is over 0.
        assert np.array_equal(src[outer >= 1], out[outer >= 1]), k
        just_outside += int(((outer >= 1) & (outer < 1.1)).sum())
        just_inside_changed += int(((outer < 1) & (outer > 0.85) & (src != out).any(axis=-1)).sum())
        (inside, move_x, move_y), _ = answer.band_field(k, xx, yy)
        assert not inside[outer >= 1].any() and inside[outer < 0.999].all()
        # The flat part moves by the offset itself, and just past it less.
        dx, dy = answer.dx[k], answer.dy[k]
        assert np.all(move_x[inner <= 1] == dx) and np.all(move_y[inner <= 1] == dy), k
        if math.hypot(dx, dy) > 0:
            assert np.all(np.hypot(move_x, move_y)[(inner > 1.02) & (outer < 1)] < math.hypot(dx, dy)), k
    assert just_outside > 0 and just_inside_changed > 0
    assert banded['ledger']['outside_moved_px_max'] > 0


def test_a_region_is_carried_by_its_own_pixels_where_the_part_slides_on_the_body(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop', sliding(slide))
    answer = answer_of(loop_dir, [CHEST], band='auto')
    assert np.allclose(answer.carry(5), body_sway(5), atol=0.5)  # the body's lay does not see the part slide
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--band', 'auto']) == 0
    moved = cells(loop_dir/'walk.strip.png')
    for k in range(N):
        # The part moves whole where it is: every pixel of it is the cell moved by the cell's offset.
        sx, sy = body_sway(k)
        ex, ey = slide(k)
        inner, _ = rings((H, W), (CHEST[0]+sx+ex, CHEST[1]+sy+ey), (14.0, 12.0, 12.0), (1, 1, 1))
        assert np.array_equal(moved[k][inner <= 1], whole_move(answer.cells[k], answer.dx[k], answer.dy[k])[inner <= 1]), k
    band = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['band']
    assert band['lay'] == [] and band['carry']['search_px'] == SEARCH
    ex0, ey0 = slide(0)
    for k, (entry,) in enumerate(band['carry']['cells']):
        if k == 0:
            assert entry == {'px': [0.0, 0.0], 'from': 'base'}
            continue
        sx, sy = body_sway(k)
        ex, ey = slide(k)
        assert entry['from'] == 'pixels' and entry['curvature'][0] > 0 and entry['curvature'][1] > 0, k
        assert abs(entry['px'][0]-(sx+ex-ex0)) < 0.1 and abs(entry['px'][1]-(sy+ey-ey0)) < 0.1, (k, entry)


@pytest.mark.slow
def test_cloth_with_no_pattern_is_carried_by_the_lay_and_a_warning_names_each_cell(tmp_path, capsys):
    loop_dir = strip_dir(tmp_path/'loop', sliding(lambda k: (0, 0), part=TORSO_COLOUR))
    lays = [[round(float(v), 4)+0.0 for v in answer_of(loop_dir, [CHEST]).carry(k)] for k in range(N)]
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--band', 'auto']) == 0
    assert lay_warning(CHEST, [(k, EDGE) for k in range(1, N)]) in capsys.readouterr().err
    band = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['band']
    assert band['lay'] == [{'cell': k, 'region': 0, 'why': EDGE} for k in range(1, N)]
    for k in range(1, N):
        entry = band['carry']['cells'][k][0]
        assert entry['from'] == 'lay' and entry['px'] == lays[k] and entry['curvature'] is None


def test_a_part_that_slides_further_from_the_lay_than_the_search_is_found_where_the_cell_before_had_it(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop', sliding(ramp))
    answer = answer_of(loop_dir, [CHEST], band='auto')
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--band', 'auto']) == 0
    moved = cells(loop_dir/'walk.strip.png')
    for k in range(N):
        # 12 px from the lay and moved whole all the same: each cell is looked at around where the cell before had it.
        sx, sy = body_sway(k)
        ex, ey = ramp(k)
        inner, _ = rings((H, W), (CHEST[0]+sx+ex, CHEST[1]+sy+ey), (14.0, 12.0, 12.0), (1, 1, 1))
        assert np.array_equal(moved[k][inner <= 1], whole_move(answer.cells[k], answer.dx[k], answer.dy[k])[inner <= 1]), k
    band = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['band']
    assert band['lay'] == []
    for k, (entry,) in enumerate(band['carry']['cells'][1:], 1):
        sx, sy = body_sway(k)
        ex, ey = ramp(k)
        assert entry['from'] == 'pixels' and abs(entry['px'][0]-(sx+ex)) < 0.1 and abs(entry['px'][1]-(sy+ey)) < 0.1, (k, entry)


def test_a_part_further_than_the_search_from_the_cell_before_is_carried_by_the_lay(tmp_path, capsys):
    loop_dir = strip_dir(tmp_path/'loop', sliding(lambda k: (0, 12) if k == 5 else (0, 0)))
    lay5 = [round(float(v), 4)+0.0 for v in answer_of(loop_dir, [CHEST]).carry(5)]
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--band', 'auto']) == 0
    edge = f"the least is on the search's edge, {SEARCH} px from where cell 4 had it"
    assert lay_warning(CHEST, [(5, edge)]) in capsys.readouterr().err
    band = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['band']
    assert band['lay'] == [{'cell': 5, 'region': 0, 'why': edge}] and band['carry']['cells'][5][0]['px'] == lay5
    # The cell after is looked at around its own lay again, and found.
    assert all(cell[0]['from'] == 'pixels' for k, cell in enumerate(band['carry']['cells']) if k not in (0, 5))


def test_cloth_dotted_every_4_px_is_a_tie_and_carried_by_the_lay(tmp_path, capsys):
    # One place of the dots looks as another 4 px away does, and none of those is on the search's edge.
    loop_dir = strip_dir(tmp_path/'loop', sliding(lambda k: (0, 0), dots=True))
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--band', 'auto']) == 0
    assert lay_warning(CHEST, [(k, TIE) for k in range(1, N)]) in capsys.readouterr().err
    band = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['band']
    assert band['lay'] == [{'cell': k, 'region': 0, 'why': TIE} for k in range(1, N)]
    assert all(cell[0]['least'] == 0 for cell in band['carry']['cells'][1:])  # the dots are found, in more than one place


# ---------------------------------------------------------------- the ledger

def test_the_ledger_counts_each_cell_and_a_warning_says_what_is_in_it(tmp_path, capsys):
    loop_dir = strip_dir(tmp_path/'loop')
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--band', 'auto',
                     '--band-up', '0.4']) == 0
    err = capsys.readouterr().err
    assert '— follow.band.ledger.band_on_regions_px' in err and '— follow.band.ledger.outside_moved_px' in err
    record = json.loads((loop_dir/'walk.strip.json').read_text())['follow']
    ledger = record['band']['ledger']
    for key in ('band_on_regions_px', 'pressed_px', 'stretched_px', 'outside_moved_px'):
        assert len(ledger[key]) == N and ledger[f'{key}_max'] == max(ledger[key])
        if ledger[f'{key}_max']:
            top = ledger[key].index(ledger[f'{key}_max'])
            cells_in = sum(1 for count in ledger[key] if count)
            assert f'in {cells_in} of {N} cells, up to {ledger[key + "_max"]} px (cell {top}' in err and f'follow.band.ledger.{key}' in err
        else:
            assert f'follow.band.ledger.{key}' not in err
    assert ledger['band_on_regions_px_max'] > 0 and ledger['outside_moved_px_max'] > 0
    for k in range(N):
        for key, area, beyond in (('pressed', 'area_least', lambda a: a < 0.85), ('stretched', 'area_most', lambda a: a > 1.15)):
            assert (ledger[f'{key}_px'][k] > 0) == (ledger[f'{key}_box'][k] is not None), k
            if ledger[f'{key}_px'][k]:
                x0, y0, x1, y1 = ledger[f'{key}_box'][k]
                a, x, y = ledger[area][k]
                assert beyond(a) and x0 <= x <= x1 and y0 <= y <= y1, k
    still = [k for k in range(N) if record['dx_px'][k] == 0 and record['dy_px'][k] == 0]
    for k in still:
        assert ledger['jacobian_min'][k] == 1.0 and ledger['pressed_px'][k] == ledger['outside_moved_px'][k] == 0


# ---------------------------------------------------------------- the same band on every path

@pytest.mark.slow
def test_the_frames_are_banded_as_the_strip_cut_from_them(tmp_path):
    src = frames_dir(tmp_path/'cycle-frames')
    frames = [frame(k) for k in range(N)]
    loop_dir, meta = cut(tmp_path, frames)
    left, top = meta['source_rect'][:2]
    region = (TORSO[0]-left, TORSO[1]-top, 16.0, 20.0)
    args = ['video-sway', '--frames', str(src), '--region', text((TORSO[0], TORSO[1], 16.0, 20.0)), '--fps', '24', '--on-fold', 'lower']
    assert cli.main([*args, '--out', str(tmp_path/'plain')]) == 0
    assert cli.main([*args, '--out', str(tmp_path/'swayed'), '--band', 'auto', '--band-up', '0.4']) == 0
    out = tmp_path/'swayed'
    assert sum(rgba(out/name).tobytes() != rgba(tmp_path/'plain'/name).tobytes() for name in FILES) > N//2
    strip_record = sway.sway_loop(loop_dir, [region], on_fold='lower', band='auto', band_up=(0.4, 'auto'))
    strip, w = rgba(loop_dir/'walk.strip.png'), meta['w']
    for k, name in enumerate(FILES):
        with Image.open(out/name) as im:
            cell = as_strip_cell(im.convert('RGBA'), meta['source_rect'])
        assert np.array_equal(cell, strip[:, k*w:(k+1)*w]), f'cell {k} differs from the strip path'
    record = json.loads((out/'sway.json').read_text())
    for key in ('gain', 'dx_px', 'dy_px', 'reach_px', 'fold', 'carry_basis'):
        assert record[key] == strip_record[key], key
    # One answer, but for the ledger's places, which are each one's own pixels (a frame's, a cell's of the strip).
    assert {k: v for k, v in record['band'].items() if k != 'ledger'} == {k: v for k, v in strip_record['band'].items() if k != 'ledger'}


@pytest.mark.slow
def test_a_turned_loop_banded_on_the_cuts_reading_is_the_cuts_banded_sway_cell_for_cell(origin, tmp_path):
    o, read_on, followed = origin
    banded = sway.sway_loop(shutil.copytree(o, tmp_path/'banded'), [chest(o)], on_fold='lower', band='auto')
    loop_dir = aligned(o, tmp_path/'turned')
    turned = meta_of(loop_dir)['cycle_align']['turned_by']
    result = sway.sway_loop(loop_dir, [chest(o)], on_fold='lower', read_on=read_on, band='auto')
    moved, reference, plain = cells_of(loop_dir), cells_of(tmp_path/'banded'), cells_of(followed)
    n = len(reference)
    assert sum(not np.array_equal(reference[k], plain[k]) for k in range(n)) > n//2  # the band moves the part otherwise
    assert all(np.array_equal(moved[k], reference[(k+turned) % n]) for k in range(n))
    assert result['read_on']['relation'] == 'transported' and result['carry_basis'] == OWN_PIXELS
    assert (result['band']['band_px'], result['band']['margin_px']) == (banded['band']['band_px'], banded['band']['margin_px'])
    cut_carry, turned_carry = banded['band']['carry']['cells'], result['band']['carry']['cells']
    assert [turned_carry[k][0]['px'] for k in range(n)] == [cut_carry[(k+turned) % n][0]['px'] for k in range(n)]
