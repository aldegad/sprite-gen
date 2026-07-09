# SPDX-License-Identifier: Apache-2.0
"""Placeholder for the v2 desktop-only image generation module.

The public sprite-gen package intentionally keeps provider/app generation out of
this refactor. This module exists so the v2 MCP import surface resolves during
the package split, but runtime use fails loudly instead of falling back.
"""


def run(**_kwargs: object) -> int:
    raise SystemExit("sprite_gen.generate_image is desktop-app scope and is not shipped in this public package refactor")
