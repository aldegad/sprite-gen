# SPDX-License-Identifier: Apache-2.0
"""결정론 호흡 레이어(sprite_gen.breathe) 회귀 — 봉투 워프의 불변식.

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

from sprite_gen.anatomy import analyze
from sprite_gen.breathe import (DEFAULT_DEPTH, MAX_ROW_STRAIN, SMOOTH_CYCLE_FRAMES, TAPER,
                                bake_breathe_sequence, breathe_reads_smoothly,
                                envelope, fit_breathe_pattern, fitted_breath_count,
                                freeze_anatomy, parity_round, phase_frame,
                                recommended_breathe_frames, row_strain, wave)
from sprite_gen.curation import state_breathe
from sprite_gen.extract import solid_alpha_bbox

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


def _frames(image: Image.Image, count: int = 12, cfg: dict | None = None):
    cfg = dict(cfg or CFG)
    cfg["anatomy"] = freeze_anatomy(image, cfg)
    return bake_breathe_sequence([image] * count, cfg)


# ── 1. 강체 구간 항등 ───────────────────────────────────────────────

def test_rigid_region_is_bit_identical_across_frames() -> None:
    src = _humanoid()
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


def test_parity_round_is_identity_at_scale_one() -> None:
    # 강체 항등의 근거. 홀짝이 바뀌면 중앙 정렬이 1px 튄다.
    for width in (1, 2, 17, 64, 173):
        assert parity_round(width, 1.0) == width
        assert parity_round(width, 1.06) % 2 == width % 2
        assert parity_round(width, 0.94) % 2 == width % 2


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
    cfg["anatomy"] = freeze_anatomy(src, cfg)
    other = _winged()
    # 다른 스프라이트에 옛 해부 결과를 물려도 지문이 어긋나 다시 잰다
    healed = phase_frame(other, cfg, 0.25)
    assert healed.size == other.size
    assert solid_alpha_bbox(healed)[3] == solid_alpha_bbox(other)[3]


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


def test_new_schema_normalizes_and_clamps() -> None:
    cfg = state_breathe({"states": {"idle": {"breathe": {"depth": 99, "breaths": 99, "lag": -1}}}}, "idle")
    assert cfg == {"depth": 0.20, "breaths": 8, "lag": 0.0, "rigid_row": None, "anatomy": None}


def test_clipping_the_cell_raises_instead_of_cropping_the_head() -> None:
    """여백이 없어 늘어난 프레임이 셀 밖으로 나가면 조용히 자르지 않는다."""
    tight = _humanoid().crop(solid_alpha_bbox(_humanoid()))   # 여백 0
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(tight, cfg)
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
