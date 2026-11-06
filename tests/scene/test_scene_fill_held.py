# SPDX-License-Identifier: Apache-2.0
"""A layer cut from a filmed clip is given at the scene's times (sprite_gen/scene/render.py `Renderer`, docs/scene.md
"Cells at the scene's times").

A loop strip's cells are read at their own rate (`fps`, or a `delay_ms` rounded to hundredths read as the whole rate it
rounds: 41.67 is 24 fps), so a 24 fps strip in a 24 fps scene shows each cell once. A layer whose strip names its
`source_cut` (a `video-loop` or `video-stop` cut) and would show a cell again at the scene's frames — a 24 fps cut in a
60 fps scene, a playback_rate under 1 — has every frame at its own time: the cell it falls on, or the frame made between
the cells either side, judged as `video-loop` judges a frame made for a held cell. What is held on purpose stays held
(before its start and after a one-shot's end, a run of cells its strip lists, any layer that is not a video cut); a
scene that shows no cell again renders byte for byte as before. With no RIFE the cells are given as cut and a warning
line and the report say so. Each layer's scene frames that show the cell the frame before shows are a table
(`repeats`), each run held on purpose or not.

The interpolator is the stand-in of the held-cell tests (`Between`): the synthetic walker drawn again at the time asked
for, so no binary runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.scene.model import load_scene
from sprite_gen.scene.render import Renderer
from tests.video.held_fill_inputs import N, Between, walk_at

CANVAS = 160


def strip(root: Path, name: str, cells: list[Image.Image], *, fps: float | None = 24.0, delay_ms: float | None = None,
          cut: bool = True, loop: bool = True, holds: list | None = None, hold_last: bool | None = None) -> str:
    """A loop strip and its sidecar as `video-loop` writes them: the cells side by side, `delay_ms` rounded to
    hundredths, `fps` where given, `source_cut` where `cut`."""
    w, h = cells[0].size
    sheet = Image.new("RGBA", (w * len(cells), h))
    for k, cell in enumerate(cells):
        sheet.paste(cell, (k * w, 0))
    rate = fps if fps is not None else 1000 / delay_ms
    meta = {"frames": len(cells), "w": w, "h": h, "delay_ms": delay_ms if delay_ms is not None else round(1000 / rate, 2),
            "loop": loop, "kind": "periodic" if loop else "once"}
    if fps is not None:
        meta["fps"] = fps
    if cut:
        meta["source_cut"] = {"source": "synthetic", "start": 0, "length": len(cells)}
    if holds is not None:
        meta["holds"] = holds
    if hold_last is not None:
        meta["hold_last"] = hold_last
    sheet.save(root / f"{name}.strip.png")
    (root / f"{name}.strip.json").write_text(json.dumps(meta))
    return f"{name}.strip.json"


def scene(root: Path, source: str, *, fps: float, duration: float, **layer) -> Path:
    spec = {"kind": "sprite-gen-scene", "version": 1, "canvas": {"width": CANVAS, "height": CANVAS, "background": [16, 16, 16]},
            "fps": fps, "duration": duration, "assets": {"hero": source},
            "layers": [{"id": "hero", "asset": "hero", "at": [CANVAS // 2, 150], **layer}]}
    path = root / "scene.json"
    path.write_text(json.dumps(spec))
    return path


def cycle() -> list[Image.Image]:
    return [walk_at(k) for k in range(N)]


def frames_digest(renderer: Renderer, count: int, fps: float) -> str:
    """The pictures of a scene's frames, as decoded: each frame's size and RGBA bytes."""
    digest = hashlib.sha256(f"{count}".encode())
    for i in range(count):
        image = renderer.frame(i / fps)
        digest.update(f"{image.size}".encode())
        digest.update(image.tobytes())
    return digest.hexdigest()


def row(renderer: Renderer, layer: str = "hero") -> dict:
    return next(r for r in renderer.repeats if r["layer"] == layer)


def shown(renderer: Renderer, fps: float, count: int) -> list[bytes]:
    return [renderer.frame(i / fps, only="hero").tobytes() for i in range(count)]


def plate(image: Image.Image) -> bytes:
    """`image` placed as the layer places a 120 x 140 cell at (80, 150), on a clear canvas."""
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.alpha_composite(image, (CANVAS // 2 - 60, 150 - 140))
    return canvas.tobytes()


class Refusing:
    """An interpolator that must not be called."""

    def __call__(self, a, b, t):
        raise AssertionError(f"no frame between is to be made here (t {t})")


def _no_rife(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not (Path(d) / "rife-ncnn-vulkan").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(path))
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))


# --- The cells' own rate -----------------------------------------------------------------------------------------------

def test_a_24_fps_strip_in_a_24_fps_scene_shows_each_cell_once(tmp_path):
    # The checkpoint's probe: `build_strip` writes delay_ms 41.67 for 24 fps, which drifted the scene a cell late from
    # its second frame (pixels 40, 40, 80, 120). Read at the strip's own rate it shows cells 0, 1, 2, 3.
    from sprite_gen.video.loop import build_strip
    frames = [Image.new("RGBA", (16, 16), (40 * (i + 1), 20, 30, 255)) for i in range(4)]
    sheet, meta = build_strip(frames, cycle_seconds=4 / 24)
    sheet.save(tmp_path / "probe.strip.png")
    for name, sidecar in (("with-fps", meta), ("old", {k: v for k, v in meta.items() if k != "fps"})):
        (tmp_path / "probe.strip.json").write_text(json.dumps(sidecar))
        spec = {"kind": "sprite-gen-scene", "version": 1, "canvas": {"width": 64, "height": 32, "background": [0, 0, 0]},
                "fps": 24, "duration": 4 / 24, "assets": {"probe": "probe.strip.json"},
                "layers": [{"id": "probe", "asset": "probe", "at": [32, 32]}]}
        (tmp_path / "scene.json").write_text(json.dumps(spec))
        renderer = Renderer(load_scene(tmp_path / "scene.json"))
        assert meta["delay_ms"] == 41.67
        assert [renderer.frame(i / 24).getpixel((32, 28))[0] for i in range(4)] == [40, 80, 120, 160], name


@pytest.mark.parametrize("rate", [24, 60, 15])
def test_a_frame_on_a_cell_boundary_shows_the_cell_that_starts_there(rate, tmp_path):
    # A strip at `rate` in a 20 fps scene: scene frame i falls in cell i * rate / 20, exactly. Where that is whole the
    # frame is on the boundary (0.25 s of a 24 fps strip is cell 6, not 5): the strip's rounded delay (41.67, 16.67,
    # 66.67 ms) read as it was drifted those frames a cell late.
    cells = [Image.new("RGBA", (16, 16), (4 * k + 10, 20, 30, 255)) for k in range(rate)]
    source = strip(tmp_path, "cells", cells, fps=None, delay_ms=round(1000 / rate, 2), cut=False)
    spec = {"kind": "sprite-gen-scene", "version": 1, "canvas": {"width": 64, "height": 32, "background": [0, 0, 0]},
            "fps": 20, "duration": 1.0, "assets": {"cells": source},
            "layers": [{"id": "cells", "asset": "cells", "at": [32, 32]}]}
    (tmp_path / "scene.json").write_text(json.dumps(spec))
    renderer = Renderer(load_scene(tmp_path / "scene.json"), interpolate=Refusing())
    got = [(renderer.frame(i / 20).getpixel((32, 28))[0] - 10) // 4 for i in range(20)]
    assert got == [i * rate // 20 for i in range(20)]
    boundary = [i for i in range(20) if i * rate % 20 == 0]
    assert len(boundary) >= 4 and all(got[i] == i * rate // 20 for i in boundary)
    if rate == 24:
        assert got[5] == 6  # 0.25 s x 24


def test_a_delay_rounded_to_hundredths_is_read_as_the_whole_rate_it_rounds_and_nothing_else_moves():
    from sprite_gen.spec.assets import frame_seconds
    assert frame_seconds(41.67) == 1 / 24 and frame_seconds(16.67) == 1 / 60 and frame_seconds(33.33) == 1 / 30
    assert frame_seconds(66.67) == 1 / 15 and frame_seconds(83.33) == 1 / 12
    # A whole number of milliseconds is what its writer meant (a pixel drawing's 100 ms, an atlas's 42 ms), and a rate
    # that is not whole (23.976 fps: 41.71 ms) is not taken for one.
    assert frame_seconds(100) == 0.1 and frame_seconds(42) == 0.042 and frame_seconds(7) == 0.007
    assert frame_seconds(41.71) == 41.71 / 1000 and frame_seconds(16.68) == 16.68 / 1000


def test_a_stride_report_measured_on_the_rounded_delay_names_the_cells_read_at_their_exact_rate(tmp_path):
    from sprite_gen.spec.assets import load_asset, same_identity, sequence_identity
    source = strip(tmp_path, "walk", cycle(), fps=None, delay_ms=41.67)
    seq = load_asset(tmp_path / source)
    old = {**sequence_identity(seq), "durations_seconds": [0.04167] * N}
    assert seq.durations == (1 / 24,) * N
    assert same_identity(old, seq)
    assert not same_identity({**old, "durations_seconds": [0.0417] * N}, seq)


def test_a_24_fps_cut_in_a_20_fps_scene_shows_each_cell_at_its_own_time(tmp_path):
    # No cell is shown twice either way; read off 41.67 ms, every fifth frame (a time on a cell's start) showed the
    # cell before it: cells 4, 5, 7 where they are 4, 6, 7. The exact rate changes those frames' pictures.
    source = strip(tmp_path, "walk", cycle())
    renderer = Renderer(load_scene(scene(tmp_path, source, fps=20, duration=1.0)), interpolate=Refusing())
    picked = [renderer.scene.layers[0].sequence.index_at(i / 20) for i in range(20)]
    assert picked == [math.floor(i * 1.2 + 1e-9) for i in range(20)] and picked[4:7] == [4, 6, 7]
    assert row(renderer)["repeated_frames"] == 0 and row(renderer)["fill_held"] == {
        "mode": "auto", "applied": False, "made": 0, "shown_again": []}


# --- A scene that shows no cell again is as before ---------------------------------------------------------------------

# Pinned on the engine before the cells were given at the scene's times (the frames as decoded, `frames_digest`).
UNREPEATED = {
    "25-in-25": (dict(fps=25.0), dict(fps=25, duration=24 / 25),
                 "1bf5022e2d337b3b07d23a6ef0ef0edcab8928f8dde7177ab328fb50941a5868"),
    "20-in-20-old-sidecar": (dict(fps=None, delay_ms=50.0), dict(fps=20, duration=24 / 20),
                             "1bf5022e2d337b3b07d23a6ef0ef0edcab8928f8dde7177ab328fb50941a5868"),
    "25-in-20": (dict(fps=25.0), dict(fps=20, duration=1.0),
                 "33bc07fc36d8e80a537b3d80f2aea54dd0a838964820922aa0b5b1d31056ca79"),
    # Rates whose rounded delay is under the exact one: read as it was, no frame drifted.
    "30-in-30": (dict(fps=30.0), dict(fps=30, duration=24 / 30),
                 "1bf5022e2d337b3b07d23a6ef0ef0edcab8928f8dde7177ab328fb50941a5868"),
    "12-in-12": (dict(fps=12.0), dict(fps=12, duration=24 / 12),
                 "1bf5022e2d337b3b07d23a6ef0ef0edcab8928f8dde7177ab328fb50941a5868"),
    "48-in-48": (dict(fps=48.0), dict(fps=48, duration=24 / 48),
                 "1bf5022e2d337b3b07d23a6ef0ef0edcab8928f8dde7177ab328fb50941a5868"),
}


@pytest.mark.parametrize("case", sorted(UNREPEATED))
def test_a_scene_that_shows_no_cell_again_renders_byte_for_byte_as_before(case, tmp_path):
    asset, timing, pinned = UNREPEATED[case]
    source = strip(tmp_path, "walk", cycle(), **asset)
    renderer = Renderer(load_scene(scene(tmp_path, source, **timing)), interpolate=Refusing())
    count = round(timing["fps"] * timing["duration"])
    assert row(renderer)["repeated_frames"] == 0 and renderer.planned == {}
    assert frames_digest(renderer, count, timing["fps"]) == pinned


# --- Cells at the scene's times ----------------------------------------------------------------------------------------

def test_a_24_fps_cut_in_a_60_fps_scene_shows_no_cell_twice(tmp_path):
    source = strip(tmp_path, "walk", cycle())
    spec = scene(tmp_path, source, fps=60, duration=1.0)
    # As cut (`--fill-held off`, the engine before it): 36 of the 60 frames show the cell the frame before shows.
    off = Renderer(load_scene(spec), fill_held="off")
    assert row(off)["unintended_frames"] == 36 and row(off)["fill_held"]["why"] == "--fill-held off"
    assert all(r["intended"] is False for r in row(off)["runs"])
    assert any("--fill-held off" in line for line in off.warnings)
    between = Between(walk_at, range(N), period=N)
    on = Renderer(load_scene(spec), interpolate=between)
    record = row(on)
    assert record["repeated_frames"] == 0 and record["unintended_frames"] == 0 and record["runs"] == []
    # 12 of the 60 times fall on a cell (every fifth: 0, 2, 4, ... cells); the 48 others are made at their own time.
    assert record["fill_held"]["made"] == 48 and record["fill_held"]["shown_again"] == []
    assert on.warnings == []
    pictures = shown(on, 60, 60)
    assert all(a != b for a, b in zip(pictures, pictures[1:]))
    for i in (0, 1, 2, 7, 59):
        assert pictures[i] == plate(walk_at(i * 0.4)), i
    # Each fraction is made once and kept: a loop's times come round again (the asset and the time).
    assert len(between.calls) == len({c for c in between.calls})


class Named:
    """An interpolator with an identity, as RIFE has one: what a frame kept by `--between-cache` is kept under."""

    def __init__(self, inner, name="stand-in"):
        self.inner, self.name, self.calls = inner, name, 0

    def identity(self):
        return self.name

    def __call__(self, a, b, t):
        self.calls += 1
        return self.inner(a, b, t)


def test_a_between_cache_keeps_the_frames_made_for_the_next_render(tmp_path):
    source = strip(tmp_path, "walk", cycle())
    spec = scene(tmp_path, source, fps=60, duration=1.0)
    cache = tmp_path / "between"
    first = Named(Between(walk_at, range(N), period=N))
    made = Renderer(load_scene(spec), interpolate=first, between_cache=cache)
    assert made.between_cache_record() == {"dir": str(cache.resolve()), "hits": 0, "misses": 48, "unreadable": 0, "not_kept": 0}
    # The next render takes every frame from the cache (the two cells' bytes, the time, the RIFE): none is made.
    taken = Renderer(load_scene(spec), interpolate=Named(Refusing()), between_cache=cache)
    assert taken.between_cache_record()["hits"] == 48 and taken.between_cache_record()["misses"] == 0
    assert shown(taken, 60, 60) == shown(made, 60, 60)
    # Another RIFE is another key; an interpolator naming none is refused, as nothing could say what made a frame.
    other = Named(Between(walk_at, range(N), period=N), name="another")
    assert Renderer(load_scene(spec), interpolate=other, between_cache=cache).between_cache_record()["misses"] == 48
    with pytest.raises(ValueError, match="names no identity"):
        Renderer(load_scene(spec), interpolate=Between(walk_at, range(N), period=N), between_cache=cache)
    # Without it, nothing is kept and the report says nothing of it.
    assert Renderer(load_scene(spec), interpolate=Between(walk_at, range(N), period=N)).between_cache_record() is None


def test_a_scene_at_60_fps_closes_on_its_first_frame_as_the_loop_check_reads_it(tmp_path):
    from sprite_gen.scene.inspect_scene import inspect_scene
    source = strip(tmp_path, "walk", cycle())
    loaded = load_scene(scene(tmp_path, source, fps=60, duration=1.0, offset=0.01))
    report = inspect_scene(loaded, renderer=Renderer(loaded, interpolate=Between(walk_at, range(N), period=N)))
    assert report["loop"]["phase_closure_pass"] and report["loop"]["phase_closure_mae"] == 0
    assert report["repeats"][0]["unintended_frames"] == 0


def test_a_playback_rate_under_1_gives_the_cut_its_frames_between(tmp_path):
    source = strip(tmp_path, "walk", cycle())
    spec = scene(tmp_path, source, fps=24, duration=1.0, playback_rate=0.5)
    assert row(Renderer(load_scene(spec), fill_held="off"))["unintended_frames"] == 12
    on = Renderer(load_scene(spec), interpolate=Between(walk_at, range(N), period=N))
    assert row(on)["repeated_frames"] == 0 and row(on)["fill_held"]["made"] == 12
    assert shown(on, 24, 24)[1] == plate(walk_at(0.5))


def test_a_once_cut_holds_its_first_cell_before_it_starts_and_its_last_after_it_ends(tmp_path):
    cells = [walk_at(k) for k in range(8)]
    source = strip(tmp_path, "act", cells, loop=False, hold_last=True)
    # Starts 0.1 s in, plays 8 cells at 24 fps over 0.33 s, holds: 0.6 s at 60 fps.
    spec = scene(tmp_path, source, fps=60, duration=0.6, offset=-0.1)
    between = Between(walk_at, range(8))
    on = Renderer(load_scene(spec), interpolate=between)
    record = row(on)
    assert record["unintended_frames"] == 0
    whys = {r["why"] for r in record["runs"]}
    assert whys == {"before its start: its first cell held", "the one-shot's end: its last cell held (hold_last)"}
    assert all(r["intended"] for r in record["runs"])
    first, last = record["runs"][0], record["runs"][-1]
    assert (first["from"], first["cell"]) == (0, 0) and (last["to"], last["cell"]) == (35, 7)
    # Between its start and its last cell, each frame at its own time: no frame is made from the last cell onward.
    assert all(ta < 7 and tb <= 7 for ta, tb, _ in between.calls)
    assert record["fill_held"]["made"] > 0


def test_a_run_of_cells_its_strip_lists_as_held_stays_held(tmp_path):
    # Cells 6 to 9 show cell 6's picture: a hold the motion means (four cells), listed by the strip.
    cells = [walk_at(k) for k in range(N)]
    cells[7:10] = [cells[6]] * 3
    holds = [{"from": 6, "to": 9, "kind": "motion-hold", "reason": "held 4 cells, longer than a drawing on threes"}]
    source = strip(tmp_path, "walk", cells, holds=holds)
    between = Between(walk_at, range(N), period=N)
    on = Renderer(load_scene(scene(tmp_path, source, fps=60, duration=1.0)), interpolate=between)
    record = row(on)
    assert record["unintended_frames"] == 0
    listed = [r for r in record["runs"] if r["intended"]]
    assert listed and all(r["why"] == "a hold its strip lists (cells 6-9, motion-hold)" for r in listed)
    # Inside the hold nothing is made; from its last cell (cell 9, cell 6's picture) to the next pose the frames are.
    assert {(ta, tb) for ta, tb, _ in between.calls if 6 <= ta < 10} == {(6.0, 10.0)}


def test_a_strip_that_names_no_cut_is_drawings_held_as_drawn(tmp_path):
    source = strip(tmp_path, "walk", cycle(), cut=False)
    spec = scene(tmp_path, source, fps=60, duration=1.0)
    on = Renderer(load_scene(spec), interpolate=Refusing())
    record = row(on)
    assert record["video_cut"] is False and "fill_held" not in record and record["unintended_frames"] == 0
    assert {r["why"] for r in record["runs"]} == {"not a video cut: its cells are drawings, each held as drawn"}
    assert shown(on, 60, 60) == shown(Renderer(load_scene(spec), fill_held="off"), 60, 60)


def test_a_still_is_one_cell_held_as_drawn(tmp_path):
    walk_at(0).save(tmp_path / "still.png")
    renderer = Renderer(load_scene(scene(tmp_path, "still.png", fps=10, duration=1.0)), interpolate=Refusing())
    assert row(renderer)["runs"] == [{"from": 0, "to": 9, "cell": 0, "intended": True,
                                      "why": "not a video cut: its cells are drawings, each held as drawn"}]


# --- What was not made is said -----------------------------------------------------------------------------------------

def test_no_rife_gives_the_cells_as_cut_and_says_which_frames_and_how_to_install(tmp_path, monkeypatch, capsys):
    from sprite_gen.scene.render import render_scene
    from sprite_gen.video import rife as rife_mod
    _no_rife(monkeypatch, tmp_path)
    source = strip(tmp_path, "walk", cycle())
    spec = scene(tmp_path, source, fps=60, duration=1.0)
    report = render_scene(spec, tmp_path / "out", formats="png")
    record = report["inspection"]["repeats"][0]
    assert record["unintended_frames"] == 36
    assert record["fill_held"]["why"] == "RIFE is not installed"
    assert record["fill_held"]["unmade"]["install"] == rife_mod.install_command()
    assert all(r["why"].endswith("RIFE is not installed, given as cut") for r in record["runs"])
    err = capsys.readouterr().err
    assert "scene-render: warning: layer hero: 36 scene frames show the cell before them again (1, 2, 4," in err
    assert f"RIFE is not installed, so no frame between was made; run `{rife_mod.install_command()}`" in err
    assert report["inspection"]["status"] == "needs-review"
    # The frames are the cells as cut.
    off = Renderer(load_scene(spec), fill_held="off")
    written = [Image.open(p).tobytes() for p in sorted((tmp_path / "out" / "frames").glob("frame-*.png"))]
    assert written == [off.frame(i / 60).tobytes() for i in range(60)]


def test_a_frame_between_refused_shows_the_nearer_cell_again_named(tmp_path):
    source = strip(tmp_path, "walk", cycle())
    # An interpolator that gives the cell before back: each frame between shows a pose beside it (`held`), refused.
    on = Renderer(load_scene(scene(tmp_path, source, fps=60, duration=0.2)), interpolate=lambda a, b, t: a.copy())
    record = row(on)
    assert record["fill_held"]["made"] == 0 and record["fill_held"]["shown_again"]
    assert record["unintended_frames"] > 0
    assert all(r["intended"] is False and r["why"].startswith("no frame between cells") for r in record["runs"])
    assert any("show the cell beside them again: no frame between was made there without a fault" in line
               for line in on.warnings)


def test_a_rife_that_fails_stops_the_render_naming_the_way_to_give_the_cells_as_cut(tmp_path):
    from sprite_gen.scene import render
    from sprite_gen.video import rife as rife_mod

    def broken(a, b, t):
        raise rife_mod.RifeUnavailable("rife-ncnn-vulkan exited 1")

    source = strip(tmp_path, "walk", cycle())
    spec = scene(tmp_path, source, fps=60, duration=0.2)
    with pytest.raises(ValueError, match="layer hero: rife-ncnn-vulkan exited 1; .*pass --fill-held off"):
        render.render_scene(spec, tmp_path / "out", formats="png", interpolate=broken)
    assert not (tmp_path / "out").exists()
    with pytest.raises(SystemExit, match="scene-render: layer hero"):
        render.run(spec=spec, out_dir=tmp_path / "out", formats="png", interpolate=broken)


def test_scene_inspect_reports_each_layer_s_repeats_and_takes_the_same_flag(tmp_path, capsys):
    from sprite_gen.scene import inspect_scene
    source = strip(tmp_path, "walk", cycle())
    spec = scene(tmp_path, source, fps=60, duration=1.0)
    assert inspect_scene.run(spec, fill_held="off") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["repeats"][0]["unintended_frames"] == 36 and report["status"] == "needs-review"
    assert any(line.startswith("layer hero: 36 scene frames show the cell before them again") for line in report["warnings"])
