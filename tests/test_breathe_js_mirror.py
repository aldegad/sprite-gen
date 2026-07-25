# SPDX-License-Identifier: Apache-2.0
"""큐레이터 JS 미러가 파이썬 굽기와 **바이트 동일**한지 (Task Card Verification).

호흡 워프는 두 곳에 구현돼 있다: 굽기(`sprite_gen/breathe.py`)와 라이브 프리뷰
(`scripts/curator/src/breathe.js`). 검출은 서버 한 곳으로 몰았지만 워프는 프리뷰를
위해 미러가 남아 있고, 미러가 갈리면 "미리보기와 결과가 다르다"가 된다.

이 테스트가 그 계약을 고정한다 — 위상 0 을 포함한 전 위상을 비교한다. 위상 0 이
중요한 이유: 진행파 지연 때문에 t=0 도 항등이 아닌데, 예전엔 두 소비자가 그걸
건너뛰어 아틀라스만 원본을 굽는 사고가 있었다 (새미 검증 2026-07-25).

node 가 없으면 skip — CI 에 node 가 없을 수 있고, 그 경우 조용히 통과시키지 않고
skip 사유를 남긴다.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sprite_gen.breathe import freeze_anatomy, phase_frame  # noqa: E402
from tests.test_breathe import CFG, _dome, _humanoid, _winged  # noqa: E402

CURATOR_BREATHE = Path(__file__).resolve().parent.parent / "scripts" / "curator" / "src" / "breathe.js"
PHASES = [i / 12 for i in range(12)]

# breathe.js 는 클래식 스크립트(모듈 아님)라 전역 어휘를 공유한다. node 에서 돌리려면
# 문서 API 최소분만 shim 하면 된다 — 워프는 canvas 2D getImageData/putImageData 만 쓴다.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

function makeCanvas(w, h, data) {
  const buf = data ? Uint8ClampedArray.from(data) : new Uint8ClampedArray(w * h * 4);
  const ctx = {
    imageSmoothingEnabled: false,
    getImageData: () => ({ data: buf, width: w, height: h }),
    putImageData: (img) => { buf.set(img.data); },
    createImageData: (cw, ch) => ({ data: new Uint8ClampedArray(cw * ch * 4), width: cw, height: ch }),
    drawImage: (src) => { buf.set(src.__buf); },
    clearRect: () => {},
  };
  const cvs = { width: w, height: h, getContext: () => ctx, __buf: buf };
  return cvs;
}

const payload = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const sandbox = {
  document: { createElement: () => makeCanvas(payload.width, payload.height) },
  console,
  entries: {}, run: { states: [] }, t: () => "", setStatus: () => {}, fetch: () => {},
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(payload.script, "utf8"), sandbox);

const out = [];
for (const item of payload.cases) {
  const base = makeCanvas(payload.width, payload.height, item.rgba);
  const res = sandbox.breatheComposite(base, payload.cfg, item.phase);
  out.push(Array.from(res.getContext().getImageData().data));
}
fs.writeFileSync(payload.out, JSON.stringify(out));
"""


def _rgba(image):
    return list(image.convert("RGBA").tobytes())


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 미러를 실행할 수 없다")
@pytest.mark.parametrize("build", [_humanoid, _winged, _dome], ids=["humanoid", "winged", "dome"])
def test_curator_mirror_is_byte_identical_to_the_bake(build, tmp_path):
    src = build()
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg)

    payload = {
        "script": str(CURATOR_BREATHE),
        "width": src.width,
        "height": src.height,
        "cfg": {"depth": cfg["depth"], "breaths": cfg["breaths"], "lag": cfg["lag"],
                "anatomy": cfg["anatomy"]},
        "cases": [{"phase": p, "rgba": _rgba(src)} for p in PHASES],
        "out": str(tmp_path / "js.json"),
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")

    proc = subprocess.run([shutil.which("node"), str(harness), str(payload_path)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"node 하네스 실패:\n{proc.stderr}"
    js_frames = json.loads((tmp_path / "js.json").read_text(encoding="utf-8"))

    assert len(js_frames) == len(PHASES)
    for phase, js in zip(PHASES, js_frames):
        py = _rgba(phase_frame(src, cfg, phase))
        diff = sum(1 for a, b in zip(py, js) if a != b)
        assert diff == 0, f"{build.__name__} 위상 {phase:.4f}: JS 미러가 굽기와 {diff}바이트 다르다"
