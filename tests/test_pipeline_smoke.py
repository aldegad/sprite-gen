# SPDX-License-Identifier: Apache-2.0
"""Quickstart smoke: prepare -> extract -> compose on the golden fixture."""

import json
from pathlib import Path

import pytest
from PIL import Image

from conftest import run_script


def test_prepare_writes_run_contract(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    result = run_script(
        "prepare_sprite_run.py",
        "--out-dir", str(out_dir),
        "--character-id", "smokebot",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["kind"] == "sprite-gen-request"
    assert request["character"]["id"] == "smokebot"
    for state in request["states"]:
        assert (out_dir / "prompts" / f"{state}.txt").is_file()
        assert (out_dir / "references" / "layout-guides" / f"{state}.png").is_file()


def test_prepare_records_fit_cli_flags_in_request(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    result = run_script(
        "prepare_sprite_run.py",
        "--out-dir", str(out_dir),
        "--character-id", "smokebot",
        "--request-json", '{"fit": {"resample": "lanczos", "palette_size": 24}}',
        "--fit-resample", "kcentroid",
        "--fit-align-x", "foot-centroid",
        "--fit-pixel-perfect",
        "--fit-logical-height", "64",
        "--fit-outline", "0.5",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    request = json.loads((out_dir / "sprite-request.json").read_text(encoding="utf-8"))
    assert request["fit"] == {
        "resample": "kcentroid",  # CLI overrides the --request-json value
        "align_x": "foot-centroid",
        "pixel_perfect": True,
        "logical_height": 64,
        "outline": 0.5,
        "palette_size": 24,  # untouched --request-json key survives the merge
    }


def test_extract_then_compose_bakes_atlas(fixture_run_dir: Path) -> None:
    extract = run_script("extract_sprite_row_frames.py", "--run-dir", str(fixture_run_dir))
    assert extract.returncode == 0, extract.stdout + extract.stderr

    compose = run_script("compose_sprite_atlas.py", "--run-dir", str(fixture_run_dir))
    assert compose.returncode == 0, compose.stdout + compose.stderr

    request = json.loads((fixture_run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    cell = request["cell"]
    states = list(request["states"])
    max_frames = max(int(entry["frames"]) for entry in request["states"].values())

    with Image.open(fixture_run_dir / "sprite-sheet-alpha.png") as atlas:
        assert atlas.size == (max_frames * cell["width"], len(states) * cell["height"])

    manifest = json.loads((fixture_run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["curation_applied"] is False
    layout_rows = manifest["frame_layout"]["rows"]
    assert set(layout_rows) == set(states)
    for row_index, state in enumerate(states):
        cells = layout_rows[state]
        assert len(cells) == int(request["states"][state]["frames"])
        for column, rect in enumerate(cells):
            assert rect == {
                "x": column * cell["width"],
                "y": row_index * cell["height"],
                "w": cell["width"],
                "h": cell["height"],
            }


def test_subset_reextract_preserves_other_states(fixture_run_dir: Path) -> None:
    """A subset `--states` re-extract (auto-correction / single-row regeneration) preserves the
    frames AND manifest rows of the states it does not rebuild, so a later compose still finds
    every state (SSoT/Idempotency: a single-row re-extract must not delete other states)."""
    import hashlib

    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    states = sorted(json.loads((run / "sprite-request.json").read_text())["states"])
    assert len(states) >= 2
    target, others = states[0], states[1:]

    before = {}
    for state in others:
        for frame in sorted((run / "frames" / state).glob("frame-*.png")):
            if not frame.name.endswith(".plain.png"):
                before[str(frame.relative_to(run))] = hashlib.sha256(frame.read_bytes()).hexdigest()
    assert before

    r = run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", target)
    assert r.returncode == 0, r.stdout + r.stderr

    for rel, digest in before.items():                       # non-target frame bytes preserved
        assert (run / rel).is_file(), f"{rel} deleted by subset re-extract"
        assert hashlib.sha256((run / rel).read_bytes()).hexdigest() == digest
    manifest = json.loads((run / "frames" / "frames-manifest.json").read_text())
    assert sorted(row["state"] for row in manifest["rows"]) == states  # manifest keeps all rows
    compose = run_script("compose_sprite_atlas.py", "--run-dir", str(run))
    assert compose.returncode == 0, compose.stdout + compose.stderr  # was exit 1 before the fix


def test_failed_subset_reextract_preserves_prior_generation(fixture_run_dir: Path) -> None:
    """A failed subset re-extract must leave the prior COMPLETE frames generation byte-intact
    and discard staging (Atomicity: publish only on success). Otherwise a single-row
    regeneration that fails would destroy the previously-good frames for that row."""
    import hashlib

    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    target = sorted(json.loads((run / "sprite-request.json").read_text())["states"])[0]

    before = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (run / "frames").rglob("frame-*.png")}
    prior_manifest = (run / "frames" / "frames-manifest.json").read_bytes()

    (run / "raw" / f"{target}.png").unlink()                  # break the target's raw strip
    r = run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", target)
    assert r.returncode == 1                                  # failed loudly

    for rel, digest in before.items():                        # prior generation byte-intact
        assert (run / rel).is_file(), f"{rel} destroyed by a failed subset re-extract"
        assert hashlib.sha256((run / rel).read_bytes()).hexdigest() == digest
    assert (run / "frames" / "frames-manifest.json").read_bytes() == prior_manifest
    assert not (run / ".frames.sg-staging").exists()          # staging discarded, no leak
    assert run_script("compose_sprite_atlas.py", "--run-dir", str(run)).returncode == 0


def test_failed_reextract_signal_reaches_correction_loop_consumer(fixture_run_dir: Path) -> None:
    """The automatic correction loop drives error-driven regeneration off inspect's per-state
    errors. A failed RE-extract keeps the prior good frames byte-intact, so inspect's own
    frame-count check sees nothing wrong — the ONLY channel for 'this state's re-extract failed,
    and why' is extract-failure.json (frames/ stays complete-only). This proves the strict-
    atomicity fix keeps that signal wired, so auto-correction never goes blind."""
    from sprite_gen.inspect import _manifest_state_notes

    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    target = sorted(json.loads((run / "sprite-request.json").read_text())["states"])[0]

    (run / "raw" / f"{target}.png").unlink()                  # break only this state's raw strip
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", target).returncode == 1

    assert (run / "frames" / target).is_dir()                 # prior good frames intact (re-extract)
    assert (run / "extract-failure.json").is_file()           # signal recorded OUTSIDE frames/
    _row, errors, _warnings = _manifest_state_notes(run, target)
    assert errors and all(e.startswith(f"{target}:") for e in errors), errors  # consumer reads it


def test_successful_extract_clears_prior_failure_evidence(fixture_run_dir: Path) -> None:
    """A success resolves any prior failure: extract-failure.json must be removed so inspect and
    the correction loop never re-flag a now-good state with a stale signal (Consistency)."""
    from sprite_gen.inspect import _manifest_state_notes

    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    target = sorted(json.loads((run / "sprite-request.json").read_text())["states"])[0]

    raw = run / "raw" / f"{target}.png"
    saved = raw.read_bytes()
    raw.unlink()
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", target).returncode == 1
    assert (run / "extract-failure.json").is_file()           # failure recorded

    raw.write_bytes(saved)                                    # fix the input, re-extract succeeds
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", target).returncode == 0
    assert not (run / "extract-failure.json").exists()        # success cleared the stale signal
    _row, errors, _warnings = _manifest_state_notes(run, target)
    assert not errors                                         # consumer sees a clean state again


def test_subset_success_preserves_other_state_failure(fixture_run_dir: Path) -> None:
    """A subset --states extract is the formal single-row / auto-correction path: it only
    determines the outcome of the states it targets. A SUCCESS on one state must never erase
    another state's still-unresolved failure — else the correction loop goes blind to it
    (Consistency / No Silent Fallback). walk-success must keep idle's failure signal."""
    from sprite_gen.inspect import _manifest_state_notes

    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    (run / "raw" / "idle.png").unlink()                       # break only idle
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", "idle").returncode == 1
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", "walk").returncode == 0

    assert (run / "extract-failure.json").is_file()           # walk-success did NOT delete it
    _row, idle_errors, _w = _manifest_state_notes(run, "idle")
    assert idle_errors and all(e.startswith("idle:") for e in idle_errors), idle_errors  # idle still failed
    _row, walk_errors, _w = _manifest_state_notes(run, "walk")
    assert not walk_errors                                    # walk is resolved — no stale signal


def test_consecutive_state_failures_accumulate_in_evidence(fixture_run_dir: Path) -> None:
    """A new failure must not overwrite a different state's prior unresolved failure — the
    evidence merges per state. Break idle, then walk; both failures must stay visible."""
    from sprite_gen.inspect import _manifest_state_notes

    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    (run / "raw" / "idle.png").unlink()
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", "idle").returncode == 1
    (run / "raw" / "walk.png").unlink()
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", "walk").returncode == 1

    _row, idle_errors, _w = _manifest_state_notes(run, "idle")
    _row, walk_errors, _w = _manifest_state_notes(run, "walk")
    assert idle_errors, "idle failure was overwritten by the later walk failure"
    assert walk_errors, "walk failure missing"


def test_commit_generation_rolls_back_both_surfaces_on_evidence_failure(tmp_path, monkeypatch):
    """The frames swap and the failure-evidence update are ONE publish transaction: if the
    evidence write fails, BOTH canonical surfaces must roll back to their pre-commit state — the
    run must never end with a new frames generation beside a stale failure record (Atomicity)."""
    from sprite_gen import extract

    run = tmp_path / "run"
    for st, tag in (("idle", b"IDLE_OLD"), ("walk", b"WALK_OLD")):   # prior complete generation
        (run / "frames" / st).mkdir(parents=True)
        (run / "frames" / st / "frame-0.png").write_bytes(tag)
    prior_evidence = {"ok": False, "errors": ["idle: broke"], "warnings": [], "rows": []}
    (run / "extract-failure.json").write_text(json.dumps(prior_evidence), encoding="utf-8")
    staging = run / ".frames.sg-staging"                              # new generation (walk rebuilt)
    for st, tag in (("idle", b"IDLE_OLD"), ("walk", b"WALK_NEW")):
        (staging / st).mkdir(parents=True)
        (staging / st / "frame-0.png").write_bytes(tag)

    real_write = extract.atomic_write_text
    def flaky(target, text):
        if Path(target).name == "extract-failure.json":
            raise OSError("injected evidence write failure")
        return real_write(target, text)
    monkeypatch.setattr(extract, "atomic_write_text", flaky)

    with pytest.raises(OSError):
        # walk success with idle still failed -> merged errors [idle] -> evidence WRITE -> boom
        extract._commit_generation(run, staging, {"ok": True, "errors": [], "warnings": [], "rows": []},
                                   {"walk"}, {"idle", "walk"})

    assert (run / "frames" / "walk" / "frame-0.png").read_bytes() == b"WALK_OLD"   # not the new gen
    assert (run / "frames" / "idle" / "frame-0.png").read_bytes() == b"IDLE_OLD"
    assert json.loads((run / "extract-failure.json").read_text()) == prior_evidence  # evidence intact
    assert not (run / ".frames.sg-backup").exists()                  # no backup leak
    assert not (run / ".extract-failure.sg-backup").exists()


# Broken canonical records that must all fail loud, NOT be read as empty/absent. Beyond
# unparseable JSON: a syntactically-valid `{}`, a missing required field, a wrong `ok`, an
# empty failure `errors`, or a row without a `state` are all schema corruption (No Silent
# Fallback — an existing-but-broken record never reads as "all clear").
_BROKEN_MANIFESTS = [
    "{ malformed",                                                    # unparseable
    "{}",                                                             # empty dict (missing all)
    '{"ok": false, "rows": [], "errors": [], "warnings": []}',       # wrong ok (must be true)
    '{"ok": true, "errors": [], "warnings": []}',                    # missing rows
    '{"ok": true, "rows": [{"files": []}], "errors": [], "warnings": []}',  # row without state
]
_BROKEN_EVIDENCE = [
    "{ malformed",                                                    # unparseable
    "{}",                                                             # empty dict (missing all)
    '{"ok": true, "errors": ["idle: x"], "warnings": [], "rows": []}',  # wrong ok (must be false)
    '{"ok": false, "errors": [], "warnings": [], "rows": []}',       # empty errors
    '{"ok": false, "warnings": [], "rows": []}',                     # missing errors
]


@pytest.mark.parametrize("payload", _BROKEN_EVIDENCE)
def test_commit_fails_loud_on_broken_failure_evidence(fixture_run_dir: Path, payload: str) -> None:
    """WRITER path: a subset --states success reads the prior extract-failure.json to merge. A
    broken record (unparseable OR valid-but-wrong-schema, incl. `{}`) must fail loud, never be
    read as 'no failures' and silently dropped on the successful publish (No Silent Fallback)."""
    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    (run / "extract-failure.json").write_text(payload, encoding="utf-8")

    r = run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", "walk")
    assert r.returncode != 0, f"commit silently accepted broken evidence: {payload!r}"
    assert "failure evidence" in (r.stdout + r.stderr).lower()
    assert (run / "extract-failure.json").read_text(encoding="utf-8") == payload  # not deleted/rewritten


@pytest.mark.parametrize("payload", _BROKEN_EVIDENCE)
def test_inspect_fails_loud_on_broken_failure_evidence(fixture_run_dir: Path, payload: str) -> None:
    """READER path: the correction loop reads per-state failures through inspect_run. A broken
    extract-failure.json must fail loud on the read too — never be read as 'no failures' — or an
    unresolved-failure run is judged all-clear (No Silent Fallback)."""
    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    (run / "extract-failure.json").write_text(payload, encoding="utf-8")

    r = run_script("inspect_sprite_run.py", "--run-dir", str(run), "--no-write")
    assert r.returncode != 0, f"inspect silently accepted broken evidence: {payload!r}"
    assert "failure evidence" in (r.stdout + r.stderr).lower()
    assert (run / "extract-failure.json").exists()                   # not silently removed


@pytest.mark.parametrize("payload", _BROKEN_MANIFESTS)
def test_subset_reextract_fails_loud_on_broken_prior_manifest(fixture_run_dir: Path, payload: str) -> None:
    """WRITER path: a subset --states re-extract seeds untouched states from the prior complete
    generation. A broken prior manifest (unparseable OR `{}`/missing-rows/wrong-ok/row-without-
    state) must fail loud BEFORE staging — never be read as 'no prior rows' and then publish an
    incomplete manifest that disagrees with the carried frame tree (No Silent Fallback /
    Consistency / Atomicity). The prior generation stays byte-intact and no staging leaks."""
    import hashlib

    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    (run / "frames" / "frames-manifest.json").write_text(payload, encoding="utf-8")
    before = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (run / "frames").rglob("*") if p.is_file()}

    r = run_script("extract_sprite_row_frames.py", "--run-dir", str(run), "--states", "walk")
    assert r.returncode != 0, f"subset re-extract silently accepted broken manifest: {payload!r}"
    assert "frames manifest" in (r.stdout + r.stderr).lower()
    after = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (run / "frames").rglob("*") if p.is_file()}
    assert after == before, "prior generation was mutated by a failed subset re-extract"
    assert not (run / ".frames.sg-staging").exists()                 # failed before staging created


def test_inspect_fails_loud_on_broken_frames_manifest(fixture_run_dir: Path) -> None:
    """READER path: inspect reads the frames manifest too; an `{}`-broken manifest must fail loud,
    not read as an empty generation (No Silent Fallback)."""
    run = fixture_run_dir
    assert run_script("extract_sprite_row_frames.py", "--run-dir", str(run)).returncode == 0
    (run / "frames" / "frames-manifest.json").write_text("{}", encoding="utf-8")

    r = run_script("inspect_sprite_run.py", "--run-dir", str(run), "--no-write")
    assert r.returncode != 0, "inspect silently accepted a broken frames manifest"
    assert "frames manifest" in (r.stdout + r.stderr).lower()
