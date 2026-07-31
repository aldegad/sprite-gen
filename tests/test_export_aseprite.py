# SPDX-License-Identifier: Apache-2.0
"""The Aseprite export is held to two external contracts, not to taste.

1. Aseprite's own `json-array` output shape (real CLI exports, aseprite/aseprite
   issues #2116/#3611): `frames[]` entries carry exactly filename/frame/rotated/
   trimmed/spriteSourceSize/sourceSize/duration, and `meta.frameTags[]` entries
   carry exactly name/from/to/direction.
2. What Phaser actually consumes (AnimationManager.createFromAseprite): frames
   addressed by stringified global index (`frameKey = i.toString()`), per-frame
   `duration`, and `meta.frameTags[].{name,from,to,direction}`.

Structural claims only — a real Phaser runtime load is not exercised here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import run_script
from sprite_gen import cli, export_aseprite
from sprite_gen.export_aseprite import aseprite_json, split_state_jsons

ROOT = Path(__file__).resolve().parents[1]

# Aseprite real-export key sets (rubric anchors REF-A1 / REF-A2).
ASEPRITE_FRAME_KEYS = {"filename", "frame", "rotated", "trimmed",
                       "spriteSourceSize", "sourceSize", "duration"}
ASEPRITE_TAG_KEYS = {"name", "from", "to", "direction"}


def _manifest(rows: dict[str, dict] | None = None) -> dict:
    """A minimal runtime manifest honouring the Runtime Contract fields."""
    layout_rows, anim_rows = {}, {}
    rows = rows if rows is not None else {
        "idle": {"rects": [(0, 0), (64, 0)], "durations": [125, 125], "fps": 8},
        "walk": {"rects": [(0, 64), (64, 64), (0, 64)], "durations": [90, 90, 90], "fps": 11},
    }
    for row_index, (state, spec) in enumerate(rows.items()):
        layout_rows[state] = [{"x": x, "y": y, "w": 64, "h": 64} for x, y in spec["rects"]]
        anim_rows[state] = {
            "row": row_index,
            "frames": len(spec["rects"]),
            "fps": spec.get("fps", 6),
            "loop": True,
            "frame_variant": "pixel",
        }
        if spec.get("durations") is not None:
            anim_rows[state]["durations_ms"] = spec["durations"]
    return {
        "game_input": "sprite-sheet-alpha.png",
        "degraded_static_fallback": False,
        "animation": {"rows": anim_rows},
        "frame_layout": {
            "sheetWidth": 192, "sheetHeight": 128,
            "cellWidth": 64, "cellHeight": 64,
            "rows": layout_rows,
        },
    }


def test_frames_and_tags_match_aseprite_export_key_structure() -> None:
    data = aseprite_json(_manifest())

    assert set(data) == {"frames", "meta"}
    for entry in data["frames"]:
        assert set(entry) == ASEPRITE_FRAME_KEYS
        assert set(entry["frame"]) == {"x", "y", "w", "h"}
    for tag in data["meta"]["frameTags"]:
        assert set(tag) == ASEPRITE_TAG_KEYS
        assert tag["direction"] == "forward"
    # meta carries every field Aseprite's own export writes for a packed sheet.
    assert {"app", "version", "image", "format", "size", "scale", "frameTags"} <= set(data["meta"])
    assert data["meta"]["image"] == "sprite-sheet-alpha.png"
    assert data["meta"]["size"] == {"w": 192, "h": 128}


def test_phaser_consumption_contract_global_index_tags_and_durations() -> None:
    data = aseprite_json(_manifest())

    # Phaser: `frameKey = i.toString()` — filenames must be the stringified
    # global index, in order, across all states.
    assert [f["filename"] for f in data["frames"]] == [str(i) for i in range(5)]
    # Tags partition the global frames array in state playback order.
    assert data["meta"]["frameTags"] == [
        {"name": "idle", "from": 0, "to": 1, "direction": "forward"},
        {"name": "walk", "from": 2, "to": 4, "direction": "forward"},
    ]
    # durations_ms is the timing SSoT and survives per frame.
    assert [f["duration"] for f in data["frames"]] == [125, 125, 90, 90, 90]


def test_cell_reuse_repeats_the_rect_but_not_the_filename() -> None:
    # walk repeats rect (0,64) as its 3rd instance — the atlas cell is shared,
    # so the exported rect repeats too (Aseprite-isomorphic), under a new index.
    data = aseprite_json(_manifest())
    walk = data["frames"][2:]
    assert walk[0]["frame"] == walk[2]["frame"]
    assert walk[0]["filename"] != walk[2]["filename"]


def test_missing_durations_fall_back_to_fps_spacing() -> None:
    rows = {"idle": {"rects": [(0, 0), (64, 0)], "durations": None, "fps": 8}}
    data = aseprite_json(_manifest(rows))
    assert [f["duration"] for f in data["frames"]] == [125, 125]


def test_disagreeing_manifest_rows_fail_loud() -> None:
    manifest = _manifest()
    del manifest["animation"]["rows"]["walk"]
    try:
        aseprite_json(manifest)
    except ValueError as err:
        assert "disagree" in str(err)
    else:  # pragma: no cover
        raise AssertionError("a manifest with mismatched rows must not export")


def test_cli_subcommand_reuses_the_module_declaration() -> None:
    """Identity, not equality — the `curation` pattern."""
    _description, add_args, run_fn = cli.COMMANDS["export-aseprite"]
    assert add_args is export_aseprite.add_arguments
    assert run_fn is export_aseprite.run


def test_json_hash_keys_are_the_filenames_in_playback_order() -> None:
    # Aseprite `json-hash`: filename becomes the map key, entries drop the
    # filename field, insertion order stays playback order.
    array = aseprite_json(_manifest())["frames"]
    hashed = aseprite_json(_manifest(), fmt="json-hash")["frames"]

    assert isinstance(hashed, dict)
    assert list(hashed) == [e["filename"] for e in array]
    for entry in hashed.values():
        assert set(entry) == ASEPRITE_FRAME_KEYS - {"filename"}
    assert hashed["2"]["frame"] == array[2]["frame"]


def test_split_states_match_flame_from_aseprite_data_contract() -> None:
    """Flame reads `jsonData['frames'] as Map`, per value `frame.{x,y,w,h}` as
    int and `duration` as int, and NEVER reads meta.frameTags — one file must
    therefore be exactly one animation (sprite_animation.dart, flame-engine)."""
    docs = split_state_jsons(_manifest(), fmt="json-hash")

    assert set(docs) == {"idle", "walk"}
    walk = docs["walk"]["frames"]
    assert isinstance(walk, dict)
    # local indices from "0", not global ones — this file IS the animation.
    assert list(walk) == ["0", "1", "2"]
    for entry in walk.values():
        assert all(isinstance(entry["frame"][k], int) for k in ("x", "y", "w", "h"))
        assert isinstance(entry["duration"], int)
    # the shared-cell instance repeats its rect under a new local key.
    assert walk["0"]["frame"] == walk["2"]["frame"]
    # every split doc points at the same atlas image.
    assert {doc["meta"]["image"] for doc in docs.values()} == {"sprite-sheet-alpha.png"}
    assert docs["idle"]["meta"]["frameTags"] == [
        {"name": "idle", "from": 0, "to": 1, "direction": "forward"},
    ]


def test_export_from_a_real_composed_run(fixture_run_dir: Path) -> None:
    extract = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert extract.returncode == 0, extract.stdout + extract.stderr
    compose = run_script("compose_sprite_atlas.py", "--run-dir", str(fixture_run_dir))
    assert compose.returncode == 0, compose.stdout + compose.stderr

    proc = subprocess.run(
        [sys.executable, "-m", "sprite_gen.export_aseprite", "--run-dir", str(fixture_run_dir)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    exported = json.loads((fixture_run_dir / "exports" / "aseprite.json").read_text(encoding="utf-8"))
    manifest = json.loads((fixture_run_dir / "manifest.json").read_text(encoding="utf-8"))

    # The referenced image is the run's actual atlas, and the geometry agrees.
    assert (fixture_run_dir / exported["meta"]["image"]).is_file()
    assert exported["meta"]["size"] == {
        "w": manifest["frame_layout"]["sheetWidth"],
        "h": manifest["frame_layout"]["sheetHeight"],
    }
    # Every playback instance of every state is present, tagged, and in-bounds.
    total = sum(len(rects) for rects in manifest["frame_layout"]["rows"].values())
    assert len(exported["frames"]) == total
    assert {t["name"] for t in exported["meta"]["frameTags"]} == set(manifest["frame_layout"]["rows"])
    for tag in exported["meta"]["frameTags"]:
        assert tag["to"] - tag["from"] + 1 == manifest["animation"]["rows"][tag["name"]]["frames"]
    for entry in exported["frames"]:
        rect = entry["frame"]
        assert 0 <= rect["x"] and rect["x"] + rect["w"] <= exported["meta"]["size"]["w"]
        assert 0 <= rect["y"] and rect["y"] + rect["h"] <= exported["meta"]["size"]["h"]

    # The Flame path from the same composed run: one hash-format file per state.
    proc = subprocess.run(
        [sys.executable, "-m", "sprite_gen.export_aseprite", "--run-dir", str(fixture_run_dir),
         "--format", "json-hash", "--split-states"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for state, rects in manifest["frame_layout"]["rows"].items():
        doc = json.loads((fixture_run_dir / "exports" / "aseprite" / f"{state}.json")
                         .read_text(encoding="utf-8"))
        assert isinstance(doc["frames"], dict)
        assert list(doc["frames"]) == [str(i) for i in range(len(rects))]
