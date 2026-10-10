# SPDX-License-Identifier: Apache-2.0
"""Weapon motion must not change the size of an unchanged body in an image row."""
import json

import pytest
from PIL import Image, ImageDraw
from conftest import run_script
from sprite_gen.frames import extract


def attack_strip():
    strip = Image.new('RGBA', (720, 240))
    for i in range(3):
        pose = Image.new('RGBA', (240, 240))
        draw = ImageDraw.Draw(pose)
        draw.rectangle((20, 90, 79, 209), fill=(220, 30, 30, 255))
        if i == 1:  # lift a weapon above the unchanged body
            draw.rectangle((40, 10, 49, 89), fill=(30, 30, 220, 255))
        if i == 2:  # extend that weapon to the right
            draw.rectangle((80, 100, 204, 109), fill=(30, 30, 220, 255))
        strip.alpha_composite(pose, (i * 240, 0))
    return strip


def red_body_size(frame):
    mask = Image.new('L', frame.size)
    mask.putdata([255 if r > 180 and g < 80 and b < 80 and a > 128 else 0
                  for r, g, b, a in frame.get_flattened_data()])
    box = mask.getbbox()
    assert box is not None
    return box[2]-box[0], box[3]-box[1]


@pytest.mark.parametrize('route', ['components', 'slots'])
@pytest.mark.parametrize('resample', ['nearest', 'lanczos', 'kcentroid'])
@pytest.mark.parametrize('cell', [(128, 128), (192, 128), (128, 192)])
def test_weapon_pose_changes_preserve_body_scale(route, resample, cell):
    fn = extract.extract_component_frames if route == 'components' else extract.extract_slot_frames
    frames = fn(attack_strip(), 3, *cell, 12, 12,
                {'align_x': 'bbox-center', 'resample': resample})
    assert frames is not None and len(frames) == 3
    bodies = [red_body_size(frame) for frame in frames]
    for axis in (0, 1):
        assert max(size[axis] for size in bodies)-min(size[axis] for size in bodies) <= 1
    for frame in frames:
        assert frame.size == cell and frame.mode == 'RGBA'
        x0, y0, x1, y1 = frame.getbbox()
        assert x0 >= 12 and y0 >= 12 and x1 <= cell[0]-12 and y1 <= cell[1]-12


def test_cli_extract_preserves_body_scale(tmp_path):
    prepared = run_script('prepare_sprite_run.py', '--out-dir', str(tmp_path),
                          '--character-id', 'weapon-scale', '--cell-size', '128',
                          '--safe-margin', '12', '--no-fit-pixel-unfake',
                          '--fit-align-x', 'bbox-center', '--fit-resample', 'nearest',
                          '--chroma-key', '#00FF00', '--request-json', json.dumps({
                              'states': {'attack': {'frames': 3, 'fps': 8, 'loop': False}}}))
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    attack_strip().save(tmp_path / 'raw/attack.png')
    extracted = run_script('extract_sprite_row_frames.py', '--run-dir', str(tmp_path))
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    manifest = json.loads((tmp_path / 'frames/frames-manifest.json').read_text())
    row = manifest['rows'][0]
    assert manifest['ok'] and row['method'] == 'components'
    sizes = [red_body_size(Image.open(tmp_path / path).convert('RGBA')) for path in row['files']]
    assert len(sizes) == 3
    for axis in (0, 1):
        assert max(size[axis] for size in sizes) - min(size[axis] for size in sizes) <= 1
