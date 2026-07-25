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
