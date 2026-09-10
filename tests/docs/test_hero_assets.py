# SPDX-License-Identifier: Apache-2.0
"""The six README translations share one animated hero, linked to the showcase."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
READMES = ["README.md", "README.ko.md", "README.ja.md", "README.zh-Hans.md", "README.es.md", "README.fr.md"]


def test_readme_hero_is_animated_and_links_to_the_showcase() -> None:
    path = ROOT / "docs/assets/hero-v2-party.gif"
    with Image.open(path) as image:
        assert image.size == (1280, 640)
        assert image.n_frames > 1
        assert image.info.get("loop") == 0
        first = image.convert("RGB").tobytes()
        image.seek(image.n_frames // 4)
        assert image.convert("RGB").tobytes() != first
    assert path.stat().st_size < 10_000_000
    for name in READMES:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert text.count('src="docs/assets/hero-v2-party.gif"') == 1
        assert 'href="https://youtu.be/zVu9YlbPtog"><img src="docs/assets/hero-v2-party.gif"' in text
