# SPDX-License-Identifier: Apache-2.0
"""One sheet for the directions of a set (docs/video-pipeline.md section 7): rows in the compass order
S SW W NW N NE E SE whatever order the loops are given in, one cell that holds every pixel of every
cell (its pivot the bottom centre), one ground line under every cell — the lowest body pixel (alpha 8
and over) of any cell of any direction —, every row at the size it was cut (nothing scaled, nothing
lost, a fainter pixel kept as no body), and the centre picture stood on that line at the rows' standing
height. Loops of different lengths are laid
all the same and said to differ (`same_length` false).

The loops are synthetic strips written the way `video-loop` writes one (`<name>.strip.png` beside
`<name>.strip.json`): a body that lifts its foot once a cycle, each direction its own colour and size."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.video import set_sheet

DELAY = 41.67
COLOURS = {"S": (200, 40, 40), "SW": (200, 120, 40), "W": (200, 200, 40), "NW": (40, 200, 40),
           "N": (40, 200, 200), "NE": (40, 40, 200), "E": (120, 40, 200), "SE": (200, 40, 200)}
VIEWS = {"S": "front", "SW": "front_diagonal@left", "W": "side@left", "NW": "back_diagonal@left", "N": "back",
         "NE": "back_diagonal@right", "E": "side@right", "SE": "front_diagonal@right"}


def _walker(size: tuple[int, int], colour: tuple[int, int, int], k: int, n: int, *, ground: int, height: int,
            half: int = 12, lift: int = 6, dx: int = 0) -> Image.Image:
    """A body standing on row `ground` (exclusive) of a w x h frame, its foot lifted by up to `lift`
    (down on the ground half a cycle in), with a faint rim column (alpha 40) on its left a lost
    pixel would show on."""
    w, h = size
    a = np.zeros((h, w, 4), dtype=np.uint8)
    cx = w // 2 + dx
    up = round(lift * (1 + math.cos(2 * math.pi * k / n)) / 2)
    a[ground - height:ground - up, cx - half:cx + half] = (*colour, 255)
    a[ground - height:ground - up, cx - half - 1] = (*colour, 40)
    return Image.fromarray(a, "RGBA")


def _loop(root: Path, folder: str, frames: list[Image.Image], *, delay_ms: float = DELAY, body_h: int | None = None,
          anchor: list[float] | None = None, name: str = "walk") -> Path:
    d = root / folder
    d.mkdir(parents=True)
    w, h = frames[0].size
    strip = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for k, f in enumerate(frames):
        strip.paste(f, (k * w, 0))
    strip.save(d / f"{name}.strip.png")
    meta = {"frames": len(frames), "w": w, "h": h, "kind": "fixed", "loop": True, "delay_ms": delay_ms,
            "cycle_frames": len(frames), "cycle_seconds": round(len(frames) * delay_ms / 1000, 4), "state": "walk"}
    if body_h is not None:
        meta["body_h"] = body_h
    if anchor is not None:
        meta["anchor"] = anchor
    (d / f"{name}.strip.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return d


def _direction(root: Path, code: str, *, size=(60, 100), n: int = 12, height: int = 80, float_px: int = 0, **kw) -> Path:
    w, h = size
    frames = [_walker(size, COLOURS[code], k, n, ground=h - float_px, height=height, **kw) for k in range(n)]
    return _loop(root, code, frames, body_h=height)


def _cells(sheet: Image.Image, meta: dict) -> dict[tuple[int, int], Image.Image]:
    cw, ch = meta["cell_w"], meta["cell_h"]
    return {(r, k): sheet.crop((k * cw, r * ch, (k + 1) * cw, (r + 1) * ch))
            for r in range(len(meta["rows"])) for k in range(meta["columns"])}


def _strip_cells(loop_dir: Path) -> list[Image.Image]:
    meta = json.loads(next(loop_dir.glob("*.strip.json")).read_text(encoding="utf-8"))
    strip = Image.open(next(loop_dir.glob("*.strip.png"))).convert("RGBA")
    return [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]


def _box(im: Image.Image):
    return im.getchannel("A").getbbox()


def _body(im: Image.Image):
    return im.getchannel("A").point(lambda v: 255 if v >= 8 else 0).getbbox()


def _make(tmp_path: Path, dirs: list[Path], views: list[str], **kw) -> tuple[dict, Image.Image]:
    out = tmp_path / "out"
    meta = set_sheet.make_sheet(dirs, views, out_dir=out, name="walk", **kw)
    assert meta == json.loads((out / "walk.sheet.json").read_text(encoding="utf-8"))
    return meta, Image.open(out / "walk.sheet.png").convert("RGBA")


def test_rows_take_the_compass_order_whatever_order_the_loops_are_given_in(tmp_path):
    given = ["E", "N", "SE", "S", "W", "NE"]
    dirs = [_direction(tmp_path, c) for c in given]
    meta, sheet = _make(tmp_path, dirs, [VIEWS[c] for c in given])
    assert meta["order"] == ["S", "W", "N", "NE", "E", "SE"]
    assert [r["code"] for r in meta["rows"]] == meta["order"]
    assert [r["view"] for r in meta["rows"]] == [VIEWS[c] for c in meta["order"]]
    assert [r["input"] for r in meta["rows"]] == [str((tmp_path / c).resolve()) for c in meta["order"]]
    assert all(r["name"] == "walk" and r["frames"] == 12 and r["delay_ms"] == DELAY for r in meta["rows"])
    for (r, _k), cell in _cells(sheet, meta).items():
        solid = np.asarray(cell)[..., 3] == 255
        assert {tuple(c) for c in np.asarray(cell)[..., :3][solid]} == {COLOURS[meta["order"][r]]}


def test_every_row_is_laid_as_cut_on_one_ground_line(tmp_path):
    """Rows of different sizes are laid at the size they were cut, pixel for pixel; the ground line is the
    lowest body pixel of any cell, and a direction whose strip leaves room under its feet stands that much
    above it, as cut."""
    dirs = [_direction(tmp_path, "S", size=(61, 100), height=80),
            _direction(tmp_path, "E", size=(90, 120), height=96, half=30),
            _direction(tmp_path, "N", size=(70, 104), height=70, float_px=5)]
    meta, sheet = _make(tmp_path, dirs, ["front", "side@right", "back"])
    cells = _cells(sheet, meta)
    base = meta["baseline_y"]
    assert base == meta["cell_h"] and meta["anchor"] == [meta["cell_w"] // 2, base] and meta["cell_w"] % 2 == 0
    lowest = max(_body(c)[3] for c in cells.values())
    assert lowest == base  # the lowest body pixel of any cell is row baseline_y - 1
    by_code = {r["code"]: r for r in meta["rows"]}
    assert by_code["S"]["body_bottom_y"] == base and by_code["E"]["body_bottom_y"] == base
    assert by_code["N"]["body_bottom_y"] == base - 5
    for r, row in enumerate(meta["rows"]):
        source = _strip_cells(tmp_path / row["code"])
        w, h = source[0].size
        for k, src in enumerate(source):
            sb, cb = _box(src), _box(cells[(r, k)])
            # as cut: the same pixels, nothing scaled, nothing dropped
            assert cells[(r, k)].crop(cb).tobytes() == src.crop(sb).tobytes()
            # its pivot (the strip's bottom centre) on the cell's: the same offset for every cell of the row
            assert (cb[0] - sb[0], cb[1] - sb[1]) == (meta["cell_w"] // 2 - math.ceil(w / 2), base - h)
    total = sum(int(np.asarray(c, dtype=np.int64)[..., 3].sum()) for c in cells.values())
    assert total == sum(int(np.asarray(s, dtype=np.int64)[..., 3].sum()) for d in dirs for s in _strip_cells(d))


def test_a_faint_pixel_is_kept_in_the_cell_and_is_no_body(tmp_path):
    """A soft shadow under the feet (alpha under 8) is not where the body stands: the ground line is the
    body's, and the cell reaches below it to keep the shadow, every pixel of the strip on the sheet."""
    frames = []
    for k in range(12):
        f = _walker((60, 100), COLOURS["S"], k, 12, ground=96, height=80)
        a = np.asarray(f).copy()
        a[96:100, 10:50] = (0, 0, 0, 4)
        frames.append(Image.fromarray(a, "RGBA"))
    d = _loop(tmp_path, "S", frames, body_h=80)
    meta, sheet = _make(tmp_path, [d], ["front"])
    cells = _cells(sheet, meta)
    assert meta["cell_h"] == meta["baseline_y"] + 4 and meta["rows"][0]["body_bottom_y"] == meta["baseline_y"]
    assert max(_body(c)[3] for c in cells.values()) == meta["baseline_y"]
    assert max(_box(c)[3] for c in cells.values()) == meta["cell_h"]
    assert sum(int(np.asarray(c, dtype=np.int64)[..., 3].sum()) for c in cells.values()) == \
        sum(int(np.asarray(f, dtype=np.int64)[..., 3].sum()) for f in frames)


def test_the_cell_is_the_union_of_every_body_box_about_the_pivot(tmp_path):
    """One cell size holds every cell's body box, centred on the pivot so every cell's pivot is its bottom
    centre; the cell is no larger: the widest side and the highest top touch its edges."""
    dirs = [_direction(tmp_path, "S", size=(60, 100), height=60),
            _direction(tmp_path, "E", size=(120, 110), height=100, half=10, dx=25)]
    meta, sheet = _make(tmp_path, dirs, ["front", "side@right"])
    cells = _cells(sheet, meta)
    boxes = [_box(c) for c in cells.values()]
    assert min(b[1] for b in boxes) == 0  # the highest top touches the cell's top
    assert max(b[2] for b in boxes) == meta["cell_w"]  # E's body, right of the pivot, touches the right edge
    assert min(b[0] for b in boxes) > 0  # and the cell is centred on the pivot, not on the bodies
    centre = meta["cell_w"] // 2
    s_box = _box(cells[(0, 0)])
    assert abs((s_box[0] + s_box[2]) / 2 - centre) <= 1


def test_a_declared_pivot_is_the_one_laid_on_the_cell_centre(tmp_path):
    """A strip whose sidecar declares its pivot (`anchor`, as `video-loop --anchor feet` writes the foot
    line) stands on it; another stands on its bottom centre."""
    plain = _direction(tmp_path, "S", size=(60, 100))
    frames = [_walker((80, 100), COLOURS["E"], k, 12, ground=100, height=80, dx=-20) for k in range(12)]
    footed = _loop(tmp_path, "E", frames, body_h=80, anchor=[20, 100])
    meta, sheet = _make(tmp_path, [plain, footed], ["front", "side@right"])
    cells = _cells(sheet, meta)
    e_row = meta["order"].index("E")
    for k, src in enumerate(_strip_cells(footed)):
        sb, cb = _box(src), _box(cells[(e_row, k)])
        assert cb[0] - sb[0] == meta["cell_w"] // 2 - 20  # strip x 20 on the cell centre


def test_same_geometry_as_a_sheet_laid_by_hand(tmp_path):
    """A hand-laid sheet: every strip centred in one wide cell and stood on its bottom, the common ground
    line the lowest body pixel of any cell. The engine's cells are that layout cropped to the bodies, one
    offset for every cell; its ground line is the hand one, moved by that offset."""
    sizes = {"S": (61, 100), "SW": (64, 100), "W": (66, 100), "NW": (63, 100), "N": (60, 100), "NE": (67, 100),
             "E": (72, 100), "SE": (65, 100)}
    dirs = [_direction(tmp_path, c, size=sizes[c], height=70 + i * 3, half=10 + i) for i, c in enumerate(set_sheet.ORDER)]
    meta, sheet = _make(tmp_path, dirs, [VIEWS[c] for c in set_sheet.ORDER])
    cw_hand, ch_hand = 120, 110
    hand = {}
    for r, c in enumerate(set_sheet.ORDER):
        for k, src in enumerate(_strip_cells(tmp_path / c)):
            cell = Image.new("RGBA", (cw_hand, ch_hand), (0, 0, 0, 0))
            cell.paste(src, ((cw_hand - src.width) // 2, ch_hand - src.height))
            hand[(r, k)] = cell
    foot = max(_box(c)[3] for c in hand.values())
    offsets = set()
    for key, cell in _cells(sheet, meta).items():
        hb, cb = _box(hand[key]), _box(cell)
        dx, dy = hb[0] - cb[0], hb[1] - cb[1]
        offsets.add((dx, dy))
        assert cell.tobytes() == hand[key].crop((dx, dy, dx + meta["cell_w"], dy + meta["cell_h"])).tobytes()
    assert len(offsets) == 1
    (dx, dy), = offsets
    assert meta["baseline_y"] == foot - dy


def test_lengths_that_differ_are_laid_all_the_same_and_said_to_differ(tmp_path, capsys):
    s = _direction(tmp_path, "S", n=12)
    e = _direction(tmp_path, "E", n=10)
    out = tmp_path / "out"
    rc = cli.main(["video-set-sheet", "--loop-dir", str(e), "--view", "side@right", "--loop-dir", str(s), "--view", "front",
                   "--out-dir", str(out), "--name", "walk"])
    assert rc == 0
    meta = json.loads((out / "walk.sheet.json").read_text(encoding="utf-8"))
    assert meta["same_length"] is False and meta["columns"] == 12
    assert [r["frames"] for r in meta["rows"]] == [12, 10]
    sheet = Image.open(out / "walk.sheet.png").convert("RGBA")
    assert sheet.size == (12 * meta["cell_w"], 2 * meta["cell_h"])
    cells = _cells(sheet, meta)
    assert _box(cells[(1, 9)]) is not None and _box(cells[(1, 10)]) is None and _box(cells[(1, 11)]) is None
    err = capsys.readouterr().err
    assert "warning" in err and "S 12 frames" in err and "E 10 frames" in err and "same_length false" in err
    # the same number of frames shown at another rate is not the same length either
    other = tmp_path / "rate"
    other.mkdir()
    a = _loop(other, "S", [_walker((60, 100), COLOURS["S"], k, 12, ground=100, height=80) for k in range(12)], body_h=80)
    b = _loop(other, "E", [_walker((60, 100), COLOURS["E"], k, 12, ground=100, height=80) for k in range(12)], body_h=80, delay_ms=50.0)
    assert set_sheet.make_sheet([a, b], ["front", "side@right"], out_dir=other / "out")["same_length"] is False
    same = set_sheet.make_sheet([s, _direction(tmp_path / "x", "N", n=12)], ["front", "back"], out_dir=tmp_path / "same")
    assert same["same_length"] is True


def test_the_centre_picture_stands_on_the_ground_line_at_the_rows_height(tmp_path):
    """A transparent still, drawn twice the rows' size and wider than any of them, with a faint shadow under
    its feet: scaled to the rows' standing height (the median of their `body_h`), its body stood on the
    ground line and centred on the pivot, the shadow kept below the line, and the cell made to hold it."""
    dirs = [_direction(tmp_path, "S", height=78), _direction(tmp_path, "E", height=80), _direction(tmp_path, "N", height=84)]
    a = np.zeros((300, 260, 4), dtype=np.uint8)
    a[40:200, 50:210] = (250, 250, 250, 255)  # 160 tall, 160 wide: at half size 80 x 80, wider than any row
    a[200:220, 50:210] = (0, 0, 0, 4)  # a shadow too faint to count as body (alpha under 8)
    still = tmp_path / "still.png"
    Image.fromarray(a, "RGBA").save(still)
    meta, sheet = _make(tmp_path, dirs, ["front", "side@right", "back"], center=still)
    centre = meta["center"]
    assert centre["png"] == "walk.center.png" and centre["input"] == str(still.resolve())
    assert centre["body_h"] == 80 and centre["standing_h"] == 160 and centre["scale"] == 0.5
    cell = Image.open(tmp_path / "out" / "walk.center.png").convert("RGBA")
    assert cell.size == (meta["cell_w"], meta["cell_h"])
    solid = _body(cell)
    assert abs((solid[3] - solid[1]) - 80) <= 1  # the rows' standing height
    assert solid[3] == meta["baseline_y"]  # its body on the ground line
    assert abs((solid[0] + solid[2]) / 2 - meta["cell_w"] / 2) <= 0.5  # centred on the pivot
    box = _box(cell)
    assert list(box) == centre["box"]
    assert box[3] > meta["baseline_y"] and box[3] == meta["cell_h"]  # the shadow kept, below the line
    assert box[2] - box[0] >= 80 and meta["cell_w"] >= box[2] - box[0]
    rows_widest = max(_box(c)[2] - _box(c)[0] for c in _cells(sheet, meta).values() if _box(c))
    assert meta["cell_w"] >= max(rows_widest, box[2] - box[0])


def test_refusals_name_what_is_wrong_and_write_nothing(tmp_path):
    s, e = _direction(tmp_path, "S"), _direction(tmp_path, "E")
    w = _direction(tmp_path / "again", "E")
    out = tmp_path / "out"
    cases = [
        (([s, e, w], ["front", "side@right", "side@right"]), {}, r"side@right .*twice"),
        (([s, e], ["front"]), {}, r"1 --view for 2 --loop-dir"),
        (([s, e], ["front@left", "side@right"]), {}, r"front@left.*not a compass direction"),
        (([s, e], ["front", "side"]), {}, r"turned right or left"),
        (([s, tmp_path], ["front", "side@right"]), {}, r"expected one <name>\.strip\.json"),
    ]
    for (dirs, views), kw, message in cases:
        with pytest.raises(SystemExit, match=message):
            set_sheet.make_sheet(dirs, views, out_dir=out, **kw)
        assert not out.exists()
    opaque = tmp_path / "opaque.png"
    Image.new("RGBA", (40, 60), (255, 255, 255, 255)).save(opaque)
    with pytest.raises(SystemExit, match=r"no transparent background"):
        set_sheet.make_sheet([s, e], ["front", "side@right"], out_dir=out, center=opaque)
    no_height = _loop(tmp_path, "N", [_walker((60, 100), COLOURS["N"], k, 12, ground=100, height=80) for k in range(12)])
    clear = tmp_path / "clear.png"
    Image.fromarray(np.pad(np.full((20, 10, 4), 255, dtype=np.uint8), ((5, 5), (5, 5), (0, 0))), "RGBA").save(clear)
    with pytest.raises(SystemExit, match=r"no body_h"):
        set_sheet.make_sheet([s, no_height], ["front", "back"], out_dir=out, center=clear)
    (e / "walk.strip.png").unlink()
    with pytest.raises(SystemExit, match=r"no walk\.strip\.png"):
        set_sheet.make_sheet([s, e], ["front", "side@right"], out_dir=out)
    assert not out.exists()


def test_cli_prints_the_sheet_it_wrote(tmp_path, capsys):
    s, w = _direction(tmp_path, "S"), _direction(tmp_path, "W")
    out = tmp_path / "out"
    assert cli.main(["video-set-sheet", "--loop-dir", str(w), "--view", "side@left", "--loop-dir", str(s), "--view", "front",
                     "--out-dir", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["sheet"] == str(out / "set.sheet.png") and printed["order"] == ["S", "W"] and printed["same_length"] is True
    meta = json.loads((out / "set.sheet.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "sprite-gen-video-set-sheet" and meta["sheet"] == "set.sheet.png" and meta["center"] is None
    assert {k: printed[k] for k in ("cell_w", "cell_h", "baseline_y")} == {k: meta[k] for k in ("cell_w", "cell_h", "baseline_y")}
    assert "video-set-sheet" in cli.command_domains()["video"]
