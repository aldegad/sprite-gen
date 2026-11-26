# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen breathe --avoid-region` — 손을 비키는 숨 박자.

손이 가슴 앞을 오르내리는 idle 에서 숨 박자(숨 수 또는 들숨 꼭대기 칸)를 칸에서 고른다: 손과 그 위 몸을 따라가
손의 제 오름을 읽고, 들숨 꼭대기에서 가슴이 손 위에 있는 박자 가운데 숨이 손을 따라가지 않고 몸과 같이 가는 것을
고른다. 꼭대기를 민 박자는 문턱(MARGIN)보다 더 나을 때만. 고른 박자와 후보는 breathe.json `beat` 에 남고, 그 파일을
받는 Sway 레이어가 같은 박자로 흔든다. 합성 흉상(질감 있는 몸, 다른 질감의 손)으로 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.effects import breath_beat, breathe

from tests.effects.test_breathe_smooth import _bust

H, N, FPS = 160, 48, 12                  # 4 초 루프 — 숨 하나(4 초) 또는 둘(2 초)이 한 숨 1.5~4 초에 든다
HANDS = (0.33, 0.74, 0.67, 0.89)         # 손 상자 (그림 비율) — 가슴 아래 앞
HAND_ROWS, HAND_COLS = (122, 141), (45, 84)
LIFT_DEPTH = 0.025
FILES = [f"frame-{k:03d}.png" for k in range(N)]


def _figure(hand_rise: int = 0, body_rise: int = 0) -> Image.Image:
    """질감 있는 흉상과 그 앞의 손 — 손은 몸에 대해 `hand_rise` px, 그림 전체는 `body_rise` px 오른다(위 양수)."""
    a = np.asarray(_bust(H)).copy()
    body = a[..., 3] > 0
    a[..., 1][body] = np.clip(a[..., 1][body].astype(int)
                              + np.random.default_rng(5).integers(-25, 26, a.shape[:2])[body], 0, 255).astype(np.uint8)
    y0, y1 = HAND_ROWS[0] - hand_rise, HAND_ROWS[1] - hand_rise
    hand = np.random.default_rng(9).integers(0, 256, (y1 - y0, HAND_COLS[1] - HAND_COLS[0], 3)).astype(np.uint8)
    a[y0:y1, HAND_COLS[0]:HAND_COLS[1], :3] = hand
    a[y0:y1, HAND_COLS[0]:HAND_COLS[1], 3] = 255
    if body_rise:
        a = np.concatenate([a[body_rise:], np.repeat(a[-1:], body_rise, axis=0)])    # 아래 변에 닿은 채로 오른다
    return Image.fromarray(a, "RGBA")


def _loop(folder: Path, hands: list[int], body: list[int] | None = None) -> Path:
    folder.mkdir(parents=True)
    for k, name in enumerate(FILES):
        _figure(hands[k], 0 if body is None else body[k]).save(folder / name)
    return folder


def _wave(cfg: dict) -> np.ndarray:
    inhale = breathe.smooth_inhale(cfg)
    return np.asarray([breathe.smooth_wave(p, inhale) for p in breathe.fit_breathe_pattern(N, {"mode": "smooth", **cfg})])


def _peaks(w: np.ndarray) -> list[int]:
    return [i for i in range(N) if w[i] > 0.9 and w[i] >= w[i - 1] and w[i] >= w[(i + 1) % N]]


def _bake(src: Path, out: Path, *extra: str) -> dict:
    assert cli.main(["breathe", str(src), str(out), "--mode", "smooth", "--depth", str(LIFT_DEPTH), *extra]) == 0
    return json.loads((out / "breathe.json").read_text(encoding="utf-8"))


def _avoid(src: Path, out: Path, *extra: str) -> dict:
    return _bake(src, out, "--avoid-region", ",".join(f"{v:g}" for v in HANDS), "--fps", str(FPS), *extra)


BUMP = [round(5 * max(0.0, 1 - abs(k - 34) / 5)) for k in range(N)]   # 손이 34 칸 둘레에서 5 px 오른다


def test_the_in_breath_peaks_miss_the_hands_big_rise(tmp_path) -> None:
    report = _avoid(_loop(tmp_path / "loop", BUMP), tmp_path / "out")
    beat = report["beat"]
    assert max(abs(a - b) for a, b in zip(beat["hands_rise_px"], BUMP)) <= 1, "손을 따라가지 못했다"
    rows = {row["breaths"]: row for row in beat["candidates"]}
    assert set(rows) == {1, 2} and rows[2]["keeps"] is False and rows[1]["keeps"] is True    # 숨 둘은 34 칸 꼭대기가 손 밑
    chosen = beat["chosen"]
    assert report["config"]["breath_peaks" if "breath_peaks" in chosen else "breaths"] == next(iter(chosen.values()))
    assert report["phases"] == breathe.fit_breathe_pattern(N, report["config"])
    peaks = _peaks(_wave(chosen))
    assert peaks and all(not 34 - 5 - breath_beat.NEAR <= p <= 34 + 5 + breath_beat.NEAR for p in peaks)
    lift = beat["lift_px"]
    assert all(lift * _wave(chosen)[p] >= max(BUMP[(p + j) % N] for j in range(-2, 3)) for p in peaks)


def test_hands_that_move_against_the_breath_keep_the_evenly_spaced_breaths(tmp_path) -> None:
    against = [round(4 * (1 - w)) for w in _wave({"breaths": 2})]
    beat = _avoid(_loop(tmp_path / "loop", against), tmp_path / "out")["beat"]
    assert beat["chosen"] == {"breaths": 2}
    rows = {row["breaths"]: row for row in beat["candidates"]}
    assert rows[2]["corr_hands"] < -0.9 and rows[2]["score"] > rows[1]["score"]
    assert beat["nudged_best"]["score"] <= rows[2]["score"] + breath_beat.MARGIN


def test_each_beat_is_read_on_the_rows_in_breath_share(tmp_path) -> None:
    """`--inhale` 이 있으면 후보마다 그 들숨 몫의 곡선으로 꼭대기와 점수를 잰다 — 굽기가 쓸 곡선 그대로다."""
    report = _avoid(_loop(tmp_path / "loop", BUMP), tmp_path / "out", "--inhale", "0.3")
    assert report["config"]["inhale"] == 0.3
    for row in report["beat"]["candidates"]:
        k = row["breaths"]
        assert row["peaks"] == _peaks(_wave({"breaths": k, "inhale": 0.3})) != _peaks(_wave({"breaths": k}))


def test_the_hands_own_rise_is_read_against_the_band_just_above_them(tmp_path) -> None:
    """몸 상자를 주지 않으면 손 상자와 같은 열, 바로 위 띠 — 몸이 숨과 같이 오르내리면 손의 제 오름은 0 이고 숨은 몸을 따른다."""
    with_body = [round(2 * w) for w in _wave({"breaths": 2})]
    beat = _avoid(_loop(tmp_path / "loop", [0] * N, with_body), tmp_path / "out")["beat"]
    band = breath_beat.anchor_band(HANDS)
    assert beat["avoid_anchor"] == list(band)
    x0, y0, x1, y1 = HANDS
    assert band[0] == x0 and band[2] == x1 and band[3] < y0 and band[3] - band[1] == pytest.approx(0.65 * (y1 - y0) - 0.02)
    assert max(abs(v) for v in beat["hands_rise_px"]) <= 1
    assert max(abs(a - b) for a, b in zip(beat["body_rise_px"], with_body)) <= 1
    assert beat["chosen"] == {"breaths": 2}


def test_a_nudged_peak_is_taken_only_when_it_is_more_than_the_margin_better() -> None:
    evenly = [{"breaths": 1, "score": 0.5, "keeps": True, "margin_px": 1.0},
              {"breaths": 2, "score": 0.9, "keeps": False, "margin_px": -1.0}]

    def nudged(score: float, keeps: bool = True) -> list[dict]:
        return [{"breath_peaks": [16], "score": score, "keeps": keeps, "margin_px": 1.0 if keeps else -1.0}]

    assert breath_beat.pick(evenly, []) is evenly[0]                        # 가슴이 손 밑으로 묻히는 숨 둘은 아니다
    assert breath_beat.pick(evenly, nudged(0.5 + breath_beat.MARGIN)) is evenly[0]
    assert breath_beat.pick(evenly, nudged(0.5 + breath_beat.MARGIN + 1e-9))["breath_peaks"] == [16]
    assert breath_beat.pick(evenly, nudged(2.0, keeps=False)) is evenly[0]
    with pytest.raises(SystemExit, match="가슴이 손 위에 있지 않다"):
        breath_beat.pick([evenly[1]], [])


def test_what_the_beat_choice_refuses_and_what_it_leaves_alone(tmp_path) -> None:
    src = _loop(tmp_path / "loop", BUMP)
    region = ",".join(f"{v:g}" for v in HANDS)
    for extra, why in ((["--breaths", "2"], "--breaths"), (["--breath-peaks", "10,30"], "--breaths"),
                       (["--depth", "0.02", "--mode", "pixel"], "smooth 의 것")):
        with pytest.raises(SystemExit, match=why):
            cli.main(["breathe", str(src), str(tmp_path / "x"), "--avoid-region", region, "--fps", "12",
                      *([] if "--mode" in extra else ["--mode", "smooth"]), *extra])
    with pytest.raises(SystemExit, match="--fps 를 주라"):
        cli.main(["breathe", str(src), str(tmp_path / "x"), "--mode", "smooth", "--avoid-region", region])
    for extra in (["--fps", "12"], ["--avoid-anchor", region]):
        with pytest.raises(SystemExit, match="--avoid-region 의 것"):
            cli.main(["breathe", str(src), str(tmp_path / "x"), "--mode", "smooth", *extra])
    assert not (tmp_path / "x").exists()
    assert "beat" not in _bake(src, tmp_path / "plain", "--breaths", "2")


@pytest.mark.slow
def test_the_sway_layer_sways_on_the_beat_chosen(tmp_path) -> None:
    out = tmp_path / "out"
    report = _avoid(_loop(tmp_path / "loop", BUMP), out)
    assert cli.main(["video-sway", "--frames", str(out), "--out", str(tmp_path / "swayed"), "--breathe", str(out / "breathe.json"),
                     "--region", "64,95,30,18"]) == 0
    drive = json.loads((tmp_path / "swayed" / "sway.json").read_text())["drive"]
    assert {k: drive[k] for k in report["beat"]["chosen"]} == report["beat"]["chosen"]
