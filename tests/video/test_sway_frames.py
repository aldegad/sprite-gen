"""`video-sway --frames <folder> --out <new folder>`: the Sway layer on a cycle's frames at their own size and count.

A strip holds 64 cells and 32,000 px across at most, so a cycle of large frames is cut down to fit it. The frames path
sways the cycle's frames themselves. It is the strip's sway cell for cell: a strip cut from the same frames at scale 1
with none left out, its regions moved by its crop origin, at the frame time its meta writes, moves its cells as the
frames are moved. On a synthetic body that bobs and sways, textured, with colour under its clear pixels.
"""
import json
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sprite_gen import cli
from sprite_gen.video import loop, sway

N, SIZE = 24, 200
TORSO = (100.0, 125.0, 30.0, 40.0)  # the region, in frame pixels: over the torso in frame 0
FILES = [f'frame-{k:03d}.png' for k in range(N)]


def frame(k, n=N):
    """A body that bobs twice a cycle and sways once, an arm swinging: noise-textured everywhere, colour under the clear
    pixels too, so a move shows and a clear pixel's colour is there to keep or lose."""
    texture = (np.random.default_rng(7).integers(0, 60, (SIZE, SIZE, 1)) + np.array([150, 110, 90])).clip(0, 255)
    t = 2*math.pi*k/n
    dy, dx = round(6*(0.5-0.5*math.cos(2*t))), round(3*math.sin(t))
    mask = Image.new('L', (SIZE, SIZE), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((60+dx, 70+dy, 140+dx, 180+dy), fill=255)  # torso
    d.ellipse((78+dx, 30+dy, 122+dx, 74+dy), fill=255)  # head
    d.line((130+dx, 100+dy, 160+dx, 150+dy+15*math.sin(t)), fill=255, width=9)  # an arm
    return Image.fromarray(np.dstack([np.roll(texture, (dy, dx), (0, 1)), np.asarray(mask)]).astype(np.uint8), 'RGBA')


def frames_dir(path, n=N):
    path.mkdir(parents=True)
    for k in range(n):
        frame(k, n).save(path/f'frame-{k:03d}.png')
    return path


def rgba(path):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGBA'))


def as_strip_cell(image, rect):
    """A frame as the strip holds it at scale 1: cropped at its source rect and laid on a clear cell (build_strip)."""
    cell = Image.new('RGBA', (rect[2]-rect[0], rect[3]-rect[1]), (0, 0, 0, 0))
    cell.alpha_composite(image.crop(tuple(rect)))
    return np.asarray(cell)


def cut(tmp_path, frames):
    """A loop directory holding the frames cut as video-loop cuts them, at scale 1, none left out."""
    strip, meta = loop.build_strip(frames, cycle_seconds=1.0, max_height=520)
    assert meta['scale'] == 1 and meta['frames'] == len(frames) and meta['delay_ms'] == 41.67
    directory = tmp_path/'loop'
    directory.mkdir()
    strip.save(directory/'walk.strip.png')
    (directory/'walk.strip.json').write_text(json.dumps(meta))
    return directory, meta


@pytest.fixture(scope='module')
def swayed_frames(tmp_path_factory):
    """The cycle's frames and those frames swayed by `video-sway --frames --fps 24 --on-fold lower` on the torso, made once
    for the module (four cases read that same sway): the frames folder and the new folder, which no case writes."""
    root = tmp_path_factory.mktemp('frames')
    src, out = frames_dir(root/'cycle-frames'), root/'swayed'
    assert cli.main(['video-sway', '--frames', str(src), '--out', str(out), '--region', ','.join(f'{v:g}' for v in TORSO),
                     '--fps', '24', '--on-fold', 'lower']) == 0
    return src, out


def test_the_frames_are_swayed_as_the_strip_cut_from_them_at_scale_1(swayed_frames, tmp_path):
    src, out = swayed_frames
    frames = [frame(k) for k in range(N)]
    loop_dir, meta = cut(tmp_path, frames)
    left, top = meta['source_rect'][:2]
    in_strip = (TORSO[0]-left, TORSO[1]-top, TORSO[2], TORSO[3])
    strip_record = sway.sway_loop(loop_dir, [in_strip], on_fold='lower')
    assert sorted(p.name for p in out.iterdir()) == sorted([*FILES, 'sway.json'])
    record = json.loads((out/'sway.json').read_text())
    for key in ('gain', 'dx_px', 'dy_px', 'reach_px', 'body_bob_px', 'fold', 'freq_hz', 'zeta'):
        assert record[key] == strip_record[key], key
    assert record['regions'] == [list(TORSO)] and record['frames'] == N and record['frame_ms'] == 41.67
    strip = rgba(loop_dir/'walk.strip.png')
    w = meta['w']
    moved = 0
    for k, name in enumerate(FILES):
        with Image.open(out/name) as im:
            cell = as_strip_cell(im.convert('RGBA'), meta['source_rect'])
        assert np.array_equal(cell, strip[:, k*w:(k+1)*w]), f'cell {k} differs from the strip path'
        moved += not np.array_equal(rgba(out/name), rgba(src/name))
    assert moved > N//2, 'the frames path moved nothing'


def test_the_frame_time_is_the_one_a_strip_meta_writes(swayed_frames):
    """--fps 24 is read as the strip meta writes 24 frames a second, 41.67 ms: the exact 1000/24 ms is another sway."""
    _, out = swayed_frames
    frames = [frame(k) for k in range(N)]
    ask = sway.read_ask([TORSO], on_fold='lower')
    rounded, exact = sway.solve_cells(ask, frames, 41.67), sway.solve_cells(ask, frames, 1000/24)
    written = [rgba(out/name) for name in FILES]
    assert all(np.array_equal(np.asarray(rounded.moved(k)), written[k]) for k in range(N))
    assert any(not np.array_equal(np.asarray(exact.moved(k)), written[k]) for k in range(N))


def test_a_loops_cycle_folder_carries_its_own_frame_time(swayed_frames, tmp_path):
    loop_dir, _ = cut(tmp_path, [frame(k) for k in range(N)])
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    assert (meta['cycle_frames'], meta['cycle_seconds']) == (N, 1.0)
    cycle = frames_dir(loop_dir/'cycle')
    region = ','.join(f'{v:g}' for v in TORSO)
    assert cli.main(['video-sway', '--frames', str(cycle), '--out', str(tmp_path/'a'), '--region', region, '--on-fold', 'lower']) == 0
    _, at_24 = swayed_frames  # the same frames swayed as `a` is, but with --fps 24 (in a folder of their own)
    assert all((tmp_path/'a'/name).read_bytes() == (at_24/name).read_bytes() for name in FILES)
    assert json.loads((tmp_path/'a'/'sway.json').read_text())['frame_ms'] == 41.67
    with pytest.raises(SystemExit, match=r'--fps 30 is a frame of 33\.33 ms.*41\.67 ms'):
        cli.main(['video-sway', '--frames', str(cycle), '--out', str(tmp_path/'c'), '--region', region, '--fps', '30'])
    assert not (tmp_path/'c').exists()


def test_the_old_name_sways_frames_alike(swayed_frames, tmp_path):
    src, by_new_name = swayed_frames  # the frames swayed by `video-sway`, as asked here
    region = ','.join(f'{v:g}' for v in TORSO)
    assert cli.main(['video-follow', '--frames', str(src), '--out', str(tmp_path/'video-follow'), '--region', region, '--fps', '24',
                     '--on-fold', 'lower']) == 0
    for name in (*FILES, 'sway.json'):
        assert (by_new_name/name).read_bytes() == (tmp_path/'video-follow'/name).read_bytes(), name


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() if p.is_file() else None for p in sorted(path.rglob('*'))}


def test_what_the_frames_path_refuses_it_refuses_before_writing_anything(tmp_path):
    src = frames_dir(tmp_path/'cycle-frames')
    region = ','.join(f'{v:g}' for v in TORSO)
    full = tmp_path/'full'
    full.mkdir()
    (full/'note.txt').write_text('mine\n')
    cases = [
        (['--out', str(src/'out'), '--fps', '24'], 'out 이 frames 안'),
        (['--out', str(tmp_path), '--fps', '24'], 'frames 가 out 안'),
        (['--out', str(full), '--fps', '24'], '비어 있지 않다'),
        (['--out', str(tmp_path/'new')], '--fps'),  # the damped mass needs the frames' time
        (['--out', str(tmp_path/'new'), '--fps', '24', '--read-on', 'a'*64], '--read-on'),
        (['--fps', '24'], '--frames needs --out'),
        (['--out', str(tmp_path/'new'), '--fps', '24', '--region', '250,100,10,10'], 'outside the 200x200 cell'),
    ]
    before = snapshot(tmp_path)
    for extra, why in cases:
        asked = ['--region', region] if '--region' not in extra else []
        with pytest.raises(SystemExit, match=why):
            cli.main(['video-sway', '--frames', str(src), *asked, *extra])
        assert snapshot(tmp_path) == before, f'refused ({why}) and something changed'
    loop_dir = tmp_path/'loop'
    with pytest.raises(SystemExit, match='--out goes with --frames'):
        cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', region, '--out', str(tmp_path/'new')])


def test_frames_of_two_sizes_are_refused(tmp_path):
    src = frames_dir(tmp_path/'cycle-frames')
    frame(3).resize((SIZE, SIZE+2)).save(src/FILES[3])
    with pytest.raises(SystemExit, match='프레임 크기가 서로 다르다'):
        cli.main(['video-sway', '--frames', str(src), '--out', str(tmp_path/'out'), '--region', '100,125,30,40', '--fps', '24'])
    assert not (tmp_path/'out').exists()
