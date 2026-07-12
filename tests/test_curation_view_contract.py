# SPDX-License-Identifier: Apache-2.0
"""Curation-view display contract (run-contract.md §3/§4): imported `_base`/`_refs`
sources surface as the base row + generation-material chips, the self-report `contract`
field is populated, and filenames with URL-special characters are percent-encoded so
they actually serve (regression: an unencoded `#` in a ref filename became a URL
fragment → 404)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from serve_curation import CurationHandler, _url, build_run_state  # noqa: E402
from sprite_gen.unpack_atlas import import_png_groups  # noqa: E402


def _png(path: Path, color=(80, 80, 80, 255), size=(48, 48)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def _build_import(tmp_path: Path, ref_names, with_base=True) -> Path:
    pngs = tmp_path / "pngs"
    if with_base:
        _png(pngs / "_base" / "founder.png", size=(96, 96))
    _png(pngs / "items" / "1-a.png")
    _png(pngs / "items" / "2-b.png")
    for name in ref_names:
        _png(pngs / "items" / "_refs" / name)
    out = tmp_path / "run"
    import_png_groups(
        out,
        [{
            "name": "items",
            "paths": sorted((pngs / "items").glob("*.png")),
            "labels": [],
            "refs": sorted((pngs / "items" / "_refs").glob("*.png")),
        }],
        base_src=(pngs / "_base" / "founder.png") if with_base else None,
    )
    return out


def _serve_status(run: Path, url: str) -> int:
    CurationHandler.run_dir = run
    CurationHandler.lang = "en"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(CurationHandler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}{url}").status
    finally:
        srv.shutdown()


def test_imported_sources_expose_base_row_and_chips(tmp_path):
    run = _build_import(tmp_path, ["anchor-idle.png", "guide-walk.png"])
    st = build_run_state(run)
    assert st["baseUrl"] == "/run/base-source.png"
    roles = {(r["role"], r["name"]) for r in st["states"][0]["refs"]}
    assert ("anchor", "anchor-idle.png") in roles
    assert ("guide", "guide-walk.png") in roles
    assert st["contract"] == {"base": True, "refs": True, "refsStates": 1, "grid": False, "sourceless": False}


def test_sourceless_import_is_reported(tmp_path):
    run = _build_import(tmp_path, [], with_base=False)
    st = build_run_state(run)
    assert st["baseUrl"] is None
    assert st["states"][0]["refs"] == []
    assert st["contract"]["sourceless"] is True


def _checker_png(path: Path, pitch: int = 4, size: int = 48) -> None:
    """A checkerboard PNG with a `pitch`-px block size so detect_pixel_pitch finds a grid."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    px = im.load()
    for y in range(size):
        for x in range(size):
            if ((x // pitch) + (y // pitch)) % 2 == 0:
                px[x, y] = (240, 240, 240, 255)
    im.save(path)


def test_special_char_state_name_keeps_pixel_grid(tmp_path):
    """A special-char group/state name must not break auto pixel-grid measurement. The
    frame url is percent-encoded for HTTP, but pixelScale is measured from the real
    decoded file path (regression: measuring off the encoded url silently null-ed the
    grid + reported contract.grid=false for special-char imports)."""
    pngs = tmp_path / "pngs"
    _checker_png(pngs / "walk" / "1-a.png")
    _checker_png(pngs / "walk #한글 %" / "1-a.png")  # same image, special-char group name
    out = tmp_path / "run"
    assert _run_import(pngs, out, "--force").returncode == 0
    st = build_run_state(out)
    plain = next(s for s in st["states"] if s["name"] == "walk")
    special = next(s for s in st["states"] if s["name"] == "walk #한글 %")
    assert plain["pixelScale"] is not None
    assert special["pixelScale"] == plain["pixelScale"]  # measured, not silently null
    assert st["contract"]["grid"] is True
    url = special["frames"][0]["url"]  # frame url still percent-encoded + serves
    assert "#" not in url and " " not in url
    assert _serve_status(out, url) == 200


@pytest.mark.parametrize("name", ["guide-a#b.png", "anchor-c d.png", "basis-100%done.png", "guide-café.png"])
def test_special_char_ref_filename_is_url_encoded_and_serves(tmp_path, name):
    run = _build_import(tmp_path, [name])
    url = build_run_state(run)["states"][0]["refs"][0]["url"]
    # nothing that breaks a URL (# → fragment) or an HTML attribute may survive raw
    assert not any(c in url for c in '# "\'<>')
    assert _serve_status(run, url) == 200


def test_url_helper_encodes_only_unsafe_segments():
    assert _url("run", "raw", "down_idle.png") == "/run/raw/down_idle.png"  # unreserved unchanged
    assert _url("run", "a b#c.png") == "/run/a%20b%23c.png"
    assert _url("frames", "down_walk", "frame-0.png") == "/frames/down_walk/frame-0.png"


def _run_import(pngs: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "unpack_atlas_run.py"),
         "--pngs-dir", str(pngs), "--out-dir", str(out), *extra],
        capture_output=True, text=True,
    )


def test_force_reimport_is_a_clean_rebuild(tmp_path):
    """--force re-import must not leave stale source truth (Idempotency/SSoT)."""
    pngs = tmp_path / "pngs"
    _png(pngs / "_base" / "b.png", size=(96, 96))
    _png(pngs / "items" / "1-a.png")
    _png(pngs / "items" / "_refs" / "anchor-x.png")
    out = tmp_path / "run"
    r1 = _run_import(pngs, out, "--force")
    assert r1.returncode == 0, r1.stderr
    assert (out / "base-source.png").is_file()
    assert (out / "references" / "imported" / "items" / "anchor-x.png").is_file()
    # remove both sources from the input, re-import into the SAME out-dir
    shutil.rmtree(pngs / "_base")
    shutil.rmtree(pngs / "items" / "_refs")
    r2 = _run_import(pngs, out, "--force")
    assert r2.returncode == 0, r2.stderr
    assert not (out / "base-source.png").exists()          # stale base gone
    assert not (out / "references" / "imported").exists()  # stale refs gone
    prov = json.loads((out / "unpack-source.json").read_text())
    assert prov["base_source"] is None and prov["imported_refs"] == {}
    st = build_run_state(out)
    assert st["baseUrl"] is None and st["contract"]["sourceless"] is True


def test_unknown_ref_role_fails_loud(tmp_path):
    """A _refs file with an unknown role prefix is rejected, not relabeled to guide."""
    pngs = tmp_path / "pngs"
    _png(pngs / "items" / "1-a.png")
    _png(pngs / "items" / "_refs" / "mystery-source.png")
    out = tmp_path / "run"
    r = _run_import(pngs, out, "--force")
    assert r.returncode != 0
    combined = r.stderr + r.stdout
    assert "mystery-source.png" in combined
    assert "anchor-/basis-/guide-" in combined
    assert not out.exists() or not (out / "references" / "imported").exists()


def _snapshot(run: Path) -> dict:
    return {p.relative_to(run): p.read_bytes()
            for p in run.rglob("*") if p.is_file() and p.name != ".sprite-gen.lock"}


def test_force_reimport_failure_preserves_prior_run(tmp_path):
    """A failed --force re-import must leave the prior valid run byte-intact (Atomicity:
    a rebuild fully succeeds or rolls back — never clear-then-fail)."""
    pngs = tmp_path / "pngs"
    _png(pngs / "_base" / "b.png", size=(96, 96))
    _png(pngs / "items" / "1-a.png")
    _png(pngs / "items" / "_refs" / "anchor-x.png")
    out = tmp_path / "run"
    assert _run_import(pngs, out, "--force").returncode == 0
    before = _snapshot(out)
    assert (out / "base-source.png").is_file() and before  # a real prior run exists
    # make the next import invalid (unknown role) and re-run --force → must fail...
    _png(pngs / "items" / "_refs" / "mystery-source.png")
    r = _run_import(pngs, out, "--force")
    assert r.returncode != 0
    # ...and the prior run must be untouched (not destroyed / emptied), no staging leak
    assert _snapshot(out) == before
    assert not (out.parent / f".{out.name}.sg-staging").exists()


def test_publish_phase_failure_rolls_back(tmp_path, monkeypatch):
    """If a publish move fails mid-swap, the prior run is rolled back byte-intact, no
    partial new run is exposed, and staging/backup are cleaned."""
    import pathlib

    from sprite_gen import unpack_atlas as ua

    pngs = tmp_path / "pngs"
    _png(pngs / "items" / "1-a.png")
    out = tmp_path / "run"
    assert _run_import(pngs, out, "--force").returncode == 0
    before = _snapshot(out)
    _png(pngs / "items" / "2-b.png")  # a real change the re-import would publish

    orig_rename = pathlib.Path.rename

    def flaky(self, target):
        if ".sg-staging" in str(self):  # moving new content out of staging into out_dir
            raise OSError("injected publish failure")
        return orig_rename(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", flaky)
    with pytest.raises((OSError, SystemExit)):
        ua.run(pngs_dir=pngs, out_dir=out, force=True)
    assert _snapshot(out) == before                             # prior run byte-intact
    assert not (out.parent / f".{out.name}.sg-staging").exists()  # staging cleaned
    assert not (out / ".sg-backup").exists()                    # in-place backup cleaned


def test_publish_never_removes_out_dir(tmp_path, monkeypatch):
    """out_dir (and the lock inside it) must never disappear during publish, so a
    concurrent writer cannot mkdir+lock the run path mid-swap (Isolation)."""
    import pathlib

    from sprite_gen import unpack_atlas as ua

    pngs = tmp_path / "pngs"
    _png(pngs / "items" / "1-a.png")
    out = tmp_path / "run"
    assert _run_import(pngs, out, "--force").returncode == 0
    from sprite_gen.runio import LOCK_FILENAME
    _png(pngs / "items" / "2-b.png")

    orig_rename = pathlib.Path.rename
    vanished = {"path": False, "lock": False}

    def watch(self, target):
        # check both before and after every rename that happens during the publish
        for _ in range(1):
            if not out.exists():
                vanished["path"] = True
            elif not (out / LOCK_FILENAME).exists():
                vanished["lock"] = True
        r = orig_rename(self, target)
        if not out.exists():
            vanished["path"] = True
        elif not (out / LOCK_FILENAME).exists():
            vanished["lock"] = True
        return r

    monkeypatch.setattr(pathlib.Path, "rename", watch)
    assert ua.run(pngs_dir=pngs, out_dir=out, force=True) == 0
    assert not vanished["path"], "out_dir vanished during publish (isolation window)"
    assert not vanished["lock"], "run-dir lock vanished during publish (isolation window)"
    # the new run published cleanly
    assert (out / "frames" / "items" / "frame-1.png").is_file()


def test_api_run_reader_isolated_from_concurrent_publish(tmp_path):
    """build_run_state reads under read_guard, so it blocks while a publish holds the
    exclusive publish_guard and returns a COMPLETE snapshot — never a mid-publish 500 or
    old/new mix (reader isolation)."""
    import threading
    import time

    from serve_curation import build_run_state
    from sprite_gen import runio

    pngs = tmp_path / "pngs"
    _png(pngs / "items" / "1-a.png")
    run = tmp_path / "run"
    assert _run_import(pngs, run, "--force").returncode == 0

    outcome: dict = {}
    ready = threading.Event()

    def reader():
        ready.wait()
        try:
            st = build_run_state(run)  # read_guard SH -> blocks under the publish EX lock
            outcome["states"] = len(st["states"])
        except Exception as exc:  # a mid-publish read would raise / 500
            outcome["err"] = repr(exc)
        outcome["at"] = time.monotonic()

    th = threading.Thread(target=reader)
    th.start()
    with runio.publish_guard(run):          # simulate a publish holding the exclusive lock
        ready.set()
        time.sleep(0.25)                     # reader must be blocked for this whole window
        assert "at" not in outcome, "reader was not blocked by publish_guard (no isolation)"
        released_at = time.monotonic()
    th.join(3)
    assert "err" not in outcome, outcome.get("err")
    assert outcome.get("states"), "reader got no complete snapshot"
    assert outcome["at"] >= released_at, "reader returned before the publish released"
