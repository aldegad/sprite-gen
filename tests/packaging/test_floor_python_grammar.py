# SPDX-License-Identifier: Apache-2.0
"""The oldest Python pyproject.toml supports (the floor of `requires-python`) reads every file of the package.

From 4.0.0 to 4.3.0 it did not: `sprite_gen/qa/holds.py` held an f-string nested in an f-string with the same quotes,
which only Python 3.12 and later read (PEP 701), and `sprite_gen.cli` imports that module at load, so on Python 3.11
every `sprite-gen` command stopped with a SyntaxError. The lane runs a newer Python, which read it.

Two checks, and both are needed. `ast.parse(source, feature_version=floor)` refuses the newer grammar its parser gates
(a `type` statement, type parameters, `except A, B:`), but it reads every f-string the running Python reads, the five
forms below among them. So while the floor is under 3.12, every f-string is also walked with the running tokenizer
(which gives an f-string's parts as tokens from 3.12 on) and held to the four rules Python 3.11 holds a replacement
field to, measured on 3.11:
  - no quote that closes an f-string around it (3.11 reads an f-string as a plain string first, so that quote ends it);
  - no backslash, in a nested string or not;
  - no comment (a `#` inside a nested string, or in a format spec such as `{n:#x}`, 3.11 reads);
  - no line break inside a single-quoted f-string.
On a Python older than 3.12 the parser is itself the old one, and `ast.parse` alone asks all of it.

Not caught here: names the standard library added after the floor (`itertools.batched`, `typing.override`), code both
read that runs differently, a format spec nested three deep (`{a:{b:{c}}}`, "expressions nested too deeply" on 3.11),
and new grammar `feature_version` does not gate. A new form needs its own sample below."""

import ast
import io
import sys
import tokenize
from pathlib import Path

import pytest

from test_project_metadata import ROOT, _declared_min_python, _version_tuple

PACKAGE = ROOT / "sprite_gen"
# The tokenizer gives an f-string's parts as tokens (FSTRING_START, FSTRING_MIDDLE, FSTRING_END) from 3.12 on.
FSTRING_TOKENS = sys.version_info >= (3, 12)

QUOTE = "a quote that closes the f-string around it"
BACKSLASH = "a backslash"
COMMENT = "a comment"
LINE_BREAK = "a line break inside a single-quoted f-string"


class _Field:
    """An f-string being walked: the quote that closes it, and where its replacement field stands."""

    def __init__(self, start: tokenize.TokenInfo) -> None:
        prefix_free = start.string.lstrip("rRfFbBuU")
        self.quote = prefix_free[:3] if prefix_free[:3] in ('"""', "'''") else prefix_free[:1]
        self.open = False  # inside a replacement field
        self.spec = False  # past the field's ':', in its format spec
        self.brackets = 0  # brackets opened inside the field and not yet closed


def fstring_rules_broken(source: str) -> list[tuple[int, str]]:
    """(line, rule) for each place an f-string of `source` breaks a rule Python 3.11 holds a replacement field to.
    Needs a tokenizer that gives an f-string's parts (`FSTRING_TOKENS`)."""
    lines = source.splitlines(keepends=True)

    def between(end: tuple[int, int], start: tuple[int, int]) -> str:
        """The text between two tokens: blanks, a line break, or a backslash that continues the line."""
        if end[0] == start[0]:
            return lines[end[0] - 1][end[1]:start[1]]
        return lines[end[0] - 1][end[1]:] + "".join(lines[end[0]:start[0] - 1]) + lines[start[0] - 1][:start[1]]

    found: list[tuple[int, str]] = []
    stack: list[_Field] = []
    before = (1, 0)
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        text = tok.string
        gap = between(before, tok.start) if stack else ""
        before = tok.end
        for depth, field in enumerate(stack):
            innermost = depth == len(stack) - 1
            # A token inside an f-string nested in this one's field is inside this one's field; the innermost
            # f-string's own literal text, and its format spec text, are not.
            if innermost and (not field.open or (tok.type == tokenize.FSTRING_MIDDLE and field.spec
                                                 and field.brackets == 0)):
                continue
            if field.quote in text:
                found.append((tok.start[0], QUOTE))
            if "\\" in text or "\\" in gap:
                found.append((tok.start[0], BACKSLASH))
            if tok.type == tokenize.COMMENT:
                found.append((tok.start[0], COMMENT))
            if len(field.quote) == 1 and ("\n" in text or "\n" in gap):
                found.append((tok.start[0], LINE_BREAK))
        if tok.type == tokenize.FSTRING_START:
            stack.append(_Field(tok))
        elif tok.type == tokenize.FSTRING_END:
            stack.pop()
        elif stack and tok.type == tokenize.OP:
            field = stack[-1]
            if not field.open:
                field.open = tok.string == "{"
            elif tok.string == ":" and field.brackets == 0:
                field.spec = True
            elif tok.string in "([{":
                field.brackets += 1
            elif tok.string in ")]}":
                if field.brackets == 0 and tok.string == "}":
                    field.open = field.spec = False
                else:
                    field.brackets -= 1
    return sorted(set(found))


def unread(source: str, floor: tuple[int, int]) -> list[tuple[int, str]]:
    """(line, why) for each place Python `floor` cannot read `source`; empty where it reads it all."""
    try:
        ast.parse(source, feature_version=floor)
    except SyntaxError as exc:
        return [(exc.lineno or 0, exc.msg)]
    if floor >= (3, 12) or not FSTRING_TOKENS:
        return []
    return fstring_rules_broken(source)


def test_the_floor_python_reads_every_file_of_the_package() -> None:
    floor = _version_tuple(_declared_min_python())
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, f"no .py file under {PACKAGE}"
    unreadable = [f"{path.relative_to(ROOT).as_posix()}:{line}: {why}"
                  for path in files for line, why in unread(path.read_text(encoding="utf-8"), floor)]
    assert not unreadable, (f"Python {floor[0]}.{floor[1]} (requires-python's floor in pyproject.toml) cannot read:\n"
                            + "\n".join(unreadable))


# --- the checker held to samples --------------------------------------------------------------------------------------

# The f-string forms only Python 3.12 and later read (PEP 701), one a sample, and the rule each breaks. The first is the
# line holds.py held from 4.0.0 to 4.3.0.
PEP_701 = {
    "same quotes nested (holds.py in 4.3.0)": (
        "runs = \"; \".join(f\"{name} {', '.join(str(r['from']) if r['from'] == r['to'] else f'{r['from']}-{r['to']}' "
        "for r in row['unlistedRuns'])}\"\n"
        "                 for name, row in report[\"table\"].items() if row[\"unlisted\"])\n", QUOTE),
    "a backslash in a nested string": ("s = f\"{'\\n'.join(xs)}\"\n", BACKSLASH),
    "a comment in a field": ('s = f"""{x  # a comment\n}"""\n', COMMENT),
    "a line break in a single-quoted field": ('s = f"{x\n}"\n', LINE_BREAK),
    "same quotes three deep": ("s = f\"{f'{f\"{x}\"}'}\"\n", QUOTE),
}

# Grammar after 3.11 that `feature_version` refuses.
NEWER_GRAMMAR = {
    "a type statement": "type Point = tuple[float, float]\n",
    "type parameters": "def first[T](xs: list[T]) -> T:\n    return xs[0]\n",
    "except without parentheses": "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n",
}

# Forms Python 3.11 reads, each beside a rule it might be mistaken for breaking.
READ_BY_3_11 = {
    "other quotes nested": "s = f\"{r['from']} {', '.join(xs)}\"\n",
    "same quotes inside a triple-quoted f-string": 's = f"""{r["from"]}"""\n',
    "a single quote inside a triple-quoted one": "s = f'''{\"it's\"}'''\n",
    "a # in a format spec": 's = f"{tag:#x} {n:>{width}}"\n',
    "a # in a nested string": "s = f\"{'#'} {f'#'}\"\n",
    "a backslash in a format spec": 's = f"{n:\\x41>5}"\n',
    "a backslash in the literal text": 's = f"a\\n{x}\\tb"\n',
    "a line break inside a triple-quoted field": 's = f"""{x +\n y}"""\n',
    "the line holds.py holds now": (
        "runs = \"; \".join(f\"{name} {', '.join(str(r['from']) if r['from'] == r['to'] else str(r['from']) + '-' + "
        "str(r['to']) for r in row['unlistedRuns'])}\"\n"
        "                 for name, row in report[\"table\"].items() if row[\"unlisted\"])\n"),
}


@pytest.mark.parametrize("form", list(PEP_701))
def test_each_f_string_form_only_3_12_reads_is_refused_at_a_floor_of_3_11(form: str) -> None:
    source, rule = PEP_701[form]
    found = unread(source, (3, 11))
    assert found, f"read at a floor of 3.11: {source!r}"
    if FSTRING_TOKENS:
        assert rule in {why for _line, why in found}, found


@pytest.mark.parametrize("form", list(NEWER_GRAMMAR))
def test_newer_grammar_is_refused_at_a_floor_of_3_11(form: str) -> None:
    assert unread(NEWER_GRAMMAR[form], (3, 11))


@pytest.mark.parametrize("form", list(READ_BY_3_11))
def test_what_3_11_reads_is_read_at_a_floor_of_3_11(form: str) -> None:
    assert unread(READ_BY_3_11[form], (3, 11)) == []


def test_a_floor_raised_to_3_12_lets_the_f_string_forms_through() -> None:
    """requires-python raised to 3.12, the f-string rules lift: the five forms are read wherever the running Python reads
    them (3.12 and later), and refused only by a Python that cannot read them itself."""
    readable_here = sys.version_info >= (3, 12)
    assert {form: unread(source, (3, 12)) == [] for form, (source, _rule) in PEP_701.items()} == dict.fromkeys(
        PEP_701, readable_here)
