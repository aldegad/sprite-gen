# SPDX-License-Identifier: Apache-2.0
"""Direct API contract, offline: no credentials, network or Grok subprocess."""
import base64
import io
import json
import subprocess
import urllib.error

import pytest
from PIL import Image

from sprite_gen import gen
from sprite_gen.gen import grok_provider as grok, xai
from sprite_gen.gen.base import GenRequest
from sprite_gen.workflow import access, guide


def encoded_image(fmt="JPEG"):
    image = Image.new("RGB", (12, 8), (255, 0, 255))
    for x in range(4, 8):
        for y in range(2, 6):
            image.putpixel((x, y), (0, 0, 200))
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("XAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "no-login"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Grok Build must never be spawned"))
    state = {"status": 200, "body": {"data": [{"b64_json": encoded_image()}]}, "calls": []}

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
        return Response()

    monkeypatch.setattr(xai.urllib.request, "urlopen", open_request)
    return state


def test_generation_decodes_jpeg_to_png_without_agent(tmp_path, api):
    result = gen.generate_image("grok", "a blue square", tmp_path / "out.png", aspect_ratio="3:2")
    request, timeout = api["calls"][0]
    body = json.loads(request.data)
    assert request.full_url == "https://api.x.ai/v1/images/generations"
    assert request.get_header("Authorization") == "Bearer synthetic-secret"
    assert body == {"model": grok.DEFAULT_MODEL, "prompt": "a blue square", "n": 1,
                    "response_format": "b64_json", "aspect_ratio": "3:2"}
    assert timeout == grok.GEN_TIMEOUT_SECONDS
    with Image.open(result.out) as image, Image.open(io.BytesIO(base64.b64decode(encoded_image()))) as original:
        assert image.format == "PNG" and image.size == (12, 8)
        assert image.tobytes() == original.tobytes()
    report = result.to_dict()
    assert report["extra"]["auth_source"] == "XAI_API_KEY"
    assert report["extra"]["transport"] == "xai-api"
    assert report["session_id"] is None
    assert "synthetic-secret" not in json.dumps(report)


@pytest.mark.parametrize("count", [1, 2, 5])
def test_reference_edit_preserves_order_and_model(tmp_path, api, count):
    refs = []
    for i in range(count):
        path = tmp_path / f"ref-{i}.png"
        Image.new("RGB", (12, 8), (i * 40, 0, 0)).save(path)
        refs.append(path)
    result = gen.generate_image("grok", "combine these", tmp_path / "edit.png", refs=refs,
                                model="explicit-image-model", aspect_ratio="16:9")
    request, _ = api["calls"][0]
    body = json.loads(request.data)
    assert request.full_url.endswith("/images/edits")
    images = [body["image"]] if count == 1 else body["images"]
    assert ("images" in body) == (count > 1)
    assert ("image" in body) == (count == 1)
    for ref, entry in zip(refs, images):
        assert base64.b64decode(entry["url"].split(",", 1)[1]) == ref.read_bytes()
    assert body["model"] == result.model == "explicit-image-model"
    assert body.get("aspect_ratio") == (None if count == 1 else "16:9")


def test_direct_output_keeps_chroma_pipeline(tmp_path, api):
    api["body"] = {"data": [{"b64_json": encoded_image("PNG")}]}
    result = gen.generate_image("grok", "blue square on magenta", tmp_path / "alpha.png", transparent=True)
    assert result.alpha["strategy"] == "chroma"
    with Image.open(result.out) as image:
        assert image.getpixel((0, 0)) == (0, 0, 0, 0)
        assert image.getpixel((6, 4))[3] == 255


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_api_failure_keeps_existing_output_and_does_not_retry(tmp_path, api, status):
    api.update(status=status, body={"error": "synthetic-secret https://signed.invalid/private"})
    out = tmp_path / "existing.png"
    out.write_bytes(b"existing")
    with pytest.raises(SystemExit, match=f"HTTP {status}") as error:
        gen.generate_image("grok", "x", out)
    assert "synthetic-secret" not in str(error.value)
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
        grok.GrokProvider().generate(GenRequest("x", raw), tmp_path)
    assert raw.read_bytes() == b"previous raw"
    assert len(api["calls"]) == 1
    assert list(tmp_path.iterdir()) == [raw]


def test_invalid_refs_and_aspect_fail_before_upload(tmp_path, api):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"invalid")
    for options in ({"refs": [ref]}, {"refs": [ref] * 6}, {"aspect_ratio": "bogus"}):
        with pytest.raises(SystemExit):
            grok.GrokProvider().generate(GenRequest("x", tmp_path / "raw.png", **options), tmp_path)
    assert api["calls"] == []


def test_expired_login_fails_without_spawn_or_upload(tmp_path, api, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY")
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"account": {"key": "synthetic-secret", "expires_at": "2000-01-01T00:00:00Z"}}))
    before = auth.read_bytes()
    with pytest.raises(SystemExit, match="expired"):
        gen.generate_image("grok", "x", tmp_path / "out.png")
    assert api["calls"] == [] and auth.read_bytes() == before


def test_timeout_does_not_repeat_billable_request(tmp_path, api, monkeypatch):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(args)
        raise TimeoutError()
    monkeypatch.setattr(xai.urllib.request, "urlopen", timeout)
    with pytest.raises(SystemExit, match="no automatic retry"):
        gen.generate_image("grok", "x", tmp_path / "out.png")
    assert len(calls) == 1


@pytest.mark.parametrize("video", [False, True])
def test_access_uses_api_key_without_grok_binary(api, monkeypatch, video):
    monkeypatch.setattr(access.shutil, "which", lambda _: None)
    result = access.probe_access("grok", video=video)
    assert result["login"] == "ready" and result["billing"] == "api-credit"


def test_image_guide_requires_explicit_api_billing_confirmation(tmp_path, api, monkeypatch):
    monkeypatch.setenv("SPRITE_GEN_CONFIG_DIR", str(tmp_path))
    options = {"choices": {"image_provider": "grok"}, "confirmed_access": ("grok",)}
    pending = guide.guide("image", **options)
    assert pending["status"] != "ready"
    ready = guide.guide("image", **options, confirm_api_billing=True)
    assert ready["status"] == "ready"


def _subscription_login(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(json.dumps({"account": {
        "key": "subscription-token", "expires_at": "2100-01-01T00:00:00Z"}}))


@pytest.mark.parametrize("status", [200, 403, 429])
def test_images_use_subscription_even_with_api_key(tmp_path, api, monkeypatch, status):
    _subscription_login(tmp_path, monkeypatch)
    api["status"] = status
    out = tmp_path / "out.png"
    if status != 200:
        with pytest.raises(SystemExit, match=f"HTTP {status}"):
            gen.generate_image("grok", "x", out)
        assert not out.exists()
    else:
        result = gen.generate_image("grok", "x", out)
        assert result.extra["auth_source"] == "grok-login"
    assert len(api["calls"]) == 1
    assert api["calls"][0][0].get_header("Authorization") == "Bearer subscription-token"


@pytest.mark.parametrize("kind", ["image", "sprite"])
def test_guide_prefers_subscription_without_api_billing_question(tmp_path, api, monkeypatch, kind):
    _subscription_login(tmp_path, monkeypatch)
    monkeypatch.setenv("SPRITE_GEN_CONFIG_DIR", str(tmp_path / "settings"))
    choices = {"image_provider": "grok"}
    if kind == "sprite":
        choices["motion_method"] = "grok-video"
    result = guide.guide(kind, choices=choices, confirmed_access=("grok",))
    assert result["status"] == "ready"
    assert all(item["billing"] == "subscription" for item in result["access"])
    assert not any(question["field"] == "confirm_api_billing" for question in result["questions"])
    assert api["calls"] == []
