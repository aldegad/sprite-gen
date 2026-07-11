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

    dest = tmp_path / "decoded.png"
    codex_provider._decode_png(results[-1], dest)
    assert gen_base.verify_png(dest) > 0


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
        '{"type":"thread.started","thread_id":"019f50c7-d177-7d73-a30b-b4bc666ac9e7"}\n'
        '{"type":"turn.started"}\n'
    )
    assert codex_provider._parse_session_id(json_stdout) == "019f50c7-d177-7d73-a30b-b4bc666ac9e7"

    legacy_stdout = "some header\nsession id: abc123de-0000-1111-2222-333344445555\ndone\n"
    assert codex_provider._parse_session_id(legacy_stdout) == "abc123de-0000-1111-2222-333344445555"

    assert codex_provider._parse_session_id("no id here") is None


def test_grok_prompt_switches_on_refs(tmp_path: Path) -> None:
    from sprite_gen.gen import grok_provider

    raw = tmp_path / "raw.png"
    no_ref = grok_provider._build_prompt(GenRequest(prompt="a red apple", raw=raw, aspect_ratio="1:1"))
    assert "image_gen" in no_ref and "1:1" in no_ref and str(raw) in no_ref

    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    with_ref = grok_provider._build_prompt(GenRequest(prompt="same char waving", raw=raw, refs=[ref]))
    assert "image_edit" in with_ref and str(ref.resolve()) in with_ref


def test_chroma_key_transparent_clears_magenta(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    img = Image.new("RGBA", (6, 6), (255, 0, 255, 255))
    img.putpixel((2, 2), (10, 20, 30, 255))  # a real subject pixel
    img.save(src)

    out = tmp_path / "out.png"
    stats = chroma_mod.key_transparent(src, out, key="magenta", white_check=tmp_path / "check.png")
    assert stats["mode"] == "RGBA"
    assert stats["stale_transparent_rgb_pixels"] == 0
    assert stats["keyed_pixels"] == 35  # 36 - 1 subject pixel

    keyed = Image.open(out).convert("RGBA")
    assert keyed.getpixel((0, 0))[3] == 0
    assert keyed.getpixel((2, 2))[3] == 255


class _FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: GenRequest, workdir: Path):
        self.calls += 1
        Image.new("RGBA", (8, 8), (255, 0, 255, 255)).save(request.raw)
        return gen_base.ProviderRun(provider=self.name, elapsed_seconds=1.23, model=request.model)


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
    assert payload["chroma"]["stale_transparent_rgb_pixels"] == 0
    assert payload["elapsed_seconds"] == 1.23


def test_generate_image_empty_prompt_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        gen.generate_image("codex", "   ", tmp_path / "x.png")


def test_unknown_provider_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        gen._make_provider("gemini", keep_session=False)
