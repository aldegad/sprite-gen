# SPDX-License-Identifier: Apache-2.0
"""동일색 런 최빈값 피치 추정기(perfectpixel unfake 이식)와 교차검증 경고를 고정한다.

detect_pixel_grid 는 정수 씨앗 ±0.75 창 안에서만 소수 피치를 정밀화하므로, 씨앗
로터리가 실패하면 참 피치가 창 밖에 놓여 조용히 틀린다 — 실사고 두 모드:

- y축 붕괴: 솔벨 주인공 컴포넌트에서 y 피치가 x 값(29.52)으로 붕괴 (실측 30.56).
- 약수 오검출: 축별 소수 피치(29.5/30.6) 그림에서 두 축 모두 참값의 절반으로.

런 길이는 경계 히스토그램과 독립인 신호(경계 위치가 아니라 경계 사이 거리)라
세컨드 오피니언이 된다. 교차검증은 경고 전용 — 스냅 결과에 영향 0 을 여기서
비트 단위로 고정한다 (자동 교체 금지, No Silent Fallback).
"""
import json
import random

from PIL import Image

import sprite_gen.extract as extract_module
from sprite_gen.extract import (
    crosscheck_pitch_runlen,
    detect_pixel_grid,
    estimate_pixel_grid_runlen,
)
from test_pitch_ground_truth import PALETTE, _logical_art

MAGENTA = (255, 0, 255)


def _upscale_axes(art: Image.Image, scale_x: float, scale_y: float) -> Image.Image:
    """축별 소수 배율 업스케일 — 비균등 리스케일된 AI 도트를 흉내낸다."""
    big = art.resize((art.width * 64, art.height * 64), Image.NEAREST)
    return big.resize((round(art.width * scale_x), round(art.height * scale_y)), Image.NEAREST)


def test_runlen_integer_pitch_is_exact():
    art = _logical_art(24, 40)
    for k in (8, 12, 16, 24):
        upscaled = art.resize((art.width * k, art.height * k), Image.NEAREST)
        estimate = estimate_pixel_grid_runlen(upscaled)
        assert abs(estimate[0] - k) < 0.01, f"k={k}: x={estimate[0]:.3f}"
        assert abs(estimate[1] - k) < 0.01, f"k={k}: y={estimate[1]:.3f}"


def test_runlen_recovers_fractional_per_axis_pitch():
    """소수 피치는 floor/ceil 런이 섞여 나오고, 그 가중 무게중심이 참값을 복원한다."""
    upscaled = _upscale_axes(_logical_art(24, 44), 29.5, 30.6)
    estimate = estimate_pixel_grid_runlen(upscaled)
    assert abs(estimate[0] - 29.5) < 0.2, f"x={estimate[0]:.3f}"
    assert abs(estimate[1] - 30.6) < 0.2, f"y={estimate[1]:.3f}"


def test_runlen_noise_is_inconclusive():
    """격자 없는 입력은 (1.0, 1.0) — 관측 가능하게 포기한다."""
    rng = random.Random(3)
    noise = Image.new("RGB", (200, 200))
    pixels = noise.load()
    for y in range(200):
        for x in range(200):
            pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    assert estimate_pixel_grid_runlen(noise) == (1.0, 1.0)


def test_runlen_small_image_is_inconclusive():
    art = _logical_art(24, 24).resize((24, 24), Image.NEAREST)
    assert estimate_pixel_grid_runlen(art) == (1.0, 1.0)


def test_crosscheck_quiet_when_estimators_agree():
    """detect 가 맞게 잡는 크기(블록 수 적음)에서는 경고 0 — 오경보 없음."""
    upscaled = _upscale_axes(_logical_art(18, 30), 29.5, 30.6)
    grid, _ = detect_pixel_grid(upscaled)
    assert abs(grid[0] - 29.5) < 0.4 and abs(grid[1] - 30.6) < 0.4, "전제: 검출 정상"
    assert crosscheck_pitch_runlen(grid, estimate_pixel_grid_runlen(upscaled)) == []


def test_crosscheck_quiet_on_integer_scales():
    art = _logical_art(24, 40)
    for k in (8, 12, 16, 24):
        upscaled = art.resize((art.width * k, art.height * k), Image.NEAREST)
        grid, _ = detect_pixel_grid(upscaled)
        notes = crosscheck_pitch_runlen(grid, estimate_pixel_grid_runlen(upscaled))
        assert notes == [], f"k={k}: {notes}"


def test_crosscheck_skips_unconfident_estimates():
    assert crosscheck_pitch_runlen((1.0, 1.0), (30.0, 30.0)) == []
    assert crosscheck_pitch_runlen((30.0, 30.0), (1.0, 1.0)) == []
    assert crosscheck_pitch_runlen((30.0, 31.0), (29.2, 30.4)) == []


def test_crosscheck_flags_divisor_misdetection():
    """약수 오검출 재현: 축별 소수 피치 29.5/30.6 에서 detect 가 두 축 다 절반으로.

    블록 수가 많아지면 어떤 정수 씨앗도 참 소수 피치의 ±0.75 창을 못 덮고, 정밀화가
    씨앗의 /2 약수 창에서 최대점을 찾는다. detect_pixel_grid 가 나중에 고쳐져 참값을
    잡게 되면 이 전제 assert 가 먼저 깨진다 — 그때는 픽스처를 더 어렵게 갱신할 것.
    """
    upscaled = _upscale_axes(_logical_art(20, 36, seed=11), 29.5, 30.6)
    grid, _ = detect_pixel_grid(upscaled)
    assert grid[0] < 20.0 and grid[1] < 20.0, f"전제: 약수 오검출 재현 (grid={grid})"

    runlen = estimate_pixel_grid_runlen(upscaled)
    assert abs(runlen[0] - 29.5) < 0.2 and abs(runlen[1] - 30.6) < 0.2

    notes = crosscheck_pitch_runlen(grid, runlen)
    divisor_notes = [note for note in notes if "divisor" in note]
    assert len(divisor_notes) == 2, notes
    assert any("x=" in note for note in divisor_notes)
    assert any("y=" in note for note in divisor_notes)


def test_crosscheck_flags_y_axis_collapse():
    """y축 붕괴 재현: x 는 정수 피치(29)라 씨앗이 잡지만, y 는 소수(30.3)+세로로 길어
    정수 씨앗이 전멸 → y 정밀화가 combined 씨앗(=x 값) 창에 갇혀 x 로 붕괴한다.
    실사고(솔벨 주인공, y 29.52 vs 실측 30.56)와 같은 메커니즘.

    축차 3~4% 는 축별 규칙 임계 밑이라, AA 공통 바이어스가 상쇄되는 축비(y/x)
    규칙이 잡는다.
    """
    upscaled = _upscale_axes(_logical_art(28, 60, seed=5), 29.0, 30.3)
    grid, _ = detect_pixel_grid(upscaled)
    assert abs(grid[0] - 29.0) < 0.6, f"전제: x 정상 검출 (grid={grid})"
    assert abs(grid[1] - grid[0]) < 1.0 and abs(grid[1] - 30.3) > 0.4, (
        f"전제: y 가 x 로 붕괴 (grid={grid})"
    )

    runlen = estimate_pixel_grid_runlen(upscaled)
    assert abs(runlen[1] - 30.3) < 0.2, f"runlen y={runlen[1]:.3f}"

    notes = crosscheck_pitch_runlen(grid, runlen)
    assert any("axis ratio" in note for note in notes), notes


def _build_pixel_perfect_run(root):
    """crosscheck 가 발화하는 픽셀퍼펙트 런 디렉토리 (약수 오검출 픽스처 2프레임)."""
    run_dir = root / "run"
    (run_dir / "raw").mkdir(parents=True)
    frame = _upscale_axes(_logical_art(20, 36, seed=11), 29.5, 30.6)
    gap = 40
    strip = Image.new("RGB", (frame.width * 2 + gap * 3, frame.height + gap * 2), MAGENTA)
    strip.paste(frame, (gap, gap))
    strip.paste(frame, (frame.width + gap * 2, gap))
    strip.save(run_dir / "raw" / "walk.png")
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "crosscheckbot", "description": "runlen crosscheck fixture", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8, "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255], "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "synthetic crosscheck fixture"}},
        "fit": {"pixel_perfect": True, "logical_height": 36},
    }
    (run_dir / "sprite-request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_dir


def test_pipeline_reports_crosscheck_warning_without_touching_snap(tmp_path, monkeypatch, capsys):
    """통합 계약 두 가지를 한 픽스처로 고정한다.

    1. 오검출 런에서 crosscheck 경고가 report warnings 와 stderr 양쪽에 표면화된다.
    2. 경고 전용 — runlen 추정기를 중화(항상 확신 없음)해도 프레임이 비트 동일하다.
       스냅은 언제나 detect_pixel_grid 합의만 쓴다는 증명.
    """
    run_a = _build_pixel_perfect_run(tmp_path / "a")
    assert extract_module.run(run_dir=run_a) == 0
    captured = capsys.readouterr()
    manifest_a = json.loads((run_a / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    crosscheck_a = [w for w in manifest_a["warnings"] if "pitch crosscheck" in w]
    assert crosscheck_a, manifest_a["warnings"]
    assert "[pitch-crosscheck]" in captured.err

    monkeypatch.setattr(extract_module, "estimate_pixel_grid_runlen", lambda *args, **kwargs: (1.0, 1.0))
    run_b = _build_pixel_perfect_run(tmp_path / "b")
    assert extract_module.run(run_dir=run_b) == 0
    manifest_b = json.loads((run_b / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    assert [w for w in manifest_b["warnings"] if "pitch crosscheck" in w] == []

    frames_a = sorted((run_a / "frames").rglob("*.png"))
    frames_b = sorted((run_b / "frames").rglob("*.png"))
    assert frames_a and [f.relative_to(run_a) for f in frames_a] == [f.relative_to(run_b) for f in frames_b]
    for file_a, file_b in zip(frames_a, frames_b):
        assert file_a.read_bytes() == file_b.read_bytes(), f"{file_a.name} differs — crosscheck touched the snap"
