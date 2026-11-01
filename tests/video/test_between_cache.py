# SPDX-License-Identifier: Apache-2.0
"""Frames made between two source frames, kept and taken again (docs/loop-repair.md section 4,
`video-cycle-align --between-cache`). An app aligns a set, then aligns it once more on a copy with
the feet told (or a retake in place): the frames between are the same frames, made from the same
bytes at the same times, and the second pass takes them from the directory the first kept them in.

What must hold: the second pass delivers the very bytes a pass without the directory delivers; a
frame is taken only for the same two frames, the same fraction and the same RIFE on the same
machine, and made again for anything else — the same RIFE too, once a file it runs from changed
under it, and a frame made while one changed is not kept; and nothing is kept unless a directory is
named. The stand-in RIFE cross-fades, names itself (`identity`) as `rife.Rife` does, and records what
it made."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import align
from sprite_gen.video import rife
from tests.video.test_cycle_align import _cut
from tests.video.test_rife import _disc, _fake_binary, _legs

VIEWS = ["front", "side@right", "back"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Blend:
    """A stand-in RIFE: the cross-fade of its two frames at t. Records each frame it made by its two
    frames' sha256 and t."""

    def __init__(self, name: str = "blend") -> None:
        self.name = name
        self.made: list[tuple[str, str, float]] = []

    def __call__(self, a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        self.made.append((_sha(a.tobytes()), _sha(b.tobytes()), t))
        return Image.blend(a, b, t)

    def identity(self) -> str:
        return self.name


def _hashes(root: Path) -> dict[str, str]:
    """Every file under a directory by its sha256."""
    return {p.relative_to(root).as_posix(): _sha(p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()}


def _said(report: dict, root: Path, as_root: Path) -> str:
    """A report as it reads with `root` named `as_root`, its cache record left out."""
    text = json.dumps({k: v for k, v in report.items() if k != "between_cache"}, sort_keys=True)
    return text.replace(str(root.resolve()), str(as_root.resolve()))


def _set(tmp_path: Path) -> tuple[Path, list[str]]:
    """Three loops of a walk cut to 20, 24 and 28 frames, as an app keeps them (`loops/<name>`)."""
    root = tmp_path / "loops"
    root.mkdir()
    for name, length in (("S", 20), ("E", 24), ("N", 28)):
        _cut(root, name, length)
    return root, ["S", "E", "N"]


def _pass(root: Path, names: list[str], made: Blend, **kw) -> dict:
    """One alignment of the set under `root`, each foot told as `feet` names it (a foot pass)."""
    feet = {str(root / n): f for n, f in (kw.pop("feet", None) or {}).items()}
    return align.align_set([root / n for n in names], interpolate=made, views=VIEWS, feet=feet or None, **kw)


@pytest.mark.slow
@pytest.mark.parametrize("between", ["rife", "auto"])
def test_the_foot_pass_takes_every_frame_the_first_pass_made_and_delivers_the_same_bytes(tmp_path, between):
    """The app's two passes: the set aligned, then aligned again on a copy with the feet told. The
    second takes every frame between from the first's directory and makes none, and every file it
    delivers is the file a second pass with no directory delivers, byte for byte."""
    root, names = _set(tmp_path)
    kept = tmp_path / "between-cache"
    first = Blend()
    report = _pass(root, names, first, between=between, between_cache=kept)
    n = len(first.made)
    assert n > 0 and report["between_cache"] == {"dir": str(kept.resolve()), "hits": 0, "misses": n, "unreadable": 0, "not_kept": 0}
    assert len(list(kept.glob("*.png"))) == n
    for label in ("foot", "plain"):
        shutil.copytree(root, tmp_path / f"loops.{label}")
    feet = {"S": "left", "E": "right", "N": "left"}
    reused, fresh = Blend(), Blend()
    taken = _pass(tmp_path / "loops.foot", names, reused, between=between, feet=feet, between_cache=kept)
    plain = _pass(tmp_path / "loops.plain", names, fresh, between=between, feet=feet)
    assert reused.made == [] and len(fresh.made) == n
    assert taken["between_cache"] == {"dir": str(kept.resolve()), "hits": n, "misses": 0, "unreadable": 0, "not_kept": 0}
    assert plain["between_cache"] is None
    assert _hashes(tmp_path / "loops.foot") == _hashes(tmp_path / "loops.plain")
    assert _said(taken, tmp_path / "loops.foot", tmp_path / "loops.plain") == _said(plain, tmp_path / "loops.plain", tmp_path / "loops.plain")


@pytest.mark.slow
def test_a_frame_whose_inputs_changed_is_made_again_and_never_taken(tmp_path):
    """A source frame changed by one pixel: every frame made from it is made again, every other is
    taken, and the loops come out as a pass with no directory makes them. Another length is other
    times: only a frame kept for the very same two frames and time is taken, and the loops are again
    the plain pass's."""
    root, names = _set(tmp_path)
    kept = tmp_path / "between-cache"
    _pass(root, names, Blend(), between="rife", between_cache=kept)
    for label in ("edit", "plain"):
        shutil.copytree(root, tmp_path / f"loops.{label}")
        path = tmp_path / f"loops.{label}" / "S" / align.SOURCE_DIR / "frame-003.png"
        frame = np.asarray(Image.open(path).convert("RGBA")).copy()
        frame[60, 50] = (255, 0, 0, 255)
        Image.fromarray(frame, "RGBA").save(path)
    changed = _sha(Image.open(tmp_path / "loops.edit" / "S" / align.SOURCE_DIR / "frame-003.png").convert("RGBA").tobytes())
    reused, fresh = Blend(), Blend()
    taken = _pass(tmp_path / "loops.edit", names, reused, between="rife", between_cache=kept)
    _pass(tmp_path / "loops.plain", names, fresh, between="rife")
    remade = [m for m in fresh.made if changed in m[:2]]
    assert remade and reused.made == remade
    assert taken["between_cache"]["misses"] == len(remade) and taken["between_cache"]["hits"] == len(fresh.made) - len(remade)
    assert _hashes(tmp_path / "loops.edit") == _hashes(tmp_path / "loops.plain")

    longer, plain = Blend(), Blend()
    taken = _pass(tmp_path / "loops.edit", names, longer, between="rife", length=22, between_cache=kept)
    _pass(tmp_path / "loops.plain", names, plain, between="rife", length=22)
    assert longer.made == [m for m in plain.made if m not in set(fresh.made)] and longer.made
    assert taken["between_cache"]["hits"] == len(plain.made) - len(longer.made)
    assert _hashes(tmp_path / "loops.edit") == _hashes(tmp_path / "loops.plain")


def _stub_call(monkeypatch) -> list[int]:
    """RIFE's binary call answered by a cross-fade of the two images it is handed."""
    calls: list[int] = []

    def blend(binary, model, x, y, t, tmp):
        calls.append(1)
        return Image.blend(x, y, t)

    monkeypatch.setattr(rife, "_call", blend)
    return calls


def test_a_kept_frame_is_taken_only_for_the_same_frames_and_fraction(monkeypatch, tmp_path):
    """The same two frames at the same fraction take the kept frame; the two swapped, another fraction — even one
    a float apart — or one pixel moved make it. Frames are kept for RGBA frames only: their mode, size and bytes are
    all of them, where a palette or a transparent colour is not in an image's bytes."""
    calls = _stub_call(monkeypatch)
    binary = _fake_binary(tmp_path / "one")
    made = rife.Rife(binary, binary.parent / rife.MODEL)
    cache = rife.Cache(tmp_path / "kept")
    a, b = _disc(40), _disc(72)
    first = cache.frame(made, a, b, 0.5)
    assert len(calls) == 2 and made.made == 1
    again = cache.frame(made, a, b, 0.5)
    assert len(calls) == 2 and made.made == 1
    assert (again.mode, again.size, again.tobytes()) == (first.mode, first.size, first.tobytes())
    assert (cache.hits, cache.misses, cache.unreadable) == (1, 1, 0)

    moved = np.asarray(a).copy()
    moved[0, 0] = (1, 0, 0, 1)
    others = [(b, a, 0.5), (a, b, 0.25), (a, b, 0.5000001), (Image.fromarray(moved, "RGBA"), b, 0.5)]
    for x, y, t in others:
        assert cache.frame(made, x, y, t).tobytes() == rife.between(x, y, t, binary=binary, model=made.model, tmp=tmp_path).tobytes()
    assert cache.misses == 1 + len(others) and cache.hits == 1
    with pytest.raises(ValueError, match=r"kept for RGBA frames only, not RGB"):
        cache.frame(made, a.convert("RGB"), b, 0.5)
    assert cache.record() == {"dir": str((tmp_path / "kept").resolve()), "hits": 1, "misses": 1 + len(others), "unreadable": 0, "not_kept": 0}
    if os.name == "posix":  # the directory and its frames are this user's only
        assert (tmp_path / "kept").stat().st_mode & 0o077 == 0
        assert all(p.stat().st_mode & 0o077 == 0 for p in (tmp_path / "kept").iterdir())


def test_the_same_rife_is_another_identity_once_its_machine_libraries_or_code_changed(monkeypatch, tmp_path):
    """Under one Rife, the machine (host name, OS build, architecture: read each time, where platform.uname() keeps
    its first answer), NumPy's or Pillow's version, or engine code loaded anew make the frame again. rife.py written
    over on disk after the import is no code that runs here: the frame the loaded code made is taken."""
    _stub_call(monkeypatch)
    binary = _fake_binary(tmp_path)
    made = rife.Rife(binary, binary.parent / rife.MODEL)
    cache = rife.Cache(tmp_path / "kept")
    a, b = _disc(40), _disc(72)
    cache.frame(made, a, b, 0.5)

    def remade(change) -> bool:
        before = cache.misses
        change()
        cache.frame(made, a, b, 0.5)
        return cache.misses == before + 1

    host = list(os.uname()) if hasattr(os, "uname") else ["Windows", "host", "10", "10.0.19045", "AMD64"]
    for field, value in ((1, "another-host"), (3, "another OS build")):
        host[field] = value
        assert remade(lambda: monkeypatch.setattr(rife.os, "uname", lambda now=tuple(host): now, raising=False))
    assert remade(lambda: monkeypatch.setattr(rife.np, "__version__", "0.0.1"))
    assert remade(lambda: monkeypatch.setattr(rife.PIL, "__version__", "0.0.1"))
    assert rife.CODE_SHA256 == _sha(Path(rife.__file__).read_bytes())
    code = tmp_path / "rife.py"
    code.write_text(Path(rife.__file__).read_text(encoding="utf-8") + "\n# another engine\n", encoding="utf-8")
    assert not remade(lambda: monkeypatch.setattr(rife, "__file__", str(code)))
    assert remade(lambda: monkeypatch.setattr(rife, "CODE_SHA256", _sha(code.read_bytes())))
    assert (cache.hits, cache.misses) == (1, 6)


def _picking_rife(root: Path, name: str = rife.MODEL) -> tuple[Path, Path]:
    """(binary, its model's flownet.bin): a stand-in RIFE that runs. It hands back the first or the second of its two
    images (`-0`, `-1`) — the one its model's flownet.bin names, the other where its own FLIP is 1 — so a frame taken
    for another model or binary shows in its pixels. Where a file `race` stands beside it, it first writes the other
    model over its own, once: a model replaced while a frame is made."""
    binary = root / "bin" / rife.installed_binary(Path(".")).name
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
        import shutil, sys
        from pathlib import Path
        FLIP = 0
        args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
        weights = Path(args["-m"]) / "flownet.bin"
        pick = int(weights.read_text())
        race = Path(sys.argv[0]).with_name("race")
        if race.exists():
            race.unlink()
            weights.write_text(str(1 - pick))
        shutil.copyfile(args["-" + str(pick ^ FLIP)], args["-o"])
        """))
    binary.chmod(0o700)
    model = binary.parent / name
    model.mkdir()
    (model / "flownet.param").write_text("synthetic")
    (model / "flownet.bin").write_text("0")
    return binary, model / "flownet.bin"


def _dot(cx: int, colour: tuple[int, int, int]) -> Image.Image:
    """`_disc` at `cx`, in `colour`."""
    im = np.asarray(_disc(cx)).copy()
    im[im[..., 3] > 0, :3] = colour
    return Image.fromarray(im, "RGBA")


@pytest.mark.slow
@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script cannot stand in for rife-ncnn-vulkan.exe")
def test_the_same_rife_makes_its_frame_again_once_a_file_it_runs_from_changed(tmp_path):
    """One Rife makes and keeps a frame, then its model's bytes are written over: the next pass makes the frame
    again, as this very RIFE now makes another. So for its binary written over, a file added to its model, and its
    model's files under another path (the binary reads the model's version from its name). Every frame taken or made
    is the frame the same RIFE makes with no directory, byte for byte. A RIFE whose model is gone fails as a RIFE that
    fails."""
    binary, weights = _picking_rife(tmp_path)
    made = rife.Rife(binary, weights.parent)
    a, b = _dot(40, (255, 0, 0)), _dot(72, (0, 0, 255))

    def again(interpolate: rife.Rife) -> tuple[bytes, int, int]:
        """A pass's frame, hits and misses; its frame is the one the same RIFE makes with no directory."""
        cache = rife.Cache(tmp_path / "kept")
        frame = cache.frame(interpolate, a, b, 0.5).tobytes()
        assert frame == interpolate(a, b, 0.5).tobytes()
        return frame, cache.hits, cache.misses

    assert again(made) == (a.tobytes(), 0, 1)
    assert again(made) == (a.tobytes(), 1, 0)
    weights.write_text("1")
    assert again(made) == (b.tobytes(), 0, 1)
    binary.write_text(binary.read_text().replace("FLIP = 0", "FLIP = 1"))
    assert again(made) == (a.tobytes(), 0, 1)
    (weights.parent / "contextnet.bin").write_text("a file added")
    assert again(made) == (a.tobytes(), 0, 1)
    assert again(rife.Rife(binary, shutil.copytree(weights.parent, tmp_path / "elsewhere" / rife.MODEL))) == (a.tobytes(), 0, 1)
    shutil.rmtree(weights.parent)
    with pytest.raises(rife.RifeUnavailable, match="could not be read"):
        rife.Cache(tmp_path / "kept").frame(made, a, b, 0.5)


@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script cannot stand in for rife-ncnn-vulkan.exe")
def test_a_frame_made_while_its_rife_changed_is_handed_on_and_not_kept(tmp_path):
    """The model written over while a frame is made, between RIFE's two runs: the frame is what RIFE made — its colour
    from one model, its coverage from the other — and it is handed on as made, as a pass with no directory hands it
    on, but not kept, as no key names what made it. The pass after makes the frame again."""
    binary, weights = _picking_rife(tmp_path)
    made = rife.Rife(binary, weights.parent)
    a, b = _dot(40, (255, 0, 0)), _dot(72, (0, 0, 255))
    race = binary.with_name("race")
    race.touch()
    cache = rife.Cache(tmp_path / "kept")
    frame = cache.frame(made, a, b, 0.5).tobytes()
    assert cache.record() == {"dir": str((tmp_path / "kept").resolve()), "hits": 0, "misses": 1, "unreadable": 0, "not_kept": 1}
    assert list((tmp_path / "kept").iterdir()) == []
    weights.write_text("0")
    race.touch()
    assert made(a, b, 0.5).tobytes() == frame and frame not in (a.tobytes(), b.tobytes())
    weights.write_text("0")
    after = rife.Cache(tmp_path / "kept")
    assert after.frame(made, a, b, 0.5).tobytes() == a.tobytes() and (after.hits, after.misses, after.not_kept) == (0, 1, 0)


@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script cannot stand in for rife-ncnn-vulkan.exe")
def test_a_frame_kept_under_one_augmentation_is_made_again_under_another(monkeypatch, tmp_path):
    """SPRITE_GEN_RIFE_TTA is read on every call (`rife.tta()`), so one Rife of rife-v4.6 makes another frame once it names
    another augmentation: a frame kept by a pass without augmentation is made again by a pass with rife-v4.6's `-x -z`,
    and by one with `-z` alone, and taken again by the next pass without. Every frame taken or made is the frame the same
    RIFE makes with no directory, byte for byte."""
    binary, weights = _picking_rife(tmp_path, "rife-v4.6")
    binary.write_text(binary.read_text().replace("FLIP = 0", 'FLIP = int("-x" in sys.argv)'))
    made = rife.Rife(binary, weights.parent)
    a, b = _dot(40, (255, 0, 0)), _dot(72, (0, 0, 255))

    def again() -> tuple[bytes, int, int]:
        cache = rife.Cache(tmp_path / "kept")
        frame = cache.frame(made, a, b, 0.5).tobytes()
        assert frame == made(a, b, 0.5).tobytes()
        return frame, cache.hits, cache.misses

    monkeypatch.setenv("SPRITE_GEN_RIFE_TTA", "")
    assert again() == (a.tobytes(), 0, 1)
    assert again() == (a.tobytes(), 1, 0)
    monkeypatch.delenv("SPRITE_GEN_RIFE_TTA")
    assert rife.tta("rife-v4.6") == rife.MODELS["rife-v4.6"].tta == ("-x", "-z")
    assert again() == (b.tobytes(), 0, 1)
    monkeypatch.setenv("SPRITE_GEN_RIFE_TTA", "-z")
    assert again() == (a.tobytes(), 0, 1)
    monkeypatch.setenv("SPRITE_GEN_RIFE_TTA", "")
    assert again() == (a.tobytes(), 1, 0)


def test_a_kept_file_that_does_not_read_back_is_made_again(monkeypatch, tmp_path):
    """A kept file cut short, overwritten with another image — even an RGBA frame of the same size —
    or not an image at all is not taken: the frame is made again and kept over it."""
    _stub_call(monkeypatch)
    binary = _fake_binary(tmp_path)
    made = rife.Rife(binary, binary.parent / rife.MODEL)
    cache = rife.Cache(tmp_path / "kept")
    a, b = _legs(40, 64), _legs(64, 52)
    truth = cache.frame(made, a, b, 0.5).tobytes()
    (path,) = (tmp_path / "kept").glob("*.png")
    whole = path.read_bytes()
    broken = [whole[: len(whole) // 2], b"not a png", _png(Image.new("RGBA", (8, 8))), _png(Image.new("RGB", a.size)),
              _png(Image.new("RGBA", a.size))]
    for data in broken:
        path.write_bytes(data)
        assert cache.frame(made, a, b, 0.5).tobytes() == truth
        assert path.read_bytes() == whole
    assert (cache.hits, cache.misses, cache.unreadable) == (0, 1 + len(broken), len(broken))
    assert cache.frame(made, a, b, 0.5).tobytes() == truth and cache.hits == 1


def _png(image: Image.Image) -> bytes:
    from io import BytesIO

    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.slow
def test_nothing_is_kept_without_a_directory_and_a_stand_in_that_names_nothing_is_refused(tmp_path):
    root, names = _set(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    report = _pass(root, names, Blend(), between="rife")
    assert report["between_cache"] is None and sorted(p.name for p in tmp_path.iterdir()) == before
    files = _hashes(root)

    def anonymous(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        return Image.blend(a, b, t)

    with pytest.raises(SystemExit, match=r"--between-cache .*names no identity"):
        align.align_set([root / n for n in names], interpolate=anonymous, between="rife", between_cache=tmp_path / "kept")
    assert _hashes(root) == files and not (tmp_path / "kept").exists()


@pytest.mark.slow
def test_video_cycle_align_between_cache_reports_hits_and_misses(tmp_path, monkeypatch, capsys):
    """The command line: `--between-cache` names the directory; the summary and the report carry the
    hits and misses. RIFE is the stand-in, found as `rife.Rife` is."""

    class Found(Blend):
        def describe(self) -> dict[str, str]:
            return {"binary": "stand-in", "model": "blend"}

    monkeypatch.setattr(align.rife_mod, "Rife", Found)
    root, names = _set(tmp_path)
    argv = [*(a for n in names for a in ("--loop-dir", str(root / n))), "--between", "rife", "--between-cache", str(tmp_path / "kept")]
    for k, label in enumerate(("first", "again")):
        assert align.main([*argv, "--report", str(tmp_path / f"{label}.json")]) == 0
        said = json.loads(capsys.readouterr().out)["between_cache"]
        written = json.loads((tmp_path / f"{label}.json").read_text())["between_cache"]
        assert said == written and said["dir"] == str((tmp_path / "kept").resolve())
        made = said["hits"] + said["misses"]
        assert made > 0 and said["hits"] == (made if k else 0)


@pytest.mark.slow
@pytest.mark.real_rife
def test_real_rife_a_kept_frame_is_the_frame_made_again(tmp_path):
    """The real binary makes the same bytes for the same frames, so a frame taken from the
    directory is the frame a pass without it makes."""
    made = rife.Rife()
    cache = rife.Cache(tmp_path / "kept")
    a, b = _legs(36, 70), _legs(60, 46)
    kept = cache.frame(made, a, b, 0.5).tobytes()
    taken = cache.frame(made, a, b, 0.5).tobytes()
    fresh = made(a, b, 0.5).tobytes()
    assert (cache.hits, cache.misses) == (1, 1) and made.made == 2
    assert _sha(taken) == _sha(kept) == _sha(fresh)
