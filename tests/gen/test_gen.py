# SPDX-License-Identifier: Apache-2.0
"""Offline tests for the sprite_gen.gen provider layer.

Network/OAuth provider calls (codex exec, grok) are exercised as live e2e in the
C-gen deliverable; here we lock the deterministic seams: PNG verification, the
codex inline-base64 extraction contract, prompt shape, chroma post-process, and
the orchestrator wiring with a fake provider.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen import gen
from sprite_gen.gen import base as gen_base
from sprite_gen.gen import chroma as chroma_mod
from sprite_gen.gen import codex_provider
from sprite_gen.gen.base import GenRequest


def _png_bytes(color=(255, 0, 255, 255), size=(4, 4)) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_verify_png_accepts_real_png_and_rejects_junk(tmp_path: Path) -> None:
    good = tmp_path / "good.png"
    good.write_bytes(_png_bytes())
    assert gen_base.verify_png(good) == len(good.read_bytes())

    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    with pytest.raises(SystemExit):
        gen_base.verify_png(bad)

    with pytest.raises(SystemExit):
        gen_base.verify_png(tmp_path / "missing.png")


def test_provider_subprocess_env_scrubs_orchestrator_session_env(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_RUNTIME_ENDPOINT_ID", "synthetic-endpoint")
    monkeypatch.setenv("ORCHESTRATOR_MEMBER_ID", "synthetic-worker")
    monkeypatch.setenv("ORCHESTRATOR_PROJECT_ID", "synthetic-project")
    monkeypatch.setenv("ORCHESTRATOR_PLAN_EXIT_GATE", "1")
    monkeypatch.setenv("ORCHESTRATOR_STUDIO_PORT", "4312")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = gen_base.provider_subprocess_env()

    assert not [
        key
        for key in env
        if key.endswith(gen_base._ORCHESTRATOR_SESSION_ENV_SUFFIXES)
    ], "orchestrator session variables must be scrubbed from provider environments"
    assert env.get("PATH") == "/usr/bin"  # 일반 env 는 유지


def test_provider_binary_resolves_windows_style_path_shims(monkeypatch) -> None:
    monkeypatch.setattr(gen_base.shutil, "which", lambda name: f"C:/tools/{name}.CMD")
    assert gen_base.provider_binary("codex") == "C:/tools/codex.CMD"
    monkeypatch.setattr(gen_base.shutil, "which", lambda _name: None)
    assert gen_base.provider_binary("codex") == "codex"


def test_provider_run_uses_scrubbed_env(tmp_path: Path, monkeypatch) -> None:
    # Both providers must route their subprocess.run through the scrubbed env.
    import sprite_gen.gen.grok_provider as grok_provider

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    seen: dict[str, dict | None] = {}

    class _Completed:
        returncode = 0
        stdout = '{"type":"thread.started","thread_id":"aaaa-bbbb"}\n'
        stderr = ""

    def _fake_codex_run(cmd, **kwargs):
        seen["codex"] = kwargs.get("env")
        seen["codex_cmd"] = cmd
        seen["codex_encoding"] = kwargs.get("encoding")
        return _Completed()

    # Short-circuit codex's post-run rollout parsing — we only assert the env.
    monkeypatch.setattr(codex_provider.subprocess, "run", _fake_codex_run)
    monkeypatch.setattr(
        codex_provider,
        "_resolve_rollout",
        lambda sid, sessions_root, *, preexisting: tmp_path / "x.jsonl",
    )
    b64 = base64.b64encode(_png_bytes()).decode()
    monkeypatch.setattr(codex_provider, "_collect_inline_results", lambda rollout: [b64])
    monkeypatch.setenv("ORCHESTRATOR_RUNTIME_ENDPOINT_ID", "synthetic-endpoint")
    codex_provider.CodexProvider(keep_session=True).generate(
        GenRequest(prompt="a mushroom", raw=tmp_path / "raw.png"), tmp_path
    )
    assert seen["codex"] is not None
    assert "ORCHESTRATOR_RUNTIME_ENDPOINT_ID" not in seen["codex"]
    assert seen["codex_encoding"] == "utf-8"

    grok_raw = tmp_path / "grok_raw.png"

    def _fake_grok_run(cmd, **kwargs):
        seen["grok"] = kwargs.get("env")
        seen["grok_cmd"] = cmd
        seen["grok_encoding"] = kwargs.get("encoding")
        # grok's truth is the PNG on disk — write one so verify_png passes.
        Image.new("RGBA", (8, 8), (255, 0, 255, 255)).save(grok_raw)
        class _C:
            returncode = 0
            stdout = str(grok_raw)
            stderr = ""
        return _C()

    monkeypatch.setattr(grok_provider.subprocess, "run", _fake_grok_run)
    grok_provider.GrokProvider().generate(
        GenRequest(prompt="a mushroom", raw=grok_raw), tmp_path
    )
    assert seen["grok"] is not None
    assert "ORCHESTRATOR_RUNTIME_ENDPOINT_ID" not in seen["grok"]
    assert seen["grok_encoding"] == "utf-8"


def test_provider_commands_use_the_single_resolved_binary(tmp_path: Path, monkeypatch) -> None:
    import sprite_gen.gen.grok_provider as grok_provider

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(codex_provider, "provider_binary", lambda _name: "C:/bin/codex.CMD")
    monkeypatch.setattr(grok_provider, "provider_binary", lambda _name: "C:/bin/grok.CMD")

    class _CodexCompleted:
        returncode = 0
        stdout = '{"type":"thread.started","thread_id":"aaaa-bbbb"}\n'
        stderr = ""

    def fake_codex(cmd, **_kwargs):
        seen["codex"] = cmd
        return _CodexCompleted()

    monkeypatch.setattr(codex_provider.subprocess, "run", fake_codex)
    monkeypatch.setattr(codex_provider, "_resolve_rollout", lambda *_args, **_kwargs: tmp_path / "rollout.jsonl")
    monkeypatch.setattr(codex_provider, "_collect_inline_results", lambda _path: [base64.b64encode(_png_bytes()).decode()])
    codex_provider.CodexProvider(keep_session=True).generate(
        GenRequest(prompt="도구", raw=tmp_path / "codex.png"), tmp_path
    )

    grok_out = tmp_path / "grok.png"

    class _GrokCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_grok(cmd, **_kwargs):
        seen["grok"] = cmd
        Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(grok_out)
        return _GrokCompleted()

    monkeypatch.setattr(grok_provider.subprocess, "run", fake_grok)
    grok_provider.GrokProvider().generate(GenRequest(prompt="도구", raw=grok_out), tmp_path)
    assert seen["codex"][0] == "C:/bin/codex.CMD"
    assert seen["grok"][0] == "C:/bin/grok.CMD"


def test_json_report_writes_utf8_bytes_without_reconfiguring_stdout(monkeypatch) -> None:
    import io
    import sys

    raw = io.BytesIO()
    cp949_stream = io.TextIOWrapper(raw, encoding="cp949")
    monkeypatch.setattr(sys, "stdout", cp949_stream)
    gen._print_json({"prompt": "도구 — 완료"})
    cp949_stream.detach()
    assert json.loads(raw.getvalue().decode("utf-8")) == {"prompt": "도구 — 완료"}


def test_codex_inline_extraction_reads_both_record_types(tmp_path: Path) -> None:
    b64 = base64.b64encode(_png_bytes()).decode()
    rollout = tmp_path / "rollout-test.jsonl"
    lines = [
        {"payload": {"type": "response_item", "text": "noise"}},
        {"payload": {"type": "image_generation_call", "result": b64}},
        {"payload": {"type": "image_generation_end", "result": b64, "status": "completed"}},
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    results = codex_provider._collect_inline_results(rollout)
    assert len(results) == 2

    dest = tmp_path / "decoded.png"
    codex_provider._decode_png(results[-1], dest)
    assert gen_base.verify_png(dest) > 0


def test_codex_inline_extraction_reads_0149_imagegen_extension(tmp_path: Path) -> None:
    b64 = base64.b64encode(_png_bytes()).decode()
    rollout = tmp_path / "rollout-codex-0149.jsonl"
    lines = [
        {
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "Extension",
                    "kind": "other.extension",
                    "status": "completed",
                    "result": b64,
                },
            }
        },
        {
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "Extension",
                    "kind": "image_gen.generation",
                    "status": "completed",
                    "result": b64,
                },
            }
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    results = codex_provider._collect_inline_results(rollout)

    assert results == [b64]


@pytest.mark.parametrize(
    ("status", "result", "expected_message"),
    [
        ("failed", "failure details", "status='failed'"),
        ("completed", None, "completed image_gen call has no result"),
    ],
)
def test_codex_inline_extraction_rejects_failed_or_empty_0149_imagegen_extension(
    tmp_path: Path,
    status: str,
    result: str | None,
    expected_message: str,
) -> None:
    rollout = tmp_path / "rollout-codex-0149-invalid.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "Extension",
                        "kind": "image_gen.generation",
                        "status": status,
                        "result": result,
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match=expected_message):
        codex_provider._collect_inline_results(rollout)


def test_codex_inline_extraction_rejects_failed_status(tmp_path: Path) -> None:
    b64 = base64.b64encode(_png_bytes()).decode()
    rollout = tmp_path / "rollout-fail.jsonl"
    rollout.write_text(
        json.dumps({"payload": {"type": "image_generation_end", "result": b64, "status": "failed"}}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        codex_provider._collect_inline_results(rollout)


def test_codex_parse_session_id_json_and_legacy() -> None:
    json_stdout = (
        '{"type":"thread.started","thread_id":"018f0000-0000-7000-8000-000000000001"}\n'
        '{"type":"turn.started"}\n'
    )
    assert codex_provider._parse_session_id(json_stdout) == "018f0000-0000-7000-8000-000000000001"

    legacy_stdout = "some header\nsession id: abc123de-0000-1111-2222-333344445555\ndone\n"
    assert codex_provider._parse_session_id(legacy_stdout) == "abc123de-0000-1111-2222-333344445555"

    assert codex_provider._parse_session_id("no id here") is None


def test_codex_prompt_carries_official_imagegen_skill_trigger() -> None:
    # Image generation lives in codex's bundled `imagegen` system skill, and the
    # official way to invoke a skill explicitly is the `$<skill>` prompt mention
    # (skills/.system/imagegen/agents/openai.yaml ships `default_prompt: "Use
    # $imagegen to make or edit an image for this project."`). This locks the
    # transport prompt to that contract; if the implicit prose phrasing comes
    # back, this goes red.
    #
    # Scope note: the trigger is a contract alignment, not a tool-exposure fix.
    # Built-in image generation is an account capability of the active Codex state
    # root — see codex_provider._no_image_records_message. Do not let this test's
    # name imply that the trigger is what makes image_gen available.
    user_prompt = "a mushroom sprite row on a #00FF00 background"
    prompt = codex_provider._build_prompt(user_prompt)

    assert codex_provider._SKILL_TRIGGER == "$imagegen"
    assert "$imagegen" in prompt, "the official skill trigger must be in the transport prompt"
    # It must lead the instruction, not trail the user's prompt as an aside.
    assert prompt.startswith("$imagegen "), prompt
    # The caller's sprite-request prompt is the SSoT — passed through verbatim.
    assert user_prompt in prompt
    # The rest of the transport contract survives the trigger.
    assert "정확히 1번" in prompt
    assert "금지" in prompt


def test_codex_empty_rollout_fails_loud_on_tool_non_exposure(tmp_path: Path, monkeypatch) -> None:
    # An empty rollout is the tool-non-exposure signature. It must fail loudly, name
    # the account capability behind the active Codex state root as the thing that is
    # missing, and point at the one real remedy — never fall back to another
    # provider on its own, never trust a model-reported path, and never send the
    # reader off to hunt for a config toggle.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    rollout = tmp_path / "rollout-empty.jsonl"

    class _Completed:
        returncode = 0
        stdout = '{"type":"thread.started","thread_id":"aaaa-bbbb"}\n'
        stderr = ""

    monkeypatch.setattr(codex_provider.subprocess, "run", lambda cmd, **kwargs: _Completed())
    monkeypatch.setattr(
        codex_provider,
        "_resolve_rollout",
        lambda sid, sessions_root, *, preexisting: rollout,
    )
    monkeypatch.setattr(codex_provider, "_collect_inline_results", lambda path: [])

    with pytest.raises(SystemExit) as excinfo:
        codex_provider.CodexProvider(keep_session=True).generate(
            GenRequest(prompt="a mushroom", raw=tmp_path / "raw.png"), tmp_path
        )

    message = str(excinfo.value)
    assert "image_gen tool never ran" in message
    assert "$imagegen" in message
    assert str(rollout) in message
    # The message must name the missing capability and the state root that lacks
    # it, and must close off the config-toggle detour.
    assert "capability" in message
    assert str(tmp_path) in message, "the failing Codex state root must be named"
    assert "does not grant it" in message
    assert not (tmp_path / "raw.png").exists(), "a failed run must not leave a raw PNG"


def test_codex_prompt_reaches_the_child_process(tmp_path: Path, monkeypatch) -> None:
    # The trigger is only real if it survives into codex exec's stdin — assert the
    # transport prompt is what the child actually receives, not just what we build.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    seen: dict[str, str] = {}

    class _Completed:
        returncode = 0
        stdout = '{"type":"thread.started","thread_id":"aaaa-bbbb"}\n'
        stderr = ""

    def _fake_run(cmd, **kwargs):
        seen["input"] = kwargs.get("input", "")
        return _Completed()

    monkeypatch.setattr(codex_provider.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        codex_provider,
        "_resolve_rollout",
        lambda sid, sessions_root, *, preexisting: tmp_path / "x.jsonl",
    )
    b64 = base64.b64encode(_png_bytes()).decode()
    monkeypatch.setattr(codex_provider, "_collect_inline_results", lambda path: [b64])

    codex_provider.CodexProvider(keep_session=True).generate(
        GenRequest(prompt="a mushroom", raw=tmp_path / "raw.png"), tmp_path
    )

    assert seen["input"].startswith("$imagegen ")
    assert "a mushroom" in seen["input"]


def test_grok_prompt_switches_on_refs(tmp_path: Path) -> None:
    from sprite_gen.gen import grok_provider

    raw = tmp_path / "raw.png"
    no_ref = grok_provider._build_prompt(GenRequest(prompt="a red apple", raw=raw, aspect_ratio="1:1"))
    assert "image_gen" in no_ref and "1:1" in no_ref and str(raw) in no_ref

    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    with_ref = grok_provider._build_prompt(GenRequest(prompt="same char waving", raw=raw, refs=[ref]))
    assert "image_edit" in with_ref and str(ref.resolve()) in with_ref


def test_chroma_key_transparent_clears_magenta(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    img = Image.new("RGBA", (6, 6), (255, 0, 255, 255))
    img.putpixel((2, 2), (10, 20, 30, 255))  # a real subject pixel
    img.save(src)

    out = tmp_path / "out.png"
    stats = chroma_mod.key_transparent(src, out, key="magenta", white_check=tmp_path / "check.png")
    assert stats["mode"] == "RGBA"
    assert stats["stale_transparent_rgb_pixels"] == 0
    assert stats["keyed_pixels"] == 35  # 36 - 1 subject pixel

    keyed = Image.open(out).convert("RGBA")
    assert keyed.getpixel((0, 0))[3] == 0
    assert keyed.getpixel((2, 2))[3] == 255


class _FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: GenRequest, workdir: Path):
        self.calls += 1
        Image.new("RGBA", (8, 8), (255, 0, 255, 255)).save(request.raw)
        return gen_base.ProviderRun(provider=self.name, elapsed_seconds=1.23, model=request.model)


def test_generate_image_orchestrates_report_and_raw_keep(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)

    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"
    rc = gen.run(
        provider="fake",
        prompt="a mushroom",
        out=out,
        ref=[],
        model=None,
        aspect_ratio=None,
        transparent=True,
        chroma_key="magenta",
        white_check=None,
        keep_session=False,
        report=report,
        prompt_file=None,
        workdir=None,
    )
    assert rc == 0
    assert fake.calls == 1
    assert out.is_file()
    assert (tmp_path / "asset.png.raw.png").is_file()  # pre-chroma raw preserved
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["provider"] == "fake"
    assert payload["transparent"] is True
    assert payload["chroma"]["stale_transparent_rgb_pixels"] == 0
    assert payload["elapsed_seconds"] == 1.23


def test_generate_image_empty_prompt_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        gen.generate_image("codex", "   ", tmp_path / "x.png")


def test_unknown_provider_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        gen._make_provider("gemini", keep_session=False)


# ── Default-provider policy (maintainer 2026-07-17): default = codex, observable grok
# fallback when codex is unavailable, SPRITE_GEN_DEFAULT_PROVIDER override. ──────

def test_resolve_default_provider_hard_default_is_codex(monkeypatch) -> None:
    monkeypatch.delenv(gen.DEFAULT_PROVIDER_ENV, raising=False)
    monkeypatch.setattr(gen, "_codex_available", lambda: (True, ""))
    assert gen.resolve_default_provider() == ("codex", None)


def test_resolve_default_provider_env_override(monkeypatch) -> None:
    monkeypatch.setenv(gen.DEFAULT_PROVIDER_ENV, "grok")
    # codex probe must not even run when the env pins a non-codex default.
    monkeypatch.setattr(gen, "_codex_available", lambda: pytest.fail("probe should not run"))
    assert gen.resolve_default_provider() == ("grok", None)


def test_resolve_default_provider_invalid_env_fails_loud(monkeypatch) -> None:
    monkeypatch.setenv(gen.DEFAULT_PROVIDER_ENV, "gemini")
    with pytest.raises(SystemExit):
        gen.resolve_default_provider()


def test_resolve_default_provider_codex_unavailable_falls_back_observably(monkeypatch) -> None:
    monkeypatch.delenv(gen.DEFAULT_PROVIDER_ENV, raising=False)
    monkeypatch.setattr(gen, "_codex_available", lambda: (False, "codex not logged in (test)"))
    provider, fallback = gen.resolve_default_provider()
    assert provider == "grok"
    assert fallback == {
        "from": "codex",
        "to": "grok",
        "reason": "codex not logged in (test)",
        "default_source": "hard-default",
    }


def test_resolve_default_provider_env_codex_still_falls_back(monkeypatch) -> None:
    # An env that pins codex still gets the availability fallback (default_source=env).
    monkeypatch.setenv(gen.DEFAULT_PROVIDER_ENV, "codex")
    monkeypatch.setattr(gen, "_codex_available", lambda: (False, "codex CLI not found on PATH"))
    provider, fallback = gen.resolve_default_provider()
    assert provider == "grok"
    assert fallback["default_source"] == gen.DEFAULT_PROVIDER_ENV


def _install_recording_provider(monkeypatch):
    """Monkeypatch _make_provider to record the requested name and emit a PNG."""
    requested: list[str] = []

    def fake_make(name, *, keep_session):
        requested.append(name)

        class _P:
            def generate(self, request: GenRequest, workdir: Path):
                Image.new("RGBA", (8, 8), (255, 0, 255, 255)).save(request.raw)
                return gen_base.ProviderRun(provider=name, elapsed_seconds=0.1, model=request.model)

        return _P()

    monkeypatch.setattr(gen, "_make_provider", fake_make)
    return requested


def test_run_explicit_provider_is_honored_without_fallback(tmp_path: Path, monkeypatch) -> None:
    requested = _install_recording_provider(monkeypatch)
    # Even with codex "down", an EXPLICIT --provider is never overridden.
    monkeypatch.setattr(gen, "_codex_available", lambda: (False, "should be irrelevant"))
    report = tmp_path / "r.json"
    rc = gen.run(provider="grok", prompt="x", out=tmp_path / "o.png", report=report)
    assert rc == 0
    assert requested == ["grok"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["provider"] == "grok"
    assert payload["provider_resolved_from"] == "explicit"
    assert "provider_fallback" not in payload


def test_run_default_uses_codex_when_available(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(gen.DEFAULT_PROVIDER_ENV, raising=False)
    requested = _install_recording_provider(monkeypatch)
    monkeypatch.setattr(gen, "_codex_available", lambda: (True, ""))
    report = tmp_path / "r.json"
    rc = gen.run(prompt="x", out=tmp_path / "o.png", report=report)
    assert rc == 0
    assert requested == ["codex"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["provider"] == "codex"
    assert payload["provider_resolved_from"] == "hard-default"
    assert "provider_fallback" not in payload


def test_run_default_falls_back_to_grok_and_reports_it(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv(gen.DEFAULT_PROVIDER_ENV, raising=False)
    requested = _install_recording_provider(monkeypatch)
    monkeypatch.setattr(gen, "_codex_available", lambda: (False, "codex not logged in (test)"))
    report = tmp_path / "r.json"
    rc = gen.run(prompt="x", out=tmp_path / "o.png", report=report)
    assert rc == 0
    assert requested == ["grok"]  # actually generated with grok
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["provider"] == "grok"
    assert payload["provider_resolved_from"] == "fallback-from-codex"
    assert payload["provider_fallback"]["from"] == "codex"
    assert payload["provider_fallback"]["to"] == "grok"
    assert "codex not logged in" in payload["provider_fallback"]["reason"]
    # And the fallback is loud on stderr (No Silent Fallback).
    assert "falling back to 'grok'" in capsys.readouterr().err


def test_codex_stream_errors_surface_the_real_cause() -> None:
    # codex reports a fatal error (e.g. an unsupported model) on stdout as a
    # `turn.failed` event, with the API error nested as JSON inside `message`.
    # Item-level warnings (model metadata, skills budget) must not drown it out.
    stream = "\n".join([
        '{"type":"thread.started","thread_id":"abc"}',
        '{"type":"item.completed","item":{"type":"error","message":"Model metadata for `x` not found."}}',
        '{"type":"turn.failed","error":{"message":'
        '"{\\"type\\":\\"error\\",\\"status\\":400,\\"error\\":'
        '{\\"type\\":\\"invalid_request_error\\",'
        '\\"message\\":\\"The model is not supported.\\"}}"}}',
    ])
    assert codex_provider._extract_stream_errors(stream) == ["The model is not supported."]


def test_codex_stream_errors_empty_when_no_failure() -> None:
    stream = '{"type":"thread.started","thread_id":"abc"}\n{"type":"turn.completed"}'
    assert codex_provider._extract_stream_errors(stream) == []
