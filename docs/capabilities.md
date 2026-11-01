# `sprite-gen capabilities`: what the engine can do, for a machine to read

> Owns: `sprite-gen capabilities`: the machine-readable list of every verb and argument, read off the CLI's parser, the providers each verb may call and be billed by, and the provider address and login variables · Index: [docs/README.md](README.md)

```bash
sprite-gen capabilities > capabilities.json
```

It prints one JSON line and takes no arguments. An agent driving the engine reads
it once instead of running `--help` for every verb. It is about a quarter of the
bytes of the top-level help and every verb's and old name's `--help` together (piped,
at the default width).

Every verb and every argument is read off the parser the CLI runs (`cli.COMMANDS`
and the parser built from it). A verb or flag added to the CLI is listed with
nothing else written, and an argument `--help` hides is not listed either. The one
fact the parser does not hold is which providers a verb may call. That is the table
`PROVIDER_CALLS` in `sprite_gen/workflow/capabilities.py`, the only place it is
written. `tests/workflow/test_capabilities.py` checks the list against the parser and
against every verb's printed options.

## The document

| Key | What it holds |
|---|---|
| `kind` | `sprite-gen-capabilities` |
| `version` | the installed package's version (`unknown` from a bare checkout) |
| `providers` | per provider (`openai`, `xai`, `anthropic`): `base_url_env` (the variable that moves its calls), `base_url` (the documented address used when it is unset), `key_env` (its API key); docs/gen.md, [provider addresses](gen.md#provider-addresses) |
| `logins_env` | `SPRITE_GEN_LOGINS`, where `off` turns subscription logins off ([subscription logins off](gen.md#subscription-logins-off)) |
| `verbs` | every verb, in the order of `sprite-gen --help`'s table |

The document says what the engine can do, not how this process is set up: a variable
set in the environment does not change it.

Each verb:

| Key | What it holds |
|---|---|
| `domain` | its group in `sprite-gen --help` (`sprite_gen/_modules.py`) |
| `description` | the line `sprite-gen --help` prints for it |
| `providers` | the providers it may call over HTTP; absent when it calls none |
| `paid` | whether running it may be billed by a provider: true for every verb that calls one, except `video-resume`, which asks for a clip an earlier run bought |
| `old_names` | earlier names that still run it (`video-follow` for `video-sway`) |
| `args` | its arguments, in the order `--help` lists them |
| `exclusive` | groups of which at most one is given: `one_of` (argument names), `required` when one must be |
| `commands` | its own sub-verbs (`rife install`), each with `description`, `args` and `exclusive` |

Each argument holds only what applies:

| Key | What it holds |
|---|---|
| `name` | its long spelling (`--out`), or its name for a positional one |
| `aliases` | its other spellings (`-o`, `--no-loop`) |
| `positional` | true for a positional argument |
| `type` | `path`, `int`, `float`, `flag` (takes no value), or the name of the reading function the parser uses (`grid` for `COLSxROWS`, `region`, `box`, ...); absent for text |
| `nargs` | how many values, as argparse says it (`*`, `?`) |
| `repeat` | true when it may be given more than once |
| `default` | its default; a flag's only when it is not off |
| `required` | true when it must be given |
| `choices` | the values it takes |

Paths are written with `/`. A verb's argument meanings, units and examples are in
its `--help` and its doc ([docs/README.md](README.md)).

## The providers each verb may call

| Verb | Providers | Paid |
|---|---|---|
| `gen`, `gen-set`, `logo` | `openai` (`--provider openai`), `xai` (`--provider grok`); codex runs on its own login | yes |
| `pixel-draw` | `anthropic` | yes |
| `curation` | `xai` (the reroll and in-between buttons on grok; codex on its own login) | yes |
| `video`, `video-extend`, `video-edit`, `video-set` | `xai` | yes |
| `video-resume` | `xai` | no |

Every other verb calls no provider.
