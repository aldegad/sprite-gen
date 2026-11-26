# SPDX-License-Identifier: Apache-2.0
"""How codex is signed in is one question (`codex_provider.codex_login`) for the default provider
resolution, `generate`, the facing check and the guided workflow: ChatGPT is the subscription, an
API key is billed per call and said on stderr before the request leaves, and a codex that is not
signed in is not usable, with its reason. No codex binary: a fake `subprocess.run`."""
from __future__ import annotations

import base64
import io
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from sprite_gen import gen
from sprite_gen.gen import codex_provider
from sprite_gen.gen.base import CodexUnavailableAsk, GenRequest
from sprite_gen.workflow.access import probe_access


def status(code: int, said: str):
    return lambda cmd, **kw: SimpleNamespace(returncode=code, stdout=said, stderr="")


@pytest.mark.parametrize("run, expected", [
    (status(0, "Logged in using ChatGPT\n"), codex_provider.CodexLogin("codex-login")),
    (status(0, "Logged in using an API key - sk-proj-***\n"), codex_provider.CodexLogin("codex-api-key")),
    (status(1, "Not logged in\n"), codex_provider.CodexLogin(None, "codex not logged in (codex login status exit 1)")),
])
def test_the_login_is_chatgpt_an_api_key_or_not_usable(monkeypatch, run, expected):
    monkeypatch.setattr(codex_provider.subprocess, "run", run)
    assert codex_provider.codex_login() == expected


def test_no_cli_and_a_failed_probe_are_not_usable_with_the_reason(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    def stuck(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 15)

    monkeypatch.setattr(codex_provider.subprocess, "run", missing)
    assert codex_provider.codex_login() == codex_provider.CodexLogin(None, "codex CLI not found on PATH")
    monkeypatch.setattr(codex_provider.subprocess, "run", stuck)
    assert codex_provider.codex_login() == codex_provider.CodexLogin(None, "codex login-status probe failed: TimeoutExpired")


def test_the_default_resolution_asks_the_same_question(monkeypatch):
    monkeypatch.delenv(gen.DEFAULT_PROVIDER_ENV, raising=False)
    monkeypatch.setattr(codex_provider, "codex_login", lambda: codex_provider.CodexLogin(None, "codex CLI not found on PATH"))
    with pytest.raises(CodexUnavailableAsk) as asked:  # nothing chosen for a codex that cannot run: the question
        gen.resolve_default_provider()
    assert asked.value.question["cause"] == "login" and asked.value.question["reason"] == "codex CLI not found on PATH"
    monkeypatch.setenv(gen.CODEX_UNAVAILABLE_ENV, "grok")
    assert gen.resolve_default_provider() == ("grok", {"from": "codex", "to": "grok", "cause": "login",
                                                       "reason": "codex CLI not found on PATH",
                                                       "default_source": "hard-default",
                                                       "chosen_by": gen.CODEX_UNAVAILABLE_ENV})
    monkeypatch.setattr(codex_provider, "codex_login", lambda: codex_provider.CodexLogin("codex-api-key"))
    assert gen.resolve_default_provider() == ("codex", None)


def _png() -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _generate(monkeypatch, tmp_path, login):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(codex_provider, "codex_login", lambda: login)
    asked = []

    def run(cmd, **kw):
        asked.append(cmd)
        return SimpleNamespace(returncode=0, stdout='{"type":"thread.started","thread_id":"aaaa-bbbb"}\n', stderr="")

    monkeypatch.setattr(codex_provider.subprocess, "run", run)
    (tmp_path / "rollout.jsonl").write_text("", encoding="utf-8")  # the passed prompt is read from it
    monkeypatch.setattr(codex_provider, "_resolve_rollout", lambda *a, **k: tmp_path / "rollout.jsonl")
    monkeypatch.setattr(codex_provider, "_collect_inline_results", lambda _p: [codex_provider.InlineResult(_png())])
    result = codex_provider.CodexProvider(keep_session=True).generate(GenRequest(prompt="a slime", raw=tmp_path / "raw.png"),
                                                                      tmp_path)
    return result, asked


def test_generate_on_the_subscription_says_nothing_and_records_it(monkeypatch, tmp_path, capsys):
    result, asked = _generate(monkeypatch, tmp_path, codex_provider.CodexLogin("codex-login"))
    assert result.extra["auth_source"] == "codex-login" and len(asked) == 1
    assert "per-call API charge" not in capsys.readouterr().err


def test_generate_on_an_api_key_login_announces_the_charge_and_records_it(monkeypatch, tmp_path, capsys):
    result, _ = _generate(monkeypatch, tmp_path, codex_provider.CodexLogin("codex-api-key"))
    assert result.extra["auth_source"] == "codex-api-key"
    assert ("[gen] codex: running on the API key codex is signed in with — this is a per-call API charge, not a "
            "subscription — `codex login` signs your ChatGPT subscription in.") in capsys.readouterr().err


def test_generate_without_a_login_is_refused_before_codex_runs(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match=r"codex-gen: codex not logged in \(codex login status exit 1\); `codex login`"):
        _generate(monkeypatch, tmp_path, codex_provider.CodexLogin(None, "codex not logged in (codex login status exit 1)"))


@pytest.mark.parametrize("login, expected", [
    (codex_provider.CodexLogin("codex-login"), {"login": "ready", "billing": "subscription"}),
    (codex_provider.CodexLogin("codex-api-key"), {"login": "ready", "billing": "api-credit"}),
    (codex_provider.CodexLogin(None, "codex CLI not found on PATH"), {"login": "unavailable", "billing": "subscription"}),
])
def test_the_workflow_reads_the_same_login(monkeypatch, login, expected):
    monkeypatch.setattr(codex_provider, "codex_login", lambda: login)
    state = probe_access("codex")
    assert {k: state[k] for k in expected} == expected


def test_the_binary_every_codex_call_runs_is_named_as_written():
    assert codex_provider.BINARY == "codex"
