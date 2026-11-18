# SPDX-License-Identifier: Apache-2.0
"""The paths and keys the screens send to their servers are declared once.

The other direction from tests/serve/test_curator_wire_keys.py: there the server writes a body
and the screen reads it; here the screen (`curator/src/*.js`, `composer/src/*.js`) builds a
request and the server routes on its path and reads its keys. A path or key spelt one letter
apart on the two sides is no error anywhere — the server answers 404, or reads `None` and
quietly takes its default (`duration_ms` -> `duration_mss` makes every compare GIF 100 ms a
frame; `t` -> `tt` makes every in-between the midpoint), and every test stayed green. So each
server declares its paths (`Route`), the keys it is asked with (`Ask`) and the values it
branches on, and this file holds both sides to them:

1. Every request a script sends is found in the comment-stripped scripts **whole**: the path
   and the keys of one call in one pattern, built from the declared values, so a key cannot
   drift onto another call's line.
2. Every declared path and key is either sent that way or listed as not sent here.
3. The scripts call no `/api/` path that is not declared.
4. The servers route on no path, and read no asked key, as a bare string, and every name they
   use exists (`do_POST`'s catch-all turns a misspelt name into a quiet 500).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from sprite_gen.serve import serve_compose, serve_curation
from test_curator_wire_keys import COMPOSER_JS, CURATOR_JS, _undeclared

A = serve_curation.Ask
R = serve_curation.Route
CA = serve_compose.Ask
CR = serve_compose.Route
C = serve_compose.Wire

# A JSON POST's options up to its body, as every script writes them.
POST = r'\{\s*method: "POST",\s*headers: \{ "Content-Type": "application/json" \},\s*body: '


def _e(text: str) -> str:
    return re.escape(text)


def _declared(namespace: type) -> dict[str, str]:
    return {name: value for name, value in vars(namespace).items() if name.isupper() and isinstance(value, str)}


# ── curator ──────────────────────────────────────────────────────────

# One entry per call: (path, the asked keys it carries, the call as written). The pattern names
# every key it carries, so dropping one from the tuple below without the pattern is a test bug,
# and `test_every_curator_ask_is_sent_or_listed_unsent` sees each key's sender.
CURATOR_SENDS: list[tuple[str, tuple[str, ...], str]] = [
    (R.RUN, (), rf'fetch\("{_e(R.RUN)}"\)'),
    (R.OP_PROGRESS, (), rf'fetch\("{_e(R.OP_PROGRESS)}"\)'),
    (R.PROGRESS, (), rf'fetch\("{_e(R.PROGRESS)}"\)'),
    (R.BASE_GRID, (), rf'fetch\("{_e(R.BASE_GRID)}"\)'),
    (R.COMPOSE, (), rf'fetch\("{_e(R.COMPOSE)}", \{{ method: "POST" \}}\)'),
    (R.CURATION, (), rf'fetch\("{_e(R.CURATION)}", {POST}JSON\.stringify\(buildPayload\(\)\)'),
    (R.BREATHE_ANATOMY, (A.STATE, A.RIGID_ROW, A.AXIS_X, A.TORSO_HALF),
     rf'new URLSearchParams\(\{{ {A.STATE}: stateName \}}\);'
     rf'\s*if \(rigidRow != null\) q\.set\("{A.RIGID_ROW}", String\(rigidRow\)\);'
     rf'\s*if \(axisX != null\) q\.set\("{A.AXIS_X}", String\(axisX\)\);'
     rf'\s*if \(torsoHalf != null\) q\.set\("{A.TORSO_HALF}", String\(torsoHalf\)\);'
     rf'\s*const res = await fetch\(`{_e(R.BREATHE_ANATOMY)}\?\$\{{q\}}`\)'),
    (R.COMPARE_GIF, (A.FRAMES, A.DURATION_MS, A.FORMAT),  # the compare view
     rf'fetch\("{_e(R.COMPARE_GIF)}", {POST}JSON\.stringify\(\{{ {A.FRAMES}, {A.DURATION_MS}: step, {A.FORMAT}: fmt \}}\)'),
    (R.COMPARE_GIF, (A.FRAMES, A.DURATION_MS, A.FORMAT),  # one row's video
     rf'fetch\("{_e(R.COMPARE_GIF)}", {POST}JSON\.stringify\(\{{\s*{A.FRAMES},'
     rf'\s*{A.DURATION_MS}: Math\.round\(1000 / Math\.max\(1, \(st && st\.fps\) \|\| 6\)\),\s*{A.FORMAT}: fmt,\s*\}}\)'),
    (R.BASE_EDIT, (A.OPS, A.SPACE, A.TRANSFORM),
     rf'fetch\("{_e(R.BASE_EDIT)}", {POST}JSON\.stringify\(\{{ {A.OPS}, {A.SPACE}: "{serve_curation.SPACE_LOGICAL}", '
     rf'{A.TRANSFORM}: trDirty \? tr : null \}}\)'),
    (R.STATE_FPS, (A.STATE, A.FPS),
     rf'fetch\("{_e(R.STATE_FPS)}", {POST}JSON\.stringify\(\{{ {A.STATE}: stateName, {A.FPS}: clamped \}}\)'),
    (R.INTERPOLATE, (A.STATE, A.FROM, A.TO, A.T, A.PROVIDER),
     rf'url: "{_e(R.INTERPOLATE)}",\s*body: \{{\s*{A.STATE}: stateName,\s*{A.FROM}: parseInt\(fromInput\.value, 10\),'
     rf'\s*{A.TO}: parseInt\(toInput\.value, 10\),\s*{A.T}: parseFloat\(tInput\.value\),\s*{A.PROVIDER}: providerSel\.value,\s*\}}'),
    (R.REROLL, (A.STATE, A.PROVIDER),
     rf'url: "{_e(R.REROLL)}",\s*body: \{{ {A.STATE}: stateName, {A.PROVIDER}: providerSel\.value \}}'),
    (R.DOWNLOAD, (), rf'fetch\(`{_e(R.DOWNLOAD)}\$\{{kind\}}`\)'),
    (R.DOWNLOAD, (A.STATE,),  # one row's GIF, chosen from the row's format menu
     rf'if \(fmt === "{serve_curation.FORMAT_GIF}"\) \{{\s*await downloadArtifact\('
     rf'`{serve_curation.DOWNLOAD_GIF}\?{A.STATE}=\$\{{encodeURIComponent\(stateName\)\}}`'),
]
# The downloads the export buttons ask for, and the formats the two download menus offer.
CURATOR_VALUES = (
    [rf'\["[\w-]+", "{kind}", \(\) =>' for kind in (serve_curation.DOWNLOAD_ATLAS, serve_curation.DOWNLOAD_PNGS,
                                                    serve_curation.DOWNLOAD_GIFS)]
    + [rf'<button type="button" data-fmt="{fmt}">' for fmt in serve_curation.COMPARE_FORMATS]
)
# Spelt out, not named: what no script sends is held to its declaration by these lists alone.
# Asked keys no script sends: the chip URL carrying `direction` is the server's own
# (`_anchor_views`), `scale` falls back to the route's default, and `label` names an in-between
# for callers other than the curator.
CURATOR_UNSENT_KEYS = {"direction", "scale", "label"}
# Paths no script calls: the anchor chip's URL is the server's own; the two exports are called by
# no script (the export buttons go through /download/ instead).
CURATOR_UNCALLED = {"/api/anchor", "/api/export", "/api/export-gif"}
# Values no script sends: base-edit ops in raw pixels, the space a body naming none is in.
CURATOR_UNSENT_VALUES = {"raw"}


def _missing(sends, scripts: str) -> list[str]:
    return [pattern for _route, _keys, pattern in sends if not re.search(pattern, scripts)]


def test_the_curator_sends_every_request_as_declared():
    missing = _missing(CURATOR_SENDS, CURATOR_JS) + [p for p in CURATOR_VALUES if not re.search(p, CURATOR_JS)]
    assert not missing, ("the curator no longer sends these requests as declared — a path, key or value "
                         "renamed on one side only:\n" + "\n".join(missing))


def test_every_curator_ask_is_sent_or_listed_unsent():
    declared = set(_declared(A).values())
    sent = {key for _route, keys, _pattern in CURATOR_SENDS for key in keys}
    assert not sent & CURATOR_UNSENT_KEYS
    assert declared == sent | CURATOR_UNSENT_KEYS, (
        f"declared but neither sent nor listed unsent: {sorted(declared - sent - CURATOR_UNSENT_KEYS)}; "
        f"listed but not declared: {sorted((sent | CURATOR_UNSENT_KEYS) - declared)}")
    assert A.ANATOMY_OVERRIDES == (A.RIGID_ROW, A.AXIS_X, A.TORSO_HALF)


def test_every_curator_route_is_called_or_listed_uncalled():
    declared = list(_declared(R).values())
    assert len(declared) == len(set(declared))
    called = {route for route, _keys, _pattern in CURATOR_SENDS}
    assert not called & CURATOR_UNCALLED
    assert set(declared) == called | CURATOR_UNCALLED, (
        f"declared but neither called nor listed: {sorted(set(declared) - called - CURATOR_UNCALLED)}; "
        f"listed but not declared: {sorted((called | CURATOR_UNCALLED) - set(declared))}")


def test_every_curator_value_is_sent_or_listed_unsent():
    sent = {serve_curation.SPACE_LOGICAL, serve_curation.FORMAT_GIF, serve_curation.DOWNLOAD_GIF,
            serve_curation.DOWNLOAD_ATLAS, serve_curation.DOWNLOAD_PNGS, serve_curation.DOWNLOAD_GIFS,
            *serve_curation.COMPARE_FORMATS}
    declared = set(serve_curation.COMPARE_FORMATS) | set(serve_curation.OPS_SPACES) | set(serve_curation.DOWNLOADS)
    assert declared == sent | CURATOR_UNSENT_VALUES


def _api_literals(scripts: str) -> set[str]:
    return set(re.findall(r'["`](/api/[\w-]+)', scripts))


def test_the_screens_call_no_undeclared_path():
    for scripts, routes in ((CURATOR_JS, R), (COMPOSER_JS, CR)):
        stray = _api_literals(scripts) - set(_declared(routes).values())
        assert not stray, f"paths the scripts call that the server does not declare: {sorted(stray)}"


# ── the servers ──────────────────────────────────────────────────────

def _handler_reads(source: str, roots: set[str]) -> list[tuple[int, str]]:
    """(line, key) of every str literal read from one of `roots` (`x.get("k")`, `x["k"]`,
    `"k" in x`) — `roots` are the request objects a handler reads."""

    def root(node: ast.AST) -> str:
        while isinstance(node, (ast.Subscript, ast.Attribute, ast.Call)):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    return node.func.id + "()"
                node = node.func.value if isinstance(node.func, ast.Attribute) else node.func
                continue
            node = node.value
        return node.id if isinstance(node, ast.Name) else ""

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) \
                and root(node.func.value) in roots:
            found.append((node.lineno, node.args[0].value))
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str) and root(node.value) in roots:
            found.append((node.lineno, node.slice.value))
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str) \
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops) \
                and any(root(c) in roots for c in node.comparators):
            found.append((node.lineno, node.left.value))
    return found


def _route_literals(source: str, routes: type) -> list[tuple[int, str]]:
    """(line, text) of every string, f-string part included, outside `routes` itself that spells
    an API path."""
    tree = ast.parse(source)
    inside = {id(n) for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef) and cls.name == routes.__name__
              for n in ast.walk(cls)}
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inside
            and (n.value.startswith("/api/") or n.value == "/download/")]


def _values_as_text(source: str, owners: set[str], values: set[str]) -> list[tuple[int, str]]:
    """(line, value) of every bare str literal inside the named defs that is one of `values`."""
    tree = ast.parse(source)
    found = []
    for owner in ast.walk(tree):
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) and owner.name in owners:
            parts = {id(v) for j in ast.walk(owner) if isinstance(j, ast.JoinedStr) for v in j.values}
            found += [(n.lineno, n.value) for n in ast.walk(owner)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in values
                      and id(n) not in parts]
    return found


CURATOR_SOURCE = Path(serve_curation.__file__).read_text(encoding="utf-8")
COMPOSER_SOURCE = Path(serve_compose.__file__).read_text(encoding="utf-8")


def test_serve_curation_routes_and_reads_its_asks_by_name():
    assert not _route_literals(CURATOR_SOURCE, R), \
        f"paths spelt as text in serve_curation.py — name them in `Route`: {_route_literals(CURATOR_SOURCE, R)}"
    # `existing` is the sidecar on disk and `slot` one of its rows, read by `curate.curation.Sidecar`.
    reads = _handler_reads(CURATOR_SOURCE, {"payload", "query", "existing", "slot", "prev_entry"})
    assert not reads, f"request or sidecar keys read as text in serve_curation.py: {reads}"
    values = set(serve_curation.COMPARE_FORMATS) | set(serve_curation.OPS_SPACES) | set(serve_curation.DOWNLOADS)
    branched = _values_as_text(CURATOR_SOURCE, {"do_GET", "do_POST", "build_download"}, values)
    assert not branched, f"asked values branched on as text in serve_curation.py: {branched}"


def test_serve_compose_routes_and_reads_its_asks_by_name():
    assert not _route_literals(COMPOSER_SOURCE, CR), \
        f"paths spelt as text in serve_compose.py — name them in `Route`: {_route_literals(COMPOSER_SOURCE, CR)}"
    reads = _handler_reads(COMPOSER_SOURCE, {"payload", "query", "row", "cell", "parse_qs()"})
    assert not reads, f"request keys read as text in serve_compose.py: {reads}"
    branched = _values_as_text(COMPOSER_SOURCE, {"do_POST", "native_pick"}, set(serve_compose.PICK_KINDS))
    assert not branched, f"pick kinds branched on as text in serve_compose.py: {branched}"
    assert set(serve_compose._PICK_SCRIPTS) == set(serve_compose.PICK_KINDS)


def test_every_route_and_ask_name_the_servers_use_is_declared():
    for source, namespaces in ((CURATOR_SOURCE, (A, R)), (COMPOSER_SOURCE, (CA, CR))):
        for namespace in namespaces:
            assert not _undeclared(source, namespace), \
                f"{namespace.__qualname__} names that do not exist: {_undeclared(source, namespace)}"


# ── composer ─────────────────────────────────────────────────────────

COMPOSER_SENDS: list[tuple[str, tuple[str, ...], str]] = [
    (CR.STATE, (), rf'fetch\("{_e(CR.STATE)}"\)'),
    (CR.MOUNT, (CA.DIR,), rf'fetch\("{_e(CR.MOUNT)}", {POST}JSON\.stringify\(\{{ {CA.DIR} \}}\)'),
    (CR.PICK, (CA.KIND,),
     rf'fetch\(`{_e(CR.PICK)}\?{CA.KIND}=\$\{{encodeURIComponent\(kind\)\}}`, \{{ method: "POST" \}}\)'),
    (CR.BROWSE, (CA.DIR,),
     rf'dir \? `{_e(CR.BROWSE)}\?{CA.DIR}=\$\{{encodeURIComponent\(dir\)\}}` : "{_e(CR.BROWSE)}"'),
    (CR.BROWSE_IMG, (CA.PATH,), rf'`{_e(CR.BROWSE_IMG)}\?{CA.PATH}=\$\{{encodeURIComponent\(path\)\}}`'),
    (CR.BUILD, (CA.OUT_DIR, CA.ROWS),
     rf'fetch\("{_e(CR.BUILD)}", {POST}JSON\.stringify\(\{{ {CA.OUT_DIR}, {CA.ROWS} \}}\)'),
    # the rows of /api/build (`session.js` sessionBuildRows); a cell's `name` is the screen's own
    (CR.BUILD, (CA.NAME, CA.CELLS, CA.PATH),
     rf'\(\{{ {CA.NAME}: r\.name, {CA.CELLS}: r\.cells\.map\(\(c\) => \(\{{ {CA.PATH}: c\.path, name: c\.name \}}\)\) \}}\)'),
    (CR.OPEN_CURATION, (), rf'fetch\("{_e(CR.OPEN_CURATION)}", {POST}JSON\.stringify\(\{{ {C.RUN_DIR} \}}\)'),
]
COMPOSER_VALUES = [rf'\{{ kind: "{kind}", labelKey: ' for kind in serve_compose.PICK_KINDS]


def test_the_composer_sends_every_request_as_declared():
    missing = _missing(COMPOSER_SENDS, COMPOSER_JS) + [p for p in COMPOSER_VALUES if not re.search(p, COMPOSER_JS)]
    assert not missing, ("the composer no longer sends these requests as declared:\n" + "\n".join(missing))


def test_every_composer_ask_and_route_is_sent():
    sent = {key for _route, keys, _pattern in COMPOSER_SENDS for key in keys}
    assert set(_declared(CA).values()) == sent
    routes = list(_declared(CR).values())
    assert len(routes) == len(set(routes))
    assert set(routes) == {route for route, _keys, _pattern in COMPOSER_SENDS}
