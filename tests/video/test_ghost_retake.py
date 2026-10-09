# SPDX-License-Identifier: Apache-2.0
"""A filmed ghost kept for want of a clean frame beside it is a take to film again (docs/loop-repair.md
section 4): `video-cycle-align` names it in `retake`, reason `filmed-ghost`, beside `held-drawings` in
one record, and says what became of each cell as it was.

The fixtures are the synthetic ones of tests/video/test_ghost_screen.py (legs with a band of 0.3
coverage between the feet, a stand-in RIFE that follows the motion) and tests/video/test_held_drawings.py
(a body and a foot going round, drawn on twos). The one real-RIFE case is marked `real_rife`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.video import align, rife
from sprite_gen.video import loop as loop_mod
from sprite_gen.video.interpolation_quality import GHOST_WARN
from tests.video import test_ghost_screen as tg
from tests.video import test_held_drawings as th


def _set(tmp_path: Path, loops: tuple[tuple[str, int, tuple[int, ...]], ...]) -> list[Path]:
    """Loops of the walk with legs, each `(name, length, filmed ghosts)`, cut as filmed."""
    dirs = []
    for name, length, ghosts in loops:
        tg._cut(tg._keyed(tmp_path, name, length, ghosts), tmp_path / name, length)
        dirs.append(tmp_path / name)
    return dirs


def _row(report: dict, name: str) -> dict:
    return next(r for r in report["loops"] if Path(r["dir"]).name == name)


def _kept(row: dict) -> list[dict]:
    return [g for g in row["ghost_at"] if g["taken"] == g["source"]]


def _cell(row: dict, k: int) -> Image.Image:
    return Image.open(Path(row["dir"]) / "cycle" / f"frame-{k:03d}.png")


def test_resample_says_why_each_ghost_was_kept():
    """Ghosts 2, 3, 4 in a row: 3 has no clean frame beside it, under any `between`; rife keeps 2 and 4
    beside a clean one, its choice."""
    frames = tg._stride(8)
    for k in (2, 3, 4):
        frames[k] = tg._band(frames[k])
    _, facts = align.resample(frames, 8, None)
    assert [(g["at"], g["taken"], g.get("kept")) for g in facts["ghost_at"]] == [(2, 1, None), (3, 3, "no-clean-frame"), (4, 5, None)]
    _, facts = align.resample(frames, 8, None, between="rife")
    assert [(g["at"], g["taken"], g.get("kept")) for g in facts["ghost_at"]] == [
        (2, 2, "between-rife"), (3, 3, "no-clean-frame"), (4, 4, "between-rife")]
    _, facts = align.resample(frames, 16, None, between="nearest")  # times 0, .5, 1, …: a made time takes the nearer
    assert [(g["at"], g["source"], g["taken"], g.get("kept")) for g in facts["ghost_at"]] == [
        (3, 2, 1, None), (4, 2, 1, None), (5, 3, 3, "no-clean-frame"), (6, 3, 3, "no-clean-frame"), (7, 4, 4, "no-clean-frame"),
        (8, 4, 5, None)]


def test_a_ghost_kept_at_a_whole_time_is_a_take_to_film_again(tmp_path, capsys):
    """A1: S 20 and E 20, nothing resampled. E's frames 9, 10, 11 are filmed ghosts in a row: 9 and 11
    give way, 10 has no clean frame beside it and is delivered. Before, `retake` stayed empty and only
    a line said to film it again, so a pipeline that films again on `retake` never did."""
    dirs = _set(tmp_path, (("S", 20, ()), ("E", 20, (9, 10, 11))))
    capsys.readouterr()
    assert align.run(loop_dir=dirs, report=tmp_path / "align.json") == 0  # no frame made: no RIFE wanted
    captured = capsys.readouterr()
    report = json.loads((tmp_path / "align.json").read_text())
    assert report["applied"] is True
    e, s = _row(report, "E"), _row(report, "S")
    assert e["retake"] is not None and e["retake"]["reason"] == "filmed-ghost"  # before: null, and only a line
    assert [g["source"] for g in e["ghost_at"]] == [9, 10, 11]
    (kept,) = _kept(e)
    assert kept["source"] == 10 and kept["kept"] == "no-clean-frame"
    assert rife.ghost(_cell(e, kept["at"])) > GHOST_WARN  # the cell shows the ghost
    assert e["retake"] == {"reason": "filmed-ghost", "reasons": ["filmed-ghost"],
                           "ghost": {"kept": [kept["at"]], "sources": [10], "ghost_max": kept["ghost"], "limit": GHOST_WARN},
                           "limits": {"ghost_kept_min": 1}}
    assert "hold" not in e["retake"] and s["retake"] is None  # the held numbers only where that reason applies
    assert report["retake"] == [{"dir": e["dir"], "name": "walk", **e["retake"]}]
    assert json.loads(captured.out)["retake"] == report["retake"]
    line = f"video-cycle-align: warning: {e['dir']}: film this direction again (filmed-ghost) — frame(s) {kept['at']} show source frame(s) 10 of cycle.source/"
    assert line in captured.err
    meta = json.loads((Path(e["dir"]) / "walk.strip.json").read_text())
    assert meta["cycle_align"]["retake"] == e["retake"] and meta["cycle_align"]["ghost_at"] == e["ghost_at"]


def _made_time(tmp_path: Path, interpolate) -> dict:
    """A2: S 22 and E 24 to 23. E's time 5.217 falls between its filmed ghosts 5 and 6: the frame made
    there carries them, the nearer (5) is a ghost and so is the other — kept. E is drawn every frame."""
    return align.align_set(_set(tmp_path, (("S", 22, ()), ("E", 24, (5, 6)))), interpolate=interpolate, length=23,
                           report_path=tmp_path / "align.json")


def _check_made_time(report: dict) -> None:
    e = _row(report, "E")
    assert e["retake"] is not None and e["retake"]["reason"] == "filmed-ghost"  # before: null (E holds no drawing)
    (kept,) = _kept(e)
    assert kept["source"] == 5 and kept["kept"] == "no-clean-frame"
    made = next(m for m in e["smear"] if m["at"] == kept["at"])
    assert made["method"] == "nearest" and "ghost" in made["faults"]
    assert e["drawings"]["hold"] == 1 and "hold" not in e["retake"] and e["retake"]["reasons"] == ["filmed-ghost"]
    assert e["retake"]["ghost"]["kept"] == [kept["at"]] and e["retake"]["ghost"]["sources"] == [5]
    assert [r["reason"] for r in report["retake"]] == ["filmed-ghost"]
    # The fault line says what became of the cell: kept, the nearer frame a ghost and the other too — not
    # that the nearer source frame was taken, as if that settled it. (Both loops are `walk`: E's line is
    # the one that reads its made frame's ghost.)
    def fault_line(m: dict) -> str:
        (line,) = [w for w in report["warnings"]
                   if w.startswith(f"walk: frame {m['at']}: RIFE's frame") and f"band over {100 * m['ghost']:.2f} %" in w]
        return line

    assert fault_line(made).endswith("so the nearer source frame, a filmed ghost, was kept there, the frames beside it being filmed "
                                     "ghosts too (--between auto)")
    others = [m for g in e["ghost_at"] if g["taken"] != g["source"] for m in e["smear"] if m["at"] == g["at"] and m["faults"]]
    assert others  # the other ghost, made beside: the frame on its other side was taken
    assert all("the source frame on its other side was taken there, the nearer being a filmed ghost" in fault_line(m) for m in others)
    assert any(f"frame {kept['at']}: source frame 5 is a filmed ghost" in w and w.endswith("kept — film this direction again")
               for w in report["warnings"])


def test_a_ghost_kept_at_a_made_time_is_a_take_to_film_again_and_its_fault_line_says_so(tmp_path):
    _check_made_time(_made_time(tmp_path, tg._follow))


@pytest.mark.real_rife
def test_real_rife_a_ghost_kept_at_a_made_time_is_a_take_to_film_again(tmp_path):
    """A2 through the real RIFE: its frame between the two ghosts carries them, and the cell keeps the ghost."""
    _check_made_time(_made_time(tmp_path, rife.Rife()))


def test_a_held_loop_that_keeps_a_ghost_carries_both_reasons_held_drawings_first(tmp_path):
    """A3: a cycle of 16 on twos stretched to 24 is a held-drawings retake; its frames 6 to 9 are filmed
    ghosts (two drawings' pairs), and the cells with no clean frame beside them keep them. One record."""
    clip = th._clip(16, 24, 2)
    for k in range(len(clip)):
        if k % 16 in (6, 7, 8, 9):
            clip[k] = tg._band(clip[k], box=(0.25, 0.80, 0.75, 0.95))
    loop_mod.run_loop(th._keyed(tmp_path, "NE", clip), tmp_path / "NE", fps=24.0, state="walk", min_len=None, max_len=None,
                      n_out=None, seam_max=1000.0, name="walk", report_path=None, cycle_mode="fixed", start=0, length=16,
                      anchor="none", repair="off")
    report = align.align_set([tmp_path / "NE", th._cut(tmp_path, "S", 24, 1)], interpolate=th._cross_fade, length=24)
    ne = _row(report, "NE")
    again = ne["retake"]
    assert again["reason"] == "held-drawings" and again.get("reasons") == ["held-drawings", "filmed-ghost"]  # before: held alone
    kept = _kept(ne)
    assert kept and all(g["kept"] == "no-clean-frame" for g in kept)
    assert again["hold"] == 2 and again["drawings"] == 8 and again["drawings_per_second"] == pytest.approx(8.0)
    assert again["ghost"] == {"kept": [g["at"] for g in kept], "sources": sorted({g["source"] for g in kept}),
                              "ghost_max": max(g["ghost"] for g in kept), "limit": GHOST_WARN}
    assert again["ghost"]["sources"] == [7, 8, 9]  # 6 gives way to the clean 5; 9 to the clean 10 at one of its cells
    assert again["limits"] == {"drawings_per_second_min": align.RETAKE_DRAWINGS_MIN, "unmade_min": align.RETAKE_UNMADE_MIN,
                               "ghost_kept_min": align.RETAKE_GHOST_KEPT_MIN}
    assert [(r["reason"], r["reasons"]) for r in report["retake"]] == [("held-drawings", ["held-drawings", "filmed-ghost"])]
    assert _row(report, "S")["retake"] is None
    for reason in ("held-drawings", "filmed-ghost"):
        assert len([w for w in report["warnings"] if f"film this direction again ({reason})" in w]) == 1
    # a made frame given way at a kept cell says it was kept (S, the other `walk`, is not resampled)
    lines = [w for g in kept for w in report["warnings"] if w.startswith(f"walk: frame {g['at']}: RIFE's frame")]
    assert lines and all(w.endswith("being filmed ghosts too (--between auto)") for w in lines)


def test_a_held_loop_with_no_ghost_carries_its_one_reason(tmp_path):
    report = align.align_set([th._cut(tmp_path, "NE", 16, 2), th._cut(tmp_path, "S", 24, 1)], interpolate=th._cross_fade, length=24)
    again = _row(report, "NE")["retake"]
    assert again["reason"] == "held-drawings" and again.get("reasons") == ["held-drawings"] and "ghost" not in again
    assert again["limits"] == {"drawings_per_second_min": align.RETAKE_DRAWINGS_MIN, "unmade_min": align.RETAKE_UNMADE_MIN}


def test_a_ghost_rife_keeps_beside_a_clean_frame_is_no_reason(tmp_path):
    """Under --between rife a filmed ghost is kept as filmed: beside a clean frame that is the choice of
    whoever aligned it, and no reason to film again; with no clean frame beside it, it is one."""
    dirs = _set(tmp_path, (("S", 20, (7,)), ("E", 20, (9, 10, 11))))
    report = align.align_set(dirs, between="rife", report_path=tmp_path / "align.json")
    s, e = _row(report, "S"), _row(report, "E")
    assert e["retake"] is not None and s["retake"] is None
    assert [g["kept"] for g in s["ghost_at"]] == ["between-rife"]
    assert [(g["source"], g["kept"]) for g in e["ghost_at"]] == [(9, "between-rife"), (10, "no-clean-frame"), (11, "between-rife")]
    assert e["retake"]["reason"] == "filmed-ghost" and e["retake"]["ghost"]["sources"] == [10]
    assert [Path(r["dir"]).name for r in report["retake"]] == ["E"]
    rife_lines = [w for w in report["warnings"] if "; kept (--between rife)" in w]
    assert len(rife_lines) == 3 and not any(" source frame 10 " in w for w in rife_lines)
    assert any("source frame 10 is a filmed ghost" in w and w.endswith("kept — film this direction again") for w in report["warnings"])
