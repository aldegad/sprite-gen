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


# ── 캔버스 버퍼 사이징 지점의 분류표 (계약) ──
# 같은 병이 호출부를 옮겨다니며 세 번 재발했다 (R2 renderState → R3 renderTick).
# 호출부 이름으로 테스트를 쓰면 네 번째 이사 자리가 남으므로, **버퍼 크기를 정하는
# 모든 지점**을 열거하고 각각을 분류하게 강제한다. 미분류 지점은 실패한다 —
# 새 캔버스를 만드는 사람이 "이건 사용자가 보는 표시면인가" 를 반드시 답하게.
#
#   display  = DOM 에 붙어 사용자가 본다. 버퍼≠표시일 수 있으므로 판정 필수.
#   offscreen= 합성용 작업 캔버스. 화면에 안 붙으므로 판정 무의미.
#   rect     = 표시 영역 실측으로 버퍼를 잡는다(버퍼=표시). nearest 가 무연산.
#   not-canvas = 캔버스가 아닌 객체의 width 속성.
CANVAS_BUFFER_SITES = {
    ("cards.js", "base"): "offscreen",
    ("row-export.js", "base"): "offscreen",
    ("row-export.js", "out"): "offscreen",
    ("display.js", "src"): "offscreen",
    ("display.js", "tmp"): "offscreen",
    ("display.js", "canvas"): "rect",          # 격자 오버레이 — stage 실측 크기
    ("base-editor.js", "probe"): "offscreen",
    ("base-editor.js", "logical"): "offscreen",
    ("transforms.js", "canvas"): "display",    # snap-canvas (소스/양자화 모드)
    ("zoom-editor.js", "zoomView"): "not-canvas",
    ("zoom-editor.js", "c"): "offscreen",
    ("zoom-editor.js", "work"): "offscreen",
    ("zoom-editor.js", "base"): "offscreen",
    ("zoom-editor.js", "cvs"): "offscreen",
    ("zoom-editor.js", "b"): "offscreen",
    ("zoom-editor.js", "prev"): "display",     # 마키 라이브 프리뷰
    ("zoom-editor.js", "bcanvas"): "display",  # 호흡 모드 줌 재생면
    ("zoom-editor.js", "cv"): "display",       # 호흡 필름스트립 셀
    ("compare.js", "base"): "offscreen",
    ("compare.js", "canvas"): "rect",          # cmp-canvas — 버퍼=표시
    ("breathe.js", "out"): "offscreen",
    ("breathe.js", "c"): "offscreen",
}

_JUDGE = re.compile(r"(applyPixelScaling|syncPixelScaling)\s*\(")


def _buffer_sites():
    """`<var>.width = ...` 버퍼 대입 지점 (CSS `.style.width` 제외)."""
    for name, text in SRC.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = re.search(r"(?<!\.style)\b(\w+)\.width\s*=", line)
            if m and ".style.width" not in line:
                yield name, m.group(1), i, lines


def test_every_canvas_buffer_site_is_classified():
    """새 버퍼 사이징 지점이 생기면 분류를 강제한다 — 조용히 계약 밖에 놓이지 않게."""
    unknown = sorted({(f, v) for f, v, _, _ in _buffer_sites()} - set(CANVAS_BUFFER_SITES))
    assert not unknown, (
        f"분류되지 않은 캔버스 버퍼 사이징 지점: {unknown}. "
        "사용자가 보는 표시면이면 'display'(판정 필수), 합성용이면 'offscreen', "
        "표시 실측으로 잡으면 'rect' 로 CANVAS_BUFFER_SITES 에 등록하라."
    )


def test_display_canvases_are_judged_where_their_buffer_is_set():
    """표시 대상 캔버스의 버퍼를 정하는 지점은 판정을 부른다.

    실사고 2건, 같은 병이 호출부만 바꿔 재발 (2026-07-24):
    - R2: `renderState` 가 만든 캔버스가 판정을 못 받아 리사이즈 전까지 뭉갬.
    - R3: `renderTick`(호흡 모드 줌)이 버퍼만 바꾸고 판정을 안 불러 64→758 보간
          (화면 고유색 114 → 10,526, 92배 번짐).
    둘 다 "리사이즈하면 소급 복구" 라는 같은 서명이었다 — 판정식은 맞고 부르는
    지점이 빠진 것. 그래서 계약을 호출부 이름이 아니라 **버퍼를 정하는 자리**로 쓴다.
    """
    # 창 40줄 = "같은 렌더 블록" 의 근사치다 (현행 실측 거리 10·23·32줄 — 부착이
    # 그리기 뒤에 오는 경로가 있어 바로 다음 줄을 강제할 수 없다). 잡으려는 실패는
    # "판정이 아예 없음" 이고 그건 블록 전체에 없으므로 이 창으로 충분하다.
    # 진짜 이빨은 위의 분류표다 — 새 표시면은 등록 없이는 통과 못 한다.
    missing = []
    for name, var, idx, lines in _buffer_sites():
        if CANVAS_BUFFER_SITES.get((name, var)) != "display":
            continue
        window = "\n".join(lines[idx: idx + 40])
        if not _JUDGE.search(window):
            missing.append(f"{name}:{idx + 1} ({var})")
    assert not missing, (
        f"표시 대상 캔버스의 버퍼를 바꾸고 판정을 안 부르는 지점: {missing}. "
        "버퍼 크기는 기하이므로 바꾼 자리에서 applyPixelScaling 을 불러라."
    )


def test_no_inline_unconditional_pixelated_grant():
    """인라인 `imageRendering` 무조건 부여 금지 — CSS grep 에 안 잡혀 계약 밖에 숨는다.

    실사고 (콩콩이 R4, 2026-07-24): 마키 프리뷰가 camelCase 인라인으로 무조건
    부여하고 있었는데, CSS 규칙 본문과 `px-upscale` 문자열만 훑는 테스트가
    구조적으로 못 봤다.
    """
    offenders = []
    for name, text in SRC.items():
        for i, line in enumerate(text.splitlines()):
            if "imageRendering" in line and "pixelated" in line:
                offenders.append(f"{name}:{i + 1}")
    assert not offenders, (
        f"인라인 무조건 nearest 부여: {offenders}. 판정 소유자(.px-upscale)에게 맡겨라."
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
