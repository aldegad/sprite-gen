# SPDX-License-Identifier: Apache-2.0
"""The shared read-only adapter: real writer formats, one-read snapshots, bound fingerprints."""

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from conftest import run_script
from sprite_gen.spec import assets
from sprite_gen.spec.assets import FrameSequence, load_asset, same_identity, sequence_identity
from sprite_gen.video.loop import build_strip


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def walk_frames(xs=(24, 22, 20, 18, 16)):
    frames = []
    for x in xs:
        frame = Image.new("RGBA", (48, 40))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((15, 5, 30, 20), fill="white")
        draw.rectangle((x, 30, x + 3, y := 32), fill=(200, 40, 40, 255))
        frames.append(frame)
    return frames


def descriptor(tmp_path, *, anchor=None, durations=(0.1, 0.1, 0.1, 0.1, 0.1), files=None):
    entries = []
    for index, (frame, duration) in enumerate(zip(walk_frames(), durations)):
        name = f"foot-{index}.png" if files is None else files[index]
        path = tmp_path / name
        if not path.exists():
            frame.save(path)
        entries.append({"file": name, "duration": duration})
    data = {"kind": "sprite-gen-asset", "version": 1, "frames": entries}
    if anchor is not None:
        data["anchor"] = anchor
    path = tmp_path / "walk.json"
    path.write_text(json.dumps(data))
    return path


def atlas(tmp_path, states=("walk", "idle")):
    frames = walk_frames()
    sheet = Image.new("RGBA", (48 * 5, 40))
    for index, frame in enumerate(frames):
        sheet.paste(frame, (48 * index, 0))
    sheet.save(tmp_path / "atlas.png")
    rects = [{"x": 48 * i, "y": 0, "w": 48, "h": 40} for i in range(5)]
    rows = {"walk": rects, "idle": [rects[0]] * 3}
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps({
        "sprite_sheet_alpha": "atlas.png", "game_input": "atlas.png",
        "frame_layout": {"rows": {s: rows[s] for s in states}},
        "animation": {"rows": {s: {"fps": 10, "durations_ms": [100] * len(rows[s]), "loop": True} for s in states}},
    }))
    return path


def test_png_single_frame_defaults_to_one_second_and_bottom_centre(tmp_path):
    source = tmp_path / "still.png"
    walk_frames()[0].save(source)
    seq = load_asset(source)
    assert len(seq.frames) == 1 and seq.frames[0].mode == "RGBA"
    assert seq.durations == (1.0,)
    assert seq.anchor == (24.0, 40)
    assert seq.metadata == {"source": str(source.resolve()), "anchor_source": "bottom-center default"}
    assert seq.fingerprints() == {str(source.resolve()): sha(source)}
    with pytest.raises(ValueError, match="state"):
        load_asset(source, state="walk")


def test_descriptor_reads_ordered_frames_durations_anchor_and_every_file_hash(tmp_path):
    source = descriptor(tmp_path, anchor=[12, 38], durations=(0.05, 0.15, 0.1, 0.1, 0.4))
    seq = load_asset(source)
    assert seq.durations == (0.05, 0.15, 0.1, 0.1, 0.4)
    assert seq.duration == pytest.approx(0.8)
    assert seq.anchor == (12.0, 38.0)
    assert seq.metadata["anchor_source"] == "asset metadata"
    expected = walk_frames()
    for frame, original in zip(seq.frames, expected):
        assert frame.tobytes() == original.tobytes()
    assert seq.fingerprints() == {str((tmp_path / name).resolve()): sha(tmp_path / name)
                                  for name in ["walk.json", *(f"foot-{i}.png" for i in range(5))]}
    assert load_asset(source, anchor=(1, 2)).metadata["anchor_source"] == "explicit override"
    with pytest.raises(ValueError, match="state"):
        load_asset(source, state="walk")


def test_descriptor_referencing_one_file_twice_reads_it_once(tmp_path, monkeypatch):
    source = descriptor(tmp_path, files=["a.png", "a.png", "b.png", "b.png", "b.png"])
    reads = []
    original = assets._read_bytes

    def counting_read(path):
        reads.append(Path(path).name)
        return original(path)

    monkeypatch.setattr(assets, "_read_bytes", counting_read)
    seq = load_asset(source)
    assert len(seq.frames) == 5
    assert sorted(reads) == ["a.png", "b.png", "walk.json"]
    assert set(seq.fingerprints()) == {str((tmp_path / n).resolve()) for n in ("walk.json", "a.png", "b.png")}


def test_real_loop_strip_writer_output_is_read_back_cell_by_cell(tmp_path):
    frames = walk_frames()
    strip, meta = build_strip(frames, cycle_seconds=0.5)
    strip.save(tmp_path / "walk.strip.png")
    (tmp_path / "walk.strip.json").write_text(json.dumps(meta))
    seq = load_asset(tmp_path / "walk.strip.json")
    assert seq.size == (meta["w"], meta["h"])
    assert len(seq.frames) == meta["frames"]
    assert seq.durations == (meta["delay_ms"] / 1000,) * meta["frames"]
    assert seq.duration == pytest.approx(0.5)
    assert seq.metadata["loop"] is True
    assert seq.anchor == (meta["w"] / 2, meta["h"])
    for index, frame in enumerate(seq.frames):
        assert frame.tobytes() == strip.crop((index * meta["w"], 0, (index + 1) * meta["w"], meta["h"])).tobytes()
    assert set(seq.fingerprints()) == {str((tmp_path / n).resolve()) for n in ("walk.strip.json", "walk.strip.png")}
    with pytest.raises(ValueError, match="state"):
        load_asset(tmp_path / "walk.strip.json", state="walk")
    strip.crop((0, 0, strip.width - 1, strip.height)).save(tmp_path / "walk.strip.png")
    with pytest.raises(ValueError, match="disagrees"):
        load_asset(tmp_path / "walk.strip.json")


def test_real_runtime_atlas_writer_output_keeps_rects_and_timing(fixture_run_dir):
    for script in ("extract_sprite_row_frames.py", "compose_sprite_atlas.py"):
        proc = run_script(script, "--run-dir", str(fixture_run_dir))
        assert proc.returncode == 0, proc.stdout + proc.stderr
    manifest_path = fixture_run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    with pytest.raises(ValueError, match="--state is required"):
        load_asset(manifest_path)
    with pytest.raises(ValueError, match="unknown asset state"):
        load_asset(manifest_path, state="fly")
    with Image.open(fixture_run_dir / manifest["sprite_sheet_alpha"]) as sheet:
        sheet = sheet.convert("RGBA")
        for state, rects in manifest["frame_layout"]["rows"].items():
            seq = load_asset(manifest_path, state=state)
            timing = manifest["animation"]["rows"][state]
            assert len(seq.frames) == len(rects)
            assert seq.durations == tuple(ms / 1000 for ms in timing["durations_ms"])
            assert seq.metadata["state"] == state
            assert seq.metadata["loop"] is bool(timing["loop"])
            for frame, rect in zip(seq.frames, rects):
                assert frame.size == (rect["w"], rect["h"])
                assert frame.tobytes() == sheet.crop((rect["x"], rect["y"], rect["x"] + rect["w"], rect["y"] + rect["h"])).tobytes()
            assert seq.fingerprints() == {str(manifest_path.resolve()): sha(manifest_path),
                                          str((fixture_run_dir / manifest["sprite_sheet_alpha"]).resolve()): sha(fixture_run_dir / manifest["sprite_sheet_alpha"])}


def test_fps_override_replaces_timing_and_is_part_of_identity(tmp_path):
    source = atlas(tmp_path)
    native = load_asset(source, state="walk")
    faster = load_asset(source, state="walk", fps=5)
    assert native.durations == (0.1,) * 5
    assert faster.durations == (0.2,) * 5
    assert faster.metadata["fps_override"] == 5.0
    assert "fps_override" not in native.metadata
    assert native.fingerprints() == faster.fingerprints()
    assert sequence_identity(native) != sequence_identity(faster)


def test_identity_separates_states_rates_and_anchors_of_one_file_set(tmp_path):
    source = atlas(tmp_path)
    walk = load_asset(source, state="walk")
    idle = load_asset(source, state="idle")
    assert walk.fingerprints() == idle.fingerprints()
    identity = json.loads(json.dumps(walk.identity()))
    assert identity == {"source": str(source.resolve()), "state": "walk", "fps_override": None, "frame_count": 5,
                        "size": [48, 40], "anchor_px": [24.0, 40.0], "durations_seconds": [0.1] * 5}
    assert same_identity(identity, walk)
    assert not same_identity(identity, idle)
    assert not same_identity(identity, load_asset(source, state="walk", fps=10.0001))
    assert same_identity(identity, load_asset(source, state="walk", fps=10))
    assert not same_identity(identity, load_asset(source, state="walk", anchor=(24, 39)))
    assert not same_identity({**identity, "durations_seconds": [0.1] * 4 + [0.2]}, walk)
    assert not same_identity({k: v for k, v in identity.items() if k != "state"}, walk)
    assert not same_identity(None, walk)


def test_fingerprints_describe_the_decoded_bytes_not_the_file_now_on_disk(tmp_path):
    source = tmp_path / "swap.png"
    Image.new("RGBA", (4, 4), "red").save(source)
    red_hash = sha(source)
    loaded = load_asset(source)
    Image.new("RGBA", (4, 4), "blue").save(source)
    assert loaded.frames[0].getpixel((0, 0)) == (255, 0, 0, 255)
    assert loaded.fingerprints() == {str(source.resolve()): red_hash}
    assert loaded.fingerprints() != load_asset(source).fingerprints()
    assert load_asset(source).fingerprints() == {str(source.resolve()): sha(source)}


def test_replacement_between_read_and_decode_cannot_separate_pixels_from_hash(tmp_path, monkeypatch):
    source = tmp_path / "race.png"
    Image.new("RGBA", (4, 4), "red").save(source)
    red_hash = sha(source)
    original = assets._read_bytes

    def read_then_replace(path):
        data = original(path)
        Image.new("RGBA", (4, 4), "blue").save(path)
        return data

    monkeypatch.setattr(assets, "_read_bytes", read_then_replace)
    loaded = load_asset(source)
    assert loaded.frames[0].getpixel((0, 0)) == (255, 0, 0, 255)
    assert loaded.fingerprints() == {str(source.resolve()): red_hash}
    assert sha(source) != red_hash


def test_descriptor_identity_is_the_parsed_bytes(tmp_path, monkeypatch):
    source = descriptor(tmp_path)
    original_hash = sha(source)
    original = assets._read_bytes

    def read_then_edit(path):
        data = original(path)
        if Path(path).name == "walk.json":
            edited = json.loads(data)
            edited["frames"] = edited["frames"][:2]
            Path(path).write_text(json.dumps(edited))
        return data

    monkeypatch.setattr(assets, "_read_bytes", read_then_edit)
    loaded = load_asset(source)
    assert len(loaded.frames) == 5
    assert loaded.fingerprints()[str(source.resolve())] == original_hash
    assert sha(source) != original_hash


def test_animated_and_oversized_images_are_refused_before_decoding(tmp_path, monkeypatch):
    animated = tmp_path / "animated.png"
    Image.new("RGBA", (4, 4), "red").save(animated, save_all=True, append_images=[Image.new("RGBA", (4, 4), "blue")], duration=50)
    with pytest.raises(ValueError, match="animated"):
        load_asset(animated)
    monkeypatch.setattr(assets, "MAX_SOURCE_PIXELS", 15)
    still = tmp_path / "still.png"
    Image.new("RGBA", (4, 4)).save(still)
    with pytest.raises(ValueError, match="exceeds the supported"):
        load_asset(still)
    monkeypatch.setattr(assets, "MAX_SOURCE_PIXELS", 16)
    assert load_asset(still).size == (4, 4)


def test_frame_at_wraps_when_looping_and_holds_the_last_frame_otherwise(tmp_path):
    seq = load_asset(descriptor(tmp_path, durations=(0.1, 0.2, 0.3, 0.1, 0.3)))
    starts = [0, 0.1, 0.3, 0.6, 0.7]
    for index, start in enumerate(starts):
        assert seq.frame_at(start) is seq.frames[index]
        assert seq.frame_at(start + 0.05) is seq.frames[index]
    assert seq.frame_at(1.0) is seq.frames[0]
    assert seq.frame_at(1.15) is seq.frames[1]
    assert seq.frame_at(-0.05) is seq.frames[4]
    assert seq.frame_at(1.0, loop=False) is seq.frames[4]
    assert seq.frame_at(-1, loop=False) is seq.frames[0]
    with pytest.raises(ValueError):
        seq.frame_at(float("nan"))


@pytest.mark.parametrize("mutate, message", [
    (lambda d: [1, 2], "must be an object"),
    (lambda d: {**d, "frames": []}, "nonempty"),
    (lambda d: {**d, "frames": [{"file": "foot-0.png"}]}, "duration"),
    (lambda d: {**d, "frames": [{"file": "foot-0.png", "duration": 0}]}, "duration"),
    (lambda d: {**d, "frames": [{"duration": 0.1}]}, "file and duration"),
    (lambda d: {**d, "version": 2}, "version 1"),
    (lambda d: {"kind": "something-else"}, "unsupported asset descriptor"),
    (lambda d: {**d, "anchor": [1]}, "anchor"),
])
def test_invalid_descriptors_are_rejected(tmp_path, mutate, message):
    source = descriptor(tmp_path)
    source.write_text(json.dumps(mutate(json.loads(source.read_text()))))
    with pytest.raises(ValueError, match=message):
        load_asset(source)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["frame_layout"]["rows"]["walk"].append({"x": 48 * 5, "y": 0, "w": 48, "h": 40}), "outside sheet"),
    (lambda d: d["frame_layout"]["rows"]["walk"].append({"x": 0, "y": 0, "w": 48.5, "h": 40}), "integer"),
    (lambda d: d["frame_layout"]["rows"]["walk"].__setitem__(0, {"x": 0.5, "y": 0, "w": 48, "h": 40}), "outside sheet"),
    (lambda d: d["animation"]["rows"]["walk"].update({"durations_ms": [0] * 5}), "duration_ms"),
    (lambda d: d["frame_layout"].update({"rows": {}}), "no animation states"),
])
def test_invalid_atlas_manifests_are_rejected(tmp_path, mutate, message):
    source = atlas(tmp_path, states=("walk",))
    data = json.loads(source.read_text())
    mutate(data)
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        load_asset(source)


def test_frame_sequence_positional_contract_defaults_anchor_and_keeps_digests_keyword_only(tmp_path):
    frame = Image.new("RGBA", (6, 4))
    bare = FrameSequence((frame,), (1.0,))
    assert bare.anchor == (3.0, 4.0)
    assert bare.source_files == () and bare.metadata == {} and bare.source_digests == ()
    assert bare.fingerprints() == {}
    assert bare.identity() == {"source": None, "state": None, "fps_override": None, "frame_count": 1,
                               "size": [6, 4], "anchor_px": [3.0, 4.0], "durations_seconds": [1.0]}
    positional = FrameSequence((frame,), (0.5,), [1, 2], (), {"source": "memory"})
    assert positional.anchor == (1.0, 2.0) and positional.metadata == {"source": "memory"}
    with pytest.raises(ValueError, match="SHA-256"):
        FrameSequence((frame,), (1.0,), None, (tmp_path / "x.png",), {})
    with pytest.raises(TypeError):
        FrameSequence((frame,), (1.0,), None, (tmp_path / "x.png",), {}, ("digest",))
    with pytest.raises(ValueError, match="anchor"):
        FrameSequence((frame,), (1.0,), (1, float("nan")))
    backed = FrameSequence((frame,), (1.0,), None, (tmp_path / "x.png",), {}, source_digests=("abc",))
    assert backed.fingerprints() == {str(tmp_path / "x.png"): "abc"}
