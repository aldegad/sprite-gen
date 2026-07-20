# SPDX-License-Identifier: Apache-2.0
"""온디맨드 픽셀퍼펙트 프리뷰 파생 캐시 계약 (plan sprite-gen/per-frame-pixel-grid).

트윈(.plain/orig) 없는 런(임포트 세트·비 pp 런)의 /api/run 스냅샷에
`pixelPreviewUrl` 이 주입되고, 파생 캐시는 (source mtime, engine_revision) 키 +
PNG 유실 self-heal + 요청당 예산(초과분 `pixelPreviewDeferred` 보고) 계약을 지킨다.
검출 실패 프레임(이미 논리 해상도 픽셀아트)은 프리뷰가 없다 — 가짜 격자 금지.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import serve_curation  # noqa: E402
from serve_curation import _PIXEL_PREVIEW_DIR, build_run_state  # noqa: E402
from sprite_gen.unpack_atlas import import_png_groups  # noqa: E402


def _native_frame(seed: int, pitch: int = 8, logical=(20, 36)) -> Image.Image:
    rng = random.Random(seed)
    art = Image.new("RGBA", logical, (0, 0, 0, 0))
    for y in range(logical[1]):
        for x in range(logical[0]):
            if rng.random() < 0.6:
                art.putpixel((x, y), (rng.randrange(30, 220), rng.randrange(30, 220),
                                      rng.randrange(30, 220), 255))
    return art.resize((logical[0] * pitch, logical[1] * pitch), Image.Resampling.NEAREST)


def _build_run(root: Path, frame_images: list[Image.Image], state: str = "walk") -> Path:
    """정식 임포터(unpack --pngs-dir 경로)로 트윈 없는 큐레이터 런을 만든다."""
    pngs = root / "pngs" / state
    pngs.mkdir(parents=True)
    for index, image in enumerate(frame_images):
        image.save(pngs / f"frame-{index}.png")
    run_dir = root / "run"
    import_png_groups(run_dir, [{
        "name": state,
        "paths": sorted(pngs.glob("*.png")),
        "labels": [],
        "refs": [],
    }])
    return run_dir


def _frames(snapshot: dict, state: str = "walk") -> list[dict]:
    return next(st for st in snapshot["states"] if st["name"] == state)["frames"]


def test_preview_injected_for_twinless_run(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path, [_native_frame(7), _native_frame(11)])
    snapshot = build_run_state(run_dir)
    frames = _frames(snapshot)
    assert all(f.get("pixelPreviewUrl") for f in frames)
    assert all(f.get("pixelPreviewPitch") for f in frames)
    assert snapshot["pixelPreviewDeferred"] == 0
    # 캐시 파일 실재 + 논리 해상도(원본보다 훨씬 작음)
    previews = list((run_dir / _PIXEL_PREVIEW_DIR).glob("*.png"))
    assert len(previews) == 2
    with Image.open(previews[0]) as preview:
        assert preview.width < 80 and preview.height < 80


def test_preview_budget_reports_deferred_and_resumes(tmp_path: Path, monkeypatch) -> None:
    run_dir = _build_run(tmp_path, [_native_frame(7), _native_frame(11), _native_frame(13)])
    monkeypatch.setattr(serve_curation, "_PIXEL_PREVIEW_BUDGET", 2)
    first = build_run_state(run_dir)
    made_first = [bool(f.get("pixelPreviewUrl")) for f in _frames(first)]
    assert made_first.count(True) == 2
    assert first["pixelPreviewDeferred"] == 1  # 조용한 캡 금지 — 밀린 수를 보고
    second = build_run_state(run_dir)  # 리로드가 이어서 계산
    assert all(f.get("pixelPreviewUrl") for f in _frames(second))
    assert second["pixelPreviewDeferred"] == 0


def test_preview_png_loss_self_heals(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path, [_native_frame(7)])
    build_run_state(run_dir)
    png = next((run_dir / _PIXEL_PREVIEW_DIR).glob("*.png"))
    png.unlink()  # 파생 캐시 유실 시뮬레이션 (meta json 은 남음)
    snapshot = build_run_state(run_dir)
    assert all(f.get("pixelPreviewUrl") for f in _frames(snapshot))
    assert png.is_file()  # meta 단독 신뢰 금지 — 재계산으로 자가치유


def test_preview_engine_revision_invalidates_cache(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path, [_native_frame(7)])
    build_run_state(run_dir)
    meta_path = next((run_dir / _PIXEL_PREVIEW_DIR).glob("*.json"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["engine"] = "0" * 12  # 구엔진 스탬프 시뮬레이션
    meta["pitch"] = [99.0, 99.0]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    snapshot = build_run_state(run_dir)
    healed = json.loads(meta_path.read_text(encoding="utf-8"))
    assert healed["engine"] != "0" * 12  # 엔진 리비전 키 — stale 파생물 자동 재유도
    assert _frames(snapshot)[0]["pixelPreviewPitch"] != [99.0, 99.0]


def test_no_fake_grid_when_detection_unconfident(tmp_path: Path, monkeypatch) -> None:
    # 검출이 확신 없음(피치 1.0)이면 프리뷰를 만들지 않는다 — 가짜 격자 금지.
    # (특정 이미지의 확신 여부는 검출기 소관이라, 계약 검증은 무확신을 명시 주입)
    run_dir = _build_run(tmp_path, [_native_frame(7)])
    import extract as extract_wrapper
    monkeypatch.setattr(extract_wrapper, "detect_pixel_grid",
                        lambda image, max_pitch=48: ((1.0, 1.0), (0.0, 0.0)))
    snapshot = build_run_state(run_dir)
    frame = _frames(snapshot)[0]
    assert "pixelPreviewUrl" not in frame
    meta = json.loads(next((run_dir / _PIXEL_PREVIEW_DIR).glob("*.json")).read_text(encoding="utf-8"))
    assert meta["pitch"] is None and meta["note"]  # 사유가 관측 가능하게 남는다
