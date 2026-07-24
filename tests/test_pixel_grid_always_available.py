# SPDX-License-Identifier: Apache-2.0
"""픽셀 격자 컨트롤은 조건부로 숨지 않는다 (계약).

수홍 2026-07-24 ("10번 넘게 말한 것 같은데"): 격자와 픽셀퍼펙은 스크립트가
계산하는 것이므로 어떤 이미지에서도 사용 가능해야 한다. 그런데 임포트 런의
1:1 픽셀아트에서 격자 버튼이 통째로 사라졌다.

단일 근본원인: 피치 측정이 "측정값 1"(항등 — 참)과 "측정 불가"(모름)를 둘 다
None 으로 뭉갰고, 그 None 이 서버·클라 7겹 게이트를 타고 컨트롤을 숨겼다.

계약: 측정은 **실패할 수 없는 정확 판정**(k×k 블록 단색성, k=1 은 자명히 참)이고,
따라서 "격자 모름" 상태가 없으며, 그 결과 컨트롤을 숨길 근거도 없다.

회귀는 호출부 이름이 아니라 계약으로 쓴다 — 오늘 `curator-pixel-scaling-ssot`
에서 호출부 이름 회귀가 같은 병을 세 호출부에 걸쳐 세 번 놓친 교훈.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
CURATOR = SCRIPTS / "curator"
SRC = {p.name: p.read_text(encoding="utf-8") for p in (CURATOR / "src").glob("*.js")}
SERVE = (SCRIPTS / "serve_curation.py").read_text(encoding="utf-8")

sys.path.insert(0, str(SCRIPTS))


def _pitch(path):
    from serve_curation import detect_pixel_pitch  # noqa: PLC0415
    return detect_pixel_pitch(path)


def test_measurement_never_fails(tmp_path):
    """측정은 어떤 입력에도 ≥1 을 돌려준다 — None/0 이 없어야 게이트가 못 생긴다."""
    cases = {}
    solid = Image.new("RGBA", (16, 16), (10, 20, 30, 255))
    solid.save(tmp_path / "solid.png")
    cases["단색"] = tmp_path / "solid.png"
    empty = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    empty.save(tmp_path / "empty.png")
    cases["완전투명"] = tmp_path / "empty.png"
    tiny = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
    tiny.save(tmp_path / "tiny.png")
    cases["1x1"] = tmp_path / "tiny.png"
    noise = Image.new("RGBA", (13, 7))
    noise.putdata([((x * 37) % 256, (y * 91) % 256, 0, 255)
                   for y in range(7) for x in range(13)])
    noise.save(tmp_path / "noise.png")
    cases["소수크기 노이즈"] = tmp_path / "noise.png"
    for label, p in cases.items():
        v = _pitch(p)
        assert isinstance(v, int) and v >= 1, f"{label}: 측정이 {v!r} — 실패 상태가 존재하면 안 된다"
    assert _pitch(tmp_path / "does-not-exist.png") >= 1, "없는 파일도 항등으로 답해야 한다"


def test_measurement_is_exact_not_estimated(tmp_path):
    """k 배 확대본은 정확히 k, 1:1 은 정확히 1 — 주기성 추정으로 되돌아가면 깨진다."""
    base = Image.new("RGBA", (8, 8))
    base.putdata([((x * 31) % 256, (y * 17) % 256, (x ^ y) * 8 % 256, 255)
                  for y in range(8) for x in range(8)])
    base.save(tmp_path / "k1.png")
    assert _pitch(tmp_path / "k1.png") == 1, "1:1 픽셀아트는 항등 1 이어야 한다"
    for k in (2, 3, 4, 8):
        up = base.resize((8 * k, 8 * k), Image.Resampling.NEAREST)
        up.save(tmp_path / f"k{k}.png")
        assert _pitch(tmp_path / f"k{k}.png") == k, f"{k}배 확대본의 측정이 {k} 가 아니다"


def test_server_never_reports_grid_as_absent():
    """서버 스냅샷이 격자 부재를 보고하는 경로가 없어야 한다."""
    assert re.search(r"has_grid\s*=\s*True", SERVE), (
        "has_grid 가 조건식으로 되돌아갔다 — 조건이 곧 컨트롤 숨김이다"
    )
    # pixelPerfect 가 null 로 접히면 클라의 contractScale 이 사라진다
    assert not re.search(r'"pixelPerfect":[^\n]*else None', SERVE), (
        "pixelPerfect 를 None 으로 접는 분기 부활 — 격자 scale 이 사라진다"
    )


def test_grid_controls_are_not_gated_in_the_client():
    """클라에 '격자 가능 줄' 게이팅 메커니즘 자체가 없다.

    콩콩이 R3 교훈: 존재-정규식은 옆에 게이트를 새로 얹으면 통과한다(5줄 mutant 실증).
    그래서 특정 줄 모양이 아니라 **knob 의 부재**를 고정한다 — gridCapableStates 집합과
    pxWrap 재숨김이 코드에 없으면 그 mutant 가 뒤집을 스위치 자체가 없다.
    (클라 런타임 행동 핀은 pytest 밖 — 실브라우저 감사로 보완, plan Verification)
    """
    for name, text in SRC.items():
        assert "gridCapableStates" not in text, f"{name}: 게이팅 집합 knob 부활"
    boot = SRC["boot.js"]
    assert re.search(r"pxWrap\.hidden\s*=\s*false", boot), "격자 전체 토글 상시 노출이 사라졌다"
    assert not re.search(r"pxWrap\.hidden\s*=\s*(?!\s*false\b)", boot), (
        "pxWrap.hidden 에 false 외 값을 넣는 코드 — 재숨김 경로"
    )
    assert re.search(r"const showGridToggle\s*=\s*true", SRC["cards.js"]), (
        "줄별 격자 토글이 조건부로 숨겨진다"
    )


def test_grid_spacing_never_resolves_to_null():
    """격자 간격은 항상 정해진다 (줄별 실측 > 계약 > 항등 1)."""
    display = SRC["display.js"]
    assert re.search(
        r"run\.pixelPerfect && run\.pixelPerfect\.scale\)\s*\|\|\s*1\)", display
    ), "scale 이 null 로 떨어지는 경로 부활 — 오버레이가 조건부로 사라진다"
