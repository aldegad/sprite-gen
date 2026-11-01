"""The gait fallback: a slow walk and a walk toward the camera, on synthetic frames."""
import hashlib
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw
from conftest import as_pinned
from sprite_gen.video import gait_fallback, loop
from tests.video import test_ghost_given_way as ghost_clips
from tests.video import test_stop as stop_clips

FOOT = (140, 170)


SS = 4  # drawn this many times larger and scaled down, so a slow swing moves by sub-pixels


def walker(k, *, period=24, grow=0.0, frames=73):
    """A front walker standing on FOOT, legs swinging with `period` frames, `grow` bigger by the end."""
    big = Image.new('RGBA', (280*SS, 180*SS))
    d = ImageDraw.Draw(big)
    phase = k*2*math.pi/period

    def box(x0, y0, x1, y1, fill):
        d.rectangle(tuple(round(v*SS) for v in (x0, y0, x1, y1)), fill=fill)

    # No head bob: the analysis fits one vertical slope over the clip, and a bob that does not
    # complete whole cycles in it tilts that slope.
    x, y = FOOT[0]-15, FOOT[1]-120
    box(x, y, x+28, y+30, (210, 150, 60, 255))
    box(x+20, y+8, x+24, y+12, (10, 30, 50, 255))
    box(x-3, y+32, x+30, y+78, (20, 90, 180, 255))
    box(x+10, y+35, x+15, y+73, (210, 190, 40, 255))
    lift = 10*math.sin(phase)
    # Two legs that do not look alike, so half a cycle is not a cycle.
    box(x-2, y+79+max(0, lift), x+10, FOOT[1]-1-max(0, lift)/2, (240, 200, 40, 255))
    box(x+24, y+79+max(0, -lift), x+29, FOOT[1]-1-max(0, -lift)/2, (30, 60, 90, 255))
    # A mark that moves every frame and comes back each cycle: no two neighbours are identical,
    # which the GIF writer would merge.
    box(x+3, y+38+12*(k % period)/period, x+5, y+40+12*(k % period)/period, (240, 240, 240, 255))
    im = big.convert('RGBa').resize((280, 180), Image.Resampling.BOX).convert('RGBA')
    if not grow:
        return im
    s = 1+grow*k/(frames-1)
    ax, ay = FOOT
    return im.convert('RGBa').transform(im.size, Image.Transform.AFFINE, (1/s, 0, ax*(1-1/s), 0, 1/s, ay*(1-1/s)),
                                        resample=Image.Resampling.BICUBIC).convert('RGBA')


def settling(k, *, settle=10, rise=6, grow=0.0, frames=73):
    """A walker that stands tall on straight legs in its first frame and settles into the walk: its
    legs start `rise` px longer and ease to their walking length over `settle` frames. The body
    above the hips does not move, and nothing changes size."""
    image = walker(k, grow=grow, frames=frames)
    lift = round(rise*0.5*(1+math.cos(math.pi*min(k, settle)/settle)), 2)
    if lift <= 0:
        return image
    hips = FOOT[1]-41
    legs = image.crop((0, hips, image.width, FOOT[1]))
    out = image.copy()
    out.paste((0, 0, 0, 0), (0, hips, image.width, image.height))
    stretched = legs.convert('RGBa').resize((image.width, round((FOOT[1]-hips+lift)*SS)), Image.Resampling.BICUBIC)
    out.alpha_composite(stretched.resize((image.width, FOOT[1]-hips+math.ceil(lift)), Image.Resampling.BOX).convert('RGBA'), (0, hips))
    return out


# A cut again by hand (`--cycle fixed`), as the recut path cuts a walk: one cycle from frame 24.
FIXED = ('--cycle', 'fixed', '--start', '24', '--length', '24', '--anchor', 'body')


def run(tmp_path, frames, *extra, cut=('--anchor', 'motion-auto'), state='walk'):
    keyed = tmp_path/'keyed'
    keyed.mkdir()
    for k, image in enumerate(frames):
        image.save(keyed/f'{k:03}.png')
    output = tmp_path/'out'
    args = ['--frames-dir', str(keyed), '--out-dir', str(output), '--state', state, *cut, '--repair', 'off',
            '--report', str(tmp_path/'loop.json'), *extra]
    try:
        code = loop.main(args)
    except SystemExit as exc:
        code = exc
    return code, json.loads((tmp_path/'loop.json').read_text()), output


def test_drift_is_measured_on_the_fitted_height_and_undone_about_the_feet():
    frames = [walker(k, grow=0.2) for k in range(73)]
    measured = gait_fallback.scale_drift(frames)
    assert 0.17 < measured['drift'] < 0.23
    undone = gait_fallback.undo_scale(frames, measured)
    boxes = gait_fallback.subject_boxes(undone)
    # Back to the walker's own height in every frame, its bob included.
    still = gait_fallback.subject_boxes([walker(k) for k in range(73)])
    assert np.all(np.abs((boxes[:, 3]-boxes[:, 1])-(still[:, 3]-still[:, 1])) <= 2)
    # The feet stay where they were filmed.
    assert np.all(np.abs(boxes[:, 3]-FOOT[1]) <= 2)
    # Nothing to undo on a walker that stays its size.
    assert abs(gait_fallback.scale_drift([walker(k) for k in range(73)])['drift']) < 0.02


def other_lapack(monkeypatch, seed):
    """np.polyfit as another machine's LAPACK might solve it: every coefficient off by up to 1e-12 of
    itself, seed by seed. Machines differ by less (on the 10-05 grid, lines a few 1e-13 px apart);
    a line read exactly is unmoved by any of it."""
    rng = np.random.default_rng(seed)
    polyfit = np.polyfit

    def solved_elsewhere(x, y, deg, *args, **kwargs):
        coef = polyfit(x, y, deg, *args, **kwargs)
        return coef*(1+rng.uniform(-1e-12, 1e-12, np.shape(coef)))
    monkeypatch.setattr(np, 'polyfit', solved_elsewhere)


def test_a_body_that_keeps_its_place_and_size_reads_them_exactly():
    """A line through equal values is that value: the fitted foot point and height of a walker that
    neither moves nor grows are its box's, to the bit, so nothing about them is left to rounding."""
    frames = [walker(0)]*73
    x0, y0, x1, y1 = gait_fallback.subject_boxes(frames[:1])[0]
    measured = gait_fallback.scale_drift(frames)
    assert np.all(measured['foot_y'] == y1) and np.all(measured['foot_x'] == (x0+x1)/2)
    assert np.all(measured['height'] == y1-y0) and measured['drift'] == 0


@pytest.mark.slow
def test_the_size_hold_is_the_same_whatever_the_lapack_rounding(monkeypatch):
    """Feet that stand on the frame's bottom edge in every frame: the hold scales about them, so
    nothing reaches past that edge and no room is added below. The foot point is read off a fitted
    line; np.polyfit's last bit put it a hair past the edge or short of it, and the frames got 2 px
    of room below or none (v2.36.0). On the exact line the foot point, the room and every held pixel
    are the same under any rounding."""
    full = [walker(k, grow=0.03) for k in range(73)]
    floor = int(np.nanmax(gait_fallback.subject_boxes(full)[:, 3]))
    frames = [im.crop((0, 0, im.width, floor)) for im in full]
    assert np.all(gait_fallback.subject_boxes(frames)[:, 3] == floor)
    seen = set()
    for seed in range(8):
        other_lapack(monkeypatch, seed)
        measured = gait_fallback.cycle_drift(frames, min_lag=12, max_lag=36)
        pad = gait_fallback.undo_padding(frames, measured)
        held = gait_fallback.undo_scale(frames, measured, pad=pad)
        seen.add((pad, measured['foot_x'].tobytes(), measured['foot_y'].tobytes(),
                  hashlib.sha256(b''.join(im.tobytes() for im in held)).hexdigest()))
        monkeypatch.undo()
    assert len(seen) == 1
    assert next(iter(seen))[0][3] == 0


@pytest.mark.slow
def test_a_clip_the_first_search_cuts_is_cut_without_the_fallback(tmp_path):
    code, report, output = run(tmp_path, [walker(k) for k in range(73)])
    assert code == 0
    assert report['cycle']['length'] == 24
    assert 'gait_fallback' not in report
    assert not list(output.glob(f'.{loop.FALLBACK_FRAMES}*'))
    # A clip that keeps its size is not held.
    assert report['size_hold']['applied'] is False
    assert abs(report['size_hold']['drift']) < gait_fallback.SIZE_HOLD_MIN
    assert 'padding_ltrb' not in report['size_hold']


@pytest.mark.slow
@pytest.mark.timeout(900)  # 16 to 22 calls of the binary at 280 x 180 (the held fill's frames between): seconds on a GPU, minutes on a CPU's Vulkan driver
def test_a_cycle_longer_than_half_the_clip_is_found_by_the_fallback(tmp_path, rife_runs):
    frames = [walker(k, period=40) for k in range(73)]
    code, report, output = run(tmp_path, frames)
    assert code == 0
    assert as_pinned(rife_runs(), {"rife-v4.25": (4, 16), "rife-v4.6": 22}), rife_runs()
    assert abs(report['cycle']['length']-40) <= 1
    fallback = report['gait_fallback']
    assert 'no periodic cycle found' in fallback['reason']
    assert fallback['scale_undone'] is False
    assert fallback['window'] == [12, gait_fallback.long_window(12, 73, 24.0)]
    assert not list(output.glob(f'.{loop.FALLBACK_FRAMES}*'))


@pytest.mark.slow
def test_a_walk_toward_the_camera_is_held_at_its_size_before_the_first_search(tmp_path):
    frames = [walker(k, grow=0.2) for k in range(73)]
    code, report, output = run(tmp_path, frames)
    assert code == 0
    assert abs(report['cycle']['length']-24) <= 1
    hold = report['size_hold']
    assert hold['applied'] is True and hold['drift'] > gait_fallback.SIZE_HOLD_MIN
    # Held first, the first search cuts it: the fallback never runs.
    assert 'gait_fallback' not in report
    cells = sorted((output/'cycle').glob('frame-*.png'))
    heights = gait_fallback.subject_boxes([Image.open(p).convert('RGBA') for p in cells])
    assert np.ptp(heights[:, 3]-heights[:, 1]) <= 6
    assert not list(output.glob(f'.{loop.FALLBACK_FRAMES}*'))
    # Its feet stand on one row as filmed: they are left there.
    assert hold['feet'] == {'min': gait_fallback.FOOT_HOLD_MIN, 'method': 'one-cycle-on', 'change_px': 0.0, 'held': False}


@pytest.mark.slow
def test_a_small_change_of_size_is_held_too(tmp_path):
    # Under the fallback's 3 %, over the hold's 1 %: held, the walker loops at its size; searched as
    # filmed, the growing walker never matches itself and the fallback does not scale it back.
    frames = [walker(k, grow=0.025) for k in range(73)]
    code, report, output = run(tmp_path, frames)
    assert code == 0 and report['size_hold']['applied'] is True
    assert abs(report['cycle']['length']-24) <= 1
    (tmp_path/'off').mkdir()
    code_off, report_off, _ = run(tmp_path/'off', frames, '--size-hold', 'off')
    assert str(code_off).startswith('video-loop: no periodic cycle found')
    assert report_off['size_hold'] == {'applied': False, 'why': '--size-hold off'}
    fallback = report_off['gait_fallback']
    assert fallback['scale_undone'] is False and fallback['scale_drift']['drift'] < gait_fallback.SCALE_DRIFT_MIN


def test_the_size_is_read_one_cycle_on():
    grows = gait_fallback.cycle_drift([walker(k, grow=0.025) for k in range(73)], min_lag=12, max_lag=36)
    # The pose matches itself one cycle on; each height is compared with the frames one, two and
    # three cycles later (49 + 25 + 1 pairs), and a cycle on it is 24/72 of the clip's growth more.
    assert grows['method'] == 'one-cycle-on' and grows['lag'] == 24 and grows['pairs'] == 49+25+1
    # (The drawn walker grows 2.5 %; a soft crown's coverage does not follow a sub-pixel move
    # exactly, so its height reads a few tenths of a percent off either way.)
    assert 1.007 < grows['per_lag'] < 1.011 and 0.021 < grows['drift'] < 0.032
    assert abs(grows['drift']-grows['drift_fitted_line']) < 0.005
    still = gait_fallback.cycle_drift([walker(k) for k in range(73)], min_lag=12, max_lag=36)
    assert still['lag'] == 24 and still['per_lag'] == 1.0 and still['drift'] == 0.0
    # A clip too short to show a cycle twice has nothing one cycle on to compare with.
    short = gait_fallback.cycle_drift([walker(k, grow=0.2) for k in range(20)], min_lag=12, max_lag=36)
    assert short['lag'] is None and short['drift'] == 0.0


@pytest.mark.slow
def test_a_first_pose_that_settles_into_the_walk_is_not_held(tmp_path):
    # Stood tall, then walking: a line through every frame reads a body that shrinks; one cycle on,
    # every pose is the size it was.
    frames = [settling(k) for k in range(73)]
    assert gait_fallback.scale_drift(frames)['drift'] <= -gait_fallback.SIZE_HOLD_MIN
    code, report, output = run(tmp_path, frames)
    assert code == 0
    hold = report['size_hold']
    assert hold['applied'] is False and abs(hold['drift']) < gait_fallback.SIZE_HOLD_MIN
    assert hold['drift_fitted_line'] <= -gait_fallback.SIZE_HOLD_MIN and hold['lag'] == 24
    (tmp_path/'off').mkdir()
    code_off, _, output_off = run(tmp_path/'off', frames, '--size-hold', 'off')
    assert code_off == 0
    # Not held, the cut is the cut of the frames as filmed.
    assert (output/'loop.strip.png').read_bytes() == (output_off/'loop.strip.png').read_bytes()


@pytest.mark.slow
def test_a_settle_does_not_hide_a_walk_that_grows(tmp_path):
    # The settle pulls the fitted line down by as much as the walk grows: a line reads under 1 %.
    frames = [settling(k, grow=0.025) for k in range(73)]
    assert abs(gait_fallback.scale_drift(frames)['drift']) < gait_fallback.SIZE_HOLD_MIN
    code, report, output = run(tmp_path, frames)
    assert code == 0 and abs(report['cycle']['length']-24) <= 1
    hold = report['size_hold']
    assert hold['applied'] is True and 0.02 < hold['drift'] < 0.03
    cells = sorted((output/'cycle').glob('frame-*.png'))
    heights = gait_fallback.subject_boxes([Image.open(p).convert('RGBA') for p in cells])
    assert np.ptp(heights[:, 3]-heights[:, 1]) <= 6


def walking_away(k, frames=73):
    """A walker going away from the camera: 15 % smaller by the end and its feet 20 px higher in the
    frame (the ground recedes toward the horizon), its crown 10 px under the top at the start."""
    image = walker(k, grow=-0.15, frames=frames)
    rows = 40 + round(20*k/(frames-1))
    out = Image.new('RGBA', image.size)
    out.alpha_composite(image.crop((0, rows, image.width, image.height)), (0, 0))
    return out


@pytest.mark.slow
def test_scaling_back_up_keeps_a_crown_carried_past_the_top():
    # Scaled back up about feet that have risen, the late frames' crown lands above the frame.
    # Widened first, nothing is cut.
    frames = [walking_away(k) for k in range(73)]
    measured = gait_fallback.scale_drift(frames)
    pad = gait_fallback.undo_padding(frames, measured)
    assert pad[1] > 0
    kept = gait_fallback.subject_boxes(gait_fallback.undo_scale(frames, measured))
    cut = gait_fallback.subject_boxes(gait_fallback.undo_scale(frames, measured, pad=(0, 0, 0, 0)))
    first = frames[0].getchannel('A').getbbox()
    first_height = first[3]-first[1]
    # Widened, the last frame stands as tall as the first; not widened, its crown is gone.
    assert abs((kept[-1, 3]-kept[-1, 1])-first_height) <= 2
    assert (cut[-1, 3]-cut[-1, 1]) < first_height-8
    assert cut[-1, 1] == 0
    # The first frame is the reference: only moved into the wider frame, not resampled.
    left, top, right, bottom = pad
    widened = Image.new('RGBA', (frames[0].width+left+right, frames[0].height+top+bottom))
    widened.paste(frames[0], (left, top))
    assert gait_fallback.undo_scale(frames, measured)[0].tobytes() == widened.tobytes()


def test_no_room_is_added_when_nothing_reaches_out():
    frames = [walker(k, grow=0.2) for k in range(73)]
    measured = gait_fallback.scale_drift(frames)
    assert gait_fallback.undo_padding(frames, measured) == (0, 0, 0, 0)
    undone = gait_fallback.undo_scale(frames, measured)
    assert all(f.size == frames[0].size for f in undone)


@pytest.mark.slow
def test_the_held_loop_of_a_shrinking_walk_keeps_its_crown(tmp_path):
    frames = [walking_away(k) for k in range(73)]
    code, report, output = run(tmp_path, frames)
    assert code == 0
    hold = report['size_hold']
    # Its feet climb 20 px: held on the first frame's row, the crown scaled back up stays in the frame.
    assert hold['applied'] is True and hold['feet']['held'] is True and -21 <= hold['feet']['change_px'] <= -19
    first = frames[0].getchannel('A').getbbox()
    cells = sorted((output/'cycle').glob('frame-*.png'))
    boxes = gait_fallback.subject_boxes([Image.open(p).convert('RGBA') for p in cells])
    # Every cell stands about as tall as the first frame did: no cell lost its crown.
    assert np.all(np.abs((boxes[:, 3]-boxes[:, 1])-(first[3]-first[1])) <= 6)
    # And the loop closes at its feet: the wrap moves them no more than a step inside does.
    edges = wrap_and_inner(output)
    assert edges['bottom'][0] <= edges['bottom'][1], edges


def test_the_feet_are_read_one_cycle_on():
    frames = [walking_away(k) for k in range(73)]
    feet = gait_fallback.foot_drift(frames, gait_fallback.cycle_drift(frames, min_lag=12, max_lag=36))
    # The feet climb 20 px over the clip (a whole crop row every three or four frames, read a cycle on: 21); each frame
    # is moved back down to the first frame's row by its share.
    assert feet['method'] == 'one-cycle-on' and feet['held'] is True and -21 <= feet['change_px'] <= -19
    assert feet['lift'][0] == 0 and np.allclose(feet['lift'], -feet['change_px']*np.arange(73)/72)
    # A walker that keeps its row, whatever its size, is left where it stands.
    for clip in ([walker(k) for k in range(73)], [walker(k, grow=-0.07) for k in range(73)]):
        feet = gait_fallback.foot_drift(clip, gait_fallback.cycle_drift(clip, min_lag=12, max_lag=36))
        assert feet['change_px'] == 0.0 and feet['held'] is False and feet['lift'] is None
    # A first pose that stands on longer legs and settles: a line through every frame's feet reads them climbing
    # (the settle pulls it down by 2.7 px), one cycle on they stand where they did.
    settle = [settling(k, grow=0.025) for k in range(73)]
    fitted = gait_fallback.scale_drift(settle)['foot_y']
    assert fitted[-1]-fitted[0] < -2
    feet = gait_fallback.foot_drift(settle, gait_fallback.cycle_drift(settle, min_lag=12, max_lag=36))
    assert feet['change_px'] == 0.0 and feet['held'] is False


@pytest.mark.slow
def test_held_with_its_feet_a_walk_away_stands_on_the_first_frames_row():
    frames = [walking_away(k) for k in range(73)]
    measured = gait_fallback.cycle_drift(frames, min_lag=12, max_lag=36)
    lift = gait_fallback.foot_drift(frames, measured)['lift']
    pad = gait_fallback.undo_padding(frames, measured, lift)
    held = gait_fallback.subject_boxes(gait_fallback.undo_scale(frames, measured, pad=pad, lift=lift))
    first = gait_fallback.subject_boxes(frames[:1])[0]
    # The first frame's height and row, every frame (moved into the frame by the room above it).
    assert np.all(np.abs((held[:, 3]-held[:, 1])-(first[3]-first[1])) <= 2)
    assert np.all(np.abs(held[:, 3]-(first[3]+pad[1])) <= 1)
    # The feet held where the crown was carried past the top: it is not, and needs no room there.
    assert pad[1] == 0 < gait_fallback.undo_padding(frames, measured)[1]
    # A box of a frame as filmed is laid where its part is held: the head, by its colour.
    k = 48
    pixels = np.asarray(frames[k])
    orange = (np.abs(pixels[..., :3].astype(int)-(210, 150, 60)).max(axis=-1) <= 8) & (pixels[..., 3] == 255)
    ys, xs = np.nonzero(orange)
    box = (int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1)
    laid = gait_fallback.held_box(box, k, measured, pad, lift)
    held_k = np.asarray(gait_fallback.undo_scale(frames, measured, pad=pad, lift=lift)[k])
    ys, xs = np.nonzero((np.abs(held_k[..., :3].astype(int)-(210, 150, 60)).max(axis=-1) <= 8) & (held_k[..., 3] == 255))
    assert all(abs(a-b) <= 1 for a, b in zip(laid, (xs.min(), ys.min(), xs.max()+1, ys.max()+1)))


def wrap_and_inner(output):
    """The cut's frames (cycle/ or cycle.refused/): for the body's height, top and bottom (its solid box), the change
    at the wrap (last frame into the first) and the largest change from one frame to the next inside."""
    folder = output/'cycle' if (output/'cycle').is_dir() else output/'cycle.refused'
    boxes = gait_fallback.subject_boxes([Image.open(p).convert('RGBA') for p in sorted(folder.glob('frame-*.png'))])
    return {name: (abs(v[-1]-v[0]), np.abs(np.diff(v)).max())
            for name, v in (('height', boxes[:, 3]-boxes[:, 1]), ('top', boxes[:, 1]), ('bottom', boxes[:, 3]))}


@pytest.mark.slow
@pytest.mark.timeout(900)  # 14 to 16 calls of the binary at 280 x 180 for the 2 % walk (the held fill's frames between): seconds on a GPU, minutes on a CPU's Vulkan driver
@pytest.mark.parametrize('clip', ['walking away', 'shrinking 2 %'])
def test_a_walk_cut_again_by_hand_is_held_at_its_size_and_its_feet(tmp_path, clip, rife_runs):
    """A cut the caller names (`--cycle fixed`, the recut path) of a walk that changes size: held over the whole clip
    before the cut is read, as a search's cut is, so the loop's last frame is the size of its first and stands on its
    row — at the wrap the body's height, crown and feet change no more than from one frame to the next inside, and
    the seam the gate reads on the frames held closes. Cut as filmed, the walk away jumped 6 px in height at the wrap
    (1 inside) and was refused at 6.83, the 2 % walk 1 px (1) at 3.80."""
    frames = [walking_away(k) for k in range(73)] if clip == 'walking away' else [walker(k, grow=-0.02) for k in range(73)]
    code, report, output = run(tmp_path, frames, cut=FIXED)
    assert as_pinned(rife_runs(), {"rife-v4.25": (4, 16), "rife-v4.6": 14} if clip == 'shrinking 2 %' else 0), rife_runs()
    edges = wrap_and_inner(output)
    # The feet first, whatever the gate said: held at its size alone, the walk away's last frame stands 6 px above its
    # first (1 px a frame inside) and the seam reads 10.03 — its size moved into its place.
    assert edges['bottom'][0] <= edges['bottom'][1], edges
    assert code == 0 and report['status'] == 'passed', code
    hold = report['size_hold']
    assert hold['applied'] is True and abs(hold['drift']) >= gait_fallback.SIZE_HOLD_MIN
    assert hold['feet']['held'] is (clip == 'walking away')
    # The cut shows the change itself, a cycle of its length apart.
    assert hold['at_cut']['pairs'] == 24 and hold['at_cut']['size'] is True and hold['at_cut']['feet'] is (clip == 'walking away')
    assert report['resampled_seam_ratio'] <= loop.SEAM_RATIO_MAX
    assert all(wrap <= inner for wrap, inner in edges.values()), edges
    assert not list(output.glob(f'.{loop.FALLBACK_FRAMES}*'))


@pytest.mark.slow
@pytest.mark.parametrize('outside', [0, 1, 5])
@pytest.mark.parametrize('end', ['after', 'before'])
def test_a_walk_cut_by_hand_is_held_with_a_few_frames_beyond_the_cut(tmp_path, end, outside):
    """A cut the caller names reads its change on each of its frames, against the frame its length later or, where the
    clip ends before that, its length earlier: a frame the clip holds beyond the cut is the partner of one frame of it
    and no more. The walk away cut on the last cycle of 72 frames, the clip run on past it by the cut's own first
    frames again (or cut on its first cycle, the clip opened by the cut's last frames), which show no change against
    it: held on 24 pairs with none, one or five of them, and the loop closes. Read on the frames with a partner later
    alone, one frame after the cut left one pair, which read no change, and the shrink the other 23 show went unheld:
    refused at 7.94, where the cut passes at 1.19 with nothing after it. Some half the cut's length of such frames are
    the partners of half its frames and can bring the median under the share; this does not reach that far."""
    frames = [walking_away(k) for k in range(72)]
    if end == 'after':
        clip, start = frames+[frames[48+j].copy() for j in range(outside)], 48
    else:
        clip, start = [frames[24-outside+j].copy() for j in range(outside)]+frames, outside
    code, report, output = run(tmp_path, clip, cut=('--cycle', 'fixed', '--start', str(start), '--length', '24', '--anchor', 'body'))
    seen = report['size_hold']['at_cut']
    assert seen['pairs'] == 24 and seen['size'] is True and seen['feet'] is True, seen
    assert report['size_hold']['applied'] is True
    assert code == 0 and report['status'] == 'passed', code
    assert report['resampled_seam_ratio'] <= loop.SEAM_RATIO_MAX
    edges = wrap_and_inner(output)
    assert all(wrap <= inner for wrap, inner in edges.values()), edges


@pytest.mark.slow
def test_a_walk_cut_again_by_hand_as_filmed(tmp_path):
    """`--size-hold off`: the cut the caller names is the frames as filmed — the cut's frames are the clip's, pixel for
    pixel — and the report says so."""
    frames = [walking_away(k) for k in range(73)]
    code, report, output = run(tmp_path, frames, '--size-hold', 'off',
                               cut=('--cycle', 'fixed', '--start', '24', '--length', '24', '--anchor', 'none'))
    assert report['size_hold'] == {'applied': False, 'why': '--size-hold off'}
    folder = output/'cycle' if (output/'cycle').is_dir() else output/'cycle.refused'
    cells = sorted(folder.glob('frame-*.png'))
    assert [Image.open(p).convert('RGBA').tobytes() for p in cells] == [im.tobytes() for im in frames[24:48]]


@pytest.mark.slow
@pytest.mark.parametrize('cut', ['fixed', 'motion-auto'])
def test_a_walk_that_keeps_its_size_is_cut_as_filmed_either_way(tmp_path, cut):
    """No change of size: held or not, the loop is the same bytes, and the report says it was not held."""
    frames = [walker(k) for k in range(73)]
    how = FIXED if cut == 'fixed' else ('--anchor', 'motion-auto')
    outputs = []
    for mode in ('auto', 'off'):
        (tmp_path/mode).mkdir()
        code, report, output = run(tmp_path/mode, frames, '--size-hold', mode, cut=how)
        assert code == 0
        outputs.append(output)
    assert report['size_hold'] == {'applied': False, 'why': '--size-hold off'}
    held = json.loads((tmp_path/'auto'/'loop.json').read_text())['size_hold']
    assert held['applied'] is False and 'feet' not in held and 'padding_ltrb' not in held
    for path in ('loop.strip.png', 'loop.gif', 'cycle/frame-000.png', 'cycle/frame-023.png'):
        assert (outputs[0]/path).read_bytes() == (outputs[1]/path).read_bytes(), path


@pytest.mark.slow
@pytest.mark.parametrize('clip', ['another motion first', 'a short clip'])
def test_a_cut_by_hand_that_keeps_its_size_is_not_held_for_what_the_clip_reads(tmp_path, clip):
    """Read over the whole clip, frames that are not the walk the cut is can read a change of size the walk never makes:
    a run filmed from a standing still that raises and waves its arm and crouches before it sets off
    (tests/video/test_stop.py) reads 6.9 % smaller by the end, and a walk of 26 frames cut 20 long, too short for the
    reading to try a lag of its cycle, reads 2.5 % larger. The cut shows no change a cycle of its length apart
    (`size_hold.at_cut`), so it is cut as filmed: the same bytes as with `--size-hold off`, and the report says why."""
    if clip == 'another motion first':
        keyed, run_from = stop_clips.clip(tmp_path, other=8)
        start, length, state, pairs = run_from+stop_clips.PERIOD, stop_clips.PERIOD, 'run', stop_clips.PERIOD
    else:
        keyed = ghost_clips._keyed(tmp_path, 'short', ())
        start, length, state, pairs = 0, ghost_clips.L, 'walk', 6
    frames = [Image.open(p).convert('RGBA') for p in sorted(keyed.glob('*.png'))]
    how = ('--cycle', 'fixed', '--start', str(start), '--length', str(length), '--anchor', 'none', '--seam-max', '1000')
    outputs = {}
    for mode in ('auto', 'off'):
        (tmp_path/mode).mkdir()
        code, _, outputs[mode] = run(tmp_path/mode, frames, '--size-hold', mode, cut=how, state=state)
        assert code == 0, code
    hold = json.loads((tmp_path/'auto'/'loop.json').read_text())['size_hold']
    assert hold['applied'] is False and abs(hold['drift']) >= gait_fallback.SIZE_HOLD_MIN
    seen = hold['at_cut']
    assert seen['pairs'] == pairs and seen['change'] == 0 and seen['size'] is False and 'why' in hold
    for path in ('loop.strip.png', 'cycle/frame-000.png'):
        assert (outputs['auto']/path).read_bytes() == (outputs['off']/path).read_bytes(), path


@pytest.mark.slow
def test_an_idle_cut_by_hand_is_not_held(tmp_path):
    # The hold is a walk's or a run's: an idle that changes size is cut as filmed, and its report has no `size_hold`.
    code, report, _ = run(tmp_path, [walker(k, grow=-0.07) for k in range(73)], '--seam-max', '1000',
                          cut=('--cycle', 'fixed', '--start', '24', '--length', '24'), state='idle')
    assert code == 0 and 'size_hold' not in report


def receding(k, *, climb=96, frames=73):
    """A walker going away from the camera over ground that climbs the frame fast: 15 % smaller by the end and its feet
    `climb` px higher, 32 px over a cycle of 24 frames — further than the motion anchor's fine search looks
    (motion_anchor.SEARCH_RADIUS, 28 rows)."""
    image = walker(k, grow=-0.15, frames=frames)
    out = Image.new('RGBA', (image.width, image.height+climb))
    out.alpha_composite(image, (0, climb-round(climb*k/(frames-1))))
    return out


@pytest.mark.slow
def test_a_walk_away_whose_feet_climb_past_the_fine_search_is_held_on_its_row(tmp_path):
    """Held at its size about feet left where they were filmed, the loop's last frame stands 32 px above its first: the
    motion anchor's fine search, 28 rows, reaches no match there, the cut is left uncorrected, and the gate refused its
    wrap at 13.03. Held on the first frame's row as well, the cut's frames stand where its first does, the anchor
    corrects what is left, and the loop closes."""
    code, report, output = run(tmp_path, [receding(k) for k in range(73)], '--overlap-wrap', 'off')
    assert code == 0 and report['status'] == 'passed', code
    hold = report['size_hold']
    assert hold['applied'] is True and hold['feet']['held'] is True and -97 <= hold['feet']['change_px'] <= -95
    assert report['motion_anchor']['method'] == 'masked-ncc-boundary-velocity-v1'
    assert report['resampled_seam_ratio'] <= loop.SEAM_RATIO_MAX
    edges = wrap_and_inner(output)
    assert edges['bottom'][0] <= edges['bottom'][1], edges


@pytest.mark.slow
def test_a_walk_cut_by_hand_on_named_regions_reads_them_held(tmp_path):
    """`--anchor motion` names its regions on the cut's first frame as filmed: held, they are read where those parts
    stand in the frame held (`size_hold.anchor_regions`), the head on the head."""
    frames = [walking_away(k) for k in range(73)]
    pixels = np.asarray(frames[24])
    boxes = []
    for colour in ((210, 150, 60), (20, 90, 180)):  # the head, the torso
        ys, xs = np.nonzero((np.abs(pixels[..., :3].astype(int)-colour).max(axis=-1) <= 8) & (pixels[..., 3] == 255))
        boxes.append((int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1))
    regions = [v for box in boxes for v in ('--anchor-region', ','.join(map(str, box)))]
    code, report, _ = run(tmp_path, frames, *regions,
                          cut=('--cycle', 'fixed', '--start', '24', '--length', '24', '--anchor', 'motion'))
    hold = report['size_hold']
    assert hold['applied'] is True
    measured = gait_fallback.cycle_drift(frames, min_lag=12, max_lag=36)
    lift = gait_fallback.foot_drift(frames, measured)['lift']
    laid = [list(gait_fallback.held_box(box, 24, measured, tuple(hold['padding_ltrb']), lift)) for box in boxes]
    assert hold['anchor_regions'] == laid and report['motion_anchor']['regions'] == laid
    assert code == 0, code


@pytest.mark.slow
def test_a_walk_toward_the_camera_is_scaled_back_before_the_second_search(tmp_path):
    frames = [walker(k, grow=0.2) for k in range(73)]
    code, report, output = run(tmp_path, frames, '--size-hold', 'off')
    assert code == 0
    assert abs(report['cycle']['length']-24) <= 1
    fallback = report['gait_fallback']
    assert fallback['scale_undone'] is True
    assert fallback['scale_drift']['drift'] > gait_fallback.SCALE_DRIFT_MIN
    # The cells are the scaled-back frames: the strip's standing height does not grow.
    cells = sorted((output/'cycle').glob('frame-*.png'))
    heights = gait_fallback.subject_boxes([Image.open(p).convert('RGBA') for p in cells])
    heights = heights[:, 3]-heights[:, 1]
    # Filmed, the cycle grows by about 6 %; scaled back, it keeps the walker's own bob only.
    assert np.ptp(heights) <= 6
    assert not list(output.glob(f'.{loop.FALLBACK_FRAMES}*'))


@pytest.mark.slow
def test_no_cycle_in_either_search_says_so_and_keeps_the_gate_line(tmp_path):
    frames = [walker(0) for _ in range(73)]
    code, report, output = run(tmp_path, frames)
    assert str(code).startswith('video-loop: no periodic cycle found')
    assert 'also with the gait fallback' in str(code)
    assert report['status'] == 'failed' and report['gait_fallback']['scale_undone'] is False
    assert not list(output.glob(f'.{loop.FALLBACK_FRAMES}*'))


@pytest.mark.parametrize('n, fps, expected', [(73, 24.0, 43), (145, 24.0, 48), (37, 24.0, 22)])
def test_the_long_window_is_bounded_by_the_clip_and_by_seconds(n, fps, expected):
    assert gait_fallback.long_window(12, n, fps) == expected
