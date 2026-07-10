"""detect_pixel_pitch 를 합성 정답 데이터로 고정한다.

논리 픽셀아트를 정수 배율 k 로 NEAREST 업스케일하면 참 피치는 정확히 k 다.
예전 구현은 `w = 1 if p >= 8 else 0` 때문에 k=8,10,12,14 에서 약수 k/2 를
반환했다 (창이 열린 참 피치의 우연 기대치가 3/p 로 부풀어, 창이 닫힌 약수에
졌다). 이 테스트가 그 회귀를 막는다.
"""
import random

from PIL import Image

from sprite_gen.extract import detect_pixel_pitch, _grid_phase

PALETTE = [
    (240, 210, 175),
    (60, 40, 30),
    (40, 90, 180),
    (230, 225, 200),
    (150, 90, 50),
    (20, 20, 20),
]


def _logical_art(width: int = 24, height: int = 40, seed: int = 11) -> Image.Image:
    """비주기 무작위 도트. 주기적 패턴이면 약수도 '진짜' 격자가 되어 테스트가 무의미해진다."""
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            px[x, y] = rng.choice(PALETTE)
    return img


def test_integer_pitch_is_detected_exactly():
    art = _logical_art()
    for k in (4, 6, 8, 10, 12, 14, 16, 17, 20, 24, 32):
        upscaled = art.resize((art.width * k, art.height * k), Image.NEAREST)
        assert detect_pixel_pitch(upscaled) == k, f"pitch {k} misdetected"


def test_divisor_is_not_preferred_over_true_pitch():
    """k=12 에서 6 을 반환하던 회귀의 최소 재현."""
    art = _logical_art()
    upscaled = art.resize((art.width * 12, art.height * 12), Image.NEAREST)
    assert detect_pixel_pitch(upscaled) == 12


def test_phase_follows_crop_offset():
    art = _logical_art()
    k = 16
    upscaled = art.resize((art.width * k, art.height * k), Image.NEAREST)
    for offset in (0, 3, 7, 11):
        cropped = upscaled.crop((offset, offset, upscaled.width, upscaled.height))
        assert detect_pixel_pitch(cropped) == k
        assert _grid_phase(cropped.convert("RGBA"), k)[0] == (-offset) % k


def test_no_grid_falls_back_to_one():
    """격자가 없는 사진 같은 입력은 1(스냅 안 함)로 관측 가능하게 떨어진다."""
    rng = random.Random(3)
    noise = Image.new("RGB", (200, 200))
    px = noise.load()
    for y in range(200):
        for x in range(200):
            px[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    assert detect_pixel_pitch(noise) == 1
