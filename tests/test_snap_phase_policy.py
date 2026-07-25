# SPDX-License-Identifier: Apache-2.0
"""추출 경로가 **어떤 위상으로 스냅하는지**를 고정한다 (헬퍼가 아니라 정책).

실사고 (수홍 2026-07-25, founder_v8 down_jump frame-0, plan
`sprite-gen/frame-pitch-consensus-eats-a-row`): 스냅이 피치 검출의 부산물인 히스토그램
위상을 쓰면 참 위상에서 pitch/2 까지 밀린다. `refine_edges_to_boundaries` 는 절단선을
±pitch/3 창 안에서만 당기므로 그 오차는 구조적으로 복구되지 않고, 캐릭터 눈 4행이
3행으로 병합됐다 (8칸 → 7칸).

`_best_phase` 를 직접 부르는 단위 테스트만으로는 이 회귀를 못 잡는다 — 헬퍼는 그대로 두고
**스냅 루프가 그 헬퍼를 안 쓰도록** 되돌리면 그런 테스트는 전부 통과한다 (검증자 하울
변이 실측: 스냅 루프를 히스토프램 위상으로 되돌려도 335 passed). 그래서 여기서는 실제
추출 스크립트를 태워 논리 격자 칸 수가 보존되는지를 본다.
"""
import json
import random
import shutil
from pathlib import Path

from PIL import Image

from conftest import run_script

PALETTE = [(240, 210, 175), (60, 40, 30), (40, 90, 180), (230, 225, 200), (150, 90, 50)]
PITCH = 13
# 참 위상을 비영으로 만드는 크롭량. 11 은 히스토그램 위상이 칸을 잃는 실측 지점이다.
PHASE_OFFSET = 11
MAGENTA = (255, 0, 255)


def _logical_art(seed: int = 11) -> Image.Image:
    """무작위 도트(격자 신호 확보) 위에 2x4 짙은 막대 둘 — 먹혔던 눈의 축소판."""
    rng = random.Random(seed)
    width, height = 24, 40
    art = Image.new("RGBA", (width, height))
    pixels = art.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (*rng.choice(PALETTE), 255)
    for left in (6, 14):
        for x in (left, left + 1):
            for y in range(12, 16):
                pixels[x, y] = (10, 8, 6, 255)
    return art


def _build_run(run_dir: Path, frames: int = 2) -> Image.Image:
    """위상이 비영인 합성 스트립 + 픽셀퍼펙 요청으로 런 디렉토리를 만든다."""
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    art = _logical_art()
    upscaled = art.resize((art.width * PITCH, art.height * PITCH), Image.NEAREST)
    # 첫 칸을 잘라내 참 위상을 격자 시작점에서 떼어놓는다.
    upscaled = upscaled.crop((PHASE_OFFSET, PHASE_OFFSET, upscaled.width, upscaled.height))

    slot = upscaled.width + 40
    strip = Image.new("RGBA", (slot * frames, upscaled.height + 40), (*MAGENTA, 255))
    for index in range(frames):
        strip.paste(upscaled, (index * slot + 20, 20), upscaled)
    strip.convert("RGB").save(run_dir / "raw" / "idle.png")

    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "phase-probe", "description": "offset-phase probe", "base_image": None},
        "cell": {"shape": "square", "width": 64, "height": 64,
                 "safe_margin_x": 2, "safe_margin_y": 2, "size": 64, "safe_margin": 2},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": list(MAGENTA),
                       "selection": "fallback"},
        "fit": {"pixel_perfect": True},
        "states": {"idle": {"frames": frames, "fps": 8, "loop": True,
                            "action": "offset-phase probe"}},
        "style": "synthetic, flat solid shapes on magenta",
        "motion_phase_guides": False,
    }
    (run_dir / "sprite-request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
    return art


def test_extraction_keeps_every_logical_cell_when_the_true_phase_is_offset(tmp_path: Path) -> None:
    """위상이 비영인 판을 추출해도 논리 칸 수가 한 칸도 줄지 않아야 한다.

    스냅이 히스토그램 위상으로 돌아가면 24x40 이 23x39 가 된다 (실측) — 잃은 행에 걸친
    2x4 덩어리가 이웃 칸에 병합되는 것이 부모 실사고의 기전이다.
    """
    run_dir = tmp_path / "run"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    art = _build_run(run_dir)

    result = run_script("extract_sprite_row_frames.py", "--run-dir", str(run_dir))
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]

    for index in range(2):
        frame = Image.open(run_dir / "frames" / "idle" / f"frame-{index}.png").convert("RGBA")
        bbox = frame.getbbox()
        logical = frame.crop(bbox) if bbox else frame
        assert logical.size == art.size, (
            f"frame {index}: 논리 격자 {logical.size} != {art.size} — "
            "위상이 밀려 칸이 병합됐다 (스냅이 실측 위상을 안 쓰는 회귀)")
