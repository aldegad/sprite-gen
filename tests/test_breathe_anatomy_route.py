# SPDX-License-Identifier: Apache-2.0
"""`GET /api/breathe-anatomy` 의 기준 프레임 해소를 **실제로** 태운다.

이 라우트는 검출 SSoT 를 서버로 몬 설계의 유일한 진입점이다 — 큐레이터가 호흡을 켤 때,
경계를 드래그할 때, `auto` 로 되돌릴 때 전부 여기를 부른다. 그런데 도입(`a385c3f`)부터
**항상 HTTP 500** 이었고 7라운드를 살아남았다: `_breathe_source_frame` 이 `state_plan` 을
잘못된 인자 순서로 부르고 존재하지 않는 `request["cell_width"]` 를 읽었는데, 이 경로를
태우는 테스트가 하나도 없었기 때문이다 (슉슉이 2026-07-26).

미러 테스트들은 파이썬에서 만든 `freeze_anatomy` 결과를 JS 에 직접 먹인다 — **서버가 그
숫자를 만들어 낼 수 있는지**는 한 번도 묻지 않았다. 그 공백을 여기서 메운다.

추출 완료 상태의 최소 런을 합성한다(골든 픽스처는 추출 전이라 프레임 manifest 가 없다).
"""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# `scripts/` 만 넣는다 — `sprite_gen/` 을 sys.path 에 올리면 그 안의 `inspect.py` 가 표준
# `inspect` 를 가려 dataclass 정의가 죽는다. serve_curation 이 자기 경로는 알아서 잡는다.
sys.path.insert(0, str(ROOT / "scripts"))

CELL = 96


def _figure(cell=CELL):
    """셀 안에 목 병목이 있는 도형 — 해부가 성립해야 한다."""
    im = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    px = im.load()
    def block(x0, y0, x1, y1, rgb):
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[x, y] = (*rgb, 255)
    block(38, 14, 58, 34, (90, 60, 30))      # 머리
    block(44, 34, 52, 40, (90, 60, 30))      # 목 (병목)
    block(32, 40, 64, 82, (90, 60, 30))      # 몸통
    for x0 in (41, 51):                       # 눈 (좌우 대칭)
        block(x0, 20, x0 + 4, 26, (10, 10, 12))
    return im


@pytest.fixture()
def run_dir(tmp_path):
    """추출까지 끝난 최소 런 — sprite-request + frames manifest + 프레임 PNG."""
    run = tmp_path / "run"
    (run / "frames" / "idle").mkdir(parents=True)
    request = {
        "character": "fixture",
        "cell": {"shape": "square", "width": CELL, "height": CELL, "size": CELL,
                 "safe_margin_x": 8, "safe_margin_y": 8, "safe_margin": 8},
        "states": {"idle": {"frames": 4, "fps": 6, "loop": True}},
    }
    (run / "sprite-request.json").write_text(json.dumps(request), encoding="utf-8")
    files = []
    for i in range(4):
        rel = f"frames/idle/frame-{i}.png"
        img = _figure()
        if i:                                  # 프레임마다 조금씩 다르게 (깜빡임 흉내)
            p = img.load()
            for y in range(20 + i, 26):
                for x in range(41, 45):
                    p[x, y] = (90, 60, 30, 255)
        img.save(run / rel)
        files.append(rel)
    (run / "frames" / "frames-manifest.json").write_text(json.dumps({
        "ok": True, "errors": [], "warnings": [],
        "rows": [{"state": "idle", "frames": 4, "method": "components", "files": files,
                  "ok": True}]}), encoding="utf-8")
    return run


def test_the_route_helper_returns_a_usable_reference_frame(run_dir):
    """예외 없이 셀 크기의 RGBA 프레임이 나와야 한다."""
    import serve_curation
    frame, key = serve_curation._breathe_source_frame(run_dir, "idle")
    assert isinstance(frame, Image.Image), f"기준 프레임을 못 만든다 ({frame!r})"
    assert isinstance(key, str) and key.startswith("breathe-ref-v1|"), \
        f"기준 프레임 키를 같이 못 내놓는다 ({key!r}) — 웹뷰가 신선도를 못 본다"
    assert frame.mode == "RGBA"
    assert frame.size == (CELL, CELL), "굽기와 같은 셀 크기여야 한다"


def test_the_route_helper_feeds_freeze_anatomy(run_dir):
    """그 프레임으로 해부를 얼릴 수 있어야 한다 — 라우트가 돌려주는 값 그대로."""
    import serve_curation
    from sprite_gen.breathe import freeze_anatomy

    frame, key = serve_curation._breathe_source_frame(run_dir, "idle")
    frozen = freeze_anatomy(frame, {}, key)
    for key in ("rigid_row", "neck_row", "basis_row", "axis_x", "torso_half",
                "max_half", "width", "height", "fingerprint"):
        assert key in frozen, f"라우트 응답에 {key} 가 없다"
    assert 0 < frozen["rigid_row"] < frozen["height"]


def test_a_manual_rigid_row_is_honoured(run_dir):
    """`?rigid_row=` 경로 — 큐레이터의 경계 드래그가 쓰는 인자."""
    import serve_curation
    from sprite_gen.breathe import freeze_anatomy

    frame, key = serve_curation._breathe_source_frame(run_dir, "idle")
    auto = freeze_anatomy(frame, {}, key)
    want = min(auto["height"] - 2, auto["rigid_row"] + 5)
    manual = freeze_anatomy(frame, {"rigid_row": want}, key)
    assert manual["rigid_row"] == want and manual["rigid_source"] == "manual"


def test_the_reference_is_the_frame_the_bake_starts_from(run_dir):
    """서버가 얼리는 기준 프레임 = **굽기가 실제로 해부를 확정하는 프레임**이어야 한다.

    둘이 다르면 지문이 영구 불일치라 프리뷰·영상 내보내기가 계속 거부된다.

    첫 판은 `_breathe_source_frame` 을 두 번 불러 비교하는 **항등식**이었다 — 이름이
    단언하는 것을 한 번도 안 물었고 그 사유로는 절대 실패할 수 없었다 (슉슉이 note
    2026-07-26, round-6 N1 과 같은 클래스). 이제 실제로 `compose_gif` 를 돌려 manifest 가
    기록한 **해부 값**과 대조한다 — 굽기는 지문을 안 찍는다(캐시를 안 믿고 매번 다시
    잰다). 프리뷰가 지켜야 하는 것도 지문이 아니라 "굽기와 같은 경계로 그린다" 이다."""
    import serve_curation
    from sprite_gen import compose_gif
    from sprite_gen.breathe import freeze_anatomy

    # 사이드카에 호흡을 켜고 실제로 굽는다
    from sprite_gen.curation import stamp_curation
    curation = stamp_curation(run_dir, {
        "version": 1, "kind": "sprite-gen-curation",
        "states": {"idle": {"selected": [0, 1, 2, 3],
                            "breathe": {"depth": 0.06, "breaths": 1, "lag": 0.1}}}})
    (run_dir / "curation.json").write_text(json.dumps(curation), encoding="utf-8")
    out = run_dir / "gif"
    compose_gif.run(run_dir=run_dir, out_dir=out)
    manifest = json.loads((out / "gif-manifest.json").read_text(encoding="utf-8"))
    baked = next(e for e in manifest["exports"] if e["state"] == "idle")
    baked_anat = baked["breathe"]["resolved"]["anatomy"]

    frame, key = serve_curation._breathe_source_frame(run_dir, "idle")
    served = freeze_anatomy(frame, {}, key)
    drift = {k: [baked_anat[k], served[k]] for k in baked_anat
             if k in served and baked_anat[k] != served[k]}
    assert not drift, (
        "라우트가 얼리는 해부가 굽기가 쓰는 해부와 다르다 — 프리뷰가 굽기와 다른 "
        f"경계로 그린다: {drift}")


@pytest.mark.parametrize("state,why", [
    ("no-such-state", "manifest 에 줄이 없다"),
    ("pending", "request 에 선언됐지만 아직 추출 안 된 줄 (manifest row 없음)"),
])
def test_a_resolvable_frame_is_always_reported_the_same_way(run_dir, state, why):
    """기준 프레임을 못 만드는 **모든** 사유가 같은 모양으로 나와야 한다.

    핸들러는 `frame, key = _breathe_source_frame(...)` 로 받는다. 한 갈래만 맨 `None`
    이면 거기서 `TypeError: cannot unpack non-iterable NoneType` 이 나고, 의도한
    404("no extracted frame for state") 대신 그 문구로 **500** 이 나간다 — 추출이 아직
    안 끝난 줄에서 호흡을 켜면 도달한다 (슉슉이 실측 2026-07-26).

    앞선 판은 `is None` 만 봐서 반환 **모양**을 한 번도 안 물었다 (round-6 N1 ·
    round-9 N1 과 같은 클래스: 이름이 단언하는 것을 안 묻는 테스트)."""
    import serve_curation
    if state == "pending":
        # 사용자가 실제로 닿는 모양: request 에는 있는데 아직 안 구워진 줄이다
        # (`load_consistent_frames_manifest(allow_pending_states=True)` 가 허용하는 상태).
        # manifest 에 row 를 넣고 파일만 없애는 판은 로더가 먼저 loud fail 해서 도달 불가다.
        rp = run_dir / "sprite-request.json"
        req = json.loads(rp.read_text(encoding="utf-8"))
        req["states"]["pending"] = {"frames": 1, "fps": 6, "loop": True}
        rp.write_text(json.dumps(req), encoding="utf-8")

    got = serve_curation._breathe_source_frame(run_dir, state)
    frame, key = got                       # 핸들러와 **같은 모양**으로 받는다 ({why})
    assert frame is None and key is None, f"{why}: {got!r}"
