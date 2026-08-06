# SPDX-License-Identifier: Apache-2.0
"""기준 프레임 키 — 파이썬과 JS 가 **같은 문자열**을 내는가 (그리고 웹뷰가 그 재료를
어디서 긁어오는가).

## 왜 이 파일이 따로 있나

호흡 지문은 원래 *변형 결과 RGBA* 의 해시였다. 그런데 굽기는 `apply_transform` 에서
BICUBIC 으로 리샘플하고(`snap_scale` 없는 런 = 기본 런 전부) 웹뷰 캔버스는
`imageSmoothingEnabled=false`, 즉 NEAREST 다. 같은 원본에 같은 변형을 걸어도 두 쪽이
만드는 그림이 다르니 지문은 **영구 불일치**였고, 회전·확대가 걸린 줄은 프리뷰가 영원히
원본으로 떨어지고 영상 내보내기가 영구 차단됐다 (슉슉이 실측 2026-07-26: rotate 3° 555px
상이, 정수 이동만 걸린 줄은 우연히 일치).

10라운드를 살아남은 이유가 구조적이다: 기존 미러 테스트는 **같은 픽셀 버퍼**를 양쪽에
먹여서 "웹뷰가 서버가 지문 찍은 그 버퍼를 만들어 낼 수 있는가" 를 한 번도 묻지 않았다.
그래서 이 파일은 픽셀을 아예 안 쓰고 **입력에서 키가 나오는 경로**만 본다.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sprite_gen.effects.breathe import anatomy_fingerprint, reference_key  # noqa: E402
from sprite_gen.serve.serve_curation import CURATOR_DIR  # noqa: E402

SRC = CURATOR_DIR / "src"

pytestmark = pytest.mark.skipif(
    not __import__("shutil").which("node"), reason="node 없음 — 미러를 못 돌린다")


def _node(script: str, payload: dict):
    with tempfile.TemporaryDirectory() as tmp:
        pj = Path(tmp) / "payload.json"
        pj.write_text(json.dumps(payload), encoding="utf-8")
        run = subprocess.run(["node", "-e", script, str(pj)],
                             capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, f"node 실패:\n{run.stderr}"
    return json.loads(run.stdout)


KEY_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const payload = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const sandbox = { console, TextEncoder, entries: {}, run: {}, setStatus: () => {},
                  document: { createElement: () => ({ getContext: () => ({}) }) } };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(payload.script, "utf8"), sandbox);
const out = payload.cases.map((c) => ({
  key: sandbox.breatheReferenceKeyOf(c),
  fingerprint: sandbox.breatheFingerprint(sandbox.breatheReferenceKeyOf(c)),
}));
process.stdout.write(JSON.stringify(out));
"""

# 값 **모양**의 전수 — 지적당한 한 칸만 메우는 방식이 열 라운드 동안 실패했다.
# 변형 없음(대부분의 프레임)·항등 명시·회전·축소·음수 이동·거울·문자열 수·소수 경계,
# 픽셀편집 없음/지움(null)/여러 점(정렬 순서 뒤섞임)까지 한 번에 건다.
CASES = [
    {"variant": "plain", "transform": None, "pixel_ops": None},
    {"variant": "plain", "transform": {}, "pixel_ops": {}},
    {"variant": "pixel", "transform": {"rotate": 0.0, "scale": 1.0, "dx": 0, "dy": 0,
                                       "shx": 0.0, "shy": 0.0, "flipX": 0}, "pixel_ops": None},
    {"variant": "plain", "transform": {"rotate": 3.0}, "pixel_ops": None},
    {"variant": "plain", "transform": {"scale": 0.9}, "pixel_ops": None},
    {"variant": "pixel", "transform": {"dx": -7, "dy": 12}, "pixel_ops": None},
    {"variant": "pixel", "transform": {"flipX": True}, "pixel_ops": None},
    {"variant": "plain", "transform": {"rotate": "3"}, "pixel_ops": None},
    {"variant": "plain", "transform": {"scale": 1.0000005}, "pixel_ops": None},
    {"variant": "plain", "transform": {"rotate": -0.5, "shx": 0.25}, "pixel_ops": None},
    {"variant": "plain", "transform": None, "pixel_ops": {"3,4": "#ff0000"}},
    {"variant": "plain", "transform": None, "pixel_ops": {"10,2": None}},
    {"variant": "pixel", "transform": None,
     "pixel_ops": {"9,9": "#00ff00", "1,1": "#0000ff", "10,0": None}},
    {"variant": "pixel", "transform": {"rotate": 3.0}, "pixel_ops": {"2,2": "#123456"}},
]


def _py(case, *, state="idle", request_stamp="120:5", source_index=0, source_stamp="900:7"):
    return reference_key(state=state, variant=case["variant"], request_stamp=request_stamp,
                         source_index=source_index, source_stamp=source_stamp,
                         pixel_ops=case["pixel_ops"], transform=case["transform"])


def _js_case(case, *, state="idle", request_stamp="120:5", source_index=0, source_stamp="900:7"):
    return {"state": state, "variant": case["variant"], "requestStamp": request_stamp,
            "sourceIndex": source_index, "sourceStamp": source_stamp,
            "pixelOps": case["pixel_ops"], "transform": case["transform"]}


def test_python_and_js_build_the_same_key_for_every_value_shape():
    got = _node(KEY_HARNESS, {"script": str(SRC / "breathe.js"),
                              "cases": [_js_case(c) for c in CASES]})
    assert len(got) == len(CASES)
    for case, js in zip(CASES, got):
        py = _py(case)
        assert js["key"] == py, (
            f"키가 갈렸다 — 변형 {case['transform']!r} / 픽셀 {case['pixel_ops']!r}\n"
            f"  py: {py}\n  js: {js['key']}")
        assert js["fingerprint"] == anatomy_fingerprint(py), "같은 키인데 지문이 갈렸다"


def test_every_ingredient_changes_the_key():
    """재료 하나를 건드리면 키가 **반드시** 달라진다.

    안 달라지는 재료가 있으면 그건 신선도 판정에서 빠진 것이다 — 사용자가 그걸 바꿔도
    프리뷰가 낡은 해부로 계속 그린다."""
    base = {"variant": "plain", "transform": {"rotate": 3.0}, "pixel_ops": {"2,2": "#123456"}}
    ref = _py(base)
    variants = {
        "state": _py(base, state="walk"),
        "variant": _py({**base, "variant": "pixel"}),
        "request_stamp": _py(base, request_stamp="121:5"),
        "source_index": _py(base, source_index=1),
        "source_stamp": _py(base, source_stamp="900:8"),
        "transform": _py({**base, "transform": {"rotate": 3.000001}}),
        "pixel_ops": _py({**base, "pixel_ops": {"2,2": "#123457"}}),
    }
    for name, key in variants.items():
        assert key != ref, f"{name} 을 바꿨는데 키가 그대로다 — 신선도 판정 밖에 있다"


def test_the_key_never_reads_pixels():
    """키 계산에 이미지가 **필요 없다**는 것이 이 설계의 요점이다.

    이 단언이 무너지면(키 함수가 프레임을 받게 되면) 리샘플러 차이가 다시 식에 들어온다."""
    import inspect

    from sprite_gen.effects import breathe
    sig = inspect.signature(breathe.reference_key)
    assert "frame" not in sig.parameters and "image" not in sig.parameters, \
        "reference_key 가 프레임을 받는다 — BICUBIC/NEAREST 불일치가 되돌아온다"
    js = (SRC / "breathe.js").read_text(encoding="utf-8")
    body = js[js.index("function breatheReferenceKeyOf"):]
    body = body[:body.index("\nfunction ", 1)]
    for banned in ("getImageData", "getContext", "canvas", "drawImage"):
        assert banned not in body, f"JS 키 빌더가 {banned} 를 쓴다 — 픽셀을 읽으면 안 된다"


# ── 웹뷰가 서버가 쓴 그 키를 만들어 낼 수 있는가 ──────────────────────

from tests.effects.test_breathe_anatomy_route import CELL, run_dir  # noqa: E402,F401

STORE_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const payload = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const sandbox = {
  console, TextEncoder, Set, Map, setTimeout,
  document: { createElement: () => ({ getContext: () => ({}), style: {} }),
              addEventListener() {}, querySelectorAll: () => [] },
  window: {}, fetch: () => {}, setStatus() {}, t: () => "", STR: {}, lang: "ko",
  scheduleSave() {}, flushSave: async () => {}, rebuildState() {},
  URL: { createObjectURL() {}, revokeObjectURL() {} },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of payload.scripts) vm.runInContext(fs.readFileSync(f, "utf8"), sandbox);

// 서버 페이로드를 웹뷰 상태로 — store.js 가 읽는 필드만.
//
// `let run`/`let entries` 는 컨텍스트의 **렉시컬 바인딩**이라 sandbox 객체에 대입해도
// 스크립트가 못 본다. 컨텍스트 안에서 대입해야 한다.
sandbox.__payload = payload;
vm.runInContext(`
  run = __payload.run;
  entries = {};
  // **손으로 unfakeStates 를 먹이지 않는다.** 웹뷰가 서버 페이로드에서 변종을 스스로
  // 골라내는지가 이 하네스의 요점이다 — 시드를 밖에 두면 그 질문을 영영 못 묻는다
  // (슉슉이 실측 2026-07-26: 변종 불일치가 그 공백으로 한 라운드를 살아남았다).
  seedUnfakeState(__payload.run);
  for (const [name, v] of Object.entries(__payload.ppOverride || {})) unfakeStates[name] = v;
  for (const [name, e] of Object.entries(__payload.entries)) {
    entries[name] = { order: e.order, sel: new Set(e.sel), transforms: e.transforms || {},
                      pixels: e.pixels || {}, clones: e.clones || {},
                      unlinked: new Set(e.unlinked || []) };
  }
  __out = {};
  for (const q of __payload.queries) {
    if (q.kind === "key") __out[q.id] = breatheReferenceKey(q.state);
    if (q.kind === "geometry") __out[q.id] = breatheGeometryFrame(q.state, q.fallback);
    if (q.kind === "variant") __out[q.id] = bakeVariant(q.state);
  }
`, sandbox);
process.stdout.write(JSON.stringify(sandbox.__out));
"""


def _webview(run_payload, entries, queries, pp_override=None):
    """웹뷰를 서버 페이로드로 부팅해 질의한다 — `unfakeStates` 는 `seedUnfakeState` 가 짓는다.

    `pp_override` 는 사용자가 화면에서 토글을 누른 상태를 흉내낼 때만 쓴다."""
    return _node(STORE_HARNESS, {
        "scripts": [str(SRC / "breathe.js"), str(SRC / "store.js")],
        "run": run_payload, "entries": entries, "ppOverride": pp_override or {},
        "queries": queries})


@pytest.fixture()
def web_run(run_dir):
    """`/api/run` 페이로드까지 조립되는 런 — 라우트 픽스처의 `character` 는 문자열이라
    페이로드 빌더(`request["character"]["id"]`)가 죽는다."""
    req = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    req["character"] = {"id": "fixture", "name": "fixture"}
    (run_dir / "sprite-request.json").write_text(json.dumps(req), encoding="utf-8")
    return run_dir


def _write_curation(run_dir, curation):
    """세대 도장까지 찍어 저장 — 안 찍으면 로더가 "프레임 재생성됨" 으로 보고 그 줄을
    통째로 드롭한다 (그러면 pixel_perfect 설정도 같이 사라져 변종이 되돌아간다)."""
    from sprite_gen.curate.curation import stamp_curation
    (run_dir / "curation.json").write_text(
        json.dumps(stamp_curation(run_dir, curation)), encoding="utf-8")


def _server_payload(run_dir):
    import sprite_gen.serve.serve_curation as serve_curation
    return serve_curation._build_run_state_impl(run_dir)


def test_the_webview_builds_the_same_key_the_route_used(web_run):
    """**이 파일의 핵심 단언.**

    미러 테스트들은 같은 픽셀 버퍼를 양쪽에 먹여서 "웹뷰가 서버가 지문 찍은 그 프레임에
    도달할 수 있는가" 를 묻지 않았다 — 그래서 리샘플러 불일치가 10라운드를 살아남았다.
    여기서는 서버가 실제로 응답에 실어 보낸 페이로드만으로 웹뷰가 키를 만들게 하고,
    라우트가 해부를 얼릴 때 쓴 키와 **문자열 동일**한지 본다."""
    import sprite_gen.serve.serve_curation as serve_curation
    _, route_key = serve_curation._breathe_source_frame(web_run, "idle")
    payload = _server_payload(web_run)
    got = _webview(payload,
                   {"idle": {"order": [0, 1, 2, 3], "sel": [0, 1, 2, 3]}},
                   [{"id": "k", "kind": "key", "state": "idle"}])
    assert got["k"] == route_key, (
        "웹뷰가 라우트와 다른 키를 만든다 — 프리뷰가 영원히 원본으로 떨어진다.\n"
        f"  라우트: {route_key}\n  웹뷰  : {got['k']}")


def test_the_key_follows_the_sidecar_the_user_actually_edited(web_run):
    """사용자가 변형·픽셀편집을 바꾸면 웹뷰 키가 **따라 움직인다.**

    안 움직이면 낡은 해부로 계속 그린다 — 지문의 존재 이유 자체가 사라진다."""
    payload = _server_payload(web_run)
    base = {"idle": {"order": [0, 1, 2, 3], "sel": [0, 1, 2, 3]}}
    q = [{"id": "k", "kind": "key", "state": "idle"}]
    plain = _webview(payload, base, q)["k"]
    rotated = _webview(payload, {"idle": {**base["idle"], "transforms": {"0": {"rotate": 3.0}}}}, q)["k"]
    painted = _webview(payload, {"idle": {**base["idle"], "pixels": {"0": {"3,4": "#ff0000"}}}}, q)["k"]
    reordered = _webview(payload, {"idle": {"order": [2, 0, 1, 3], "sel": [0, 1, 2, 3]}}, q)["k"]
    assert plain and rotated != plain, "회전을 걸었는데 키가 그대로다"
    assert painted != plain, "픽셀을 칠했는데 키가 그대로다"
    assert reordered != plain, "재생 순서를 바꿔 기준 프레임이 달라졌는데 키가 그대로다"


def test_a_missing_stamp_refuses_instead_of_guessing(web_run):
    """스탬프가 없으면 **키를 만들지 않는다** (null).

    없는 재료를 기본값으로 메우면 서로 다른 프레임이 같은 키를 갖고, 그때 신선도 검사는
    통과하면서 그림은 갈린다 — 조용한 오답이 시끄러운 거부보다 나쁘다."""
    payload = _server_payload(web_run)
    for st in payload["states"]:
        for fr in st.get("frames", []):
            fr.pop("stamp", None)
            fr.pop("plainBakeStamp", None)
    got = _webview(payload, {"idle": {"order": [0, 1, 2, 3], "sel": [0, 1, 2, 3]}},
                   [{"id": "k", "kind": "key", "state": "idle"}])
    assert got["k"] is None, f"스탬프가 없는데 키를 만들어냈다: {got['k']!r}"


def test_geometry_measures_the_reference_frame_not_the_opened_one(web_run):
    """경계 지오메트리의 원점 = 재생 첫 슬롯. 모달이 열린 프레임이 아니다.

    `rigid_row` 는 기준 프레임의 콘텐츠 행 인덱스라, 열린 프레임을 재면 선의 화면
    위치와 저장되는 행 번호가 굽기와 어긋난다 — 재생 순서만 바꿔도 도달하고 골든
    픽스처에서 8px(50px 캐릭터의 16%) 어긋났다 (슉슉이 실측 2026-07-26).
    round-10 은 이 결함을 고쳤지만 **그물이 없어** 되돌려도 전체 스위트가 통과했다."""
    payload = _server_payload(web_run)
    got = _webview(payload, {"idle": {"order": [2, 0, 1, 3], "sel": [0, 1, 2, 3]}},
                   [{"id": "g", "kind": "geometry", "state": "idle", "fallback": 3}])
    assert got["g"]["index"] == 2, (
        f"지오메트리가 재생 첫 슬롯(2)이 아니라 {got['g']['index']} 를 잰다 — "
        "열린 프레임을 재면 경계가 굽기와 어긋난다")
    empty = _webview(payload, {"idle": {"order": [0, 1, 2, 3], "sel": []}},
                     [{"id": "g", "kind": "geometry", "state": "idle", "fallback": 3}])
    assert empty["g"]["index"] == 3, "재생목록이 비면 열린 프레임으로 떨어져야 한다"


@pytest.fixture()
def web_run_plain(web_run):
    """pp OFF 줄 — `.plain.png` 쌍둥이를 굽기가 읽는다.

    R1 이 실제로 터진 경로다: 굽기는 `snap_scale` 이 없어 **BICUBIC** 으로 리샘플하고
    웹뷰는 NEAREST 라, 결과 픽셀을 해시하던 옛 지문이 여기서 영구 불일치였다."""
    from PIL import Image
    for i in range(4):
        src = web_run / f"frames/idle/frame-{i}.png"
        with Image.open(src) as im:
            im.convert("RGBA").resize((CELL, CELL), Image.NEAREST).save(
                src.with_suffix("").with_suffix(".plain.png"))
    from sprite_gen.curate.curation import empty_curation
    curation = empty_curation()
    curation["states"]["idle"] = {"pixel_unfake": False}
    _write_curation(web_run, curation)
    return web_run


def test_the_webview_agrees_on_the_plain_variant_too(web_run_plain):
    """pp OFF 줄에서도 웹뷰 키 == 라우트 키.

    변종이 갈리면 서로 다른 **파일**을 기준으로 삼는다 — 이 줄이 정확히 그 경로다."""
    import sprite_gen.serve.serve_curation as serve_curation
    _, route_key = serve_curation._breathe_source_frame(web_run_plain, "idle")
    assert "|plain|" in route_key, f"라우트가 plain 변종을 안 골랐다: {route_key}"
    payload = _server_payload(web_run_plain)
    got = _webview(payload, {"idle": {"order": [0, 1, 2, 3], "sel": [0, 1, 2, 3]}},
                   [{"id": "k", "kind": "key", "state": "idle"}])
    assert got["k"] == route_key, (
        f"pp OFF 줄에서 갈렸다\n  라우트: {route_key}\n  웹뷰  : {got['k']}")


def test_a_rotated_row_stays_fresh(web_run_plain):
    """**R1 회귀 그물.** 회전이 걸린 pp OFF 줄이 신선하다고 판정돼야 한다.

    옛 지문(변형 결과 RGBA 해시)에서는 이 줄이 영구 불일치라 프리뷰가 영원히 원본으로
    떨어지고 영상 내보내기가 영구 차단됐다 — 그리고 안내대로 해부를 갱신해도 라우트가
    또 BICUBIC 프레임으로 같은 숫자를 만들어 **절대 안 풀렸다** (슉슉이 실측 2026-07-26:
    rotate 3° 에서 555px 상이)."""
    import sprite_gen.serve.serve_curation as serve_curation
    from sprite_gen.effects.breathe import freeze_anatomy
    curation = json.loads((web_run_plain / "curation.json").read_text(encoding="utf-8"))
    curation["states"]["idle"]["transforms"] = {"0": {"rotate": 3.0}}
    _write_curation(web_run_plain, curation)

    frame, route_key = serve_curation._breathe_source_frame(web_run_plain, "idle")
    frozen = freeze_anatomy(frame, {}, route_key)      # 라우트가 사이드카에 얼리는 값
    payload = _server_payload(web_run_plain)
    got = _webview(payload,
                   {"idle": {"order": [0, 1, 2, 3], "sel": [0, 1, 2, 3],
                             "transforms": {"0": {"rotate": 3.0}}}},
                   [{"id": "k", "kind": "key", "state": "idle"}])
    assert got["k"] == route_key, "회전이 걸린 줄에서 웹뷰가 다른 키를 만든다"
    assert anatomy_fingerprint(got["k"]) == frozen["fingerprint"], (
        "회전이 걸린 줄이 얼린 지문과 안 맞는다 — 프리뷰가 영원히 원본으로 떨어진다")


# ── 굽기 변종 해소표 전수 ────────────────────────────────────────────

# 해소 축은 셋이다: 트윈 존재(2) × per-state 선언(없음/켬/끔) × 런 전역(없음/켬/끔) = **18칸**.
#
# 앞선 판은 8칸을 "전수" 라고 적었는데 `twin=False` 일 때 `own=None` 한 줄만 돌았다. 빠진
# 6칸 중 3칸이 갈리고 있었다 — 파이썬 per-state 분기는 **무조건**인데 미러가 트윈 존재로
# 게이트했기 때문이다. 곱집합을 코드로 만들어 "전수" 가 이름이 아니라 사실이 되게 한다.
VARIANT_CASES = [(twin, own, wide)
                 for twin in (False, True)
                 for own in (None, True, False)
                 for wide in (None, True, False)]


def _variant_id(case):
    twin, own, wide = case
    fmt = {None: "없음", True: "켬", False: "끔"}
    return f"twin{'있음' if twin else '없음'}-own{fmt[own]}-wide{fmt[wide]}"


@pytest.mark.parametrize("twin,own,wide", VARIANT_CASES, ids=[_variant_id(c) for c in VARIANT_CASES])
def test_the_webview_resolves_the_bake_variant_like_the_server(twin, own, wide):
    """웹뷰의 굽기 변종 == 파이썬 `frame_variant` — 해소표 전수.

    `unfakeOn` 은 **표시 렌즈**다: boot 시드가 트윈 없는 줄을 기본 OFF 로 놓는데 그 값은
    사이드카에 저장되지 않으므로 서버는 그 줄을 `pixel` 로 본다. 둘을 같은 값으로 쓰면
    `fit.pixel_perfect` 없이 만든 **기본 런 전부**에서 지문이 영구 불일치가 되고,
    해부를 갱신해도 라우트가 또 `pixel` 로 같은 키를 만들어 절대 안 풀린다
    (슉슉이 실측 2026-07-26).

    한 칸씩 메우지 않고 표를 통째로 건다 — 같은 실패 모드가 축만 바꿔(파일 → 리샘플러
    → 변종 이름) 세 번 되돌아왔다."""
    from sprite_gen.curate.curation import frame_variant

    curation = {"states": {}}
    if own is not None:
        curation["states"]["idle"] = {"pixel_unfake": own}
    if wide is not None:
        curation["pixel_unfake"] = wide
    frames = [{"index": 0, "present": True, "url": "/f0.png", "stamp": "1:1"}]
    if twin:
        frames[0]["plainUrl"] = "/f0.plain.png"
        frames[0]["plainBakeUrl"] = "/f0.plain.png"
        frames[0]["plainBakeStamp"] = "2:2"
    payload = {"requestStamp": "9:9", "curation": curation,
               "states": [{"name": "idle", "frames": frames}]}
    got = _webview(payload, {"idle": {"order": [0], "sel": [0]}},
                   [{"id": "v", "kind": "variant", "state": "idle"}])
    assert got["v"] == frame_variant(curation, "idle"), (
        f"트윈{twin} per-state{own} 전역{wide}: "
        f"웹뷰 {got['v']!r} vs 서버 {frame_variant(curation, 'idle')!r}")


def test_the_saved_sidecar_is_what_the_variant_resolves_from():
    """굽기 변종은 **저장되는 값**으로 해소한다.

    트윈 없는 줄의 퍼펙은 사이드카에 안 적힌다(적으면 굽기가 존재하지 않는 `.plain`
    변형을 요구한다). 그래서 그 줄의 화면 토글은 변종을 바꾸면 안 된다 — 눌렀다고
    키가 바뀌면 굽기는 그대로인데 프리뷰만 거부된다."""
    payload = {"requestStamp": "9:9", "curation": {"states": {}},
               "states": [{"name": "idle", "frames": [
                   {"index": 0, "present": True, "url": "/f0.png", "stamp": "1:1"}]}]}
    q = [{"id": "v", "kind": "variant", "state": "idle"}]
    on = _webview(payload, {"idle": {"order": [0], "sel": [0]}}, q, pp_override={"idle": True})
    off = _webview(payload, {"idle": {"order": [0], "sel": [0]}}, q, pp_override={"idle": False})
    assert on["v"] == off["v"] == "pixel", (
        f"트윈 없는 줄의 표시 토글이 굽기 변종을 바꾼다 (켬 {on['v']}, 끔 {off['v']})")


def test_a_declared_twinless_row_still_builds_a_key():
    """트윈 없는 줄에 per-state 선언이 있어도 웹뷰가 **키를 만들어야** 한다.

    변종 해소가 갈리면 웹뷰는 `plain` 을 고르고 `bakeFrame` 이 null 을 돌려줘 키가 아예
    안 만들어진다 → `breatheAssertFresh` 가 "기준 프레임 키를 만들 수 없다" 로 **영구
    거부**하고, 프리뷰 3곳·`row-export`·비교뷰 다운로드·경계 드래그가 전부 죽는다.
    굽기는 그 사이 멀쩡히 `pixel` 로 굽는다 (슉슉이 실측 2026-07-26).

    "갈렸다" 를 값으로 보는 위 해소표와 달리, 이건 **사용자가 겪는 결과**를 본다."""
    curation = {"pixel_unfake": False, "states": {"idle": {"pixel_unfake": True}}}
    payload = {"requestStamp": "9:9", "curation": curation,
               "states": [{"name": "idle", "frames": [
                   {"index": 0, "present": True, "url": "/f0.png", "stamp": "1:1"}]}]}
    got = _webview(payload, {"idle": {"order": [0], "sel": [0]}},
                   [{"id": "k", "kind": "key", "state": "idle"},
                    {"id": "g", "kind": "geometry", "state": "idle", "fallback": 0}])
    assert got["k"] and "|pixel|" in got["k"], (
        f"키를 못 만들거나 변종이 틀렸다: {got['k']!r} — 프리뷰·내보내기가 영구 거부된다")
    assert got["g"]["url"], "경계 드래그가 잴 프레임을 못 잡는다"


# ── 미러 전수: 파이썬 함수를 JS 가 베낀 자리는 **곱집합**으로 건다 ──────
#
# 여덟 라운드 동안 같은 형태로 기각당했다: 미러가 어떤 입력 부분집합에서 원본과 갈리고,
# 그 부분집합이 손으로 적은 케이스 목록 밖에 있었다 (축만 파일 → 리샘플러 → 변종 이름 →
# per-state 게이트로 옮겨갔다). 인스턴스가 아니라 **클래스**를 닫으려면 케이스를 적지 말고
# 입력 축의 곱집합을 **생성**해야 한다. 이 절이 남은 미러들에 그 형태를 적용한다.

MIRROR_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const payload = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const sandbox = { console, TextEncoder, Set, Map, setTimeout,
  document: { createElement: () => ({ getContext: () => ({}), style: {} }),
              addEventListener() {}, querySelectorAll: () => [] },
  window: {}, fetch: () => {}, setStatus() {}, t: () => "", STR: {}, lang: "ko",
  scheduleSave() {}, flushSave: async () => {}, rebuildState() {},
  URL: { createObjectURL() {}, revokeObjectURL() {} } };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of payload.scripts) vm.runInContext(fs.readFileSync(f, "utf8"), sandbox);
sandbox.__payload = payload;
vm.runInContext(`
  __out = [];
  for (const c of __payload.cases) {
    if (__payload.fn === "normalizeTransform") {
      __out.push(breatheNormalizeTransform(c.transform));
    } else if (__payload.fn === "editIndex") {
      entries = { idle: { order: [0, 1, 2], sel: new Set([0, 1, 2]),
                          clones: c.clones || {},
                          unlinked: new Set(c.unlinked || []) } };
      __out.push(editIndex("idle", c.index));
    }
  }
`, sandbox);
process.stdout.write(JSON.stringify(sandbox.__out));
"""


def _mirror(fn, cases):
    return _node(MIRROR_HARNESS, {
        "scripts": [str(SRC / "breathe.js"), str(SRC / "store.js")],
        "fn": fn, "cases": cases})


# 변형 필드 × 값 모양의 곱집합 — 하나라도 갈리면 그 필드가 걸린 줄의 키가 통째로 갈린다.
_TRANSFORM_FIELDS = ["rotate", "scale", "dx", "dy", "shx", "shy", "flipX"]
_TRANSFORM_VALUES = [0, 1, -1, 3.0, 0.9, 1.0000005, "3", "0.5", True, False]
NORMALIZE_CASES = ([{"transform": None}, {"transform": {}}]
                   + [{"transform": {f: v}} for f in _TRANSFORM_FIELDS for v in _TRANSFORM_VALUES])


def test_normalize_transform_mirror_agrees_on_the_full_product():
    """`normalize_transform` 미러 — 필드 7 × 값 모양 10 곱집합.

    이 함수의 출력이 그대로 기준 프레임 키에 들어간다. 한 필드의 한 모양만 갈려도 그
    변형이 걸린 줄은 지문이 영구 불일치가 된다."""
    from sprite_gen.curate.curation import normalize_transform

    got = _mirror("normalizeTransform", NORMALIZE_CASES)
    assert len(got) == len(NORMALIZE_CASES)
    for case, js in zip(NORMALIZE_CASES, got):
        py = normalize_transform(case["transform"])
        assert {k: float(v) for k, v in js.items()} == {k: float(v) for k, v in py.items()}, (
            f"normalize_transform 미러가 갈렸다 — 입력 {case['transform']!r}\n"
            f"  py: {py}\n  js: {js}")


# 복제 링크 × 링크끊기 × 인덱스의 곱집합.
EDIT_INDEX_CASES = [{"clones": clones, "unlinked": unlinked, "index": index}
                    for clones in ({}, {"2": 0}, {"2": 1})
                    for unlinked in ([], [2])
                    for index in (0, 1, 2)]


def test_edit_index_mirror_agrees_on_the_full_product():
    """`edit_index` 미러 — 복제 링크 3 × 링크끊기 2 × 인덱스 3 곱집합.

    이 값이 어느 프레임의 변형·픽셀편집을 읽을지 정하고, 그게 기준 프레임 키의 재료다.
    갈리면 웹뷰와 굽기가 **다른 프레임의 편집**을 기준으로 삼는다. 미러라고 적혀 있지도
    않았고 교차언어 테스트도 없었다."""
    from sprite_gen.curate.curation import edit_index

    got = _mirror("editIndex", EDIT_INDEX_CASES)
    assert len(got) == len(EDIT_INDEX_CASES)
    for case, js in zip(EDIT_INDEX_CASES, got):
        curation = {"states": {"idle": {"clones": case["clones"],
                                        "unlinked": case["unlinked"]}}}
        py = edit_index(curation, "idle", case["index"])
        assert js == py, (
            f"edit_index 미러가 갈렸다 — clones={case['clones']} "
            f"unlinked={case['unlinked']} index={case['index']}: py={py} js={js}")


def test_the_mirrored_limits_are_the_same_numbers():
    """사이드카 허용 범위 상수 — 파이썬과 JS 가 **같은 숫자**여야 한다.

    갈리면 웹뷰가 굽기의 loud reject 를 못 예측해, 사용자는 저장되는데 안 구워지는
    설정을 만들 수 있다."""
    from sprite_gen.curate import curation as C

    js = (SRC / "breathe.js").read_text(encoding="utf-8")
    for name, want in (("BREATHE_DEPTH_MAX", C.BREATHE_DEPTH_MAX),
                       ("BREATHE_BREATHS_MAX", C.BREATHE_BREATHS_MAX),
                       ("BREATHE_LAG_MAX", C.BREATHE_LAG_MAX)):
        m = re.search(rf"const {name} = ([\d.]+);", js)
        assert m, f"{name} 이 미러에 없다"
        assert float(m.group(1)) == float(want), \
            f"{name}: py={want} js={m.group(1)} — 갈리면 저장은 되는데 안 구워진다"

    retired = re.search(r"const RETIRED_BREATHE_KEYS = \[(.*?)\];",
                        (SRC / "store.js").read_text(encoding="utf-8"))
    assert retired, "폐기 키 목록이 미러에 없다"
    js_keys = {k.strip().strip('"') for k in retired.group(1).split(",")}
    assert js_keys == set(C.RETIRED_BREATHE_KEYS), \
        f"폐기 키 목록이 갈렸다: py={sorted(C.RETIRED_BREATHE_KEYS)} js={sorted(js_keys)}"


def test_a_plain_variant_without_a_twin_refuses_instead_of_falling_back():
    """변종이 `plain` 인데 트윈 파일이 없으면 **캐노니컬로 조용히 떨어지지 않는다**.

    그 상태는 굽기도 `row_frame_rel(row, i, "plain")` 로 없는 파일을 가리켜 실패하는
    상태다. 웹뷰만 캐노니컬로 떨어지면 두 쪽이 서로 다른 진실을 갖고, 프리뷰는 멀쩡해
    보이는데 굽기는 죽는다.

    이 계약은 round-12 가 세웠는데 그물이 없었다 — `frame.plainBakeUrl || frame.url` 로
    되돌려도 529 전부 통과했다 (슉슉이 note 2026-07-26)."""
    curation = {"states": {"idle": {"pixel_unfake": False}}}   # plain 을 강제
    payload = {"requestStamp": "9:9", "curation": curation,
               "states": [{"name": "idle", "frames": [        # 트윈 없음
                   {"index": 0, "present": True, "url": "/f0.png", "stamp": "1:1"}]}]}
    got = _webview(payload, {"idle": {"order": [0], "sel": [0]}},
                   [{"id": "v", "kind": "variant", "state": "idle"},
                    {"id": "k", "kind": "key", "state": "idle"},
                    {"id": "g", "kind": "geometry", "state": "idle", "fallback": 0}])
    assert got["v"] == "plain", "픽스처가 plain 변종을 안 만든다 — 이 테스트는 공허하다"
    assert got["k"] is None, (
        f"트윈이 없는데 키를 만들어냈다: {got['k']!r} — 캐노니컬로 조용히 떨어졌다는 뜻이다")
    assert got["g"]["url"] is None, (
        f"지오메트리가 굽기가 못 읽는 파일을 가리킨다: {got['g']['url']!r}")


@pytest.mark.parametrize("shape", ["null", "empty", "missing"])
def test_the_mirror_refuses_when_the_key_cannot_be_built(shape):
    """키를 못 만들면 "확인 불가" 이지 "괜찮음" 이 아니다.

    형제 분기(지문 없음)는 round-10 R3 이후 그물이 있는데 이쪽만 비어 있었다 —
    `throw` 를 `return` 으로 바꿔도 529 전부 통과했다 (슉슉이 note 2026-07-26).
    키가 없는 상태는 트윈 없는 plain 줄·빈 재생목록·스탬프 없는 프레임에서 실제로 나온다."""
    key = {"null": None, "empty": "", "missing": None}[shape]
    harness = r"""
const fs = require("fs"), vm = require("vm");
const P = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const sb = { console, TextEncoder, document: { createElement: () => ({ getContext: () => ({}) }) },
             entries: {}, run: {}, t: () => "", setStatus: () => {}, fetch: () => {} };
sb.globalThis = sb; vm.createContext(sb);
vm.runInContext(fs.readFileSync(P.script, "utf8"), sb);
let out;
try {
  if (P.shape === "missing") sb.breatheAssertFresh(undefined, P.cfg);
  else sb.breatheAssertFresh(P.key, P.cfg);
  out = { ok: true };
} catch (e) {
  out = { ok: false, refused: e.constructor.name === "BreatheRefused", message: e.message };
}
process.stdout.write(JSON.stringify(out));
"""
    cfg = {"depth": 0.06, "breaths": 1, "lag": 0.1,
           "anatomy": {"height": 50, "rigid_row": 12, "fingerprint": "pixel:0:deadbeef"}}
    got = _node(harness, {"script": str(SRC / "breathe.js"), "key": key,
                          "shape": shape, "cfg": cfg})
    assert not got["ok"] and got["refused"], (
        f"키가 {shape} 인데 미러가 신선하다고 판정했다 — 낡은 해부로 그리고, "
        f"`row-export` 는 그 캔버스로 파일을 굽는다 ({got})")
