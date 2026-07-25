# SPDX-License-Identifier: Apache-2.0
"""`fit.logical_height` 계약 — 선언값이 아니라 파생 격자가 진실이다.

실사고 (2026-07-25, 제품 solvell hero founder_v7/v8): `conform`(눌림)이 제거된 뒤
`logical_height: 48` 은 셀 64 에서 아무 픽셀도 바꾸지 않는 값이 됐다 (`64//48 == 1`
== `64//64`). 그런데 남아서 두 가지를 오염시켰다:

1. 큐레이션 행 지문(`state_revision`)이 **선언값**을 물고 있어서, 그 죽은 값을 지우기만
   해도 프레임이 바이트 동일한데 14행 큐레이션이 통째로 드롭됐다.
2. 웹뷰 헤더가 "48px" 라고 거짓 라벨을 띄웠다 (실제 논리 높이는 64).

계약:
- `effective_logical_height` = 셀 높이 / 파생 배율. 정수 격자가 반올림한 결과가 진실.
- 행 지문은 파생 배율만 본다 — 출력이 같은 선언 편집은 큐레이션을 살린다.
- 선언이 적용되지 못하면 추출이 경고로 관측시킨다 (조용히 무시하지 않는다).
- 배율 식은 한 곳(`pixel_snap_scale`)만 소유한다 — extract·웹뷰가 손으로 복제하지 않는다.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from PIL import Image

import sprite_gen.extract as extract_module
from sprite_gen.curation import (
    effective_logical_height,
    pixel_snap_scale,
    state_revision,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAGENTA = (255, 0, 255)
CELL = 64


def _logical_art(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    art = Image.new("RGB", (width, height), MAGENTA)
    for y in range(height):
        for x in range(width):
            if rng.random() < 0.55:
                art.putpixel((x, y), (rng.randrange(30, 220), rng.randrange(30, 220),
                                      rng.randrange(30, 220)))
    return art


def _build_pp_run(root: Path, logical_height: int | None) -> Path:
    """셀 64 픽셀퍼펙트 런 (정수 피치로 확대한 논리 아트 2프레임)."""
    run_dir = root / "run"
    (run_dir / "raw").mkdir(parents=True)
    pitch = 8
    frame = _logical_art(20, 36, seed=11).resize((20 * pitch, 36 * pitch),
                                                 Image.Resampling.NEAREST)
    gap = 40
    strip = Image.new("RGB", (frame.width * 2 + gap * 3, frame.height + gap * 2), MAGENTA)
    strip.paste(frame, (gap, gap))
    strip.paste(frame, (frame.width + gap * 2, gap))
    strip.save(run_dir / "raw" / "walk.png")
    fit: dict = {"pixel_perfect": True}
    if logical_height is not None:
        fit["logical_height"] = logical_height
    request = {
        "version": 1, "kind": "sprite-gen-request", "engine": "component-row",
        "character": {"id": "lhbot", "description": "logical height fixture", "base_image": None},
        "cell": {"shape": "square", "width": CELL, "height": CELL, "size": CELL,
                 "safe_margin_x": 6, "safe_margin_y": 6, "safe_margin": 6},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255],
                       "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "synthetic"}},
        "fit": fit,
    }
    (run_dir / "sprite-request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    return run_dir


def _set_fit(run_dir: Path, fit: dict) -> None:
    path = run_dir / "sprite-request.json"
    request = json.loads(path.read_text(encoding="utf-8"))
    request["fit"] = fit
    path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")


def _request(cell: int, **fit) -> dict:
    return {
        "cell": {"shape": "square", "size": cell, "width": cell, "height": cell,
                 "safe_margin": 6, "safe_margin_x": 6, "safe_margin_y": 6},
        "fit": fit,
        "states": {"idle": {"frames": 4, "fps": 4}},
    }


def test_declaration_that_the_integer_grid_rounds_away_reports_the_effective_height():
    """셀 64 + 선언 48 = 배율 1 = 유효 논리 높이 64 (선언은 무효)."""
    assert pixel_snap_scale(_request(64, pixel_perfect=True, logical_height=48)) == 1
    assert effective_logical_height(_request(64, pixel_perfect=True, logical_height=48)) == 64
    # 생략 = 셀과 1:1 (권장 기본값)
    assert effective_logical_height(_request(64, pixel_perfect=True)) == 64


def test_a_real_declaration_still_scales():
    """약수 선언은 그대로 살아있다 — 청키 룩은 기능이지 레거시가 아니다."""
    assert pixel_snap_scale(_request(64, pixel_perfect=True, logical_height=32)) == 2
    assert effective_logical_height(_request(64, pixel_perfect=True, logical_height=32)) == 32
    assert pixel_snap_scale(_request(96, pixel_perfect=True, logical_height=32)) == 3
    assert effective_logical_height(_request(96, pixel_perfect=True, logical_height=32)) == 32


def test_legacy_run_without_pixel_perfect_has_no_logical_grid():
    assert pixel_snap_scale(_request(64)) is None
    assert effective_logical_height(_request(64)) is None


def test_row_fingerprint_survives_removing_a_dead_declaration(tmp_path: Path):
    """핵심 회귀: 출력이 같은 선언 편집은 행 지문을 바꾸지 않는다.

    이게 깨지면 죽은 값을 지우는 순간 큐레이션이 전량 드롭된다 (실사고 founder_v8:
    프레임이 바이트 동일했는데도 14행이 통째로 날아갔다)."""
    run = _build_pp_run(tmp_path, logical_height=48)  # 셀 64 → 배율 1 → 선언 무효
    assert extract_module.run(run_dir=run) == 0
    frames_before = {p.name: p.read_bytes()
                     for p in sorted((run / "frames" / "walk").glob("frame-*.png"))}
    with_dead_value = state_revision(run, "walk")
    assert with_dead_value, "행 지문을 계산하지 못했다 — 픽스처 전제가 깨졌다"

    _set_fit(run, {"pixel_perfect": True})
    assert state_revision(run, "walk") == with_dead_value, (
        "무효 선언을 지웠을 뿐인데 행 지문이 바뀌었다 — 큐레이션이 통째로 드롭된다")

    # 전제 확인: 그 편집으로 프레임이 실제로 안 바뀐다 (지문 유지가 정당한 이유)
    assert extract_module.run(run_dir=run) == 0
    frames_after = {p.name: p.read_bytes()
                    for p in sorted((run / "frames" / "walk").glob("frame-*.png"))}
    assert frames_after == frames_before, "전제 붕괴: 선언 제거가 픽셀을 바꿨다"

    # 진짜로 격자를 바꾸는 선언은 지문을 바꿔야 한다 (검증이 무력화되면 안 된다)
    _set_fit(run, {"pixel_perfect": True, "logical_height": CELL // 2})
    assert state_revision(run, "walk") != with_dead_value, (
        "배율이 실제로 바뀌는 선언인데 지문이 그대로다 — 낡은 선택이 새 프레임에 적용된다")


def test_unhonored_declaration_is_reported_as_a_warning(tmp_path: Path):
    """조용히 무시하지 않는다 — 요청에 남은 죽은 값은 경고로 보인다 (원칙 6)."""
    run = _build_pp_run(tmp_path, logical_height=48)
    assert extract_module.run(run_dir=run) == 0
    manifest = json.loads((run / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    warnings = " ".join(manifest.get("warnings") or [])
    assert "logical_height" in warnings and "not applied as declared" in warnings, (
        f"무효 선언이 관측되지 않았다: {manifest.get('warnings')}")

    # 유효한 선언은 경고하지 않는다 (경고가 노이즈가 되면 아무도 안 본다)
    quiet = _build_pp_run(tmp_path / "quiet", logical_height=CELL // 2)
    assert extract_module.run(run_dir=quiet) == 0
    quiet_manifest = json.loads(
        (quiet / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    assert not any("not applied as declared" in w for w in (quiet_manifest.get("warnings") or [])), (
        f"유효한 선언에 경고가 붙었다: {quiet_manifest.get('warnings')}")


def test_scale_formula_has_a_single_owner():
    """extract·웹뷰가 배율 식을 손으로 복제하지 않는다 (원칙 1).

    복제본은 실제로 갈렸다 — 웹뷰 사본에는 usable-height 클램프 분기가 없었다."""
    formula = re.compile(r"cell(_state\[.height.\]|_height)\s*//\s*max\(1,\s*logical_height")
    for rel in ("sprite_gen/extract.py", "scripts/serve_curation.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not formula.search(source), (
            f"{rel} 가 배율 식을 다시 유도한다 — pixel_snap_scale 을 호출해야 한다")
        assert "pixel_snap_scale" in source, f"{rel} 가 배율 SSoT 를 쓰지 않는다"
