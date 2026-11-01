# SPDX-License-Identifier: Apache-2.0
"""RIFE's torch backend (docs/loop-repair.md section 1): the binary's model in PyTorch, from the binary's own files, run
as one call of the binary runs it — rife-v4.25, the default, padded to 128 with no augmentation
(tests/video/test_rife_torch_v425.py), and rife-v4.6, the pair turned eight ways and run both ways in time, their flows
averaged at every level.

Chosen by SPRITE_GEN_RIFE_BACKEND=torch, on the device SPRITE_GEN_RIFE_DEVICE names. Nothing falls back: with torch chosen
the binary is never run, a device that cannot be had is not traded for another, and PyTorch missing is RIFE not
installed, its reason named.

The tests marked `torch_rife` run the network: they need PyTorch and the models (`rife.locate_model()`, rife-v4.6 beside it), are
skipped where either is missing (tests/conftest.py) and run with none skipped by the lanes (`-m torch_rife`,
docs/release.md), on the device SPRITE_GEN_RIFE_DEVICE names. The rest need neither and stand a runner in where one runs.

The fixtures are synthetic: discs, legs and walkers drawn here or in tests/video/test_rife.py and test_crossfade.py,
and tests/fixtures/rife-walkers — walker pairs, clean and faulty, with the binary's own -x -z frame of each made on
an Apple GPU, the engine's verdict on it, and how far the same binary's frame on Mesa llvmpipe lay from it; and, for
each model, the pairs it is judged alike on by the binary and the torch backend (`alike`)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import align, rife, rife_install
from sprite_gen.video.interpolation_quality import faults, looks
from tests.video.test_rife import _disc, _legs, _marked, _recording_binary

WALKERS = Path(__file__).resolve().parents[1] / "fixtures" / "rife-walkers"


@pytest.fixture(scope="module")
def runner():
    """The torch backend on SPRITE_GEN_RIFE_DEVICE, from the installed model the engine runs (rife-v4.25 by default)."""
    from sprite_gen.video import rife_torch

    return rife_torch.TorchRunner(rife.locate_model(), rife.device())


@pytest.fixture(scope="module")
def runner46():
    """The torch backend on SPRITE_GEN_RIFE_DEVICE, from the installed rife-v4.6, which `sprite-gen rife install` puts beside
    rife-v4.25: the model whose augmentation the binary runs inside one call, and the one the walker fixtures recorded."""
    from sprite_gen.video import rife_torch

    return rife_torch.TorchRunner(rife.locate_model().parent / "rife-v4.6", rife.device())


def _key(x: np.ndarray) -> tuple[tuple[int, ...], bytes]:
    return x.shape, np.ascontiguousarray(x).tobytes()


@pytest.mark.torch_rife
def test_the_eight_ways_turn_back_and_are_the_eight_turns_of_the_single_passes():
    """`-x`'s eight ways (`rife_torch._geo`) are each undone by `_geo_back`, and they are the eight flips and transposes the
    single passes turn the pair (`rife.PASS_GEOMETRIES`): none of them missing, none twice."""
    import torch

    from sprite_gen.video import rife_torch

    x = np.asarray(_marked(40))[..., :3].copy()  # no flip or transpose of it is itself
    image = torch.from_numpy(x.transpose(2, 0, 1).copy())[None]
    ways = set()
    for g in range(8):
        turned = rife_torch._geo(image, g)
        assert torch.equal(rife_torch._geo_back(turned, g), image)
        ways.add(_key(turned[0].permute(1, 2, 0).numpy()))
    assert len(ways) == 8 and ways == {_key(rife._turned(x, geometry)) for geometry in rife.PASS_GEOMETRIES}


def _patch(cx: int) -> Image.Image:
    """A square of noise 36 px wide (3 px cells, the same each time) centred at (cx, 48) on transparency: every pixel of it
    a feature a flow can follow, where the inside of a flat disc gives the flow nothing to hold."""
    cells = (np.random.default_rng(7).random((12, 12, 3)) * 255).astype(np.uint8)
    im = np.zeros((96, 128, 4), dtype=np.uint8)
    im[30:66, cx - 18:cx + 18] = np.dstack([np.kron(cells, np.ones((3, 3, 1), np.uint8)), np.full((36, 36), 255, np.uint8)])
    return Image.fromarray(im, "RGBA")


@pytest.mark.torch_rife
def test_flows_turned_back_from_every_way_point_alike(runner46):
    """Five fields of different values turned to each of the sixteen ways (eight turns, each both ways in time, the backward
    swapped) and turned back are themselves. And a patch moving right, run alone each of the sixteen ways with its flows
    turned back to the pair's own frame: at t = 0.25 it stands 8 px past where it was in a and 24 px short of where it is in
    b, and every way's flows say so — the flow to a pointing back left, the one to b on to the right, both level, the mask
    taking the nearer frame (a). A sign turned the wrong way for one turn, x and y not swapped for a transpose, or a backward
    run's flows or mask not swapped, puts that way's reading elsewhere."""
    import torch

    from sprite_gen.video import rife_torch as rt

    field = torch.arange(1.0, 6.0).view(1, 5, 1, 1).expand(1, 5, 6, 10).contiguous()
    for g in range(8):
        for d in (0, 1):
            turn = rt._swap if d else (lambda c: c)
            assert torch.equal(turn(rt._to_canon(rt._from_canon(turn(field), g), g)), field)
    colour, _ = rife.inputs(_patch(40), _patch(72))  # 96 x 128, a multiple of 32: no padding
    pads = [runner46._pad(c) for c in colour]
    with torch.inference_mode():
        flows = [runner46._infer(pads, 0.25, [(g, d)], flows=True)[1][0][0].cpu().numpy() for g in range(8) for d in (0, 1)]
    yy, xx = np.mgrid[:96, :128]
    on = (np.abs(yy - 48) <= 12) & (np.abs(xx - 48) <= 12)  # inside the patch where it stands at t
    x0, y0, x1, y1, mask = np.array([[f[c][on].mean() for c in range(5)] for f in flows]).T
    assert np.all(np.abs(x0 + 8) < 2) and np.all(np.abs(x1 - 24) < 2), (x0, x1)
    assert np.all(np.abs(y0) < 1) and np.all(np.abs(y1) < 1), (y0, y1)
    assert np.all(mask > 0.5), mask


@pytest.mark.torch_rife
def test_a_frame_is_the_same_made_the_other_way_in_time_or_turned(runner46):
    """rife-v4.6's `-z`: the frame from a to b at t is the frame from b to a at 1 - t. `-x`: the frame of the pair flipped
    left-right is the frame flipped. Each to within a level of a channel (the order of a mean's terms)."""
    colour, _ = rife.inputs(_marked(40), _marked(72))
    there, back = runner46.run(colour[0], colour[1], 0.25, ("-z",)), runner46.run(colour[1], colour[0], 0.75, ("-z",))
    assert np.abs(there.astype(int) - back.astype(int)).max() <= 1
    flipped = runner46.run(colour[0][:, ::-1].copy(), colour[1][:, ::-1].copy(), 0.25, ("-x",))
    assert np.abs(flipped[:, ::-1].astype(int) - runner46.run(colour[0], colour[1], 0.25, ("-x",)).astype(int)).max() <= 1


@pytest.mark.torch_rife
@pytest.mark.parametrize("model, named", [("rife-v4.25", None), ("rife-v4.6", None), ("rife-v4.6", "")])
def test_the_frame_between_two_discs_lands_half_way(request, monkeypatch, model, named):
    """`sprite-gen rife install`'s check frame through the torch backend, each model with the augmentation every call asks
    for (rife-v4.25 none, rife-v4.6 -x -z) and rife-v4.6 with none: the disc lands half way (x 48), not cross-faded at both
    ends."""
    if named is not None:
        monkeypatch.setenv("SPRITE_GEN_RIFE_TTA", named)
    runner = request.getfixturevalue("runner46" if model == "rife-v4.6" else "runner")
    assert runner.model_name == model
    mid = rife.Rife(runner=runner)(rife_install._disc(30), rife_install._disc(66), 0.5)
    xs = np.nonzero(np.asarray(mid)[..., 3] > 127)[1]
    assert abs(xs.mean() - 48) <= 4


@pytest.mark.torch_rife
def test_the_model_loads_whole_and_any_other_bytes_are_refused_by_sha256(tmp_path):
    """The installed model fills every weight of the network and no other (loaded strict). A copy with one byte changed, a
    flownet.bin cut short or a flownet.param changed is refused by its sha256, the file named, before a weight is read."""
    import shutil

    from sprite_gen.video import rife_torch

    for held in rife.MODELS:
        model = rife.locate_model().parent / held
        state = rife_torch.state_from_ncnn(rife.model_bytes(model), held)
        assert set(state) == set(rife_torch.IFNet(held).state_dict())
        for k, (name, change) in enumerate((("flownet.bin", lambda b: b[:100] + bytes([b[100] ^ 1]) + b[101:]),
                                            ("flownet.bin", lambda b: b[: len(b) // 2]),
                                            ("flownet.param", lambda b: b.replace(b"Convolution", b"Convolutiom", 1)))):
            copy = shutil.copytree(model, tmp_path / held / str(k) / held)
            (copy / name).write_bytes(change((copy / name).read_bytes()))
            pins = " or ".join(sorted(set(rife.MODEL_SHA256[held][name].values())))
            with pytest.raises(rife.RifeUnavailable, match=rf"{name} has sha256 [0-9a-f]{{64}}, expected one of {pins}"):
                rife_torch.TorchRunner(copy, "cpu")


@pytest.mark.torch_rife
def test_the_same_frame_twice_is_the_same_bytes_and_the_identity_names_what_makes_it(runner):
    """The same pair made twice is the same bytes on the device. Its runner's identity names the backend, the device, the
    weights it loaded by sha256, the network's code and PyTorch's version; on CUDA the GPU and the settings that make a
    frame the same bytes, as they read back (cuDNN deterministic, its benchmark off, its convolutions in IEEE float32), and
    no CPU; on the CPU the instructions it runs."""
    import torch

    from sprite_gen.video import rife_torch

    made = rife.Rife(runner=runner)
    a, b = _legs(40, 64), _legs(64, 52)
    assert made(a, b, 0.5).tobytes() == made(a, b, 0.5).tobytes()
    ident = runner.identity()
    assert (ident["backend"], ident["device"], ident["model"]) == ("torch", rife.device(), rife.MODEL)
    assert all(ident["weights"][name] in pins.values() for name, pins in rife.MODEL_SHA256[rife.MODEL].items())
    assert (ident["net"], ident["torch"]) == (rife_torch.CODE_SHA256, torch.__version__)
    if runner.device == "cuda":
        assert ident["determinism"] == {"cudnn_deterministic": True, "cudnn_benchmark": False, "cudnn_conv_fp32_precision": "ieee"}
        assert ident["gpu"] == torch.cuda.get_device_name() and "cpu" not in ident
    else:
        assert ident["cpu"] == torch.backends.cpu.get_cpu_capability() and "gpu" not in ident and "determinism" not in ident


def _walkers() -> dict:
    return json.loads((WALKERS / "walkers.json").read_text())["pairs"]


def _walker(key: str, name: str) -> Image.Image:
    """A frame of tests/fixtures/rife-walkers, its pixels the ones recorded."""
    im = Image.open(WALKERS / f"{key}.{name}.png").convert("RGBA")
    assert hashlib.sha256(im.tobytes()).hexdigest() == _walkers()[key]["sha256"][name]
    return im


def _verdict(made: Image.Image, a: Image.Image, b: Image.Image, t: float, apart: np.ndarray) -> tuple[list[str], list[str]]:
    """The engine's faults and looks of a made frame and its passes, every reading the overlapped wrap takes."""
    m = {**rife.smear(made, a, b), "outline_shift": rife.outline_shift(made, a, b), "ghost": rife.ghost(made),
         "crossfade": rife.crossfade(made, a, b, t), "torn": rife.torn(apart, made)}
    return faults(m), looks(m)


def _apart(m: Image.Image, n: Image.Image) -> tuple[float, float]:
    """Mean and largest absolute difference of two frames, premultiplied RGBA 0..255, inside the box either covers."""
    pm, pn = (np.dstack([x[..., :3] * x[..., 3:] / 255.0, x[..., 3:]]) for x in
              (np.asarray(f, dtype=np.float64) for f in (m, n)))
    ys, xs = np.nonzero((pm[..., 3] > 0) | (pn[..., 3] > 0))
    d = np.abs(pm - pn)[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return float(d.mean()), float(d.max())


@pytest.mark.torch_rife
@pytest.mark.timeout(900)  # a frame and its sixteen passes, seven pairs: seconds on a GPU, a minute or two on two CPU cores
@pytest.mark.parametrize("key", sorted(_walkers()))
def test_walkers_are_judged_as_the_binary_judged_them(runner46, key):
    """Each recorded walker pair, its rife-v4.6 frame and passes made by the torch backend, has the verdict the binary's
    frame had,
    on a machine with no binary: the same faults (smear, outline, ghost, torn) and looks (crossfade). The pairs were
    picked where the binary on an Apple GPU, the binary on Mesa llvmpipe and the torch backend on a CPU all gave that
    verdict, every reading at least a tenth of its bound away from it. The frame lies from the binary's no further than
    twice as far as the binary's own frames on those two GPUs lay apart."""
    rec = _walkers()[key]
    a, b, ncnn = (_walker(key, name) for name in ("a", "b", "ncnn"))
    made, apart = rife.Rife(runner=runner46).read(a, b, rec["t"])
    assert _verdict(made, a, b, rec["t"], apart) == (rec["faults"], rec["looks"])
    mean, most = _apart(made, ncnn)
    assert mean <= 2 * rec["gpu_gap"]["mean_abs"] and most <= 2 * rec["gpu_gap"]["max_abs"], (mean, most, rec["gpu_gap"])


def _alike() -> dict:
    """The walker pairs each model is judged alike on (walkers.json `alike`): those its own frames met the pick's rule on,
    the binary on an Apple GPU and on llvmpipe and the torch backend on a CPU giving the same verdict, every reading at
    least a tenth of its bound away from it (`picked` says what was left out, and why)."""
    return json.loads((WALKERS / "walkers.json").read_text())["alike"]


@pytest.mark.real_rife
@pytest.mark.torch_rife
@pytest.mark.timeout(1800)  # the binary on a CPU's Vulkan driver: a minute or more a pair
@pytest.mark.parametrize("model, key", [(model, key) for model, picked in sorted(_alike().items()) for key in picked["pairs"]])
def test_the_binary_and_the_torch_backend_judge_the_walkers_alike_on_one_machine(model, key):
    """The walker pairs picked for each model through this machine's binary (llvmpipe on the Linux lane) and the torch
    backend, both running that model by its name: the same verdict for each. rife-v4.6 on the seven recorded for it,
    rife-v4.25, the default, on the six its own frames met the rule on."""
    from sprite_gen.video import rife_torch

    t = _walkers()[key]["t"]
    a, b = _walker(key, "a"), _walker(key, "b")
    binary, held = rife.locate()
    verdicts = []
    for made in (rife.Rife(binary, held.parent / model),
                 rife.Rife(runner=rife_torch.TorchRunner(rife.locate_model().parent / model, rife.device()))):
        assert made.model_name == model
        frame, apart = made.read(a, b, t)
        verdicts.append(_verdict(frame, a, b, t, apart))
    assert verdicts[0] == verdicts[1]


# --- a device that fails: a RIFE that fails, in PyTorch's words -------------------------------------------------------

# A device that fails, in PyTorch's words: the GPU out of its memory, its driver failing on a call, CUDA not starting,
# and a CUDA call queued before CUDA started failing when it did.
FAILURE_TEXT = {
    "OutOfMemoryError": "CUDA out of memory. Tried to allocate 72.00 MiB. GPU 0 has a total capacity of 14.56 GiB of which "
                        "21.81 MiB is free.",
    "AcceleratorError": "CUDA error: an illegal memory access was encountered",
    "RuntimeError": "No CUDA GPUs are available",
    "DeferredCudaCallError": "CUDA call failed lazily at initialization with error: device-side assert triggered",
}
# Where it fails: as the network, its model loaded, is put on it, or as a frame runs on it.
WHERE = {"set up": ("IFNet", "to"), "run": ("TorchRunner", "_run")}


def _failure(name: str) -> BaseException:
    import torch

    return {"OutOfMemoryError": torch.OutOfMemoryError, "AcceleratorError": torch.AcceleratorError, "RuntimeError": RuntimeError,
            "DeferredCudaCallError": torch.cuda.DeferredCudaCallError}[name](FAILURE_TEXT[name])


def _fails(monkeypatch, where: str, error: BaseException) -> None:
    """The torch backend raising `error` where WHERE names."""
    from sprite_gen.video import rife_torch

    def raised(*args, **kwargs):
        raise error

    owner, name = WHERE[where]
    monkeypatch.setattr(getattr(rife_torch, owner), name, raised)


def _made(runner, where: str) -> None:
    """The torch backend set up as `video-loop` finds it (`rife.Rife()`, torch chosen), or a frame made by it."""
    if where == "set up":
        rife.Rife()
    else:
        rife.Rife(runner=runner)(_disc(40), _disc(72), 0.5)


@pytest.mark.torch_rife
@pytest.mark.parametrize("where", sorted(WHERE))
@pytest.mark.parametrize("failure", sorted(FAILURE_TEXT))
def test_a_device_that_fails_is_a_rife_that_fails_in_pytorchs_words(runner, monkeypatch, failure, where):
    """The device failing — out of its memory, its driver failing, CUDA not starting, a CUDA call queued before CUDA
    started failing when it did — as the network, its model loaded, is put on it or as a frame runs: a RIFE that fails
    (`RifeUnavailable`, not RIFE not installed, which a loop would cut around), PyTorch's words in its message and the
    error its cause."""
    error = _failure(failure)
    _fails(monkeypatch, where, error)
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    with pytest.raises(rife.RifeUnavailable) as failed:
        _made(runner, where)
    assert not isinstance(failed.value, rife.RifeNotInstalled)
    assert FAILURE_TEXT[failure] in str(failed.value) and failed.value.__cause__ is error


@pytest.mark.torch_rife
@pytest.mark.parametrize("where", sorted(WHERE))
def test_a_fault_of_the_code_where_the_device_is_used_is_raised_as_it_is(runner, monkeypatch, where):
    """A TypeError where the network is put on the device or a frame runs is a fault of this code, not the device failing:
    raised as it is, not read as a RIFE that fails."""
    error = TypeError("to() received an invalid combination of arguments - got (str, bool)")
    _fails(monkeypatch, where, error)
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    with pytest.raises(TypeError) as raised:
        _made(runner, where)
    assert raised.value is error


@pytest.mark.torch_rife
def test_a_loop_whose_device_fails_as_the_network_is_put_on_it_fails_with_its_record(monkeypatch, tmp_path):
    """`video-loop` with a jump to repair, torch chosen, and the device out of its memory as the network is put on it: the
    loop fails as one whose RIFE fails does — its record written `failed` with PyTorch's words, the gait fallback's frames
    removed, the line saying to fix RIFE or pass --repair off — not with the error raised past them."""
    from sprite_gen.video import loop
    from tests.video.test_loop_repair import _keyed, _loop, _run

    error = _failure("OutOfMemoryError")
    _fails(monkeypatch, "set up", error)
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    filmed, _ = _loop({10: 12})
    keyed = _keyed(tmp_path, filmed)
    with pytest.raises(SystemExit, match=r"fix RIFE, or pass --repair off") as failed:
        _run(tmp_path, keyed)
    assert FAILURE_TEXT["OutOfMemoryError"] in str(failed.value) and failed.value.__cause__.__cause__ is error
    report = json.loads((tmp_path / "w.json").read_text())
    assert report["status"] == "failed" and FAILURE_TEXT["OutOfMemoryError"] in report["error"]
    assert not list((tmp_path / "out").glob(f".{loop.FALLBACK_FRAMES}*"))


@pytest.mark.torch_rife
def test_the_install_check_frame_fails_where_the_device_fails_as_the_network_is_put_on_it(monkeypatch):
    """`sprite-gen rife install --backend torch`'s check frame, the model installed and the device out of its memory as the
    network is put on it: the install says its check frame failed, in PyTorch's words, not with the error raised past
    it."""
    error = _failure("OutOfMemoryError")
    _fails(monkeypatch, "set up", error)
    with pytest.raises(SystemExit, match=r"^rife install: installed, but the check frame failed: ") as failed:
        rife_install.check(Path("unused"), rife.locate_model(), backend="torch")
    assert FAILURE_TEXT["OutOfMemoryError"] in str(failed.value) and failed.value.__cause__.__cause__ is error


# --- choosing and refusing: no torch needed ---------------------------------------------------------------------------

def _no_torch(monkeypatch) -> None:
    """PyTorch not importable, as where it is not installed."""
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.delitem(sys.modules, "sprite_gen.video.rife_torch", raising=False)


def _binary_beside_a_model(monkeypatch, tmp_path: Path) -> Path:
    """A binary that would run and log its arguments, found by SPRITE_GEN_RIFE with a model beside it: its log."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    binary, log = _recording_binary(tmp_path)
    (tmp_path / rife.MODEL).mkdir()
    for name in ("flownet.param", "flownet.bin"):
        (tmp_path / rife.MODEL / name).write_bytes(b"x")
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    return log


@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script cannot stand in for rife-ncnn-vulkan.exe")
def test_torch_chosen_and_not_installed_is_rife_not_installed_and_the_binary_is_never_run(monkeypatch, tmp_path):
    """SPRITE_GEN_RIFE_BACKEND=torch where PyTorch is not installed: RIFE not installed, naming PyTorch and the line that
    installs it — and the binary, found and runnable, is not looked for, let alone run."""
    _no_torch(monkeypatch)
    log = _binary_beside_a_model(monkeypatch, tmp_path)
    rife.Rife(*rife.locate())  # the binary is there to be found
    for device in ("cuda", "cpu"):
        monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
        monkeypatch.setenv("SPRITE_GEN_RIFE_DEVICE", device)
        with pytest.raises(rife.RifeNotInstalled, match=r'PyTorch cannot be imported .*pip install "sprite-gen\[torch\]" && '
                                                        r"sprite-gen rife install --backend torch"):
            rife.Rife()
    assert rife.install_command() == 'pip install "sprite-gen[torch]" && sprite-gen rife install --backend torch'
    assert not log.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script cannot stand in for rife-ncnn-vulkan.exe")
def test_a_loop_cut_where_torch_is_chosen_and_not_installed_says_so_and_runs_no_binary(monkeypatch, tmp_path):
    """`video-loop --repair auto` with a jump to repair, torch chosen and PyTorch not installed: the jump is cut as filmed
    with the warning and the record a RIFE not installed leaves, and the record names PyTorch and its install line, not
    the binary's; the binary beside it never runs."""
    from tests.video.test_loop_repair import _keyed, _loop, _run

    _no_torch(monkeypatch)
    log = _binary_beside_a_model(monkeypatch, tmp_path / "rife")
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    filmed, _ = _loop({10: 12})
    rep = _run(tmp_path, _keyed(tmp_path, filmed))
    jr = rep["jump_repair"]
    assert rep["status"] == "passed" and jr["applied"] is False and jr["why"] == "RIFE not installed — cut as filmed"
    assert jr["install"] == 'pip install "sprite-gen[torch]" && sprite-gen rife install --backend torch'
    assert "PyTorch cannot be imported" in jr["rife"] and jr["unmade"]["frames"] == [10]
    assert not log.exists()


@pytest.mark.parametrize("names, refused", [
    ({"SPRITE_GEN_RIFE_BACKEND": "vulkan"}, r"SPRITE_GEN_RIFE_BACKEND='vulkan': name ncnn .* or torch"),
    ({"SPRITE_GEN_RIFE_BACKEND": "torch", "SPRITE_GEN_RIFE_DEVICE": "mps"}, r"mps: the torch backend does not run on Apple's GPU"),
    ({"SPRITE_GEN_RIFE_BACKEND": "torch", "SPRITE_GEN_RIFE_DEVICE": "gpu"}, r"SPRITE_GEN_RIFE_DEVICE='gpu': name cuda .* or cpu"),
])
def test_a_backend_or_device_named_wrong_is_refused_before_anything_is_looked_for(monkeypatch, tmp_path, names, refused):
    """A backend or a device the engine does not know, or Apple's GPU: refused by name (`RifeUnavailable`, never RIFE not
    installed, which a loop would cut around), before PyTorch is imported or a binary or model looked for — nothing is
    chosen in its place."""
    _no_torch(monkeypatch)
    log = _binary_beside_a_model(monkeypatch, tmp_path)
    for name, value in names.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(rife.RifeUnavailable, match=refused) as refusal:
        rife.Rife()
    assert not isinstance(refusal.value, rife.RifeNotInstalled) and not log.exists()


@pytest.mark.torch_rife
def test_cuda_named_where_there_is_none_is_refused_not_run_on_the_cpu(monkeypatch):
    """SPRITE_GEN_RIFE_DEVICE=cuda (the default) where PyTorch finds no CUDA: refused, naming why — PyTorch built for the CPU
    only, or built for a CUDA that finds no GPU — and that nothing runs elsewhere in its place."""
    import torch

    from sprite_gen.video import rife_torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    monkeypatch.delenv("SPRITE_GEN_RIFE_DEVICE", raising=False)
    monkeypatch.setattr(torch.version, "cuda", None)
    with pytest.raises(rife.RifeUnavailable, match=r"CUDA is not available here: a PyTorch built for the CPU only .*nothing "
                                                   r"runs on another device in its place"):
        rife.Rife()
    monkeypatch.setattr(torch.version, "cuda", "13.0")
    with pytest.raises(rife.RifeUnavailable, match=r"built for CUDA 13.0, which finds no GPU"):
        rife_torch.TorchRunner(rife.locate_model(), "cuda")


def test_a_model_other_than_the_pinned_one_is_refused_and_one_not_there_is_not_installed(monkeypatch, tmp_path):
    """The model the torch backend reads (`rife.locate_model`: SPRITE_GEN_RIFE_MODEL, else the install root, never beside a
    binary): not there is RIFE not installed, naming the line that installs it and the variable; there with other bytes is
    refused by sha256 before it is read (`rife.model_bytes`)."""
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    _binary_beside_a_model(monkeypatch, tmp_path / "beside")
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    with pytest.raises(rife.RifeNotInstalled, match=r"sprite-gen rife install --backend torch.*SPRITE_GEN_RIFE_MODEL"):
        rife.locate_model()
    model = tmp_path / "other" / rife.MODEL
    model.mkdir(parents=True)
    (model / "flownet.param").write_text("another model")
    (model / "flownet.bin").write_text("another model")
    monkeypatch.setenv("SPRITE_GEN_RIFE_MODEL", str(model))
    assert rife.locate_model() == model.resolve()
    pins = " or ".join(sorted(set(rife.MODEL_SHA256[rife.MODEL]["flownet.param"].values())))
    with pytest.raises(rife.RifeUnavailable, match=rf"flownet.param has sha256 [0-9a-f]{{64}}, expected one of {pins}"):
        rife.model_bytes(model)


# --- reports and keys: a runner stood in ------------------------------------------------------------------------------

class FakeRunner:
    """A runner that makes each frame `a` itself (its colour as RIFE is given it, its coverage), and names itself."""

    def __init__(self, **identity):
        self.ident = {"backend": "torch", "device": "cpu", "model": rife.MODEL, "net": "n" * 64,
                      "weights": {name: pins["ubuntu"] for name, pins in rife.MODEL_SHA256[rife.MODEL].items()},
                      "torch": "2.13.0", "cpu": "AVX2", "arch": "x86_64", **identity}
        self.model = Path("/models") / rife.MODEL
        self.frames = self.reads = 0

    def frame(self, colour, cover, t):
        self.frames += 1
        return colour[0], cover[0]

    def read(self, colour, cover, t):
        self.reads += 1
        return colour[0], cover[0], np.zeros(cover[0].shape, np.float32)

    def identity(self):
        return dict(self.ident)

    def describe(self):
        return {"kind": "rife-torch", "device": self.ident["device"], "model": self.model.name}


def test_a_report_names_the_torch_backend_and_counts_its_runs(monkeypatch, tmp_path):
    """`describe()` names what made the frames (`kind`): `rife-ncnn-vulkan` for the binary, as before, `rife-torch` for the
    torch backend; `counts()` names its runs as the overlapped wrap's record does — the binary's calls (`binary_calls`), the
    torch backend's runs (`runs`), 2 a frame and 2 + PASS_CALLS a read."""
    binary, _ = _recording_binary(tmp_path)
    ncnn = rife.Rife(binary, tmp_path / rife.MODEL)
    assert ncnn.describe() == {"kind": "rife-ncnn-vulkan", "binary": str(binary), "model": rife.MODEL, "tta": ""}
    a, b = _disc(40), _disc(72)
    ncnn(a, b, 0.5)
    ncnn.read(a, b, 0.5)
    assert ncnn.counts() == {"binary_calls": 2 + 2 + rife.PASS_CALLS}
    fake = FakeRunner()
    torch = rife.Rife(runner=fake)
    assert torch.describe() == {"kind": "rife-torch", "device": "cpu", "model": rife.MODEL, "tta": ""}
    assert torch(a, b, 0.5).tobytes() == a.tobytes()
    frame, apart = torch.read(a, b, 0.5)
    assert frame.tobytes() == a.tobytes() and apart.shape == (96, 128) and (fake.frames, fake.reads) == (1, 1)
    assert torch.counts() == {"runs": 2 + 2 + rife.PASS_CALLS} and torch.made == 2


def test_the_loop_and_the_alignment_name_the_torch_backend_that_made_their_frames(monkeypatch, tmp_path):
    """Where `video-loop` and `video-cycle-align` find RIFE themselves and SPRITE_GEN_RIFE_BACKEND names torch, their reports
    name the torch backend as what made their frames — `kind` rife-torch, its device and model, the augmentation —
    not the binary."""
    from tests.video.test_cycle_align import _cut
    from tests.video.test_loop_repair import _keyed, _loop, _run

    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    monkeypatch.setattr(rife, "torch_runner", FakeRunner, raising=False)
    filmed, _ = _loop({10: 12})
    (tmp_path / "loop").mkdir()
    rep = _run(tmp_path / "loop", _keyed(tmp_path / "loop", filmed))
    assert rep["jump_repair"]["interpolator"] == {"kind": "rife-torch", "device": "cpu", "model": rife.MODEL, "tta": ""}
    dirs = [_cut(tmp_path, n, L) for n, L in (("S", 20), ("E", 24))]
    report = align.align_set(dirs, report_path=tmp_path / "align.json", between="rife")
    assert report["made_by_rife"] > 0
    assert report["interpolator"] == {"kind": "rife-torch", "device": "cpu", "model": rife.MODEL, "tta": ""}


@pytest.mark.parametrize("field, value", [("backend", "ncnn"), ("weights", {"flownet.param": "0" * 64, "flownet.bin": "1" * 64}),
                                          ("torch", "2.14.0"), ("device", "cuda"), ("net", "m" * 64), ("cpu", "AVX512"),
                                          ("determinism", {"cudnn_deterministic": False})])
def test_a_frame_kept_by_one_runner_is_not_taken_for_another(tmp_path, field, value):
    """`Cache` keys a frame on its RIFE's identity, the runner's part of it whole: a runner whose backend, weights, PyTorch,
    device, network code, CPU or determinism differs in any one is another key, and its frame is made, not taken."""
    a, b = _disc(40), _disc(72)
    cache = rife.Cache(tmp_path / "kept")
    one = rife.Rife(runner=FakeRunner())
    assert cache.key(one, a, b, 0.5) == cache.key(rife.Rife(runner=FakeRunner()), a, b, 0.5)
    other = rife.Rife(runner=FakeRunner(**{field: value}))
    assert cache.key(one, a, b, 0.5) != cache.key(other, a, b, 0.5)
    cache.frame(one, a, b, 0.5)
    cache.frame(other, a, b, 0.5)
    assert (cache.hits, cache.misses) == (0, 2)
