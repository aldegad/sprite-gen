# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-face` — a face pack: the lower face of a talking clip, cut to be laid on body motions.

The request (docs/face-parts.md "The request") names a reference cell and the face on it, a talking clip (keyed
frames, and where on the reference its frames show) and the body motions the face is laid on: one loop the others
come back to, and clips that play once. The pack is a folder a player reads (`sprite-gen-face-v1.mjs`, copied in) and
`face-bake` bakes from:

- **Anchors** for every frame of every clip, read on the band round the reference's own lower face (so a clip's
  anchors are the same whatever else the request names); a body frame whose own mouth is closed has its place read
  again on the ring of skin round the mouth, its size and tilt the band's (a head that turns moves its hair, headset
  and cheeks further than its mouth), and its other frames take the move of the read ones round them. The **weight**:
  the lower faces the anchors lay on the reference, joined (`face_layer`).
- **Face frames**: each talking frame's mouth read in reference coordinates (`face_layer.mouth_reading`), its open
  area (`face_layer.open_area`) and its step to every other frame (`face_layer.step_features`, over the clip's median
  step). A closed mouth that smiles is in the smile set, one that does not in the neutral set; an open run takes the
  set of the closed frames on both its sides, and a run between a smile and a neutral frame turns from one to the
  other and is in neither. A frame in a set is cut as a sprite (`face_layer.face_sprite`).
- **Body clips**: each frame's cell, its anchor and what of it stays on top of the layer (`face_layer.body_keep`).
  A clip that says something with its own mouth has an `own_mouth` span, from its start to the frame its mouth has
  closed after its own sound: given as seconds, or read off its mouth timeline (the first `closed` frame after the
  last `voiced` one). The layer stays off there; the clip's own sound plays.
- **Entry**: where the layer comes on — a clip's `own_mouth` end, or a clip's first frame — the engine reads the
  clip's own mouth and enters in the set its lift reads by the talking frames' own line (`face_layer.SMILE_FROM`), at
  the rest (a closed frame of that set the line opens from and closes back to) nearest in lift, the least switch
  among those within LIFT_TIE px of it.
- **Switch**: per body frame and per rest the pack uses, how far laying that rest moves the frame's face, over the
  larger of the clip's median step and the talking clip's (a body that only breathes steps far less than a face that
  talks); the layer comes on and goes off only where that is within the jump line.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import multiprocessing
import multiprocessing.connection
import os
import shutil
import tempfile
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.util.console import utf8_streams
from sprite_gen.video import face_layer as fl

PROG = "video-face"
REQUEST_KIND = "sprite-gen-face-request"
PACK_KIND = "sprite-gen-face-pack"
VERSION = 1
PLAYER_PATH = Path(__file__).with_name("sprite-gen-face-v1.mjs")
LIFT_TIE = 0.5  # px: rests this near the nearest lift are told apart by their switch

# The voice -> mouth level rule the player runs (sprite-gen-face-v1.mjs `mouthTrack`): the flap's numbers,
# docs/face-parts.md "The line".
MOUTH_RULES = {"closed_under": 0.12, "prominence": 0.05, "min_gap_s": 1 / 12, "open_lo_hi": [2, 5], "surprise_at": 0.85,
               "lead_frames": 1, "max_step": 3, "normal_top": 6, "surprise_top": 6, "wide_from": 4,
               "surprise_tags": ["[surprised]", "[gasps]"]}
# The face frame pick (`pickFaces`): a hold, a jump within a line and an entry from or exit to the rest cost these
# (in mouth levels); a mouth opened less than the line wants weighs UNDER_WEIGHT times one opened more.
PICK_RULES = {"hold_cost": 0.6, "jump_cost": 1.5, "edge_cost": 1.0, "under_weight": 3.0}


@dataclass
class Clip:
    name: str
    files: list[Path]
    place: tuple[int, int, int]
    native: tuple[int, int]
    loop: bool = False
    own_mouth: dict[str, Any] | None = None
    sound: Path | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    used: list[dict[str, Any]] = field(default_factory=list)

    def frame(self, k: int) -> Image.Image:
        return Image.open(self.files[k]).convert("RGBA")

    def on_reference(self, k: int, size: tuple[int, int]) -> Image.Image:
        return fl.to_reference(self.frame(k), self.place, size)

    def to_native(self, k: int, geom: fl.Geometry) -> np.ndarray:
        """The reference's pixels to frame k's own: its anchor, then its placement."""
        return fl.placement(self.place, self.native) @ fl.matrix(self.used[k], geom)


# --- the request --------------------------------------------------------------------------------------------------

def _frames(folder: Path, what: str) -> list[Path]:
    files = sorted(folder.glob("frame-*.png"))
    if len(files) < 6:
        raise SystemExit(f"{PROG}: {what} {folder} holds {len(files)} frame-*.png; a clip needs six or more")
    return files


def _place(value: Any, size: tuple[int, int], what: str) -> tuple[int, int, int]:
    if value is None:
        return (0, 0, size[0])
    if not isinstance(value, list) or len(value) != 3 or any(isinstance(v, bool) or not isinstance(v, int) for v in value) or value[2] <= 0:
        raise SystemExit(f"{PROG}: {what}.place must be [x, y, side]: integers, where on the reference the clip's square frame shows")
    return tuple(value)  # type: ignore[return-value]


def own_mouth_from_timeline(path: Path, frames: int, fps: float) -> dict[str, Any]:
    """The span a clip talks with its own mouth, off its mouth timeline (a TSV with `frame`, `state` and `sound`
    columns, one row a frame): from its first frame to the first `closed` frame after its last `voiced` one."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if not rows or not {"frame", "state", "sound"} <= set(rows[0]):
        raise SystemExit(f"{PROG}: {path} is not a mouth timeline: it needs the tab-separated columns frame, state and sound")
    if [int(r["frame"]) for r in rows] != list(range(frames)):
        raise SystemExit(f"{PROG}: {path} has {len(rows)} rows for frames {rows[0]['frame']}..{rows[-1]['frame']}; the clip has {frames} frames from 0")
    voiced = [int(r["frame"]) for r in rows if r["sound"] == "voiced"]
    if not voiced:
        raise SystemExit(f"{PROG}: {path} has no voiced frame; a clip that says nothing has no own_mouth span")
    last = voiced[-1]
    end = next((int(r["frame"]) for r in rows[last + 1:] if r["state"] == "closed"), None)
    if end is None:
        raise SystemExit(f"{PROG}: {path}: the mouth never reads closed after the last voiced frame {last}; the face layer has no frame to come on")
    return {"start_frame": 0, "end_frame": end, "start_s": 0.0, "end_s": round(end / fps, 4), "last_voiced_frame": last,
            "from": "mouth_timeline", "timeline": str(path)}


def read_request(path: Path) -> tuple[dict[str, Any], fl.Geometry, Image.Image, Clip, list[Clip]]:
    try:
        req = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{PROG}: --request {path}: {exc}") from None
    if not isinstance(req, dict) or req.get("kind") != REQUEST_KIND or req.get("version") != VERSION:
        raise SystemExit(f"{PROG}: --request {path}: a {REQUEST_KIND} version {VERSION}")
    base = path.parent
    fps = req.get("fps", 24)
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0:
        raise SystemExit(f"{PROG}: fps must be a positive number")
    reference = Image.open(base / str(req.get("reference"))).convert("RGBA")
    try:
        geom = fl.Geometry.read(req.get("face"), reference.size)
    except ValueError as exc:
        raise SystemExit(f"{PROG}: {exc}") from None

    def clip(value: Any, what: str, name: str) -> Clip:
        if not isinstance(value, dict) or not isinstance(value.get("frames"), str):
            raise SystemExit(f"{PROG}: {what} needs frames (a folder of keyed frame-*.png)")
        files = _frames(base / value["frames"], what)
        native = Image.open(files[0]).size
        if native[0] != native[1]:
            raise SystemExit(f"{PROG}: {what} frames are {native[0]}x{native[1]}; a clip's frames are square")
        place = _place(value.get("place"), reference.size, what)
        if value.get("place") is None and native != reference.size:
            raise SystemExit(f"{PROG}: {what} frames are {native[0]}x{native[1]}, the reference {reference.size[0]}x{reference.size[1]}; "
                             "give place [x, y, side] for a clip of part of it")
        return Clip(name, files, place, native)

    talk = clip(req.get("talk"), "talk", "talk")
    bodies = []
    names = set()
    for k, value in enumerate(req.get("body") or []):
        name = value.get("name") if isinstance(value, dict) else None
        if not isinstance(name, str) or not name or name in names or not all(c.isalnum() or c in "-_" for c in name):
            raise SystemExit(f"{PROG}: body[{k}].name must be a new name of letters, digits, - and _")
        names.add(name)
        c = clip(value, f"body[{k}]", name)
        c.loop = bool(value.get("loop", False))
        timeline, span = value.get("mouth_timeline"), value.get("own_mouth")
        if timeline is not None and span is not None:
            raise SystemExit(f"{PROG}: body {name}: own_mouth or mouth_timeline, not both")
        if c.loop and (timeline is not None or span is not None):
            raise SystemExit(f"{PROG}: body {name} loops; the loop the others come back to talks only with the face layer")
        if timeline is not None:
            c.own_mouth = own_mouth_from_timeline(base / str(timeline), len(c.files), fps)
        elif span is not None:
            if not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in span) or span[0] != 0 or span[1] <= 0:
                raise SystemExit(f"{PROG}: body {name}: own_mouth must be [0, end seconds]: the clip talks with its own mouth from its start")
            end = int(round(span[1] * fps))
            if end >= len(c.files):
                raise SystemExit(f"{PROG}: body {name}: own_mouth ends at frame {end}, past the clip's last frame {len(c.files) - 1}")
            c.own_mouth = {"start_frame": 0, "end_frame": end, "start_s": 0.0, "end_s": round(end / fps, 4), "from": "own_mouth"}
        if value.get("sound") is not None:
            c.sound = base / str(value["sound"])
            if not c.sound.is_file():
                raise SystemExit(f"{PROG}: body {name}: sound {c.sound} is not a file")
        bodies.append(c)
    loops = [c for c in bodies if c.loop]
    if len(loops) != 1:
        raise SystemExit(f"{PROG}: the request needs exactly one body with loop true (the rest motion the others come back to); it has {len(loops)}")
    return req, geom, reference, talk, bodies


# --- reading ------------------------------------------------------------------------------------------------------

def each(fn, items, jobs: int) -> list[Any]:
    """fn over items, in their order, on `jobs` threads (every frame is read on its own)."""
    items = list(items)
    if jobs <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(fn, items))


_WORK: dict[str, Any] = {}


def _work_init(work: dict[str, Any]) -> None:
    _WORK.update(work)
    # A parent killed outright (a timeout, a crashed test worker) does not take spawned workers with it on
    # Windows: each would go on holding its memory. The worker leaves as soon as its parent is gone.
    parent = multiprocessing.parent_process()
    if parent is not None:
        threading.Thread(target=lambda: (multiprocessing.connection.wait([parent.sentinel]), os._exit(1)),
                         daemon=True).start()


def _anchor(task: tuple[str, tuple[int, int, int]]) -> dict[str, Any]:
    """One frame's anchor and how well it lays the reference's face on the frame (a worker's task)."""
    band, geom = _WORK["band"], _WORK["geom"]
    path, place = task
    feat = fl.features(fl.to_reference(Image.open(path).convert("RGBA"), place, geom.size))
    a = fl.fit(band, feat, geom)
    a["band_residual"] = fl.band_residual(band, feat, fl.matrix(a, geom))
    a["band_residual_place_only"] = fl.band_residual(band, feat, fl.sim(*a["place_only"], 1.0, 0.0, geom.mouth))
    a["band_residual_fixed"] = fl.band_residual(band, feat, np.eye(3))
    return a


def _on_ring(task: tuple[str, tuple[int, int, int], tuple[int, int], dict[str, Any]]) -> dict[str, Any] | None:
    """One body frame's anchor with its place read on the ring round the mouth, or None where its own mouth, read
    through the anchor, is not closed (a worker's task)."""
    ring, geom = _WORK["ring"], _WORK["geom"]
    path, place, native, a = task
    frame = Image.open(path).convert("RGBA")
    body_ref = fl.warp(np.asarray(frame, np.float32), fl.placement(place, native) @ fl.matrix(a, geom), geom.size)
    if fl.expression(fl.mouth_reading(body_ref, geom)) is None:
        return None
    return fl.place_on(ring, fl.features(fl.to_reference(frame, place, geom.size)), a, geom)


def _pool_map(fn, tasks: list[Any], work: dict[str, Any], jobs: int) -> list[Any]:
    if jobs <= 1:
        _work_init(work)
        return [fn(t) for t in tasks]
    with ProcessPoolExecutor(max_workers=jobs, initializer=_work_init, initargs=(work,)) as pool:
        return list(pool.map(fn, tasks, chunksize=2))


def read_anchors(clips: list[Clip], band: fl.Band, geom: fl.Geometry, jobs: int) -> None:
    """Every frame's anchor, read on `jobs` processes (the reading is most of a pack's time), then each clip's
    misreadings taken out (`face_layer.hampel`)."""
    rows = _pool_map(_anchor, [(str(p), c.place) for c in clips for p in c.files], {"band": band, "geom": geom}, jobs)
    at = 0
    for c in clips:
        c.rows = rows[at:at + len(c.files)]
        c.used = fl.hampel(c.rows, cyclic=c.loop)
        at += len(c.files)


def read_places(bodies: list[Clip], ring: fl.Band, geom: fl.Geometry, jobs: int) -> None:
    """Every body frame's place read again on the ring round the mouth where its own mouth is closed, and moved with
    the read frames round it where it is not (`face_layer.on_ring`). The talking clip keeps the band's: its jaw moves
    as it talks, and the layer is cut from it."""
    tasks = [(str(p), c.place, c.native, a) for c in bodies for p, a in zip(c.files, c.used)]
    placed = _pool_map(_on_ring, tasks, {"ring": ring, "geom": geom}, jobs)
    at = 0
    for c in bodies:
        c.used = fl.on_ring(c.used, placed[at:at + len(c.files)], cyclic=c.loop)
        at += len(c.files)


def lower_faces(clip: Clip, geom: fl.Geometry, jobs: int) -> list[np.ndarray]:
    def one(k: int) -> np.ndarray:
        arr = np.asarray(clip.on_reference(k, geom.size), np.float32)
        return fl.lower_face(fl.warp(arr, fl.matrix(clip.used[k], geom), geom.size), geom)

    return each(one, range(len(clip.files)), jobs)


def face_sets(readings: list[dict[str, Any] | None]) -> dict[str, list[int]]:
    """The smile and neutral sets of the talking frames (module docstring "Face frames")."""
    tags = {k: fl.expression(r) for k, r in enumerate(readings) if fl.expression(r) is not None}
    closed = sorted(tags)
    sets: dict[str, list[int]] = {"smile": [], "neutral": [], "turn": []}
    for k in closed:
        sets[tags[k]].append(k)  # type: ignore[index]
    for a, b in zip(closed, closed[1:]):
        run = list(range(a + 1, b))
        if run:
            sets[tags[a] if tags[a] == tags[b] else "turn"] += run  # type: ignore[index]
    return {k: sorted(v) for k, v in sets.items()}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _six(m: np.ndarray) -> list[float]:
    return [round(float(v), 6) for v in (m[0, 0], m[0, 1], m[0, 2], m[1, 0], m[1, 1], m[1, 2])]


def _summary(values: list[float]) -> dict[str, float]:
    v = np.asarray([x for x in values if x == x], float)
    return {"median": round(float(np.median(v)), 3), "p90": round(float(np.percentile(v, 90)), 3), "max": round(float(v.max()), 3)} if v.size else {}


def build(request: Path, out: Path, jobs: int = 0) -> dict[str, Any]:
    req, geom, reference, talk, bodies = read_request(request)
    if out.exists():
        raise SystemExit(f"{PROG}: --out {out} exists; a pack is written whole into a new folder")
    fps = float(req.get("fps", 24))
    jobs = jobs or os.cpu_count() or 1
    ref = np.asarray(reference, np.float32)
    clips = [talk, *bodies]

    # anchors, read on the band round the reference's own lower face: a clip's anchors are the same whatever else
    # the request names; then the lower faces they lay on the reference, joined, give the weight
    ref_face = fl.lower_face(ref, geom)
    if not ref_face.any():
        raise SystemExit(f"{PROG}: no lower face grows from face.lower_face.seeds on the reference: the seeds must sit on its skin")
    w_ref = fl.weight(fl.fill_holes(ref_face), geom)
    read_anchors(clips, fl.Band.read(reference, w_ref, geom), geom, jobs)
    read_places(bodies, fl.Band.ring(reference, w_ref, geom), geom, jobs)
    faces = []
    for c in clips:
        faces.extend(lower_faces(c, geom, jobs))
    face_union = fl.union(ref_face, faces)
    del faces
    w0 = fl.weight(face_union, geom)

    # the talking frames
    def talking(k: int) -> tuple[Any, int, np.ndarray]:
        face_ref = fl.warp(np.asarray(talk.frame(k), np.float32), talk.to_native(k, geom), geom.size)
        return fl.mouth_reading(face_ref, geom), fl.open_area(face_ref, ref, geom), fl.step_features(face_ref, geom)

    readings, areas, feats = (list(v) for v in zip(*each(talking, range(len(talk.files)), jobs)))
    F = np.stack(feats)
    steps = np.abs(np.diff(F, axis=0)).mean(1)
    med = float(np.median(steps))
    D = np.stack([np.abs(F - F[a]).mean(1) for a in range(len(F))]) / med
    sets = face_sets(readings)
    in_set = {k: s for s in ("smile", "neutral") for k in sets[s]}
    if not sets["smile"] and not sets["neutral"]:
        raise SystemExit(f"{PROG}: no talking frame reads a closed mouth: the face clip has no rest to open from")

    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent if out.parent.exists() else None))
    try:
        (tmp / "face").mkdir()
        sprites: dict[int, fl.Sprite] = {}

        def face_row(k: int) -> dict[str, Any]:
            row: dict[str, Any] = {"index": k, "set": in_set.get(k), "open_px": areas[k],
                                   "closed": fl.expression(readings[k]) is not None, "reading": readings[k]}
            if k in in_set:
                sprite, rec = fl.face_sprite(talk.frame(k), talk.to_native(k, geom), w0, ref)
                name = f"face/frame-{k:05d}.png"
                sprite.image.save(tmp / name)
                # laid from the saved picture, as a player reads it
                sprites[k] = fl.Sprite(Image.open(tmp / name).convert("RGBA"), sprite.to_ref)
                row.update({"file": name, "to_ref": _six(sprite.to_ref), "sprite": rec})
            return row

        face_rows = each(face_row, range(len(talk.files)), jobs)

        def lift(k: int) -> float:
            return float(readings[k]["lift"])  # type: ignore[index]

        closed_in = {s: [k for k in sets[s] if face_rows[k]["closed"]] for s in ("smile", "neutral")}

        # the body clips
        body_rows, rests_used = [], set()
        prepared = []
        for c in bodies:
            folder = tmp / "body" / c.name
            folder.mkdir(parents=True)

            def body_frame(k: int, c: Clip = c, folder: Path = folder) -> tuple[Any, ...]:
                cell = folder / f"frame-{k:05d}.png"
                shutil.copyfile(c.files[k], cell)
                body = np.asarray(c.frame(k), np.float32)
                m = c.to_native(k, geom)
                keep, krec = fl.body_keep(body, m, w0, ref, geom)
                overlay = fl.keep_overlay(body, keep)
                kept = None
                if overlay is not None:
                    kname = folder / f"keep-{k:05d}.png"
                    overlay[0].save(kname)
                    kept = {"file": kname.relative_to(tmp).as_posix(), "at": list(overlay[1]), **krec}
                body_ref = fl.warp(body, m, geom.size)
                return cell.relative_to(tmp).as_posix(), kept, m, fl.mouth_reading(body_ref, geom), fl.step_features(body_ref, geom)

            cells, keeps, to_body, own, steps_b = (list(v) for v in zip(*each(body_frame, range(len(c.files)), jobs)))
            Fb = np.stack(steps_b)
            med_b = float(np.median(np.abs(np.diff(Fb, axis=0)).mean(1)))
            entry_frame = c.own_mouth["end_frame"] if c.own_mouth else 0
            prepared.append((c, folder, cells, keeps, to_body, Fb, med_b, own, entry_frame))

        def switch(c: Clip, to_body: list[np.ndarray], keeps: list[Any], Fb: np.ndarray, med_b: float, k: int, rest: int) -> float:
            kept = None if keeps[k] is None else (Image.open(tmp / keeps[k]["file"]).convert("RGBA"), tuple(keeps[k]["at"]))
            laid = fl.lay(c.frame(k), sprites[rest], to_body[k], kept)
            comp = fl.warp(np.asarray(laid, np.float32), to_body[k], geom.size)
            return float(np.abs(fl.step_features(comp, geom) - Fb[k]).mean() / max(med_b, med))

        for c, folder, cells, keeps, to_body, Fb, med_b, own, entry_frame in prepared:
            reading = own[entry_frame]
            if reading is None:
                raise SystemExit(f"{PROG}: body {c.name}: no mouth reads at frame {entry_frame}, where the face layer comes on")
            target = float(reading["lift"])
            entry_set = "smile" if target >= fl.SMILE_FROM else "neutral"
            candidates = [(abs(lift(k) - target), k) for k in closed_in[entry_set]]
            if not candidates:
                raise SystemExit(f"{PROG}: body {c.name} enters in the {entry_set} set, which has no closed talking frame to rest on")
            nearest = min(candidates)[0]
            tied = [k for d, k in candidates if d <= nearest + LIFT_TIE]
            switches = dict(zip(tied, each(lambda k: switch(c, to_body, keeps, Fb, med_b, entry_frame, k), tied, jobs)))
            rest = min(switches, key=lambda k: (round(switches[k], 6), k))
            rests_used.add(rest)
            c_entry = {"frame": entry_frame, "own_lift": target, "set": entry_set, "rest": rest,
                       "candidates": {str(k): {"lift": lift(k), "switch": round(v, 3)} for k, v in switches.items()}}
            body_rows.append({"name": c.name, "loop": c.loop, "cells": cells, "keep": keeps,
                              "to_body": [_six(m) for m in to_body], "ring": [u["ring"] for u in c.used],
                              "ring_read": [k for k, u in enumerate(c.used) if u["ring_read"]],
                              "own_mouth": c.own_mouth, "entry": c_entry, "median_step": med_b})

        for row, (c, folder, cells, keeps, to_body, Fb, med_b, own, entry_frame) in zip(body_rows, prepared):
            row["switch"] = {str(r): [round(v, 3) for v in each(lambda k, r=r: switch(c, to_body, keeps, Fb, med_b, k, r), range(len(c.files)), jobs)]
                             for r in sorted(rests_used)}
            if c.sound is not None:
                dest = folder / ("sound" + c.sound.suffix.lower())
                shutil.copyfile(c.sound, dest)
                row["sound"] = dest.relative_to(tmp).as_posix()
            row["own_lift"] = [None if r is None else r["lift"] for r in own]

        top = max(areas)
        levels = MOUTH_RULES["normal_top"]
        rules = {**MOUTH_RULES, **PICK_RULES, "levels": levels,
                 "open_px": [round(top * level / levels, 1) for level in range(levels + 1)],
                 "jump_line": fl.JUMP_LINE, "enter_line": round(float(steps.max() / med), 3)}
        shutil.copyfile(PLAYER_PATH, tmp / PLAYER_PATH.name)
        np.save(tmp / "weight.npy", w0)
        pack = {"kind": PACK_KIND, "version": VERSION, "fps": fps, "size": list(geom.size), "rules": rules,
                "face": {"geometry": req["face"], "frames": face_rows, "sets": {s: {"frames": sets[s], "closed": closed_in[s]} for s in ("smile", "neutral")},
                         "steps": [[round(float(v), 3) for v in row] for row in D], "median_step": med},
                "body": body_rows, "player": {"path": PLAYER_PATH.name, "export": "createFacePlayer"}}
        files = sorted(p for p in tmp.rglob("*") if p.is_file())
        pack["files"] = {p.relative_to(tmp).as_posix(): _digest(p) for p in files}
        (tmp / "face-pack.json").write_text(json.dumps(pack, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        report = {"request": str(request), "pack": str(out), "weight_px": int((w0 > 0).sum()), "union_px": int(face_union.sum()),
                  "talk": _clip_report(talk) | {"sets": {k: v for k, v in sets.items()}, "median_step": med,
                                                "open_px_max": top, "enter_line": rules["enter_line"]},
                  "body": [{**_clip_report(c), "own_mouth": row["own_mouth"], "entry": row["entry"],
                            "hand_frames": [k for k, kp in enumerate(row["keep"]) if kp and kp["hand_px"] > 0],
                            "switch_within_line": {r: sum(v <= fl.JUMP_LINE for v in vals) for r, vals in row["switch"].items()}}
                           for c, row in zip(bodies, body_rows)]}
        (tmp / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return report


def _clip_report(c: Clip) -> dict[str, Any]:
    rows, used = c.rows, c.used
    return {"name": c.name, "frames": len(c.files), "place": list(c.place),
            "anchor": {k: [round(min(u[k] for u in used), 3), round(max(u[k] for u in used), 3)] for k in ("tx", "ty", "s", "th")},
            "band_residual": _summary([r["band_residual"] for r in rows]),
            "band_residual_place_only": _summary([r["band_residual_place_only"] for r in rows]),
            "band_residual_fixed": _summary([r["band_residual_fixed"] for r in rows]),
            "misread": [k for k, u in enumerate(used) if u["misread"]],
            **({"ring_read": sum(u["ring_read"] for u in used), "ring_move": _summary([float(np.hypot(*u["ring"])) for u in used])}
               if used and "ring" in used[0] else {}),
            "wide_grid": [k for k, r in enumerate(rows) if r["wide_grid"]],
            "wide_search": [k for k, r in enumerate(rows) if r["search_px"] > fl.SEARCH],
            "search_edge": [k for k, r in enumerate(rows) if r["edge"]]}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", type=Path, required=True,
                        help="the face request JSON: the reference and its face, the talking clip, the body clips (docs/face-parts.md)")
    parser.add_argument("--out", type=Path, required=True, help="the pack folder to write (must not exist)")
    parser.add_argument("--jobs", type=int, default=0, help="threads that read frames at once (default: one a CPU)")


def run(**kwargs: object) -> int:
    report = build(Path(str(kwargs["request"])), Path(str(kwargs["out"])), jobs=int(kwargs.get("jobs") or 0))  # type: ignore[arg-type]
    print(json.dumps({"pack": report["pack"], "sets": {k: len(v) for k, v in report["talk"]["sets"].items()},
                      "body": [{"name": b["name"], "own_mouth": b["own_mouth"] and [b["own_mouth"]["start_s"], b["own_mouth"]["end_s"]],
                                "entry": {k: b["entry"][k] for k in ("frame", "set", "rest")}, "hand_frames": len(b["hand_frames"])}
                               for b in report["body"]]}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog=f"sprite-gen {PROG}", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
