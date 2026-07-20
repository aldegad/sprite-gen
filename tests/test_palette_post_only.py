# SPDX-License-Identifier: Apache-2.0
"""팔레트 = 절단 후 후처리 전용 불변식 (수홍 확정 2026-07-20, plan
sprite-gen/per-frame-pixel-grid).

팔레트(락 포함)는 격자 검출·절단에 어떤 영향도 주면 안 된다. 같은 raw 를
전혀 다른 팔레트로 두 번 추출하면 절단선(input_grids)·프레임 콘텐츠
bbox·논리 크기는 완전히 동일하고 색만 달라져야 한다.
"""

import json
import random
from pathlib import Path

from PIL import Image

import sprite_gen.extract as extract_module

MAGENTA = (255, 0, 255)


def _logical_art(width, height, seed):
    rng = random.Random(seed)
    art = Image.new("RGB", (width, height), MAGENTA)
    for y in range(height):
        for x in range(width):
            if rng.random() < 0.55:
                art.putpixel((x, y), (rng.randrange(30, 220), rng.randrange(30, 220), rng.randrange(30, 220)))
    return art


def _strip(frames, seed, pitch=8, logical=(20, 36), gap=40):
    frame = _logical_art(*logical, seed=seed).resize(
        (logical[0] * pitch, logical[1] * pitch), Image.Resampling.NEAREST)
    strip = Image.new("RGB", (frame.width * frames + gap * (frames + 1), frame.height + gap * 2), MAGENTA)
    for i in range(frames):
        strip.paste(frame, (gap + i * (frame.width + gap), gap))
    return strip


def _build_run(root: Path) -> Path:
    run_dir = root / "run"
    (run_dir / "raw").mkdir(parents=True)
    _strip(2, seed=7).save(run_dir / "raw" / "walk.png")
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "palettebot", "description": "palette invariant fixture", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8,
                 "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255], "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "palette fixture"}},
        "fit": {"pixel_perfect": True, "logical_height": 48},
    }
    (run_dir / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_dir


def _manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))


def _grid_and_geometry(run_dir: Path) -> tuple:
    row = _manifest(run_dir)["rows"][0]
    geometry = []
    for rel in row["files"]:
        with Image.open(run_dir / rel) as im:
            alpha = im.getchannel("A")
            geometry.append((im.size, alpha.getbbox(), alpha.tobytes()))
    return row.get("input_grids"), geometry


def test_palette_never_touches_the_grid(tmp_path: Path) -> None:
    # run A: 자동 팔레트 산출. run B: 추출 전에 괴상한 2색 팔레트를 핀으로 강제.
    run_a = _build_run(tmp_path / "a")
    run_b = _build_run(tmp_path / "b")
    extract_module.write_pinned_palette(run_b, [(255, 0, 0), (0, 0, 255)], "test-pin")

    assert extract_module.run(run_dir=run_a) == 0
    assert extract_module.run(run_dir=run_b) == 0

    grids_a, geometry_a = _grid_and_geometry(run_a)
    grids_b, geometry_b = _grid_and_geometry(run_b)

    # 절단선 자체가 팔레트와 무관해야 한다 (검출 경로에 팔레트 참조 금지)
    assert grids_a == grids_b
    # 프레임 논리 크기·콘텐츠 bbox·알파(형상)까지 동일 — 색만 다르다
    for (size_a, bbox_a, alpha_a), (size_b, bbox_b, alpha_b) in zip(geometry_a, geometry_b):
        assert size_a == size_b
        assert bbox_a == bbox_b
        assert alpha_a == alpha_b

    # 팔레트는 실제로 적용됐어야 한다 (불변식이 '팔레트 무시'로 위장 통과하면 안 됨):
    # 2색(순적/순청) 강제 런의 불투명 픽셀은 그 두 색조뿐이다 — outline 후처리가
    # 색을 어둡게 만들 수 있으므로(예: (96,0,0)) 채널 패턴으로 판정한다.
    row_b = _manifest(run_b)["rows"][0]
    seen = set()
    for rel in row_b["files"]:
        with Image.open(run_b / rel) as im:
            for r, g, b, a in im.convert("RGBA").getdata():
                if a:
                    seen.add((r, g, b))
    assert seen
    for r, g, b in seen:
        is_reddish = g == 0 and b == 0
        is_bluish = r == 0 and g == 0
        assert is_reddish or is_bluish, (r, g, b)
