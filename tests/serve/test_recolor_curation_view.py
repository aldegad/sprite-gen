# SPDX-License-Identifier: Apache-2.0
"""Baked colourways reach the curation view, and the adopted pick survives.

`sprite-gen recolor` bakes variant sheets into `<run-dir>/variants/`; the view has to
show them side by side and record which one the human adopted. Three properties are
load-bearing and each has a test below:

1. The numbers the view prints come from the BAKE REPORT, not from the view recounting
   pixels — one truth for "how many pixels this colourway swapped".
2. Nothing disappears quietly: a report row whose PNG is gone is reported as missing
   rather than dropped from the list, and a pick naming a variant the current bake no
   longer produces is kept and flagged (`pickedKnown: False`) instead of cleared.
3. The pick is a normal curation-sidecar field, so it round-trips through the view's own
   save route and is carried over by a save that does not author it (the same contract
   `anchors` has — otherwise an edit made in a run without variants would erase it).
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.effects import recolor
from sprite_gen.curate.curation import load_curation, recolor_pick
from sprite_gen.serve.serve_curation import CurationHandler, build_run_state
from sprite_gen.frames.unpack_atlas import import_png_groups

ROOT = Path(__file__).resolve().parents[2]
CURATOR = ROOT / "sprite_gen" / "serve" / "curator"

SHIRT = (200, 40, 40, 255)
TRIM = (30, 30, 60, 255)


def _png(path: Path, color, size=(48, 48)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def _sheet(path: Path) -> None:
    """A tiny two-colour sheet: left half shirt, right half trim."""
    image = Image.new("RGBA", (8, 4), SHIRT)
    for x in range(4, 8):
        for y in range(4):
            image.putpixel((x, y), TRIM)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


@pytest.fixture()
def baked_run(tmp_path: Path) -> Path:
    """A run dir with two baked colourways in `variants/`."""
    pngs = tmp_path / "pngs"
    _png(pngs / "items" / "1-a.png", (80, 80, 80, 255))
    _png(pngs / "items" / "2-b.png", (80, 80, 80, 255))
    run = tmp_path / "run"
    import_png_groups(run, [{
        "name": "items",
        "paths": sorted((pngs / "items").glob("*.png")),
        "labels": [],
        "refs": [],
    }])
    _sheet(run / "sprite-sheet-alpha.png")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "kind": recolor.SPEC_KIND,
        "variants": [
            {"name": "azure", "map": {"#c82828": "#2860c8"}},
            # `#00ff00` is in no sheet pixel: an unused source the report names.
            {"name": "moss", "map": {"#c82828": "#3c7a3c", "#00ff00": "#111111"}},
        ],
    }), encoding="utf-8")
    recolor.bake(run / "sprite-sheet-alpha.png", spec, run / recolor.VARIANTS_DIRNAME)
    return run


def _serve(run: Path):
    CurationHandler.run_dir = run
    CurationHandler.lang = "en"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(CurationHandler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _post_curation(run: Path, body: dict) -> int:
    """The view's own save route, so the server-side write contract is what is tested."""
    srv = _serve(run)
    port = srv.server_address[1]
    try:
        snapshot = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run").read())
        payload = {"version": 1, "kind": "sprite-gen-curation",
                   "runRevision": snapshot["runRevision"], "states": {}, **body}
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/curation",
                                     data=json.dumps(payload).encode(), method="POST")
        with urllib.request.urlopen(req) as res:
            return res.status
    finally:
        srv.shutdown()


def test_baked_variants_surface_with_the_reports_own_numbers(baked_run: Path) -> None:
    view = build_run_state(baked_run)["recolor"]
    assert view is not None
    assert view["match"] == "exact"
    assert view["base"]["present"] and view["base"]["name"] == "sprite-sheet-alpha.png"
    names = [v["name"] for v in view["variants"]]
    assert names == ["azure", "moss"]

    report = json.loads((baked_run / recolor.VARIANTS_DIRNAME / recolor.DEFAULT_REPORT_NAME)
                        .read_text(encoding="utf-8"))
    for shown, baked in zip(view["variants"], report["variants"]):
        assert shown["substitutedPixels"] == baked["substituted_pixels"]
        assert shown["passthroughPixels"] == baked["passthrough_pixels"]
        assert shown["passthroughColorCount"] == baked["passthrough_color_count"]
    # the half-sheet that matched, and the half the map never covered — both reported
    assert view["variants"][0]["substitutedPixels"] == 16
    assert view["variants"][0]["passthroughPixels"] == 16
    # a spec typo (a source colour no pixel has) is named, not swallowed
    assert view["variants"][1]["unusedSources"] == ["#00ff00"]
    assert view["variants"][0]["unusedSources"] == []


def test_variant_urls_serve_and_bust_the_cache(baked_run: Path) -> None:
    view = build_run_state(baked_run)["recolor"]
    srv = _serve(baked_run)
    port = srv.server_address[1]
    try:
        for entry in [view["base"], *view["variants"]]:
            assert re.search(r"\?v=\d+$", entry["url"]), entry["url"]
            res = urllib.request.urlopen(f"http://127.0.0.1:{port}{entry['url']}")
            assert res.status == 200
            assert res.headers["Content-Type"] == "image/png"
    finally:
        srv.shutdown()


def test_the_thumbnail_crop_comes_from_the_manifest_layout(baked_run: Path) -> None:
    """Thumbnails crop to a frame, and the box is the one compose wrote — never an
    assumed origin. Without a layout there is no box, and they show the whole sheet."""
    assert build_run_state(baked_run)["recolor"]["swatch"] is None, \
        "no manifest yet — the view must not invent a frame box"

    (baked_run / "manifest.json").write_text(json.dumps({
        "frame_layout": {"sheetWidth": 8, "sheetHeight": 4, "cellWidth": 4, "cellHeight": 4,
                         "rows": {"items": [{"x": 4, "y": 0, "w": 4, "h": 4}]}},
    }), encoding="utf-8")
    assert build_run_state(baked_run)["recolor"]["swatch"] == {
        "x": 4, "y": 0, "w": 4, "h": 4, "sheetWidth": 8, "sheetHeight": 4}

    (baked_run / "manifest.json").write_text(json.dumps({"atlas": "sprite-sheet-alpha.png"}),
                                             encoding="utf-8")
    assert build_run_state(baked_run)["recolor"]["swatch"] is None


def test_a_run_without_a_bake_has_no_variant_section(tmp_path: Path) -> None:
    pngs = tmp_path / "pngs"
    _png(pngs / "items" / "1-a.png", (80, 80, 80, 255))
    run = tmp_path / "run"
    import_png_groups(run, [{"name": "items",
                             "paths": sorted((pngs / "items").glob("*.png")),
                             "labels": [], "refs": []}])
    assert build_run_state(run)["recolor"] is None


def test_a_foreign_report_file_fails_loud(baked_run: Path) -> None:
    """A file where the report belongs but of another kind is an error, not "no variants" —
    reading it as an empty result would hide a bake that landed somewhere unexpected."""
    path = baked_run / recolor.VARIANTS_DIRNAME / recolor.DEFAULT_REPORT_NAME
    path.write_text(json.dumps({"kind": "something-else"}), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        build_run_state(baked_run)
    assert recolor.REPORT_KIND in str(excinfo.value)


def test_a_missing_sheet_is_reported_not_dropped(baked_run: Path) -> None:
    (baked_run / recolor.VARIANTS_DIRNAME / "moss.png").unlink()
    view = build_run_state(baked_run)["recolor"]
    assert [v["name"] for v in view["variants"]] == ["azure", "moss"]
    missing = view["variants"][1]
    assert missing["present"] is False and missing["url"] is None


def test_the_pick_round_trips_through_the_view_save_route(baked_run: Path) -> None:
    assert _post_curation(baked_run, {"recolor": {"picked": "moss"}}) == 200
    assert recolor_pick(load_curation(baked_run)) == "moss"
    view = build_run_state(baked_run)["recolor"]
    assert view["picked"] == "moss" and view["pickedKnown"] is True
    # clearing is an intent too: the view sends an empty object, and it lands
    assert _post_curation(baked_run, {"recolor": {}}) == 200
    assert recolor_pick(load_curation(baked_run)) is None


def test_a_save_that_does_not_author_the_pick_carries_it_over(baked_run: Path) -> None:
    """A view on a run with no variant section omits the field — that must read as
    "untouched", not as "cleared" (same carry-over contract as `anchors`)."""
    assert _post_curation(baked_run, {"recolor": {"picked": "azure"}}) == 200
    assert _post_curation(baked_run, {}) == 200
    assert recolor_pick(load_curation(baked_run)) == "azure"


def test_a_pick_the_bake_no_longer_produces_is_kept_and_flagged(baked_run: Path) -> None:
    assert _post_curation(baked_run, {"recolor": {"picked": "azure"}}) == 200
    report_path = baked_run / recolor.VARIANTS_DIRNAME / recolor.DEFAULT_REPORT_NAME
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["variants"] = [v for v in report["variants"] if v["name"] != "azure"]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    view = build_run_state(baked_run)["recolor"]
    assert view["picked"] == "azure", "채택이 조용히 사라졌다"
    assert view["pickedKnown"] is False, "없는 컬러웨이인데 정상으로 보고했다"
    assert recolor_pick(load_curation(baked_run)) == "azure"


def test_the_spa_wires_the_section_in_every_language() -> None:
    """The section is three files agreeing: index.html loads the module, boot renders it,
    and both dictionaries know its strings. A key present in only one language falls back
    to English silently, which is exactly the drift this catches."""
    index = (CURATOR / "index.html").read_text(encoding="utf-8")
    assert '<script src="/src/recolor.js"></script>' in index
    assert index.index("/src/recolor.js") < index.index("/src/boot.js"), \
        "recolor.js must load before boot.js calls it"

    boot = (CURATOR / "src" / "boot.js").read_text(encoding="utf-8")
    assert "seedRecolorPick(run)" in boot and "renderRecolorVariants(run.recolor)" in boot

    module = (CURATOR / "src" / "recolor.js").read_text(encoding="utf-8")
    for fn in ("function seedRecolorPick", "function renderRecolorVariants",
               "function pickRecolorVariant"):
        assert fn in module

    persistence = (CURATOR / "src" / "persistence.js").read_text(encoding="utf-8")
    assert "payload.recolor" in persistence

    i18n = (CURATOR / "src" / "i18n.js").read_text(encoding="utf-8")
    en, ko = i18n.split("  ko: {", 1)
    for key in ("rcTitle", "rcBase", "rcPick", "rcPicked", "rcMissing", "rcMeta",
                "rcVariantNote", "rcUnusedSources", "rcPickUnknown", "rcPickSaved",
                "rcPickCleared", "tRcSection", "tRcThumb", "tRcPick",
                "treeRecolorNote", "tTreeRecolor"):
        assert f"{key}:" in en, f"en dictionary is missing {key}"
        assert f"{key}:" in ko, f"ko dictionary is missing {key}"
