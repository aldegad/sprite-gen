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
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sprite_gen.breathe import anatomy_fingerprint, reference_key  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "scripts" / "curator" / "src"

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

    from sprite_gen import breathe
    sig = inspect.signature(breathe.reference_key)
    assert "frame" not in sig.parameters and "image" not in sig.parameters, \
        "reference_key 가 프레임을 받는다 — BICUBIC/NEAREST 불일치가 되돌아온다"
    js = (SRC / "breathe.js").read_text(encoding="utf-8")
    body = js[js.index("function breatheReferenceKeyOf"):]
    body = body[:body.index("\nfunction ", 1)]
    for banned in ("getImageData", "getContext", "canvas", "drawImage"):
        assert banned not in body, f"JS 키 빌더가 {banned} 를 쓴다 — 픽셀을 읽으면 안 된다"


# ── 웹뷰가 서버가 쓴 그 키를 만들어 낼 수 있는가 ──────────────────────

from tests.test_breathe_anatomy_route import CELL, run_dir  # noqa: E402,F401

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
  ppStates = __payload.ppStates || {};
  for (const [name, e] of Object.entries(__payload.entries)) {
    entries[name] = { order: e.order, sel: new Set(e.sel), transforms: e.transforms || {},
                      pixels: e.pixels || {}, clones: e.clones || {},
                      unlinked: new Set(e.unlinked || []) };
  }
  __out = {};
  for (const q of __payload.queries) {
    if (q.kind === "key") __out[q.id] = breatheReferenceKey(q.state);
    if (q.kind === "geometry") __out[q.id] = breatheGeometryFrame(q.state, q.fallback);
  }
`, sandbox);
process.stdout.write(JSON.stringify(sandbox.__out));
"""


def _webview(run_payload, entries, queries, pp_states=None):
    return _node(STORE_HARNESS, {
        "scripts": [str(SRC / "breathe.js"), str(SRC / "store.js")],
        "run": run_payload, "entries": entries, "ppStates": pp_states or {},
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
    from sprite_gen.curation import stamp_curation
    (run_dir / "curation.json").write_text(
        json.dumps(stamp_curation(run_dir, curation)), encoding="utf-8")


def _server_payload(run_dir):
    import serve_curation
    return serve_curation._build_run_state_impl(run_dir)


def test_the_webview_builds_the_same_key_the_route_used(web_run):
    """**이 파일의 핵심 단언.**

    미러 테스트들은 같은 픽셀 버퍼를 양쪽에 먹여서 "웹뷰가 서버가 지문 찍은 그 프레임에
    도달할 수 있는가" 를 묻지 않았다 — 그래서 리샘플러 불일치가 10라운드를 살아남았다.
    여기서는 서버가 실제로 응답에 실어 보낸 페이로드만으로 웹뷰가 키를 만들게 하고,
    라우트가 해부를 얼릴 때 쓴 키와 **문자열 동일**한지 본다."""
    import serve_curation
    _, route_key = serve_curation._breathe_source_frame(web_run, "idle")
    payload = _server_payload(web_run)
    got = _webview(payload,
                   {"idle": {"order": [0, 1, 2, 3], "sel": [0, 1, 2, 3]}},
                   [{"id": "k", "kind": "key", "state": "idle"}],
                   pp_states={"idle": True})
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
    from sprite_gen.curation import empty_curation
    curation = empty_curation()
    curation["states"]["idle"] = {"pixel_perfect": False}
    _write_curation(web_run, curation)
    return web_run


def test_the_webview_agrees_on_the_plain_variant_too(web_run_plain):
    """pp OFF 줄에서도 웹뷰 키 == 라우트 키.

    변종이 갈리면 서로 다른 **파일**을 기준으로 삼는다 — 이 줄이 정확히 그 경로다."""
    import serve_curation
    _, route_key = serve_curation._breathe_source_frame(web_run_plain, "idle")
    assert "|plain|" in route_key, f"라우트가 plain 변종을 안 골랐다: {route_key}"
    payload = _server_payload(web_run_plain)
    got = _webview(payload, {"idle": {"order": [0, 1, 2, 3], "sel": [0, 1, 2, 3]}},
                   [{"id": "k", "kind": "key", "state": "idle"}],
                   pp_states={"idle": False})
    assert got["k"] == route_key, (
        f"pp OFF 줄에서 갈렸다\n  라우트: {route_key}\n  웹뷰  : {got['k']}")


def test_a_rotated_row_stays_fresh(web_run_plain):
    """**R1 회귀 그물.** 회전이 걸린 pp OFF 줄이 신선하다고 판정돼야 한다.

    옛 지문(변형 결과 RGBA 해시)에서는 이 줄이 영구 불일치라 프리뷰가 영원히 원본으로
    떨어지고 영상 내보내기가 영구 차단됐다 — 그리고 안내대로 해부를 갱신해도 라우트가
    또 BICUBIC 프레임으로 같은 숫자를 만들어 **절대 안 풀렸다** (슉슉이 실측 2026-07-26:
    rotate 3° 에서 555px 상이)."""
    import serve_curation
    from sprite_gen.breathe import freeze_anatomy
    curation = json.loads((web_run_plain / "curation.json").read_text(encoding="utf-8"))
    curation["states"]["idle"]["transforms"] = {"0": {"rotate": 3.0}}
    _write_curation(web_run_plain, curation)

    frame, route_key = serve_curation._breathe_source_frame(web_run_plain, "idle")
    frozen = freeze_anatomy(frame, {}, route_key)      # 라우트가 사이드카에 얼리는 값
    payload = _server_payload(web_run_plain)
    got = _webview(payload,
                   {"idle": {"order": [0, 1, 2, 3], "sel": [0, 1, 2, 3],
                             "transforms": {"0": {"rotate": 3.0}}}},
                   [{"id": "k", "kind": "key", "state": "idle"}],
                   pp_states={"idle": False})
    assert got["k"] == route_key, "회전이 걸린 줄에서 웹뷰가 다른 키를 만든다"
    assert anatomy_fingerprint(got["k"]) == frozen["fingerprint"], (
        "회전이 걸린 줄이 얼린 지문과 안 맞는다 — 프리뷰가 영원히 원본으로 떨어진다")
