# SPDX-License-Identifier: Apache-2.0
"""rife-v4.25 in PyTorch, the torch backend of the default model (docs/loop-repair.md section 1): Practical-RIFE 4.25's
IFNet — an encoder run on each image and five blocks — read from the binary's own rife-v4.25 files, each image padded to
128 with zeros as the engine hands the binary it, with no augmentation.

Marked `torch_rife`: they need PyTorch and the installed model (`rife.locate_model()`); the lanes run them with none
skipped. That this network makes Practical-RIFE's own frame from the same weights — byte for byte on the CPU — was
measured against the official `train_log/IFNet_HDv3.py` outside this repository, which does not vendor it."""

from __future__ import annotations

import numpy as np
import pytest

from sprite_gen.video import rife, rife_install
from tests.video.test_rife import _marked


@pytest.fixture(scope="module")
def runner():
    from sprite_gen.video import rife_torch

    return rife_torch.TorchRunner(rife.locate_model().parent / "rife-v4.25", rife.device())


@pytest.mark.torch_rife
def test_the_network_is_padded_as_the_engine_hands_the_binary_each_image():
    """rife-v4.25's IFNet pads to `rife.MODELS`' 128 and runs five blocks at scales 16 to 1, encoding each image;
    rife-v4.6's is unchanged (32, the binary's own padding, four blocks)."""
    from sprite_gen.video import rife_torch

    new, old = rife_torch.NETS["rife-v4.25"], rife_torch.NETS["rife-v4.6"]
    assert (new.pad, new.scales, len(new.blocks), new.head) == (rife.MODELS["rife-v4.25"].pad, (16, 8, 4, 2, 1), 5, True)
    assert (old.pad, old.scales, old.blocks, old.head) == (32, (8, 4, 2, 1), ((7, 192), (12, 128), (12, 96), (12, 64)), False)


@pytest.mark.torch_rife
def test_the_model_is_read_layer_by_layer_in_its_own_order_and_loads_whole():
    """The installed rife-v4.25's layers with weights, read without ncnn, are the network's parameters in the order
    `rife_torch.layers` names them, each of the type named there: the encoder twice (once for each image), then each
    block's betas held apart before their convolutions (not folded in, as rife-v4.6's are), its strided convolutions and
    its transposed one. They fill every parameter of the network and no other; the betas are the model's, not ones."""
    import torch

    from sprite_gen.video import rife_torch

    files = rife.model_bytes(rife.locate_model().parent / "rife-v4.25")
    found = rife_torch._ncnn_layers(files["flownet.param"], files["flownet.bin"])
    assert [kind for kind, _, _ in found] == [kind for _, kind in rife_torch.layers("rife-v4.25")]
    state = rife_torch.state_from_ncnn(files, "rife-v4.25")
    assert set(state) == set(rife_torch.IFNet("rife-v4.25").state_dict())
    betas = [v for k, v in state.items() if k.endswith(".beta")]
    assert len(betas) == 5 * 8 and not all(torch.equal(b, torch.ones_like(b)) for b in betas)


@pytest.mark.torch_rife
def test_a_model_whose_encoder_copies_differ_or_whose_layers_are_out_of_order_is_refused(monkeypatch):
    """The encoder the model holds twice must be the same both times, and each layer of the type the network has there:
    a second copy changed, or two layers swapped, is refused by name as the state is filled (`rife.RifeUnavailable` from
    the runner), not loaded."""
    from sprite_gen.video import rife_torch

    files = rife.model_bytes(rife.locate_model().parent / "rife-v4.25")
    real = rife_torch._ncnn_layers(files["flownet.param"], files["flownet.bin"])
    changed = list(real)
    kind, w, b = changed[4]  # the second copy's first convolution
    changed[4] = (kind, w + 1e-3, b)
    monkeypatch.setattr(rife_torch, "_ncnn_layers", lambda *_: changed)
    with pytest.raises(ValueError, match=r"encode.cnn0: the model's two copies differ"):
        rife_torch.state_from_ncnn(files, "rife-v4.25")
    with pytest.raises(rife.RifeUnavailable, match=r"does not load: encode.cnn0: the model's two copies differ"):
        rife_torch.TorchRunner(rife.locate_model().parent / "rife-v4.25", "cpu")
    swapped = list(real)
    swapped[8], swapped[9] = swapped[9], swapped[8]  # block0's first beta and its first convolution
    monkeypatch.setattr(rife_torch, "_ncnn_layers", lambda *_: swapped)
    with pytest.raises(ValueError, match=r"block0.convblock.0.beta: the model holds a Convolution where the network has a "
                                         r"MemoryData"):
        rife_torch.state_from_ncnn(files, "rife-v4.25")


@pytest.mark.torch_rife
def test_rife_v4_25_pads_to_128_runs_with_no_augmentation_and_names_its_model(runner):
    """Each image goes in padded with zeros to a multiple of 128; an augmentation asked for is refused, not run; the runner
    names its model, in its identity (a `Cache` key, and what an executor answers as) and for a report."""
    import torch

    padded = runner._pad(np.full((97, 130, 3), 255, np.uint8))
    assert tuple(padded.shape) == (1, 3, 128, 256)
    assert torch.all(padded[..., :97, :130] == 1) and not padded[..., 97:, :].any() and not padded[..., 130:].any()
    colour = [np.asarray(_marked(x))[..., :3].copy() for x in (40, 72)]
    for modes in (("-x",), ("-z",), ("-x", "-z")):
        with pytest.raises(rife.RifeUnavailable, match=r"rife-v4.25 runs with no augmentation"):
            runner.run(colour[0], colour[1], 0.5, modes)
    made = runner.run(colour[0], colour[1], 0.5, None)
    assert made.shape == colour[0].shape and made.dtype == np.uint8
    assert np.array_equal(made, runner.run(colour[0], colour[1], 0.5, ()))
    assert runner.identity()["model"] == "rife-v4.25" and runner.describe()["model"] == "rife-v4.25"


@pytest.mark.torch_rife
def test_the_check_frame_through_rife_v4_25_lands_half_way(runner):
    """`sprite-gen rife install --backend torch`'s check frame from rife-v4.25: the disc lands half way (x 48)."""
    mid = rife.Rife(runner=runner)(rife_install._disc(30), rife_install._disc(66), 0.5)
    xs = np.nonzero(np.asarray(mid)[..., 3] > 127)[1]
    assert abs(xs.mean() - 48) <= 4
