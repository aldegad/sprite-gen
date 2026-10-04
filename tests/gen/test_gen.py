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
    # Codex remains the subprocess provider; Grok now calls the API directly.

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
    monkeypatch.setattr(
        codex_provider, "_collect_inline_results", lambda rollout: [codex_provider.InlineResult(b64)]
    )
    monkeypatch.setenv("ORCHESTRATOR_RUNTIME_ENDPOINT_ID", "synthetic-endpoint")
    codex_provider.CodexProvider(keep_session=True).generate(
        GenRequest(prompt="a mushroom", raw=tmp_path / "raw.png"), tmp_path
    )
    assert seen["codex"] is not None
    assert "ORCHESTRATOR_RUNTIME_ENDPOINT_ID" not in seen["codex"]
    assert seen["codex_encoding"] == "utf-8"


def test_provider_commands_use_the_single_resolved_binary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(codex_provider, "provider_binary", lambda _name: "C:/bin/codex.CMD")

    class _CodexCompleted:
        returncode = 0
        stdout = '{"type":"thread.started","thread_id":"aaaa-bbbb"}\n'
        stderr = ""

    def fake_codex(cmd, **_kwargs):
        seen["codex"] = cmd
        return _CodexCompleted()

    monkeypatch.setattr(codex_provider.subprocess, "run", fake_codex)
    monkeypatch.setattr(codex_provider, "_resolve_rollout", lambda *_args, **_kwargs: tmp_path / "rollout.jsonl")
    monkeypatch.setattr(
        codex_provider,
        "_collect_inline_results",
        lambda _path: [codex_provider.InlineResult(base64.b64encode(_png_bytes()).decode())],
    )
    codex_provider.CodexProvider(keep_session=True).generate(
        GenRequest(prompt="도구", raw=tmp_path / "codex.png"), tmp_path
    )

    assert seen["codex"][0] == "C:/bin/codex.CMD"


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
    # Pre-0.149 records carry no transparentBackground field — reported as unknown.
    assert [r.transparent_background for r in results] == [None, None]

    dest = tmp_path / "decoded.png"
    codex_provider._decode_png(results[-1].result, dest)
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
                    "transparentBackground": True,
                },
            }
        },
    ]
    rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    results = codex_provider._collect_inline_results(rollout)

    # codex 0.153 reports its own transparency claim on the completed item; it is
    # carried through (the orchestrator still measures the decoded PNG itself).
    assert results == [codex_provider.InlineResult(b64, transparent_background=True)]


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
    monkeypatch.setattr(
        codex_provider, "_collect_inline_results", lambda path: [codex_provider.InlineResult(b64)]
    )

    codex_provider.CodexProvider(keep_session=True).generate(
        GenRequest(prompt="a mushroom", raw=tmp_path / "raw.png"), tmp_path
    )

    assert seen["input"].startswith("$imagegen ")
    assert "a mushroom" in seen["input"]


def test_chroma_key_transparent_clears_magenta(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    img = Image.new("RGBA", (6, 6), (255, 0, 255, 255))
    for y in range(2, 4):
        for x in range(2, 4):
            img.putpixel((x, y), (245, 245, 245, 255))
    img.save(src)

    out = tmp_path / "out.png"
    stats = chroma_mod.key_transparent(src, out, key="magenta", white_check=tmp_path / "check.png")
    assert stats["mode"] == "RGBA"
    assert stats["stale_transparent_rgb_pixels"] == 0
    assert stats["keyed_pixels"] == 32

    keyed = Image.open(out).convert("RGBA")
    assert keyed.getpixel((0, 0))[3] == 0
    assert keyed.getpixel((2, 2))[3] == 255


@pytest.mark.parametrize(
    ("key", "background", "subject"),
    [
        (
            "magenta",
            lambda x, y: (238 - (x % 7), 124 + ((x + y) % 31), 229 - (y % 5), 255),
            (20, 220, 60, 255),
        ),
        (
            "green",
            lambda x, y: (124 + ((x + y) % 31), 238 - (x % 7), 129 + (y % 5), 255),
            (220, 20, 200, 255),
        ),
    ],
)
def test_chroma_key_transparent_removes_textured_gradient_background(
    tmp_path: Path,
    key: str,
    background,
    subject: tuple[int, int, int, int],
) -> None:
    src = tmp_path / f"{key}-gradient.png"
    image = Image.new("RGBA", (96, 64))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = background(x, y)
    for y in range(20, 44):
        for x in range(32, 64):
            pixels[x, y] = subject
    image.save(src)

    out = tmp_path / f"{key}-out.png"
    stats = chroma_mod.key_transparent(src, out, key=key)

    keyed = Image.open(out).convert("RGBA")
    assert stats["alpha_zero_pct"] > 50.0
    assert keyed.getpixel((0, 0))[3] == 0
    assert keyed.getpixel((48, 32))[3] == 255


def test_chroma_key_transparent_rejects_zero_percent_alpha(
    tmp_path: Path,
    monkeypatch,
) -> None:
    src = tmp_path / "src.png"
    Image.new("RGBA", (512, 512), (20, 30, 40, 255)).save(src)

    def ineffective_matte(image, chroma_key, warnings):
        result = image.convert("RGBA")
        result.putpixel((0, 0), (0, 0, 0, 0))
        return result

    monkeypatch.setattr(chroma_mod, "remove_chroma_background_ycbcr", ineffective_matte)

    out = tmp_path / "out.png"
    with pytest.raises(SystemExit, match=r"0\.0% transparent pixels"):
        chroma_mod.key_transparent(src, out, key="magenta")
    assert not out.exists()


class _FakeProvider:
    name = "fake"
    transparency = gen_base.TRANSPARENCY_CHROMA

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[GenRequest] = []

    def generate(self, request: GenRequest, workdir: Path):
        self.calls += 1
        self.requests.append(request)
        Image.new("RGBA", (8, 8), (255, 0, 255, 255)).save(request.raw)
        return gen_base.ProviderRun(provider=self.name, elapsed_seconds=1.23, model=request.model)


def _native_rgba_image() -> Image.Image:
    """A model-style RGBA: transparent margin, opaque core, one stale-RGB transparent px."""
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    for y in range(2, 6):
        for x in range(2, 6):
            image.putpixel((x, y), (200, 90, 20, 253))
    image.putpixel((0, 0), (255, 0, 255, 0))  # stale RGB under alpha 0
    return image


class _FakeNativeProvider(_FakeProvider):
    """A provider that returns its own alpha (the codex image_gen shape)."""

    name = "fake-native"
    transparency = gen_base.TRANSPARENCY_NATIVE

    def __init__(self, image: Image.Image | None = None) -> None:
        super().__init__()
        self.image = image if image is not None else _native_rgba_image()

    def generate(self, request: GenRequest, workdir: Path):
        self.calls += 1
        self.requests.append(request)
        self.image.save(request.raw)
        return gen_base.ProviderRun(provider=self.name, elapsed_seconds=2.5, model=request.model)


def _gen_kwargs(out: Path, report: Path, **overrides):
    kwargs = dict(
        provider="fake",
        prompt="a mushroom",
        out=out,
        ref=[],
        model=None,
        aspect_ratio=None,
        transparent=True,
        alpha_mode="auto",
        chroma_key="magenta",
        white_check=None,
        keep_session=False,
        report=report,
        prompt_file=None,
        workdir=None,
    )
    kwargs.update(overrides)
    return kwargs


def test_native_strategy_publishes_measured_alpha_and_asks_provider_for_it(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _FakeNativeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"

    rc = gen.run(**_gen_kwargs(out, report))

    assert rc == 0
    # The strategy is decided before the model runs and rides on the request.
    assert fake.requests[0].native_alpha is True
    published = Image.open(out)
    assert published.mode == "RGBA"
    assert published.getpixel((0, 0)) == (0, 0, 0, 0)  # stale RGB scrubbed
    assert published.getpixel((3, 3)) == (200, 90, 20, 253)  # partial alpha kept as produced
    assert (tmp_path / "asset.png.raw.png").is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["transparent"] is True
    assert payload["alpha"]["strategy"] == "native"
    assert payload["alpha"]["strategy_source"] == "provider-default"
    assert payload["alpha"]["method"] == "native"
    assert payload["alpha"]["alpha_zero_pct"] == 75.0
    assert payload["alpha"]["partial_alpha_pct"] == 25.0
    assert payload["alpha"]["opaque_pct"] == 0.0
    assert payload["alpha"]["cleaned_transparent_rgb_pixels"] == 1
    assert payload["alpha"]["stale_transparent_rgb_pixels"] == 0
    assert payload["chroma"] is None  # no chroma key ran


def test_chroma_strategy_reports_itself_under_alpha_too(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"

    assert gen.run(**_gen_kwargs(out, report)) == 0

    assert fake.requests[0].native_alpha is False
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "chroma"
    assert payload["alpha"]["method"] == "ycbcr"
    assert payload["chroma"]["method"] == "ycbcr"


@pytest.mark.parametrize(
    ("image", "expected_message"),
    [
        (Image.new("RGB", (8, 8), (255, 255, 255)), "no alpha channel"),
        (Image.new("RGBA", (8, 8), (10, 20, 30, 255)), r"0\.0% transparent pixels"),
    ],
)
def test_native_strategy_refuses_drawn_or_opaque_backgrounds(
    tmp_path: Path, monkeypatch, image: Image.Image, expected_message: str
) -> None:
    # A drawn checkerboard is an RGB image; a fully opaque RGBA has no transparency.
    # Neither can be rescued by chroma keying, so the run fails before publishing.
    fake = _FakeNativeProvider(image)
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"

    with pytest.raises(SystemExit, match=expected_message):
        gen.run(**_gen_kwargs(out, report))

    assert not out.exists()
    assert not report.exists()


def test_alpha_mode_native_is_refused_on_a_chroma_provider(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    with pytest.raises(SystemExit, match="--alpha-mode native is not a capability"):
        gen.run(**_gen_kwargs(tmp_path / "a.png", tmp_path / "r.json", alpha_mode="native"))
    assert fake.calls == 0  # refused before any model call


def test_alpha_mode_chroma_forces_keying_on_a_native_provider(tmp_path: Path, monkeypatch) -> None:
    # The prompt already carries a magenta key: the native provider is told NOT to
    # return alpha and the raw is keyed out like any chroma run.
    fake = _FakeNativeProvider(Image.new("RGBA", (8, 8), (255, 0, 255, 255)))
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"

    assert gen.run(**_gen_kwargs(out, report, alpha_mode="chroma")) == 0

    assert fake.requests[0].native_alpha is False
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "chroma"
    assert payload["chroma"]["key"] == "magenta"


def test_auto_steps_down_to_chroma_when_refs_are_attached(tmp_path: Path, monkeypatch, capsys) -> None:
    # 2026-09-08 실측: codex native alpha with --ref drew checkerboards 5/6, chroma 6/6.
    # `auto` therefore keys a ref run instead of gambling on native — decided before
    # the model call, printed, and recorded in the report.
    fake = _FakeNativeProvider(Image.new("RGBA", (8, 8), (255, 0, 255, 255)))
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"

    assert gen.run(**_gen_kwargs(out, report, ref=[ref])) == 0

    assert fake.requests[0].native_alpha is False
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "chroma"
    assert payload["alpha"]["strategy_source"] == "refs-attached"
    assert "reference image(s) attached" in capsys.readouterr().err


def test_explicit_native_still_runs_with_refs(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeNativeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"

    assert gen.run(**_gen_kwargs(out, report, ref=[ref], alpha_mode="native")) == 0

    assert fake.requests[0].native_alpha is True
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "native"
    assert payload["alpha"]["strategy_source"] == "explicit"


# Three raws a ref run can come back with (2026-10-04): a real cut-out (a transparent
# --ref is answered with alpha), a drawn checkerboard and a painted key background.
# The figure is a dark outline around an orange body with a cream belly — the two
# colours a second key took off the real cut-out.
_OUTLINE, _BODY, _CREAM = (20, 20, 20), (240, 140, 40), (245, 230, 200)
_OUTLINE_PX, _CREAM_PX = (17, 32), (32, 37)


def _outlined_figure(background: Image.Image) -> Image.Image:
    from PIL import ImageDraw

    alpha = (255,) if background.mode == "RGBA" else ()
    draw = ImageDraw.Draw(background)
    draw.ellipse((16, 12, 48, 52), fill=_OUTLINE + alpha)
    draw.ellipse((19, 15, 45, 49), fill=_BODY + alpha)
    draw.ellipse((26, 30, 38, 44), fill=_CREAM + alpha)
    return background


def _real_alpha_raw() -> Image.Image:
    return _outlined_figure(Image.new("RGBA", (64, 64), (0, 0, 0, 0)))


def _checkerboard_raw() -> Image.Image:
    board = Image.new("RGB", (64, 64))
    for y in range(64):
        for x in range(64):
            board.putpixel((x, y), (204, 204, 204) if (x // 8 + y // 8) % 2 else (255, 255, 255))
    return _outlined_figure(board)


def _key_background_raw() -> Image.Image:
    return _outlined_figure(Image.new("RGB", (64, 64), (255, 0, 255)))


def _ref_run(tmp_path: Path, monkeypatch, raw: Image.Image, **overrides):
    fake = _FakeNativeProvider(raw)
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes(color=(0, 0, 0, 0)))
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"
    rc = gen.run(**_gen_kwargs(out, report, ref=[ref], **overrides))
    return fake, rc, out, report


def test_ref_run_publishes_a_real_alpha_raw_without_keying_its_outline(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    fake, rc, out, report = _ref_run(tmp_path, monkeypatch, _real_alpha_raw())

    assert rc == 0
    assert fake.requests[0].native_alpha is False  # still planned as a key: no transparency request
    published = Image.open(out)
    assert published.getpixel(_OUTLINE_PX) == _OUTLINE + (255,)
    assert published.getpixel(_CREAM_PX) == _CREAM + (255,)
    assert published.getchannel("A").tobytes() == _real_alpha_raw().getchannel("A").tobytes()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "native"
    assert payload["alpha"]["strategy_source"] == "refs-attached-raw-alpha"
    assert payload["alpha"]["method"] == "native"
    assert payload["alpha"]["raw_alpha"]["verdict"] == "real-alpha"
    assert payload["alpha"]["raw_alpha"]["border_alpha_zero_pct"] == 100.0
    assert payload["chroma"] is None
    assert "publishing its own alpha" in capsys.readouterr().err


@pytest.mark.parametrize("raw", [_checkerboard_raw, _key_background_raw], ids=["checkerboard", "key-background"])
def test_ref_run_still_keys_a_raw_with_no_alpha(tmp_path: Path, monkeypatch, raw) -> None:
    fake, rc, out, report = _ref_run(tmp_path, monkeypatch, raw())

    assert rc == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "chroma"
    assert payload["alpha"]["strategy_source"] == "refs-attached"
    assert payload["alpha"]["method"] == "ycbcr"
    assert payload["alpha"]["raw_alpha"] == {
        "mode": "RGB", "has_alpha_band": False, "verdict": "no-alpha",
        "alpha_zero_pct": 0.0, "border_alpha_zero_pct": 0.0,
    }
    assert payload["chroma"]["key"] == "magenta"
    assert Image.open(out).getpixel((0, 0)) == (0, 0, 0, 0)  # the background was keyed out


def test_key_background_ref_run_keeps_its_outline(tmp_path: Path, monkeypatch) -> None:
    _, _, out, _ = _ref_run(tmp_path, monkeypatch, _key_background_raw())
    published = Image.open(out)
    assert published.getpixel(_OUTLINE_PX) == _OUTLINE + (255,)
    assert published.getpixel(_CREAM_PX) == _CREAM + (255,)


def test_ref_run_keys_an_ambiguous_raw_and_warns(tmp_path: Path, monkeypatch, capsys) -> None:
    # A key background with a few transparent pixels in one corner: alpha 0 that is
    # not a transparent background. Keyed as planned, and the report says why.
    raw = _outlined_figure(Image.new("RGBA", (64, 64), (255, 0, 255, 255)))
    for y in range(3):
        for x in range(3):
            raw.putpixel((x, y), (0, 0, 0, 0))
    _, rc, _, report = _ref_run(tmp_path, monkeypatch, raw)

    assert rc == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "chroma"
    assert payload["alpha"]["strategy_source"] == "refs-attached"
    assert payload["alpha"]["raw_alpha"]["verdict"] == "ambiguous"
    assert "keyed as planned" in payload["alpha"]["raw_alpha"]["warning"]
    assert "warning: the raw has some transparent pixels" in capsys.readouterr().err


@pytest.mark.parametrize("decontam", ["palette", "auto"])
def test_ref_run_with_decontam_skips_it_on_a_real_alpha_raw(tmp_path: Path, monkeypatch, capsys, decontam) -> None:
    # The generation is paid for and its alpha is good: there is no key to decontaminate,
    # so the pass is skipped, said on stderr and recorded — not refused after the call.
    fake = _FakeNativeProvider(_real_alpha_raw())
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    out = tmp_path / "asset.png"
    result = gen.generate_image("fake", "a mushroom", out, refs=[ref], transparent=True, decontam=decontam)

    assert result.alpha["strategy_source"] == "refs-attached-raw-alpha"
    assert result.alpha["decontam"]["requested"] == decontam
    assert "has no key to remove" in result.alpha["decontam"]["skipped"]
    assert Image.open(out).getpixel(_OUTLINE_PX) == _OUTLINE + (255,)
    err = capsys.readouterr().err
    assert f"--decontam {decontam} skipped" in err
    assert ("warning: --decontam" in err) is (decontam == "palette")


def test_ref_run_with_decontam_palette_still_decontaminates_a_keyed_raw(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeNativeProvider(_key_background_raw())
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    result = gen.generate_image("fake", "a mushroom", tmp_path / "asset.png", refs=[ref], transparent=True,
                                decontam="palette")
    assert result.alpha["strategy_source"] == "refs-attached"
    assert "skipped" not in result.alpha["decontam"]


# `auto`'s step down plans a key, so the prompt must ask for one: a ref prompt with no key
# background came back on white (2026-10-04), and keying white takes the outline's light
# neighbours and the cream with it. The engine adds the line unless the prompt names a key.
class _PromptFollowingProvider(_FakeNativeProvider):
    """Draws the figure on the key the prompt asks for, or on white when it asks for none."""

    def generate(self, request: GenRequest, workdir: Path):
        key = chroma_mod.named_key_background(request.prompt)
        ground = chroma_mod.KEYS[key]["target"] if key else (255, 255, 255)
        self.image = _outlined_figure(Image.new("RGB", (64, 64), ground))
        return super().generate(request, workdir)


def test_ref_run_adds_the_key_background_line_to_a_prompt_without_one(tmp_path: Path, monkeypatch, capsys) -> None:
    fake = _PromptFollowingProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"

    assert gen.run(**_gen_kwargs(out, report, ref=[ref])) == 0

    prompt = fake.requests[0].prompt
    assert prompt.startswith("a mushroom")
    assert prompt.endswith(chroma_mod.KEY_BACKGROUND_TEXT["magenta"])
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["key_background"] == {"injected": True, "key": "magenta"}
    assert payload["prompt"] == prompt
    published = Image.open(out)
    assert published.getpixel((0, 0)) == (0, 0, 0, 0)
    assert published.getpixel(_OUTLINE_PX) == _OUTLINE + (255,)
    assert published.getpixel(_CREAM_PX) == _CREAM + (255,)
    assert "added a magenta key background line" in capsys.readouterr().err


def test_ref_run_adds_the_line_in_the_chosen_key(tmp_path: Path, monkeypatch) -> None:
    fake = _PromptFollowingProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    result = gen.generate_image("fake", "a magenta mushroom", tmp_path / "asset.png", refs=[ref],
                                transparent=True, chroma_key="green")
    assert fake.requests[0].prompt.endswith(chroma_mod.KEY_BACKGROUND_TEXT["green"])
    assert result.alpha["key_background"] == {"injected": True, "key": "green"}
    assert result.chroma["key"] == "green"


@pytest.mark.parametrize(
    ("prompt", "named"),
    [
        ("a mushroom. " + chroma_mod.KEY_BACKGROUND_TEXT["magenta"], "magenta"),
        ("a mushroom on a flat #ff00ff background", "magenta"),
        ("a mushroom on a pure magenta chroma-key background", "magenta"),
        ("a mushroom in front of a green screen", "green"),
    ],
    ids=["engine-line", "hex", "name", "other-key"],
)
def test_ref_run_keeps_a_key_background_the_prompt_already_names(
    tmp_path: Path, monkeypatch, prompt: str, named: str
) -> None:
    fake = _PromptFollowingProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    result = gen.generate_image("fake", prompt, tmp_path / "asset.png", refs=[ref], transparent=True)
    assert fake.requests[0].prompt == prompt
    assert result.alpha["key_background"] == {"injected": False, "key": named}


@pytest.mark.parametrize(
    "overrides",
    [{"alpha_mode": "chroma"}, {"alpha_mode": "native"}, {"ref": []}, {"transparent": False}],
    ids=["explicit-chroma", "explicit-native", "no-ref", "opaque"],
)
def test_only_autos_ref_step_down_adds_the_key_line(tmp_path: Path, monkeypatch, overrides) -> None:
    fake = _FakeNativeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"
    gen.run(**_gen_kwargs(out, report, **{"ref": [ref], **overrides}))
    assert fake.requests[0].prompt == "a mushroom"
    alpha = json.loads(report.read_text(encoding="utf-8")).get("alpha") or {}
    assert "key_background" not in alpha


@pytest.mark.parametrize(
    ("prompt", "named"),
    [
        ("a green frog on a white background", None),
        ("a magenta mushroom, red cap", None),
        ("draw it on #00FF00", "green"),
        ("FF00FF", "magenta"),
        ("a colour FF00FFAA nearby", None),
        ("a green backdrop; #FF00FF elsewhere", "green"),
    ],
)
def test_named_key_background(prompt: str, named: str | None) -> None:
    assert chroma_mod.named_key_background(prompt) == named


def test_explicit_chroma_keys_even_a_real_alpha_raw(tmp_path: Path, monkeypatch) -> None:
    # --alpha-mode chroma is the caller's own key: the raw check belongs to auto's step down only.
    _, rc, _, report = _ref_run(tmp_path, monkeypatch, _real_alpha_raw(), alpha_mode="chroma")
    assert rc == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "chroma"
    assert payload["alpha"]["strategy_source"] == "explicit"
    assert "raw_alpha" not in payload["alpha"]


@pytest.mark.parametrize(
    ("image", "verdict"),
    [
        (Image.new("RGB", (16, 16), (255, 0, 255)), "no-alpha"),
        (Image.new("RGBA", (16, 16), (255, 0, 255, 255)), "no-alpha"),
        (Image.new("LA", (16, 16), (0, 0)), "real-alpha"),
    ],
    ids=["rgb", "opaque-rgba", "transparent-la"],
)
def test_classify_raw_alpha_reads_the_alpha_band(tmp_path: Path, image: Image.Image, verdict: str) -> None:
    path = tmp_path / "raw.png"
    image.save(path)
    assert chroma_mod.classify_raw_alpha(path)["verdict"] == verdict


def test_classify_raw_alpha_needs_a_transparent_border(tmp_path: Path) -> None:
    # Plenty of alpha 0, all of it inside an opaque frame: not a transparent background.
    image = Image.new("RGBA", (20, 20), (255, 0, 255, 255))
    for y in range(4, 16):
        for x in range(4, 16):
            image.putpixel((x, y), (0, 0, 0, 0))
    path = tmp_path / "raw.png"
    image.save(path)
    stats = chroma_mod.classify_raw_alpha(path)
    assert stats["alpha_zero_pct"] == 36.0
    assert stats["border_alpha_zero_pct"] == 0.0
    assert stats["verdict"] == "ambiguous"


def test_classify_raw_alpha_judges_before_rounding(tmp_path: Path) -> None:
    # One alpha-0 pixel in 200 x 200 is 0.0025 %, reported as 0.0 but not "no alpha".
    image = Image.new("RGBA", (200, 200), (255, 0, 255, 255))
    image.putpixel((0, 0), (0, 0, 0, 0))
    path = tmp_path / "raw.png"
    image.save(path)
    stats = chroma_mod.classify_raw_alpha(path)
    assert stats["alpha_zero_pct"] == 0.0
    assert stats["verdict"] == "ambiguous"


def test_provider_without_a_declared_strategy_fails_loud(tmp_path: Path, monkeypatch) -> None:
    class _Undeclared(_FakeProvider):
        name = "undeclared"
        transparency = None

    fake = _Undeclared()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    with pytest.raises(SystemExit, match="declares no transparency strategy"):
        gen.run(**_gen_kwargs(tmp_path / "a.png", tmp_path / "r.json"))
    assert fake.calls == 0


def test_non_transparent_run_ignores_alpha_mode(tmp_path: Path, monkeypatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"
    assert gen.run(**_gen_kwargs(out, report, transparent=False, alpha_mode="native")) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"] is None and payload["chroma"] is None
    assert fake.requests[0].native_alpha is False


def test_real_providers_declare_their_transparency_strategy() -> None:
    from sprite_gen.gen import grok_provider

    # SSoT: each adapter declares once what it can do (2026-09-08 실측 — codex
    # image_gen returns real RGBA; grok Imagine 2.0 returns JPEG from API and CLI).
    assert codex_provider.CodexProvider.transparency == gen_base.TRANSPARENCY_NATIVE
    assert grok_provider.GrokProvider.transparency == gen_base.TRANSPARENCY_CHROMA


@pytest.mark.parametrize("options, flag", [({"quality": "max"}, "--quality"),
                                           ({"quality": "auto"}, "--quality"),
                                           ({"resolution": "2k"}, "--resolution")])
def test_codex_refuses_a_level_image_gen_cannot_carry(tmp_path: Path, monkeypatch, options, flag) -> None:
    """image_gen has no effort or size dial, so the level is refused, never dropped:
    a caller who paid attention to --quality must not get a default-effort image."""
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("codex must not be spawned"))
    with pytest.raises(SystemExit, match=flag) as error:
        codex_provider.CodexProvider().generate(GenRequest("x", tmp_path / "raw.png", **options), tmp_path)
    assert "openai" in str(error.value)
    assert not (tmp_path / "raw.png").exists()


def test_grok_refuses_a_native_alpha_request_before_upload(tmp_path: Path, monkeypatch) -> None:
    from sprite_gen.gen import grok_provider

    spawned: list = []
    monkeypatch.setattr(grok_provider.xai, "http_json", lambda *a, **k: spawned.append(a))
    with pytest.raises(SystemExit, match="cannot return an alpha channel"):
        grok_provider.GrokProvider().generate(
            GenRequest(prompt="x", raw=tmp_path / "raw.png", native_alpha=True), tmp_path
        )
    assert spawned == []


def test_codex_prompt_carries_native_alpha_request_only_when_asked() -> None:
    user_prompt = "a fox sprite"
    plain = codex_provider._build_prompt(user_prompt)
    native = codex_provider._build_prompt(user_prompt, native_alpha=True)

    assert codex_provider._NATIVE_ALPHA_INSTRUCTION not in plain
    assert codex_provider._NATIVE_ALPHA_INSTRUCTION in native
    # The bundled imagegen skill keys on this phrase to request real alpha.
    assert "transparent background" in native
    # The transport contract and the verbatim user prompt survive on both.
    for prompt in (plain, native):
        assert prompt.startswith("$imagegen ")
        assert user_prompt in prompt


def test_codex_generate_reports_transparent_background_claim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    prompts: list[str] = []

    class _Completed:
        returncode = 0
        stdout = '{"type":"thread.started","thread_id":"aaaa-bbbb"}\n'
        stderr = ""

    def _fake_run(cmd, **kwargs):
        prompts.append(kwargs.get("input"))
        return _Completed()

    monkeypatch.setattr(codex_provider.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        codex_provider, "_resolve_rollout", lambda sid, root, *, preexisting: tmp_path / "x.jsonl"
    )
    b64 = base64.b64encode(_png_bytes()).decode()
    monkeypatch.setattr(
        codex_provider,
        "_collect_inline_results",
        lambda rollout: [codex_provider.InlineResult(b64, transparent_background=True)],
    )
    run = codex_provider.CodexProvider(keep_session=True).generate(
        GenRequest(prompt="a fox", raw=tmp_path / "raw.png", native_alpha=True), tmp_path
    )
    assert run.extra["transparent_background_reported"] is True
    assert codex_provider._NATIVE_ALPHA_INSTRUCTION in prompts[0]


def test_verify_native_alpha_writes_white_check(tmp_path: Path) -> None:
    raw = tmp_path / "raw.png"
    _native_rgba_image().save(raw)
    out = tmp_path / "out.png"
    check = tmp_path / "check.png"
    stats = chroma_mod.verify_native_alpha(raw, out, white_check=check)
    assert stats["white_check"] == str(check)
    assert Image.open(check).mode == "RGB"
    assert Image.open(check).getpixel((0, 0)) == (255, 255, 255)


def test_cli_alpha_mode_defaults_to_auto_and_rejects_unknown() -> None:
    parser = gen._build_parser()
    args = parser.parse_args(["--out", "x.png", "--prompt", "p"])
    assert args.alpha_mode == "auto"
    assert parser.parse_args(["--out", "x.png", "--alpha-mode", "chroma"]).alpha_mode == "chroma"
    with pytest.raises(SystemExit):
        parser.parse_args(["--out", "x.png", "--alpha-mode", "magic"])


def test_generate_image_zero_percent_alpha_fails_without_success_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    monkeypatch.setattr(
        chroma_mod,
        "remove_chroma_background_ycbcr",
        lambda image, chroma_key, warnings: image.convert("RGBA"),
    )

    out = tmp_path / "asset.png"
    report = tmp_path / "report.json"
    with pytest.raises(SystemExit, match=r"0\.0% transparent pixels"):
        gen.run(
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

    assert not out.exists()
    assert not report.exists()


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
    assert payload["chroma"]["method"] == "ycbcr"
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
