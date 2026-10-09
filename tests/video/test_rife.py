# SPDX-License-Identifier: Apache-2.0
"""RIFE runner: it is found or refused by name, and a real binary (when one is installed)
interpolates an RGBA frame that sits between its two neighbours, alpha included.

The real-binary test runs only where `rife-ncnn-vulkan` is reachable (SPRITE_GEN_RIFE, PATH or the install root);
the locating rules need no binary at all."""

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
    with pytest.raises(rife.RifeNotInstalled, match=r"run `sprite-gen rife install` \(rife-ncnn-vulkan 20221029"):
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
    assert rife.installed_binary().parent.name == "rife-ncnn-vulkan-20221029-ubuntu"
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


def _disc(cx: int) -> Image.Image:
    im = np.zeros((96, 128, 4), dtype=np.uint8)
    yy, xx = np.mgrid[:96, :128]
    inside = (yy - 48) ** 2 + (xx - cx) ** 2 <= 18 ** 2
    im[inside] = (200, 60, 40, 255)
    return Image.fromarray(im, "RGBA")


def _real_rife_available() -> bool:
    try:
        rife.locate()
    except rife.RifeUnavailable:
        return False
    return True


@pytest.mark.skipif(not _real_rife_available(), reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH / sprite-gen rife install)")
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


@pytest.mark.skipif(not _real_rife_available(), reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH / sprite-gen rife install)")
def test_real_rife_between_crossing_legs_adds_no_black():
    """Two outlined legs walk through each other (the near one forward, the far one back): the frame
    between must not be darker inside the body than either neighbour. 2.24 premultiplied the colour
    over black and left 6 % of the body dark at t=0.5 here."""
    interpolate = rife.Rife()
    a, b = _legs(36, 70), _legs(60, 46)
    for t in (0.25, 0.5, 0.75):
        assert rife.smear(interpolate(a, b, t), a, b)["dark_excess"] <= 0


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
    made = rife.between(a, b, 0.5, binary=tmp_path, model=tmp_path, tmp=tmp_path)
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


@pytest.mark.skipif(not _real_rife_available(), reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH / sprite-gen rife install)")
def test_real_rife_melts_legs_crossing_too_far_and_alignment_takes_the_nearer_frame_there():
    """Outlined legs that cross a long way between two drawings (a clip drawn on twos) come out of
    RIFE as one shape with no outline where they meet the air (measured 32 % of the edge at t=0.5);
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
    # the melt made a to b is left part-covered too, a ghost on its own coverage as well; the one made
    # b to a is not (RIFE is not symmetric); neither is a cross-fade of two drawings (section 4)
    assert [(m["method"], m["faults"], m["look"]) for m in facts["smear"]] == [("nearest", ["outline", "ghost"], []),
                                                                               ("nearest", ["outline"], [])]
    kept, facts = align.resample(list(far), 4, interpolate, between="rife")
    assert facts["made_at"] == [1, 3] and [(m["method"], m["faults"]) for m in facts["smear"]] == [("rife", ["outline", "ghost"]),
                                                                                                  ("rife", ["outline"])]
    out, facts = align.resample(list(near), 4, interpolate)
    assert facts["made_at"] == [1, 3] and facts["nearest_at"] == [] and all(not m["faults"] for m in facts["smear"])


@pytest.mark.skipif(not _real_rife_available(), reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH / sprite-gen rife install)")
def test_real_rife_repair_rejects_outline_loss_and_keeps_the_original_middle(monkeypatch):
    from sprite_gen.video import repair

    frames = [_walker(20, -20), _walker(8, -8), _walker(-5, 5), _walker(10, -10)]
    before = [f.tobytes() for f in frames]
    # Isolate adoption from candidate detection: this target spans the wide crossing
    # above, where the actual external RIFE loses an outline the original middle has.
    scores = np.array([3., 2., 1., 1.])
    monkeypatch.setattr(repair, "jump_scores", lambda *a, **kw: {k: scores for k in ("whole", "hair", "score")})
    interpolate = rife.Rife()
    out, facts = repair.repair_jumps(frames, interpolate)
    assert interpolate.made == facts["attempts"] == 1
    assert facts["replaced"] == [] and facts["rounds"][0]["faults"][0] == "outline"
    assert [f.tobytes() for f in out] == before == [f.tobytes() for f in frames]
