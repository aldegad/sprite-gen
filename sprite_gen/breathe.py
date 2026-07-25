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

import hashlib
import math

from PIL import Image

from sprite_gen.anatomy import Anatomy, analyze
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
    total = max(1, round(acc))

    out = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    dst = out.load()
    y_cursor = baseline - total
    prev = 0
    clipped = 0
    for j in range(height):
        u = 1.0 - j / max(1, height - 1)
        cur = round(heights[j])
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

def anatomy_fingerprint(frame: Image.Image) -> str:
    """해부 결과가 파생된 소스의 지문 — 캔버스 크기 + solid bbox + 알파 해시.

    스프라이트가 바뀌면 지문이 달라지고 다음 굽기에서 자동으로 재검출된다
    (원칙 1: 캐시 미스 시 canonical state 가 live truth 로부터 자가 복구)."""
    box = solid_alpha_bbox(frame) or (0, 0, 0, 0)
    digest = hashlib.sha256(frame.convert("RGBA").getchannel("A").tobytes()).hexdigest()[:16]
    return f"{frame.width}x{frame.height}:{box[0]},{box[1]},{box[2]},{box[3]}:{digest}"


def frame_anatomy(frame: Image.Image, cfg: dict) -> tuple[Anatomy, bool]:
    """(해부, 재검출 여부) — 얼려둔 결과를 쓰되 없거나 지문이 어긋나면 다시 잰다.

    사람이 덮어쓴 `rigid_row` 는 지문과 무관하게 보존한다 — 그건 캐시가 아니라
    명시된 의도라서 자가 복구 대상이 아니다.

    **재검출 여부를 돌려주는 게 계약이다.** 자가 복구가 조용히 일어나면 사이드카의
    숫자와 실제로 구운 숫자가 다른데도 아무도 모른다 (원칙 6). 굽기 경로는 이 값을
    manifest 에 실어야 한다 — `anatomy_report` 참조."""
    frozen = cfg.get("anatomy")
    if isinstance(frozen, dict) and frozen.get("fingerprint") == anatomy_fingerprint(frame):
        face = frozen.get("face")
        return Anatomy(width=frozen["width"], height=frozen["height"], axis_x=frozen["axis_x"],
                       neck_row=frozen["neck_row"], neck_source=frozen["neck_source"],
                       rigid_row=frozen["rigid_row"], rigid_source=frozen["rigid_source"],
                       basis_row=frozen["basis_row"], torso_half=frozen["torso_half"],
                       max_half=frozen["max_half"],
                       face=tuple(face) if face else None,
                       warnings=tuple(frozen.get("warnings", ()))), False
    return analyze(frame, rigid_row=cfg.get("rigid_row")), True


def anatomy_report(images: list[Image.Image], cfg: dict) -> dict:
    """굽기가 실제로 쓴 해부를 manifest 에 실을 형태로 — 자가 복구를 관측 가능하게.

    프레임마다 따로 잰다: 시퀀스 프레임이 서로 다르면(깜빡임 등) 얼려둔 지문은 하나만
    맞으므로 나머지는 프레임별 재검출이 되고, 그러면 `rigid_row` 가 프레임마다 달라질
    수 있다. 그 사실이 숨으면 안 된다."""
    used, redetected, rows = [], [], set()
    for index, frame in enumerate(images):
        anat, fresh = frame_anatomy(frame, cfg)
        if fresh:
            redetected.append(index)
        rows.add(anat.rigid_row)
        used.append(anat)
    first = used[0] if used else None
    return {
        "anatomy": first.as_dict() if first else None,
        "redetected_frames": redetected,
        "redetected": bool(redetected),
        "rigid_row_varies": sorted(rows) if len(rows) > 1 else [],
        "warnings": sorted({w for a in used for w in a.warnings}),
    }


def freeze_anatomy(frame: Image.Image, cfg: dict) -> dict:
    """큐레이션 시점에 해부를 1회 돌려 사이드카에 얼릴 dict 를 만든다.

    이걸 사이드카에 넣어두면 굽기와 큐레이터 웹뷰가 같은 숫자를 읽는다 — 웹뷰는
    검출을 재구현하지 않고 warp 만 미러링하면 된다 (검출 중복 제거)."""
    anat = analyze(frame, rigid_row=cfg.get("rigid_row"))
    return {**anat.as_dict(), "fingerprint": anatomy_fingerprint(frame)}


# ── 재생 시퀀스 계약 (compose/GIF 진입점) ───────────────────────────

def fit_breathe_pattern(seq_len: int, cfg: dict) -> list[float]:
    """시퀀스 길이에 딱 맞는 호흡 위상 시퀀스 (길이 == seq_len, 루프 불변).

    위상은 [0, 1) 의 연속 값이다 — 구 방식의 정수 분할선 단계가 아니다. breaths 회가
    시퀀스 안에서 정확히 반복되므로 루프 이음매가 없고, 등분 보정도 필요 없다."""
    if seq_len <= 0:
        return []
    breaths = max(1, int(cfg.get("breaths", 1)))
    return [(i * breaths / seq_len) % 1.0 for i in range(seq_len)]


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


def phase_frame(frame: Image.Image, cfg: dict, phase: float) -> Image.Image:
    """프레임에 호흡 위상 하나(0 <= phase < 1)를 적용."""
    anat, _ = frame_anatomy(frame, cfg)
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
    return [phase_frame(images[i], cfg, phases[i]) for i in range(len(images))], phases
