# SPDX-License-Identifier: Apache-2.0
"""Scene layout and time belong here; source frames belong to asset metadata."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from sprite_gen.spec.assets import FrameSequence, finite, load_asset, same_identity


def fields(data, allowed, where):
    if not isinstance(data, dict):
        raise ValueError(f"{where} must be an object")
    extra = set(data) - set(allowed)
    if extra:
        raise ValueError(f"unknown {where} fields: {', '.join(sorted(extra))}")


def pair(value, where):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{where} must be [x, y]")
    return tuple(finite(v, where) for v in value)


def boolean(value, where):
    if not isinstance(value, bool):
        raise ValueError(f"{where} must be boolean")
    return value


def read_json(path: Path) -> tuple[object, str]:
    """Parse a JSON file from one read and return the document with the SHA-256 of those bytes."""
    data = Path(path).read_bytes()
    return json.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest()


@dataclass
class Layer:
    id: str
    asset: str
    sequence: FrameSequence
    at: tuple
    velocity: tuple
    scale: float
    # Integer pixel size the frames are actually rasterised at. Its ratio to the
    # source size, not the requested `scale`, is the scale motion is applied with.
    raster_size: tuple
    opacity: float
    z: float
    parallax: float
    repeat_x: bool
    period: float
    shadow: bool
    playback_rate: float
    loop: bool
    offset: float
    plane: str | None


@dataclass
class Scene:
    path: Path
    spec: dict
    width: int
    height: int
    fps: float
    duration: float
    background: tuple
    camera_at: tuple
    camera_velocity: tuple
    light: dict
    layers: list[Layer]
    assets: dict[str, FrameSequence]
    source_fingerprints: dict

    @property
    def frame_count(self):
        return round(self.fps * self.duration)


def bound_stride(measurement, sequence: FrameSequence) -> float:
    """Return the verified stride of ``measurement`` if it measured exactly ``sequence``.

    The report must be verified, hash the same bytes the sequence was decoded
    from, and name the same selection: source, atlas state, frame-rate override,
    frame count, size, anchor and per-frame durations. A walk report cannot
    drive the idle state of the same atlas, and a report taken at another frame
    rate cannot drive the sequence at its native timing.
    """
    if not isinstance(measurement, dict) or measurement.get("kind") != "sprite-gen-motion-report":
        raise ValueError("stride report must be a sprite-gen-motion-report")
    if measurement.get("stride_verified") is not True:
        raise ValueError("stride measurement is unverified; provide reliable contact evidence")
    if measurement.get("source_fingerprints") != sequence.fingerprints():
        raise ValueError("stride measurement is stale or belongs to another asset")
    if "sequence" not in measurement:
        raise ValueError("stride report lacks its sequence identity; measure again with the current motion tool")
    if not same_identity(measurement["sequence"], sequence):
        raise ValueError("stride measurement belongs to another state, frame rate or anchor of this asset")
    return finite(measurement.get("stride_px_per_second"), "measured stride", positive=True)


def load_scene(path: Path) -> Scene:
    path = Path(path).expanduser().resolve()
    data, digest = read_json(path)
    fields(data, {"kind", "version", "canvas", "fps", "duration", "camera", "light", "planes", "assets", "layers"}, "scene")
    if data.get("kind") != "sprite-gen-scene" or data.get("version") != 1:
        raise ValueError("scene requires kind sprite-gen-scene and version 1")
    canvas = data.get("canvas", {})
    fields(canvas, {"width", "height", "background"}, "canvas")
    dims = [finite(canvas.get(k), f"canvas {k}", positive=True) for k in ("width", "height")]
    if any(v != int(v) or v > 8192 for v in dims) or dims[0]*dims[1] > 16_777_216:
        raise ValueError("scene canvas must use integer dimensions up to 8192 and 16 megapixels")
    background = canvas.get("background", [0, 0, 0, 255])
    if not isinstance(background, list) or len(background) not in (3, 4) or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in background):
        raise ValueError("canvas background must be RGB or RGBA bytes")
    if len(background) == 3:
        background = background + [255]
    fps = finite(data.get("fps", 24), "fps", positive=True)
    duration = finite(data.get("duration"), "duration", positive=True)
    if fps > 120 or not 1 <= round(duration*fps) <= 10_000 or abs(duration*fps-round(duration*fps)) > 1e-6:
        raise ValueError("duration * fps must be an integer from 1 to 10000; fps <= 120")
    camera = data.get("camera", {})
    fields(camera, {"at", "velocity"}, "camera")
    camera_at = pair(camera.get("at", [0, 0]), "camera at")
    camera_velocity = pair(camera.get("velocity", [0, 0]), "camera velocity")
    light = data.get("light", {})
    fields(light, {"squash", "shear", "opacity", "blur", "color"}, "light")
    planes = data.get("planes", {})
    if not isinstance(planes, dict):
        raise ValueError("planes must be an object")
    for name, plane in planes.items():
        fields(plane, {"velocity"}, f"plane {name}")
        pair(plane.get("velocity", [0, 0]), "plane velocity")
    assets, fingerprints = {}, {str(path): digest}
    if not isinstance(data.get("assets"), dict) or not data["assets"]:
        raise ValueError("scene assets must be a nonempty object")
    for name, reference in data["assets"].items():
        if isinstance(reference, str):
            reference = {"source": reference}
        fields(reference, {"source", "state"}, f"asset {name}")
        if not isinstance(reference.get("source"), str):
            raise ValueError(f"asset {name} requires a source path")
        asset = load_asset(path.parent / reference["source"], state=reference.get("state"))
        assets[name] = asset
        fingerprints.update(asset.fingerprints())
    entries = data.get("layers")
    if not isinstance(entries, list) or not entries:
        raise ValueError("scene layers must be a nonempty list")
    layers, names = [], set()
    for entry in entries:
        fields(entry, {"id", "asset", "at", "velocity", "scale", "opacity", "z", "parallax", "repeat_x", "period", "shadow", "playback_rate", "loop", "offset", "plane", "stride"}, "layer")
        name = entry.get("id")
        if not isinstance(name, str) or not name or not all(c.isalnum() or c in "-_" for c in name) or name in names:
            raise ValueError("layer id must be unique and contain only letters, digits, dash or underscore")
        names.add(name)
        asset_name = entry.get("asset")
        if asset_name not in assets:
            raise ValueError(f"unknown asset for layer {name}: {asset_name}")
        seq = assets[asset_name]
        scale = finite(entry.get("scale", 1), "layer scale", positive=True)
        raster_size = tuple(round(v*scale) for v in seq.size)
        if min(raster_size) < 1 or max(raster_size) > 16384 or raster_size[0]*raster_size[1] > 16_777_216:
            raise ValueError("scaled layer dimensions must be between 1 and 16384 and at most 16 megapixels")
        opacity = finite(entry.get("opacity", 1), "layer opacity")
        if not 0 <= opacity <= 1:
            raise ValueError("layer opacity must be 0..1")
        plane = entry.get("plane")
        if plane is not None and plane not in planes:
            raise ValueError(f"unknown plane: {plane}")
        plane_v = pair(planes[plane].get("velocity", [0, 0]), "plane velocity") if plane is not None else (0, 0)
        velocity = pair(entry.get("velocity", [0, 0]), "layer velocity")
        rate = finite(entry.get("playback_rate", 1), "playback_rate", positive=True)
        if "stride" in entry:
            if "velocity" in entry:
                raise ValueError("choose layer velocity or measured stride, not both")
            stride = entry["stride"]
            fields(stride, {"report", "direction"}, "stride")
            if stride.get("direction") not in ("left", "right") or not isinstance(stride.get("report"), str):
                raise ValueError("stride requires a report and explicit left/right direction")
            rp = (path.parent / stride["report"]).resolve()
            measurement, report_digest = read_json(rp)
            speed = bound_stride(measurement, seq)
            # Source px per source second, scaled to rendered px per scene second:
            # the raster scale actually used for this layer times the playback rate.
            raster_scale = raster_size[0] / seq.size[0]
            velocity = (speed*raster_scale*rate*(-1 if stride["direction"] == "left" else 1), 0)
            fingerprints[str(rp)] = report_digest
        velocity = tuple(a+b for a, b in zip(plane_v, velocity))
        period = finite(entry.get("period", seq.size[0]), "repeat period", positive=True)
        if period > seq.size[0] or int(period) != period or round(period*scale) < 1:
            raise ValueError("repeat period must be an integer within the source width")
        layer = Layer(name, asset_name, seq, pair(entry.get("at", [0, 0]), "layer at"), velocity, scale, raster_size, opacity,
                      finite(entry.get("z", 0), "layer z"), finite(entry.get("parallax", 1), "parallax"),
                      boolean(entry.get("repeat_x", False), "repeat_x"), period, boolean(entry.get("shadow", False), "shadow"),
                      rate, boolean(entry.get("loop", seq.metadata.get("loop", True)), "loop"), finite(entry.get("offset", 0), "offset"), plane)
        layers.append(layer)
    return Scene(path, data, int(dims[0]), int(dims[1]), fps, duration, tuple(background), camera_at, camera_velocity,
                 light, sorted(layers, key=lambda layer: layer.z), assets, fingerprints)
