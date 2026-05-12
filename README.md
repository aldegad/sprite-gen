# sprite-gen skill

Experimental Codex/Claude skill for generating quick 2D game sprite sheets for Kuma/Hermes-style live demos.

This is intentionally unfinished and demo-oriented. It is useful as a practical pipeline reference for:

- attaching a canonical character image as identity input;
- generating magenta-keyed sprite sheets;
- removing magenta backgrounds into alpha PNGs;
- validating that a sheet is an actual animated character sheet rather than a tiled still image;
- recovering `frame_layout` metadata when generated frames drift away from a fixed grid.

The main workflow lives in [`SKILL.md`](SKILL.md). Deterministic helpers live under [`scripts/`](scripts/), and built-in layout references live under [`assets/`](assets/).

## Install

From Codex skill installer workflows, install this repository as a root skill:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen \
  --path .
```

## Status

Experimental. Expect to adapt prompts, validation thresholds, and runtime integration for each game/demo.

## License

MIT
