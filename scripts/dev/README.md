# scripts/dev — maintainer experiments, not pipeline stages

These are not `sprite-gen` verbs and the skill never requires them. Each one is a
measurement or a proof that fed a rule now living in the package or its docs:

| Script | What it measures |
|---|---|
| `breathe_mutation_battery.py` | plants each breathe-contract mutation in the source and checks the tests bite (exit 0 = every net bites) |
| `measure_align_sigma.py` | per-frame horizontal jitter σ of `fit.align_x` variants on a source run's raw strips |
| `validate_pr6_subject_profile.py` | same-raw A/B proof for the subject-specific sparse floor (PR #6) |
| `check_visible_magenta.py` | chroma-leak guard for screenshots (`sprite_gen.frames.check_visible_magenta`) |

Run them with the project interpreter (`.venv/bin/python scripts/dev/<name>.py`).
