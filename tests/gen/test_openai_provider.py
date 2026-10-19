# SPDX-License-Identifier: Apache-2.0
# Modified: retain the OpenAI opt-in invariant while allowing other explicit-only providers.
"""OpenAI Images API contract, offline: no key, no network, no codex subprocess.

The paid seam is the request body, so that is what is pinned here: which endpoint
is called, every field that decides what is billed (model / quality / size /
background / output_format), how the base64 answer becomes a verified PNG, and
which failures must never publish, retry or leak the key.
"""
import base64
import io
import json
import os
import re
import subprocess
import urllib.error
from types import SimpleNamespace

import pytest
from PIL import Image

from http_answer import Answer
from sprite_gen import gen
from sprite_gen.gen import grok_provider as grok
from sprite_gen.gen import openai_provider as openai
from sprite_gen.gen import base, retry, xai
from sprite_gen.gen.base import GenRequest
from sprite_gen.workflow import access
from sprite_gen.workflow.catalog import FIELDS, GUIDED_PROVIDERS, PROVIDER_LABELS, validate_choices

KEY = "synthetic-secret"


def encoded_image(fmt="PNG", *, alpha=False, key=False):
    """A 12x8 fixture: a 4x4 subject block on a plain, transparent or magenta field."""
    if alpha:
        image, subject = Image.new("RGBA", (12, 8), (0, 0, 0, 0)), (20, 90, 200, 255)
    else:
        image, subject = Image.new("RGB", (12, 8), (255, 0, 255) if key else (20, 90, 200)), (20, 90, 200)
    for x in range(4, 8):
        for y in range(2, 6):
            image.putpixel((x, y), subject)
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no provider subprocess may be spawned"))
    state = {"status": 200, "body": {"data": [{"b64_json": encoded_image()}],
                                     "usage": {"input_tokens": 12, "output_tokens": 340}}, "headers": {}, "calls": [],
             "slept": []}
    monkeypatch.setattr(retry, "_sleep", state["slept"].append)

    def open_request(request, *, timeout):
        state["calls"].append((request, timeout))
        if state.get("script"):  # one (status, body, headers) per request, in order
            state["status"], state["body"], state["headers"] = state["script"].pop(0)
        answer = Answer(state["body"], status=state["status"], headers=state["headers"])
        if state["status"] == 200:
            return answer
        raise urllib.error.HTTPError(request.full_url, state["status"], "error", state["headers"], answer)

    monkeypatch.setattr(openai.urllib.request, "urlopen", open_request)
    return state


def _body(request):
    return json.loads(request.data)


def test_generation_sends_the_billed_fields_and_publishes_a_png(tmp_path, api):
    result = gen.generate_image("openai", "a blue square", tmp_path / "out.png")
    request, timeout = api["calls"][0]
    assert request.full_url == "https://api.openai.com/v1/images/generations"
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert request.get_header("Content-type") == "application/json"
    assert _body(request) == {"model": openai.DEFAULT_MODEL, "prompt": "a blue square", "n": 1,
                              "size": "1024x1024", "quality": "auto",
                              "output_format": "png", "background": "opaque"}
    assert timeout == pytest.approx(openai.GEN_TIMEOUT_SECONDS, abs=1)  # the first send waits the whole time limit
    # `response_format` is rejected for gpt-image models; the answer is always b64.
    assert "response_format" not in _body(request)
    with Image.open(result.out) as image:
        assert image.format == "PNG" and image.size == (12, 8)
    report = result.to_dict()
    assert report["provider"] == "openai" and report["model"] == openai.DEFAULT_MODEL
    assert report["extra"]["transport"] == "openai-api"
    assert report["extra"]["auth_source"] == "OPENAI_API_KEY"
    assert report["extra"]["usage"] == {"input_tokens": 12, "output_tokens": 340}
    assert KEY not in json.dumps(report)


def test_the_report_carries_the_rate_limit_headers_that_came(tmp_path, api):
    """What the account may spend and has left, read off real traffic: the response's
    `x-ratelimit-*` headers, names lower-cased and values as sent, and no other header."""
    api["headers"] = {"x-ratelimit-limit-requests": "50", "X-RateLimit-Remaining-Requests": "49",
                      "x-ratelimit-reset-requests": "1.2s", "x-request-id": "req_synthetic",
                      "openai-processing-ms": "5120", "Content-Type": "application/json"}
    report = gen.generate_image("openai", "x", tmp_path / "out.png").to_dict()
    assert report["extra"]["rate_limit"] == {"x-ratelimit-limit-requests": "50",
                                             "x-ratelimit-remaining-requests": "49",
                                             "x-ratelimit-reset-requests": "1.2s"}


def test_a_response_without_rate_limit_headers_reports_that_none_came(tmp_path, api):
    """Read and empty, not left out: an empty record is the answer that none came."""
    api["headers"] = {"Content-Type": "application/json"}
    assert gen.generate_image("openai", "x", tmp_path / "out.png").to_dict()["extra"]["rate_limit"] == {}


def test_aspect_ratio_maps_to_a_legal_gpt_image_size():
    # The lookup is one `SIZES[ratio]` (sent end to end in test_cli_accepts_provider_and_quality).
    for ratio, size in sorted(openai.SIZES.items()):
        if size == "auto":
            continue
        width, height = (int(side) for side in size.split("x"))
        # The documented custom-size constraints, checked on the table itself.
        assert width % 16 == 0 and height % 16 == 0, ratio
        assert max(width, height) <= 3840, ratio
        assert 1 / 3 <= width / height <= 3, ratio
        assert 655_360 <= width * height <= 8_294_400, ratio


def test_model_and_quality_overrides_are_not_second_guessed(tmp_path, api):
    gen.generate_image("openai", "x", tmp_path / "out.png", model="gpt-image-2.5-sunburst", quality="max")
    body = _body(api["calls"][0][0])
    assert body["model"] == "gpt-image-2.5-sunburst" and body["quality"] == "max"


def test_transparent_asks_for_a_transparent_background_and_keeps_the_alpha(tmp_path, api):
    api["body"] = {"data": [{"b64_json": encoded_image(alpha=True)}]}
    result = gen.generate_image("openai", "a blue square", tmp_path / "alpha.png", transparent=True)
    body = _body(api["calls"][0][0])
    assert body["background"] == "transparent" and body["output_format"] == "png"
    assert result.alpha["strategy"] == "native" and result.alpha["alpha_zero_pct"] > 0
    with Image.open(result.out) as image:
        assert image.getpixel((0, 0)) == (0, 0, 0, 0)
        assert image.getpixel((6, 4))[3] == 255


def test_reference_edit_posts_multipart_images_in_order(tmp_path, api):
    refs = []
    for index in range(3):
        path = tmp_path / f"ref-{index}.png"
        Image.new("RGB", (12, 8), (index * 40, 0, 0)).save(path)
        refs.append(path)
    gen.generate_image("openai", "combine these", tmp_path / "edit.png", refs=refs, quality="high")
    request, _ = api["calls"][0]
    assert request.full_url == "https://api.openai.com/v1/images/edits"
    content_type = request.get_header("Content-type")
    assert content_type.startswith("multipart/form-data; boundary=")
    payload = request.data
    assert payload.count(b'name="image[]"') == len(refs)
    assert b'name="quality"\r\n\r\nhigh\r\n' in payload
    assert b'name="model"\r\n\r\n' + openai.DEFAULT_MODEL.encode() in payload
    positions = [payload.index(ref.read_bytes()) for ref in refs]
    assert positions == sorted(positions), "reference images are read positionally; order must survive"


# Two branches: 401/403 the credential, the rest a refusal; which are sent again is tests/gen/test_retry.py's.
@pytest.mark.parametrize("status", [401, 500])
def test_api_failure_keeps_existing_output_and_does_not_retry(tmp_path, api, status):
    api.update(status=status, body={"error": {"message": f"{KEY} https://signed.invalid/private?sig=abc"}})
    out = tmp_path / "existing.png"
    out.write_bytes(b"existing")
    with pytest.raises(SystemExit, match=f"HTTP {status}") as error:
        gen.generate_image("openai", "x", out)
    # The body is shown; the key the request carried and the URL's signature are not.
    assert KEY not in str(error.value) and "sig=abc" not in str(error.value)
    assert "https://signed.invalid/private?[redacted]" in str(error.value)
    assert out.read_bytes() == b"existing"
    assert len(api["calls"]) == 1


@pytest.mark.parametrize(("refused", "slept"), [
    ((429, {"error": {"type": "rate_limit_error", "code": "rate_limit_exceeded"}}, {"retry-after": "2"}), [2.0]),
    ((503, {"error": {"type": "service_unavailable_error", "code": "server_is_overloaded"}}, {}), [8.0]),
], ids=["rate-limit", "overloaded"])
def test_a_refusal_openai_documents_as_not_taken_is_sent_again(tmp_path, api, capsys, refused, slept):
    """429 (a rate limit) and 503 (overloaded) are the two the error-codes guide and the SDKs retry
    (gen.retry, read 2026-10-13): the image is asked for again after the answer's retry-after or the
    doubling wait, and published once."""
    api["script"] = [refused, (200, {"data": [{"b64_json": encoded_image()}]}, {"x-ratelimit-remaining-requests": "9"})]
    result = gen.generate_image("openai", "x", tmp_path / "out.png")
    assert len(api["calls"]) == 2 and api["slept"] == slept and result.out.is_file()
    assert result.extra["rate_limit"] == {"x-ratelimit-remaining-requests": "9"}
    assert f"[gen] openai refused the request ({refused[0]} {refused[1]['error']['code']}); nothing was made" in capsys.readouterr().err


# Which billing codes are not sent again is tests/gen/test_retry.py's table; the line is the same for each.
@pytest.mark.parametrize("code", ["insufficient_quota"])
def test_a_billing_429_asks_the_caller_to_act_and_is_not_sent_again(tmp_path, api, code):
    api.update(status=429, body={"error": {"type": "insufficient_quota", "code": code}})
    with pytest.raises(SystemExit, match=f"HTTP 429.*code={code}; no retry or provider fallback"):
        gen.generate_image("openai", "x", tmp_path / "out.png")
    assert len(api["calls"]) == 1 and api["slept"] == []


def test_a_refusal_still_there_after_two_more_sends_ends_the_call(tmp_path, api):
    api.update(status=503, body={"error": {"code": "server_is_overloaded"}})
    with pytest.raises(SystemExit) as error:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    assert "(HTTP 503) code=server_is_overloaded; sent 3 times, no provider fallback" in str(error.value)
    assert len(api["calls"]) == 3 and api["slept"] == [8.0, 16.0]


def test_a_refusal_is_sent_again_only_within_the_time_limit_of_the_image(tmp_path, api, monkeypatch, capsys):
    """SPRITE_GEN_GEN_TIMEOUT_SECONDS is the image's time limit from its first send (gen.retry): the waits
    before a refused request goes again count in it and each send waits only what is left of it, so a
    refusal whose next wait would reach it ends the call, its line saying why, before a caller's ceiling."""
    now = [0.0]

    def sleep(seconds):
        api["slept"].append(seconds)
        now[0] += seconds

    for module in (openai, retry, xai):
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(retry, "_sleep", sleep)
    monkeypatch.setattr(openai, "GEN_TIMEOUT_SECONDS", 20)
    limited = (429, {"error": {"type": "rate_limit_error", "code": "rate_limit_exceeded"}}, {})
    api["script"] = [limited, limited, (200, api["body"], {})]
    with pytest.raises(SystemExit) as refused:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    assert [timeout for _request, timeout in api["calls"]] == [20, 12] and api["slept"] == [8.0]
    assert "(HTTP 429) code=rate_limit_exceeded; sent 2 times, no provider fallback" in str(refused.value)
    assert ("[gen] openai refused the request (429 rate_limit_exceeded); nothing was made — not asking again: "
            "the 16s wait would reach the time limit (12s left)") in capsys.readouterr().err
    assert not (tmp_path / "out.png").exists()


@pytest.mark.parametrize("call", ["image", "facing check"])
def test_a_refusal_whose_wait_woke_past_the_time_limit_is_not_sent_again(tmp_path, api, monkeypatch, capsys, call):
    """A sleep may wake later than asked: with 8.01 s, the 8 s wait after a 429 woke 20 ms late, past the time
    limit. The limit is read again right before the send, so nothing is sent and the 429 ends the call as it
    came, its stderr line saying why — not a send that finds no time left and ends as a request the server may
    have taken."""
    now = [0.0]

    def sleep(seconds):
        api["slept"].append(seconds)
        now[0] += seconds + 0.020

    for module in (openai, retry, xai):
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(retry, "_sleep", sleep)
    monkeypatch.setattr(openai, "GEN_TIMEOUT_SECONDS", 8.010)
    limited = (429, {"error": {"type": "rate_limit_error", "code": "rate_limit_exceeded"}}, {})
    api["script"] = [limited, (200, api["body"], {})]
    Image.new("RGB", (4, 4), (20, 90, 200)).save(tmp_path / "raw.png")
    with pytest.raises(SystemExit) as refused:
        if call == "image":
            gen.generate_image("openai", "x", tmp_path / "out.png")
        else:
            openai.OpenAIProvider().inspect_facing(tmp_path / "raw.png", tmp_path)
    assert len(api["calls"]) == 1 and api["slept"] == [8.0]
    assert "HTTP 429" in str(refused.value) and "request failed or timed out" not in str(refused.value)
    assert capsys.readouterr().err.splitlines()[-1] == (
        "[gen] openai refused the request (429 rate_limit_exceeded); nothing was made — not asking again: "
        "the time limit came during the 8s wait")
    assert not (tmp_path / "out.png").exists()


@pytest.mark.parametrize("body", [{}, {"data": [None]}, {"data": [{}, {}]},
                                  {"data": [{"b64_json": "???"}]},
                                  {"data": [{"b64_json": base64.b64encode(b"invalid image").decode()}]},
                                  {"data": [{"url": "https://signed.invalid/private"}]}])
def test_invalid_response_never_reuses_stale_raw(tmp_path, api, body):
    api["body"] = body
    raw = tmp_path / "raw.png"
    raw.write_bytes(b"previous raw")
    with pytest.raises(SystemExit):
        openai.OpenAIProvider().generate(GenRequest("x", raw), tmp_path)
    assert raw.read_bytes() == b"previous raw"
    assert len(api["calls"]) == 1
    assert list(tmp_path.iterdir()) == [raw]


@pytest.mark.parametrize("value", [None, ""])
def test_missing_key_names_the_variable_and_never_falls_back(tmp_path, api, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("OPENAI_API_KEY")
    else:
        monkeypatch.setenv("OPENAI_API_KEY", value)
    with pytest.raises(SystemExit, match="OPENAI_API_KEY") as error:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    assert "codex" in str(error.value)  # names the login it will NOT silently use
    assert api["calls"] == []
    assert not (tmp_path / "out.png").exists()


def test_invalid_request_fails_before_any_billable_call(tmp_path, api):
    bad_ref = tmp_path / "ref.png"
    bad_ref.write_bytes(b"invalid")
    good_ref = tmp_path / "good.png"
    Image.new("RGB", (4, 4), (1, 2, 3)).save(good_ref)
    for options in ({"prompt": "   "}, {"refs": [bad_ref]}, {"refs": [good_ref] * (openai.MAX_REFS + 1)},
                    {"aspect_ratio": "19.5:9"}, {"quality": "ultra"}, {"resolution": "2k"}):
        prompt = options.pop("prompt", "x")
        with pytest.raises(SystemExit):
            openai.OpenAIProvider().generate(GenRequest(prompt, tmp_path / "raw.png", **options), tmp_path)
    assert api["calls"] == []


def test_timeout_does_not_repeat_billable_request(tmp_path, api, monkeypatch):
    calls = []

    def timeout(*args, **kwargs):
        calls.append(args)
        raise TimeoutError()

    monkeypatch.setattr(openai.urllib.request, "urlopen", timeout)
    with pytest.raises(SystemExit, match="no automatic retry"):
        gen.generate_image("openai", "x", tmp_path / "out.png")
    assert len(calls) == 1


def test_native_alpha_is_a_declared_capability(tmp_path, api):
    assert openai.OpenAIProvider().transparency == "native"
    # Forcing chroma stays legal (a prompt may already carry a key background).
    api["body"] = {"data": [{"b64_json": encoded_image(key=True)}]}
    result = gen.generate_image("openai", "blue on magenta", tmp_path / "keyed.png",
                                transparent=True, alpha_mode="chroma")
    assert result.alpha["strategy"] == "chroma"
    assert _body(api["calls"][0][0])["background"] == "opaque"


def test_provider_is_registered_everywhere_a_provider_must_be(api):
    assert "openai" in gen.PROVIDERS
    assert isinstance(gen._make_provider("openai", keep_session=False), openai.OpenAIProvider)
    # Every registered backend is labelled, so a new one can never be dropped from
    # the workflow surface by a silent positional zip.
    assert set(PROVIDER_LABELS) == set(gen.PROVIDERS)
    assert access.probe_access("openai") == {"provider": "openai", "login": "ready", "subscription": "unknown",
                                             "quota": "unknown", "billing": "api-credit",
                                             "reason": "OpenAI images will use OPENAI_API_KEY and separate "
                                                       "API credit; confirm this billing choice."}


# --- 구독 우선 불변식 (수홍 2026-09-20, 2026-10-16) ---------------------------
# sprite-gen runs on subscriptions people already pay for. The API-key backend is
# for servers and SaaS and is billed per call, so nothing routes to it unless the
# user chose it: by naming it (`--provider openai`), or as the answer to "codex
# cannot run here, what now?" (`--codex-unavailable openai`). These two tests are
# the guard rails for invariants 1 and 2; invariant 3 (the Grok login wins over
# XAI_API_KEY) is tests/gen/test_grok_provider.py's.


def test_openai_is_never_a_default_and_runs_only_when_chosen(monkeypatch, api):
    """불변식 1: no default, no guided-flow image provider; only a choice the user made reaches it."""
    assert gen.HARD_DEFAULT_PROVIDER == "codex"
    assert "openai" in gen.EXPLICIT_ONLY_PROVIDERS
    monkeypatch.delenv("SPRITE_GEN_DEFAULT_PROVIDER", raising=False)
    monkeypatch.setattr(gen, "_codex_available", lambda: (True, ""))
    assert gen.resolve_default_provider() == ("codex", None)
    # The env knob cannot stand it up either.
    monkeypatch.setenv("SPRITE_GEN_DEFAULT_PROVIDER", "openai")
    with pytest.raises(SystemExit, match=r"--provider openai"):
        gen.resolve_default_provider()
    # Not offered by the guided flow as the image provider, so it is never saved as one.
    assert "openai" not in GUIDED_PROVIDERS
    assert "openai" not in FIELDS["image_provider"]["options"]
    with pytest.raises(ValueError):
        validate_choices("image", {"image_provider": "openai"})
    # It is one of the answers to a codex that cannot run, and never the one taken unasked.
    assert "openai" in gen.CODEX_UNAVAILABLE_CHOICES and gen.CODEX_UNAVAILABLE_DEFAULT == "ask"


@pytest.mark.parametrize("configured", [None, "codex"])
def test_an_api_key_in_the_environment_reroutes_nothing(monkeypatch, api, configured):
    """불변식 2: a codex outage asks, or reaches what the user chose; the metered key alone changes nothing."""
    assert "OPENAI_API_KEY" in os.environ  # the fixture set it
    if configured is None:
        monkeypatch.delenv("SPRITE_GEN_DEFAULT_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("SPRITE_GEN_DEFAULT_PROVIDER", configured)
    monkeypatch.setattr(gen, "_codex_available", lambda: (False, "synthetic outage"))
    with pytest.raises(base.CodexUnavailableAsk):
        gen.resolve_default_provider()
    monkeypatch.setenv(gen.CODEX_UNAVAILABLE_ENV, "grok")
    provider, fallback = gen.resolve_default_provider()
    assert provider == "grok"
    assert fallback["from"] == "codex" and fallback["to"] == "grok"
    assert api["calls"] == []


def test_every_api_key_call_announces_the_charge_before_it_leaves(tmp_path, api, capsys):
    """불변식 5: one stderr line, before the request, naming the billing route."""
    gen.generate_image("openai", "x", tmp_path / "out.png", quality="low")
    notice = [line for line in capsys.readouterr().err.splitlines() if "per-call API charge" in line]
    assert len(notice) == 1
    assert "OPENAI_API_KEY" in notice[0] and "quality=low" in notice[0]


def test_cli_accepts_provider_and_quality(tmp_path, api):
    report = tmp_path / "report.json"
    assert gen.main(["--provider", "openai", "--prompt", "x", "--out", str(tmp_path / "cli.png"),
                     "--quality", "low", "--aspect-ratio", "16:9", "--report", str(report)]) == 0
    body = _body(api["calls"][0][0])
    assert body["quality"] == "low" and body["size"] == "1536x864"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["provider"] == "openai" and payload["provider_resolved_from"] == "explicit"


def test_a_grok_resolution_is_refused_rather_than_dropped(tmp_path, api):
    """gpt-image has no long-edge preset: --resolution would be paid for and ignored."""
    with pytest.raises(SystemExit, match="aspect-ratio") as error:
        gen.generate_image("openai", "x", tmp_path / "out.png", resolution="2k")
    assert "2k" in str(error.value)
    assert api["calls"] == []
    assert not (tmp_path / "out.png").exists()


# --- refusal lines: the provider code, never the body -----------------------------

HTTP_STATUS = re.compile(r"\(HTTP (\d{3})\)")  # what a caller reads the status with


def _refused(tmp_path, api, status, error):
    """The line in front of the body; the body itself must follow it as sent."""
    api.update(status=status, body={"error": {"message": "synthetic prompt echo", **error}})
    with pytest.raises(SystemExit) as refused:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    message = str(refused.value)
    body = ": " + json.dumps(api["body"], ensure_ascii=False)
    assert message.endswith(body) and "\n" not in message
    assert HTTP_STATUS.search(message).group(1) == str(status)
    assert len(api["calls"]) == 1
    return message[: -len(body)]


# Three branches: a documented stage, no moderation object, a stage outside MODERATION_STAGES.
@pytest.mark.parametrize("details, stage", [
    ({"moderation_details": {"moderation_stage": "output", "categories": ["other"]}}, " moderation_stage=output"),
    ({}, ""),
    ({"moderation_details": {"moderation_stage": "a sentence the body made up"}}, ""),
])
def test_a_content_policy_block_says_so_with_its_code(tmp_path, api, details, stage):
    error = {"code": "moderation_blocked", "type": "image_generation_user_error", "param": None, **details}
    assert _refused(tmp_path, api, 400, error) == (
        f"openai-gen: refused by the provider's content policy (HTTP 400) code=moderation_blocked{stage}; "
        "no retry or provider fallback"
    )


def test_another_code_keeps_the_plain_failure_and_names_the_code(tmp_path, api):
    assert _refused(tmp_path, api, 400, {"code": "invalid_value", "param": "size"}) == (
        "openai-gen: image request failed (HTTP 400) code=invalid_value; no retry or provider fallback"
    )


# `refusal.identifier`: no code, a code that is not a str, a str outside CODE_PATTERN.
@pytest.mark.parametrize("error", [{}, {"code": 400}, {"code": "has a space"}, {"code": "x" * 65}])
def test_no_code_or_a_code_outside_the_format_leaves_the_line_as_it_was(tmp_path, api, error):
    assert _refused(tmp_path, api, 400, error) == (
        "openai-gen: image request failed (HTTP 400); no retry or provider fallback"
    )


def test_a_top_level_code_is_not_the_openai_error_code(tmp_path, api):
    api.update(status=400, body={"code": "moderation_blocked", "error": {"message": "x"}})
    with pytest.raises(SystemExit) as refused:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    assert str(refused.value) == ("openai-gen: image request failed (HTTP 400); no retry or provider fallback: "
                                  '{"code": "moderation_blocked", "error": {"message": "x"}}')


def test_a_rejected_key_names_the_code_too(tmp_path, api):
    assert _refused(tmp_path, api, 401, {"code": "invalid_api_key"}).startswith(
        "openai-gen: credential OPENAI_API_KEY rejected (HTTP 401) code=invalid_api_key; check the key's value"
    )


# --- the body the provider sent: shown as received, keys and signatures masked ----

# Synthetic, shaped like the real thing: a project key, an xAI key, a key the
# provider already masked, a bearer header and a signed URL.
PROJECT_KEY = "sk-proj-" + "Synthetic0Key1For2Tests3Only4" * 2
XAI_KEY = "xai-" + "Synthetic0Key1For2Tests3Only4" * 3
MASKED_KEY = "sk-proj-" + "*" * 20 + "ab12"
BEARER = "Bearer " + "synthetic.token.value-0123456789"
SIGNED = "https://files.example.invalid/img/out.png?X-Signature=0a1b2c&Expires=1"


def _line(tmp_path, api, status, body):
    api.update(status=status, body=body)
    with pytest.raises(SystemExit) as refused:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    return str(refused.value)


def test_the_body_follows_the_line_as_received_on_one_line(tmp_path, api):
    body = {"error": {"message": "Your request was rejected by the safety system.\n요청이 거절됐어요.",
                      "type": "image_generation_user_error", "param": None, "code": "moderation_blocked",
                      "moderation_details": {"moderation_stage": "output", "categories": ["other"]}}}
    assert _line(tmp_path, api, 400, body) == (
        "openai-gen: refused by the provider's content policy (HTTP 400) code=moderation_blocked "
        "moderation_stage=output; no retry or provider fallback: " + json.dumps(body, ensure_ascii=False)
    )


def test_keys_and_url_query_strings_are_masked_and_nothing_else(tmp_path, api):
    said = (f"key {PROJECT_KEY} or {XAI_KEY}; masked {MASKED_KEY}; sent {KEY}; header {BEARER}; "
            f"url {SIGNED} then https://example.invalid/docs/errors and sk-short")
    message = _line(tmp_path, api, 401, {"error": {"message": said, "code": "invalid_api_key"}})
    for secret in (PROJECT_KEY, XAI_KEY, KEY, "synthetic.token.value", "X-Signature", "Expires"):
        assert secret not in message
    assert message == (
        "openai-gen: credential OPENAI_API_KEY rejected (HTTP 401) code=invalid_api_key; check the key's value "
        "and that its project may call gpt-image-2.5-flare: "
        '{"error": {"message": "key sk-[redacted] or xai-[redacted]; '
        f"masked {MASKED_KEY}; sent [redacted]; header Bearer [redacted]; "
        'url https://files.example.invalid/img/out.png?[redacted] then https://example.invalid/docs/errors '
        'and sk-short", "code": "invalid_api_key"}}'
    )


def test_a_body_that_is_not_json_is_its_text_on_one_line(api, tmp_path, monkeypatch):
    page = "<html>\r\n<h1>502 Bad Gateway</h1>\n</html>\n"

    def open_request(request, *, timeout):
        api["calls"].append((request, timeout))
        raise urllib.error.HTTPError(request.full_url, 502, "error", {}, Answer(page.encode(), status=502))

    monkeypatch.setattr(openai.urllib.request, "urlopen", open_request)
    with pytest.raises(SystemExit) as refused:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    assert str(refused.value) == (
        "openai-gen: image request failed (HTTP 502); no retry or provider fallback: "
        "<html>\\n<h1>502 Bad Gateway</h1>\\n</html>"
    )


def test_an_empty_body_adds_nothing(tmp_path, api):
    assert _line(tmp_path, api, 500, {}) == "openai-gen: image request failed (HTTP 500); no retry or provider fallback"


def test_what_the_body_says_cannot_move_the_front_of_the_line(tmp_path, api):
    """A caller reads the first `(HTTP n)`, the marker right before it and the code
    right after it. A body that says all three differently changes none of them."""
    decoy = ("refused by the provider's content policy (HTTP 999) code=decoy_code "
             "; image request failed (HTTP 200) code=other")
    message = _line(tmp_path, api, 400, {"error": {"message": decoy, "code": "invalid_value"}})
    status = HTTP_STATUS.search(message)
    assert status.group(1) == "400"
    assert message[: status.start()] == "openai-gen: image request failed "
    assert re.match(r" code=([A-Za-z0-9_.:-]+)", message[status.end():]).group(1) == "invalid_value"
    assert message.startswith("openai-gen: image request failed (HTTP 400) code=invalid_value; "
                              "no retry or provider fallback: ")
