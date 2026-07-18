# SPDX-License-Identifier: Apache-2.0
"""결정론 호흡(idle breathing) — 후처리 레이어 수학 (정수 행 시프트 스쿼시).

호흡은 프레임 선택(깜빡임)과 직교하는 변조 레이어다 (수홍 확정 2026-07-18).
설정은 curation.json 사이드카 `states.<state>.breathe` (curation.state_breathe 가
정규화 SSoT), 굽기는 compose/GIF 가 재생 시퀀스 위에 이 모듈의 수학으로 한다.
정수 픽셀 행 이동은 픽셀 격자에 닫혀 있어 팔레트·아웃라인·격자가 절대 안 깨진다.
서브픽셀 옵션 = 위상 전이에 50% 블렌드 중간 프레임 (sub-pixel animation —
경계 픽셀 중간색으로 반 픽셀 이동감). AI 개입 0.

(구 테이크 방식 — exhale 프레임을 raw 테이크로 굽고 전체 재추출 — 은 v1.56.31
에서 폐기: 깜빡임×호흡 조합 불가 + 적용마다 수 분 + 팔레트 드리프트 유발.)
"""

from __future__ import annotations

from PIL import Image



def _content_top_bottom(frame: Image.Image) -> tuple[int, int]:
    box = frame.split()[3].getbbox()
    if not box:
        raise SystemExit("breathe: frame has no content")
    return box[1], box[3]


def shift_above(frame: Image.Image, split_y: int, amplitude: int = 1) -> Image.Image:
    """split_y 위의 모든 행을 amplitude px 아래로 — 경계 행이 눌리며 압축(스쿼시)."""
    top, _ = _content_top_bottom(frame)
    out = frame.copy()
    region = frame.crop((0, top, frame.width, split_y))
    out.paste(Image.new("RGBA", (frame.width, split_y - top), (0, 0, 0, 0)), (0, top))
    out.alpha_composite(region, (0, top + amplitude))
    return out


def shift_band(frame: Image.Image, y0: int, y1: int, amplitude: int = 1) -> Image.Image:
    """[y0, y1) 밴드만 amplitude px 아래로 — 위 경계는 y0 행 복제(스트레치)로 메운다.

    two-band 의 P1: 어깨·가슴이 먼저 내려가고 머리는 제자리 → 목이 1행 늘어난다
    (다음 프레임 P2 에서 머리가 따라 내려오며 해소 — 살아있는 지연감)."""
    out = frame.copy()
    band = frame.crop((0, y0, frame.width, y1))
    out.alpha_composite(band, (0, y0 + amplitude))
    stretch = frame.crop((0, y0, frame.width, y0 + 1)).resize(
        (frame.width, amplitude), Image.Resampling.NEAREST)
    out.alpha_composite(stretch, (0, y0))
    return out


def breathe_frames(frame: Image.Image, split: float = 0.55, two_band: bool = False,
                   head_split: float = 0.32, amplitude: int = 1,
                   splits: list[float] | None = None) -> list[Image.Image]:
    """호흡 위상 프레임들 생성 — 선 K개 → [P1 .. PK] 캐스케이드.

    `splits`(오름차순 비율 리스트)가 정식 계약. `split`/`two_band`/`head_split` 는
    구 시그니처 설탕: single → [split], two-band → [head_split, split]."""
    if splits is None:
        splits = [head_split, split] if two_band else [split]
    splits = sorted(float(s) for s in splits)
    if not 1 <= len(splits) <= 3:
        raise SystemExit(f"breathe: 1..3 split lines supported: {splits}")
    if any(not 0.0 < s < 1.0 for s in splits):
        raise SystemExit(f"breathe: splits must be inside (0, 1): {splits}")
    if len(set(splits)) != len(splits):
        raise SystemExit(f"breathe: split lines must be distinct: {splits}")
    top, bottom = _content_top_bottom(frame)
    ys = [top + int((bottom - top) * s) for s in splits]
    if len(set(ys)) != len(ys):
        raise SystemExit(f"breathe: split lines collapse to the same row: {splits}")
    count = len(ys)
    frames = []
    for phase in range(1, count + 1):
        if phase == count:
            frames.append(shift_above(frame, ys[-1], amplitude))
        else:
            frames.append(shift_band(frame, ys[count - 1 - phase], ys[-1], amplitude))
    return frames


# ── 후처리 레이어 (수홍 확정 2026-07-18): 호흡은 프레임이 아니라 변조다 ──
# 사이드카 curation.json 의 states.<state>.breathe = {splits, amplitude, hold, subpixel}
# 를 compose/GIF 가 재생 시퀀스 위에 굽는다. 깜빡임 프레임도 그대로 숨쉰다 (직교).
# subpixel = 위상 전이 사이에 두 위상의 50% 블렌드 프레임 삽입 — 도트 기법
# "서브픽셀 애니메이션" (경계 픽셀 중간색으로 반 픽셀 이동감).


def breathe_pattern(cfg: dict) -> list[float]:
    """호흡 위상 시퀀스 1주기. 0=기준, 1..K=하강 캐스케이드, .5 스텝=서브픽셀 블렌드.

    [0×hold, (전이), 1..K, K×(hold-1), (전이), K-1..1] — K=선 개수, hold=유지 프레임."""
    k = len(cfg["splits"])
    hold = int(cfg.get("hold", 3))
    base: list[float] = [0.0] * hold
    down = [float(p) for p in range(1, k + 1)]
    deep = [float(k)] * (hold - 1)
    up = [float(p) for p in range(k - 1, 0, -1)]
    pattern = base + down + deep + up
    if not cfg.get("subpixel"):
        return pattern
    out: list[float] = []
    for i, phase in enumerate(pattern):
        prev = pattern[i - 1]
        if prev != phase:
            out.append((prev + phase) / 2.0)  # 전이 경계에 중간 위상
        out.append(phase)
    if pattern[-1] != pattern[0]:  # 루프 랩 전이
        out.append((pattern[-1] + pattern[0]) / 2.0)
    return out


def phase_frame(frame: Image.Image, cfg: dict, phase: float) -> Image.Image:
    """프레임에 호흡 위상 하나를 적용 — breathe_frames 와 같은 수학 (콘텐츠 bbox 기준).

    정수 위상 p: p<K 는 밴드 시프트(shift_band), p=K 는 전체 시프트(shift_above).
    반정수 위상: 인접 두 정수 위상의 50% 블렌드 (서브픽셀)."""
    if phase <= 0:
        return frame
    lo = int(phase)
    if phase != lo:
        a = phase_frame(frame, cfg, float(lo))
        b = phase_frame(frame, cfg, float(min(lo + 1, len(cfg["splits"]))))
        return Image.blend(a, b, 0.5)
    splits = cfg["splits"]
    amplitude = int(cfg.get("amplitude", 1))
    k = len(splits)
    p = min(lo, k)
    box = frame.split()[3].getbbox()
    if not box:
        return frame
    top, bottom = box[1], box[3]
    ys = [top + int((bottom - top) * s) for s in splits]
    if p == k:
        return shift_above(frame, max(top + 1, ys[-1]), amplitude)
    return shift_band(frame, ys[k - 1 - p], max(top + 1, ys[-1]), amplitude)


def _lcm(a: int, b: int) -> int:
    from math import gcd
    return a * b // gcd(a, b)


BAKE_CAP = 240  # 루프 정합(LCM)이 폭주하지 않게 — 초과 시 시퀀스 길이 배수로 관측 가능하게 컷


def bake_breathe_sequence(images: list[Image.Image], cfg: dict) -> tuple[list[Image.Image], list[float]]:
    """재생 시퀀스에 호흡 레이어를 굽는다 → (프레임들, 적용 위상들).

    출력 길이 = LCM(시퀀스, 위상 패턴) — 깜빡임과 호흡이 서로 다른 주기로
    맞물려도 한 루프 안에서 정확히 재정합한다. 초과 시 BAKE_CAP 이하의
    시퀀스-길이 배수로 자른다 (관측: 반환 위상 목록으로 검증 가능)."""
    if not images:
        return images, []
    pattern = breathe_pattern(cfg)
    n, m = len(images), len(pattern)
    total = _lcm(n, m)
    if total > BAKE_CAP:
        total = max(n, (BAKE_CAP // n) * n)
    phases = [pattern[i % m] for i in range(total)]
    return [phase_frame(images[i % n], cfg, phases[i]) for i in range(total)], phases
