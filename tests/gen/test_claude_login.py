# SPDX-License-Identifier: Apache-2.0
"""Claude calls on the person's subscription first (`claude_login`): which credential a call runs on,
what the child `claude -p` inherits, how a message maps onto the binary's flags and stdin, and how
its stream-json answer, a cut answer and an error come back. No binary, no network: a fake `run`
answers with the event shapes `claude` 2.1.300 printed (2026-10-15)."""
from __future__ import annotations

import json
import subprocess

import pytest

from sprite_gen.gen import anthropic_api as api
from sprite_gen.gen import claude_login
from sprite_gen.gen.anthropic_api import ApiError

KEY = "sk-ant-api03-" + "Synthetic0Key1For2Tests" * 2


def status(code: int, payload) -> "subprocess.CompletedProcess[str]":
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(["claude"], code, stdout=text, stderr="")


class Run:
    """A fake `subprocess.run`: records what it was asked and answers in turn."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, args, **kwargs):
        self.calls.append({"args": args, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


SIGNED_IN = status(0, {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"})
SIGNED_OUT = status(1, {"loggedIn": False, "authMethod": "none"})


def test_the_child_runs_without_a_key_and_without_the_calling_session():
    env = {"PATH": "/usr/bin", "HOME": "/home/x", "ANTHROPIC_API_KEY": KEY, "ANTHROPIC_AUTH_TOKEN": "t",
           "CLAUDECODE": "1", "CLAUDE_PID": "7", "CLAUDE_EFFORT": "max", "CLAUDE_CODE_EFFORT_LEVEL": "max",
           "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_OAUTH_TOKEN": "o",
           "CLAUDE_CODE_USE_VERTEX": "1", "ORCHESTRATOR_MEMBER_ID": "m"}
    child = claude_login.child_env(env)
    assert child == {"PATH": "/usr/bin", "HOME": "/home/x", "CLAUDE_CODE_OAUTH_TOKEN": "o",
                     "CLAUDE_CODE_USE_VERTEX": "1"}


# `method not in SUBSCRIPTION_METHODS` is one check: each method it lets in, one it keeps out.
@pytest.mark.parametrize("answer, expected", [
    (SIGNED_IN, claude_login.Login("claude.ai", "max")),
    (status(0, {"loggedIn": True, "authMethod": "oauth_token", "subscriptionType": "team"}),
     claude_login.Login("oauth_token", "team")),
    (SIGNED_OUT, None),
    (status(0, {"loggedIn": True, "authMethod": "api_key"}), None),
    (FileNotFoundError("claude"), None),
])
def test_the_login_is_a_subscription_or_none(answer, expected):
    run = Run(answer)
    assert claude_login.login({"PATH": "/usr/bin", "ANTHROPIC_API_KEY": KEY}, run=run) == expected
    (call,) = run.calls
    assert call["args"][1:] == ["auth", "status", "--json"]
    assert "ANTHROPIC_API_KEY" not in call["env"]  # the status of the login the child would use


# A timeout and an OSError share one `except`.
@pytest.mark.parametrize("answer", [status(0, "not json"), status(2, {"loggedIn": False}),
                                    subprocess.TimeoutExpired(["claude"], 30)])
def test_an_unreadable_status_is_refused_not_read_as_no_login(answer):
    with pytest.raises(SystemExit, match="refusing to guess whether your Claude subscription is signed in"):
        claude_login.login({}, run=Run(answer))


def test_the_login_comes_before_the_key():
    run = Run(SIGNED_IN)
    credential = claude_login.resolve_credential(env={"ANTHROPIC_API_KEY": KEY}, run=run)
    assert credential == claude_login.Credential("claude-login", login=claude_login.Login("claude.ai", "max"))
    assert credential.token is None


def test_the_key_only_without_a_login():
    credential = claude_login.resolve_credential(env={"ANTHROPIC_API_KEY": KEY}, run=Run(SIGNED_OUT))
    assert credential.source == "ANTHROPIC_API_KEY" and credential.token == KEY


def test_neither_is_refused_naming_both():
    with pytest.raises(SystemExit) as caught:
        claude_login.resolve_credential(env={}, run=Run(FileNotFoundError("claude")))
    message = str(caught.value)
    assert "no subscription login (`claude auth status`) and ANTHROPIC_API_KEY is not set" in message
    assert "`claude` then `/login`" in message
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY is set but empty"):
        claude_login.resolve_credential(env={"ANTHROPIC_API_KEY": " "}, run=Run(SIGNED_OUT))


def test_a_batch_runs_on_the_key_and_does_not_ask_the_login():
    run = Run()
    credential = claude_login.resolve_credential(batch=True, env={"ANTHROPIC_API_KEY": KEY}, run=run)
    assert credential.source == "ANTHROPIC_API_KEY" and run.calls == []
    with pytest.raises(SystemExit, match=r"(?s)--batch is Anthropic's Message Batches API.*drop --batch"):
        claude_login.resolve_credential(batch=True, env={}, run=run)


def stream(*events) -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess(["claude"], 0, stdout="".join(json.dumps(e) + "\n" for e in events), stderr="")


USAGE = {"input_tokens": 2, "cache_creation_input_tokens": 689, "cache_read_input_tokens": 0, "output_tokens": 52,
         "output_tokens_details": {"thinking_tokens": 30}}
INIT = {"type": "system", "subtype": "init", "session_id": "s"}


def assistant(*blocks, message_id="msg_1"):
    return {"type": "assistant", "message": {"id": message_id, "role": "assistant", "content": list(blocks)}}


def result(**fields):
    return {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn", "usage": USAGE,
            "result": "", **fields}


def test_a_message_maps_onto_the_flags_and_stdin(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0K"}}
    body = api.message_body(system=None, content=[image, {"type": "text", "text": "pick"}], effort="low",
                            max_tokens=16000, schema={"type": "object"})
    run = Run(stream(INIT, assistant({"type": "thinking", "thinking": "A reads best"}),
                     assistant({"type": "tool_use", "name": "StructuredOutput"}),
                     result(stop_reason="tool_use", result='{"best":"A"}', structured_output={"best": "A"})))
    reply = claude_login.send_message(body, label="pixel-draw pick", run=run)
    (call,) = run.calls
    args = call["args"]
    assert args[1:] == ["-p", "--model", "claude-opus-5-5", "--effort", "low", "--input-format", "stream-json",
                        "--output-format", "stream-json", "--verbose", "--system-prompt", "", "--tools", "",
                        "--strict-mcp-config", "--no-session-persistence", "--safe-mode", "--max-turns", "1",
                        "--json-schema", '{"type":"object"}']
    assert json.loads(call["input"]) == {"type": "user", "message": {"role": "user", "content": body["messages"][0]["content"]}}
    assert call["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "16000"
    assert "ANTHROPIC_API_KEY" not in call["env"] and "CLAUDE_CODE_SESSION_ID" not in call["env"]
    assert call["timeout"] == claude_login.CALL_TIMEOUT_SECONDS and call["cwd"]
    assert json.loads(reply.text) == {"best": "A"} and reply.thinking == "A reads best"
    assert (reply.mode, reply.stop_reason, reply.usage, reply.message_id) == ("subscription", "tool_use", USAGE, "msg_1")


def test_a_text_prompt_becomes_one_text_block():
    body = api.message_body(system="You draw.", content="Draw: a slime", effort="high", max_tokens=32000)
    run = Run(stream(INIT, assistant({"type": "text", "text": "```pixels\n...```"}), result(result="```pixels\n...```")))
    reply = claude_login.send_message(body, label="pixel-draw c1-gen1", run=run)
    (call,) = run.calls
    assert call["args"][call["args"].index("--system-prompt") + 1] == "You draw."
    assert "--json-schema" not in call["args"]
    assert json.loads(call["input"])["message"]["content"] == [{"type": "text", "text": "Draw: a slime"}]
    assert reply.text == "```pixels\n...```" and reply.stop_reason == "end_turn"


def test_an_answer_the_binary_continued_past_the_cap_is_read_whole():
    """Past the output cap the binary asks for a continuation and the answer goes on in a second
    message; `result` then holds only that last part (claude 2.1.300, 2026-10-15: a 48 px drawing whose
    thinking took 63,092 tokens of a 64,000 cap came back as its last 40 rows, fence and palette gone)."""
    body = api.message_body(system="s", content="Draw", effort="high", max_tokens=64000)
    first = "```pixels\nPALETTE\n. transparent\na #ff0000\nFRAME f01\n..a\n"
    rest = ".a.\na..\nEND\n```"
    reply = claude_login.send_message(body, label="c1-part1-gen1", run=Run(stream(
        INIT, assistant({"type": "thinking", "thinking": "plan"}, {"type": "text", "text": first}, message_id="msg_1"),
        assistant({"type": "text", "text": rest}, message_id="msg_2"), result(result=rest))))
    assert reply.text == first + rest and reply.stop_reason == "end_turn"


def test_an_answer_cut_at_the_cap_is_max_tokens_with_what_was_written():
    body = api.message_body(system="s", content="Draw", effort="high", max_tokens=64000)
    cut = result(is_error=True, stop_reason="stop_sequence", terminal_reason="api_error",
                 result="API Error: Claude's response exceeded the 64000 output token maximum. To configure this "
                        "behavior, set the CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable.")
    reply = claude_login.send_message(body, label="c1-gen1", run=Run(stream(INIT, assistant(
        {"type": "text", "text": "PALETTE"}), assistant({"type": "text", "text": "\n. transparent"}), cut)))
    assert (reply.stop_reason, reply.text, reply.mode) == ("max_tokens", "PALETTE\n. transparent", "subscription")


def test_a_refused_or_limited_login_is_an_error_not_a_switch_to_the_key():
    body = api.message_body(system="s", content="Draw", effort="high", max_tokens=64000)
    limited = result(is_error=True, subtype="success", api_error_status=429,
                     result="Claude AI usage limit reached|1760500000", usage={"output_tokens": 0})
    with pytest.raises(ApiError) as caught:
        claude_login.send_message(body, label="pixel-draw c1-gen1", run=Run(stream(INIT, limited)))
    assert caught.value.line == ("pixel-draw c1-gen1: the Claude subscription did not answer (success, HTTP 429): "
                                 "Claude AI usage limit reached|1760500000")
    assert caught.value.status == 429 and caught.value.billed is False


def test_no_result_a_timeout_and_a_missing_binary_are_errors():
    body = api.message_body(system="s", content="Draw", effort="high", max_tokens=64000)
    broken = subprocess.CompletedProcess(["claude"], 1, stdout="", stderr="Error: something broke")
    with pytest.raises(ApiError, match=r"ended with exit 1 and no result: 'Error: something broke'"):
        claude_login.send_message(body, label="c1-gen1", run=Run(broken))
    with pytest.raises(ApiError, match=r"gave no answer in .*SPRITE_GEN_CLAUDE_CALL_TIMEOUT_SECONDS"):
        claude_login.send_message(body, label="c1-gen1", run=Run(subprocess.TimeoutExpired(["claude"], 1)))
    with pytest.raises(ApiError, match="did not start") as caught:
        claude_login.send_message(body, label="c1-gen1", run=Run(FileNotFoundError("claude")))
    assert caught.value.billed is False


def test_a_reply_that_is_not_utf8_is_an_error_not_an_answer():
    """The reply is the drawing, read as the UTF-8 the binary writes and never as other characters. A byte that is not
    UTF-8 on stdout fails the call with its error, as Windows leaves it (each stream is decoded on a thread of its own,
    and one that does not decode comes back None) and as elsewhere (the decode raises from `run`). On Windows a stderr
    alone that does not decode is lost and the reply on stdout comes back."""
    body = api.message_body(system="s", content="Draw", effort="high", max_tokens=64000)
    unread = r"c1-gen1: `claude -p` answered with a byte that is not UTF-8"
    windows = subprocess.CompletedProcess(["claude"], 0, stdout=None, stderr="")
    with pytest.raises(ApiError, match=unread) as caught:
        claude_login.send_message(body, label="c1-gen1", run=Run(windows))
    assert caught.value.billed is True
    elsewhere = UnicodeDecodeError("utf-8", b"\xea\xb1", 0, 2, "unexpected end of data")
    with pytest.raises(ApiError, match=unread) as caught:
        claude_login.send_message(body, label="c1-gen1", run=Run(elsewhere))
    assert caught.value.billed is True and caught.value.__cause__ is elsewhere
    stderr_lost = stream(INIT, assistant({"type": "text", "text": "PALETTE"}), result(result="PALETTE"))
    stderr_lost.stderr = None
    assert claude_login.send_message(body, label="c1-gen1", run=Run(stderr_lost)).text == "PALETTE"
