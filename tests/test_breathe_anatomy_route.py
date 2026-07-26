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
    import sprite_gen.serve_curation as serve_curation
    frame, key = serve_curation._breathe_source_frame(run_dir, "idle")
    assert isinstance(frame, Image.Image), f"기준 프레임을 못 만든다 ({frame!r})"
    assert isinstance(key, str) and key.startswith("breathe-ref-v1|"), \
        f"기준 프레임 키를 같이 못 내놓는다 ({key!r}) — 웹뷰가 신선도를 못 본다"
    assert frame.mode == "RGBA"
    assert frame.size == (CELL, CELL), "굽기와 같은 셀 크기여야 한다"


def test_the_route_helper_feeds_freeze_anatomy(run_dir):
    """그 프레임으로 해부를 얼릴 수 있어야 한다 — 라우트가 돌려주는 값 그대로."""
    import sprite_gen.serve_curation as serve_curation
    from sprite_gen.breathe import freeze_anatomy

    frame, key = serve_curation._breathe_source_frame(run_dir, "idle")
    frozen = freeze_anatomy(frame, {}, key)
    for key in ("rigid_row", "neck_row", "basis_row", "axis_x", "torso_half",
                "max_half", "width", "height", "fingerprint"):
        assert key in frozen, f"라우트 응답에 {key} 가 없다"
    assert 0 < frozen["rigid_row"] < frozen["height"]


def test_a_manual_rigid_row_is_honoured(run_dir):
    """`?rigid_row=` 경로 — 큐레이터의 경계 드래그가 쓰는 인자."""
    import sprite_gen.serve_curation as serve_curation
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
    import sprite_gen.serve_curation as serve_curation
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
    import sprite_gen.serve_curation as serve_curation
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


def test_the_reference_frame_is_measured_after_the_transform(run_dir):
    """해부는 **변형을 적용한 뒤**의 프레임에서 잰다.

    굽기는 `apply_pixel_edits` → `apply_transform` 뒤에 호흡을 얹으므로, 원본 프레임에서
    재면 라우트가 얼리는 경계와 굽기가 쓰는 경계가 다르다. 계약은 지켜지고 있었지만
    `_breathe_source_frame` 의 마지막 줄을 `return source, key`(변형 생략)로 되돌려도
    **전체 스위트 529 가 전부 통과했다** — 회귀하면 경계가 몇 행 어긋난 채 조용히 나간다
    (슉슉이 note 2026-07-26; round-8 이 "라우트 그물 0" 으로 기각한 그 자리다).

    변형이 해부를 **실제로 바꾸는** 값을 쓴다 — 안 그러면 단언이 공허하다."""
    import sprite_gen.serve_curation as serve_curation
    from sprite_gen.breathe import freeze_anatomy
    from sprite_gen.curation import stamp_curation

    plain_frame, plain_key = serve_curation._breathe_source_frame(run_dir, "idle")
    plain = freeze_anatomy(plain_frame, {}, plain_key)

    curation = {"version": 1, "kind": "sprite-gen-curation",
                # 호흡을 **켜둔다** — 안 켜면 manifest 에 `breathe` 가 없어 아래 굽기 대조
                # 절이 통째로 죽은 코드가 된다 (노을이 note 2026-07-26).
                "states": {"idle": {"selected": [0, 1, 2, 3],
                                    "breathe": {"depth": 0.06, "breaths": 1, "lag": 0.1},
                                    "transforms": {"0": {"rotate": 5.0, "scale": 1.05}}}}}
    (run_dir / "curation.json").write_text(
        json.dumps(stamp_curation(run_dir, curation)), encoding="utf-8")
    rot_frame, rot_key = serve_curation._breathe_source_frame(run_dir, "idle")
    rotated = freeze_anatomy(rot_frame, {}, rot_key)

    moved = {k for k in ("rigid_row", "axis_x", "height", "neck_row")
             if plain[k] != rotated[k]}
    assert moved, (
        "변형을 걸었는데 해부가 하나도 안 움직였다 — 이 픽스처로는 '변형 후에 잰다' 를 "
        f"보증할 수 없다 (plain={plain}, rotated={rotated})")

    # 그리고 그 값은 **굽기가 쓰는 값**과 같아야 한다
    from sprite_gen import compose_gif
    out = run_dir / "gif-rot"
    compose_gif.run(run_dir=run_dir, out_dir=out)
    manifest = json.loads((out / "gif-manifest.json").read_text(encoding="utf-8"))
    baked = next(e for e in manifest["exports"] if e["state"] == "idle")
    assert baked.get("breathe"), "굽기가 호흡을 안 실었다 — 이 대조 절이 죽은 코드가 된다"
    baked_anat = baked["breathe"]["resolved"]["anatomy"]
    drift = {k: [baked_anat[k], rotated[k]] for k in baked_anat
             if k in rotated and baked_anat[k] != rotated[k]}
    assert not drift, f"라우트가 변형 후 프레임을 안 쟀다 — 굽기와 어긋난다: {drift}"


def test_the_reference_frame_honours_every_axis_the_bake_uses(run_dir):
    """"굽기가 보는 것과 같은 픽셀" 은 **네 축 전부**다 — 변형만이 아니다.

    `_breathe_source_frame` 은 (1) 재생 순서로 기준 프레임을 고르고 (2) 픽셀 편집을 얹고
    (3) 변형을 걸고 (4) pp 런이면 격자로 재양자화한다. 앞선 그물은 (3) 만 태웠고, 나머지
    셋은 픽스처가 기본값이라 변이를 걸어도 **행동이 안 바뀌어** 통과했다 (노을이 note
    2026-07-26: `snap=None`, `pixel_ops={}`, `ordered==range(4)`).

    그래서 축마다 **결과를 실제로 바꾸는** 값을 준 뒤, 그 축을 무시하는 변이가 다른
    프레임을 만들어내는지 본다. "닫았다" 고 적은 계약이 한 축만 덮고 있던 것을 고친다."""
    import sprite_gen.serve_curation as serve_curation
    from sprite_gen.curation import stamp_curation

    # 재생 첫 슬롯을 2번 프레임으로 바꾸고(순서), 그 프레임에만 픽셀을 칠하고(편집),
    # 변형을 건다 — 셋 다 기준 프레임의 픽셀을 바꾼다.
    paint = {f"{x},{y}": "#ff00ff" for x in range(20, 34) for y in range(44, 58)}
    curation = {"version": 1, "kind": "sprite-gen-curation",
                "states": {"idle": {"selected": [2, 0, 1, 3],
                                    "breathe": {"depth": 0.06, "breaths": 1, "lag": 0.1},
                                    "pixels": {"2": paint},
                                    "transforms": {"2": {"rotate": 5.0, "scale": 1.05}}}}}
    (run_dir / "curation.json").write_text(
        json.dumps(stamp_curation(run_dir, curation)), encoding="utf-8")

    frame, key = serve_curation._breathe_source_frame(run_dir, "idle")
    assert frame is not None, "기준 프레임을 못 만든다"
    got = frame.tobytes()

    # 축 (1) 재생 순서: 물리 0번을 기준으로 삼으면 다른 그림이 나와야 한다
    plain = {"version": 1, "kind": "sprite-gen-curation",
             "states": {"idle": {"selected": [0, 1, 2, 3],
                                 "breathe": {"depth": 0.06, "breaths": 1, "lag": 0.1},
                                 "pixels": {"2": paint},
                                 "transforms": {"2": {"rotate": 5.0, "scale": 1.05}}}}}
    (run_dir / "curation.json").write_text(
        json.dumps(stamp_curation(run_dir, plain)), encoding="utf-8")
    other, _ = serve_curation._breathe_source_frame(run_dir, "idle")
    assert other.tobytes() != got, (
        "재생 순서를 바꿔도 같은 기준 프레임이 나온다 — 이 축이 그물 밖이다")

    # 축 (2) 픽셀 편집: 칠한 색이 기준 프레임에 실제로 있어야 한다
    (run_dir / "curation.json").write_text(
        json.dumps(stamp_curation(run_dir, curation)), encoding="utf-8")
    frame, _ = serve_curation._breathe_source_frame(run_dir, "idle")
    colors = {px[:3] for px in frame.convert("RGBA").getdata() if px[3] > 0}
    assert (255, 0, 255) in colors, (
        "픽셀 편집이 기준 프레임에 안 얹혔다 — 굽기는 얹고 굽는다")

    # 축 (4) 스냅은 별도 테스트 — sprite-request 를 도중에 고쳐 쓰면 `run_revision` 이
    # 바뀌어 큐레이션이 통째로 무효화되고, 그러면 "달라졌다" 가 **엉뚱한 이유로** 참이 된다
    # (실제로 그랬다: snap 을 None 으로 만든 변이에서도 통과했다).


def test_a_pixel_perfect_run_snaps_the_reference_frame_to_the_grid(tmp_path):
    """pp 런의 기준 프레임은 **격자에 물려** 있다 (`pixel_snap_scale` 재양자화).

    앞선 판은 테스트 도중 `sprite-request.json` 을 고쳐 써서 두 프레임을 비교했는데,
    그러면 `run_revision` 이 바뀌어 큐레이션이 무효화되고 "달라졌다" 가 스냅과 무관한
    이유로 참이 된다 — snap 을 None 으로 만든 변이에서도 통과했다. 그래서 비교가 아니라
    **속성**을 본다: 격자에 물린 그림은 k 로 줄였다 늘려도 자기 자신이다."""
    import sprite_gen.serve_curation as serve_curation
    from sprite_gen.curation import pixel_snap_scale, stamp_curation

    run = tmp_path / "run"
    (run / "frames" / "idle").mkdir(parents=True)
    request = {
        "character": {"id": "fixture", "name": "fixture"},
        "cell": {"shape": "square", "width": CELL, "height": CELL, "size": CELL,
                 "safe_margin_x": 8, "safe_margin_y": 8, "safe_margin": 8},
        "fit": {"pixel_unfake": True, "logical_height": CELL // 2},
        "states": {"idle": {"frames": 2, "fps": 6, "loop": True}},
    }
    (run / "sprite-request.json").write_text(json.dumps(request), encoding="utf-8")
    k = pixel_snap_scale(request)
    assert k and k > 1, f"픽스처가 스냅 배율을 안 만든다 (k={k}) — 이 테스트는 공허하다"
    files = []
    for i in range(2):
        rel = f"frames/idle/frame-{i}.png"
        _figure().save(run / rel)
        files.append(rel)
    (run / "frames" / "frames-manifest.json").write_text(json.dumps({
        "ok": True, "errors": [], "warnings": [],
        "rows": [{"state": "idle", "frames": 2, "method": "components", "files": files,
                  "ok": True}]}), encoding="utf-8")
    # 회전을 걸어 둔다 — 스냅이 없으면 BICUBIC 결과가 격자를 벗어난다
    curation = {"version": 1, "kind": "sprite-gen-curation",
                "states": {"idle": {"selected": [0, 1],
                                    "breathe": {"depth": 0.06, "breaths": 1, "lag": 0.1},
                                    "transforms": {"0": {"rotate": 5.0}}}}}
    (run / "curation.json").write_text(
        json.dumps(stamp_curation(run, curation)), encoding="utf-8")

    frame, _ = serve_curation._breathe_source_frame(run, "idle")
    assert frame is not None
    small = frame.resize((CELL // k, CELL // k), Image.NEAREST)
    regrid = small.resize((CELL, CELL), Image.NEAREST)
    assert regrid.tobytes() == frame.tobytes(), (
        f"기준 프레임이 k={k} 격자에 안 물려 있다 — 굽기는 물려서 굽는다"
        " (`apply_transform(snap_scale=...)`)")
