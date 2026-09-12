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
import tempfile

from PIL import Image

from sprite_gen.scene.model import Scene, load_scene
from sprite_gen.spec.assets import finite
from sprite_gen.spec.runio import LOCK_FILENAME, acquire_run_dir_lock, atomic_write_set, release_run_dir_lock


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


class Renderer:
    def __init__(self, scene: Scene):
        self.scene = scene
        self.prepared = {}
        # Import only when scene/shadow is used; ordinary sprite commands need no scene setup.
        from sprite_gen.effects.shadow import project_shadow
        for layer in scene.layers:
            seq = layer.sequence
            size = tuple(layer.raster_size)
            anchor = tuple(a*new/old for a, new, old in zip(seq.anchor, size, seq.size))
            frames = {}
            for source in seq.frames:
                image = source.resize(size, Image.Resampling.LANCZOS)
                if layer.repeat_x:
                    image = image.crop((0, 0, round(layer.period*layer.scale), size[1]))
                if layer.opacity != 1:
                    image.putalpha(image.getchannel("A").point(lambda a: round(a*layer.opacity)))
                shadow = project_shadow(image, anchor, **scene.light) if layer.shadow else None
                frames[id(source)] = (image, anchor, shadow, image.getchannel("A").getbbox())
            self.prepared[layer.id] = frames

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
    if any(p == output or output in p.parents for p in map(Path, scene.source_fingerprints)):
        raise ValueError("output directory contains scene inputs; choose a separate output directory")
    if output.exists() and not output.is_dir():
        raise ValueError("scene output must be a directory")
    if output.exists() and any(p.name != LOCK_FILENAME for p in output.iterdir()):
        raise ValueError("scene output directory must be empty; choose a new render directory")


def render_scene(spec, out_dir, *, formats="mp4,gif", gif_max_bytes=GIF_MAX_BYTES_DEFAULT, gif_width=None, gif_fps=None,
                 gif_colors=GIF_COLORS_DEFAULT, gif_dither=GIF_DITHER_DEFAULT, export_layers=False):
    scene = load_scene(spec)
    formats = parse_formats(formats)
    renderer = Renderer(scene)
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
        return _render_locked(scene, renderer, output, formats, settings, export_layers)
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
                  "source_fingerprints": scene.source_fingerprints, "frames": scene.frame_count,
                  "files": [str(p.relative_to(output)) for p in payloads] + ["placement.json", "scene.report.json"],
                  "encoding": encoding, "inspection": check}
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


def run(**kwargs):
    try:
        report = render_scene(**kwargs)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f"scene-render: {exc}") from exc
    print(json.dumps({"frames": report["frames"], "files": report["files"], "encoding": report["encoding"],
                      "inspection_status": report["inspection"]["status"]}, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
