# SPDX-License-Identifier: Apache-2.0
"""Video-to-sprite pipeline: one still -> a Grok Imagine clip -> keyed frames -> a
transparent, seamless loop (cycle frames, strip, GIF, WebP) with a report at every stage.

Stages, each its own module and CLI verb (contract: docs/video-pipeline.md):

- `canvas`  `sprite-gen video-canvas`  pad the base still into the state's canvas
                                       (tall for jumps, wide for attacks, square otherwise)
- `frames`  `sprite-gen video-frames`  extract the clip's frames and key the chroma out
- `loop`    `sprite-gen video-loop`    find the true period, cut one cycle, emit strip/GIF/WebP
- `batch`   `sprite-gen video-set`     directions x states, rate-limit aware, one report each

`sprite-gen video` (the clip generation itself) lives in `sprite_gen.gen.video`.
"""
