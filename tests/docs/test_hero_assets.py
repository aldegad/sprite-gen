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
