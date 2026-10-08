# SPDX-License-Identifier: Apache-2.0
"""A clip pinned to a closing frame (`--last-frame`) is refused at 1080p before anything is uploaded: the API answers
HTTP 400 and returns no clip (crashbang KUMA pack, 2026-10-07, five combo takes). `sprite-gen video` refuses the request
itself; `video-set` refuses a 1080p set that would film any item pinned (`pins_last_frame`) before its first clip, and
names those items. No network: the HTTP layer and the clip runner are fakes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.gen import video
from sprite_gen.video import batch

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
CRED = video.Credential(token="tok", source=video.AUTH_SOURCE_GROK_LOGIN)


def _still(tmp_path: Path, name: str = "still.png") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (0, 255, 0)).save(path)
    return path


class _FakeApi:
    def __init__(self):
        self.calls: list[tuple[str, str, str, dict | None]] = []

    def call(self, method, url, token, body):
        self.calls.append((method, url, token, body))
        if method == "POST":
            return 200, {"request_id": "req-1"}
        return 200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v/1.mp4", "duration": 3.0}}

    def kw(self):
        return dict(credential=CRED, call=self.call, download=lambda url, token: MP4, sleep=lambda s: None)


@pytest.mark.parametrize("first", [True, False], ids=["first-last", "last-frame-alone"])
def test_video_refuses_a_pinned_closing_frame_at_1080p_before_any_call(tmp_path: Path, first) -> None:
    api = _FakeApi()
    request = video.VideoRequest(image=_still(tmp_path, "a.png") if first else None, last_frame=_still(tmp_path, "z.png"),
                                 prompt="walks in place", out=tmp_path / "clip.mp4", resolution="1080p")
    with pytest.raises(SystemExit, match=r"--last-frame is refused at 1080p .*use --resolution 720p"):
        video.generate_video(request, **api.kw())
    assert api.calls == [] and not request.out.exists()


@pytest.mark.parametrize("resolution", ["480p", "720p"])
def test_a_pinned_closing_frame_below_1080p_is_sent(tmp_path: Path, resolution) -> None:
    api = _FakeApi()
    request = video.VideoRequest(image=_still(tmp_path, "a.png"), last_frame=_still(tmp_path, "z.png"),
                                 prompt="walks in place", out=tmp_path / "clip.mp4", resolution=resolution)
    video.generate_video(request, **api.kw())
    body = api.calls[0][3]
    assert body["resolution"] == resolution and "last_frame" in body


def test_an_unpinned_clip_at_1080p_is_sent(tmp_path: Path) -> None:
    api = _FakeApi()
    video.generate_video(video.VideoRequest(image=_still(tmp_path), prompt="walks in place", out=tmp_path / "clip.mp4",
                                            resolution="1080p"), **api.kw())
    assert api.calls[0][3]["resolution"] == "1080p" and "last_frame" not in api.calls[0][3]


# -- video-set ------------------------------------------------------------------------------------

@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(batch.facing_mod.vision, "grok_inspect", lambda path, **kw: ("right", {}))
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60, "keyed_dir": str(out)})
    monkeypatch.setattr(batch.loop_mod, "run_loop", lambda *a, **kw: {
        "cycle": {"length": 10, "period_global": 10, "ratio": 0.2}, "resampled_seam_ratio": 0.2, "n_out": 10,
        "gif": {"file": "loop.gif"}, "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"}})
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)


def _run(tmp_path: Path, bases: dict[str, str], states: list[str], resolution: str, asked: list[str]):
    def clip(image, prompt, out, report, *, resolution, **kw):
        asked.append(f"{out.parent.name}@{resolution}")
        out.write_bytes(b"test-video")
        report.write_text(json.dumps({"prompt": prompt}))
        return 0

    return batch.run_set(bases={view: _still(tmp_path, f"{view}.png") for view in bases}, states=states,
                         root=tmp_path / "set", character=None, duration=None, resolution=resolution, key="green",
                         concurrency=1, force=False, gap=0, video_runner=clip, walk_start="as-given", align_cycles="off")


@pytest.mark.parametrize("bases,states,named", [
    (["side"], ["idle", "walk"], "side-idle is filmed pinned"),
    (["side"], ["walk", "attack"], "side-attack is filmed pinned"),
    (["front", "back_diagonal"], ["walk", "run"], "back_diagonal-walk, back_diagonal-run are filmed pinned"),
])
def test_a_1080p_set_with_a_pinned_item_is_refused_before_any_clip(tmp_path, offline, bases, states, named) -> None:
    asked: list[str] = []
    with pytest.raises(SystemExit, match=rf"video-set: {named} to end on the canvas \(--last-frame\), which is refused "
                                         r"at 1080p .*use --resolution 720p"):
        _run(tmp_path, bases, states, "1080p", asked)
    assert asked == [] and not any((tmp_path / "set").glob("*/canvas.png"))


def test_a_1080p_set_with_nothing_pinned_is_filmed_and_720p_films_the_pinned_ones(tmp_path, offline) -> None:
    asked: list[str] = []
    assert _run(tmp_path, ["side", "front"], ["walk", "run", "jump"], "1080p", asked)["ok"] == 6
    assert sorted(asked) == sorted(f"{v}-{s}@1080p" for v in ("side", "front") for s in ("walk", "run", "jump"))
    asked.clear()
    assert _run(tmp_path / "pinned", ["back_diagonal"], ["walk", "idle"], "720p", asked)["ok"] == 2
    assert sorted(asked) == ["back_diagonal-idle@720p", "back_diagonal-walk@720p"]
