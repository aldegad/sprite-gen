"""`video-sway --band --hold-mask`: pixels a band moves round and leaves as they are.

A mask drawn on the cell the regions were read on is carried into each cell by its own pixels, and there it, the pixels
not opaque round it and the clear pixels beside it keep their bytes, whatever part of the figure it holds: an arm that
hangs over the moving part, a belt below it. Every other pixel is read as the band reads it, but a read headed into what
is kept stops short, its share of the move rising by HOLD_SLOPE a pixel, so the gap the part leaves is drawn from the part
in order and no read takes the source backwards — even where the band's own move leaves a pixel almost none of its area.
Synthetic figures only: the inspection's body with a striped arm over its left side, a half-clear edge beside the arm, a
clear notch at the waist under it, a belt across its hips and a patch of the torso's own cloth; and the read-on test's
runner, cut and turned, a mask on its chest's rim.

Each case first reads what a sway writes with and without the mask (its cells, whether it refuses, what it says), so on
a release without the mask the same input fails on what comes out; the hold's own record and ledger are read after.
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

from tests.video.test_follow_inspect import CHEST, N, TORSO, W, H, figure, strip_dir, sway as body_sway
from tests.video.test_follow_read_on import aligned, cells_of as cut_cells, chest as cut_chest, meta_of, origin, origin_once  # noqa: F401 (fixtures)
from tests.video.test_sway_band import swayed

POLICY = 'own-pixels-grown/kept-edge-air/read-short-sloped/1'
ARM = [(150, 60, 40), (120, 40, 25), (180, 90, 60)]  # an arm's fur in stripes 2 px high, redder to the right
ARM_BOX = (28, 50, 40, 92)  # x0, y0, x1, y1 in cell 0, the last pixel's included: over the torso's left side and past it
SOFT = (150, 60, 40, 160)  # a half-clear column beside the arm, over the torso
NOTCH = (35, 93, 37, 98)  # the waist's notch under the arm: clear, its right column half clear
BELT_BOX = (35, 100, 85, 108)  # a belt across the hips, a colour of its own each column
PATCH_BOX = (74, 94, 80, 97)  # a patch of the torso's own cloth: nothing tells one place of it from the next
FILES = [f'frame-{k:03d}.png' for k in range(N)]


def text(region):
    return ','.join(f'{v:g}' for v in region)


def arm_colour(x, y):
    base = ARM[(y // 2) % 3]
    return base[0] + 3*(x - ARM_BOX[0]), base[1], base[2]


def belt_colour(x, y):
    return 90 + (x - BELT_BOX[0]), 30 + 10*((y - BELT_BOX[1]) % 3), 140


def held_figure(k, n=N):
    """The inspection's body with an arm, its half-clear edge, the waist's notch and a belt, all carried with the body."""
    sx, sy = body_sway(k, n)
    im = figure(k, n)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = ARM_BOX
    for y in range(y0, y1+1):
        for x in range(x0, x1+1):
            d.point((x+sx, y+sy), fill=(*arm_colour(x, y), 255))
    d.line((x1+1+sx, y0+sy, x1+1+sx, y1+sy), fill=SOFT)
    nx0, ny0, nx1, ny1 = NOTCH
    d.rectangle((nx0+sx, ny0+sy, nx1-1+sx, ny1+sy), fill=(0, 0, 0, 0))
    d.line((nx1+sx, ny0+sy, nx1+sx, ny1+sy), fill=(*TORSO[:3], 190))
    bx0, by0, bx1, by1 = BELT_BOX
    for y in range(by0, by1+1):
        for x in range(bx0, bx1+1):
            d.point((x+sx, y+sy), fill=(*belt_colour(x, y), 255))
    return im


def boxed(box, shift=(0, 0)):
    """A cell's pixels in a box (x0, y0, x1, y1, the last pixel's included), moved by `shift`."""
    m = np.zeros((H, W), bool)
    x0, y0, x1, y1 = box
    m[y0+shift[1]:y1+1+shift[1], x0+shift[0]:x1+1+shift[0]] = True
    return m


def mask_png(path, *boxes):
    """A hold mask drawn on cell 0: opaque white over the boxes, clear elsewhere."""
    m = np.zeros((H, W), bool)
    for box in boxes:
        m |= boxed(box)
    Image.fromarray(np.where(m[..., None], 255, 0).astype(np.uint8).repeat(4, axis=2), 'RGBA').save(path)
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


def grow(mask, px):
    """`mask` grown by `px` px every way (8-neighbour steps)."""
    for _ in range(px):
        p = np.pad(mask, 1)
        mask = np.logical_or.reduce([p[dy:dy+H, dx:dx+W] for dy in range(3) for dx in range(3)])
    return mask


def arm_like(rgb):
    """Pixels whose colour is within 25 of the arm's fur: each colour the cell holds measured once, not once a pixel."""
    fur = np.array([arm_colour(x, y) for y in range(6) for x in range(ARM_BOX[0], ARM_BOX[2]+1)], np.float64)
    colours, where = np.unique(rgb[..., :3].reshape(-1, 3), axis=0, return_inverse=True)
    d = np.min(np.linalg.norm(colours[:, None, :].astype(np.float64) - fur, axis=-1), axis=-1)
    return (d < 25)[where.reshape(-1)].reshape(rgb.shape[:2])


def folded_reads(drawn, read):
    """A cell's opaque pixels, neither kept nor beside a kept one, whose read takes the source backwards: the area each
    takes, read off the move each read took (`held_cell`'s: the cell drawn, how each pixel was read), at 0 or under."""
    kept = read['held'] | read['air']
    p = np.pad(kept, 1)
    beside = kept | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    rx, ry = read['read_x'], read['read_y']
    area = (1 - (rx[1:-1, 2:] - rx[1:-1, :-2])/2) * (1 - (ry[2:, 1:-1] - ry[:-2, 1:-1])/2) \
        - (rx[2:, 1:-1] - rx[:-2, 1:-1])/2 * (ry[1:-1, 2:] - ry[1:-1, :-2])/2
    counted = (np.asarray(drawn)[..., 3] >= 128) & ~beside
    return int((counted[1:-1, 1:-1] & (area <= 0)).sum()), float(area[counted[1:-1, 1:-1]].min()) if counted[1:-1, 1:-1].any() else 1.0


@pytest.fixture(scope='module')
def banded_once(tmp_path_factory):
    """The held figure swayed with --band auto and no hold mask (`hold_mask=None`, as one left out), made once for the
    module: three cases read that same sway (`swayed`: its loop directory, which no case writes, its record and the answer
    it moved the loop by)."""
    loop_dir = strip_dir(tmp_path_factory.mktemp('banded')/'loop', held_figure)
    return (loop_dir, *swayed(loop_dir, [CHEST], band='auto', hold_mask=None))


@pytest.fixture(scope='module')
def held_once(tmp_path_factory):
    """The same with the arm's and the belt's mask, made once for the module for two cases: as `banded_once`, and the
    mask."""
    root = tmp_path_factory.mktemp('held')
    mask, loop_dir = mask_png(root/'mask.png', ARM_BOX, BELT_BOX), strip_dir(root/'loop', held_figure)
    return (loop_dir, *swayed(loop_dir, [CHEST], band='auto', hold_mask=mask), mask)


# ---------------------------------------------------------------- off, nothing of it

def test_without_a_hold_mask_the_band_is_as_ever(banded_once, tmp_path):
    plain = strip_dir(tmp_path/'plain', held_figure)
    assert cli.main(['video-sway', '--loop-dir', str(plain), '--region', text(CHEST), '--band', 'auto']) == 0
    asked, _, _ = banded_once  # asked by the API with hold_mask=None
    assert (plain/'walk.strip.png').read_bytes() == (asked/'walk.strip.png').read_bytes()
    assert 'hold' not in json.loads((plain/'walk.strip.json').read_text())['follow']


# ---------------------------------------------------------------- what is held

def test_what_the_mask_holds_its_soft_edge_and_the_air_keep_their_bytes_in_every_cell(banded_once, held_once, tmp_path):
    plain_dir, _, plain_answer = banded_once  # the band without the mask, and the answer it moved the loop by
    held_dir, record, _, mask = held_once  # with the arm's and the belt's mask
    source, plain, held = cells_of(strip_dir(tmp_path/'source', held_figure)), cells_of(plain_dir), cells_of(held_dir)
    arm_moved = arm_new_plain = 0
    for k in range(N):
        shift = body_sway(k)
        arm, belt = boxed(ARM_BOX, shift), boxed(BELT_BOX, shift)
        edge, rim = boxed((ARM_BOX[2]+1, ARM_BOX[1], ARM_BOX[2]+1, ARM_BOX[3]), shift), boxed((NOTCH[2], NOTCH[1], NOTCH[2], NOTCH[3]), shift)
        soft, notch = edge | rim, boxed((NOTCH[0], NOTCH[1], NOTCH[2]-1, NOTCH[3]), shift)
        assert (source[k][..., 3][edge] == SOFT[3]).all() and (source[k][..., 3][rim] == 190).all(), k
        assert (source[k][..., 3][notch] == 0).all(), k
        for what, where in (('arm', arm), ('belt', belt), ('soft edge', soft), ('air in the notch', notch)):
            assert np.array_equal(held[k][where], source[k][where]), (k, what)
        arm_moved += not np.array_equal(plain[k][arm], source[k][arm])
        # No fur is drawn off the arm, and no pixel solid by the arm (2 px round it, off it and what it keeps) is left clear.
        off, source_fur = ~arm & ~soft, arm_like(source[k])  # the source's fur, read once for both cells
        new = lambda cell: arm_like(cell) & (cell[..., 3] >= 128) & ~source_fur & off
        arm_new_plain += int(new(plain[k]).sum())
        assert not new(held[k]).any(), k
        near = grow(arm, 2) & ~arm & ~soft & ~notch & (source[k][..., 3] >= 128)
        assert (held[k][..., 3][near] >= 128).all(), k
    assert arm_moved > N//2 and arm_new_plain > 0  # without the mask the band moves the arm and draws its fur on the chest
    hold = record['hold']
    assert (hold['policy'], hold['asked'], hold['grow_px']) == (POLICY, {'per_cell': False, 'sha256': hashlib.sha256(mask.read_bytes()).hexdigest()}, 3)
    assert hold['edge_px'] == math.ceil(plain_answer.reach) + 2
    assert [piece['px'] for piece in hold['pieces']] == [int(boxed(ARM_BOX).sum()), int(boxed(BELT_BOX).sum())]
    assert [piece['box'] for piece in hold['pieces']] == [list(ARM_BOX), list(BELT_BOX)]
    carry = hold['carry']['cells']
    assert carry[0] == [{'px': [0.0, 0.0], 'from': 'base'}]*2
    for k in range(1, N):  # the arm and the belt are each found by their own pixels, where the body carries them
        assert [entry['from'] for entry in carry[k]] == ['pixels', 'pixels'], k
        for entry in carry[k]:
            assert np.allclose(entry['px'], body_sway(k), atol=0.5), k
    assert hold['lay'] == [] and max(hold['ledger']['short_px']) > 0


@pytest.mark.slow
def test_pixels_no_read_round_the_mask_comes_near_are_the_bands_byte_for_byte(banded_once, tmp_path):
    mask = mask_png(tmp_path/'mask.png', ARM_BOX)
    plain_dir, _, _ = banded_once  # the band without the mask
    held_dir = strip_dir(tmp_path/'held', held_figure)
    _, answer = swayed(held_dir, [CHEST], band='auto', hold_mask=mask)  # the answer it moved the loop by
    plain, held = cells_of(plain_dir), cells_of(held_dir)
    assert sum(not np.array_equal(held[k], plain[k]) for k in range(N)) > N//2  # the mask holds what the band moved
    # A read is shortened only within reach / HOLD_SLOPE (and a pixel and the margin) of what is kept, every way (its
    # share, lowered to rise by HOLD_SLOPE a px at most, is never over HOLD_SLOPE times that far), and its share is rounded
    # over HOLD_ROUND px each way: past that, the cell is the band's.
    far = math.ceil(answer.reach/sway.HOLD_SLOPE) + 1 + sway.HOLD_MARGIN + 2*sway.HOLD_ROUND + 1
    beyond = 0
    for k in range(N):
        _, read = answer.held_cell(k)
        away = ~grow(read['held'] | read['air'], far)
        assert np.array_equal(held[k][away], plain[k][away]), k
        beyond += int((away & np.any(plain[k] != np.asarray(answer.cells[k]), axis=-1)).sum())
    assert beyond > 0  # the band moves pixels out there, and they are moved as without the mask


@pytest.mark.slow
def test_a_steep_band_beside_the_mask_is_read_in_order(tmp_path):
    # A band the move at gain 1.3 does not fold and the move at 2.5 does, past a margin of 2, at the gain the fold rule
    # lowers it to: the band's own move leaves a pixel little of its area, and the arm's right side lies in it. Where the
    # part leaves the arm the gap is drawn from the part, its read never taking the source backwards.
    mask = mask_png(tmp_path/'mask.png', ARM_BOX)
    per_gain = answer_of(strip_dir(tmp_path/'unit', held_figure), [CHEST], gain=1.0).reach_per_gain
    kw = dict(band=math.ceil(1.3*per_gain*math.pi/2), band_margin=2, on_fold='lower')
    held_dir = strip_dir(tmp_path/'held', held_figure)
    record, answer = swayed(held_dir, [CHEST], hold_mask=mask, **kw)  # and the answer it moved the loop by
    source, held = cells_of(strip_dir(tmp_path/'source', held_figure)), cells_of(held_dir)
    for k in range(N):
        arm = boxed(ARM_BOX, body_sway(k))
        assert np.array_equal(held[k][arm], source[k][arm]), k
    assert record['fold']['lowered'] and min(record['band']['ledger']['jacobian_min']) < 0.2
    short = 0
    for k in range(N):
        drawn, read = answer.held_cell(k)  # drawn once a cell, and read for both
        folded, least = folded_reads(drawn, read)
        assert folded == 0 and least > 0, (k, least)
        short += int((read['share'] < 1).sum())
    assert short > 0
    ledger = record['hold']['ledger']
    assert ledger['read_folded_px'] == [0]*N and ledger['read_folded_px_max'] == 0
    assert all(least is None or least[0] > 0 for least in ledger['read_least'])
    # Where a read turns from the whole move to its share round the mask the bound does not hold; the ledger reads it.
    assert ledger['read_turn_folded_px'] == [0]*N and any(least is not None for least in ledger['read_turn_least'])
    assert all(least is None or least[0] > 0 for least in ledger['read_turn_least'])


# ---------------------------------------------------------------- the same hold on every path

@pytest.mark.slow
def test_a_folder_of_the_masks_carried_holds_as_the_one_mask(held_once, tmp_path):
    one_dir, one, answer, _ = held_once  # the one mask, carried, and the answer the loop was moved by
    folder_dir = strip_dir(tmp_path/'folder', held_figure)
    source, held = cells_of(strip_dir(tmp_path/'source', held_figure)), cells_of(one_dir)
    for k in range(N):
        arm = boxed(ARM_BOX, body_sway(k))
        assert np.array_equal(held[k][arm], source[k][arm]), k
    masks = tmp_path/'masks'
    masks.mkdir()
    for k in range(N):
        held = answer.hold.mask_in(k)
        Image.fromarray(np.where(held, 255, 0).astype(np.uint8), 'L').save(masks/f'cell-{k:03d}.png')
    given = sway.sway_loop(folder_dir, [CHEST], band='auto', hold_mask=masks)
    assert (one_dir/'walk.strip.png').read_bytes() == (folder_dir/'walk.strip.png').read_bytes()
    assert given['hold']['asked']['per_cell'] and len(given['hold']['asked']['sha256']) == N
    assert given['hold']['carry'] is None and given['hold']['grow_px'] is None and given['hold']['pieces'] == []
    assert given['hold']['ledger'] == one['hold']['ledger']


@pytest.mark.slow
def test_the_frames_are_held_as_the_strip(tmp_path):
    mask = mask_png(tmp_path/'mask.png', ARM_BOX, BELT_BOX)
    frames = tmp_path/'frames'
    frames.mkdir()
    for k, name in enumerate(FILES):
        held_figure(k).save(frames/name)
    loop_dir = strip_dir(tmp_path/'loop', held_figure, delay_ms=41.67)  # a frame at --fps 24, as a strip's meta writes it
    assert cli.main(['video-sway', '--frames', str(frames), '--out', str(tmp_path/'out'), '--fps', '24', '--region', text(CHEST),
                     '--band', 'auto', '--hold-mask', str(mask)]) == 0
    strip_record = sway.sway_loop(loop_dir, [CHEST], band='auto', hold_mask=mask)
    strip = cells_of(loop_dir)
    for k, name in enumerate(FILES):
        out, arm = np.asarray(Image.open(tmp_path/'out'/name).convert('RGBA')), boxed(ARM_BOX, body_sway(k))
        assert np.array_equal(out[arm], np.asarray(held_figure(k))[arm]), k
        assert np.array_equal(out, strip[k]), k
    assert json.loads((tmp_path/'out'/'sway.json').read_text())['hold'] == strip_record['hold']


@pytest.mark.slow
def test_a_turned_loop_held_on_the_cuts_reading_is_the_cuts_held_sway_cell_for_cell(origin, tmp_path):
    # The mask is drawn on the cut's first cell, where the regions were read; the turned loop carries it from there.
    o, read_on, _ = origin
    cx, cy, rx, _ = cut_chest(o)
    rim = np.zeros(cut_cells(o)[0].shape[:2], bool)
    rim[round(cy) - 4:round(cy) + 5, round(cx - rx) - 2:round(cx - rx) + 7] = True  # the chest's left rim and just past it
    mask = tmp_path/'mask.png'
    Image.fromarray(np.where(rim, 255, 0).astype(np.uint8), 'L').save(mask)
    sway.sway_loop(shutil.copytree(o, tmp_path/'banded'), [cut_chest(o)], on_fold='lower', band='auto')
    held = sway.sway_loop(shutil.copytree(o, tmp_path/'held'), [cut_chest(o)], on_fold='lower', band='auto', hold_mask=mask)
    loop_dir = aligned(o, tmp_path/'turned')
    turned = meta_of(loop_dir)['cycle_align']['turned_by']
    result = sway.sway_loop(loop_dir, [cut_chest(o)], on_fold='lower', read_on=read_on, band='auto', hold_mask=mask)
    moved, reference, banded = cut_cells(loop_dir), cut_cells(tmp_path/'held'), cut_cells(tmp_path/'banded')
    n = len(reference)
    assert sum(not np.array_equal(reference[k], banded[k]) for k in range(n)) > n//2  # the mask holds what the band moves
    assert all(np.array_equal(moved[k], reference[(k+turned) % n]) for k in range(n))
    assert result['read_on']['relation'] == 'transported'
    cut_carry, turned_carry = held['hold']['carry']['cells'], result['hold']['carry']['cells']
    assert [[entry['px'] for entry in turned_carry[k]] for k in range(n)] == [[entry['px'] for entry in cut_carry[(k+turned) % n]] for k in range(n)]


# ---------------------------------------------------------------- refused, and said

def test_what_a_mask_is_refused_for_leaves_the_loop_as_it_was(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop', held_figure)
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    small = tmp_path/'small.png'
    Image.new('RGBA', (W-1, H), (255, 255, 255, 255)).save(small)
    empty = tmp_path/'empty.png'
    Image.new('RGBA', (W, H)).save(empty)
    broken = tmp_path/'broken.png'
    broken.write_bytes(b'not a png')
    few = tmp_path/'few'
    few.mkdir()
    for k in range(3):
        Image.new('L', (W, H), 255).save(few/f'{k}.png')
    nothing = tmp_path/'nothing'
    nothing.mkdir()
    mask = mask_png(tmp_path/'mask.png', ARM_BOX)
    for args, said in (
            (['--hold-mask', str(mask)], r'--hold-mask keeps pixels as they are while a band moves its regions round them; it needs --band$'),
            (['--band', 'auto', '--hold-mask', str(small)], rf'--hold-mask is {W-1}x{H}, the cells are {W}x{H}: a mask is drawn on a cell'),
            (['--band', 'auto', '--hold-mask', str(empty)], r'holds no pixel \(alpha 128 or more'),
            (['--band', 'auto', '--hold-mask', str(broken)], r'broken.png: not read'),
            (['--band', 'auto', '--hold-mask', str(few)], rf'--hold-mask holds 3 masks and there are {N} cells: a folder gives one mask per cell'),
            (['--band', 'auto', '--hold-mask', str(nothing)], r'a folder of masks with no PNG in it$')):
        with pytest.raises(SystemExit, match=said) as refused:
            cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), *args])
        assert refused.value.code not in (0, None), args
        assert (loop_dir/'walk.strip.png').read_bytes() == as_cut and 'follow' not in json.loads((loop_dir/'walk.strip.json').read_text())


def test_a_piece_on_cloth_with_no_pattern_is_carried_by_the_lay_and_the_record_and_the_warnings_say_so(tmp_path, capsys):
    mask = mask_png(tmp_path/'mask.png', ARM_BOX, PATCH_BOX)
    loop_dir = strip_dir(tmp_path/'loop', held_figure)
    source = cells_of(strip_dir(tmp_path/'source', held_figure))
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(CHEST), '--band', 'auto', '--hold-mask', str(mask)]) == 0
    out = capsys.readouterr()
    held = cells_of(loop_dir)
    for k in range(N):  # carried by the lay, which is where the body carries it here: held all the same
        patch = boxed(PATCH_BOX, body_sway(k))
        assert np.array_equal(held[k][patch], source[k][patch]), k
    hold = json.loads((loop_dir/'walk.strip.json').read_text())['follow']['hold']
    lay = hold['lay']
    assert lay and {entry['piece'] for entry in lay} == {1}
    whys = '; '.join(f"cell {e['cell']}: {e['why']}" for e in lay)
    assert (f"--hold-mask carries its piece 1 by the body's lay in {len(lay)} cells, its own pixels not found surely ({whys}) "
            "— follow.hold.lay") in out.err
    summary = json.loads(out.out)
    assert summary['hold'] == {'pieces': 2, 'lay_cells': sorted({e['cell'] for e in lay}), 'read_folded_px_max': 0}
    ledger = hold['ledger']
    for key in ('held_px', 'soft_px', 'air_px', 'short_px', 'renormalised_px', 'walked_px', 'read_folded_px', 'read_folded_edge_px',
                'read_turn_folded_px'):
        assert len(ledger[key]) == N and ledger[f'{key}_max'] == max(ledger[key]), key
    assert len(ledger['read_least']) == len(ledger['read_turn_least']) == N
    assert min(ledger['held_px']) > int(boxed(ARM_BOX).sum()) and min(ledger['soft_px']) > 0 and min(ledger['air_px']) > 0
    for key in ('renormalised_px', 'walked_px', 'read_folded_px'):
        assert (f'follow.hold.ledger.{key}' in out.err) == (ledger[f'{key}_max'] > 0), key
