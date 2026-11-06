# SPDX-License-Identifier: Apache-2.0
"""A magenta plate painted into the still (`video-canvas --prop-plate`), found in each frame of the clip filmed over
it and erased from the keyed frames (`video-frames --prop-plate`), so the frames keep no plate and the cut
`video-loop` makes has none.

The pictures are synthetic: an outlined figure on the green key, filmed by a "model" that returns the canvas at twice
its size and runs the figure across in front of the plate. One clip keeps the plate where the canvas has it (a pixel
off in two frames); another pans the camera so the plate moves left frame by frame, turns it in one frame and leaves it
out of another, as a video model does with a plate it is told to keep still.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.video import canvas as canvas_mod
from sprite_gen.video import frames as frames_mod
from sprite_gen.video import loop as loop_mod
from sprite_gen.video import prop_plate

GREEN = (0, 255, 0)
OUTLINE = (28, 18, 16)
SKIN = (238, 206, 178)
SHIRT = (245, 245, 240)
NAVY = (40, 52, 110)
HAIR = (170, 52, 36)
PINK = (230, 100, 190)  # a colour the figure of the palette clip owns, read as magenta by the erase's own rule
SS = 4  # supersamples per pixel side: the figure's edge is antialiased as a drawn one is
STILL = (160, 120)
AT = (10, 20)  # the figure's place on the still
SCALE = 2  # the clip's frames are the canvas at twice its size
FRAMES = 16
STEP = 22  # frame px the figure runs each frame over the ground: in front of the plate in frames 8..12 of the still clip
OFF = {3: 1, 11: 1}  # frames the model draws the plate a pixel right in (the still clip)
PAN = 4  # frame px the panned clip's camera moves right each frame: its ground, the plate on it, moves left
TILT = (4, 12.0)  # the panned clip's frame the plate is turned in, and by how many degrees (clockwise, about its foot)
HIDDEN = (14,)  # the panned clip's frames the model leaves the plate out of
OUTLINE_MAX = 60  # an outline pixel: no channel above it


def figure(scale: int, *, patch: tuple[int, int, int] | None = None) -> Image.Image:
    """A 40 x 96 runner at `scale`, RGBA: a dark outline round a head, a shirt, shorts, legs and shoes; `patch` paints a
    square of that colour on the shirt."""
    k = SS * scale
    im = Image.new("RGBA", (40 * k, 96 * k))
    d = ImageDraw.Draw(im)
    line = 2 * k
    d.ellipse((6 * k, 0, 30 * k, 22 * k), fill=HAIR, outline=OUTLINE, width=line)
    d.ellipse((12 * k, 4 * k, 34 * k, 26 * k), fill=SKIN, outline=OUTLINE, width=line)
    for x in (12, 23):
        d.rectangle(((x) * k, 62 * k, (x + 8) * k, 90 * k), fill=SKIN, outline=OUTLINE, width=line)
        d.rectangle(((x - 2) * k, 88 * k, (x + 11) * k, 96 * k - 1), fill=SHIRT, outline=OUTLINE, width=line)
    d.rectangle((10 * k, 26 * k, 34 * k, 54 * k), fill=SHIRT, outline=OUTLINE, width=line)
    d.rectangle((10 * k, 52 * k, 34 * k, 66 * k), fill=NAVY, outline=OUTLINE, width=line)
    if patch is not None:
        d.rectangle((15 * k, 31 * k, 29 * k, 47 * k), fill=patch)
    return im.resize((40 * scale, 96 * scale), Image.BOX)


def body_mask(scale: int, *, least: int = 128) -> np.ndarray:
    """Where the figure at `scale` covers at least `least` of 255."""
    return np.asarray(figure(scale))[..., 3] >= least


def still(path: Path) -> Path:
    im = Image.new("RGB", STILL, GREEN)
    f = figure(1)
    im.paste(f, AT, f)
    im.save(path)
    return path


def make_canvas(tmp: Path, *, facing: str = "right", plate: bool = True, report: bool = True, **kwargs) -> dict:
    return canvas_mod.run_canvas(still(tmp / "still.png"), tmp / "canvas.png", state="vault", shape="wide", facing=facing,
                                 headroom=None, lead=None, report_path=tmp / "canvas.json" if report else None,
                                 prop_plate=plate, **kwargs)


def plate_mask(size: tuple[int, int], box: list[int], dx: int, tilt: float) -> np.ndarray:
    """The plate as the model draws it on a frame of `size`: the canvas's `box` at the frame's scale, moved `dx` px
    right, turned `tilt` degrees clockwise about the middle of its foot."""
    im = Image.new("L", size, 0)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    if tilt == 0:
        d.rectangle((x0 + dx, y0, x1 - 1 + dx, y1 - 1), fill=255)
    else:
        cx, cy, t = (x0 + x1) / 2 + dx, y1, math.radians(tilt)
        corners = [(x + dx - cx, y - cy) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        d.polygon([(cx + u * math.cos(t) - v * math.sin(t), cy + u * math.sin(t) + v * math.cos(t)) for u, v in corners], fill=255)
    return np.asarray(im) > 0


class Filmed:
    """A clip a model made of the canvas, keyed with the plate erased (`erased`) and without (`kept`)."""

    def __init__(self, tmp: Path, report: dict, clip: Path, places: list[tuple[int, int]], plates: list[np.ndarray | None],
                 erased: dict, kept: dict):
        self.tmp, self.report, self.clip, self.places, self.plates, self.erased, self.kept = (
            tmp, report, clip, places, plates, erased, kept)


def film(tmp: Path, report: dict, *, pan: int = 0, off: dict[int, int] | None = None, tilt: tuple[int, float] | None = None,
         hidden: tuple[int, ...] = (), runner: Image.Image | None = None) -> tuple[Path, list[tuple[int, int]], list[np.ndarray | None]]:
    """The clip a model makes of the canvas: the canvas at SCALE, its camera moving `pan` px right a frame (the plate
    with the ground), the plate a pixel right in the frames `off` names, turned in the frame `tilt` names and left out of
    the `hidden` ones, the figure running right across in front of it. Returns the clip, the figure's place and the plate
    as drawn (None where left out) in each frame."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    w, h = report["canvas"]
    size = (w * SCALE, h * SCALE)
    box = [v * SCALE for v in report["prop_plate"]["box"]]
    ox, oy = report["offset"]
    made = tmp / "made"
    made.mkdir()
    runner = runner if runner is not None else figure(SCALE)
    places, plates = [], []
    for k in range(FRAMES):
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        frame[...] = GREEN
        drawn = None if k in hidden else plate_mask(size, box, (off or {}).get(k, 0) - pan * k,
                                                    tilt[1] if tilt is not None and tilt[0] == k else 0.0)
        if drawn is not None:
            frame[drawn] = prop_plate.RGB
        im = Image.fromarray(frame, "RGB")
        at = ((ox + AT[0]) * SCALE + (STEP - pan) * k, (oy + AT[1]) * SCALE)
        im.paste(runner, at, runner)
        places.append(at)
        plates.append(drawn)
        im.save(made / f"frame-{k:04d}.png")
    subprocess.run(["ffmpeg", "-v", "error", "-framerate", "24", "-i", str(made / "frame-%04d.png"), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "12", str(tmp / "clip.mp4")], check=True)
    return tmp / "clip.mp4", places, plates


def key(tmp: Path, clip: Path, out: str, plate: Path | None, *, decontam: str = "off") -> dict:
    """The clip keyed into `tmp / out`. The erase reads the frames as decoded, so the edge pass is left off where two
    keyings are compared pixel for pixel; with it on, the plate keyed with the clip changes the subject's palette."""
    return frames_mod.run_frames(clip, tmp / out, key="green", allow_edge_contact=False, report_path=None, spill="auto",
                                 reference=tmp / "canvas.png", prop_plate=plate, decontam=decontam)


def keyed(tmp: Path, out: str) -> list[np.ndarray]:
    return [np.asarray(Image.open(f).convert("RGBA")) for f in sorted((tmp / out / "keyed").glob("*.png"))]


def placed(mask: np.ndarray, at: tuple[int, int], size: tuple[int, int]) -> np.ndarray:
    """`mask` put at `at` on a frame of `size`."""
    out = np.zeros((size[1], size[0]), dtype=bool)
    x, y = at
    out[y:y + mask.shape[0], x:x + mask.shape[1]] = mask
    return out


def grown(mask: np.ndarray, px: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(px):
        out[1:] |= out[:-1].copy(); out[:-1] |= out[1:].copy()
        out[:, 1:] |= out[:, :-1].copy(); out[:, :-1] |= out[:, 1:].copy()
    return out


def shrunk(mask: np.ndarray, px: int) -> np.ndarray:
    return ~grown(~mask, px)


def shoot(tmp: Path, **kwargs) -> Filmed:
    report = make_canvas(tmp)
    clip, places, plates = film(tmp, report, **kwargs)
    return Filmed(tmp, report, clip, places, plates, key(tmp, clip, "erased", tmp / "canvas.json"), key(tmp, clip, "kept", None))


@pytest.fixture(scope="module")
def filmed(tmp_path_factory) -> Filmed:
    """The plate kept where the canvas has it, a pixel off in two frames."""
    return shoot(tmp_path_factory.mktemp("plate"), off=OFF)


@pytest.fixture(scope="module")
def panned(tmp_path_factory) -> Filmed:
    """The camera panning right: the plate moves left PAN px a frame, is turned in one frame and left out of one."""
    return shoot(tmp_path_factory.mktemp("panned"), pan=PAN, tilt=TILT, hidden=HIDDEN)


# --- video-canvas ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("facing", ["right", "left"])
def test_the_canvas_paints_the_plate_in_front_of_the_subject_on_its_foot_row(tmp_path: Path, facing: str) -> None:
    report = make_canvas(tmp_path, facing=facing)
    bare, _ = canvas_mod.pad_canvas(Image.open(tmp_path / "still.png"), canvas_mod.profile_for("vault", "wide"), facing=facing)
    left, top, right, bottom = canvas_mod.subject_box(bare, GREEN, 0)
    height = bottom - top
    width, gap = round(prop_plate.WIDTH * height), round(prop_plate.GAP * height)
    x0 = right + gap if facing == "right" else left - gap - width
    box = [x0, bottom - round(prop_plate.HEIGHT * height), x0 + width, bottom]
    assert report["prop_plate"]["box"] == box
    assert report["prop_plate"]["subject"] == [left, top, right, bottom]
    assert json.loads((tmp_path / "canvas.json").read_text())["prop_plate"]["box"] == box
    painted, plain = np.asarray(Image.open(tmp_path / "canvas.png")), np.asarray(bare)
    inside = np.zeros(plain.shape[:2], dtype=bool)
    inside[box[1]:box[3], box[0]:box[2]] = True
    assert (painted[inside] == prop_plate.RGB).all()
    assert (painted[~inside] == plain[~inside]).all(), "the plate is the only change"


def test_without_the_option_the_canvas_is_the_padded_still_and_its_report_has_no_plate(tmp_path: Path) -> None:
    report = make_canvas(tmp_path, plate=False)
    bare, _ = canvas_mod.pad_canvas(Image.open(tmp_path / "still.png"), canvas_mod.profile_for("vault", "wide"))
    assert "prop_plate" not in report and "prop_plate" not in json.loads((tmp_path / "canvas.json").read_text())
    assert (np.asarray(Image.open(tmp_path / "canvas.png")) == np.asarray(bare)).all()


def test_a_plate_the_canvas_has_no_room_for_is_refused(tmp_path: Path) -> None:
    """The still's figure stands at its left: facing left on its own square, the plate would stand off the canvas."""
    with pytest.raises(SystemExit, match="falls past the canvas's left edge"):
        canvas_mod.run_canvas(still(tmp_path / "still.png"), tmp_path / "c.png", state="vault", shape="square", facing="left",
                              headroom=None, lead=None, report_path=tmp_path / "c.json", prop_plate=True)
    assert not (tmp_path / "c.png").exists()


def test_the_plate_needs_a_green_key_and_a_report(tmp_path: Path) -> None:
    im = Image.new("RGB", STILL, (255, 0, 255))
    f = figure(1)
    im.paste(f, AT, f)
    im.save(tmp_path / "magenta.png")
    with pytest.raises(SystemExit, match="green key"):
        canvas_mod.run_canvas(tmp_path / "magenta.png", tmp_path / "c.png", state="vault", shape="wide", facing="right",
                              headroom=None, lead=None, report_path=tmp_path / "c.json", prop_plate=True)
    with pytest.raises(SystemExit, match="--report"):
        make_canvas(tmp_path, report=False)


# --- video-frames ------------------------------------------------------------------------------------------------


def magenta(a: np.ndarray) -> np.ndarray:
    """Where `a` (RGBA) is led by red and blue over green as the erase reads the plate."""
    r, g, b = (a[..., i].astype(np.int32) for i in range(3))
    return (a[..., 3] > 0) & (np.minimum(r, b) - g > prop_plate.MAGENTA_LEAD) & (np.abs(r - b) < prop_plate.MAGENTA_BALANCE)


def left_of_the_plate(shot: Filmed) -> list[tuple[int, int]]:
    """Each erased frame's pixels the figure does not cover (grown by 3 px) that are opaque within 3 px of the plate as
    drawn, and those that are magenta anywhere."""
    frames = keyed(shot.tmp, "erased")
    size = (frames[0].shape[1], frames[0].shape[0])
    out = []
    for a, at, drawn in zip(frames, shot.places, shot.plates):
        free = (a[..., 3] > 0) & ~grown(placed(body_mask(SCALE), at, size), 3)
        near = grown(drawn, 3) if drawn is not None else np.zeros(free.shape, dtype=bool)
        out.append((int((free & near).sum()), int((free & magenta(a)).sum())))
    return out


@pytest.mark.parametrize("clip", ["filmed", "panned"])
def test_the_plate_is_gone_from_every_keyed_frame(clip: str, request) -> None:
    """Gone where the model kept the plate where the canvas has it, drew it a pixel off, carried it off with the camera
    or turned it: no pixel of it or of the line its edge blends into the key is left, and no magenta anywhere."""
    shot: Filmed = request.getfixturevalue(clip)
    left = left_of_the_plate(shot)
    assert all(n == 0 and m == 0 for n, m in left), f"(plate px, magenta px) left, frame by frame: {left}"
    kept = [int(((a[..., 3] > 0) & drawn).sum()) for a, drawn in zip(keyed(shot.tmp, "kept"), shot.plates) if drawn is not None]
    assert min(kept) > 0.5 * min(int(d.sum()) for d in shot.plates if d is not None), "keyed without the erase, the plate is there"


def test_each_frame_reports_where_its_plate_was_found(panned: Filmed) -> None:
    """A row a frame: the box the plate was found in (the part of the drawn plate the figure leaves showing, but for the
    pixel or two the codec blends at its edge, a turned plate's sharp corners), and, where the figure is clear of it,
    how far it was turned; a frame it was left out of is named as one with no plate found, and the frames the figure
    crosses it in as ones where it meets the subject."""
    plate = panned.erased["prop_plate"]
    rows = plate["rows"]
    names = [f"frame-{k + 1:04d}.png" for k in range(FRAMES)]
    assert [r["frame"] for r in rows] == names and plate["frames"] == FRAMES
    size = (panned.plates[0].shape[1], panned.plates[0].shape[0])
    assert plate["canvas_report"] == str((panned.tmp / "canvas.json").resolve())
    assert plate["start_box"] == list(prop_plate.frame_box(panned.report["prop_plate"]["box"], panned.report["canvas"], size))
    meets = []
    for k, (row, drawn, at) in enumerate(zip(rows, panned.plates, panned.places)):
        if drawn is None:
            assert row == {"frame": names[k], "found": False}
            continue
        body = placed(body_mask(SCALE, least=1), at, size)
        showing = drawn & ~body
        ys, xs = np.nonzero(showing)
        assert row["found"] is True
        assert all(abs(a - b) <= 2 for a, b in zip(row["box"], (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))), (k, row["box"])
        if (grown(body, prop_plate.RIM + 1) & drawn).any():
            meets.append(names[k])
            continue
        assert abs(row["plate_px"] - int(drawn.sum())) <= 0.05 * drawn.sum(), (k, row["plate_px"], int(drawn.sum()))
        assert abs(row["tilt"] - (TILT[1] if k == TILT[0] else 0.0)) <= 1.0, (k, row["tilt"])
    assert plate["not_found"] == [names[k] for k in HIDDEN]
    assert meets and plate["touching"] == meets
    assert [r["frame"] for r in rows if r.get("touch_px")] == meets
    assert plate["frames_changed"] == FRAMES - len(HIDDEN)
    assert plate["erased_px"] == sum(r.get("erased_px", 0) for r in rows)


def test_the_warnings_name_the_frames_with_no_plate_and_those_where_it_meets_the_subject(panned: Filmed) -> None:
    plate = panned.erased["prop_plate"]
    lines = prop_plate.warnings(plate)
    assert len(lines) == 2
    assert all(name in lines[0] for name in plate["not_found"]) and "no plate found" in lines[0]
    assert plate["touching"][0] in lines[1] and "meets the subject" in lines[1]
    assert prop_plate.warnings({**plate, "not_found": [], "touching": []}) == []


@pytest.mark.parametrize("clip", ["filmed", "panned"])
def test_the_figure_crossing_in_front_of_the_plate_keeps_its_pixels(clip: str, request) -> None:
    """The figure crosses the plate and the line round it, its dark outline over that line. None of it goes but the few
    edge pixels the codec tinted with the plate's magenta."""
    shot: Filmed = request.getfixturevalue(clip)
    erased, kept = keyed(shot.tmp, "erased"), keyed(shot.tmp, "kept")
    size = (erased[0].shape[1], erased[0].shape[0])
    lost, tinted, over_rim = [], [], []
    for a, b, at, drawn in zip(erased, kept, shot.places, shot.plates):
        core = shrunk(placed(body_mask(SCALE), at, size), 2)
        gone = core & (a != b).any(axis=2)
        lost.append(int(gone.sum()))
        tinted.append(int((gone & magenta(b)).sum()))
        rim = grown(drawn, prop_plate.RIM) & ~drawn if drawn is not None else np.zeros(core.shape, dtype=bool)
        over_rim.append(int((core & rim & (b[..., :3].astype(np.int32).max(axis=2) < OUTLINE_MAX)).sum()))
    assert sum(1 for n in over_rim if n) >= 4, f"the outline crosses the rim in too few frames: {over_rim}"
    assert lost == tinted, f"figure pixels erased that the codec did not tint magenta: lost {lost}, tinted {tinted}"
    assert max(lost) <= 2, f"figure pixels erased, frame by frame: {lost}"


@pytest.mark.parametrize("clip", ["filmed", "panned"])
def test_away_from_each_frame_s_plate_the_frames_are_the_frames_keyed_without_it(clip: str, request) -> None:
    shot: Filmed = request.getfixturevalue(clip)
    for a, b, drawn in zip(keyed(shot.tmp, "erased"), keyed(shot.tmp, "kept"), shot.plates):
        away = ~grown(drawn, prop_plate.RIM + 2) if drawn is not None else np.ones(a.shape[:2], dtype=bool)
        assert (a[away] == b[away]).all()
    assert "prop_plate" not in shot.kept


def test_the_search_starts_from_the_box_the_canvas_report_holds(filmed: Filmed, tmp_path: Path) -> None:
    """The canvas report's box is where the first frame is searched: moved more than the plate's own size away, no frame
    finds it, and nothing is erased."""
    moved = json.loads((filmed.tmp / "canvas.json").read_text())
    x0, y0, x1, y1 = moved["prop_plate"]["box"]
    moved["prop_plate"]["box"] = [2, y0, 2 + x1 - x0, y1]
    (tmp_path / "moved.json").write_text(json.dumps(moved))
    raw = sorted((filmed.tmp / "erased" / "raw").glob("*.png"))
    files = []
    for f in sorted((filmed.tmp / "kept" / "keyed").glob("*.png")):
        shutil.copy(f, tmp_path / f.name)
        files.append(tmp_path / f.name)
    plate = prop_plate.read(tmp_path / "moved.json")
    done = prop_plate.erase(files, raw, prop_plate.find(raw, plate), plate)
    assert done["not_found"] == [f.name for f in raw] and done["erased_px"] == 0 and done["frames_changed"] == 0
    for f in files:
        assert (np.asarray(Image.open(f)) == np.asarray(Image.open(filmed.tmp / "kept" / "keyed" / f.name))).all()


DENIM = (66, 92, 140)
RING = (70, 100, 125)  # a blue grey a codec can ring beside a magenta plate on green, off the plate-key axis as denim is
# A plate's side as a codec can decode it, outward from its colour: dark magenta, then the line blended into the key
EDGE = ((205, 40, 190), (225, 65, 215), (90, 160, 110), (115, 180, 135))


def test_the_plate_s_edge_and_a_blue_grey_line_beside_it_go_and_the_denim_crossing_its_rim_stays(tmp_path: Path) -> None:
    """The left side is a plate's edge as decoded: the line blended into the key ends 4 px past the plate's colour.
    On the right, no colour tells the codec's blue grey from denim; it lies wholly within the plate's rim and goes. A
    denim bar crossing the rim from outside it stays, the part in front of the plate too."""
    w, h, box = 120, 80, (60, 20, 76, 70)
    (tmp_path / "canvas.json").write_text(json.dumps({"kind": prop_plate.CANVAS_REPORT, "canvas": [w, h],
                                                      "prop_plate": {"box": list(box)}}))
    decoded = np.zeros((h, w, 3), dtype=np.uint8)
    decoded[...] = GREEN
    decoded[box[1]:box[3], box[0]:box[2]] = prop_plate.RGB
    decoded[box[1]:box[3], box[2]] = RING
    for k, colour in enumerate(EDGE):
        decoded[box[1]:box[3], box[0] - 1 - k] = colour
    decoded[40:46, 50:63] = DENIM
    Image.fromarray(decoded, "RGB").save(tmp_path / "frame-0001.png")
    keyed_frame = np.zeros((h, w, 4), dtype=np.uint8)
    solid = (decoded != GREEN).any(axis=2)
    keyed_frame[solid, :3], keyed_frame[solid, 3] = decoded[solid], 255
    (tmp_path / "keyed").mkdir()
    Image.fromarray(keyed_frame, "RGBA").save(tmp_path / "keyed" / "frame-0001.png")
    plate = prop_plate.read(tmp_path / "canvas.json")
    raw = [tmp_path / "frame-0001.png"]
    done = prop_plate.erase([tmp_path / "keyed" / "frame-0001.png"], raw, prop_plate.find(raw, plate), plate)
    out = np.asarray(Image.open(tmp_path / "keyed" / "frame-0001.png"))
    bar = (decoded == DENIM).all(axis=2)
    assert (out[..., 3][bar] == 255).all(), "the denim bar keeps every pixel"
    assert not (out[..., 3] > 0)[~bar].any(), f"left beside the bar: {np.argwhere((out[..., 3] > 0) & ~bar)[:5].tolist()}"
    assert done["touching"] == ["frame-0001.png"] and done["rows"][0]["touch_px"] == int(
        (bar & prop_plate.not_subject(prop_plate.find(raw, plate)[0], (w, h))).sum())


def test_a_canvas_report_without_a_plate_is_refused_before_anything_is_keyed(filmed: Filmed, tmp_path: Path) -> None:
    tmp = filmed.tmp
    bare = json.loads((tmp / "canvas.json").read_text())
    del bare["prop_plate"]
    (tmp_path / "bare.json").write_text(json.dumps(bare))
    (tmp_path / "other.json").write_text(json.dumps({"kind": "sprite-gen-video-frames-report"}))
    for report, words in ((tmp_path / "bare.json", "holds no plate"), (tmp_path / "other.json", "not a video-canvas report")):
        with pytest.raises(SystemExit, match=words):
            frames_mod.run_frames(tmp / "clip.mp4", tmp_path / "out", key="green", allow_edge_contact=False, report_path=None,
                                  prop_plate=report)
        assert not (tmp_path / "out" / "raw").exists()


def test_frame_box_is_the_canvas_box_on_the_frame_rounded_outward() -> None:
    assert prop_plate.frame_box([700, 729, 777, 919], [1920, 1080], (1920, 1080)) == (700, 729, 777, 919)
    assert prop_plate.frame_box([10, 10, 21, 31], [100, 50], (300, 150)) == (30, 30, 63, 93)
    assert prop_plate.frame_box([1500, 700, 1650, 1000], [2400, 1350], (1280, 720)) == (800, 373, 880, 534)


# --- the edge pass learns the subject's palette without the plate --------------------------------------------------


@pytest.fixture(scope="module")
def palette_clip(tmp_path_factory) -> Filmed:
    """The still clip keyed with the edge pass on, its figure wearing a pink patch: a magenta of its own."""
    tmp = tmp_path_factory.mktemp("palette")
    report = make_canvas(tmp)
    clip, places, plates = film(tmp, report, runner=figure(SCALE, patch=PINK))
    return Filmed(tmp, report, clip, places, plates, key(tmp, clip, "erased", tmp / "canvas.json", decontam="palette"),
                  key(tmp, clip, "kept", None, decontam="palette"))


def nearest(palette: list[list[int]], colour: tuple[int, int, int]) -> int:
    """How far the palette colour nearest `colour` is from it, on its farthest channel."""
    return int(np.abs(np.asarray(palette) - np.asarray(colour)).max(axis=1).min())


def test_the_edge_pass_learns_the_palette_without_the_plate(palette_clip: Filmed) -> None:
    """The palette is learned on the first frame, where the plate stands as opaque as the figure: keyed without the
    plate's report it takes the plate's magenta for a colour the subject owns; with it, the plate's place is left out
    and the figure's own pink stays."""
    with_plate, without = palette_clip.erased["decontam"], palette_clip.kept["decontam"]
    assert with_plate["applied_frames"] == without["applied_frames"] == FRAMES
    assert nearest(without["palette"], prop_plate.RGB) <= prop_plate.PLATE_TOLERANCE
    assert nearest(with_plate["palette"], prop_plate.RGB) > prop_plate.PLATE_TOLERANCE, with_plate["palette"]
    assert nearest(with_plate["palette"], PINK) <= 24, with_plate["palette"]
    left = left_of_the_plate(palette_clip)
    assert all(n == 0 for n, _ in left), f"plate px left with the edge pass on, frame by frame: {left}"


# --- video-loop: the cut is made of the erased frames -------------------------------------------------------------


def test_the_cut_of_the_erased_frames_has_no_plate(filmed: Filmed) -> None:
    tmp = filmed.tmp
    counts = []
    for out in ("erased", "kept"):
        loop_mod.run_loop(tmp / out / "keyed", tmp / f"loop-{out}", fps=24, state="vault", min_len=None, max_len=None,
                          n_out=None, seam_max=0.001, name="vault", report_path=None, cycle_mode="once", start=0,
                          length=FRAMES, fill_held="off")
        strip = np.asarray(Image.open(tmp / f"loop-{out}" / "vault.strip.png").convert("RGBA"))
        counts.append(int(magenta(strip).sum()))
    assert counts[0] == 0 and counts[1] > 0, counts
