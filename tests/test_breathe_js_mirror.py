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

from sprite_gen.breathe import (MAX_ROW_STRAIN, freeze_anatomy, phase_frame,  # noqa: E402
                                resolve_anatomy, row_strain)
from sprite_gen.extract import solid_alpha_bbox  # noqa: E402
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
  try {
    const res = sandbox.breatheComposite(base, payload.cfg, item.phase);
    out.push({ ok: true, data: Array.from(res.getContext().getImageData().data) });
  } catch (e) {
    out.push({ ok: false, refused: e.constructor.name === "BreatheRefused", message: e.message });
  }
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
        assert js["ok"], f"{build.__name__} 위상 {phase:.4f}: JS 가 거부했다 — {js['message']}"
        py = _rgba(phase_frame(src, cfg, phase))
        diff = sum(1 for a, b in zip(py, js["data"]) if a != b)
        assert diff == 0, f"{build.__name__} 위상 {phase:.4f}: JS 미러가 굽기와 {diff}바이트 다르다"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 미러를 실행할 수 없다")
@pytest.mark.parametrize("build", [_humanoid, _winged, _dome], ids=["humanoid", "winged", "dome"])
def test_the_mirror_refuses_exactly_what_the_bake_refuses(build, tmp_path):
    """굽기가 `SystemExit` 으로 거부하는 프레임은 미러도 만들어 주면 안 된다.

    미러가 조용히 잘라 내보내면 (a) 프리뷰는 멀쩡한데 굽기가 죽고 (b) `row-export` 의
    WebM/MP4 는 서버를 안 거치므로 **잘린 영상이 사용자 손에 들어간다** (슉슉이 실측
    2026-07-25: 여백 0 셀에서 불투명 73px 소실, 오류 0건). 여기서 여백을 없애 그 상황을
    강제로 만든다."""
    src = build()
    src = src.crop(solid_alpha_bbox(src))            # 여백 0 — 늘어나면 반드시 잘린다
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg)

    refused_py = []
    for phase in PHASES:
        try:
            phase_frame(src, cfg, phase)
        except SystemExit:
            refused_py.append(phase)
    assert refused_py, "이 픽스처는 굽기가 거부하는 위상을 가져야 의미가 있다"

    payload = {
        "script": str(CURATOR_BREATHE), "width": src.width, "height": src.height,
        "cfg": {"depth": cfg["depth"], "breaths": cfg["breaths"], "lag": cfg["lag"],
                "anatomy": cfg["anatomy"]},
        "cases": [{"phase": p, "rgba": _rgba(src)} for p in PHASES],
        "out": str(tmp_path / "js.json"),
    }
    (tmp_path / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "harness.cjs").write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), str(tmp_path / "harness.cjs"),
                           str(tmp_path / "payload.json")], capture_output=True, text=True)
    assert proc.returncode == 0, f"node 하네스 실패:\n{proc.stderr}"
    js = json.loads((tmp_path / "js.json").read_text(encoding="utf-8"))

    for phase, got in zip(PHASES, js):
        py_refused = phase in refused_py
        assert got["ok"] != py_refused, (
            f"{build.__name__} 위상 {phase:.4f}: 굽기 거부={py_refused} 인데 "
            f"미러는 {'거부' if not got['ok'] else '통과'} — 계약이 한쪽에만 있다")
        if not got["ok"]:
            assert got["refused"], f"BreatheRefused 가 아닌 예외로 죽었다: {got['message']}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 미러를 실행할 수 없다")
@pytest.mark.parametrize("build", [_humanoid, _winged], ids=["humanoid", "winged"])
def test_the_mirror_matches_on_frames_other_than_the_frozen_one(build, tmp_path):
    """줄의 **다른** 프레임에서도 굽기와 미러가 같아야 한다.

    사이드카에는 해부가 한 벌뿐이고 미러는 그걸 그대로 쓴다. 굽기가 프레임마다 다시
    재던 시절엔 깜빡임처럼 프레임이 조금만 달라도 갈렸다 (슉슉이 실측 2026-07-25:
    골든 픽스처 idle 4프레임에서 최대 220바이트, 불투명 픽셀 **수**까지 불일치).
    지금은 줄 전체가 한 벌을 쓰므로 같아야 한다 — 이 테스트가 그 계약이다.

    기존 미러 테스트는 `freeze_anatomy(src)` 한 프레임만 비교해 이 경로가 통째로
    그물 밖이었다."""
    frozen_src = build()
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(frozen_src, cfg)

    # 같은 줄의 '다른 프레임' — 알파 무변 RGB 덧칠 (큐레이터 픽셀 편집기의 문서화된 기능)
    other = frozen_src.copy()
    px = other.load()
    box = solid_alpha_bbox(other)
    axis = (box[0] + box[2]) // 2                    # 몸통 축 — 반드시 불투명하다
    painted = 0
    for y in range(box[1] + 4, box[1] + 14):
        for x in range(axis - 3, axis + 4):
            if px[x, y][3] >= 128:
                px[x, y] = (12, 14, 18, px[x, y][3])
                painted += 1
    assert painted, "덧칠할 불투명 픽셀을 못 찾았다"
    assert other.tobytes() != frozen_src.tobytes(), "픽스처가 실제로 달라야 의미가 있다"

    anat, redetected = resolve_anatomy(frozen_src, cfg)   # 기준은 얼린 프레임
    assert redetected is False, "기준 프레임은 지문이 맞아야 한다"

    payload = {
        "script": str(CURATOR_BREATHE), "width": other.width, "height": other.height,
        "cfg": {"depth": cfg["depth"], "breaths": cfg["breaths"], "lag": cfg["lag"],
                "anatomy": cfg["anatomy"]},
        "cases": [{"phase": p, "rgba": _rgba(other)} for p in PHASES],
        "out": str(tmp_path / "js.json"),
    }
    (tmp_path / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "harness.cjs").write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), str(tmp_path / "harness.cjs"),
                           str(tmp_path / "payload.json")], capture_output=True, text=True)
    assert proc.returncode == 0, f"node 하네스 실패:\n{proc.stderr}"
    js = json.loads((tmp_path / "js.json").read_text(encoding="utf-8"))

    for phase, got in zip(PHASES, js):
        assert got["ok"], f"위상 {phase:.4f}: JS 가 거부했다 — {got['message']}"
        py = _rgba(phase_frame(other, cfg, phase, anat))   # 줄 해부 한 벌로 굽는다
        diff = sum(1 for a, b in zip(py, got["data"]) if a != b)
        assert diff == 0, f"{build.__name__} 위상 {phase:.4f}: 얼린 프레임 밖에서 {diff}바이트 갈렸다"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 미러를 실행할 수 없다")
def test_the_mirror_refuses_the_same_strain_cap(tmp_path):
    """행당 변형 상한 거부도 양쪽이 같아야 한다 (슉슉이 note: JS 상한 그물이 비어 있었다).

    클리핑 거부만 그물이 있고 상한 거부는 없어서, JS 가드를 죽여도 미러 테스트가
    전부 통과했다. 동작은 동치였지만 회귀를 막는 것이 없었다."""
    # 강체 경계를 아래로 내리면 변형 구간이 좁아지고 정규화 계수가 커져 행당 변형이
    # 상한을 넘는다 — 사람이 `rigid_row` 로 도달할 수 있는 실제 조합이다.
    src = _humanoid()
    base = dict(CFG)
    base["anatomy"] = freeze_anatomy(src, base)
    height = base["anatomy"]["height"]
    cfg = {**base, "rigid_row": int(height * 0.78)}
    anat, _ = resolve_anatomy(src, cfg)
    cfg["anatomy"] = {**anat.as_dict(), "fingerprint": base["anatomy"]["fingerprint"]}
    depth = next((round(0.005 * i, 3) for i in range(1, 41)
                  if row_strain(anat, round(0.005 * i, 3)) > MAX_ROW_STRAIN), None)
    assert depth is not None, (
        f"경계 {cfg['rigid_row']}/{height} 에서도 스키마 범위 안에서 상한을 못 넘긴다 "
        f"(depth 0.20 에서 {row_strain(anat, 0.20):.3f}) — 상한이 도달 불가면 계약이 죽은 것이다")
    over = {**cfg, "depth": depth}

    with pytest.raises(SystemExit):
        phase_frame(src, over, 0.25, anat)

    payload = {
        "script": str(CURATOR_BREATHE), "width": src.width, "height": src.height,
        "cfg": {"depth": depth, "breaths": cfg["breaths"], "lag": cfg["lag"],
                "anatomy": cfg["anatomy"]},
        "cases": [{"phase": 0.25, "rgba": _rgba(src)}],
        "out": str(tmp_path / "js.json"),
    }
    (tmp_path / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "harness.cjs").write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), str(tmp_path / "harness.cjs"),
                           str(tmp_path / "payload.json")], capture_output=True, text=True)
    assert proc.returncode == 0, f"node 하네스 실패:\n{proc.stderr}"
    got = json.loads((tmp_path / "js.json").read_text(encoding="utf-8"))[0]
    assert not got["ok"] and got["refused"], \
        f"굽기는 상한으로 거부하는데 미러는 통과시켰다: {got}"
