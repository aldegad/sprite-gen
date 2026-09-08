# SPDX-License-Identifier: Apache-2.0
"""Parts rig — split one character image into generated body-part layers,
register them back onto the original by pixel matching, and export a JSON rig
plus a seek-safe HTML runtime (lip-sync, blink, head sway).

Contract: `docs/parts-rig.md`. Stages: `catalog` (declaration) → `gen` (per-part
generation from the base + crop) → `match` (pixel registration gate) → `rig`
(rig.json + runtime export).
"""
