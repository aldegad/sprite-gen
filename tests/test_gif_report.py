# SPDX-License-Identifier: Apache-2.0
"""Inspect each decoded GIF frame before the decoder advances."""
from pathlib import Path

from PIL import Image

from sprite_gen.util.gif_utils import gif_report


def test_report_preserves_individual_holds_disposal_and_alpha(tmp_path: Path):
    palette = [255, 0, 255, 20, 40, 60, 80, 100, 120] + [0] * (768 - 9)
    frames = []
    for index in (1, 2, 1):
        frame = Image.new("P", (12, 12), index)
        frame.putpalette(palette)
        frames.append(frame)
    frames[-1].putpixel((0, 0), 0)
    path = tmp_path / "varying.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=[60, 70, 90], disposal=[1, 2, 2], loop=3,
                   transparency=0, optimize=False)

    report = gif_report(path)
    assert report["frames"] == 3
    assert report["delay_ticks"] == [6, 7, 9]
    assert report["disposal"] == [1, 2, 2]
    assert report["loop"] == 3
    assert report["transparent"] is False  # The first two frames are opaque.
