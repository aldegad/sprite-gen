# SPDX-License-Identifier: Apache-2.0
"""Deterministic asset placement. No generation calls or sprite source writes.

Every prerequisite (formats, codec constraints, GIF settings, ffmpeg, output
directory) is checked before anything is created; frames and encodes are staged
in a private temporary directory and published as one atomic set. A failure
leaves no partial output behind and is never retried automatically.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

from sprite_gen.scene.model import Scene, load_scene
from sprite_gen.spec.assets import finite
from sprite_gen.spec.runio import (LOCK_FILENAME, acquire_run_dir_lock, atomic_write_set, inside_folder, relative_posix,
                                   release_run_dir_lock, same_folder)
from sprite_gen.util.resample import resize_cell
from sprite_gen.util.console import utf8_streams


FORMATS = ("png", "mp4", "gif")
# GIF defaults: the scene's own frame rate, a full 256-colour palette and no
# downscale unless the canvas is wider than 640 px. Nothing is reduced silently.
GIF_MAX_WIDTH_DEFAULT = 640
GIF_COLORS_DEFAULT = 256
GIF_DITHER_CHOICES = ("none", "bayer", "floyd_steinberg", "sierra2_4a")
GIF_DITHER_DEFAULT = "none"
GIF_MAX_BYTES_DEFAULT = 8_000_000


def png_bytes(image):
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def composite(target, source, x, y):
    x, y = round(x), round(y)
    left, top = max(0, x), max(0, y)
    right, bottom = min(target.width, x+source.width), min(target.height, y+source.height)
    if left < right and top < bottom:
        target.alpha_composite(source.crop((left-x, top-y, right-x, bottom-y)), (left, top))


# `--fill-held`: auto gives a layer cut from a filmed clip at the scene's own times (`Renderer`), off gives its cells as
# the asset's timing picks them — the word `video-loop --fill-held` uses.
FILL_HELD_MODES = ("auto", "off")
DOC = 'docs/scene.md "Cells at the scene\'s times"'


def _frames_said(frames, most=12):
    """Scene frame numbers, as a person is told them: the first `most`, then how many more."""
    shown = ", ".join(map(str, frames[:most]))
    return shown + (f" and {len(frames) - most} more" if len(frames) > most else "")


class _Between:
    """The frames made between two cells of a layer for one render, each kept by the two cells and its fraction (the
    asset's cells and the time): RIFE is located the first time a frame is asked for, so a render that makes none
    needs no binary. With `cache` (`rife.Cache`, `--between-cache`), a frame is also kept across renders under the bytes
    of its two cells, the time and the RIFE that made it, and taken from there when asked for again."""

    def __init__(self, interpolate=None, cache=None):
        self.interpolate = interpolate
        self.made_by = None if interpolate is None else {"kind": "injected"}
        self.cache = cache
        self.kept = {}

    def __call__(self, a, b, t):
        key = (id(a), id(b), float(t).hex())
        if key not in self.kept:
            if self.interpolate is None:
                from sprite_gen.video import rife as rife_mod
                found = rife_mod.Rife()
                self.made_by = found.describe()
                self.interpolate = found
            if self.cache is not None:
                self.kept[key] = self.cache.frame(self.interpolate, a.convert("RGBA"), b.convert("RGBA"), t)
            else:
                self.kept[key] = self.interpolate(a, b, t)
        return self.kept[key]


class Renderer:
    """Each layer's frames placed at a scene time (`frame`).

    A layer cut from a filmed clip (a `video-loop` or `video-stop` strip: its sidecar names `source_cut`) is read for
    the cells it shows at the scene's frames, by which cell each frame shows. Where a scene frame shows the cell the
    frame before it shows, and nothing holds that cell on purpose — before the layer's start or after a one-shot's end
    (where it holds its first or last cell), within a run of cells its strip lists as held (`holds`) — the layer is
    given at the scene's times (`fill_held` auto): every frame of it the cell its time falls on, or the frame RIFE makes
    between the two cells either side at its own time, judged as `video-loop` judges a frame made for a held cell
    (`held_fill.at_times`); one refused shows the nearer cell again, named. A layer that shows no cell again keeps its
    cells as picked, byte for byte. Any other layer (a still, an atlas row, a pixel drawing) holds its cells as drawn.
    No RIFE installed: the layer keeps its cells and `warnings` says so. `repeats` is each layer's table of scene frames
    showing the cell the frame before shows, each run held on purpose (`intended`, and why) or not. `between_cache` is a
    directory the frames made are kept in and taken from (`rife.Cache`: under the bytes of the two cells, the time and
    the RIFE), counted in `between_cache_record()`; nothing is kept without it."""

    def __init__(self, scene: Scene, *, fill_held="auto", interpolate=None, between_cache=None):
        if fill_held not in FILL_HELD_MODES:
            raise ValueError(f"fill_held must be one of {', '.join(FILL_HELD_MODES)}, not {fill_held!r}")
        cache = None
        if between_cache is not None:
            from sprite_gen.video import rife as rife_mod
            if interpolate is not None and not callable(getattr(interpolate, "identity", None)):
                raise ValueError("--between-cache keeps frames under the RIFE that made them: the interpolator given names "
                                 "no identity")
            try:
                cache = rife_mod.Cache(Path(between_cache))
            except OSError as exc:
                raise ValueError(f"--between-cache {between_cache}: {exc}") from exc
        self.cache = cache
        self.scene = scene
        self.fill_held = fill_held
        self.prepared = {}
        self.planned = {}
        self.repeats = []
        self.warnings = []
        # Import only when scene/shadow is used; ordinary sprite commands need no scene setup.
        from sprite_gen.effects.shadow import project_shadow
        self._project_shadow = project_shadow
        for layer in scene.layers:
            self.prepared[layer.id] = {id(source): self._prepare(layer, source) for source in layer.sequence.frames}
        between = _Between(interpolate, cache)
        for layer in scene.layers:
            self._plan(layer, between)

    def between_cache_record(self):
        """The `--between-cache` directory and its counts (`rife.Cache.record`), or None without one."""
        return self.cache.record() if self.cache is not None else None

    def _prepare(self, layer, source):
        seq = layer.sequence
        size = tuple(layer.raster_size)
        anchor = tuple(a*new/old for a, new, old in zip(seq.anchor, size, seq.size))
        image = resize_cell(source, size)
        if layer.repeat_x:
            image = image.crop((0, 0, round(layer.period*layer.scale), size[1]))
        if layer.opacity != 1:
            image.putalpha(image.getchannel("A").point(lambda a: round(a*layer.opacity)))
        shadow = self._project_shadow(image, anchor, **self.scene.light) if layer.shadow else None
        return (image, anchor, shadow, image.getchannel("A").getbbox())

    def _plan(self, layer, between):
        """Read which cell `layer` shows at each scene frame (and at the scene's end, which the loop check reads), give
        it at the scene's times where it shows a cell again and nothing holds it (see the class), and write its row of
        `repeats`."""
        scene, seq = self.scene, layer.sequence
        n, count = len(seq.frames), scene.frame_count
        asset = [i / scene.fps * layer.playback_rate + layer.offset for i in range(count + 1)]
        cells = [seq.index_at(a, loop=layer.loop) for a in asset]
        video = bool(seq.metadata.get("video_cut"))
        even = len(set(seq.durations)) == 1
        holds = seq.metadata.get("holds") or []
        hold_last = bool(seq.metadata.get("hold_last"))

        def held(i):
            """Why the cell frame `i` shows is held on purpose, or None."""
            if not video:
                return "not a video cut: its cells are drawings, each held as drawn"
            if n < 2:
                return "a single cell"
            if not even:
                return "its cells are not one length each: held as its timing says"
            if not layer.loop and asset[i] < 0:
                return "before its start: its first cell held"
            if not layer.loop and cells[i] == n - 1:
                return "the one-shot's end: its last cell held" + (" (hold_last)" if hold_last else "")
            for h in holds:
                if h["from"] <= cells[i] < h["to"]:
                    return f"a hold its strip lists (cells {h['from']}-{h['to']}{', ' + h['kind'] if h.get('kind') else ''})"
            return None

        why_held = [held(i) for i in range(count + 1)]
        # A frame that shows the cell a frame held on purpose shows (the first cell, on at its start) goes on holding it.
        kept = [None] + [why_held[i] or why_held[i - 1] for i in range(1, count + 1)]
        again = [i for i in range(1, count) if cells[i] == cells[i - 1] and kept[i] is None]
        identity = [("cell", c) for c in cells]
        unintended = {}
        record = {"mode": self.fill_held, "applied": False, "made": 0, "shown_again": []} if video else None
        if again:
            cell_fps = 1 / seq.durations[0]
            cause = (f"the scene's frames come faster than the cells ({cell_fps:g} cells a second at playback "
                     f"{layer.playback_rate:g} in a {scene.fps:g} fps scene)")
            if self.fill_held == "off":
                unintended.update({i: f"{cause}: --fill-held off, given as cut" for i in again})
                record["why"] = "--fill-held off"
                self.warnings.append(f"layer {layer.id}: {len(again)} scene frame{'s' if len(again) > 1 else ''} show the cell "
                                     f"before them again ({_frames_said(again)}) — --fill-held off, given as cut ({DOC})")
            else:
                self._fill(layer, between, asset, cells, why_held, kept, identity, unintended, record, again, cause)
        repeated = [i for i in range(1, count) if identity[i] == identity[i - 1]]
        runs = []
        for i in repeated:
            why = unintended.get(i) or kept[i] or "shown again"
            intended = i not in unintended and kept[i] is not None
            shown = identity[i][1] if identity[i][0] == "cell" else {"between": list(identity[i][1]), "t": identity[i][2]}
            last = runs[-1] if runs else None
            if last and last["to"] == i - 1 and last["why"] == why and last["cell"] == shown:
                last["to"] = i
            else:
                runs.append({"from": i - 1, "to": i, "cell": shown, "intended": intended, "why": why})
        row = {"layer": layer.id, "asset": layer.asset, "video_cut": video, "cells": n, "frames": count,
               "repeated_frames": len(repeated), "unintended_frames": sum(1 for i in repeated if i in unintended or kept[i] is None),
               "runs": runs}
        if record is not None:
            row["fill_held"] = record
        self.repeats.append(row)

    def _fill(self, layer, between, asset, cells, why_held, kept, identity, unintended, record, again, cause):
        """The layer at the scene's times (`held_fill.at_times`): each frame not held on purpose, the cell its time
        falls on or the frame made between the cells either side; what became of it in `record`."""
        from sprite_gen.video import held_fill as held_fill_mod
        from sprite_gen.video import rife as rife_mod
        seq = layer.sequence
        n, d = len(seq.frames), seq.durations[0]
        playing = [i for i in range(len(asset)) if why_held[i] is None]
        at = [((a % seq.duration) if layer.loop else a) / d for a in (asset[i] for i in playing)]
        spacing = min(1.0, layer.playback_rate / (self.scene.fps * d))
        try:
            out, made = held_fill_mod.at_times(list(seq.frames), between, at, ring=layer.loop, spacing=spacing)
        except rife_mod.RifeNotInstalled as exc:
            unintended.update({i: f"{cause}: RIFE is not installed, given as cut" for i in again})
            record.update(why="RIFE is not installed", unmade={"frames": again, "rife": str(exc), "install": rife_mod.install_command()})
            self.warnings.append(f"layer {layer.id}: {len(again)} scene frame{'s' if len(again) > 1 else ''} show the cell before "
                                 f"them again ({_frames_said(again)}) — RIFE is not installed, so no frame between was made; "
                                 f"run `{rife_mod.install_command()}` and render again ({DOC})")
            return
        except (ValueError, rife_mod.RifeUnavailable) as exc:
            raise ValueError(f"layer {layer.id}: {exc}; the layer shows a cell again and the frames between are to be made — "
                             f"fix RIFE, or pass --fill-held off to give its cells as cut ({DOC})") from exc
        plan = {}
        hows = {how["cell"]: how for how in made["frames"]}
        refused = {playing[k]: how["between"] for k, how in hows.items() if "taken" in how}
        for k, (i, frame, source) in enumerate(zip(playing, out, made["source_frames"])):
            if source is None:
                how = hows[k]
                identity[i] = ("made", tuple(how["between"]), how["t"])
                plan[i] = self._prepare(layer, frame)
            else:
                identity[i] = ("cell", source)
                if source != cells[i]:
                    plan[i] = self.prepared[layer.id][id(seq.frames[source])]
        for i in range(1, len(asset) - 1):
            if identity[i] == identity[i - 1] and kept[i] is None:
                a = identity[i][1]
                pair = refused.get(i) or refused.get(i - 1)
                unintended[i] = (f"no frame between cells {pair[0]} and {pair[1]} was made without a fault: the nearer cell "
                                 "shown again" if pair else
                                 f"cells {a} and {(a + 1) % n} show one picture and its strip lists no hold there")
        self.planned[layer.id] = plan
        shown_again = sorted(i for i in refused if i < len(asset) - 1)  # scene frames: the scene's end is the loop check's
        record.update(applied=True, made=made["made"], shown_again=shown_again,
                      interpolator=between.made_by if made["made"] or shown_again else None)
        if shown_again:
            self.warnings.append(f"layer {layer.id}: {len(shown_again)} scene frame{'s' if len(shown_again) > 1 else ''} "
                                 f"({_frames_said(shown_again)}) show the cell beside them again: no frame between was made "
                                 f"there without a fault ({DOC})")

    def cell_at(self, time):
        """The scene frame `time` falls on, where the plan reads it (a frame of the scene, or its end), else None."""
        index = time * self.scene.fps
        i = round(index)
        return i if abs(index - i) < 1e-6 else None

    def frame(self, time: float, *, only=None, geometry=False):
        """Composite the scene at ``time``; with ``geometry`` also return every placement.

        Placements carry the unclipped bounding box of each placed copy in canvas
        coordinates (``None`` for a fully transparent source frame) and whether
        any of it lies inside the viewport, so a layer that is entirely off
        screen is reported rather than silently dropped.
        """
        scene = self.scene
        frame = Image.new("RGBA", (scene.width, scene.height), (0, 0, 0, 0) if only else scene.background)
        placements = []
        for layer in scene.layers:
            if only and layer.id != only:
                continue
            planned = self.planned.get(layer.id)
            at = self.cell_at(time) if planned else None
            if at is not None and at in planned:
                image, anchor, shadow, bbox = planned[at]
            else:
                source = layer.sequence.frame_at(time*layer.playback_rate+layer.offset, loop=layer.loop)
                image, anchor, shadow, bbox = self.prepared[layer.id][id(source)]
            point = [layer.at[i] + layer.velocity[i]*time - (scene.camera_at[i]+scene.camera_velocity[i]*time)*layer.parallax for i in range(2)]
            left, top = point[0]-anchor[0], point[1]-anchor[1]
            offsets = [0]
            if layer.repeat_x:
                period = image.width
                left %= period
                offsets = range(-period, scene.width+period, period)
            for dx in offsets:
                if shadow:
                    sh, sh_anchor = shadow
                    composite(frame, sh, left+dx+anchor[0]-sh_anchor[0], top+anchor[1]-sh_anchor[1])
                composite(frame, image, left+dx, top)
                if geometry:
                    entry = {"id": layer.id, "repeat_x": layer.repeat_x, "bbox": None, "visible": False}
                    if bbox:
                        box = [left+dx+bbox[0], top+bbox[1], left+dx+bbox[2], top+bbox[3]]
                        entry["bbox"] = box
                        entry["visible"] = box[2] > 0 and box[0] < scene.width and box[3] > 0 and box[1] < scene.height
                    placements.append(entry)
        return (frame, placements) if geometry else frame


def _ffmpeg_binary():
    binary = shutil.which("ffmpeg")
    if not binary:
        raise ValueError("ffmpeg is required for scene video export")
    return binary


def _ffmpeg(args):
    process = subprocess.run([_ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if process.returncode:
        raise ValueError(f"ffmpeg failed: {process.stderr[-3000:]}")


def _bounded_integer(value, name, low, high):
    number = finite(value, name)
    if number != int(number) or not low <= number <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return int(number)


def parse_formats(formats):
    chosen = {f.strip() for f in formats.split(",")} if isinstance(formats, str) else set(formats)
    if not chosen or chosen - set(FORMATS):
        raise ValueError("formats must be a comma-separated selection of png,mp4,gif")
    return chosen


def gif_settings(scene: Scene, *, gif_width=None, gif_fps=None, gif_colors=GIF_COLORS_DEFAULT,
                 gif_dither=GIF_DITHER_DEFAULT, gif_max_bytes=GIF_MAX_BYTES_DEFAULT):
    """Validate the explicit GIF choices. Every value is used as given; none is lowered later."""
    if scene.background[3] != 255:
        raise ValueError("scene GIF preview needs opaque background; export transparent layers as PNG")
    width = min(GIF_MAX_WIDTH_DEFAULT, scene.width) if gif_width is None else _bounded_integer(gif_width, "gif_width", 1, scene.width)
    fps = scene.fps if gif_fps is None else finite(gif_fps, "gif_fps", positive=True)
    if fps > scene.fps:
        raise ValueError("gif_fps cannot exceed the scene fps; frames are never invented")
    colors = _bounded_integer(gif_colors, "gif_colors", 2, 256)
    if gif_dither not in GIF_DITHER_CHOICES:
        raise ValueError(f"gif_dither must be one of {', '.join(GIF_DITHER_CHOICES)}")
    max_bytes = None if gif_max_bytes is None else int(finite(gif_max_bytes, "gif_max_bytes", positive=True))
    return {"width": width, "fps": fps, "colors": colors, "dither": gif_dither, "max_bytes": max_bytes}


def encoding_settings(scene: Scene, formats: set, **gif) -> dict:
    """Everything the encoders need, checked before any output path exists."""
    settings = {}
    if "mp4" in formats:
        if scene.width % 2 or scene.height % 2 or scene.background[3] != 255:
            raise ValueError("MP4 requires even canvas dimensions and an opaque background")
        settings["mp4"] = {"codec": "libx264", "crf": 18, "preset": "fast", "pix_fmt": "yuv420p"}
    if "gif" in formats:
        settings["gif"] = gif_settings(scene, **gif)
    if settings:
        settings["ffmpeg"] = _ffmpeg_binary()
    return settings


def encode_frames(work: Path, scene: Scene, formats: set, settings: dict):
    """Encode staged frames once each, before publishing anything. No automatic retries."""
    outputs, report = {}, {}
    pattern = str(work / "frame-%05d.png")
    if "mp4" in formats:
        mp4 = settings["mp4"]
        dest = work / "scene.mp4"
        _ffmpeg(["-framerate", str(scene.fps), "-i", pattern, "-frames:v", str(scene.frame_count), "-c:v", mp4["codec"],
                 "-preset", mp4["preset"], "-crf", str(mp4["crf"]), "-pix_fmt", mp4["pix_fmt"], "-movflags", "+faststart", "-an", str(dest)])
        outputs["scene.mp4"] = dest.read_bytes()
        report["mp4"] = {**mp4, "fps": scene.fps, "bytes": len(outputs["scene.mp4"])}
    if "gif" in formats:
        gif = settings["gif"]
        filters = []
        if gif["fps"] != scene.fps:
            filters.append(f"fps={gif['fps']}")
        if gif["width"] != scene.width:
            # LANCZOS stays here: the frames are opaque composites, so nothing is divided by a
            # low coverage (the ring `resize_cell` removes from keyed layers cannot form).
            filters.append(f"scale={gif['width']}:-1:flags=lanczos")
        chain = ",".join(filters)
        pal, dest = work / "palette.png", work / "scene.gif"
        # The background is opaque, so the whole palette is spent on colours.
        palettegen = f"palettegen=max_colors={gif['colors']}:reserve_transparent=0"
        paletteuse = f"paletteuse=dither={gif['dither']}"
        _ffmpeg(["-framerate", str(scene.fps), "-i", pattern, "-vf", f"{chain},{palettegen}" if chain else palettegen, "-frames:v", "1", str(pal)])
        _ffmpeg(["-framerate", str(scene.fps), "-i", pattern, "-i", str(pal), "-lavfi",
                 f"{chain}[x];[x][1:v]{paletteuse}" if chain else f"[0:v][1:v]{paletteuse}", "-loop", "0", str(dest)])
        data = dest.read_bytes()
        with Image.open(dest) as encoded:
            frames, size = getattr(encoded, "n_frames", 1), encoded.size
        report["gif"] = {"width": size[0], "height": size[1], "fps": gif["fps"], "colors": gif["colors"], "dither": gif["dither"],
                         "frames": frames, "bytes": len(data), "max_bytes": gif["max_bytes"]}
        if gif["max_bytes"] is not None and len(data) > gif["max_bytes"]:
            raise ValueError(f"scene.gif is {len(data)} bytes, over the {gif['max_bytes']} byte budget; "
                             "lower gif_width, gif_fps or gif_colors, or raise gif_max_bytes (quality is never reduced automatically)")
        outputs["scene.gif"] = data
    return outputs, report


def _validate_output(output: Path, scene: Scene):
    # A scene output directory is dedicated; never overwrite source assets or scene specs.
    if any(same_folder(p, output) or inside_folder(p, output) for p in map(Path, scene.source_fingerprints)):
        raise ValueError("output directory contains scene inputs; choose a separate output directory")
    if output.exists() and not output.is_dir():
        raise ValueError("scene output must be a directory")
    if output.exists() and any(p.name != LOCK_FILENAME for p in output.iterdir()):
        raise ValueError("scene output directory must be empty; choose a new render directory")


def render_scene(spec, out_dir, *, formats="mp4,gif", gif_max_bytes=GIF_MAX_BYTES_DEFAULT, gif_width=None, gif_fps=None,
                 gif_colors=GIF_COLORS_DEFAULT, gif_dither=GIF_DITHER_DEFAULT, export_layers=False, fill_held="auto",
                 interpolate=None, between_cache=None):
    """Render `spec` into the new or empty `out_dir`. `fill_held` (`Renderer`): auto gives a layer cut from a filmed clip
    at the scene's times where it would show a cell again; off gives its cells as cut. `interpolate` stands in for RIFE;
    `between_cache` keeps the frames made between cells across renders (`Renderer`).
    What was not made (no RIFE, a frame refused, off) is printed as `scene-render: warning:` lines once the render is
    published, and recorded in the report's `inspection.repeats`."""
    scene = load_scene(spec)
    formats = parse_formats(formats)
    renderer = Renderer(scene, fill_held=fill_held, interpolate=interpolate, between_cache=between_cache)
    settings = encoding_settings(scene, formats, gif_width=gif_width, gif_fps=gif_fps, gif_colors=gif_colors,
                                 gif_dither=gif_dither, gif_max_bytes=gif_max_bytes)
    output = Path(out_dir).expanduser().resolve()
    _validate_output(output, scene)
    created = not output.exists()
    output.mkdir(parents=True, exist_ok=True)
    try:
        acquire_run_dir_lock(output, "scene-render")
        if any(p.name != LOCK_FILENAME for p in output.iterdir()):
            raise ValueError("scene output directory became nonempty before the writer lock")
        report = _render_locked(scene, renderer, output, formats, settings, export_layers)
        for line in renderer.warnings:
            print(f"scene-render: warning: {line}", file=sys.stderr)
        return report
    except BaseException:
        release_run_dir_lock(output)
        if created:
            # Only the directory this call created, and only while it is still empty:
            # a partially published set stays in place and is reported as a failure.
            with contextlib.suppress(OSError):
                output.rmdir()
        raise
    finally:
        release_run_dir_lock(output)


def _render_locked(scene, renderer, output, formats, settings, export_layers):
    from sprite_gen.scene.inspect_scene import inspect_scene
    with tempfile.TemporaryDirectory(prefix="sprite-gen-scene-") as tmp:
        work = Path(tmp)
        check = inspect_scene(scene, renderer=renderer, frame_dir=work)
        payloads = {}
        for index in sorted(set([0, scene.frame_count//3, 2*scene.frame_count//3, scene.frame_count-1])):
            payloads[output / f"check-{index:05d}.png"] = (work / f"frame-{index:05d}.png").read_bytes()
        if "png" in formats:
            for p in sorted(work.glob("frame-*.png")):
                payloads[output / "frames" / p.name] = p.read_bytes()
        encoded, encoding = encode_frames(work, scene, formats, settings)
        payloads.update({output / name: data for name, data in encoded.items()})
        if export_layers:
            # Frame-zero layer plates plus original references and motion in placement.json.
            # Moving sprites are kept as source references; PNG plates alone are not animation export.
            for layer in scene.layers:
                payloads[output / "layers" / f"{layer.id}.png"] = png_bytes(renderer.frame(0, only=layer.id))
        placement = {"kind": "sprite-gen-scene-placement", "version": 1, "source_spec": str(scene.path),
                     "source_fingerprints": scene.source_fingerprints, "canvas": [scene.width, scene.height],
                     "fps": scene.fps, "duration": scene.duration, "camera": scene.spec.get("camera", {}), "light": scene.light,
                     "assets": {k: {"source": a.metadata["source"], "state": a.metadata.get("state")} for k, a in scene.assets.items()},
                     "layers": [{"id": l.id, "asset": l.asset, "at": l.at, "velocity": l.velocity, "scale": l.scale,
                                 "raster_size": list(l.raster_size), "opacity": l.opacity, "z": l.z, "parallax": l.parallax,
                                 "repeat_x": l.repeat_x, "period": l.period, "shadow": l.shadow, "playback_rate": l.playback_rate,
                                 "loop": l.loop, "offset": l.offset, "plane": l.plane} for l in scene.layers],
                     "layer_plate_time": 0 if export_layers else None, "portable": False,
                     "note": "Placement metadata references source files; copy referenced assets when moving this output."}
        report = {"kind": "sprite-gen-scene-render", "version": 1, "source_spec": str(scene.path),
                  "source_fingerprints": scene.source_fingerprints, "frames": scene.frame_count, "fill_held": renderer.fill_held,
                  # Report paths are manifest entries, so they use "/" on every OS.
                  "files": [relative_posix(p, output) for p in payloads] + ["placement.json", "scene.report.json"],
                  "encoding": encoding, "inspection": check}
        if renderer.cache is not None:
            report["between_cache"] = renderer.between_cache_record()
        payloads[output / "placement.json"] = json.dumps(placement, indent=2)+"\n"
        payloads[output / "scene.report.json"] = json.dumps(report, indent=2)+"\n"
        # The fingerprints are the bytes that were decoded; an input replaced since
        # then is detected here, before any success report is published.
        if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest for p, digest in scene.source_fingerprints.items()):
            raise ValueError("scene inputs changed during render; output not published")
        # The output directory was verified empty under the lock; its subdirectories
        # are created only now, after every encode succeeded, and removed again
        # if they are still empty when publication fails.
        directories = sorted({p.parent for p in payloads} - {output})
        created = []
        try:
            for directory in directories:
                directory.mkdir()
                created.append(directory)
            atomic_write_set(payloads)
        except BaseException:
            for directory in reversed(created):
                with contextlib.suppress(OSError):
                    directory.rmdir()
            raise
    return report


def add_arguments(parser):
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--formats", default="mp4,gif", help="comma-separated selection of png,mp4,gif")
    parser.add_argument("--gif-max-bytes", type=int, default=GIF_MAX_BYTES_DEFAULT,
                        help="fail (do not degrade) when the GIF is larger than this many bytes")
    parser.add_argument("--gif-width", type=int, help="GIF width in pixels; default min(640, canvas width), never wider than the canvas")
    parser.add_argument("--gif-fps", type=float, help="GIF frame rate; default the scene fps, never higher")
    parser.add_argument("--gif-colors", type=int, default=GIF_COLORS_DEFAULT, help="palette size 2..256; default 256")
    parser.add_argument("--gif-dither", choices=GIF_DITHER_CHOICES, default=GIF_DITHER_DEFAULT,
                        help="paletteuse dithering; default none keeps flat pixel-art colours exact")
    parser.add_argument("--export-layers", action="store_true")
    parser.add_argument("--fill-held", choices=FILL_HELD_MODES, default="auto",
                        help="auto (default): a layer cut from a filmed clip (a video-loop or video-stop strip) that would show "
                             "a cell again at the scene's frames — the scene faster than its cells, a playback_rate under 1 — "
                             "is given at the scene's times, each frame its cell or RIFE's frame between the cells either side, "
                             "judged as video-loop judges one; held on purpose stays held (before its start, a one-shot's end, "
                             "a hold its strip lists, any layer that is not a video cut). No RIFE: its cells as cut, with a "
                             "warning line. off: every layer's cells as its timing picks them")
    parser.add_argument("--between-cache", type=Path,
                        help="a directory the frames made between cells are kept in and taken from on the next render, "
                             "under the bytes of the two cells, the time and the RIFE that made them (the report's "
                             "between_cache counts them); nothing is kept without it")


def run(**kwargs):
    try:
        report = render_scene(**kwargs)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f"scene-render: {exc}") from exc
    print(json.dumps({"frames": report["frames"], "files": report["files"], "encoding": report["encoding"],
                      "inspection_status": report["inspection"]["status"]}, indent=2))
    return 0


def main():
    utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
