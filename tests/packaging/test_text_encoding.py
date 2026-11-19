# SPDX-License-Identifier: Apache-2.0
"""The package reads and writes text in an encoding it names, never in the locale's.

A text read or write that names no encoding takes the locale's (`locale.getencoding()`). On a Windows whose code page
is not UTF-8 (cp949 on a Korean one) that is not the UTF-8 the package writes: the read stops at the first character
the code page cannot decode, or reads other characters, which the package may then write back. This scans the
package's code (`sprite_gen/`) for every call that would: a `read_text` or `write_text` without an encoding; a
text-mode open without one (the built-in `open`, `io.open`, `codecs.open`, `os.fdopen`, a path's `open`, the
compressed and temporary files' openers, `io.TextIOWrapper`), its mode given by keyword or by position; and any of
them passed on uncalled (`opener = open`, `partial(io.open, ...)`, `getattr(path, "read_text")`), where no call can
be checked. A name the module imports is followed (`from io import open as o`, `import builtins as b; b.open`).

What it cannot follow: an object it does not know the type of. A `.open(...)` on a name the module imports (a module,
such as `Image.open` or `os.open`) is that module's function and is checked only if it is a text opener above; on
anything else (a local, an attribute, a call) it is taken for a path's `open`, except on a name bound in the same
function to a `zipfile.ZipFile`, whose `open` reads bytes. Comments are not read: an AST holds none.
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


def _imports(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """Each name the module's imports bind, to what it names; and every module or object imported."""
    names: dict[str, str] = {}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                imported.update(".".join(parts[:k]) for k in range(1, len(parts) + 1))
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    names[parts[0]] = parts[0]
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            for alias in node.names:
                qualified = f"{base}.{alias.name}" if node.module else f"{base}{alias.name}"
                names[alias.asname or alias.name] = qualified
                imported.add(qualified)
    return names, imported


class _Scan(ast.NodeVisitor):
    def __init__(self, tree: ast.AST, source: str) -> None:
        self.names, self.imported = _imports(tree)
        self.source = source
        self.found: list[tuple[int, str, str]] = []
        self.called: set[int] = set()
        self.byte_openers: list[set[str]] = [set()]

    def qualify(self, node: ast.AST) -> str | None:
        """What a name or a dotted name names, where an import or the built-in `open` says; None for anything else."""
        if isinstance(node, ast.Name):
            if node.id in self.names:
                return self.names[node.id]
            return "builtins.open" if node.id == "open" else None
        if isinstance(node, ast.Attribute):
            base = self.qualify(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def flag(self, node: ast.AST, why: str) -> None:
        self.found.append((node.lineno, ast.get_source_segment(self.source, node) or "", why))

    # scopes: a name bound to a zipfile.ZipFile holds for the function it is bound in
    def _scope(self, node: ast.AST) -> None:
        self.byte_openers.append(set())
        self.generic_visit(node)
        self.byte_openers.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _scope

    def _bind(self, target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, ast.Name):
            if isinstance(value, ast.Call) and self.qualify(value.func) in BYTES_OPEN:
                self.byte_openers[-1].add(target.id)
            else:
                self.byte_openers[-1].discard(target.id)

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._bind(item.optional_vars, item.context_expr)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        for target in node.targets:
            self._bind(target, node.value)

    def visit_Call(self, node: ast.Call) -> None:
        self.called.add(id(node.func))
        func = node.func
        qualified = self.qualify(func)
        if qualified in OPENERS:
            self.check(node, OPENERS[qualified], qualified)
        elif isinstance(func, ast.Attribute) and func.attr in PATH_METHODS:
            unbound = self.qualify(func.value) in PATH_CLASSES  # Path.read_text(path): the path comes first
            self.check(node, (None, PATH_METHODS[func.attr] + unbound, "", _always), f".{func.attr}")
        elif isinstance(func, ast.Attribute) and func.attr == "open" and self.is_path(func.value):
            mode_at, encoding_at, default, text = PATH_OPEN
            shift = self.qualify(func.value) in PATH_CLASSES  # Path.open(path, mode): the path comes first
            self.check(node, (mode_at + shift, encoding_at + shift, default, text), ".open")
        elif (qualified == "builtins.getattr" or (isinstance(func, ast.Name) and func.id == "getattr")) and len(node.args) >= 2:
            name = node.args[1]
            if isinstance(name, ast.Constant) and name.value in {"open", *PATH_METHODS}:
                self.flag(node, f"getattr(..., {name.value!r}): an opener taken by name, its call not seen")
        self.generic_visit(node)

    def is_path(self, receiver: ast.AST) -> bool:
        """Whether `receiver.open(...)` is taken for a path's `open` (see the module's docstring)."""
        qualified = self.qualify(receiver)
        if qualified in PATH_CLASSES:
            return True
        if qualified is not None and qualified in self.imported:
            return False  # a module's own open (Image.open, os.open, webbrowser.open), or a text opener checked above
        return not (isinstance(receiver, ast.Name) and receiver.id in self.byte_openers[-1])

    def check(self, call: ast.Call, spec: tuple, what: str) -> None:
        mode_at, encoding_at, default, text = spec
        keywords = {k.arg: k.value for k in call.keywords if k.arg is not None}
        unpacked = any(k.arg is None for k in call.keywords) or any(isinstance(a, ast.Starred) for a in call.args)
        encoding = keywords.get("encoding")
        if encoding is None and encoding_at is not None and len(call.args) > encoding_at:
            if not any(isinstance(a, ast.Starred) for a in call.args[:encoding_at + 1]):
                encoding = call.args[encoding_at]
        if encoding is not None and not (isinstance(encoding, ast.Constant) and encoding.value is None):
            return
        mode = keywords.get("mode")
        if mode is None and mode_at is not None and len(call.args) > mode_at:
            mode = call.args[mode_at]
        if mode is None:
            mode = None if unpacked else ast.Constant(default)
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and not text(mode.value):
            return  # bytes
        how = "text mode" if isinstance(mode, ast.Constant) else "a mode it cannot read"
        self.flag(call, f"{what} in {how} with no encoding: the locale's")

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and id(node) not in self.called and self.qualify(node) in OPENERS:
            self.flag(node, "a text opener passed on uncalled: its call cannot be checked")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load) and id(node) not in self.called:
            if self.qualify(node) in OPENERS or node.attr in PATH_METHODS or (node.attr == "open" and self.is_path(node.value)):
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
}


@pytest.mark.parametrize("source", list(LOCALE.values()), ids=list(LOCALE))
def test_the_scan_finds_a_text_read_or_write_that_names_no_encoding(source):
    assert locale_calls(source)


@pytest.mark.parametrize("source", list(NAMED.values()), ids=list(NAMED))
def test_the_scan_leaves_one_that_names_its_encoding_or_reads_bytes(source):
    assert locale_calls(source) == []
