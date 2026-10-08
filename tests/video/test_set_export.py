# SPDX-License-Identifier: Apache-2.0
"""`video-set-export`: a finished video-set as one eight-heading set (docs/direction-set.md).

The sets are real on disk: strips cut by `loop.build_strip` (or `run_loop` and `align_set` where the
alignment itself is under test) and a `set.report.json` in the shape `video-set` writes — one test
writes it with `batch.run_set` itself, offline (no clip, no vision call), so the reader cannot drift
from the writer."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.video import align
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import set_export as sx

FIXTURES = Path(__file__).parents[1] / "fixtures" / "facing"
WATCH = {"item": "the black smartwatch", "side": "left", "part": "wrist"}
FIVE = ("front", "back", "side@right", "front_diagonal@right", "back_diagonal@right")
EIGHT = ("front", "back", "side@right", "side@left", "front_diagonal@right", "front_diagonal@left",
         "back_diagonal@right", "back_diagonal@left")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frames(n: int = 6, *, height: int = 50, tag: int = 0) -> list[Image.Image]:
    """A figure on a 100 px floor, lopsided on purpose (a nose on its right, a wider right foot), with a
    marker that moves cell by cell so no two cells are alike and none is its own mirror image."""
    out = []
    for k in range(n):
        a = np.zeros((110, 110, 4), np.uint8)
        top = 100 - height
        a[top:100, 40:52] = (200, 60, 60, 255)  # body
        a[top + 5:top + 10, 52:58] = (60, 200, 60, 255)  # nose, on the right
        a[94:100, 38:44] = (30, 30, 30, 255)  # left foot
        a[94:100, 48:56] = (30, 30, 30, 255)  # right foot, wider
        a[top + 20:top + 22, 41 + k:43 + k] = (40 + 30 * k, (90 * tag) % 256, 255, 255)  # marker, one step right per cell
        out.append(Image.fromarray(a, "RGBA"))
    return out


def _item(root: Path, name: str, frames: list[Image.Image], *, anchor: str = "none", state: str = "walk",
          extra: dict | None = None) -> None:
    """A loop folder as video-loop leaves it: `<name>.strip.png`, its json and a GIF."""
    loop = root / name / "loop"
    loop.mkdir(parents=True)
    strip, meta = loop_mod.build_strip(frames, cycle_seconds=len(frames) / 24, anchor=anchor)
    meta["state"] = state
    meta.update(extra or {})
    strip.save(loop / f"{name}.strip.png")
    (loop / f"{name}.strip.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    cells = [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]
    save_clean_gif(cells, loop / f"{name}.gif", duration_ms=42, loop=0, alpha_threshold=128)


def _set(tmp_path: Path, views=FIVE, *, states=("walk",), facing: str = "right", handed=None, failed=(),
         frames=None, anchor: dict | None = None, extra: dict | None = None) -> Path:
    """A set folder: one item per (view, state) — `frames`/`anchor`/`extra` per item name — and its report."""
    root = tmp_path / "set"
    root.mkdir()
    both = "," in facing
    items = []
    for state in states:
        for i, view in enumerate(views):
            direction, _, turned = view.partition("@")
            name = f"{direction}-{turned}-{state}" if both and turned else f"{direction}-{state}"
            r = {"item": name, "direction": direction, "state": state, "dir": str(root / name)}
            if turned:
                r["turned"] = turned
            if name in failed:
                r |= {"ok": False, "error": "clip generation failed after 1 attempt(s)"}
            else:
                _item(root, name, (frames or {}).get(name) or _frames(tag=i), anchor=(anchor or {}).get(name, "none"),
                      state=state, extra=(extra or {}).get(name))
                r |= {"ok": True, "loop": {"strip": str(root / name / "loop" / f"{name}.strip.png")}}
            items.append(r)
    report = {"kind": "sprite-gen-video-set-report", "root": str(root), "states": list(states), "facing": facing,
              "facing_fix": "none", "directions": list(views), **({"handed": handed} if handed else {}), "body_height": None,
              "ok": sum(1 for r in items if r["ok"]), "failed": [r["item"] for r in items if not r["ok"]], "warnings": [],
              "items": items, "cycle_align": {}}
    (root / "set.report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return root


def _cells(path: Path, frames: int, w: int) -> list[np.ndarray]:
    a = np.asarray(Image.open(path).convert("RGBA"))
    return [a[:, k * w:(k + 1) * w] for k in range(frames)]


def test_each_cell_is_turned_over_where_it_stands_and_the_order_is_kept():
    strip, meta = loop_mod.build_strip(_frames(), cycle_seconds=0.25)
    n, w = meta["frames"], meta["w"]
    out = np.asarray(sx.mirror_cells(strip, n, w))
    src = np.asarray(strip)
    for k in range(n):
        assert np.array_equal(out[:, k * w:(k + 1) * w], src[:, k * w:(k + 1) * w][:, ::-1])
    # turning the whole strip over turns each cell over too, but plays them backwards
    whole = np.asarray(strip.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    assert np.array_equal(whole[:, :w], out[:, (n - 1) * w:])
    assert not np.array_equal(whole, out)


def test_five_right_facing_views_cut_by_video_set_make_all_eight_headings(tmp_path, monkeypatch):
    """`batch.run_set` writes the report (offline: no clip, vision or keying); its loops are real strips."""
    from sprite_gen.video import batch

    monkeypatch.setattr(batch.facing_mod.vision, "grok_inspect", lambda path, **kw: ("right", {}))
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 6, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)
    names = [f"{v.partition('@')[0]}-walk" for v in FIVE]

    def run_loop(keyed, out_dir, **kw):
        loop = Path(out_dir)
        loop.mkdir(parents=True, exist_ok=True)
        strip, meta = loop_mod.build_strip(_frames(tag=names.index(kw["name"])), cycle_seconds=0.25)
        strip.save(loop / f"{kw['name']}.strip.png")
        (loop / f"{kw['name']}.strip.json").write_text(json.dumps(meta, indent=2) + "\n")
        save_clean_gif([strip.crop((0, 0, meta["w"], meta["h"]))], loop / f"{kw['name']}.gif", duration_ms=42)
        return {"cycle": {"length": 6, "period_global": 6, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 6,
                "gif": {"file": f"{kw['name']}.gif"}, "webp": {"file": f"{kw['name']}.webp"},
                "strip": {"path": str(loop / f"{kw['name']}.strip.png")}}
    monkeypatch.setattr(batch.loop_mod, "run_loop", run_loop)

    def video(image, prompt, out, report, **kw):
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0
    root = tmp_path / "set"
    bases = {v.partition("@")[0]: FIXTURES / ("front.png" if "@" not in v else "right.png") for v in FIVE}
    result = batch.run_set(bases=bases, states=["walk"], root=root, character=None, duration=3, resolution="720p",
                           key="green", concurrency=1, force=False, gap=0, video_runner=video, walk_start="as-given",
                           align_cycles="off")
    assert result["ok"] == 5

    out = tmp_path / "export"
    assert cli.main(["video-set-export", "--set-dir", str(root), "--out-dir", str(out)]) == 0
    index = json.loads((out / "directions.json").read_text(encoding="utf-8"))
    assert index["kind"] == "sprite-gen-direction-set" and index["complete"]
    assert index["convention"]["up"] == "N" and index["convention"]["view"]["SE"] == "front_diagonal@right"
    walk = index["states"]["walk"]["headings"]
    assert list(walk) == list(sx.HEADINGS)
    assert {h: e["status"] for h, e in walk.items()} == {
        "S": "filmed", "SE": "filmed", "E": "filmed", "NE": "filmed", "N": "filmed",
        "NW": "mirrored", "W": "mirrored", "SW": "mirrored"}
    assert {h: walk[h]["mirroredFrom"] for h in ("W", "SW", "NW")} == {"W": "E", "SW": "SE", "NW": "NE"}
    assert walk["S"]["source_item"] == "front-walk" and walk["W"]["source_item"] == "side-walk"
    # provenance: the bytes it was made from, and the bytes it is
    side = root / "side-walk" / "loop" / "side-walk.strip.png"
    assert walk["W"]["source_sha256"] == _sha(side) == walk["E"]["sha256"] == _sha(out / "walk" / "E.png")
    assert walk["W"]["sha256"] == _sha(out / "walk" / "W.png") != walk["E"]["sha256"]
    w_json = json.loads((out / "walk" / "W.json").read_text(encoding="utf-8"))
    assert w_json["mirroredFrom"]["source_sha256"] == _sha(side) and w_json["mirroredFrom"]["heading"] == "E"
    n, w = walk["E"]["cells"], walk["E"]["cell"][0]
    for e_cell, w_cell in zip(_cells(out / "walk" / "E.png", n, w), _cells(out / "walk" / "W.png", n, w)):
        assert np.array_equal(w_cell, e_cell[:, ::-1])
    # the filmed headings are copies (strip, json, GIF); a mirror's GIF is the source's turned over
    assert (out / "walk" / "E.json").read_bytes() == side.with_name("side-walk.strip.json").read_bytes()
    assert walk["E"]["previews"] == {"gif": "walk/E.gif"} and walk["W"]["previews"] == {"gif": "walk/W.gif"}
    assert (out / "walk" / "E.gif").read_bytes() == side.with_name("side-walk.gif").read_bytes()
    assert walk["W"]["previews_from"] == "source-mirrored" and walk["W"]["phase_rotated_by"] == 0
    assert "| walk | W | mirrored | E (side-walk) |" in (out / "table.md").read_text(encoding="utf-8")


def test_the_foot_anchor_is_the_pivot_and_is_mirrored_with_the_cells(tmp_path):
    root = _set(tmp_path, ("front", "side@right"), anchor={"side-walk": "feet"})
    index = sx.export_set(root, tmp_path / "out")
    walk = index["states"]["walk"]["headings"]
    meta = json.loads((root / "side-walk" / "loop" / "side-walk.strip.json").read_text(encoding="utf-8"))
    w, h = meta["w"], meta["h"]
    assert walk["E"]["pivot"] == meta["anchor"] == [meta["foot_x"], h] and walk["E"]["pivot_source"] == "strip-anchor"
    assert walk["W"]["pivot"] == [w - meta["foot_x"], h] and walk["W"]["pivot_source"] == "mirrored:strip-anchor"
    w_json = json.loads((tmp_path / "out" / "walk" / "W.json").read_text(encoding="utf-8"))
    assert w_json["anchor"] == [w - meta["foot_x"], h] and w_json["foot_x"] == w - meta["foot_x"]
    assert set(walk["W"]["mirror"]["x_mirrored"]) == {"anchor", "foot_x"}


def test_a_strip_with_no_anchor_stands_on_its_alpha_and_the_mirror_on_the_mirrored_one(tmp_path):
    root = _set(tmp_path, ("front", "side@right"))
    out = tmp_path / "out"
    walk = sx.export_set(root, out)["states"]["walk"]["headings"]
    e, west = walk["E"], walk["W"]
    w, h = e["cell"]
    assert e["pivot_source"] == "alpha" and e["pivot"][1] == h  # the feet touch the strip's floor
    assert e["pivot"][0] != w / 2  # the wider right foot pulls it off the cell's middle
    assert west["pivot"] == [w - e["pivot"][0], h]
    # read off the mirrored pixels themselves, the alpha pivot is the mirrored one
    assert sx.alpha_pivot(Image.open(out / "walk" / "W.png"), e["cells"], w, h) == west["pivot"]


def test_feet_a_camera_from_above_draws_at_two_depths_both_count():
    """A camera from above draws the near foot lower than the far one, and here the left foot is the near
    one in four cells of six: the pivot stands between the feet, not on the foot that is lower more often
    (the KUMA walks, docs/direction-set.md#pivot)."""
    frames = []
    for k in range(6):
        a = np.zeros((110, 110, 4), np.uint8)
        a[40:94, 44:56] = (200, 60, 60, 255)  # body, centred on x 50
        low_left = k < 4
        a[(94 if low_left else 88):(100 if low_left else 94), 36:46] = (30, 30, 30, 255)
        a[(88 if low_left else 94):(94 if low_left else 100), 54:64] = (30, 30, 30, 255)
        frames.append(Image.fromarray(a, "RGBA"))
    strip, meta = loop_mod.build_strip(frames, cycle_seconds=0.25)
    pivot = sx.alpha_pivot(strip, meta["frames"], meta["w"], meta["h"])
    assert pivot == [meta["w"] / 2, meta["h"]]  # x 50 of the frame: between the feet, under the body


def test_x_offsets_are_negated_and_an_unturned_mirror_starts_on_the_other_own_foot(tmp_path):
    extra = {"side-walk": {"wrap_dx_px": 3, "cycle_align": {"start_foot": "right", "view": "side@right", "strikes": [0, 3]},
                           "sequence": [{"frame": 0, "durationMs": 40, "shake": [2, -1]}]},
             "front-walk": {"cycle_align": {"start_foot": "right", "view": "front"}}}
    root = _set(tmp_path, ("front", "side@right"), extra=extra)
    index = sx.export_set(root, tmp_path / "out", keep_mirror_phase=True)
    w_json = json.loads((tmp_path / "out" / "walk" / "W.json").read_text(encoding="utf-8"))
    assert w_json["wrap_dx_px"] == -3 and w_json["sequence"][0]["shake"] == [-2, -1]
    # the source's own cut records (its source-frame rectangle, its alignment) stay in E.json
    assert "cycle_align" not in w_json and "source_rect" not in w_json
    assert w_json["mirroredFrom"]["not_carried"] == ["source_rect", "cycle_align"]
    assert w_json["mirroredFrom"]["start_foot"] == "left" and w_json["mirroredFrom"]["phase_why"] == "--keep-mirror-phase"
    walk = index["states"]["walk"]["headings"]
    assert (walk["S"]["start_foot"], walk["E"]["start_foot"], walk["W"]["start_foot"]) == ("right", "right", "left")
    assert walk["E"]["aligned"] and walk["W"]["aligned"] and walk["W"]["phase_rotated_by"] == 0
    assert any("different own feet" in line and "W left (mirrored from E)" in line for line in index["warnings"])


@pytest.mark.parametrize("record, extra, rotate, why", [
    ({"start_foot": "right", "strikes": [0, 4]}, {}, 4, "other strike, frame 4"),
    ({"start_foot": "right", "strikes": [2, 0]}, {}, 2, "other strike, frame 2"),
    ({"start_foot": "left"}, {"steps": 2}, 3, "half the cells"),  # no strike recorded: one cycle of two steps
    ({"start_foot": "right"}, {"steps": 1}, 0, "not one cycle"),
    ({"start_foot": None, "strikes": [0, 3]}, {}, 0, "did not name"),
    ({"start_foot": "right", "strikes": [0, 3]}, {"loop": False}, 0, "one-shot"),
])
def test_a_mirrored_aligned_loop_is_turned_to_start_on_the_sets_foot(tmp_path, record, extra, rotate, why):
    extra = {"side-walk": {"cycle_align": record, **extra}}
    root = _set(tmp_path, ("front", "side@right"), extra=extra)
    out = tmp_path / "out"
    index = sx.export_set(root, out)
    west = index["states"]["walk"]["headings"]["W"]
    assert west["phase_rotated_by"] == rotate and why in west["phase_why"]
    n, w = west["cells"], west["cell"][0]
    east = _cells(out / "walk" / "E.png", n, w)
    for k, cell in enumerate(_cells(out / "walk" / "W.png", n, w)):  # the same cycle, forward, from cell `rotate`
        assert np.array_equal(cell, east[(k + rotate) % n][:, ::-1])
    w_json = json.loads((out / "walk" / "W.json").read_text(encoding="utf-8"))
    e_json = json.loads((out / "walk" / "E.json").read_text(encoding="utf-8"))
    assert w_json["sample_indices"] == e_json["sample_indices"][rotate:] + e_json["sample_indices"][:rotate]
    if rotate:
        # turned, it starts as the source's own start foot lands; its GIF is made from its own cells
        assert west["start_foot"] == record["start_foot"] and west["previews_from"] == "cells"
        gif = Image.open(out / "walk" / "W.gif")
        assert gif.n_frames == n and np.array_equal(np.asarray(gif.convert("RGBA"))[..., 3] > 0, east[rotate][:, ::-1][..., 3] > 0)
        assert not [line for line in index["warnings"] if "different own feet" in line]


def test_a_mirrors_gif_and_webp_turn_each_frame_over_and_keep_its_timing(tmp_path, monkeypatch):
    root = _set(tmp_path, ("front", "side@right"))
    loop = root / "side-walk" / "loop"
    strip = Image.open(loop / "side-walk.strip.png")
    meta = json.loads((loop / "side-walk.strip.json").read_text(encoding="utf-8"))
    cells = [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]
    durations = [40, 80, 40, 120, 40, 60]
    (loop / "side-walk.gif").write_bytes(sx._gif_bytes(cells, durations, 0))
    binary = sx._img2webp()
    if binary:
        (loop / "side-walk.webp").write_bytes(sx._webp_bytes(cells, durations, 0, binary, tmp_path / "webp-work"))
    out = tmp_path / "out"
    west = sx.export_set(root, out)["states"]["walk"]["headings"]["W"]
    for ext in ("gif", "webp") if binary else ("gif",):
        src, durs, loops = sx._frames_of((loop / f"side-walk.{ext}").read_bytes())
        got, got_durs, got_loops = sx._frames_of((out / "walk" / f"W.{ext}").read_bytes())
        assert (got_durs, got_loops) == (durs, loops) and durs == durations, ext
        for a, b in zip(got, src):
            a, b = np.asarray(a), np.asarray(b)[:, ::-1]
            assert np.array_equal(a[..., 3], b[..., 3]) and np.array_equal(a[a[..., 3] > 0], b[b[..., 3] > 0]), ext
    assert west["previews"] == ({"gif": "walk/W.gif", "webp": "walk/W.webp"} if binary else {"gif": "walk/W.gif"})
    # without an img2webp that keeps the alpha exact, a mirror has no WebP, and says so
    monkeypatch.setattr(sx, "_img2webp", lambda: None)
    (loop / "side-walk.webp").write_bytes(b"RIFF-not-read")
    west = sx.export_set(root, tmp_path / "out2")["states"]["walk"]["headings"]["W"]
    assert west["previews"] == {"gif": "walk/W.gif"} and "img2webp" in west["preview_notes"]["webp"]
    assert not (tmp_path / "out2" / "walk" / "W.webp").exists()


def test_headings_ask_each_state_for_its_own_set(tmp_path):
    """The KUMA rule: movement in all eight, actions in the four diagonals, two of them mirrored."""
    root = _set(tmp_path, FIVE, states=("walk", "attack"))
    out = tmp_path / "out"
    assert sx.run(set_dir=root, out_dir=out, headings="walk=all,attack=diagonals") == 0
    index = json.loads((out / "directions.json").read_text(encoding="utf-8"))
    walk, attack = index["states"]["walk"], index["states"]["attack"]
    assert walk["requested"] == list(sx.HEADINGS) and walk["requested_as"] == "all"
    assert attack["requested"] == ["SE", "NE", "NW", "SW"] and attack["requested_as"] == "diagonals"
    assert {h: e["status"] for h, e in attack["headings"].items()} == {"SE": "filmed", "NE": "filmed", "NW": "mirrored", "SW": "mirrored"}
    assert sorted(p.stem for p in (out / "attack").glob("*.png")) == ["NE", "NW", "SE", "SW"]
    assert index["complete"] and index["headings_arg"] == "walk=all,attack=diagonals"
    # a list for every state; a mirror's source is read though it is not asked for
    west = sx.export_set(root, tmp_path / "w", headings="W,S")["states"]["walk"]
    assert list(west["headings"]) == ["S", "W"] and west["headings"]["W"]["mirroredFrom"] == "E"
    assert not (tmp_path / "w" / "walk" / "E.png").exists()


def test_a_heading_asked_for_and_not_there_is_the_failure(tmp_path):
    root = _set(tmp_path, ("front", "side@right"))
    assert sx.run(set_dir=root, out_dir=tmp_path / "out", headings="S,E,W") == 0
    assert sx.run(set_dir=root, out_dir=tmp_path / "out2", headings="diagonals") == 1
    st = json.loads((tmp_path / "out2" / "directions.json").read_text(encoding="utf-8"))["states"]["walk"]
    assert list(st["missing"]) == ["SE", "NE", "NW", "SW"] and not list((tmp_path / "out2").glob("walk/*.png"))


@pytest.mark.parametrize("spec, error", [
    ("S,UP", "'UP' is not a heading"),
    ("S,walk=all", "per-state entries start with state="),
    ("walk=all,walk=S", "given twice"),
    ("jump=all", "names jump, not a state of the set"),
])
def test_headings_that_make_no_sense_are_refused(tmp_path, spec, error):
    root = _set(tmp_path, ("front",))
    with pytest.raises(SystemExit, match=error):
        sx.export_set(root, tmp_path / "out", headings=spec)


def test_a_handed_set_is_not_mirrored_unless_asked_and_then_it_is_recorded(tmp_path):
    root = _set(tmp_path, FIVE, handed=[WATCH])
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match=r"walk/W \(from E\).*--facing right,left.*--allow-mirror-handed"):
        sx.export_set(root, out)
    assert not out.exists()  # refused before anything is written
    index = sx.export_set(root, out, allow_mirror_handed=True)
    walk = index["states"]["walk"]["headings"]
    assert index["allow_mirror_handed"] and index["source"]["handed"] == [WATCH]
    assert all(walk[h]["handed_override"] for h in ("W", "SW", "NW"))
    assert "handed_override" not in walk["E"]
    assert any("mirrored despite --handed" in line for line in index["warnings"])


def test_a_set_filmed_both_ways_uses_every_filmed_view_and_mirrors_nothing(tmp_path):
    root = _set(tmp_path, EIGHT, facing="right,left", handed=[WATCH])  # handed: nothing to refuse, nothing mirrored
    out = tmp_path / "out"
    index = sx.export_set(root, out)
    walk = index["states"]["walk"]["headings"]
    assert index["complete"] and all(e["status"] == "filmed" for e in walk.values())
    assert walk["W"]["source_item"] == "side-left-walk" and walk["NW"]["source_item"] == "back_diagonal-left-walk"
    for heading, e in walk.items():
        src = root / e["source_item"] / "loop" / f"{e['source_item']}.strip.png"
        assert (out / "walk" / f"{heading}.png").read_bytes() == src.read_bytes()


def test_a_heading_with_nothing_to_stand_on_is_missing_and_the_export_says_so(tmp_path):
    root = _set(tmp_path, ("front", "back", "side@right"), failed=("back-walk",))
    out = tmp_path / "out"
    assert sx.run(set_dir=root, out_dir=out) == 1  # written, but not a set a loader can trust
    index = json.loads((out / "directions.json").read_text(encoding="utf-8"))
    st = index["states"]["walk"]
    assert not index["complete"] and not st["complete"]
    assert st["missing"]["N"].startswith("back-walk failed: clip generation failed")
    assert st["missing"]["SE"] == "not filmed, nor its opposite SW"
    assert {h: e["status"] for h, e in st["headings"].items() if e["status"] != "missing"} == {"S": "filmed", "E": "filmed", "W": "mirrored"}
    assert not (out / "walk" / "N.png").exists()
    assert "| walk | N | MISSING |" in (out / "table.md").read_text(encoding="utf-8")


def test_a_lost_facing_of_a_set_filmed_both_ways_is_missing_not_mirrored(tmp_path):
    root = _set(tmp_path, ("front", "side@right", "side@left"), facing="right,left", failed=("side-left-walk",))
    st = sx.export_set(root, tmp_path / "out")["states"]["walk"]
    assert st["headings"]["W"]["status"] == "missing" and st["missing"]["W"].startswith("side-left-walk failed")


def _walker(phase: float) -> Image.Image:
    a = np.zeros((120, 100, 4), dtype=np.uint8)
    bob = round(4 * (1 + math.cos(2 * phase)) / 2)
    a[20 + bob:90, 40:60] = (90, 90, 200, 255)
    fx, fy = 50 + round(20 * math.cos(phase)), 104 + round(8 * math.sin(phase))
    a[fy - 5:fy + 5, fx - 5:fx + 5] = (60, 60, 60, 255)
    return Image.fromarray(a, "RGBA")


def _cut(root: Path, name: str, length: int) -> Path:
    keyed = root / f"{name}-keyed"
    keyed.mkdir(parents=True)
    for k in range(length + 6):
        _walker(2 * math.pi * k / length).save(keyed / f"{k:04d}.png")
    loop_mod.run_loop(keyed, root / name / "loop", fps=24.0, state="walk", min_len=None, max_len=None, n_out=None,
                      seam_max=1000.0, name=name, report_path=None, cycle_mode="fixed", start=0, length=length,
                      anchor="none", repair="off")
    return root / name / "loop"


def test_the_aligned_strip_is_exported_not_the_cut(tmp_path):
    root = _set(tmp_path, ())  # an empty set folder; its loops are cut and aligned below
    dirs = [_cut(root, "front-walk", 20), _cut(root, "side-walk", 28)]
    cut = (dirs[0] / "front-walk.strip.png").read_bytes()
    align.align_set(dirs, interpolate=lambda a, b, t: Image.blend(a, b, t), between="rife", views=["front", "side@right"])
    aligned = dirs[0] / "front-walk.strip.png"
    assert aligned.read_bytes() != cut
    report = json.loads((root / "set.report.json").read_text(encoding="utf-8"))
    report["items"] = [{"item": n, "direction": d, "state": "walk", "ok": True, **t}
                       for n, d, t in (("front-walk", "front", {}), ("side-walk", "side", {"turned": "right"}))]
    report["cycle_align"] = {"walk": {"ok": True, "applied": True, "length": 24}}
    (root / "set.report.json").write_text(json.dumps(report))
    index = sx.export_set(root, tmp_path / "out")
    walk = index["states"]["walk"]["headings"]
    assert walk["S"]["aligned"] and walk["S"]["source_sha256"] == _sha(aligned)
    assert (tmp_path / "out" / "walk" / "S.png").read_bytes() == aligned.read_bytes()
    assert walk["S"]["cells"] == walk["E"]["cells"] == walk["W"]["cells"] == 24
    assert walk["S"]["fps"] == 24 and walk["S"]["previews"] == {"gif": "walk/S.gif", "webp": "walk/S.webp"}
    assert not [line for line in index["warnings"] if "cut again after" in line or "cell counts" in line]


def test_a_cut_after_the_alignment_is_named(tmp_path):
    root = _set(tmp_path, ("front", "side@right"))
    report = json.loads((root / "set.report.json").read_text(encoding="utf-8"))
    report["cycle_align"] = {"walk": {"ok": True, "applied": True}}
    (root / "set.report.json").write_text(json.dumps(report))
    index = sx.export_set(root, tmp_path / "out")
    assert any("front-walk's strip carries no alignment" in line for line in index["warnings"])


@pytest.mark.parametrize("east, warned", [(45, True), (49, False)])
def test_a_size_spread_past_three_percent_is_warned_and_nothing_is_rescaled(tmp_path, east, warned):
    root = _set(tmp_path, ("front", "side@right"),
                frames={"front-walk": _frames(height=50), "side-walk": _frames(height=east, tag=1)})
    out = tmp_path / "out"
    st = sx.export_set(root, out)
    size = st["states"]["walk"]["size"]
    s_meta = json.loads((root / "front-walk" / "loop" / "front-walk.strip.json").read_text(encoding="utf-8"))
    e_meta = json.loads((root / "side-walk" / "loop" / "side-walk.strip.json").read_text(encoding="utf-8"))
    assert size["body_h"] == {"S": s_meta["body_h"], "E": e_meta["body_h"]}
    assert size["spread"] == round(s_meta["body_h"] / e_meta["body_h"] - 1, 4)
    assert size["ok"] is not warned
    assert any("standing height differs" in line for line in st["warnings"]) is warned
    assert (out / "walk" / "E.png").read_bytes() == (root / "side-walk" / "loop" / "side-walk.strip.png").read_bytes()


def test_the_same_set_exports_the_same_bytes_and_a_narrower_export_leaves_nothing_stale(tmp_path):
    root = _set(tmp_path, FIVE, states=("walk", "run"), anchor={"side-walk": "feet"})
    a, b = tmp_path / "a", tmp_path / "b"
    sx.export_set(root, a)
    sx.export_set(root, b)
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    assert len(files) == 2 + 2 * (8 * 3)  # index, table; per state 8 strips, 8 jsons, 8 GIFs (3 of them mirrored)
    for rel in files:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel
    sx.export_set(root, a, states=["walk"])  # again into a: run/ was this export's and goes
    assert not (a / "run").exists() and (a / "walk" / "W.png").is_file()
    assert list(json.loads((a / "directions.json").read_text(encoding="utf-8"))["states"]) == ["walk"]


def test_a_folder_that_is_not_an_export_is_never_written_into(tmp_path):
    root = _set(tmp_path, ("front",))
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("mine")
    with pytest.raises(SystemExit, match="not empty and holds no directions.json"):
        sx.export_set(root, other)
    with pytest.raises(SystemExit, match="--out-dir is the set itself"):
        sx.export_set(root, root)
    with pytest.raises(SystemExit, match="--states jump not in the set"):
        sx.export_set(root, tmp_path / "out", states=["jump"])
