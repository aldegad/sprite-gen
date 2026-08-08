# SPDX-License-Identifier: Apache-2.0
"""Tests for the opt-in YCbCr chrominance matting path (chroma.mode: "ycbcr").

Port of perfectpixel-studio internal/sprite/chroma.go (MIT — see NOTICE).
What the RGB-distance path cannot do and this path must:

1. **Shaded/gradient key background** — a green key darkened by shading keeps
   its chroma direction but moves >96 RGB distance from the declared key, so
   the RGB threshold leaves it opaque. On the CbCr plane it stays in the key's
   chroma family and the border flood fill removes it.
2. **Key detection by histogram mode, not mean** — a border containing two
   chroma clusters must yield the dominant cluster's average, never the
   global mean (which lands between clusters and mattes neither).
3. **Key-direction despill** — a green-spilled subject pixel loses only its
   key-direction chroma; colors orthogonal to the key keep their saturation.
4. **Connectivity preserves the interior** — a key-family pixel enclosed by
   subject never connects to the border and survives the flood fill.
5. **Self-diagnostic rematte is observable** — when border sampling
   mis-detects the key (subject crowds the corners), the declared-key rematte
   engages and reports itself through the warnings channel (no silent
   fallback).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


extract = _load("extract_sprite_row_frames")

GREEN = (0, 255, 0)
# Shaded green: same chroma direction as the key, dimmed luma. RGB distance to
# pure green is 115 (past the 96 erase radius) yet its CbCr offset stays inside
# the lenient flood tolerance.
SHADED_GREEN = (0, 140, 0)
RED = (200, 40, 40)


def _opaque_count(image: Image.Image, threshold: int = 10) -> int:
    return sum(image.getchannel("A").histogram()[threshold + 1 :])


def _green_family_opaque(image: Image.Image) -> int:
    """Opaque pixels still in the key's chroma family (residual background)."""
    pixels = image.load()
    width, height = image.size
    _, key_cb, key_cr = extract.rgb_to_ycc(*GREEN)
    count = 0
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha <= 10:
                continue
            _, cb, cr = extract.rgb_to_ycc(red, green, blue)
            if ((cb - key_cb) ** 2 + (cr - key_cr) ** 2) ** 0.5 < 55.0:
                count += 1
    return count


def _shaded_key_strip() -> Image.Image:
    """Green-key strip with a wide shaded-green band and a red subject.

    The shaded band is kept >6px away from any pure-key pixel by making it a
    thick border-adjacent region, so the RGB path's depth-limited unmix cannot
    reach it either.
    """
    strip = Image.new("RGB", (96, 64), GREEN)
    # Shaded background band across the bottom third — border-connected.
    for y in range(44, 64):
        for x in range(96):
            strip.putpixel((x, y), SHADED_GREEN)
    # Red subject block, clear of the band.
    for y in range(8, 36):
        for x in range(30, 62):
            strip.putpixel((x, y), RED)
    return strip


def test_rgb_path_leaves_shaded_key_but_ycbcr_clears_it():
    strip = _shaded_key_strip()
    rgb_result = extract.remove_chroma_background(strip, GREEN, 96.0, 180.0, 18.0)
    ycc_result = extract.remove_chroma_background_ycbcr(strip, GREEN)

    # The shaded band's lower rows sit beyond the RGB path's depth-limited
    # unmix reach; count survivors there directly.
    band = (0, 50, 96, 64)
    rgb_residue = _opaque_count(rgb_result.crop(band))
    ycc_residue = _opaque_count(ycc_result.crop(band))
    assert rgb_residue > 500, "expected the RGB path to leave the shaded band opaque"
    assert ycc_residue == 0, f"ycbcr path left {ycc_residue} background pixels"
    assert _green_family_opaque(ycc_result) == 0

    # The subject must survive intact in both.
    subject = ycc_result.crop((30, 8, 62, 36))
    assert _opaque_count(subject) == subject.width * subject.height


def test_detect_background_key_uses_histogram_mode_not_mean():
    # Two chroma clusters on the border: dominant real background vs a
    # minority stripe. The mean of the samples is a muddy midpoint that
    # mattes neither cluster; both resolution branches (declared-key family
    # bias, plain histogram mode) must land on the dominant cluster.
    image = Image.new("RGB", (60, 60), (11, 238, 27))
    for y in range(60):
        for x in range(0, 12):  # left edge minority stripe
            image.putpixel((x, y), (200, 40, 200))
    rgba = image.convert("RGBA")
    # Declared key green → the green family on the border wins.
    detected = extract.detect_background_key_ycc(rgba, GREEN)
    assert extract.color_distance(detected, (11, 238, 27)) < 30.0
    # A declared key with no family on the border (blue) forces the pure
    # histogram-mode branch; the dominant green cluster must still win.
    detected_mode = extract.detect_background_key_ycc(rgba, (0, 77, 255))
    assert extract.color_distance(detected_mode, (11, 238, 27)) < 30.0


def test_despill_subtracts_key_direction_only():
    # Green-spilled interior pixels ((60, 220, 60): CbCr distance ~51 from the
    # key — inside the despill band) shielded from the border flood fill by a
    # solid red ring, so the despilled soft-alpha result survives.
    spilled = (60, 220, 60)
    strip = Image.new("RGB", (48, 48), GREEN)
    for y in range(10, 38):
        for x in range(10, 38):
            strip.putpixel((x, y), RED)
    for y in range(16, 32):
        for x in range(16, 32):
            strip.putpixel((x, y), spilled)
    result = extract.remove_chroma_background_ycbcr(strip, GREEN)
    red, green, blue, alpha = result.getpixel((24, 24))
    assert 0 < alpha < 255, "spill blend must resolve to partial coverage"
    # Green excess over the other channels must shrink after despill.
    before_excess = spilled[1] - (spilled[0] + spilled[2]) / 2
    after_excess = green - (red + blue) / 2
    assert after_excess < before_excess * 0.7
    # A color orthogonal to the key direction is preserved byte-exact.
    assert result.getpixel((12, 12)) == (*RED, 255)


def test_flood_fill_preserves_enclosed_key_family_pixel():
    strip = Image.new("RGB", (48, 48), GREEN)
    # Solid red subject with one shaded-green pixel sealed inside.
    for y in range(10, 38):
        for x in range(10, 38):
            strip.putpixel((x, y), RED)
    strip.putpixel((24, 24), SHADED_GREEN)
    result = extract.remove_chroma_background_ycbcr(strip, GREEN)
    assert result.getpixel((24, 24))[3] > 0, "enclosed key-family pixel must survive"
    # Border-connected shaded green (same color) is removed.
    strip.putpixel((2, 2), SHADED_GREEN)
    result2 = extract.remove_chroma_background_ycbcr(strip, GREEN)
    assert result2.getpixel((2, 2))[3] == 0


def test_self_diagnostic_rematte_is_reported():
    # Subject crowds every corner: border sampling detects the red frame as
    # the key, mattes away the subject, and the green background survives —
    # the residue symptom must trigger the declared-key rematte.
    strip = Image.new("RGB", (64, 64), RED)
    for y in range(16, 48):
        for x in range(16, 48):
            strip.putpixel((x, y), GREEN)
    warnings: list[str] = []
    result = extract.remove_chroma_background_ycbcr(strip, GREEN, warnings)
    assert warnings, "fallback rematte must be observable"
    assert result.getpixel((32, 32))[3] == 0, "green region must be keyed out"
    assert result.getpixel((4, 4))[3] == 255, "red subject must survive"


def test_extract_cli_ycbcr_mode_end_to_end(fixture_run_dir: Path):
    from conftest import run_script

    result = run_script(
        "extract_sprite_row_frames.py",
        "--run-dir",
        str(fixture_run_dir),
        "--chroma-mode",
        "ycbcr",
    )
    assert result.returncode == 0, result.stderr
    request = json.loads((fixture_run_dir / "sprite-request.json").read_text())
    assert request["chroma"]["mode"] == "ycbcr"
    for state, frames in (("idle", 4), ("walk", 3)):
        for index in range(frames):
            frame = fixture_run_dir / "frames" / state / f"frame-{index}.png"
            assert frame.is_file()
            with Image.open(frame) as opened:
                assert _opaque_count(opened.convert("RGBA")) > 0


def test_default_mode_stays_rgb(fixture_run_dir: Path):
    from conftest import run_script

    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert result.returncode == 0, result.stderr
    request = json.loads((fixture_run_dir / "sprite-request.json").read_text())
    assert request["chroma"]["mode"] == "rgb"


def test_gen_transparent_unmixes_key_blended_edges():
    """`gen --transparent` 경로가 키와 섞인 가장자리를 **풀어서 부분 알파로** 만든다.

    수홍 2026-08-08 "크로마키에 '약한 경로' 가 있니?": 이 경로의 fringe 보정은
    `a <= fringe_alpha_max(239)` 인 픽셀만 건드렸는데 생성 PNG 는 전부 불투명(a=255)이라
    한 번도 돌지 않았다. 그래서 잔머리처럼 키와 40% 섞인 픽셀이 이진 판정의 "키 아님" 에
    걸려 불투명한 채 초록을 남겼다. 추출과 같은 프리미티브(`unmix_key_blend`)로 despill +
    부분 알파를 만든다.

    시트 레벨 매팅을 통째로 가져오면 안 된다는 것도 함께 고정한다 — 그건 경계 flood fill
    이 큰 시트를 전제해서 작은 이미지의 **진짜 피사체 픽셀까지 지운다**(실측).
    """
    import tempfile

    from sprite_gen.gen import chroma as chroma_mod
    green, hair = (0, 255, 0), (60, 40, 30)
    tmp = Path(tempfile.mkdtemp())
    img = Image.new("RGBA", (64, 64), green + (255,))
    for y in range(16, 48):
        for x in range(20, 34):
            img.putpixel((x, y), hair + (255,))
    # 초록 40% 섞인 잔머리 열 — 예전에는 여기가 불투명 초록으로 남았다.
    blend = tuple(round(hair[c] * 0.6 + green[c] * 0.4) for c in range(3))
    for y in range(16, 48):
        img.putpixel((34, y), blend + (255,))
    src = tmp / "raw.png"
    img.save(src)
    out = tmp / "keyed.png"
    chroma_mod.key_transparent(src, out, key="green")
    result = Image.open(out).convert("RGBA")

    r, g, b, a = result.getpixel((34, 32))
    assert 0 < a < 255, f"블렌드 가장자리는 부분 알파가 되어야 한다: a={a}"
    assert not (g > r + 8 and g > b + 8), f"초록이 남았다: ({r},{g},{b})"
    for channel, want in enumerate(hair):
        assert abs((r, g, b)[channel] - want) <= 8, f"despill 색이 머리색과 다르다: ({r},{g},{b})"
    # 내부 피사체는 건드리지 않는다.
    assert result.getpixel((25, 32)) == hair + (255,)
