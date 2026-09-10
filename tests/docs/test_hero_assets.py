# SPDX-License-Identifier: Apache-2.0
"""The README hero GIFs are pipeline outputs shown at their real size: every hero <img>
height equals the file's pixel height (a jump has air room, so it is taller), every hero
loops forever, and the six READMEs agree."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
READMES = ["README.md", "README.ko.md", "README.ja.md", "README.zh-Hans.md", "README.es.md", "README.fr.md"]
TAG = re.compile(r'<img src="docs/assets/(hero-[a-z-]+\.gif)" height="(\d+)"')


def test_hero_img_heights_match_the_files_in_every_readme() -> None:
    sets = []
    for name in READMES:
        tags = TAG.findall((ROOT / name).read_text(encoding="utf-8"))
        assert tags, f"{name} has no hero tags"
        sets.append(tags)
        for gif, height in tags:
            im = Image.open(ROOT / "docs" / "assets" / gif)
            assert im.height == int(height), (name, gif, im.height, height)
            assert im.info.get("loop") == 0 and im.n_frames > 1, gif
    assert all(s == sets[0] for s in sets), "hero rows differ between READMEs"


def test_showcase_poster_fits_social_preview_limits_and_all_readmes_link_it() -> None:
    poster = ROOT / "docs/assets/sprite-gen-v2-showcase.jpg"
    with Image.open(poster) as image:
        assert image.format == "JPEG"
        assert image.size == (1280, 640)
    assert poster.stat().st_size < 1_000_000
    for name in READMES:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert 'href="https://youtu.be/zVu9YlbPtog"><img src="docs/assets/sprite-gen-v2-showcase.jpg"' in text
