# SPDX-License-Identifier: Apache-2.0
"""Cell geometry checks for sprite_gen.slice_sheet on a synthetic sheet."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from sprite_gen.slice_sheet import parse_chroma_key, slice_sheet

MAGENTA = (255, 0, 255)
GRID = (3, 2)
SHEET_SIZE = (300, 200)  # 100x100 cells
CANVAS = (128, 192)
BASELINE = 180.0
TARGET = 120.0


def _synthetic_sheet() -> Image.Image:
    """3x2 grid of solid figures at deliberately different sizes, plus:
    - a detached in-cell effect square (cell 1) that must survive,
    - a neighbour overhang: cell 0's figure grows a thin arm crossing into
      cell 1 (fuses the two figures sheet-wide) that must NOT stay in cell 1.
    """
    sheet = Image.new("RGB", SHEET_SIZE, MAGENTA)
    px = sheet.load()

    def rect(x0: int, y0: int, x1: int, y1: int, color=(40, 60, 90)) -> None:
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[x, y] = color

    # cell 0: tall figure (feet at y=90), with an arm crossing into cell 1
    rect(30, 20, 70, 90)
    rect(70, 40, 112, 44)  # arm overhang into cell 1 (x>=100)
    # cell 1: shorter figure + detached effect square well inside the cell
    rect(130, 40, 170, 90)
    rect(175, 22, 183, 30, color=(200, 40, 40))  # effect
    # cell 2: medium figure
    rect(230, 30, 270, 90)
    # bottom row figures
    rect(30, 130, 70, 190)
    rect(130, 120, 170, 190)
    rect(230, 140, 270, 190)
    return sheet


def _subject_rows(image: Image.Image) -> tuple[int, int]:
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    assert bbox is not None
    return bbox[1], bbox[3]


def test_slice_sheet_normalizes_height_and_baseline(tmp_path: Path) -> None:
    sheet_path = tmp_path / "sheet.png"
    _synthetic_sheet().save(sheet_path)
    written = slice_sheet(
        sheet_path,
        tmp_path / "out",
        parse_chroma_key("magenta"),
        grid=GRID,
        canvas=CANVAS,
        baseline_y=BASELINE,
        target_height=TARGET,
        noise_min=10,
    )
    assert len(written) == GRID[0] * GRID[1]
    for path in written:
        image = Image.open(path)
        assert image.size == CANVAS
        top, bottom = _subject_rows(image)
        assert bottom == round(BASELINE), path.name
        if path.stem != "cell-1":  # cell 1 carries an effect above the head
            assert (bottom - top) == round(TARGET), path.name


def test_slice_sheet_drops_neighbour_overhang_keeps_effect(tmp_path: Path) -> None:
    sheet_path = tmp_path / "sheet.png"
    _synthetic_sheet().save(sheet_path)
    written = slice_sheet(
        sheet_path,
        tmp_path / "out",
        parse_chroma_key("magenta"),
        grid=GRID,
        canvas=CANVAS,
        baseline_y=BASELINE,
        target_height=TARGET,
        noise_min=10,
    )
    cell1 = Image.open(next(p for p in written if p.stem == "cell-1"))
    pixels = cell1.load()
    reds = greys = 0
    for y in range(cell1.size[1]):
        for x in range(cell1.size[0]):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            if r > 150 and g < 90:
                reds += 1
            if r < 90 and b > 60:
                greys += 1
    assert reds > 0, "detached in-cell effect must survive"
    # 오버행 팔은 셀 좌측 경계에 붙은 얇은 조각으로 들어오므로, 본체+이펙트 외의
    # 좌측 경계 접촉 픽셀이 남아 있으면 실패다: 경계 3px 안 불투명 픽셀 검사
    left_edge = sum(1 for y in range(cell1.size[1]) for x in range(0, 3) if pixels[x, y][3] > 0)
    assert left_edge == 0, "neighbour overhang must be dropped"


def test_slice_sheet_no_chroma_residue(tmp_path: Path) -> None:
    sheet_path = tmp_path / "sheet.png"
    _synthetic_sheet().save(sheet_path)
    written = slice_sheet(
        sheet_path,
        tmp_path / "out",
        parse_chroma_key("magenta"),
        grid=GRID,
        canvas=CANVAS,
        baseline_y=BASELINE,
        target_height=TARGET,
        noise_min=10,
    )
    for path in written:
        pixels = Image.open(path).load()
        image = Image.open(path)
        for y in range(image.size[1]):
            for x in range(image.size[0]):
                r, g, b, a = pixels[x, y]
                assert not (a > 60 and r > 150 and b > 150 and g < 100), f"{path.name} keeps magenta"
