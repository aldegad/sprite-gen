# SPDX-License-Identifier: Apache-2.0
"""결정론 호흡 레이어(sprite_gen.effects.breathe) 회귀 — 봉투 워프의 불변식.

지키는 것 (구현이 아니라 계약):
  1. 강체 구간은 프레임 간 **비트 동일** (근사가 아니라 항등)
  2. 가로 사상은 단조 — 접힘 없음
  3. 홀짝 보존 — 중심축이 프레임마다 안 튄다
  4. 발바닥 고정 · 루프 길이 불변
  5. 정규화 기준은 병목이 진짜일 때만 목
  6. 행당 변형 상한 초과는 조용히 깎지 않고 멈춘다
"""

import pytest
from PIL import Image

from sprite_gen.effects.anatomy import analyze
from pathlib import Path

from sprite_gen.effects.breathe import (DEFAULT_DEPTH, MAX_ROW_STRAIN, SMOOTH_CYCLE_FRAMES, TAPER,
                                anatomy_report, bake_breathe_sequence, breathe_reads_smoothly,
                                envelope, fit_breathe_pattern, fitted_breath_count,
                                freeze_anatomy, phase_frame, reference_key, resolve_anatomy,
                                recommended_breathe_frames, row_strain, wave)
from sprite_gen.curate.curation import state_breathe
from sprite_gen.frames.extract import solid_alpha_bbox

CFG = {"depth": DEFAULT_DEPTH, "breaths": 1, "lag": 0.10}


def _humanoid() -> Image.Image:
    """머리 + 목 병목 + 몸통 + 대칭 눈쌍. 검출 세 경로를 모두 태우는 최소 도형."""
    im = Image.new("RGBA", (64, 96), (0, 0, 0, 0))
    body = (90, 60, 30, 255)
    for y in range(8, 30):                       # 머리
        for x in range(22, 42):
            im.putpixel((x, y), body)
    for y in range(30, 36):                      # 목 (병목)
        for x in range(28, 36):
            im.putpixel((x, y), body)
    for y in range(36, 84):                      # 몸통
        for x in range(18, 46):
            im.putpixel((x, y), body)
    for x0 in (25, 35):                          # 눈 — 축 좌우 대칭
        for y in range(14, 20):
            for x in range(x0, x0 + 4):
                im.putpixel((x, y), (10, 10, 12, 255))
    return im


def _winged() -> Image.Image:
    """몸통 + 옆으로 뻗은 얇은 부속 — 부속 보호 경로를 태운다."""
    im = Image.new("RGBA", (120, 96), (0, 0, 0, 0))
    body = (60, 40, 120, 255)
    for y in range(10, 30):
        for x in range(50, 70):
            im.putpixel((x, y), body)
    for y in range(30, 34):
        for x in range(56, 64):
            im.putpixel((x, y), body)
    for y in range(34, 84):
        for x in range(46, 74):
            im.putpixel((x, y), body)
    for y in range(40, 56):                      # 날개
        for x in range(6, 114):
            im.putpixel((x, y), body)
    return im


def _dome(with_face: bool = False) -> Image.Image:
    """아래로 갈수록 단조 증가하는 돔 — 병목이 없다 (슬라임형).

    `with_face` 면 몸통 한가운데에 대칭 눈쌍을 둔다. 목이 없고 얼굴이 몸통에 있는
    가장 어려운 조합이라, 실제 스프라이트를 레포에 넣지 않고도 얼굴 주도 경계
    경로를 그대로 태운다 (실 스프라이트는 출처·라이선스가 불분명해 픽스처로 안 넣는다)."""
    pad = 10                                     # 셀 여백 — 늘어난 프레임이 나갈 자리
    im = Image.new("RGBA", (80, 80 + 2 * pad), (0, 0, 0, 0))
    for y in range(80):
        half = 4 + int(34 * (y / 79) ** 0.6)
        for x in range(40 - half, 40 + half):
            im.putpixel((x, y + pad), (40, 160, 90, 255))
    if with_face:
        for x0 in (30, 44):
            for y in range(44, 52):
                for x in range(x0, x0 + 6):
                    im.putpixel((x, y + pad), (8, 20, 14, 255))
    return im


def _key(tag: str = "fixture") -> str:
    """테스트용 기준 프레임 키. 키는 그 프레임을 만드는 **입력**의 정규형이라 픽셀과
    무관하다 — 픽스처에서는 태그 하나면 충분하고, 파이썬/JS 일치는
    `test_breathe_reference_key.py` 가 전수로 본다."""
    return reference_key(state="idle", variant="plain", request_stamp="1:1", source_index=0,
                         source_stamp=tag, pixel_ops=None, transform=None)


def _frames(image: Image.Image, count: int = 12, cfg: dict | None = None):
    cfg = dict(cfg or CFG)
    cfg["anatomy"] = freeze_anatomy(image, cfg, _key())
    return bake_breathe_sequence([image] * count, cfg)


# ── 1. 강체 구간 항등 ───────────────────────────────────────────────

@pytest.mark.parametrize("build", [_humanoid, _winged, lambda: _dome(with_face=True)],
                         ids=["humanoid", "winged", "dome-with-face"])
def test_rigid_region_is_bit_identical_across_frames(build) -> None:
    """강체 구간은 프레임 간 비트 동일하다.

    **얼굴 없는 돔(`_dome()`)은 이 계약을 태울 수 없다** — 경계가 4행이고 테이퍼 밴드가
    5행이라 강체 구간 높이가 음수다. 병목도 얼굴도 없는 실루엣은 지킬 강체 구간이 애초에
    없고, 그건 결함이 아니라 그 분기의 성질이다 (슉슉이 note 2026-07-25). 그래서 여기서는
    강체 구간이 실재하는 3종만 태운다."""
    src = build()
    anat = analyze(src)
    frames, _ = _frames(src)
    band = int(max(1.5, TAPER * anat.height)) + 1
    rigid_h = anat.rigid_row - band
    assert rigid_h > 0, "테스트 도형이 강체 구간을 갖도록 잡혀야 한다"
    ref = frames[0]
    top = solid_alpha_bbox(ref)[1]
    expect = ref.crop((0, top, ref.width, top + rigid_h)).tobytes()
    for i, frame in enumerate(frames[1:], 1):
        t = solid_alpha_bbox(frame)[1]
        got = frame.crop((0, t, frame.width, t + rigid_h)).tobytes()
        assert got == expect, f"frame {i}: 강체 구간이 바뀌었다 — 항등이어야 한다"


def test_zero_strain_is_byte_identical_to_the_source() -> None:
    """변형이 0 이면 **원본과 바이트 동일**이어야 한다.

    프레임끼리 비교하는 테스트는 전 프레임이 똑같이 밀려도 통과한다. 원본 대비로
    재야 축 재중심화 같은 전역 오프셋이 잡힌다 (실사고 2026-07-25: bbox 중앙을
    기준으로 잡아 축이 중앙과 다른 3/3 픽스처가 변형 0 에서 1px 밀렸다)."""
    for build in (_humanoid, _winged, _dome):
        src = build()
        cfg = dict(CFG)
        cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
        rest = phase_frame(src, {**cfg, "lag": 0.0}, 0.0)   # lag 0 + 위상 0 = 전 행 g==0
        assert rest.tobytes() == src.tobytes(), f"{build.__name__}: 변형 0 인데 원본과 다르다"


def test_phase_zero_is_not_identity_when_the_wave_travels() -> None:
    """진행파 지연이 있으면 위상 0 도 변형된 프레임이다.

    구 분할선 방식에선 위상 0 이 항등이라 소비자가 건너뛰어도 됐다. 봉투에선 윗행이
    `wave(-lag*u)` 만큼 변형되므로 건너뛰면 그 슬롯만 원본이 되어 아틀라스가 매 루프
    시작에서 튀고 GIF 굽기와 그림이 갈린다 (새미 검증 2026-07-25, 실측 353px 차이).
    소비자 3곳이 이 계약에 걸려 있다 — `compose_atlas`, `compare.js`, 그리고 확대
    편집기(`zoom-editor.js` 재생·필름스트립). 앞의 둘은 `if phase:` 가드였고, 편집기는
    호흡을 **끈** 상태에서 위상만 0 으로 넘기던 경로였다(끈 프리뷰가 워프돼 굽기와 달랐다).

    **이 테스트는 성질만 고정한다 — 소비자가 되살아나는 것은 못 잡는다.** 처음엔
    "어느 쪽이든 되살아나면 여기서 잡힌다"고 적었는데 거짓이었다: `compose_atlas` 의
    가드를 옛 형태로 되돌려도 이 파일은 전부 통과한다(부리 변이 검증 2026-07-25).
    소비자 가드 자체는 `tests/test_breathe_off_state.py` 가 JS·파이썬 양쪽으로 지킨다."""
    src = _humanoid()
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    assert cfg["lag"] > 0, "이 계약은 지연이 있을 때의 이야기다"
    assert phase_frame(src, cfg, 0.0).tobytes() != src.tobytes(), \
        "위상 0 이 항등이면 소비자가 건너뛰어도 되는 것처럼 보인다"
    # 지연이 0 이면 위상 0 은 진짜 항등 — 두 경우의 차이가 계약의 핵심이다
    assert phase_frame(src, {**cfg, "lag": 0.0}, 0.0).tobytes() == src.tobytes()


def _small_outlined() -> Image.Image:
    """문어 크기대(폭 26px)의 밝은 내부 + 1px 검은 외곽선 — 좁은 머리 + 넓은 몸통.

    발끝 외곽선 플리커는 **작은 스프라이트**에서만 난다 — forward 밀도매핑의 정수
    반올림이 좁은 폭에서 실루엣 양끝 열을 통째로 떨구기 때문이다. 큰 픽스처는 여유가
    있어 안 떨어진다. 이 기하(폭 26, 몸통폭 21, depth 0.08)는 fix 없이 24위상에서
    수백 건 드롭을 재현한다(탐색으로 확정, 2026-07-30). 내부색(밝음)과 외곽선(검음)을
    뚜렷이 갈라 소실을 색으로 검출한다."""
    pad = 4
    w = 26
    im = Image.new("RGBA", (w, 30 + 2 * pad), (0, 0, 0, 0))
    px = im.load()
    cx = w // 2
    for y in range(30):
        bw = 10 if y < 12 else 21                              # 좁은 머리(병목) → 넓은 몸통
        for x in range(cx - bw // 2, cx - bw // 2 + bw):
            px[x, y + pad] = (210, 185, 120, 255)              # 밝은 내부
    edge = []
    for y in range(im.size[1]):
        for x in range(w):
            if px[x, y][3] == 0:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or ny < 0 or nx >= w or ny >= im.size[1] or px[nx, ny][3] == 0:
                    edge.append((x, y))
                    break
    for x, y in edge:
        px[x, y] = (0, 0, 0, 255)                               # 1px 검은 외곽선
    return im


def test_side_outline_survives_every_phase() -> None:
    """가로 축소 위상에서도 좌우 1px 외곽선이 사라지지 않는다 (발끝 플리커 회귀, 2026-07-30).

    forward 밀도매핑이 축소(날숨) 위상에서 실루엣 양끝 열을 통째로 떨궈, 큐레이터
    미리보기·굽기 모두에서 발끝 검은 외곽선 1px 가 위상마다 사라졌다 나타났다 했다
    (실측: 문어 gptaku-pet idle y25~y28). `_warp` 의 외곽선 보존이 이 행의 최말단 불투명
    소스 열(= 외곽선)을 출력 양끝 불투명 픽셀에 실어 막는다. 실루엣 끝에 내부색(밝음)이
    노출되면 = 외곽선이 떨어진 것이므로, 전 위상 우측 끝이 어두워야 한다."""
    src = _small_outlined()
    cfg = dict(CFG)
    cfg["depth"] = 0.08
    cfg["breaths"] = 3
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    anat = resolve_anatomy(src, cfg)
    dark = lambda p: (0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]) < 96
    dropped = []
    for i in range(24):
        phase = i / 24
        frame = phase_frame(src, cfg, phase, anat)
        px = frame.load()
        w, h = frame.size
        for y in range(h):
            rm = next((x for x in range(w - 1, -1, -1) if px[x, y][3] >= 128), None)
            if rm is not None and not dark(px[rm, y]):
                dropped.append((round(phase, 3), y, rm))
    assert not dropped, (
        f"실루엣 우측 끝에 내부색 노출(1px 외곽선 소실) {len(dropped)}건 — "
        f"발끝 플리커 회귀: {dropped[:8]}")


def test_region_overrides_change_the_anatomy_and_the_bake() -> None:
    """axis_x/torso_half 사람 오버라이드 (큐레이터 영역 UI, 2026-07-30).

    rigid_row 와 같은 지위: 사이드카의 의도 입력이고 anatomy 는 파생 캐시다.
    (a) analyze 가 오버라이드를 값으로 받아들이고 관측 warning 을 남긴다,
    (b) 범위 밖은 조용히 깎지 않고 거부한다,
    (c) torso_half 를 좁히면 부속 보호가 실제로 걸려 굽기 출력이 달라진다."""
    src = _winged()
    auto = analyze(src)
    manual = analyze(src, axis_x=auto.axis_x + 3, torso_half=5)
    assert manual.axis_x == auto.axis_x + 3
    assert manual.torso_half == 5
    assert any("axis-x-override" in w for w in manual.warnings)
    assert any("torso-half-override" in w for w in manual.warnings)

    with pytest.raises(SystemExit):
        analyze(src, axis_x=auto.width + 10)
    with pytest.raises(SystemExit):
        analyze(src, torso_half=0)

    # (c) 날개 픽스처: torso_half 를 좁히면 protect() 가 더 넓게 걸려 출력이 달라진다.
    # 기본 depth 는 정수 반올림에 묻힐 수 있어 크게 잡고 전 위상에서 차이를 찾는다.
    cfg = dict(CFG)
    cfg["depth"] = 0.15
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    narrow = {**cfg, "torso_half": 4,
              "anatomy": freeze_anatomy(src, {**cfg, "torso_half": 4}, _key())}
    assert any(phase_frame(src, cfg, i / 12).tobytes() != phase_frame(src, narrow, i / 12).tobytes()
               for i in range(12)), "torso_half 오버라이드가 굽기에 아무 효과가 없다"


def test_no_intermittent_dark_edge_protrusion() -> None:
    """변형 프레임의 좌우 실루엣 끝에 1px 어두운 돌출점이 남지 않는다 (안쪽점 기준, 2026-07-30).

    바깥점 기준 다듬기(초기 구현)는 2점 지점의 바깥점을 선으로 남겨, 이웃 행들과 1px
    어긋난 돌출점이 위상마다 나타났다 사라졌다 했다 (수홍 실기기 판정: "안쪽 검은점
    기준이 더 정확하다"). 지금은 바깥 복제분을 제거하고 안쪽 자리에 선을 그린다 —
    위아래 행 끝보다 바깥으로 튄 어두운 끝점이 0 이어야 한다. 원본에 없던 돌출만
    문제이므로 원본 돌출 수를 기준치로 삼는다."""
    src = _small_outlined()
    cfg = dict(CFG)
    cfg["depth"] = 0.08
    cfg["breaths"] = 3
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    anat = resolve_anatomy(src, cfg)

    def protrusions(frame):
        px = frame.load()
        w, h = frame.size
        dark = lambda p: p[3] >= 128 and (0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]) < 96
        opaque = lambda p: p[3] >= 128
        lo, hi = {}, {}
        for y in range(h):
            xs = [x for x in range(w) if opaque(px[x, y])]
            if xs:
                lo[y], hi[y] = xs[0], xs[-1]
        n = 0
        for y, m in lo.items():
            if y - 1 in lo and y + 1 in lo and m < lo[y - 1] and m < lo[y + 1] and dark(px[m, y]):
                n += 1
        for y, r in hi.items():
            if y - 1 in hi and y + 1 in hi and r > hi[y - 1] and r > hi[y + 1] and dark(px[r, y]):
                n += 1
        return n

    base = protrusions(src)
    for i in range(24):
        frame = phase_frame(src, cfg, i / 24, anat)
        got = protrusions(frame)
        assert got <= base, (
            f"위상 {i / 24:.3f}: 어두운 끝 돌출점 {got} > 원본 {base} — 간헐 2줄/돌출점 회귀")


def test_thinning_normalizes_warp_doubled_outline(monkeypatch) -> None:
    """워프가 복제로 두껍게 만든 외곽선을 다듬기가 1px 로 정규화한다 (볼 2줄 회귀, 2026-07-30).

    세로/가로 정수 복제가 외곽선을 국소적으로 2px 두께로 만들어 "가로 2줄 검은선" 으로
    읽혔다 (실측: 문어 idle 왼쪽 볼, 수홍 발견). `_thin_outline_1px` 가 변형 프레임의
    두꺼워진 구간을 내부색으로 되돌린다. 외곽선 경로가 꺾이는 정당한 모서리(실루엣 끝
    픽셀)는 보호되므로 "모서리 0" 을 단정하지 않는다 — 다듬기 유/무를 같은 위상에서
    비교해 (a) 있으면 항상 적거나 같고 (b) 최소 한 위상에서 실제로 줄이는 것을 단정한다.
    변형 0 프레임은 다듬지 않으므로 원본 동일 계약과 공존한다 (zero-strain 테스트가 지킴)."""
    import sprite_gen.effects.breathe as breathe_mod

    src = _small_outlined()
    cfg = dict(CFG)
    cfg["depth"] = 0.08
    cfg["breaths"] = 3
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    anat = resolve_anatomy(src, cfg)

    def non_end_dark(frame):
        """실루엣 끝(외곽선의 정당한 경로)이 아닌 위치의 어두운 픽셀 수.

        1px 외곽선의 픽셀은 전부 행 끝이거나 열 끝이다. 워프 복제로 두꺼워진 안쪽
        분(가로 2줄의 두 번째 줄)만 이 메트릭에 잡힌다. 눈처럼 원래 내부에 있는
        어두운 특징도 잡히지만 다듬기 유/무 양쪽에 똑같이 들어가 상쇄된다. 다듬기는
        어두운 픽셀을 내부색으로 바꾸기만 하고 알파는 불변이라, 이 메트릭은 다듬기로
        단조 감소하며 with/without 프레임의 끝 집합이 동일해 비교가 정확하다."""
        px = frame.load()
        w, h = frame.size
        dark = lambda p: p[3] >= 128 and (0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]) < 96
        opaque = lambda p: p[3] >= 128
        ends = set()
        for y in range(h):
            xs = [x for x in range(w) if opaque(px[x, y])]
            if xs:
                ends.add((xs[0], y))
                ends.add((xs[-1], y))
        for x in range(w):
            ys = [y for y in range(h) if opaque(px[x, y])]
            if ys:
                ends.add((x, ys[0]))
                ends.add((x, ys[-1]))
        return sum(1 for y in range(h) for x in range(w)
                   if dark(px[x, y]) and (x, y) not in ends)

    with_thin = [non_end_dark(phase_frame(src, cfg, i / 24, anat)) for i in range(24)]
    monkeypatch.setattr(breathe_mod, "_thin_outline_1px", lambda out: None)
    without = [non_end_dark(phase_frame(src, cfg, i / 24, anat)) for i in range(24)]
    monkeypatch.undo()

    for i, (a, b) in enumerate(zip(with_thin, without)):
        assert a <= b, f"위상 {i / 24:.3f}: 다듬기가 두꺼운 구간을 오히려 늘렸다 ({a} > {b})"
    assert sum(with_thin) < sum(without), (
        f"다듬기가 어떤 위상에서도 두꺼운 외곽선을 줄이지 못했다 "
        f"(with={sum(with_thin)}, without={sum(without)}) — 볼 2줄 회귀")


def test_the_body_axis_column_is_a_fixed_point() -> None:
    """어떤 위상에서도 몸통 축 열은 제자리 — 이게 좌우 지터를 구조적으로 막는다."""
    src = _humanoid()
    anat = analyze(src)
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    box = solid_alpha_bbox(src)
    axis_col = box[0] + anat.axis_x
    for i in range(12):
        frame = phase_frame(src, cfg, i / 12)
        fb = solid_alpha_bbox(frame)
        # 축 열의 콘텐츠가 그 프레임 안에서도 축 열에 그대로 있어야 한다: 소스 축 열의
        # 불투명 span 이 출력 축 열에서도 연속으로 살아 있고, 좌우 이웃 열로 새지 않는다.
        col = [frame.getpixel((axis_col, y))[3] >= 128 for y in range(fb[1], fb[3])]
        assert sum(col) == fb[3] - fb[1], f"위상 {i}: 축 열에 구멍 — 열이 통째로 밀렸다"
        left_edge = solid_alpha_bbox(frame)[0]
        assert left_edge <= axis_col < solid_alpha_bbox(frame)[2], f"위상 {i}: 축이 실루엣 밖"


# ── 2. 발바닥 고정 · 루프 길이 불변 ─────────────────────────────────

def test_feet_stay_planted_and_loop_length_is_preserved() -> None:
    src = _humanoid()
    baseline = solid_alpha_bbox(src)[3]
    frames, phases = _frames(src, count=12)
    assert len(frames) == 12 and len(phases) == 12
    for i, frame in enumerate(frames):
        assert solid_alpha_bbox(frame)[3] == baseline, f"frame {i}: 발이 떴다"


def test_breathing_actually_moves_the_body() -> None:
    src = _humanoid()
    frames, _ = _frames(src, count=12)
    heights = {solid_alpha_bbox(f)[3] - solid_alpha_bbox(f)[1] for f in frames}
    assert len(heights) > 1, "봉투 워프가 높이를 전혀 안 바꿨다"


# ── 3. 부속은 밀리기만 하고 늘어나지 않는다 ─────────────────────────

def test_appendage_is_pushed_not_stretched() -> None:
    src = _winged()
    anat = analyze(src)
    assert anat.has_appendage, "테스트 도형은 부속이 있어야 한다"
    frames, _ = _frames(src, count=12)
    spans = [solid_alpha_bbox(f)[2] - solid_alpha_bbox(f)[0] for f in frames]
    body_h = anat.height
    # 날개 전체 폭 변화가 몸통이 부푸는 양(depth*기준높이)을 크게 넘지 않아야 한다 —
    # 넘으면 부속이 몸통과 같은 배율로 늘어난 것이다.
    assert max(spans) - min(spans) <= 2 * round(DEFAULT_DEPTH * body_h) + 2


# ── 4. 정규화 기준 분기 ─────────────────────────────────────────────

def test_amplitude_basis_uses_neck_only_when_the_bottleneck_is_real() -> None:
    real = analyze(_humanoid())
    assert real.neck_source == "bottleneck"
    assert real.basis_row == real.neck_row

    anat = analyze(_dome())
    assert anat.neck_source == "shoulder-gradient"
    assert anat.basis_row == anat.rigid_row, "병목이 가짜면 기준은 강체 경계여야 한다"
    assert any("neck-absent" in w for w in anat.warnings), "대체 판정은 관측 가능해야 한다"
    # 기준이 목이었다면 정규화가 폭주했을 것 — 상한 안에 들어와야 한다
    assert row_strain(anat, DEFAULT_DEPTH) <= MAX_ROW_STRAIN


def test_row_strain_over_the_cap_raises_instead_of_clamping() -> None:
    src = _humanoid()
    anat = analyze(src)
    depth = DEFAULT_DEPTH
    while row_strain(anat, depth) <= MAX_ROW_STRAIN:
        depth *= 2
        assert depth < 10, "상한을 넘길 수 있어야 한다"
    with pytest.raises(SystemExit) as err:
        phase_frame(src, {**CFG, "depth": depth}, 0.25)
    assert "행당 변형" in str(err.value)


# ── 5. 수동 override 와 자가 복구 ───────────────────────────────────

def test_manual_rigid_row_overrides_detection_and_is_observable() -> None:
    src = _humanoid()
    auto = analyze(src)
    manual = analyze(src, rigid_row=auto.rigid_row + 6)
    assert manual.rigid_row == auto.rigid_row + 6
    assert manual.rigid_source == "manual"
    assert any("rigid-row-override" in w for w in manual.warnings)


def test_stale_frozen_anatomy_self_heals() -> None:
    src = _humanoid()
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    other = _winged()
    assert _warp_inputs(analyze(src)) != _warp_inputs(analyze(other)), \
        "픽스처 쌍이 워프 입력을 안 바꾸면 이 테스트는 아무것도 보증하지 못한다"

    # 다른 스프라이트에 옛 해부 결과를 물려도 **그 프레임에서 다시 잰 값**으로 구워야 한다.
    # 크기·bbox 만 보면 낡은 해부로 구워도 통과한다 — 실제로 쓴 해부를 픽셀로 대조한다.
    healed = phase_frame(other, cfg, 0.25)
    want = phase_frame(other, {k: v for k, v in cfg.items() if k != "anatomy"}, 0.25)
    assert healed.tobytes() == want.tobytes(), \
        "낡은 해부가 굽기로 샜다 — 다른 스프라이트의 경계로 구웠다"
    stale = phase_frame(src, cfg, 0.25)
    assert healed.tobytes() != stale.tobytes(), \
        "픽스처가 같은 그림을 내면 위 단언이 공허하다"


# ── 6. 위상 시퀀스 ──────────────────────────────────────────────────

def test_phase_pattern_fits_requested_breaths_into_the_loop() -> None:
    for seq_len, breaths in ((12, 1), (12, 2), (10, 3), (7, 2)):
        pattern = fit_breathe_pattern(seq_len, {"breaths": breaths})
        assert len(pattern) == seq_len
        assert all(0.0 <= p < 1.0 for p in pattern)
        assert pattern[0] == 0.0
        assert fitted_breath_count(seq_len, {"breaths": breaths}) == breaths


def test_wave_is_loop_closed() -> None:
    assert wave(0.0) == pytest.approx(wave(1.0), abs=1e-9)


def test_envelope_is_zero_above_the_rigid_boundary() -> None:
    anat = analyze(_humanoid())
    env, _ = envelope(anat)
    band = max(1.5, TAPER * anat.height) / anat.height
    above = anat.rigid_u + band + 1e-6
    assert env(min(1.0, above)) == pytest.approx(0.0, abs=1e-12)
    assert env(0.0) == pytest.approx(0.0, abs=1e-12), "발바닥도 고정"


def test_smoothness_hint_scales_with_breath_count() -> None:
    assert recommended_breathe_frames({"breaths": 1}) == SMOOTH_CYCLE_FRAMES
    assert recommended_breathe_frames({"breaths": 3}) == 3 * SMOOTH_CYCLE_FRAMES
    assert breathe_reads_smoothly(SMOOTH_CYCLE_FRAMES, {"breaths": 1}) is True
    assert breathe_reads_smoothly(SMOOTH_CYCLE_FRAMES - 1, {"breaths": 1}) is False


# ── 7. 폐기된 분할선 스키마는 요란하게 거부된다 ─────────────────────

@pytest.mark.parametrize("retired", [{"splits": [0.55]}, {"amplitude": 2}, {"subpixel": True}])
def test_retired_split_schema_is_rejected_loudly(retired: dict) -> None:
    curation = {"states": {"idle": {"breathe": {**retired, "breaths": 1}}}}
    with pytest.raises(SystemExit) as err:
        state_breathe(curation, "idle")
    message = str(err.value)
    assert "폐기된" in message
    assert "migrate-breathe" in message, "마이그레이션 경로를 알려줘야 한다"


def test_new_schema_normalizes_within_range() -> None:
    cfg = state_breathe({"states": {"idle": {"breathe": {"depth": 0.08, "breaths": 3}}}}, "idle")
    assert cfg == {"depth": 0.08, "depth_x": None, "breaths": 3, "lag": 0.10,
                   "rigid_row": None, "axis_x": None, "torso_half": None, "anatomy": None}


def test_clipping_the_cell_raises_instead_of_cropping_the_head() -> None:
    """여백이 없어 늘어난 프레임이 셀 밖으로 나가면 조용히 자르지 않는다."""
    tight = _humanoid().crop(solid_alpha_bbox(_humanoid()))   # 여백 0
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(tight, cfg, _key())
    with pytest.raises(SystemExit) as err:
        bake_breathe_sequence([tight] * 12, cfg)
    assert "셀 밖으로" in str(err.value)


# ── 8. 얼굴이 몸통에 있는 실루엣 (목 없음) ─────────────────────────

def test_face_on_the_body_pushes_the_boundary_below_the_face() -> None:
    """슬라임형: 목이 없고 얼굴이 몸통 한가운데다.

    목만 보면 경계가 얼굴 위에 걸려 눈 행이 변형 구간에 들어간다. 얼굴 검출이
    경계를 얼굴 아래로 내려야 표정이 살아남는다."""
    slime = _dome(with_face=True)
    anat = analyze(slime)
    assert anat.neck_source == "shoulder-gradient", "돔에는 병목이 없어야 한다"
    assert anat.face is not None, "대칭 눈쌍을 찾아야 한다"
    assert anat.rigid_row > anat.face[0], "경계가 얼굴 위에 걸리면 안 된다"
    assert anat.rigid_source == "face"
    assert anat.basis_row == anat.rigid_row

    frames, _ = _frames(slime)
    band = int(max(1.5, TAPER * anat.height)) + 1
    rigid_h = anat.rigid_row - band
    # 눈(픽스처 44~51행)이 강체 구간 안이어야 한다. face[1] 아래쪽은 입 여유분이라
    # 테이퍼가 걸쳐도 된다 — 지켜야 하는 건 표정을 만드는 도트지 여유분이 아니다.
    assert rigid_h > 51, f"눈이 변형 구간에 들어갔다 (강체 {rigid_h}행까지)"
    ref = frames[0]
    top = solid_alpha_bbox(ref)[1]
    expect = ref.crop((0, top, ref.width, top + rigid_h)).tobytes()
    for i, frame in enumerate(frames[1:], 1):
        t = solid_alpha_bbox(frame)[1]
        assert frame.crop((0, t, frame.width, t + rigid_h)).tobytes() == expect, \
            f"frame {i}: 얼굴이 흔들렸다"


def test_face_detection_ignores_a_single_eye_paired_with_a_centred_mouth() -> None:
    """눈 후보는 축을 **사이에 두고** 있어야 한다.

    이 제약이 없으면 한쪽 눈과 축 위의 입이 짝으로 잡혀 얼굴 구간이 입 아래까지
    늘어난다 (실측: 버섯에서 경계가 57 -> 64 로 밀렸다)."""
    im = _dome(with_face=True)
    for y in range(66, 72):                    # 축 위 입 (눈보다 아래, 눈과 안 닿게)
        for x in range(37, 44):
            im.putpixel((x, y), (8, 20, 14, 255))
    anat = analyze(im)
    assert anat.face is not None
    # 눈쌍(44~52)이 이겨야 한다 — 입(56~62)까지 삼키면 bottom 이 훨씬 아래로 간다
    assert anat.face[0] < 56, f"눈쌍이 아니라 다른 짝이 이겼다: {anat.face}"


# ── 9. Validator round 2 회귀 ───────────────────────────────────────

def test_repeated_phases_are_bit_identical_so_atlas_cells_can_be_shared() -> None:
    """수학적으로 같은 위상은 **같은 double** 이어야 한다.

    아틀라스는 (프레임, 위상)을 칸 키로 써서 같은 그림을 한 칸만 굽는다. 위상을
    `(i*breaths/seq_len) % 1.0` 로 계산하면 표현 노이즈로 같은 위상이 갈려서
    바이트 동일한 칸이 중복 구워진다 (실측 18슬롯 3호흡: 유니크 6 -> 14,
    시트 폭 576 -> 1344, 새미 검증 2026-07-25)."""
    for seq_len, breaths in ((18, 3), (12, 2), (12, 4), (20, 5)):
        pattern = fit_breathe_pattern(seq_len, {"breaths": breaths})
        exact = {(i * breaths) % seq_len for i in range(seq_len)}
        assert len(set(pattern)) == len(exact), \
            f"{seq_len}슬롯 {breaths}호흡: 유니크 위상 {len(set(pattern))} != 수학적 {len(exact)}"
        # 같은 나머지를 갖는 슬롯끼리 실제로 같은 값인지
        for i in range(seq_len):
            j = i + seq_len // breaths
            if j < seq_len and (i * breaths) % seq_len == (j * breaths) % seq_len:
                assert pattern[i] == pattern[j], f"슬롯 {i}/{j}: 같은 위상인데 double 이 다르다"


def test_repeated_phases_render_byte_identical_frames() -> None:
    """위상이 같으면 구워진 픽셀도 같아야 칸 공유가 정당하다."""
    src = _humanoid()
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    frames, phases = bake_breathe_sequence([src] * 18, {**cfg, "breaths": 3})
    by_phase: dict[float, bytes] = {}
    for frame, phase in zip(frames, phases):
        data = frame.tobytes()
        if phase in by_phase:
            assert by_phase[phase] == data, f"위상 {phase}: 같은 위상인데 픽셀이 다르다"
        by_phase[phase] = data
    assert len(by_phase) == 6, f"18슬롯 3호흡의 유니크 위상은 6이어야 한다 (got {len(by_phase)})"


def test_manual_rigid_row_beats_a_frozen_anatomy() -> None:
    """`rigid_row` 는 사람의 의도(입력)고 `anatomy` 는 파생 캐시다 — 의도가 이긴다.

    frozen 분기가 `cfg["rigid_row"]` 를 안 보면 사람이 고친 숫자가 조용히 버려진다
    (실측: cfg 33 을 줘도 얼린 23 이 구워지고 경고도 없었다, 새미 검증 2026-07-25)."""
    src = _humanoid()
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg, _key())
    frozen_row = cfg["anatomy"]["rigid_row"]

    same = resolve_anatomy(src, cfg)
    assert same.rigid_row == frozen_row, "override 없으면 같은 프레임에서 같은 값이 나온다"

    want = frozen_row + 10
    anat = resolve_anatomy(src, {**cfg, "rigid_row": want})
    assert anat.rigid_row == want, "사람이 준 값이 얼린 값에 먹혔다"
    assert anat.rigid_source == "manual"
    assert any("rigid-row-override" in w for w in anat.warnings)

    # **거짓말하는 캐시가 굽기로 새지 않는다.** 굽기는 진짜 프레임을 손에 들고 있으니
    # 재는 게 언제나 옳고, 얼린 값은 웹뷰가 그림을 그리려고 들고 있는 캐시일 뿐이다.
    lying = {**cfg, "anatomy": {**cfg["anatomy"], "rigid_row": frozen_row + 7,
                                "axis_x": cfg["anatomy"]["axis_x"] + 5}}
    assert resolve_anatomy(src, lying).rigid_row == frozen_row, \
        "사이드카 캐시가 굽기 값을 밀어냈다 — 캐시는 진실이 아니다"


def test_the_bake_re_measures_alpha_invariant_paint() -> None:
    """불투명 픽셀 위에 눈을 덧칠하면 굽기가 **그 얼굴을 찾아** 경계를 내린다.

    이건 큐레이터 픽셀 편집기의 문서화된 기능이고 알파가 1바이트도 안 바뀐다. 예전 설계는
    "얼린 해부를 믿을지" 를 프레임 RGBA 해시로 판정했는데, 그 해시가 웹뷰와 영구 불일치를
    만드는 원흉이었다 (BICUBIC vs NEAREST). 지금은 굽기가 **캐시를 아예 안 믿고** 매번
    자기 기준 프레임에서 재므로, 판정 자체가 필요 없다 — 그래서 이 테스트는 지문이 아니라
    구워진 해부를 본다.

    사용자 쪽 사이드카 캐시가 낡았는지는 `anatomy_report(...)["sidecar_drift"]` 가 값으로
    보고하고, 웹뷰의 미리보기 신선도는 `reference_key`(입력 기반)가 판정한다."""
    plain = _dome(with_face=False)
    painted = _dome(with_face=True)          # 알파 동일, RGB 만 다름
    assert plain.getchannel("A").tobytes() == painted.getchannel("A").tobytes(), \
        "이 픽스처 쌍은 알파가 같아야 의미가 있다"

    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(plain, cfg, _key())      # 얼굴 없는 상태로 확정
    anat = resolve_anatomy(painted, cfg)                      # 그 뒤 눈을 덧칠
    assert anat.face is not None and anat.rigid_source == "face", \
        "덧칠된 얼굴을 못 찾았다 — 굽기가 낡은 경계로 굽는다"

    report = anatomy_report([painted], cfg)
    assert report["matches_sidecar"] is False, "사이드카가 낡았는데 보고가 조용하다"
    assert "rigid_row" in (report["sidecar_drift"] or {}), \
        "무엇이 어긋났는지 값으로 보고해야 한다 (원칙 6)"


def test_out_of_range_values_are_refused_instead_of_clamped() -> None:
    """범위 밖 값은 조용히 깎지 않는다.

    클램프가 파이썬에만 있어서 미러·배지·문서가 굽기와 다른 값을 말했다 (슉슉이 실측
    2026-07-25: `breaths 12` 를 8 로 깎는데 프리뷰·필름스트립·WebM 은 12회 숨쉬고
    배지는 "적용 12회" 라고 띄웠다). 폐기 키는 요란하게 거부하면서 값 범위만 조용한 것도
    계약이 어긋난다 — 한쪽으로 정했다."""
    from sprite_gen.curate.curation import (BREATHE_BREATHS_MAX, BREATHE_DEPTH_MAX,
                                     BREATHE_LAG_MAX, state_breathe)
    for key, over in (("depth", BREATHE_DEPTH_MAX + 0.01),
                      ("breaths", BREATHE_BREATHS_MAX + 1),
                      ("lag", BREATHE_LAG_MAX + 0.01)):
        with pytest.raises(SystemExit) as err:
            state_breathe({"states": {"idle": {"breathe": {key: over}}}}, "idle")
        assert key in str(err.value) and "범위" in str(err.value)
    # 경계값은 통과한다
    ok = state_breathe({"states": {"idle": {"breathe": {
        "depth": BREATHE_DEPTH_MAX, "breaths": BREATHE_BREATHS_MAX, "lag": BREATHE_LAG_MAX}}}}, "idle")
    assert ok["breaths"] == BREATHE_BREATHS_MAX


def test_the_curator_ui_cap_matches_the_schema_bound() -> None:
    """UI 컨트롤 상한과 스키마 상한이 갈리면 UI 가 만든 값을 굽기가 거부한다."""
    from sprite_gen.curate.curation import BREATHE_BREATHS_MAX, BREATHE_DEPTH_MAX, BREATHE_LAG_MAX
    import re
    from sprite_gen.serve.serve_curation import CURATOR_DIR
    js = (CURATOR_DIR / "src" / "breathe.js").read_text(encoding="utf-8")
    for name, want in (("BREATHE_BREATHS_MAX", BREATHE_BREATHS_MAX),
                       ("BREATHE_DEPTH_MAX", BREATHE_DEPTH_MAX),
                       ("BREATHE_LAG_MAX", BREATHE_LAG_MAX)):
        # 값을 숫자로 파싱해서 비교한다 — 문자열 포함 검사는 0.2 가 0.25 에도 걸린다
        m = re.search(rf"^const {name} = ([0-9.]+);", js, re.M)
        assert m, f"{name} 미러 상수가 breathe.js 에 없다"
        assert float(m.group(1)) == float(want), \
            f"{name} 미러 {m.group(1)} != 파이썬 {want} — UI 가 만든 값을 굽기가 거부한다"


def _reaches_further(base):
    """같은 줄의 다른 프레임 — **워프가 실제로 쓰는 값**이 갈리게 만든다.

    round-5 에 넣은 첫 판은 RGB 만 덧칠해서 `face`/`warnings` 만 바뀌고 워프 입력
    (`axis_x`·`torso_half`·`max_half`)은 그대로였다. 그래서 프레임별 재검출로 되돌리는
    변이를 넣어도 출력이 바이트 동일해 **테스트가 안 물었다** (슉슉이 note 2026-07-25).
    팔을 뻗으면 `axis_x` 13→14, `max_half` 14→19 로 워프 입력이 실제로 갈린다."""
    out = base.copy()
    px = out.load()
    box = solid_alpha_bbox(out)
    for y in range(box[1] + 40, box[1] + 50):
        for x in range(box[2], min(out.width, box[2] + 6)):
            px[x, y] = (90, 60, 30, 255)
    return out


def _warp_inputs(anat):
    return (anat.rigid_row, anat.basis_row, anat.axis_x, anat.torso_half, anat.max_half)


def test_row_anatomy_is_shared_across_the_sequence() -> None:
    """줄 전체가 해부 한 벌을 쓴다 — 프레임마다 다시 재면 경계·축이 흔들린다.

    경계가 프레임마다 움직이면 '강체 구간' 이 프레임 간 같은 구간이 아니게 되고,
    사이드카 한 벌로 그리는 프리뷰와도 갈린다 (슉슉이 실측 2026-07-25)."""
    base = _humanoid()
    other = _reaches_further(base)
    assert _warp_inputs(analyze(base)) != _warp_inputs(analyze(other)), \
        "픽스처 쌍이 워프 입력을 안 바꾸면 이 테스트는 아무것도 보증하지 못한다"

    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(base, cfg, _key())
    seq = [base, other, base, other]
    frames, phases = bake_breathe_sequence(seq, cfg)
    anat = resolve_anatomy(base, cfg)

    diverged = 0
    for i, frame in enumerate(frames):
        want = phase_frame(seq[i], cfg, phases[i], anat)
        assert frame.tobytes() == want.tobytes(), \
            f"frame {i}: 줄 해부 한 벌로 구운 것과 다르다 — 프레임별 재검출이 되살아났다"
        per_frame = phase_frame(seq[i], cfg, phases[i])      # 프레임 자기 해부로 구운 것
        if per_frame.tobytes() != frame.tobytes():
            diverged += 1
    assert diverged, ("어떤 프레임도 자기 해부와 줄 해부의 출력이 다르지 않다 — "
                      "이 픽스처로는 변이가 안 잡힌다 (그물이 이름값을 못 한다)")

    report = anatomy_report(seq, cfg)
    assert "rigid_row_varies" not in report, "경계가 프레임마다 달라질 수 있는 표현이 남아 있다"
    assert report["anatomy"]["rigid_row"] == anat.rigid_row


def test_a_non_integer_breaths_is_refused_not_truncated() -> None:
    """비정수는 조용히 깎지 않는다 (round-7 R2).

    `int(2.7) -> 2` 로 되돌려도 전체 스위트가 통과했다 — 그물이 없었다
    (슉슉이 변이 검증 2026-07-26). 파이썬이 버리고 미러가 반올림하면 굽기 2회 /
    프리뷰 3회가 되고, 첫 autosave 가 사이드카를 반올림값으로 덮는다."""
    from sprite_gen.curate.curation import state_breathe
    for value in (2.7, "3.5", 1.0001):
        with pytest.raises(SystemExit) as err:
            state_breathe({"states": {"idle": {"breathe": {"depth": 0.06, "breaths": value}}}}, "idle")
        assert "정수가 아니다" in str(err.value), f"{value!r}: 정수 계약이 아닌 사유로 죽었다"
    # 정수로 표현되는 실수는 받는다 (3.0 == 3)
    assert state_breathe({"states": {"idle": {"breathe": {"depth": 0.06, "breaths": 3.0}}}},
                         "idle")["breaths"] == 3


# ── 7. 가로/세로 진폭 분리 + 수동 밴드 보호 앵커 (2026-07-30) ───────

def test_depth_x_absent_is_byte_identical_to_depth_x_equal_depth() -> None:
    """레거시 계약: depth_x 부재 = depth 따름. 명시 depth_x==depth 와 바이트 동일해야
    분리 도입이 기존 굽기를 1픽셀도 안 바꿨다는 증명이 된다 (순수 확장)."""
    src = _dome()
    base, _ = _frames(src, count=12)
    explicit, _ = _frames(src, count=12, cfg={**CFG, "depth_x": CFG["depth"]})
    assert [f.tobytes() for f in base] == [f.tobytes() for f in explicit]


def test_depth_x_zero_keeps_horizontal_extent() -> None:
    """depth_x=0 = 가로 사상 전 위상 항등 — 가로 bbox 가 움직이지 않는다."""
    src = _dome()
    x0, _, x1, _ = solid_alpha_bbox(src)
    frames, _ = _frames(src, count=12, cfg={**CFG, "depth": 0.10, "depth_x": 0.0})
    for f in frames:
        b = solid_alpha_bbox(f)
        assert (b[0], b[2]) == (x0, x1), "가로 항등인데 가로 bbox 가 움직였다"
    # 대조군: depth_x 가 depth 를 따르면 같은 설정에서 출력이 실제로 달라야 한다
    moving, _ = _frames(src, count=12, cfg={**CFG, "depth": 0.10})
    assert any(a.tobytes() != b.tobytes() for a, b in zip(frames, moving)), \
        "depth_x=0 과 따름이 같은 그림 — 가로 성분이 아예 안 굽는다"


def test_depth_x_does_not_leak_into_the_vertical_axis() -> None:
    """축 독립: `depth_x` 를 바꿔도 세로 운동(정수리 궤적)은 한 톨도 안 바뀐다.

    분리의 요점은 두 축이 **스칼라만 다르고 나머지를 공유**하는 것이라, 세로 누적에
    가로 진폭이 섞여도 겉보기 출력은 계속 그럴듯하다 — 변이 검증 2026-07-31 에서
    세로 누적을 `dx_amp` 로 바꾼 mutant 가 기존 depth_x 그물 3개를 전부 통과했다."""
    src = _humanoid()

    def top_track(dx):
        cfg = {**CFG, "depth": 0.10, "depth_x": dx}
        frames, _ = _frames(src, count=12, cfg=cfg)
        return [solid_alpha_bbox(f)[1] for f in frames]

    baseline = top_track(0.10)
    for dx in (0.0, 0.03, 0.12):
        assert top_track(dx) == baseline, (
            f"depth_x={dx} 가 세로 궤적을 바꿨다 — 가로 진폭이 세로 축으로 샌다")


def test_manual_band_anchors_protection_to_the_band() -> None:
    """수동 밴드는 무조건 켜지고 램프가 밴드에 앵커된다 — 블롭에서 밴드 조정이
    무력했던 버그의 회귀 그물 (실측 2026-07-30 gptaku-pet: 밴드 12→4 무변화)."""
    from sprite_gen.effects.breathe import protect
    src = _humanoid()
    auto = analyze(src)
    assert not auto.has_appendage, "휴머노이드는 부속이 없어야 한다 (자동 경로 무보호 전제)"
    assert auto.torso_source == "auto"
    manual = analyze(src, torso_half=6)
    assert manual.torso_source == "manual"
    p = protect(manual)
    assert p(manual.axis_x) == 0.0, "축은 비보호"
    assert p(manual.axis_x + 5) == 0.0, "밴드 안(t0 미만)은 비보호"
    assert p(manual.axis_x + 9) == 1.0, "밴드 밖(t1 초과)은 완전 보호 — 밀리기만 한다"
    assert protect(auto)(auto.axis_x + 9) == 0.0, "자동 경로(블롭)는 기존 계약 그대로 무보호"


def test_manual_band_actually_changes_the_bake() -> None:
    """**부속이 없는** 픽스처로, **폭 변동 크기**로 잰다 — 그게 실제 계약이다.

    두 번의 변이 검증(2026-07-31)이 순진한 그물을 둘 다 통과시켰다:
    (1) `_dome` 은 부속이 있어서 자동 램프가 이미 켜져 있고,
    (2) `_humanoid` 이라도 밴드를 좁히면 `has_appendage`(max_half >= 1.3*torso_half)
        판정이 뒤집혀 자동 램프가 켜지므로, 앵커 수리를 되돌려도 **출력은 어차피
        달라진다** — "다르다" 단정으로는 회귀를 못 잡는다.
    실제 계약은 **밴드 밖이 늘어나지 않고 밀리기만 하는 것**이므로, 밴드를 좁힐수록
    실루엣 폭 변동이 줄어드는지를 잰다 (실측: 수리 3px→1px, mutant 3px→2px).
    """
    src = _humanoid()
    assert not analyze(src).has_appendage, "이 그물은 부속 없는 도형이어야 의미가 있다"

    def width_swing(cfg):
        frames, _ = _frames(src, count=12, cfg=cfg)
        spans = [solid_alpha_bbox(f)[2] - solid_alpha_bbox(f)[0] for f in frames]
        return max(spans) - min(spans)

    wide = width_swing({**CFG, "depth": 0.10})
    narrow = width_swing({**CFG, "depth": 0.10, "torso_half": 4})
    assert narrow <= 1, (
        f"수동 밴드 4 인데 폭이 {narrow}px 흔들린다 — 밴드 밖이 밀리지 않고 늘어났다")
    assert narrow < wide, (
        f"밴드를 좁혔는데 폭 변동이 안 줄었다 (auto {wide}px vs 밴드4 {narrow}px) — "
        f"보호가 밴드에 앵커되지 않았다")


def test_depth_x_schema_bounds() -> None:
    """depth_x 는 null(따름)과 0(가로 끄기)이 유효하고, 범위 밖은 요란하게 거부."""
    def cur(dx):
        return {"states": {"idle": {"breathe": {"depth": 0.06, "breaths": 1,
                                                "lag": 0.1, "depth_x": dx}}}}
    assert state_breathe(cur(None), "idle")["depth_x"] is None
    base = {"states": {"idle": {"breathe": {"depth": 0.06, "breaths": 1, "lag": 0.1}}}}
    assert state_breathe(base, "idle")["depth_x"] is None
    assert state_breathe(cur(0), "idle")["depth_x"] == 0.0
    assert state_breathe(cur(0.12), "idle")["depth_x"] == 0.12
    for bad in (-0.01, 0.5, "abc"):
        with pytest.raises(SystemExit):
            state_breathe(cur(bad), "idle")
