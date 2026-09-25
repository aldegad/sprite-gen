"""Synthetic regressions for the opt-in edge decontamination pass (`decontam: "palette"`).

Every scene here is drawn at 4x and box-downsampled, so each pixel's true coverage
and true colour are known exactly, then composited on a painted key. Red hair
strands are the case the pass exists for: their blends with green read as barely
key-tinted to the engine's tint axis, stay opaque, and come out olive or orange.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from sprite_gen.frames import decontam
from sprite_gen.frames.cutout import cutout
from sprite_gen.frames.extract import detect_background_key_rgb, hard_key_mask, remove_chroma_background
from sprite_gen.video import frames as frames_mod

SS = 4
GREEN_PAINTED = (18, 232, 26)
MAGENTA_PAINTED = (232, 20, 226)
HAIR = (200, 34, 44)
LINEART = (92, 14, 24)
HIGHLIGHT = (245, 118, 118)
CLOTH = (236, 234, 230)
GEM = (30, 110, 40)  # key-hued, yet farther than the hard cut from the painted key
KEY_ARGS = (96.0, 180.0, 18.0)  # the extract CLI's key / fringe thresholds


def _render(size=(176, 144), *, subject=HAIR, lineart=LINEART, highlight=HIGHLIGHT, strands=True, gem=False,
            blue_dot=False, shift=0.0):
    """(F, A): true colour and coverage of a hair block, a cloth block and thin strands."""
    w, h = size
    layers = []  # (mask at SS, colour)

    def canvas():
        return Image.new("L", (w * SS, h * SS), 0)

    def p(x, y):
        return ((x + shift) * SS, y * SS)

    m = canvas(); ImageDraw.Draw(m).rectangle([*p(40, 30), *p(100, 110)], fill=255)
    layers.append((m, subject))
    m = canvas(); draw = ImageDraw.Draw(m)
    draw.rectangle([*p(40, 30), *p(100, 110)], outline=255, width=int(1.5 * SS))
    for x in (58, 74, 90):  # drawn art has ink inside the silhouette too
        draw.line([p(x, 44), p(x - 6, 100)], fill=255, width=int(1.5 * SS))
    layers.append((m, lineart))
    m = canvas(); ImageDraw.Draw(m).rectangle([*p(108, 60), *p(150, 120)], fill=255)
    layers.append((m, CLOTH))
    if gem:
        m = canvas(); ImageDraw.Draw(m).rectangle([*p(56, 50), *p(84, 90)], fill=255)
        layers.append((m, GEM))
    if strands:
        for i in range(9):
            a = math.radians(200 + i * 14)
            x0, y0 = 40, 45 + i * 7
            pts = [p(x0 + math.cos(a) * r * 0.9, y0 + math.sin(a) * r * 0.5 + (r / 30) ** 2) for r in range(0, 38, 2)]
            m = canvas(); ImageDraw.Draw(m).line(pts, fill=255, width=max(1, int((0.7 + 0.35 * (i % 4)) * SS)))
            layers.append((m, highlight if i % 4 == 3 else subject))
    if blue_dot:
        m = canvas(); ImageDraw.Draw(m).ellipse([*p(96, 67), *p(105, 76)], fill=255)
        layers.append((m, (40, 70, 230)))
    P = np.zeros((h * SS, w * SS, 3)); A = np.zeros((h * SS, w * SS))
    for mask, colour in layers:
        cov = np.asarray(mask, dtype=np.float64) / 255
        P = np.asarray(colour, np.float64) * cov[..., None] + P * (1 - cov[..., None])
        A = cov + A * (1 - cov)
    P = P.reshape(h, SS, w, SS, 3).mean((1, 3)); A = A.reshape(h, SS, w, SS).mean((1, 3))
    F = np.where(A[..., None] > 0, P / np.maximum(A, 1e-9)[..., None], 0)
    return F, A


def _composite_on(F, A, key):
    C = F * A[..., None] + np.asarray(key, np.float64) * (1 - A[..., None])
    return np.clip(np.round(C), 0, 255).astype(np.uint8)


def _subsample_420(rgb: np.ndarray) -> np.ndarray:
    """Decode-like 4:2:0: full-resolution luma, chroma averaged over 2x2 blocks (BT.601)."""
    x = rgb.astype(np.float64)
    y = x @ np.array([0.299, 0.587, 0.114])
    cb = (x[..., 2] - y) * 0.564
    cr = (x[..., 0] - y) * 0.713
    h, w = y.shape
    for c in (cb, cr):
        blk = c[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2).mean((1, 3))
        c[: h // 2 * 2, : w // 2 * 2] = np.repeat(np.repeat(blk, 2, 0), 2, 1)
    r = y + cr / 0.713
    b = y + cb / 0.564
    g = (y - 0.299 * r - 0.114 * b) / 0.587
    return np.clip(np.round(np.stack([r, g, b], -1)), 0, 255).astype(np.uint8)


def _key(raw: np.ndarray, key=(0, 255, 0), **kwargs) -> tuple[np.ndarray, dict]:
    stats: dict = {}
    image = Image.fromarray(raw, "RGB")
    out = remove_chroma_background(image, key, *KEY_ARGS, decontam_stats=stats, **kwargs)
    return np.array(out), stats


def _edge(raw: np.ndarray, key=(0, 255, 0)) -> tuple[np.ndarray, np.ndarray]:
    """(edge, interior): the pass's band+flank region and what lies deeper."""
    image = Image.fromarray(raw, "RGB")
    painted = detect_background_key_rgb(image.convert("RGBA"), key)
    keyed, _ = hard_key_mask(raw.astype(np.int32), np.full(raw.shape[:2], 255), key, painted, KEY_ARGS[0])
    depth = decontam._ring_depth(keyed, decontam.BAND_PX)
    flank = keyed & (decontam._ring_depth(~keyed, decontam.FLANK_PX) <= decontam.FLANK_PX)
    band = ~keyed & (depth <= decontam.BAND_PX)
    return band | flank, ~keyed & ~band


def _lab(rgb):
    c = np.clip(np.asarray(rgb, np.float64) / 255, 0, 1)
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = lin @ np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                          [0.0193339, 0.1191920, 0.9503041]]).T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def _toward_key(out, F, A, edge, key_rgb=(0, 255, 0), degrees=12.0, ab=8.0):
    """Share of edge pixels covered in both output and truth whose colour turned toward the key.

    Coloured truth: hue rotated `degrees` or more in the direction of the key's hue (for a red
    strand under green, orange, gold, olive and green all lie that way: the green residue and
    the despill warm drift). Near-grey truth: a*/b* moved the key's way by `ab`. A darker or
    paler colour of the same hue is not contamination and does not count.
    """
    cov = edge & (out[..., 3] >= 26) & (A >= 0.1)
    lo, lt, lk = _lab(out[..., :3][cov]), _lab(F[cov]), _lab(np.array(key_rgb))
    hue = lambda lab: np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
    ho, ht, hk = hue(lo), hue(lt), float(hue(lk))
    towards = np.sign(((hk - ht + 180) % 360) - 180)
    rotation = (((ho - ht + 180) % 360) - 180) * towards
    coloured = np.hypot(lt[:, 1], lt[:, 2]) >= 12
    grey_shift = ((lo[:, 1] - lt[:, 1]) * np.sign(lk[1]) > ab) | ((lo[:, 2] - lt[:, 2]) * np.sign(lk[2]) > ab)
    return float(((coloured & (rotation >= degrees)) | (~coloured & grey_shift)).mean())


def _halo(out, F, A, edge):
    """Mean absolute composite error against the truth on black and on white, edge pixels."""
    a = out[..., 3:4] / 255.0
    errors = []
    for bg in (0.0, 255.0):
        got = out[..., :3] * a + bg * (1 - a)
        want = F * A[..., None] + bg * (1 - A[..., None])
        errors.append(float(np.abs(got - want)[edge].mean()))
    return errors


def _recall(out, A, edge):
    strand = edge & (A >= 0.1)
    return float((out[..., 3][strand] >= 26).mean())


# --------------------------------------------------------------------------- default path

def test_off_is_the_engine_byte_for_byte():
    F, A = _render()
    raw = _composite_on(F, A, GREEN_PAINTED)
    default = np.array(remove_chroma_background(Image.fromarray(raw), (0, 255, 0), *KEY_ARGS))
    off, stats = _key(raw, decontam="off")
    assert np.array_equal(default, off)
    assert stats == {}


def test_cutout_default_is_off_and_auto_runs_where_it_applies(tmp_path: Path):
    F, A = _render()
    src = tmp_path / "raw.png"
    raw = _composite_on(F, A, GREEN_PAINTED)
    Image.fromarray(raw).save(src)
    default = cutout(src, tmp_path / "default.png", key="green")
    auto = cutout(src, tmp_path / "auto.png", key="green", decontam="auto")
    palette = cutout(src, tmp_path / "palette.png", key="green", decontam="palette")
    assert "decontam" not in default
    engine = np.array(remove_chroma_background(Image.fromarray(raw), (0, 255, 0), *KEY_ARGS))
    assert np.array_equal(np.array(Image.open(tmp_path / "default.png")), engine)
    assert auto["decontam"]["mode"] == "auto" and auto["decontam"]["applied"] is True
    assert np.array_equal(np.array(Image.open(tmp_path / "auto.png")), np.array(Image.open(tmp_path / "palette.png")))
    assert palette["decontam"]["mode"] == "palette"


def test_auto_reports_why_it_did_not_run(tmp_path: Path):
    # the white matte has no key colour to remove
    img = Image.new("RGB", (64, 64), (250, 250, 248))
    ImageDraw.Draw(img).ellipse([16, 16, 48, 48], fill=(200, 40, 40))
    img.save(tmp_path / "white.png")
    stats = cutout(tmp_path / "white.png", tmp_path / "white-out.png", key="auto", decontam="auto")
    assert stats["route"] == "matte" and stats["decontam"] == {
        "mode": "auto", "applied": False, "reason": "the white matte has no key colour to remove"}
    # a subject with no interior to learn from: auto keys it as before and says why
    bar = np.full((80, 160, 3), GREEN_PAINTED, np.uint8)
    bar[38:41, 20:140] = HAIR
    Image.fromarray(bar).save(tmp_path / "bar.png")
    auto = cutout(tmp_path / "bar.png", tmp_path / "bar-auto.png", key="green", decontam="auto")
    cutout(tmp_path / "bar.png", tmp_path / "bar-off.png", key="green")
    assert auto["decontam"]["applied"] is False and "deeper than" in auto["decontam"]["reason"]
    assert np.array_equal(np.array(Image.open(tmp_path / "bar-auto.png")), np.array(Image.open(tmp_path / "bar-off.png")))
    with pytest.raises(SystemExit, match="no subject pixel lies deeper"):
        cutout(tmp_path / "bar.png", tmp_path / "bar-palette.png", key="green", decontam="palette")


# --------------------------------------------------------------------------- what the pass is for

@pytest.mark.parametrize("video", [False, True], ids=["still", "4:2:0"])
def test_palette_halves_key_contamination_without_growing_halo_or_losing_strands(video):
    F, A = _render()
    raw = _composite_on(F, A, GREEN_PAINTED)
    if video:
        raw = _subsample_420(raw)
    edge, _ = _edge(raw)
    off, _ = _key(raw)
    pal, stats = _key(raw, decontam="palette", decontam_fit="video" if video else "still")
    assert stats["palette_keyfree"] is True and stats["changed_px"] > 0
    before, after = _toward_key(off, F, A, edge), _toward_key(pal, F, A, edge)
    assert before > 0.02  # the engine leaves red strand edges olive/orange
    assert after <= 0.5 * before
    halo_off, halo_pal = _halo(off, F, A, edge), _halo(pal, F, A, edge)
    assert halo_pal[0] <= halo_off[0] and halo_pal[1] <= halo_off[1]
    assert _recall(pal, A, edge) >= _recall(off, A, edge) - 0.05


def test_red_strand_blend_comes_back_red_with_partial_coverage():
    """One pixel, the textbook case: 70 % red hair over the painted key."""
    F, A = _render()
    raw = _composite_on(F, A, GREEN_PAINTED)
    blend = np.round(0.7 * np.array(HAIR) + 0.3 * np.array(GREEN_PAINTED)).astype(np.uint8)
    raw[10:14, 10:14] = GREEN_PAINTED
    raw[12, 12] = blend  # an isolated strand pixel beside the key, next to nothing else
    raw[12, 13] = blend
    off, _ = _key(raw)
    pal, _ = _key(raw, decontam="palette")
    r, g, b, a = (int(v) for v in pal[12, 12])
    assert g < 0.35 * r and b < 0.4 * r  # red, not olive
    assert 140 <= a <= 220  # partial coverage, near the true 0.7
    ro, go, bo, _ = (int(v) for v in off[12, 12])
    assert go > 0.35 * ro  # the engine's result is not red


def test_interior_pixels_stay_byte_identical():
    F, A = _render()
    raw = _composite_on(F, A, GREEN_PAINTED)
    _, interior = _edge(raw)
    off, _ = _key(raw)
    pal, _ = _key(raw, decontam="palette")
    assert interior.sum() > 1000
    assert np.array_equal(off[interior], pal[interior])


def test_same_input_same_bytes():
    F, A = _render()
    raw = _subsample_420(_composite_on(F, A, GREEN_PAINTED))
    first, s1 = _key(raw, decontam="palette", decontam_fit="video")
    second, s2 = _key(raw, decontam="palette", decontam_fit="video")
    assert np.array_equal(first, second) and s1 == s2


def test_transparent_pixels_carry_no_colour():
    F, A = _render()
    pal, _ = _key(_composite_on(F, A, GREEN_PAINTED), decontam="palette")
    assert not pal[pal[..., 3] == 0, :3].any()


def test_magenta_key_is_handled_by_the_same_pass():
    # a teal subject, the kind a magenta key is chosen for (red would be magenta-adjacent)
    F, A = _render(subject=(30, 150, 140), lineart=(12, 52, 60), highlight=(150, 235, 225))
    raw = _composite_on(F, A, MAGENTA_PAINTED)
    edge, _ = _edge(raw, (255, 0, 255))
    off, _ = _key(raw, (255, 0, 255))
    pal, stats = _key(raw, (255, 0, 255), decontam="palette")
    assert stats["palette_keyfree"] is True
    before = _toward_key(off, F, A, edge, key_rgb=(255, 0, 255))
    after = _toward_key(pal, F, A, edge, key_rgb=(255, 0, 255))
    assert before > 0.02 and after <= 0.5 * before


# Grok's "magenta" comes back darker and bluer than the painted key (the values measured in
# test_chroma_key_relative). Warm colours carry no magenta hue of their own (min(R, B) - G < 0).
GROK_MAGENTA = (216, 46, 147)


def test_key_hue_cap_touches_only_key_hued_colours():
    colours = np.array([[110, 70, 50], [40, 70, 230], [232, 180, 150], [200, 40, 30],  # no magenta hue
                        [210, 60, 120], [190, 150, 190], [60, 200, 70], [30, 110, 40]], np.float64)
    green = decontam._cap_key_hue(colours, [1], [0, 2], 8.0)
    classic = colours.copy()
    classic[:, 1] = np.minimum(colours[:, 1], colours[:, [0, 2]].max(1) + 8)  # G <= max(R, B) + 8
    assert np.array_equal(green, classic)
    magenta = decontam._cap_key_hue(colours, [0, 2], [1], 8.0)
    assert np.array_equal(magenta[:4], colours[:4])  # warm, blue, skin and red are not magenta
    assert magenta[4].tolist() == [158, 60, 68]  # a red with a magenta cast stays red
    assert magenta[5].tolist() == [158, 150, 158]  # a grey with a magenta cast comes back grey
    assert np.array_equal(magenta[6:], colours[6:]) and (magenta <= colours).all()


@pytest.mark.parametrize("painted", [MAGENTA_PAINTED, GROK_MAGENTA], ids=["painted", "grok"])
def test_magenta_key_leaves_an_opaque_warm_subject_alone(painted):
    """A hard-edged brown square: nothing on its edge is a blend, so nothing may change."""
    raw = np.full((128, 128, 3), painted, np.uint8)
    raw[32:96, 32:96] = (110, 70, 50)
    raw[56:72, 56:72] = (230, 210, 190)
    off, _ = _key(raw, (255, 0, 255))
    pal, stats = _key(raw, (255, 0, 255), decontam="palette")
    assert stats["palette_keyfree"] is True
    assert [110, 70, 50] in stats["palette"] and [230, 210, 190] in stats["palette"]
    assert np.array_equal(pal, off)


@pytest.mark.parametrize("video", [False, True], ids=["still", "4:2:0"])
def test_magenta_key_keeps_a_skin_tone_and_cleans_its_edge(video):
    F, A = _render(subject=(232, 180, 150), lineart=(90, 50, 40), highlight=(250, 215, 195))
    raw = _composite_on(F, A, GROK_MAGENTA)
    if video:
        raw = _subsample_420(raw)
    edge, interior = _edge(raw, (255, 0, 255))
    off, _ = _key(raw, (255, 0, 255))
    pal, stats = _key(raw, (255, 0, 255), decontam="palette", decontam_fit="video" if video else "still")
    assert any(r > g + 40 for r, g, _b in stats["palette"])  # the skin tone is learned, not greyed toward G + 8
    halo_off, halo_pal = _halo(off, F, A, edge), _halo(pal, F, A, edge)
    assert halo_pal[0] <= 0.5 * halo_off[0] and halo_pal[1] <= 0.5 * halo_off[1]
    assert _recall(pal, A, edge) >= _recall(off, A, edge)
    assert np.array_equal(off[interior], pal[interior])


# --------------------------------------------------------------------------- guards

def test_subject_that_owns_key_material_keeps_it():
    F, A = _render(gem=True)
    raw = _composite_on(F, A, GREEN_PAINTED)
    pal, stats = _key(raw, decontam="palette")
    assert stats["palette_keyfree"] is False
    assert stats["key_material_share"] > decontam.KEY_MATERIAL_MAX_SHARE
    gem = pal[62:78, 62:78]
    assert (gem[..., 1] > gem[..., 0] + 60).all()  # the green gem is still green


def test_a_colour_the_palette_never_saw_keeps_the_engine_bytes():
    F, A = _render(blue_dot=True)
    raw = _composite_on(F, A, GREEN_PAINTED)
    off, _ = _key(raw)
    pal, stats = _key(raw, decontam="palette")
    dot = (slice(70, 73), slice(99, 102))
    assert np.array_equal(off[dot], pal[dot])
    assert (pal[dot][..., 2] > pal[dot][..., 0] + 60).all()  # still blue


def test_degenerate_key_fails_loud():
    F, A = _render()
    raw = _composite_on(F, A, (128, 128, 128))
    with pytest.raises(SystemExit, match="no key hue"):
        _key(raw, (128, 128, 128), decontam="palette")


def test_nothing_to_learn_from_fails_loud():
    F, A = _render()
    raw = _composite_on(F, A, GREEN_PAINTED)
    raw[:, :] = GREEN_PAINTED
    raw[60:63, 20:140] = HAIR  # a 3 px bar: every subject pixel is edge
    with pytest.raises(SystemExit, match="no subject pixel lies deeper"):
        _key(raw, decontam="palette")


def test_unknown_mode_and_fit_fail_loud():
    raw = _composite_on(*_render(), GREEN_PAINTED)
    with pytest.raises(SystemExit, match="unknown mode"):
        _key(raw, decontam="restore")
    with pytest.raises(SystemExit, match="unknown fit"):
        _key(raw, decontam="palette", decontam_fit="film")


def test_white_matte_refuses_decontam(tmp_path: Path):
    img = Image.new("RGB", (64, 64), (250, 250, 248))
    ImageDraw.Draw(img).ellipse([16, 16, 48, 48], fill=(200, 40, 40))
    img.save(tmp_path / "white.png")
    with pytest.raises(SystemExit, match="needs a chroma key background"):
        cutout(tmp_path / "white.png", tmp_path / "out.png", key="auto", decontam="palette")


# --------------------------------------------------------------------------- entry points

def test_cutout_reports_what_the_pass_did(tmp_path: Path):
    F, A = _render()
    src = tmp_path / "raw.png"
    Image.fromarray(_composite_on(F, A, GREEN_PAINTED)).save(src)
    stats = cutout(src, tmp_path / "out.png", key="green", decontam="palette")
    done = stats["decontam"]
    assert done["mode"] == "palette" and done["fit"] == "still" and done["palette_source"] == "frame"
    assert done["changed_px"] > 0 and len(done["palette"]) == done["palette_size"]
    json.dumps(stats)  # the report is plain JSON


def test_video_frames_learn_one_palette_per_clip(tmp_path: Path):
    raws = []
    for t in range(3):
        F, A = _render(shift=0.4 * t)
        path = tmp_path / f"frame-{t + 1:04d}.png"
        Image.fromarray(_subsample_420(_composite_on(F, A, GREEN_PAINTED))).save(path)
        raws.append(path)
    report = frames_mod.key_frames(raws, tmp_path / "keyed", key="green", check_edges=False, spill="full",
                                   decontam="palette")
    done = report["decontam"]
    assert done["mode"] == "palette" and done["fit"] == frames_mod.DECONTAM_FIT
    assert done["palette_source"] == "clip (learned on frame-0001.png)"
    assert all("decontam" in row for row in report["rows"])
    assert done["totals"]["changed_px"] == sum(row["decontam"]["changed_px"] for row in report["rows"])
    off = frames_mod.key_frames(raws, tmp_path / "plain", key="green", check_edges=False, spill="full")
    assert off["decontam"] == {"mode": "off", "applied_frames": 0}
    assert done["applied_frames"] == len(raws)
    assert all("decontam" not in row for row in off["rows"])


def test_video_frames_rejects_an_unknown_mode(tmp_path: Path):
    with pytest.raises(SystemExit, match="unknown --decontam"):
        frames_mod.run_frames(tmp_path / "missing.mp4", tmp_path / "out", key="green", allow_edge_contact=True,
                              report_path=None, spill="small", decontam="restore")


def test_row_extractor_reads_and_records_chroma_decontam(fixture_run_dir: Path):
    from conftest import run_script

    request_path = fixture_run_dir / "sprite-request.json"
    plain = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert plain.returncode == 0, plain.stderr
    assert "decontam" not in json.loads(request_path.read_text())["chroma"]
    frames_off = {p.name: p.read_bytes() for p in sorted((fixture_run_dir / "frames").rglob("frame-*.png"))}

    with_flag = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir), "--decontam", "palette")
    assert with_flag.returncode == 0, with_flag.stderr
    request = json.loads(request_path.read_text())
    assert request["chroma"]["decontam"] == "palette"
    frames_on = {p.name: p.read_bytes() for p in sorted((fixture_run_dir / "frames").rglob("frame-*.png"))}
    assert frames_on.keys() == frames_off.keys()


def test_cli_exposes_the_flag_on_every_entry_point():
    from sprite_gen import cli

    parser = cli._build_parser()
    for argv in (["cutout", "x.png", "--decontam", "palette"],
                 ["gen", "--out", "x.png", "--transparent", "--decontam", "palette"],
                 ["video-frames", "--clip", "c.mp4", "--out-dir", "o", "--decontam", "palette"],
                 ["extract", "--run-dir", "r", "--decontam", "palette"],
                 ["inspect", "--run-dir", "r", "--decontam", "palette"]):
        assert parser.parse_args(argv).decontam == "palette"


# --------------------------------------------------------------------------- gen (YCbCr matte)

class _FakeKeyProvider:
    """Paints the hair scene on a green key, the way a chroma-strategy provider returns it."""

    name = "fake"

    def __init__(self, transparency: str) -> None:
        self.transparency = transparency
        self.calls = 0

    def generate(self, request, workdir):
        from sprite_gen.gen import base as gen_base

        self.calls += 1
        F, A = _render()
        Image.fromarray(_composite_on(F, A, GREEN_PAINTED)).save(request.raw)
        return gen_base.ProviderRun(provider=self.name, elapsed_seconds=0.1, model=request.model)


def _gen(tmp_path: Path, monkeypatch, provider, **overrides) -> int:
    from sprite_gen import gen

    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: provider)
    kwargs = dict(provider="fake", prompt="a red-haired girl", out=tmp_path / "girl.png", ref=[], model=None,
                  aspect_ratio=None, transparent=True, alpha_mode="auto", chroma_key="green", white_check=None,
                  keep_session=False, report=tmp_path / "girl.json", prompt_file=None, workdir=None)
    kwargs.update(overrides)
    return gen.run(**kwargs)


def test_gen_decontam_runs_after_the_ycbcr_matte_and_reports(tmp_path: Path, monkeypatch):
    from sprite_gen.gen import base as gen_base

    provider = _FakeKeyProvider(gen_base.TRANSPARENCY_CHROMA)
    # the default publishes the matte as it is; auto adds the pass
    assert _gen(tmp_path, monkeypatch, provider, out=tmp_path / "off.png", report=tmp_path / "off.json") == 0
    assert _gen(tmp_path, monkeypatch, provider, decontam="auto") == 0
    off = json.loads((tmp_path / "off.json").read_text())
    on = json.loads((tmp_path / "girl.json").read_text())
    assert "decontam" not in off["chroma"]
    assert on["chroma"]["decontam"]["mode"] == "auto" and on["chroma"]["decontam"]["changed_px"] > 0
    raw = np.array(Image.open(tmp_path / "girl.png.raw.png").convert("RGB"))
    _, interior = _edge(raw)
    before, after = np.array(Image.open(tmp_path / "off.png")), np.array(Image.open(tmp_path / "girl.png"))
    assert np.array_equal(before[interior], after[interior])
    F, A = _render()
    edge, _ = _edge(raw)
    assert _toward_key(after, F, A, edge) <= 0.5 * _toward_key(before, F, A, edge)


def test_gen_decontam_refuses_native_alpha_before_generating(tmp_path: Path, monkeypatch):
    from sprite_gen.gen import base as gen_base

    provider = _FakeKeyProvider(gen_base.TRANSPARENCY_NATIVE)
    with pytest.raises(SystemExit, match="removes a chroma key"):
        _gen(tmp_path, monkeypatch, provider, decontam="palette")
    assert provider.calls == 0  # refused before a paid generation


def test_gen_decontam_needs_a_transparent_image(tmp_path: Path, monkeypatch):
    from sprite_gen.gen import base as gen_base

    provider = _FakeKeyProvider(gen_base.TRANSPARENCY_CHROMA)
    with pytest.raises(SystemExit, match="add --transparent"):
        _gen(tmp_path, monkeypatch, provider, transparent=False, decontam="palette")
    assert provider.calls == 0


def test_gen_auto_leaves_native_alpha_and_opaque_images_alone(tmp_path: Path, monkeypatch):
    from sprite_gen.gen import base as gen_base

    class _Native(_FakeKeyProvider):
        def generate(self, request, workdir):
            self.calls += 1
            image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            for y in range(2, 6):
                for x in range(2, 6):
                    image.putpixel((x, y), (200, 90, 20, 255))
            image.save(request.raw)
            return gen_base.ProviderRun(provider=self.name, elapsed_seconds=0.1, model=request.model)

    native = _Native(gen_base.TRANSPARENCY_NATIVE)
    assert _gen(tmp_path, monkeypatch, native, out=tmp_path / "native.png", report=tmp_path / "native.json",
                decontam="auto") == 0
    payload = json.loads((tmp_path / "native.json").read_text())
    assert payload["alpha"]["strategy"] == "native" and payload["chroma"] is None
    opaque = _FakeKeyProvider(gen_base.TRANSPARENCY_CHROMA)
    assert _gen(tmp_path, monkeypatch, opaque, transparent=False, out=tmp_path / "opaque.png",
                report=tmp_path / "opaque.json", decontam="auto") == 0
    assert opaque.calls == 1 and json.loads((tmp_path / "opaque.json").read_text())["chroma"] is None
