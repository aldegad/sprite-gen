# SPDX-License-Identifier: Apache-2.0
"""실루엣 해부 — 호흡 변형이 어디까지 미쳐야 하는지를 기하로 결정한다 (AI 개입 0).

호흡을 "가슴선 하나"로 정의하려던 접근은 임의 몬스터에 일반화되지 않는다. 가슴은
해부학 개념이라 버섯(몸통에 얼굴)·박쥐(옆으로 뻗은 날개)·슬라임(목 자체가 없음)에서
서로 다른 것을 뜻한다. 대신 이 모듈은 **변형시키면 안 되는 영역**을 찾는다:

  · 목  — 폭 프로파일의 병목 (medial axis "neck" 개념). 있으면 그 위는 머리다.
  · 얼굴 — 좌우 대칭 눈쌍. 목이 없거나 목이 얼굴을 관통할 때 이쪽이 경계를 정한다.
  · 부속 — 몸통 반폭을 크게 넘는 가로 질량(날개·긴 팔). 늘리지 말고 밀어야 한다.

셋 다 실패해도 조용히 넘어가지 않는다 — 무엇으로 판정했는지(`neck_source`)와 무엇을
못 찾았는지(`face is None`)가 결과에 그대로 남는다.

근거 문헌: 실루엣 인체계측의 행별 girth 프로파일(Expert Systems with Applications
2017), Medial Axis Isoperimetric Profiles (arXiv:2007.01428).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from sprite_gen.frames.extract import solid_alpha_bbox
from sprite_gen.frames.segment import mask_components, smooth_profile

ALPHA_SOLID = 128

# 부속 인정 임계 — 최대반폭이 몸통반폭의 이 배를 넘어야 "옆으로 뻗은 것"으로 본다.
APPENDAGE_RATIO = 1.30
# 병목 인정 임계 — prominence 가 최대폭의 이 비율을 넘어야 진짜 목이다.
BOTTLENECK_PROMINENCE = 0.06
# 눈 후보 제약 — 눈은 작고 컴팩트하다. 이게 골렘 팔·아웃라인 덩어리를 걸러낸다.
EYE_MAX_EXTENT = 0.20      # bbox 변 길이 / 이미지 변 길이
EYE_ASPECT = (0.4, 2.5)
EYE_MIN_AREA = 0.002       # 불투명 면적 대비
EYE_MAX_AREA = 0.06
EYE_TOP_LIMIT = 0.65       # 이 아래에서 시작하는 덩어리는 눈이 아니다
DARK_QUANTILE = 0.30       # 명도 하위 이만큼을 어두운 픽셀로 본다


@dataclass(frozen=True)
class Anatomy:
    """한 상태(state)의 실루엣 해부 결과 — 사이드카에 얼릴 수 있는 순수 값."""

    width: int
    height: int
    axis_x: int                       # 몸통 세로축 (알파 무게중심 x), 콘텐츠 bbox 기준
    neck_row: int                     # 정수리 기준 행
    neck_source: str                  # "bottleneck" | "shoulder-gradient"
    rigid_row: int                    # 이 위는 변형하지 않는다 (목과 얼굴 중 아래쪽)
    rigid_source: str                 # "neck" | "face" | "manual"
    basis_row: int                    # 진폭 정규화 기준 — 병목이 진짜일 때만 목
    torso_half: int
    max_half: int
    torso_source: str = "auto"        # "auto" | "manual" — 보호 램프 앵커가 갈린다 (breathe.protect)
    face: tuple[int, int] | None = None
    warnings: tuple[str, ...] = field(default=())

    @property
    def has_appendage(self) -> bool:
        return self.max_half >= APPENDAGE_RATIO * self.torso_half

    @property
    def rigid_u(self) -> float:
        """발바닥 기준(0=발, 1=정수리) 강체 경계 — 프레임 높이가 달라도 쓸 수 있다."""
        return 1.0 - self.rigid_row / max(1, self.height - 1)

    @property
    def basis_rows(self) -> int:
        return max(1, self.height - self.basis_row)

    def as_dict(self) -> dict:
        return {"width": self.width, "height": self.height, "axis_x": self.axis_x,
                "neck_row": self.neck_row, "neck_source": self.neck_source,
                "rigid_row": self.rigid_row, "rigid_source": self.rigid_source,
                "basis_row": self.basis_row, "torso_half": self.torso_half,
                "max_half": self.max_half, "torso_source": self.torso_source,
                "face": list(self.face) if self.face else None,
                "warnings": list(self.warnings)}


# ── 실루엣 ──────────────────────────────────────────────────────────

def axis_x(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    """몸통 세로축 = 불투명 픽셀 x 무게중심 (box 상대 좌표)."""
    px = image.load()
    x0, y0, x1, y1 = box
    total = acc = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if px[x, y][3] >= ALPHA_SOLID:
                acc += x - x0
                total += 1
    return acc // max(1, total)


# analyze() 의 `axis_x` **파라미터**(사람 오버라이드)가 함수 이름을 가리므로 별칭으로 부른다.
_axis_centroid = axis_x


def width_profile(image: Image.Image, box: tuple[int, int, int, int], cx: int) -> list[int]:
    """행별 '몸통 중심 런' 폭.

    행 전체 불투명 픽셀을 세면 박쥐 날개처럼 옆으로 뻗은 부속이 폭을 지배한다.
    몸통 축을 지나는 **연속 런**만 재면 떨어져 있는 부속이 배제된다 — 실루엣의
    국소 두께를 세로축으로 샘플링한 것과 같다."""
    px = image.load()
    x0, y0, x1, y1 = box
    width = x1 - x0
    out: list[int] = []
    for y in range(y0, y1):
        seed = None
        if px[x0 + cx, y][3] >= ALPHA_SOLID:
            seed = cx
        else:
            best = None
            for i in range(width):
                if px[x0 + i, y][3] >= ALPHA_SOLID and (best is None or abs(i - cx) < abs(best - cx)):
                    best = i
            seed = best
        if seed is None:
            out.append(0)
            continue
        lo = seed
        while lo > 0 and px[x0 + lo - 1, y][3] >= ALPHA_SOLID:
            lo -= 1
        hi = seed
        while hi < width - 1 and px[x0 + hi + 1, y][3] >= ALPHA_SOLID:
            hi += 1
        out.append(hi - lo + 1)
    return out


def bottleneck_prominences(profile: list[float]) -> list[tuple[int, float]]:
    """국소 최소와 그 prominence — 양옆으로 다시 올라가는 높이 중 작은 쪽.

    prominence 를 쓰는 이유: 도트 계단이 만드는 얕은 최소는 병목이 아니다. 형상을
    실제로 분절하는 병목만 남긴다 (medial axis 의 neck 개념을 1D 프로파일에 적용)."""
    n = len(profile)
    if n < 3:
        return []
    out = []
    for i in range(1, n - 1):
        if profile[i] > profile[i - 1] or profile[i] > profile[i + 1]:
            continue
        left = max(profile[:i])
        right = max(profile[i + 1:])
        out.append((i, min(left, right) - profile[i]))
    return out


def _best_bottleneck(profile: list[float], u0: float, u1: float) -> int | None:
    n = len(profile)
    lo, hi = int(n * u0), int(n * u1)
    peak = max(profile) if profile else 0.0
    cand = [(i, p) for i, p in bottleneck_prominences(profile) if lo <= i < hi]
    if not cand:
        return None
    i, p = max(cand, key=lambda t: t[1])
    return i if p >= BOTTLENECK_PROMINENCE * peak else None


def _steepest_widening(profile: list[float], u0: float, u1: float) -> int:
    """아래로 갈수록 실루엣이 가장 급히 넓어지는 행 = 어깨선.

    골렘처럼 머리 위가 계속 좁아 병목이 아예 없는 실루엣의 대체값. 이 값은 목이
    아니라 '목이 없다'는 사실의 표식이므로, 진폭 정규화 기준으로는 쓰지 않는다."""
    n = len(profile)
    lo, hi = max(1, int(n * u0)), max(2, int(n * u1))
    return max(range(lo, hi), key=lambda i: profile[i] - profile[i - 1])


def detect_neck(profile: list[int]) -> tuple[int, str]:
    n = len(profile)
    smooth = smooth_profile([float(v) for v in profile], 2 * max(1, n // 40) + 1)
    row = _best_bottleneck(smooth, 0.05, 0.70)
    if row is not None:
        return row, "bottleneck"
    return _steepest_widening(smooth, 0.05, 0.60), "shoulder-gradient"


def detect_face(image: Image.Image, box: tuple[int, int, int, int], cx: int) -> tuple[int, int] | None:
    """좌우 대칭 눈쌍으로 얼굴 세로 구간 (top_row, bottom_row) 을 찾는다.

    눈은 가장 어두운 **작고 컴팩트한** 덩어리이면서 몸통 축에 대해 거울 대칭인 쌍이다.
    두 조건을 동시에 요구하는 게 핵심이다 — 어둡기만 보면 아웃라인이, 대칭만 보면
    좌우 팔이 걸린다(실측: 컴팩트 제약 없이 골렘의 양팔이 눈으로 잡혔다).
    못 찾으면 None — 그때는 목이 경계를 정한다."""
    px = image.load()
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    lums: list[tuple[float, int]] = []
    for j in range(h):
        for i in range(w):
            p = px[x0 + i, y0 + j]
            if p[3] >= ALPHA_SOLID:
                lums.append((0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2], j * w + i))
    if not lums:
        return None
    values = sorted(v for v, _ in lums)
    threshold = values[0] + DARK_QUANTILE * (values[-1] - values[0])
    area = len(lums)
    mask = [False] * (w * h)
    for v, idx in lums:
        if v <= threshold:
            mask[idx] = True

    blobs = []
    for bx0, by0, bx1, by1 in mask_components(mask, w, h,
                                              min_area=max(4, int(EYE_MIN_AREA * area)),
                                              max_area=int(EYE_MAX_AREA * area)):
        bw, bh = bx1 - bx0, by1 - by0
        if bh > EYE_MAX_EXTENT * h or bw > EYE_MAX_EXTENT * w:
            continue
        if not EYE_ASPECT[0] <= bw / bh <= EYE_ASPECT[1]:
            continue
        if by0 > EYE_TOP_LIMIT * h:
            continue
        blobs.append((bx0, by0, bx1, by1, bw * bh))

    best = None
    for i, a in enumerate(blobs):
        for b in blobs[i + 1:]:
            amid, bmid = (a[0] + a[2]) / 2, (b[0] + b[2]) / 2
            da, db = amid - cx, bmid - cx
            # 눈은 축을 **사이에 두고** 있다. 이 조건이 없으면 한쪽 눈 + 축 위의 입이
            # 짝으로 잡혀 얼굴 구간이 입 아래까지 늘어난다 (실측: 버섯).
            if da * db >= 0 or min(abs(da), abs(db)) < 0.05 * w:
                continue
            if abs((amid + bmid) / 2 - cx) > 0.08 * w:       # 쌍의 중심이 축에 있나
                continue
            if abs(amid - bmid) < 0.10 * w:                   # 충분히 떨어져 있나
                continue
            if max(a[4], b[4]) > 2.5 * min(a[4], b[4]):       # 크기가 비슷한가
                continue
            if min(a[3], b[3]) < max(a[1], b[1]):             # 세로 구간이 겹치나
                continue
            # 큰 쌍 우선, 같으면 축에 더 대칭인 쌍 (결정론 — 순서 의존 제거)
            score = (a[4] + b[4], -abs(da + db))
            if best is None or score > best[0]:
                best = (score, min(a[1], b[1]), max(a[3], b[3]) - 1)
    if best is None:
        return None
    top, bot = best[1], best[2]
    # 눈 아래 입까지 여유를 준다 — 눈만 지키고 입이 흔들리면 표정이 깨진다.
    return top, min(h - 1, bot + max(1, round((bot - top) * 0.7)))


def torso_metrics(image: Image.Image, box: tuple[int, int, int, int], cx: int,
                  profile: list[int], row_lo: int, row_hi: int) -> tuple[int, int]:
    """(몸통 반폭 중앙값, 실루엣 최대 반폭) — 둘의 비가 부속의 존재량이다."""
    px = image.load()
    x0, y0, x1, y1 = box
    band = [profile[r] for r in range(max(0, row_lo), min(len(profile), row_hi)) if profile[r]]
    torso = (sorted(band)[len(band) // 2] // 2) if band else (x1 - x0) // 4
    max_half = 0
    for j in range(max(0, row_lo), min(y1 - y0, row_hi)):
        for i in range(x1 - x0):
            if px[x0 + i, y0 + j][3] >= ALPHA_SOLID:
                max_half = max(max_half, abs(i - cx))
    return max(1, torso), max(1, max_half)


def analyze(image: Image.Image, rigid_row: int | None = None,
            axis_x: int | None = None, torso_half: int | None = None) -> Anatomy:
    """한 프레임의 실루엣 해부 — 검출 SSoT.

    `rigid_row`/`axis_x`/`torso_half` 를 주면 사람이 정한 값으로 덮어쓴다(자동 검출은
    그대로 수행해 관측값으로 남긴다). 값 하나에 출처만 auto/manual 로 갈릴 뿐 진실은
    하나다. `axis_x` 는 몸통 세로축(콘텐츠 bbox 상대 열)이라 얼굴 검출·폭 프로파일·
    부속 판정까지 그 축 기준으로 다시 돈다. `torso_half` 는 몸통 반폭 — 부속 보호
    밴드가 어디서 시작하는지를 정한다 (큐레이터 영역 조정 UI, 2026-07-30)."""
    box = solid_alpha_bbox(image)
    if not box:
        raise SystemExit("anatomy: 프레임에 불투명 콘텐츠가 없다")
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx = _axis_centroid(image, box)
    if axis_x is not None:
        if not 0 <= axis_x < w:
            raise SystemExit(f"anatomy: axis_x {axis_x} 가 콘텐츠 폭 {w} 밖이다")
        if axis_x != cx:
            cx = int(axis_x)
    profile = width_profile(image, box, cx)
    neck_row, neck_source = detect_neck(profile)
    face = detect_face(image, box, cx)

    warnings: list[str] = []
    auto_cx = _axis_centroid(image, box)
    if axis_x is not None and axis_x != auto_cx:
        warnings.append(f"axis-x-override: auto {auto_cx} -> manual {axis_x}")
    auto_rigid = max(neck_row, (face[1] + 1) if face else 0)
    auto_rigid = min(auto_rigid, int(h * 0.80))   # 몸통이 통째로 사라지지는 않게
    rigid_source = "face" if (face and face[1] + 1 > neck_row) else "neck"
    if rigid_row is not None:
        if not 0 < rigid_row < h:
            raise SystemExit(f"anatomy: rigid_row {rigid_row} 가 콘텐츠 높이 {h} 밖이다")
        if rigid_row != auto_rigid:
            warnings.append(f"rigid-row-override: auto {auto_rigid} -> manual {rigid_row}")
        auto_rigid, rigid_source = rigid_row, "manual"

    # 진폭 정규화 기준: 병목이 진짜일 때만 목을 쓴다. 슬라임처럼 폭 프로파일이 단조
    # 증가라 병목이 없으면 (H - neck) 이 실제 변형 구간의 몇 배가 되어 정규화가 폭주한다.
    basis_row = neck_row if neck_source == "bottleneck" else auto_rigid
    if neck_source != "bottleneck":
        warnings.append("neck-absent: 폭 프로파일에 병목이 없어 어깨-기울기로 대체했다 "
                        f"(정규화 기준은 강체 경계 {auto_rigid} 사용)")
    if face is None:
        warnings.append("face-absent: 대칭 눈쌍을 못 찾아 목만으로 경계를 정했다")

    torso, max_half = torso_metrics(image, box, cx, profile, auto_rigid, h)
    torso_source = "auto"
    if torso_half is not None:
        if not 1 <= torso_half <= w:
            raise SystemExit(f"anatomy: torso_half {torso_half} 가 범위 [1, {w}] 밖이다")
        if torso_half != torso:
            warnings.append(f"torso-half-override: auto {torso} -> manual {torso_half}")
        torso, torso_source = int(torso_half), "manual"
    return Anatomy(width=w, height=h, axis_x=cx, neck_row=neck_row, neck_source=neck_source,
                   rigid_row=auto_rigid, rigid_source=rigid_source, basis_row=basis_row,
                   torso_half=torso, max_half=max_half, torso_source=torso_source, face=face,
                   warnings=tuple(warnings))
