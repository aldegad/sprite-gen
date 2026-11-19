# SPDX-License-Identifier: Apache-2.0
"""The keys the servers put on the wire and the screens read are declared once.

`serve_curation.py` builds every JSON body the curator (`curator/src/*.js`) reads, and
`serve_compose.py` every body the composer (`composer/src/*.js`) reads. A key spelt one
letter apart on the two sides is no error anywhere: the screen reads `undefined` and
quietly shows nothing (`extractOk` -> `extractOK` left every test green). So each server
declares its keys once (`Wire`), writes every body through those names, and this file
holds the screen to them:

1. Every key the screen reads is found in the comment-stripped scripts **with the object
   it is read from**, built from the declared value. Looking for the bare token is not
   enough: `cell` -> `cells`, `takes` -> `take`, `anchor` -> `anchors`, `sheet` -> `sheets`,
   `defaults` -> `default`, `breaths` -> `breath` and `dir` -> `dirs` each land on a word
   the scripts already use for something else.
2. Every declared key is either read that way or listed as unread here — a new key has to
   say which.
3. The servers write no key as a bare string literal (dict key or subscript store), apart
   from the other vocabularies listed below, and every `Wire.X` they name exists. The
   second half matters because the curation server's `do_POST` catch-all turns a
   misspelt name into a quiet HTTP 500 on routes no other test calls.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

from sprite_gen.curate.curation import IMPORTED_REF_ROLES
from sprite_gen.frames import extract
from sprite_gen.serve import serve_compose, serve_curation
from test_curator_single_display_pipeline import _no_comment

W = serve_curation.Wire
C = serve_compose.Wire


def _scripts(src: Path) -> str:
    return "\n".join(_no_comment(p.read_text(encoding="utf-8"), "js") for p in sorted(src.glob("*.js")))


CURATOR_JS = _scripts(serve_curation.CURATOR_DIR / "src")
COMPOSER_JS = _scripts(serve_compose.COMPOSER_DIR / "src")


def _member(parent: str, *keys: str) -> str:
    """`parent.key[.key]` as a whole read — the object the value is read from, then the key."""
    return r"\b" + re.escape(parent) + "".join(r"\." + re.escape(key) for key in keys) + r"\b"


# ── curator ──────────────────────────────────────────────────────────

# Key -> where the curator reads it (one pattern per object it is read from).
CURATOR_READS: dict[str, tuple[str, ...]] = {
    # /api/run
    W.CHARACTER_ID: (_member("run", W.CHARACTER_ID),),
    W.RUN_DIR: (_member("run", W.RUN_DIR),),
    W.BASE_URL: (_member("run", W.BASE_URL),),
    W.CELL: (_member("run", W.CELL, W.WIDTH),),
    W.PIXEL_UNFAKE: (_member("run", W.PIXEL_UNFAKE, W.SCALE),),
    W.SCHEMA_VERSION: (_member("run", W.SCHEMA_VERSION),),
    W.RUN_REVISION: (_member("run", W.RUN_REVISION), _member("next", W.RUN_REVISION)),
    W.REQUEST_STAMP: (_member("run", W.REQUEST_STAMP),),
    W.DIRECTION_GROUPS: (_member("run", W.DIRECTION_GROUPS),),
    W.ANCHOR_FILES: (_member("run", W.ANCHOR_FILES),),
    W.STATES: (_member("run", W.STATES), _member("next", W.STATES), _member("group", W.STATES)),
    W.CURATION: (_member("run", W.CURATION),),
    W.CURATION_DROPPED: (_member("run", W.CURATION_DROPPED),),
    W.ISO: (_member("run", W.ISO),),
    W.LANG: (_member("run", W.LANG),),
    W.HAS_ATLAS: (_member("run", W.HAS_ATLAS),),
    W.ATLAS: (_member("run", W.ATLAS),),
    W.RECOLOR: (_member("run", W.RECOLOR),),
    W.FIT_PIXEL_UNFAKE: (_member("run", W.FIT_PIXEL_UNFAKE),),
    W.HEAL: (_member("run", W.HEAL),),
    # cell, pixelUnfake
    W.WIDTH: (_member("run", W.CELL, W.WIDTH),),
    W.HEIGHT: (_member("run", W.CELL, W.HEIGHT),),
    W.SCALE: (_member("run", W.PIXEL_UNFAKE, W.SCALE),),
    W.LABEL: (_member("run", W.PIXEL_UNFAKE, W.LABEL), _member("frame", W.LABEL)),
    # states[]
    W.NAME: (_member("state", W.NAME), _member("p", W.NAME), _member("r", W.NAME), _member("a", W.NAME),
             _member("v", W.NAME), _member("info", W.BASE, W.NAME)),
    W.RAW_PRESENT: (_member("state", W.RAW_PRESENT),),
    W.PIXEL_SCALE: (_member("st", W.PIXEL_SCALE),),
    W.REFS: (_member("state", W.REFS),),
    W.FPS: (_member("state", W.FPS),),
    W.LOOP: (_member("state", W.LOOP),),
    W.ACTION: (_member("state", W.ACTION),),
    W.REQUEST_FRAMES: (_member("state", W.REQUEST_FRAMES),),
    W.EXTRACT_OK: (_member("state", W.EXTRACT_OK),),
    W.FRAMES: (_member("st", W.FRAMES), _member("p", W.FRAMES)),
    W.TAKES: (_member("st", W.TAKES),),
    # states[].frames[]
    W.INDEX: (_member("frame", W.INDEX),),
    W.URL: (_member("frame", W.URL), _member("r", W.URL), _member("a", W.URL), _member("v", W.URL),
            _member("info", W.URL), _member("info", W.BASE, W.URL), _member("run", W.ATLAS, W.URL)),
    W.PRESENT: (_member("frame", W.PRESENT), _member("v", W.PRESENT), _member("info", W.BASE, W.PRESENT)),
    W.PLAIN_URL: (_member("frame", W.PLAIN_URL),),
    W.STAMP: (_member("frame", W.STAMP),),
    W.PLAIN_BAKE_URL: (_member("frame", W.PLAIN_BAKE_URL),),
    W.PLAIN_BAKE_STAMP: (_member("frame", W.PLAIN_BAKE_STAMP),),
    W.INPUT_GRID: (_member("frame", W.INPUT_GRID),),
    W.CONTENT_SIZE: (_member("frame", W.CONTENT_SIZE),),
    W.CONTENT_BOX: (_member("frame", W.CONTENT_BOX),),
    # states[].refs[]
    W.ROLE: (_member("r", W.ROLE),),
    W.ANCHOR_FRAME: (_member("r", W.ANCHOR_FRAME),),
    # directionGroups[]
    W.DIRECTION: (_member("group", W.DIRECTION),),
    W.ANCHOR: (_member("group", W.ANCHOR),),
    W.ANCHOR_ERROR: (_member("g", W.ANCHOR_ERROR),),
    W.ANCHOR_PENDING: (_member("g", W.ANCHOR_PENDING),),
    W.ANCHOR_URL: (_member("g", W.ANCHOR_URL),),
    W.MIRROR_OF: (_member("group", W.MIRROR_OF),),
    # atlas
    W.MTIME: (_member("info", W.MTIME),),
    W.MANIFEST_URL: (_member("info", W.MANIFEST_URL), _member("v", W.MANIFEST_URL)),
    # recolor
    W.DIR: (_member("run", W.RECOLOR, W.DIR),),
    W.SWATCH: (_member("info", W.SWATCH),),
    W.MATCH: (_member("info", W.MATCH),),
    W.TOLERANCE: (_member("info", W.TOLERANCE),),
    W.BASE: (_member("info", W.BASE, W.URL),),
    W.VARIANTS: (_member("run", W.RECOLOR, W.VARIANTS), _member("info", W.VARIANTS)),
    W.PICKED: (_member("runState", W.RECOLOR, W.PICKED), _member("info", W.PICKED)),
    W.PICKED_KNOWN: (_member("info", W.PICKED_KNOWN),),
    W.X: (_member("swatch", W.X),),
    W.Y: (_member("swatch", W.Y),),
    W.W: (_member("swatch", W.W),),
    W.H: (_member("swatch", W.H),),
    W.SHEET_WIDTH: (_member("swatch", W.SHEET_WIDTH),),
    W.SHEET: (_member("v", W.SHEET),),
    W.SUBSTITUTED_PIXELS: (_member("v", W.SUBSTITUTED_PIXELS),),
    W.PASSTHROUGH_PIXELS: (_member("v", W.PASSTHROUGH_PIXELS),),
    W.UNUSED_SOURCES: (_member("v", W.UNUSED_SOURCES),),
    # replies
    W.ERROR: (_member("run", W.ERROR), _member("data", W.ERROR)),
    W.BUSY: (_member("run", W.BUSY), _member("data", W.BUSY)),
    W.PROGRESS: (_member("data", W.PROGRESS),),
    # /api/progress
    W.RAW: (_member("p", W.RAW),),
    W.RAW_URL: (_member("p", W.RAW_URL),),
    W.FRAME0_URL: (_member("p", W.FRAME0_URL),),
    W.REL_RAW: (f'"{W.REL_RAW}"',),
    W.REL_FRAMES: (f'"{W.REL_FRAMES}"',),
    # /api/breathe-anatomy
    W.ANATOMY: (rf"\{{ {W.ANATOMY}, {W.DEFAULTS} \}} = await fetchBreatheAnatomy\(",),
    W.DEFAULTS: (rf"\{{ {W.ANATOMY}, {W.DEFAULTS} \}} = await fetchBreatheAnatomy\(",),
    W.DEPTH: (_member("defaults", W.DEPTH),),
    W.LAG: (_member("defaults", W.LAG),),
    W.BREATHS: (_member("defaults", W.BREATHS),),
    # /api/base-grid
    W.GRID: (rf'fetch\("{re.escape(serve_curation.Route.BASE_GRID)}"\)\)\.json\(\)\)\.{W.GRID}\b',),
    W.X_EDGES: (_member("grid", W.X_EDGES),),
    W.Y_EDGES: (_member("grid", W.Y_EDGES),),
    # POST results
    W.OK: (_member("data", W.OK),),
    W.STDERR: (_member("data", W.STDERR),),
    W.APPLIED: (_member("data", W.APPLIED),),
    # download header
    W.FILENAME_HEADER: (rf'headers\.get\("{W.FILENAME_HEADER}"\)',),
}

# On the wire, read by no script (dropping one changes the response bytes and any outside
# caller, so they stay; listed here so a new key has to say which kind it is).
# Spelt out, not named: a key no script reads is held to its declaration by this list alone.
CURATOR_UNREAD = {
    "safeMarginX", "safeMarginY", "logicalHeight", "source", "state", "anchorErrorCode",
    "contract", "refsStates", "sourceless", "sheetHeight", "passthroughColorCount", "size",
    "path", "note", "pitch", "returncode", "stdout", "export", "gif", "space", "backup",
}

# Keys other modules own that reach the curator inside a body: the heal report
# (`extract.heal_run`, relayed as `run.heal`) and the progress file (`extract._write_progress`,
# relayed as `progress`), plus the values the screen branches on.
CURATOR_READS_OF_OTHERS: dict[str, tuple[str, ...]] = {
    extract.HEAL_HEALED: (_member("run", W.HEAL, extract.HEAL_HEALED),),
    extract.HEAL_KEPT_STALE: (_member("run", W.HEAL, extract.HEAL_KEPT_STALE),),
    extract.HEAL_FAILED: (_member("run", W.HEAL, extract.HEAL_FAILED),),
    extract.PROGRESS_DONE: (_member("p", extract.PROGRESS_DONE),),
    extract.PROGRESS_TOTAL: (_member("p", extract.PROGRESS_TOTAL),),
    extract.PROGRESS_LABEL: (_member("p", extract.PROGRESS_LABEL),),
    extract.PROGRESS_PHASE: (rf'\bp\.{extract.PROGRESS_PHASE} === "{extract.PROGRESS_PHASE_COMPONENTS}"',),
    **{f"ref_{role}": (rf"\bref_{role}:",) for role in IMPORTED_REF_ROLES},
}
# The heal report's and progress file's keys no script reads, spelt out as `CURATOR_UNREAD` is.
EXTRACT_UNREAD = {"op", "ts", "frozen", "notes"}

# Dict keys and stores in serve_curation.py that are not on the curator wire: other
# vocabularies the module also writes. Dicts by the def (or module table) they are in,
# stores by the name of the object they write into; "*" = every key.
CURATOR_OTHER_DICTS = {
    ("CONTENT_TYPES", "*"),                # file suffix -> HTTP Content-Type
    ("_heal_state", "*"),                  # the heal thread's slots, server-side only
    ("_breathe_source_frame", "rows"),     # frames-manifest default
    ("_build_run_state_impl", "rows"),     # frames-manifest default
    ("_anchor_views", "url"),              # the inner anchor view, read back by _state_refs
}
CURATOR_OTHER_STORES = {
    ("_heal_state", "*"),
    ("request", "fps"),                    # sprite-request.json
}


def _owner(stack: list[ast.AST]) -> str:
    """The innermost def as a dotted name (classes included), or "" at module level."""
    return ".".join(n.name for n in stack if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))


def _root_name(node: ast.AST) -> str:
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _raw_keys(source: str, other_dicts: set, other_stores: set) -> list[tuple[int, str, str]]:
    """(line, key, source of the dict or store) for every str literal written as a dict key or
    subscript store that is not one of the listed other vocabularies."""
    tree = ast.parse(source)
    found: list[tuple[int, str, str]] = []

    def listed(entries: set, owner: str, key: str) -> bool:
        return (owner, key) in entries or (owner, "*") in entries

    def visit(node: ast.AST, stack: list[ast.AST], table: str) -> None:
        if isinstance(node, ast.Dict):
            owner = table or _owner(stack)
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                        and not listed(other_dicts, owner, key.value):
                    found.append((key.lineno, key.value, ast.get_source_segment(source, node)))
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) \
                and not listed(other_stores, _root_name(node.value), node.slice.value):
            found.append((node.lineno, node.slice.value, ast.get_source_segment(source, node)))
        inner = stack + [node] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else stack
        for child in ast.iter_child_nodes(node):
            child_table = table
            if not stack and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                child_table = next((t.id for t in targets if isinstance(t, ast.Name)), table)
            visit(child, inner, child_table)

    visit(tree, [], "")
    return found


def _undeclared(source: str, namespace: type) -> list[tuple[int, str]]:
    return [(node.lineno, node.attr) for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == namespace.__name__ and not hasattr(namespace, node.attr)]


def _declared(namespace: type) -> dict[str, str]:
    return {name: value for name, value in vars(namespace).items() if name.isupper()}


def _missing_reads(reads: dict[str, tuple[str, ...]], scripts: str) -> list[str]:
    return [f"{key}: {pattern}" for key, patterns in reads.items() for pattern in patterns
            if not re.search(pattern, scripts)]


def test_the_curator_reads_every_key_from_the_object_the_server_puts_it_on():
    missing = _missing_reads({**CURATOR_READS, **CURATOR_READS_OF_OTHERS}, CURATOR_JS)
    assert not missing, ("the curator no longer reads these keys as declared — a key renamed "
                         "on one side only:\n" + "\n".join(missing))


def test_every_curator_key_is_read_or_listed_as_unread():
    declared = _declared(W)
    values = list(declared.values())
    assert len(values) == len(set(values)), \
        f"one key declared twice: {[v for v, n in Counter(values).items() if n > 1]}"
    assert not set(CURATOR_READS) & CURATOR_UNREAD
    assert set(values) == set(CURATOR_READS) | CURATOR_UNREAD, (
        f"declared but neither read nor listed unread: {sorted(set(values) - set(CURATOR_READS) - CURATOR_UNREAD)}; "
        f"listed but not declared: {sorted(set(CURATOR_READS) | CURATOR_UNREAD - set(values))}")


def test_every_heal_and_progress_key_is_read_or_listed_as_unread():
    declared = {name: value for name, value in vars(extract).items()
                if name.startswith(("HEAL_", "PROGRESS_")) and name not in ("PROGRESS_REL", "PROGRESS_PHASE_COMPONENTS")}
    keys = set(CURATOR_READS_OF_OTHERS) | EXTRACT_UNREAD
    assert set(declared.values()) == keys - {f"ref_{role}" for role in IMPORTED_REF_ROLES}, (
        f"declared but neither read nor listed unread: {sorted(set(declared.values()) - keys)}")


def test_serve_curation_writes_no_wire_key_as_text():
    source = Path(serve_curation.__file__).read_text(encoding="utf-8")
    raw = _raw_keys(source, CURATOR_OTHER_DICTS, CURATOR_OTHER_STORES)
    assert not raw, (f"{len(raw)} keys written as text in serve_curation.py — name them in `Wire` "
                     "(or, if not on the wire, list them above):\n"
                     + "\n".join(f"  {line}: {key!r}" for line, key, _ in raw))
    headers = [(node.lineno, node.args[0].value) for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "send_header"
               and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in _declared(W).values()]
    assert not headers, f"headers the curator reads sent as text — name them in `Wire`: {headers}"


def test_every_wire_name_the_servers_use_is_declared():
    for module, namespace in ((serve_curation, W), (serve_compose, C)):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not _undeclared(source, namespace), \
            f"{module.__name__}: Wire names that do not exist: {_undeclared(source, namespace)}"


# ── composer ─────────────────────────────────────────────────────────

COMPOSER_READS: dict[str, tuple[str, ...]] = {
    C.DIR: (_member("picked", C.DIR),),
    C.ENTRIES: (_member("data", C.ENTRIES),),
    C.NAME: (_member("entry", C.NAME),),
    C.PATH: (_member("entry", C.PATH),),
    C.TYPE: (rf'\bentry\.{C.TYPE} === "{serve_compose.TYPE_DIR}"',),
    C.LANG: (_member("state", C.LANG),),
    C.MOUNT: (_member("state", C.MOUNT), _member("data", C.MOUNT)),
    C.CANCELLED: (_member("picked", C.CANCELLED),),
    C.RUN_DIR: (_member("data", C.RUN_DIR),),
    C.STATES: (_member("data", C.STATES),),
    C.FRAMES: (_member("data", C.FRAMES),),
    C.URL: (_member("data", C.URL),),
    C.ERROR: (_member("data", C.ERROR),),
}
COMPOSER_UNREAD = {"files", "cell", "code"}  # spelt out, as CURATOR_UNREAD
COMPOSER_OTHER_DICTS = {
    ("CONTENT_TYPES", "*"),                # file suffix -> HTTP Content-Type
    ("_PICK_SCRIPTS", "*"),                # pick kind -> osascript
    ("_build_groups", "*"),                # import_png_groups' group shape
}


def test_the_composer_reads_every_key_from_the_object_the_server_puts_it_on():
    missing = _missing_reads(COMPOSER_READS, COMPOSER_JS)
    assert not missing, ("the composer no longer reads these keys as declared:\n" + "\n".join(missing))


def test_every_composer_key_is_read_or_listed_as_unread():
    values = list(_declared(C).values())
    assert len(values) == len(set(values))
    assert not set(COMPOSER_READS) & COMPOSER_UNREAD
    assert set(values) == set(COMPOSER_READS) | COMPOSER_UNREAD


def test_serve_compose_writes_no_wire_key_as_text():
    source = Path(serve_compose.__file__).read_text(encoding="utf-8")
    raw = _raw_keys(source, COMPOSER_OTHER_DICTS, set())
    assert not raw, (f"{len(raw)} keys written as text in serve_compose.py:\n"
                     + "\n".join(f"  {line}: {key!r}" for line, key, _ in raw))
