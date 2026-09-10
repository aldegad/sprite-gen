# SPDX-License-Identifier: Apache-2.0
"""User journeys, persisted defaults, access uncertainty and real CLI contracts."""
import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sprite_gen.workflow import access, guide, preferences
from sprite_gen.workflow.catalog import FIELDS, FLOWS, MOTION_METHODS
from sprite_gen.gen.video import AUTH_SOURCE_API_KEY


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SPRITE_GEN_CONFIG_DIR", str(tmp_path / "settings"))


@pytest.fixture
def ready_login(monkeypatch):
    seen = []
    def probe(provider, *, video=False):
        seen.append((provider, video))
        return {"provider": provider, "login": "ready", "subscription": "unknown", "quota": "unknown",
                "billing": "subscription", "reason": "unverified media access"}
    monkeypatch.setattr(guide, "probe_access", probe)
    return seen


def test_reads_and_request_overrides_never_write(tmp_path, ready_login):
    assert preferences.read_settings()["revision"] == "missing"
    assert not preferences.settings_path().parent.exists()
    saved = preferences.save_settings("image", {"image_provider": "codex", "curation": "skip"}, expected_revision="missing")
    path = preferences.settings_path()
    original, mtime = path.read_bytes(), path.stat().st_mtime_ns
    for _ in range(2):
        view = guide.guide("image", choices={"image_provider": "grok"}, confirmed_access=("grok",))
        assert view["status"] == "ready"
        assert view["selected"] == {"image_provider": "grok", "curation": "skip"}
        assert view["sources"] == {"image_provider": "request", "curation": "saved"}
    assert path.read_bytes() == original and path.stat().st_mtime_ns == mtime
    again = preferences.save_settings("image", saved["profiles"]["image"], expected_revision=saved["revision"])
    assert again == saved and path.stat().st_mtime_ns == mtime


def test_profiles_merge_and_clear_without_erasing_other_kind():
    first = preferences.save_settings("image", {"image_provider": "grok"}, expected_revision="missing")
    second = preferences.save_settings("sprite", {"motion_method": "gpt-rows"}, expected_revision=first["revision"])
    third = preferences.save_settings("image", {"curation": "open"}, expected_revision=second["revision"])
    assert third["profiles"]["image"] == {"image_provider": "grok", "curation": "open"}
    cleared = preferences.save_settings("sprite", {}, expected_revision=third["revision"], clear=True)
    assert cleared["profiles"] == {"image": third["profiles"]["image"]}


@pytest.mark.parametrize("raw", [b"bad json", b'[]', b'{"version":2,"profiles":{}}',
                                    b'{"version":1,"profiles":{"image":{"motion_method":"gpt-rows"}}}',
                                    b'{"version":1,"profiles":{"image":{"image_provider":true}}}'])
def test_corrupt_settings_fail_without_reset(raw):
    path = preferences.settings_path()
    path.parent.mkdir()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="invalid preferences"):
        preferences.read_settings()
    with pytest.raises(ValueError):
        preferences.save_settings("image", {"curation": "open"}, expected_revision="missing")
    assert path.read_bytes() == raw


def _race_writer(root, kind, revision, event, queue):
    os.environ["SPRITE_GEN_CONFIG_DIR"] = root
    event.wait(10)
    try:
        preferences.save_settings(kind, {"curation": "skip"}, expected_revision=revision)
        queue.put((kind, "saved"))
    except ValueError:
        queue.put((kind, "conflict"))


def test_two_processes_cannot_overwrite_same_snapshot():
    ctx = multiprocessing.get_context("spawn")
    event, queue = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_race_writer, args=(str(preferences.settings_path().parent), kind, "missing", event, queue)) for kind in FLOWS]
    for worker in workers:
        worker.start()
    event.set()
    results = [queue.get(timeout=15) for _ in workers]
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    assert sorted(status for _, status in results) == ["conflict", "saved"]
    winner = next(kind for kind, status in results if status == "saved")
    assert preferences.read_settings()["profiles"] == {winner: {"curation": "skip"}}


def test_first_sprite_request_and_existing_base(ready_login, tmp_path):
    view = guide.guide("sprite")
    assert [q["field"] for q in view["questions"]] == ["image_provider", "motion_method"]
    assert view["pipeline"] is None
    base = tmp_path / "base.png"
    base.write_bytes(b"reference fixture")
    ready_login.clear()
    view = guide.guide("sprite", base_image=base, choices={"motion_method": "gpt-rows"}, confirmed_access=("codex",))
    assert view["status"] == "ready" and view["pipeline"]["image_provider"] is None
    assert ready_login == [("codex", False)]
    assert "extract" in view["pipeline"]["steps"]


@pytest.mark.parametrize("base_provider", ["codex", "grok"])
@pytest.mark.parametrize("method", MOTION_METHODS)
def test_every_sprite_combination_keeps_base_and_motion_distinct(ready_login, base_provider, method):
    view = guide.guide("sprite", choices={"image_provider": base_provider, "motion_method": method}, confirmed_access=("codex", "grok"))
    assert view["status"] == "ready"
    assert view["pipeline"]["image_provider"] == base_provider
    assert view["pipeline"]["generation_provider"] == MOTION_METHODS[method]["provider"]
    assert view["pipeline"]["steps"] == MOTION_METHODS[method]["steps"]


def test_unknown_subscription_is_not_inferred_from_login_or_defaults(ready_login):
    preferences.save_settings("image", {"image_provider": "codex"}, expected_revision="missing")
    view = guide.guide("image")
    assert view["access"][0]["subscription"] == "unknown"
    assert [q["field"] for q in view["questions"]] == ["access_codex"]
    assert view["status"] == "needs-input"


def test_failed_provider_stays_blocked_even_with_confirmation(monkeypatch):
    monkeypatch.setattr(guide, "probe_access", lambda provider, **kw: {"provider": provider, "login": "unavailable", "billing": "subscription", "reason": "expired"})
    view = guide.guide("image", choices={"image_provider": "codex"}, confirmed_access=("codex",))
    assert view["status"] == "blocked"
    assert view["selected"]["image_provider"] == "codex"


def test_finish_offers_curation_then_save_but_never_writes_or_probes(tmp_path, monkeypatch):
    monkeypatch.setattr(guide, "probe_access", lambda *a, **k: pytest.fail("finish must not probe"))
    output = tmp_path / "output.png"
    output.touch()
    view = guide.guide("image", stage="finish", result=output, choices={"image_provider": "grok"})
    assert [q["field"] for q in view["questions"]] == ["curation"]
    view = guide.guide("image", stage="finish", result=output, choices={"image_provider": "grok", "curation": "skip"})
    assert [q["field"] for q in view["questions"]] == ["save_defaults"]
    assert preferences.read_settings()["revision"] == "missing"
    preferences.save_settings("image", view["save_candidate"], expected_revision=view["settings_revision"])
    view = guide.guide("image", stage="finish", result=output, choices={"image_provider": "codex"})
    assert view["questions"] == [] and view["curation"] == "skip"
    assert preferences.read_settings()["profiles"]["image"]["image_provider"] == "grok"


def test_finish_requires_result_and_generation_choices(tmp_path):
    with pytest.raises(ValueError, match="existing --result"):
        guide.guide("image", stage="finish")
    with pytest.raises(ValueError, match="actual generation choices"):
        guide.guide("sprite", stage="finish", result=tmp_path)


def test_access_probe_redacts_output_and_separates_auth_from_subscription(monkeypatch):
    monkeypatch.setattr(access.shutil, "which", lambda _: "/fake/codex")
    monkeypatch.setattr(access.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="synthetic-secret"))
    result = access.probe_access("codex")
    assert result["login"] == "ready" and result["subscription"] == "unknown"
    assert "synthetic-secret" not in json.dumps(result)
    monkeypatch.setattr(access.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="API key authentication", stderr=""))
    assert access.probe_access("codex")["login"] == "unknown"


def test_video_api_key_requires_own_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(access, "resolve_credential", lambda **kw: SimpleNamespace(source=AUTH_SOURCE_API_KEY))
    actual = access.probe_access("grok", video=True)
    assert actual["billing"] == "api-credit"
    monkeypatch.setattr(guide, "probe_access", lambda *a, **k: actual)
    base = tmp_path / "base.png"
    base.touch()
    options = dict(base_image=base, choices={"motion_method": "grok-video"}, confirmed_access=("grok",))
    view = guide.guide("sprite", **options)
    assert [q["field"] for q in view["questions"]] == ["confirm_api_billing"]
    assert guide.guide("sprite", confirm_api_billing=True, **options)["status"] == "ready"


def test_real_cli_finish_save_and_reload(tmp_path):
    def cli(*args):
        p = subprocess.run([sys.executable, "-m", "sprite_gen.cli", *args], capture_output=True, text=True)
        return p, json.loads(p.stdout) if p.stdout else None
    p, data = cli("defaults", "show")
    assert p.returncode == 0 and data["revision"] == "missing"
    p, data = cli("workflow", "--kind", "image", "--stage", "finish", "--result", str(tmp_path), "--image-provider", "grok", "--curation", "skip")
    assert p.returncode == 2 and data["questions"][0]["field"] == "save_defaults"
    p, saved = cli("defaults", "save", "--kind", "image", "--image-provider", "grok", "--curation", "skip", "--expected-revision", data["settings_revision"])
    assert p.returncode == 0
    p, data = cli("workflow", "--kind", "image", "--stage", "finish", "--result", str(tmp_path))
    assert p.returncode == 0 and not data["questions"] and data["curation"] == "skip"
    p, _ = cli("defaults", "save", "--kind", "image", "--image-provider", "codex", "--expected-revision", "missing")
    assert p.returncode != 0 and "changed since" in p.stderr


def test_catalog_routes_use_real_engine_verbs_and_docs():
    from sprite_gen.cli import COMMANDS
    root = Path(__file__).resolve().parents[2]
    for method in MOTION_METHODS.values():
        assert set(method["steps"]) <= set(COMMANDS)
        assert (root / method["doc"]).is_file()
    assert all(set(flow["fields"]) <= set(FIELDS) for flow in FLOWS.values())


def test_one_image_can_enter_optional_curation(tmp_path, ready_login):
    from PIL import Image
    from sprite_gen.serve.serve_curation import build_run_state

    pngs = tmp_path / "images"
    pngs.mkdir()
    output = pngs / "candidate.png"
    Image.new("RGBA", (24, 24), (55, 80, 110, 255)).save(output)
    view = guide.guide("image", stage="finish", result=output,
                       choices={"image_provider": "codex", "curation": "open"})
    assert view["curation"] == "open"
    run = tmp_path / "curation-run"
    imported = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "unpack-atlas",
                               "--pngs-dir", str(pngs), "--out-dir", str(run)],
                              capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr
    state = build_run_state(run)
    assert sum(len(row["frames"]) for row in state["states"]) == 1
    assert output.is_file()
