"""The Sway layer under its new name and its old one.

`video-follow` became `video-sway` (the module `sprite_gen.video.sway`, `sway_loop`, `video-sway-inspect`): a soft
part of a looping body sways with the body. Nothing it does changed. The old names stay as aliases that give the old
results, their own name in what they say included, and a loop's record is read under either key. What is written
does not move in this release: the record stays under `follow` and the strip as cut stays `follow.source.png`, which
a consumer may read by those names.
"""
import json
import shutil

import pytest
from sprite_gen import cli
from sprite_gen.video import loop

from tests.video.test_follow import chest_region, loop_dir, walk_cut  # noqa: F401 (fixtures)
from tests.video.test_follow_inspect import CHEST, strip_dir

LOOP_FILES = ('walk.strip.png', 'walk.strip.json', 'walk.gif', 'walk.webp', 'follow.source.png')


def _files(directory):
    return {name: (directory/name).read_bytes() for name in LOOP_FILES}


def _twin(loop_dir, tmp_path):
    twin = tmp_path/'twin'/'loop'
    shutil.copytree(loop_dir.parent, twin.parent)
    return twin


@pytest.mark.slow
def test_the_new_verb_and_the_old_one_write_the_same_bytes(loop_dir, tmp_path):
    region = ','.join(f'{v:g}' for v in chest_region(loop_dir))
    twin = _twin(loop_dir, tmp_path)
    assert cli.main(['video-sway', '--loop-dir', str(loop_dir), '--region', region]) == 0
    assert cli.main(['video-follow', '--loop-dir', str(twin), '--region', region]) == 0
    assert _files(loop_dir) == _files(twin), "video-sway and video-follow wrote different files"
    meta = json.loads((loop_dir/'walk.strip.json').read_text())
    assert 'follow' in meta and 'sway' not in meta, "the record is written under `follow` in this release"


def test_the_old_module_and_function_names_reach_the_sway_layer(loop_dir, tmp_path):
    from sprite_gen.video import follow, sway

    region = chest_region(loop_dir)
    twin = _twin(loop_dir, tmp_path)
    new = sway.sway_loop(loop_dir, [region])
    old = follow.follow_loop(twin, [region])
    assert {k: v for k, v in new.items() if k != 'strip'} == {k: v for k, v in old.items() if k != 'strip'}
    assert _files(loop_dir) == _files(twin)
    assert follow.SOURCE == sway.SOURCE == 'follow.source.png'
    assert follow.body_motion is sway.body_motion and follow.POLICY == sway.POLICY
    assert (follow.follow_offsets is sway.sway_offsets) or \
        (follow.follow_offsets.__wrapped__ is sway.sway_offsets)  # the same function, under its old name


def test_each_name_says_its_own_name_when_it_refuses(loop_dir):
    from sprite_gen.video import follow, sway

    with pytest.raises(SystemExit, match=r'^video-sway: at least one --region cx,cy,rx,ry$'):
        sway.sway_loop(loop_dir, [])
    with pytest.raises(SystemExit, match=r'^video-follow: at least one --region cx,cy,rx,ry$'):
        follow.follow_loop(loop_dir, [])
    asked = ['--loop-dir', str(loop_dir), '--out-dir', str(loop_dir.parent/'out'), '--region', '60,72,5,5', '--gain', '-1']
    with pytest.raises(SystemExit, match=r'^video-sway-inspect: --gain must be 0 or more'):
        cli.main(['video-sway-inspect', *asked])
    with pytest.raises(SystemExit, match=r'^video-follow-inspect: --gain must be 0 or more'):
        cli.main(['video-follow-inspect', *asked])


def test_the_new_inspect_verb_and_the_old_one_write_the_same_record(tmp_path):
    loop_dir = strip_dir(tmp_path/'loop')
    region = ','.join(f'{v:g}' for v in CHEST)
    assert cli.main(['video-sway-inspect', '--loop-dir', str(loop_dir), '--region', region,
                     '--out-dir', str(tmp_path/'new')]) == 0
    assert cli.main(['video-follow-inspect', '--loop-dir', str(loop_dir), '--region', region,
                     '--out-dir', str(tmp_path/'old')]) == 0
    for name in ('follow-inspect.json', 'follow-inspect.source.png', 'follow-inspect.moved.png'):
        assert (tmp_path/'new'/name).read_bytes() == (tmp_path/'old'/name).read_bytes(), name


def test_a_record_under_sway_is_read_as_one_under_follow(tmp_path):
    """A loop whose record sits under `sway` (written by hand, or by a later release) is a loop whose sway is made,
    exactly as under `follow`: the inspection reads it as recorded and keys the cut the same."""
    from sprite_gen.video import sway, sway_inspect

    loop_dir = strip_dir(tmp_path/'loop')
    sway.sway_loop(loop_dir, [CHEST])
    under_follow = sway_inspect.inspect_loop(loop_dir, [CHEST], tmp_path/'a')
    meta_path = loop_dir/'walk.strip.json'
    meta = json.loads(meta_path.read_text())
    meta['sway'] = meta.pop('follow')
    meta_path.write_text(json.dumps(meta, indent=2) + '\n')
    under_sway = sway_inspect.inspect_loop(loop_dir, [CHEST], tmp_path/'b')
    assert under_sway['inputs']['meta']['follow_recorded'] is True
    assert under_sway['inputs']['meta']['cut_sha256'] == under_follow['inputs']['meta']['cut_sha256']
    assert loop.sway_recorded(meta) and loop.sway_recorded({'follow': {}}) and not loop.sway_recorded({'steps': 2})


def test_sway_again_over_a_record_under_sway_keeps_one_record(tmp_path):
    from sprite_gen.video import sway

    loop_dir = strip_dir(tmp_path/'loop')
    sway.sway_loop(loop_dir, [CHEST])
    meta_path = loop_dir/'walk.strip.json'
    meta = json.loads(meta_path.read_text())
    meta['sway'] = meta.pop('follow')
    meta_path.write_text(json.dumps(meta, indent=2) + '\n')
    sway.sway_loop(loop_dir, [CHEST])
    again = json.loads(meta_path.read_text())
    assert 'follow' in again and 'sway' not in again, "two records of one sway"


def test_help_names_the_sway_verbs_and_the_old_names_still_run():
    groups = cli.command_domains()
    assert 'video-sway' in groups['video'] and 'video-sway-inspect' in groups['video']
    assert 'video-follow' not in cli.COMMANDS and 'video-follow-inspect' not in cli.COMMANDS
    parser = cli._build_parser()
    assert parser.parse_args(['video-follow', '--loop-dir', '.', '--region', '1,1,1,1']).command == 'video-follow'
