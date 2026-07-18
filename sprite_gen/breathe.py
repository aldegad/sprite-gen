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
# 사이드카 curation.json 의 states.<state>.breathe = {splits, amplitude, breaths, subpixel}
# 를 compose/GIF 가 재생 시퀀스 위에 굽는다. 깜빡임 프레임도 그대로 숨쉰다 (직교).
#
# 루프-맞춤 (수홍 확정 2026-07-18 2차): 루프 길이는 항상 기존 시퀀스 그대로 유지하고,
# 호흡이 그 루프 안에서 breaths 회 "딱 떨어지게" 일어난다 — LCM 전개 없음.
# breaths 가 시퀀스를 나누지 못하면 나눠지는 가장 가까운(작은) 횟수로 자가 보정한다
# (관측: 반환 위상 목록·에디터 필름스트립에 그대로 보인다).
#
# subpixel = 위상 런의 첫 프레임을 이전 위상과의 50% 블렌드로 치환 (길이 보존) —
# 도트 기법 "서브픽셀 애니메이션" (움직이는 이음새 행에만 중간색, _seam_rows).


def fit_breathe_pattern(seq_len: int, cfg: dict) -> list[float]:
    """시퀀스 길이에 딱 맞는 호흡 위상 시퀀스 (길이 == seq_len, 루프 불변).

    v2 (수홍 정정 2026-07-18): **요청한 횟수를 그대로 적용한다** — 등분 제약 폐기.
    사이클 길이가 나눠떨어지지 않으면 나머지 프레임을 앞쪽 사이클들의 쉼에 1개씩
    배분한다 (사이클 길이 [4,4,3] 식). GUI 숫자 = 실제 호흡 횟수 (Consistency).
    유일한 보정: 시퀀스가 물리적으로 못 담는 횟수(사이클당 최소 2K 프레임)만
    가능한 최대 횟수로 클램프 — 이 경우만 편집기 배지가 표시한다."""
    k = len(cfg["splits"])
    if seq_len <= 0:
        return []
    want = max(1, int(cfg.get("breaths", 1)))
    min_cycle = max(2, 2 * k)  # 하강 K + 복귀 K-1 + 쉼 최소 1
    fit = min(want, seq_len // min_cycle)
    if fit < 1:
        return [0.0] * seq_len  # 시퀀스가 너무 짧음 — 호흡 없음 (관측: 전부 0)
    base_len = seq_len // fit
    remainder = seq_len - base_len * fit
    down = [float(p) for p in range(1, k + 1)]
    up = [float(p) for p in range(k - 1, 0, -1)]
    pattern: list[float] = []
    for i in range(fit):
        length = base_len + (1 if i < remainder else 0)
        free = length - len(down) - len(up)
        deep = free // 2
        rest = free - deep
        pattern += [0.0] * rest + down + [float(k)] * deep + up
    if cfg.get("subpixel"):
        out = list(pattern)
        n = len(pattern)
        for i in range(n):
            prev = pattern[i - 1]
            if pattern[i] == prev:
                continue
            run = 1
            while run < n and pattern[(i + run) % n] == pattern[i]:
                run += 1
            if run >= 2:  # 길이 보존: 런의 첫 슬롯을 중간 위상으로 치환
                out[i] = (prev + pattern[i]) / 2.0
        pattern = out
    return pattern


def fitted_breath_count(seq_len: int, cfg: dict) -> int:
    """실제 적용되는 호흡 횟수 — v2 에선 물리 클램프 경우에만 요청과 달라진다."""
    k = len(cfg["splits"])
    if seq_len <= 0:
        return 0
    want = max(1, int(cfg.get("breaths", 1)))
    return min(want, seq_len // max(2, 2 * k))


def _seam_rows(top: int, ys: list[int], amplitude: int, height: int) -> list[tuple[int, int]]:
    """서브픽셀 중간색을 허용하는 행 밴드 — 움직이는 경계(정수리 + 각 분할선 이음새)만.

    수홍 정정 2026-07-18: 전체 프레임 블렌드는 몸통 안 가로 경계(눈·스카프)까지
    잔상을 만든다 — 중간색은 호흡선 부분에만 딱 붙어야 한다."""
    bands = []
    for y in [top, *ys]:
        r0 = max(0, y - 1)
        r1 = min(height, y + amplitude + 1)
        if r1 > r0:
            bands.append((r0, r1))
    return bands


def _frame_palette(frame: Image.Image) -> list[tuple[int, int, int]]:
    """프레임의 불투명 픽셀 고유 RGB — 서브픽셀 중간색은 이 안에서만 고른다."""
    colors = set()
    px = frame.load()
    for y in range(frame.height):
        for x in range(frame.width):
            p = px[x, y]
            if p[3] >= 128:
                colors.add(p[:3])
    return list(colors)


def _nearest(color: tuple[int, int, int], palette: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    return min(palette, key=lambda c: (c[0] - color[0]) ** 2 + (c[1] - color[1]) ** 2 + (c[2] - color[2]) ** 2)


def phase_frame(frame: Image.Image, cfg: dict, phase: float) -> Image.Image:
    """프레임에 호흡 위상 하나를 적용 — breathe_frames 와 같은 수학 (콘텐츠 bbox 기준).

    정수 위상 p: p<K 는 밴드 시프트(shift_band), p=K 는 전체 시프트(shift_above).
    반정수 위상(서브픽셀): 도트 장인 규칙의 알고리즘화 (수홍 확정 2026-07-18 —
    정식 논문이 없는 craft 영역이라 규칙을 코드로 옮겼다):
    (1) 중간색은 스프라이트에 이미 있는 색으로만 (팔레트 스냅 — 램프 중간 톤),
    (2) 실루엣/투명 경계는 통픽셀 유지 (반투명 생성 금지 — 커버리지 경계 제외),
    (3) 움직이는 이음새 밴드(_seam_rows) 안에서만."""
    if phase <= 0:
        return frame
    lo = int(phase)
    if phase != lo:
        a = phase_frame(frame, cfg, float(lo))
        b = phase_frame(frame, cfg, float(min(lo + 1, len(cfg["splits"]))))
        box = frame.split()[3].getbbox()
        if not box:
            return a
        top, bottom = box[1], box[3]
        ys = [top + int((bottom - top) * s) for s in cfg["splits"]]
        out = a.copy()
        apx, bpx, opx = a.load(), b.load(), out.load()
        palette = _frame_palette(a)
        if not palette:
            return a
        for r0, r1 in _seam_rows(top, ys, int(cfg.get("amplitude", 1)), frame.height):
            for y in range(r0, r1):
                for x in range(frame.width):
                    pa, pb = apx[x, y], bpx[x, y]
                    if pa[3] < 128 or pb[3] < 128:
                        continue  # 실루엣 경계 통픽셀 (반투명 생성 금지)
                    if pa[:3] == pb[:3]:
                        continue
                    mid = ((pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2, (pa[2] + pb[2]) // 2)
                    opx[x, y] = (*_nearest(mid, palette), 255)
        return out
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


def bake_breathe_sequence(images: list[Image.Image], cfg: dict) -> tuple[list[Image.Image], list[float]]:
    """재생 시퀀스에 호흡 레이어를 굽는다 → (프레임들, 적용 위상들).

    출력 길이 = 입력 길이 (루프 불변 — 수홍 확정: 루프는 기존 프레임 그대로,
    호흡이 그 안에서 breaths 회 딱 떨어진다)."""
    if not images:
        return images, []
    phases = fit_breathe_pattern(len(images), cfg)
    return [phase_frame(images[i], cfg, phases[i]) for i in range(len(images))], phases
