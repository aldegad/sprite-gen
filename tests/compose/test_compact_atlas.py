import json
from pathlib import Path

from PIL import Image, ImageDraw

from sprite_gen.compose import compact_atlas


def _run(tmp_path: Path, *, clips: int = 2, frames: int = 2, page_size: int = 64,
         max_empty_percent: float = 100, allow_split: bool = False):
    atlas = Image.new("RGBA", (frames * 32, clips * 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(atlas)
    rows = {}
    animation_rows = {}
    for clip in range(clips):
        name = f"clip_{clip}"
        row = []
        for frame in range(frames):
            x, y = frame * 32, clip * 32
            draw.rectangle((x + 4, y + 4, x + 27, y + 27), fill=(clip * 70, frame * 70, 255, 255))
            row.append({"x": x, "y": y, "w": 32, "h": 32})
        rows[name] = row
        animation_rows[name] = {"row": clip, "frames": frames, "fps": 8, "loop": True}
    atlas.save(tmp_path / "sprite-sheet-alpha.png")
    manifest = {
        "characterId": "test",
        "sprite_sheet_alpha": "sprite-sheet-alpha.png",
        "game_input": "sprite-sheet-alpha.png",
        "animation": {"cellWidth": 32, "cellHeight": 32, "columns": frames, "rows": animation_rows},
        "frame_layout": {"sheetWidth": atlas.width, "sheetHeight": atlas.height,
                         "cellWidth": 32, "cellHeight": 32, "rows": rows},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = compact_atlas.run(
        run_dir=tmp_path, page_size=page_size, max_pages=4, gutter=1, alpha_padding=0,
        max_empty_percent=max_empty_percent,
        allow_clip_split_over_threshold=allow_split,
    )
    return result, json.loads((tmp_path / "manifest.compact.json").read_text()), \
        json.loads((tmp_path / "compact-atlas.report.json").read_text())


def test_compact_atlas_trims_without_resampling_and_preserves_each_clip_page(tmp_path: Path) -> None:
    result, manifest, report = _run(tmp_path)

    assert result == 0
    assert manifest["frame_layout"]["sheetCount"] == 1
    assert manifest["frame_layout"]["sheets"] == ["sprite-sheet-alpha-0.png"]
    assert report["clipsSpanningPages"] == {}
    frame = manifest["frame_layout"]["rows"]["clip_0"][0]
    assert (frame["w"], frame["h"], frame["sourceX"], frame["sourceY"]) == (24, 24, 4, 4)
    with Image.open(tmp_path / "sprite-sheet-alpha-0.png") as page:
        restored = page.crop((frame["x"], frame["y"], frame["x"] + frame["w"], frame["y"] + frame["h"]))
    assert restored.getpixel((0, 0)) == (0, 0, 255, 255)


def test_clip_split_is_applied_only_when_threshold_is_exceeded_and_enabled(tmp_path: Path) -> None:
    _result, manifest, report = _run(
        tmp_path, clips=2, frames=3, page_size=64, max_empty_percent=10, allow_split=True)

    assert manifest["compact_atlas"]["clipSplitApplied"] is True
    assert report["clipSplitApplied"] is True
    assert report["clipsSpanningPages"]


def test_threshold_excess_does_not_split_without_opt_in(tmp_path: Path) -> None:
    _result, manifest, report = _run(
        tmp_path, clips=2, frames=2, page_size=64, max_empty_percent=10, allow_split=False)

    assert manifest["compact_atlas"]["clipSplitApplied"] is False
    assert report["thresholdExceeded"] is True
    assert report["clipsSpanningPages"] == {}
