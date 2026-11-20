# SPDX-License-Identifier: Apache-2.0
"""The package reads and writes text in an encoding it names, never in the locale's.

A text read or write that names no encoding takes the locale's (`locale.getencoding()`). On a Windows whose code page
is not UTF-8 (cp949 on a Korean one) that is not the UTF-8 the package writes: the read stops at the first character
the code page cannot decode, or reads other characters, which the package may then write back. This scans the
package's code (`sprite_gen/`) for every call that would.

What it guards, and what it does not claim. It guards against a locale read or write written by mistake: a call in
the package's own code, written with names the module binds, that reads or writes text, or reads a child's output, in
the locale's encoding. Each such call it can follow is read by the values its arguments end with, and each form on the
way that it cannot follow is found, not passed: keywords unpacked with `**`, or positions with `*`, where the encoding
or the text mode may be; a partial of what it cannot name; a call of what it cannot name that names `text`,
`universal_newlines` or `encoding`; an opener or a child spawner passed on where its call is not seen. It claims
nothing about a name made at run time (`getattr` with a name not written as a string, a name built from strings,
`operator.attrgetter`, `vars`, `eval`, `importlib.import_module`); a name another module of the package binds (`from
.x import reader`, a star import of any module but the openers' and spawners' own, a module's `__getattr__`), which it
does not follow into that module; a call of what it cannot name that names none of those keywords, only positions,
`errors` (every codec's: `data.decode("utf-8", errors="replace")`) or `**`; an object's type beyond the names it
follows (a path class reached another way, as `type(path).read_text(path)` or a loop over classes, is taken for a
path, its first argument for the encoding); the calls that decode in the locale's encoding when given none and are not
listed here (`logging.FileHandler`, `fileinput`, a `ConfigParser`'s `read`); or comments, which an AST does not hold.
Where `getattr` names an opener or a spawner in a string it is found, but that is not part of the claim.

The text calls: a `read_text` or `write_text`; a text-mode open (the built-in `open`, `io.open`, `codecs.open`,
`os.fdopen`, a path's `open`, the compressed and temporary files' openers, `io.TextIOWrapper`), its mode given by
keyword or by position; and any of them passed on uncalled (`opener = open`, `partial(io.open, ...)`,
`getattr(path, "read_text")`), where no call can be checked.

A child's output is text read the same way. A `subprocess` call told to read it as text (`text` or
`universal_newlines`, by keyword or, for `universal_newlines`, by position; or `errors` alone) decodes what the child
writes in the locale's encoding unless it names one; `getoutput` and `getstatusoutput` always read text; `os.popen`
reads text and takes no encoding at all, so each mention of it is found.

Only a string literal names an encoding, on an opener as on a spawner: None, a name, an expression and "" name none.
An encoding named `"locale"` is the locale's by name, and is found on both kinds of call: a call that means it is
listed below with why.

It reads a name closed: as everything the module binds it to, in every scope at once. A name is followed through an
import (`from io import open as o`, `import builtins as b; b.open`, `from io import *`), through an assignment of a
name (`x = io; x.TextIOWrapper(...)`, `P = Path; P.read_text(path)`, `g = getattr`), through a parameter's default
(`def probe(run=subprocess.run)`) and, for a spawner, through the parameter of a def or a class of the module it is
handed to, by keyword or by position (`probe(cmd, run=subprocess.run)`); `open` and `getattr` are always the built-ins
as well. A name bound anywhere in the module to a text opener or a child spawner is checked as one wherever it is
called, whatever another scope binds it to: a module that binds `os.open` to a name it binds `io.open` to elsewhere,
or `os.system` to one it binds `subprocess.run` to, is told so, and renames one. A `.open(...)` is taken for a path's
unless its receiver is a name
the module only imports, a module's own `open` such as `Image.open` or `os.open` (a binding to a literal, as
`Image = None` after a failed import, does not count: a literal has no `open`), or a name a scope binds to a
`zipfile.ZipFile` and to nothing else, whose `open` reads bytes.

A spawner is read by the arguments it ends up called with, and whether it always reads text is its own: `getoutput`
and `getstatusoutput` do under any number of partials. A call of it is read where it stands. A `functools.partial` of
it, however many deep, is read once, where the partials end (at the one no other partial wraps and nothing calls at
once), with their arguments merged as `functools.partial` merges them: positions in order, keywords from the inner to
the outer, the outer winning (`partial(partial(getoutput, encoding="utf-8"), cmd, encoding=None)` reads None, so the
locale's; `partial(partial(getoutput, encoding=None), cmd, encoding="utf-8")` reads UTF-8). `text`,
`universal_newlines` and `encoding` are read from what the arguments end with; one a `**` may set after it was last
written, or a position after a `*` at or before `universal_newlines`', cannot be read and is found. A partial bound to
a name (by an assignment, a parameter's default, or a call of a def or a class of the module) is read where it is
written, and each call of that name, or of a name bound to it, is read through it with the call's own arguments as one
more layer: found there when the call makes it read in the locale's encoding, and found once, where it is written, when
it already does. A spawner handed to a def or a class of the module binds the parameter it is handed to, and each call
of that parameter is read as the spawner's. A spawner, a partial of one or a name bound to one is found, its call not
seen, when it is handed to any other call (an import's, a method's, a built-in's, or a def of the module with no
parameter of that name) or passed on any other way (into a container, as a return value, to an attribute), whatever it
reads where it is written. A call of what the scan cannot name (a parameter, an attribute of an object, a call's
result, or a partial of one) is read as a spawner's when it names `text`, `universal_newlines` or `encoding`, and is
found unless it names an encoding.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "sprite_gen"

# The child calls below, as written with each run of white space made one space (a line broken elsewhere keeps the
# call; any other change to it does not).
RIFE_CALL = ('subprocess.run( [str(binary), "-0", str(tmp / "a.png"), "-1", str(tmp / "b.png"), "-o", str(out), '
             '"-s", f"{t:.4f}", "-m", str(model), *modes], capture_output=True, text=True, '
             'timeout=CALL_TIMEOUT_SECONDS, )')
NVIDIA_SMI_CALL = ('subprocess.run([smi, "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True, '
                   'text=True, timeout=30)')
HEAL_CALL = "subprocess.run(cmd, capture_output=True, text=True, env=env)"

# The calls allowed to read or write in the locale's encoding: the file, the call as written (white space as above),
# and why. The comment in sprite_gen/spec/runio.py that quotes the old `read_text()` needs no entry: an AST holds no
# comment. The three child outputs listed sit in the three files whose bytes are an identity: frames/extract.py is
# hashed into `engine_revision`, which the curation view compares with each row to re-extract it, and video/rife.py
# and video/rife_torch.py each into `CODE_SHA256`, which keys the RIFE cache and, for rife.py, the remote RIFE
# handshake. Why each stays is in its entry, not beside the call: a comment there would change that identity too.
LOCALE_READS = {
    ("sprite_gen/video/rife_torch.py", "NVIDIA_VERSION.read_text()"):
        "/proc/driver/nvidia/version, which only Linux has: ASCII the NVIDIA kernel module writes, read where the "
        "locale is the host's own; no Windows reaches the read",
    ("sprite_gen/video/rife.py", RIFE_CALL):
        "rife-ncnn-vulkan writes in the locale's encoding: on Windows its wmain calls setlocale(LC_ALL, \"\") and "
        "prints its paths with fwprintf (src/main.cpp), in the code page, which is the encoding Python reads here; "
        "elsewhere it prints its arguments' bytes. Under UTF-8 mode (PYTHONUTF8=1, and the default from Python 3.15 "
        "by PEP 686) an encoding not named reads UTF-8 instead, and encoding=\"locale\" would keep the code page: that "
        "waits for the next change to this file",
    ("sprite_gen/video/rife_torch.py", NVIDIA_SMI_CALL):
        "nvidia-smi names no encoding for what it prints; the one value read is --query-gpu=driver_version, digits "
        "and dots, which every code page writes as ASCII and the locale's encoding reads as written",
    ("sprite_gen/frames/extract.py", HEAL_CALL):
        "the heal's child is this package's extract, which writes UTF-8 (util.console.utf8_streams first in its "
        "main), so on a Windows whose code page is not UTF-8 this reads it wrong: a failed state's heal note loses "
        "or garbles the tail it quotes, and the reader thread prints the decode error. It is left until the "
        "extraction engine next changes: changing this line changes engine_revision, and every row not frozen of "
        "every run the curation view opens is extracted again",
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
# Each call that starts a child and may read what it writes as text, by its qualified name: whether it always reads
# text (getoutput and getstatusoutput do; the others when told to), and where `universal_newlines` stands among its
# positional arguments (Popen's twelfth; run, call, check_call and check_output hand theirs to Popen).
SPAWNERS = {
    "subprocess.Popen": (False, 11),
    "subprocess.run": (False, 11),
    "subprocess.call": (False, 11),
    "subprocess.check_call": (False, 11),
    "subprocess.check_output": (False, 11),
    "subprocess.getoutput": (True, None),
    "subprocess.getstatusoutput": (True, None),
}
SPAWNER_BY_KEYWORDS = (False, None)  # what the scan cannot name, read as a spawner by its keywords alone
TEXT_MODE = ("text", "universal_newlines")  # the keywords that ask a spawner for text
TEXT_MARKS = (*TEXT_MODE, "encoding")  # the keywords that mark a call of what the scan cannot name as a spawner's
NAMELESS = (SPAWNER_BY_KEYWORDS, "what the scan cannot name")
PARTIAL = "functools.partial"  # wraps what it is given first, its arguments merged with the wrapped one's
UNREAD = object()  # a keyword a `**` may have set after it was last written: its value is not known
LOCALE_ONLY = {"os.popen"}  # reads a child's output as text in the locale's encoding, and takes no encoding
BUILTINS = {"open": {"builtins.open"}, "getattr": {"builtins.getattr"}}  # names a built-in, whatever else binds them
# getattr(x, name) taking an opener, or a spawner that reads text whatever it is given.
BY_NAME = {"open", *PATH_METHODS, *(name.rpartition(".")[2] for name in OPENERS),
           *(name.rpartition(".")[2] for name, (always, _) in SPAWNERS.items() if always),
           *(name.rpartition(".")[2] for name in LOCALE_ONLY)}
# What an assignment of a name or a parameter's default is followed to: the openers, the path classes, zipfile.ZipFile,
# getattr, the spawners, functools.partial, their modules. A parameter a call hands an argument to: the spawners.
FOLLOWED = {*OPENERS, *PATH_CLASSES, *BYTES_OPEN, "builtins.getattr", *SPAWNERS, *LOCALE_ONLY, PARTIAL}
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


def _signature(arguments: ast.arguments) -> tuple[list[str], set[str]]:
    """The parameters an argument is handed to: by position, in order, and by keyword."""
    return ([a.arg for a in (*arguments.posonlyargs, *arguments.args)],
            {a.arg for a in (*arguments.args, *arguments.kwonlyargs)})


def _signatures(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[tuple[list[str], set[str]]]:
    """Each signature a call of a def's or a class's name hands its arguments to: the def's, or each `__init__` of
    the class without its first parameter."""
    if not isinstance(node, ast.ClassDef):
        return [_signature(node.args)]
    inits = [_signature(part.args) for part in node.body
             if isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef)) and part.name == "__init__"]
    return [(positional[1:], named - set(positional[:1])) for positional, named in inits]


def _bindings(tree: ast.AST) -> tuple[dict[str, set[str]], set[str], set[str], set[str], set[str], set[str], set[int],
                                      list[tuple[str, ast.AST]]]:
    """What the module binds, read closed: every scope at once, so a name bound in two places names both.

    names: each name an import, an assignment of a name or a parameter's default binds, to everything it names, and
    each parameter of a def or a class of the module, to the spawners a call of it hands that parameter by name.
    imported: every module or object imported. rebound: every name also bound another way (an assignment of anything
    but a literal, a parameter, a loop, a def, `except ... as`). anywhere: every name a walrus, `global` or `nonlocal`
    binds, which may bind it in a scope other than the one it stands in. defined: every name a def or a class binds.
    valued: every name bound to a value the scan does not follow, as rebound is without the defs and classes.
    followed: each node an assignment of a name or a parameter's default binds a name to, and each argument a call of
    a def or a class of the module hands a parameter. parameters: each such parameter, with the argument handed it.
    """
    names: dict[str, set[str]] = {}
    imported: set[str] = set()
    rebound: set[str] = set()
    anywhere: set[str] = set()
    defined: set[str] = set()
    valued: set[str] = set()
    aliases: list[tuple[str, ast.AST]] = []
    literal: set[int] = set()  # names assigned a literal, which has no `open`
    signatures: dict[str, list[tuple[list[str], set[str]]]] = {}  # each def's or class's, by its name
    calls: list[ast.Call] = []  # each call of a name

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
                # a star import binds an opener's or a spawner's module's openers and spawners
                for qualified in (*OPENERS, *PATH_CLASSES, *SPAWNERS, *LOCALE_ONLY):
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
        elif isinstance(node, ast.arguments):  # a parameter's default binds its name as an assignment of a name would
            positional = [*node.posonlyargs, *node.args]
            pairs = [*zip(positional[len(positional) - len(node.defaults):], node.defaults),
                     *zip(node.kwonlyargs, node.kw_defaults)]
            aliases.extend((arg.arg, default) for arg, default in pairs
                           if isinstance(default, (ast.Name, ast.Attribute)))
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and id(node) not in literal:
            rebound.add(node.id)
            valued.add(node.id)
        elif isinstance(node, ast.arg):
            rebound.add(node.arg)
            valued.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rebound.add(node.name)
            defined.add(node.name)
            signatures.setdefault(node.name, []).extend(_signatures(node))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append(node)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            rebound.add(node.name)
            valued.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            rebound.add(node.rest)
            valued.add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            anywhere.update(node.names)
    parameters: list[tuple[str, ast.AST]] = []
    for call in calls:  # what a call of a def or a class of the module hands a parameter binds that parameter
        for positional, named in signatures.get(call.func.id, []):
            given = []
            for argument in call.args:
                if isinstance(argument, ast.Starred):
                    break  # the positions after it are not known
                given.append(argument)
            parameters += [*zip(positional, given), *((k.arg, k.value) for k in call.keywords if k.arg in named)]
    pairs = [*((name, value, FOLLOWED) for name, value in aliases),
             *((name, value, set(SPAWNERS)) for name, value in parameters
               if isinstance(value, (ast.Name, ast.Attribute)))]  # a spawner handed on; a partial: _Scan.bound
    grown = True
    while grown:  # an alias of an alias, until nothing more is found: FOLLOWED is finite
        grown = False
        for name, value, kinds in pairs:
            more = (_qualify(value, names) & kinds) - names.get(name, set())
            if more:
                names.setdefault(name, set()).update(more)
                grown = True
    followed = {id(value) for _, value in (*aliases, *parameters)}
    return names, imported, rebound, anywhere, defined, valued, followed, parameters


def _shifted(spec: tuple) -> tuple:
    """`spec` for the method called on its class (`Path.open(path, mode)`): every place one later, the path first."""
    mode_at, encoding_at, default, text = spec
    return (None if mode_at is None else mode_at + 1, None if encoding_at is None else encoding_at + 1, default, text)


def _none(node: ast.AST | None) -> bool:
    """Whether `node` is absent or the literal None: an argument not given."""
    return node is None or isinstance(node, ast.Constant) and node.value is None


def _handed(call: ast.Call) -> list[ast.AST]:
    """What `call` is handed: each argument but an unpacked one, by position or by keyword."""
    return [a for a in call.args if not isinstance(a, ast.Starred)] + [k.value for k in call.keywords if k.arg]


def _encoding(encoding: ast.AST | None, what: str) -> str | None:
    """Why `encoding`, as `what` is given it, is the locale's; None if it names another. Only a string literal that is
    not empty names one: "" when it names none (not given, None, a name, an expression, ""), for the caller to say
    why, and "locale" is the locale's by name."""
    if isinstance(encoding, ast.Constant) and isinstance(encoding.value, str) and encoding.value:
        return f"{what} with encoding 'locale': the locale's, by name" if encoding.value == "locale" else None
    return ""


def _no_encoding(encoding: ast.AST | None) -> str:
    """How a call that names no encoding was given `encoding`, for its finding."""
    return "no encoding" if _none(encoding) else "an encoding it does not name"


def _merge(layers: list[tuple[list[ast.expr], list[ast.keyword]]]) -> tuple[list[ast.expr], dict, bool]:
    """The arguments a spawner ends up called with, from `layers`: the positions and keywords of each partial around it
    and of its call, the innermost first, merged as functools.partial merges them (positions in order, keywords from
    the inner to the outer, the outer winning). A keyword a `**` may set after it was last written is UNREAD; the flag
    says whether one never written may be set."""
    positions: list[ast.expr] = []
    keywords: dict = {}
    unpacked = False
    for args, given in layers:
        positions.extend(args)
        if any(keyword.arg is None for keyword in given):
            keywords, unpacked = dict.fromkeys(keywords, UNREAD), True
        keywords.update((keyword.arg, keyword.value) for keyword in given if keyword.arg is not None)
    return positions, keywords, unpacked


class _Scan(ast.NodeVisitor):
    def __init__(self, tree: ast.AST, source: str) -> None:
        (self.names, self.imported, self.rebound, self.anywhere, self.defined, self.valued,
         self.followed, parameters) = _bindings(tree)
        self.source = source
        self.found: list[tuple[int, str, str]] = []
        self.called: set[int] = set()
        self.handed: set[int] = set()  # each argument of a call: a spawner there is read at that call
        self.wrapped: set[int] = set()  # each call's callee and each partial's first argument: read with that call
        self.byte_openers: list[set[str]] = [self._byte_openers(tree)]
        # Each name a functools.partial or another name is bound to, by an assignment, a parameter's default or a call
        # of a def or a class of the module: a call of the name is read through what it is bound to. kept: each value
        # the scan follows where it goes, bound to names only or to a parameter, or dropped (an expression statement).
        self.bound: dict[str, list[ast.AST]] = {}
        self.kept: set[int] = set()
        pairs = list(parameters)
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr):
                self.kept.add(id(node.value))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                pairs += [(target.id, node.value) for target in targets if isinstance(target, ast.Name)]
                if all(isinstance(target, ast.Name) for target in targets):
                    self.kept.add(id(node.value))
            elif isinstance(node, ast.arguments):
                positional = [*node.posonlyargs, *node.args]
                defaults = [(arg.arg, default) for arg, default in
                            (*zip(positional[len(positional) - len(node.defaults):], node.defaults),
                             *zip(node.kwonlyargs, node.kw_defaults)) if default is not None]
                pairs += defaults
                self.kept.update(id(default) for _, default in defaults)
        self.kept.update(id(value) for _, value in parameters)
        for name, value in pairs:
            if isinstance(value, ast.Name) or self.is_partial(value):
                self.bound.setdefault(name, []).append(value)

    def qualify(self, node: ast.AST) -> set[str]:
        return _qualify(node, self.names)

    def is_rebound(self, node: ast.AST) -> bool:
        """Whether the name a name or a dotted name starts from is also bound other than by an import (_bindings)."""
        while isinstance(node, ast.Attribute):
            node = node.value
        return isinstance(node, ast.Name) and node.id in self.rebound

    def nameless(self, func: ast.AST) -> bool:
        """Whether the scan cannot say what `func` is: anything but a name or a dotted name; one it names nothing for,
        unless a def or a class of the module binds the name; or one whose name is also bound to a value it does not
        follow (a parameter, an assignment, a loop), which may hold anything."""
        base = func
        while isinstance(base, ast.Attribute):
            base = base.value
        if not isinstance(base, ast.Name):
            return True
        return base.id in self.valued or (not self.qualify(func) and base.id not in self.defined)

    def flag(self, node: ast.AST, why: str) -> None:
        self.found.append((node.lineno, " ".join((ast.get_source_segment(self.source, node) or "").split()), why))

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
        arguments = _handed(node)
        self.handed.update(id(argument) for argument in arguments)
        inner = node.args[0] if self.is_partial(node) and node.args else None
        inner = None if isinstance(inner, ast.Starred) else inner
        self.wrapped.update(id(part) for part in (func, inner) if part is not None)
        # a call of a spawner, or of partials of one, read with the arguments they end with
        reads = [(spec, f"a call of {what}", [*layers, (node.args, node.keywords)], bound)
                 for spec, what, layers, bound in self.carriers(func)]
        if self.is_partial(node) and id(node) not in self.wrapped:  # where partials end: none wraps or calls this one
            reads += [(spec, f"functools.partial of {what}", *rest) for spec, what, *rest in self.carriers(node)]
            if id(node) not in self.kept and id(node) not in self.handed:  # handed: read where it is handed
                whys += [f"functools.partial of {what} passed on uncalled: its call cannot be checked"
                         for _, what, _, _ in self.carriers(node, spawners_only=True)]
        for spec, what, layers, bound in reads:
            # through a partial bound to a name, what that partial already reads in the locale's encoding is found
            # where it is written, once
            if bound is not None and self.child(layers[:bound], spec, what):
                continue
            why = self.child(layers, spec, what)
            if why:
                whys.append(why)
        whys += [f"{what} handed to a call the scan does not follow: its call cannot be checked"
                 for argument in arguments if argument is not inner and id(argument) not in self.followed
                 for _, what, _, _ in self.carriers(argument, spawners_only=True)]
        whys += [f"{name}: a child's output as text in the locale's encoding, and no encoding to name"
                 for name in sorted(found.intersection(LOCALE_ONLY))]
        if whys:
            self.flag(node, whys[0])
        if "builtins.getattr" in found and len(node.args) >= 2:
            name = node.args[1]
            if isinstance(name, ast.Constant) and name.value in BY_NAME:
                self.flag(node, f"getattr(..., {name.value!r}): an opener taken by name, its call not seen")
        self.generic_visit(node)

    def is_partial(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and PARTIAL in self.qualify(node.func)

    def carriers(self, node: ast.AST, spawners_only: bool = False,
                 seen: frozenset[str] = frozenset()) -> list[tuple[tuple, str, list, int | None]]:
        """What a call of `node` may start, read as a spawner: each spec (whether it always reads text, and where
        `universal_newlines` stands among its positional arguments) and name, with the layers of arguments the partials
        around it give, the innermost first, and how many of them a partial bound to a name gave (None: none was).
        Through a functools.partial, what it wraps with its arguments as one more layer; a spawner `node` names; for a
        name, the partials it is bound to (`bound`), directly or through other names; and, unless `spawners_only`, what
        the scan cannot name, read by its keywords."""
        if self.is_partial(node):
            if node.args and not isinstance(node.args[0], ast.Starred):
                inner, layer = self.carriers(node.args[0], spawners_only, seen), (node.args[1:], node.keywords)
            else:  # what it wraps is not written by position: not named
                inner, layer = ([] if spawners_only else [(*NAMELESS, [], None)]), (node.args, node.keywords)
            return [(spec, what, [*layers, layer], bound) for spec, what, layers, bound in inner]
        found = [(SPAWNERS[name], name, [], None) for name in sorted(self.qualify(node).intersection(SPAWNERS))]
        if isinstance(node, ast.Name) and node.id not in seen:
            found += [(spec, what, layers, len(layers)) for value in self.bound.get(node.id, [])
                      for spec, what, layers, _ in self.carriers(value, True, seen | {node.id}) if layers]
        if not spawners_only and self.nameless(node):
            found.append((*NAMELESS, [], None))
        return found

    def child(self, layers: list, spec: tuple, what: str) -> str | None:
        """Why a child started with the arguments `layers` end with (_merge), read as `spec` (`carriers`), reads what it
        writes in the locale's encoding; None if they name an encoding or read bytes. What the scan cannot name is read
        as a spawner only when they name a keyword of TEXT_MARKS, and then an encoding they name none with is found."""
        always, newlines_at = spec
        positions, keywords, unpacked = _merge(layers)
        nameless = spec is SPAWNER_BY_KEYWORDS
        if nameless and not keywords.keys() & set(TEXT_MARKS):
            return None  # nothing says it starts a child or reads text

        def value(name: str) -> object:
            return keywords.get(name, UNREAD if unpacked else None)

        unread = f"{what} with keywords it cannot read: a child's output, maybe text, in the locale's encoding"
        encoding = value("encoding")
        if encoding is UNREAD:
            return unread
        why = _encoding(encoding, what)
        if why != "":
            return why
        if nameless and "encoding" in keywords:
            return f"{what} with {_no_encoding(encoding)}: a child's output, maybe text, in the locale's encoding"
        asks = [value(name) for name in TEXT_MODE]
        errors = value("errors")
        if any(ask is UNREAD for ask in (*asks, errors)):
            return unread
        asks = [ask for ask in asks if ask is not None]
        if newlines_at is not None:
            starred = next((at for at, a in enumerate(positions) if isinstance(a, ast.Starred)), None)
            if starred is not None and starred <= newlines_at:
                return f"{what} with arguments it cannot read: a child's output, maybe text, in the locale's encoding"
            if len(positions) > newlines_at:
                asks.append(positions[newlines_at])
        if always or not _none(errors):  # errors alone asks for text too
            asks.append(ast.Constant(True))
        asks = [ask for ask in asks if not (isinstance(ask, ast.Constant) and not ask.value)]
        if not asks:
            return None  # bytes
        how = "as text" if all(isinstance(ask, ast.Constant) for ask in asks) else "in a text mode it cannot read"
        return f"{what}: a child's output {how} with {_no_encoding(encoding)}: the locale's"

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
        why = _encoding(encoding, what)
        if why != "":
            return why
        mode = keywords.get("mode")
        if mode is None and mode_at is not None and len(call.args) > mode_at:
            mode = call.args[mode_at]
        if mode is None:
            mode = None if unpacked else ast.Constant(default)
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and not text(mode.value):
            return None  # bytes
        how = "text mode" if isinstance(mode, ast.Constant) else "a mode it cannot read"
        return f"{what} in {how} with {_no_encoding(encoding)}: the locale's"

    def uncalled_child(self, node: ast.AST) -> str | None:
        """Why `node`, a name or a dotted name not called, passes on what reads a child's output where its call is not
        seen: a spawner, or a name bound to a partial of one, anywhere but bound to a name or a parameter (followed) or
        handed to a call (read at that call: wrapped by a partial, or found as handed to a call the scan does not
        follow), or a call that reads text in the locale's encoding whatever it is given."""
        found = self.qualify(node)
        if found & LOCALE_ONLY:
            return "a call that reads a child's output in the locale's encoding, passed on uncalled"
        if self.carriers(node, spawners_only=True) and id(node) not in self.followed and id(node) not in self.handed:
            return "a child spawner passed on uncalled: its call cannot be checked"
        return None

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and id(node) not in self.called:
            if self.qualify(node).intersection(OPENERS):
                self.flag(node, "a text opener passed on uncalled: its call cannot be checked")
            elif why := self.uncalled_child(node):
                self.flag(node, why)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load) and id(node) not in self.called:
            if self.qualify(node).intersection(OPENERS) or node.attr in PATH_METHODS or (
                    node.attr == "open" and self.opens_path(node.value)):
                self.flag(node, "a text opener passed on uncalled: its call cannot be checked")
            elif why := self.uncalled_child(node):
                self.flag(node, why)
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


# A partial of a spawner bound to a name, which rows below bind again by each form of target.
BOUND = "import functools, subprocess\nrun_it = functools.partial(subprocess.run, cmd, capture_output=True)\n"

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
    "open-encoding-locale": "open(p, 'r', encoding='locale')",
    "read-text-encoding-locale": "p.read_text('locale')",
    # A child's output read as text with no encoding: asked for by keyword or by position, or by `errors` alone; or
    # always, by getoutput and getstatusoutput; with an encoding of None or "locale"; or in a way the scan cannot read.
    "subprocess-text": "import subprocess\nsubprocess.run(cmd, capture_output=True, text=True)",
    "subprocess-universal-newlines": "import subprocess\nsubprocess.check_output(cmd, universal_newlines=True)",
    "subprocess-universal-newlines-positional": (
        "import subprocess\n"
        "subprocess.Popen(cmd, -1, None, None, subprocess.PIPE, None, None, True, False, None, None, True)"),
    "subprocess-errors-only": "import subprocess\nsubprocess.run(cmd, capture_output=True, errors='replace')",
    "subprocess-encoding-none": "import subprocess\nsubprocess.run(cmd, capture_output=True, text=True, encoding=None)",
    "subprocess-encoding-locale": (
        "import subprocess\nsubprocess.run(cmd, capture_output=True, text=True, encoding='locale')"),
    "subprocess-text-unread": "import subprocess\nsubprocess.run(cmd, capture_output=True, text=as_text)",
    "subprocess-kwargs": "import subprocess\nsubprocess.run(cmd, **options)",
    "subprocess-starred": "import subprocess\nsubprocess.Popen(*arguments)",
    "subprocess-getoutput": "import subprocess\nsubprocess.getoutput(cmd)",
    "subprocess-getstatusoutput-errors": "import subprocess\nsubprocess.getstatusoutput(cmd, errors='replace')",
    "os-popen": "import os\nos.popen(cmd)",
    # Every name the module binds a spawner to: an import under any name, a star import, an assignment of a name and a
    # parameter's default.
    "subprocess-run-as": "from subprocess import run as r\nr(cmd, capture_output=True, text=True)",
    "subprocess-popen-from": "from subprocess import Popen, PIPE\nPopen(cmd, stdout=PIPE, text=True)",
    "subprocess-check-output-from": "from subprocess import check_output\ncheck_output(cmd, universal_newlines=True)",
    "subprocess-getoutput-as": "from subprocess import getoutput as g\ng(cmd)",
    "subprocess-module-as": "import subprocess as sp\nsp.run(cmd, text=True)",
    "subprocess-star": "from subprocess import *\ncheck_output(cmd, text=True)",
    "subprocess-star-getoutput": "from subprocess import *\ngetoutput(cmd)",
    "subprocess-module-assigned": "import subprocess\nsp = subprocess\nsp.call(cmd, text=True)",
    "subprocess-function-assigned": "import subprocess\nr = subprocess.run\nr(cmd, text=True)",
    "subprocess-parameter-default": (
        "import subprocess\n"
        "def probe(cmd, *, run=subprocess.run):\n"
        "    return run(cmd, capture_output=True, text=True)\n"),
    # Through those names `errors` alone asks for text too, as it does not through a name the scan cannot name.
    "subprocess-module-assigned-errors": "import subprocess\nsp = subprocess\nsp.run(cmd, errors='replace')",
    "subprocess-function-assigned-errors": "import subprocess\nr = subprocess.run\nr(cmd, errors='replace')",
    "subprocess-parameter-default-errors": (
        "import subprocess\n"
        "def probe(cmd, *, run=subprocess.run):\n"
        "    return run(cmd, capture_output=True, errors='replace')\n"),
    # A spawner handed to a call is read by that call's keywords; passed on any other way uncalled, its call is not
    # seen; a call the scan cannot name that asks for text is read as a spawner's.
    "subprocess-handed-on": "import functools, subprocess\nfunctools.partial(subprocess.run, text=True)",
    "subprocess-handed-on-by-alias": (
        "import functools\nfrom subprocess import check_output as c\nfunctools.partial(c, errors='replace')"),
    "subprocess-in-a-container": "import subprocess\nRUNNERS = {'probe': subprocess.check_output}",
    "subprocess-returned": "import subprocess\ndef runner():\n    return subprocess.run",
    "subprocess-getattr": "import subprocess\ngetattr(subprocess, 'getoutput')(cmd)",
    "os-popen-uncalled": "import os\nreader = os.popen",
    "nameless-parameter": "def probe(runner, cmd):\n    return runner(cmd, capture_output=True, text=True)",
    "nameless-attribute": "self.run(cmd, universal_newlines=True)",
    "nameless-result": "import subprocess\ngetattr(subprocess, 'run')(cmd, text=True)",
    # An encoding on a call the scan cannot name marks it as well, and is read as on a spawner's: None or a name names
    # none, as through a getoutput or a partial handed in.
    "nameless-encoding-none": "def probe(read, cmd):\n    return read(cmd, encoding=None)",
    "nameless-encoding-a-name": "self.read(cmd, encoding=codec)",
    # getoutput and getstatusoutput read text whatever they are told, so a call either is handed to reads text unless
    # it names an encoding: under every name the module binds them to, handed by position or by keyword, in a partial
    # of a partial, in a container. A review's probe is the first row. os.popen is found handed on as passed on.
    "subprocess-getoutput-handed-on": (
        "import functools, subprocess\n"
        "read_output = functools.partial(subprocess.getoutput)\n"
        "read_output(cmd)\n"),
    "subprocess-getstatusoutput-handed-on": (
        "import functools, subprocess\nfunctools.partial(subprocess.getstatusoutput, cmd)"),
    "subprocess-getoutput-handed-on-by-alias": (
        "from functools import partial\nfrom subprocess import getoutput as g\npartial(g, cmd)"),
    "subprocess-getoutput-handed-on-from-star": (
        "from functools import partial\nfrom subprocess import *\npartial(getoutput)"),
    "subprocess-getoutput-handed-on-assigned": (
        "import functools, subprocess\ng = subprocess.getoutput\nfunctools.partial(g)"),
    "subprocess-getstatusoutput-handed-on-module-assigned": (
        "import functools, subprocess\nsp = subprocess\nfunctools.partial(sp.getstatusoutput)"),
    "subprocess-getoutput-handed-on-default": (
        "import functools, subprocess\n"
        "def reader(get=subprocess.getoutput):\n"
        "    return functools.partial(get)\n"),
    "subprocess-getoutput-partial-of-partial": (
        "import functools, subprocess\nfunctools.partial(functools.partial(subprocess.getoutput), cmd)"),
    "subprocess-getoutput-handed-by-keyword": (
        "import functools, subprocess\nfunctools.partial(func=subprocess.getoutput)"),
    "subprocess-getstatusoutput-handed-by-keyword": (
        "import subprocess\n"
        "def probe(cmd, run):\n"
        "    return run(cmd)\n"
        "probe(cmd, run=subprocess.getstatusoutput)\n"),
    "subprocess-getoutput-mapped": "import subprocess\nlist(map(subprocess.getoutput, cmds))",
    "subprocess-getoutput-handed-on-in-a-container": (
        "import functools, subprocess\nRUNNERS = [functools.partial(subprocess.getoutput)]\nRUNNERS[0](cmd)"),
    "subprocess-getoutput-out-of-a-container": (
        "import functools, subprocess\nRUNNERS = {'probe': subprocess.getoutput}\nfunctools.partial(RUNNERS['probe'])"),
    "os-popen-handed-on": "import functools, os\nfunctools.partial(os.popen, cmd)",
    "os-popen-handed-on-by-alias": "import functools\nfrom os import popen as p\nfunctools.partial(p)",
    "os-popen-handed-on-from-star": "import functools\nfrom os import *\nfunctools.partial(func=popen)",
    # run handed to a call, and that call's result handed to one that asks for text.
    "subprocess-run-partial-of-partial": (
        "import functools, subprocess\nfunctools.partial(functools.partial(subprocess.run), text=True)"),
    # Partials merge their keywords as functools.partial does, inner to outer, the outer winning, and what is read is
    # what they end with; a spawner that always reads text does so under any number of them. A review's probe is the
    # first row: getoutput's encoding named inside and taken back outside.
    "subprocess-getoutput-partial-encoding-taken-back": (
        "import functools, subprocess\n"
        "read_output = functools.partial(functools.partial(subprocess.getoutput, encoding='utf-8'), cmd, "
        "encoding=None)\n"
        "read_output()\n"),
    "subprocess-getstatusoutput-partial-encoding-taken-back": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(subprocess.getstatusoutput, encoding='utf-8'), cmd, encoding=None)"),
    "os-popen-partial-encoding-taken-back": (
        "import functools, os\nfunctools.partial(functools.partial(os.popen, encoding='utf-8'), cmd, encoding=None)"),
    "subprocess-getoutput-three-partials-outer-none": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(functools.partial(subprocess.getoutput, encoding='utf-8'), "
        "encoding='utf-8'), encoding=None)"),
    "subprocess-getoutput-partial-encoding-a-name": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(subprocess.getoutput, encoding='utf-8'), cmd, encoding=codec)"),
    # Only a string literal names an encoding: a name or "" names none, on a spawner as on an opener.
    "subprocess-encoding-a-name": (
        "import subprocess\nsubprocess.run(cmd, capture_output=True, text=True, encoding=codec)"),
    "subprocess-encoding-empty": "import subprocess\nsubprocess.check_output(cmd, text=True, encoding='')",
    "open-encoding-a-name": "open(p, 'r', encoding=codec)",
    "write-text-encoding-empty": "p.write_text(text, encoding='')",
    # What the scan cannot follow is found: keywords or positions unpacked where they may decide, a partial of what it
    # cannot name, a partial called later with an encoding it does not name, a spawner handed to a call it does not
    # follow (an import's, or a function of the module without a parameter of that name).
    "subprocess-getoutput-partial-kwargs": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(subprocess.getoutput, encoding='utf-8'), **options)"),
    "subprocess-popen-partial-arguments-unpacked": (
        "import functools, subprocess\nfunctools.partial(functools.partial(subprocess.Popen, cmd), *arguments)"),
    "partial-of-what-it-cannot-name": "import functools\nfunctools.partial(runners[name], text=True)",
    "subprocess-getoutput-partial-called-with-encoding-none": (
        "import functools, subprocess\n"
        "read_output = functools.partial(subprocess.getoutput, encoding='utf-8')\n"
        "read_output(cmd, encoding=None)\n"),
    "subprocess-run-handed-to-an-import": "import subprocess, helpers\nhelpers.probe(cmd, run=subprocess.run)",
    "subprocess-run-handed-to-kwargs": (
        "import subprocess\n"
        "def probe(cmd, **options):\n"
        "    return options['run'](cmd, errors='replace')\n"
        "probe(cmd, run=subprocess.run)\n"),
    # A spawner handed to a function of the module binds its parameter, by keyword or by position, and each call of
    # that parameter is read as the spawner's.
    "subprocess-run-handed-on-unpacked": (
        "import subprocess\n"
        "def probe(cmd, run):\n"
        "    return run(cmd, **TEXT)\n"
        "probe(cmd, run=subprocess.run)\n"),
    "subprocess-run-handed-by-position": (
        "import subprocess\n"
        "def probe(cmd, run):\n"
        "    return run(cmd, errors='replace')\n"
        "probe(cmd, subprocess.run)\n"),
    # A partial bound to a name is followed: a call of the name, or of a name bound to it, is read through the partial
    # with its own arguments as the outer layer (errors alone, keywords unpacked), and the name passed on is found.
    "subprocess-run-partial-bound-called-with-errors": (
        "import functools, subprocess\n"
        "run_it = functools.partial(subprocess.run, cmd)\n"
        "run_it(errors='replace')\n"),
    "subprocess-getoutput-partial-bound-called-unpacked": (
        "import functools, subprocess\n"
        "read_output = functools.partial(subprocess.getoutput, encoding='utf-8')\n"
        "read_output(cmd, **options)\n"),
    "subprocess-run-partial-bound-and-bound-again": (
        "import functools, subprocess\n"
        "run_it = functools.partial(subprocess.run, cmd)\n"
        "again = run_it\n"
        "again(errors='replace')\n"),
    "subprocess-run-partial-bound-passed-on": (
        "import functools, subprocess\nrun_it = functools.partial(subprocess.run, cmd)\nRUNNERS = [run_it]"),
    # A partial of a spawner passed on where its call is not seen is found as the spawner is, whatever it reads where
    # it is written: into a container, as a return value, to an attribute.
    "subprocess-run-partial-in-a-container": (
        "import functools, subprocess\nRUNNERS = [functools.partial(subprocess.run, cmd)]"),
    "subprocess-run-partial-returned": (
        "import functools, subprocess\n"
        "def runner(cmd):\n"
        "    return functools.partial(subprocess.run, cmd, check=True)\n"),
    "subprocess-getoutput-partial-to-an-attribute": (
        "import functools, subprocess\nself.read = functools.partial(subprocess.getoutput, encoding='utf-8')"),
    # The closed reading on a spawner's name: subprocess.run under a name another scope binds os.system to is checked
    # wherever that name is called, whichever binding the walk reaches last.
    "subprocess-alias-scope-later-function": (
        "from subprocess import run as r\n"
        "def unrelated():\n"
        "    from os import system as r\n"
        "    r('true')\n"
        "def probe(cmd):\n"
        "    return r(cmd, capture_output=True, text=True)\n"),
    "subprocess-alias-scope-earlier-function": (
        "def unrelated():\n"
        "    from os import system as r\n"
        "    r('true')\n"
        "from subprocess import run as r\n"
        "def probe(cmd):\n"
        "    return r(cmd, capture_output=True, text=True)\n"),
    "subprocess-alias-scope-nested-function": (
        "from subprocess import check_output as r\n"
        "def probe(cmd):\n"
        "    def unrelated():\n"
        "        from os import system as r\n"
        "        r('true')\n"
        "    return r(cmd, text=True)\n"),
    "subprocess-alias-scope-os-first": (
        "from os import system as r\n"
        "def probe(cmd):\n"
        "    from subprocess import Popen as r\n"
        "    return r(cmd, text=True)\n"),
    # A name bound to a partial is followed when a tuple or a list target binds it again, as when `=` does: by its
    # place, in nested ones, before and after a star, under each target of a chained assignment that unpacks it, and so
    # is a partial written there (a review's probe is the first row). An annotated `=` and a walrus bind as `=` does.
    "subprocess-run-partial-unpacked-from-a-tuple": BOUND + "alias, = (run_it,)\nalias(errors='replace')\n",
    "subprocess-run-partial-unpacked-from-a-list": BOUND + "[alias] = [run_it]\nalias(errors='replace')\n",
    "subprocess-run-partial-unpacked-list-from-a-tuple": (
        BOUND + "[first, alias] = cmd, run_it\nalias(errors='replace')\n"),
    "subprocess-run-partial-unpacked-nested": (
        BOUND + "first, (second, [alias]) = cmd, [cmd, (run_it,)]\nalias(errors='replace')\n"),
    "subprocess-run-partial-unpacked-chained": BOUND + "(alias,) = [again] = (run_it,)\nagain(errors='replace')\n",
    "subprocess-run-partial-unpacked-before-a-star": (
        BOUND + "alias, *rest = run_it, cmd, cmd\nalias(errors='replace')\n"),
    "subprocess-run-partial-unpacked-after-a-star": (
        BOUND + "*rest, alias = cmd, cmd, run_it\nalias(errors='replace')\n"),
    "subprocess-run-partial-unpacked-around-an-empty-star": (
        BOUND + "first, *rest, alias = cmd, run_it\nalias(errors='replace')\n"),
    "subprocess-run-partial-written-where-it-is-unpacked": (
        "import functools, subprocess\n"
        "alias, first = functools.partial(subprocess.run, cmd), cmd\n"
        "alias(errors='replace')\n"),
    "subprocess-run-partial-annotated": BOUND + "alias: object = run_it\nalias(errors='replace')\n",
    "subprocess-run-partial-walrus": BOUND + "(alias := run_it)\nalias(errors='replace')\n",
    # A walrus gives back its value: called, handed to a call or bound to a name, it is read as that value; passed on
    # anywhere else, its call is not seen.
    "subprocess-run-partial-walrus-assigned": BOUND + "alias = (again := run_it)\nalias(errors='replace')\n",
    "subprocess-run-partial-walrus-unpacked": (
        BOUND + "first, alias = cmd, (again := run_it)\nalias(errors='replace')\n"),
    "subprocess-run-partial-walrus-called": BOUND + "(alias := run_it)(errors='replace')\n",
    "subprocess-run-partial-walrus-handed-to-a-def": (
        BOUND + "def probe(cmd, run):\n    return run(cmd, errors='replace')\nprobe(cmd, run=(alias := run_it))\n"),
    "subprocess-run-partial-walrus-handed-to-an-import": (
        BOUND + "import helpers\nhelpers.probe(cmd, run=(alias := run_it))\n"),
    "subprocess-run-partial-walrus-in-a-container": BOUND + "RUNNERS = [(alias := run_it)]\n",
    "subprocess-run-walrus-returned": "import subprocess\ndef runner():\n    return (run := subprocess.run)\n",
    # What the scan does not follow is found where it is written: a starred name (a list of what it takes); a name that
    # takes a tuple or a list another target of the same assignment unpacks; an attribute or a subscript; a value the
    # scan cannot unpack (not a tuple or a list written there, a `*` in it, another length); an augmented assignment's
    # value; a `with` item's context; a loop's iterable.
    "subprocess-run-partial-into-a-star": BOUND + "*rest, = (run_it,)\nrest[0](errors='replace')\n",
    "subprocess-run-partial-into-a-star-among-others": (
        BOUND + "first, *rest = cmd, cmd, run_it\nrest[-1](errors='replace')\n"),
    "subprocess-run-partial-chained-with-a-name": BOUND + "whole = (alias,) = (run_it,)\nwhole[0](errors='replace')\n",
    "subprocess-run-partial-chained-with-a-name-called": (
        BOUND + "whole = (alias,) = (run_it,)\nalias(errors='replace')\n"),
    "subprocess-run-partial-unpacked-to-an-attribute": (
        BOUND + "self.run, first = run_it, cmd\nself.run(errors='replace')\n"),
    "subprocess-run-partial-to-a-subscript": BOUND + "RUNNERS['probe'] = run_it\nRUNNERS['probe'](errors='replace')\n",
    "subprocess-run-partial-unpacked-from-a-name": (
        BOUND + "pair = run_it, cmd\nalias, first = pair\nalias(errors='replace')\n"),
    "subprocess-run-partial-unpacked-with-a-star-in-the-value": (
        BOUND + "alias, first = run_it, *rest\nalias(errors='replace')\n"),
    "subprocess-run-partial-unpacked-to-another-length": (
        BOUND + "alias, first = run_it, cmd, cmd\nalias(errors='replace')\n"),
    "subprocess-run-partial-augmented": BOUND + "alias = cmd\nalias += run_it\nalias(errors='replace')\n",
    "subprocess-run-partial-with-as": BOUND + "with run_it as alias:\n    alias(errors='replace')\n",
    "subprocess-run-partial-for": BOUND + "for alias in (run_it,):\n    alias(errors='replace')\n",
    "subprocess-run-partial-comprehension": BOUND + "[alias(errors='replace') for alias in [run_it]]\n",
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
    "subprocess-bytes": "import subprocess\nsubprocess.run(cmd, capture_output=True)",
    "subprocess-text-off": "import subprocess\nsubprocess.run(cmd, capture_output=True, text=False)",
    "subprocess-encoding": (
        "import subprocess\nsubprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')"),
    "subprocess-encoding-alone": "import subprocess\nsubprocess.check_output(cmd, encoding='utf-8')",
    "subprocess-getoutput-encoding": "import subprocess\nsubprocess.getoutput(cmd, encoding='utf-8')",
    "subprocess-popen-bytes": "from subprocess import Popen, PIPE\nPopen(cmd, stdout=PIPE, stderr=PIPE)",
    "subprocess-run-as-encoding": "from subprocess import run as r\nr(cmd, text=True, encoding='utf-8')",
    "subprocess-assigned-encoding": "import subprocess\nr = subprocess.run\nr(cmd, text=True, encoding='utf-8')",
    # A spawner a parameter defaults to, and handed on by that name to another call: each call's keywords are read.
    "subprocess-parameter-default-encoding": (
        "import subprocess\n"
        "def probe(cmd, *, run=subprocess.run):\n"
        "    return run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')\n"
        "def ask(cmd, *, run=subprocess.run):\n"
        "    return probe(cmd, run=run)\n"),
    "subprocess-handed-on-encoding": (
        "import functools, subprocess\nfunctools.partial(subprocess.run, text=True, encoding='utf-8')"),
    # getoutput handed to a call that names an encoding; run handed on with nothing that asks for text.
    "subprocess-getoutput-handed-on-encoding": (
        "import functools, subprocess\nfunctools.partial(subprocess.getoutput, encoding='utf-8')"),
    "subprocess-getoutput-partial-of-partial-encoding": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(subprocess.getoutput, encoding='utf-8'), cmd)"),
    "subprocess-run-handed-on-bytes": "import functools, subprocess\nfunctools.partial(subprocess.run, cmd)",
    "subprocess-run-partial-of-partial-bytes": (
        "import functools, subprocess\nfunctools.partial(functools.partial(subprocess.run, cmd), check=True)"),
    # What partials end with is read, not what each one says on its way: an encoding taken back and given again, the
    # validator's probe kept and called, a middle partial's None under an outer one's encoding, an encoding written
    # after an unpacking, text asked for and taken back, a partial given its encoding when it is called at once.
    "subprocess-getoutput-partial-encoding-given-back": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(subprocess.getoutput, encoding=None), cmd, encoding='utf-8')"),
    "subprocess-getoutput-partial-encoding-kept-and-called": (
        "import functools, subprocess\n"
        "read_output = functools.partial(functools.partial(subprocess.getoutput, encoding='utf-8'), cmd)\n"
        "read_output()\n"),
    "subprocess-getoutput-three-partials-middle-none": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(functools.partial(subprocess.getoutput, cmd), encoding=None), "
        "encoding='utf-8')"),
    "subprocess-getoutput-partial-kwargs-then-encoding": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(subprocess.getoutput, **options), encoding='utf-8')"),
    "subprocess-run-partial-text-taken-back": (
        "import functools, subprocess\n"
        "functools.partial(functools.partial(subprocess.run, cmd, text=True), text=False)"),
    "subprocess-getoutput-partial-called-at-once": (
        "import functools, subprocess\nfunctools.partial(subprocess.getoutput, encoding=None)(cmd, encoding='utf-8')"),
    # A spawner handed by position to a function of the module, whose call of it names its encoding.
    "subprocess-run-handed-by-position-encoding": (
        "import subprocess\n"
        "def probe(cmd, run):\n"
        "    return run(cmd, text=True, encoding='utf-8')\n"
        "probe(cmd, subprocess.run)\n"),
    # A partial bound to a name and called with arguments that read bytes, or name the encoding's errors only.
    "subprocess-run-partial-bound-called-bytes": (
        "import functools, subprocess\nrun_it = functools.partial(subprocess.run, cmd)\nrun_it(check=True)"),
    "subprocess-getoutput-partial-bound-called-with-errors": (
        "import functools, subprocess\n"
        "read_output = functools.partial(subprocess.getoutput, encoding='utf-8')\n"
        "read_output(cmd, errors='replace')\n"),
    "os-system": "import os\nos.system(cmd)",
    # `text` on a call the scan names, and that is no spawner: a function or a class of the module, or an import.
    "a-function-with-a-text-keyword": "def piece(text):\n    return text\npiece(text='walk')",
    "an-imported-class-with-a-text-keyword": "from .api import Reply\nReply(text='walk')",
    "a-module-function-with-a-text-keyword": "import textwrap\ntextwrap.shorten(text='walk', width=4)",
    # A call the scan cannot name that names its encoding.
    "nameless-encoding-named": "def probe(read, cmd):\n    return read(cmd, encoding='utf-8')",
    # The way out of a spawner's alias-scope finding: os.system under a name of its own.
    "subprocess-alias-scope-renamed": (
        "from subprocess import run as r\n"
        "def unrelated():\n"
        "    from os import system as shell\n"
        "    shell('true')\n"
        "def probe(cmd):\n"
        "    return r(cmd, capture_output=True, text=True, encoding='utf-8')\n"),
    # A partial bound again by a tuple target, or by a walrus, and called with arguments that read bytes; a partial
    # written where a tuple target unpacks it, which names its encoding.
    "subprocess-run-partial-unpacked-called-bytes": BOUND + "alias, = (run_it,)\nalias(check=True)\n",
    "subprocess-run-partial-walrus-called-bytes": BOUND + "(alias := run_it)(check=True)\n",
    "subprocess-getoutput-partial-written-where-it-is-unpacked-encoding": (
        "import functools, subprocess\n"
        "read, = (functools.partial(subprocess.getoutput, encoding='utf-8'),)\n"
        "read(cmd)\n"),
}


@pytest.mark.parametrize("source", list(LOCALE.values()), ids=list(LOCALE))
def test_the_scan_finds_a_text_read_or_write_that_names_no_encoding(source):
    assert locale_calls(source)


@pytest.mark.parametrize("source", list(NAMED.values()), ids=list(NAMED))
def test_the_scan_leaves_one_that_names_its_encoding_or_reads_bytes(source):
    assert locale_calls(source) == []


# A partial of partials is found once, at the partial its layers end with, not at the ones it wraps.
OUTERMOST = {
    "subprocess-getoutput-partial-encoding-taken-back": (
        "functools.partial(functools.partial(subprocess.getoutput, encoding='utf-8'), cmd, encoding=None)"),
    "subprocess-getoutput-three-partials-outer-none": (
        "functools.partial(functools.partial(functools.partial(subprocess.getoutput, encoding='utf-8'), "
        "encoding='utf-8'), encoding=None)"),
}


@pytest.mark.parametrize("row", list(OUTERMOST))
def test_a_partial_of_partials_is_found_where_its_layers_end(row):
    assert [call for _, call, _ in locale_calls(LOCALE[row])] == [OUTERMOST[row]]


# Where each target form's row is found: at the call, for a target the scan follows; where the value is written, for one
# it does not follow; at both, for a value that one target of an assignment follows and another does not.
AT_CALL = "alias(errors='replace')"
WRITTEN = "run_it"
FOUND_AT = {
    "subprocess-run-partial-unpacked-from-a-tuple": [AT_CALL],
    "subprocess-run-partial-unpacked-from-a-list": [AT_CALL],
    "subprocess-run-partial-unpacked-list-from-a-tuple": [AT_CALL],
    "subprocess-run-partial-unpacked-nested": [AT_CALL],
    "subprocess-run-partial-unpacked-chained": ["again(errors='replace')"],
    "subprocess-run-partial-unpacked-before-a-star": [AT_CALL],
    "subprocess-run-partial-unpacked-after-a-star": [AT_CALL],
    "subprocess-run-partial-unpacked-around-an-empty-star": [AT_CALL],
    "subprocess-run-partial-written-where-it-is-unpacked": [AT_CALL],
    "subprocess-run-partial-annotated": [AT_CALL],
    "subprocess-run-partial-walrus": [AT_CALL],
    "subprocess-run-partial-walrus-assigned": [AT_CALL],
    "subprocess-run-partial-walrus-unpacked": [AT_CALL],
    "subprocess-run-partial-walrus-called": ["(alias := run_it)(errors='replace')"],
    "subprocess-run-partial-walrus-handed-to-a-def": ["run(cmd, errors='replace')"],
    "subprocess-run-partial-walrus-handed-to-an-import": ["helpers.probe(cmd, run=(alias := run_it))"],
    "subprocess-run-partial-walrus-in-a-container": ["alias := run_it"],
    "subprocess-run-walrus-returned": ["run := subprocess.run"],
    "subprocess-run-partial-into-a-star": [WRITTEN],
    "subprocess-run-partial-into-a-star-among-others": [WRITTEN],
    "subprocess-run-partial-chained-with-a-name": [WRITTEN],
    "subprocess-run-partial-chained-with-a-name-called": [WRITTEN, AT_CALL],
    "subprocess-run-partial-unpacked-to-an-attribute": [WRITTEN],
    "subprocess-run-partial-to-a-subscript": [WRITTEN],
    "subprocess-run-partial-unpacked-from-a-name": [WRITTEN],
    "subprocess-run-partial-unpacked-with-a-star-in-the-value": [WRITTEN],
    "subprocess-run-partial-unpacked-to-another-length": [WRITTEN],
    "subprocess-run-partial-augmented": [WRITTEN],
    "subprocess-run-partial-with-as": [WRITTEN],
    "subprocess-run-partial-for": [WRITTEN],
    "subprocess-run-partial-comprehension": [WRITTEN],
}


@pytest.mark.parametrize("row", list(FOUND_AT))
def test_each_target_form_is_found_where_the_scan_follows_it_to(row):
    assert [call for _, call, _ in locale_calls(LOCALE[row])] == FOUND_AT[row]


def _sequences(layers: int) -> list[tuple[type, tuple]]:
    """Each tuple and list target of the small grammar: of one to three (in an inner layer) or one or two (in the outer)
    of a name, a starred name (at most one in each) and, with a layer to spare, an inner one."""
    elements = ["name", "star", *(_sequences(layers - 1) if layers > 1 else [])]
    shapes = []
    for length in range(1, (3 if layers == 1 else 2) + 1):
        shapes += [(kind, parts) for parts in itertools.product(elements, repeat=length) if parts.count("star") <= 1
                   for kind in (ast.Tuple, ast.List)]
    return shapes


def _leaves(shape, path: tuple = ()) -> list[tuple[tuple, str]]:
    """Each name of a target shape, by its place: the path to it and whether it is starred."""
    if isinstance(shape, str):
        return [(path, shape)]
    return [leaf for at, part in enumerate(shape[1]) for leaf in _leaves(part, (*path, at))]


def _unfolded() -> list[tuple[str, int]]:
    """Each case of the grammar, with the one line it is found on. For each name of each target (a name, or a tuple or
    a list of `_sequences(2)`), a value written to match it, with a partial of a spawner at that name's place, as a
    name bound to it or written there, and a call of the name that reads text in the locale's encoding: found at the
    call when the name is followed, and where it is assigned when the name is starred (it takes a list). Each case is
    written twice: every other starred name taking none, a tuple or a list written as the target's, and the partial
    by its name; then every other starred name taking two, the other of tuple and list, and the partial written
    there."""
    other = ast.Name("cmd", ast.Load())
    flip = {ast.Tuple: ast.List, ast.List: ast.Tuple}
    cases = []
    for shape in ["name", *_sequences(2)]:
        for chosen, kind in _leaves(shape):
            for written, spare, flipped in ((False, 0, False), (True, 2, True)):
                carrier = ast.parse("functools.partial(subprocess.run, cmd, capture_output=True)" if written
                                    else "run_it", mode="eval").body
                names = iter(f"n{at}" for at in range(64))
                called = {}

                def target(part, path=()):
                    if isinstance(part, str):
                        called[path] = next(names)
                        name = ast.Name(called[path], ast.Store())
                        return ast.Starred(name, ast.Store()) if part == "star" else name
                    return part[0]([target(inner, (*path, at)) for at, inner in enumerate(part[1])], ast.Store())

                def value(part, path=()):
                    """What `part` takes, written: a name one element, a starred name `spare` of them (the partial
                    after half of them, for the chosen one); an inner tuple or list, one."""
                    if isinstance(part, str):
                        if path != chosen:
                            return [other] if part == "name" else [other] * spare
                        return [carrier] if part == "name" else [*[other] * (spare // 2), carrier]
                    elements = [element for at, inner in enumerate(part[1]) for element in value(inner, (*path, at))]
                    return [(flip[part[0]] if flipped else part[0])(elements, ast.Load())]

                assigned = ast.unparse(ast.Assign([target(shape)], value(shape)[0], lineno=1))
                cases.append((f"{BOUND}{assigned}\n{called[chosen]}(errors='replace')\n", 3 if kind == "star" else 4))
    return cases


def test_a_partial_bound_by_any_target_of_the_grammar_is_found_once():
    cases = _unfolded()
    wrong = [(source, found) for source, line in cases
             if (found := [at for at, _, _ in locale_calls(source)]) != [line]]
    assert not wrong, f"{len(wrong)} of {len(cases)} cases not found once where they should be, as: {wrong[:3]}"
