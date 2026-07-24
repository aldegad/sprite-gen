# SPDX-License-Identifier: Apache-2.0
"""표시 파이프라인 단일화 계약 (수홍 2026-07-24 "구현체가 몇 종류라 노이즈").

한 파이프라인("이 프레임을 큐레이션 상태로 보여라")의 구현이 세 갈래였다:
엔진 트윈 파일 / 서버 추정 스냅 프리뷰 / 클라 양자화 캔버스 — 그리고 표시면도
img·canvas 두 갈래. 오늘 표시 결함 5건(v1.56.85~89)이 전부 그 fork 의 한쪽만
보다 생겼다. 계약: 스테이지 표시는 캔버스 렌더러 하나, 퍼펙 양자화 격자는 표시
격자와 같은 측정값, 레거시(서버 프리뷰)와 게이팅 knob 은 코드에서 사라진다.

주의: 텍스트 어서션은 구현의 글자 모양이 아니라 **메커니즘의 부재**를 고정한다
(콩콩이 R3 교훈 — 존재-정규식은 옆에 새 게이트를 얹으면 통과한다. 부재-어서션은
그 메커니즘을 되살리는 코드 자체를 금지한다). 클라 런타임 행동 핀은 pytest 밖이라
실브라우저 감사(plan Verification)로 보완한다.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURATOR = ROOT / "scripts" / "curator"
SRC = {p.name: p.read_text(encoding="utf-8") for p in (CURATOR / "src").glob("*.js")}
SERVE = (ROOT / "scripts" / "serve_curation.py").read_text(encoding="utf-8")
INDEX = (CURATOR / "index.html").read_text(encoding="utf-8")


def _no_comment(text: str, lang: str) -> str:
    """주석 제거본 — 계약은 코드에 걸고, 회고 주석은 허용한다."""
    if lang == "js":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"(?m)^\s*//.*$|(?<=[;{})\s])//[^\n]*$", "", text)
    return re.sub(r"(?m)#.*$", "", text)


def test_stage_display_is_the_canvas_renderer_only():
    """applyCardTransform 에 img 표시 분기가 없다 — 표시면은 캔버스 하나다."""
    tr = SRC["transforms.js"]
    assert 'el.style.visibility = "hidden"' in tr, "img 가 숨김 로더로 강등되지 않았다"
    body = _no_comment(tr, "js")
    assert 'el.style.visibility = ""' not in body, (
        "img 를 표시면으로 되돌리는 분기 부활 — 표시면이 다시 두 갈래가 된다"
    )
    assert 'canvas.style.display = "none"' not in body, (
        "applyCardTransform 이 캔버스를 끄는 분기 부활 (img 표시 모드의 서명)"
    )


def test_quantize_grid_is_the_displayed_grid():
    """퍼펙 양자화 격자 = 격자 오버레이가 그리는 측정값 — 격자 진실은 하나다."""
    store = SRC["store.js"]
    m = re.search(r"function snapScaleFor\(.*?\n\}", store, re.S)
    assert m, "snapScaleFor 를 찾지 못했다"
    body = m.group(0)
    assert "st.pixelScale" in body and re.search(r"\|\|\s*1;", body), (
        "트윈 없는 줄의 양자화가 측정 k(pixelScale)로 떨어지지 않는다 — "
        "격자 따로 퍼펙 따로가 재발한다"
    )
    assert "run.pixelPerfect.scale" in body, "pp 런의 계약 scale(굽기 거울)이 사라졌다"


def test_pp_toggle_is_ungated_on_every_surface():
    """퍼펙 토글은 줄 컨트롤·줌 모달 어디에도 게이트가 없다."""
    assert re.search(r"const showPpToggle\s*=\s*true", SRC["cards.js"]), (
        "줄별 퍼펙 토글이 조건부로 돌아갔다"
    )
    assert not re.search(
        r"ppTwinStates\.has\([^)]*\)\)?\s*controls\.appendChild\(makePpToggle",
        SRC["zoom-editor.js"],
    ), "줌 모달 퍼펙 토글이 트윈 줄로 게이트됐다 (수홍 재현: 확대화면에 버튼 없음)"


def test_legacy_preview_and_gating_knobs_are_gone():
    """레거시 구현·게이팅 메커니즘의 심볼이 코드에 존재하지 않는다.

    (부재 어서션 — 이 심볼이 없으면 콩콩이 R3 의 5줄 mutant 가 뒤집을 knob 자체가 없다)
    """
    dead = ["_pixel_preview_meta", "pixelPreviewUrl", "pixelPreviewDeferred",
            ".pixel-preview", "ppPreviewStates", "ppAvailable", "gridCapableStates"]
    surfaces = {"serve_curation.py": _no_comment(SERVE, "py"),
                "index.html": INDEX}
    for name, text in SRC.items():
        surfaces[name] = _no_comment(text, "js")
    for sym in dead:
        hits = [n for n, text in surfaces.items() if sym in text]
        assert not hits, f"레거시 심볼 {sym!r} 이 살아 있다: {hits}"


def test_server_snapshot_grid_behavior(tmp_path):
    """실행 행동 핀 (콩콩이 R3): 진짜 1:1 임포트 런을 만들어 스냅샷을 어서션한다."""
    import subprocess
    import sys
    import json
    from PIL import Image

    pngs = tmp_path / "pngs" / "rowa"
    pngs.mkdir(parents=True)
    im = Image.new("RGBA", (16, 16))
    im.putdata([((x * 31) % 256, (y * 17) % 256, 40, 255) for y in range(16) for x in range(16)])
    im.save(pngs / "1-a.png")
    im.save(pngs / "2-b.png")
    out = tmp_path / "run"
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "unpack_atlas_run.py"),
         "--pngs-dir", str(tmp_path / "pngs"), "--out-dir", str(out), "--force"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sys.path.insert(0, str(ROOT / "scripts"))
    import serve_curation
    snap = serve_curation.build_run_state(out)
    assert snap["contract"]["grid"] is True
    assert snap["pixelPerfect"] and snap["pixelPerfect"]["scale"] >= 1
    for st in snap["states"]:
        assert isinstance(st["pixelScale"], int) and st["pixelScale"] >= 1, (
            f"{st['name']}: pixelScale={st['pixelScale']!r} — 격자 없는 줄이 다시 생겼다"
        )
        for fr in st["frames"]:
            assert "pixelPreviewUrl" not in fr, "레거시 프리뷰 필드가 스냅샷에 되살아났다"
