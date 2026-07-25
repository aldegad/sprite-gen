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
                                frame_anatomy, freeze_anatomy, phase_frame,
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


def test_zero_strain_is_byte_identical_to_the_source() -> None:
    """변형이 0 이면 **원본과 바이트 동일**이어야 한다.

    프레임끼리 비교하는 테스트는 전 프레임이 똑같이 밀려도 통과한다. 원본 대비로
    재야 축 재중심화 같은 전역 오프셋이 잡힌다 (실사고 2026-07-25: bbox 중앙을
    기준으로 잡아 축이 중앙과 다른 3/3 픽스처가 변형 0 에서 1px 밀렸다)."""
    for build in (_humanoid, _winged, _dome):
        src = build()
        cfg = dict(CFG)
        cfg["anatomy"] = freeze_anatomy(src, cfg)
        rest = phase_frame(src, {**cfg, "lag": 0.0}, 0.0)   # lag 0 + 위상 0 = 전 행 g==0
        assert rest.tobytes() == src.tobytes(), f"{build.__name__}: 변형 0 인데 원본과 다르다"


def test_phase_zero_is_not_identity_when_the_wave_travels() -> None:
    """진행파 지연이 있으면 위상 0 도 변형된 프레임이다.

    구 분할선 방식에선 위상 0 이 항등이라 소비자가 건너뛰어도 됐다. 봉투에선 윗행이
    `wave(-lag*u)` 만큼 변형되므로 건너뛰면 그 슬롯만 원본이 되어 아틀라스가 매 루프
    시작에서 튀고 GIF 굽기와 그림이 갈린다 (새미 검증 2026-07-25, 실측 353px 차이).
    소비자 3곳이 이 계약에 걸려 있다 — `compose_atlas`, `compare.js`, 그리고 확대
    편집기(`zoom-editor.js` 재생·필름스트립). 앞의 둘은 `if phase:` 가드였고, 편집기는
    호흡을 **끈** 상태에서 위상만 0 으로 넘기던 경로였다(끈 프리뷰가 워프돼 굽기와
    달랐다). 어느 쪽이든 "위상 0 은 원본" 이라는 가정이 되살아나면 여기서 잡힌다."""
    src = _humanoid()
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg)
    assert cfg["lag"] > 0, "이 계약은 지연이 있을 때의 이야기다"
    assert phase_frame(src, cfg, 0.0).tobytes() != src.tobytes(), \
        "위상 0 이 항등이면 소비자가 건너뛰어도 되는 것처럼 보인다"
    # 지연이 0 이면 위상 0 은 진짜 항등 — 두 경우의 차이가 계약의 핵심이다
    assert phase_frame(src, {**cfg, "lag": 0.0}, 0.0).tobytes() == src.tobytes()


def test_the_body_axis_column_is_a_fixed_point() -> None:
    """어떤 위상에서도 몸통 축 열은 제자리 — 이게 좌우 지터를 구조적으로 막는다."""
    src = _humanoid()
    anat = analyze(src)
    cfg = dict(CFG)
    cfg["anatomy"] = freeze_anatomy(src, cfg)
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
    cfg["anatomy"] = freeze_anatomy(src, cfg)
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
    cfg["anatomy"] = freeze_anatomy(src, cfg)
    frozen_row = cfg["anatomy"]["rigid_row"]

    same, redetected = frame_anatomy(src, cfg)
    assert same.rigid_row == frozen_row and redetected is False, "override 없으면 캐시 그대로"

    want = frozen_row + 10
    anat, redetected = frame_anatomy(src, {**cfg, "rigid_row": want})
    assert anat.rigid_row == want, "사람이 준 값이 얼린 값에 먹혔다"
    assert redetected is True, "캐시가 낡았으므로 재검출로 관측돼야 한다"
    assert anat.rigid_source == "manual"
    assert any("rigid-row-override" in w for w in anat.warnings)
