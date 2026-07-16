# SPDX-License-Identifier: Apache-2.0
"""Pixel-perfect twins share the pp footprint + curated transforms re-snap to the grid.

1. plain/orig twins are fitted into the pixel-perfect frame's content bbox, so the
   curator's toggle compares pixel quality at the same size (no size jump).
2. apply_transform(snap_scale=s) re-quantizes a curated move/rotate onto the fixed
   logical grid — a pixel-variant bake can never produce off-grid smear.
"""

import json
import random

from PIL import Image

import sprite_gen.extract as extract_module
from sprite_gen.curation import apply_transform, pixel_snap_scale

MAGENTA = (255, 0, 255)


def _logical_art(width, height, seed):
    rng = random.Random(seed)
    art = Image.new("RGB", (width, height), MAGENTA)
    for y in range(height):
        for x in range(width):
            if rng.random() < 0.55:
                art.putpixel((x, y), (rng.randrange(30, 220), rng.randrange(30, 220), rng.randrange(30, 220)))
    return art


def _build_pp_run(root):
    """2-frame pixel-perfect fixture: logical art upscaled by an integer pitch."""
    run_dir = root / "run"
    (run_dir / "raw").mkdir(parents=True)
    pitch = 8
    frame = _logical_art(20, 36, seed=7).resize((20 * pitch, 36 * pitch), Image.Resampling.NEAREST)
    gap = 40
    strip = Image.new("RGB", (frame.width * 2 + gap * 3, frame.height + gap * 2), MAGENTA)
    strip.paste(frame, (gap, gap))
    strip.paste(frame, (frame.width + gap * 2, gap))
    strip.save(run_dir / "raw" / "walk.png")
    request = {
        "version": 1,
        "kind": "sprite-gen-request",
        "engine": "component-row",
        "character": {"id": "snapbot", "description": "pixel snap fixture", "base_image": None},
        "cell": {"shape": "square", "width": 96, "height": 96, "safe_margin_x": 8, "safe_margin_y": 8, "size": 96, "safe_margin": 8},
        "chroma_key": {"name": "magenta", "hex": "#FF00FF", "rgb": [255, 0, 255], "selection": "fallback"},
        "states": {"walk": {"frames": 2, "fps": 8, "loop": True, "action": "synthetic snap fixture"}},
        "fit": {"pixel_perfect": True, "logical_height": 48},
    }
    (run_dir / "sprite-request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_dir


def _conform_request(run_dir, conform):
    """cell 48 (pp_scale=1, cap 42) + logical_height 30 < art's native 36 logical."""
    import json as _json
    request = _json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    request["cell"] = {"shape": "square", "width": 48, "height": 48,
                      "safe_margin_x": 6, "safe_margin_y": 6, "size": 48, "safe_margin": 6}
    request["fit"]["logical_height"] = 30
    if conform is not None:
        request["fit"]["conform"] = conform
    (run_dir / "sprite-request.json").write_text(_json.dumps(request), encoding="utf-8")


def _walk_frame0_height(run_dir) -> int:
    im = Image.open(run_dir / "frames" / "walk" / "frame-0.png").convert("RGBA")
    bb = im.getchannel("A").getbbox()
    return bb[3] - bb[1]


def test_default_keeps_native_logical_size(tmp_path) -> None:
    """DEFAULT (no conform key) skips the contract squeeze: the output keeps the
    snapped native logical size (36) even though logical_height is 30 (physical
    cap 42 only). The squeeze runs only on explicit `fit.conform: true`."""
    run_dir = _build_pp_run(tmp_path)
    _conform_request(run_dir, conform=None)
    assert extract_module.run(run_dir=run_dir) == 0
    height = _walk_frame0_height(run_dir)
    assert height > 30, f"native size squeezed by default: {height}"


def test_conform_true_squeezes_to_contract(tmp_path) -> None:
    """Explicit fit.conform=true restores the legacy contract squeeze (36 -> <=30).
    min_used_pixels lowered: the squeezed 30px fixture sprite is legitimately small."""
    run_dir = _build_pp_run(tmp_path)
    _conform_request(run_dir, conform=True)
    assert extract_module.run(run_dir=run_dir, min_used_pixels=200) == 0
    height = _walk_frame0_height(run_dir)
    assert height <= 30, f"conform=true did not squeeze: {height}"


def test_pixel_snap_scale_resolution() -> None:
    assert pixel_snap_scale({"cell": {"size": 64}}) is None  # no fit
    assert pixel_snap_scale({"cell": {"size": 64}, "fit": {"resample": "kcentroid"}}) is None
    base = {"cell": {"width": 64, "height": 64, "safe_margin_x": 6, "safe_margin_y": 6}}
    assert pixel_snap_scale({**base, "fit": {"pixel_perfect": True, "logical_height": 48}}) == 1
    assert pixel_snap_scale({**base, "fit": {"pixel_perfect": True, "logical_height": 32}}) == 2
    assert pixel_snap_scale({**base, "fit": {"pixel_perfect": True}}) == 1  # logical = cell


def _blocks_uniform(image: Image.Image, scale: int) -> bool:
    px = image.load()
    for by in range(image.height // scale):
        for bx in range(image.width // scale):
            first = px[bx * scale, by * scale]
            for dy in range(scale):
                for dx in range(scale):
                    if px[bx * scale + dx, by * scale + dy] != first:
                        return False
    return True


def test_apply_transform_snap_keeps_grid() -> None:
    """A fractional move + rotation on a 2px-block frame stays block-uniform when snapped."""
    scale = 2
    cell = (64, 64)
    frame = Image.new("RGBA", cell, (0, 0, 0, 0))
    art = _logical_art(20, 24, seed=3).convert("RGBA").resize((40, 48), Image.Resampling.NEAREST)
    frame.alpha_composite(art, (12, 10))
    assert _blocks_uniform(frame, scale)

    moved = apply_transform(frame, {"dx": 0.7, "dy": -1.3, "rotate": 9.0, "scale": 1.05}, cell, snap_scale=scale)
    assert moved.size == frame.size
    assert moved.getbbox() is not None
    assert _blocks_uniform(moved, scale), "snapped bake must stay on the logical grid"

    smeared = apply_transform(frame, {"dx": 0.7, "dy": -1.3, "rotate": 9.0, "scale": 1.05}, cell)
    assert not _blocks_uniform(smeared, scale), "unsnapped BICUBIC control should break blocks"


def test_twins_share_pixel_perfect_footprint(tmp_path) -> None:
    run_dir = _build_pp_run(tmp_path)
    assert extract_module.run(run_dir=run_dir) == 0

    manifest = json.loads((run_dir / "frames" / "frames-manifest.json").read_text(encoding="utf-8"))
    row = manifest["rows"][0]
    assert row["ok"], row
    assert row.get("plain_files"), "pixel-perfect run must save plain twins"

    for index in range(2):
        pixel = Image.open(run_dir / "frames" / "walk" / f"frame-{index}.png").convert("RGBA")
        plain = Image.open(run_dir / "frames" / "walk" / f"frame-{index}.plain.png").convert("RGBA")
        pb = pixel.getchannel("A").getbbox()
        lb = plain.getchannel("A").getbbox()
        assert pb is not None and lb is not None
        # same footprint: identical box within grid-rounding tolerance (contain fit)
        for a, b in zip(pb, lb):
            assert abs(a - b) <= 2, f"frame {index}: pixel bbox {pb} vs plain bbox {lb}"

    # detected input grid (the actual cut lines) is recorded per frame, mapped into
    # cell coords: the lattice must cover the plain twin's content bbox.
    grids = row.get("input_grids")
    assert grids and len(grids) == 2
    for index, grid in enumerate(grids):
        assert grid and grid["x"] and grid["y"], f"frame {index}: missing input grid"
        plain = Image.open(run_dir / "frames" / "walk" / f"frame-{index}.plain.png").convert("RGBA")
        lb = plain.getchannel("A").getbbox()
        assert grid["x"][0] <= lb[0] + 2 and grid["x"][-1] >= lb[2] - 2
        assert grid["y"][0] <= lb[1] + 2 and grid["y"][-1] >= lb[3] - 2
        # ~one logical pixel per cut cell: line count tracks the sprite's logical size
        assert len(grid["x"]) - 1 >= 10 and len(grid["y"]) - 1 >= 20
        if row.get("orig_files"):
            orig = Image.open(run_dir / "frames" / "walk" / "orig" / f"frame-{index}.png").convert("RGBA")
            scale = orig.width // pixel.width
            assert scale >= 2
            ob = orig.getchannel("A").getbbox()
            for a, b in zip(ob, tuple(v * scale for v in pb)):
                assert abs(a - b) <= 2 * scale, f"frame {index}: orig bbox {ob} vs pixel bbox x{scale}"


def test_partial_generation_view_tolerance(tmp_path) -> None:
    """부분 생성(일부 상태만 추출) 세대: 관찰자(뷰)는 allow_pending_states 로 통과,
    소비자 기본 게이트는 여전히 fail-loud."""
    import json as _json
    import pytest as _pytest
    from sprite_gen.extract import load_consistent_frames_manifest
    run_dir = _build_pp_run(tmp_path)
    request = _json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    request["states"]["walk2"] = dict(request["states"]["walk"])  # 아직 raw 없는 두 번째 상태
    (run_dir / "sprite-request.json").write_text(_json.dumps(request), encoding="utf-8")
    assert extract_module.run(run_dir=run_dir, states="walk") == 0
    manifest = load_consistent_frames_manifest(run_dir, allow_pending_states=True)
    assert [r["state"] for r in manifest["rows"]] == ["walk"]
    with _pytest.raises(SystemExit, match="incomplete generation"):
        load_consistent_frames_manifest(run_dir)


def test_apply_pixel_edits_sidecar() -> None:
    """픽셀 편집 사이드카: 칠하기/지우기 합성, 원본 불변, 손상 키 무시."""
    from sprite_gen.curation import apply_pixel_edits
    frame = Image.new("RGBA", (8, 8), (10, 20, 30, 255))
    out = apply_pixel_edits(frame, {"1,1": "#ff0000", "2,2": None, "bad": "#000000", "99,0": "#000000"})
    assert out.getpixel((1, 1)) == (255, 0, 0, 255)
    assert out.getpixel((2, 2)) == (0, 0, 0, 0)
    assert out.getpixel((3, 3)) == (10, 20, 30, 255)
    assert frame.getpixel((1, 1)) == (10, 20, 30, 255)  # 원본 불변
    assert apply_pixel_edits(frame, None) is frame


def test_padded_component_no_ghost_bottom_row() -> None:
    """실사고 2026-07-14 회귀 (down_idle blink 발밑 1px 돌출, 수홍 발견).

    component_group_image 의 4px 투명 패딩 + 위상 추정 노이즈(참 lead 4 를 2.8 로
    측정 → _grid_edges 가 0 으로 스냅) 조합이 격자를 패딩만큼 위로 밀었다. 꼬리에
    자투리 셀이 생기고, 발바닥 블록의 쪼개진 하단 + 문턱 근처 알파(~134) 프린지가
    유령 픽셀로 응고해 최종 프레임 발밑에 1px 이 돌출했다. 스냅 전 알파 bbox 로
    조이면(tighten_components) 콘텐츠가 블록 정수배가 되어 격자가 등분으로 떨어진다.
    """
    pitch = 13.213
    blocks_w, blocks_h = 8, 6
    pad = 4
    solid_h = round(blocks_h * pitch) - 1  # 마지막 블록의 1px 은 프린지가 채운다 (실사고 기하)
    width = round(blocks_w * pitch)
    comp = Image.new("RGBA", (width + pad * 2, solid_h + 1 + pad * 2), (0, 0, 0, 0))
    for y in range(solid_h):
        for x in range(width):
            comp.putpixel((pad + x, pad + y), (60, 90, 180, 255))
    for x in range(width):  # 문턱(128) 살짝 위의 접지 프린지 — 육안으론 거의 안 보인다
        comp.putpixel((pad + x, pad + solid_h), (60, 90, 180, 134))

    noisy_phase = (2.8, 2.8)  # 참 lead=4 인데 lead-스냅 문턱(pitch/4=3.3) 아래로 측정된 실사고 값

    # 버그 경로 재현: 패딩째 스냅하면 유령 바닥 행이 응고한다 (테스트가 무는지 자기검증)
    buggy = extract_module.grid_snap_downscale(comp, (pitch, pitch), True, noisy_phase)
    assert buggy.getbbox()[3] > blocks_h

    tight = extract_module.tighten_components([comp])[0]
    assert tight.size == (width, solid_h + 1)
    fixed = extract_module.grid_snap_downscale(tight, (pitch, pitch), True, noisy_phase)
    bbox = fixed.getbbox()
    assert bbox[3] - bbox[1] == blocks_h, f"ghost row survived: {fixed.size} bbox={bbox}"


def test_fringe_does_not_inflate_grid() -> None:
    """실사고 2026-07-17 회귀 (founder_v7 pp 부스러기/디테일 뭉개짐, 수홍 발견).

    크로마 제거가 남긴 sub-128 AA 프린지가 any-alpha `getbbox()` 크롭에 포함되면
    격자 원점이 프린지 폭만큼 밀리고 `_grid_edges` 셀 개수 반올림이 한 칸 는다 —
    모든 셀이 참 블록을 빗겨 샘플링해 색이 섞이고, 실루엣 밖 부스러기 열이 응고한다.
    tighten_components 는 solid alpha bbox(α>=128) 로 조여 프린지를 격자 계산
    밖으로 밀어낸다 (grid_snap_downscale/binarize_alpha 와 동일 불투명 기준).
    """
    pitch = 12
    blocks_w, blocks_h = 12, 10
    lut = [(200, 60, 60), (60, 200, 60), (60, 60, 200), (220, 160, 40),
           (240, 230, 210), (90, 50, 20), (20, 20, 20), (120, 160, 220)]

    def block_color(bx: int, by: int) -> tuple[int, int, int]:
        return lut[(bx * 7 + by * 3) % len(lut)]

    solid_w, solid_h = blocks_w * pitch, blocks_h * pitch
    fringe_left, fringe_top = 7, 5
    comp = Image.new("RGBA", (fringe_left + solid_w, fringe_top + solid_h), (0, 0, 0, 0))
    for y in range(solid_h):
        for x in range(solid_w):
            comp.putpixel((fringe_left + x, fringe_top + y), block_color(x // pitch, y // pitch) + (255,))
    for y in range(comp.height):  # 남는 자리 전부 문턱 미만(α=90) 프린지
        for x in range(comp.width):
            if comp.getpixel((x, y))[3] == 0:
                comp.putpixel((x, y), (30, 30, 30, 90))

    def snap(image: Image.Image) -> Image.Image:
        grid, phase = extract_module.detect_pixel_grid(image)
        return extract_module.grid_snap_downscale(image, grid, True, phase)

    def exact(out: Image.Image) -> bool:
        return out.size == (blocks_w, blocks_h) and all(
            out.getpixel((bx, by))[:3] == block_color(bx, by)
            for by in range(blocks_h) for bx in range(blocks_w))

    # 버그 경로 재현: any-alpha bbox 크롭(프린지 포함)은 격자가 부푼다 (테스트가 무는지 자기검증)
    buggy = snap(comp.crop(comp.getbbox()))
    assert not exact(buggy), f"buggy path unexpectedly clean: {buggy.size}"

    tight = extract_module.tighten_components([comp])[0]
    assert tight.size == (solid_w, solid_h), f"tighten kept fringe: {tight.size}"
    fixed = snap(tight)
    assert exact(fixed), f"solid-bbox snap not exact: {fixed.size}"


def test_shared_palette_preserves_rare_saturated_color() -> None:
    """실사고 2026-07-17 회귀 (founder_v7 금색 머리끈·목걸이 실종, 수홍 발견).

    run-wide 팔레트 기본 24 는 다상태 배치에서 희소 포인트 컬러를 굶겼다 —
    population median-cut 이 큰 색군들을 쫓느라 0.2% 금색 클러스터를 이웃 박스에
    흡수한다 (실측: founder 36상태 배치 24색에서 금색 최근접 ΔRGB 59, 48색 5.5).
    기본 48 상향의 회귀 가드: 뚜렷한 색군 30개 + 희소 금색에서 기본 크기가
    금색을 보존해야 한다.
    """
    frame = Image.new("RGBA", (128, 75), (0, 0, 0, 0))
    clusters = [((k * 53) % 200, (k * 97) % 200, (k * 151) % 200) for k in range(30)]
    i = 0
    for y in range(75):
        for x in range(128):
            c = clusters[min(29, (y // 15) * 6 + (x // 22))]
            j = (i * 31) % 17 - 8
            frame.putpixel((x, y), tuple(max(0, min(255, v + j)) for v in c) + (255,))
            i += 1
    gold = (240, 158, 45)
    for y in range(4):
        for x in range(4):
            frame.putpixel((x, y), gold + (255,))

    def nearest(palette: list, c: tuple) -> float:
        return min(((e[0] - c[0]) ** 2 + (e[1] - c[1]) ** 2 + (e[2] - c[2]) ** 2) ** 0.5 for e in palette)

    starved = extract_module.build_shared_palette([frame], 24)
    assert nearest(starved, gold) > 30, "starvation setup lost its bite — recalibrate the fixture"
    healthy = extract_module.build_shared_palette([frame], 48)
    assert nearest(healthy, gold) <= 8, f"default-size palette still starves gold: {nearest(healthy, gold):.1f}"
