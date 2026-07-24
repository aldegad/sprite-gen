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

# 판정 면제는 **버퍼 해상도 = 표시 해상도** 인 서피스뿐이다 (JS 가 표시 크기에 맞춰
# 직접 래스터를 그리는 경우). `snap-canvas` 는 한때 여기 있었지만 소스 표시 모드가
# 소스 해상도로 그리게 되면서 전제가 깨졌고, 면제가 남아 있는 동안 이 계약이 그
# 표시면을 통째로 놓쳤다 (콩콩이 R1, 2026-07-24). 화이트리스트를 넓히기 전에
# "이 서피스는 버퍼와 표시가 정말 같은가" 를 먼저 실측하라.
INTERNAL_SURFACES = ("cmp-canvas",)


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


def test_every_display_surface_is_under_the_decision():
    """표시면 목록에 캔버스 표시면이 빠지면 계약이 그 경로를 통째로 놓친다.

    실사고 (콩콩이 R1, 2026-07-24): 소스 표시 캔버스의 **버퍼**를 소스 해상도로
    올려놓고 `PIXEL_SCALE_TARGETS` 에서는 제외해 둬서, 896 버퍼가 152 표시로
    nearest 데시메이션(2.9% 생존)됐다. 버퍼 해상도를 올린 것과 표시 샘플링을
    지킨 것은 다른 일이다 — 목표한 양(표시 샘플링)을 재야 한다.
    """
    display = SRC["display.js"]
    assert '".stage canvas.snap-canvas"' in display, (
        "실제 표시면인 snap-canvas 가 판정 대상에서 빠졌다"
    )


def test_new_display_surfaces_get_judged_when_rendered():
    """표시면을 새로 만드는 렌더 함수는 판정을 호출한다.

    실사고 (콩콩이 R2, 2026-07-24): `renderState` 가 만든 미리보기 캔버스는
    load 위임 훅(<img> 전용)이 못 덮어서, 리사이즈가 올 때까지 뭉갠 채로 남았다.
    리사이즈가 소급 복구한다는 것이 "판정식은 맞고 부르는 지점이 빠졌다" 는 증거였다.
    """
    assert re.search(
        r"function renderState\(.*?\n\}", SRC["cards.js"], re.S
    ), "renderState 를 찾지 못했다"
    body = re.search(r"function renderState\(.*?\n\}", SRC["cards.js"], re.S).group(0)
    assert "syncPixelScaling(wrap)" in body, (
        "renderState 가 새로 만든 표시면에 판정을 호출하지 않는다 — "
        "rebuildState 경로 전체가 판정 없이 렌더된다"
    )


def test_source_display_canvas_renders_at_source_resolution():
    """실제 표시면이 캔버스일 때도 원본 해상도를 지킨다.

    실사고 (수홍 2026-07-24, DOM 을 직접 열어 지목): 픽셀 편집이 있는 프레임은
    `<img>` 가 `visibility: hidden` 이 되고 `snap-canvas` 가 실제 표시면이 되는데,
    그 캔버스가 **항상 셀 크기(64×64)** 라서 896px 원본 트윈이 64픽셀로 파괴됐다.
    표시 계약을 이미지에만 걸면 이 경로가 통째로 빠져나간다.

    양자화 모드(픽셀퍼펙트 결과 미리보기)는 셀 크기가 목적이므로 ss=1 이며,
    두 모드는 배타적이다.
    """
    tr = SRC["transforms.js"]
    display = SRC["display.js"]
    assert "superSampleFor" in display, "소스 해상도 배율 계산자가 없다"
    assert re.search(r"const ss = quantize \? 1 : superSampleFor\(", tr), (
        "소스 표시 모드에서 캔버스가 소스 해상도로 올라가지 않는다"
    )
    assert re.search(r"canvas\.width = cw \* ss", tr) and re.search(r"canvas\.height = ch \* ss", tr), (
        "캔버스가 셀 크기에 고정돼 있다 — 고해상 원본이 파괴된다"
    )
    # 좌표 계약: 저장·입력은 셀 공간, ss 는 렌더 해상도만 올린다
    assert re.search(r"sctx\.fillRect\(x \* ss, y \* ss, ss, ss\)", display), (
        "픽셀 편집이 슈퍼샘플 공간으로 매핑되지 않는다"
    )
    assert re.search(r"const ss = canvas\.width / cw", display), (
        "스포이드가 슈퍼샘플 배율을 반영하지 않는다 — 다른 픽셀을 집는다"
    )
