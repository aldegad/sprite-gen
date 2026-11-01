# SPDX-License-Identifier: Apache-2.0
"""Where every direct provider call goes (`sprite_gen.gen.address`, docs/gen.md "Provider addresses").

Each provider's calls go to its `SPRITE_GEN_<PROVIDER>_BASE_URL` when it is set — read at the call, so a value set
after import is followed — and to the documented address when it is unset or empty. A URL the provider hands back
(a finished clip, a batch's results file) is fetched through the same base. A real local HTTP server stands in for
the relay; nothing reaches the network."""
from __future__ import annotations

import ast
import base64
import http.server
import io
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from http_answer import Answer
from sprite_gen import gen
from sprite_gen.gen import anthropic_api, facing_vision, openai_provider, retry, video, xai

ROOT = Path(__file__).resolve().parents[2]
URLOPEN = urllib.request.urlopen
KEY = "door-token"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
CLIP = "https://vidgen.x.ai/v/1.mp4?sig=abc"
PRIVATE_CLIP = "https://vidgen.x.ai/v/2.mp4"  # answered only with the bearer token
RESULTS = "https://api.anthropic.com/v1/messages/batches/msgbatch_01/results"
DEFAULTS = {
    "openai": ("SPRITE_GEN_OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "xai": ("SPRITE_GEN_XAI_BASE_URL", "https://api.x.ai/v1"),
    "anthropic": ("SPRITE_GEN_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
}


def _png(tmp_path: Path) -> Path:
    path = tmp_path / "still.png"
    Image.new("RGBA", (8, 8), (255, 120, 0, 255)).save(path)
    return path


def _png_b64(field=(20, 90, 200)) -> str:
    """A 12x8 picture: a blue block on `field` (magenta for a provider keyed on chroma)."""
    image = Image.new("RGB", (12, 8), field)
    for x in range(4, 8):
        for y in range(2, 6):
            image.putpixel((x, y), (20, 90, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


VISION = {"model": "m", "status": "completed",
          "output": [{"type": "message", "content": [{"type": "output_text", "text": "right"}]}]}
SSE = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in (
    {"type": "message_start", "message": {"id": "msg_01", "model": anthropic_api.MODEL, "content": [],
                                          "usage": {"input_tokens": 5, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
    {"type": "message_stop"})).encode()


def _answer(method: str, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    """What the relay answers, by what was asked: each provider's own answer shape."""
    route = urllib.parse.urlsplit(path)
    if route.path.endswith("/sprite-gen/fetch"):
        fetched = urllib.parse.parse_qs(route.query)["url"][0]
        if fetched == CLIP:
            return 200, {}, MP4
        if fetched == PRIVATE_CLIP:
            return (200, {}, MP4) if (headers or {}).get("authorization") == f"Bearer {KEY}" else (403, {}, b"{}")
        assert fetched == RESULTS, fetched
        line = {"custom_id": "c1", "result": {"type": "succeeded", "message": {"id": "msg_b", "content": []}}}
        return 200, {}, json.dumps(line).encode() + b"\n"
    answers = {
        ("POST", "/openai/images/generations"): {"data": [{"b64_json": _png_b64()}]},
        ("POST", "/openai/responses"): VISION,
        ("POST", "/xai/images/generations"): {"data": [{"b64_json": _png_b64((255, 0, 255))}]},
        ("POST", "/xai/responses"): VISION,
        ("POST", "/xai/videos/generations"): {"request_id": "req-1"},
        ("GET", "/xai/videos/req-1"): {"status": "done", "video": {"url": CLIP, "duration": 6.0}, "model": "m"},
        ("POST", "/anthropic/messages/batches"): {"id": "msgbatch_01", "processing_status": "in_progress"},
        ("GET", "/anthropic/messages/batches/msgbatch_01"): {"id": "msgbatch_01", "processing_status": "ended",
                                                             "results_url": RESULTS},
    }
    if (method, route.path) == ("POST", "/anthropic/messages"):
        return 200, {"Content-Type": "text/event-stream"}, SSE
    return 200, {"Content-Type": "application/json"}, json.dumps(answers[(method, route.path)]).encode()


class _Relay(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        asked = {name.lower(): value for name, value in self.headers.items()}
        self.server.seen.append((self.command, self.path, asked, self.rfile.read(length)))
        status, headers, data = _answer(self.command, self.path, asked)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _serve


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """No connection leaves this machine: a call that goes anywhere but the relay fails here, not at a provider."""
    connect = socket.create_connection

    def refuse(where, *args, **kwargs):
        if where[0] != "127.0.0.1":
            raise ConnectionRefusedError(f"the test reached for {where[0]}")
        return connect(where, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def relay(monkeypatch, tmp_path):
    """A relay on 127.0.0.1 standing in for all three providers, one path prefix each — its address is not set yet."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Relay)
    server.seen = []
    server.root = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in DEFAULTS:
        monkeypatch.delenv(DEFAULTS[name][0], raising=False)
        monkeypatch.setenv(f"{name.upper()}_API_KEY", KEY)
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "no-login"))
    monkeypatch.setattr(retry, "_sleep", lambda seconds: pytest.fail("nothing is refused here"))
    yield server
    server.shutdown()
    server.server_close()


def _point(monkeypatch, relay, *names: str) -> None:
    for name in names or DEFAULTS:
        monkeypatch.setenv(DEFAULTS[name][0], f"{relay.root}/{name}")


# Every direct provider call site, run as the engine runs it (`address` names each in its docstring).
def _openai_image(tmp_path):
    gen.generate_image("openai", "a blue square", tmp_path / "openai.png")


def _openai_vision(tmp_path):
    openai_provider.OpenAIProvider().inspect_image(_png(tmp_path), tmp_path, "which way?")


def _grok_image(tmp_path):
    gen.generate_image("grok", "a blue square", tmp_path / "grok.png")


def _grok_vision(tmp_path):
    facing_vision.grok_inspect(_png(tmp_path), credential=xai.Credential(KEY, xai.AUTH_SOURCE_API_KEY))


def _video(tmp_path, name="clip.mp4"):
    request = video.VideoRequest(image=_png(tmp_path), prompt="sway", out=tmp_path / name)
    result = video.generate_video(request, credential=xai.Credential(KEY, xai.AUTH_SOURCE_API_KEY), sleep=lambda s: None)
    assert result.out.read_bytes() == MP4


def _claude_message(tmp_path):
    assert anthropic_api.stream_message(KEY, {"model": anthropic_api.MODEL, "max_tokens": 8}).text == "ok"


def _claude_batch(tmp_path):
    batch = anthropic_api.submit_batch(KEY, [("c1", {"model": anthropic_api.MODEL, "max_tokens": 8})])
    ended = anthropic_api.get_batch(KEY, batch["id"])
    assert set(anthropic_api.batch_results(KEY, ended)) == {"c1"}


SITES = {
    "openai-image": (_openai_image, "openai", [("POST", "/openai/images/generations")]),
    "openai-vision": (_openai_vision, "openai", [("POST", "/openai/responses")]),
    "grok-image": (_grok_image, "xai", [("POST", "/xai/images/generations")]),
    "grok-vision": (_grok_vision, "xai", [("POST", "/xai/responses")]),
    "video": (_video, "xai", [("POST", "/xai/videos/generations"), ("GET", "/xai/videos/req-1"),
                              ("GET", "/xai/sprite-gen/fetch?url=" + urllib.parse.quote(CLIP, safe=""))]),
    "claude-message": (_claude_message, "anthropic", [("POST", "/anthropic/messages")]),
    "claude-batch": (_claude_batch, "anthropic", [
        ("POST", "/anthropic/messages/batches"), ("GET", "/anthropic/messages/batches/msgbatch_01"),
        ("GET", "/anthropic/sprite-gen/fetch?url=" + urllib.parse.quote(RESULTS, safe=""))]),
}


@pytest.mark.parametrize("site", SITES)
def test_every_call_goes_to_its_providers_variable(relay, monkeypatch, tmp_path, site) -> None:
    run, provider, asked = SITES[site]
    _point(monkeypatch, relay, provider)
    run(tmp_path)
    assert [(method, path) for method, path, _headers, _body in relay.seen] == asked


@pytest.mark.parametrize("site", SITES)
def test_a_variable_for_another_provider_moves_nothing(relay, monkeypatch, tmp_path, site) -> None:
    run, provider, _asked = SITES[site]
    _point(monkeypatch, relay, *(name for name in DEFAULTS if name != provider))
    sent: list[str] = []
    _capture(monkeypatch, sent)
    run(tmp_path)
    assert relay.seen == []
    assert sent and all(url.startswith(DEFAULTS[provider][1] + "/") or url in (CLIP, RESULTS) for url in sent), sent


def _capture(monkeypatch, sent: list[str]) -> None:
    """Every request any direct transport sends, answered as the relay would, without a connection."""
    def urlopen(request, *args, **kwargs):
        url = request if isinstance(request, str) else request.full_url
        sent.append(url)
        split = urllib.parse.urlsplit(url)
        if url in (CLIP, RESULTS):
            path = "/x/sprite-gen/fetch?url=" + urllib.parse.quote(url, safe="")
        else:
            provider = next(name for name, (_env, base) in DEFAULTS.items() if url.startswith(base + "/"))
            path = f"/{provider}" + split.path[len(urllib.parse.urlsplit(DEFAULTS[provider][1]).path):] + (
                f"?{split.query}" if split.query else "")
        status, headers, data = _answer(request.get_method() if not isinstance(request, str) else "GET", path)
        answer = Answer(data, status=status, headers=headers)
        return _Lines(answer) if headers.get("Content-Type") == "text/event-stream" else answer

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


class _Lines(Answer):
    """An SSE answer read line by line, as `anthropic_api.stream_message` reads one."""

    def __init__(self, answer: Answer) -> None:
        super().__init__(answer._body.getvalue(), status=answer.status, headers=answer.headers)

    def __iter__(self):
        return iter(self._body.getvalue().splitlines(keepends=True))


@pytest.mark.parametrize("unset", ["absent", "empty", "blank"])
@pytest.mark.parametrize("site", SITES)
def test_without_a_value_every_call_goes_where_it_went(relay, monkeypatch, tmp_path, site, unset) -> None:
    run, provider, asked = SITES[site]
    if unset != "absent":
        monkeypatch.setenv(DEFAULTS[provider][0], "" if unset == "empty" else "  ")
    sent: list[str] = []
    _capture(monkeypatch, sent)
    run(tmp_path)
    base = DEFAULTS[provider][1]
    expected = []
    for _method, path in asked:
        if "/sprite-gen/fetch?url=" in path:
            expected.append(urllib.parse.unquote(path.split("?url=", 1)[1]))
        else:
            expected.append(base + path[len(f"/{provider}"):])
    assert sent == expected
    assert relay.seen == []


def test_a_value_set_after_import_is_followed_and_unsetting_it_goes_back(relay, monkeypatch, tmp_path) -> None:
    """Nothing keeps an address from import: the same process follows the variable as it changes."""
    sent: list[str] = []
    _capture(monkeypatch, sent)
    _video(tmp_path, "first.mp4")
    assert sent == ["https://api.x.ai/v1/videos/generations", "https://api.x.ai/v1/videos/req-1", CLIP]
    monkeypatch.setattr(urllib.request, "urlopen", URLOPEN)
    _point(monkeypatch, relay, "xai")
    _video(tmp_path, "second.mp4")
    assert [path for _m, path, _h, _b in relay.seen] == [
        "/xai/videos/generations", "/xai/videos/req-1", "/xai/sprite-gen/fetch?url=" + urllib.parse.quote(CLIP, safe="")]
    monkeypatch.delenv("SPRITE_GEN_XAI_BASE_URL")
    later: list[str] = []
    _capture(monkeypatch, later)
    _video(tmp_path, "third.mp4")
    assert later == sent


def test_the_relay_gets_the_bytes_the_provider_would_have(relay, monkeypatch, tmp_path) -> None:
    """Only the address moves: the method, the path under the base, the body and the headers are what went before."""
    direct: list[urllib.request.Request] = []

    def keep(request, *args, **kwargs):
        direct.append(request)
        return Answer({"data": [{"b64_json": _png_b64()}]})

    monkeypatch.setattr(urllib.request, "urlopen", keep)
    gen.generate_image("openai", "a blue square", tmp_path / "direct.png")
    monkeypatch.setattr(urllib.request, "urlopen", URLOPEN)
    _point(monkeypatch, relay, "openai")
    gen.generate_image("openai", "a blue square", tmp_path / "relayed.png")
    (method, path, headers, body), = relay.seen
    before = direct[0]
    assert before.full_url == "https://api.openai.com/v1/images/generations"
    assert (method, path, body) == (before.get_method(), "/openai/images/generations", before.data)
    assert {name.lower(): value for name, value in before.header_items()}.items() <= headers.items()


def test_a_clip_is_downloaded_through_the_relay_with_the_bearer_token_only_when_refused(relay, monkeypatch) -> None:
    _point(monkeypatch, relay, "xai")
    assert video.http_download(CLIP, KEY) == MP4
    assert video.http_download(PRIVATE_CLIP, KEY) == MP4
    fetch = "/xai/sprite-gen/fetch?url="
    assert [(method, path, headers.get("authorization")) for method, path, headers, _body in relay.seen] == [
        ("GET", fetch + urllib.parse.quote(CLIP, safe=""), None),
        ("GET", fetch + urllib.parse.quote(PRIVATE_CLIP, safe=""), None),
        ("GET", fetch + urllib.parse.quote(PRIVATE_CLIP, safe=""), f"Bearer {KEY}"),
    ]


@pytest.mark.parametrize("value", ["127.0.0.1:8080/v1", "ftp://relay/v1", "http:///v1", "http://relay/v1?x=1",
                                   "http://relay/v1#frag"])
def test_a_value_that_is_not_an_http_address_is_refused_naming_the_variable(monkeypatch, value) -> None:
    from sprite_gen.gen import address
    monkeypatch.setenv("SPRITE_GEN_OPENAI_BASE_URL", value)
    with pytest.raises(SystemExit, match=r"SPRITE_GEN_OPENAI_BASE_URL="):
        address.url("openai", "/responses")


def test_a_trailing_slash_is_the_same_base() -> None:
    from sprite_gen.gen import address
    env = {"SPRITE_GEN_XAI_BASE_URL": "http://127.0.0.1:9/xai/"}
    assert address.url("xai", "/videos/req-1", env=env) == "http://127.0.0.1:9/xai/videos/req-1"


def test_the_registry_is_the_four_providers_and_their_documented_addresses() -> None:
    from sprite_gen.gen import address
    assert {name: (p.env, p.default) for name, p in address.PROVIDERS.items()} == DEFAULTS


def _modules() -> list[Path]:
    return sorted((ROOT / "sprite_gen").rglob("*.py"))


def test_no_module_but_the_registry_names_a_provider_address() -> None:
    """A copy of an address anywhere else would be a call the variable does not move."""
    hosts = [urllib.parse.urlsplit(base).netloc for _env, base in DEFAULTS.values()]
    named = [(path.relative_to(ROOT).as_posix(), host) for path in _modules() if path.name != "address.py"
             for host in hosts if host in path.read_text(encoding="utf-8")]
    assert named == []


def test_no_module_reads_an_address_at_import() -> None:
    """`address` is called inside functions only: a module-level call would keep the address the import saw."""
    at_import = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(statement):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "address"):
                    at_import.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert at_import == []
