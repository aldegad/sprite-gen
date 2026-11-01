# SPDX-License-Identifier: Apache-2.0
"""RIFE runner: it is found or refused by name, and a real binary (when one is installed)
interpolates an RGBA frame that sits between its two neighbours, alpha included.

The real-binary tests are marked `real_rife` and run only where `rife-ncnn-vulkan` is reachable (SPRITE_GEN_RIFE,
PATH or the install root; tests/conftest.py); the locating rules need no binary at all."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import rife


def _fake_binary(tmp_path: Path, with_model: bool = True) -> Path:
    # The platform's own file name (rife-ncnn-vulkan.exe on Windows): PATH lookup and the
    # install root only find the binary under that name.
    binary = tmp_path / "bin" / rife.installed_binary(Path(".")).name
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    if with_model:
        (binary.parent / rife.MODEL).mkdir()
        for name in ("flownet.param", "flownet.bin"):
            (binary.parent / rife.MODEL / name).write_bytes(b"x")
    return binary


def test_missing_binary_is_refused_with_the_install_line(monkeypatch, tmp_path):
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(rife.RifeNotInstalled, match=r"run `sprite-gen rife install` \(TNTwise's rife-ncnn-vulkan 20250112 with "
                                                    r"the models rife-v4.25 and rife-v4.6"):
        rife.locate()


def test_the_installed_copy_is_found_after_the_environment_and_path(monkeypatch, tmp_path):
    """SPRITE_GEN_RIFE, then PATH, then the install root of `sprite-gen rife install`."""
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    staged = _fake_binary(tmp_path / "staged")
    installed = rife.installed_binary()
    assert installed.parent.parent == tmp_path / "data" / "rife"
    installed.parent.parent.mkdir(parents=True)
    staged.parent.rename(installed.parent)
    assert rife.locate()[0] == installed.resolve()
    on_path = _fake_binary(tmp_path / "path")
    monkeypatch.setenv("PATH", str(on_path.parent))
    assert rife.locate()[0] == on_path.resolve()
    named = _fake_binary(tmp_path / "named")
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(named))
    assert rife.locate()[0] == named.resolve()


def test_the_data_directory_follows_the_platform_rules(monkeypatch, tmp_path):
    monkeypatch.delenv("SPRITE_GEN_DATA_DIR", raising=False)
    monkeypatch.setattr(rife.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert rife.data_dir() == tmp_path / "xdg" / "sprite-gen"
    assert rife.installed_binary().parent.name == "rife-ncnn-vulkan-20250112-ubuntu"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert rife.data_dir() == Path.home() / ".local" / "share" / "sprite-gen"
    monkeypatch.setattr(rife.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert rife.data_dir() == tmp_path / "local" / "sprite-gen"
    assert rife.installed_binary().name == "rife-ncnn-vulkan.exe"
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "named"))
    assert rife.data_dir() == tmp_path / "named"


def test_model_beside_the_binary_is_found(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    found, model = rife.locate()
    assert found == binary.resolve() and model == binary.resolve().parent / rife.MODEL


def test_missing_model_is_refused(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path, with_model=False)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    with pytest.raises(rife.RifeUnavailable, match="flownet.param"):
        rife.locate()


def _recording_binary(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in binary that logs its arguments and returns its first frame: (binary, log)."""
    log = tmp_path / "argv.txt"
    binary = tmp_path / rife.BINARY
    binary.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n'
                      'while [ $# -gt 0 ]; do case "$1" in -0) a="$2"; shift;; -o) o="$2"; shift;; esac; shift; done\n'
                      'cp "$a" "$o"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary, log


def _modes_asked(monkeypatch, tmp_path: Path, named: str | None) -> tuple[list[list[str]], dict[str, str]]:
    """The augmentation flags of each call one made frame of rife-v4.6 sends, and the interpolator's record."""
    if named is None:
        monkeypatch.delenv("SPRITE_GEN_RIFE_TTA", raising=False)
    else:
        monkeypatch.setenv("SPRITE_GEN_RIFE_TTA", named)
    binary, log = _recording_binary(tmp_path)
    interpolate = rife.Rife(binary, tmp_path / "rife-v4.6")
    interpolate(_disc(40), _disc(72), 0.5)
    calls = [line.split() for line in log.read_text().splitlines()]
    return [[a for a in argv if a in ("-x", "-z", "-u", "-g")] for argv in calls], interpolate.describe()


def test_every_call_of_rife_v4_6_asks_for_both_augmentations_and_never_the_cpu_path(monkeypatch, tmp_path):
    """rife-v4.6's colour run and coverage run both go out with `-x -z` (docs/loop-repair.md section 1,
    "Which setting"), and with no `-g`: the binary's own CPU path is wrong with this model. (rife-v4.25's go out with
    neither: tests/video/test_rife_models.py.)"""
    modes, record = _modes_asked(monkeypatch, tmp_path, None)
    assert modes == [["-x", "-z"], ["-x", "-z"]] and record["tta"] == "-x -z"


@pytest.mark.parametrize("named, flags", [("", []), ("-x", ["-x"]), ("-z", ["-z"]), (" -z  -x ", ["-z", "-x"])])
def test_sprite_gen_rife_tta_names_another_augmentation_or_none(monkeypatch, tmp_path, named, flags):
    modes, record = _modes_asked(monkeypatch, tmp_path, named)
    assert modes == [flags, flags] and record["tta"] == " ".join(flags)


def test_a_call_past_its_time_is_a_rife_that_failed(monkeypatch, tmp_path):
    binary = tmp_path / rife.BINARY
    binary.write_text("#!/bin/sh\nexec sleep 5\n")  # exec: the stopped process is the one holding the pipes
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(rife, "CALL_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(rife.RifeUnavailable, match=r"made no frame in 0.2 s .*augmentation none"):
        rife.Rife(binary, tmp_path / rife.MODEL)(_disc(40), _disc(72), 0.5)


@pytest.mark.parametrize("named", ["-g -1", "x", "-x -x", "-u"])
def test_sprite_gen_rife_tta_refuses_anything_but_the_two_modes(monkeypatch, tmp_path, named):
    monkeypatch.setenv("SPRITE_GEN_RIFE_TTA", named)
    binary, log = _recording_binary(tmp_path)
    with pytest.raises(rife.RifeUnavailable, match="SPRITE_GEN_RIFE_TTA"):
        rife.Rife(binary, tmp_path / rife.MODEL)
    assert not log.exists()


def _disc(cx: int) -> Image.Image:
    im = np.zeros((96, 128, 4), dtype=np.uint8)
    yy, xx = np.mgrid[:96, :128]
    inside = (yy - 48) ** 2 + (xx - cx) ** 2 <= 18 ** 2
    im[inside] = (200, 60, 40, 255)
    return Image.fromarray(im, "RGBA")


def _marked(cx: int) -> Image.Image:
    """The disc with a block and a bar off its centre lines: no flip or transpose of it is itself."""
    im = np.asarray(_disc(cx)).copy()
    im[10:22, cx - 30:cx - 14] = (40, 160, 90, 255)
    im[70:74, cx + 4:cx + 30] = (230, 220, 40, 128)
    return Image.fromarray(im, "RGBA")


def _premultiplied(frame: Image.Image) -> np.ndarray:
    """A frame's colour and coverage as `rife.passes` gives them to the binary, premultiplied (0..1)."""
    x = np.asarray(frame, dtype=np.float32) / 255.0
    colour = np.uint8(np.clip(rife.bleed(x), 0, 1) * 255 + 0.5) / 255.0
    cover = np.uint8(x[..., 3:] * 255 + 0.5) / 255.0
    return np.concatenate([colour * cover, cover], axis=-1)


@pytest.mark.slow
@pytest.mark.parametrize("model", list(rife.MODELS))
def test_read_makes_the_frame_as_every_call_and_reads_sixteen_single_passes_turned_back(monkeypatch, tmp_path, model):
    """`Rife.read`: the frame as `between` makes it, with the augmentation every call of the model asks for, then the
    sixteen single passes with none — the pair flipped and transposed eight ways, each from a to b at t and from b to a at
    1 - t. A binary that hands back its first frame makes every forward pass a and every backward pass b, once each pass is
    turned back (and, for rife-v4.25, cut back from the padding): the passes disagree by exactly how far a and b differ,
    and only where they differ."""
    monkeypatch.delenv("SPRITE_GEN_RIFE_TTA", raising=False)
    binary, log = _recording_binary(tmp_path)
    interpolate = rife.Rife(binary, tmp_path / model)
    a, b = _marked(40), _marked(72)
    frame, apart = interpolate.read(a, b, 0.25)
    calls = [line.split() for line in log.read_text().splitlines()]
    assert len(calls) == 2 + rife.PASS_CALLS == interpolate.calls and interpolate.made == 1
    flags = [[x for x in argv if x in ("-x", "-z", "-u", "-g")] for argv in calls]
    assert flags[:2] == [list(rife.MODELS[model].tta)] * 2 and all(f == [] for f in flags[2:])
    times = [argv[argv.index("-s") + 1] for argv in calls[2:]]
    assert times.count("0.2500") == times.count("0.7500") == rife.PASS_CALLS // 2
    assert np.array_equal(np.asarray(frame), np.asarray(a))  # the frame between, from the copying binary: a itself
    pa, pb = _premultiplied(a), _premultiplied(b)
    expected = np.sqrt(((pa - pb) ** 2).sum(axis=-1) / 4.0)
    assert apart.shape == (96, 128) and np.allclose(apart, expected, atol=1e-5)
    assert apart[pa[..., 3] + pb[..., 3] == 0].max() == 0 and apart.max() > 0.5


def test_torn_reads_a_thick_patch_in_a_window_and_not_an_edge_or_scattered_pixels():
    """`rife.torn` on disagreement drawn by hand over a body 100 px high, windows of 10 px: a patch a window wide where the
    passes disagree fills its window; lines a pixel or two wide, pixels scattered one in five, or a patch they disagree on
    by less than TORN_LEVEL leave it empty; a frame with no body reads nothing."""
    body = np.zeros((160, 128, 4), dtype=np.uint8)
    body[30:130, 34:94] = (200, 180, 160, 255)
    made = Image.fromarray(body, "RGBA")
    apart = np.zeros((160, 128), dtype=np.float32)
    patch = apart.copy()
    patch[60:72, 50:62] = 0.3
    assert rife.torn(patch, made) == 1.0
    lines = apart.copy()
    lines[:, 40] = lines[:, 70:72] = lines[90, :] = 0.5
    assert rife.torn(lines, made) == 0.0
    scattered = np.where(np.random.default_rng(7).random(apart.shape) < 0.2, 0.5, 0.0).astype(np.float32)
    assert rife.torn(scattered, made) < 0.05
    faint = apart.copy()
    faint[60:72, 50:62] = rife.TORN_LEVEL * 0.8
    assert rife.torn(faint, made) == 0.0
    assert rife.torn(patch, Image.new("RGBA", (128, 160))) == 0.0


@pytest.mark.real_rife
def test_real_rife_makes_the_frame_between_in_colour_and_alpha():
    interpolate = rife.Rife()
    mid = interpolate(_disc(40), _disc(72), 0.5)
    a = np.asarray(mid)[..., 3] / 255.0
    xs = np.nonzero(a > 0.5)[1]
    assert abs(xs.mean() - 56) <= 3  # the disc moved half way, not cross-faded at both ends
    assert a[:, :20].max() == 0 and a[:, -20:].max() == 0  # no coverage invented far from the body
    rgb = np.asarray(mid)[..., :3][a > 0.9]
    assert np.abs(rgb.mean(axis=0) - (200, 60, 40)).max() < 12  # unpremultiplied back to the body colour
    assert interpolate.made == 1


@pytest.mark.slow
@pytest.mark.real_rife
@pytest.mark.parametrize("model", list(rife.MODELS))
def test_real_rife_between_crossing_legs_adds_no_black(model):
    """Two outlined legs walk through each other (the near one forward, the far one back): the frames between are not
    the black fill 2.24 left where they cross, premultiplying the colour over black — 6 % of the body dark at t=0.5 —
    only a line each neighbour draws in another place, under 3 % of the body at t=0.25, 0.5 and 0.75. rife-v4.6's flips
    of `-x` keep the near leg's outline whole over the far one: 1.3 % and 2.1 % either side of the crossing, the same on
    two GPUs, and at the crossing no darker inside the body than either neighbour. rife-v4.25, with no augmentation,
    leaves 0.4 %, 0.4 % and 1.8 % (an Apple GPU)."""
    root = rife.locate()[0].parent
    interpolate = rife.Rife(rife.locate()[0], root / model)
    a, b = _legs(36, 70), _legs(60, 46)
    dark = [rife.smear(interpolate(a, b, t), a, b)["dark_excess"] for t in (0.25, 0.5, 0.75)]
    assert max(dark) < 0.03, dark
    if model == "rife-v4.6":
        assert dark[1] <= 0, dark


def _legs(x_near: int, x_far: int) -> Image.Image:
    """Hips and two legs with a 1 px black outline, on transparency: the near leg white and drawn
    over the far one, which is shaded."""
    im = np.zeros((96, 128, 4), dtype=np.uint8)
    for x, grey in ((x_far, 200), (x_near, 245)):
        im[16:86, x - 8:x + 8] = (20, 20, 20, 255)
        im[17:85, x - 7:x + 7] = (grey, grey, grey, 255)
    im[10:18, 30:90] = (20, 20, 20, 255)
    im[11:17, 31:89] = (230, 230, 230, 255)
    return Image.fromarray(im, "RGBA")


def test_where_the_colour_and_coverage_runs_disagree_the_body_shows_not_black(monkeypatch, tmp_path):
    """RIFE warps the colour and the coverage in two runs. A stand-in returns the colour run as
    the first frame and the coverage run as the second, so where the second covers what the first
    leaves bare the two disagree — the overlap of crossing legs. That must read as the body."""
    a, b = _legs(40, 64), _legs(64, 52)
    calls = []

    def stand_in(binary, model, x, y, t, tmp):
        calls.append(1)
        return x if len(calls) % 2 else y

    monkeypatch.setattr(rife, "_call", stand_in)
    made = rife.between(a, b, 0.5, binary=tmp_path, model=tmp_path / rife.MODEL, tmp=tmp_path)
    m = np.asarray(made).astype(int)
    bare_in_a = np.asarray(a)[..., 3] == 0
    covered = (m[..., 3] >= 128) & bare_in_a
    inside = covered & (np.asarray(b)[..., 0] >= 190)  # b's leg fill, where a had nothing
    assert inside.sum() > 0
    luma = m[..., :3] @ np.array([0.299, 0.587, 0.114])
    assert luma[inside].min() >= 190  # the body's own light fill, not the black 2.24 put there


def test_smear_counts_a_black_blot_and_not_a_moved_dark_part():
    a, b = _legs(40, 64), _legs(48, 64)
    moved = _legs(44, 64)
    assert rife.smear(moved, a, b)["dark_excess"] <= 0
    blot = np.asarray(moved).copy()
    blot[40:60, 58:70, :3] = 0
    assert rife.smear(Image.fromarray(blot, "RGBA"), a, b)["dark_excess"] > 0.01


def _walker(front: float, back: float) -> Image.Image:
    """A body over two legs swung from the hip to these angles (degrees, 0 straight down), each a
    capsule with a 2 px black outline on transparency; the far leg shaded, the near drawn over it."""
    from PIL import ImageDraw

    w, h = 128, 192
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    hip, length, r = (w // 2, int(h * 0.55)), int(h * 0.36), int(w * 0.09)
    for angle, fill in ((back, (215, 205, 200, 255)), (front, (250, 240, 235, 255))):
        a = np.radians(angle)
        foot = (hip[0] + length * np.sin(a), hip[1] + length * np.cos(a))
        for radius, colour in ((r, (20, 20, 20, 255)), (r - 2, fill)):
            d.line([hip, foot], fill=colour, width=2 * radius)
            d.ellipse([foot[0] - radius, foot[1] - radius, foot[0] + radius, foot[1] + radius], fill=colour)
    body = [w * 0.25, h * 0.15, w * 0.75, h * 0.62]
    d.ellipse(body, fill=(20, 20, 20, 255))
    d.ellipse([body[0] + 2, body[1] + 2, body[2] - 2, body[3] - 2], fill=(250, 240, 235, 255))
    return im


def test_outline_loss_reads_a_melted_limb_and_not_a_moved_one():
    """A frame whose legs became one shape of fill (the outline gone where they meet the air) loses
    its outline; legs drawn half way lose none, and art drawn without outlines loses none."""
    a, b = _walker(20, -20), _walker(-5, 5)
    assert rife.smear(_walker(8, -8), a, b)["outline_loss"] <= 0.01
    melted = np.asarray(_walker(8, -8)).copy()
    legs = melted[110:, :]
    dark = (legs[..., 3] > 0) & (legs[..., :3].max(axis=-1) < 70)
    legs[dark] = (240, 230, 225, 255)
    m = rife.smear(Image.fromarray(melted, "RGBA"), a, b)
    assert m["outline_loss"] > 0.05 and m["dark_excess"] < 0
    flat = [Image.fromarray(np.where(np.asarray(f)[..., 3:] > 0, (240, 230, 225, 255), 0).astype(np.uint8), "RGBA")
            for f in (a, b, Image.fromarray(melted, "RGBA"))]
    assert rife.smear(flat[2], flat[0], flat[1])["outline_loss"] <= 0.01


@pytest.mark.slow
@pytest.mark.real_rife
@pytest.mark.timeout(900)  # 20 calls of the binary at 128 x 192, each run 16 ways (-x -z): seconds on a GPU, minutes on a CPU's Vulkan driver
def test_real_rife_melts_legs_crossing_too_far_and_alignment_takes_the_nearer_frame_there():
    """Outlined legs that cross a long way between two drawings (a clip drawn on twos) come out of
    RIFE as one shape with no outline where they meet the air (measured 26 % of the edge at t=0.5);
    a short step comes out outlined (under 3 % at any t). `--between auto` takes the nearer source
    frame for the first and keeps RIFE's for the second; `rife` keeps both and names the first."""
    from sprite_gen.video import align

    interpolate = rife.Rife()
    far, near = (_walker(20, -20), _walker(-5, 5)), (_walker(10, -10), _walker(4, -4))
    assert rife.smear(interpolate(*far, 0.5), *far)["outline_loss"] > align.OUTLINE_WARN
    for t in (0.25, 0.5, 0.75):
        assert rife.smear(interpolate(*near, t), *near)["outline_loss"] < align.OUTLINE_WARN
    out, facts = align.resample(list(far), 4, interpolate)  # times 0, 0.5, 1, 1.5: both made at t=0.5
    assert facts["between"] == "auto" and facts["made_by_rife"] == 0 and facts["nearest_at"] == [1, 3]
    assert out[1] is far[1] and out[3] is far[0]
    # the two melts are one frame, made both ways in time (`-z`), and neither is left part-covered: the
    # augmentation takes away the ghost band the one made a to b carried without it (docs/loop-repair.md
    # section 1, "Which setting"); neither is a cross-fade of two drawings (section 4)
    assert [(m["method"], m["faults"], m["look"]) for m in facts["smear"]] == [("nearest", ["outline"], []),
                                                                               ("nearest", ["outline"], [])]
    kept, facts = align.resample(list(far), 4, interpolate, between="rife")
    assert facts["made_at"] == [1, 3] and [(m["method"], m["faults"]) for m in facts["smear"]] == [("rife", ["outline"]),
                                                                                                  ("rife", ["outline"])]
    out, facts = align.resample(list(near), 4, interpolate)
    assert facts["made_at"] == [1, 3] and facts["nearest_at"] == [] and all(not m["faults"] for m in facts["smear"])


@pytest.mark.real_rife
def test_real_rife_repair_rejects_outline_loss_and_keeps_the_original_middle(monkeypatch):
    from sprite_gen.video import repair
    from tests.video.test_loop_repair import forced_flow_group

    frames = [_walker(20, -20), _walker(8, -8), _walker(-5, 5), _walker(10, -10)]
    before = [f.tobytes() for f in frames]
    # Isolate adoption from candidate detection: this target spans the wide crossing
    # above, where the actual external RIFE loses an outline the original middle has.
    forced_flow_group(monkeypatch, [1], (0, 2), [0.5])
    interpolate = rife.Rife()
    out, facts = repair.repair_jumps(frames, interpolate)
    assert interpolate.made == facts["attempts"] == 1
    assert facts["replaced"] == [] and facts["rounds"][0]["faults"] == ["outline"]
    assert [f.tobytes() for f in out] == before == [f.tobytes() for f in frames]
