# SPDX-License-Identifier: Apache-2.0
"""Projection-profile + DP optimal-cut frame segmentation (opt-in).

Ported from perfectpixel-studio `internal/sprite/segment.go`
(https://github.com/gykim80/perfectpixel-studio, Copyright (c) gykim80,
MIT License — 이식 허용, 출처 표기). OCR 라인/단어 분리에서 쓰는
projection-profile + optimal-cut 기법.

connected-components 추출은 팔·소품이 이웃 프레임과 닿으면 붙은 포즈를
한 덩어리로 합쳐 프레임 분리가 실패한다. 이 모듈은 컬럼별 알파 질량
P[x] = Σ_y α(x,y) 의 골(gutter)로 자연 포즈 수를 세고, 포즈가 닿아 골이
사라졌을 때는 DP 로 `Σ P[cut] + λ·(width−ideal)²` 최소 컷을 찾아
정확히 기대 프레임 수의 컬럼 세그먼트로 나눈다.

엔진 통합은 `separate_fused_poses` 하나로 한다: 옵트인(sprite-request
`fit.segmentation: "projection"` 또는 extract CLI `--segmentation
projection`)일 때만 스트립을 세그먼트 경계에서 갈라 투명 거터를 넣어
재조립한다. 이후의 connected-components 추출·위성 병합·pixel-perfect
경로는 무변경으로 그대로 동작한다. 기본은 off — 기존 런 골든 재현성
보존 (No Silent Fallback: 분리 실패는 stderr 로 보고하고 스트립을
건드리지 않아 하류가 기존 에러로 관측 가능하게 실패한다).
"""

from __future__ import annotations

import sys

from PIL import Image

# 재조립 시 세그먼트 사이에 넣는 투명 거터 폭(px). 4-연결 flood fill 분리에는
# 1px 이면 충분하지만 하류 위성 병합 근접 판정이 인접 프레임을 물지 않도록 여유를 둔다.
_GUTTER = 8


def project_alpha(image: Image.Image) -> list[float]:
    """컬럼별 알파 질량 P[x] = Σ_y α(x,y)."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    data = rgba.getchannel("A").tobytes()
    profile = [0.0] * width
    for y in range(height):
        row = data[y * width : (y + 1) * width]
        for x, value in enumerate(row):
            if value:
                profile[x] += value
    return profile


def smooth_profile(profile: list[float], window: int) -> list[float]:
    """박스 이동평균으로 프로파일을 평활한다 (압축 잡음/얇은 틈 억제)."""
    if window < 1 or not profile:
        return profile
    length = len(profile)
    half = window // 2
    output = [0.0] * length
    for i in range(length):
        lo = max(0, i - half)
        hi = min(length - 1, i + half)
        output[i] = sum(profile[lo : hi + 1]) / (hi - lo + 1)
    return output


def content_runs(
    profile: list[float], eps: float, peak_min: float, min_width: int
) -> list[tuple[int, int]]:
    """P 가 eps 를 넘는 연속 구간(포즈) [start, end). 좁거나 봉우리가 낮은 구간은 잡티로 버린다."""
    runs: list[tuple[int, int]] = []
    i = 0
    length = len(profile)
    while i < length:
        if profile[i] <= eps:
            i += 1
            continue
        j = i
        peak = 0.0
        while j < length and profile[j] > eps:
            if profile[j] > peak:
                peak = profile[j]
            j += 1
        if j - i >= min_width and peak >= peak_min:
            runs.append((i, j))
        i = j
    return runs


def run_mass(profile: list[float], span: tuple[int, int]) -> float:
    start, end = span
    return sum(profile[start : min(end, len(profile))])


def drop_minor_runs(
    profile: list[float], runs: list[tuple[int, int]], fraction: float
) -> list[tuple[int, int]]:
    """최대 런 질량의 fraction 미만인 런(원거리 잔여물/잡티)을 제거한다.

    연결요소 방식의 "최대 blob 면적 대비 시드 임계값" 가드와 같은 취지.
    """
    if len(runs) <= 1:
        return runs
    max_mass = max(run_mass(profile, run) for run in runs)
    threshold = max_mass * fraction
    return [run for run in runs if run_mass(profile, run) >= threshold]


def median_run_width(runs: list[tuple[int, int]]) -> float:
    """런 폭의 중앙값 (전형적 단일 포즈 폭 추정)."""
    if not runs:
        return 0.0
    widths = sorted(end - start for start, end in runs)
    return float(widths[len(widths) // 2])


def pose_peaks(profile: list[float], start: int, end: int) -> list[int]:
    """[start, end) 구간에서 prominence(돌출도) 기준의 강한 봉우리(=포즈) 컬럼.

    봉우리 후보는 런 최대값의 45% 이상인 국소 최대이고, 더 높은 봉우리와의
    사이 골이 충분히 깊어야(자기 높이의 62% 미만으로 내려가야) 별개 포즈로 인정된다.
    """
    if end - start < 3:
        return [(start + end) // 2]
    run_max = max(profile[start:end])
    if run_max <= 0:
        return [(start + end) // 2]
    candidates = [
        x
        for x in range(start + 1, end - 1)
        if profile[x] >= profile[x - 1]
        and profile[x] > profile[x + 1]
        and profile[x] >= 0.45 * run_max
    ]
    if not candidates:
        return [(start + end) // 2]
    keep: list[int] = []
    for peak in candidates:
        prominent = True
        for other in candidates:
            if other == peak or profile[other] < profile[peak]:
                continue  # 자기보다 높은 봉우리에 대해서만 골 깊이 검사
            lo, hi = (peak, other) if peak < other else (other, peak)
            valley = min(profile[lo : hi + 1])
            if valley > 0.62 * profile[peak]:  # 사이 골이 얕다 → 같은 포즈의 일부
                prominent = False
                break
        if prominent:
            keep.append(peak)
    return keep or [candidates[0]]


def dp_n_cut(profile: list[float], x0: int, x1: int, count: int) -> list[int] | None:
    """[x0, x1) 구간을 정확히 count 개 세그먼트로 나누는 count-1 개 컷 컬럼.

    비용 = Σ P[cut] (질량이 적은 곳을 자르는 게 저렴) + 폭 정규화(이상폭에서
    벗어날수록 벌점). 닿아 있는 포즈를 강제로 기대 개수로 분리할 때 쓴다.
    """
    if count <= 1 or x1 - x0 < count:
        return None
    width = x1 - x0
    ideal = width / count
    min_width = max(2, int(ideal * 0.45))
    lam = 0.0015  # 폭 정규화 가중 (질량 비용 대비)
    infinity = 1e18

    cuts = count - 1
    cost = [[infinity] * (x1 + 1) for _ in range(cuts + 1)]
    previous = [[-1] * (x1 + 1) for _ in range(cuts + 1)]
    cost[0][x0] = 0.0  # 가상 시작 경계
    for k in range(1, cuts + 1):
        lo = x0 + (k - 1) * min_width
        prior_row = cost[k - 1]
        row = cost[k]
        back = previous[k]
        for x in range(x0 + k * min_width, x1 - (cuts - k + 1) * min_width + 1):
            best = infinity
            best_previous = -1
            mass = profile[x]
            for xp in range(lo, x - min_width + 1):
                base = prior_row[xp]
                if base >= 1e17:
                    continue
                deviation = (x - xp) - ideal
                candidate = base + mass + lam * deviation * deviation
                if candidate < best:
                    best = candidate
                    best_previous = xp
            row[x] = best
            back[x] = best_previous
    best_end, best_cost = -1, infinity
    for x in range(x0 + cuts * min_width, x1 - min_width + 1):
        deviation = (x1 - x) - ideal
        candidate = cost[cuts][x] + lam * deviation * deviation
        if candidate < best_cost:
            best_cost, best_end = candidate, x
    if best_end < 0:
        return None
    output = [0] * cuts
    x = best_end
    for k in range(cuts, 0, -1):
        output[k - 1] = x
        x = previous[k][x]
        if x < 0:
            return None
    return output


def split_range(profile: list[float], start: int, end: int, count: int) -> list[tuple[int, int]]:
    """[start, end) 구간을 DP 최소 절단으로 count 개 세그먼트로 나눈다 (실패 시 균등)."""
    if count <= 1 or end - start < count:
        return [(start, end)]
    cuts = dp_n_cut(profile, start, end, count)
    if cuts is not None and len(cuts) == count - 1:
        spans: list[tuple[int, int]] = []
        anchor = start
        for cut in cuts:
            spans.append((anchor, cut))
            anchor = cut
        spans.append((anchor, end))
        return spans
    return [
        (start + (end - start) * i // count, start + (end - start) * (i + 1) // count)
        for i in range(count)
    ]


def segment_strip(image: Image.Image, expected: int) -> tuple[list[tuple[int, int]], int]:
    """스트립을 expected 개 컬럼 세그먼트로 나눈다. 반환 = (세그먼트, 자연 포즈 수).

    자연 포즈 수(강제 복구 전 추정치)가 expected 와 같으면 골(gutter) 중심에서
    깔끔히 잘리고, 아니면 DP 로 expected 개를 강제 분할한다. 강제 분할이
    불가능하면(폭 부족) 추정 세그먼트를 그대로 반환한다 — 호출자가 개수
    불일치를 관측 가능하게 처리한다.
    """
    width = image.width
    if width == 0 or expected < 1:
        return [], 0
    raw = project_alpha(image)
    window = max(3, width // 220)
    profile = smooth_profile(raw, window)
    peak_max = max(profile, default=0.0)
    if peak_max <= 0:
        return [], 0
    eps = 0.045 * peak_max
    peak_min = 0.18 * peak_max
    min_run = max(4, width // 100)
    runs = content_runs(profile, eps, peak_min, min_run)
    runs = drop_minor_runs(profile, runs, 0.20)
    if not runs:
        return [], 0

    # 런마다 "토르소 봉우리(prominence peak)" 수로 포즈 수를 추정하되, 런 폭으로
    # 상한을 둔다. 봉우리는 "어디서 자를지", 폭은 "몇 개로 자를지"를 정한다:
    # 발차기처럼 한 포즈가 토르소+뻗은 다리로 두 봉우리를 만들어도, 런 폭이 단일
    # 포즈 폭(중앙값)이면 1개로 묶어 과분할을 막는다. 닿아 넓어진 런만 그만큼 쪼갠다.
    med = median_run_width(runs)
    width_total = float(sum(end - start for start, end in runs))
    segments: list[tuple[int, int]] = []
    for start, end in runs:
        peak_count = len(pose_peaks(profile, start, end))
        if len(runs) > 1 and med > 0:
            max_by_width = max(1, int((end - start) / med + 0.5))
            if peak_count > max_by_width:
                peak_count = max_by_width
            # 포즈 사이 간격이 거의 없어(overlapping) 봉우리가 1개뿐이지만
            # 런 폭이 평균 포즈 폭의 1.45배 이상이면 강제로 2개로 의심한다.
            if peak_count == 1 and (end - start) > med * 1.45:
                peak_count = 2
        if peak_count <= 1:
            segments.append((start, end))
        else:
            segments.extend(split_range(profile, start, end, peak_count))

    natural = len(segments)
    # 강제 복구: 감지된 수가 기대와 다르고, 전체 콘텐츠 폭이 기대 개수의
    # 최소 폭을 감당할 수 있다면 전체 strip 을 DP 로 expected 개 분할.
    # AI 가 포즈를 gutter 없이 완전히 붙여 그리는 경우를 방어한다.
    if natural != expected and width_total / expected >= 16 and width // expected >= 16:
        segments = split_range(profile, 0, width, expected)
    return segments, natural


def segment_boundaries(image: Image.Image, expected: int) -> tuple[list[int] | None, int]:
    """스트립 전체를 타일링하는 expected-1 개 컷 컬럼. 실패 시 (None, 자연 포즈 수).

    세그먼트 사이 골은 골 중심에서 자르고, DP 컷(인접 세그먼트가 경계를 공유)은
    그 컬럼 그대로 쓴다. 경계 밖 콘텐츠(잡티로 버려진 런)도 어느 한 슬라이스에
    남도록 전체 폭을 타일링한다 — 버릴지는 하류 위성 병합 로직이 판단한다.
    """
    segments, natural = segment_strip(image, expected)
    if len(segments) != expected:
        return None, natural
    boundaries = [
        (left_end + right_start) // 2
        for (_, left_end), (right_start, _) in zip(segments, segments[1:])
    ]
    width = image.width
    if any(not 0 < cut < width for cut in boundaries) or any(
        later <= earlier for earlier, later in zip(boundaries, boundaries[1:])
    ):
        return None, natural
    return boundaries, natural


def resolve_segmentation(fit: dict | None, cli_mode: str | None) -> str:
    """분리 모드 SSoT = sprite-request `fit.segmentation`, CLI 는 명시 override 만."""
    if cli_mode:
        return str(cli_mode).lower()
    return str((fit or {}).get("segmentation", "components")).lower()


def separate_fused_poses(
    strip: Image.Image,
    frame_count: int,
    fit: dict | None = None,
    cli_mode: str | None = None,
    label: str = "strip",
) -> Image.Image:
    """옵트인 융착 포즈 분리 훅 — 활성일 때만 스트립을 갈라 거터를 넣어 재조립한다.

    분리가 기대 개수를 못 내면 스트립을 건드리지 않고 stderr 로 보고한다 —
    하류 connected-components 추출이 기존 에러로 관측 가능하게 실패한다.
    """
    if resolve_segmentation(fit, cli_mode) != "projection":
        return strip
    boundaries, natural = segment_boundaries(strip, frame_count)
    if boundaries is None:
        print(
            f"[segment] {label}: projection segmentation found {natural} pose(s) "
            f"for expected {frame_count} — strip left untouched",
            file=sys.stderr,
        )
        return strip
    width, height = strip.size
    edges = [0, *boundaries, width]
    slices = [strip.crop((edges[i], 0, edges[i + 1], height)) for i in range(len(edges) - 1)]
    total_width = sum(piece.width for piece in slices) + _GUTTER * (len(slices) + 1)
    rebuilt = Image.new("RGBA", (total_width, height), (0, 0, 0, 0))
    x = _GUTTER
    for piece in slices:
        rebuilt.paste(piece, (x, 0))
        x += piece.width + _GUTTER
    forced = ", DP-forced" if natural != frame_count else ""
    print(
        f"[segment] {label}: projection split at columns {boundaries} "
        f"(natural poses={natural}{forced})",
        file=sys.stderr,
    )
    return rebuilt
