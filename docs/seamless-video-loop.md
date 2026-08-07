# Seamless Video Loop (ambient clip → infinite loop)

Turn a **non-looping AI-generated ambient video clip** (an `sprite-gen gen` video, a
Seedance/Veo/Kling i2v clip, a title/hero backdrop) into a clip that loops **forever with
no visible seam** — for a title-screen background, a hero loop, a store-page ambient shot.

Not to be confused with [`frame-interpolation.md`](frame-interpolation.md): that is a
**generative** in-between for **sprite** frames (identity-preserving, RIFE deliberately
retired there). This doc is **RIFE optical-flow interpolation** used to bridge a **video
loop seam** — a different job, where RIFE's fluid morph is exactly right.

## Why AI clips don't just loop

An image-to-video model paints continuous, non-repeating motion. The last frame is never
the first frame, and there is usually **no** pair of near-identical frames anywhere. So a
raw loop cuts hard from the end state back to the start state and the eye catches a "pop".

Approaches that were tried and rejected (measured on the dungeon-sookuma title clips):

- **Crossfade the seam** — the dissolve is *visible as a fade in/out*. Rejected.
- **Ping-pong / reverse** (`forward + reverse`) — mathematically seamless, but the motion
  plays **backwards** for half the loop (embers fall, honey un-drips). Rejected.
- **Seedance flf2v** (`start_image == end_image`) — 2.0's loop is weak (first-vs-last stayed
  ~6% different, no better than a naive cut); 2.5 has **no** start/end image params at all
  (`t2v` / `omni_reference` / `video_extension` only). Dead end for a native loop.
- **Trim to the best single-frame match** — halves the pop but still pops, because matching
  two frames' *position* ignores their *motion* (see next section).

## The pipeline

Two stages: find the best **forward** loop segment numerically, then **bridge its seam**
with RIFE so the wrap is continuous motion, not a cut or a fade.

### 1. Find the loop segment — match flow, not two frames

Extract frames (downscaled for speed), then over candidate cuts `(i, j)` minimise a cost
that matches **both position and motion flow** at the seam. Position-only is not enough:
these clips flicker, so `velocity[t] = |frame[t+1] - frame[t]|` **alternates every frame**;
a cut that matches position but lands on the opposite flicker phase jumps in speed.

```
cost(i, j) = Σ_k w_k · D(frame[i+k], frame[j+k])          # windowed position match (flow shape)
           + β · Σ_k w_k · |vel[i+k] - vel[j+k]|          # windowed velocity match (phase/speed)
```

- `w_k` = small Gaussian window (radius ~3 frames) so the *trajectory around the seam*
  matches, not just the boundary frame.
- `β ≈ 5`. Without it the cut lands on a velocity mismatch (measured on t06: `|Δvel|` 0.28);
  with it the velocities align (`|Δvel|` 0.04) with the same position quality.
- Constrain `j - i ≥ minlen` (~2.5 s) so the loop isn't too short.

The chosen segment is `frames[i .. j]`, trimmed hard from the full clip (`ffmpeg -ss i/fps
-t (j-i)/fps`). Play it forward; the only remaining discontinuity is the wrap `j → i`.

### 2. Bridge the seam with RIFE

Interpolate the wrap `frame_j → frame_i` with **rife-ncnn-vulkan** (neural optical-flow
frame interpolation) and append those in-between frames after the segment. The loop then
plays forward, **morphs** `j → i` over the bridge as continuous motion, and wraps to `i`.
No fade, no reverse.

rife-ncnn-vulkan interpolates **one** midpoint per call, so recurse for N frames
(depth 3 → 7 frames ≈ 0.29 s at 24 fps):

```bash
R="rife-ncnn-vulkan"; J=frame_j.png; I=frame_i.png
"$R" -0 "$J" -1 "$I"  -o m.png                       # 50%
"$R" -0 "$J" -1 m.png -o q1.png ;  "$R" -0 m.png -1 "$I" -o q3.png    # 25% / 75%
"$R" -0 "$J" -1 q1.png -o e1.png ; "$R" -0 q1.png -1 m.png -o e2.png
"$R" -0 m.png -1 q3.png -o e3.png ; "$R" -0 q3.png -1 "$I" -o e4.png
# append after the segment frames, in order: e1 q1 e2 m e3 q3 e4, then re-encode at the clip fps
```

`frame_i` itself is the loop's first frame, so the bridge stops at `e4` (just before `i`) —
the wrap `e4 → i` is one more small RIFE step.

## Verify with numbers, not one screenshot

A single still cannot tell a playing loop from a paused first frame. Judge numerically:

- **Loop seam**: output `first-vs-last` frame mean-abs-diff should drop (t06: hard cut 1.65
  → RIFE bridge **0.87**). Frame-diff alone is necessary, not sufficient — the flow-match in
  stage 1 is what removes the *velocity* jump the eye actually reads.
- **Motion present**: `first-vs-mid` diff should stay high (a paused clip reads ~0).
- To confirm a **built** player is actually *playing* (not showing a static fallback of the
  same scene), capture two frames ~1.5 s apart and diff them — motion ⇒ the video plays.

## Tools

- **rife-ncnn-vulkan** (nihui, prebuilt macOS release) — runs on Apple Silicon via
  Vulkan/MoltenVK (verified on M4 Max). Ships model sets (`rife-v4`, `rife-anime`, …);
  the default is fine for fluid ambient motion.
- **ffmpeg** — frame extract, trim, and re-encode. `minterpolate` (its built-in
  motion-compensated interpolation) is a fallback but is block-based and fails on isolated
  2-frame pairs; prefer RIFE.

## Runtime note

For a game/app, ship the looped clips as the player's native codec and let the player loop
(Unity `VideoPlayer.isLooping = true`, HTML `<video loop>`). Because the seam is already
seamless in the file, the engine's plain loop needs no crossfade.
