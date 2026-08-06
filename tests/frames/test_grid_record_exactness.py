# SPDX-License-Identifier: Apache-2.0
"""기록된 격자가 진실인가 — 칸수 불변식 + 쌍둥이 풋프린트 (plan sprite-gen/grid-record-exactness).

실사고 (2026-07-18 side_idle): kCentroid 가 실제 샘플한 격자(31×58)와 manifest 에 기록된
절단선(45×83 — 반피치 하모닉)이 **서로 다른 검출 패스**에서 나와, 오버레이가 최종 픽셀과 무관한
격자를 그렸다. 그 이중 검출은 프레임별 검출 엔진(v1.56.75+)이 제거했지만, **"기록 칸수 == 최종
픽셀 수" 를 단정으로 못박은 곳은 없었다** — 표시측 `display.js` 의 ±1 관용 가드만 있었고, 그건
엔진이 거짓을 기록해도 조용히 격자를 접는 쪽으로 실패한다.

계측 근거 (2026-07-25, `_measure_grid_slack.py`, 피치 4·8·9.5·13·17.24·21.7 × 셀 48~256 =
10조합): 칸수 delta 전부 0, 쌍둥이 어긋남 전부 0.0 논리픽셀. 관용치가 아니라 **정확 일치**가
현재 엔진의 실제 동작이므로, 그 사실을 단정으로 고정한다 (여유를 재고 나서 조인다).
"""

from __future__ import annotations

import json
import random

from PIL import Image

import sprite_gen.frames.extract as extract_module
from sprite_gen.curate.curation import pixel_snap_scale

MAGENTA = (255, 0, 255)


def _logical_art(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    art = Image.new("RGB", (width, height), MAGENTA)
    for y in range(height):
        for x in range(width):
            if rng.random() < 0.55:
                art.putpixel((x, y), (rng.randrange(30, 220), rng.randrange(30, 220),
                                      rng.randrange(30, 220)))
    return art


def _build_run(root, pitch: float, cell: int = 96, logical_height: int = 48,
               frames: int = 2, logical=(20, 36)) -> "object":
    """pp 런 하나. 비정수 피치는 실수 배율 리샘플로 만든다 — AI 도트의 블록 폭은 정수로
    떨어지지 않으므로(예 17.24px) 정수 피치만 재면 라운딩 여유를 과소평가한다."""
    run_dir = root / "run"
    (run_dir / "raw").mkdir(parents=True)
    base = max(2, int(round(pitch)))
    frame = _logical_art(*logical, seed=7).resize(
        (logical[0] * base, logical[1] * base), Image.Resampling.NEAREST)
    if abs(pitch - base) > 1e-6:
        ratio = pitch / base
        frame = frame.resize((round(frame.width * ratio), round(frame.height * ratio)),
                             Image.Resampling.LANCZOS)
    gap = 40
    strip = Image.new("RGB", (frame.width * frames + gap * (frames + 1),
                              frame.height + gap * 2), MAGENTA)
    for index in range(frames):
        strip.paste(frame, (gap + index * (frame.width + gap), gap))
    strip.save(run_dir / "raw" / "walk.png")
    margin = max(2, cell // 16)
    (run_dir / "sprite-request.json").write_text(json.dumps({
        "version": 1, "kind": "sprite-gen-request", "engine": "component-row",
        "character": {"id": "gridbot", "description": "grid record fixture", "base_image": None},
        "cell": {"shape": "square", "width": cell, "height": cell, "safe_margin_x": margin,
                 "safe_margin_y": margin, "size": cell, "safe_margin": margin},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255],
                       "selection": "fallback"},
        "states": {"walk": {"frames": frames, "fps": 8, "loop": True, "action": "grid fixture"}},
        "fit": {"pixel_unfake": True, "logical_height": logical_height},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_dir


def _row_and_scale(run_dir):
    manifest = json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    return manifest["rows"][0], (pixel_snap_scale(request) or 1)


def _content_logical_size(path, scale: int) -> tuple[int, int]:
    with Image.open(path) as im:
        box = im.convert("RGBA").getchannel("A").getbbox()
    return (box[2] - box[0]) // scale, (box[3] - box[1]) // scale


def _cell_counts(grid: dict) -> tuple[int, int]:
    return len(grid["x"]) - 1, len(grid["y"]) - 1


def test_recorded_grid_cell_count_equals_final_pixel_count(tmp_path) -> None:
    """기록 칸수 == 최종 콘텐츠 픽셀 수. **정확 일치**다 (관용치 없음).

    이게 깨지면 기록이 진실이 아니라는 뜻이고, 오버레이는 최종 픽셀과 무관한 격자를 그린다."""
    run_dir = _build_run(tmp_path, pitch=8)
    assert extract_module.run(run_dir=run_dir) == 0
    row, scale = _row_and_scale(run_dir)
    grids = row.get("input_grids")
    assert grids, "input_grids 가 기록되지 않았다 — 격자 진실의 소유자가 없어진다"
    for index, rel in enumerate(row["files"]):
        final = _content_logical_size(run_dir / rel, scale)
        assert _cell_counts(grids[index]) == final, (
            f"frame {index}: 기록 칸수 {_cell_counts(grids[index])} != 최종 픽셀 수 {final}")


def test_cell_count_invariant_catches_a_half_pitch_harmonic(tmp_path) -> None:
    """mutant 검증: 반피치 하모닉으로 기록을 오염시키면 위 단정이 **실제로** 잡는가.

    2026-07-18 실사고의 형태를 그대로 재현한다 (기록 45칸 vs 최종 31칸). 단정이 잡지 못하면
    그 테스트는 통과만 하는 장식이다."""
    run_dir = _build_run(tmp_path, pitch=8)
    assert extract_module.run(run_dir=run_dir) == 0
    row, scale = _row_and_scale(run_dir)
    grid = dict(row["input_grids"][0])
    # 반피치: 절단선 사이에 중간점을 끼워 칸수를 2배로 만든다 (하모닉 오검출과 같은 결과)
    harmonic_x = [grid["x"][0]]
    for a, b in zip(grid["x"], grid["x"][1:]):
        harmonic_x += [(a + b) / 2, b]
    mutant = {"x": harmonic_x, "y": grid["y"]}
    final = _content_logical_size(run_dir / row["files"][0], scale)
    assert _cell_counts(grid) == final                      # 원본은 통과
    assert _cell_counts(mutant) != final, "mutant 가 단정을 통과했다 — 불변식이 아무것도 막지 못한다"


def test_twin_footprint_matches_the_final_frame_exactly(tmp_path) -> None:
    """orig 쌍둥이의 콘텐츠 bbox 가 최종 프레임과 **논리픽셀 단위로 정확히** 겹친다.

    어긋나면 픽셀퍼펙트 토글에서 스프라이트가 튄다 (실사고 2026-07-18 항목 2: final top 0 vs
    orig top 1). `tests/test_pixel_snap.py` 는 같은 축을 ±2 논리픽셀 경계로 지키는데, 실측
    (10조합)에서 실제 어긋남은 0.0 이므로 여기서 정확 일치를 단정한다 — 경계 테스트는 회귀
    안전망으로 남기고, 이 테스트가 정확성을 잃는 순간을 잡는다."""
    run_dir = _build_run(tmp_path, pitch=8)
    assert extract_module.run(run_dir=run_dir) == 0
    row, scale = _row_and_scale(run_dir)
    assert row.get("orig_files"), "orig 쌍둥이가 없다 — 이 축을 검증할 재료가 없다"
    checked = 0
    for index, rel in enumerate(row["files"]):
        head, _, name = rel.rpartition("/")
        orig_path = run_dir / f"{head}/orig/{name}"
        if not orig_path.is_file():
            continue
        with Image.open(run_dir / rel) as pixel, Image.open(orig_path) as orig:
            pixel_box = pixel.convert("RGBA").getchannel("A").getbbox()
            orig_box = orig.convert("RGBA").getchannel("A").getbbox()
            orig_scale = max(1, orig.width // pixel.width)
        for edge, (o, p) in enumerate(zip(orig_box, pixel_box)):
            assert o / orig_scale / scale == p / scale, (
                f"frame {index} edge {edge}: orig {o}/{orig_scale} vs final {p} (논리픽셀 어긋남)")
        checked += 1
    assert checked, "쌍둥이를 한 장도 검사하지 못했다"


def test_grid_record_stays_exact_across_pitches_and_cells(tmp_path) -> None:
    """정수/비정수 피치·여러 셀 크기에서 두 불변식이 함께 성립한다.

    비정수 피치(17.24)는 AI 도트의 실제 성질이고, 셀 크기는 pp_scale 을 1/2/4 로 가른다 —
    라운딩이 걸릴 조건을 골라 넣었다 (계측 표: 플랜 Notes)."""
    cases = [(8, 48, 30), (17.24, 48, 42), (9.5, 64, 48), (4, 96, 48)]
    for pitch, cell, logical_height in cases:
        run_dir = _build_run(tmp_path / f"p{pitch}c{cell}", pitch=pitch, cell=cell,
                             logical_height=logical_height)
        assert extract_module.run(run_dir=run_dir) == 0, f"pitch {pitch} cell {cell} 추출 실패"
        row, scale = _row_and_scale(run_dir)
        for index, rel in enumerate(row["files"]):
            final = _content_logical_size(run_dir / rel, scale)
            grid = (row.get("input_grids") or [None])[index]
            assert grid, f"pitch {pitch} cell {cell} frame {index}: 기록 없음"
            assert _cell_counts(grid) == final, (
                f"pitch {pitch} cell {cell} frame {index}: {_cell_counts(grid)} != {final}")
