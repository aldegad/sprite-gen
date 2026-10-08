# SPDX-License-Identifier: Apache-2.0
"""Every row prompt `prepare` writes for a character with no body plan, drawn through `prepare.run`.

The sheet rows were measured on people, and a body plan (`--body-plan`, 2.33.0) changes only what is said
to a body that is not one biped. This table is the evidence that a run with no body plan, or one biped, is
the 2.32.0 run to the byte: it draws every row prompt for every state with a sentence of its own (the walk
and run rows, the diagonal runs, the wave and the jump), the default states, a state with no sentence,
with and without a base image, and a direction-contract run, through the entry point 2.32.0 already had —
so the same code runs against the tag's source and records what it wrote.

Record (from the repo root, the tag's source unpacked somewhere):

    git archive 44d6242 | tar -x -C /tmp/sprite-gen-2.32.0
    python tests/gen/prepare_freeze_table.py /tmp/sprite-gen-2.32.0 tests/fixtures/prepare-rows-v2.32.0.json.gz

`tests/gen/test_prompt_freeze.py` draws the table again against this tree and compares.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

CHARACTER = "A small fox adventurer in a green cloak"
# every state `prepare` has a sentence for, and one it has none for
ROW_STATES = ("running-right", "running-left", "running-front-right", "running-front-left", "running-back-right",
              "running-back-left", "run", "walk", "frontwalk", "45_frontwalk", "wave", "jump", "idle", "attack", "cheer")
DIRECTION_STATES = ("down_idle", "down_walk", "side_walk", "side_attack", "up_wave")


def _requests() -> Iterator[tuple[str, dict[str, Any]]]:
    """(label, `prepare.run` keyword arguments other than the run dir and base image)."""
    yield "default-states", {}
    # no action: a state `prepare` has a default for takes it; the others say their own
    yield "every-state", {"request_json": json.dumps({"states": {
        state: {"frames": 4} if state in ("idle", "attack", "jump", "wave") else {"frames": 6, "action": f"{state} cycle"}
        for state in ROW_STATES}})}
    yield "directions", {"request_json": json.dumps({"states": {state: {"frames": 4} for state in DIRECTION_STATES}}),
                         "directions": "down,side,up", "mirror": "left=side"}


def draw(**extra: Any) -> dict[str, str]:
    """Every row prompt, keyed by `<request>/<base>/<prompt path>`; `extra` goes to every `prepare.run`."""
    from PIL import Image
    from sprite_gen.gen import prepare

    out: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base.png"
        Image.new("RGB", (32, 32), (90, 140, 60)).save(base)
        for (label, kwargs), with_base in ((r, b) for r in _requests() for b in (False, True)):
            run_dir = Path(tmp) / f"{label}-{with_base}"
            with contextlib.redirect_stdout(io.StringIO()):
                prepare.run(out_dir=run_dir, character_id="fox", description=CHARACTER,
                            base_image=base if with_base else None, **kwargs, **extra)
            for prompt in sorted((run_dir / "prompts").rglob("*.txt")):
                name = f"{label}/{'base' if with_base else 'no-base'}/{prompt.relative_to(run_dir / 'prompts').as_posix()}"
                out[name] = prompt.read_text(encoding="utf-8")
    return out


def write(path: Path, table: dict[str, str]) -> None:
    data = json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True).encode() + b"\n"
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as out:
        out.write(data)


def read(path: Path) -> dict[str, str]:
    with gzip.open(path, "rb") as file:
        return json.loads(file.read())


if __name__ == "__main__":
    source, target = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    sys.path.insert(0, str(source))
    import sprite_gen

    assert Path(sprite_gen.__file__).resolve().is_relative_to(source), sprite_gen.__file__
    write(target, draw())
    print(f"{target}: {len(read(target))} prompts from {source}")
