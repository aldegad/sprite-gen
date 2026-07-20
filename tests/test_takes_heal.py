# SPDX-License-Identifier: Apache-2.0
"""Takes 1급 계약 + 파생 캐시 자가치유(heal_run).

- 한 상태의 프레임 풀 = primary raw + 선언된 테이크들(`states.<s>.takes`),
  각 스트립은 독립 스냅(스트립별 합의 피치), 행 정합·배치는 함께.
- frames/ 는 (raw + request + 엔진)의 파생 캐시 — 행의 engine_revision 이 현재
  엔진과 다르면 heal_run 이 raw 에서 자동 재유도한다. 뷰에 '재추출' 개념이
  없는 실시간 계약(수홍 확정 2026-07-14)의 엔진 반쪽이다.
"""

import json
import random
from pathlib import Path

import pytest
from PIL import Image

import sprite_gen.extract as extract_module
from sprite_gen.layout import take_raw_rel

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


def _build_run(root: Path, takes=None) -> Path:
    run_dir = root / "run"
    (run_dir / "raw").mkdir(parents=True)
    _strip(2, seed=7).save(run_dir / "raw" / "walk.png")
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "takebot", "description": "takes fixture", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8,
                 "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255], "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "takes fixture"}},
        "fit": {"pixel_perfect": True, "logical_height": 48},
    }
    if takes is not None:
        request["states"]["walk"]["takes"] = takes
    (run_dir / "sprite-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_dir


def _manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))


def test_takes_extend_row_pool(tmp_path: Path) -> None:
    takes = [{"label": "alt", "frames": 2}]
    run_dir = _build_run(tmp_path, takes=takes)
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    take_path = run_dir / take_raw_rel(request, "walk", "alt")
    take_path.parent.mkdir(parents=True)
    _strip(2, seed=11).save(take_path)

    assert extract_module.run(run_dir=run_dir) == 0
    row = _manifest(run_dir)["rows"][0]
    assert row["frames"] == 4
    assert len(row["files"]) == 4
    assert row["labels"] == ["", "", "alt#0", "alt#1"]
    assert row["takes"] == [
        {"label": None, "start": 0, "frames": 2, "raw": "raw/walk.png"},
        {"label": "alt", "start": 2, "frames": 2, "raw": "raw/walk.takes/alt.png"},
    ]
    for rel in row["files"]:
        assert (run_dir / rel).is_file(), rel
    # 테이크 프레임은 다른 생성물이다 — primary 프레임과 내용이 달라야 한다
    primary = Image.open(run_dir / row["files"][0]).tobytes()
    alt = Image.open(run_dir / row["files"][2]).tobytes()
    assert primary != alt


def test_take_raw_missing_fails_loud(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path, takes=[{"label": "alt", "frames": 2}])
    assert extract_module.run(run_dir=run_dir) == 1  # 선언된 테이크 누락 = 행 실패 (부분 풀 게시 금지)
    assert not (run_dir / "frames" / "frames-manifest.json").is_file()


def test_takes_require_pixel_perfect(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path, takes=[{"label": "alt", "frames": 2}])
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    del request["fit"]["pixel_perfect"]
    (run_dir / "sprite-request.json").write_text(json.dumps(request), encoding="utf-8")
    take_path = run_dir / take_raw_rel(request, "walk", "alt")
    take_path.parent.mkdir(parents=True)
    _strip(2, seed=11).save(take_path)
    assert extract_module.run(run_dir=run_dir) == 1


def test_heal_rederives_stale_rows(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    assert extract_module.run(run_dir=run_dir) == 0
    manifest_path = run_dir / "frames" / "frames-manifest.json"

    # 현재 엔진으로 갓 구운 런은 이미 신선 — no-op
    report = extract_module.heal_run(run_dir)
    assert report["healed"] == [] and report["kept_stale"] == []

    # 구엔진 스탬프 시뮬레이션 → heal 이 raw 에서 재유도하고 재스탬프
    manifest = _manifest(run_dir)
    manifest["rows"][0]["engine_revision"] = "0" * 12
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = extract_module.heal_run(run_dir)
    assert report["healed"] == ["walk"]
    assert _manifest(run_dir)["rows"][0]["engine_revision"] == extract_module.engine_revision()
    assert (run_dir / "frames" / "walk" / "frame-1.png").is_file()


def test_heal_keeps_stale_row_without_raw(tmp_path: Path) -> None:
    run_dir = _build_run(tmp_path)
    assert extract_module.run(run_dir=run_dir) == 0
    manifest_path = run_dir / "frames" / "frames-manifest.json"
    manifest = _manifest(run_dir)
    manifest["rows"][0]["engine_revision"] = "0" * 12
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    frame_bytes = (run_dir / "frames" / "walk" / "frame-0.png").read_bytes()
    (run_dir / "raw" / "walk.png").unlink()

    report = extract_module.heal_run(run_dir)
    assert report["healed"] == []
    assert report["kept_stale"] == ["walk"]
    assert report["notes"]
    # 재료가 없으면 이전 세대를 바이트 그대로 보존한다
    assert (run_dir / "frames" / "walk" / "frame-0.png").read_bytes() == frame_bytes


def test_view_heals_and_downloads_live_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """뷰/다운로드 계약: 스냅샷 요청이 stale 캐시를 자가치유하고, 버튼 3종은
    현재 라이브 상태를 계산해 zip 으로 내려준다 (게임 적용 의미 없음)."""
    import sys as _sys
    import io
    import zipfile

    run_dir = _build_run(tmp_path)
    assert extract_module.run(run_dir=run_dir) == 0
    manifest_path = run_dir / "frames" / "frames-manifest.json"
    manifest = _manifest(run_dir)
    manifest["rows"][0]["engine_revision"] = "0" * 12
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import serve_curation

    # 결정론: 그레이스를 넉넉히 주입해 느린 CI 러너(관측: Python 3.10)에서도 소규모
    # heal 이 동기 경로로 완료됨을 안정 검증한다. 프로덕션 그레이스(1.5s)는 무변 —
    # 벽시계 1.5s 에 의존하던 flaky 실패 제거 (다람이 2026-07-21, heal 로직 무변).
    monkeypatch.setattr(serve_curation, "_HEAL_GRACE_SECONDS", 60.0)
    busy = serve_curation.maybe_heal(run_dir)
    assert busy is False  # 소규모 heal 은 그레이스 안에 끝나 동기 경로 유지
    heal = serve_curation.take_heal_report()  # 단일 소비자 (/api/run 역할)
    assert heal is not None and heal["healed"] == ["walk"]
    assert serve_curation.maybe_heal(run_dir) is False  # 신선하면 no-op
    assert serve_curation.take_heal_report() is None

    built = serve_curation.build_download(run_dir, "atlas")
    assert not isinstance(built, dict), built
    data, filename = built
    assert filename == "takebot-atlas.zip"
    names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == {"sprite-sheet-alpha.png", "manifest.json"}

    built = serve_curation.build_download(run_dir, "gifs")
    assert not isinstance(built, dict), built
    data, filename = built
    assert filename == "takebot-gifs.zip"
    assert any(n.endswith("walk.gif") for n in zipfile.ZipFile(io.BytesIO(data)).namelist())

    # 줄 단위 단건 GIF — zip 이 아니라 GIF 원파일, 미지 상태는 fail loud
    built = serve_curation.build_download(run_dir, "gif:walk")
    assert not isinstance(built, dict), built
    data, filename = built
    assert filename == "takebot-walk.gif"
    assert data[:6] in (b"GIF87a", b"GIF89a")
    assert isinstance(serve_curation.build_download(run_dir, "gif:nope"), dict)


def test_atlas_reuses_cells_for_identical_instances(tmp_path: Path) -> None:
    """아틀라스 셀 재사용 (수홍 승인 2026-07-16): 같은 (원본, 변형, 픽셀편집)의
    복제 인스턴스는 칸 하나를 공유한다 — 루프딜레이용 프레임 복제가 텍스처를
    늘리지 않는다. rect 는 재생 순서대로 반복되고, durations_ms 계약이 실린다."""
    import sprite_gen.compose_atlas as compose_module

    run_dir = _build_run(tmp_path)
    assert extract_module.run(run_dir=run_dir) == 0
    # 프레임 0 을 두 번 복제(인스턴스 2, 3): 하나는 동일 굽기(재사용), 하나는 변형(별도 칸)
    curation = {
        "version": 1, "kind": "sprite-gen-curation",
        "states": {"walk": {
            "selected": [0, 1, 2, 3],
            "clones": {"2": 0, "3": 0},
            "transforms": {"3": {"rotate": 0, "scale": 1, "dx": 3, "dy": 0,
                                  "shx": 0, "shy": 0, "flipX": 0}},
        }},
    }
    from sprite_gen.curation import stamp_curation
    stamped = stamp_curation(run_dir, curation)  # 세대 도장 없는 사이드카는 드롭된다 (정상 쓰기 경로)
    (run_dir / "curation.json").write_text(json.dumps(stamped), encoding="utf-8")
    assert compose_module.run(run_dir=run_dir, min_used_pixels=100) == 0

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rects = manifest["frame_layout"]["rows"]["walk"]
    assert len(rects) == 4
    assert rects[2] == rects[0]          # 동일 굽기 복제 = 같은 칸 재사용
    assert rects[3] != rects[0]          # 변형이 다르면 별도 칸
    unique = {(r["x"], r["y"]) for r in rects}
    assert len(unique) == 3
    # 아틀라스 폭 = 고유 칸 수 (인스턴스 수 아님)
    assert manifest["frame_layout"]["sheetWidth"] == 3 * manifest["frame_layout"]["cellWidth"]
    anim = manifest["animation"]["rows"]["walk"]
    assert anim["frames"] == 4
    assert anim["durations_ms"] == [round(1000 / anim["fps"])] * 4
