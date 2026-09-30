# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video` contract: credential resolution, the api.x.ai call shape,
verified mp4 publishing, and loud failures. No network — the HTTP layer is a fake."""

from __future__ import annotations

import json
import re
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


def test_refresh_prescription_is_a_non_agent_command_run_outside_the_repo(tmp_path: Path, monkeypatch) -> None:
    # 2026-09-13 incident: `grok -p ok` started the coding agent in the worker's cwd,
    # overwrote a script and spent a paid video call. The prescription must never be
    # a prompt, and must say where to run it.
    assert video.GROK_REFRESH_COMMAND == "grok models"
    assert " -p " not in f" {video.GROK_REFRESH_COMMAND} " and "--single" not in video.GROK_REFRESH_COMMAND
    assert "empty directory" in video.GROK_REFRESH_WHERE
    monkeypatch.setenv("GROK_HOME", str(_login_file(tmp_path, expires_at="2026-09-08T10:56:34.899829Z")))
    with pytest.raises(SystemExit) as excinfo:
        video.resolve_credential(env={}, now=NOW)
    message = str(excinfo.value)
    assert video.GROK_REFRESH_WHERE in message
    assert "grok -p ok" not in message.replace("`grok -p …`", "")
    assert "coding agent" in message


# --- the API-credit notice (구독 우선 불변식 5) ---------------------------------


def _billed_run(verb: str, tmp_path: Path, **transport):
    """Submit one job of `verb` through the fake transport."""
    if verb == "video":
        return video.generate_video(_request(tmp_path, duration=3, resolution="480p"), **transport)
    clip = tmp_path / "in.mp4"
    clip.write_bytes(MP4)
    transport = {**transport, "probe": lambda path: 6.0}  # ffprobe stand-in: input and staged alike
    if verb == "video-extend":
        return video.extend_video(video.ExtendRequest(video=clip, prompt="she presses on", out=tmp_path / "longer.mp4", duration=4), **transport)
    return video.edit_video(video.EditRequest(video=clip, prompt="make the hakama black", out=tmp_path / "edited.mp4"), **transport)


@pytest.mark.parametrize("verb, priced", [
    ("video", " (duration=3s, resolution=480p)"),
    ("video-extend", " (duration=4s)"),
    ("video-edit", ""),  # the edit body sends neither knob, so the line names neither
])
def test_every_verb_announces_the_charge_before_the_request_leaves(tmp_path: Path, capsys, verb, priced) -> None:
    """A clip on XAI_API_KEY is metered API credit; the payer hears it before the upload,
    not on the invoice. Imagine prices a clip by length x output size, so the knobs that
    set the amount ride along with the charge."""
    api = _FakeApi()
    heard: list[str] = []

    def call(method, url, token, body):
        if method == "POST":
            heard.append(capsys.readouterr().err)  # everything said before the job was submitted
        return api.call(method, url, token, body)

    _billed_run(verb, tmp_path, credential=video.Credential("console-key", video.AUTH_SOURCE_API_KEY),
                call=call, download=api.download, sleep=lambda s: None)

    notice = [line for line in heard[0].splitlines() if "per-call API charge" in line]
    assert len(notice) == 1
    assert notice[0].startswith(f"[gen] {verb}: running on {video.AUTH_ENV} ")
    assert f"not a subscription{priced} — " in notice[0]
    # The shared prefix denies the subscription once; the detail carries the remedy only
    # (the grok image notice read "not a subscription (...), not your Grok subscription").
    assert notice[0].count("not ") == 1
    assert f"`{video.GROK_LOGIN_COMMAND}` signs your Grok subscription in." in notice[0]


@pytest.mark.parametrize("verb", ["video", "video-extend", "video-edit"])
def test_the_subscription_route_stays_silent_about_billing(tmp_path: Path, capsys, verb) -> None:
    api = _FakeApi()
    _billed_run(verb, tmp_path, credential=video.Credential("tok", video.AUTH_SOURCE_GROK_LOGIN),
                call=api.call, download=api.download, sleep=lambda s: None)
    assert "per-call API charge" not in capsys.readouterr().err


def test_a_request_that_never_leaves_never_announces_a_charge(tmp_path: Path, monkeypatch, capsys) -> None:
    """The line tracks charges, not intentions: a body refused locally costs nothing."""
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "no-login"))
    monkeypatch.setenv("XAI_API_KEY", "console-key")
    api = _FakeApi()
    with pytest.raises(SystemExit, match="--duration must be"):
        video.generate_video(_request(tmp_path, duration=99), call=api.call, download=api.download, sleep=lambda s: None)
    assert api.calls == []
    assert "per-call API charge" not in capsys.readouterr().err


def test_the_cli_announces_on_stderr_leaving_the_report_on_stdout(tmp_path: Path, monkeypatch, capsys) -> None:
    """The credential the environment actually hands the CLI is what decides, and the
    notice goes to stderr so a caller parsing stdout still reads one JSON report."""
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "no-login"))
    monkeypatch.setenv("XAI_API_KEY", "console-key")
    api = _FakeApi()
    monkeypatch.setattr(video, "http_json", api.call)
    monkeypatch.setattr(video, "http_download", api.download)
    monkeypatch.setattr(video.time, "sleep", lambda s: None)

    rc = video.run(image=_still(tmp_path), prompt="sway", prompt_file=None, out=tmp_path / "clip.mp4",
                   duration=6, resolution="720p", aspect_ratio=None, model=video.DEFAULT_MODEL,
                   generate_audio=None, report=None)

    captured = capsys.readouterr()
    assert rc == 0
    assert len([line for line in captured.err.splitlines() if "per-call API charge" in line]) == 1
    assert json.loads(captured.out)["auth_source"] == "XAI_API_KEY"
    assert "console-key" not in captured.err + captured.out  # the key itself is never printed


# --- refusal lines: the provider code, and xAI's one content-policy signal ---------

HTTP_STATUS = re.compile(r"\(HTTP (\d{3})\)")  # what a caller reads the status with


def _refused(tmp_path: Path, api: _FakeApi) -> tuple[str, Path]:
    request = _request(tmp_path)
    with pytest.raises(SystemExit) as refused:
        video.generate_video(request, credential=video.Credential("t", video.AUTH_SOURCE_GROK_LOGIN), call=api.call, download=api.download, sleep=lambda s: None)
    assert not request.out.exists() and not list(tmp_path.glob("*.part"))
    assert api.downloads == []
    return str(refused.value), request.out


def test_a_moderated_clip_is_a_content_policy_refusal(tmp_path: Path) -> None:
    api = _FakeApi(polls=[(200, {"status": "done", "video": {"url": "", "respect_moderation": False}})])
    message, _ = _refused(tmp_path, api)
    assert message == ("video: generation req-1 refused by the provider's content policy (HTTP 200) "
                       "respect_moderation=false; nothing was written")
    assert HTTP_STATUS.search(message).group(1) == "200"


def test_a_moderated_clip_is_refused_even_if_a_url_came_back(tmp_path: Path) -> None:
    api = _FakeApi(polls=[(200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v/1.mp4", "respect_moderation": False}})])
    assert "refused by the provider's content policy" in _refused(tmp_path, api)[0]


def test_a_clip_that_respects_moderation_publishes(tmp_path: Path) -> None:
    api = _FakeApi(polls=[(200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v/1.mp4", "duration": 6.0, "respect_moderation": True}})])
    request = _request(tmp_path)
    video.generate_video(request, credential=video.Credential("t", video.AUTH_SOURCE_GROK_LOGIN), call=api.call, download=api.download, sleep=lambda s: None)
    assert request.out.read_bytes() == MP4


def test_a_failed_poll_names_the_code_inside_its_error_object(tmp_path: Path) -> None:
    """`invalid_argument` covers moderation and bad parameters alike, so it is a code, not a policy verdict."""
    api = _FakeApi(polls=[(200, {"status": "failed", "error": {"code": "invalid_argument", "message": "synthetic"}})])
    message, _ = _refused(tmp_path, api)
    assert message.startswith("video: generation req-1 ended with status='failed' (HTTP 200) code=invalid_argument: ")
    assert "content policy" not in message


@pytest.mark.parametrize("scripted", ["post", "poll"])
def test_what_xai_said_is_shown_with_keys_and_signatures_masked(tmp_path: Path, scripted) -> None:
    """The image lines' mask (refusal.masked) on the clip lines, which already print the body."""
    token = "synthetic-login-token"
    said = (f"echo {token}, key xai-{'Synthetic0Key1For2Tests3Only4' * 3}, "
            "url https://vidgen.x.ai/v/1.mp4?sig=abc, (HTTP 999) code=decoy")
    error = {"code": "invalid_argument", "message": said}
    api = _FakeApi(post=(400, {"error": error})) if scripted == "post" else _FakeApi(polls=[(200, {"status": "failed", "error": error})])
    request = _request(tmp_path)
    with pytest.raises(SystemExit) as refused:
        video.generate_video(request, credential=video.Credential(token, video.AUTH_SOURCE_GROK_LOGIN),
                             call=api.call, download=api.download, sleep=lambda s: None)
    message = str(refused.value)
    front = ("video: generation request refused (HTTP 400) code=invalid_argument: " if scripted == "post"
             else "video: generation req-1 ended with status='failed' (HTTP 200) code=invalid_argument: ")
    # A query runs to the next space or quote, so the comma after it goes too.
    assert message == front + ("error={'code': 'invalid_argument', 'message': 'echo [redacted], key xai-[redacted], "
                               "url https://vidgen.x.ai/v/1.mp4?[redacted] (HTTP 999) code=decoy'}"
                               + ("" if scripted == "post" else ", status='failed'"))
    assert HTTP_STATUS.search(message).group(1) == ("400" if scripted == "post" else "200")


@pytest.mark.parametrize("body", [
    "x" * 285 + " xai-" + "K" * 60,
    {"detail": "x" * 270 + " xai-" + "K" * 60},
], ids=["text", "json"])
def test_a_key_the_300_character_cut_would_split_is_masked_before_the_cut(body) -> None:
    """Masked, then cut: cut first, the key's tail was shorter than a key and slipped through."""
    detail = video._error_detail(body)
    assert "KKKK" not in detail
    assert "xai-[redacted]" in detail and len(detail) <= 300


@pytest.mark.parametrize(("said", "masked"), [
    ("api_key_sk-" + "Synthetic0Key1For2Tests" * 2, "api_key_sk-[redacted]"),
    ("token-xai-" + "Synthetic0Key1For2Tests" * 2, "token-xai-[redacted]"),
    ("task-" + "Synthetic0Key1For2Tests" * 2, "task-" + "Synthetic0Key1For2Tests" * 2),
], ids=["underscore", "hyphen", "word"])
def test_a_key_after_an_underscore_or_hyphen_is_masked_and_a_word_ending_in_sk_is_not(said, masked) -> None:
    assert video._error_detail({"message": said}) == f"message={masked!r}"


@pytest.mark.parametrize(("said", "masked"), [
    ("Authorization: Bearer " + "synthetic.token.value-0123456789", "Authorization: Bearer [redacted]"),
    ("x_Bearer " + "synthetic.token.value-0123456789", "x_Bearer [redacted]"),
    ("x-bearer " + "synthetic.token.value-0123456789", "x-bearer [redacted]"),
    ("xbearer " + "synthetic.token.value-0123456789", "xbearer " + "synthetic.token.value-0123456789"),
], ids=["header", "underscore", "hyphen", "word"])
def test_a_bearer_value_after_an_underscore_or_hyphen_is_masked_and_a_word_ending_in_bearer_is_not(said, masked) -> None:
    assert video._error_detail({"message": said}) == f"message={masked!r}"


@pytest.mark.parametrize(("post", "prefix"), [
    ((400, {"code": "invalid-argument", "error": "synthetic"}), "video: generation request refused (HTTP 400) code=invalid-argument: "),
    ((400, {"error": {"code": "invalid_argument", "message": "synthetic"}}), "video: generation request refused (HTTP 400) code=invalid_argument: "),
    ((400, {"error": "synthetic"}), "video: generation request refused (HTTP 400): "),
    ((400, {"code": "not a code", "error": "synthetic"}), "video: generation request refused (HTTP 400): "),
    ((429, {"error": {"code": "x" * 65}}), "video: generation request refused (HTTP 429): "),
])
def test_a_refused_request_names_a_well_formed_code(tmp_path: Path, post, prefix) -> None:
    message, _ = _refused(tmp_path, _FakeApi(post=post))
    assert message.startswith(prefix)
    assert HTTP_STATUS.search(message).group(1) == str(post[0])
