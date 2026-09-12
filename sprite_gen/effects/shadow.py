# SPDX-License-Identifier: Apache-2.0
"""Project an asset's alpha silhouette into a shadow about its stationary foot."""

from __future__ import annotations

import argparse
import io
import json
import math
from numbers import Integral, Real
from pathlib import Path

from PIL import Image, ImageFilter

from sprite_gen.spec.runio import (
    acquire_run_dir_lock,
    atomic_write_set,
    release_run_dir_lock,
)


_MAX_FRAME_PIXELS = 16_777_216
_MAX_SEQUENCE_PIXELS = 67_108_864
_MAX_DIMENSION = 65_536


def _number(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} is too large") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
    return result


def _anchor(value) -> tuple[float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("anchor must contain two pixel coordinates")
    return (_number(value[0], "anchor x", -1_000_000, 1_000_000),
            _number(value[1], "anchor y", -1_000_000, 1_000_000))


def _rgb(value) -> tuple[int, int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("color must contain three RGB integers in [0, 255]")
    if any(isinstance(v, bool) or not isinstance(v, Integral) or not 0 <= v <= 255
           for v in value):
        raise ValueError("color must contain three RGB integers in [0, 255]")
    return tuple(int(v) for v in value)


def _parameters(squash, shear, opacity, blur, color):
    return (_number(squash, "squash", 0.001, 1),
            _number(shear, "shear", -16, 16),
            _number(opacity, "opacity", 0, 1),
            _number(blur, "blur", 0, 128), _rgb(color))


def _check_size(size, *, limit=_MAX_FRAME_PIXELS):
    width, height = size
    if (width <= 0 or height <= 0 or max(width, height) > _MAX_DIMENSION
            or width * height > limit):
        raise ValueError(f"shadow canvas {width}x{height} exceeds the supported size "
                         f"(positive dimensions <= {_MAX_DIMENSION}, <= {limit} pixels)")


def _geometry(size, anchor, squash, shear, blur):
    _check_size(size)
    width, height = size
    ax, ay = anchor
    # Forward map: x' = x + shear*(y-ay), y' = ay + squash*(y-ay).
    # Including the anchor keeps even an external pivot representable. Bounds
    # come from the full image rectangle, never a changing per-frame alpha bbox.
    # Bicubic interpolation can touch two transparent source pixels past an
    # edge; their projected support must fit even at the maximum shear.
    xs = [x + shear * (y - ay) for x in (-2, width + 2)
          for y in (-2, height + 2)] + [ax]
    ys = [ay + squash * (y - ay) for y in (-2, height + 2)] + [ay]
    # Pillow's Gaussian approximation has finite support. Four radii plus
    # interpolation clearance leave a transparent border even at opaque edges.
    padding = math.ceil(4 * blur) + 3
    left, top = math.floor(min(xs)) - padding, math.floor(min(ys)) - padding
    right, bottom = math.ceil(max(xs)) + padding, math.ceil(max(ys)) + padding
    output_size = (right - left, bottom - top)
    _check_size(output_size)
    return output_size, (ax - left, ay - top), (left, top)


def project_shadow(
    image: Image.Image,
    anchor: tuple[float, float],
    *,
    squash: float = 0.25,
    shear: float = 0.8,
    opacity: float = 0.4,
    blur: float = 3,
    color: tuple[int, int, int] = (20, 15, 30),
) -> tuple[Image.Image, tuple[float, float]]:
    """Return an RGBA shadow and its foot anchor, both in canvas pixel units.

    Place the returned anchor at the sprite's world anchor. The affine map
    fixes that point exactly; positive shear sends pixels above it to the left.
    Canvas bounds include the entire projection and blur, including for an
    off-center anchor. Neither source pixels nor image metadata are modified.

    ``squash`` is in [0.001, 1], ``shear`` in [-16, 16], ``opacity`` in [0, 1],
    and ``blur`` is a radius in [0, 128] pixels. RGB components are bytes.
    """
    if not isinstance(image, Image.Image):
        raise ValueError("image must be a PIL Image")
    anchor = _anchor(anchor)
    squash, shear, opacity, blur, color = _parameters(squash, shear, opacity, blur, color)
    size, shadow_anchor, (left, top) = _geometry(image.size, anchor, squash, shear, blur)
    _, ay = anchor
    # Pillow expects the inverse map, in edge-based pixel coordinates.
    # A transparent source border avoids Pillow's interpolation edge clamping:
    # an opaque silhouette touching the source edge then behaves exactly as it
    # would on a larger transparent source canvas.
    source_alpha = Image.new("L", (image.width + 4, image.height + 4))
    source_alpha.paste(image.convert("RGBA").getchannel("A"), (2, 2))
    alpha = source_alpha.transform(
        size, Image.Transform.AFFINE,
        (1, -shear / squash, left - shear * (top - ay) / squash + 2,
         0, 1 / squash, ay + (top - ay) / squash + 2),
        resample=Image.Resampling.BICUBIC, fillcolor=0,
    )
    if blur:
        alpha = alpha.filter(ImageFilter.GaussianBlur(blur))
    # Applying opacity last ensures monotonicity, including blurred edges.
    alpha = alpha.point([round(value * opacity) for value in range(256)])
    shadow = Image.new("RGBA", size, (*color, 0))
    shadow.putalpha(alpha)
    return shadow, shadow_anchor


def _parse_color(value: str) -> tuple[int, int, int]:
    try:
        if value.startswith("#") and len(value) == 7:
            return _rgb(tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)))
        return _rgb(tuple(int(part.strip()) for part in value.split(",")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("color must be R,G,B or #RRGGBB") from exc


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", required=True, type=Path, help="asset PNG or manifest")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--state", help="animation state for an atlas")
    parser.add_argument("--fps", type=float, help="explicit source frame rate")
    parser.add_argument("--anchor-x", type=float, help="source foot x in pixels; supply both axes")
    parser.add_argument("--anchor-y", type=float, help="source foot y in pixels; supply both axes")
    parser.add_argument("--squash", type=float, default=0.25, help="vertical scale [0.001, 1]")
    parser.add_argument("--shear", type=float, default=0.8, help="shear [-16, 16]; positive falls left")
    parser.add_argument("--opacity", type=float, default=0.4, help="alpha multiplier [0, 1]")
    parser.add_argument("--blur", type=float, default=3, help="blur radius [0, 128] pixels")
    parser.add_argument("--color", type=_parse_color, default=(20, 15, 30), help="R,G,B or #RRGGBB")


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _validate_targets(targets, sources):
    protected = {Path(path).resolve() for path in sources}
    for target in targets:
        if target.resolve() in protected:
            raise ValueError(f"shadow output would overwrite source: {target}")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"shadow output must be a regular file: {target}")


def run(
    *, source: Path, out_dir: Path, state: str | None = None, fps: float | None = None,
    anchor_x: float | None = None, anchor_y: float | None = None,
    squash: float = 0.25, shear: float = 0.8, opacity: float = 0.4,
    blur: float = 3, color: tuple[int, int, int] = (20, 15, 30),
) -> int:
    """Publish frame PNGs, ``shadow.png`` strip and ``shadow.asset.json`` descriptor."""
    squash, shear, opacity, blur, color = _parameters(squash, shear, opacity, blur, color)
    if (anchor_x is None) != (anchor_y is None):
        raise ValueError("anchor-x and anchor-y must be supplied together")
    anchor = None if anchor_x is None else _anchor((anchor_x, anchor_y))
    if fps is not None:
        fps = _number(fps, "fps", 0.001, 1000)
    source, out_dir = Path(source).resolve(), Path(out_dir).resolve()
    # The shared adapter alone owns source formats and timing interpretation.
    # Lazy import also keeps the pure image operation independent of file IO.
    from sprite_gen.spec.assets import load_asset

    asset = load_asset(source, state=state, fps=fps, anchor=anchor)
    if not asset.frames or len(asset.frames) != len(asset.durations):
        raise ValueError("asset must have one duration for every frame")
    durations = [_number(value, "frame duration", 1e-9, 86_400)
                 for value in asset.durations]
    if any(frame.size != asset.frames[0].size for frame in asset.frames):
        raise ValueError("asset frames must share a uniform canvas")
    anchor = _anchor(asset.anchor)
    size, output_anchor, _ = _geometry(asset.frames[0].size, anchor, squash, shear, blur)
    strip_size = (size[0] * len(asset.frames), size[1])
    _check_size(strip_size, limit=_MAX_SEQUENCE_PIXELS)
    protected = (source, *asset.source_files)
    targets = [out_dir / f"frame-{i:03d}.png" for i in range(len(asset.frames))]
    sheet_path, descriptor_path = out_dir / "shadow.png", out_dir / "shadow.asset.json"
    _validate_targets([*targets, sheet_path, descriptor_path], protected)
    if out_dir.exists() and not out_dir.is_dir():
        raise ValueError(f"out-dir must be a directory: {out_dir}")

    # All rendering, encoding and validation precede directory/lock creation.
    # No failed projection can leave a partially published frame sequence.
    sheet = Image.new("RGBA", strip_size)
    payloads = {}
    for index, (frame, target) in enumerate(zip(asset.frames, targets)):
        rendered, _ = project_shadow(
            frame, anchor, squash=squash, shear=shear, opacity=opacity, blur=blur, color=color)
        sheet.paste(rendered, (index * size[0], 0))
        payloads[target] = _png_bytes(rendered)
    payloads[sheet_path] = _png_bytes(sheet)
    descriptor = {
        "kind": "sprite-gen-asset", "version": 1,
        "frames": [{"file": target.name, "duration": duration}
                   for target, duration in zip(targets, durations)],
        "anchor": list(output_anchor),
    }
    payloads[descriptor_path] = json.dumps(descriptor, indent=2, allow_nan=False) + "\n"
    out_dir.mkdir(parents=True, exist_ok=True)
    acquire_run_dir_lock(out_dir, "shadow")
    try:
        # A concurrent writer may have changed the destination while rendering.
        _validate_targets(payloads, protected)
        atomic_write_set(payloads)
    finally:
        release_run_dir_lock(out_dir)
    print(json.dumps({"ok": True, "manifest": str(descriptor_path), "sheet": str(sheet_path),
                      "frames": len(targets), "anchor": list(output_anchor)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run(**vars(args))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
