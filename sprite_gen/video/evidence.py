# SPDX-License-Identifier: Apache-2.0
"""Source identity and gait evidence shared by automatic and explicit loop cuts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen.video import legs


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def engine_identity() -> dict[str, str]:
    """Fingerprint the measured video implementation, including an unreleased checkout."""
    root = Path(__file__).parent
    files = [*sorted(root.glob("*.py")), *sorted((root.parent / "frames").glob("*.py")),
             root.parent / "cli.py", root.parent / "util/resample.py", root.parent / "util/gif_utils.py"]
    return {"implementation_sha256": digest(b"".join(str(p.relative_to(root.parent)).encode() + b"\0" + p.read_bytes() for p in files))}


def source_identity(files: list[Path], fps: float) -> dict[str, Any]:
    """Ordered original keyed-file bytes, before anchoring, cleanup, or size hold."""
    return {"kind": "keyed-frame-sequence", "sha256": digest(b"".join(bytes.fromhex(digest(p.read_bytes())) for p in files)),
            "frames": len(files), "fps": fps}


def gait_observation(frames: list[Image.Image], cycle: dict[str, Any]) -> dict[str, Any]:
    """Silhouette signals are observations, never a count of identified foot contacts."""
    record: dict[str, Any] = {
        "status": "unverified", "reason": "own-foot-contacts-not-identified",
        "cut": {"start": cycle["start"], "length": cycle["length"]},
        "screens": {k: cycle[k] for k in ("fundamental", "step_screen", "steps") if k in cycle},
    }
    try:
        signals = legs.strike_signals(frames)
        record["silhouette"] = {"signals": signals, "swings": legs.swings(signals),
                                "legs_by": legs.legs_by(signals)}
    except ValueError as exc:
        record["silhouette"] = {"status": "unread", "reason": str(exc)}
    return record
