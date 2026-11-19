# SPDX-License-Identifier: Apache-2.0
"""The package reads and writes text in an encoding it names, never in the locale's.

A text read or write that names no encoding takes the locale's (`locale.getencoding()`). On a Windows whose code page
is not UTF-8 (cp949 on a Korean one) that is not the UTF-8 the package writes: the read stops at the first character
the code page cannot decode, or reads other characters, which the package may then write back. This scans the
package's code (`sprite_gen/`) for every call that would: a `read_text` or `write_text` without an encoding; a
text-mode open without one (the built-in `open`, `io.open`, `codecs.open`, `os.fdopen`, a path's `open`, the
compressed and temporary files' openers, `io.TextIOWrapper`), its mode given by keyword or by position; and any of
them passed on uncalled (`opener = open`, `partial(io.open, ...)`, `getattr(path, "read_text")`), where no call can
be checked.

It reads a name closed: as everything the module binds it to, in every scope at once. A name is followed through an
import (`from io import open as o`, `import builtins as b; b.open`, `from io import *`) and through an assignment of
a name (`x = io; x.TextIOWrapper(...)`, `P = Path; P.read_text(path)`, `g = getattr`); `open` and `getattr` are
always the built-ins as well. A name bound anywhere in the module to a text opener is checked as one wherever it is
called, whatever another scope binds it to: a module that binds `os.open` to a name it binds `io.open` to elsewhere
is told so, and renames one. A `.open(...)` is taken for a path's unless its receiver is a name the module only
imports, a module's own `open` such as `Image.open` or `os.open` (a binding to a literal, as `Image = None` after a
failed import, does not count: a literal has no `open`), or a name a scope binds to a `zipfile.ZipFile` and to nothing
else, whose `open` reads bytes.

What it does not see:
- a name another module of the package binds (`from .x import reader`, `from .x import *`, a module's `__getattr__`):
  it is not followed into that module;
- a star import from any module but the openers' own (`io`, `codecs`, `os`, `gzip`, `bz2`, `lzma`, `tempfile`,
  `pathlib`, `builtins`);
- a name found at run time: `getattr` with a name not written in the call as a string, `operator.attrgetter`, `vars`,
  `eval`, `importlib.import_module`;
- an object's type beyond those names: a path class reached any other way (`type(path).read_text(path)`, a loop over
  classes) is taken for a path, its first argument for the encoding; an encoding given as a name is taken as named,
  whatever it holds;
- the calls that decode in the locale's encoding when given none and are not on the list above (`logging.FileHandler`,
  `fileinput`, a `ConfigParser`'s `read`, `os.popen`, a child's output under `subprocess`'s `text=True`);
- comments: an AST holds none.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "sprite_gen"

# The calls allowed to read or write in the locale's encoding: the file, the call as written, and why. The comment in
# sprite_gen/spec/runio.py that quotes the old `read_text()` needs no entry: an AST holds no comment.
LOCALE_READS = {
    ("sprite_gen/video/rife_torch.py", "NVIDIA_VERSION.read_text()"):
        "/proc/driver/nvidia/version, which only Linux has: ASCII the NVIDIA kernel module writes, read where the "
        "locale is the host's own; no Windows reaches the read",
}


def _no_b(mode: str) -> bool:
    return "b" not in mode


def _has_t(mode: str) -> bool:
    return "t" in mode


def _always(mode: str) -> bool:
    return True


# Each text opener by its qualified name: where its mode and its encoding stand among its positional arguments (None:
# not positional), its default mode, and when a mode is text.
OPENERS = {
    "builtins.open": (1, 3, "r", _no_b),
    "io.open": (1, 3, "r", _no_b),
    "codecs.open": (1, 2, "r", _no_b),  # without an encoding it is the built-in open
    "os.fdopen": (1, 3, "r", _no_b),
    "gzip.open": (1, 3, "rb", _has_t),
    "bz2.open": (1, 3, "rb", _has_t),
    "lzma.open": (1, None, "rb", _has_t),
    "tempfile.TemporaryFile": (0, 2, "w+b", _no_b),
    "tempfile.NamedTemporaryFile": (0, 2, "w+b", _no_b),
    "tempfile.SpooledTemporaryFile": (1, 3, "w+b", _no_b),
    "io.TextIOWrapper": (None, 1, "", _always),
}
PATH_OPEN = (0, 2, "r", _no_b)  # Path.open(mode, buffering, encoding)
PATH_METHODS = {"read_text": 0, "write_text": 1}  # where the encoding stands: read_text(encoding), write_text(data, encoding)
PATH_CLASSES = {"pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "pathlib.WindowsPath"}
BYTES_OPEN = {"zipfile.ZipFile"}  # objects whose `.open` gives bytes
BUILTINS = {"open": {"builtins.open"}, "getattr": {"builtins.getattr"}}  # names a built-in, whatever else binds them
BY_NAME = {"open", *PATH_METHODS, *(name.rpartition(".")[2] for name in OPENERS)}  # getattr(x, name) taking an opener
# What an assignment of a name is followed to: the openers, the path classes, zipfile.ZipFile, getattr, their modules.
FOLLOWED = {*OPENERS, *PATH_CLASSES, *BYTES_OPEN, "builtins.getattr"}
FOLLOWED |= {name.rpartition(".")[0] for name in FOLLOWED}
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _qualify(node: ast.AST, names: dict[str, set[str]]) -> set[str]:
    """Everything a name or a dotted name may name, as the module's bindings say; nothing for anything else."""
    if isinstance(node, ast.Name):
        return names.get(node.id, set()) | BUILTINS.get(node.id, set())
    if isinstance(node, ast.Attribute):
        return {f"{base}.{node.attr}" for base in _qualify(node.value, names)}
    return set()


def _aliases(target: ast.AST, value: ast.AST) -> list[tuple[str, ast.AST]]:
    """Each name an assignment binds to a name or a dotted name (`x = io`, `a, b = io, os`), with that name."""
    if isinstance(target, ast.Name) and isinstance(value, (ast.Name, ast.Attribute)):
        return [(target.id, value)]
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) \
            and len(target.elts) == len(value.elts):
        return [pair for t, v in zip(target.elts, value.elts) for pair in _aliases(t, v)]
    return []


def _bindings(tree: ast.AST) -> tuple[dict[str, set[str]], set[str], set[str], set[str]]:
    """What the module binds, read closed: every scope at once, so a name bound in two places names both.

    names: each name an import or an assignment of a name binds, to everything it names. imported: every module or
    object imported. rebound: every name also bound another way (an assignment of anything but a literal, a parameter,
    a loop, a def, `except ... as`). anywhere: every name a walrus, `global` or `nonlocal` binds, which may bind it in
    a scope other than the one it stands in.
    """
    names: dict[str, set[str]] = {}
    imported: set[str] = set()
    rebound: set[str] = set()
    anywhere: set[str] = set()
    aliases: list[tuple[str, ast.AST]] = []
    literal: set[int] = set()  # names assigned a literal, which has no `open`

    def bind(name: str, qualified: str) -> None:
        names.setdefault(name, set()).add(qualified)
        imported.add(qualified)

    for node in ast.walk(tree):  # breadth first: an assignment comes before the names it assigns
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                imported.update(".".join(parts[:k]) for k in range(1, len(parts) + 1))
                bind(alias.asname or parts[0], alias.name if alias.asname else parts[0])
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            for alias in node.names:
                if alias.name != "*":
                    bind(alias.asname or alias.name, f"{base}.{alias.name}" if node.module else f"{base}{alias.name}")
                    continue
                for qualified in (*OPENERS, *PATH_CLASSES):  # a star import binds an opener's module's openers
                    owner, _, name = qualified.rpartition(".")
                    if node.level == 0 and owner == node.module:
                        bind(name, qualified)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(node.value, ast.Constant):
                literal.update(id(target) for target in targets if isinstance(target, ast.Name))
            elif node.value is not None:
                aliases.extend(pair for target in targets for pair in _aliases(target, node.value))
            if isinstance(node, ast.NamedExpr):
                anywhere.add(node.target.id)
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and id(node) not in literal:
            rebound.add(node.id)
        elif isinstance(node, ast.arg):
            rebound.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler, ast.MatchAs,
                               ast.MatchStar)) and node.name:
            rebound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            rebound.add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            anywhere.update(node.names)
    grown = True
    while grown:  # an alias of an alias, until nothing more is found: FOLLOWED is finite
        grown = False
        for name, value in aliases:
            more = (_qualify(value, names) & FOLLOWED) - names.get(name, set())
            if more:
                names.setdefault(name, set()).update(more)
                grown = True
    return names, imported, rebound, anywhere


def _shifted(spec: tuple) -> tuple:
    """`spec` for the method called on its class (`Path.open(path, mode)`): every place one later, the path first."""
    mode_at, encoding_at, default, text = spec
    return (None if mode_at is None else mode_at + 1, None if encoding_at is None else encoding_at + 1, default, text)


class _Scan(ast.NodeVisitor):
    def __init__(self, tree: ast.AST, source: str) -> None:
        self.names, self.imported, self.rebound, self.anywhere = _bindings(tree)
        self.source = source
        self.found: list[tuple[int, str, str]] = []
        self.called: set[int] = set()
        self.byte_openers: list[set[str]] = [self._byte_openers(tree)]

    def qualify(self, node: ast.AST) -> set[str]:
        return _qualify(node, self.names)

    def is_rebound(self, node: ast.AST) -> bool:
        """Whether the name a name or a dotted name starts from is also bound other than by an import (_bindings)."""
        while isinstance(node, ast.Attribute):
            node = node.value
        return isinstance(node, ast.Name) and node.id in self.rebound

    def flag(self, node: ast.AST, why: str) -> None:
        self.found.append((node.lineno, ast.get_source_segment(self.source, node) or "", why))

    # scopes: a name a scope binds to a zipfile.ZipFile, and to nothing else, holds an archive in it
    def _scope(self, node: ast.AST) -> None:
        body = [node.body] if isinstance(node, ast.Lambda) else node.body
        inside = {id(part) for part in body}
        for child in ast.iter_child_nodes(node):
            if id(child) not in inside:
                self.visit(child)  # decorators, defaults, bases: evaluated in the scope around it
        self.byte_openers.append(self._byte_openers(node))
        for part in body:
            self.visit(part)
        self.byte_openers.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = visit_ClassDef = _scope

    def _byte_openers(self, scope: ast.AST) -> set[str]:
        """The names `scope` binds to a zipfile.ZipFile (`with ... as name`, `name = ...`) and to nothing else: no
        other binding of the name in the scope (a nested scope's are its own), no parameter of that name, and no
        walrus, `global` or `nonlocal` of it anywhere in the module."""
        archives: dict[int, str] = {}
        others = set(self.anywhere)
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = scope.args
            others.update(a.arg for a in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                                          arguments.vararg, arguments.kwarg) if a is not None)
        todo = [scope.body] if isinstance(scope, ast.Lambda) else list(scope.body)
        while todo:  # a node before the nodes in it
            node = todo.pop()
            if isinstance(node, SCOPES):
                if not isinstance(node, ast.Lambda):
                    others.add(node.name)
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and self.archive(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                archives.update((id(target), target.id) for target in targets if isinstance(target, ast.Name))
            elif isinstance(node, ast.withitem) and self.archive(node.context_expr) \
                    and isinstance(node.optional_vars, ast.Name):
                archives[id(node.optional_vars)] = node.optional_vars.id
            elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and id(node) not in archives:
                others.add(node.id)
            elif isinstance(node, ast.alias):
                others.add(node.asname or node.name.split(".")[0])
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                others.add(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                others.add(node.rest)
            todo.extend(ast.iter_child_nodes(node))
        return set(archives.values()) - others

    def archive(self, value: ast.AST | None) -> bool:
        """Whether `value` opens a zip archive: a call of zipfile.ZipFile, under a name bound to nothing else."""
        if not isinstance(value, ast.Call):
            return False
        found = self.qualify(value.func)
        return bool(found) and found <= BYTES_OPEN and not self.is_rebound(value.func)

    def readings(self, receiver: ast.AST) -> tuple[bool, bool]:
        """How `receiver.method(...)` may be read: as a path class's, the path given first (unbound), and as an
        object's own (bound). A receiver the scan cannot name, or names as more than a path class, may be either."""
        found = self.qualify(receiver)
        return bool(found & PATH_CLASSES), not found or bool(found - PATH_CLASSES) or self.is_rebound(receiver)

    def exempt(self, receiver: ast.AST) -> bool:
        """Whether an object's `.open` on `receiver` is not a path's: an archive's, or the own `open` of a module or an
        object the name is only ever imported as."""
        if isinstance(receiver, ast.Name) and receiver.id in self.byte_openers[-1]:
            return True
        found = self.qualify(receiver) - PATH_CLASSES
        return bool(found) and found <= self.imported and not self.is_rebound(receiver)

    def opens_path(self, receiver: ast.AST) -> bool:
        """Whether `receiver.open` may be a path's `open` (see the module's docstring)."""
        unbound, bound = self.readings(receiver)
        return unbound or (bound and not self.exempt(receiver))

    def visit_Call(self, node: ast.Call) -> None:
        self.called.add(id(node.func))
        func = node.func
        found = self.qualify(func)
        specs = [(OPENERS[name], name) for name in sorted(found.intersection(OPENERS))]
        if isinstance(func, ast.Attribute) and (func.attr in PATH_METHODS or func.attr == "open"):
            unbound, bound = self.readings(func.value)
            if func.attr == "open":
                spec, what, bound = PATH_OPEN, ".open", bound and not self.exempt(func.value)
            else:
                spec, what = (None, PATH_METHODS[func.attr], "", _always), f".{func.attr}"
            if unbound:  # Path.read_text(path), Path.open(path, mode): the path comes first
                specs.append((_shifted(spec), what))
            if bound:
                specs.append((spec, what))
        whys = [why for why in (self.locale(node, spec, what) for spec, what in specs) if why]
        if whys:
            self.flag(node, whys[0])
        if "builtins.getattr" in found and len(node.args) >= 2:
            name = node.args[1]
            if isinstance(name, ast.Constant) and name.value in BY_NAME:
                self.flag(node, f"getattr(..., {name.value!r}): an opener taken by name, its call not seen")
        self.generic_visit(node)

    def locale(self, call: ast.Call, spec: tuple, what: str) -> str | None:
        """Why `call`, read as `spec`, reads or writes text in the locale's encoding; None if it names one or reads
        bytes."""
        mode_at, encoding_at, default, text = spec
        keywords = {k.arg: k.value for k in call.keywords if k.arg is not None}
        unpacked = any(k.arg is None for k in call.keywords) or any(isinstance(a, ast.Starred) for a in call.args)
        encoding = keywords.get("encoding")
        if encoding is None and encoding_at is not None and len(call.args) > encoding_at:
            if not any(isinstance(a, ast.Starred) for a in call.args[:encoding_at + 1]):
                encoding = call.args[encoding_at]
        if encoding is not None and not (isinstance(encoding, ast.Constant) and encoding.value is None):
            return None
        mode = keywords.get("mode")
        if mode is None and mode_at is not None and len(call.args) > mode_at:
            mode = call.args[mode_at]
        if mode is None:
            mode = None if unpacked else ast.Constant(default)
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and not text(mode.value):
            return None  # bytes
        how = "text mode" if isinstance(mode, ast.Constant) else "a mode it cannot read"
        return f"{what} in {how} with no encoding: the locale's"

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and id(node) not in self.called and self.qualify(node).intersection(OPENERS):
            self.flag(node, "a text opener passed on uncalled: its call cannot be checked")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load) and id(node) not in self.called:
            if self.qualify(node).intersection(OPENERS) or node.attr in PATH_METHODS or (
                    node.attr == "open" and self.opens_path(node.value)):
                self.flag(node, "a text opener passed on uncalled: its call cannot be checked")
        self.generic_visit(node)


def locale_calls(source: str) -> list[tuple[int, str, str]]:
    """Each call in `source` that reads or writes text in the locale's encoding: its line, the call as written, why."""
    tree = ast.parse(source)
    scan = _Scan(tree, source)
    scan.visit(tree)
    return sorted(scan.found)


def package_locale_calls() -> list[tuple[str, int, str, str]]:
    """Each call of the package that reads or writes text in the locale's encoding: its file, line, call and why."""
    return [(path.relative_to(ROOT).as_posix(), line, call, why)
            for path in sorted(PACKAGE.rglob("*.py"))
            for line, call, why in locale_calls(path.read_text(encoding="utf-8"))]


def test_the_package_names_the_encoding_of_every_text_it_reads_or_writes():
    unexplained = [f"{path}:{line}: {call} — {why}" for path, line, call, why in package_locale_calls()
                   if (path, call) not in LOCALE_READS]
    assert not unexplained, "text read or written in the locale's encoding:\n" + "\n".join(unexplained)


def test_every_allowed_locale_read_is_still_there():
    """Each entry of LOCALE_READS names a call the scan finds, as it is written: an entry for a call since given an
    encoding, moved or never there fails, and so does a scan that has stopped finding anything."""
    found = {(path, call) for path, _, call, _ in package_locale_calls()}
    assert [key for key in LOCALE_READS if key not in found] == []


# Each way of writing a text read or write that names no encoding, which the scan finds, and ways that name one or
# read bytes, which it leaves. The forms a review names: an alias, the mode given by position, io.open, codecs.open.
LOCALE = {
    "open-default": "open(p)",
    "open-mode-positional": "open(p, 'r')",
    "open-mode-keyword": "open(p, mode='w')",
    "open-encoding-none": "open(p, 'w', encoding=None)",
    "open-mode-unread": "open(p, mode)",
    "open-unpacked": "open(*args)",
    "open-kwargs": "open(p, **options)",
    "open-aliased": "opener = open\nopener(p)",
    "open-partial": "import functools\nfunctools.partial(open, p)",
    "open-imported-as": "from io import open as o\no(p, 'r')",
    "builtins-open": "import builtins\nbuiltins.open(p)",
    "builtins-as": "import builtins as b\nb.open(p, 'a')",
    "io-open": "import io\nio.open(p, 'r', -1)",
    "io-as": "import io as x\nx.open(p)",
    "codecs-open": "import codecs\ncodecs.open(p, 'r')",
    "codecs-from": "from codecs import open\nopen(p)",
    "os-fdopen": "import os\nos.fdopen(fd, 'w')",
    "gzip-text": "import gzip\ngzip.open(p, 'rt')",
    "tempfile-text": "import tempfile\ntempfile.NamedTemporaryFile('w')",
    "text-wrapper": "import io\nio.TextIOWrapper(buffer)",
    "path-open": "p.open()",
    "path-open-mode": "p.open('w')",
    "path-open-expression": "(folder / 'a.json').open('r')",
    "path-open-unbound": "from pathlib import Path\nPath.open(p, 'r')",
    "read-text": "p.read_text()",
    "read-text-attribute": "module.PATH.read_text()",
    "read-text-unbound": "from pathlib import Path\nPath.read_text(p)",
    "read-text-aliased": "reader = p.read_text\nreader()",
    "read-text-getattr": "getattr(p, 'read_text')()",
    "write-text": "p.write_text(text)",
    "write-text-errors-only": "p.write_text(text, errors='strict')",
    "zip-name-rebound": "import zipfile\nwith zipfile.ZipFile(z) as f:\n    pass\nf = p\nf.open()",
    # A name bound in two scopes names both, whichever binding the walk reaches last: one scope's import of os.open
    # does not hide another scope's io.open (a review's probe is the first row). The two rows whose os.open the walk
    # reaches first guard the other way round: the first binding does not win either.
    "alias-scope-later-function": (
        "from io import open as reader\n"
        "def unrelated():\n"
        "    from os import open as reader\n"
        "    return reader\n"
        "def read_report(path):\n"
        "    with reader(path, 'r') as handle:\n"
        "        return handle.read()\n"),
    "alias-scope-earlier-function": (
        "def unrelated():\n"
        "    from os import open as reader\n"
        "    return reader\n"
        "from io import open as reader\n"
        "def read_report(path):\n"
        "    with reader(path, 'r') as handle:\n"
        "        return handle.read()\n"),
    "alias-scope-sibling-functions": (
        "def read_report(path):\n"
        "    from io import open as reader\n"
        "    with reader(path, 'r') as handle:\n"
        "        return handle.read()\n"
        "def unrelated():\n"
        "    from os import open as reader\n"
        "    return reader\n"),
    "alias-scope-nested-function": (
        "from io import open as reader\n"
        "def read_report(path):\n"
        "    def unrelated():\n"
        "        from os import open as reader\n"
        "        return reader\n"
        "    with reader(path, 'r') as handle:\n"
        "        return handle.read()\n"),
    "alias-scope-os-first": (
        "from os import open as reader\n"
        "def read_report(path):\n"
        "    from io import open as reader\n"
        "    with reader(path, 'r') as handle:\n"
        "        return handle.read()\n"),
    "alias-scope-os-first-nested": (
        "from os import open as reader\n"
        "def outer(path):\n"
        "    def read_report():\n"
        "        from io import open as reader\n"
        "        with reader(path, 'r') as handle:\n"
        "            return handle.read()\n"
        "    return read_report\n"),
    "open-hidden-in-another-scope": (
        "def raw(path):\n"
        "    from os import open\n"
        "    return open(path, 0)\n"
        "def read_report(path):\n"
        "    return open(path).read()\n"),
    "module-alias-other-scope": (
        "import io as x\n"
        "def unrelated():\n"
        "    import os as x\n"
        "    return x\n"
        "x.open(p)\n"),
    # A star import of an opener's module binds its openers; an assignment of a name binds what that name names: a
    # module, a path class, getattr.
    "star-io": "from io import *\nTextIOWrapper(buffer)",
    "star-tempfile": "from tempfile import *\nNamedTemporaryFile('w')",
    "star-os": "from os import *\nfdopen(fd, 'w')",
    "star-pathlib": "from pathlib import *\nPath.read_text(p)",
    "module-assigned": "import io\nx = io\nx.TextIOWrapper(buffer)",
    "class-assigned": "from pathlib import Path\nP = Path\nP.read_text(p)",
    "getattr-assigned": "g = getattr\ng(p, 'read_text')()",
    "getattr-other-opener": "import tempfile\ngetattr(tempfile, 'NamedTemporaryFile')('w')",
    # A name the module imports, or binds to a zip archive, bound again some other way: its `.open` may be a path's.
    "imported-name-rebound": (
        "import logging as log\n"
        "def write(run):\n"
        "    log = run / 'log.txt'\n"
        "    with log.open('w') as handle:\n"
        "        handle.write('done')\n"),
    "zip-name-loop": "import zipfile\nwith zipfile.ZipFile(z) as f:\n    pass\nfor f in paths:\n    f.open('w')",
    "zip-name-branch": "import zipfile\nif fresh:\n    f = p\nelse:\n    f = zipfile.ZipFile(z)\nf.open('w')",
    "zip-name-comprehension": "import zipfile\nf = zipfile.ZipFile(z)\n[f.open('w') for f in paths]",
    "zip-name-class-body": (
        "import zipfile\n"
        "archive = p\n"
        "def read(z):\n"
        "    class Kept:\n"
        "        archive = zipfile.ZipFile(z)\n"
        "    return archive.open('w')\n"),
    "zip-name-global": (
        "import zipfile\n"
        "f = zipfile.ZipFile(z)\n"
        "def swap():\n"
        "    global f\n"
        "    f = p\n"
        "f.open('w')\n"),
}
NAMED = {
    "open-encoding": "open(p, 'r', encoding='utf-8')",
    "open-encoding-positional": "open(p, 'r', -1, 'utf-8')",
    "open-bytes": "open(p, 'rb')",
    "open-bytes-keyword": "open(p, mode='wb')",
    "io-open-encoding": "import io\nio.open(p, encoding='utf-8')",
    "codecs-open-encoding": "import codecs\ncodecs.open(p, 'r', 'utf-8')",
    "os-fdopen-bytes": "import os\nos.fdopen(fd, 'wb')",
    "gzip-bytes": "import gzip\ngzip.open(p)",
    "tempfile-bytes": "import tempfile\ntempfile.TemporaryFile()",
    "path-open-bytes": "p.open('rb')",
    "path-open-encoding": "p.open('w', encoding='utf-8')",
    "read-text-encoding": "p.read_text(encoding='utf-8')",
    "read-text-encoding-positional": "p.read_text('utf-8')",
    "write-text-encoding": "p.write_text(text, encoding='utf-8')",
    "write-text-encoding-positional": "p.write_text(text, 'utf-8')",
    "image-open": "from PIL import Image\nImage.open(p)",
    "os-open": "import os\nos.open(p, os.O_RDONLY)",
    "webbrowser-open": "import webbrowser\nwebbrowser.open(url)",
    "zip-member": "import zipfile\nwith zipfile.ZipFile(z) as zf:\n    zf.open(info)",
    "zip-assigned": "import zipfile\nzf = zipfile.ZipFile(z)\nzf.open('a')",
    "a-function-named-read-text": "def read_text(backend):\n    return backend\nread_text(b)",
    # The way out of an alias-scope finding: os.open under a name of its own.
    "alias-scope-renamed": (
        "from io import open as reader\n"
        "def unrelated():\n"
        "    from os import open as raw\n"
        "    return raw\n"
        "def read_report(path):\n"
        "    with reader(path, 'r', encoding='utf-8') as handle:\n"
        "        return handle.read()\n"),
    "imported-name-literal": "try:\n    from PIL import Image\nexcept ImportError:\n    Image = None\nImage.open(p)",
    "zip-member-loop": (
        "import zipfile\n"
        "with zipfile.ZipFile(z) as zf:\n"
        "    for info in zf.infolist():\n"
        "        zf.open(info)\n"),
}


@pytest.mark.parametrize("source", list(LOCALE.values()), ids=list(LOCALE))
def test_the_scan_finds_a_text_read_or_write_that_names_no_encoding(source):
    assert locale_calls(source)


@pytest.mark.parametrize("source", list(NAMED.values()), ids=list(NAMED))
def test_the_scan_leaves_one_that_names_its_encoding_or_reads_bytes(source):
    assert locale_calls(source) == []
