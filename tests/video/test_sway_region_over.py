"""`video-sway --region-mask` with `--region-over`, `--region-over-solid`, `--region-rim-round`, `--region-membrane` and
its four keys, `--region-shear`, and the crease the wear wears in and the layer reaches down to again.

`--region-over` lays the region over the figure under it: what lies under its lower edge is no anchor of the rim, so the
lower edge moves with the rest; the band of it within the air's reach is drawn over by the region's own pixels by their
alpha and keeps its bytes where they do not land; the air and where the region lifts are drawn by the move, the figure
under it read up there; its outline against the air goes with it and leaves the air behind, its outline against the
figure stays; `--hold-mask` names the pixels it leaves out (an arm): those the region mask covers stay the region's and
move with it, the rest are never moved and never drawn over (on a band, every one of them is held). `--region-over-solid`
lays it over the solid pixels under its lower edge alone: the rest there stays and holds the rim, and its soft edge goes
with it beside the figure under it too. `--region-rim-round` holds the rim round beside it, so below the corner where the
figure beside it stops over what lies under its lower edge the move rises down as gently as across. `--region-membrane N`
moves what lies under its lower edge within N + 1 px of it, none of what it leaves out, as a membrane between the
region's move and none on the rest of the figure (each pixel the mean of its figure neighbours' to within a pass's
MEMBRANE_STEP), drawn by that move instead of laid over; with it, `--region-membrane-unpin` makes what the membrane moves
no anchor of the rim, `--region-clear-alpha A` reads a pixel under alpha A as clear, `--region-floor-beside N` sets the
lower edge beside the region by its lowest pixel within N columns of each side, and `--region-own-outline G` grows the
region G px over the outline its mask leaves out; without it they shape nothing. `--region-shear S` widens the rim so a
move one way rises across it the other way by at most S px a px. With either, the ledger reads the steps the drawn move
takes, the strip a region drawn short of its side leaves standing, and its tip. `--region-crease` with `--region-shrink`:
the wear wears the crease in with the rest of the mask, and laid over the figure the region reaches down to the crease
again in every cell. Synthetic figures only: the region test's body and its striped part over the torso's right side and
past it over the air, a dark crease under it on the torso; an arm block beside the part's lower left; a dark outline
under the part over the air; a piece of the arm's colour right under the part's lower edge, or inside the part; a few
pixels not opaque and a near-clear speck under the part's lower edge over the air; a pixel or two of the part's outline
over the air past its mask, in a row that changes from cell to cell.

Each case first reads what a sway writes with the new keys (its cells, its record), so on a release without them the
same input fails on what comes out.
"""
import json
import math

import numpy as np
import pytest
from PIL import Image
from sprite_gen import cli
from sprite_gen.video import sway

from tests.video.test_follow_inspect import N, W, H, strip_dir, sway as body_sway
from tests.video.test_sway_band import swayed
from tests.video.test_sway_region import (BOTTOM, CREASE, PART, SOFT, answer_of, cells_of, mask_png, part_at, part_figure,
                                          part_swayed, text)

ARM = (60, 200, 90, 255)
ARM_BOX = (42, 78, 49, 104)  # x0, y0, x1, y1 in cell 0, last included: beside the part's lower left, on the torso
OUTLINE = (40, 20, 30, 255)


def arm_at(k):
    """The arm's pixels in cell k: carried with the body."""
    sx, sy = body_sway(k)
    yy, xx = np.mgrid[0:H, 0:W]
    x0, y0, x1, y1 = ARM_BOX
    return (xx >= x0+sx) & (xx <= x1+sx) & (yy >= y0+sy) & (yy <= y1+sy)


def arm_figure(k, n=N):
    """The region test's body and part, and an arm block beside the part's lower left, over the torso."""
    a = np.asarray(part_figure(k, n)).copy()
    a[arm_at(k)] = ARM
    return Image.fromarray(a, 'RGBA')


def outline_at(k):
    """A dark outline row under the part where it hangs over the air (past the torso's soft side)."""
    sx, sy = body_sway(k)
    yy, xx = np.mgrid[0:H, 0:W]
    below = np.roll(part_at(k), 1, axis=0) & ~part_at(k)
    return below & (yy == BOTTOM+1+sy) & (xx > 86+sx)


def outlined_figure(k, n=N):
    a = np.asarray(arm_figure(k, n)).copy()
    a[outline_at(k)] = OUTLINE
    return Image.fromarray(a, 'RGBA')


def arm_masks(path, at=arm_at):
    """The arm (or `at`'s piece) in every cell, a folder of one PNG per cell, held as given."""
    path.mkdir()
    for k in range(N):
        mask_png(path/f'cell-{k:03d}.png', at(k))
    return path


UNDER_BOX = (56, BOTTOM+1, 64, BOTTOM+10)  # x0, y0, x1, y1 in cell 0, last included: right under the part's lower edge


def under_at(k):
    """A piece of the arm's colour right under the part's lower edge in cell k, carried with the body."""
    sx, sy = body_sway(k)
    yy, xx = np.mgrid[0:H, 0:W]
    x0, y0, x1, y1 = UNDER_BOX
    return (xx >= x0+sx) & (xx <= x1+sx) & (yy >= y0+sy) & (yy <= y1+sy)


def under_figure(k, n=N):
    a = np.asarray(part_figure(k, n)).copy()
    a[under_at(k)] = ARM
    return Image.fromarray(a, 'RGBA')


def lower_rows(region, solid, columns, rows=3):
    """The region's lowest `rows` pixels in each of `columns` whose lowest one has a solid pixel under it (on the figure)."""
    out = np.zeros_like(region)
    for x in np.nonzero(region.any(axis=0))[0]:
        if x not in columns:
            continue
        ys = np.nonzero(region[:, x])[0]
        if ys.max()+1 < H and solid[ys.max()+1, x]:
            out[ys[-rows:], x] = True
    return out


def share_of(answer, k, where):
    """The mean move over `where` in cell k over the cell's offset."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    field, _ = answer.mask_field(k, xx, yy)
    offset = math.hypot(answer.dx[k], answer.dy[k])
    return float(np.hypot(field[1], field[2])[where].mean()/offset)


def reads_wholly_on(field, where, any_tap=False):
    """The pixels whose read at the move (`field`: covered, across, down) takes its four taps on `where` (with `any_tap`,
    one at least of those it weighs)."""
    yy, xx = np.mgrid[0:H, 0:W]
    rx, ry = xx-field[1], yy-field[2]
    x0, y0 = np.clip(np.floor(rx).astype(int), 0, W-2), np.clip(np.floor(ry).astype(int), 0, H-2)
    fx, fy = rx-np.floor(rx), ry-np.floor(ry)
    taps = [(where[y0+oy, x0+ox], weight > 0) for oy, ox, weight in
            ((0, 0, (1-fx)*(1-fy)), (0, 1, fx*(1-fy)), (1, 0, (1-fx)*fy), (1, 1, fx*fy))]
    if any_tap:
        return np.logical_or.reduce([on & weighed for on, weighed in taps])
    return np.logical_and.reduce([on for on, _ in taps])


RISE = 8  # rows over the lower rows the part's own move is read for them to be set against


def lifting(answer, least=1.0):
    """The cells whose part lifts by `least` px or more."""
    return [k for k in range(N) if answer.dy[k] <= -least]


GENTLE = 0.9  # the gain of a move of about 2.2 px, the size a membrane a few px long is for: the default's 6.2 px folds it


# Sways several cases read, each made once for the module (`part_swayed`: the part figure, a mask of the part; its loop
# directory, which no case writes, its record and the answer it moved the loop by).

@pytest.fixture(scope='module')
def layer_once(tmp_path_factory):
    """Laid over the figure with the crease, the mask as drawn: four cases read this layer."""
    return part_swayed(tmp_path_factory.mktemp('layer'), region_shrink=0, region_crease=True, region_over=True)


@pytest.fixture(scope='module')
def gentle_layer_once(tmp_path_factory):
    """The same layer at the gentle gain, the one each membrane reach is set against."""
    return part_swayed(tmp_path_factory.mktemp('gentle'), gain=GENTLE, region_shrink=0, region_crease=True, region_over=True)


@pytest.fixture(scope='module')
def unsheared_once(tmp_path_factory):
    """At gain 1.25, the mask as drawn, a shear no move reaches (the ledger reads the edges; the rim as ever): the one each
    shear is set against."""
    return part_swayed(tmp_path_factory.mktemp('unsheared'), gain=1.25, region_shrink=0, region_shear=1e6)


# ---------------------------------------------------------------- off, nothing of them

@pytest.mark.slow
def test_without_the_new_keys_nothing_of_them_is_read_or_written(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    as_ever, asked_off = strip_dir(tmp_path/'as-ever', arm_figure), strip_dir(tmp_path/'off', arm_figure)
    record = sway.sway_loop(as_ever, [PART], region_mask=mask, band_up=(0.4, 'auto'), on_fold='lower')
    off = sway.sway_loop(asked_off, [PART], region_mask=mask, band_up=(0.4, 'auto'), on_fold='lower', region_over=False,
                         region_shear=None)
    assert (as_ever/'walk.strip.png').read_bytes() == (asked_off/'walk.strip.png').read_bytes()
    assert off['region_mask'] == record['region_mask'] and off['fold'] == record['fold']
    masked = record['region_mask']
    assert 'over' not in masked and 'over' not in masked['asked'] and 'shear' not in masked['asked'] and 'over_solid' not in masked['asked']
    assert 'rim_round' not in masked['asked'] and 'round' not in masked['rim']
    assert 'shear_px' not in masked['rim']
    assert not {'band_px', 'step_across_most', 'still_strip', 'tip', 'edge_folded_px'} & set(masked['ledger'])


# ---------------------------------------------------------------- the crease the wear leaves where it is

@pytest.mark.slow
def test_the_wear_wears_the_crease_in_and_laid_over_the_region_reaches_down_to_it(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    loop_dir = strip_dir(tmp_path/'loop', part_figure)
    record, answer = swayed(loop_dir, [PART], region_mask=mask, region_crease=True)  # and the answer it moved the loop by
    worn, part = answer.mask.worn[0], part_at(0)
    on_torso = [x for x in range(W) if part[:, x].any() and x <= 85]
    over_air = [x for x in range(W) if part[:, x].any() and x > 85]
    lowest = {x: int(np.nonzero(worn[:, x])[0].max()) for x in range(W) if worn[:, x].any()}
    kept = [x for x in on_torso if x in lowest]
    assert kept and all(lowest[x] == BOTTOM+1-2 for x in kept)  # the crease crease_of ended it on, worn in by 2
    assert all(lowest[x] == BOTTOM-2 for x in over_air if x in lowest)  # over the air worn in by 2 as ever
    xs = sorted(lowest)
    assert xs[0] == min(on_torso)+2 and xs[-1] == max(over_air)-2  # the sides worn in by 2 as ever
    assert record['region_mask']['mask']['box'][3] == BOTTOM+1-2 and record['region_mask']['crease'][0]['rows'] == \
        {str(x): BOTTOM+1 for x in on_torso}
    # Nothing under the crease moves: the torso keeps its bytes there in every cell.
    source, moved = cells_of(strip_dir(tmp_path/'source', part_figure)), cells_of(loop_dir)
    yy = np.mgrid[0:H, 0:W][0]
    for k in range(N):
        under = (source[k][..., 3] > 0) & (yy > BOTTOM+1+body_sway(k)[1]) & ~part_at(k)
        assert np.array_equal(moved[k][under], source[k][under]), k
    # Laid over the figure, the region reaches down to the crease carried into each cell again: in every column of the
    # crease the worn region keeps, its lowest pixel is the crease's row.
    over = answer_of(strip_dir(tmp_path/'over', part_figure), [PART], region_mask=mask, region_crease=True, region_over=True)
    for k in range(N):
        sx, sy = body_sway(k)
        region = over.mask_field(k, *np.mgrid[0:H, 0:W][::-1].astype(np.float32))[1]['region']
        assert all(int(np.nonzero(region[:, x+sx])[0].max()) == BOTTOM+1+sy for x in kept), k
        assert all(int(np.nonzero(region[:, x+sx])[0].max()) == BOTTOM-2+sy for x in over_air if x in lowest), k


# ---------------------------------------------------------------- the layer

@pytest.mark.slow
@pytest.mark.parametrize('gain', [2.5, 1.25])
def test_laid_over_the_figure_its_lower_edge_moves_with_the_rest(layer_once, tmp_path, gain):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(gain=gain, region_mask=mask, region_shrink=0, region_crease=True)
    if gain == sway.GAIN_DEFAULT:  # the default gain (what `read_ask` reads for one left out): the module's layer
        _, record, over = layer_once
    else:  # and the answer it moved the loop by
        record, over = swayed(strip_dir(tmp_path/'loop', part_figure), [PART], region_over=True, **kw)
    pinned = answer_of(strip_dir(tmp_path/'pinned', part_figure), [PART], **kw)
    read = 0
    for k in range(N):
        if math.hypot(over.dx[k], over.dy[k]) < 0.5:
            continue
        solid = np.asarray(over.cells[k])[..., 3] >= 128
        region = over.mask.region_in(k, solid)
        # well inside the torso, away from its soft side and the part's corner over the air
        sx = body_sway(k)[0]
        lower = lower_rows(region, solid, range(60+sx, 76+sx))
        above = np.roll(lower, -RISE, axis=0) & region
        # Laid over it, the lower edge moves as the part over it does; held by the figure under it, it does not.
        assert share_of(over, k, lower) >= share_of(over, k, above) - 0.05, k
        assert share_of(pinned, k, lower) <= 0.5*share_of(pinned, k, above), k
        read += 1
    assert read > 0
    assert record['region_mask']['over']['policy'] == sway.OVER_POLICY
    assert record['region_mask']['over']['band_px'] == math.ceil(over.reach)+sway.REGION_AIR == record['region_mask']['air_px']
    assert record['region_mask']['asked']['over'] is True and record['region_mask']['asked']['hold'] is None


def test_the_band_is_drawn_over_where_the_layer_lands_and_keeps_its_bytes_elsewhere(layer_once, tmp_path):
    loop_dir, record, answer = layer_once  # laid over the figure with the crease, and the answer the loop was moved by
    source, moved = cells_of(strip_dir(tmp_path/'source', part_figure)), cells_of(loop_dir)
    air = math.ceil(answer.reach)+2
    covered = aired = 0
    walked = []
    for k in range(N):
        drawn, read = answer.masked_cell(k)
        assert np.array_equal(np.asarray(drawn), moved[k]), k
        band, laid = read['band'], read['laid']
        # the layer draws the band, no read of the move's taken there; the move draws the air
        assert not (read['readers'] & band).any(), k
        aired += int((read['readers'] & read['air']).sum())
        # a read of the air's that takes the held figure beside it (the torso's soft side past the band) is walked back,
        # as `draw_held` walks any: in the air alone, never on the band
        assert not (read['walked'] & ~read['air']).any() and not ((read['walked'] | read['renormalised']) & band).any(), k
        walked.append(int(read['walked'].sum()))
        changed = np.any(moved[k] != source[k], axis=-1)
        visible = source[k][..., 3] > 0
        # Outside the region, its soft edge, the air and the band nothing changes; in the band only where the layer lands.
        outside = visible & ~read['region'] & ~read['soft'] & ~band
        assert not (changed & outside).any(), k
        assert not (changed & band & ~laid).any(), k
        covered += int((changed & band).sum())
        # The band is the torso under the lower edge (the crease, the region's last row; beside it, the torso's soft side),
        # within the air's reach of it.
        ys, _ = np.nonzero(band)
        assert len(ys) and not (band & ~visible).any(), k
        assert np.nonzero(band & (source[k][..., 3] >= 128))[0].min() >= BOTTOM+2+body_sway(k)[1], k
        assert ys.min() >= BOTTOM+1+body_sway(k)[1] and ys.max() <= BOTTOM+1+body_sway(k)[1]+sway.REGION_SOFT+air, k
    assert covered > 0 and aired > 0
    ledger = record['region_mask']['ledger']
    assert ledger['cover_depth_px_max'] <= air and ledger['over_px_max'] > 0 and ledger['held_changed_px_max'] == 0
    assert ledger['folded_px_max'] == 0 and min(ledger['jacobian_min']) > 0
    assert ledger['walked_px'] == walked == [0]*9 + [4, 9, 6] + [0]*12


def test_where_the_part_lifts_it_shows_the_figure_under_it(layer_once, tmp_path):
    loop_dir, record, answer = layer_once  # laid over the figure with the crease, and the answer the loop was moved by
    source, moved = cells_of(strip_dir(tmp_path/'source', part_figure)), cells_of(loop_dir)
    yy, xx = np.mgrid[0:H, 0:W]
    cells = lifting(answer)
    assert cells
    shown, thin, notched = 0, {}, []
    for k in cells:
        _, read = answer.masked_cell(k)
        lifted = read['lifted']
        assert lifted.any(), k
        sx, sy = body_sway(k)
        side = xx == 86+sx  # the torso's soft side
        # what the lift uncovers is read at its whole move as any read (`draw_held`), none of it short: its alpha is the
        # source's read there
        alpha = np.clip(np.round(sway._bilinear(source[k][..., 3].astype(np.float64), xx-read['read_x'], yy-read['read_y'])),
                        0, 255)
        assert not (lifted & (read['renormalised'] | read['walked'])).any(), k
        assert np.array_equal(moved[k][lifted][:, 3], alpha[lifted]), k
        # where that read lies wholly on opaque pixels, it stays opaque; the torso's and the crease's red, none of the
        # stripes' (200 or more)
        opaque = lifted & reads_wholly_on(read['field'], source[k][..., 3] == 255)
        assert opaque.any(), k
        assert (moved[k][opaque][:, 3] == 255).all(), (k, sorted(set(moved[k][opaque][:, 3].tolist())))
        assert float(moved[k][lifted][:, 0].mean()) < 100, k
        # beside the torso's soft side the read takes that side and the air past it: one column of the part's, within
        # 2 px of that side, as soft as that read
        under = lifted & ~side & (moved[k][..., 3] < 128)
        assert len(set(xx[under].tolist())) <= 1 and (abs(xx[under]-(86+sx)) <= 2).all(), k
        thin[k] = int(under.sum())
        # in the rows the part leaves, its column over the torso's soft side reads at the move too: where the part moves
        # toward the torso that read takes the air past the side, and the side shows thinner than it was
        rows = slice(BOTTOM+1+sy+math.floor(answer.dy[k]), BOTTOM+1+sy)
        if not (moved[k][rows, 86+sx, 3] >= SOFT-1).all():
            notched.append(k)
        shown += int(lifted.sum())
    assert thin == {0: 7, 1: 0, 2: 0, 9: 2, 10: 4, 11: 6, 12: 0, 13: 0, 14: 0, 21: 3, 22: 5, 23: 7}
    assert notched == [k for k in cells if answer.dx[k] < 0] == [0, 1, 13, 14, 21, 22, 23]
    assert shown > 0 and record['region_mask']['ledger']['lifted_px_max'] > 0


@pytest.mark.slow
def test_where_the_part_lifts_off_a_piece_left_out_no_hole_opens_and_the_piece_keeps_its_bytes(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(region_mask=mask, region_shrink=0, region_over=True, hold_mask=arm_masks(tmp_path/'piece', under_at))
    loop_dir = strip_dir(tmp_path/'loop', under_figure)
    _, answer = swayed(loop_dir, [PART], **kw)  # the answer it moved the loop by
    source, moved = cells_of(strip_dir(tmp_path/'source', under_figure)), cells_of(loop_dir)
    cells = lifting(answer)
    assert cells
    took_px = 0
    for k in cells:
        _, read = answer.masked_cell(k)
        piece = under_at(k)
        # the piece left out under the lower edge is held: a read of the part's that would take it is read from its other
        # pixels (`draw_held`), none of them where the part lifts, so no hole opens; the piece keeps its bytes
        took = read['field'][0] & reads_wholly_on(read['field'], piece, any_tap=True) & ~piece
        assert not (took & read['lifted']).any() and not (took & ~read['renormalised']).any(), k
        assert (moved[k][took][:, 3] == 255).all(), (k, sorted(set(moved[k][took][:, 3].tolist())))
        assert np.array_equal(moved[k][piece], source[k][piece]), k
        took_px += int(took.sum())
    assert took_px == 10*len(cells)


@pytest.mark.slow
def test_its_outline_against_the_air_goes_with_it_and_against_the_figure_stays(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(region_mask=mask, region_shrink=0, region_over=True, hold_mask=arm_masks(tmp_path/'arm'))
    loop_dir = strip_dir(tmp_path/'loop', outlined_figure)
    _, answer = swayed(loop_dir, [PART], **kw)  # the answer it moved the loop by
    source, moved = cells_of(strip_dir(tmp_path/'source', outlined_figure)), cells_of(loop_dir)
    for k in range(N):
        _, read = answer.masked_cell(k)
        line = outline_at(k)
        assert line.any() and np.array_equal(read['edge'] & line, line), k  # the outline over the air: the part's
        crease = np.all(source[k][..., :3] == CREASE, axis=-1) & (source[k][..., 3] == 255)
        assert crease.any() and not (read['edge'] & crease).any(), k  # on the torso, the figure's
        assert (read['band'] & crease).any(), k
    cells = lifting(answer)
    assert cells
    for k in cells:
        line = outline_at(k)
        # lifted, the outline over the air leaves the air behind it, no speck: none of it stays where it was, at its end
        # beside the torso's soft side too (under the lower edge, no anchor of the rim: `layer_sets`)
        assert (moved[k][line][:, 3] == 0).all(), k
        # and goes up with the part: over its source row, in its columns, dark where the stripes were
        sy, columns = body_sway(k)[1], np.nonzero(line.any(axis=0))[0]
        rows = slice(BOTTOM+1+sy+math.floor(answer.dy[k]), BOTTOM+1+sy)
        dark = moved[k][rows, columns.min():columns.max()+1]
        assert ((dark[..., :3].astype(int).sum(axis=-1) < 300) & (dark[..., 3] >= 128)).any(), k
    # where it is not lifted the crease keeps its bytes but where the part lands on it
    for k in cells:
        crease = np.all(source[k][..., :3] == CREASE, axis=-1) & (source[k][..., 3] == 255)
        assert np.array_equal(moved[k][crease], source[k][crease]), k


INSIDE_BOX = (58, 82, 66, 90)  # x0, y0, x1, y1 in cell 0, last included: a piece of the arm's colour inside the part


def inside_at(k):
    """A piece of the arm's colour inside the part's lower half in cell k, carried with the body."""
    sx, sy = body_sway(k)
    yy, xx = np.mgrid[0:H, 0:W]
    x0, y0, x1, y1 = INSIDE_BOX
    return (xx >= x0+sx) & (xx <= x1+sx) & (yy >= y0+sy) & (yy <= y1+sy)


def inside_figure(k, n=N):
    """The arm figure with a piece of the arm's colour inside the part too."""
    a = np.asarray(arm_figure(k, n)).copy()
    a[inside_at(k)] = ARM
    return Image.fromarray(a, 'RGBA')


@pytest.mark.slow
def test_laid_over_the_figure_the_pixels_the_hold_mask_names_under_the_region_mask_move_with_it(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    source = cells_of(strip_dir(tmp_path/'source', inside_figure))
    both = arm_masks(tmp_path/'both', lambda k: inside_at(k) | arm_at(k))
    for name, hold in (('folder', both), ('one', mask_png(tmp_path/'both.png', inside_at(0) | arm_at(0)))):
        kw = dict(region_mask=mask, region_shrink=0, region_over=True, hold_mask=hold)
        loop_dir = strip_dir(tmp_path/name, inside_figure)
        record, answer = swayed(loop_dir, [PART], **kw)  # and the answer it moved the loop by
        moved = cells_of(loop_dir)
        carried = 0
        for k in range(N):
            drawn, read = answer.masked_cell(k)
            assert np.array_equal(np.asarray(drawn), moved[k]), (name, k)
            piece = inside_at(k)
            # The piece the region mask covers is the region's: no anchor of the rim, never left out of it.
            assert np.array_equal(read['region'] & piece, piece) and np.array_equal(read['left_out'] & piece, piece), (name, k)
            assert not (read['anchors'] & piece).any(), (name, k)
            if read['field'] is None:
                continue
            # It moves with the part: each pixel of the part whose read takes the piece wholly is the piece's colour, and
            # some of them lie where it was not.
            on_piece = read['region'] & reads_wholly_on(read['field'], piece)
            assert (moved[k][on_piece] == ARM).all(), (name, k)
            carried += int((on_piece & ~piece).sum())
        assert carried > 0, name
        masked = record['region_mask']
        assert masked['ledger']['left_out_px_max'] > 0 and masked['asked']['hold']['per_cell'] == (name == 'folder')
        left_out = masked['over']['left_out']
        if name == 'folder':
            assert left_out['carry'] is None and left_out['grow_px'] is None
        else:
            assert left_out['grow_px'] == 3 and len(left_out['carry']['cells']) == N and left_out['lay'] == []
            assert sorted(p['px'] for p in left_out['pieces']) == sorted([int(inside_at(0).sum()), int(arm_at(0).sum())])


@pytest.mark.slow
def test_the_pixels_the_hold_mask_names_outside_the_region_mask_and_on_a_band_keep_their_bytes(tmp_path):
    over_arm = part_at(0) | (arm_at(0) & (np.mgrid[0:H, 0:W][0] >= 86))  # the mask runs onto the arm's lower rows
    mask = mask_png(tmp_path/'mask.png', over_arm)
    source = cells_of(strip_dir(tmp_path/'source', inside_figure))
    yy = np.mgrid[0:H, 0:W][0]
    loop_dir = strip_dir(tmp_path/'over', inside_figure)
    sway.sway_loop(loop_dir, [PART], region_mask=mask, region_shrink=0, region_over=True, hold_mask=arm_masks(tmp_path/'arms'))
    moved = cells_of(loop_dir)
    for k in range(N):  # laid over the figure: the arm's rows the mask does not cover keep their bytes in every cell
        out = arm_at(k) & (yy < 86+body_sway(k)[1])
        assert np.array_equal(moved[k][out], source[k][out]), k
    # On a band (no region mask, so none laid over) every pixel the hold mask names keeps its bytes, the piece inside the
    # part too, while the part moves round them.
    both = arm_masks(tmp_path/'both', lambda k: inside_at(k) | arm_at(k))
    band_dir = strip_dir(tmp_path/'band', inside_figure)
    sway.sway_loop(band_dir, [PART], band='auto', hold_mask=both)
    banded = cells_of(band_dir)
    for k in range(N):
        held = inside_at(k) | arm_at(k)
        assert np.array_equal(banded[k][held], source[k][held]), k
    assert any(np.any(banded[k][part_at(k) & ~inside_at(k)] != source[k][part_at(k) & ~inside_at(k)]) for k in range(N))


# ---------------------------------------------------------------- the shear

@pytest.mark.slow
@pytest.mark.parametrize('shear', [0.5, 0.25])
def test_the_shear_widens_the_rim_so_a_move_rises_across_it_no_steeper(unsheared_once, tmp_path, shear):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(gain=1.25, region_mask=mask, region_shrink=0)
    record, answer = swayed(strip_dir(tmp_path/'loop', part_figure), [PART], region_shear=shear, **kw)  # and its answer
    _, plain, _ = unsheared_once  # region_shear=1e6, one for every shear: the ledger reads the edges; the rim as ever
    rim, was = record['region_mask']['rim'], plain['region_mask']['rim']
    floor = [math.ceil(0.5*math.pi*float(np.max(np.abs(answer.dy)))/shear), math.ceil(0.5*math.pi*float(np.max(np.abs(answer.dx)))/shear)]
    assert rim['shear_px'] == floor and record['region_mask']['asked']['shear'] == shear
    assert (rim['across_px'], rim['down_px']) == (max(was['across_px'], floor[0]), max(was['down_px'], floor[1]))
    assert rim['across_px'] > was['across_px'] and rim['least_px'] == was['least_px'] and was['shear_px'] == [1, 1]
    lx, ly = rim['across_px'], rim['down_px']
    reach = max(math.hypot(x, y) for x, y in zip(answer.dx, answer.dy))
    ledger, before = record['region_mask']['ledger'], plain['region_mask']['ledger']
    # sin² rises at most π/2 a rim's width: a step across at most π/2 · |move| / Lx, a move down within it at most shear
    assert ledger['step_across_max'] <= 0.5*math.pi*reach/lx + 1e-6
    assert ledger['step_down_max'] <= 0.5*math.pi*reach/ly + 1e-6
    for k in range(N):
        spot = ledger['step_across_most'][k]
        if spot is not None:
            assert spot[0] <= math.hypot(shear, 0.5*math.pi*abs(answer.dx[k])/lx) + 1e-6, k
    assert ledger['step_across_max'] < before['step_across_max']
    assert ledger['folded_px_max'] == 0 and ledger['walked_px_max'] == 0


# ---------------------------------------------------------------- the solid pixels alone

def below_floor(read):
    """The pixels under the region's lower edge: from each column's first row under it down (`layer_sets`' floor)."""
    return np.mgrid[0:H, 0:W][0] >= read['floor'][None, :]


@pytest.mark.slow
def test_laid_over_the_solid_pixels_alone_the_rest_under_it_stays_and_its_soft_edge_goes_with_it(layer_once, tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(region_mask=mask, region_shrink=0, region_crease=True, region_over=True)
    loop_dir = strip_dir(tmp_path/'loop', part_figure)
    record, answer = swayed(loop_dir, [PART], region_over_solid=True, **kw)  # and the answer it moved the loop by
    sliding = layer_once[2]  # the same asked without --region-over-solid: the module's layer
    source, moved = cells_of(strip_dir(tmp_path/'source', part_figure)), cells_of(loop_dir)
    soft_beside = kept = 0
    for k in range(N):
        drawn, read = answer.masked_cell(k)
        assert np.array_equal(np.asarray(drawn), moved[k]), k
        alpha = source[k][..., 3]
        under = below_floor(read)
        # Only the solid pixels under the lower edge are free of the rim and drawn over.
        assert not (read['under'] & (alpha < 128)).any() and not (read['band'] & (alpha < 128)).any(), k
        # The rest there, not opaque, is no part of the region: an anchor of the rim, kept byte for byte.
        rest = under & (alpha > 0) & (alpha < 128) & ~read['region'] & ~read['soft']
        assert np.array_equal(read['anchors'] & rest, rest), k
        assert np.array_equal(moved[k][rest], source[k][rest]), k
        kept += int(rest.sum())
        # The region's soft edge under its lower edge goes with it beside the figure under it (the torso's soft side);
        # sliding over it as ever, that side is the figure's.
        _, slid = sliding.masked_cell(k)
        beside = read['soft'] & under & sway._grow8((alpha >= 128) & ~read['region'], 1)
        assert not (slid['soft'] & beside).any(), k
        soft_beside += int(beside.sum())
    assert kept > 0 and soft_beside > 0
    masked = record['region_mask']
    assert masked['asked']['over_solid'] is True and masked['over']['solid'] == sway.OVER_SOLID_POLICY


# ---------------------------------------------------------------- the membrane

def membrane_pass(read, alpha):
    """How far one more of the membrane's passes would move its pixels, the most of it, across and down: each pixel's
    move set against the mean of its figure 4-neighbours' (the region's own move on it, its soft edge and its outline over
    the band; none on the rest of the figure)."""
    free, field = read['membrane'], read['field']
    own = read['region'] | read['soft'] | read['edge']
    figure = alpha > 0
    gaps = []
    for move in (field[1], field[2]):
        u = np.where(free | own, move, 0.0)
        pu, pf = np.pad(u, 1), np.pad(figure, 1)
        total = sum(pf[1+oy:1+oy+H, 1+ox:1+ox+W]*pu[1+oy:1+oy+H, 1+ox:1+ox+W] for oy, ox in ((-1, 0), (1, 0), (0, -1), (0, 1)))
        count = sum(pf[1+oy:1+oy+H, 1+ox:1+ox+W].astype(float) for oy, ox in ((-1, 0), (1, 0), (0, -1), (0, 1)))
        gaps.append(float(np.abs(u-total/np.maximum(count, 1))[free].max()))
    return max(gaps)


def seam_step(read, columns):
    """The most the drawn move steps down from the region's last row to the row under it, over `columns` (the move: the
    field's on the region, its soft edge, its outline over the band and the membrane; none on what the layer slides over)."""
    field = read['field']
    moving = read['region'] | read['soft'] | read['edge'] | read.get('membrane', np.zeros((H, W), bool))
    size = np.where(moving, np.hypot(field[1], field[2]), 0.0)
    steps = []
    for x in columns:
        ys = np.nonzero(read['region'][:, x])[0]
        if len(ys) and ys.max()+1 < H:
            steps.append(abs(size[ys.max(), x]-size[ys.max()+1, x]))
    return max(steps)


@pytest.mark.slow
@pytest.mark.parametrize('reach', [3, 6])
def test_the_membrane_under_the_lower_edge_hangs_between_the_part_and_the_rest(gentle_layer_once, tmp_path, reach):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(gain=GENTLE, region_mask=mask, region_shrink=0, region_crease=True, region_over=True)
    loop_dir = strip_dir(tmp_path/'loop', part_figure)
    record, answer = swayed(loop_dir, [PART], region_membrane=reach, **kw)  # and the answer it moved the loop by
    sliding_over = gentle_layer_once[2]  # the same asked without a membrane, for every reach: the module's layer
    source, moved = cells_of(strip_dir(tmp_path/'source', part_figure)), cells_of(loop_dir)
    read_cells = 0
    for k in range(N):
        drawn, read = answer.masked_cell(k)
        assert np.array_equal(np.asarray(drawn), moved[k]), k
        free = read['membrane']
        if read['field'] is None:
            assert not free.any(), k
            continue
        # Under the lower edge, within reach + 1 px of the part and its soft edge, never its outline over the band.
        sx, sy = body_sway(k)
        yy = np.mgrid[0:H, 0:W][0]
        assert free.any() and not (free & (yy <= BOTTOM+sy)).any() and not (free & read['edge']).any(), k
        assert not (free & (sway.rim_distance(read['region'] | read['soft'], reach+1, reach+1) >= 1)).any(), k
        assert (free & (source[k][..., 3] > 0)).sum() == free.sum(), k
        # Each pixel's move the mean of its figure 4-neighbours', the part's own and none on the rest of the figure, to
        # within what one more pass would move it: less than the step the passes stop at.
        assert membrane_pass(read, source[k][..., 3]) < sway.MEMBRANE_STEP, k
        if math.hypot(answer.dx[k], answer.dy[k]) < 0.5:
            continue
        # The figure under the part stretches with it: the move steps down from the part's last row to the row under it
        # by a share of what it does where the part slides over it.
        columns = range(60+sx, 76+sx)
        _, slid = sliding_over.masked_cell(k)
        assert seam_step(read, columns) <= 0.5*seam_step(slid, columns), k
        # drawn by the move, never laid over: the torso under the part changes where it moves, and the layer lands on none
        # of it
        assert (np.any(moved[k] != source[k], axis=-1) & free).any() and not (read['laid'] & free).any(), k
        read_cells += 1
    assert read_cells > 0
    masked = record['region_mask']
    assert masked['asked']['membrane_px'] == reach
    assert masked['over']['membrane'] == {'policy': sway.MEMBRANE_POLICY, 'reach_px': reach+1, 'step_px': sway.MEMBRANE_STEP,
                                          'passes_most': sway.MEMBRANE_PASSES}
    ledger = masked['ledger']
    assert len(ledger['membrane_px']) == N and max(ledger['membrane_px']) > 0
    most = [entry for entry in ledger['membrane_most'] if entry is not None]
    assert most and max(entry[0] for entry in most) <= max(math.hypot(x, y) for x, y in zip(answer.dx, answer.dy))+1e-6


@pytest.mark.slow
def test_the_membrane_never_moves_a_piece_left_out(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(gain=GENTLE, region_mask=mask, region_shrink=0, region_over=True, region_membrane=6,
              hold_mask=arm_masks(tmp_path/'piece', under_at))
    loop_dir = strip_dir(tmp_path/'loop', under_figure)
    _, answer = swayed(loop_dir, [PART], **kw)  # the answer it moved the loop by
    source, moved = cells_of(strip_dir(tmp_path/'source', under_figure)), cells_of(loop_dir)
    beside = 0
    for k in range(N):
        _, read = answer.masked_cell(k)
        piece = under_at(k)
        assert not (read['membrane'] & piece).any(), k
        assert np.array_equal(moved[k][piece], source[k][piece]), k
        beside += int((read['membrane'] & sway._grow4(piece, 1)).sum())
    assert beside > 0  # the membrane reaches the piece's sides and stops there


@pytest.mark.slow
def test_the_layer_without_the_membrane_is_as_it_was(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(region_mask=mask, region_shrink=0, region_crease=True, region_over=True, hold_mask=arm_masks(tmp_path/'arms'))
    as_ever, asked_off = strip_dir(tmp_path/'as-ever', arm_figure), strip_dir(tmp_path/'off', arm_figure)
    record, answer = swayed(as_ever, [PART], **kw)  # and the answer it moved the loop by
    off = sway.sway_loop(asked_off, [PART], region_membrane=None, **kw)
    assert (as_ever/'walk.strip.png').read_bytes() == (asked_off/'walk.strip.png').read_bytes()
    assert off['region_mask'] == record['region_mask']
    # The membrane's four keys shape nothing without it: the same bytes and the same record, none of them named.
    keyed_dir = strip_dir(tmp_path/'keyed', arm_figure)
    keyed = sway.sway_loop(keyed_dir, [PART], region_membrane_unpin=True, region_clear_alpha=8, region_floor_beside=12,
                           region_own_outline=2, **kw)
    assert (as_ever/'walk.strip.png').read_bytes() == (keyed_dir/'walk.strip.png').read_bytes()
    assert keyed['region_mask'] == record['region_mask']
    masked = record['region_mask']
    assert 'membrane_px' not in masked['asked'] and 'membrane' not in masked['over'] and 'solid' not in masked['over']
    assert not {'membrane_unpin', 'clear_alpha', 'floor_beside_px', 'own_outline_px'} & set(masked['asked'])
    assert not {'membrane_px', 'membrane_most', 'membrane_folded_px'} & set(masked['ledger'])
    _, read = answer.masked_cell(0)
    assert 'membrane' not in read


@pytest.mark.slow
def test_a_membrane_too_short_for_the_move_is_refused_by_its_name(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop', part_figure)
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    with pytest.raises(SystemExit, match=r'--region-membrane 1 hangs a move of 6\.2 px over 2 px under the region\'s lower edge, '
                                         r'and cell \d+\'s move folds the picture there \(a pixel takes -?[\d.]+ of its area\): a '
                                         r'longer membrane, or a smaller move — nothing was written$'):
        sway.sway_loop(loop_dir, [PART], region_mask=mask, region_shrink=0, region_over=True, region_membrane=1)
    assert (loop_dir/'walk.strip.png').read_bytes() == as_cut
    ledger = answer_of(strip_dir(tmp_path/'gentle', part_figure), [PART], gain=GENTLE, region_mask=mask, region_shrink=0,
                       region_over=True, region_membrane=6).mask.ledger
    assert ledger['membrane_folded_px'] == [0]*N and ledger['membrane_folded_px_max'] == 0


# ---------------------------------------------------------------- the membrane's keys

HAZE_COLUMNS = range(88, 92)  # in cell 0: under the part's lower edge where it hangs over the air
SPECK = (90, BOTTOM+6)  # x, y in cell 0: a near-clear speck under it


def hem_figure(k, n=N):
    """The part over the air with a few pixels not opaque right under its lower edge there, and a near-clear speck."""
    sx, sy = body_sway(k)
    a = np.asarray(part_figure(k, n)).copy()
    for x in HAZE_COLUMNS:  # past the part's soft edge (REGION_SOFT px), within the membrane's reach
        a[BOTTOM+3+sy, x+sx] = (*OUTLINE[:3], 60)
        a[BOTTOM+4+sy, x+sx] = (*OUTLINE[:3], 30)
    a[SPECK[1]+sy, SPECK[0]+sx] = (*OUTLINE[:3], 3)
    return Image.fromarray(a, 'RGBA')


BUMP_ROWS = (79, 83, 87, 81, 85, 89)  # in cell 0: the row a pixel of the part's outline over the air stands past its mask


def bump_at(k):
    """The outline's pixels past the part's mask in cell k: one beside its side over the air, two in the odd cells, in a
    row that changes from cell to cell."""
    sx, sy = body_sway(k)
    row = BUMP_ROWS[k % len(BUMP_ROWS)]
    out = np.zeros((H, W), bool)
    out[row+sy:row+sy+1+k % 2, 93+sx] = True
    return out


def bump_figure(k, n=N):
    a = np.asarray(part_figure(k, n)).copy()
    a[bump_at(k)] = OUTLINE
    return Image.fromarray(a, 'RGBA')


def last_crossing(alpha):
    """Where `alpha` (0 to 1 along a line) last falls through 0.5, subpixel; None where it never does."""
    falls = np.nonzero((alpha[:-1] >= 0.5) & (alpha[1:] < 0.5))[0]
    if not len(falls):
        return None
    i = int(falls[-1])
    return i+float((alpha[i]-0.5)/(alpha[i]-alpha[i+1]))


def edge_lags(drawn, cut, move, columns):
    """Per column, the lower edge's row drawn (where its alpha last falls through 0.5 going down) less the same row of the
    cut moved whole by `move` (its alpha read bilinear at x - dx, y - dy)."""
    alpha, rows = cut[..., 3]/255, np.arange(H, dtype=float)
    return [last_crossing(drawn[:, x, 3]/255)-last_crossing(sway._bilinear(alpha, np.full(H, x-move[0]), rows-move[1]))
            for x in columns]


def side_lags(drawn, cut, moves):
    """Per row, the side's column drawn (where its alpha last falls through 0.5 going right) less the same row's of the cut
    moved whole by the move that row is meant to take (`moves`: row -> (dx, dy))."""
    alpha, columns = cut[..., 3]/255, np.arange(W, dtype=float)
    return {y: last_crossing(drawn[y, :, 3]/255)-last_crossing(sway._bilinear(alpha, columns-mx, np.full(W, y-my)))
            for y, (mx, my) in moves.items()}


def hem_lags(tmp_path, name, **kw):
    """The most |lag| of the lower edge over the air (`edge_lags`, the part's own move) per cell that moves 0.5 px or more
    down or up, a hem figure swayed as asked; its record, and the answer it moved the loop by."""
    loop_dir = strip_dir(tmp_path/name, hem_figure)
    record, answer = swayed(loop_dir, [PART], **kw)
    source, moved = cells_of(strip_dir(tmp_path/f'{name}-source', hem_figure)), cells_of(loop_dir)
    lags = {k: max(abs(v) for v in edge_lags(moved[k], source[k], (record['dx_px'][k], record['dy_px'][k]),
                                              [x+body_sway(k)[0] for x in HAZE_COLUMNS]))
            for k in range(N) if abs(record['dy_px'][k]) >= 0.5}
    return lags, record, answer


def bump_lags(tmp_path, name, **kw):
    """Per cell that moves 0.5 px or more across, the most |lag| of the part's side over the air (`side_lags`, each row
    meant to take the move the part without the outline past its mask takes at its side's last pixel there) and its row,
    a bump figure swayed as asked; and the answer."""
    loop_dir = strip_dir(tmp_path/name, bump_figure)
    _, answer = swayed(loop_dir, [PART], **kw)  # the answer it moved the loop by
    clean = answer_of(strip_dir(tmp_path/f'{name}-clean', part_figure), [PART], **kw)
    source, moved = cells_of(strip_dir(tmp_path/f'{name}-source', bump_figure)), cells_of(loop_dir)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    out = {}
    for k in range(N):
        if abs(answer.dx[k]) < 0.5:
            continue
        sx, sy = body_sway(k)
        field, _ = clean.mask_field(k, xx, yy)
        lags = side_lags(moved[k], source[k], {y+sy: (float(field[1][y+sy, 92+sx]), float(field[2][y+sy, 92+sx]))
                                               for y in range(78, BOTTOM-1)})
        row = max(lags, key=lambda y: abs(lags[y]))
        out[k] = (abs(lags[row]), row-sy)
    return out, answer


@pytest.mark.slow
def test_with_the_membrane_unpinned_the_lower_edge_over_the_air_moves_with_the_part_over_what_is_not_opaque(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(gain=GENTLE, region_mask=mask, region_shrink=0, region_over=True, region_over_solid=True, region_membrane=6,
              region_shear=0.35)
    keyed, record, answer = hem_lags(tmp_path, 'keyed', region_membrane_unpin=True, region_clear_alpha=8, **kw)
    pinned, _, plain = hem_lags(tmp_path, 'pinned', **kw)
    # Laid over the solid pixels alone, the pixels not opaque under the lower edge are the membrane's and, but for the key,
    # anchors of the rim too: the lower edge over them stays short of the part's move. Unpinned, it moves with it.
    assert keyed and max(keyed.values()) <= 0.25, keyed
    assert max(pinned.values()) >= 0.5, pinned
    asked = record['region_mask']['asked']
    assert asked['membrane_unpin'] is True and asked['clear_alpha'] == 8 and 'floor_beside_px' not in asked
    # Under the clear alpha the speck is clear: neither held nor the membrane's (`answer`, `plain`: the answers the keyed
    # loop and the pinned one were moved by).
    for k in range(N):
        sx, sy = body_sway(k)
        speck = (SPECK[1]+sy, SPECK[0]+sx)
        _, read = answer.masked_cell(k)
        assert not read['held'][speck] and not read['membrane'][speck], k
        _, as_cut = plain.masked_cell(k)
        assert as_cut['membrane'][speck], k


@pytest.mark.slow
def test_grown_over_its_own_outline_the_side_over_the_air_moves_by_its_rows_share(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    kw = dict(gain=GENTLE, band_up=(0.4, 'auto'), region_mask=mask, region_shrink=0, region_over=True, region_membrane=6)
    grown, answer = bump_lags(tmp_path, 'grown', region_own_outline=2, **kw)
    held, as_cut = bump_lags(tmp_path, 'held', **kw)
    # Held, the outline's pixel past the mask stands where it is and pins the rim: the side bends at its row, a row that
    # changes from cell to cell. Grown over, it is the part's and the side moves by each row's share of the move.
    assert grown and max(lag for lag, _ in grown.values()) <= 0.25, grown
    assert max(lag for lag, _ in held.values()) >= 0.5 and len({row for lag, row in held.values() if lag >= 0.25}) > 1, held
    for k in range(N):
        _, read = answer.masked_cell(k)
        _, kept = as_cut.masked_cell(k)
        assert np.array_equal(read['region'] & bump_at(k), bump_at(k)) and not (read['anchors'] & bump_at(k)).any(), k
        assert np.array_equal(kept['anchors'] & bump_at(k), bump_at(k)), k
    assert answer.mask.asked.own_outline == 2


def test_the_floor_beside_the_region_is_its_lowest_pixel_within_n_columns_of_that_side():
    region = np.zeros((40, 30), bool)
    for x in range(10, 20):
        region[5:20+(x-10), x] = True  # the lower edge falls 1 px a column to the right
    alpha = np.full((40, 30), 255, np.uint8)
    none = np.zeros((40, 30), bool)
    nearest = sway.layer_sets(region, alpha, 3, none)['floor']
    beside = sway.layer_sets(region, alpha, 3, none, beside=4)['floor']
    lowest = {x: 20+(x-10) for x in range(10, 20)}  # one under each column's lowest pixel
    assert [int(v) for v in nearest[7:10]] == [lowest[10]]*3 and [int(v) for v in nearest[20:23]] == [lowest[19]]*3
    assert [int(v) for v in beside[7:10]] == [lowest[14]]*3 and [int(v) for v in beside[20:23]] == [lowest[19]]*3
    assert np.array_equal(beside[10:20], nearest[10:20]) and (beside[:7] == 40).all() and (beside[23:] == 40).all()


# ---------------------------------------------------------------- the rim held round beside it

def corner_step(read, columns, rows):
    """The most the drawn move steps between two pixels of the region one over the other, over `columns` and `rows`."""
    field, region = read['field'], read['region']
    size = np.hypot(field[1], field[2])
    steps = [abs(size[y, x]-size[y+1, x]) for x in columns for y in rows if region[y, x] and region[y+1, x]]
    return max(steps)


@pytest.mark.slow
def test_the_rim_held_round_beside_the_part_rises_below_its_corner_as_gently_down_as_across(tmp_path):
    yy, xx = np.mgrid[0:H, 0:W]
    # the part's lower edge falls 8 rows from its left side: the torso beside it holds the rim down to the row its left
    # side ends on, and under that row lies what is under its lower edge, which holds none of it
    sloped = part_at(0) & (yy <= BOTTOM-np.clip(58-xx, 0, 8))
    mask = mask_png(tmp_path/'mask.png', sloped)
    kw = dict(gain=GENTLE, region_mask=mask, region_shrink=0, region_over=True, region_shear=0.35)
    loop_dir = strip_dir(tmp_path/'loop', part_figure)
    record, rounded = swayed(loop_dir, [PART], region_rim_round=True, **kw)  # and the answer it moved the loop by
    plain = answer_of(strip_dir(tmp_path/'plain', part_figure), [PART], **kw)
    moved = cells_of(loop_dir)
    rim = record['region_mask']['rim']
    r = max(rim['across_px'], rim['down_px'])
    assert rim['round'] == {'policy': sway.RIM_ROUND_POLICY, 'px': r} and rim['down_px'] < r
    assert record['region_mask']['asked']['rim_round'] is True
    most = max(math.hypot(x, y) for x, y in zip(rounded.dx, rounded.dy))
    read_cells, bounds, as_evers = 0, [], []
    for k in range(N):
        drawn, read = rounded.masked_cell(k)
        assert np.array_equal(np.asarray(drawn), moved[k]), k
        if read['field'] is None or math.hypot(rounded.dx[k], rounded.dy[k]) < 0.5*most:
            continue
        sx, sy = body_sway(k)
        columns, rows = range(51+sx, 59+sx), range(BOTTOM-9+sy, BOTTOM+sy)
        _, as_ever = plain.masked_cell(k)
        # Round, the move rises below the corner by at most π/2 of its size over the rim's wider width a px; held as
        # ever, by the rim's height, more than that.
        bound = 0.5*math.pi*math.hypot(rounded.dx[k], rounded.dy[k])/r
        assert corner_step(read, columns, rows) <= bound+0.02, k
        bounds.append(bound)
        as_evers.append(corner_step(as_ever, columns, rows))
        read_cells += 1
    assert read_cells > 0 and max(as_evers) > 1.25*max(bounds)


# ---------------------------------------------------------------- the edges the ledger reads

@pytest.mark.slow
def test_the_still_strip_is_read_from_the_region_to_its_side(tmp_path):
    short = part_at(0) & (np.mgrid[0:H, 0:W][1] >= 53)  # drawn 3 px short of the part's left side
    mask = mask_png(tmp_path/'mask.png', short)
    cell = np.asarray(arm_figure(0))
    arm = arm_at(0)
    widths = sway.still_strip(short, cell[..., 3], arm)
    by_row = {}
    for y in np.nonzero(short.any(axis=1))[0]:
        xs = np.nonzero(short[y])[0]
        left = xs.min()-1-(max(x for x in range(xs.min()) if arm[y, x] or cell[y, x, 3] < 128))
        right = (min(x for x in range(xs.max()+1, W) if arm[y, x] or cell[y, x, 3] < 128))-xs.max()-1
        by_row[int(y)] = (int(left), int(right))
    assert sorted(widths) == sorted(w for pair in by_row.values() for w in pair)
    assert all(by_row[y][0] == 3 for y in range(ARM_BOX[1], BOTTOM+1))  # beside the arm, the 3 px the mask left
    assert all(by_row[y][1] == 0 for y in range(int(PART[1]), BOTTOM+1))  # over the air, it reaches its side
    loop_dir = strip_dir(tmp_path/'loop', arm_figure)
    record = sway.sway_loop(loop_dir, [PART], region_mask=mask, region_shrink=0, region_over=True,
                            hold_mask=arm_masks(tmp_path/'arms'))
    ledger, rim = record['region_mask']['ledger'], record['region_mask']['rim']['across_px']
    first = ledger['still_strip'][0]
    assert first == {'median': float(np.median(widths)), 'most': max(widths), 'ends': len(widths),
                     'covered': sum(w <= rim for w in widths)}
    assert len(ledger['still_strip']) == N and all(entry is not None for entry in ledger['still_strip'])


@pytest.mark.slow
def test_the_tip_is_read_on_the_side_over_the_air(tmp_path):
    # A tip moved whole is its own shape: 0 px off it once the move is taken away, the move found.
    cell = np.asarray(part_figure(0))
    mine = part_at(0)
    shifted = np.zeros_like(cell)
    shifted[1:, 2:] = cell[:-1, :-2]
    move = (np.full((H, W), 2.0), np.full((H, W), 1.0))
    tip = sway.tip_shape(cell, shifted, mine, move)
    assert tip['side'] == 'right' and tip['shape_px'] == 0.0 and tip['shift'] == [2.0, 1.0] and tip['move_mean'] == 2.2361
    xs = np.nonzero(mine.any(axis=0))[0]
    rows = np.nonzero(mine[:, xs.max()-9:].any(axis=1))[0]
    assert tip['box'] == [int(xs.max())-9, int(rows.min())-8, int(xs.max())+9, int(rows.max())+9] and tip['ring_px'] > 0
    assert sway.tip_shape(cell, cell, mine, (np.zeros((H, W)), np.zeros((H, W))))['shape_px'] == 0.0
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    record = sway.sway_loop(strip_dir(tmp_path/'loop', part_figure), [PART], region_mask=mask, region_shear=0.5)
    ledger = record['region_mask']['ledger']
    tips = ledger['tip']
    assert len(tips) == N and all(t['side'] == 'right' for t in tips)
    assert ledger['tip_shape_max'] == list(max(((t['shape_px'], k) for k, t in enumerate(tips)), key=lambda q: q[0]))
    assert ledger['tip_move_max'][0] > 0.5 and all(t['shape_px'] >= 0 for t in tips)


def test_move_steps_reads_the_most_step_both_ways_where_both_are_shown():
    mx, my = np.zeros((6, 8)), np.zeros((6, 8))
    my[:, 5:] = 0.75  # a step down of 0.75 between columns 4 and 5
    mx[3:, :] = 0.5   # a step across of 0.5 between rows 2 and 3
    shown = np.ones((6, 8), bool)
    across, down = sway.move_steps(mx, my, shown)
    assert across == [0.75, 4, 0] and down == [0.5, 0, 2]
    shown[:, 5] = False
    assert sway.move_steps(mx, my, shown)[0][0] == 0.0


# ---------------------------------------------------------------- the same on every path, and refused

@pytest.mark.slow
def test_the_frames_are_moved_as_the_strip(tmp_path):
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    arms = arm_masks(tmp_path/'arms')
    frames = tmp_path/'frames'
    frames.mkdir()
    names = [f'frame-{k:03d}.png' for k in range(N)]
    for k, name in enumerate(names):
        arm_figure(k).save(frames/name)
    loop_dir = strip_dir(tmp_path/'loop', arm_figure, delay_ms=41.67)
    asked = ['--gain', str(GENTLE), '--region-mask', str(mask), '--region-crease', '--region-over', '--hold-mask', str(arms),
             '--region-shear', '0.5', '--region-membrane', '6']
    assert cli.main(['video-sway', '--frames', str(frames), '--out', str(tmp_path/'out'), '--fps', '24', '--region', text(PART),
                     *asked]) == 0
    record = sway.sway_loop(loop_dir, [PART], gain=GENTLE, region_mask=mask, region_crease=True, region_over=True,
                            hold_mask=arms, region_shear=0.5, region_membrane=6)
    strip = cells_of(loop_dir)
    out = [np.asarray(Image.open(tmp_path/'out'/name).convert('RGBA')) for name in names]
    assert all(np.array_equal(out[k], strip[k]) for k in range(N))
    assert json.loads((tmp_path/'out'/'sway.json').read_text())['region_mask'] == record['region_mask']


def test_what_the_new_keys_are_refused_for_leaves_the_loop_as_it_was(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop', part_figure)
    as_cut = (loop_dir/'walk.strip.png').read_bytes()
    mask = mask_png(tmp_path/'mask.png', part_at(0))
    for args, said in (
            (['--region-over'], r'--region-over, --region-over-solid, --region-rim-round, --region-shear and --region-membrane shape '
                                r'a region mask; they need --region-mask$'),
            (['--region-rim-round'], r'they need --region-mask$'),
            (['--region-mask', str(mask), '--region-rim-round'], r'--region-rim-round holds the rim round beside a region laid over '
                                                                 r'the figure, where what lies under its lower edge holds none of '
                                                                 r'it; it needs --region-over$'),
            (['--region-mask', str(mask), '--region-over-solid'], r'--region-over-solid says which pixels under a region laid over '
                                                                  r'the figure are laid over; it needs --region-over$'),
            (['--region-shear', '0.5'], r'they need --region-mask$'),
            (['--region-membrane', '6'], r'they need --region-mask$'),
            (['--region-mask', str(mask), '--region-membrane', '6'], r'--region-membrane moves the figure under a region laid over it; it needs --region-over$'),
            (['--region-mask', str(mask), '--region-over', '--region-membrane', '-1'], r'0 or more; got -1$'),
            (['--region-membrane-unpin'], r"--region-membrane-unpin, --region-clear-alpha, --region-floor-beside and "
                                          r"--region-own-outline shape a region mask's membrane; they need --region-mask$"),
            (['--region-clear-alpha', '8'], r"membrane; they need --region-mask$"),
            (['--region-floor-beside', '12'], r"membrane; they need --region-mask$"),
            (['--region-own-outline', '2'], r"membrane; they need --region-mask$"),
            (['--region-mask', str(mask), '--region-over', '--region-membrane', '6', '--region-clear-alpha', '0'],
             r"--region-clear-alpha is the alpha under which a pixel is read as clear, a whole number from 1 to 127 \(under a "
             r"solid pixel's\); got 0$"),
            (['--region-mask', str(mask), '--region-clear-alpha', '128'], r'from 1 to 127 \(under a solid pixel\'s\); got 128$'),
            (['--region-mask', str(mask), '--region-floor-beside', '-1'], r'--region-floor-beside is px, a whole number, 0 or more; got -1$'),
            (['--region-mask', str(mask), '--region-own-outline', '-2'], r'--region-own-outline is px, a whole number, 0 or more; got -2$'),
            (['--region-mask', str(mask), '--region-shear', '0'], r'--region-shear is the most a move one way may rise across'),
            (['--region-mask', str(mask), '--region-shear', '-1'], r'over 0; got -1$'),
            (['--region-mask', str(mask), '--region-shear', 'nan'], r'over 0; got nan$'),
            (['--region-mask', str(mask), '--hold-mask', str(mask)],
             r"--region-mask holds every visible pixel outside the region already; --hold-mask is a band's, or with --region-over")):
        with pytest.raises(SystemExit, match=said) as refused:
            cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', text(PART), *args])
        assert refused.value.code not in (0, None), args
        assert (loop_dir/'walk.strip.png').read_bytes() == as_cut and 'follow' not in json.loads((loop_dir/'walk.strip.json').read_text())
