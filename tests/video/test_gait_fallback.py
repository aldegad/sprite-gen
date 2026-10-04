"""The gait fallback: a slow walk and a walk toward the camera, on synthetic frames."""
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen.video import gait_fallback, loop

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


def run(tmp_path, frames, *extra):
    keyed = tmp_path/'keyed'
    keyed.mkdir()
    for k, image in enumerate(frames):
        image.save(keyed/f'{k:03}.png')
    output = tmp_path/'out'
    args = ['--frames-dir', str(keyed), '--out-dir', str(output), '--state', 'walk', '--anchor', 'motion-auto', '--repair', 'off',
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


def test_a_clip_the_first_search_cuts_is_cut_without_the_fallback(tmp_path):
    code, report, output = run(tmp_path, [walker(k) for k in range(73)])
    assert code == 0
    assert report['cycle']['length'] == 24
    assert 'gait_fallback' not in report
    assert not (output/loop.FALLBACK_FRAMES_DIR).exists()


def test_a_cycle_longer_than_half_the_clip_is_found_by_the_fallback(tmp_path):
    frames = [walker(k, period=40) for k in range(73)]
    code, report, output = run(tmp_path, frames)
    assert code == 0
    assert abs(report['cycle']['length']-40) <= 1
    fallback = report['gait_fallback']
    assert 'no periodic cycle found' in fallback['reason']
    assert fallback['scale_undone'] is False
    assert fallback['window'] == [12, gait_fallback.long_window(12, 73, 24.0)]
    assert not (output/loop.FALLBACK_FRAMES_DIR).exists()


def test_an_explicit_max_len_stays_the_ceiling(tmp_path):
    code, report, _ = run(tmp_path, [walker(k, period=40) for k in range(73)], '--max-len', '36')
    assert str(code).startswith('video-loop: no periodic cycle found')
    assert report['gait_fallback']['window'] == [12, 36]


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
    assert not (output/loop.FALLBACK_FRAMES_DIR).exists()


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


def test_a_clip_that_keeps_its_size_is_not_held(tmp_path):
    code, report, output = run(tmp_path, [walker(k) for k in range(73)])
    assert code == 0
    assert report['size_hold']['applied'] is False
    assert abs(report['size_hold']['drift']) < gait_fallback.SIZE_HOLD_MIN
    assert 'padding_ltrb' not in report['size_hold']


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


def test_the_held_loop_of_a_shrinking_walk_keeps_its_crown(tmp_path):
    frames = [walking_away(k) for k in range(73)]
    code, report, output = run(tmp_path, frames)
    assert code == 0
    assert report['size_hold']['applied'] is True and report['size_hold']['padding_ltrb'][1] > 0
    first = frames[0].getchannel('A').getbbox()
    cells = sorted((output/'cycle').glob('frame-*.png'))
    boxes = gait_fallback.subject_boxes([Image.open(p).convert('RGBA') for p in cells])
    # Every cell stands about as tall as the first frame did: no cell lost its crown.
    assert np.all(np.abs((boxes[:, 3]-boxes[:, 1])-(first[3]-first[1])) <= 6)


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
    assert not (output/loop.FALLBACK_FRAMES_DIR).exists()


def test_no_cycle_in_either_search_says_so_and_keeps_the_gate_line(tmp_path):
    frames = [walker(0) for _ in range(73)]
    code, report, output = run(tmp_path, frames)
    assert str(code).startswith('video-loop: no periodic cycle found')
    assert 'also with the gait fallback' in str(code)
    assert report['status'] == 'failed' and report['gait_fallback']['scale_undone'] is False
    assert not (output/loop.FALLBACK_FRAMES_DIR).exists()


@pytest.mark.parametrize('n, fps, expected', [(73, 24.0, 43), (145, 24.0, 48), (37, 24.0, 22)])
def test_the_long_window_is_bounded_by_the_clip_and_by_seconds(n, fps, expected):
    assert gait_fallback.long_window(12, n, fps) == expected
