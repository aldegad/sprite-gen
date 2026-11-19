# SPDX-License-Identifier: Apache-2.0
"""The keys of a video-loop report and of its strip sidecar that another module reads back are named once.

`loop.py` writes `<name>.loop.report.json` and `<name>.strip.json`; `align.py` (video-cycle-align), `compare.py`
(video-loop-compare) and `source.py` (video-loop-repair's source proof) read them. A key spelt one letter apart on the
two sides is no error anywhere: `.get` reads None and the reader takes the loop for one that recorded nothing. So
the keys are declared once
(`loop.Report`, `loop.Strip`, `loop.SourceCut`, `loop.SourceProjection`, `loop.SizeHold`), both sides go through the
names, and this file holds that:

1. The names spell the keys as the files on disk do — written out here, as a reader of the JSON writes them: a set
   built from the names agrees with itself whatever they say.
2. No key a namespace declares is read, or written, as a bare string off an object that holds that record (the
   objects each module holds them in, below) — directly or through a loop over a written-out tuple of keys — nor
   written as a bare string in the dicts video-loop builds them as.
3. Every `<Namespace>.<NAME>` the four modules write exists, so a misspelt name fails here and not as an AttributeError
   on a route no other test runs.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from sprite_gen.video import align, compare, loop, source

NAMESPACES = {"Report": loop.Report, "Strip": loop.Strip, "SourceCut": loop.SourceCut,
              "SourceProjection": loop.SourceProjection, "SizeHold": loop.SizeHold}


def _declared(ns: type) -> dict[str, str]:
    return {k: v for k, v in vars(ns).items() if k.isupper()}


def test_the_names_spell_the_keys_as_the_files_on_disk_do():
    assert set(_declared(loop.Report).values()) == {
        "kind", "status", "source", "producer", "fps", "frames_total", "anchor", "cycle", "cycle_drawings", "size_hold",
        "gait", "jump_repair", "source_projection", "strip", "path"}
    assert set(_declared(loop.Strip).values()) == {
        "frames", "sample_indices", "source_rect", "w", "h", "kind", "loop", "body_src_h", "body_h", "body_ref",
        "body_ref_frame", "scale", "delay_ms", "cycle_frames", "cycle_seconds", "subsampled", "cell_height_cap",
        "top_margin_px", "body_height_target",
        "foot_anchor", "source_cut", "out_fps", "source_fps", "air_retime", "state", "steps", "drawings",
        "cycle_drawings", "clip_feet", "held_fill", "holds", "wrap_blend", "motion_anchor", "wrap_dx_px",
        "cycle_align"}
    assert set(_declared(loop.SourceCut).values()) == {"source", "start", "length", "samples", "times"}
    assert set(_declared(loop.SourceProjection).values()) == {"recipe", "wrap_dx_px", "before_repair_pixels_sha256"}
    assert set(_declared(loop.SizeHold).values()) == {"applied", "cut_frames", "padding_ltrb"}
    for ns in NAMESPACES.values():
        assert len(set(_declared(ns).values())) == len(_declared(ns)), f"{ns.__name__} names one key twice"


# Where each module holds a record: (module, function or None for anywhere in it, the object as written, namespace).
HELD = [
    (loop, None, "report_base", loop.Report),
    (loop, None, "payload", loop.Report),
    (loop, None, "again", loop.Report),
    (loop, None, "strip_meta", loop.Strip),
    (loop, None, "again['strip']", loop.Strip),
    (loop, None, "payload['strip']", loop.Strip),
    (loop, "build_strip", "meta", loop.Strip),
    (loop, "_size_hold", "hold", loop.SizeHold),
    (align, None, "data", loop.Report),
    (align, "cut_report", "data.get('strip') or {}", loop.Report),
    (align, None, "meta", loop.Strip),
    (align, None, "merged", loop.Strip),
    (align, None, "strip_meta", loop.Strip),
    (align, None, "loops[i][2]", loop.Strip),
    (align, None, "cut['strip']", loop.Strip),
    (align, "unfilled_source", "cut", loop.Report),
    (compare, None, "self.source_report", loop.Report),
    (compare, None, "self.meta", loop.Strip),
    (compare, None, "a.meta", loop.Strip),
    (compare, None, "b.meta", loop.Strip),
    (compare, "read", "m", loop.Strip),
    (compare, "provenance_issue", "cut", loop.SourceCut),
    (source, "project", "report", loop.Report),
    (source, "project", "m", loop.Strip),
    (source, "project", "hold", loop.SizeHold),
    (source, "project", "native", loop.SourceProjection),
    (source, "_cut_moves", "hold", loop.SizeHold),
]

# The dicts video-loop builds a record as: (function, the object it is bound to or stored under, namespace). A dict
# that unpacks `report_base` is the report too.
BUILT = [
    ("_cut_loop", "report_base", loop.Report),
    ("_cut_loop", "payload", loop.Report),
    ("build_strip", "meta", loop.Strip),
    ("_cut_loop", "strip_meta['source_cut']", loop.SourceCut),
    ("_cut_loop", "report_base['source_projection']", loop.SourceProjection),
    ("_cut_loop", "report_base['size_hold']", loop.SizeHold),
    ("_size_hold", "hold", loop.SizeHold),
]


def _functions(tree: ast.AST) -> dict[ast.AST, str | None]:
    """Each node -> the name of the outermost function it is in (None: module level)."""
    owner: dict[ast.AST, str | None] = {}

    def visit(node: ast.AST, name: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = name or (child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else None)
            owner[child] = inner
            visit(child, inner)

    visit(tree, None)
    return owner


def _key_reads(module) -> list[tuple[int, str, str, ast.Constant]]:
    """(line, object, key, node) of every string key read or stored off a held object of `module` that its namespace
    declares."""
    tree = ast.parse(inspect.getsource(module))
    owner = _functions(tree)
    held = [(fn, base, set(_declared(ns).values())) for mod, fn, base, ns in HELD if mod is module]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            base, key = node.value, node.slice
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop", "setdefault")
              and node.args and isinstance(node.args[0], ast.Constant)):
            base, key = node.func.value, node.args[0]
        elif (isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant) and len(node.ops) == 1
              and isinstance(node.ops[0], (ast.In, ast.NotIn))):
            base, key = node.comparators[0], node.left
        else:
            continue
        if not isinstance(key.value, str):
            continue
        text = _spelt(base)
        for fn, want, keys in held:
            if text == want and (fn is None or owner.get(node) == fn) and key.value in keys:
                found.append((node.lineno, text, key.value, key))
    return found + _iterated_keys(tree, owner, held)


def _iterated_keys(tree: ast.AST, owner: dict[ast.AST, str | None], held: list) -> list[tuple[int, str, str, ast.Constant]]:
    """Keys read off a held object through a loop over a written-out tuple: `{k: merged[k] for k in ("frames", "w")}`,
    `(m.get(k) for k in (...))`, `for key in (...): ... meta[key]`."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            loops = [(gen.target, gen.iter, node) for gen in node.generators]
        elif isinstance(node, ast.For):
            loops = [(node.target, node.iter, node)]
        else:
            continue
        for target, written, scope in loops:
            if not isinstance(target, ast.Name) or not isinstance(written, (ast.Tuple, ast.List)):
                continue
            for use in ast.walk(scope):
                if isinstance(use, ast.Subscript) and isinstance(use.slice, ast.Name) and use.slice.id == target.id:
                    base = use.value
                elif (isinstance(use, ast.Call) and isinstance(use.func, ast.Attribute) and use.func.attr in ("get", "pop")
                      and use.args and isinstance(use.args[0], ast.Name) and use.args[0].id == target.id):
                    base = use.func.value
                else:
                    continue
                text = _spelt(base)
                for fn, want, keys in held:
                    if text == want and (fn is None or owner.get(use) == fn):
                        found += [(k.lineno, text, k.value, k) for k in written.elts
                                  if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value in keys]
    return found


def _built_keys() -> list[tuple[int, str, str, ast.Constant]]:
    tree = ast.parse(inspect.getsource(loop))
    owner = _functions(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Dict):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for fn, want, ns in BUILT:
                if _spelt(target) == want and owner.get(node) == fn:
                    found += _literal_keys(node.value, ns, want)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(k is None and ast.unparse(v) == "report_base" for k, v in zip(node.keys, node.values)):
            found += _literal_keys(node, loop.Report, "{**report_base}")
    return found


def _spelt(node: ast.AST) -> str:
    """The object as written with each declared name read as the key it names: `report_base[Report.SIZE_HOLD]` is
    `report_base['size_hold']`, and `cut[loop_mod.Report.STRIP]` is `cut['strip']`."""
    class Spell(ast.NodeTransformer):
        def visit_Attribute(self, n: ast.Attribute) -> ast.AST:
            ns = n.value.id if isinstance(n.value, ast.Name) else n.value.attr if isinstance(n.value, ast.Attribute) else None
            if ns in NAMESPACES and hasattr(NAMESPACES[ns], n.attr):
                return ast.Constant(getattr(NAMESPACES[ns], n.attr))
            return self.generic_visit(n)
    return ast.unparse(Spell().visit(ast.parse(ast.unparse(node), mode="eval")).body)


def _literal_keys(node: ast.Dict, ns: type, where: str) -> list[tuple[int, str, str, ast.Constant]]:
    keys = set(_declared(ns).values())
    return [(k.lineno, where, k.value, k) for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value in keys]


@pytest.mark.parametrize("module", [loop, align, compare, source], ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_no_declared_key_is_read_off_a_record_as_a_bare_string(module):
    bare = [f"{line}: {base}[{key!r}]" for line, base, key, _ in _key_reads(module)]
    assert not bare, f"{module.__name__} spells declared keys:\n" + "\n".join(bare)


def test_video_loop_builds_its_records_through_the_names():
    bare = [f"{line}: {where} {key!r}" for line, where, key, _ in _built_keys()]
    assert not bare, "loop.py builds a record with declared keys spelt out:\n" + "\n".join(bare)


@pytest.mark.parametrize("module", [loop, align, compare, source], ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_every_key_name_used_exists(module):
    missing = []
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Attribute) and isinstance(node.value, (ast.Name, ast.Attribute)):
            ns = node.value.attr if isinstance(node.value, ast.Attribute) else node.value.id
            if ns in NAMESPACES and not hasattr(NAMESPACES[ns], node.attr):
                missing.append(f"{node.lineno}: {ns}.{node.attr}")
    assert not missing, f"{module.__name__} names keys nobody declared: {missing}"
