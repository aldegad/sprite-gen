# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video` contract: credential resolution, the api.x.ai call shape,
verified mp4 publishing, and loud failures. No network — the HTTP layer is a fake."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.gen import video

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


def _still(tmp_path: Path) -> Path:
    path = tmp_path / "still.png"
    Image.new("RGBA", (8, 8), (255, 120, 0, 255)).save(path)
    return path


def _login_file(tmp_path: Path, *, expires_at: str | None = "2026-09-08T18:00:00.000000Z", key: str = "tok") -> Path:
    home = tmp_path / "grokhome"
    home.mkdir(exist_ok=True)
    entry = {"key": key, "auth_mode": "oidc"}
    if expires_at is not None:
        entry["expires_at"] = expires_at
    (home / "auth.json").write_text(json.dumps({"https://auth.x.ai::client": entry}), encoding="utf-8")
    return home


class _FakeApi:
    """Scripted xAI: one POST reply, then a sequence of poll replies."""

    def __init__(self, post=(200, {"request_id": "req-1"}), polls=None):
        self.post = post
        self.polls = list(polls or [(200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v/1.mp4", "duration": 6.0}, "model": "grok-imagine-video-1.5"})])
        self.calls: list[tuple[str, str, str, dict | None]] = []
        self.downloads: list[str] = []
        self.payload = MP4

    def call(self, method, url, token, body):
        self.calls.append((method, url, token, body))
        if method == "POST":
            return self.post
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def download(self, url, token):
        self.downloads.append(url)
        return self.payload


def _request(tmp_path: Path, **overrides) -> video.VideoRequest:
    kwargs = dict(image=_still(tmp_path), prompt="camera locked, gentle idle sway", out=tmp_path / "clip.mp4")
    kwargs.update(overrides)
    return video.VideoRequest(**kwargs)


# --- credential resolution -------------------------------------------------


@pytest.mark.parametrize("api_key", ["xai-console", "", "  "])
def test_grok_login_wins_over_api_key_env(tmp_path: Path, monkeypatch, api_key) -> None:
    monkeypatch.setenv("GROK_HOME", str(_login_file(tmp_path)))
    cred = video.resolve_credential(env={"XAI_API_KEY": api_key}, now=NOW)
    assert cred.token == "tok" and cred.source == video.AUTH_SOURCE_GROK_LOGIN


def test_api_key_is_used_only_without_a_login_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "no-login"))
    cred = video.resolve_credential(env={"XAI_API_KEY": "xai-console"}, now=NOW)
    assert cred == video.Credential(token="xai-console", source=video.AUTH_SOURCE_API_KEY)


def test_grok_login_is_used_when_no_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(_login_file(tmp_path, key="oidc-token")))
    cred = video.resolve_credential(env={}, now=NOW)
    assert cred.source == video.AUTH_SOURCE_GROK_LOGIN
    assert cred.token == "oidc-token"
    assert cred.expires_at == "2026-09-08T18:00:00.000000Z"


def test_expired_grok_login_fails_before_upload_with_refresh_prescription(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(_login_file(tmp_path, expires_at="2026-09-08T10:56:34.899829Z")))
    with pytest.raises(SystemExit, match="expired at 2026-09-08T10:56:34") as excinfo:
        video.resolve_credential(env={}, now=NOW)
    message = str(excinfo.value)
    assert video.GROK_REFRESH_COMMAND in message
    assert video.GROK_LOGIN_COMMAND in message
    assert "never rewrites" in message


def test_missing_login_and_key_prescribes_both_setups(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "nowhere"))
    with pytest.raises(SystemExit, match="no xAI credential") as excinfo:
        video.resolve_credential(env={}, now=NOW)
    assert "grok login" in str(excinfo.value) and "XAI_API_KEY" in str(excinfo.value)


def test_empty_api_key_env_is_refused_not_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "no-login"))
    with pytest.raises(SystemExit, match="XAI_API_KEY is set but empty"):
        video.resolve_credential(env={"XAI_API_KEY": "  "}, now=NOW)


def test_login_file_with_two_accounts_is_refused(tmp_path: Path, monkeypatch) -> None:
    home = _login_file(tmp_path)
    (home / "auth.json").write_text(json.dumps({"a": {"key": "1"}, "b": {"key": "2"}}), encoding="utf-8")
    monkeypatch.setenv("GROK_HOME", str(home))
    with pytest.raises(SystemExit, match="holds 2 account entries"):
        video.resolve_credential(env={}, now=NOW)


def test_login_without_expiry_is_accepted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_HOME", str(_login_file(tmp_path, expires_at=None)))
    assert video.resolve_credential(env={}, now=NOW).expires_at is None


# --- generation ------------------------------------------------------------


def test_generate_video_posts_data_url_polls_and_publishes_verified_mp4(tmp_path: Path) -> None:
    api = _FakeApi(polls=[(200, {"status": "pending", "progress": 10}), (200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v/1.mp4", "duration": 6.0}, "model": "grok-imagine-video-1.5"})])
    slept: list[float] = []
    cred = video.Credential(token="tok", source=video.AUTH_SOURCE_GROK_LOGIN)

    result = video.generate_video(
        _request(tmp_path, aspect_ratio="1:1", generate_audio=False),
        credential=cred, call=api.call, download=api.download, sleep=slept.append,
    )

    method, url, token, body = api.calls[0]
    assert (method, url, token) == ("POST", f"{video.API_BASE}/videos/generations", "tok")
    assert body["model"] == video.DEFAULT_MODEL
    assert body["duration"] == 6 and body["resolution"] == "720p" and body["aspect_ratio"] == "1:1"
    assert body["generate_audio"] is False
    assert body["image"]["url"].startswith("data:image/png;base64,")
    assert "output" not in body  # no upload_url: the direct call is what works on ZDR teams
    assert api.calls[1][:2] == ("GET", f"{video.API_BASE}/videos/req-1")
    assert slept == [video.POLL_INTERVAL_SECONDS]
    assert api.downloads == ["https://vidgen.x.ai/v/1.mp4"]
    assert result.out.read_bytes() == MP4
    assert not result.out.with_name("clip.mp4.part").exists()
    payload = result.to_dict()
    assert payload["kind"] == "sprite-gen-video-report"
    assert payload["auth_source"] == "grok-login"
    assert payload["request_id"] == "req-1"
    assert payload["host"] == "vidgen.x.ai"
    assert payload["duration_reported"] == 6.0 and payload["duration_requested"] == 6
    assert payload["polls"] == 2
    # Secrets never reach the report: no token, no full download URL.
    dumped = json.dumps(payload)
    assert "tok" not in dumped.replace("token", "") and "vidgen.x.ai/v/1.mp4" not in dumped


def test_audio_default_is_left_to_the_api(tmp_path: Path) -> None:
    api = _FakeApi()
    video.generate_video(_request(tmp_path), credential=video.Credential("t", "XAI_API_KEY"), call=api.call, download=api.download, sleep=lambda s: None)
    assert "generate_audio" not in api.calls[0][3] and "aspect_ratio" not in api.calls[0][3]


@pytest.mark.parametrize(
    ("post", "polls", "expected"),
    [
        ((200, {"error": "quota"}), None, "generation request refused"),
        ((400, {"code": "invalid-argument", "error": "Zero Data Retention teams must provide output.upload_url"}), None, "refused \\(HTTP 400\\)"),
        ((403, {"code": "unauthenticated:bad-credentials", "error": "The OAuth2 access token could not be validated."}), None, "rejected the credential \\(grok-login\\) with HTTP 403"),
        ((200, {"request_id": "r"}), [(200, {"status": "failed", "error": "nsfw"})], "status='failed'"),
        ((200, {"request_id": "r"}), [(200, {"status": "expired"})], "status='expired'"),
        ((200, {"request_id": "r"}), [(500, {"raw": "boom"})], "HTTP 500"),
        ((200, {"request_id": "r"}), [(200, {"status": "done", "video": {}})], "reported no video url"),
    ],
)
def test_api_failures_are_named_and_write_nothing(tmp_path: Path, post, polls, expected) -> None:
    api = _FakeApi(post=post, polls=polls)
    request = _request(tmp_path)
    with pytest.raises(SystemExit, match=expected):
        video.generate_video(request, credential=video.Credential("t", video.AUTH_SOURCE_GROK_LOGIN), call=api.call, download=api.download, sleep=lambda s: None)
    assert not request.out.exists()
    assert not list(tmp_path.glob("*.part"))


def test_bad_credential_prescribes_refresh_for_login_and_check_for_key(tmp_path: Path) -> None:
    api = _FakeApi(post=(403, {"error": "bad"}))
    with pytest.raises(SystemExit, match=video.GROK_REFRESH_COMMAND.split()[0]):
        video.generate_video(_request(tmp_path), credential=video.Credential("t", video.AUTH_SOURCE_GROK_LOGIN), call=api.call, download=api.download, sleep=lambda s: None)
    with pytest.raises(SystemExit, match="check the XAI_API_KEY value"):
        video.generate_video(_request(tmp_path), credential=video.Credential("t", video.AUTH_SOURCE_API_KEY), call=api.call, download=api.download, sleep=lambda s: None)


def test_poll_timeout_fails_loud(tmp_path: Path, monkeypatch) -> None:
    api = _FakeApi(polls=[(200, {"status": "pending"})])
    clock = {"t": 0.0}
    monkeypatch.setattr(video.time, "monotonic", lambda: clock["t"])

    def sleep(seconds: float) -> None:
        clock["t"] += seconds

    request = _request(tmp_path)
    with pytest.raises(SystemExit, match="poll timeout"):
        video.generate_video(request, credential=video.Credential("t", "XAI_API_KEY"), call=api.call, download=api.download, sleep=sleep, poll_timeout=10)
    assert not request.out.exists()


def test_non_mp4_download_is_refused(tmp_path: Path) -> None:
    api = _FakeApi()
    api.payload = b"<html>not a video</html>"
    request = _request(tmp_path)
    with pytest.raises(SystemExit, match="not an mp4"):
        video.generate_video(request, credential=video.Credential("t", "XAI_API_KEY"), call=api.call, download=api.download, sleep=lambda s: None)
    assert not request.out.exists()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"duration": 0}, "--duration must be 1..15"),
        ({"duration": 16}, "--duration must be 1..15"),
        ({"resolution": "4k"}, "--resolution must be one of"),
        ({"aspect_ratio": "21:9"}, "--aspect-ratio must be one of"),
        ({"prompt": "   "}, "empty prompt"),
    ],
)
def test_request_validation_runs_before_any_call(tmp_path: Path, overrides, expected) -> None:
    api = _FakeApi()
    with pytest.raises(SystemExit, match=expected):
        video.generate_video(_request(tmp_path, **overrides), credential=video.Credential("t", "XAI_API_KEY"), call=api.call, download=api.download)
    assert api.calls == []


def test_missing_still_fails_before_any_call(tmp_path: Path) -> None:
    api = _FakeApi()
    with pytest.raises(SystemExit, match="still image not found"):
        video.generate_video(video.VideoRequest(image=tmp_path / "nope.png", prompt="p", out=tmp_path / "o.mp4"), credential=video.Credential("t", "XAI_API_KEY"), call=api.call, download=api.download)
    assert api.calls == []


# --- CLI ---------------------------------------------------------------------


def test_cli_run_writes_report_and_defaults(tmp_path: Path, monkeypatch) -> None:
    api = _FakeApi()
    monkeypatch.setattr(video, "resolve_credential", lambda **_: video.Credential("t", video.AUTH_SOURCE_API_KEY))
    monkeypatch.setattr(video, "http_json", api.call)
    monkeypatch.setattr(video, "http_download", api.download)
    monkeypatch.setattr(video.time, "sleep", lambda s: None)
    out = tmp_path / "clip.mp4"
    report = tmp_path / "report.json"

    rc = video.run(image=_still(tmp_path), prompt="sway", prompt_file=None, out=out, duration=6, resolution="720p", aspect_ratio=None, model=video.DEFAULT_MODEL, generate_audio=None, report=report)

    assert rc == 0
    assert out.read_bytes() == MP4
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["auth_source"] == "XAI_API_KEY" and payload["out"] == str(out.resolve())


def test_cli_parser_shape() -> None:
    parser = video._build_parser()
    args = parser.parse_args(["--image", "a.png", "--prompt", "p", "--out", "o.mp4"])
    assert args.duration == 6 and args.resolution == "720p" and args.aspect_ratio is None and args.generate_audio is None
    assert parser.parse_args(["--image", "a.png", "--out", "o.mp4", "--no-audio"]).generate_audio is False
    assert parser.parse_args(["--image", "a.png", "--out", "o.mp4", "--audio"]).generate_audio is True
    with pytest.raises(SystemExit):
        parser.parse_args(["--image", "a.png", "--out", "o.mp4", "--resolution", "4k"])


def test_unified_cli_exposes_video() -> None:
    from sprite_gen import cli

    assert "video" in cli.COMMANDS
    parser = cli._build_parser()
    args = parser.parse_args(["video", "--image", "a.png", "--prompt", "p", "--out", "o.mp4"])
    assert args.command == "video"


@pytest.mark.parametrize("contents, message", [
    ('{"account":{"key":"subscription","expires_at":"2000-01-01T00:00:00Z"}}', "expired"),
    ('{', "cannot read grok login"),
    ('{"account":{}}', "no access token"),
    ('{"a":{"key":"one"},"b":{"key":"two"}}', "holds 2 account"),
])
def test_invalid_login_never_falls_back_to_configured_key(tmp_path, monkeypatch, contents, message):
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    auth = tmp_path / "auth.json"
    auth.write_text(contents)
    with pytest.raises(SystemExit, match=message):
        video.resolve_credential(env={"XAI_API_KEY": "console-key"}, now=NOW)
    assert auth.read_text() == contents


def test_login_directory_never_permits_api_credit(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    (tmp_path / "auth.json").mkdir()
    with pytest.raises(SystemExit, match="not a readable file"):
        video.resolve_credential(env={"XAI_API_KEY": "console-key"}, now=NOW)


def test_uninspectable_login_never_permits_api_credit(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    original = Path.lstat
    def denied(path, *args, **kwargs):
        if path == tmp_path / "auth.json":
            raise PermissionError("test")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(SystemExit, match="refusing API-credit fallback"):
        video.resolve_credential(env={"XAI_API_KEY": "console-key"}, now=NOW)


@pytest.mark.parametrize("status", [200, 403, 429])
def test_video_uses_subscription_with_api_key_present(tmp_path, monkeypatch, status):
    monkeypatch.setenv("GROK_HOME", str(_login_file(tmp_path, key="subscription-token", expires_at="2100-01-01T00:00:00Z")))
    monkeypatch.setenv("XAI_API_KEY", "console-key")
    api = _FakeApi(post=(status, {"error": "rejected"}) if status != 200 else (200, {"request_id": "req-1"}))
    request = _request(tmp_path)
    if status != 200:
        with pytest.raises(SystemExit, match=f"HTTP {status}"):
            video.generate_video(request, call=api.call, download=api.download, sleep=lambda _: None)
        assert len(api.calls) == 1 and not request.out.exists()
    else:
        result = video.generate_video(request, call=api.call, download=api.download, sleep=lambda _: None)
        assert result.auth_source == "grok-login"
    assert all(call[2] == "subscription-token" for call in api.calls)
