"""`detect_background_key_rgb` reads the image a band of rows at a time and must answer as the
whole-image form did. `_whole_image_detect` is that form, frozen: every sample widened to
int32 at once, then `np.unique` over the bins. The banded form keeps a count and a channel
sum per bin instead, so a large still costs a histogram rather than an array of samples.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from sprite_gen.frames.cutout import _corner_average
from sprite_gen.frames.extract import (_KEY_DETECT_BAND_ROWS, _KEY_DETECT_BIN_SHIFT, _KEY_DETECT_CORNER_DIV,
                                       _KEY_DETECT_MIN_FRACTION, _border_key_candidate_field, _key_channel_split,
                                       detect_background_key_rgb)

GREEN = (0, 255, 0)
MAGENTA = (255, 0, 255)


def _whole_image_mask(height: int, width: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    if height == 0 or width == 0:
        return mask
    corner_w = width // _KEY_DETECT_CORNER_DIV
    corner_h = height // _KEY_DETECT_CORNER_DIV
    if corner_w < 2:
        corner_w = width
    if corner_h < 2:
        corner_h = height
    mask[:corner_h, :corner_w] = True
    mask[:corner_h, width - corner_w:] = True
    mask[height - corner_h:, :corner_w] = True
    mask[height - corner_h:, width - corner_w:] = True
    mask[0, :] = mask[-1, :] = True
    mask[:, 0] = mask[:, -1] = True
    return mask


def _whole_image_detect(image: Image.Image, chroma_key: tuple[int, int, int]) -> tuple[int, int, int]:
    keyed_channels, unkeyed_channels = _key_channel_split(chroma_key)
    if not keyed_channels:
        return tuple(chroma_key)
    data = np.array(image.convert("RGBA"), dtype=np.uint8)
    height, width = data.shape[:2]
    sample = _whole_image_mask(height, width) & (data[..., 3] != 0)
    if not sample.any():
        return tuple(chroma_key)
    rgb = data[..., :3].astype(np.int32)
    family = sample & _border_key_candidate_field(rgb, keyed_channels, unkeyed_channels)
    if int(np.count_nonzero(family)) < int(np.count_nonzero(sample)) * _KEY_DETECT_MIN_FRACTION:
        return tuple(chroma_key)
    colors = rgb[family]
    bins = (colors >> _KEY_DETECT_BIN_SHIFT).astype(np.int64)
    slots = (bins[:, 0] << 16) | (bins[:, 1] << 8) | bins[:, 2]
    _, inverse, counts = np.unique(slots, return_inverse=True, return_counts=True)
    members = colors[inverse == int(np.argmax(counts))]
    mean = members.sum(axis=0, dtype=np.int64) // len(members)
    return (int(mean[0]), int(mean[1]), int(mean[2]))


def _noisy(size: tuple[int, int], colour: tuple[int, int, int], spread: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = np.asarray(colour, dtype=np.int16) + rng.integers(-spread, spread + 1, size=(size[1], size[0], 3))
    return np.clip(base, 0, 255).astype(np.uint8)


def _images() -> list[tuple[str, Image.Image]]:
    rng = np.random.default_rng(7)
    out: list[tuple[str, Image.Image]] = []
    for width, height in [(1, 1), (2, 3), (5, 4), (13, 9), (9, 131), (300, 7), (97, 301), (211, 5 * _KEY_DETECT_BAND_ROWS + 3)]:
        painted = _noisy((width, height), (8, 162, 24), 9, width * 1000 + height)
        subject = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
        # a subject over the middle third: the corners and borders stay background
        mixed = painted.copy()
        mixed[height // 3: 2 * height // 3 + 1, width // 3: 2 * width // 3 + 1] = subject[height // 3: 2 * height // 3 + 1, width // 3: 2 * width // 3 + 1]
        out.append((f"painted {width}x{height}", Image.fromarray(mixed)))
        out.append((f"random {width}x{height}", Image.fromarray(subject)))
        alpha = (rng.random((height, width)) > 0.4).astype(np.uint8) * 255
        out.append((f"holes {width}x{height}", Image.fromarray(np.dstack([mixed, alpha]))))
    # two clusters of equal size on the border: the tie goes to the lowest bin
    tie = np.zeros((40, 40, 3), dtype=np.uint8)
    tie[:, :20] = (8, 162, 24)
    tie[:, 20:] = (8, 170, 24)
    out.append(("tie", Image.fromarray(tie)))
    # a tie between bins that order one way by red and the other by blue
    tie[:, :20] = (8, 162, 40)
    tie[:, 20:] = (16, 162, 24)
    out.append(("tie across channels", Image.fromarray(tie)))
    grok = _noisy((120, 90), (216, 46, 147), 6, 11)
    out.append(("grok magenta", Image.fromarray(grok)))
    out.append(("palette", Image.fromarray(_noisy((64, 80), (8, 162, 24), 12, 3)).convert("P")))
    return out


@pytest.mark.parametrize("key", [GREEN, MAGENTA, (128, 128, 128)])
def test_banded_detection_answers_as_the_whole_image_form(key: tuple[int, int, int]) -> None:
    for name, image in _images():
        assert detect_background_key_rgb(image, key) == _whole_image_detect(image, key), name


def test_the_detected_colour_is_the_painted_one() -> None:
    painted = Image.fromarray(_noisy((300, 2 * _KEY_DETECT_BAND_ROWS + 10), (8, 162, 24), 2, 5))
    assert detect_background_key_rgb(painted, GREEN) != GREEN
    r, g, b = detect_background_key_rgb(painted, GREEN)
    assert abs(r - 8) <= 3 and abs(g - 162) <= 3 and abs(b - 24) <= 3


def test_corner_average_reads_any_mode_as_rgba() -> None:
    for _, image in _images():
        assert _corner_average(image) == _corner_average(image.convert("RGBA"))
