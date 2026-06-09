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
