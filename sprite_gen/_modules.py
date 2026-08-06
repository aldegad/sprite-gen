# SPDX-License-Identifier: Apache-2.0
"""Domain of each pipeline module — the single source for the package taxonomy.

Used at runtime to run a pipeline step as `-m sprite_gen.<domain>.<step>` and by
the reorg tooling. Keep in sync with the physical folder layout.
"""

MODULE_DOMAIN = {
    'runio': 'spec',
    'layout': 'spec',
    'migrate_request': 'spec',
    'migrate_breathe': 'spec',
    'generate_image': 'gen',
    'prepare': 'gen',
    'extract': 'frames',
    'cutout': 'frames',
    'segment': 'frames',
    'slice_sheet': 'frames',
    'check_visible_magenta': 'frames',
    'unpack_atlas': 'frames',
    'curation': 'curate',
    'anchor': 'curate',
    'compose_atlas': 'compose',
    'compose_cycle': 'compose',
    'compose_gif': 'compose',
    'compose_layers': 'compose',
    'layers': 'compose',
    'export_pngs': 'compose',
    'breathe': 'effects',
    'anatomy': 'effects',
    'recolor': 'effects',
    'interpolate': 'effects',
    'reroll': 'effects',
    'inspect': 'qa',
    'score': 'qa',
    'correction_loop': 'qa',
    'preview': 'qa',
    'serve_curation': 'serve',
    'serve_compose': 'serve',
    'gif_utils': 'util',
}


def qualified(name: str) -> str:
    """Fully-qualified module path for a pipeline step basename."""
    return f"sprite_gen.{MODULE_DOMAIN[name]}.{name}"
