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

import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

from PIL import Image

from sprite_gen.frames.extract import (_grid_edges, detect_pixel_grid,
                                       remove_chroma_background_ycbcr,
                                       solid_alpha_bbox)
from sprite_gen.serve.serve_curation import (CurationHandler, _base_grid_response,
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


def _write_half_lead_base(run_dir, pitch=8, cells=6, margin=6, lead=4):
    """왼쪽·위쪽 첫 칸이 반 블록(lead)인 베이스. 콘텐츠 bbox 가 블록 경계가 아니라
    블록 **중간**에서 시작하므로, 그 피치의 최적 위상은 0 이 아니라 lead 다."""
    side = lead + cells * pitch
    img = Image.new("RGBA", (side + margin * 2, side + margin * 2), CHROMA + (255,))
    xs = [0] + [lead + i * pitch for i in range(cells + 1)]
    for by in range(len(xs) - 1):
        for bx in range(len(xs) - 1):
            color = _PALETTE[(bx + by * 2) % len(_PALETTE)] + (255,)
            for y in range(xs[by], xs[by + 1]):
                for x in range(xs[bx], xs[bx + 1]):
                    img.putpixel((margin + x, margin + y), color)
    path = run_dir / "base-source.png"
    img.save(path)
    return path


def test_manual_pitch_measures_phase_not_zero(fixture_run_dir):
    """수동 피치의 위상은 **실측**이다 — 0 으로 강제하면 격자가 통째로 밀린다.

    회귀 근거(수홍 발견 2026-08-08, 커서 베이스): 수동 피치일 때 위상을 0 으로 두던
    구현에서 실측 최적 위상이 x=18.25 y=24.44 였고, 그만큼 어긋나 "픽셀 끝단이 안
    맞는" 증상이 났다. 여기서는 첫 칸이 반 블록인 합성 베이스로 그 성질을 고정한다:
    위상을 재면 선행 칸이 lead 폭으로 잡히고, 0 으로 강제하면 그 칸이 사라진다.
    """
    base = _write_half_lead_base(fixture_run_dir, pitch=8, lead=4)
    grid = _base_grid_response(fixture_run_dir, base, override_pitch=(8.0, 8.0))["grid"]
    assert grid["source"] == "manual"
    lead_x = grid["xEdges"][1] - grid["xEdges"][0]
    lead_y = grid["yEdges"][1] - grid["yEdges"][0]
    assert lead_x == 4, f"선행 칸이 실측 위상(4)이어야 한다 — 위상 0 강제면 8 이 된다: {lead_x}"
    assert lead_y == 4, f"세로도 마찬가지: {lead_y}"


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


def _serve(run):
    CurationHandler.run_dir = run
    CurationHandler.lang = "en"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(CurationHandler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as res:
        return json.loads(res.read())


def _post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return res.status, json.loads(res.read())


def test_route_base_grid_query_override(fixture_run_dir):
    """GET /api/base-grid?pitchX&pitchY 가 그 피치의 격자를 응답한다 (라우트 e2e)."""
    _write_base(fixture_run_dir)
    srv = _serve(fixture_run_dir)
    port = srv.server_address[1]
    try:
        grid = _get(port, "/api/base-grid?pitchX=12&pitchY=12")["grid"]
        assert grid["source"] == "manual"
        assert grid["pitch"] == [12.0, 12.0]
        # 쓰레기 값은 폴백 — 자동 검출로.
        assert _get(port, "/api/base-grid?pitchX=1&pitchY=12")["grid"]["source"] == "detected"
    finally:
        srv.shutdown()


def test_route_base_pitch_save_and_clear(fixture_run_dir):
    """POST /api/base-pitch 가 fit.pitch_manual 을 켜고(저장) 끈다(비움→자동 복귀)."""
    _write_base(fixture_run_dir)
    srv = _serve(fixture_run_dir)
    port = srv.server_address[1]
    try:
        status, data = _post(port, "/api/base-pitch", {"pitchX": 14, "pitchY": 14})
        assert status == 200 and data["pitch_manual"] == [14.0, 14.0]
        assert load_request(fixture_run_dir)["fit"]["pitch_manual"] == [14.0, 14.0]
        # 저장된 값이 표시 격자를 지배한다 (override 쿼리 없이도).
        assert _get(port, "/api/base-grid")["grid"]["pitch"] == [14.0, 14.0]
        # 비움 저장 → 키 제거 → 자동 복귀.
        _post(port, "/api/base-pitch", {})
        assert "pitch_manual" not in (load_request(fixture_run_dir).get("fit") or {})
        assert _get(port, "/api/base-grid")["grid"]["source"] == "detected"
        # <2 는 400 거부 (조용한 쓰레기 저장 금지).
        try:
            _post(port, "/api/base-pitch", {"pitchX": 1, "pitchY": 8})
            assert False, "expected 400"
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        srv.shutdown()


def _red_pixels(path):
    with Image.open(path) as im:
        px = im.convert("RGBA").getdata()
    return sum(1 for r, g, b, a in px if a > 128 and r > 180 and g < 80 and b < 80)


def test_route_base_edit_logical_honors_preview_pitch(fixture_run_dir):
    """base-edit(space=logical)가 프리뷰 override 피치를 받으면 화면과 같은 격자로
    굽는다 — 표시 격자 = 샘플링 진실. 큰 피치 셀이 작은 검출 셀보다 넓게 칠해진다."""
    base = _write_base(fixture_run_dir)  # 검출 피치 ~8px
    srv = _serve(fixture_run_dir)
    port = srv.server_address[1]
    try:
        # 논리 (1,1) 을 칠한다 — (0,0) 은 위상 실측이 만드는 **선행 부분셀**일 수 있어
        # 두 격자에서 우연히 같은 크기가 나온다(실측 2026-08-08). 안쪽 셀은 온전한
        # 피치 크기라 격자 차이가 그대로 면적 차이로 드러난다.
        status, data = _post(port, "/api/base-edit",
                             {"ops": {"1,1": "#ff0000"}, "space": "logical"})
        assert status == 200 and data.get("ok")
        detected_red = _red_pixels(base)
        # 원본 복원 후 프리뷰 override 16px 로 같은 논리 셀 → ~16px 셀 (더 넓다).
        import shutil
        shutil.copyfile(base.with_name(base.name + ".orig"), base)
        status, data = _post(port, "/api/base-edit",
                             {"ops": {"1,1": "#ff0000"}, "space": "logical",
                              "pitchX": 16, "pitchY": 16})
        assert status == 200 and data.get("ok")
        override_red = _red_pixels(base)
        # override 를 무시했다면 두 값이 같다(둘 다 검출 격자). 더 넓다 = override 격자로 구웠다.
        assert override_red > detected_red, (
            f"override 16px 셀({override_red})이 검출 ~8px 셀({detected_red})보다 넓어야 한다")
    finally:
        srv.shutdown()


def test_query_pitch_pair_rejects_garbage():
    """쓰레기 값은 None (다음 소스로 폴백) — 조용한 가짜 격자 금지."""
    assert _query_pitch_pair({"pitchX": ["36"], "pitchY": ["36"]}, "pitchX", "pitchY") == (36.0, 36.0)
    assert _query_pitch_pair({"pitchX": ["1"], "pitchY": ["36"]}, "pitchX", "pitchY") is None  # <2
    assert _query_pitch_pair({"pitchX": ["x"], "pitchY": ["36"]}, "pitchX", "pitchY") is None  # 비수치
    assert _query_pitch_pair({"pitchX": ["36"]}, "pitchX", "pitchY") is None  # 한 축 누락
    assert _query_pitch_pair({}, "pitchX", "pitchY") is None
    # phase 는 validate=False — 2 미만도 허용 (위상은 오프셋이라 크기 제약이 없다).
    assert _query_pitch_pair({"phaseX": ["0"], "phaseY": ["1"]}, "phaseX", "phaseY", validate=False) == (0.0, 1.0)


def test_hand_placed_edges_win_and_survive_roundtrip(fixture_run_dir):
    """사람이 선 단위로 잡은 격자(`fit.base_grid_manual`)가 피치·검출을 모두 이긴다.

    이 축이 따로 있는 이유: 균일 피치로는 **칸마다 폭이 다른 격자**를 담을 수 없다.
    AI 생성물은 블록이 고르지 않아 사람이 한 줄씩 맞추게 되는데, 그 결과를 평균 피치로
    접으면 맞춰 놓은 불균일이 사라진다 (수홍 2026-08-08 "손으로 한줄한줄").
    """
    base = _write_base(fixture_run_dir)
    srv = _serve(fixture_run_dir)
    port = srv.server_address[1]
    try:
        auto = _get(port, "/api/base-grid")["grid"]
        # 일부러 **불균일**한 절단선 — 균일 피치로는 표현 불가능한 격자.
        x_edges = [10, 18, 40, 47, 80]
        y_edges = [12, 30, 33, 70]
        status, data = _post(port, "/api/base-grid-edges", {"x": x_edges, "y": y_edges})
        assert status == 200 and data["ok"]
        grid = _get(port, "/api/base-grid")["grid"]
        assert grid["source"] == "edges"
        assert grid["xEdges"] == x_edges, "사람이 잡은 절단선이 그대로 나와야 한다"
        assert grid["yEdges"] == y_edges
        assert grid["xEdges"] != auto["xEdges"]
        # 저장된 피치가 있어도 선 단위 격자가 이긴다.
        _post(port, "/api/base-pitch", {"pitchX": 9, "pitchY": 9})
        assert _get(port, "/api/base-grid")["grid"]["xEdges"] == x_edges
        # 비우면 피치/검출 경로로 돌아간다.
        _post(port, "/api/base-grid-edges", {})
        assert _get(port, "/api/base-grid")["grid"]["source"] != "edges"
        # 쓰레기(내림차순·1개)는 400 — 조용히 고치지 않는다.
        for bad in ({"x": [5, 3], "y": y_edges}, {"x": [5], "y": y_edges}):
            try:
                _post(port, "/api/base-grid-edges", bad)
                assert False, f"expected 400 for {bad}"
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
    finally:
        srv.shutdown()


def _extract_run(fixture_run_dir):
    """픽셀 프레임이 실제로 뽑힌 런 — 프레임 격자 계약을 프레임 위에서 검사하려면 필요."""
    from conftest import run_script
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir)).returncode == 0
    return fixture_run_dir


def test_frame_grid_saves_per_frame_and_rebakes(fixture_run_dir):
    """프레임별 격자는 프레임별로 저장되고, 저장이 그 프레임을 다시 굽는다.

    수홍 2026-08-08 "일단 저장되게 해줘. 그 상태에서 언페이크 누르면 그 격자에 맞게
    깨끗하게". 백업은 **프레임 트리 밖**에 둔다 — 안에 두면 정식 프레임으로 세어져
    매니페스트 무결성 검사가 깨진다(실측으로 잡힌 결함).
    """
    run = _extract_run(fixture_run_dir)
    state = next(iter(load_request(run)["states"]))
    srv = _serve(run)
    port = srv.server_address[1]
    try:
        before = (run / "frames" / state / "frame-0.png").read_bytes()
        # 저장 전에는 후보(또는 검출 실패)다 — 어느 쪽이든 "저장된 격자" 는 아니다.
        pre = _get(port, f"/api/frame-grid?state={state}&index=0")["grid"]
        assert pre is None or pre["candidate"] is True
        # 절단선은 트윈 픽셀 좌표다. 크기는 파일에서 직접 읽는다(검출에 의존하지 않게).
        from sprite_gen.serve.serve_curation import _frame_plain_path  # noqa: PLC0415
        with Image.open(_frame_plain_path(run, state, 0)) as opened:
            w, h = opened.size
        x_edges = [1, w // 3, (2 * w) // 3, w - 1]
        y_edges = [1, h // 3, (2 * h) // 3, h - 1]
        status, data = _post(port, "/api/frame-grid-edges",
                             {"state": state, "index": 0, "x": x_edges, "y": y_edges})
        assert status == 200 and data["ok"]
        assert data["rebaked"]["logical"] == [3, 3], "잡은 절단선대로 3x3 논리로 잘려야 한다"
        # 프레임별 SSoT 에 그 프레임만 담긴다.
        table = load_request(run)["fit"]["frame_grid_manual"]
        assert list(table[state].keys()) == ["0"]
        # 저장된 격자가 응답을 지배하고, 더는 후보가 아니다.
        saved = _get(port, f"/api/frame-grid?state={state}&index=0")["grid"]
        assert saved["source"] == "edges" and saved["candidate"] is False
        assert saved["xEdges"] == x_edges
        # 언페이크 프레임이 실제로 다시 구워졌다.
        assert (run / "frames" / state / "frame-0.png").read_bytes() != before
        # 백업은 프레임 트리 밖 — 정식 프레임 개수를 오염시키지 않는다.
        leaked = sorted(p.name for p in (run / "frames" / state).glob("*pregrid*"))
        assert not leaked, f"백업이 프레임 트리에 샜다 — 매니페스트 무결성이 깨진다: {leaked}"
        assert (run / ".pregrid" / state / "frame-0.png").is_file()
        # 비우면 격자도 이미지도 원래대로.
        status, data = _post(port, "/api/frame-grid-edges", {"state": state, "index": 0})
        assert status == 200 and data["rebaked"]["restored"] is True
        assert "frame_grid_manual" not in (load_request(run).get("fit") or {})
        assert (run / "frames" / state / "frame-0.png").read_bytes() == before
        post = _get(port, f"/api/frame-grid?state={state}&index=0")["grid"]
        assert post is None or post["candidate"] is True
    finally:
        srv.shutdown()


def test_rebake_keeps_every_logical_column(fixture_run_dir):
    """다시 구운 프레임은 논리 칸을 **하나도 빠뜨리지 않는다**.

    수홍 실측 2026-08-08: "세로줄 하나가 통째로 빠져버린다 — 왼쪽부터 6번째 칸".
    원인은 논리→트윈 확대 뒤 트윈→셀 축소라는 2단 리사이즈였다. NEAREST 축소는 좁은
    칸을 표본에서 그냥 지나칠 수 있어 칸이 사라진다. 논리에서 목표 발자국으로 **한 번만**
    확대하면 구조적으로 빠질 수 없다. 여기서는 칸마다 색이 다른 트윈을 만들어 굽고,
    결과에 그 색이 전부 남아 있는지로 고정한다.
    """
    from sprite_gen.serve.serve_curation import (_frame_plain_path,  # noqa: PLC0415
                                                 _rebake_frame_from_edges)
    run = _extract_run(fixture_run_dir)
    state = next(iter(load_request(run)["states"]))
    twin_path = _frame_plain_path(run, state, 0)
    with Image.open(twin_path) as opened:
        w, h = opened.size
    # 폭이 제각각인 9칸 — 좁은 칸이 축소 표본에서 빠지던 바로 그 형태.
    x_edges = [0, w // 40, w // 16, w // 8, w // 5, w // 3, w // 2, (2 * w) // 3, (5 * w) // 6, w]
    y_edges = [0, h // 2, h]
    painted = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    for i in range(len(x_edges) - 1):
        for x in range(x_edges[i], x_edges[i + 1]):
            for y in range(h):
                painted.putpixel((x, y), (10 + i * 25, 40, 60, 255))
    painted.save(twin_path)
    result = _rebake_frame_from_edges(run, state, 0, twin_path, x_edges, y_edges)
    assert "error" not in result, result
    with Image.open(run / "frames" / state / "frame-0.png") as opened:
        baked = opened.convert("RGBA")
    reds = {px[0] for px in baked.getdata() if px[3] > 0}
    expected = {10 + i * 25 for i in range(len(x_edges) - 1)}
    assert expected <= reds, f"논리 칸이 결과에서 사라졌다 — 빠진 칸: {sorted(expected - reds)}"


def test_rebake_logical_pixels_are_uniform(fixture_run_dir):
    """다시 구운 프레임의 논리 픽셀은 **균일**하다 — 칸 하나 = 출력 픽셀 하나(정수 배율).

    수홍 실측 2026-08-08: "가로가 23칸이었는데 왜 언페이크 누르면 22칸이 되냐". 굽기가
    논리 폭을 트윈 발자국으로 늘려 칸당 1.91px 이 됐고, 어떤 칸은 1px 어떤 칸은 2px 라
    얇은 칸이 이웃과 붙어 보였다. 배치는 추출과 같은 `fit_to_cell` 이 해야 하고, 그러면
    콘텐츠 크기가 논리 크기의 정수배로 떨어진다.
    """
    from sprite_gen.serve.serve_curation import (_frame_plain_path,  # noqa: PLC0415
                                                 _rebake_frame_from_edges)
    run = _extract_run(fixture_run_dir)
    state = next(iter(load_request(run)["states"]))
    twin_path = _frame_plain_path(run, state, 0)
    with Image.open(twin_path) as opened:
        w, h = opened.size
    cols, rows = 7, 9
    x_edges = [round(i * w / cols) for i in range(cols + 1)]
    y_edges = [round(i * h / rows) for i in range(rows + 1)]
    # 모든 칸이 불투명해야 칸이 투명으로 떨어지지 않는다(그건 별개 규칙).
    Image.new("RGBA", (w, h), (200, 40, 40, 255)).save(twin_path)
    assert "error" not in _rebake_frame_from_edges(run, state, 0, twin_path, x_edges, y_edges)
    with Image.open(run / "frames" / state / "frame-0.png") as opened:
        box = opened.convert("RGBA").getbbox()
    width, height = box[2] - box[0], box[3] - box[1]
    assert width % cols == 0 and height % rows == 0, (
        f"논리 픽셀이 균일하지 않다 — {width}x{height} 안에 {cols}x{rows} 칸이면 "
        f"칸마다 폭이 달라진다")
