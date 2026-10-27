# SPDX-License-Identifier: Apache-2.0
"""The face layer (`sprite-gen video-face`, `face-bake`, the player `sprite-gen-face-v1.mjs`) on a synthetic talking
character (tests/talking_face.py): the pack's sets, entries, own-mouth spans, anchors and what stays the body's; the
player's mouth rule and pick against the rules they were moved from; the live player's queue on a fake audio clock; a
baked line.

The pack is made once for the module. The player's tests run it with node and skip, saying so, where there is none.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import talking_face as tf
from sprite_gen.video import face_bake, face_layer as fl, face_pack
from sprite_gen.video import parts as parts_mod

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the face player cannot run")


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    root = tmp_path_factory.mktemp("face")
    request = tf.write_case(root / "case")
    # Two workers: the default (one per core) multiplied by parallel test workers exhausts memory.
    face_pack.build(request, root / "pack", jobs=2)
    pack = json.loads((root / "pack" / "face-pack.json").read_text(encoding="utf-8"))
    return root, pack


def body(pack, name):
    return next(b for b in pack["body"] if b["name"] == name)


def matrix(six):
    return np.array([six[:3], six[3:], [0.0, 0.0, 1.0]])


def anchor(six):
    """(dx, dy, size, tilt in degrees) of a body frame's anchor, read about the mouth."""
    m = matrix(six)
    c = np.array([*tf.MOUTH, 1.0])
    d = m @ c - c
    return d[0], d[1], math.sqrt(abs(np.linalg.det(m[:2, :2]))), math.degrees(math.atan2(m[1, 0], m[0, 0]))


def laid(root, pack, name, k, face):
    b = body(pack, name)
    row = pack["face"]["frames"][face]
    sprite = fl.Sprite(Image.open(root / "pack" / row["file"]).convert("RGBA"), matrix(row["to_ref"]))
    keep = b["keep"][k]
    overlay = None if keep is None else (Image.open(root / "pack" / keep["file"]).convert("RGBA"), tuple(keep["at"]))
    cell = Image.open(root / "pack" / b["cells"][k]).convert("RGBA")
    to_body = matrix(b["to_body"][k])
    w_ell = fl.warp(np.load(root / "pack" / "weight.npy"), np.linalg.inv(to_body), cell.size)
    return np.asarray(fl.lay(cell, sprite, to_body, overlay)), np.asarray(cell), w_ell


# --- the pack -----------------------------------------------------------------------------------------------------

def test_talking_frames_split_into_smile_and_neutral_sets(case):
    root, pack = case
    sets = pack["face"]["sets"]
    assert sets["neutral"]["frames"] == tf.TALK_SETS["neutral"]
    assert sets["smile"]["frames"] == tf.TALK_SETS["smile"]
    for k, row in enumerate(pack["face"]["frames"]):
        assert ("file" in row) == (k not in tf.TALK_SETS["turn"]), k  # the turn between the two is cut for neither


def test_the_entry_set_is_read_off_the_clip_mouth_where_the_layer_comes_on(case):
    _, pack = case
    idle, greet = body(pack, "idle")["entry"], body(pack, "greet")["entry"]
    assert idle["frame"] == 0 and idle["set"] == "neutral" and idle["rest"] in pack["face"]["sets"]["neutral"]["closed"]
    assert greet["frame"] == tf.GREET_OWN_END and greet["set"] == "smile" and greet["rest"] in pack["face"]["sets"]["smile"]["closed"]
    assert greet["own_lift"] >= fl.SMILE_FROM > idle["own_lift"]


def test_own_mouth_runs_to_the_first_closed_frame_after_the_last_sound(case):
    _, pack = case
    own = body(pack, "greet")["own_mouth"]
    assert own["end_frame"] == tf.GREET_OWN_END and own["end_s"] == round(tf.GREET_OWN_END / 24, 4)
    assert body(pack, "idle")["own_mouth"] is None


def _timeline(path, rows):
    path.write_text("frame\tstate\tsound\n" + "".join(f"{k}\t{s}\t{v}\n" for k, (s, v) in enumerate(rows)), encoding="utf-8")
    return path


def test_a_mouth_closed_while_it_still_sounds_is_not_the_end(tmp_path):
    rows = [("closed", "silent"), ("open", "voiced"), ("closed", "voiced"), ("open", "voiced"), ("open", "silent"),
            ("open", "silent"), ("closed", "silent"), ("closed", "silent")]
    own = face_pack.own_mouth_from_timeline(_timeline(tmp_path / "t.tsv", rows), len(rows), 24)
    assert (own["end_frame"], own["last_voiced_frame"]) == (6, 3)


def test_a_timeline_whose_mouth_never_closes_or_misses_frames_is_refused(tmp_path):
    open_end = [("closed", "silent"), ("open", "voiced"), ("open", "silent"), ("open", "silent")]
    with pytest.raises(SystemExit, match="never reads closed"):
        face_pack.own_mouth_from_timeline(_timeline(tmp_path / "a.tsv", open_end), 4, 24)
    with pytest.raises(SystemExit, match="rows"):
        face_pack.own_mouth_from_timeline(_timeline(tmp_path / "b.tsv", open_end), 5, 24)


def test_a_head_far_off_its_place_is_read_where_it_went(case):
    _, pack = case
    rows = [anchor(six) for six in body(pack, "jump")["to_body"]]
    for k, dy in enumerate(tf.JUMP):
        assert abs(rows[k][1] - dy) < 1.5 and abs(rows[k][0]) < 1.5, (k, rows[k], dy)


def test_size_and_tilt_are_read_with_the_place(case):
    _, pack = case
    rows = [anchor(six) for six in body(pack, "greet")["to_body"]]
    for k in range(len(tf.GREET)):
        w = math.sin(math.pi * k / 15)
        assert abs(rows[k][2] - (1 + 0.02 * w)) < 0.01 and abs(rows[k][3] - 3 * w) < 0.7, (k, rows[k])


def test_a_head_that_turns_is_placed_where_its_mouth_is(case):
    """A head that turns moves its hair, headset and cheeks further than its mouth: the band reads them moved, and its
    place would carry the layer off the mouth. Size and tilt stay the band's; the place is read on the skin round the
    frame's closed mouth, and a frame whose mouth is open takes the move of the closed frames round it."""
    _, pack = case
    rows = [anchor(six) for six in body(pack, "turn")["to_body"]]
    for k, (w, mouth) in enumerate(zip(tf.TURN, tf.TURN_MOUTH)):
        dx, dy, _, th = rows[k]
        assert math.hypot(dx, dy) < (1.0 if mouth == "neutral" else 2.0), (k, rows[k])
        if w > 0.9:
            assert th < -2.5, (k, rows[k])


def test_a_hand_over_the_cheek_stays_on_top_of_the_layer(case):
    root, pack = case
    keep = body(pack, "cheek")["keep"]
    assert [k for k, rec in enumerate(keep) if rec and rec["hand_px"]] == list(tf.HAND_FRAMES)
    hand = tf.hand_mask()
    for k in tf.HAND_FRAMES:
        out, cell, w_ell = laid(root, pack, "cheek", k, tf.TALK.index(4))
        under = hand & (w_ell > 0.5)
        assert under.sum() > 400
        assert (np.abs(out[under].astype(int) - cell[under]).max(-1) <= 2).mean() > 0.97, k


def test_a_tongue_in_the_body_s_own_open_mouth_is_not_a_hand_s(case):
    """A finger at the corner of the body's own open mouth joins the mouth to the hand; the tongue in it is light and
    warm like the hand's skin, but it is the mouth's: the layer covers it."""
    root, pack = case
    tongue = parts_mod._erode(tf.tongue_mask(3), 2)
    assert tongue.sum() > 100
    for k in tf.HAND_FRAMES:
        out, cell, _ = laid(root, pack, "cheek", k, tf.TALK.index("neutral"))
        assert (np.abs(out[tongue].astype(int) - cell[tongue]).max(-1) > 30).mean() > 0.9, k


def test_the_boom_and_the_choker_stay_the_body_s_and_nothing_changes_outside_the_weight(case):
    root, pack = case
    for name, k in (("idle", 0), ("idle", 5), ("greet", 12), ("jump", 4)):
        out, cell, w_ell = laid(root, pack, name, k, tf.TALK.index(4))
        assert np.array_equal(out[w_ell <= 0], cell[w_ell <= 0]), (name, k)
        rgb = cell[..., :3].astype(int)
        dark_grey = (rgb.max(-1) < 60) & (rgb.max(-1) - rgb.min(-1) < 10) & (cell[..., 3] == 255) & (w_ell > 0)
        assert dark_grey.sum() > 50
        assert (np.abs(out[dark_grey].astype(int) - cell[dark_grey]).max(-1) <= 2).mean() > 0.95, (name, k)


def test_the_rest_comes_on_and_goes_off_within_the_jump_line_on_the_loop(case):
    _, pack = case
    idle = body(pack, "idle")
    assert all(v <= pack["rules"]["jump_line"] for v in idle["switch"][str(idle["entry"]["rest"])])


def test_every_file_of_the_pack_is_listed_with_its_digest(case):
    root, pack = case
    files = sorted(p.relative_to(root / "pack").as_posix() for p in (root / "pack").rglob("*") if p.is_file())
    assert set(pack["files"]) == set(files) - {"face-pack.json", "report.json"}
    for name, digest in pack["files"].items():
        assert hashlib.sha256((root / "pack" / name).read_bytes()).hexdigest() == digest
    assert "sprite-gen-face-v1.mjs" in pack["files"]


def test_a_pack_is_written_into_a_new_folder_only(case, tmp_path):
    root, _ = case
    with pytest.raises(SystemExit, match="exists"):
        face_pack.build(root / "case" / "face-request.json", root / "pack")


# --- the player ---------------------------------------------------------------------------------------------------

def _flap(x, sr, rules, surprise, fps=24):
    """The flap as it was first written (numpy, Python's round), the rule the player moved."""
    nf = int(np.ceil(len(x) / sr * fps))
    hop = sr // 96
    env = np.array([np.sqrt(np.mean(x[i:i + 2 * hop] ** 2) + 1e-12) for i in range(0, len(x) - 2 * hop, hop)])
    env = np.convolve(env, np.ones(3) / 3, "same")
    env /= env.max()
    cand = []
    for i in range(1, len(env) - 1):
        if env[i] >= env[i - 1] and env[i] > env[i + 1] and env[i] > rules["closed_under"]:
            lo, hi = max(0, i - 10), min(len(env), i + 11)
            if env[i] - max(env[lo:i].min(), env[i + 1:hi].min()) > rules["prominence"]:
                cand.append(i)
    syl = []
    for i in cand:
        if syl and (i - syl[-1]) / 96 < rules["min_gap_s"]:
            if env[i] > env[syl[-1]]:
                syl[-1] = i
        else:
            syl.append(i)
    top = rules["surprise_top"] if surprise else rules["normal_top"]
    louds = [float(env[i]) for i in syl]
    lo_l, hi_l = (min(louds), max(louds)) if louds else (0, 1)
    lo_o, hi_o = rules["open_lo_hi"]
    mouth = np.zeros(nf, int)
    for i, loud in zip(syl, louds):
        f = int(round(i / 96 * fps)) - rules["lead_frames"]
        if not 0 <= f < nf:
            continue
        size = top if surprise and loud >= rules["surprise_at"] else int(round(lo_o + (loud - lo_l) / max(hi_l - lo_l, 1e-6) * (hi_o - lo_o)))
        mouth[f] = max(mouth[f], size)
        if f > 0:
            mouth[f - 1] = max(mouth[f - 1], int(np.ceil(size * 0.5)))
        if f + 1 < nf:
            mouth[f + 1] = max(mouth[f + 1], int(size * 0.4))
    for _ in range(20):
        changed = False
        for i in range(1, nf):
            if mouth[i] - mouth[i - 1] > rules["max_step"]:
                mouth[i - 1] = mouth[i] - rules["max_step"]; changed = True
            if mouth[i - 1] - mouth[i] > rules["max_step"]:
                mouth[i] = mouth[i - 1] - rules["max_step"]; changed = True
            if mouth[i] > rules["normal_top"] and mouth[i - 1] < rules["wide_from"]:
                mouth[i - 1] = rules["wide_from"]; changed = True
        if not changed:
            break
    return mouth.tolist()


def _viterbi(pack, levels, set_name, rest):
    """The draft's pick (every talking frame a state, those outside the set never worth showing), on the pack's steps."""
    r = pack["rules"]
    D = np.array(pack["face"]["steps"], float)
    n = len(D)
    allowed = np.zeros(n, bool)
    allowed[pack["face"]["sets"][set_name]["frames"]] = True
    L = np.where(allowed, np.array([f["open_px"] for f in pack["face"]["frames"]], float), 1e9)
    target = np.array([r["open_px"][min(v, len(r["open_px"]) - 1)] for v in levels])
    scale = r["open_px"][1]
    enter = (D[rest] <= r["enter_line"]) & allowed
    leave = (D[:, rest] <= r["enter_line"]) & allowed
    IDLE, INF = n, 1e18
    cost = np.full(n + 1, INF); cost[IDLE] = 0.0
    back = np.zeros((len(target), n + 1), int)
    near = D <= r["jump_line"]
    for t in range(len(target)):
        new = np.full(n + 1, INF); arg = np.zeros(n + 1, int)
        d = L - target[t]
        emit = np.where(d < 0, -d * r["under_weight"], d) / scale
        for j in range(n):
            best, how = INF, -1
            if j > 0 and cost[j - 1] < best:
                best, how = cost[j - 1], j - 1
            if cost[j] + r["hold_cost"] < best:
                best, how = cost[j] + r["hold_cost"], j
            if enter[j] and cost[IDLE] + r["edge_cost"] < best:
                best, how = cost[IDLE] + r["edge_cost"], IDLE
            src = np.nonzero(near[np.minimum(np.arange(n) + 1, n - 1), j])[0]
            if len(src):
                c = cost[src] + r["jump_cost"] + D[np.minimum(src + 1, n - 1), j]
                i = int(np.argmin(c))
                if c[i] < best:
                    best, how = c[i], int(src[i])
            new[j] = best + emit[j]; arg[j] = how
        leavers = [(cost[j] + r["edge_cost"], j) for j in range(n) if leave[j]]
        lb = min(leavers) if leavers else (INF, -1)
        if cost[IDLE] <= lb[0]:
            new[IDLE], arg[IDLE] = cost[IDLE] + target[t] / scale, IDLE
        else:
            new[IDLE], arg[IDLE] = lb[0] + target[t] / scale, lb[1]
        cost, back[t] = new, arg
    path = [IDLE]
    for t in range(len(target) - 1, 0, -1):
        path.append(back[t][path[-1]])
    return [None if s == IDLE else int(s) for s in path[::-1]]


PLAYER_RUN = r"""
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const [player, inPath, outPath] = process.argv.slice(2);
const m = await import(pathToFileURL(player).href);
const job = JSON.parse(readFileSync(inPath, "utf8"));
const pack = JSON.parse(readFileSync(job.pack, "utf8"));
const out = {};
if (job.samples) out.track = m.mouthTrack(Float32Array.from(job.samples), job.rate, pack.rules, { fps: 24, tags: job.tags });
if (job.pick) out.pick = m.pickFaces(pack, job.pick.levels, job.pick.set, job.pick.rest);
if (job.scene) out.scene = m.planScene(pack, job.scene);
writeFileSync(outPath, JSON.stringify(out));
"""


def player(root, job):
    (root / "run.mjs").write_text(PLAYER_RUN, encoding="utf-8")
    (root / "job.json").write_text(json.dumps({"pack": str(root / "pack" / "face-pack.json"), **job}), encoding="utf-8")
    proc = subprocess.run([NODE, str(root / "run.mjs"), str(root / "pack" / "sprite-gen-face-v1.mjs"), str(root / "job.json"),
                           str(root / "out.json")], capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return json.loads((root / "out.json").read_text(encoding="utf-8"))


def padded(rate=48000):
    return np.concatenate([np.zeros(rate // 2, np.float32), tf.voice(rate), np.zeros(int(0.6 * rate), np.float32)])


@needs_node
@pytest.mark.parametrize("tags", [[], ["[surprised]"]])
def test_the_player_s_mouth_rule_is_the_flap_as_first_written(case, tags):
    root, pack = case
    x = padded()
    out = player(root, {"samples": x.tolist(), "rate": 48000, "tags": tags})
    assert out["track"]["levels"] == _flap(x.astype(np.float64), 48000, pack["rules"], bool(tags))
    assert max(out["track"]["levels"]) >= 4


@needs_node
@pytest.mark.parametrize("set_name", ["neutral", "smile"])
def test_the_player_s_pick_is_the_draft_s_viterbi_inside_one_set(case, set_name):
    root, pack = case
    levels = _flap(padded().astype(np.float64), 48000, pack["rules"], False)
    rest = pack["face"]["sets"][set_name]["closed"][0]
    path = player(root, {"pick": {"levels": levels, "set": set_name, "rest": rest}})["pick"]["path"]
    assert path == _viterbi(pack, levels, set_name, rest)
    shown = [j for j in path if j is not None]
    assert shown and set(shown) <= set(pack["face"]["sets"][set_name]["frames"])


@needs_node
def test_a_line_said_on_a_clip_that_talks_waits_for_its_own_mouth(case):
    root, pack = case
    levels = _flap(padded().astype(np.float64), 48000, pack["rules"], False)
    scene = player(root, {"scene": {"play": "greet", "levels": levels}})["scene"]
    frames = scene["frames"]
    assert len(frames) == tf.GREET_OWN_END + len(levels)
    assert all(f["face"] is None and f["body"] == "greet" and f["cell"] == t for t, f in enumerate(frames[:tf.GREET_OWN_END]))
    assert scene["line_start"] == tf.GREET_OWN_END and scene["wait"] == 0 and scene["set"] == "smile"
    assert all(f["face"] in pack["face"]["sets"]["smile"]["frames"] for f in frames[tf.GREET_OWN_END:])
    greet_cells = len(body(pack, "greet")["cells"])
    assert [(f["body"], f["cell"]) for f in frames[greet_cells:greet_cells + 3]] == [("idle", 0), ("idle", 1), ("idle", 2)]
    loop = player(root, {"scene": {"start": 5, "levels": levels}})["scene"]
    assert [f["cell"] for f in loop["frames"][:9]] == [5, 6, 7, 8, 9, 10, 11, 0, 1] and loop["set"] == "neutral"


@needs_node
def test_a_line_waits_for_a_frame_the_layer_can_come_on_at(case, tmp_path):
    """No voice on a frame whose mouth cannot move: where the switch to the rest is over the jump line after the
    clip's own mouth, the line starts at the first frame it is within, and the frames between show the clip as it is."""
    root, pack = case
    greet = body(pack, "greet")
    rest = str(greet["entry"]["rest"])
    late = json.loads(json.dumps(pack))
    for k in range(tf.GREET_OWN_END, tf.GREET_OWN_END + 3):
        body(late, "greet")["switch"][rest][k] = pack["rules"]["jump_line"] + 1
    (tmp_path / "late.json").write_text(json.dumps(late), encoding="utf-8")
    levels = _flap(padded().astype(np.float64), 48000, pack["rules"], False)
    scene = player(root, {"pack": str(tmp_path / "late.json"), "scene": {"play": "greet", "levels": levels}})["scene"]
    assert (scene["own_mouth"], scene["line_start"], scene["wait"]) == (tf.GREET_OWN_END, tf.GREET_OWN_END + 3, 3)
    assert len(scene["frames"]) == tf.GREET_OWN_END + 3 + len(levels)
    assert all(f["face"] is None for f in scene["frames"][:tf.GREET_OWN_END + 3])
    assert scene["frames"][tf.GREET_OWN_END + 3]["face"] is not None


# --- the live player ----------------------------------------------------------------------------------------------

LIVE_RUN = r"""
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const [player, inPath, outPath] = process.argv.slice(2);
const { createFacePlayer, mouthTrack } = await import(pathToFileURL(player).href);
const job = JSON.parse(readFileSync(inPath, "utf8"));
const pack = JSON.parse(readFileSync(job.pack, "utf8"));
const fps = pack.fps;
let frame = 0;

// A decoded voice, as an AudioBuffer holds it: a syllable every quarter second.
class Voice {
  constructor(id, seconds, rate = 48000) {
    this.id = id;
    this.sampleRate = rate;
    this.numberOfChannels = 1;
    this.length = Math.round(seconds * rate);
    this.duration = this.length / rate;
    this.data = new Float32Array(this.length);
    for (let s = 0, at = 0.08; at < seconds - 0.15; s++, at += 0.25) {
      const loud = [0.6, 1, 0.45, 0.8][s % 4];
      for (let i = Math.floor(at * rate); i < Math.min(this.length, (at + 0.14) * rate); i++) {
        const t = i / rate, w = (t - at) / 0.14;
        const tone = Math.sin(2 * Math.PI * 220 * t) + 0.5 * Math.sin(2 * Math.PI * 440 * t);
        if (w > 0) this.data[i] += 0.3 * loud * Math.sin(Math.PI * w) ** 2 * tone;
      }
    }
  }
  getChannelData() { return this.data; }
}
globalThis.AudioBuffer = Voice;

// A sound by URL loads (or fails) at the frame the job names; a clip's own sound at once unless the job gives it one.
const loads = new Map();
const due = [];
for (const b of pack.body) {
  const s = job.sounds?.[b.name];
  if (s) b.sound = `sound/${b.name}`;
  if (b.sound) loads.set(b.sound, { voice: new Voice(b.name, s?.seconds ?? b.cells.length / fps), arrives: s?.arrives, fails: s?.fails });
}
globalThis.fetch = (url) => new Promise((resolve, reject) => {
  const v = loads.get(url);
  const arrive = () => (v.fails ? reject(new Error(`${url} did not load`)) : resolve({ arrayBuffer: async () => url }));
  due.push([v.arrives ?? frame, arrive]);
});
const sounds = [];
const audio = {
  currentTime: 0,
  destination: {},
  decodeAudioData: async (url) => loads.get(url).voice,
  createBufferSource: () => ({
    buffer: null,
    connect() {},
    start(at, offset = 0, seconds) {
      sounds.push({ id: this.buffer.id, frame: Math.round(at * fps), frames: (seconds ?? this.buffer.duration - offset) * fps });
    },
  }),
};

// What each frame draws: the body cell, then the face over it.
const cellOf = new Map(pack.body.flatMap((b) => b.cells.map((path, k) => [path, [b.name, k]])));
const drawn = [];
globalThis.Image = class { complete = true; };
const ctx = {
  setTransform() {},
  clearRect() {},
  drawImage(im) {
    if (cellOf.has(im.src)) drawn[frame] = [...cellOf.get(im.src), null];
    else if ("face" in im) drawn[frame][2] = im.face;
  },
};
pack.url = (path) => path;
pack.images = {
  face: pack.face.frames.map((_, k) => ({ face: k })),
  keep: Object.fromEntries(pack.body.map((b) => [b.name, b.keep.map((k) => k && { width: 1, height: 1 })])),
};

const ticks = [];
globalThis.requestAnimationFrame = (tick) => ticks.push(tick);
const p = createFacePlayer({ width: pack.size[0], height: pack.size[1], getContext: () => ctx }, pack, { audioContext: audio });
const levels = {};
const settled = {};
function ask(r) {
  if (r.play) return p.play(r.play);
  const v = new Voice(r.say, r.seconds);
  levels[r.say] = mouthTrack(v.data, v.sampleRate, pack.rules, { fps }).levels;
  if (r.arrives === undefined && !r.fails) return p.say(v);
  loads.set(`voice/${r.say}`, { voice: v, arrives: r.arrives, fails: r.fails });
  return p.say(`voice/${r.say}`);
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
p.start();
for (; frame < job.frames; frame++) {
  audio.currentTime = (frame + 0.5) / fps;
  for (const r of job.requests.filter((r) => r.frame === frame)) {
    const id = r.play ?? r.say;
    ask(r).then(() => { settled[id] = { frame, error: null }; }, (e) => { settled[id] = { frame, error: String(e.message) }; });
  }
  for (const d of due.filter((d) => d[0] <= frame)) due.splice(due.indexOf(d), 1)[0][1]();
  await settle();
  for (const tick of ticks.splice(0)) tick();
  await settle();
}
p.stop();
writeFileSync(outPath, JSON.stringify({ frames: drawn, sounds, levels, settled }));
"""


def live(root, job):
    """The live player (`createFacePlayer`) run by node on a fake audio clock, one frame at a time, with requests made
    at the frames the job names: what each frame drew (body, cell, face), each sound's start frame and length in
    frames, each line's mouth levels and the frame each request settled on."""
    (root / "live.mjs").write_text(LIVE_RUN, encoding="utf-8")
    (root / "live.json").write_text(json.dumps({"pack": str(root / "pack" / "face-pack.json"), **job}), encoding="utf-8")
    proc = subprocess.run([NODE, str(root / "live.mjs"), str(root / "pack" / "sprite-gen-face-v1.mjs"), str(root / "live.json"),
                           str(root / "live-out.json")], capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    out = json.loads((root / "live-out.json").read_text(encoding="utf-8"))
    ids = [s["id"] for s in out["sounds"]]
    assert len(ids) == len(set(ids)), ids
    out["sounds"] = {s["id"]: s for s in out["sounds"]}
    return out


def shown(plan):
    return [[f["body"], f["cell"], f["face"]] for f in plan["frames"]]


def talks(out, pack, line):
    """Every frame of a line's voice shows the layer, in one expression set."""
    at = out["sounds"][line]["frame"]
    faces = [out["frames"][t][2] for t in range(at, at + len(out["levels"][line]))]
    return None not in faces and any(set(faces) <= set(s["frames"]) for s in pack["face"]["sets"].values())


@needs_node
def test_two_lines_asked_for_during_a_clip_s_own_mouth_run_one_after_another(case):
    """Two lines asked for while the greeting talks with its own mouth: the first is planned with the clip and starts
    where the layer comes on after the span; the second waits until the first's voice has ended, and the first keeps
    its own frames."""
    root, pack = case
    out = live(root, {"frames": 80, "requests": [{"frame": 0, "play": "greet"}, {"frame": 3, "say": "A", "seconds": 1.0},
                                                 {"frame": 3, "say": "B", "seconds": 0.5}]})
    a, b = out["sounds"]["A"], out["sounds"]["B"]
    t0 = out["frames"].index(["greet", 0, None])
    plan = player(root, {"scene": {"play": "greet", "levels": out["levels"]["A"]}})["scene"]
    assert a["frame"] == t0 + plan["line_start"] == t0 + tf.GREET_OWN_END
    assert b["frame"] >= a["frame"] + a["frames"], (a, b)
    assert out["frames"][t0:t0 + len(plan["frames"])] == shown(plan)
    assert talks(out, pack, "A") and talks(out, pack, "B")
    assert sorted(out["settled"]) == ["A", "B", "greet"] and all(s["error"] is None for s in out["settled"].values())


@needs_node
def test_three_lines_asked_for_on_the_loop_run_one_after_another_in_the_order_asked(case):
    root, pack = case
    out = live(root, {"frames": 90, "requests": [{"frame": 2, "say": "A", "seconds": 0.8},
                                                 {"frame": 2, "say": "B", "seconds": 0.5},
                                                 {"frame": 5, "say": "C", "seconds": 0.6}]})
    a, b, c = (out["sounds"][k] for k in "ABC")
    assert b["frame"] >= a["frame"] + a["frames"] and c["frame"] >= b["frame"] + b["frames"], (a, b, c)
    assert all(talks(out, pack, k) for k in "ABC")
    settled = out["settled"]
    assert sorted(settled) == ["A", "B", "C"] and all(s["error"] is None for s in settled.values())
    assert settled["A"]["frame"] < settled["B"]["frame"] < settled["C"]["frame"]


@needs_node
def test_a_line_asked_for_after_the_own_mouth_waits_for_the_line_planned_with_the_clip(case):
    """One line asked for during the greeting's own mouth, one after the span while the first still talks: the first
    starts after the span with the clip, the second when the first's voice has ended."""
    root, pack = case
    out = live(root, {"frames": 80, "requests": [{"frame": 0, "play": "greet"}, {"frame": 4, "say": "A", "seconds": 1.0},
                                                 {"frame": 14, "say": "B", "seconds": 0.5}]})
    a, b = out["sounds"]["A"], out["sounds"]["B"]
    t0 = out["frames"].index(["greet", 0, None])
    assert t0 + tf.GREET_OWN_END < 14 < a["frame"] + a["frames"]
    plan = player(root, {"scene": {"play": "greet", "levels": out["levels"]["A"]}})["scene"]
    assert a["frame"] == t0 + plan["line_start"]
    assert b["frame"] >= a["frame"] + a["frames"], (a, b)
    assert out["frames"][t0:t0 + len(plan["frames"])] == shown(plan)
    assert talks(out, pack, "A") and talks(out, pack, "B")


@needs_node
def test_a_line_whose_voice_loads_late_still_goes_before_the_line_asked_for_after_it(case):
    """A request takes its place in the queue when it is made, not when its sound has loaded."""
    root, pack = case
    out = live(root, {"frames": 70, "requests": [{"frame": 1, "say": "A", "seconds": 0.6, "arrives": 6},
                                                 {"frame": 2, "say": "B", "seconds": 0.5}]})
    a, b = out["sounds"]["A"], out["sounds"]["B"]
    assert a["frame"] > 6 and b["frame"] >= a["frame"] + a["frames"], (a, b)
    assert talks(out, pack, "A") and talks(out, pack, "B")


@needs_node
def test_a_line_asked_for_right_after_a_clip_whose_sound_loads_late_is_said_after_its_own_mouth(case):
    """As face-bake --play plans them: the clip and its own sound first, then the line where the layer comes on after
    the span, though the line's voice was ready long before the clip's sound."""
    root, pack = case
    out = live(root, {"frames": 60, "sounds": {"greet": {"seconds": 0.5, "arrives": 5}},
                      "requests": [{"frame": 0, "play": "greet"}, {"frame": 1, "say": "A", "seconds": 1.0}]})
    t0 = out["frames"].index(["greet", 0, None])
    plan = player(root, {"scene": {"play": "greet", "levels": out["levels"]["A"]}})["scene"]
    assert out["sounds"]["greet"]["frame"] == t0 > 5
    assert out["sounds"]["A"]["frame"] == t0 + plan["line_start"]
    assert out["frames"][t0:t0 + len(plan["frames"])] == shown(plan)


@needs_node
@pytest.mark.parametrize("arrives", [13, 20])
def test_a_line_whose_voice_loads_only_after_the_clip_s_own_mouth_waits_for_the_clip_to_end(case, arrives):
    """Asked for during the greeting's own mouth, its voice loaded after the span (13) or after the clip (20): the line
    is not planned with the clip, which plays to its end with the layer off, and starts on the loop after it."""
    root, pack = case
    out = live(root, {"frames": 70, "requests": [{"frame": 0, "play": "greet"},
                                                 {"frame": 4, "say": "A", "seconds": 0.5, "arrives": arrives}]})
    t0 = out["frames"].index(["greet", 0, None])
    cells = len(body(pack, "greet")["cells"])
    assert t0 + tf.GREET_OWN_END <= arrives
    assert out["frames"][t0:t0 + cells] == [["greet", k, None] for k in range(cells)]
    assert out["sounds"]["A"]["frame"] > max(t0 + cells, arrives) and talks(out, pack, "A")
    assert out["settled"]["greet"]["frame"] <= out["settled"]["A"]["frame"]


@needs_node
def test_a_line_asked_for_right_after_a_clip_that_fails_plays_on_the_loop(case):
    root, pack = case
    out = live(root, {"frames": 50, "sounds": {"greet": {"seconds": 0.5, "arrives": 3, "fails": True}},
                      "requests": [{"frame": 0, "play": "greet"}, {"frame": 1, "say": "A", "seconds": 0.5}]})
    assert "did not load" in out["settled"]["greet"]["error"] and out["settled"]["A"]["error"] is None
    assert ["greet", 0, None] not in out["frames"] and out["sounds"]["A"]["frame"] > 3 and talks(out, pack, "A")


@needs_node
def test_a_request_that_fails_rejects_and_the_next_goes_on(case):
    """The queue drops nothing of its own accord: a line whose voice does not load rejects, and the line after it plays."""
    root, pack = case
    out = live(root, {"frames": 40, "requests": [{"frame": 1, "say": "A", "seconds": 0.5, "arrives": 3, "fails": True},
                                                 {"frame": 2, "say": "B", "seconds": 0.5}]})
    assert "A" not in out["sounds"] and "did not load" in out["settled"]["A"]["error"]
    assert out["settled"]["B"]["error"] is None and talks(out, pack, "B")


# --- the bake -----------------------------------------------------------------------------------------------------

def _wav(path, x, rate=48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        st = np.stack([x, x], -1)
        w.writeframes(np.clip(np.round(st * 32767), -32768, 32767).astype("<i2").tobytes())


def _seconds(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


@needs_node
def test_a_baked_line_on_the_loop_puts_in_no_jump_and_changes_nothing_outside(case, tmp_path):
    root, pack = case
    _wav(tmp_path / "line.wav", tf.voice())
    report = face_bake.bake(root / "pack", tmp_path / "line.wav", tmp_path / "out")
    frames = math.ceil(round((0.5 + 1.6 + 0.6) * 24, 6))
    assert report["verdict"] == "pass", report["refused"]
    assert report["frames"] == frames == len(list((tmp_path / "out" / "frames").glob("frame-*.png")))
    assert report["outside_changed_px_max"] == 0 and report["over_sum"] == []
    assert report["talk_frames"] > 0 and report["set"] == "neutral"
    assert abs(_seconds(tmp_path / "out" / "voice-baked.wav") - frames / 24) < 1 / 48000 * 2


@needs_node
def test_a_baked_greeting_keeps_its_own_frames_and_sound_then_talks(case, tmp_path):
    root, pack = case
    _wav(tmp_path / "line.wav", tf.voice())
    report = face_bake.bake(root / "pack", tmp_path / "line.wav", tmp_path / "out", play="greet", lead=0.2)
    assert report["verdict"] == "pass", report["refused"]
    assert report["line_start"] == report["own_mouth_frames"] == tf.GREET_OWN_END and report["wait_frames"] == 0
    for t in range(tf.GREET_OWN_END):
        out = np.asarray(Image.open(tmp_path / "out" / "frames" / f"frame-{t:05d}.png"))
        cell = np.asarray(Image.open(root / "pack" / body(pack, "greet")["cells"][t]))
        assert np.array_equal(out, cell), t
    assert report["frames"] == tf.GREET_OWN_END + math.ceil(round((0.2 + 1.6 + 0.6) * 24, 6))


def test_a_pack_that_changed_after_it_was_made_is_refused(case, tmp_path):
    root, pack = case
    shutil.copytree(root / "pack", tmp_path / "pack")
    name = next(n for n in pack["files"] if n.startswith("face/"))
    with open(tmp_path / "pack" / name, "ab") as fh:
        fh.write(b"\0")
    with pytest.raises(SystemExit, match="not the file the pack was made with"):
        face_bake.load_pack(tmp_path / "pack")
