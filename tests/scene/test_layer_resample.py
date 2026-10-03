# SPDX-License-Identifier: Apache-2.0
"""A scene layer is scaled without a ring at its edge.

`Renderer` brings every frame of a layer to the layer's raster size. It used LANCZOS over
premultiplied RGBA, which drew a lighter rim and a key tint around a keyed sprite that the
sprite did not have; it scales through `resize_cell` (`tests/video/test_strip_resample.py`).
The test fails with LANCZOS put back.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from keyed_figure import colour_outside, figure
from sprite_gen.scene.model import load_scene
from sprite_gen.scene.render import Renderer
from sprite_gen.util.resample import resize_cell


@pytest.mark.parametrize("scale", (0.5, 0.75, 1.25, 2.0))
def test_a_scaled_layer_carries_no_colour_its_frame_did_not_have(tmp_path: Path, scale: float) -> None:
    src = figure()
    src.save(tmp_path / "hero.png")
    (tmp_path / "scene.json").write_text(json.dumps({
        "kind": "sprite-gen-scene", "version": 1, "canvas": {"width": 400, "height": 400, "background": [16, 16, 16]},
        "fps": 10, "duration": 0.5, "assets": {"hero": "hero.png"},
        "layers": [{"id": "hero", "asset": "hero", "at": [200, 380], "scale": scale}]}))
    scene = load_scene(tmp_path / "scene.json")
    layer = scene.layers[0]
    size = tuple(layer.raster_size)
    assert size == (round(src.width * scale), round(src.height * scale))
    (image, _anchor, _shadow, _bbox), = Renderer(scene).prepared["hero"].values()
    assert colour_outside(src, image) == 0
    assert image.tobytes() == resize_cell(src, size).tobytes()
    assert colour_outside(src, src.resize(size, Image.Resampling.LANCZOS)) > 100


def test_a_layer_at_its_own_size_is_the_frame_itself(tmp_path: Path) -> None:
    src = figure()
    src.save(tmp_path / "hero.png")
    (tmp_path / "scene.json").write_text(json.dumps({
        "kind": "sprite-gen-scene", "version": 1, "canvas": {"width": 200, "height": 200}, "fps": 10, "duration": 0.5,
        "assets": {"hero": "hero.png"}, "layers": [{"id": "hero", "asset": "hero", "at": [100, 190]}]}))
    (image, _anchor, _shadow, _bbox), = Renderer(load_scene(tmp_path / "scene.json")).prepared["hero"].values()
    assert image.tobytes() == src.tobytes()
