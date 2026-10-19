# SPDX-License-Identifier: Apache-2.0
"""Azure transport/auth/configuration contracts. Synthetic credentials; no cloud calls."""
import base64
import io
import json
import subprocess
import urllib.error
from types import SimpleNamespace

import pytest
from PIL import Image

from http_answer import Answer
from sprite_gen import gen
from sprite_gen.gen import azure_provider as azure, logo
from sprite_gen.gen.base import GenRequest
from sprite_gen.workflow import access
from sprite_gen.workflow.catalog import GUIDED_PROVIDERS, validate_choices

KEY = "synthetic-azure-secret"
TOKEN = "synthetic-entra-token"
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"


class ClosingAnswer(Answer):
    def close(self):
        pass


def png(alpha=False):
    image = Image.new("RGBA", (12, 8), (0, 0, 0, 0) if alpha else (40, 70, 180, 255))
    image.putpixel((6, 4), (250, 180, 40, 255))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


@pytest.fixture(autouse=True)
def own_azure_environment(monkeypatch):
    for name in (azure.ENDPOINT_ENV, azure.DEPLOYMENT_ENV, azure.AUTH_ENV, azure.KEY_ENV,
                 azure.VISION_ENV, azure.SUBSCRIPTION_ENV, azure.TENANT_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(azure.ENDPOINT_ENV, "https://example.services.ai.azure.com/")
    monkeypatch.setenv(azure.DEPLOYMENT_ENV, "image-deployment")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv(azure.AUTH_ENV, azure.AUTH_KEY)
    monkeypatch.setenv(azure.KEY_ENV, KEY)
    monkeypatch.setattr(azure.subprocess, "run", lambda *a, **k: pytest.fail("API-key auth must not spawn az"))
    state = {"status": 200, "body": {"data": [{"b64_json": png()}], "usage": {"input_tokens": 12, "output_tokens": 30}},
             "headers": {}, "calls": [], "handlers": []}

    def open_request(request, *, timeout):
        state["calls"].append((request, timeout))
        if state.get("error"):
            raise state["error"]
        answer = ClosingAnswer(state["body"], status=state["status"], headers=state["headers"])
        if state["status"] == 200:
            return answer
        raise urllib.error.HTTPError(request.full_url, state["status"], "synthetic error", state["headers"], answer)

    def build(*handlers):
        state["handlers"].extend(handlers)
        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(azure.urllib.request, "build_opener", build)
    return state


def test_api_key_generation_uses_azure_and_reports_usage(tmp_path, api, capsys):
    result = gen.generate_image("azure", "an original blue sprite", tmp_path / "sprite.png", quality="medium")
    request, timeout = api["calls"][0]
    assert request.full_url == "https://example.services.ai.azure.com/openai/v1/images/generations?api-version=preview"
    assert request.get_header("Api-key") == KEY
    assert request.get_header("Authorization") is None
    assert json.loads(request.data) == {
        "model": "image-deployment", "prompt": "an original blue sprite", "n": 1,
        "size": "1024x1024", "quality": "medium", "output_format": "png", "background": "opaque"}
    assert 0 < timeout <= azure.GEN_TIMEOUT_SECONDS
    assert Image.open(result.out).size == (12, 8)
    report = result.to_dict()
    assert report["provider"] == "azure" and report["model"] == "image-deployment"
    assert report["extra"]["auth_source"] == azure.KEY_ENV
    assert report["extra"]["usage"] == api["body"]["usage"]
    assert report["extra"]["transport"] == "azure-openai-api"
    assert KEY not in json.dumps(report)
    assert "this is a per-call API charge, not a subscription" in capsys.readouterr().err


def test_reference_edit_preserves_reference_order_and_deployment_override(tmp_path, api):
    refs = [tmp_path / "one.png", tmp_path / "two.png"]
    for index, path in enumerate(refs):
        Image.new("RGB", (8, 8), (index * 50, 0, 0)).save(path)
    gen.generate_image("azure", "animate this", tmp_path / "sprite.png", refs=refs,
                       model="other-deployment", aspect_ratio="3:1")
    request, _ = api["calls"][0]
    assert "/images/edits?" in request.full_url
    assert request.get_header("Content-type").startswith("multipart/form-data; boundary=")
    assert b"name=\"model\"\r\n\r\nother-deployment" in request.data
    assert b"name=\"size\"\r\n\r\n1536x512" in request.data
    assert request.data.index(refs[0].read_bytes()) < request.data.index(refs[1].read_bytes())
    assert request.data.count(b'name="image[]"') == 2


def test_native_alpha_and_rate_limits(tmp_path, api):
    api["body"]["data"][0]["b64_json"] = png(alpha=True)
    api["headers"] = {"X-RateLimit-Remaining-Requests": "5", "x-request-id": "not-a-rate-limit"}
    result = gen.generate_image("azure", "sprite", tmp_path / "sprite.png", transparent=True)
    assert json.loads(api["calls"][0][0].data)["background"] == "transparent"
    assert Image.open(result.out).getpixel((0, 0))[3] == 0
    assert result.extra["rate_limit"] == {"x-ratelimit-remaining-requests": "5"}


@pytest.mark.parametrize("endpoint", [
    "http://example.openai.azure.com", "https://name:secret@example.openai.azure.com",
    "https://example.openai.azure.com/?key=secret", "https://example.openai.azure.com/#fragment",
    "https://example.services.ai.azure.com/api/projects/test", "https://example.openai.azure.com/openai/v1/images/generations",
    "https://example.openai.azure.com:0", "https://example.openai.azure.com:999999",
])
def test_invalid_endpoint_fails_before_paid_transport(endpoint, tmp_path, api, monkeypatch):
    monkeypatch.setenv(azure.ENDPOINT_ENV, endpoint)
    with pytest.raises(SystemExit, match="azure-gen:"):
        gen.generate_image("azure", "x", tmp_path / "out.png")
    assert not api["calls"]


@pytest.mark.parametrize("endpoint", ["https://example.openai.azure.com", "https://example.services.ai.azure.com/openai/v1/"])
def test_resource_and_v1_endpoints(endpoint, tmp_path, api, monkeypatch):
    monkeypatch.setenv(azure.ENDPOINT_ENV, endpoint)
    gen.generate_image("azure", "x", tmp_path / "out.png")
    assert api["calls"][0][0].full_url.count("/openai/v1") == 1


def test_missing_deployment_or_invalid_fields_never_spend(tmp_path, api, monkeypatch):
    monkeypatch.delenv(azure.DEPLOYMENT_ENV)
    with pytest.raises(SystemExit, match="deployment name"):
        gen.generate_image("azure", "x", tmp_path / "out.png")
    with pytest.raises(SystemExit, match="azure-gen: resolution"):
        gen.generate_image("azure", "x", tmp_path / "out.png", model="chosen", resolution="2k")
    with pytest.raises(SystemExit, match="azure-gen: invalid reference"):
        (tmp_path / "bad.png").write_text("not an image")
        gen.generate_image("azure", "x", tmp_path / "out.png", model="chosen", refs=[tmp_path / "bad.png"])
    assert not api["calls"]


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500, 503])
def test_http_errors_never_retry_fallback_publish_or_leak(tmp_path, api, status):
    api.update(status=status, body={"error": {"code": "synthetic_error", "message": KEY + " https://signed.invalid/x?sig=secret"}})
    out = tmp_path / "out.png"
    out.write_bytes(b"prior output")
    with pytest.raises(SystemExit, match=f"HTTP {status}") as error:
        gen.generate_image("azure", "x", out)
    assert "no retry or provider fallback" in str(error.value)
    assert KEY not in str(error.value) and "sig=secret" not in str(error.value)
    assert "code=synthetic_error" in str(error.value)
    assert out.read_bytes() == b"prior output"
    assert len(api["calls"]) == 1
    redirect = next(handler for handler in api["handlers"] if isinstance(handler, azure._NoRedirect))
    assert redirect.redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid") is None


def test_uncertain_transport_failure_is_not_replayed(tmp_path, api):
    api["error"] = urllib.error.URLError("synthetic connection failure")
    with pytest.raises(SystemExit, match="no automatic retry"):
        gen.generate_image("azure", "x", tmp_path / "out.png")
    assert len(api["calls"]) == 1 and not (tmp_path / "out.png").exists()


@pytest.mark.parametrize("data", [[], [{"b64_json": "invalid"}], [{"unexpected": "response"}]])
def test_unusable_success_does_not_replace_existing_output(tmp_path, api, data):
    api["body"] = {"data": data}
    out = tmp_path / "out.png"
    out.write_bytes(b"prior")
    with pytest.raises(SystemExit):
        gen.generate_image("azure", "x", out)
    assert out.read_bytes() == b"prior"


def test_cli_auth_pins_subscription_and_checks_tenant_without_changing_defaults(monkeypatch):
    monkeypatch.setenv(azure.SUBSCRIPTION_ENV, SUBSCRIPTION)
    monkeypatch.setenv(azure.TENANT_ENV, TENANT)
    monkeypatch.setenv(azure.KEY_ENV, KEY)  # ignored, not an implicit authentication switch
    monkeypatch.setenv("TEST_MEMBER_ID", "not-for-child")
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/synthetic/profile")
    monkeypatch.setattr(azure, "provider_binary", lambda name: r"C:\Azure CLI\az.cmd")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        data = {"tenantId": TENANT} if "show" in command else {"accessToken": TOKEN}
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

    monkeypatch.setattr(azure.subprocess, "run", run)
    credential = azure.resolve_credential(azure.Config.from_env())
    assert credential.header_value == "Bearer " + TOKEN and credential.source == azure.AUTH_CLI
    assert len(calls) == 2
    for command, kwargs in calls:
        assert command[0] == r"C:\Azure CLI\az.cmd"
        assert "--subscription" in command and SUBSCRIPTION in command
        assert "--tenant" not in command and "set" not in command
        assert kwargs["encoding"] == "utf-8" and kwargs["timeout"] == azure.CLI_TIMEOUT_SECONDS
        assert "TEST_MEMBER_ID" not in kwargs["env"]
        assert kwargs["env"]["AZURE_CONFIG_DIR"] == "/synthetic/profile"
    assert TOKEN not in repr(credential)


def test_tenant_only_auth_does_not_require_a_subscription(monkeypatch):
    monkeypatch.setenv(azure.TENANT_ENV, TENANT)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"accessToken": TOKEN}), stderr="")
    monkeypatch.setattr(azure.subprocess, "run", run)
    azure.resolve_credential(azure.Config.from_env())
    assert len(calls) == 1 and "--tenant" in calls[0] and "--subscription" not in calls[0]


def test_cli_failure_does_not_echo_output_or_fall_back_to_a_key(monkeypatch):
    monkeypatch.setenv(azure.KEY_ENV, KEY)
    monkeypatch.setattr(azure.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=TOKEN, stderr=TOKEN))
    with pytest.raises(SystemExit, match="no auth fallback") as error:
        azure.resolve_credential(azure.Config.from_env())
    assert TOKEN not in str(error.value) and KEY not in str(error.value)


def test_cli_generation_sends_bearer_not_an_available_api_key(tmp_path, api, monkeypatch):
    monkeypatch.delenv(azure.AUTH_ENV)
    monkeypatch.setattr(azure.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({"accessToken": TOKEN}), stderr=""))
    result = gen.generate_image("azure", "x", tmp_path / "out.png")
    request = api["calls"][0][0]
    assert request.get_header("Authorization") == "Bearer " + TOKEN
    assert request.get_header("Api-key") is None
    assert result.extra["auth_source"] == azure.AUTH_CLI
    assert TOKEN not in json.dumps(result.to_dict())


@pytest.mark.parametrize("failure", [FileNotFoundError("missing"), subprocess.TimeoutExpired("az", 30)])
def test_unavailable_cli_fails_without_fallback(monkeypatch, failure):
    def run(*args, **kwargs):
        raise failure
    monkeypatch.setattr(azure.subprocess, "run", run)
    with pytest.raises(SystemExit, match="no auth fallback"):
        azure.resolve_credential(azure.Config.from_env())


def test_malformed_cli_output_is_not_echoed(monkeypatch):
    monkeypatch.setattr(azure.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=TOKEN, stderr=""))
    with pytest.raises(SystemExit, match="invalid credential metadata") as error:
        azure.resolve_credential(azure.Config.from_env())
    assert TOKEN not in str(error.value)


def test_tenant_mismatch_stops_before_token_acquisition(monkeypatch):
    monkeypatch.setenv(azure.SUBSCRIPTION_ENV, SUBSCRIPTION)
    monkeypatch.setenv(azure.TENANT_ENV, TENANT)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"tenantId": SUBSCRIPTION}), stderr="")
    monkeypatch.setattr(azure.subprocess, "run", run)
    with pytest.raises(SystemExit, match="does not belong"):
        azure.resolve_credential(azure.Config.from_env())
    assert len(calls) == 1


@pytest.mark.parametrize("key", [None, "", "bad\ncredential"])
def test_key_auth_never_falls_back_to_cli(monkeypatch, key):
    monkeypatch.setenv(azure.AUTH_ENV, azure.AUTH_KEY)
    if key is not None: monkeypatch.setenv(azure.KEY_ENV, key)
    monkeypatch.setattr(azure.subprocess, "run", lambda *a, **k: pytest.fail("no CLI fallback"))
    with pytest.raises(SystemExit):
        azure.resolve_credential(azure.Config.from_env())


def test_azure_cannot_be_a_default_or_a_guided_subscription_route(monkeypatch, tmp_path, api):
    monkeypatch.setenv(gen.DEFAULT_PROVIDER_ENV, "azure")
    with pytest.raises(SystemExit, match="bills"):
        gen.resolve_default_provider()
    assert "azure" not in GUIDED_PROVIDERS
    with pytest.raises(ValueError):
        validate_choices("image", {"image_provider": "azure"})
    assert not api["calls"]
    assert access.probe_access("azure")["billing"] == "api-credit"
    assert logo.route_of(azure.KEY_ENV) == logo.route_of(azure.AUTH_CLI) == "per-call"


def test_missing_vision_configuration_fails_before_generation_or_logo_spend(tmp_path, api):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    with pytest.raises(SystemExit, match=azure.VISION_ENV):
        gen.generate_image("azure", "x", tmp_path / "out.png", refs=[ref], facing="right")
    with pytest.raises(SystemExit, match=azure.VISION_ENV):
        logo.make_logo("azure", text="HELLO", out_dir=tmp_path / "logo")
    assert not api["calls"]


def test_optional_inspection_uses_only_the_named_azure_deployment(tmp_path, api, monkeypatch):
    monkeypatch.setenv(azure.VISION_ENV, "vision-deployment")
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    api["body"] = {"model": "vision-deployment", "status": "completed",
                   "output": [{"type": "message", "content": [{"type": "output_text", "text": "synthetic answer"}]}]}
    text, metadata = azure.AzureProvider().inspect_image(ref, tmp_path, "Read this image")
    request = api["calls"][0][0]
    assert "/openai/v1/responses?api-version=preview" in request.full_url
    assert json.loads(request.data)["model"] == "vision-deployment"
    assert json.loads(request.data)["store"] is False
    assert text == "synthetic answer" and metadata["transport"] == "azure-openai-api"
