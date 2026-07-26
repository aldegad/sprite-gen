# Security

This repository uses `safedeps` as the local security gate for agent-driven
dependency changes and commit-time secret scanning.

## Dependency Changes

Before adding a dependency with npm, pip, cargo, Go, RubyGems, Maven, or NuGet,
run the advisory gate first:

```bash
safedeps check <ecosystem> <pkg>@<version-or-range> --json
```

Install only after the result is `clean` or `already_approved`, and use the
reported `install_hint` or `suggested_spec` exactly. Do not install when the
provider is unavailable, when a CISA KEV match is reported, or when no patched
version is available.

## Secret Scanning

The repository-owned secret policy is `.gitleaks.toml`. The repo-local
pre-commit hook is installed through `core.hooksPath=.githooks` and runs:

```bash
safedeps scan secrets --staged --root .
```

Do not commit real `.env` files or secret-bearing local configuration. Keep
example files limited to placeholders.

## Current Dependency Surface

The runtime surface is two PyPI packages, both declared directly in
`pyproject.toml`: **Pillow** (image I/O and the PIL-vector paths) and **NumPy**
(the vectorized chroma extraction path). Neither may be relied on transitively —
a package that arrives only because some other dependency pulls it in can leave
on the next clean environment, and this package's own code must not be at the
mercy of another package's dependency list.

NumPy was cleared through the advisory gate above before it was declared, and
every version that `numpy>=2.2.6,<3` resolves to on a supported interpreter
carries a live approval: 2.2.6 on CPython 3.10, 2.4.6 on 3.11, and 2.5.1 on 3.12
and later.

Pillow is a separate matter and predates this section, so this document does not
claim it is cleared. Its ledger approval for 12.2.0 is recorded as revoked, and
12.3.0 — the version `pillow>=12.0,<13` actually resolves to today — has no
ledger entry at all. The declared Pillow range is therefore not covered by a
live approval. Closing that gap is tracked separately and is not resolved here.

A pure-Python fallback for a missing NumPy is not permitted. The extraction path
carries a byte-identity contract, and a second code path for the same contract
is two answers to one question, so an interpreter without NumPy must fail
loudly rather than silently take a slower route. `sprite_gen/_deps.py` is the
single module that imports NumPy and is imported by `sprite_gen/__init__.py`, so
that failure happens at package import and names both the interpreter that was
used and the install command for the skill venv;
`tests/test_numpy_dependency_gate.py` holds it there.

This repository currently has no npm lockfile, so `safedeps audit npm` cannot
produce a reproducible npm verdict yet. If a package manager is added later,
commit the lockfile and let the pre-commit hook audit it.

## Release Gates

Run the local release gate before a release:

```bash
safedeps gates run --root . --strict
```

GitHub security workflows and branch protection are opt-in for this repository
because they can spend runner minutes or change remote governance.
