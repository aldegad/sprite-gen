# SPDX-License-Identifier: Apache-2.0
"""폐기된 호흡 사이드카가 큐레이터 왕복에서 살아남는지 + JS 위상식이 파이썬과 같은지.

굽기 경로만 `splits`/`amplitude`/`subpixel`/`hold` 를 loud reject 하면 계약이 반쪽이다.
구 런을 큐레이터로 한 번 여는 것만으로 폐기 키가 사이드카에서 사라지면 (a) 사용자는
호흡 설정이 있었다는 사실도 못 듣고 (b) `migrate-breathe` 가 옮길 근거가 파괴되고
(c) 그 뒤 굽기는 "호흡 없음" 으로 조용히 성공한다. 검증자 둘(새미·부리)이 독립으로
같은 경로를 재현했다 (2026-07-25).

위상식은 별개 건이다. round-2 가 파이썬에서 `((i*breaths) % seq) / seq` 로 고쳤는데
미러가 옛 식으로 남아 프리뷰와 굽기가 반올림 경계에서 갈렸다 (부리 실측: seq=30
breaths=7 slot 24 → 4바이트 차이). 기존 미러 테스트는 위상을 파이썬에서 만들어
양쪽에 먹여주기 때문에 `breathePattern` 을 한 번도 안 태운다 — 그래서 못 잡았다.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sprite_gen.breathe import fit_breathe_pattern  # noqa: E402
from sprite_gen.curation import RETIRED_BREATHE_KEYS, state_breathe  # noqa: E402

CURATOR = ROOT / "scripts" / "curator" / "src"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 큐레이터 JS 를 실행할 수 없다")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const P = process.argv[2] + "/";
const sb = { console, document: { createElement: () => ({ getContext: () => ({}) }) },
  window: {}, __status: [], setStatus: (m, k) => sb.__status.push([k || "", String(m)]),
  t: () => "", fetch: () => {}, alert: () => {}, lang: "ko", STR: { ko: {} },
  entries: {}, anchorPicks: {}, previews: {} };
sb.globalThis = sb; vm.createContext(sb);
for (const f of ["util.js", "store.js", "persistence.js"]) {
  try { vm.runInContext(fs.readFileSync(P + f, "utf8"), sb); } catch (e) {}
}
const input = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
if (input.mode === "roundtrip") {
  vm.runInContext(`run = ${JSON.stringify(input.run)};`, sb);
  sb.seedEntries();
  const payload = sb.buildPayload();
  fs.writeFileSync(input.out, JSON.stringify({ payload, status: sb.__status }));
} else {
  vm.runInContext(fs.readFileSync(P + "breathe.js", "utf8"), sb);
  const out = input.cases.map((c) => sb.breathePattern({ breaths: c.breaths }, c.seq));
  fs.writeFileSync(input.out, JSON.stringify(out));
}
"""


def _run(payload, tmp_path):
    harness = tmp_path / "h.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    payload["out"] = str(tmp_path / "out.json")
    src = tmp_path / "in.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), str(harness), str(CURATOR), str(src)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"node 하네스 실패:\n{proc.stderr}"
    return json.loads(Path(payload["out"]).read_text(encoding="utf-8"))


@node
@pytest.mark.parametrize("retired_key", sorted(RETIRED_BREATHE_KEYS))
def test_curator_preserves_a_retired_breathe_sidecar(retired_key, tmp_path):
    """구 스키마 키가 있는 사이드카를 웹뷰가 읽고 되쓰면 그 키가 그대로 남아야 한다."""
    breathe = {retired_key: {"splits": [0.55], "amplitude": 2,
                             "subpixel": True, "hold": 2}[retired_key], "breaths": 3}
    run = {"schemaVersion": 1, "fps": 6,
           "states": [{"name": "idle", "fps": 6,
                       "frames": [{"index": 0, "present": True}, {"index": 1, "present": True}]}],
           "curation": {"states": {"idle": {"selected": [0, 1], "breathe": breathe}}}}
    got = _run({"mode": "roundtrip", "run": run}, tmp_path)
    saved = got["payload"]["states"]["idle"].get("breathe")
    assert saved is not None, f"{retired_key}: 저장 payload 에서 breathe 가 통째로 사라졌다"
    assert retired_key in saved, f"{retired_key}: 폐기 키가 삭제됐다 — migrate 근거가 파괴된다"
    assert saved == breathe, "폐기 사이드카는 정규화도 하지 않고 원본 그대로여야 한다"
    # 굽기 쪽 loud reject 가 여전히 걸리는지 (계약이 왕복 후에도 성립)
    with pytest.raises(SystemExit):
        state_breathe({"states": {"idle": {"breathe": saved}}}, "idle")


@node
def test_curator_tells_the_user_about_a_retired_sidecar(tmp_path):
    """조용히 넘어가지 않는다 — 마이그레이션 경로를 화면에 알린다."""
    run = {"schemaVersion": 1, "fps": 6,
           "states": [{"name": "idle", "fps": 6, "frames": [{"index": 0, "present": True}]}],
           "curation": {"states": {"idle": {"selected": [0],
                                            "breathe": {"splits": [0.55], "breaths": 1}}}}}
    got = _run({"mode": "roundtrip", "run": run}, tmp_path)
    errs = [m for kind, m in got["status"] if kind == "err"]
    assert errs, "폐기 스키마를 만났는데 아무 알림도 없다"
    assert any("migrate-breathe" in m for m in errs), f"마이그레이션 경로 안내가 없다: {errs}"


@node
@pytest.mark.parametrize("seq,breaths", [(18, 3), (30, 7), (12, 2), (12, 4), (7, 2), (20, 5)])
def test_js_phase_pattern_is_bit_identical_to_python(seq, breaths, tmp_path):
    """위상식 미러 — 부동소수 표현까지 같아야 프리뷰가 굽기와 안 갈린다."""
    got = _run({"mode": "pattern", "cases": [{"seq": seq, "breaths": breaths}]}, tmp_path)[0]
    want = fit_breathe_pattern(seq, {"breaths": breaths})
    assert len(got) == len(want)
    for i, (a, b) in enumerate(zip(want, got)):
        assert a == b, f"seq={seq} breaths={breaths} slot {i}: py={a!r} js={b!r}"
    assert len(set(got)) == len(set(want)), "유니크 위상 개수가 다르다 (표현 노이즈)"


@node
@pytest.mark.parametrize("bad", [
    {"depth": 0.5, "breaths": 12, "lag": 0.9},
    {"depth": 0},
    {"breaths": 12},
], ids=["all-three", "depth-zero", "breaths-over"])
def test_the_curator_never_clamps_an_out_of_range_sidecar(bad, tmp_path):
    """굽기가 요란하게 거부하는 값을 웹뷰가 조용히 깎아 되쓰면 원본이 파괴된다.

    실측 (슉슉이 2026-07-25): `depth 0.5 / breaths 12 / lag 0.9` 를 웹뷰가 `0.2 / 8 / 0.45`
    로 조용히 로드하고(status 0건) 첫 autosave 가 그 값으로 사이드카를 갈아치웠다 —
    loud reject 자체가 사라진다. round-3 A(폐기 키 삼킴)와 같은 경로다."""
    breathe = {"depth": 0.06, "breaths": 1, "lag": 0.1, **bad}
    run = {"schemaVersion": 1, "fps": 6,
           "states": [{"name": "idle", "fps": 6,
                       "frames": [{"index": 0, "present": True}, {"index": 1, "present": True}]}],
           "curation": {"states": {"idle": {"selected": [0, 1], "breathe": breathe}}}}
    got = _run({"mode": "roundtrip", "run": run}, tmp_path)

    saved = got["payload"]["states"]["idle"].get("breathe")
    assert saved == breathe, f"웹뷰가 범위 밖 사이드카를 바꿔 썼다: {saved}"
    errs = [m for kind, m in got["status"] if kind == "err"]
    assert errs, "범위 밖인데 아무 알림도 없다"

    # 굽기 쪽 게이트가 왕복 후에도 그대로 걸린다
    with pytest.raises(SystemExit):
        state_breathe({"states": {"idle": {"breathe": saved}}}, "idle")


@node
def test_the_curator_accepts_what_the_bake_accepts(tmp_path):
    """굽기가 받는 값(숫자 문자열 포함)을 웹뷰도 호흡 켜짐으로 읽어야 한다.

    `typeof !== "number"` 게이트는 `"0.08"` 을 호흡 없음으로 읽고 저장에서 breathe 키를
    통째로 지웠다 — 굽기는 정상 수용하는 값이라 켜짐/꺼짐 자체가 반대였다."""
    breathe = {"depth": "0.08", "breaths": 3, "lag": 0.1}
    run = {"schemaVersion": 1, "fps": 6,
           "states": [{"name": "idle", "fps": 6, "frames": [{"index": 0, "present": True}]}],
           "curation": {"states": {"idle": {"selected": [0], "breathe": breathe}}}}
    got = _run({"mode": "roundtrip", "run": run}, tmp_path)
    saved = got["payload"]["states"]["idle"].get("breathe")
    assert saved is not None, "굽기가 받는 값을 웹뷰가 호흡 없음으로 읽고 지웠다"
    assert float(saved["depth"]) == 0.08
    assert state_breathe({"states": {"idle": {"breathe": saved}}}, "idle")["depth"] == 0.08


@node
@pytest.mark.parametrize("sidecar", [
    {"breaths": 2},
    {"breaths": 2, "lag": 0.1},
    {"depth": 0.06, "breaths": 2},
], ids=["depth-omitted", "depth-omitted-with-lag", "depth-explicit"])
def test_an_omitted_depth_survives_the_round_trip(sidecar, tmp_path):
    """`depth` 생략은 굽기에서 기본값 0.06 이다 — 왕복이 그걸 파괴하면 안 된다.

    `Number(undefined)` 는 NaN 이고 `JSON.stringify(NaN)` 은 `null` 이라, 기본값 분기가
    없으면 첫 autosave 가 사이드카에 `depth: null` 을 박는다. 그러면 **굽기가 정상 수용하던
    사이드카를 큐레이터로 한 번 여는 것만으로 굽기가 죽는 사이드카로 바꿔놓고 아무 말도
    안 한다** (슉슉이 실측 2026-07-26). `SKILL.md` 가 문서화한 "뷰 없이 에이전트가 breathe
    만 쓴 런" 이 정확히 이 모양이다."""
    before = state_breathe({"states": {"idle": {"breathe": sidecar}}}, "idle")
    assert before is not None, "이 사이드카는 굽기가 받아야 의미가 있다"

    run = {"schemaVersion": 1, "fps": 6,
           "states": [{"name": "idle", "fps": 6,
                       "frames": [{"index": 0, "present": True}, {"index": 1, "present": True}]}],
           "curation": {"states": {"idle": {"selected": [0, 1], "breathe": sidecar}}}}
    got = _run({"mode": "roundtrip", "run": run}, tmp_path)
    saved = got["payload"]["states"]["idle"].get("breathe")
    assert saved is not None, "왕복이 호흡 설정을 통째로 지웠다"
    assert saved.get("depth") is not None, f"왕복이 depth 를 null 로 박았다: {saved}"

    after = state_breathe({"states": {"idle": {"breathe": saved}}}, "idle")
    assert after["depth"] == before["depth"], "왕복이 굽기가 쓰는 값을 바꿨다"
    assert after["breaths"] == before["breaths"]


# ── 왕복 계약 전수 매트릭스 ─────────────────────────────────────────
#
# 아홉 라운드 동안 같은 실패 모드가 값 모양만 바꿔 네 번 반복됐다 (round-3 `splits`,
# round-6 `depth:"0.08"`, round-7 `breaths:"3.5"`, round-9 `depth` 생략, round-10 `lag:null`).
# 매번 지적된 한 칸만 메웠기 때문이다. 여기서는 **키 × 값 모양을 전수 열거**해 굽기와
# 큐레이터의 판정이 어긋나는 칸이 하나도 없음을 한 번에 고정한다.

_VALUE_SHAPES = {
    "omitted": None,                 # 키 자체를 안 넣는다
    "null": None,
    "string": None,
    "zero": 0,
    "negative": -1,
    "huge": 999,
    "fractional": 2.5,
}


def _sidecar_for(key, shape):
    base = {"depth": 0.06, "breaths": 2, "lag": 0.1}
    if shape == "omitted":
        base.pop(key)
    elif shape == "null":
        base[key] = None
    elif shape == "string":
        base[key] = "0.08" if key in ("depth", "lag") else "3"
    else:
        base[key] = _VALUE_SHAPES[shape]
    return base


def _bake_verdict(sidecar):
    """굽기의 판정 — (수용?, 정규화 결과)."""
    try:
        return True, state_breathe({"states": {"idle": {"breathe": sidecar}}}, "idle")
    except SystemExit:
        return False, None


@node
@pytest.mark.parametrize("shape", sorted(_VALUE_SHAPES))
@pytest.mark.parametrize("key", ["depth", "breaths", "lag"])
def test_the_curator_agrees_with_the_bake_on_every_value_shape(key, shape, tmp_path):
    """굽기가 거부하는 사이드카는 큐레이터도 **원본 그대로 두고 알린다**.
    굽기가 받는 사이드카는 큐레이터도 받고, 왕복해도 굽기가 쓰는 값이 안 바뀐다.

    어느 쪽이든 **첫 autosave 가 사용자의 원래 숫자를 바꾸면 안 된다** — 그게 이
    플랜에서 네 번 반복된 실패 모드다(굽기가 거부하던 근거가 조용히 사라진다)."""
    sidecar = _sidecar_for(key, shape)
    accepted, before = _bake_verdict(sidecar)

    run = {"schemaVersion": 1, "fps": 6,
           "states": [{"name": "idle", "fps": 6,
                       "frames": [{"index": 0, "present": True}, {"index": 1, "present": True}]}],
           "curation": {"states": {"idle": {"selected": [0, 1], "breathe": sidecar}}}}
    got = _run({"mode": "roundtrip", "run": run}, tmp_path)
    saved = got["payload"]["states"]["idle"].get("breathe")
    errs = [m for kind, m in got["status"] if kind == "err"]

    if not accepted:
        assert saved == sidecar, (
            f"{key}={shape}: 굽기가 거부하는 사이드카를 큐레이터가 바꿔 썼다 — "
            f"거부 근거가 사라진다\n  원본={sidecar}\n  저장={saved}")
        assert errs, f"{key}={shape}: 굽기가 거부하는데 화면 알림이 없다"
        after_accepted, _ = _bake_verdict(saved)
        assert not after_accepted, f"{key}={shape}: 왕복이 거부를 통과로 바꿨다"
    else:
        assert saved is not None, f"{key}={shape}: 굽기가 받는 설정을 큐레이터가 지웠다"
        after_accepted, after = _bake_verdict(saved)
        assert after_accepted, (
            f"{key}={shape}: 왕복이 굽기가 죽는 사이드카를 만들었다\n"
            f"  원본={sidecar}\n  저장={saved}")
        assert after == before, (
            f"{key}={shape}: 왕복이 굽기가 쓰는 값을 바꿨다\n  전={before}\n  후={after}")
