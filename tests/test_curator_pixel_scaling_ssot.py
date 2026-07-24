# SPDX-License-Identifier: Apache-2.0
"""표시 샘플링 판정(nearest 여부)의 SSoT 회귀.

수홍 2026-07-24 "절대 원본 아님": 고해상 orig 트윈이 축소 표시되는 카드에서
nearest 데시메이션을 겪어(896px → 152px, 전체의 2.1%만 표본) 화면이 양자화된
형태로 보였다. 원인은 `.stage img { image-rendering: pixelated }` 무조건 규칙이
`clientWidth > naturalWidth` 조건 판정을 덮어써 죽은 코드로 만든 것 —
같은 질문에 여섯 군데가 다르게 답하고 있었다 (원칙 1·2).

계약: nearest 부여는 `display.js applyPixelScaling` 한 곳만 하고, CSS 는
`.px-upscale` 클래스에만 반응한다. 내부 렌더 서피스(snap-canvas·cmp-canvas)는
표시 해상도로 직접 그려지므로 예외이며 각자 규칙을 유지한다.
"""
from __future__ import annotations

import re
from pathlib import Path

CURATOR = Path(__file__).resolve().parent.parent / "scripts" / "curator"
CSS = (CURATOR / "curator.css").read_text(encoding="utf-8")
SRC = {p.name: p.read_text(encoding="utf-8") for p in (CURATOR / "src").glob("*.js")}

# 표시 해상도로 직접 그려지는 내부 서피스 — 판정 대상이 아니다.
INTERNAL_SURFACES = ("snap-canvas", "cmp-canvas")


def _rule_selectors_with_pixelated() -> list[str]:
    """`image-rendering: pixelated` 을 켜는 CSS 규칙의 셀렉터 목록."""
    out = []
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        selector, body = block.group(1), block.group(2)
        if "image-rendering" in body and "pixelated" in body:
            out.append(" ".join(selector.split()))
    return out


def test_pixelated_is_granted_only_through_the_upscale_class():
    """CSS 에서 nearest 를 켜는 규칙은 `.px-upscale` 과 내부 서피스뿐이다."""
    offenders = [
        sel for sel in _rule_selectors_with_pixelated()
        if "px-upscale" not in sel and not any(s in sel for s in INTERNAL_SURFACES)
    ]
    assert not offenders, (
        "무조건 nearest 규칙이 표시면에 남아 있다 — 축소 표시에서 데시메이션이 되고 "
        f"조건 판정을 덮어쓴다: {offenders}"
    )


def test_no_blanket_stage_image_rule():
    """회귀 고정: `.stage img` 자체에 nearest 를 거는 규칙이 되살아나면 안 된다."""
    for sel in _rule_selectors_with_pixelated():
        assert not re.search(r"(^|,)\s*\.stage img\s*(,|$)", sel), (
            f"`.stage img` 무조건 nearest 규칙 부활: {sel}"
        )


def test_single_writer_of_the_upscale_class():
    """`px-upscale` 을 부여/해제하는 코드는 display.js 한 곳뿐이다."""
    writers = {
        name: [ln.strip() for ln in text.splitlines() if "px-upscale" in ln]
        for name, text in SRC.items()
        if "px-upscale" in text
    }
    assert set(writers) == {"display.js"}, (
        f"판정이 여러 파일로 흩어졌다 (SSoT 위반): { {k: v for k, v in writers.items()} }"
    )


def test_decision_reads_display_geometry_not_a_heuristic():
    """판정 입력은 표시 기하(표시폭 vs 원본폭)여야 한다 — 셀 크기 추측 금지."""
    display = SRC["display.js"]
    assert "classList.toggle(\"px-upscale\", shown > natural)" in display, (
        "applyPixelScaling 이 표시폭>원본폭 판정을 잃었다"
    )
    # 셀 폭 휴리스틱(`run.cell.width < 160`)으로 되돌아가지 않았는지 확인
    for name, text in SRC.items():
        assert "cell.width < 160" not in text, f"{name}: 셀 폭 휴리스틱 부활"


def test_decision_is_re_evaluated_on_geometry_change():
    """기하가 바뀌면 재평가된다 — 1회성 부여로 되돌아가면 축소/확대 전환을 놓친다."""
    assert "syncPixelScaling()" in SRC["display.js"], "sizePxGrids 가 재평가를 부르지 않는다"
    assert re.search(r"sizePxGrids\(\)\s*\{[^}]*syncPixelScaling\(\)", SRC["display.js"], re.S), (
        "sizePxGrids(기하 갱신 지점)가 syncPixelScaling 을 부르지 않는다"
    )
    assert 'addEventListener("resize"' in SRC["boot.js"], "창 크기 변경 재평가 훅이 없다"


def test_decision_is_re_evaluated_when_the_image_source_changes():
    """픽셀퍼펙트 토글은 **레이아웃이 아니라 원본 기하**를 바꾼다 — 로드에도 물려야 한다.

    실사고 (수홍 2026-07-24 "달리기 시퀀스... 절대 원본 아님"): 토글을 끄면 src 가
    64px 출력 → 704px 원본 트윈으로 갈리는데, 레이아웃 이벤트만 보던 재평가는
    그 순간을 못 잡아 확대용 nearest 가 축소된 원본 위에 stale 로 남았다.
    """
    display = SRC["display.js"]
    assert "installPixelScalingLoadHook" in display, "이미지 로드 재평가 훅이 없다"
    # load 는 버블링하지 않는다 — capture 로 받아야 위임이 성립한다
    assert re.search(
        r'addEventListener\(\s*"load".*?\}\s*,\s*true\s*\)', display, re.S
    ), "load 위임 훅이 capture 모드가 아니다 (load 는 버블링하지 않는다)"
    assert "installPixelScalingLoadHook()" in SRC["boot.js"], "부팅에서 로드 훅을 설치하지 않는다"
    # 같은 src 재대입(캐시)은 load 가 안 뜨므로 변형 스왑 경로가 직접 한 번 더 답한다
    assert re.search(
        r"refreshVariantImages\(\)\s*\{.*?applyPixelScaling\(el\)", display, re.S
    ), "src 스왑 경로가 판정을 다시 받지 않는다"
