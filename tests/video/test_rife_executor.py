# SPDX-License-Identifier: Apache-2.0
"""RIFE's torch backend on a GPU elsewhere (docs/loop-repair.md section 1): the host names a send it plugs in
(SPRITE_GEN_RIFE_EXECUTOR=module:attr, from a request's bytes to its answer's), which reaches the executor's side of the
same sprite-gen (`rife_torch.serve`, `rife.answer`) on a machine with the GPU. The engine knows nothing of what carries
the bytes.

A frame is one call (its two runs on the executor), a read one call (its two runs and sixteen passes, the passes
reduced to their disagreement there), and a RIFE asks the executor what it is once, as it is made, and holds that for
its life: every request carries it (`expect`), an executor that is not it makes nothing and says what differs, and an
answer from another is not handed on. So a frame kept is the RIFE's own, taken or made, and a RIFE made after the
executor changed keys its frames anew.

The executor here is stood in: a pool of devices behind the send, each answering as the executor does (`rife.answer`)
from a device whose runs follow from the images and from what it is, so another device makes other bytes. It runs
rife-v4.6, named on this host too (SPRITE_GEN_RIFE_MODEL_NAME alone: the model directory a lane names is unset), so the
augmentation each request names is the host's `-x -z` or another; an executor of another model than the host's is
refused as the RIFE is made. One test, marked `torch_rife`, serves the torch backend itself (`rife_torch.serve`) on the
model the lane names (SPRITE_GEN_RIFE_MODEL, else the default installed) and finds the frames this process makes."""

from __future__ import annotations

import collections
import hashlib
import io
import json
import sys
import zipfile
import zlib

import numpy as np
import pytest

from sprite_gen.video import rife
from tests.video.test_rife import _disc, _legs
from tests.video.test_rife_torch import _binary_beside_a_model, _no_torch

# The send these tests name (SPRITE_GEN_RIFE_EXECUTOR): the executor a test stands in (`reached`).
NAME = f"{__name__}:send"
DETERMINISM = {"cudnn_deterministic": True, "cudnn_benchmark": False, "cudnn_conv_fp32_precision": "ieee"}
# What a device of the executor is, as the torch backend names it on CUDA: no CPU, no host name.
T4 = {"backend": "torch", "device": "cuda", "model": "rife-v4.6", "net": "n" * 64, "torch": "2.13.0+cu130",
      "weights": {name: pins["ubuntu"] for name, pins in rife.MODEL_SHA256["rife-v4.6"].items()},
      "cuda": "13.0", "cudnn": 92000, "gpu": "Tesla T4", "capability": [7, 5], "driver": "610.57.04",
      "determinism": DETERMINISM}
L4 = {**T4, "gpu": "NVIDIA L4", "capability": [8, 9]}


class Device:
    """A device of the executor, stood in as the torch backend's runner: a run blends its two images at t, with one pixel
    standing for what the device is and the augmentation it ran (`ident`, `modes`) — the same device the same bytes,
    another other bytes. Counts its runs; `fails` is raised by the next run."""

    def __init__(self, **ident):
        self.ident = {**T4, **ident}
        self.runs = 0
        self.fails: BaseException | None = None

    def identity(self) -> dict:
        return json.loads(json.dumps(self.ident))

    def run(self, x: np.ndarray, y: np.ndarray, t: float, modes) -> np.ndarray:
        if self.fails is not None:
            raise self.fails
        self.runs += 1
        modes = rife.tta(self.ident["model"]) if modes is None else modes
        out = np.rint(x.astype(np.float64) * (1 - t) + y.astype(np.float64) * t).astype(np.uint8)
        out[0, 0] = list(hashlib.sha256(json.dumps([self.ident, list(modes)], sort_keys=True).encode()).digest()[:3])
        return out

    # as a runner of this process (`rife.Rife(runner=...)`): the frame the executor's device makes, made here
    def frame(self, colour, cover, t):
        return rife.frame_runs(self.run, colour, cover, t)

    def read(self, colour, cover, t):
        return (*rife.frame_runs(self.run, colour, cover, t), rife.pass_runs(self.run, colour, cover, t))

    def describe(self) -> dict:
        return {"kind": "rife-torch", "device": self.ident["device"], "model": self.ident["model"]}


class Executor:
    """The host's send, stood in: each request goes to the device of the pool `route` picks (by the calls so far) and is
    answered as the executor answers it (`rife.answer`). `ignores`: a serve that does not read `expect`, answering as the
    device it is; `code`: an executor whose sprite-gen rife.py is another; `after`: run once a frame is answered. Keeps
    each request's header (`headers`)."""

    def __init__(self, *devices: Device, route=None):
        self.devices, self.route = list(devices), route or (lambda n: 0)
        self.headers: list[dict] = []
        self.loads = 0
        self.ignores, self.code, self.after = False, None, None

    def __call__(self, request: bytes) -> bytes:
        device = self.devices[self.route(len(self.headers))]
        header, arrays = rife.unpack(request)
        self.headers.append(header)
        if self.ignores and "expect" in header:
            request = rife.pack({**header, "expect": {"protocol": rife.PROTOCOL, **device.identity()}}, arrays)

        def loaded():
            self.loads += 1
            return device

        with pytest.MonkeyPatch.context() as mp:
            if self.code is not None:
                mp.setattr(rife, "CODE_SHA256", self.code)
            answer = rife.answer(request, loaded)
        if self.after is not None and not header.get("probe"):
            self.after()
        return answer

    def kinds(self) -> list[str]:
        return ["probe" if h.get("probe") else "read" if h.get("read") else "frame" for h in self.headers]


reached: Executor | None = None


def send(request: bytes) -> bytes:
    """The send NAME names: the executor the test stands in."""
    assert reached is not None, "no executor stood in"
    return reached(request)


@pytest.fixture(autouse=True)
def _host_model(request, monkeypatch) -> None:
    """This host runs rife-v4.6, the model of the devices stood in (T4), and names it one way:
    SPRITE_GEN_RIFE_MODEL_NAME, with no SPRITE_GEN_RIFE_MODEL left from a lane. A lane's names its own model's directory
    (rife-v4.25, scripts/t4_lane.py), which no device stood in runs and no test stood in reads; beside the name, the
    engine refuses the two as two models (`rife.model_name`). The test marked torch_rife serves the torch backend itself
    on the lane's model, and keeps the lane's environment."""
    if request.node.get_closest_marker("torch_rife"):
        return
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    monkeypatch.setenv("SPRITE_GEN_RIFE_MODEL_NAME", "rife-v4.6")


@pytest.fixture
def executor(monkeypatch) -> Executor:
    """The torch backend chosen and its frames sent (NAME) to an executor of one T4 running rife-v4.6, the model this host
    names; no device named on this host."""
    stood = Executor(Device())
    monkeypatch.setattr(sys.modules[__name__], "reached", stood)
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    monkeypatch.setenv("SPRITE_GEN_RIFE_EXECUTOR", NAME)
    monkeypatch.delenv("SPRITE_GEN_RIFE_DEVICE", raising=False)
    return stood


def _remote(ident: dict) -> dict:
    """A device's identity as the executor answers it."""
    return {"protocol": rife.PROTOCOL, **json.loads(json.dumps(ident))}


# --- a frame, a read and the probe: one call each --------------------------------------------------------------------

def test_a_frame_is_one_call_a_read_one_call_and_the_rife_asks_the_executor_once_as_it_is_made(executor, monkeypatch):
    """A RIFE made with an executor asks it what it is once (the probe), as it is made; a frame is then one call of the
    send (its two runs on the executor's device), a read one call (two runs and PASS_CALLS passes), and what comes back is
    the frame and the disagreement a runner of this process on that device makes, byte for byte, with the augmentation
    this host asks for. The report names the executor and its GPU; its counts name the runs and the calls."""
    device = executor.devices[0]
    made = rife.Rife()
    assert executor.kinds() == ["probe"] and device.runs == 0
    a, b = _legs(40, 64), _legs(64, 52)
    here = rife.Rife(runner=Device())
    assert made(a, b, 0.25).tobytes() == here(a, b, 0.25).tobytes()
    assert executor.kinds() == ["probe", "frame"] and device.runs == 2
    frame, apart = made.read(a, b, 0.25)
    there, apart_here = here.read(a, b, 0.25)
    assert frame.tobytes() == there.tobytes() and apart.dtype == np.float32 and np.array_equal(apart, apart_here)
    assert executor.kinds() == ["probe", "frame", "read"] and device.runs == 2 + 2 + rife.PASS_CALLS
    assert made.counts() == {"runs": 2 + 2 + rife.PASS_CALLS, "executor_calls": 3}
    assert made.describe() == {"kind": "rife-torch", "executor": NAME, "device": "cuda", "gpu": "Tesla T4",
                               "model": "rife-v4.6", "tta": "-x -z"}
    monkeypatch.setenv("SPRITE_GEN_RIFE_TTA", "-z")  # the host's augmentation, read as each frame is asked for
    other = made(a, b, 0.25)
    assert other.tobytes() == here(a, b, 0.25).tobytes() and executor.headers[-1]["modes"] == ["-z"]


def test_each_request_carries_the_engine_code_and_the_identity_the_rife_holds(executor):
    """The probe names the protocol and this host's engine code (`rife.CODE_SHA256`); every frame and read names them too,
    with the executor's identity the RIFE was made with (`expect`), the fraction exactly, the augmentation and whether the
    passes are read — and the images RIFE is given, the colour as `rife.inputs` makes it and the coverage one channel."""
    made = rife.Rife()
    a, b = _disc(40), _disc(72)
    made(a, b, 1 / 3)
    probe, frame = executor.headers
    assert probe == {"protocol": rife.PROTOCOL, "code": rife.CODE_SHA256, "probe": True}
    assert frame == {"protocol": rife.PROTOCOL, "code": rife.CODE_SHA256, "expect": _remote(T4), "t": 1 / 3,
                     "modes": ["-x", "-z"], "read": False}
    colour, cover = rife.inputs(a, b)
    captured = {}

    def keeps(request: bytes) -> bytes:
        captured.update(rife.unpack(request)[1])
        return executor(request)

    rife.RemoteRunner(keeps, "keeps").frame(colour, cover, 0.5)
    assert set(captured) == {"colour_a", "colour_b", "cover_a", "cover_b"}
    for name, want in (("colour_a", colour[0]), ("colour_b", colour[1]), ("cover_a", cover[0]), ("cover_b", cover[1])):
        assert captured[name].dtype == np.uint8 and np.array_equal(captured[name], want)


@pytest.mark.parametrize("host, held", [(None, "rife-v4.6"), ("rife-v4.6", "rife-v4.25"), ("rife-v4.6", None)])
def test_an_executor_of_another_model_than_this_host_runs_is_refused_as_the_rife_is_made(executor, monkeypatch, host, held):
    """The model is the executor's, named in its identity, and this host runs one (SPRITE_GEN_RIFE_MODEL_NAME, else
    rife-v4.25): an executor running another — or naming none, an older sprite-gen — is refused by the probe, both named,
    before a frame is asked for; one running the host's model makes its frames with the host's augmentation for it."""
    if host is None:
        monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL_NAME", raising=False)
    else:
        monkeypatch.setenv("SPRITE_GEN_RIFE_MODEL_NAME", host)
    executor.devices[0].ident = {k: v for k, v in {**T4, "model": held}.items() if v is not None}
    with pytest.raises(rife.RifeUnavailable, match=rf"the executor runs the model {held!r}, and this host runs "
                                                   rf"{host or rife.MODEL} .*run one model on both sides") as refused:
        rife.Rife()
    assert not isinstance(refused.value, rife.RifeNotInstalled)
    assert executor.kinds() == ["probe"] and executor.devices[0].runs == 0
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL_NAME", raising=False)
    executor.devices[0].ident = {**T4, "model": rife.MODEL}
    made = rife.Rife()
    made(_disc(40), _disc(72), 0.5)
    assert made.describe()["model"] == rife.MODEL and executor.headers[-1]["modes"] == []


def test_the_host_runs_with_no_pytorch_installed(executor, monkeypatch):
    """The host that sends its frames needs no PyTorch: with PyTorch not importable a RIFE is made, makes its frame through
    the executor, and the torch backend's module is never imported here."""
    _no_torch(monkeypatch)
    made = rife.Rife()
    assert made(_disc(40), _disc(72), 0.5).size == (128, 96)
    assert "sprite_gen.video.rife_torch" not in sys.modules


# --- identity and keys ------------------------------------------------------------------------------------------------

def test_the_identity_is_the_executors_as_the_rife_was_made_and_this_hosts_as_it_is_now(executor, tmp_path):
    """A RIFE's identity is the executor's as the probe answered it, the executor named, and this host's part as it reads
    now; a frame kept for it is not taken for the same device run in this process, which is another RIFE."""
    made = rife.Rife()
    ident = json.loads(made.identity())
    assert {k: ident[k] for k in _remote(T4)} == _remote(T4) and ident["executor"] == NAME
    assert ident["code"] == rife.CODE_SHA256 and ident["tta"] == ["-x", "-z"] and "binary" not in ident
    a, b = _disc(40), _disc(72)
    cache = rife.Cache(tmp_path / "kept")
    assert cache.key(made, a, b, 0.5) != cache.key(rife.Rife(runner=Device()), a, b, 0.5)


def test_a_frame_kept_from_a_t4_is_the_rifes_own_after_the_executor_turns_l4_and_the_next_rife_makes_its_own(executor, tmp_path):
    """netti-20261021-082833, turned: a T4 frame kept, then the executor turns L4. The same RIFE asked the same frame takes
    the T4 frame kept (hits 1) — the frame this RIFE makes — and asked to make it again is refused, the GPU named, and
    nothing made: every frame it handed on is a T4 frame. A RIFE made after the change asks again (L4), misses (hits 0,
    misses 1), and keeps the L4 frame under its own key. Every file kept holds the pixels of the identity its key names."""
    device = executor.devices[0]
    a, b = _disc(40), _disc(72)
    t4_frame = rife.Rife(runner=Device())(a, b, 0.5).tobytes()
    l4_frame = rife.Rife(runner=Device(**L4))(a, b, 0.5).tobytes()
    assert t4_frame != l4_frame
    made, cache = rife.Rife(), rife.Cache(tmp_path / "kept")
    first = cache.frame(made, a, b, 0.5)
    device.ident = dict(L4)
    again = cache.frame(made, a, b, 0.5)
    assert first.tobytes() == again.tobytes() == t4_frame and (cache.hits, cache.misses, cache.not_kept) == (1, 1, 0)
    runs = device.runs
    with pytest.raises(rife.RifeUnavailable, match=r"capability: \[7, 5\] -> \[8, 9\], gpu: Tesla T4 -> NVIDIA L4") as refused:
        made(a, b, 0.5)
    assert device.runs == runs and "made nothing" in str(refused.value)
    after, later = rife.Rife(), rife.Cache(tmp_path / "kept")
    made_after = later.frame(after, a, b, 0.5)
    assert made_after.tobytes() == l4_frame and (later.hits, later.misses) == (0, 1)
    kept = {p.name for p in (tmp_path / "kept").glob("*.png")}
    assert kept == {f"{cache.key(made, a, b, 0.5)}.png", f"{later.key(after, a, b, 0.5)}.png"}
    for rifed, pixels in ((made, t4_frame), (after, l4_frame)):
        assert rife._kept(tmp_path / "kept" / f"{cache.key(rifed, a, b, 0.5)}.png", a.size).tobytes() == pixels


@pytest.mark.parametrize("field, value", [("gpu", "NVIDIA L4"), ("driver", "615.00.00"), ("net", "m" * 64),
                                          ("weights", {"flownet.param": "0" * 64, "flownet.bin": "1" * 64}),
                                          ("torch", "2.14.0+cu130"), ("cudnn", 92100),
                                          ("determinism", {**DETERMINISM, "cudnn_conv_fp32_precision": "tf32"})])
def test_an_executor_other_than_the_one_the_rife_was_made_with_makes_nothing_and_says_what_differs(executor, field, value):
    """Any one thing the executor's frames depend on changed after the RIFE was made — its GPU, driver, network code,
    weights, PyTorch, cuDNN or determinism: a frame or a read asked for is refused before the device runs anything, the
    field that differs named; nothing is asked again and nothing runs elsewhere."""
    device = executor.devices[0]
    made = rife.Rife()
    device.ident[field] = value
    a, b = _disc(40), _disc(72)
    for ask in (made, made.read):
        with pytest.raises(rife.RifeUnavailable, match=rf"\b{field}: ") as refused:
            ask(a, b, 0.5)
        assert not isinstance(refused.value, rife.RifeNotInstalled)
    assert device.runs == 0 and executor.kinds() == ["probe", "frame", "read"]


def test_an_answer_from_another_than_the_rife_runs_as_is_not_handed_on_or_kept(executor, tmp_path):
    """A serve that does not read `expect` and answers as another device (an L4 where the RIFE was made on a T4): the host
    reads the identity of the answer, refuses it by what differs and neither hands the frame on nor keeps it."""
    device = executor.devices[0]
    executor.ignores = True
    made, cache = rife.Rife(), rife.Cache(tmp_path / "kept")
    a, b = _disc(40), _disc(72)
    cache.frame(made, a, b, 0.5)
    device.ident = dict(L4)
    with pytest.raises(rife.RifeUnavailable, match=r"answered as another .*gpu: Tesla T4 -> NVIDIA L4.*not handed on"):
        cache.frame(made, _disc(44), b, 0.5)
    assert (cache.hits, cache.misses) == (0, 1) and len(list((tmp_path / "kept").glob("*.png"))) == 1


def test_another_device_of_the_same_identity_runs_on_and_the_next_rife_takes_every_frame_with_one_call(executor, tmp_path):
    """Two devices behind the send that are the same (another container of the same image, GPU and driver; its host's CPU
    and name are no part of it), the calls going to each in turn: the RIFE runs on, and a RIFE made later over the same
    directory takes every frame kept, asking the executor once (its probe) and nothing else."""
    executor.devices.append(Device())
    executor.route = lambda n: n % 2
    pairs = [(_disc(40 + 4 * k), _disc(72), 0.5) for k in range(4)]
    cache = rife.Cache(tmp_path / "kept")
    made = rife.Rife()
    first = [cache.frame(made, *p).tobytes() for p in pairs]
    assert cache.misses == 4 and [d.runs for d in executor.devices] == [4, 4]
    calls = len(executor.headers)
    after, later = rife.Rife(), rife.Cache(tmp_path / "kept")
    again = [later.frame(after, *p).tobytes() for p in pairs]
    assert again == first and (later.hits, later.misses) == (4, 0) and executor.kinds()[calls:] == ["probe"]


def test_a_pool_of_t4s_and_l4s_is_refused_at_the_first_request_an_l4_takes(executor):
    """A pool whose GPU may be either of two (a fallback list): the RIFE made on the T4 that took its probe is refused at
    the first frame an L4 takes, the GPU named, never handed an L4 frame among its T4 ones."""
    executor.devices.append(Device(**L4))
    executor.route = lambda n: 1 if n == 3 else 0
    made = rife.Rife()
    a, b = _disc(40), _disc(72)
    made(a, b, 0.25)
    made(a, b, 0.5)
    with pytest.raises(rife.RifeUnavailable, match=r"gpu: Tesla T4 -> NVIDIA L4"):
        made(a, b, 0.75)
    assert [d.runs for d in executor.devices] == [4, 0]


def test_a_frame_made_while_this_hosts_augmentation_changed_is_handed_on_and_not_kept(executor, monkeypatch, tmp_path):
    """This host's part of the identity read again once a frame is made: where it changed while the executor made it, the
    frame is handed on and not kept (`not_kept`), as for a RIFE of this process."""
    executor.after = lambda: monkeypatch.setenv("SPRITE_GEN_RIFE_TTA", "-x")
    cache = rife.Cache(tmp_path / "kept")
    cache.frame(rife.Rife(), _disc(40), _disc(72), 0.5)
    assert (cache.misses, cache.not_kept) == (1, 1) and not list((tmp_path / "kept").glob("*.png"))


# --- refused and failing ----------------------------------------------------------------------------------------------

def test_an_executor_of_other_engine_code_is_refused_as_the_rife_is_made_and_runs_nothing(executor):
    """The executor's sprite-gen rife.py is not this host's (the frame's two runs and the passes' reduction are defined
    there): the probe is refused, both named, before the executor loads or runs anything."""
    executor.code = "0" * 64
    with pytest.raises(rife.RifeUnavailable, match=rf"the executor's sprite-gen rife.py is {'0' * 64}, this one's "
                                                   rf"{rife.CODE_SHA256}: run the same sprite-gen on both sides"):
        rife.Rife()
    assert executor.kinds() == ["probe"] and executor.loads == 0 and executor.devices[0].runs == 0


def test_a_request_of_another_protocol_is_answered_by_the_protocol_before_anything_is_loaded(monkeypatch):
    """The executor answers a request of another protocol by its own, loading nothing; the host refuses that answer by
    both."""

    def unloaded():
        raise AssertionError("loaded")

    header, _ = rife.unpack(rife.answer(rife.pack({"protocol": rife.PROTOCOL + 1, "code": rife.CODE_SHA256, "probe": True}),
                                        unloaded))
    assert header == {"error": "protocol", "protocol": rife.PROTOCOL}
    with pytest.raises(rife.RifeUnavailable, match=rf"the executor speaks protocol {rife.PROTOCOL + 1}, this one "
                                                   rf"{rife.PROTOCOL}"):
        rife.RemoteRunner(lambda request: rife.pack({"error": "protocol", "protocol": rife.PROTOCOL + 1}), "other")


def _pickled() -> bytes:
    """A request whose array is pickled objects."""
    out = io.BytesIO()
    np.savez(out, header=np.frombuffer(json.dumps({"protocol": 1, "code": rife.CODE_SHA256, "probe": True}).encode(),
                                       np.uint8), colour_a=np.array([object()], dtype=object))
    return out.getvalue()


def _probe() -> bytes:
    """The probe a RIFE sends as it is made."""
    return rife.pack({"protocol": rife.PROTOCOL, "code": rife.CODE_SHA256, "probe": True})


def _member(data: bytes, name: str) -> tuple[int, int, int]:
    """A member of an archive: where its local header starts, where its bytes (stored or compressed) start, and how many."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(name)
    start = info.header_offset
    names, extra = (int.from_bytes(data[start + k:start + k + 2], "little") for k in (26, 28))
    return start, start + 30 + names + extra, info.compress_size


def _flipped(data: bytes, at: int) -> bytes:
    """`data` with every bit of its byte at `at` turned over."""
    out = bytearray(data)
    out[at] ^= 0xFF
    return bytes(out)


def _block_type_3(data: bytes) -> bytes:
    """`data` with its header's first DEFLATE block given the reserved type 3, which no decompressor reads."""
    _, at, _ = _member(data, "header.npy")
    out = bytearray(data)
    out[at] |= 0b110
    return bytes(out)


def test_a_request_that_does_not_read_is_answered_so_and_nothing_pickled_is_loaded():
    """A request that is not the format, holds pickled objects, or was damaged on the way (a block that does not
    decompress) is answered as one that does not read, in the reading's words, nothing loaded; pickled objects are never
    loaded (NumPy's `allow_pickle=False`)."""

    def unloaded():
        raise AssertionError("loaded")

    for request, words in ((_block_type_3(_probe()), "zlib.error: Error -3 while decompressing data: invalid block type"),
                           (b"not a request", "zipfile.BadZipFile: File is not a zip file"),
                           (_pickled(), "Object arrays cannot be loaded when allow_pickle=False")):
        header, arrays = rife.unpack(rife.answer(request, unloaded))
        assert header["error"] == "failed" and header["message"].startswith("the request does not read: ") and not arrays
        assert words in header["message"]


class Answers:
    """A send that answers the probe as the T4 executor would and every later request with `later`."""

    def __init__(self, later):
        self.later, self.calls = later, 0

    def __call__(self, request: bytes) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return rife.answer(request, lambda: Device())
        return self.later(request)


def _raises(error: BaseException):
    def raised(request: bytes) -> bytes:
        raise error
    return raised


def _wrong_size(request: bytes) -> bytes:
    return rife.pack({"identity": _remote(T4)}, {"rgb": np.zeros((2, 2, 3), np.uint8), "alpha": np.zeros((2, 2), np.uint8)})


def _failed_there(request: bytes) -> bytes:
    device = Device()
    device.fails = rife.RifeUnavailable("rife-torch on cuda failed: CUDA out of memory. Tried to allocate 72.00 MiB.")
    return rife.answer(request, lambda: device)


FAILING = {
    "the send failing": (_raises(ConnectionError("Connection reset by peer")), r"sending to the executor failed: Connection reset by peer"),
    "an answer that is not one": (lambda request: b"<html>502 Bad Gateway</html>", r"the executor's answer does not read"),
    "an answer that is not bytes": (lambda request: "<html>502 Bad Gateway</html>", r"the executor's answer does not read: str, not bytes"),
    "an answer of another size": (_wrong_size, r"the executor's answer does not read: rgb is \(2, 2, 3\) uint8, not \(96, 128, 3\)"),
    "the executor's device failing": (_failed_there, r"the executor failed: rife-torch on cuda failed: CUDA out of memory"),
}


@pytest.mark.parametrize("case", sorted(FAILING))
def test_a_send_or_an_executor_that_fails_is_a_rife_that_fails_in_its_own_words(case):
    """The send failing (its error the cause), an answer that does not read or is not the frame's size, and the executor's
    own failure (its device out of memory): a RIFE that fails (`RifeUnavailable`, not RIFE not installed, which a loop would
    cut around), in the words of what failed — nothing asked again, nothing run elsewhere."""
    later, words = FAILING[case]
    answers = Answers(later)
    made = rife.Rife(runner=rife.RemoteRunner(answers, "stood in"))
    with pytest.raises(rife.RifeUnavailable, match=words) as failed:
        made(_disc(40), _disc(72), 0.5)
    assert not isinstance(failed.value, rife.RifeNotInstalled) and answers.calls == 2
    if case == "the send failing":
        assert isinstance(failed.value.__cause__, ConnectionError)


class Damaged:
    """A send to a T4 executor (`rife.answer`) that answers the probe, makes the answer to the first request after it once
    (`made`), and hands that answer back to every request after the probe as `damage` leaves it."""

    def __init__(self, damage=None):
        self.damage, self.made, self.calls = damage, None, 0

    def __call__(self, request: bytes) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return rife.answer(request, lambda: Device())
        if self.made is None:
            self.made = rife.answer(request, lambda: Device())
        return self.made if self.damage is None else self.damage(self.made)


def _stored(data: bytes) -> bytes:
    """An answer as an archive stored, not compressed (`np.savez`), which reads as the answer does."""
    header, arrays = rife.unpack(data)
    out = io.BytesIO()
    np.savez(out, header=np.frombuffer(json.dumps(header).encode(), np.uint8), **arrays)
    return out.getvalue()


def _colour_byte(data: bytes) -> bytes:
    """An answer stored, the last byte of its colour turned over: an array's own bytes, under the CRC-32 of its member."""
    stored = _stored(data)
    _, at, size = _member(stored, "rgb.npy")
    return _flipped(stored, at + size - 1)


def _shorter_header(data: bytes) -> bytes:
    """An answer stored, the header of its colour's array made 16 bytes shorter (the low byte of its length): the header's
    dict is still whole, so NumPy's archive reader would read the array from 16 bytes early and stop short of the
    member's end, where its CRC-32 is checked — and hand on other pixels."""
    stored = bytearray(_stored(data))
    _, at, _ = _member(bytes(stored), "rgb.npy")
    stored[at + 8] -= 16
    return bytes(stored)


DAMAGED = {
    # one byte of the answer damaged on the way, or the answer cut short: (the damage, what reading it raises, in its words)
    "a member's local header": (lambda data: _flipped(data, _member(data, "rgb.npy")[0]), zipfile.BadZipFile,
                                "Bad magic number for file header"),
    "a compressed block": (_block_type_3, zlib.error, "Error -3 while decompressing data: invalid block type"),
    "an array's header": (_shorter_header, zipfile.BadZipFile, "Bad CRC-32 for file 'rgb.npy'"),
    "an array's own bytes": (_colour_byte, zipfile.BadZipFile, "Bad CRC-32 for file 'rgb.npy'"),
    "the archive's end": (lambda data: _flipped(data, len(data) - 22), zipfile.BadZipFile, "File is not a zip file"),
    "the answer cut short": (lambda data: data[:len(data) // 2], zipfile.BadZipFile, "File is not a zip file"),
}


@pytest.mark.parametrize("case", sorted(DAMAGED))
def test_an_answer_damaged_in_a_header_a_block_an_array_or_its_end_is_a_rife_that_fails_in_the_readings_words(case):
    """netti-20261022-045132: the executor's answer with one byte damaged on the way — a member's local header, a
    compressed block (given the reserved block type, which raises the decompressor's own error), an array's header made
    shorter (read as NumPy's archive reader reads it, the array from the wrong place, handed on), an array's own bytes
    under its CRC-32, the archive's end record — or cut short. Each is a RIFE that fails (`RifeUnavailable`, not RIFE not
    installed), naming that the answer does not read in the words of what reading it raised, which is its cause; nothing
    is asked again. The same answer undamaged is the frame the device makes in this process, byte for byte."""
    damage, error, words = DAMAGED[case]
    a, b = _disc(40), _disc(72)
    whole = rife.Rife(runner=rife.RemoteRunner(Damaged(), "stood in"))
    assert whole(a, b, 0.5).tobytes() == rife.Rife(runner=Device())(a, b, 0.5).tobytes()
    send = Damaged(damage)
    made = rife.Rife(runner=rife.RemoteRunner(send, "stood in"))
    with pytest.raises(rife.RifeUnavailable, match=r"^stood in: the executor's answer does not read: ") as failed:
        made(a, b, 0.5)
    cause = failed.value.__cause__
    assert not isinstance(failed.value, rife.RifeNotInstalled) and type(cause) is error and words in str(cause)
    assert str(cause) in str(failed.value) and send.calls == 2


def test_an_answer_with_any_one_byte_damaged_is_a_rife_that_fails_or_reads_as_it_was_made():
    """Each byte of the probe's answer, a frame's and a read's damaged in turn (its bits turned over), on one RIFE's runner:
    every answer is a RIFE that fails, naming that it does not read, or — the byte one reading it does not depend on (a
    time, the CRC-32 a local header repeats) — reads as it was made, byte for byte. None raises anything else, and none
    hands on other pixels or another reading of the passes."""
    a, b = (_disc(x).crop((16, 24, 96, 72)) for x in (40, 72))
    colour, cover = rife.inputs(a, b)
    outcomes = collections.Counter()
    probe = rife.answer(_probe(), lambda: Device())
    for at in range(len(probe)):
        try:
            runner = rife.RemoteRunner(lambda request, damaged=_flipped(probe, at): damaged, "stood in")
        except rife.RifeUnavailable as exc:
            assert str(exc).startswith("stood in: the executor's answer does not read: "), (at, str(exc))
            outcomes["probe", "fails"] += 1
        else:
            assert runner.remote == _remote(T4), at
            outcomes["probe", "reads"] += 1
    for ask in ("frame", "read"):
        send = Damaged()
        runner = rife.RemoteRunner(send, "stood in")
        made = getattr(runner, ask)(colour, cover, 0.5)
        for at in range(len(send.made)):
            send.damage = lambda data, at=at: _flipped(data, at)
            try:
                got = getattr(runner, ask)(colour, cover, 0.5)
            except rife.RifeUnavailable as exc:
                assert str(exc).startswith("stood in: the executor's answer does not read: "), (ask, at, str(exc))
                outcomes[ask, "fails"] += 1
            else:
                assert all(g.dtype == m.dtype and np.array_equal(g, m) for g, m in zip(got, made, strict=True)), (ask, at)
                outcomes[ask, "reads"] += 1
    assert all(outcomes[ask, how] for ask in ("probe", "frame", "read") for how in ("fails", "reads")), outcomes


def test_a_reading_that_cannot_read_what_pack_writes_raises_as_it_is_a_fault_of_the_code(monkeypatch):
    """The reading itself failing — NumPy refusing how its array reader is called, as a NumPy whose reader took no
    `allow_pickle` would — reads no bytes, `pack`'s own neither: a fault of the code, not of an answer or a request. It
    is raised as it is, on the host as on the executor, never a RIFE that fails nor a request answered as one that does
    not read."""
    probe, request = rife.answer(_probe(), lambda: Device()), _probe()

    def refused(*args, **kwargs):
        raise TypeError("read_array() got an unexpected keyword argument 'allow_pickle'")

    monkeypatch.setattr(np.lib.format, "read_array", refused)
    with pytest.raises(TypeError, match="allow_pickle"):
        rife.RemoteRunner(lambda sent: probe, "stood in")
    with pytest.raises(TypeError, match="allow_pickle"):
        rife.answer(request, lambda: Device())


def test_an_executor_whose_model_is_not_installed_is_a_rife_that_fails_here():
    """The executor's own RIFE not installed (its model missing there) is not RIFE not installed here, which a loop would
    cut the walk around: a RIFE that fails, in the executor's words, as the RIFE is made."""

    def not_there(request: bytes) -> bytes:
        def missing():
            raise rife.RifeNotInstalled("RIFE model /root/rife-v4.6 has no flownet.param / flownet.bin")
        return rife.answer(request, missing)

    with pytest.raises(rife.RifeUnavailable, match=r"the executor failed: RIFE model /root/rife-v4.6 has no") as failed:
        rife.Rife(runner=rife.RemoteRunner(not_there, "not there"))
    assert not isinstance(failed.value, rife.RifeNotInstalled)


@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script cannot stand in for rife-ncnn-vulkan.exe")
@pytest.mark.parametrize("names, refused", [
    ({"SPRITE_GEN_RIFE_BACKEND": None}, r"an executor runs the torch backend"),
    ({"SPRITE_GEN_RIFE_BACKEND": "ncnn"}, r"an executor runs the torch backend"),
    ({"SPRITE_GEN_RIFE_DEVICE": "cpu"}, r"SPRITE_GEN_RIFE_DEVICE='cpu'.*the executor's own"),
    ({"SPRITE_GEN_RIFE_EXECUTOR": "send"}, r"SPRITE_GEN_RIFE_EXECUTOR='send': name .* as module:attr"),
    ({"SPRITE_GEN_RIFE_EXECUTOR": "no_such_module_here:send"}, r"no_such_module_here could not be imported"),
    ({"SPRITE_GEN_RIFE_EXECUTOR": f"{__name__}:nothing"}, rf"{__name__} has no function nothing"),
])
def test_an_executor_named_with_the_binary_with_a_device_or_not_there_is_refused(executor, monkeypatch, tmp_path, names, refused):
    """An executor named where the binary runs, with a device named on this host as well (the device is the executor's,
    named by its identity), or one whose send cannot be found: refused by name (`RifeUnavailable`, never RIFE not
    installed), before the executor is asked anything — and the binary beside a model is not run in its place."""
    log = _binary_beside_a_model(monkeypatch, tmp_path)
    for name, value in names.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with pytest.raises(rife.RifeUnavailable, match=refused) as refusal:
        rife.Rife()
    assert not isinstance(refusal.value, rife.RifeNotInstalled)
    assert executor.headers == [] and not log.exists()


def test_a_loop_whose_executor_answer_is_damaged_on_the_way_fails_with_its_record(executor, monkeypatch, tmp_path):
    """`video-loop` with a jump to repair and an executor named, its answer to the first frame damaged on the way (a
    compressed block given the reserved type): the loop fails as one whose RIFE fails does — its record written `failed`
    in the decompressor's words, the gait fallback's frames removed, the line saying to fix RIFE or pass --repair off —
    not with the decompressor's error raised past them."""
    from sprite_gen.video import loop
    from tests.video.test_loop_repair import _keyed, _loop, _run

    def damaged(request: bytes) -> bytes:
        answered = executor(request)
        return answered if rife.unpack(request)[0].get("probe") else _block_type_3(answered)

    monkeypatch.setattr(sys.modules[__name__], "reached", damaged)
    filmed, _ = _loop({10: 12})
    keyed = _keyed(tmp_path, filmed)
    with pytest.raises(SystemExit, match=r"fix RIFE, or pass --repair off") as failed:
        _run(tmp_path, keyed)
    words = "the executor's answer does not read: zlib.error: Error -3 while decompressing data: invalid block type"
    assert words in str(failed.value) and isinstance(failed.value.__cause__.__cause__, zlib.error)
    report = json.loads((tmp_path / "w.json").read_text())
    assert report["status"] == "failed" and words in report["error"]
    assert not list((tmp_path / "out").glob(f".{loop.FALLBACK_FRAMES}*")) and executor.kinds() == ["probe", "frame"]


# --- what names it ----------------------------------------------------------------------------------------------------

def test_a_loop_names_the_executor_that_made_its_frames(executor, tmp_path):
    """`video-loop` finding RIFE itself with an executor named: its record names the torch backend, the executor and the
    GPU that made its frames, not the binary."""
    from tests.video.test_loop_repair import _keyed, _loop, _run

    filmed, _ = _loop({10: 12})
    rep = _run(tmp_path, _keyed(tmp_path, filmed))
    assert rep["jump_repair"]["interpolator"] == {"kind": "rife-torch", "executor": NAME, "device": "cuda",
                                                  "gpu": "Tesla T4", "model": "rife-v4.6", "tta": "-x -z"}
    assert executor.kinds()[0] == "probe" and executor.kinds().count("probe") == 1


# --- the torch backend served ---------------------------------------------------------------------------------------

@pytest.mark.torch_rife
def test_the_torch_backend_served_makes_the_frame_and_the_read_this_process_makes(monkeypatch):
    """`rife_torch.serve` as the executor, called in this process, on the model the lane names (SPRITE_GEN_RIFE_MODEL,
    else the default installed), the one this host runs too: the frame and the read are the torch backend's of this
    process, byte for byte, its identity is that backend's with the protocol, and the backend is loaded once for every
    request it serves."""
    from sprite_gen.video import rife_torch

    monkeypatch.setattr(rife_torch, "_served", None)
    here = rife_torch.TorchRunner(rife.locate_model(), rife.device())
    served = rife.Rife(runner=rife.RemoteRunner(rife_torch.serve, "sprite_gen.video.rife_torch:serve"))
    assert served.model_name == here.model_name == rife.model_name()
    loaded = rife_torch._served
    a, b = _legs(40, 64), _legs(64, 52)
    assert served(a, b, 0.5).tobytes() == rife.Rife(runner=here)(a, b, 0.5).tobytes()
    frame, apart = served.read(a, b, 0.5)
    there, apart_here = rife.Rife(runner=here).read(a, b, 0.5)
    assert frame.tobytes() == there.tobytes() and np.array_equal(apart, apart_here)
    ident = json.loads(served.identity())
    assert {k: ident[k] for k in _remote(here.identity())} == _remote(here.identity())
    assert loaded is not None and rife_torch._served is loaded
