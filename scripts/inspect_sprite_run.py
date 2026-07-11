#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Backward-compatible wrapper for sprite_gen.inspect."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sprite_gen.inspect as _impl

globals().update({name: value for name, value in vars(_impl).items() if name not in {"__name__", "__package__", "__loader__", "__spec__"}})


if __name__ == "__main__":
    raise SystemExit(main())
