# SPDX-License-Identifier: Apache-2.0
"""video-set reads the clip report `sprite-gen video` wrote as the UTF-8 it is.

`sprite-gen video` writes its report in UTF-8 with the prompt as sent (`runio.atomic_write_text`,
`json.dumps(..., ensure_ascii=False)`), and video-set reads it back for `auth_source` and, for an item filmed to an end
frame, to add the frame's digest and write it again. Read in the locale's encoding, as on a Windows whose code page is
not UTF-8 (cp949 on a Korean one), a dash or Korean in the prompt either stopped the read or came back as other
characters, which the digest's write then kept in the report. The cases run on any machine: on a UTF-8 locale the old
read passed them too; on a Korean Windows without PYTHONUTF8 they measure it. No clip is made: the clip run is a
stand-in that writes the report as `sprite-gen video` does.
"""

import hashlib
import json

import pytest
from PIL import Image

from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.video import batch

# Read as cp949, the first two stop the read (0xe2 0x80; 0x9c then a space) and the third reads as other characters.
SAMPLES = {"dash": "walk — loop", "korean-undecodable": "한 바퀴 도는 걸음", "korean-misread": "걷는 모습"}
AUTH = "grok-login"


def stand_ins(tmp_path, monkeypatch):
    """The still, its end frame (the same picture) and the steps after the clip, each a stand-in."""
    base = tmp_path / "base.png"
    Image.new("RGB", (32, 32), (0, 255, 0)).save(base)
    final = tmp_path / "final.png"
    Image.new("RGB", (32, 32), (0, 255, 0)).save(final)

    def canvas_stub(still, out, **kw):
        Image.new("RGB", (32, 32), (0, 255, 0)).save(out)
        report = {"fit": "state", "shape": "wide", "canvas": [32, 32], "offset": [0, 0]}
        kw["report_path"].write_text(json.dumps(report), encoding="utf-8")
        return report

    def frames_stub(clip, out, **kw):
        return {"fps": 24, "frames": 24, "alpha_zero_pct_min": 0, "alpha_zero_pct_max": 0, "keyed_dir": str(out)}

    def loop_stub(frames, out, **kw):
        return {"cycle": {"kind": "once", "length": 24, "period_global": None, "ratio": 0},
                "resampled_seam_ratio": None, "held_fill": {"cells": 10, "poses": 10, "held_runs": [], "made": [], "before": {"cells": 10, "poses": 10, "held_runs": []}}, "n_out": 24, "gif": {"file": "g"}, "webp": {"file": "w"}, "strip": {"path": "s"}}

    monkeypatch.setattr(batch.canvas_mod, "run_canvas", canvas_stub)
    monkeypatch.setattr(batch.frames_mod, "run_frames", frames_stub)
    monkeypatch.setattr(batch.loop_mod, "run_loop", loop_stub)
    return base, final


def run_death(tmp_path, base, video_runner, **kw):
    return batch.run_set(bases={"front": base}, states=["death"], root=tmp_path / "set", character=None, duration=None,
                         resolution="480p", key="green", concurrency=1, force=False, gap=0, video_runner=video_runner, **kw)


@pytest.mark.parametrize("sample", list(SAMPLES.values()), ids=list(SAMPLES))
def test_the_end_frame_digest_keeps_the_prompt_and_the_auth_source(tmp_path, monkeypatch, sample):
    base, final = stand_ins(tmp_path, monkeypatch)
    sent = []

    def video_stub(image, prompt, out, report, **kw):
        assert kw["last_frame"] == final
        sent.append(prompt)
        out.write_bytes(b"clip")
        atomic_write_text(report, json.dumps({"prompt": prompt, "auth_source": AUTH}, ensure_ascii=False, indent=2) + "\n")
        return 0

    result = run_death(tmp_path, base, video_stub, end_frames={"front-death": final},
                       motion={"death": f"The character takes {sample} and falls."})
    item = result["items"][0]
    assert item["ok"], item.get("error")
    assert sample in sent[0]
    report = json.loads((tmp_path / "set" / "front-death" / "clip.report.json").read_bytes().decode("utf-8"))
    assert report["end_frame_sha256"] == hashlib.sha256(final.read_bytes()).hexdigest()
    assert report["prompt"].encode("utf-8") == sent[0].encode("utf-8")
    assert report["auth_source"] == AUTH and item["clip"]["auth_source"] == AUTH


@pytest.mark.parametrize("written", [b'{"prompt": "walk \xff loop", "auth_source": "grok-login"}',
                                     b'{"prompt": "walk', b'["walk"]'],
                         ids=["not-utf8", "cut-off", "not-an-object"])
def test_a_clip_report_that_cannot_be_read_fails_the_item_saying_so(tmp_path, monkeypatch, written):
    """A report that is there and cannot be read is not one that is missing: the item stops, naming the report, and
    no `auth_source` is written for it."""
    base, _ = stand_ins(tmp_path, monkeypatch)

    def video_stub(image, prompt, out, report, **kw):
        out.write_bytes(b"clip")
        report.write_bytes(written)
        return 0

    item = run_death(tmp_path, base, video_stub)["items"][0]
    assert not item["ok"] and "clip report" in item["error"] and "clip.report.json" in item["error"]
    assert "auth_source" not in item["clip"]


def test_a_missing_clip_report_leaves_auth_source_empty(tmp_path, monkeypatch):
    base, _ = stand_ins(tmp_path, monkeypatch)

    def video_stub(image, prompt, out, report, **kw):
        out.write_bytes(b"clip")
        return 0

    item = run_death(tmp_path, base, video_stub)["items"][0]
    assert item["ok"], item.get("error")
    assert item["clip"]["auth_source"] is None
