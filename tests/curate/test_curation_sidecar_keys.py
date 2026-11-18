# SPDX-License-Identifier: Apache-2.0
"""The keys of `curation.json` are declared once (`curate.curation.Sidecar`) and the curator holds to them.

The sidecar crosses a language boundary twice: the curator SPA reads it from the run snapshot
(`store.js` `seedEntries`, `boot.js`) and writes it back on every autosave (`persistence.js`
`buildPayload`), and the server, the bake and the generation read it in Python. A key spelt one
letter apart on the two sides is no error anywhere: the screen drops the field it no longer
recognises on its next autosave, and the bake reads the default — at `main`, `clones` ->
`clone` in persistence.js (every clone gone after one save) and `frozen` -> `frozn` in the
server's carry-over each left every test green.

1. A sidecar built from the declared names, with every field the screen authors set, goes
   through the screen's own read and write in node and comes back with the same keys and
   values — so a name changed on either side drops a field.
2. The keys the screen reads outside that round trip (the anchor picks in `boot.js`, the re-pin
   in `store.js`) and the recolor pick it writes are found built from the declared values.
3. curation.py reads and writes no sidecar key as a bare string.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sprite_gen.curate import curation
from sprite_gen.curate.curation import CURATION_KIND, IDENTITY, SCHEMA_VERSION, Sidecar as S
from sprite_gen.serve import serve_curation
from sprite_gen.serve.serve_curation import CURATOR_DIR, Wire

# The scripts that read or write the sidecar outside the round trip below.
SCRIPTS = "\n".join((CURATOR_DIR / "src" / name).read_text(encoding="utf-8")
                    for name in ("boot.js", "store.js", "persistence.js"))

node = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 큐레이터 JS 를 실행할 수 없다")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const P = process.argv[2] + "/";
const input = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const sb = { console, document: { createElement: () => ({ getContext: () => ({}) }) },
  window: {}, setStatus: () => {}, t: () => "", fetch: () => {}, alert: () => {}, lang: "ko", STR: { ko: {} } };
sb.globalThis = sb; vm.createContext(sb);
for (const f of ["util.js", "store.js", "persistence.js", "breathe.js"]) {
  try { vm.runInContext(fs.readFileSync(P + f, "utf8"), sb); } catch (e) {}
}
vm.runInContext(`run = ${JSON.stringify(input.run)}; anchorPicks = ${JSON.stringify(input.anchorPicks)};`, sb);
sb.seedUnfakeState(input.run);
sb.seedEntries();
fs.writeFileSync(input.out, JSON.stringify(sb.buildPayload()));
"""

STATE = "idle"
# Frames 0..3 extracted, each with a twin (`plainUrl`), so the row's own pixel_unfake is the
# screen's to author; 4 is the clone of 1.
FRAMES = [{"index": i, "present": True, "plainUrl": f"/frames/{STATE}/orig/frame-{i}.png"} for i in range(4)]
TRANSFORM = {S.ROTATE: 12.5, S.SCALE: 1.25, S.DX: 3, S.DY: -2, S.SHX: 0.1, S.SHY: -0.2, S.FLIP_X: 1}
ROW = {
    S.SELECTED: [2, 0, 4],
    S.ORDER: [2, 0, 4, 1],
    S.DELETED: [3],
    S.CLONES: {"4": 1},
    S.UNLINKED: [4],
    S.NAMES: {"2": "blink"},
    S.TRANSFORMS: {"0": TRANSFORM},
    S.PIXELS: {"2": {"3,4": "#1f2430"}},
    S.PIXEL_UNFAKE: False,
    S.BREATHE: {"depth": 0.06, "breaths": 1, "lag": 0.1, "rigid_row": None, "anatomy": None},
}
SIDECAR = {S.VERSION: SCHEMA_VERSION, S.KIND: CURATION_KIND, S.PIXEL_UNFAKE: False, S.STATES: {STATE: ROW}}
ANCHOR = {S.STATE: STATE, S.INDEX: 2}


def _round_trip(tmp_path: Path) -> dict:
    harness = tmp_path / "h.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    run = {Wire.SCHEMA_VERSION: SCHEMA_VERSION, Wire.RUN_REVISION: "r1", Wire.CURATION: SIDECAR,
           Wire.STATES: [{Wire.NAME: STATE, Wire.FPS: 6, Wire.FRAMES: FRAMES}]}
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"run": run, "anchorPicks": {"down": ANCHOR}, "out": str(tmp_path / "out.json")}),
                   encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), str(harness), str(CURATOR_DIR / "src"), str(src)],
                          capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, f"node 하네스 실패:\n{proc.stderr}"
    return json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))


@node
def test_the_curator_reads_and_writes_back_every_sidecar_key(tmp_path):
    payload = _round_trip(tmp_path)
    assert set(payload) == {S.VERSION, S.KIND, S.STATES, S.ANCHORS, S.PIXEL_UNFAKE, Wire.RUN_REVISION}, \
        f"the saved document's keys are not the declared ones: {sorted(payload)}"
    assert (payload[S.VERSION], payload[S.KIND], payload[S.PIXEL_UNFAKE]) == (SCHEMA_VERSION, CURATION_KIND, False)
    assert payload[S.ANCHORS] == {"down": ANCHOR}
    row = payload[S.STATES][STATE]
    assert set(row) == set(ROW), (f"a row's keys did not survive the screen: lost {sorted(set(ROW) - set(row))}, "
                                  f"gained {sorted(set(row) - set(ROW))}")
    assert set(row[S.TRANSFORMS]["0"]) == set(IDENTITY), \
        f"a transform's keys are not curation.IDENTITY's: {sorted(row[S.TRANSFORMS]['0'])}"
    assert row == ROW


# The screen's reads and writes of the sidecar outside the round trip above.
SIDECAR_JS = [
    # boot.js seeds the anchor picks from run.curation.anchors, keeping a stale one stale
    rf'Object\.entries\(\(run\.curation && run\.curation\.{S.ANCHORS}\) \|\| \{{\}}\)',
    rf'anchorPicks\[direction\] = \{{ {S.STATE}: pick\.{S.STATE}, {S.INDEX}: Number\(pick\.{S.INDEX}\) \}};'
    rf'\s*if \(pick\.{S.STALE}\) \{{\s*anchorPicks\[direction\]\.{S.STALE} = true;'
    rf'\s*anchorPicks\[direction\]\.{S.STALE_REASON} = pick\.{S.STALE_REASON} \|\| "regenerated";',
    # store.js: a stale pin pressed again is an explicit re-pin
    rf'anchorPicks\[direction\] = \{{ {S.STATE}: stateName, {S.INDEX}: idx, {S.REPIN}: true \}};',
    # persistence.js: the adopted colourway
    rf'payload\.{S.RECOLOR} = recolorPicked \? \{{ {S.PICKED}: recolorPicked \}} : \{{\}};',
]


def test_the_curator_spells_the_anchor_and_recolor_keys_as_declared():
    missing = [p for p in SIDECAR_JS if not re.search(p, SCRIPTS)]
    assert not missing, "the curator no longer reads or writes these sidecar keys as declared:\n" + "\n".join(missing)


# Spelt out, not named: the keys the screen never touches are held to their declaration by this
# list alone — the engine stamps the generation (`run_revision`, a row's and a pin's `revision`),
# and `frozen` is a row field an agent plants and every save carries over.
SCREEN_UNTOUCHED = {"run_revision", "revision", "frozen"}


def test_every_sidecar_key_is_held_by_the_screen_or_listed():
    declared = [v for k, v in vars(S).items() if k.isupper()]
    assert len(declared) == len(set(declared)), "one sidecar key declared twice"
    held = ({S.VERSION, S.KIND, S.STATES, S.ANCHORS, S.PIXEL_UNFAKE} | set(ROW) | set(TRANSFORM)
            | {S.STATE, S.INDEX, S.STALE, S.STALE_REASON, S.REPIN, S.RECOLOR, S.PICKED})
    assert not held & SCREEN_UNTOUCHED
    assert set(declared) == held | SCREEN_UNTOUCHED, (
        f"declared but neither held nor listed: {sorted(set(declared) - held - SCREEN_UNTOUCHED)}; "
        f"listed but not declared: {sorted((held | SCREEN_UNTOUCHED) - set(declared))}")


# (function, object, key) reads in curation.py of vocabularies other than the sidecar.
OTHER_VOCABULARIES = {
    ("pixel_snap_scale", "fit", "pixel_unfake"),     # sprite-request.json `fit`
    ("state_revision", "fit", "pixel_unfake"),
    ("state_revision", "request", "states"),        # sprite-request.json
    ("state_revision", "r", "state"),               # frames-manifest rows
}


def _sidecar_text(source: str) -> list[tuple[int, str, str, str]]:
    """(line, def, object, key) of every sidecar key written as text in a key position: a dict
    key, a subscript, `.get`/`.pop`'s first argument, or the left of `in`."""
    keys = {v for k, v in vars(S).items() if k.isupper()}
    tree = ast.parse(source)
    found = []

    def obj(node: ast.AST) -> str:
        while isinstance(node, (ast.Subscript, ast.Attribute, ast.Call, ast.BoolOp)):
            node = node.values[0] if isinstance(node, ast.BoolOp) else (
                node.func if isinstance(node, ast.Call) else node.value)
        return node.id if isinstance(node, ast.Name) else ""

    def visit(node: ast.AST, owner: str) -> None:
        if isinstance(node, ast.ClassDef) and node.name == S.__name__:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        hits: list[tuple[ast.AST, str]] = []
        if isinstance(node, ast.Dict):
            hits += [(k, "{}") for k in node.keys if k is not None]
        if isinstance(node, ast.Subscript):
            hits.append((node.slice, obj(node.value)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop") \
                and node.args:
            hits.append((node.args[0], obj(node.func.value)))
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            hits.append((node.left, obj(node.comparators[0])))
        for key, of in hits:
            if isinstance(key, ast.Constant) and key.value in keys and (owner, of, key.value) not in OTHER_VOCABULARIES:
                found.append((key.lineno, owner, of, key.value))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree, "")
    return found


def test_curation_reads_and_writes_no_sidecar_key_as_text():
    raw = _sidecar_text(Path(curation.__file__).read_text(encoding="utf-8"))
    assert not raw, ("sidecar keys spelt as text in curation.py — name them through `Sidecar` (or, if another "
                     "vocabulary, list it above):\n" + "\n".join(f"  {line} {owner}: {of}[{key!r}]"
                                                              for line, owner, of, key in raw))


def test_every_sidecar_name_used_is_declared():
    for module in (curation, serve_curation):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        missing = [(n.lineno, n.attr) for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                   and isinstance(n.value, ast.Name) and n.value.id == S.__name__ and not hasattr(S, n.attr)]
        assert not missing, f"{module.__name__}: Sidecar names that do not exist: {missing}"
