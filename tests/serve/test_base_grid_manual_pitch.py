# SPDX-License-Identifier: Apache-2.0
"""수동 픽셀 격자(피치) — 큐레이션뷰 프런트엔드의 서버 계약.

배경: AI raw 는 블록이 정수로 안 떨어져 `detect_pixel_grid` 가 종종 틀린다. 사람이
큐레이션뷰에서 피치를 직접 맞추면 그 값이 `fit.pitch_manual` 로 저장되고, 표시 격자·
base-edit 굽기·추출이 모두 그 한 값을 읽어야 한다 ("표시 격자 = 샘플링 진실").

여기서 강제하는 계약:
  1) `_base_grid_response` 소스 우선순위 = 쿼리 override > fit.pitch_manual > 자동 검출.
  2) 자동 경로 골든 유지 — fit.pitch_manual 이 없으면 예전 검출 격자 그대로.
  3) 저장된 수동 피치가 표시 격자를 실제로 바꾸고, 그 절단선이 `_grid_edges` 와 일치한다.
  4) `_query_pitch_pair` 는 쓰레기 값을 조용히 격자로 만들지 않는다.
  5) 저장/비움 도장(SSoT 라운드트립)이 fit.pitch_manual 을 켜고 끈다.
"""
from __future__ import annotations

from PIL import Image

from sprite_gen.frames.extract import (_grid_edges, detect_pixel_grid,
                                       remove_chroma_background_ycbcr,
                                       solid_alpha_bbox)
from sprite_gen.serve.serve_curation import (_base_grid_response,
                                             _query_pitch_pair)
from sprite_gen.spec.runio import load_request, write_request

CHROMA = (255, 0, 255)
_PALETTE = [(200, 40, 40), (40, 200, 40), (40, 40, 200),
            (220, 200, 40), (200, 40, 200), (40, 200, 200)]


def _write_base(run_dir, pitch=8, cells=6, margin=6):
    """마젠타 배경 위 pitch×pitch 단색 블록 cells×cells 합성 픽셀아트 베이스. 인접
    블록 색이 항상 달라 색경계가 pitch 배수에 몰리므로 검출이 확신 피치를 잡는다."""
    side = cells * pitch
    img = Image.new("RGBA", (side + margin * 2, side + margin * 2), CHROMA + (255,))
    for by in range(cells):
        for bx in range(cells):
            color = _PALETTE[(bx + by * 2) % len(_PALETTE)] + (255,)
            for y in range(pitch):
                for x in range(pitch):
                    img.putpixel((margin + bx * pitch + x, margin + by * pitch + y), color)
    path = run_dir / "base-source.png"
    img.save(path)
    return path


def _detect_old_way(base_path):
    """내 변경 이전의 자동 경로를 그대로 재현 — 골든 비교용. `_base_grid_response` 의
    자동 분기가 이것과 비트 동일해야 한다."""
    with Image.open(base_path) as opened:
        cleaned = remove_chroma_background_ycbcr(opened.convert("RGBA"), CHROMA)
    box = solid_alpha_bbox(cleaned) or cleaned.getbbox()
    tight = cleaned.crop(box)
    (px, py), (phx, phy) = detect_pixel_grid(tight)
    return {
        "xEdges": [box[0] + e for e in _grid_edges(tight.width, px, phx)],
        "yEdges": [box[1] + e for e in _grid_edges(tight.height, py, phy)],
        "pitch": [round(px, 2), round(py, 2)],
    }


def _set_manual(run_dir, pitch):
    request = load_request(run_dir)
    fit = request.setdefault("fit", {})
    if pitch is None:
        fit.pop("pitch_manual", None)
    else:
        fit["pitch_manual"] = list(pitch)
    write_request(run_dir, request)


def test_auto_path_unchanged_when_no_manual(fixture_run_dir):
    """골든: fit.pitch_manual 이 없으면 자동 검출 격자가 변경 이전과 비트 동일하다."""
    base = _write_base(fixture_run_dir)
    grid = _base_grid_response(fixture_run_dir, base)["grid"]
    assert grid is not None
    assert grid["source"] == "detected"
    old = _detect_old_way(base)
    assert grid["xEdges"] == old["xEdges"]
    assert grid["yEdges"] == old["yEdges"]
    assert grid["pitch"] == old["pitch"]


def test_saved_manual_pitch_wins_over_detection(fixture_run_dir):
    """fit.pitch_manual 이 있으면 검출을 이기고 그 피치의 격자를 응답한다."""
    base = _write_base(fixture_run_dir)
    auto = _base_grid_response(fixture_run_dir, base)["grid"]
    _set_manual(fixture_run_dir, (10, 10))
    manual = _base_grid_response(fixture_run_dir, base)["grid"]
    assert manual["source"] == "manual"
    assert manual["pitch"] == [10.0, 10.0]
    assert manual["xEdges"] != auto["xEdges"]  # override 가 표시 격자를 실제로 바꿨다


def test_display_edges_equal_sampling_edges(fixture_run_dir):
    """표시 격자 = 샘플링 진실: 응답 절단선이 `_grid_edges`(추출/굽기가 쓰는 함수)와
    비트 동일해야 한다. base-edit 굽기도 같은 `_base_grid_response` 를 호출하므로,
    이 동일성이 표시·굽기·추출 3자 일치를 보증한다."""
    base = _write_base(fixture_run_dir)
    _set_manual(fixture_run_dir, (10, 10))
    grid = _base_grid_response(fixture_run_dir, base)["grid"]
    box0_x, box0_y = grid["xEdges"][0], grid["yEdges"][0]
    # 콘텐츠 폭/높이 = 마지막-처음 절단선. 그 폭에 pitch 10, 위상 0(블록정렬) 을 먹인
    # `_grid_edges` 가 응답과 같아야 한다.
    width = grid["xEdges"][-1] - box0_x
    height = grid["yEdges"][-1] - box0_y
    assert [box0_x + e for e in _grid_edges(width, 10.0, 0.0)] == grid["xEdges"]
    assert [box0_y + e for e in _grid_edges(height, 10.0, 0.0)] == grid["yEdges"]


def test_query_override_beats_saved_manual(fixture_run_dir):
    """라이브 프리뷰 쿼리 override 는 저장된 fit.pitch_manual 보다도 우선한다."""
    base = _write_base(fixture_run_dir)
    _set_manual(fixture_run_dir, (10, 10))
    preview = _base_grid_response(fixture_run_dir, base, override_pitch=(12.0, 12.0))["grid"]
    assert preview["pitch"] == [12.0, 12.0]
    # 프리뷰는 디스크를 바꾸지 않는다 — 저장값은 여전히 10.
    assert load_request(fixture_run_dir)["fit"]["pitch_manual"] == [10, 10]


def test_clear_manual_reverts_to_auto(fixture_run_dir):
    """fit.pitch_manual 을 비우면 자동 검출로 복귀한다 (source=detected)."""
    base = _write_base(fixture_run_dir)
    auto = _base_grid_response(fixture_run_dir, base)["grid"]
    _set_manual(fixture_run_dir, (10, 10))
    assert _base_grid_response(fixture_run_dir, base)["grid"]["source"] == "manual"
    _set_manual(fixture_run_dir, None)
    reverted = _base_grid_response(fixture_run_dir, base)["grid"]
    assert reverted["source"] == "detected"
    assert reverted["xEdges"] == auto["xEdges"]  # 자동 검출 격자로 정확히 복귀


def test_query_pitch_pair_rejects_garbage():
    """쓰레기 값은 None (다음 소스로 폴백) — 조용한 가짜 격자 금지."""
    assert _query_pitch_pair({"pitchX": ["36"], "pitchY": ["36"]}, "pitchX", "pitchY") == (36.0, 36.0)
    assert _query_pitch_pair({"pitchX": ["1"], "pitchY": ["36"]}, "pitchX", "pitchY") is None  # <2
    assert _query_pitch_pair({"pitchX": ["x"], "pitchY": ["36"]}, "pitchX", "pitchY") is None  # 비수치
    assert _query_pitch_pair({"pitchX": ["36"]}, "pitchX", "pitchY") is None  # 한 축 누락
    assert _query_pitch_pair({}, "pitchX", "pitchY") is None
    # phase 는 validate=False — 2 미만도 허용 (위상은 오프셋이라 크기 제약이 없다).
    assert _query_pitch_pair({"phaseX": ["0"], "phaseY": ["1"]}, "phaseX", "phaseY", validate=False) == (0.0, 1.0)
