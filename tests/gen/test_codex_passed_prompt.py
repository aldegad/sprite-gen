# SPDX-License-Identifier: Apache-2.0
"""The prompt a codex generation hands image_gen (docs/gen.md, "The codex driver model"): the caller's prompt sits
between two markers the driver is told to pass as written; what it passed is read from the `exec` code in the
session record and the report says whether it is the prompt asked for (`extra.prompt_passed`); other text is said on
stderr in one line and the image is kept."""

from __future__ import annotations

import base64
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from sprite_gen.gen import codex_provider
from sprite_gen.gen.base import GenRequest, verify_png

SESSION = "018f0000-0000-7000-8000-0000000000e2"
ASKED = "Create one sprite of a fox.\n\nSide view, facing right.\nOutput only the sprite image.\n"


def _exec_record(code: str) -> dict:
    """The record codex 0.169.1 writes for the JavaScript the driver ran through its `exec` tool."""
    return {"type": "response_item", "payload": {"type": "custom_tool_call", "status": "completed",
                                                 "call_id": "call_1", "name": "exec", "input": code}}


def _image_record() -> dict:
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 255, 255)).save(buffer, format="PNG")
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "Extension", "kind": "image_gen.generation", "status": "completed",
        "result": base64.b64encode(buffer.getvalue()).decode("ascii"), "transparentBackground": False}}}


def _call(prompt_literal: str) -> str:
    return ('// @exec: {"yield_time_ms": 120000}\n'
            f"const result = await tools.image_gen__imagegen({{prompt:{prompt_literal}}});\n")


def _write_rollout(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return path


@pytest.mark.parametrize("literal, value", [
    (json.dumps(ASKED), ASKED),  # a JSON string is a JavaScript string
    ("`" + ASKED + "`", ASKED),  # a template with real line breaks
    ("`a\r\nb\rc`", "a\nb\nc"),  # a template reads CR LF and CR as LF
    ("'it\\'s \\\"x\\\" \\u00e9 \\u{1F98A} \\uD83E\\uDD8A \\x41\\t\\0'", "it's \"x\" é 🦊 🦊 A\t\0"),
    ('"one \\\nline"', "one line"),  # a backslash before a line break continues the line
    ('"a\\`b\\$c\\\\"', "a`b$c\\"),
])
def test_the_prompt_of_the_image_gen_call_is_read_as_javascript_reads_it(literal, value):
    code = _call(literal)
    assert codex_provider._object_prompt(code, code.index("({") + 1) == value


@pytest.mark.parametrize("obj, value", [
    ('{num_last_images_to_include:1, transparent_background:false, prompt:"p"}', "p"),
    ('{referenced_image_paths:["/a/prompt:\\"x\\".png"], transparent_background:false, prompt:`p`}', "p"),
    ('{"prompt": "p"}', "p"),
    ("{'prompt' : 'p' }", "p"),
    ('{options:{prompt:"inner"}, sizes:[1,(2)], prompt:"p"}', "p"),
])
def test_the_prompt_key_is_found_among_the_other_arguments(obj, value):
    assert codex_provider._object_prompt(obj, 0) == value


@pytest.mark.parametrize("obj", [
    "{prompt: p}",  # a variable
    '{prompt: "a" + b}',  # a joined string
    '{prompt: "a".trim()}',
    "{prompt: `a ${b}`}",  # a template with a value in it
    "{prompt}",  # shorthand for a variable
    '{/* the prompt */ prompt:"p"}',  # a comment
    '{transparent_background:false}',  # no prompt
    '{label:`${x}`, prompt:"p"}',
    '{prompt:"\\1"}',  # a legacy octal escape
    '{prompt:"\\uD83D"}',  # a lone surrogate
    '{prompt:"no closing quote}',
    '{prompt:"a\nb"}',  # a line break in a quoted string
])
def test_a_prompt_that_is_not_one_string_literal_is_not_guessed_at(obj):
    assert codex_provider._object_prompt(obj, 0) is None


def test_the_passed_prompts_are_read_from_the_exec_code_in_the_record(tmp_path):
    rollout = _write_rollout(tmp_path / "rollout.jsonl", [
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": _call('"not code"')}]}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "other", "input": _call('"x"')}},
        _exec_record(_call(json.dumps("first")) + "await tools.image_gen__imagegen({prompt: p});\n"),
        _image_record(),
        _exec_record("generatedImage(await tools.image_gen__imagegen({prompt:`last`}));"),
    ])
    assert codex_provider._passed_prompts(rollout) == ["first", None, "last"]


def test_the_verdict_is_on_the_last_call_with_the_surrounding_whitespace_aside():
    same = codex_provider.passed_prompt(ASKED, [None, ASKED.strip()])
    assert same.report() == {"verdict": "same", "asked_chars": len(ASKED.strip()),
                             "passed_chars": len(ASKED.strip())}
    dropped = ASKED.strip().rsplit("\n", 1)[0]
    assert codex_provider.passed_prompt(ASKED, [dropped]).report() == {
        "verdict": "differs", "asked_chars": len(ASKED.strip()), "passed_chars": len(dropped)}
    for passed in ([], [ASKED, None]):
        unknown = codex_provider.passed_prompt(ASKED, passed).report()
        assert unknown["verdict"] == "unknown" and unknown["passed_chars"] is None and unknown["reason"]


def test_the_caller_prompt_sits_between_the_markers_the_driver_is_told_to_pass_as_written():
    for native_alpha in (False, True):
        wrapped = codex_provider._build_prompt(ASKED, native_alpha=native_alpha)
        before, rest = wrapped.split(f"\n{codex_provider._PROMPT_BEGIN}\n", 1)
        between, after = rest.split(f"\n{codex_provider._PROMPT_END}\n", 1)
        assert between == ASKED and after == ""  # the caller's prompt as it is, and nothing after the end marker
        assert (f"{codex_provider._PROMPT_BEGIN} 와 {codex_provider._PROMPT_END} 표시 사이의 글을 한 글자도 더하거나 "
                "빼지 말고 그대로 넘겨라(마지막 줄 포함)") in before


@pytest.fixture
def codex_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A `codex exec` that writes a rollout of `records` (an image by default) and answers with its session id."""
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex_provider, "codex_login",
                        lambda: codex_provider.CodexLogin(codex_provider.AUTH_SOURCE_LOGIN))
    seen = SimpleNamespace(records=[_image_record()], inputs=[])

    def run(cmd, **kwargs):
        seen.inputs.append(kwargs.get("input"))
        _write_rollout(codex_home / "sessions" / "2026" / "08" / "03" / f"rollout-2026-08-03T02-40-51-{SESSION}.jsonl",
                       seen.records)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "thread.started", "thread_id": SESSION}) + "\n",
                                           "")

    monkeypatch.setattr(codex_provider.subprocess, "run", run)
    return seen


def _generate(tmp_path: Path):
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    return codex_provider.CodexProvider().generate(GenRequest(prompt=ASKED, raw=workdir / "raw.png"), workdir)


def test_a_prompt_passed_as_written_is_reported_same_and_nothing_is_said(tmp_path, codex_session, capsys):
    codex_session.records.insert(0, _exec_record(_call(json.dumps(ASKED.strip()))))
    run = _generate(tmp_path)
    assert codex_provider._PROMPT_BEGIN in codex_session.inputs[0]
    assert run.extra["prompt_passed"] == {"verdict": "same", "asked_chars": len(ASKED.strip()),
                                          "passed_chars": len(ASKED.strip())}
    assert "the driver passed image_gen other text" not in capsys.readouterr().err


def test_other_text_is_said_on_stderr_in_one_line_and_the_image_is_kept(tmp_path, codex_session, capsys):
    dropped = ASKED.strip().rsplit("\n", 1)[0]  # the last line left out
    codex_session.records.insert(0, _exec_record(_call(json.dumps(dropped))))
    run = _generate(tmp_path)
    asked = len(ASKED.strip())
    assert run.extra["prompt_passed"] == {"verdict": "differs", "asked_chars": asked, "passed_chars": len(dropped)}
    said = [line for line in capsys.readouterr().err.splitlines() if "the driver passed image_gen" in line]
    assert said == [f"[gen] codex: the driver passed image_gen other text than the prompt ({asked} characters "
                    f"asked, {len(dropped)} passed); the image is kept"]
    assert verify_png(tmp_path / "work" / "raw.png") > 0


def test_a_record_without_the_call_is_reported_unknown_and_the_image_is_kept(tmp_path, codex_session, capsys):
    run = _generate(tmp_path)
    assert run.extra["prompt_passed"] == {"verdict": "unknown", "asked_chars": len(ASKED.strip()),
                                          "passed_chars": None, "reason": "no image_gen call in the session record"}
    assert "the driver passed image_gen other text" not in capsys.readouterr().err
    assert verify_png(tmp_path / "work" / "raw.png") > 0
