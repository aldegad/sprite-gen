# SPDX-License-Identifier: Apache-2.0
"""`slice-sheet --scale sheet` keeps the sizes a sheet was drawn at, `--fit contain` keeps every cut whole
on its canvas, and the report states the factor, each cell's size and where its subject stands
(docs/sheet-slicing.md "Props and icons"). Synthetic sheets only: solid boxes of known sizes."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.frames import slice_sheet as slice_module
from sprite_gen.frames.slice_sheet import slice_sheet

MAGENTA = (255, 0, 255)
INK = (40, 60, 90)
PROP = (60, 90, 140, 250)


def chroma_sheet(path: Path, boxes, cell: int = 100) -> Path:
    """One row of square cells on magenta, a solid ``(w, h)`` box centred in each, standing 6 px above the cell bottom."""
    sheet = np.zeros((cell, cell * len(boxes), 3), dtype=np.uint8)
    sheet[...] = MAGENTA
    for index, (w, h) in enumerate(boxes):
        x0, bottom = index * cell + (cell - w) // 2, cell - 6
        sheet[bottom - h:bottom, x0:x0 + w] = INK
    Image.fromarray(sheet).save(path)
    return path


def transparent_sheet(path: Path, boxes, cell: int = 100) -> Path:
    """The same boxes on a sheet that is already transparent."""
    pixels = np.zeros((cell, cell * len(boxes), 4), dtype=np.uint8)
    for index, (w, h) in enumerate(boxes):
        x0, bottom = index * cell + (cell - w) // 2, cell - 10
        pixels[bottom - h:bottom, x0:x0 + w] = PROP
    Image.fromarray(pixels).save(path)
    return path


def opaque_box(image) -> tuple[int, int, int, int]:
    """``(x0, y0, x1, y1)``, right and bottom exclusive, of the pixels at least half opaque."""
    if isinstance(image, Path):
        with Image.open(image) as opened:
            image = opened.convert("RGBA")
    solid = np.asarray(image.convert("RGBA"))[..., 3] >= 128
    rows, cols = np.flatnonzero(solid.any(axis=1)), np.flatnonzero(solid.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def report(out: Path, prefix: str = "") -> dict:
    return json.loads((out / f"{prefix}slice-sheet.json").read_text())


def test_sheet_scale_keeps_the_drawn_heights_relative_and_the_default_still_evens_them(tmp_path):
    boxes = [(30, 80), (30, 40), (24, 20)]
    sheet = chroma_sheet(tmp_path / "props.png", boxes)
    common = dict(grid=(3, 1), canvas=(128, 192), baseline_y=180.0, target_height=120.0)
    kept = slice_sheet(sheet, tmp_path / "sheet", MAGENTA, scale="sheet", **common)
    data = report(tmp_path / "sheet")
    assert data["scale"] == "sheet" and data["fit"] == "none"
    assert data["factor"] == pytest.approx(120 / 80)
    assert data["factor_from"] == {"rule": "target_height", "cell": "cell-0"}
    for path, (w, h), cell in zip(kept, boxes, data["cells"]):
        x0, y0, x1, y1 = opaque_box(path)
        assert abs((y1 - y0) - h * 1.5) <= 1, path.name
        assert abs((x1 - x0) - w * 1.5) <= 1, path.name
        assert abs(y1 - 180) <= 1, path.name
        assert cell["factor"] == pytest.approx(1.5)
        assert cell["subject"] == {"sheet": {"w": w, "h": h}, "canvas": {"w": round(w * 1.5), "h": round(h * 1.5)}}
        assert cell["clipped_px"] == 0
    # the default is unchanged: every cell's main subject reaches the target height on its own factor
    evened = slice_sheet(sheet, tmp_path / "cell", MAGENTA, **common)
    data = report(tmp_path / "cell")
    assert data["scale"] == "cell" and data["fit"] == "none"
    assert data["factor"] is None and data["factor_from"] is None
    for path, (w, h), cell in zip(evened, boxes, data["cells"]):
        x0, y0, x1, y1 = opaque_box(path)
        assert (y1 - y0) == 120, path.name
        assert cell["factor"] == pytest.approx(120 / h) and cell["factor_from"] == "target_height"
    explicit = slice_sheet(sheet, tmp_path / "explicit", MAGENTA, scale="cell", fit="none", **common)
    assert [p.read_bytes() for p in explicit] == [p.read_bytes() for p in evened]


def test_contain_keeps_a_two_to_one_subject_whole_on_a_square_canvas(tmp_path):
    boxes = [(80, 40), (30, 60)]
    sheet = chroma_sheet(tmp_path / "icons.png", boxes)
    common = dict(grid=(2, 1), canvas=(96, 96), baseline_y=92.0, target_height=80.0)
    slice_sheet(sheet, tmp_path / "none", MAGENTA, **common)
    cut = report(tmp_path / "none")["cells"][0]
    # the default scales the wide subject to 160 x 80 on a 96 px canvas: it still cuts it, and now says so
    assert cut["clipped_px"] > 0 and cut["box"]["w"] > 96
    whole = slice_sheet(sheet, tmp_path / "contain", MAGENTA, fit="contain", **common)
    data = report(tmp_path / "contain")
    assert data["fit"] == "contain"
    wide, tall = data["cells"]
    assert wide["clipped_px"] == tall["clipped_px"] == 0
    assert wide["factor_from"] == "fit" and tall["factor_from"] == "target_height"
    factor = wide["factor"]
    assert factor < 80 / 40
    x0, y0, x1, y1 = opaque_box(whole[0])
    assert abs((x1 - x0) - 80 * factor) <= 1 and abs((y1 - y0) - 40 * factor) <= 1
    assert x0 >= 0 and x1 <= 96 and abs(y1 - 92) <= 1
    box = wide["box"]
    assert box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["w"] <= 96 and box["y"] + box["h"] <= 96
    # a subject that fits keeps the height rule
    x0, y0, x1, y1 = opaque_box(whole[1])
    assert (y1 - y0) == 80 and tall["factor"] == pytest.approx(80 / 60)


def test_sheet_scale_with_contain_takes_the_one_factor_every_cut_fits(tmp_path):
    boxes = [(30, 80), (30, 20), (90, 30)]
    sheet = chroma_sheet(tmp_path / "props.png", boxes)
    written = slice_sheet(sheet, tmp_path / "out", MAGENTA, grid=(3, 1), canvas=(128, 128), baseline_y=124.0,
                          target_height=120.0, scale="sheet", fit="contain")
    data = report(tmp_path / "out")
    factor = data["factor"]
    assert factor < 120 / 80
    assert data["factor_from"] == {"rule": "fit", "cell": "cell-2"}
    for path, (w, h), cell in zip(written, boxes, data["cells"]):
        assert cell["factor"] == factor and cell["clipped_px"] == 0
        x0, y0, x1, y1 = opaque_box(path)
        assert abs((y1 - y0) - h * factor) <= 1, path.name
        assert abs((x1 - x0) - w * factor) <= 1, path.name
        assert x0 >= 0 and x1 <= 128 and abs(y1 - 124) <= 1, path.name
        anchor_x, anchor_y = cell["anchor"]
        assert abs(anchor_y - y1) <= 1 and abs(anchor_x - (x0 + x1) / 2) <= 1, path.name


def test_cli_and_the_script_take_scale_and_fit(tmp_path):
    sheet = chroma_sheet(tmp_path / "props.png", [(30, 80), (30, 20), (90, 30)])
    flags = ["--sheet", str(sheet), "--chroma-key", "magenta", "--grid", "3x1", "--prefix", "prop_",
             "--cell-width", "128", "--cell-height", "128", "--baseline-y", "124", "--target-height", "120",
             "--scale", "sheet", "--fit", "contain"]
    assert cli.main(["slice-sheet", "--out-dir", str(tmp_path / "cli"), *flags]) == 0
    assert slice_module.main(["--out-dir", str(tmp_path / "script"), *flags]) == 0
    for out in (tmp_path / "cli", tmp_path / "script"):
        data = report(out, "prop_")
        assert data["scale"] == "sheet" and data["fit"] == "contain"
        assert [cell["file"] for cell in data["cells"]] == ["prop_cell-0.png", "prop_cell-1.png", "prop_cell-2.png"]
    assert report(tmp_path / "cli", "prop_") == report(tmp_path / "script", "prop_")


@pytest.mark.parametrize("change, message", [
    ({"scale": "tallest"}, "scale"),
    ({"fit": "cover"}, "fit"),
    ({"fit": "contain", "baseline_y": 130.0, "target_height": 100.0}, "baseline"),
])
def test_bad_settings_are_refused(tmp_path, change, message):
    sheet = chroma_sheet(tmp_path / "props.png", [(30, 80), (30, 20)])
    settings = dict(grid=(2, 1), canvas=(128, 128), baseline_y=124.0, target_height=120.0)
    settings.update(change)
    with pytest.raises(ValueError, match=message):
        slice_sheet(sheet, tmp_path / "out", MAGENTA, **settings)


def test_contain_refuses_a_part_below_the_base_with_no_room_under_the_baseline(tmp_path):
    sheet = np.zeros((100, 100, 3), dtype=np.uint8)
    sheet[...] = MAGENTA
    sheet[20:70, 35:65] = INK   # the main subject
    sheet[80:90, 45:55] = INK   # a detached part below its base
    Image.fromarray(sheet).save(tmp_path / "sheet.png")
    common = dict(grid=(1, 1), canvas=(96, 96), target_height=60.0, noise_min=10)
    with pytest.raises(ValueError, match="cell-0"):
        slice_sheet(tmp_path / "sheet.png", tmp_path / "floor", MAGENTA, baseline_y=96.0, fit="contain", **common)
    written = slice_sheet(tmp_path / "sheet.png", tmp_path / "room", MAGENTA, baseline_y=80.0, fit="contain", **common)
    cell = report(tmp_path / "room")["cells"][0]
    assert cell["clipped_px"] == 0 and cell["parts"] == 2
    assert abs(cell["anchor"][1] - 80) <= 1
    _x0, _y0, _x1, y1 = opaque_box(written[0])
    assert 80 < y1 <= 96
