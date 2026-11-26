# SPDX-License-Identifier: Apache-2.0
"""The model a codex generation runs on (docs/gen.md, "The codex driver model"): the built-in light driver is named
on every call, `--model` wins, then the saved one, then the built-in; a driver codex refuses fails naming it, with
nothing run on codex's own default; `defaults show` and `capabilities` say which driver is in effect."""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from sprite_gen.gen import codex_provider
from sprite_gen.gen.base import GenRequest, verify_png
from sprite_gen.workflow import preferences

REPO_ROOT = Path(__file__).parents[2]
SESSION = "018f0000-0000-7000-8000-0000000000d1"
# What codex 0.169.1 says on stdout for a model the ChatGPT login does not serve, then exits 1.
REFUSAL = "The 'gpt-no-such-model' model is not supported when using Codex with a ChatGPT account."


def _rollout(codex_home: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 255, 255)).save(buffer, format="PNG")
    path = codex_home / "sessions" / "2026" / "08" / "03" / f"rollout-2026-08-03T02-40-51-{SESSION}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"payload": {"type": "image_generation_end", "status": "completed",
                                            "result": base64.b64encode(buffer.getvalue()).decode("ascii")}}) + "\n",
                    encoding="utf-8")


@pytest.fixture
def codex_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Every `codex exec` command a generation runs (`calls`); each answers with one image, or with codex's refusal
    of the model when `refuse` is set."""
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex_provider, "codex_login", lambda: codex_provider.CodexLogin(codex_provider.AUTH_SOURCE_LOGIN))
    seen = SimpleNamespace(calls=[], refuse=False)

    def run(cmd, **kwargs):
        seen.calls.append(cmd)
        if seen.refuse:
            events = [{"type": "thread.started", "thread_id": SESSION}, {"type": "error", "message": REFUSAL},
                      {"type": "turn.failed", "error": {"message": REFUSAL}}]
            return subprocess.CompletedProcess(cmd, 1, "\n".join(json.dumps(e) for e in events) + "\n", "")
        _rollout(codex_home)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "thread.started", "thread_id": SESSION}) + "\n", "")

    monkeypatch.setattr(codex_provider.subprocess, "run", run)
    return seen


def _generate(tmp_path: Path, *, model: str | None = None, refs: list[Path] | None = None):
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    request = GenRequest(prompt="a mushroom", raw=workdir / "raw.png", model=model, refs=refs or [])
    return codex_provider.CodexProvider().generate(request, workdir)


def _named(cmd: list[str]) -> tuple[str, str]:
    return cmd[cmd.index("--model") + 1], cmd[cmd.index("-c") + 1]


def _save(**general: str) -> None:
    preferences.save_settings(None, {}, general=general, expected_revision=preferences.read_settings()["revision"])


def test_the_built_in_light_driver_is_named_on_every_call_over_the_login_config(tmp_path, codex_runs):
    # medium, not low: at low the driver passed image_gen the prompt without its last line in 2 of 6 calls
    assert (codex_provider.DRIVER_MODEL, codex_provider.DRIVER_EFFORT) == ("gpt-6.1-terra", "medium")
    ref = tmp_path / "ref.png"
    Image.new("RGB", (4, 4)).save(ref)
    run = _generate(tmp_path, refs=[ref])
    (cmd,) = codex_runs.calls
    assert _named(cmd) == (codex_provider.DRIVER_MODEL, f'model_reasoning_effort="{codex_provider.DRIVER_EFFORT}"')
    assert cmd.index("-c") < cmd.index("-i")  # before the image list, which takes every path after it
    assert run.model == codex_provider.DRIVER_MODEL
    assert run.extra["driver"] == {"model": codex_provider.DRIVER_MODEL, "effort": codex_provider.DRIVER_EFFORT,
                                   "model_from": "built-in", "effort_from": "built-in"}
    assert verify_png(tmp_path / "work" / "raw.png") > 0


def test_the_saved_driver_beats_the_built_in_one(tmp_path, codex_runs):
    _save(codex_model="gpt-6-luna", codex_effort="medium")
    run = _generate(tmp_path)
    assert _named(codex_runs.calls[0]) == ("gpt-6-luna", 'model_reasoning_effort="medium"')
    assert run.extra["driver"] == {"model": "gpt-6-luna", "effort": "medium", "model_from": "saved",
                                   "effort_from": "saved"}


def test_model_wins_over_the_saved_and_the_built_in_driver(tmp_path, codex_runs):
    _save(codex_model="gpt-6-luna")
    run = _generate(tmp_path, model="gpt-6.2-astra")
    assert _named(codex_runs.calls[0]) == ("gpt-6.2-astra", f'model_reasoning_effort="{codex_provider.DRIVER_EFFORT}"')
    assert codex_runs.calls[0].count("--model") == 1
    assert run.model == "gpt-6.2-astra" and run.extra["driver"]["model_from"] == "--model"


@pytest.mark.parametrize("saved, model, source, remedy", [
    ({}, None, "built-in", "name one with --model"),
    ({"codex_model": "gpt-no-such-model"}, None, "saved", "sprite-gen defaults clear --setting codex_model"),
    ({}, "gpt-no-such-model", "--model", "drop --model or name another"),
])
def test_a_driver_codex_refuses_fails_naming_it_and_nothing_runs_on_another(tmp_path, codex_runs, monkeypatch,
                                                                           saved, model, source, remedy):
    if saved:
        _save(**saved)
    else:
        monkeypatch.setattr(codex_provider, "DRIVER_MODEL", "gpt-no-such-model")
    codex_runs.refuse = True
    with pytest.raises(SystemExit) as failed:
        _generate(tmp_path, model=model)
    message = str(failed.value)
    assert f"driver model 'gpt-no-such-model' ({source})" in message and REFUSAL in message
    assert "nothing fell back to codex's own default model" in message and remedy in message
    assert len(codex_runs.calls) == 1 and _named(codex_runs.calls[0])[0] == "gpt-no-such-model"
    assert not isinstance(failed.value, codex_provider.CodexUsageLimit)  # not turned into another provider


def test_a_settings_file_that_cannot_be_read_stops_the_call_before_codex_runs(tmp_path, codex_runs):
    path = preferences.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version":2,"profiles":{},"general":{"codex_model":"bad model"}}', encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot read the saved one"):
        _generate(tmp_path)
    assert codex_runs.calls == []


def test_no_image_gen_names_the_driver_it_ran_on(tmp_path, codex_runs, monkeypatch):
    monkeypatch.setattr(codex_provider, "_collect_inline_results", lambda rollout: [])
    with pytest.raises(SystemExit) as failed:
        _generate(tmp_path)
    assert f"driver model {codex_provider.DRIVER_MODEL!r} (built-in)" in str(failed.value)


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "sprite_gen.cli", *args], capture_output=True, encoding="utf-8",
                          cwd=REPO_ROOT)


def test_defaults_and_capabilities_say_the_driver_in_effect_and_clear_removes_it():
    built_in = {"model": codex_provider.DRIVER_MODEL, "effort": codex_provider.DRIVER_EFFORT,
                "model_from": "built-in", "effort_from": "built-in"}
    shown = json.loads(_cli("defaults", "show").stdout)
    assert shown["codex_driver"] == built_in
    capabilities = _cli("capabilities").stdout
    assert capabilities.count("\n") == 1  # still one line
    assert json.loads(capabilities)["codex_driver"] == {**built_in, "built_in": {"model": built_in["model"],
                                                                                 "effort": built_in["effort"]}}
    saved = _cli("defaults", "save", "--codex-model", "gpt-6-luna", "--expected-revision", shown["revision"])
    assert saved.returncode == 0, saved.stderr
    saved = json.loads(saved.stdout)
    assert saved["general"] == {"codex_model": "gpt-6-luna"}
    assert saved["codex_driver"] == {**built_in, "model": "gpt-6-luna", "model_from": "saved"}
    assert json.loads(_cli("capabilities").stdout)["codex_driver"]["model"] == "gpt-6-luna"
    cleared = _cli("defaults", "clear", "--setting", "codex_model", "--expected-revision", saved["revision"])
    assert cleared.returncode == 0, cleared.stderr
    cleared = json.loads(cleared.stdout)
    assert cleared["general"] == {} and cleared["codex_driver"] == built_in
    for refused in (("--codex-model", "bad model"), ("--codex-effort", "ultra")):
        assert _cli("defaults", "save", *refused, "--expected-revision", cleared["revision"]).returncode != 0


def test_gen_md_names_the_built_in_driver():
    doc = (REPO_ROOT / "docs" / "gen.md").read_text(encoding="utf-8")
    assert f"`{codex_provider.DRIVER_MODEL}`" in doc and f"`{codex_provider.DRIVER_EFFORT}`" in doc
