# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen handed-check` — is a colour-marked item on the side its handedness puts it, in every frame?

A still or loop of a character with an asymmetric item (`handedness.parse`: "the black smartwatch=left
wrist") is checked against where that item belongs in the view (`handedness.placement`). The item is
found by its colour: `--marker` is a colour the item has and nothing else on the character has (a
watch's glowing screen, a red ribbon). Per frame, the pixels of that hue are grouped into blobs and
each blob's centre is compared with the body's own centre (the mean x of its opaque pixels):

- a view with a picture side (front, back, the diagonals): every blob must be on that side;
- the item in two places (two blobs farther apart than a link distance, the smaller at least a quarter
  of the larger; smaller ones are specks of compression tint) fails in every view;
- the near side of a diagonal view: the item must show in at least half the frames; of a side view, in at
  least three quarters (a near arm is in view the whole walk; a far arm's item shows about half of it);
- the far side of a side view, an item on an arm or a leg (`handedness.limb`): the limb swings out in front
  of the body with each step and the item shows then, whole. Every blob must be in front of the body —
  on the facing side of its centre by at least a tenth of the body's height — and with `--state walk` or
  `run` it must show in at least a quarter of the frames. An item that also shows at or behind the body's
  centre is on the near limb, the wrong one;
- the far side of a side view, an item that does not swing (a pin on the far side of the head): hidden or a
  sliver — no blob may be larger than half the item seen whole, measured on `--reference` pictures of the
  same character (front or back, the item in full view);
- the near side of a side view, against `--reference`: a frame shows the item only when it is more than
  that sliver, so a far sliver turned over to face the other way does not count as the near item shown.
  Against a reference a sliver is also not judged for where it is. Without one the size rules are not
  checked (`far_hidden` / `near_whole`), and the report says so.

The screen colour cannot see the item's band without its marker — a watch's bare strap on the other arm.
`--strap` names the band's colour (usually dark) and `--zone` the rows the item sits in (black is common:
outlines, a hat, motion smear on the legs); a band of that colour outside every marker blob, wider and
taller than an outline, is another place the item shows. Specks of the marker's hue that a clip's colour
blocks leave on outlines are recorded and not judged.

A picture turned over moves the item: a front view turned over puts it on the wrong side; a side walk facing
right (the item on the far arm, in view while that arm is forward) turned over to face left shows it in too
few frames for a near arm; one facing left (the item on the near arm, crossing the body) turned over to face
right shows it behind the body's centre. So a set that made its left-hand views by mirroring fails here
(`tests/qa/test_handed_check.py` proves it).
Pixels cannot judge everything — an unmarked item, an item drawn on the wrong hand where it stays hidden —
so `--board` draws every frame with its blobs ringed (green as expected, red not) for a person to look at.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.gen import handedness as handed_mod
from sprite_gen.gen.facing import FACINGS
from sprite_gen.spec.runio import atomic_write_text

# Opaque enough to be the character: the body's centre and the marker are read on these pixels.
ALPHA_MIN = 128
# A marker pixel: within HUE_TOL degrees of the marker's hue, at least SAT_MIN saturated (or half the
# marker's own saturation, if that is lower) and at least VAL_MIN bright. A glowing screen keeps its hue
# from its dark edge to its highlight while its brightness changes by half.
HUE_TOL = 20.0
SAT_MIN = 0.45
VAL_MIN = 0.25
# Sizes as fractions of the body's height in the frame: a blob is at least MIN_BLOB_SIDE of it squared,
# and two pieces closer than LINK are one item (an outline or a highlight splits a screen).
MIN_BLOB_SIDE = 0.007
LINK = 0.02
# A blob smaller than this share of the frame's largest is a speck, not a second place: a clip's 4:2:0
# colour blocks tint a white outline or fur edge toward a saturated item's hue, and a strap can cut a
# corner off a screen. Specks are recorded and drawn on the board; they are not judged.
SPECK = 0.25
# A band with no screen (`--strap`): the item's dark band seen without its marker, as when a watch shows
# its bare strap on the other arm. Dark pixels within STRAP_TOL of the strap colour on every channel (a dark
# colour has no hue to match), eroded by STRAP_ERODE of the body height so outlines and thin seams drop out;
# what is left at least STRAP_MIN_SIDE wide and tall, farther than STRAP_GAP from every marker blob, is a
# band of its own: another place the item shows. Black is common (outlines, hair, a hat), so `--zone`
# limits the search to the rows the item sits in.
STRAP_TOL = 55
STRAP_ERODE = 0.004
STRAP_MIN_SIDE = 0.016
STRAP_GAP = 0.024
# At least this share of the frames must show the item on the near side of a diagonal view.
NEAR_SHOWN_MIN = 0.5
# ... and of a side view, where the share is all that tells a near limb from a far one that swings into view:
# a near limb is in view for the whole walk, a far limb's item for the half of it the limb is forward.
SIDE_NEAR_SHOWN_MIN = 0.75
# On the far side of a side view, a blob larger than this share of the item seen whole is not a sliver.
FAR_MAX = 0.5
# A far limb's item shows while the limb is out past the front of the body: its centre at least this share of
# the body's height in front of the body's centre. A near limb's item crosses the centre both ways as it
# swings, and stays close to the centre on a limb held at the body.
FAR_FRONT_MIN = 0.1
# In a walk or run, a far limb's item must show in at least this share of the frames: it is in view for about
# half of each step, and a walk that never shows it has lost it.
FAR_SHOWN_MIN = 0.25
GAIT_STATES = ("walk", "run")


def parse_marker(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise SystemExit(f"handed-check: --marker is a colour as #RRGGBB, got {value!r}")
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        raise SystemExit(f"handed-check: --marker is a colour as #RRGGBB, got {value!r}") from None


def parse_zone(value: str) -> tuple[float, float]:
    try:
        top, bottom = (float(v) for v in value.split(","))
    except ValueError:
        raise SystemExit(f"handed-check: --zone is TOP,BOTTOM (fractions of the body's height), got {value!r}") from None
    return top, bottom


def marker_mask(rgba: np.ndarray, marker: tuple[int, int, int], hue_tol: float = HUE_TOL) -> np.ndarray:
    """Opaque pixels of the marker's hue (`HUE_TOL`, `SAT_MIN`, `VAL_MIN`)."""
    mh, ms, _ = colorsys.rgb_to_hsv(*(c / 255 for c in marker))
    if ms < 0.2:
        raise SystemExit("handed-check: --marker must be a saturated colour (a grey, white or black item cannot be told "
                         "apart from shading); check that item on the --board by eye")
    rgb = rgba[..., :3].astype(np.float32) / 255
    mx, mn = rgb.max(-1), rgb.min(-1)
    delta = mx - mn
    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-6), 0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    d = np.maximum(delta, 1e-6)
    hue = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) / 6
    diff = np.abs(hue - mh)
    diff = np.minimum(diff, 1 - diff) * 360
    return (rgba[..., 3] >= ALPHA_MIN) & (diff <= hue_tol) & (sat >= min(SAT_MIN, ms / 2)) & (mx >= VAL_MIN)


def blobs(mask: np.ndarray, *, link: int, min_area: int) -> list[dict[str, Any]]:
    """Marker pixels grouped into items: pixels within `link` px of each other are one item."""
    ys, xs = np.nonzero(mask)
    pixels = set(zip(ys.tolist(), xs.tolist()))
    offsets = [(dy, dx) for dy in range(-link, link + 1) for dx in range(-link, link + 1) if dy * dy + dx * dx <= link * link]
    out = []
    while pixels:
        seed = pixels.pop()
        queue, members = deque([seed]), [seed]
        while queue:
            y, x = queue.popleft()
            for dy, dx in offsets:
                p = (y + dy, x + dx)
                if p in pixels:
                    pixels.remove(p)
                    queue.append(p)
                    members.append(p)
        if len(members) >= min_area:
            a = np.array(members)
            out.append({"area": len(members), "x": round(float(a[:, 1].mean()), 1), "y": round(float(a[:, 0].mean()), 1),
                        "box": [int(a[:, 1].min()), int(a[:, 0].min()), int(a[:, 1].max()), int(a[:, 0].max())]})
    return sorted(out, key=lambda b: -b["area"])


def erode(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    padded = np.pad(mask, r)
    h, w = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out &= padded[r + dy:r + dy + h, r + dx:r + dx + w]
    return out


def _gap(a: list[int], b: list[int]) -> int:
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3], 0)


def measure(image: Image.Image, marker: tuple[int, int, int], *, strap: tuple[int, int, int] | None = None,
            zone: tuple[float, float] = (0.0, 1.0)) -> dict[str, Any]:
    """The body (opaque pixels), the marker blobs and, with `strap`, the bands without a marker of one frame.
    Only the rows in `zone` (fractions of the body's height from its top) are searched."""
    rgba = np.array(image.convert("RGBA"))
    body = rgba[..., 3] >= ALPHA_MIN
    if not body.any():
        raise SystemExit("handed-check: a frame has no opaque pixels")
    if body.all():
        raise SystemExit("handed-check: a frame has no transparent pixels — check keyed frames (video-frames, cutout or "
                         "gen --transparent), so the body's centre is the body's")
    ys, xs = np.nonzero(body)
    height = int(ys.max() - ys.min() + 1)
    rows = np.zeros_like(body)
    rows[int(ys.min() + zone[0] * height):int(ys.min() + zone[1] * height) + 1] = True
    found = blobs(marker_mask(rgba, marker) & rows, link=max(2, round(LINK * height)),
                  min_area=max(4, round((MIN_BLOB_SIDE * height) ** 2)))
    keep = [b for b in found if b["area"] >= SPECK * found[0]["area"]] if found else []
    straps = []
    if strap is not None:
        near = np.abs(rgba[..., :3].astype(int) - np.array(strap)).max(-1) <= STRAP_TOL
        dark = erode((rgba[..., 3] >= ALPHA_MIN) & near & rows, max(1, round(STRAP_ERODE * height)))
        side, gap = STRAP_MIN_SIDE * height, STRAP_GAP * height
        straps = [b for b in blobs(dark, link=max(2, round(STRAP_ERODE * height)), min_area=4)
                  if b["box"][2] - b["box"][0] + 1 >= side and b["box"][3] - b["box"][1] + 1 >= side
                  and all(_gap(b["box"], m["box"]) > gap for m in found)]
    return {"body_area": int(body.sum()), "body_height": height, "centre_x": round(float(xs.mean()), 1), "blobs": keep,
            "specks": [b for b in found if b not in keep], "straps": straps}


def full_ratio(references: list[Image.Image], marker: tuple[int, int, int],
               zone: tuple[float, float] = (0.0, 1.0)) -> float | None:
    """The item seen whole, as a share of the body's area: the median of the largest blob in each reference."""
    ratios = []
    for ref in references:
        m = measure(ref, marker, zone=zone)
        if m["blobs"]:
            ratios.append(m["blobs"][0]["area"] / m["body_area"])
    if references and not ratios:
        raise SystemExit("handed-check: no --reference shows the marker; pass a front or back picture with the item in full view")
    return float(np.median(ratios)) if ratios else None


def check(frames: list[Image.Image], *, item: handed_mod.Handed, view: str, facing: str | None,
          marker: tuple[int, int, int], references: list[Image.Image] | None = None,
          strap: tuple[int, int, int] | None = None, zone: tuple[float, float] = (0.0, 1.0),
          state: str | None = None) -> dict[str, Any]:
    """Every frame against `handedness.placement(item.side, view, facing)`; `ok` is the verdict. With `strap`,
    a band without its marker counts as a place the item shows (`STRAP_TOL`). `state` is the loop's motion
    state: in a walk or run, a far limb's item must show while the limb swings forward (`FAR_SHOWN_MIN`)."""
    if not 0 <= zone[0] < zone[1] <= 1:
        raise SystemExit(f"handed-check: --zone is TOP,BOTTOM as fractions of the body's height, 0 <= TOP < BOTTOM <= 1, got {zone}")
    if not frames:
        raise SystemExit("handed-check: no frames")
    if view not in handed_mod.LATERAL_VIEWS:
        facing = None
    expect = handed_mod.placement(item.side, view, facing)
    full = full_ratio(references or [], marker, zone)
    # a far limb in a side view swings out in front of the body; any other far part stays behind it
    swings = handed_mod.limb(item.part) if view == "side" and expect["depth"] == "far" else None
    rows, fails = [], []
    for i, frame in enumerate(frames):
        m = measure(frame, marker, strap=strap, zone=zone)
        bad = []
        places = len(m["blobs"]) + len(m["straps"])
        if places > 1:
            bare = f" ({len(m['straps'])} a band without its marker)" if m["straps"] else ""
            bad.append(f"the item shows in {places} places{bare}")
        for b in m["straps"]:
            b["side"] = "right" if b["x"] > m["centre_x"] else "left"
            if expect["picture"] and b["side"] != expect["picture"]:
                bad.append(f"a band without its marker on the picture's {b['side']}, expected its {expect['picture']}")
        for b in m["blobs"]:
            b["side"] = "right" if b["x"] > m["centre_x"] else "left"
            b["ratio"] = round(b["area"] / m["body_area"], 6)
            if expect["picture"] and b["side"] != expect["picture"]:
                bad.append(f"on the picture's {b['side']}, expected its {expect['picture']}")
            whole = full is None or b["ratio"] > FAR_MAX * full
            if swings and whole:
                ahead = (b["x"] - m["centre_x"]) * (1 if facing == "right" else -1) / m["body_height"]
                b["ahead"] = round(ahead, 3)
                if ahead < FAR_FRONT_MIN:
                    bad.append(f"at or behind the body's centre ({ahead:+.2f} of the body's height in front of it, at "
                               f"least {FAR_FRONT_MIN} for a far {swings} swung forward): the item is on the near {swings}")
            elif view == "side" and expect["depth"] == "far" and not swings and full is not None and whole:
                bad.append(f"in full view ({b['ratio'] / full:.0%} of the whole item) on the far side")
        rows.append({**m, "bad": bad})
        if bad:
            fails.append({"frame": i, "why": bad})
    # A side view has no picture side, so near and far differ only in size: on the near side a frame shows the
    # item when its largest blob is more than a sliver (the far side's own limit), or a far sliver turned over
    # would count as the near item shown.
    sized = view == "side" and (expect["depth"] == "near" or swings) and full is not None
    shown = sum(1 for r in rows if r["blobs"] and (not sized or r["blobs"][0]["ratio"] > FAR_MAX * full))
    rules: dict[str, Any] = {}
    if expect["picture"]:
        rules["picture_side"] = expect["picture"]
    if expect["depth"] == "near":
        near_min = SIDE_NEAR_SHOWN_MIN if view == "side" else NEAR_SHOWN_MIN
        rules["near_shown"] = {"min": near_min, "shown": shown, "frames": len(rows), "ok": shown >= near_min * len(rows)}
    if view == "side":
        size = ({"checked": False, "why": "no --reference: the item seen whole is unknown"} if full is None
                else {"checked": True, "full_ratio": round(full, 6)})
        if swings:
            rules["far_in_front"] = {"limb": swings, "min": FAR_FRONT_MIN, "of": "body height, toward the facing side"}
            if state in GAIT_STATES:
                rules["far_shown"] = {"checked": True, "min": FAR_SHOWN_MIN, "shown": shown, "frames": len(rows),
                                      "ok": shown >= FAR_SHOWN_MIN * len(rows)}
            else:
                why = ("no --state: only a walk or run swings the far limb into view" if state is None
                       else f"a {state} does not swing the far {swings} into view")
                rules["far_shown"] = {"checked": False, "why": why, "shown": shown, "frames": len(rows)}
        elif expect["depth"] == "far":
            rules["far_hidden"] = {**size, **({"max": FAR_MAX} if full is not None else {})}
        else:
            rules["near_whole"] = {**size, **({"min": FAR_MAX} if full is not None else {})}
    ok = not fails and rules.get("near_shown", {}).get("ok", True) and rules.get("far_shown", {}).get("ok", True)
    return {
        "kind": "sprite-gen-handed-check",
        "item": vars(item), "view": view, "facing": facing, "expect": expect,
        **({"state": state} if state else {}),
        "marker": "#%02x%02x%02x" % marker, **({"strap": "#%02x%02x%02x" % strap} if strap else {}),
        "zone": list(zone), "rules": rules,
        "frames": len(rows), "frames_shown": shown, "fails": fails, "ok": bool(ok),
        "per_frame": [{k: r[k] for k in ("centre_x", "body_area", "blobs", "specks", "straps", "bad")} for r in rows],
    }


def board(frames: list[Image.Image], report: dict[str, Any], out: Path, *, height: int = 240) -> Path:
    """Every frame on a light ground, its blobs ringed (green as expected, red not), the verdict on top."""
    cells = []
    for frame, row in zip(frames, report["per_frame"]):
        k = height / frame.height
        cell = Image.new("RGBA", frame.size, (236, 236, 236, 255))
        cell.alpha_composite(frame.convert("RGBA"))
        cell = cell.convert("RGB").resize((max(1, round(frame.width * k)), height), Image.LANCZOS)
        draw = ImageDraw.Draw(cell)
        colour = (200, 0, 0) if row["bad"] else (0, 160, 0)
        for b in row["blobs"]:
            x0, y0, x1, y1 = (c * k for c in b["box"])
            draw.ellipse((x0 - 8, y0 - 8, x1 + 8, y1 + 8), outline=colour, width=3)
        for b in row["straps"]:
            x0, y0, x1, y1 = (c * k for c in b["box"])
            draw.rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), outline=(230, 120, 0), width=3)
        for b in row["specks"]:
            x0, y0, x1, y1 = (c * k for c in b["box"])
            draw.rectangle((x0 - 4, y0 - 4, x1 + 4, y1 + 4), outline=(150, 150, 150), width=1)
        cx = row["centre_x"] * k
        draw.line((cx, 0, cx, 10), fill=(110, 110, 110), width=2)
        cells.append(cell)
    head = 34
    width = sum(c.width for c in cells)
    sheet = Image.new("RGB", (max(width, 480), height + head), (250, 250, 250))
    draw = ImageDraw.Draw(sheet)
    e = report["expect"]
    where = ", ".join(f"{k} {v}" for k, v in (("picture", e["picture"]), ("depth", e["depth"])) if v)
    verdict = "PASS" if report["ok"] else f"FAIL ({len(report['fails'])} frame(s))"
    draw.text((8, 10), f"{report['view']}{' ' + report['facing'] if report['facing'] else ''} | {report['item']['item']} = "
              f"own {report['item']['side']} | expect {where} | shown {report['frames_shown']}/{report['frames']} | {verdict}",
              fill=(0, 120, 0) if report["ok"] else (190, 0, 0))
    x = 0
    for cell in cells:
        sheet.paste(cell, (x, head))
        x += cell.width
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def load_frames(*, images: list[Path], frames_dir: Path | None, strip: Path | None) -> list[Image.Image]:
    """Frames from still or frame PNGs, a folder of PNGs (name order), or a loop strip with its `.strip.json`."""
    given = sum(bool(x) for x in (images, frames_dir, strip))
    if given != 1:
        raise SystemExit("handed-check: give one of --image (repeatable), --frames-dir or --strip")
    if strip:
        meta_path = strip.with_name(strip.name.replace(".strip.png", ".strip.json"))
        if not meta_path.is_file():
            raise SystemExit(f"handed-check: {strip} has no {meta_path.name} beside it (video-loop writes both)")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        with Image.open(strip) as im:
            sheet = im.convert("RGBA")
        w, h = int(meta["w"]), int(meta["h"])
        return [sheet.crop((i * w, 0, (i + 1) * w, h)) for i in range(int(meta["frames"]))]
    paths = sorted(frames_dir.glob("*.png")) if frames_dir else images
    if not paths:
        raise SystemExit(f"handed-check: no PNG frames in {frames_dir}")
    out = []
    for p in paths:
        with Image.open(p) as im:
            out.append(im.convert("RGBA"))
    return out


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--image", action="append", type=Path, default=[], help="a keyed still or frame PNG (repeatable)")
    parser.add_argument("--frames-dir", type=Path, help="a folder of keyed frame PNGs (video-frames' keyed/)")
    parser.add_argument("--strip", type=Path, help="a loop strip (<name>.strip.png, with its .strip.json beside it)")
    parser.add_argument("--direction", required=True, choices=handed_mod.VIEWS, help="the view the frames are drawn in")
    parser.add_argument("--facing", choices=FACINGS, help="side and diagonal views: which way they face")
    parser.add_argument("--handed", required=True, metavar="ITEM=SIDE [PART]", help="the item and the character's own side it is on, e.g. 'the black smartwatch=left wrist'")
    parser.add_argument("--marker", required=True, help="#RRGGBB: a saturated colour of the item that nothing else on the character has")
    parser.add_argument("--strap", help="#RRGGBB: the item's band colour (a watch strap, usually dark); a band of it with no marker on it, away from the marker, counts as another place the item shows. Use with --zone")
    parser.add_argument("--zone", default="0,1", help="TOP,BOTTOM: the rows the item sits in, as fractions of the body's height from its top (default 0,1, the whole body); e.g. 0.5,0.9 for a wrist")
    parser.add_argument("--state", help="the loop's motion state (walk, run, idle, ...): in a walk or run of a side view, an item on the far arm or leg must show while that limb swings forward")
    parser.add_argument("--reference", action="append", type=Path, default=[], help="a keyed front or back picture of the same character with the item in full view (repeatable): in a side view the far item must stay smaller than half of it, and the near item counts as shown only when larger")
    parser.add_argument("--report", type=Path, help="write the report JSON here")
    parser.add_argument("--board", type=Path, help="write the review board PNG here")


def run(**kwargs: object) -> int:
    frames = load_frames(images=list(kwargs.get("image") or []), frames_dir=kwargs.get("frames_dir"),  # type: ignore[arg-type]
                         strip=kwargs.get("strip"))  # type: ignore[arg-type]
    references = load_frames(images=list(kwargs.get("reference") or []), frames_dir=None, strip=None) if kwargs.get("reference") else []
    report = check(frames, item=handed_mod.parse(str(kwargs["handed"])), view=str(kwargs["direction"]),
                   facing=kwargs.get("facing"), marker=parse_marker(str(kwargs["marker"])), references=references,  # type: ignore[arg-type]
                   strap=parse_marker(str(kwargs["strap"])) if kwargs.get("strap") else None,
                   zone=parse_zone(str(kwargs.get("zone") or "0,1")),
                   state=str(kwargs["state"]) if kwargs.get("state") else None)
    if kwargs.get("board"):
        report["board"] = str(board(frames, report, Path(str(kwargs["board"]))))
    if kwargs.get("report"):
        atomic_write_text(Path(str(kwargs["report"])), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    summary = {k: report[k] for k in ("view", "facing", "expect", "frames", "frames_shown", "ok")}
    summary["fails"] = len(report["fails"])
    unchecked = [f"{rule}: {report['rules'][rule]['why']}" for rule in ("far_hidden", "near_whole", "far_shown")
                 if report["rules"].get(rule, {}).get("checked") is False]
    if unchecked:
        summary["unchecked"] = "; ".join(unchecked)
    print(json.dumps(summary, ensure_ascii=False))
    if not report["ok"]:
        for f in report["fails"][:10]:
            print(f"handed-check: frame {f['frame']}: {'; '.join(f['why'])}", file=sys.stderr)
        if report["rules"].get("near_shown", {}).get("ok") is False:
            r = report["rules"]["near_shown"]
            whole = " more than a sliver" if report["rules"].get("near_whole", {}).get("checked") else ""
            print(f"handed-check: on the near side the item shows{whole} in {r['shown']} of {r['frames']} frames", file=sys.stderr)
        if report["rules"].get("far_shown", {}).get("ok") is False:
            r = report["rules"]["far_shown"]
            print(f"handed-check: on the far {report['rules']['far_in_front']['limb']} the item shows in {r['shown']} of "
                  f"{r['frames']} frames of the {report['state']}; it must show while that limb swings forward", file=sys.stderr)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen handed-check", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
