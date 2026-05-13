# sprite-gen skill

Codex/Claude skill for generating clean 2D game sprites and animation atlases with a hatch-pet-style component-row pipeline.

The workflow is intentionally single-path:

```text
sprite-request.json -> layout guides + prompts -> image-gen state rows
-> chroma alpha -> connected components -> square frames
-> sprite-sheet-alpha.png + manifest.json.frame_layout
```

The main workflow lives in [`SKILL.md`](SKILL.md). Deterministic helpers live under [`scripts/`](scripts/), and default request values live under [`assets/sprite-gen-assets.json`](assets/sprite-gen-assets.json).

## Install

From Codex skill installer workflows, install this repository as a root skill:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen \
  --path .
```

## Status

Component-row is the canonical engine. Fixed-grid cutting and one-shot master sheets are no longer part of the accepted path.

## Attribution

This skill is inspired by the Apache-2.0 licensed `hatch-pet` component-row workflow, but targets generic game sprite atlases instead of Codex pet packages.

## License

Apache-2.0
