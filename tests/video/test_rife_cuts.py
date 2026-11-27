# SPDX-License-Identifier: Apache-2.0
"""The default interpolator on three public cuts (docs/loop-repair.md section 1, "Which setting"): the regression set
that chose rife-v4.25, padded to 128 with no augmentation, over rife-v4.6 with `-x -z`.

tests/fixtures/rife-cuts holds three loop cuts of the spritegen-app landing samples, drawn with thick lines (a knight's
walk, Pip's run, Pip's jump), keyed and cropped as `cuts.json` says, each frame's RGBA sha256 pinned, and what was
measured on them: each cut's frames filled 24 -> 60 fps by `held_fill.upsample` and the poses of what came out
(`held_fill.reading`), for rife-v4.6 (-x -z), rife-v4.25 through the binary (each image padded to 128, Apple GPU) and
Practical-RIFE 4.25 in PyTorch (padded to 128). Two more cuts the choice was measured on, drawn with thin lines, are
private and measured outside this repository.

What holds, wherever RIFE runs (the binary on a GPU or on llvmpipe, `real_rife`; the torch backend, `torch_rife`):

- poses: the knight's walk at least 40 (two under rife-v4.6's 42, measured: its frames 2, 34 and 36 are refused by
  rife-v4.25 in PyTorch as by the binary, and the binary refuses 6 as well, which PyTorch takes at t 0.2 — a frame on
  the bar, its ghost band made off its time 0.00602 through the binary, `align.RETIME_WINDOW_MAX` 0.006); Pip's run
  and jump within one of what rife-v4.25 took (39, 67);
- Pip's jump takes the very cells PyTorch took from the published weights padded to 128: padded to 64, as the binary pads
  this model itself, it took 48 of them (`rife.MODELS`);
- the feet: where a planted pair's feet stand still, each frame rife-v4.6 made there and the feet windows caught dragged
  2 px sideways or 2 px up (`held_fill.still_kept`'s windows over the feet band) is caught for the default model's frame
  too.

Each is many runs of RIFE at about 550 x 780 (padded to 640 x 896): minutes on a GPU, tens of minutes on llvmpipe or a
CPU's PyTorch. So they are `slow`, run by `--full` (the lanes). The feet test asks for frames between the pairs a cut was
filled at, most of which filling the cut already made: what it reads of a frame the cut's test made (`DRAG_READ`) is
read there and not made again."""

from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from sprite_gen.video import held_fill, legs, rife

CUTS = Path(__file__).resolve().parents[1] / "fixtures" / "rife-cuts"
# poses each cut must take with the default model: (at least, at most), None where only a floor is held
BOUNDS = {"knight-walk": (40, None), "pip-run": (38, 40), "pip-jump": (66, 68)}
FPS, OUT_FPS = 24, 60  # each cut is filled 24 -> 60 fps, as measured


@lru_cache(maxsize=None)
def _record() -> dict:
    return json.loads((CUTS / "cuts.json").read_text(encoding="utf-8"))["cuts"]


def _frames(cut: str) -> list[Image.Image]:
    """A cut's frames, each the pixels recorded."""
    rec = _record()[cut]
    frames = []
    for k, sha in enumerate(rec["sha256"]):
        im = Image.open(CUTS / cut / f"{k:02d}.webp").convert("RGBA")
        assert hashlib.sha256(im.tobytes()).hexdigest() == sha, (cut, k)
        frames.append(im)
    return frames


def _interpolator(request: pytest.FixtureRequest, backend: str, monkeypatch: pytest.MonkeyPatch) -> rife.Rife:
    """The default model through the binary, or through the torch backend on SPRITE_GEN_RIFE_DEVICE."""
    if backend == "torch":
        monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    made = rife.Rife()
    assert made.model_name == rife.MODEL == "rife-v4.25" and made.tta == ()
    return made


BACKENDS = [pytest.param("ncnn", marks=pytest.mark.real_rife), pytest.param("torch", marks=pytest.mark.torch_rife)]


def test_the_fixture_is_what_was_measured():
    """The three cuts, their frames whole, and the numbers the bounds come from: rife-v4.25 took fewer poses than
    rife-v4.6 only on the knight's walk, and on Pip's jump the binary padded to 128 took PyTorch's cells."""
    measured = {cut: rec["measured"] for cut, rec in _record().items()}
    assert sorted(measured) == sorted(BOUNDS)
    for cut, m in measured.items():
        assert len(_frames(cut)) == len(_record()[cut]["sha256"])
        low, high = BOUNDS[cut]
        assert low <= m["rife-v4.25"]["poses"] and (high is None or m["rife-v4.25"]["poses"] <= high)
        assert low <= m["rife-v4.25-torch"]["poses"] and (high is None or m["rife-v4.25-torch"]["poses"] <= high)
    assert measured["knight-walk"]["rife-v4.6"]["poses"] - measured["knight-walk"]["rife-v4.25"]["poses"] == 2
    lost = set(measured["knight-walk"]["rife-v4.6"]["taken"]) - set(measured["knight-walk"]["rife-v4.25"]["taken"])
    assert lost == {2, 6, 34, 36} and 6 in measured["knight-walk"]["rife-v4.25-torch"]["taken"]
    assert measured["pip-jump"]["rife-v4.25"]["taken"] == measured["pip-jump"]["rife-v4.25-torch"]["taken"]


# What the feet test reads of a frame (each drag's least kept share, `_drag_read`), by cut, backend and recorded key, kept
# where the cut's test made that very frame while filling the cut: the default model's frame between the same two
# pictures at that time, as the feet test would ask for it.
DRAG_READ: dict[tuple[str, str, str], dict[str, float | None]] = {}


def _reading_drags(interpolate: rife.Interpolate, frames: list[Image.Image], cut: str, backend: str) -> rife.Interpolate:
    """`interpolate`, keeping in DRAG_READ what the feet test reads of each frame it makes at one of the cut's drag keys."""
    keys = set(_record()[cut]["drag"]["keys"])
    index = {id(im): k for k, im in enumerate(frames)}

    def made(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        frame = interpolate(a, b, t)
        key = f"{index.get(id(a), -1):02d}-{index.get(id(b), -1):02d}-{t:.6f}"
        # Kept only where RIFE was handed the very time the feet test hands it for that key: the torch backend takes it
        # as a float32 (the binary to four places), so a time the key rounds (2/9 as 0.222222) is made again there.
        if key in keys and np.float32(t) == np.float32(float(key.rsplit("-", 1)[1])):
            DRAG_READ[(cut, backend, key)] = _drag_read(np.asarray(frame), a, b)
        return frame

    return made


def _as_measured(cut: str, out: list[Image.Image], made: list[dict]) -> None:
    """The cut filled 24 -> 60 fps (`out`, how each made cell was in `made`) as measured: its cells, its poses within the
    cut's bounds (BOUNDS); Pip's jump takes the cells PyTorch took from the published weights padded to 128."""
    rec = _record()[cut]
    reading = held_fill.reading(held_fill.same_steps(out))
    low, high = BOUNDS[cut]
    assert reading["cells"] == rec["measured"]["rife-v4.25"]["cells"]
    assert low <= reading["poses"] and (high is None or reading["poses"] <= high), held_fill.said(reading)
    if cut == "pip-jump":
        taken = sorted(r["cell"] for r in made if "taken" not in r)
        assert taken == rec["measured"]["rife-v4.25-torch"]["taken"]


@pytest.mark.slow
@pytest.mark.timeout(5400)  # 450 to 550 runs of RIFE at 640 x 896: tens of minutes on a CPU's PyTorch
@pytest.mark.parametrize("backend", [pytest.param("torch", marks=pytest.mark.torch_rife)])
@pytest.mark.parametrize("cut", sorted(BOUNDS))
def test_a_public_cut_takes_as_many_poses_with_the_default_model_as_measured(request, monkeypatch, cut, backend):
    """The cut filled whole 24 -> 60 fps by the default model through the torch backend, as measured (`_as_measured`).
    Through the binary the cut is filled in pieces and joined (below)."""
    rec = _record()[cut]
    frames = _frames(cut)
    made = _interpolator(request, backend, monkeypatch)
    interpolate = _reading_drags(made, frames, cut, backend)
    out, record = held_fill.upsample(frames, interpolate, ring=rec["ring"], fps=FPS, out_fps=OUT_FPS)
    request.node.user_properties.append(("rife_calls", made.calls))  # what the GPU lane's record of the test says
    _as_measured(cut, out, record["frames"])


# Through the binary each public cut is filled in pieces, each a test of its own, and joined by another (docs/release.md,
# "혼자 도는 시험"): the cut whole is 450 to 550 runs of the binary, past the GPU lane's budget. A piece after the first
# starts at a cell the filling starts again at: one on a frame's time, or between two frames showing one picture, is
# that frame and asks nothing of the cell before it (`held_fill.at_times`), so the cell after it is handed the very frame
# (`before`) it is handed in the cut filled whole, and a piece filled on its own is the cut's cells there byte for byte.
# The cells each piece after the first starts at:
PIECES = {"knight-walk": (20, 40), "pip-run": (20, 40), "pip-jump": (10, 62)}
JOINED = "joined_from"  # the pytest property a test joining pieces names them by (scripts/cuda_lane.py, JOINED)


def _times(cut: str) -> list[float]:
    """The times of the cut's cells, as `held_fill.upsample` fills it 24 -> 60 fps."""
    rec = _record()[cut]
    return held_fill.times(len(rec["sha256"]), FPS, OUT_FPS, ring=rec["ring"])


def _spans(cut: str) -> list[tuple[int, int]]:
    """The cells of each of the cut's pieces, in order: (its first, past its last)."""
    return list(zip((0, *PIECES[cut]), (*PIECES[cut], len(_times(cut)))))


def _digest(im: Image.Image) -> str:
    return hashlib.sha256(f"{im.mode} {im.size}".encode() + im.tobytes()).hexdigest()


@pytest.fixture
def pieces(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """Where this run keeps its pieces, and the run's mark: the GPU lane hands both to every test it runs of a pack
    (`scripts/cuda_lane.py`, CUDA_LANE_PIECES and CUDA_LANE_RUN), each piece and the join run alone in a process of its
    own; a session of its own keeps them in its base temporary directory, marked by that directory."""
    if os.environ.get("CUDA_LANE_PIECES"):
        return Path(os.environ["CUDA_LANE_PIECES"]), os.environ["CUDA_LANE_RUN"]
    base = tmp_path_factory.getbasetemp()
    return base / "pieces", str(base)


def _keep(where: Path, mark: str, test: str, cut: str, span: tuple[int, int], out: list[Image.Image], made: list[dict],
          calls: int) -> None:
    """A piece kept for the test joining the cut: each of its frames a PNG named by its cell of the cut, and a record of
    the run it is of (`mark`), the test that filled it, each frame's digest, how each made cell was (its cell counted in
    the cut)."""
    a, b = span
    folder = where / cut
    folder.mkdir(parents=True, exist_ok=True)
    for k, im in enumerate(out):
        im.save(folder / f"{a + k:03d}.png", compress_level=1)
    rec = {"run": mark, "test": test, "cells": [a, b], "sha256": [_digest(im) for im in out],
           "frames": [{**m, "cell": m["cell"] + a} for m in made], "rife_calls": calls}
    (folder / f"{a:03d}-{b:03d}.json").write_text(json.dumps(rec), encoding="utf-8")


def _png(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return im.copy()


def _joined(where: Path, mark: str, cut: str) -> tuple[list[Image.Image], list[dict], list[str]]:
    """The cut's cells joined from its pieces as this run kept them: the frames, how each made cell was, the tests that
    filled them. Fails where a piece of PIECES was not kept, was kept by another run, or its frames are not the ones it
    kept."""
    out: list[Image.Image] = []
    made: list[dict] = []
    tests: list[str] = []
    spans = _spans(cut)
    for k, (a, b) in enumerate(spans, 1):
        name = f"piece {k} of {len(spans)} of {cut} (cells {a} to {b - 1})"
        path = where / cut / f"{a:03d}-{b:03d}.json"
        if not path.is_file():
            pytest.fail(f"{name} did not run in this run: no {path}")
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("run") != mark:
            pytest.fail(f"{name} was kept by another run ({rec.get('run')}), not this one ({mark})")
        frames = [_png(where / cut / f"{c:03d}.png") for c in range(a, b)]
        if [_digest(im) for im in frames] != rec["sha256"]:
            pytest.fail(f"{name}: its frames are not the ones it kept")
        out += frames
        made += rec["frames"]
        tests.append(rec["test"])
    return out, made, tests


def test_each_piece_of_a_public_cut_after_the_first_starts_at_a_cell_the_filling_starts_again_at():
    """The pieces cover each cut's cells once, in order, and each after the first starts at a cell on a frame's time or
    between two frames showing one picture: moved to a cell that is made, a piece would start without the frame the cut
    whole hands the cell after, and could differ from it."""
    assert sorted(PIECES) == sorted(BOUNDS)
    for cut in PIECES:
        rec, frames, at = _record()[cut], _frames(cut), _times(cut)
        assert len(at) == rec["measured"]["rife-v4.25"]["cells"]
        spans = _spans(cut)
        assert [a for a, _ in spans[1:]] == [b for _, b in spans[:-1]] and all(a < b for a, b in spans)
        same = held_fill.same_steps(frames + ([frames[0]] if rec["ring"] else []))
        for a in PIECES[cut]:
            assert abs(at[a] - round(at[a])) <= held_fill.SNAP or same[math.floor(at[a])], (cut, a, at[a])


def test_a_cut_is_joined_only_from_every_piece_this_run_kept(tmp_path):
    """A piece missing, one an earlier run kept, or one whose frames are not those it kept: the join fails, and reads no
    earlier run's piece as this run's."""
    cut = "pip-jump"
    spans = _spans(cut)

    def keep(where: Path, mark: str, span: tuple[int, int]) -> None:
        a, b = span
        out = [Image.new("RGBA", (4, 4), (a, k, 0, 255)) for k in range(b - a)]
        _keep(where, mark, f"piece-{a}", cut, span, out, [{"cell": 1, "between": [0, 1]}], 2)

    def kept(where: Path) -> Path:
        for span in spans:
            keep(where, "this run", span)
        return where

    out, made, tests = _joined(kept(tmp_path / "whole"), "this run", cut)
    assert len(out) == len(_times(cut)) and [im.getpixel((0, 0))[:2] for im in out[9:11]] == [(0, 9), (10, 0)]
    assert tests == [f"piece-{a}" for a, _ in spans] and [m["cell"] for m in made] == [a + 1 for a, _ in spans]
    missing = kept(tmp_path / "missing")
    (missing / cut / f"{spans[2][0]:03d}-{spans[2][1]:03d}.json").unlink()
    with pytest.raises(pytest.fail.Exception, match="piece 3 of 3 of pip-jump \\(cells 62 to 71\\) did not run in this run"):
        _joined(missing, "this run", cut)
    earlier = kept(tmp_path / "earlier")
    keep(earlier, "an earlier run", spans[1])
    with pytest.raises(pytest.fail.Exception, match="piece 2 of 3 of pip-jump \\(cells 10 to 61\\) was kept by another run"):
        _joined(earlier, "this run", cut)
    changed = kept(tmp_path / "changed")
    Image.new("RGBA", (4, 4)).save(changed / cut / "030.png")
    with pytest.raises(pytest.fail.Exception, match="piece 2 of 3 of pip-jump \\(cells 10 to 61\\): its frames are not"):
        _joined(changed, "this run", cut)


PIECE_IDS = [(cut, span, f"{cut}-{k}of{len(_spans(cut))}") for cut in sorted(PIECES)
             for k, span in enumerate(_spans(cut), 1)]


@pytest.mark.slow
@pytest.mark.real_rife
@pytest.mark.timeout(2700)  # 60 to 340 runs of RIFE at 640 x 896: minutes on a GPU, tens of minutes on llvmpipe
@pytest.mark.parametrize("cut, span", [(c, s) for c, s, _ in PIECE_IDS], ids=[i for _, _, i in PIECE_IDS])
def test_a_piece_of_a_public_cut_is_filled_by_the_default_model_from_a_cell_the_filling_starts_again_at(
        request, monkeypatch, pieces, cut, span):
    """A piece's cells (PIECES) filled by the default model through the binary, as `held_fill.upsample` fills them in the
    cut whole, kept for the test joining the cut. A piece after the first starts at a cell that is a frame of the cut,
    made by nothing: the frame the cell after it is handed (`before`) is that frame, as in the cut filled whole."""
    rec, frames = _record()[cut], _frames(cut)
    made = _interpolator(request, "ncnn", monkeypatch)
    a, b = span
    out, record = held_fill.at_times(frames, _reading_drags(made, frames, cut, "ncnn"), _times(cut)[a:b],
                                     ring=rec["ring"], spacing=FPS / OUT_FPS)
    request.node.user_properties.append(("rife_calls", made.calls))  # what the GPU lane's record of the test says
    if a:
        first = record["source_frames"][0]
        assert first is not None and all(m["cell"] for m in record["frames"]), (
            f"cell {a} of {cut} was made, not a frame of the cut: the filling does not start again there")
        assert _digest(out[0]) == _digest(frames[first])
    _keep(*pieces, request.node.nodeid, cut, span, out, record["frames"], made.calls)


@pytest.mark.slow
@pytest.mark.real_rife
@pytest.mark.parametrize("cut", sorted(PIECES))
def test_a_public_cut_joined_from_its_pieces_takes_as_many_poses_with_the_default_model_as_measured(request, pieces,
                                                                                                    cut):
    """The cut filled 24 -> 60 fps by the default model through the binary, joined from the pieces this run kept, as
    measured (`_as_measured`); each piece after the first starts at a cell that is a frame of the cut, made by nothing."""
    out, made, tests = _joined(*pieces, cut)
    request.node.user_properties.append((JOINED, json.dumps(tests)))  # the GPU lane runs this after every piece named
    frames = {_digest(im) for im in _frames(cut)}
    for a in PIECES[cut]:
        assert all(m["cell"] != a for m in made) and _digest(out[a]) in frames, (cut, a)
    _as_measured(cut, out, made)


def _band(a: Image.Image, b: Image.Image) -> tuple[int, int]:
    """The feet band of a pair: (its top, the lowest solid row of either): the lowest `rife.GHOST_BAND` of their body
    rows, at least 8, and 4 px over it."""
    sa, sb = (np.asarray(x)[..., 3] >= legs.LOW_ALPHA for x in (a, b))
    rows = np.nonzero((sa | sb).any(axis=1))[0]
    top, bottom = int(rows[0]), int(rows[-1])
    win = max(8, round((bottom - top + 1) * rife.GHOST_BAND))
    return bottom - win + 1 - 4, bottom


def _dragged(frame: np.ndarray, top: int, bottom: int, dx: int, dy: int) -> np.ndarray:
    """`frame` with its feet band moved `dx` px right, or `-dy` px up."""
    out = frame.copy()
    band = frame[top:bottom + 1].copy()
    out[top:bottom + 1] = 0
    moved = np.zeros_like(band)
    if dx:
        moved[:, dx:] = band[:, :-dx]
    else:
        moved[:band.shape[0] + dy] = band[-dy:]
    out[top:bottom + 1] = moved
    return out


def _feet_kept(frame: Image.Image, a: Image.Image, b: Image.Image, top: int) -> float | None:
    """The least share of the still outline `held_fill.still_kept` reads a window keeps, among its windows over the feet
    band (from `top`); None where none reads."""
    A, B, F = (np.asarray(im.convert("RGBA"), dtype=np.int16) for im in (a, b, frame))
    sa, sb, sf = (x[..., 3] >= legs.LOW_ALPHA for x in (A, B, F))
    still = held_fill._edge(sa) & held_fill._edge(sb) & (np.abs(A[..., :3] - B[..., :3]).max(axis=-1) <= held_fill.STILL_RGB)
    drawn = sf & (np.abs(F[..., :3] - A[..., :3]).max(axis=-1) <= held_fill.STILL_RGB)
    near = np.asarray(Image.fromarray(np.uint8(drawn) * 255).filter(ImageFilter.MaxFilter(3))) > 0
    rows = np.nonzero((sa | sb).any(axis=1))[0]
    first, last = int(rows[0]), int(rows[-1])
    win = max(8, round((last - first + 1) * rife.GHOST_BAND))
    sr, kr = still.sum(axis=1), (still & near).sum(axis=1)
    kept = [float(kr[y:y + win].sum()) / int(sr[y:y + win].sum())
            for y in range(first, max(first, last - win + 1) + 1, max(1, win // 4))
            if int(sr[y:y + win].sum()) >= held_fill.STILL_MIN_PX and y + win > top]
    return min(kept, default=None)


def _drag_read(made: np.ndarray, a: Image.Image, b: Image.Image) -> dict[str, float | None]:
    """The least kept share the feet windows read of `made`, its feet band dragged 2 px sideways (h2) and 2 px up (v2)."""
    top, bottom = _band(a, b)
    return {kind: _feet_kept(Image.fromarray(_dragged(made, top, bottom, dx, dy)), a, b, top)
            for kind, dx, dy in (("h2", 2, 0), ("v2", 0, -2))}


@pytest.mark.slow
@pytest.mark.timeout(3600)  # 88 and 91 frames, two runs each, at 640 x 896 (fewer where the cut's test made them)
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("cut", ["knight-walk", "pip-jump"])
def test_the_default_model_s_frame_dragged_off_still_feet_is_caught_wherever_rife_v4_6_s_was(request, monkeypatch, cut,
                                                                                             backend):
    """Each recorded key — a planted pair whose feet stand still (their feet bands' solid pixels overlapping 0.9 and over)
    and a t either model made there — made by the default model, its feet band dragged 2 px sideways (h2) and 2 px up
    (v2): every key whose rife-v4.6 frame the feet windows caught (kept under `held_fill.STILL_KEPT_MIN`) is caught for
    this one too. A frame the cut's test made at a key while filling the cut is read there (DRAG_READ), not made again."""
    rec = _record()[cut]
    frames = _frames(cut)
    interpolate = _interpolator(request, backend, monkeypatch)
    caught: dict[str, set[str]] = {"h2": set(), "v2": set()}
    for key in rec["drag"]["keys"]:
        read = DRAG_READ.get((cut, backend, key))
        if read is None:
            i, j, t = key.split("-")
            a, b = frames[int(i)], frames[int(j)]
            read = _drag_read(np.asarray(interpolate(a, b, float(t))), a, b)
        for kind, kept in read.items():
            if kept is not None and kept < held_fill.STILL_KEPT_MIN:
                caught[kind].add(key)
    request.node.user_properties.append(("rife_calls", interpolate.calls))  # what the GPU lane's record of the test says
    for kind, wanted in rec["drag"]["caught_by_rife_v4_6"].items():
        assert not set(wanted) - caught[kind], (kind, sorted(set(wanted) - caught[kind]))
