# SPDX-License-Identifier: Apache-2.0
"""결정론 호흡(idle breathing) — 봉투 기반 스쿼시&스트레치 (변형 강도 장).

호흡은 프레임 선택(깜빡임)과 직교하는 변조 레이어다 (수홍 확정 2026-07-18).
설정은 curation.json 사이드카 `states.<state>.breathe` (curation.state_breathe 가
정규화 SSoT), 굽기는 compose/GIF 가 재생 시퀀스 위에 이 모듈의 수학으로 한다.
AI 개입 0 — 같은 입력이면 항상 같은 출력.

## 왜 분할선이 아니라 봉투인가 (2026-07-25 교체)

구 방식은 수평 분할선 위의 행을 정수 px 내리는 것이었다. 실제 출력은 **2상태 토글**
이라(측정: idle GIF 6프레임이 `f0==f2==f4`, `f1==f3==f5`) 호흡이 아니라 1px 진동으로
읽혔다. 머리를 잘라 붙이는 변형으로 바꿔봐도 잘린 단면이 목에 가로선으로 드러났다.

붙이는 연산이 있는 한 이음매는 없앨 수 없다. 그래서 **자르지 않는다.** 스프라이트
전체에 연속 변형장 하나를 걸고 그 강도를 목(또는 얼굴 아래)에서 0 으로 떨군다:

    env(y) = 발끝램프(y) x 강체테이퍼(y)          # 0 = 원본 그대로, 1 = 최대 변형
    g(y,t) = depth * norm * wave(t - lag*u(y)) * env(y)
    sx(y)  = 1 + g                                 # 가로
    sy(y)  = 1 / (1 + g)                           # 세로 (면적 보존)

## 불변식 (테스트가 지키는 것)

1. **강체 구간은 항등이다 — 근사가 아니다.** env=0 인 행은 g=0 -> 가로 사상이 원본
   좌표 그대로, sy=1 -> 누적 높이가 정확히 1씩 증가 -> 행 복제/삭제 0.
   그래서 그 구간은 프레임 간 **비트 동일**하다. 눈·입이 몇 도트뿐인 픽셀아트에서
   3% 세로 신장도 표정을 뭉개므로 이게 핵심 계약이다.
2. **가로 사상은 단조다.** X(x) = ∫ dens(s) ds 이고 dens > 0 이므로 접힘이 구조적으로
   불가능하다. 몸통만 1+g, 부속(날개)은 1 -> 날개는 밖으로 밀리기만 하고 안 늘어난다.
3. **몸통 축 열이 가로 사상의 고정점이다.** 출력 위치를 축의 적분값 기준으로 잡으므로
   축은 언제나 제자리이고, 그래서 (a) 변형이 0 이면 사상이 진짜 항등이 되고
   (b) 배율이 변해도 스프라이트가 통째로 좌우로 튀지 않는다. bbox 중앙을 기준으로
   잡으면 축이 중앙과 다른 캐릭터가 변형 0 에서도 밀린다 (실사고 2026-07-25 새미 검증).
4. **격자를 벗어나는 연산이 없다.** 세로는 행 정수 복제/삭제, 가로는 정수 열 사상.
   보간·블렌딩·비정수 스케일이 한 번도 일어나지 않으므로 출력이 항상 정수 도트다.
   언페이크(pitch 검출)를 프레임마다 다시 할 필요가 없는 이유이자, 다시 하면 안 되는
   이유이기도 하다 (hero_v7 up_run frame-2, 2026-07-22 하모닉 오검출 붕괴).
5. **루프 길이 불변.** 출력 프레임 수 = 입력 프레임 수. 호흡이 그 안에서 breaths 회
   딱 떨어진다.

(구 테이크 방식 — exhale 프레임을 raw 테이크로 굽고 전체 재추출 — 은 v1.56.31 에서
폐기: 깜빡임×호흡 조합 불가 + 적용마다 수 분 + 팔레트 드리프트. 봉투 방식은 그 셋 중
무엇도 해소하지 않으므로 되돌릴 근거가 없다.)
"""

from __future__ import annotations

import math

from PIL import Image

from sprite_gen.anatomy import Anatomy, analyze
from sprite_gen.curation import normalize_transform
from sprite_gen.extract import solid_alpha_bbox

# 강체 경계에서 변형 강도가 0 이 되기까지의 테이퍼 반폭 (콘텐츠 높이 비율).
# 이 테이퍼가 이음매를 없앤다 — 경계에서 강도가 계단이 아니라 경사로 떨어진다.
TAPER = 0.055
# 발끝 고정 램프 — 변형 구간 아래쪽 이 비율만큼은 강도가 0 에서 올라온다 (발이 안 뜬다).
FOOT = 0.28
# 행당 변형 상한. 넘으면 조용히 깎지 않고 멈춘다 (No Silent Fallback).
MAX_ROW_STRAIN = 0.25
# 기본 진폭 — 몸통 높이 대비 총 신장량.
DEFAULT_DEPTH = 0.06
# 진행파 위상지연 (호흡 주기 비율) — 몸통 윗부분이 늦게 밀어올려 머리가 한 박자 늦게 온다.
DEFAULT_LAG = 0.10
# 한 호흡이 부드럽게 읽히려면 필요한 최소 프레임 수 (프레임을 찍어낼 때의 권고치).
SMOOTH_CYCLE_FRAMES = 6


# ── 파형 ────────────────────────────────────────────────────────────

def wave(t: float) -> float:
    """한 호흡의 기본 파형. 2차 하모닉을 살짝 섞어 정점이 뾰족해진다(들숨의 어택)."""
    return 0.86 * math.sin(2 * math.pi * t) + 0.14 * math.sin(4 * math.pi * t)


def smoothstep(a: float, b: float, x: float) -> float:
    if b <= a:
        return 1.0 if x >= b else 0.0
    u = min(1.0, max(0.0, (x - a) / (b - a)))
    return u * u * (3 - 2 * u)


# ── 변형 강도 봉투 ──────────────────────────────────────────────────

def envelope(anat: Anatomy, taper: float = TAPER, foot: float = FOOT):
    """(env(u), 진폭 정규화 계수) — u = 0(발바닥) .. 1(정수리).

    정규화: 테이퍼·발끝램프가 변형 구간을 갉아먹으므로 그대로 두면 강체 경계가 낮은
    캐릭터일수록 호흡이 작아진다. sum(env) 로 나눠 **총 신장량이 항상
    depth × 기준높이**가 되게 한다 — 진폭 숫자가 캐릭터와 무관하게 같은 뜻을 갖는다."""
    height = anat.height
    ru = anat.rigid_u
    band = max(1.5, taper * height) / max(1, height)
    foot_top = foot * ru

    def env(u: float) -> float:
        return smoothstep(0.0, foot_top, u) * (1.0 - smoothstep(ru - band, ru + band, u))

    total = sum(env(j / max(1, height - 1)) for j in range(height))
    norm = anat.basis_rows / total if total > 1e-6 else 0.0
    return env, norm


def protect(anat: Anatomy):
    """부속 보호 가중 p(x) — 1 이면 그 열은 가로로 안 늘어난다 (콘텐츠 bbox 상대 x)."""
    if not anat.has_appendage:
        return lambda x: 0.0
    t0 = anat.torso_half * 1.15
    t1 = max(t0 + 1.0, anat.max_half * 0.95)
    cx = anat.axis_x
    return lambda x: smoothstep(t0, t1, abs(x - cx))


def row_strain(anat: Anatomy, depth: float) -> float:
    """행당 최대 변형 — MAX_ROW_STRAIN 초과 여부 판정용 (관측 가능하게 노출)."""
    env, norm = envelope(anat)
    peak = max(env(j / max(1, anat.height - 1)) for j in range(anat.height))
    return depth * norm * peak


# ── 워프 ────────────────────────────────────────────────────────────

def _warp(frame: Image.Image, anat: Anatomy, depth: float, lag: float, t: float) -> Image.Image:
    """한 위상 t 의 프레임 — 캔버스 크기 불변, 발바닥 고정.

    세로는 행 국소 배율을 누적해 각 원본 행이 출력에서 몇 행을 차지하는지 정하고,
    가로는 행 안에서 위치마다 다른 밀도를 적분해 단조 사상을 만든다. 둘 다 정수
    연산이라 출력이 정수 도트다."""
    box = solid_alpha_bbox(frame)
    if not box:
        return frame.copy()
    body = frame.convert("RGBA").crop(box)
    src = body.load()
    width, height = body.size
    canvas_w, canvas_h = frame.size
    anchor_x = box[0] + anat.axis_x
    baseline = box[3]

    env, norm = envelope(anat)
    p_of = protect(anat)
    ru = anat.rigid_u

    def gain(u: float) -> float:
        e = env(u)
        if e <= 0.0:
            return 0.0
        return depth * norm * wave(t - lag * min(1.0, u / max(1e-6, ru))) * e

    # 세로 누적 — j=0 이 정수리
    heights: list[float] = []
    acc = 0.0
    for j in range(height):
        g = gain(1.0 - j / max(1, height - 1))
        acc += 1.0 if g == 0.0 else 1.0 / (1.0 + g)
        heights.append(acc)
    total = max(1, math.floor(acc + 0.5))

    out = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    dst = out.load()
    y_cursor = baseline - total
    prev = 0
    clipped = 0
    for j in range(height):
        u = 1.0 - j / max(1, height - 1)
        cur = math.floor(heights[j] + 0.5)
        reps = max(0, cur - prev)
        prev = cur
        if reps == 0:
            continue
        g = gain(u)
        if g == 0.0:
            # 변형 없음 = 원본 위치 그대로. 축 고정점 사상의 g->0 극한과 정확히 같다.
            row_map = [(box[0] + i, i) for i in range(width)]
        else:
            dens = [max(0.05, 1.0 + g * (1.0 - p_of(i))) for i in range(width)]
            edge = [0.0]
            for d in dens:
                edge.append(edge[-1] + d)
            origin = edge[anat.axis_x]       # 축을 고정점으로 — 여기가 anchor_x 에 박힌다
            # half-up 반올림: 파이썬 기본 round() 는 은행가 반올림(round(0.5)==0)이라
            # JS Math.round(half-up) 미러와 .5 경계에서 갈린다. 규칙을 명시해 고정한다.
            lo = math.floor(edge[0] - origin + 0.5)
            hi = math.floor(edge[width] - origin + 0.5)
            row_map = []
            i = 0
            for ox in range(lo, hi):
                while i < width - 1 and (edge[i + 1] - origin) <= ox:
                    i += 1
                row_map.append((anchor_x + ox, i))
        for r in range(reps):
            yy = y_cursor + r
            for ox, si in row_map:
                pixel = src[si, j]
                if not pixel[3]:
                    continue
                if 0 <= yy < canvas_h and 0 <= ox < canvas_w:
                    dst[ox, yy] = pixel
                else:
                    clipped += 1
        y_cursor += reps
    if clipped:
        # 조용히 자르지 않는다 — 정수리나 옆구리가 셀 밖으로 나가면 스프라이트가
        # 망가지고, 그건 여백이나 진폭을 사람이 정해야 하는 문제다 (No Silent Fallback).
        raise SystemExit(
            f"breathe: 늘어난 프레임이 셀 밖으로 나가 불투명 픽셀 {clipped}개가 잘린다 "
            f"(셀 {canvas_w}x{canvas_h}, 콘텐츠 {box[0]},{box[1]}-{box[2]},{box[3]}). "
            f"셀 여백을 늘리거나 depth 를 낮춰라.")
    return out


# ── 해부 결과 얼리기 / 자가 복구 ────────────────────────────────────

def _fnv1a(data: bytes) -> int:
    """FNV-1a 32비트 — **JS 미러가 똑같이 계산할 수 있어야 해서** 이 해시를 쓴다.

    SHA-256 은 브라우저에서 동기로 못 구한다(SubtleCrypto 는 async 라 렌더 루프에서 못 쓴다).
    미러가 지문을 못 구하면 프리뷰는 자기가 그리는 프레임이 얼린 해부와 맞는지 확인할
    방법이 없고, 그러면 굽기만 자가 복구하고 프리뷰는 낡은 숫자로 계속 그린다
    (슉슉이 실측 2026-07-25: 픽셀 편집 후 최대 617바이트, 불투명 픽셀 수까지 불일치).
    충돌 위험은 "프레임이 바뀌었나" 판정에는 무시할 수준이다."""
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def _num(value) -> str:
    """키에 들어갈 수의 정규형 — 파이썬과 JS 가 **같은 문자열**을 내야 한다.

    `f"{v:.6f}"` 와 `v.toFixed(6)` 는 6번째 자리가 정확히 반올림 경계일 때 갈린다(파이썬은
    십진 표현 기준 round-half-even, JS 는 이진값 기준). `repr` 계열도 지수 표기가 다르다
    (`1e-07` vs `1e-7`). 그래서 **부동소수 곱·합·floor 만** 쓴다 — 양쪽 다 IEEE754 double 이라
    같은 입력에 같은 결과가 나오는 연산이다."""
    if value is None:
        return "~"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(math.floor(float(value) * 1000000.0 + 0.5))
    return str(value)


def _canon(value) -> str:
    """키에 들어갈 값의 정규 직렬화 — dict 는 키 정렬, 수는 `_num`."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}={_canon(value[k])}" for k in sorted(value, key=str)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canon(v) for v in value) + "]"
    return _num(value)


def reference_key(*, state: str, variant: str, request_stamp: str, source_index: int,
                  source_stamp: str, pixel_ops, transform) -> str:
    """기준 프레임의 **정체성** — 그 프레임을 만드는 입력들로만 만든다. 픽셀은 안 읽는다.

    예전엔 이게 *변형 결과 RGBA* 의 해시였다. 그런데 굽기는 `apply_transform` 에서
    **BICUBIC** 으로 리샘플하고(`snap_scale` 없는 런 = 기본 런 전부) 웹뷰 캔버스는
    `imageSmoothingEnabled=false`, 즉 **NEAREST** 다. 같은 원본에 같은 변형을 걸어도 두 쪽이
    만드는 그림이 다르니 지문은 **영구 불일치**였다 — 회전·확대가 걸린 줄은 프리뷰가 영원히
    원본으로 떨어지고 영상 내보내기가 영구 차단됐으며, 안내대로 "해부를 갱신" 해도 라우트가
    또 BICUBIC 프레임으로 같은 숫자를 만들어 **절대 안 풀렸다** (슉슉이 실측 2026-07-26:
    rotate 3°에서 555px 상이, 정수 이동만 걸린 줄은 우연히 일치).

    입력을 해시하면 리샘플러가 식에서 아예 빠진다. 여기 들어가는 값은 전부 사이드카(사람의
    의도)와 서버가 준 파일 스탬프라, 웹뷰가 **동기로 정확히** 아는 것들이다. 덤으로 웹뷰가
    신선도 판정에 캔버스를 읽을 이유가 없어져서 "어느 캔버스를 쟀나" 계열 결함(9·10라운드)이
    구조적으로 사라진다.

    실패 방향도 안전하다: 런을 복사해 mtime 이 바뀌면 키가 달라져 **시끄럽게 거부**하고 한 번
    다시 잰다 — 조용히 낡은 숫자로 굽는 반대 방향이 아니다.

    ## 왜 이 재료들인가 (그리고 셀·snap 이 왜 없는가)

    재료는 전부 웹뷰가 **동기로 정확히** 아는 것이어야 한다. 사이드카 값(변종·픽셀편집·변형)은
    웹뷰가 소유한 canon 이고, 파일 스탬프 둘은 서버가 상태 응답에 실어 보낸다.

    셀 크기와 `snap_scale` 은 뺐다 — 둘 다 `sprite-request.json` 과 변종의 순수 함수인데,
    변종은 이미 키에 있고 request 는 `request_stamp` 로 통째로 덮인다. 파생값을 또 넣으면
    양쪽이 그 파생을 각자 재구현해야 하고(웹뷰의 측정 k 는 `pixel_snap_scale` 과 다른
    검출기다) 거기서 드리프트가 난다. 원인을 덮는 편이 결과를 흉내내는 것보다 안전하다."""
    # 정규화를 **키 안에서** 한다. 호출부에 맡기면 한쪽은 사이드카 원문(`{"rotate": 3}`)을,
    # 다른 쪽은 항등 머지본(7키 전부)을 넘겨 같은 프레임이 다른 키를 갖는다 — 변형이 없는
    # 프레임(대부분)이 정확히 그 모양이었다.
    return "|".join((
        "breathe-ref-v1", str(state), str(variant), str(request_stamp),
        str(int(source_index)), str(source_stamp),
        _canon(pixel_ops or {}), _canon(normalize_transform(transform)),
    ))


def anatomy_fingerprint(key: str) -> str:
    """기준 프레임 키의 지문 — 변종·소스 인덱스는 읽을 수 있게 남기고 나머지는 해시한다.

    키를 그대로 쓰면 거부 메시지는 친절해지지만 사이드카가 픽셀 편집량만큼 부푼다(칠한
    점 하나가 키 한 조각이다). 앞머리 두 조각만 남겨 "어느 변종의 몇 번 프레임" 은
    보이게 하고, 입력 전체는 해시로 접는다."""
    parts = key.split("|")
    variant, index = (parts[2], parts[4]) if len(parts) > 5 else ("?", "?")
    return f"{variant}:{index}:{_fnv1a(key.encode('utf-8')):08x}"


def resolve_anatomy(reference: Image.Image, cfg: dict) -> Anatomy:
    """**줄 전체가 공유하는 한 벌**을 기준 프레임에서 확정한다.

    해부는 **캐릭터의 속성이지 프레임의 속성이 아니다.** 깜빡임 프레임이라고 목이 옮겨
    가지 않는다. 프레임마다 다시 재면 검출 지터로 `rigid_row` 가 흔들리고(실측 2↔3),
    그러면 "강체 구간" 이 프레임 간 **같은 구간이 아니게 된다** — 이 모듈의 핵심 계약이
    프레임별 재검출 때문에 깨지는 것이다. 게다가 사이드카에는 해부가 한 벌뿐이라
    프리뷰(미러)는 그 한 벌로 그리고 굽기는 프레임마다 다른 값으로 구워 갈렸다
    (슉슉이 실측 2026-07-25: 골든 픽스처 idle 4프레임에서 최대 220바이트, 불투명 픽셀
    **수**까지 불일치 — 위치가 아니라 그림이 달랐다).

    그래서 지문은 **기준 프레임 하나**를 지킨다: 스프라이트가 바뀌면 여기서 잡혀 한 번
    다시 재고 그 사실을 보고한다. 나머지 프레임이 서로 다른 것은 정상이며 재검출 사유가
    아니다.

    `rigid_row` 는 **사람의 의도(입력)** 이고 `anatomy` 는 거기서 파생된 **캐시**다.
    그래서 얼린 값이 override 와 어긋나면 캐시가 낡은 것이므로 다시 잰다 — 얼린 값을
    그대로 쓰면 사람이 고친 숫자가 조용히 버려진다 (새미 검증 2026-07-25: cfg 33 을
    줘도 얼린 23 이 구워졌고 경고도 없었다).

    **굽기는 얼린 해부를 쓰지 않는다 — 매번 자기 기준 프레임에서 다시 잰다.** 굽기는
    진짜 프레임을 손에 들고 있으니 재는 게 언제나 옳고, 얼린 값은 웹뷰가 미리보기를
    그리려고 들고 있는 **캐시**일 뿐이다 (C11: `rigid_row` 는 의도, `anatomy` 는 캐시).
    예전엔 여기서 얼린 값을 신뢰할지 판정하려고 프레임 RGBA 를 해시했는데, 그 해시가
    웹뷰와 영구 불일치를 만드는 원흉이었다 — `reference_key` 주석 참조.

    사이드카 캐시가 실제로 구운 값과 어긋났는지는 `anatomy_report` 가 대조해서 보고한다.
    "재검출했나(proxy)" 보다 "**사이드카와 다른 값을 구웠나**" 가 원칙 6이 요구하는 바로
    그 관측이다."""
    return analyze(reference, rigid_row=cfg.get("rigid_row"))


def anatomy_report(images: list[Image.Image], cfg: dict) -> dict:
    """굽기가 실제로 쓴 해부를 manifest 에 실을 형태로 — 자가 복구를 관측 가능하게.

    줄 전체가 **한 벌**을 쓰므로 보고도 한 벌이다. 예전엔 프레임별로 재서
    `rigid_row_varies` 를 실어 보냈는데, 그건 관측이 아니라 결함의 증상이었다 —
    경계가 프레임마다 움직이면 강체 구간이 프레임 간 같은 구간이 아니다."""
    if not images:
        return {"anatomy": None, "matches_sidecar": True, "warnings": []}
    anat = resolve_anatomy(images[0], cfg)
    baked = anat.as_dict()
    frozen = cfg.get("anatomy")
    # 사이드카가 들고 있던 캐시와 실제로 구운 값의 **대조**. 캐시가 없으면 대조할 게 없다.
    # 지문 비교가 아니라 값 비교인 게 핵심이다 — 지문은 웹뷰가 자기 미리보기의 신선도를
    # 판정하는 도구이고, 여기서 궁금한 건 "사이드카 숫자와 다른 그림을 구웠나" 다.
    stale = None
    if isinstance(frozen, dict):
        stale = {k: [frozen.get(k), v] for k, v in baked.items()
                 if k in frozen and frozen.get(k) != v}
    return {
        "anatomy": baked,
        "matches_sidecar": not stale,
        "sidecar_drift": stale or None,
        "warnings": sorted(anat.warnings),
    }


def freeze_anatomy(frame: Image.Image, cfg: dict, key: str) -> dict:
    """큐레이션 시점에 해부를 1회 돌려 사이드카에 얼릴 dict 를 만든다.

    이걸 사이드카에 넣어두면 굽기와 큐레이터 웹뷰가 같은 숫자를 읽는다 — 웹뷰는
    검출을 재구현하지 않고 warp 만 미러링하면 된다 (검출 중복 제거)."""
    anat = analyze(frame, rigid_row=cfg.get("rigid_row"))
    return {**anat.as_dict(), "fingerprint": anatomy_fingerprint(key)}


# ── 재생 시퀀스 계약 (compose/GIF 진입점) ───────────────────────────

def fit_breathe_pattern(seq_len: int, cfg: dict) -> list[float]:
    """시퀀스 길이에 딱 맞는 호흡 위상 시퀀스 (길이 == seq_len, 루프 불변).

    위상은 [0, 1) 의 연속 값이다 — 구 방식의 정수 분할선 단계가 아니다. breaths 회가
    시퀀스 안에서 정확히 반복되므로 루프 이음매가 없고, 등분 보정도 필요 없다.

    **정수 나머지를 먼저 취한다.** `(i*breaths/seq_len) % 1.0` 로 쓰면 수학적으로 같은
    위상이 서로 다른 double 이 되어(18슬롯 3호흡: 유니크 6 -> 14) 아틀라스 칸 재사용이
    표현 노이즈로 깨진다. 분자를 정수로 접고 한 번만 나누면 반복 위상이 비트 동일하다
    (새미 검증 2026-07-25: 시트 1344x192 -> 576x192)."""
    if seq_len <= 0:
        return []
    breaths = max(1, int(cfg.get("breaths", 1)))
    return [((i * breaths) % seq_len) / seq_len for i in range(seq_len)]


def fitted_breath_count(seq_len: int, cfg: dict) -> int:
    """실제 적용되는 호흡 횟수 — 연속 위상이라 요청값이 그대로 성립한다."""
    if seq_len <= 0:
        return 0
    return max(1, int(cfg.get("breaths", 1)))


def recommended_breathe_frames(cfg: dict, per_cycle: int = SMOOTH_CYCLE_FRAMES) -> int:
    """호흡이 부드럽게 읽히려면 필요한 최소 재생-시퀀스 길이.

    정지 1컷 + 링크 복제로 프레임을 찍어내는 레시피(정지자세)는 이 값으로 복제 수를
    정한다: `clones = recommended_breathe_frames(cfg) - 1`. 유저가 이미 가진 프레임
    수를 줄이거나 호흡 횟수를 바꾸지 않는다 — 오직 프레임을 새로 만들 때의 목표다."""
    return max(1, int(cfg.get("breaths", 1))) * max(2, per_cycle)


def breathe_reads_smoothly(seq_len: int, cfg: dict, per_cycle: int = SMOOTH_CYCLE_FRAMES) -> bool:
    """요청한 호흡 횟수가 이 시퀀스 길이에서 부드럽게 렌더되는가 (관측용)."""
    if seq_len <= 0:
        return False
    return seq_len // max(1, int(cfg.get("breaths", 1))) >= max(2, per_cycle)


def phase_frame(frame: Image.Image, cfg: dict, phase: float,
                anat: Anatomy | None = None) -> Image.Image:
    """프레임에 호흡 위상 하나(0 <= phase < 1)를 적용.

    `anat` 를 주면 그것을 쓴다 — 줄 전체가 한 벌을 공유해야 하므로 호출자가 한 번
    확정해 넘기는 게 정석이다. 생략하면 이 프레임을 기준으로 확정한다 (단발 호출용)."""
    if anat is None:
        anat = resolve_anatomy(frame, cfg)
    depth = float(cfg.get("depth", DEFAULT_DEPTH))
    strain = row_strain(anat, depth)
    if strain > MAX_ROW_STRAIN:
        raise SystemExit(
            f"breathe: 행당 변형 {strain:.3f} > 상한 {MAX_ROW_STRAIN} — 변형 구간이 너무 좁다 "
            f"(강체 경계 {anat.rigid_row}/{anat.height}, 정규화 기준 {anat.basis_row}). "
            f"depth 를 낮추거나 rigid_row 를 올려라. 조용히 깎지 않는다.")
    return _warp(frame, anat, depth, float(cfg.get("lag", DEFAULT_LAG)), phase)


def bake_breathe_sequence(images: list[Image.Image], cfg: dict) -> tuple[list[Image.Image], list[float]]:
    """재생 시퀀스에 호흡 레이어를 굽는다 -> (프레임들, 적용 위상들).

    출력 길이 = 입력 길이 (루프 불변 — 루프는 기존 프레임 그대로, 호흡이 그 안에서
    breaths 회 딱 떨어진다)."""
    if not images:
        return images, []
    phases = fit_breathe_pattern(len(images), cfg)
    anat = resolve_anatomy(images[0], cfg)        # 줄 전체가 한 벌을 공유한다
    return [phase_frame(images[i], cfg, phases[i], anat) for i in range(len(images))], phases
