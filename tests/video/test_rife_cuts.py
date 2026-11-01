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
CPU's PyTorch. So they are `slow`, run by `--full` (the lanes)."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from sprite_gen.video import held_fill, legs, rife

CUTS = Path(__file__).resolve().parents[1] / "fixtures" / "rife-cuts"
# poses each cut must take with the default model: (at least, at most), None where only a floor is held
BOUNDS = {"knight-walk": (40, None), "pip-run": (38, 40), "pip-jump": (66, 68)}


@lru_cache(maxsize=None)
def _record() -> dict:
    return json.loads((CUTS / "cuts.json").read_text())["cuts"]


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


@pytest.mark.slow
@pytest.mark.timeout(5400)  # 450 to 550 runs of RIFE at 640 x 896: minutes on a GPU, tens of minutes on llvmpipe or a CPU
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("cut", sorted(BOUNDS))
def test_a_public_cut_takes_as_many_poses_with_the_default_model_as_measured(request, monkeypatch, cut, backend):
    """The cut filled 24 -> 60 fps by the default model: its poses within the cut's bounds (BOUNDS); Pip's jump takes the
    cells PyTorch took from the published weights padded to 128."""
    rec = _record()[cut]
    interpolate = _interpolator(request, backend, monkeypatch)
    out, record = held_fill.upsample(_frames(cut), interpolate, ring=rec["ring"], fps=24, out_fps=60)
    reading = held_fill.reading(held_fill.same_steps(out))
    low, high = BOUNDS[cut]
    assert reading["cells"] == rec["measured"]["rife-v4.25"]["cells"]
    assert low <= reading["poses"] and (high is None or reading["poses"] <= high), held_fill.said(reading)
    if cut == "pip-jump":
        taken = sorted(r["cell"] for r in record["frames"] if "taken" not in r)
        assert taken == rec["measured"]["rife-v4.25-torch"]["taken"]


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


@pytest.mark.slow
@pytest.mark.timeout(3600)  # 88 and 91 frames, two runs each, at 640 x 896
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("cut", ["knight-walk", "pip-jump"])
def test_the_default_model_s_frame_dragged_off_still_feet_is_caught_wherever_rife_v4_6_s_was(request, monkeypatch, cut,
                                                                                             backend):
    """Each recorded key — a planted pair whose feet stand still (their feet bands' solid pixels overlapping 0.9 and over)
    and a t either model made there — made by the default model, its feet band dragged 2 px sideways (h2) and 2 px up
    (v2): every key whose rife-v4.6 frame the feet windows caught (kept under `held_fill.STILL_KEPT_MIN`) is caught for
    this one too."""
    rec = _record()[cut]
    frames = _frames(cut)
    interpolate = _interpolator(request, backend, monkeypatch)
    caught: dict[str, set[str]] = {"h2": set(), "v2": set()}
    for key in rec["drag"]["keys"]:
        i, j, t = key.split("-")
        a, b = frames[int(i)], frames[int(j)]
        made = np.asarray(interpolate(a, b, float(t)))
        top, bottom = _band(a, b)
        for kind, dx, dy in (("h2", 2, 0), ("v2", 0, -2)):
            kept = _feet_kept(Image.fromarray(_dragged(made, top, bottom, dx, dy)), a, b, top)
            if kept is not None and kept < held_fill.STILL_KEPT_MIN:
                caught[kind].add(key)
    for kind, wanted in rec["drag"]["caught_by_rife_v4_6"].items():
        assert not set(wanted) - caught[kind], (kind, sorted(set(wanted) - caught[kind]))
