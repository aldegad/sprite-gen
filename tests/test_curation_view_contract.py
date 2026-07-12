# SPDX-License-Identifier: Apache-2.0
"""Curation-view display contract (run-contract.md §3/§4): imported `_base`/`_refs`
sources surface as the base row + generation-material chips, the self-report `contract`
field is populated, and filenames with URL-special characters are percent-encoded so
they actually serve (regression: an unencoded `#` in a ref filename became a URL
fragment → 404)."""

from __future__ import annotations

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
