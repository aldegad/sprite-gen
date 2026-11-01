# SPDX-License-Identifier: Apache-2.0
"""The two models RIFE runs (docs/loop-repair.md section 1): `rife-v4.25`, the default, with no augmentation, each image
handed to the binary padded with zeros to a multiple of 128 (`rife.Model.pad`); and `rife-v4.6`, chosen by name
(SPRITE_GEN_RIFE_MODEL_NAME), with `-x -z` and each image handed as it is — what 3.14.0 handed the binary, so its frames
are the old ones. A model named wrong, or a model directory named as another, is refused before anything runs.

A stand-in binary keeps each image it is handed and returns its first, so the tests need no RIFE and see what the binary
saw."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import rife
from tests.video.test_rife import _disc, _marked


def _keeping_binary(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in binary that keeps every pair it is handed (`in-<n>-0.png`, `in-<n>-1.png`, its arguments in `argv.txt`)
    and returns its first image: (binary, the directory it keeps them in)."""
    kept = tmp_path / "kept"
    kept.mkdir(parents=True)
    binary = tmp_path / rife.BINARY
    binary.write_text(f'#!/bin/sh\nk="{kept}"\necho "$@" >> "$k/argv.txt"\nn=$(wc -l < "$k/argv.txt" | tr -d " ")\n'
                      'while [ $# -gt 0 ]; do case "$1" in -0) a="$2"; shift;; -1) b="$2"; shift;; -o) o="$2"; shift;; esac; shift; done\n'
                      'cp "$a" "$k/in-$n-0.png"\ncp "$b" "$k/in-$n-1.png"\ncp "$a" "$o"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary, kept


def _handed(kept: Path) -> list[tuple[np.ndarray, np.ndarray, list[str]]]:
    """Each call's two images as the binary was handed them, and its arguments."""
    calls = [line.split() for line in (kept / "argv.txt").read_text().splitlines()]
    return [(np.asarray(Image.open(kept / f"in-{n}-0.png").convert("RGB")), np.asarray(Image.open(kept / f"in-{n}-1.png").convert("RGB")),
             argv) for n, argv in enumerate(calls, 1)]


def _model_dir(tmp_path: Path, name: str) -> Path:
    model = tmp_path / name
    model.mkdir(parents=True)
    for f in ("flownet.param", "flownet.bin"):
        (model / f).write_bytes(b"x")
    return model


@pytest.fixture
def unnamed(monkeypatch):
    for name in ("SPRITE_GEN_RIFE_MODEL_NAME", "SPRITE_GEN_RIFE_MODEL", "SPRITE_GEN_RIFE_TTA"):
        monkeypatch.delenv(name, raising=False)


def test_the_default_model_is_rife_v4_25_with_no_augmentation_and_rife_v4_6_keeps_both(unnamed, monkeypatch, tmp_path):
    """Nothing named, the model is rife-v4.25 and every call asks for no augmentation (TNTwise's README marks -x and -z
    deprecated with it); rife-v4.6 named, every call asks for -x -z as before. Each is found beside the binary."""
    assert rife.MODEL == rife.model_name() == "rife-v4.25" and set(rife.MODELS) == {"rife-v4.25", "rife-v4.6"}
    binary, kept = _keeping_binary(tmp_path)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    for name in rife.MODELS:
        _model_dir(binary.parent, name)
    assert rife.locate() == (binary.resolve(), binary.resolve().parent / "rife-v4.25")
    made = rife.Rife()
    made(_disc(40), _disc(72), 0.5)
    assert [[a for a in argv if a in ("-x", "-z")] for _, _, argv in _handed(kept)] == [[], []]
    assert made.describe()["tta"] == "" and made.describe()["model"] == "rife-v4.25"
    monkeypatch.setenv("SPRITE_GEN_RIFE_MODEL_NAME", "rife-v4.6")
    assert rife.locate() == (binary.resolve(), binary.resolve().parent / "rife-v4.6")
    old = rife.Rife()
    old(_disc(40), _disc(72), 0.5)
    assert [[a for a in argv if a in ("-x", "-z")] for _, _, argv in _handed(kept)][2:] == [["-x", "-z"], ["-x", "-z"]]
    assert old.describe()["tta"] == "-x -z" and old.describe()["model"] == "rife-v4.6"


def test_a_model_directory_names_the_model_it_holds(unnamed, monkeypatch, tmp_path):
    """SPRITE_GEN_RIFE_MODEL names a directory, and its name is the model run (the binary reads the model's version from
    it): a rife-v4.6 directory runs rife-v4.6 with -x -z, as it did before rife-v4.25 was the default."""
    binary, kept = _keeping_binary(tmp_path)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    old = _model_dir(tmp_path / "models", "rife-v4.6")
    monkeypatch.setenv("SPRITE_GEN_RIFE_MODEL", str(old))
    assert rife.model_name() == "rife-v4.6" and rife.locate()[1] == old
    assert rife.Rife().describe()["tta"] == "-x -z"


@pytest.mark.parametrize("names, refused", [
    ({"SPRITE_GEN_RIFE_MODEL_NAME": "rife-v4.26"}, r"SPRITE_GEN_RIFE_MODEL_NAME='rife-v4.26': name rife-v4.25 or rife-v4.6"),
    ({"SPRITE_GEN_RIFE_MODEL": "{tmp}/models/rife-v4.6-copy"}, r"its directory is named 'rife-v4.6-copy', not as the model it holds"),
    ({"SPRITE_GEN_RIFE_MODEL": "{tmp}/models/rife-v4.6", "SPRITE_GEN_RIFE_MODEL_NAME": "rife-v4.25"},
     r"SPRITE_GEN_RIFE_MODEL=.*rife-v4.6' holds rife-v4.6, and SPRITE_GEN_RIFE_MODEL_NAME='rife-v4.25'"),
    ({"SPRITE_GEN_RIFE_TTA": "-x"}, r"SPRITE_GEN_RIFE_TTA='-x': rife-v4.25 runs with no augmentation"),
    ({"SPRITE_GEN_RIFE_TTA": "-x -z"}, r"SPRITE_GEN_RIFE_TTA='-x -z': rife-v4.25 runs with no augmentation"),
])
def test_a_model_named_wrong_or_an_augmentation_it_has_not_is_refused_before_anything_runs(unnamed, monkeypatch, tmp_path,
                                                                                         names, refused):
    """A model the engine does not run, a model directory not named as one, a directory and a name that disagree, and
    rife-v4.25 asked for an augmentation: refused by name (`RifeUnavailable`, not RIFE not installed, which a loop would
    cut around), and the binary never runs."""
    binary, kept = _keeping_binary(tmp_path)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    for name in rife.MODELS:
        _model_dir(binary.parent, name)
    for name in ("rife-v4.6", "rife-v4.6-copy"):
        _model_dir(tmp_path / "models", name)
    for name, value in names.items():
        monkeypatch.setenv(name, value.format(tmp=tmp_path))
    with pytest.raises(rife.RifeUnavailable, match=refused) as refusal:
        rife.Rife()
    assert not isinstance(refusal.value, rife.RifeNotInstalled) and not (kept / "argv.txt").exists()


def _padded(n: int, multiple: int) -> int:
    return -(-n // multiple) * multiple


@pytest.mark.parametrize("size", [(128, 96), (130, 97), (400, 771)])
def test_rife_v4_25_is_handed_each_image_padded_with_zeros_to_128_and_the_frame_cut_back(tmp_path, size):
    """rife-v4.25: each image the binary is handed — the colour and the coverage of a frame, and each of a read's sixteen
    passes, turned or not — is the image RIFE is given (`rife.inputs`) at the top left of a canvas a multiple of 128 each
    way, zeros round it (`rife.Model.pad`: Practical-RIFE pads by 128 at scale 1, the binary by 64 for this model; the
    binary's own padding then adds nothing). The frame that comes back is cut back to the frame's size: from a binary that
    returns its first image, `a` itself."""
    w, h = size
    a, b = (_marked(40).resize(size, Image.NEAREST), _marked(72).resize(size, Image.NEAREST))
    binary, kept = _keeping_binary(tmp_path)
    made = rife.Rife(binary, _model_dir(tmp_path, "rife-v4.25"))
    frame, _ = made.read(a, b, 0.25)
    assert frame.size == size and np.array_equal(np.asarray(frame), np.asarray(a))
    colour, cover = rife.inputs(a, b)
    handed = _handed(kept)
    assert len(handed) == 2 + rife.PASS_CALLS
    for k, (x, y, argv) in enumerate(handed):
        for img in (x, y):
            ih, iw = img.shape[:2]
            assert ih % 128 == 0 and iw % 128 == 0, (k, img.shape)
            inner = (h, w) if k < 2 or not rife.PASS_GEOMETRIES[(k - 2) // 4][0] else (w, h)  # a transposed pass is w x h
            assert (ih, iw) == (_padded(inner[0], 128), _padded(inner[1], 128))
            assert not img[inner[0]:].any() and not img[:, inner[1]:].any(), "outside the image is not zero"
    assert np.array_equal(handed[0][0][:h, :w], colour[0]) and np.array_equal(handed[0][1][:h, :w], colour[1])
    assert np.array_equal(handed[1][0][:h, :w], rife._grey(cover[0])) and np.array_equal(handed[1][1][:h, :w], rife._grey(cover[1]))


def test_rife_v4_6_is_handed_each_image_as_it_is_as_before(tmp_path):
    """rife-v4.6: each image goes to the binary at the frame's own size, exactly what RIFE is given (`rife.inputs`), with
    -x -z — what 3.14.0 handed it, so the frames are the old ones (the binary pads by 32 itself)."""
    a, b = _marked(40).resize((130, 97), Image.NEAREST), _marked(72).resize((130, 97), Image.NEAREST)
    binary, kept = _keeping_binary(tmp_path)
    made = rife.Rife(binary, _model_dir(tmp_path, "rife-v4.6"))
    frame = made(a, b, 0.5)
    colour, cover = rife.inputs(a, b)
    (x0, y0, argv0), (x1, y1, argv1) = _handed(kept)
    assert np.array_equal(x0, colour[0]) and np.array_equal(y0, colour[1])
    assert np.array_equal(x1, rife._grey(cover[0])) and np.array_equal(y1, rife._grey(cover[1]))
    assert "-x" in argv0 and "-z" in argv0 and "-x" in argv1 and "-z" in argv1
    assert np.array_equal(np.asarray(frame), np.asarray(a))


def test_each_model_is_pinned_on_every_platform_and_another_is_refused(tmp_path):
    """Each model's two files are pinned by sha256 as each release zip ships them (`rife.MODEL_SHA256`: the Windows zip's
    flownet.param has CRLF line ends, the others LF); the model read is checked against its own model's pins, any platform's,
    and another model's bytes are refused by name."""
    assert set(rife.MODEL_SHA256) == set(rife.MODELS)
    for name, files in rife.MODEL_SHA256.items():
        assert set(files) == {"flownet.param", "flownet.bin"}
        for pins in files.values():
            assert set(pins) == {"macos", "ubuntu", "windows"} and all(len(v) == 64 for v in pins.values())
    model = tmp_path / "rife-v4.25"
    model.mkdir()
    (model / "flownet.param").write_bytes(b"one")
    (model / "flownet.bin").write_bytes(b"two")
    with pytest.raises(rife.RifeUnavailable, match=r"flownet.param has sha256 [0-9a-f]{64}, expected one of"):
        rife.model_bytes(model)


def test_a_frame_kept_for_one_model_is_not_taken_for_the_other(unnamed, tmp_path):
    """`Cache` keys a frame on its RIFE's identity, which names the model directory and each of its files by sha256: the
    same binary running rife-v4.6 and rife-v4.25 makes two keys, and a frame kept for one is made again for the other."""
    binary, _ = _keeping_binary(tmp_path)
    models = {}
    for name in rife.MODELS:
        models[name] = _model_dir(tmp_path / "beside", name)
        (models[name] / "flownet.bin").write_bytes(hashlib.sha256(name.encode()).digest())
    a, b = _disc(40), _disc(72)
    cache = rife.Cache(tmp_path / "kept")
    new, old = (rife.Rife(binary, models[n]) for n in ("rife-v4.25", "rife-v4.6"))
    assert cache.key(new, a, b, 0.5) != cache.key(old, a, b, 0.5)
    cache.frame(old, a, b, 0.5)
    cache.frame(new, a, b, 0.5)
    cache.frame(old, a, b, 0.5)
    assert (cache.hits, cache.misses) == (1, 2)
