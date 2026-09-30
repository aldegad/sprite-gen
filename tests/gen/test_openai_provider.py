# SPDX-License-Identifier: Apache-2.0
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

import pytest
from PIL import Image

from sprite_gen import gen
from sprite_gen.gen import grok_provider as grok
from sprite_gen.gen import openai_provider as openai
from sprite_gen.gen import xai
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


class _GrokResponse:
    """Minimal 200 answer for the xai transport (a PNG, so chroma stays untouched)."""

    def __init__(self, request, api):
        api["calls"].append((request, None))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    status = 200

    def read(self):
        return json.dumps({"data": [{"b64_json": encoded_image()}]}).encode()


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no provider subprocess may be spawned"))
    state = {"status": 200, "body": {"data": [{"b64_json": encoded_image()}],
                                     "usage": {"input_tokens": 12, "output_tokens": 340}}, "calls": []}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @property
        def status(self):
            return state["status"]

        def read(self):
            return json.dumps(state["body"]).encode()

    def open_request(request, *, timeout):
        state["calls"].append((request, timeout))
        if state["status"] == 200:
            return Response()
        raise urllib.error.HTTPError(request.full_url, state["status"], "error", {},
                                     io.BytesIO(json.dumps(state["body"]).encode()))

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
    assert timeout == openai.GEN_TIMEOUT_SECONDS
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


@pytest.mark.parametrize("quality", openai.SUPPORTED_QUALITIES)
def test_every_quality_level_reaches_the_request_verbatim(tmp_path, api, quality):
    gen.generate_image("openai", "x", tmp_path / f"{quality}.png", quality=quality)
    assert _body(api["calls"][0][0])["quality"] == quality


@pytest.mark.parametrize("ratio,size", sorted(openai.SIZES.items()))
def test_aspect_ratio_maps_to_a_legal_gpt_image_size(tmp_path, api, ratio, size):
    gen.generate_image("openai", "x", tmp_path / "out.png", aspect_ratio=ratio)
    assert _body(api["calls"][0][0])["size"] == size
    if size == "auto":
        return
    width, height = (int(side) for side in size.split("x"))
    # The documented custom-size constraints, checked on the table itself.
    assert width % 16 == 0 and height % 16 == 0
    assert max(width, height) <= 3840
    assert 1 / 3 <= width / height <= 3
    assert 655_360 <= width * height <= 8_294_400


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


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_api_failure_keeps_existing_output_and_does_not_retry(tmp_path, api, status):
    api.update(status=status, body={"error": {"message": f"{KEY} https://signed.invalid/private"}})
    out = tmp_path / "existing.png"
    out.write_bytes(b"existing")
    with pytest.raises(SystemExit, match=f"HTTP {status}") as error:
        gen.generate_image("openai", "x", out)
    assert KEY not in str(error.value)
    assert "signed.invalid" not in str(error.value)
    assert out.read_bytes() == b"existing"
    assert len(api["calls"]) == 1


@pytest.mark.parametrize("body", [{}, {"data": []}, {"data": [None]}, {"data": [{}, {}]},
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


# --- 구독 우선 불변식 (수홍 2026-09-20) --------------------------------------
# sprite-gen runs on subscriptions people already pay for. The API-key backend is
# for servers and SaaS and is billed per call, so nothing may route to it on its
# own. These three tests are the guard rails for invariants 1, 2 and 3.


def test_openai_is_explicit_only_and_never_a_default(monkeypatch, api):
    """불변식 1: no default, no saved preference, no guided-flow option."""
    assert gen.HARD_DEFAULT_PROVIDER == "codex"
    assert gen.EXPLICIT_ONLY_PROVIDERS == ("openai",)
    monkeypatch.delenv("SPRITE_GEN_DEFAULT_PROVIDER", raising=False)
    monkeypatch.setattr(gen, "_codex_available", lambda: (True, ""))
    assert gen.resolve_default_provider() == ("codex", None)
    # The env knob cannot stand it up either.
    monkeypatch.setenv("SPRITE_GEN_DEFAULT_PROVIDER", "openai")
    with pytest.raises(SystemExit, match=r"--provider openai"):
        gen.resolve_default_provider()
    # Not offered by the guided flow, so it can never be saved as a preference.
    assert "openai" not in GUIDED_PROVIDERS
    assert "openai" not in FIELDS["image_provider"]["options"]
    with pytest.raises(ValueError):
        validate_choices("image", {"image_provider": "openai"})


@pytest.mark.parametrize("configured", [None, "codex"])
def test_an_api_key_in_the_environment_reroutes_nothing(monkeypatch, api, configured):
    """불변식 2: a codex outage reaches grok, never the metered key."""
    assert "OPENAI_API_KEY" in os.environ  # the fixture set it
    if configured is None:
        monkeypatch.delenv("SPRITE_GEN_DEFAULT_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("SPRITE_GEN_DEFAULT_PROVIDER", configured)
    monkeypatch.setattr(gen, "_codex_available", lambda: (False, "synthetic outage"))
    provider, fallback = gen.resolve_default_provider()
    assert provider == "grok"
    assert fallback["from"] == "codex" and fallback["to"] == "grok"
    assert api["calls"] == []


def test_grok_login_still_outranks_its_api_key(tmp_path, api, monkeypatch, capsys):
    """불변식 3: the Grok subscription login wins over XAI_API_KEY."""
    monkeypatch.setenv("XAI_API_KEY", "xai-api-secret")
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(json.dumps({"account": {
        "key": "subscription-token", "expires_at": "2100-01-01T00:00:00Z"}}))
    assert xai.resolve_credential().source == xai.AUTH_SOURCE_GROK_LOGIN
    monkeypatch.setattr(xai.urllib.request, "urlopen",
                        lambda request, *, timeout: _GrokResponse(request, api))
    result = gen.generate_image("grok", "x", tmp_path / "grok.png")
    assert result.extra["auth_source"] == xai.AUTH_SOURCE_GROK_LOGIN
    # A subscription call says nothing about API billing; only the key route does.
    assert "per-call API charge" not in capsys.readouterr().err


def test_every_api_key_call_announces_the_charge_before_it_leaves(tmp_path, api, capsys):
    """불변식 5: one stderr line, before the request, naming the billing route."""
    gen.generate_image("openai", "x", tmp_path / "out.png", quality="low")
    notice = [line for line in capsys.readouterr().err.splitlines() if "per-call API charge" in line]
    assert len(notice) == 1
    assert "OPENAI_API_KEY" in notice[0] and "quality=low" in notice[0]


def test_grok_on_an_api_key_announces_the_charge_too(tmp_path, api, monkeypatch, capsys):
    monkeypatch.setenv("XAI_API_KEY", "xai-api-secret")
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "no-login"))
    monkeypatch.setattr(xai.urllib.request, "urlopen",
                        lambda request, *, timeout: _GrokResponse(request, api))
    gen.generate_image("grok", "x", tmp_path / "grok.png")
    notice = [line for line in capsys.readouterr().err.splitlines() if "per-call API charge" in line]
    assert len(notice) == 1 and "XAI_API_KEY" in notice[0]


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
    api.update(status=status, body={"error": {"message": f"{KEY} synthetic prompt echo", **error}})
    with pytest.raises(SystemExit) as refused:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    message = str(refused.value)
    assert KEY not in message and "prompt echo" not in message
    assert HTTP_STATUS.search(message).group(1) == str(status)
    assert len(api["calls"]) == 1
    return message


@pytest.mark.parametrize("details, stage", [
    ({"moderation_details": {"moderation_stage": "output", "categories": ["other"]}}, " moderation_stage=output"),
    ({"moderation_details": {"moderation_stage": "input"}}, " moderation_stage=input"),
    ({"moderation_details": {"moderation_stage": "unknown"}}, " moderation_stage=unknown"),
    ({}, ""),
    ({"moderation_details": None}, ""),
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


@pytest.mark.parametrize("error", [{}, {"code": None}, {"code": 400}, {"code": "has a space"},
                                   {"code": "x" * 65}, {"code": ""}, {"code": "quote'd"}])
def test_no_code_or_a_code_outside_the_format_leaves_the_line_as_it_was(tmp_path, api, error):
    assert _refused(tmp_path, api, 400, error) == (
        "openai-gen: image request failed (HTTP 400); no retry or provider fallback"
    )


def test_a_top_level_code_is_not_the_openai_error_code(tmp_path, api):
    api.update(status=400, body={"code": "moderation_blocked", "error": {"message": "x"}})
    with pytest.raises(SystemExit) as refused:
        gen.generate_image("openai", "x", tmp_path / "out.png")
    assert str(refused.value) == "openai-gen: image request failed (HTTP 400); no retry or provider fallback"


def test_a_rejected_key_names_the_code_too(tmp_path, api):
    assert _refused(tmp_path, api, 401, {"code": "invalid_api_key"}).startswith(
        "openai-gen: credential OPENAI_API_KEY rejected (HTTP 401) code=invalid_api_key; check the key's value"
    )
