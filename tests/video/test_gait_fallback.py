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


def run(tmp_path, frames, *extra):
    keyed = tmp_path/'keyed'
    keyed.mkdir()
    for k, image in enumerate(frames):
        image.save(keyed/f'{k:03}.png')
    output = tmp_path/'out'
    args = ['--frames-dir', str(keyed), '--out-dir', str(output), '--state', 'walk', '--anchor', 'motion-auto',
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


def test_a_walk_toward_the_camera_is_scaled_back_before_the_second_search(tmp_path):
    frames = [walker(k, grow=0.2) for k in range(73)]
    code, report, output = run(tmp_path, frames)
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
