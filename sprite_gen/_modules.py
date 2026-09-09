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
    'video': 'gen',
    'gen_set': 'gen',
    'canvas': 'video',
    'frames': 'video',
    'loop': 'video',
    'batch': 'video',
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
    'export_aseprite': 'compose',
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


# Display order and one-line meaning of each domain — the taxonomy the CLI help and the
# scripts map enumerate from (the docs classification is catalogued in docs/README.md and
# tested against the file set). Adding a module to MODULE_DOMAIN puts it in its group;
# nothing else needs a hand edit.
DOMAINS: list[tuple[str, str]] = [
    ("gen", "Generation — prepare a run, generate stills / rows / clips"),
    ("video", "Video → loop — canvas, keyed frames, seamless cycle, batch"),
    ("frames", "Frames — chroma/white removal, row extraction, sheet slicing, atlas unpacking"),
    ("curate", "Curation — direction anchors and the curation sidecar"),
    ("compose", "Compose — runtime atlas, cycles, GIFs, layers, exports"),
    ("effects", "Post-processing — recolor, breathing, interpolation"),
    ("qa", "QA — inspect, score, preview, bounded correction loop"),
    ("serve", "Webviews — curation and composition canvases"),
    ("spec", "Spec — request migrations and run I/O"),
    ("util", "Utilities"),
]
DOMAIN_ORDER = [d for d, _ in DOMAINS]
DOMAIN_TITLE = dict(DOMAINS)


# The four pipelines — first-class objects. The CLI help, the docs index and the README
# pipeline table are checked against THIS list; a verb named here must exist as a verb.
PIPELINES: list[dict[str, object]] = [
    {"key": "A", "name": "atlas rows", "verbs": ["prepare", "gen", "gen-set", "extract", "curation", "compose-atlas"],
     "chain": "prepare → gen (or gen-set) → extract → curation → compose-atlas", "doc": "docs/run-contract.md"},
    {"key": "B", "name": "video → loop", "verbs": ["video-canvas", "video", "video-frames", "video-loop", "video-set"],
     "chain": "video-canvas → video → video-frames → video-loop, or video-set", "doc": "docs/video-pipeline.md"},
    {"key": "C", "name": "utilities", "verbs": ["cutout", "slice-sheet", "unpack-atlas"],
     "chain": "cutout · slice-sheet · unpack-atlas (each stands alone)", "doc": "docs/sheet-slicing.md"},
    {"key": "D", "name": "post-processing", "verbs": ["recolor", "recolor-palette", "compose-layers", "export-pngs", "export-aseprite"],
     "chain": "recolor · compose-layers · export-pngs · export-aseprite", "doc": "docs/recolor.md"},
]


def domain_of(module_path: str) -> str:
    """Domain of a fully-qualified module path (`sprite_gen.video.loop` → `video`).

    A domain package itself (`sprite_gen.gen`, whose `run` is the `gen` verb) is its own
    domain. Anything else must be a MODULE_DOMAIN entry — an unknown module is an error,
    not a silent "misc" bucket, so the taxonomy stays a single table.
    """
    parts = module_path.split(".")
    if len(parts) == 2 and parts[0] == "sprite_gen" and parts[1] in DOMAIN_TITLE:
        return parts[1]
    leaf = parts[-1]
    if leaf not in MODULE_DOMAIN:
        raise KeyError(f"{module_path} is not in sprite_gen._modules.MODULE_DOMAIN — add it there (single taxonomy table)")
    return MODULE_DOMAIN[leaf]


def qualified(name: str) -> str:
    """Fully-qualified module path for a pipeline step basename."""
    return f"sprite_gen.{MODULE_DOMAIN[name]}.{name}"
