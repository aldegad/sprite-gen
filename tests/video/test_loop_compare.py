# SPDX-License-Identifier: Apache-2.0
"""Final pixels and comparable source times own the comparison, never old report scores."""

import hashlib
import json

import pytest
from PIL import Image, ImageDraw

from sprite_gen.cli import main
from sprite_gen.video import compare, evidence


def _frames():
    frames = []
    for k in range(12):
        im = Image.new("RGBA", (48, 64))
        d = ImageDraw.Draw(im)
        y = [0, 1, 2, 1, 0, -1][k % 6]
        d.rectangle((14, 8 + y, 34, 48 + y), fill=(220, 170, 120), outline=(20, 20, 20), width=2)
        # Two alternating feet and a twice-per-cycle bob remain in every comparison.
        d.rectangle((12 + k % 6, 49, 20 + k % 6, 59), fill=(30, 30, 30))
        d.rectangle((26 - k % 6, 49, 34 - k % 6, 59), fill=(30, 30, 30))
        frames.append(im)
    return frames


def _lose_outline(frame):
    im = frame.copy()
    pixels = im.load()
    for y in range(5, 49):
        for x in range(im.width):
            if pixels[x, y] == (20, 20, 20, 255):
                pixels[x, y] = (220, 170, 120, 255)
    return im


def _artifact(tmp_path, name, frames=None):
    frames = frames or _frames()
    folder = tmp_path / name
    folder.mkdir()
    n, w, h = len(frames), *frames[0].size
    strip = Image.new("RGBA", (n * w, h))
    for k, f in enumerate(frames):
        strip.paste(f, (k * w, 0))
    files = {k: folder / fn for k, fn in (("strip", "strip.png"), ("meta", "strip.json"), ("report", "loop.json"))}
    strip.save(files["strip"])
    src = {"kind": "keyed-frame-sequence", "sha256": "a" * 64, "frames": 36, "fps": 24.0}
    m = {"frames": n, "w": w, "h": h, "delay_ms": 41.67, "cycle_seconds": .5, "cycle_frames": n, "loop": True,
         "scale": 1.0, "source_rect": [0, 0, w, h], "foot_anchor": "none",
         "source_cut": {"source": src, "start": 4, "length": n, "samples": list(range(4, 4 + n))}}
    files["meta"].write_text(json.dumps(m) + "\n")
    files["report"].write_text(json.dumps({"kind": "sprite-gen-video-loop-report", "status": "passed", "source": src,
                                         "fps": 24.0, "frames_total": 36, "cycle": {"start": 4, "length": n},
                                         "strip": m, "resampled_seam_ratio": .1, "jolt": {"passed": True}}))
    return files


def _edit(path, mutate):
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc))


def _compare(a, b):
    return compare.compare(compare.Loop.read(**a), compare.Loop.read(**b))


def test_identical_final_pixels_are_not_improvement_and_digests_are_exact_bytes(tmp_path):
    a, b = _artifact(tmp_path, "a"), _artifact(tmp_path, "b")
    b["meta"].write_text(b["meta"].read_text() + "\n\n")
    _edit(b["report"], lambda d: d.update(resampled_seam_ratio=.00001))
    r = _compare(a, b)
    assert r["verdict"] == "non_regressing" and r["reasons"] == ["identical-pixels-and-timing"]
    for side, paths in (("baseline", a), ("candidate", b)):
        for key, path in paths.items():
            assert r[side]["artifacts"][key]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert r["baseline"]["artifacts"]["meta"] != r["candidate"]["artifacts"]["meta"]


def test_actual_outline_improvement_preserves_bob_and_inverse_is_regression(tmp_path):
    bad = _frames()
    bad[5] = _lose_outline(bad[5])
    a, b = _artifact(tmp_path, "bad", bad), _artifact(tmp_path, "good")
    r = _compare(a, b)
    assert r["verdict"] == "improved"
    assert r["axes"]["outline_loss"]["status"] == "improved"
    assert r["axes"]["silhouette_motion"]["status"] == "non_regressing"
    assert r["axes"]["body_motion"]["baseline"] == r["axes"]["body_motion"]["candidate"]
    assert _compare(b, a)["verdict"] == "regressed"


def test_follow_after_a_good_report_is_measured_from_final_pixels(tmp_path):
    good = _artifact(tmp_path, "good")
    final = _frames()
    final[5] = _lose_outline(final[5])
    bad = _artifact(tmp_path, "bad-final", final)
    _edit(bad["meta"], lambda d: d.update(follow={"gain": 1}))
    assert _compare(good, bad)["verdict"] == "regressed"
    assert _compare(good, bad)["candidate"]["follow"] is True


@pytest.mark.parametrize("change,reason", [
    (lambda d: d.pop("source_cut"), "source-provenance-missing"),
    (lambda d: d.pop("delay_ms"), "timing-missing"),
    (lambda d: d.update(delay_ms=80), "timing-inconsistent"),
    (lambda d: d.update(cycle_align={"shift": 0}), "aligned-source-phase-unverified"),
    (lambda d: d.pop("source_rect"), "spatial-basis-unverified"),
])
def test_missing_or_inconsistent_evidence_is_unknown(tmp_path, change, reason):
    a, b = _artifact(tmp_path, "a"), _artifact(tmp_path, "b")
    _edit(b["meta"], change)
    r = _compare(a, b)
    assert r["verdict"] == "unknown" and any(reason in s for s in r["reasons"])


def test_different_cut_with_favourable_report_ratio_cannot_win(tmp_path):
    a, b = _artifact(tmp_path, "a"), _artifact(tmp_path, "b")
    def cut(d):
        d["source_cut"].update(start=6, samples=list(range(6, 18)))
    _edit(b["meta"], cut)
    _edit(b["report"], lambda d: d.update(cycle={"start": 6, "length": 12}, resampled_seam_ratio=0))
    r = _compare(a, b)
    assert r["verdict"] == "unknown"
    assert "different-source-samples-phase-unverified" in r["reasons"]
    assert r["axes"]["seam"]["comparable"] is False


def test_a_flattened_pose_is_never_certified_as_improved(tmp_path):
    a = _artifact(tmp_path, "a")
    b = _artifact(tmp_path, "frozen", [_frames()[0]] * 12)
    assert _compare(a, b)["verdict"] in ("unknown", "regressed")


def test_flattening_colour_motion_with_the_same_alpha_cannot_win_on_seam(tmp_path):
    frames = _frames()
    # Draw a changing shirt colour inside identical coverage, including at the seam.
    coloured = []
    for k, frame in enumerate(frames):
        im = frame.copy()
        ImageDraw.Draw(im).rectangle((19, 25, 29, 35), fill=(120 + k * 5, 160, 190, 255))
        coloured.append(im)
    a = _artifact(tmp_path, "colour-motion", coloured)
    b = _artifact(tmp_path, "flattened-colour", frames)
    r = _compare(a, b)
    assert r["axes"]["silhouette_motion"]["status"] == "non_regressing"
    assert r["verdict"] in ("unknown", "regressed")


def test_cli_writes_unknown_successfully_and_refuses_corrupt_input_or_overwrite(tmp_path, capsys):
    a, b = _artifact(tmp_path, "a"), _artifact(tmp_path, "b")
    _edit(b["report"], lambda d: d.pop("source"))
    args = ["video-loop-compare"]
    for side, paths in (("baseline", a), ("candidate", b)):
        for key, path in paths.items():
            args.extend([f"--{side}-{key}", str(path)])
    output = tmp_path / "comparison.json"
    assert main(args + ["--report", str(output)]) == 0
    r = json.loads(output.read_text())
    assert r["kind"] == compare.KIND and r["verdict"] == "unknown"
    assert json.loads(capsys.readouterr().out) == r
    with pytest.raises(SystemExit, match="cannot overwrite"):
        main(args + ["--report", str(a["meta"])])
    _edit(b["meta"], lambda d: d.update(w=1))
    with pytest.raises(SystemExit, match="size agrees"):
        main(args + ["--report", str(output)])
    b["meta"].write_text("{")
    with pytest.raises(SystemExit, match="video-loop-compare"):
        main(args + ["--report", str(output)])


def test_gait_evidence_is_observation_for_fixed_and_automatic(tmp_path):
    for kind in ("fixed", "periodic"):
        r = evidence.gait_observation(_frames(), {"start": 0, "length": 12, "kind": kind})
        assert r["status"] == "unverified" and r["reason"] == "own-foot-contacts-not-identified"
        assert "signals" in r["silhouette"]
