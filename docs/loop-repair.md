# Loop repair — RIFE in-betweens for jump frames, the jolt index, one cycle per set

> Owns: Where RIFE runs and what it costs, which model and augmentation it runs with, its two backends (the binary and PyTorch, the latter in this process or on a GPU elsewhere), how it is installed and what runs without it, jump-frame repair in `video-loop`, the flow reading (frames off a walk's way), the eased retime (a part that rushes on its way), the part pop (a hand or a foot that jumps at the wrap), the seam bridge (the wrap of a loop that is not a walk), the jolt index and its gate, cycle alignment across a direction set, the redrawn wrap of a pinned clip (`--wrap-blend`), held cells (a picture shown again) and a cut given above its clip's rate (`--fill-held`, `--out-fps`) · Index: [docs/README.md](README.md)

A walk loop cut from a generated clip can look right in every still and still hitch when it
plays: the video model redraws thin hair a little differently every frame, and now and then
a ponytail lands somewhere it was not one frame earlier. This doc owns the three repairs
`video-loop` and `video-set` make for that, all built on one optical-flow interpolator.

Not to be confused with [frame-interpolation.md](frame-interpolation.md) (a *generative*
in-between drawn by an image model, where RIFE was retired because it smears a changed
*appearance*) or [seamless-video-loop.md](seamless-video-loop.md) (RIFE bridging an ambient
clip's seam). Here RIFE only ever makes a frame between two neighbours of the *same* walk,
where the appearance does not change and only the motion does — the case optical flow is for.

## 1. RIFE — what runs, where, and what it costs

### What

[rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan) release **20221029**, model
**`rife-v4.6`** (shipped inside the release zip). The engine calls the binary as an external
tool, like `ffmpeg` and `img2webp`; nothing is vendored into the package. Every call asks for
the binary's test-time augmentation, spatial and temporal (`-x -z`, `rife.TTA`;
[Which setting](#which-setting--measured-2026-10-15) below); `SPRITE_GEN_RIFE_TTA` names another —
`-x`, `-z`, both, or empty for none — and anything else is refused.

| Release zip | SHA-256 (measured 2026-10-03) |
|---|---|
| `rife-ncnn-vulkan-20221029-ubuntu.zip` | `1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68` |
| `rife-ncnn-vulkan-20221029-macos.zip` | `4a63a1f3c9c715773c57d2ee51df1b315ed20cd6c63103e45c483ecc4400b595` |
| `rife-ncnn-vulkan-20221029-windows.zip` | `d8e4d772d26cd8006ef0ad0bc82eb191b53c68677d1ae2f42506d74cbbbea606` |

| Model file (the macOS and Ubuntu zips hold the same, measured 2026-10-21) | SHA-256 |
|---|---|
| `rife-v4.6/flownet.param` | `724569596bcd1e7b9fa50455c604777ebed99746d2ef40aa86e31b5725f1053c` |
| `rife-v4.6/flownet.bin` | `f334ed2260149ce0188a6dcf049844e8b0cdd912e01cbcfb63553157d2508958` |

#### The torch backend: `SPRITE_GEN_RIFE_BACKEND=torch`

The binary is the default. `SPRITE_GEN_RIFE_BACKEND=torch` runs the same model in PyTorch instead
(`sprite_gen/video/rife_torch.py`), for a GPU the binary cannot run on: rife-ncnn-vulkan needs Vulkan,
and a GPU offered for compute alone (CUDA with no Vulkan driver) has none. It is the same RIFE:

- the network is rife-v4.6's IFNet (Practical-RIFE `IFNet_HDv3`), written again with its parameter
  names;
- its weights are read out of the binary's own model files above, checked against those SHA-256 first
  (the binary's float16 weights widened as the binary widens them, no other download);
- one run does what one call of the binary does with `-x -z`: the pair turned its eight ways and run
  both ways in time, every way's flows turned back and averaged at each of the network's four levels,
  the frames averaged and rounded as the binary rounds them; `SPRITE_GEN_RIFE_TTA` means the same;
- a frame is the same two runs (colour, coverage) and a read the same sixteen passes (section 4), the
  engine's part of them done by the same code for either backend (`rife.frame_runs`, `rife.pass_runs`).

| Variable | Values | Default |
|---|---|---|
| `SPRITE_GEN_RIFE_BACKEND` | `ncnn` (the binary), `torch` | `ncnn` |
| `SPRITE_GEN_RIFE_DEVICE` | `cuda`, `cpu` (the torch backend's device) | `cuda` |
| `SPRITE_GEN_RIFE_MODEL` | a `rife-v4.6` directory | beside the binary (`ncnn`); the install root's (`torch`) |
| `SPRITE_GEN_RIFE_TTA` | `-x`, `-z`, both, or empty | `-x -z`, for both backends |
| `SPRITE_GEN_RIFE_EXECUTOR` | `module:attr`, the host's send to a GPU elsewhere (torch backend; below) | unset: in this process |

Nothing falls back. With torch chosen the engine neither looks for the binary nor runs it. A device is
not chosen for you and not traded for another: `cuda` where PyTorch finds no CUDA is refused, naming
why (a PyTorch built for the CPU only, or a CUDA that finds no GPU), and does not run on the CPU.
Apple's GPU (`mps`) is refused: no release lane measures it, and on a Mac the binary already runs on
the GPU. PyTorch not installed, or no model where it looks, is RIFE not installed ("Without RIFE"
below); a backend or device named wrong, a model whose files are not the pinned ones, or a device
that fails as the network is put on it or as a frame runs (out of its memory, its driver or CUDA
failing), is a RIFE that fails, in PyTorch's own words.

On CUDA every frame runs with cuDNN deterministic, its benchmark off and its convolutions in IEEE
float32 (`torch.backends.cudnn.conv.fp32_precision = "ieee"`: PyTorch turns TF32 on by default), so the
same frame twice is the same bytes on one GPU, driver and PyTorch. On the CPU it is the same bytes
whatever the thread count. PyTorch does not promise the same bytes across GPUs or releases, so what
made a frame (the backend, the model bytes it loaded, its network code, PyTorch, on CUDA the GPU,
driver, CUDA, cuDNN and those settings as they read back, on the CPU its instruction set) is the RIFE's
identity, and a frame kept for one is never taken for another (section 4, `--between-cache`). The
engine's reports name what made their frames: `interpolator.kind` is `rife-ncnn-vulkan` or
`rife-torch`, and the overlapped wrap counts the binary's calls (`binary_calls`) or the torch backend's
runs (`runs`).

#### A GPU elsewhere: `SPRITE_GEN_RIFE_EXECUTOR`

A host with no GPU (a job container, say) can have the torch backend's frames made on a machine that
has one. The engine knows nothing of what carries them. The host names a function of its own,
`SPRITE_GEN_RIFE_EXECUTOR=module:attr`, from a request's bytes to its answer's, and that function
reaches `sprite_gen.video.rife_torch.serve` on the GPU's machine: the same sprite-gen, with PyTorch
and the model installed there (`sprite-gen rife install --backend torch`). The host needs neither.
It sets `SPRITE_GEN_RIFE_BACKEND=torch` and no `SPRITE_GEN_RIFE_DEVICE`: the device is the
executor's, and naming one here as well is refused. So is an executor named with the binary's
backend.

| Asked for | Runs on the executor | Calls of the send |
|---|---|---|
| a RIFE made (the probe) | none: the executor says what it is, loading the backend (the cold start) | 1 |
| a frame | 2 (colour and coverage), with the augmentation this host names | 1 |
| a read (the overlapped wrap) | 2, and the 32 runs of the sixteen passes, reduced there to how far they disagree | 1 |

Frames are not batched: the repairs make and judge one frame at a time. A `video-loop` makes at
most 53 calls: the probe, three flow repairs, nine eased frames and forty frames of the overlapped
wrap. A `video-cycle-align` makes one call for each frame it makes, and the probe.

A RIFE asks the executor what it is once, as it is made, and holds the answer for its life. The
answer is the torch backend's identity there (above: the model bytes, its network code, PyTorch,
on CUDA the GPU, its driver, CUDA, cuDNN and the determinism settings), with the protocol. The
RIFE's identity, which `--between-cache` keys frames on (section 4), is that answer, the
executor's name and this host's own part. Every frame and read carries the identity held. An
executor that is not it any more (another GPU, driver or image) makes nothing, and the RIFE fails,
naming what differs (`gpu: Tesla T4 -> NVIDIA L4`). An answer from another identity is not handed
on. So one RIFE never hands on frames of two GPUs, and a frame it takes from the cache is the frame
it would make. The next loop or alignment makes a new RIFE, which asks again and keys its frames
anew. Containers of one image, GPU and driver are one executor: their hosts' CPU and name are no
part of the identity, so a cache directory serves every one of them.

The host keeps to three things:

- Name one GPU type for the executor, with no list to fall back on. A request a GPU of another type
  takes fails the RIFE.
- Run the same sprite-gen on both sides. A request names this host's `rife.py` by SHA-256, and an
  executor of other code is refused as the RIFE is made.
- Do not deploy the executor anew while a loop or an alignment runs: that run fails, by why.

A send that raises, an answer that does not read (not the format, damaged or cut short on the
way, or not the frame's size), or the executor's own failure (its model not installed, its GPU out
of memory) is a RIFE that fails, in its words. Nothing is asked again, and nothing runs here or on
another device in its place.

A request and an answer are each one compressed NumPy archive (`np.savez_compressed`, read with
`allow_pickle=False`). Its `header` is JSON: `protocol` (1) and `code` (the `rife.py` SHA-256),
then `probe`, or `expect`, `t`, `modes` and `read`. Its images are RIFE's input as `rife.inputs`
makes it: the colour (H × W × 3) and the coverage (H × W) of the two frames, uint8. The answer
holds the identity, and the made colour and coverage (uint8) and, for a read, the disagreement
(float32); or an error (`protocol`, `code`, `identity` or `failed`, with what it names). Each
member of the archive is read whole, so its CRC-32 is checked before its array is read: an answer
damaged on the way does not read, and hands on no other pixels. Reports name the executor:
`interpolator` reads `kind` `rife-torch`, `executor`, and the executor's `device` and `gpu`; the
overlapped wrap counts `runs` and `executor_calls`.

### Install — `sprite-gen rife install`

```bash
sprite-gen rife install            # once per machine
```

It downloads the zip for this platform (macOS, universal; Linux and Windows, x86-64), checks
the SHA-256 above before anything is unpacked, and keeps only what the engine runs — the
binary, `rife-v4.6/`, `LICENSE` and `README.md` (and `vcomp140.dll` on Windows), about 35 MB of
the 430 MB zip. It lands in the user data directory:

| | Install root |
|---|---|
| any platform, when set | `$SPRITE_GEN_DATA_DIR/rife` |
| macOS, Linux | `$XDG_DATA_HOME/sprite-gen/rife`, else `~/.local/share/sprite-gen/rife` |
| Windows | `%LOCALAPPDATA%\sprite-gen\rife` |

It unpacks into a staging directory and renames it into place, so an interrupted install
leaves nothing half-written where the engine looks. A finished install of the same zip is left
alone (`--force` unpacks again). It ends with a check frame: one frame half way between two
discs through the installed binary, so a Linux machine with the files but no Vulkan driver
finds out at install time (`--no-check` skips it). `--zip FILE` installs from a zip already
downloaded (behind a proxy, in an image build), checked the same way; `--dir DIR` installs
elsewhere and prints the `SPRITE_GEN_RIFE=…` line the engine then needs. Other platforms (Linux
on ARM, for one) have no release build: build it from the repository and set `SPRITE_GEN_RIFE`.
Either backend's install checks the model's two files against their SHA-256 above once the zip is
unpacked, and installs nothing where they differ.

The engine finds the binary by, in order: `SPRITE_GEN_RIFE` (the binary's path), then
`rife-ncnn-vulkan` on `PATH`, then the install root above. The model is `SPRITE_GEN_RIFE_MODEL` or
`rife-v4.6/` next to the binary. When an earlier place holds another RIFE, the install says which one
the engine runs.

For the torch backend:

```bash
pip install "sprite-gen[torch]"           # PyTorch 2.13 or later
sprite-gen rife install --backend torch    # the model, and a check frame through PyTorch
```

`--backend torch` (the default where `SPRITE_GEN_RIFE_BACKEND=torch`) installs the same zip and makes
the check frame through PyTorch on `SPRITE_GEN_RIFE_DEVICE` (name `cpu` for an image built without a
GPU). It reads the model only, so a Linux machine the binary does not run on (ARM) is not refused. The
backend reads the model from `SPRITE_GEN_RIFE_MODEL`, else the install root's `rife-v4.6/`, never from
beside a binary on `PATH`. PyPI's Linux wheel of PyTorch is its CUDA build (527 MB, and its CUDA
libraries); a host that runs the backend on the CPU only installs PyTorch's CPU build first (192 MB):
`pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu`.

### Without RIFE

RIFE is located only when a frame is to be made, so a smooth walk never needs it. When one is
to be made and no RIFE is found in those three places, nothing fails by default — a walk that
cut before RIFE existed still cuts — and nothing is left unsaid:

| Where | Default without RIFE | Asked for by name |
|---|---|---|
| `video-loop` jump repair (section 2) | `--repair auto`: a jump is cut as filmed, and each filmed ghost is given way to the clean frame beside it (its own `warning:` line). Where a jump is left, `jump_repair` reads `why`, `rife` (what was not found), `install` and `unmade` (the frames, neighbours and `off` of the group the flow search stopped at), and one `video-loop: warning:` line on stderr says the jump frame was not repaired. `applied` is false only where nothing was replaced. For the seam bridge, the wrap is cut as filmed: `jump_repair` reads `why`, `rife`, `install`, and `unmade` (the `frames`, `anchors`, and `line` of the run it stopped at). One `video-loop: warning:` line says no frame was made round the wrap and names `sprite-gen rife install`. | `--repair on`: fails with the install line |
| `video-set` cycle alignment (section 4) | `--align-cycles auto`: the state is skipped and every loop keeps its own length; `cycle_align.<state>` reads `applied: false` with `why`, `rife`, `install`; the set report lists it under `warnings`; one `video-set: warning:` line | `video-cycle-align`: fails with the install line |

With `SPRITE_GEN_RIFE_BACKEND=torch`, PyTorch not installed or no model where the backend looks is RIFE
not installed in the same way: the records' `rife` names what was missing and `install` gives the torch
backend's line (`pip install "sprite-gen[torch]" && sprite-gen rife install --backend torch`); the
binary is not run in its place.

A RIFE that is found and then fails (a binary that cannot reach Vulkan, say) is an error in every
mode: the install is there and broken, which a warning would hide. A jolt gate passed to a loop
whose repair could not run (section 3) reads the loop as filmed, and its failure says the
repair did not run.

RIFE reads three colour channels and no alpha. A frame is therefore interpolated as two
images — its colour, and its coverage as a grey image — and put back together
(`sprite_gen/video/rife.py`). Coverage below 2/255 is dropped, so no faint halo is invented
around the body.

The two images are two runs of the flow, and where the picture is hard to follow — two legs
passing each other — they do not agree: the coverage says body where the colour run still
carries what lay around the body. Until 2.24 the colour was premultiplied over black, so that
disagreement came out as a black smear between the legs. The colour image now holds the
body's own colour around the body (`rife.bleed`): the colour from 0.8 % of the body's height
inside its solid edge — past a drawn outline — pushed out over the transparent area, coarse to
fine. A disagreement then reads as the body's fill. Where the frame has coverage its colour is
its own, outline included, and it is taken from the colour run as it comes out (no division by
the coverage). A limb RIFE cannot follow at all still comes out pale and soft, never black;
and where two drawings are too far apart for the flow — legs crossing a long way, as a clip
drawn on twos gives between its drawings — the legs come out as one shape of fill with no
outline between them or around them. `rife.smear` measures all three, and a set alignment takes
the nearer source frame where a made frame melted (section 4).

### Where — measured 2026-10-03

One real pair: two frames of a Lite side walk two apart (545 x 544, premultiplied), the frame
between them as the truth. Time per RIFE call, wall clock, process start included.

| Where | How | Time per call | Mean abs error vs the true middle frame |
|---|---|---|---|
| Local Mac, M4 Max | Apple GPU through MoltenVK (`-g` auto) | 0.33–0.41 s | 3.32 (the frame either side: 7.77) |
| Local Mac, M4 Max | ncnn CPU path (`-g -1`) | 0.28–0.39 s | **39.3 — wrong output** |
| Modal, Linux, default reservation (as `run_job`) | Mesa llvmpipe software Vulkan (`-g` auto) | 2.8–3.0 s (first call 6.9 s) | 3.32 |
| Modal, Linux, `cpu=2` | llvmpipe | 1.6–3.0 s over two runs (first 4.3–7.4 s) | 3.32 |
| Modal, Linux, `cpu=8` | llvmpipe | 2.7 s (first 6.6 s) | 3.32 |
| Modal, Linux, `cpu=2` | ncnn CPU path (`-g -1`) | 0.72–0.85 s | **39.3 — wrong output** |
| Modal, T4 GPU | — | not run: the workspace has no payment method for GPU functions | — |

Read:

- **llvmpipe gives the same frame as the Apple GPU**, to the third decimal of the error. It is
  the CPU route on Linux. The binary's own CPU path (`-g -1`) is fast and wrong on both
  platforms with this model; the engine never passes it.
- The first call in a fresh container compiles llvmpipe's shaders (4–7 s); later calls in the
  same container do not.
- More cores barely help (8 cores: 2.7 s against 2.8–3.0 s), so the default reservation is
  the right size.

### Which setting — measured 2026-10-15

What there is to choose (read 2026-10-15):

| Source | Newest | What it offers |
|---|---|---|
| [nihui/rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan/releases) | release 20221029, the one above | models up to `rife-v4.6`; `-x`, spatial test-time augmentation (the pair run as its eight flips and transposes, the results averaged); `-z`, temporal (the pair run both ways in time as well); `-u`, a mode for 4K frames |
| [TNTwise/rife-ncnn-vulkan](https://github.com/TNTwise/rife-ncnn-vulkan/releases) (a fork) | release 20250112 (an 830 MB zip) | adds `rife-v4.25`, `rife-v4.25-lite` and `rife-v4.26` (and `-v4.25-heavy`, `-v4.26-large`, which Practical-RIFE does not list); its README marks `-x` and `-z` "deprecated with rife-v4.25" |
| [hzwer/Practical-RIFE](https://github.com/hzwer/Practical-RIFE) (the weights) | 4.26 (2024-09-21) and 4.25.lite (2024-10-20); no model since | "recommended to choose 4.25 by default for most scenes" |

Chosen: **`rife-v4.6`, the model above, with `-x -z`**: the pair is run as its eight flips and
transposes and both ways in time, and the results are averaged, so a frame made from a to b is the
one made from b to a. The fork's models are not used.

What each setting makes of the synthetic pairs of `tests/video/test_rife.py`: every setting through
the engine's own `rife.between`, named by `SPRITE_GEN_RIFE_TTA`, read with its own measures
(`rife.smear`, `rife.ghost`, `interpolation_quality.faults`), on a Radeon Pro 570 (an Apple M4 Max
gives the same within three percentage points):

| `rife-v4.6` with | legs crossing far (`_walker` 20/−20 and −5/5, t 0.5): outline lost a→b, b→a; ghost | a short step (10/−10 and 4/−4, t 0.5): outline lost, worse way | legs walking through each other (`_legs`): dark added at t 0.25 / 0.5 / 0.75 |
|---|---|---|---|
| nothing | 29 %, 24 %; 0.0096 a→b (a ghost) | 3.6 % | −1.0 / −2.0 / −0.9 % |
| `-x` | 28 %, 25 %; none | **5.3 %** (over the 5 % bar) | −2.6 / −2.3 / +1.4 % |
| `-z` | 29 %, the same both ways; 0.021 (a ghost) | 4.0 % | −1.4 / −2.6 / +0.4 % |
| **`-x -z`** | **26 %, the same both ways; none** | **1.7 %** | +1.3 / −2.2 / +2.1 % |

Only `-x -z` makes the frame between legs that cross far the same both ways and leaves no ghost
there, and it loses the least of a short step's outline: `-x` alone is not symmetric and loses 5.3 %
of the short step's outline one way, over the 5 % bar, and `-z` alone leaves a ghost band where legs
cross far. Where the legs cross (t 0.5) none adds dark. Either side, the flips keep the near leg's
outline whole over the far one — a line each neighbour draws in another place, not the black fill
2.24 left (6 % of the body at t 0.5, `test_real_rife_between_crossing_legs_adds_no_black`).

Time per frame made (two calls, wall clock, process start included; the CPU seconds, all cores,
after it), on the walker of those pairs — legs crossing far and the short step, t 0.5 — drawn
280 × 520, and drawn 520 × 900 on a 960 × 960 frame (the size of a 720p clip's frame):

| `rife-v4.6` with | Mac M4 Max, GPU, 280 × 520 / 960 × 960 | Modal `cpu=2` (`run_job`), 280 × 520 | Modal `cpu=2`, 960 × 960 | Modal default request (`run_align`), 960 × 960 |
|---|---|---|---|---|
| nothing | 0.64 / 1.06 s | 2.2–3.7 s (8–24 CPU s) | 4.3–6.2 s (32–46) | 4.6–4.8 s (33–35) |
| `-x` | 0.70 / 1.29 s | 7.1–15.8 s (59–184) | 22.7–31.4 s (296–436) | 26.3–26.5 s (327–328) |
| `-z` | 0.65 / 1.08 s | 2.7–5.4 s (17–47) | 7.3–10.3 s (76–111) | 8.0–8.5 s (80–84) |
| **`-x -z`** | **0.76 / 1.59 s** | **11.7–29.8 s (120–366)** | **44.0–60.5 s (596–874)** | **49.2–50.6 s (635–650)** |

On the Mac's GPU the augmentation adds a tenth of a second a frame at 280 × 520 and half a second at
960 × 960: the process start is most of a call. On Modal's CPU it is 15 to 19 times the plain call's
CPU. Each Modal range is two runs, and hosts differ: the same 280 × 520 frame took 11.7 s on one and
29.8 s on the other. llvmpipe gives the Mac's frame there as before (a mean difference of 0.006 to
0.76 of 255 a byte, every setting; the outline reading within two percentage points). The first
frame in a container takes longer (llvmpipe compiles its shaders). One call is stopped at 600 s
(`rife.CALL_TIMEOUT_SECONDS`) and reported as a RIFE that failed.

### Decision

**RIFE runs where the engine runs.** On a Mac that is the Apple GPU. In the app it is the
Modal job container itself, on CPU through llvmpipe — no GPU function and no second service:

```
apt_install("libvulkan1", "mesa-vulkan-drivers")    # the Vulkan loader + llvmpipe
curl -fsSL -o /tmp/rife.zip https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-ubuntu.zip
echo '1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68  /tmp/rife.zip' | sha256sum -c -
unzip -q /tmp/rife.zip -d /opt && ln -s /opt/rife-ncnn-vulkan-20221029-ubuntu/rife-ncnn-vulkan /usr/local/bin/
```

`sprite-gen rife install` (after the package, as the image's own user) does the same download,
hash and unpack, and adds the check frame; CI installs RIFE that way.

Cost on Modal (prices read from modal.com/pricing on 2026-10-03: CPU $0.0000131 per core per
second, memory $0.00000222 per GiB per second), with the augmentation ("Which setting" above): one
repaired frame of 280 × 520 is 12 to 30 s and 120 to 370 CPU seconds, a fifth of a cent to half a
cent; a loop makes at most three, so at most about a minute and a half of one container — under two
cents. Aligning a set's cycles (section 4) makes more frames, and larger: 44 to 61 s and 600 to 870
CPU seconds a 960 × 960 frame, about a cent, so a view that needs 20 made frames takes 15 to 20
minutes. A deployment that aligns on a CPU names a lighter augmentation for that job:
`SPRITE_GEN_RIFE_TTA=` (none) is 4 to 6 s a 960 × 960 frame, `-z` 7 to 10 s. On a Mac it is under
two seconds either way. That is the reason section 4 keeps every frame that lands on a source frame.

**The torch backend is for a GPU the binary cannot use; the binary stays the default.** A GPU offered
for compute only cannot run the binary at all, and there the torch backend runs the same model and
augmentation. Elsewhere it adds PyTorch, hundreds of megabytes, for the same model: on a Mac the binary
already runs on the GPU. On a CPU host the torch backend is faster than the binary on llvmpipe (the
table below), but its frames are not the binary's bytes; a deployment that wants it there names it, and
the default does not move. Measured on synthetic walkers, 2026-10-21:

- **The same verdicts.** The seven pairs of `tests/fixtures/rife-walkers` (clean steps, an outline lost
  where legs cross far, a ghost band, parts torn, a cross-fade to look at) get the same faults and
  looks from the binary on an Apple GPU, the binary on llvmpipe and the torch backend on a CPU, each
  reading at least a tenth of its bound away from it (and three more pairs besides, one left out for a
  torn reading 8 % from its bound on llvmpipe). The tests keep it: the torch backend judges the seven
  as the binary did on a machine with no binary, and as this machine's binary does where both run.
- **Frames as close as two GPUs'.** Against the binary's frame on the Apple GPU, the torch backend's
  lies 0.005 to 0.052 of 255 apart on average and 11 at most (premultiplied RGBA in the box either
  covers), and the binary's own frame on llvmpipe 0.007 to 0.25 apart and 76 at most: closer on six of
  the seven pairs, the seventh 7 % further.
- **Time** (a 960 × 960 frame of the walker drawn 512 × 768, legs crossing far and the short step, t
  0.5, after a warm call):

| | `-x -z` frame | plain frame | read (`-x -z` frame and sixteen passes) |
|---|---|---|---|
| binary, Apple M4 Max GPU | 1.27 to 1.37 s | 0.77 to 0.82 s | 12.6 to 13.2 s |
| torch backend, Apple M4 Max CPU (12 threads) | 3.3 to 3.6 s | 0.38 to 0.40 s | 8.8 to 9.0 s |
| binary, Modal `cpu=2` (llvmpipe), one cold call each | 127.7 to 131.5 s | 9.3 to 9.5 s | not run |
| torch backend, the same Modal container, 2 threads | 22.6 to 28.5 s | 1.5 to 1.7 s | 45.1 to 46.1 s |
| torch backend, Modal NVIDIA T4 (CUDA), in its process | 1.36 to 1.41 s | 0.32 to 0.36 s | 3.9 to 4.1 s |
| torch backend on that T4, from a `cpu=2` host without PyTorch (`SPRITE_GEN_RIFE_EXECUTOR`, a Modal call each) | 1.49 to 1.52 s | 0.46 to 0.49 s | 4.0 to 4.1 s |

The torch backend's frame on an x86 CPU is not the same bytes as on an Apple CPU (its torn reading is),
which the CPU's instruction set in its identity keeps apart. PyTorch sizes its threads by the host's
cores, not a container's share (the container above saw 18): a container names its share
(`OMP_NUM_THREADS`).

On the T4 (driver 610.57.04, PyTorch 2.13.0 for CUDA 13.0, cuDNN 9.20, 2026-10-22) the frames made
through the executor, from another container of the same image, are the bytes made in the T4's own
process: both walkers' frames, plain frames, and reads with their disagreement. The executor adds 0.1 to
0.2 s to a frame, a read takes as long as in the process, and the probe, a T4 container's cold start,
took 8.4 s. The T4 lane runs the torch tests on CUDA for a release (docs/release.md).

### Licences

| Part | Licence | Source (read 2026-10-03) |
|---|---|---|
| rife-ncnn-vulkan (code, release binaries) | MIT, © 2020 nihui | `LICENSE` in the repository and in the release zip |
| RIFE (the network) | MIT | github.com/hzwer/ECCV2022-RIFE; the authors add that they "respect the commercial behavior of other developers" |
| RIFE v4.x weights (Practical-RIFE) | MIT — "The content of these links is under the same MIT license as this project." | github.com/hzwer/Practical-RIFE |
| ncnn | BSD 3-Clause | github.com/Tencent/ncnn |

| sprite-gen's torch backend (`sprite_gen/video/rife_torch.py`) | MIT notices in `NOTICE`: the IFNet written again from Practical-RIFE `IFNet_HDv3.py` and `warplayer.py`, © 2021 hzwer; the augmentation from rife-ncnn-vulkan's `RIFE::process_v4`, © 2020 nihui | Practical-RIFE f6b5132 (2022-09-26); rife-ncnn-vulkan 20221029 |
| PyTorch (the `torch` extra) | BSD 3-Clause | github.com/pytorch/pytorch |

sprite-gen ships none of the binaries, weights or PyTorch: its torch backend is code written again from
the two MIT sources above, their notices in `NOTICE`, and reads the weights from the release zip's
model. A deployment that bakes the release zip into an image ships the zip's `LICENSE` with it.

## 2. Jump frames — `video-loop --repair auto`

A walk or run loop (`--state walk|run`) is read, after it is cut and anchored, as it will play:
cyclic, the last frame followed by the first, inside the union box of the body over the loop
(the strip cells' own crop). The frames that leave the loop's flow — one or two drawings in a row
that lie far off the line between the frames either side of them, by direction and speed, not by
how much a step changes ([the flow reading](#the-flow-reading--a-frame-off-the-walks-way-not-a-large-step)
below) — are taken out and made again by RIFE between the frames either side. Where a part keeps its way
and rushes — a tail that runs short of the cycle, rushing on at the wrap — the frames either side of
the rush are then made again at times that spread it, and played only where the rest of the body
keeps its own beat on them ([the eased retime](#the-eased-retime--a-part-that-rushes-on-its-way)
below; a frame made is the whole picture at one time, so the legs take the time the part is given,
and on every walker measured the loop keeps the flow repair). Before all of it, a cut two steps
long whose wrap a part on its own beat leaves open has the frames beside its wrap made again between
the clip's frames a cycle apart and its own ([the overlapped
wrap](#the-overlapped-wrap--a-part-on-its-own-beat-that-no-cut-of-two-steps-closes) below). A part the size of a hand or a foot that jumps at a walk's wrap is read on the part itself: the engine reads and names the pop and records the loop as a take to film again, and the cut stays as it was ([the part pop](#the-part-pop-a-hand-or-a-foot-that-jumps-at-the-wrap)). A loop of any other state is not read for its flow; where its wrap jumps, frames round it are made again ([the seam bridge](#the-seam-bridge-a-loop-that-is-not-a-walk-whose-wrap-jumps) below).

| Rule | Value | Why |
|---|---|---|
| A frame off the flow | an `off` of at least **3.0** (`FLOW_LIMIT`): that many of its region's usual steps off the line between its neighbours | an ordinary walk reads at most 1.65, a part flung 5 % of the body height against its sway 2.8 to 4.1 (below) |
| What is taken out | a group of one or two drawings in a row (`FLOW_SPAN`), worst first (`repair.flow_targets`, "Which frames" below); on twos or threes a drawing is every frame that shows it | the drawings off the flow, not the frames beside them that read off only because a neighbour is |
| What it is made from | the frames either side of the group — the first frame of the drawing before it and of the drawing after it (`neighbours`) | the video's own frames, never a frame made |
| When each made frame stands | each drawing at its own time between the two: t = (its first frame − the frame before) / (the frame after − the frame before), counted in play order across the wrap (`repair.between_time`) — a half for one frame, a third and two thirds for two in a row, and a half for a drawing on twos whose neighbours show from two frames before it and two after | the loop keeps its length and every frame its time; a drawing made is shown on every frame the drawing was, so a loop held on twos stays held |
| How | one RIFE call a drawing; the group is taken only when none of its made frames has an interpolation fault (below) | a refused group keeps every frame it would take, as filmed; every other frame stays the video's own |
| How many | at most **3 calls** (`MAX_REPAIRS`), refused groups included, worst group first; a group the calls left cannot pay for stops the stage (`left`) | a loop that needs more is jolting everywhere, not jumping once |
| Never | made from or beside a frame already made, given way or refused | two made frames side by side are made from each other and melt the legs; the frames beside a refused group read off only because it does, and an in-between there would smooth toward it |

Re-making every frame (an offset of half a frame) is not offered: it softens the frames that
were fine, and it was judged "not corrected" (2026-10-03).

**A filmed ghost is a target of the same stage, first.** A walk filmed in one direction is
delivered as cut — it never meets the cycle alignment, which takes a clean frame beside a ghost
(section 4) — so the cut gives its filmed ghosts way itself. The ghost screen reads the cut as
filmed, before the repair (`ghost_screen` in the report); each frame it names is a round of its
own (`why: "ghost"`), in frame order, before the jumps are read:

| A filmed ghost at frame j | Taken there | `taken` |
|---|---|---|
| both frames beside it clean, and RIFE's frame half way between them has no fault (`ghost` included, as above) | RIFE's frame | `"rife"` |
| RIFE's frame has a fault, a frame beside it is a filmed ghost too, or no RIFE is installed | the clean frame beside it: the one after, else the one before (`interpolation_quality.clean_beside`, the alignment's own choice at a time on a ghost) | `{"source": k}` |
| both frames beside it are filmed ghosts too | nothing: the ghost is kept, named, and the direction is to be filmed again | `null` |

Ghost rounds are outside the three-call budget, which is the flow groups'. A cell given way is in
`replaced` like a frame made for the flow, so the rule "never made from or beside a frame already
made" holds around it too, and the flow is then read on the loop as the ghosts left it. A cell given way to the
frame beside it is that frame shown twice in a row — a step of nothing and a step of two — and the
jolt index (section 3) measures it as it plays; the GIF and WebP hold it as one frame of twice the
delay, and are checked for one frame per run of identical cells. The seam gate reads it as filmed
(below). A loop the screen does not read (drawn part-covered, `reads: false`) has no ghost round.

The report's `jump_repair` carries `reading` (`flow`), `limit`, `span` and `max_frames`; `facing`,
recorded for the jump guard of `video-loop-compare`, which reads the size (below; the repair reads
the flow whichever way the body faces); `hold` and `drawings`, the flow reading's of the loop as the
ghosts left it; `replaced` (cycle frame indices, ghost and flow rounds alike), `refused` (the frames
of the groups refused), `attempts` (the flow rounds' calls), `unfixed` and `left`; and each round's
`why` (`ghost` or `flow`) and `outcome`. A flow round carries its group as `flow_targets` names it —
`frames`, `drawings`, `neighbours`, `t`, `off`, `turn`, `into`, `out`, `window`, `move` and `off_after`
(the reading the straight in-between brings it to) — and `made`, per drawing: its `frames`, `t`,
`original` and `proposal` measurements (with each one's `ghost`, and the proposal's `crossfade`),
`faults` and `look`. The round's own `faults` and `look` are its drawings' together, its `outcome`
`accepted` or `rejected`. A ghost round carries its target, `original` and `proposal`, `faults`,
`look`, `neighbours`, `taken` and `outcome` (`accepted` for RIFE's frame, `given-way` for the frame
beside it, `kept`), and `no_proposal` where no frame was made ("a frame beside it is a filmed ghost
too", "no interpolator"); its `original.ghost` is the screen's reading. The report and its summary also carry `ghost_given_way` (`frame`, `ghost`,
`taken`) and `ghost_kept` (`frame`, `ghost`), and the strip metadata carries each where it is not
empty: `cycle/` is what `video-cycle-align` reads as `cycle.source/`, and its screen no longer
sees a ghost given way, so the strip says what the cut gave way, in frames of the cut. A
`warning:` line names each: "frame 10 of the cut carries a part-covered band … a filmed ghost;
given way to RIFE's frame between frames 9 and 11", "given way to frame 11 beside it, shown twice
in a row", or "kept … — film this direction again". A cell given way is a cell the jump repair
made for the source projection and for restoration, which never restores it ([the ghost
guard](loop-comparison.md#the-ghost-guard)). `--repair off` gives nothing way: the cut's ghosts are
delivered as filmed, named (section 4).

Under `--repair auto` (the default) or `on`, jump repair takes out a group of one or two drawings that lie 3.0 or more usual steps off the line between neighbours (`repair.FLOW_LIMIT`) and has RIFE make them again. A group whose made frames have a fault is refused and keeps its frames as filmed in `refused`. When the loop as it plays still reads a drawing of a refused group at or over the limit, that drawing is in `jump_repair.unfixed` too: `frames`, `off`, `turn`, `into`, `out`, `window` (read on the playing loop) and `refused`. Every drawing in `unfixed` prints the warning line `video-loop: warning: frame <frame> of the loop lies <off>x its window's usual step off the walk's flow after the jump repair: the group that would bring it back was refused (<faults>), so it plays as filmed — a jump played as filmed; --unfixed refuse refuses such a loop (docs/loop-repair.md section 2)`. Flag `--unfixed warn|refuse` (default `warn`) can refuse such a loop with `video-loop: loop frames left off the flow — ` followed by those words, the cut, and `; the motion does not play smoothly — regenerate the clip`. The `--unfixed` flag is caller choice and is not lifted by `--seam-max`. The flag also covers a loop recorded `retake` reason `part-pop`: under `refuse`, the loop fails, whatever `--repair` says ([the part pop](#the-part-pop-a-hand-or-a-foot-that-jumps-at-the-wrap)).

The flow is read again after each group, on the union box of the loop as filmed; a refused group is
never tried again in that run, and nothing is made from or beside it. The circular last/first
adjacency rule and the call budget hold across the wrap. The shared `interpolation_quality` policy
is the same one used by cycle alignment: `dark_excess > 0.001`, or `outline_loss` and
`outline_shift` both over 0.05 (an outline lost by the dark line and lighter than the neighbours'
edge rank by rank, read between outlined neighbours; between neighbours painted without a line,
`outline_loss` alone, section 4), rejects a proposal, and so does a proposal that is a ghost on its own
coverage (`ghost` over 0.003, read where the loop's ghost screen reads it, section 4). An empty
proposal is also rejected. One made frame at fault refuses its whole group. A proposal that
cross-fades two drawings inside its silhouette (`crossfade` over 0.015, section 4) is taken all the
same and named: its round's `look` says so, and a `warning:` line names the frames.
A refused group keeps its frames as filmed, not a duplicate of a neighbour.

`jump_repair` also records `off_max_before` (the flow reading of the cut as filmed) /
`off_max_after` (of the loop as it now plays), why it stopped (`stopped`: "no frame off the flow",
the frames left `unfixed`, the calls used, or no interpolator), and which interpolator made the
proposals. Rejection is not overall quality failure
or success: original drawing changes and normal gait motion can remain.
When a frame was made — RIFE's, for a jump or for a filmed ghost — the seam gate measures the
rendered cells (`seam_measurement: rendered-cells`), because the source frames no longer say what
plays. A cell given way to the frame beside it is read as filmed (`seam_as_filmed`, frames of the
cut). The gate asks whether the cut closes, and that frame shown twice in a row is a step of nothing
and a step of two wherever the ghost was: at the wrap — a ghost at the cut's first frame given way
to its second, or at its last given way to its first — the step of two read as a cut that does not
close (a walk whose wrap is an ordinary step read twice its mean step and was refused), and the
step of nothing as one that closes whatever the cut. On the source frames the cell is the frame as
filmed; on the rendered cells, the cell of the strip the cut makes with that frame as filmed. A loop
that only gave its ghosts way to the frames beside them is gated on its source frames, as filmed.
`--anchor body` reads its `seam_ratio_after_anchor` the same way.
Where the source frames refuse the wrap, the body anchor moved the cells (`wrap_dx_px` not 0), and
no frame was made, the gate reads the cells that play again. Where the repair made a frame, the gate
reads the wrap alone, but here the cut passes on them (`seam_measurement: rendered-cells`) when the
wrap and the largest step of the loop, each against the mean step, are both within `--seam-max`
(default 2.0); otherwise the refusal is the source frames' own. See
[loop review](loop-review.md#the-body-anchor-and-the-seam-gate).

To compare an active result with a final candidate after all processing, use
[`video-loop-compare`](loop-comparison.md); a seam pass alone is not an improvement.
A replaced cell restored later from its source frame is compared with the
size reading around it (`repair.jump_scores`, the change of coverage over the whole box and the hair
behind the body, with the recorded `facing`): a restoration that brings
the jump back is not an improvement ([the jump guard](loop-comparison.md#the-jump-guard)).

RIFE is located only when a frame is to be made. A loop with a jump and no RIFE is cut as filmed
with a warning under `--repair auto`, and fails under `--repair on` (section 1, "Without RIFE");
so does a filmed ghost between two clean frames under `--repair on`. Under `--repair auto` each
filmed ghost then gives way to the clean frame beside it (`no_proposal: "no interpolator"`), and
only a jump left (`unmade`: the `frames`, `neighbours` and `off` of the group the search stopped at)
sets `why`, `rife` and `install`.
`--repair off` cuts the loop as filmed and records `jump_repair: {"applied": false, "why": "--repair off"}`. A loop of another state has its wrap read (the seam bridge, below); a cut played once is not touched (`"why": "not a gait state (...)"`).

Until the flow reading picked the frames, the size reading did: a step whose change of coverage,
over the whole box or the hair behind the body, was at least 1.4 times the loop's median remade
the frame after it, half way between its neighbours. Checked on the 2026-10-03 takes with it
(`retime.py` in the experiment folder is the reference), that engine replaced the same frames as the experiment on every take tried — Lite side E1
[0, 16, 8] (score 1.75 → 1.42), Lite back-diagonal NE2 [13, 0] (1.60 → 1.43), Pro side E1
[3, 15, 19], Pro back-diagonal NE2 [9], Lite front S2 [0, 11, 14], and nothing in the smooth
Lite side E2. About 0.7 s a replaced frame on the Mac's GPU. Those takes have not been read with
the flow reading.

### The flow reading — a frame off the walk's way, not a large step

The jump score above counts how much a step changes. A walk's own steps change by very different
amounts — a leg swung through moves far in one frame, a planted foot not at all — so on an ordinary
walk the size reading names fast swings as jumps (on the synthetic walks below, five to eighteen
steps of a loop that has none), and a frame that pops against the way a part is going reads no
larger than a swing. `repair.flow_scores` and `repair.flow_targets` read the same loop by direction
and speed instead, and `--repair auto` takes out the frames `flow_targets` names (above).

**What is read.** Each frame is a point of the loop's path: its coverage and its shading (luma
times coverage) inside the union box, averaged down to a grid a third of the blur's size and
blurred with a sigma of 3 % of the body height (`FLOW_BLUR`) — a shape moved an ordinary step still
overlaps where it was, so a step is a short move along the path, not a jump to an unrelated
picture. The box is read in nine windows, each half its width and half its height, at a stride of a
quarter (`FLOW_GRID` 4), so a tail, a hand or a foot is whole in at least one window.

| Per frame t, per window | What |
|---|---|
| off the path | \|x(t+1) − 2 x(t) + x(t−1)\| / 2: how far the frame lies from the straight line between its two neighbours — the second difference, the acceleration of pose-from-video papers, and the error a straight in-between of the neighbours would make there |
| usual step | the window's median step over the loop, and no less than the median of those over the windows (a window where little moves is not read on its noise) |
| `off` | off the path over the usual step; a frame's `off` is its worst window's |
| `turn` | there, the cosine between the step into the frame and the step out of it: below 0 the part goes back the way it came (1 where either step is still) |
| `into`, `out` | there, the step into the frame and the step out of it, in the window's usual steps: a lurch is one far larger than the other |

Off the path is direction and speed at once: |out − in|² = |in|² + |out|² − 2 |in| |out| cos θ.
A part that moves on the way it was going, slow or fast, keeps it small; one that turns back, or
lurches, makes it large. A part that slows to a stop and swings back (a leg at the end of its swing)
turns back over small steps, and stays small.

**On its drawings.** A clip filmed on twos or threes shows each drawing for two or three frames
(`sprite_gen/video/held.py`; section 4, "Held drawings — a take to film again"): a step of nothing, then a step of two, at every drawing — a lurch at
every drawing, read frame by frame, and a median step that falls between the two kinds. The path is
read on the drawings instead (`flow_drawings`): the hold is `held.measure`'s, read on the path's own
steps as a ring, a frame whose step from the frame before it is under its boundary repeats that
drawing, and every frame that shows a drawing reads the drawing's values. A loop drawn every frame is
a drawing a frame. A loop whose only large steps are the steps into and out of a few jumps can read
as held to `held.measure` — its ordinary steps the repeats, its jumps the drawings; on those drawings
its jumps would read as ordinary steps, and a drawing made again would stand for every frame of a
run of five. Its runs are not its hold, so a hold is taken only where at least three in four runs
(`HELD_RUNS`) show their drawing for the hold, a frame more or less (a slip); the loop is otherwise
read a drawing a frame. A held loop's runs are its hold by construction.

**Which frames.** `flow_targets` takes the worst drawing at or over `FLOW_LIMIT` (3.0) first. A
group is one or two drawings in a row (`FLOW_SPAN`) that take it or lie beside it; its in-between is
the straight line of the path from the drawing before the group to the drawing after, each drawing at
its own time (the time rule above). Of the groups whose in-between brings every drawing from the one
before them to the one after under the limit, the one taken is the one whose every drawing the
in-between moves furthest (`move`: the least, over the group's drawings, of how far the in-between
moves it in its worst window, in that window's usual steps). Those are the drawings off the flow;
the drawings beside them read off only because a neighbour does, and a drawing the in-between
hardly moves is not off at all. Ties go to the smaller result, then to the fewer drawings. The path
then reads with that in-between and the next worst drawing is read. A group never takes, or is made
from, a drawing a group before it took (two made frames side by side are made from each other), or
one that shows a frame `fixed` names (the repair passes the frames already made, given way or
refused). A drawing no group of up to two brings under the limit is `unfixed`.

| Off the flow | The group taken | Not |
|---|---|---|
| one frame flung | that frame (on twos, the two frames that show it) | two frames with it: the in-between hardly moves the second |
| two frames in a row flung together | both | the frame on either side of the pair: its in-between can bring the reading under the limit by smoothing the pair's edges, and leaves the pair standing — it moves half as far as the pair's own |
| a part that snaps back at a wrap that does not close | the frame on one side of the wrap | two frames, which spread the snap over a frame the in-between moves two thirds as far |
| two frames flung with one frame between them | each its own group, where the frame between reads under the limit once the first is made (flung 8 % of the body height on the synthetic walker) | flung farther (12 %), no group of up to two drawings straightens either while the other stands: the frames around them are `unfixed`, kept and named |

The straight line is a stand-in for the frames RIFE makes there: it says which frames to take out,
not how the made frames will look.

Each group records its `frames` in play order, the `drawings` it takes (the frames that show each),
its `neighbours` and each drawing's time `t`, the worst drawing's `off`, `turn`, `into`, `out` and
`window` (fractions of the box, x0, y0, x1, y1), `move` and `off_after`; an `unfixed` entry its frames,
the same readings and the best `off_after` a group reached. The record has `limit`, `span`, `blur`,
`grid`, `hold`, `drawings`, `off_max_before` and `off_max_after`.

**The limit.** On the synthetic walker of `tests/video/test_loop_flow.py` and its variants (measured
2026-10-15), an ordinary loop reads at most 1.65 — drawn every frame with its quick swing faster or
even, at 24 frames, with a tail swaying twice a cycle, or on twos. A frame whose tail is flung 5 % of
the body height against its sway reads 2.8 to 4.1, by where the sway is; 8 % reads 3.7 to 6.4; 3 %
reads 2.2, under the limit; a tail that runs a quarter of a cycle short snaps back at the wrap at 5.3,
half a cycle short at 4.8. A part flung over the body reads less (1.8 to 2.2 at 5 %, 2.6 to 3.0 at
8 %, where the tail crosses the torso): there the coverage does not change, only the shading. Two
frames in a row flung as far are a plateau, each of its edges half as far off the path (2.2 at 5 %,
3.4 at 8 %). `FLOW_LIMIT` 3.0 sits above every
ordinary loop with room to spare: a frame is taken only where it plainly leaves the way.

**The methods weighed.** The question — which frame of a sequence leaves its motion — has answers
already; they were measured side by side on the synthetic loops above, each read in the same windows
of the same blurred path, over the same usual step unless said otherwise.

| Method | Source | On the synthetic loops | Taken? |
|---|---|---|---|
| Size of a step (the jump score above) | section 2, above | the ordinary walks name 8 to 18 steps over 1.4; a frame flung 3 % reads 2.61, under an ordinary walk's 2.67 | no: it counts size, not direction |
| **Second difference — the acceleration — on the drawings** | Kanazawa et al., "Learning 3D Human Dynamics from Video", CVPR 2019, section 6.1: the acceleration error, and where there is no ground truth "we simply report the acceleration" | ordinary ≤ 1.65; flung 5 % 2.8 to 4.1; flung 3 % 2.2 | **yes**: it is the error of the straight in-between, the repair's own question; Pillow and numpy |
| Interpolation error, with RIFE as the interpolator (the optical-flow warping family) | Baker et al., "A Database and Evaluation Methodology for Optical Flow", IJCV 2011 (interpolation error); Lai et al., "Learning Blind Video Temporal Consistency", ECCV 2018 (temporal warping error) | the same ranks; ordinary up to 2.11, on the quickest swing | no: a RIFE call for every frame (about 3 s each on a CPU in the cloud, section 1), and flow is least sure on large, occluded and flat-coloured motion in 2D animation — AnimeRun (Siyao et al., NeurIPS 2022, table 2) measures RAFT's end-point error at 58 px over motions past 50 px and 35 px on occluded pixels, where a pop is |
| Optical flow itself (Farnebäck; RAFT) | Farnebäck, SCIA 2003; Teed and Deng, ECCV 2020 | not run | no: OpenCV or PyTorch for the engine (a PyTorch RIFE was turned down for the same reason, section 1), and the weakness above |
| A periodic (Fourier) curve fitted to each region's path | gait as a Fourier series: Cunado, Nixon and Carter, CVIU 2003 | ordinary up to 2.94; flung 3 % 2.23 | no: a quick swing and a slow plant need more harmonics than a one-cycle loop has frames to fit them with |
| Constant-velocity prediction (a Kalman predictor) | Kalman, Journal of Basic Engineering, 1960 | ordinary up to 3.29; names the frame after the flung one | no: one-sided — the frame after a flung frame misses most |
| Savitzky–Golay smoothing residual | Savitzky and Golay, Analytical Chemistry, 1964 | the same ranks at half the scale | no: a wider smoothing of the same second difference |
| A Hampel test (median and MAD) of the acceleration | Hampel, JASA 1974; Leys et al., JESP 2013 | ordinary walks up to 10; flung 3 % 20 | no: an even walk's acceleration varies so little that its z has no fixed bound. The median stays, as the usual step |
| Frame deletion and insertion by optical-flow consistency | Chao, Jiang and Sun, IWDW 2012 | not run | no: the same question for edited video, answered on flow (above) |
| Outermost points (each row's outermost column) fitted on a periodic curve | — | not run on these loops | no: the outermost point of a row switches from one part to another (a foot leaving the row), a jump on an ordinary step |

### The eased retime — a part that rushes on its way

The flow reading names a frame that leaves the way a part is going. A part can keep its way and
still jump: a tail that runs short of the cycle — swinging on a beat of its own, as a generated
clip's tail can — rushes on at the wrap, covering in one step what it covers in eight elsewhere.
Each frame there lies near enough the line between its neighbours that the flow reading lets it
pass, or takes the frame at the wrap out and leaves the rush over two steps, half as fast and still
far faster than the steps around it. `repair.surges` reads the speed itself, and a stage after the
flow groups makes the frames either side of the rush again, at times that spread it.

**The rush.** On the flow reading's own path, windows and usual steps, on the loop as the ghost and
flow rounds left it: a run of one or two steps (`SURGE_SPAN`) whose mean step in a window is at
least **3.0** usual steps (`SURGE_LIMIT`) and **3.0** times the mean of the three steps either side
of it (`SURGE_RATIO`, `SURGE_AROUND`), and through which the part goes on the way it came — no
drawing from the run's first to its last turns back (the flow reading's `turn` at least 0; a step
that turns back is a frame flung, the flow repair's). Rushes are taken worst first by that ratio,
none sharing a step. On the synthetic walker of the tests (measured 2026-10-17):

| Loop | Largest step, usual steps | Largest run over 2 against the steps either side | Read |
|---|---|---|---|
| ordinary — drawn every frame, at 24 frames, on twos | 2.1 to 2.4 | 1.9 to 2.2 | no rush |
| a frame flung 5 % of the body height against the sway | 5.0 | 3.9 | not a rush: it turns back (the flow repair takes it) |
| the tail 5 % of a cycle short | 3.8 at the wrap | 3.2 | a rush |
| 8 % short | 4.9 | 4.4 | a rush |
| a tenth short — the flow reading's 2.6, under its limit | 5.8 | 5.8 | a rush |
| a quarter short — the flow repair makes a frame at the wrap, and two steps of 5.8 are left | 10.6 (7.2 after the flow repair) | 13.2 (6.6) | a rush |

**The stretch.** Three or four drawings either side of the rush (`EASE_WIDTHS`), between the two
drawings at its ends, which are kept: three a side for a one-step rush is six frames, four a side
for a two-step one nine. It is made from the frames as filmed: a drawing the flow repair made
inside it is made again (`supersedes`), never made from, and a stretch never ends on one. A stretch
never holds a frame given way, refused, or read off the flow turning back. Four a side is taken
only where it eases the part more than three by over 2 % (`EASE_SLACK`); one stretch a loop, the
worst rush that has one. With the frames the ghost and flow rounds replaced, a stretch makes at
most half the loop (`MADE_SHARE`): `video-loop-compare` reads no more than that back as the
repair's (its source restoration, [loop comparison](loop-comparison.md)).

**The times.** How far the part has gone is the path of its coverage's centre inside each window
that reads the rush, in px, through the frames as filmed: a centre moves as far as the part does,
where the distance between two blurred states stops growing once they no longer overlap, and a
rush split by it would come out faster in the middle than the search reckoned. Every frame of the
stretch is given a time, on a grid of a twentieth of a step (`EASE_GRID`); of the timings that go
forward and keep, in every step,

- each body measure — the foot line's centre (`loop.foot_centre`) and the torso (below), x, and the
  lowest solid row, y, the ground it stands on (`repair.drift_points`) — within the stretch's largest
  step as it played, and half a pixel (`DRIFT_PIXEL` − `EASE_MARGIN`), drawn between the frames as
  filmed, and
- every other window of the flow reading — the rest of the body — within its own largest step in
  the stretch, read on the line between the states of the frames as filmed: no leg or arm hurried
  past what it did there (read again on the frames made, below),

`repair.ease_times` takes the one whose largest change of the part's step from one step to the next,
from the step into the stretch to the step out of it, is least (`accel`): the part speeds up and
slows down as gradually as the body lets it, from the speed it came in with to the speed it goes on
with — an ease in and out whose ends are its own. Of the timings within 2 % of that, the one nearest
the frames' own times, so a frame the part does not need moved keeps its time.

**The torso.** Drift is the body sliding or lifting off its line. A part that sways on its own beat
— a tail, the hair's ends — pulls the body's mass along, and moving such a part is what the repair
is for: that is not drift. The body's point is the torso, the head and torso the motion anchor
tracks (`repair.Torso`): `auto_motion.discover` on the loop as cut finds the two regions the anchor
would track — a part swaying on its own beat does not hold still enough to be chosen — and each frame
is matched by the anchor's masked NCC within the anchor's own fine search (`motion_anchor.SEARCH_RADIUS`:
14 px across, 28 down — a walk's bob lifts the head further than it sways) of where the loop's first
frame has them, the two weighted as the anchor weights them (0.7 and 0.3), x to a fraction of a pixel
on the parabola through the costs either side of the best. Where the anchor finds no such regions,
or one of them reaches the edge of that search on a frame of the loop, the body's mass
(`loop.body_centre`) stands in. The round records which (`torso`: `regions`, or
`stand_in` and `why`), and reports the body's mass beside the drift either way, as information
(`drift.body_mass`: its largest step over the stretch as it played, `bound`, and on the frames made,
`moved`). The overlapped wrap below reads the same torso.

**Made, then read again.** Each drawing not at its own time is made by RIFE between the two frames
as filmed either side of its time, at that fraction of the way between them (in frames), and shown
on every frame the drawing was; a time on a frame as filmed is that frame. One call a drawing made,
at most nine, outside the three of the flow groups (`ease_calls`). The stretch is taken only where,
on the frames made:

| Read on the frames made | Refused for (`rejected_for`) |
|---|---|
| no made frame carries an interpolation fault (section 4) | `fault` |
| no body measure — the foot line, the torso, the ground — moves in a step more than a pixel past the stretch's largest step as it played — the measures are read on whole pixels, the ground on a row (`drift`: `bound`, `moved`, `over`; the body's mass beside it, `body_mass`) | `drift` |
| the part's largest step over the stretch falls (`part`) | `no gain` |
| no frame of the stretch is read further off the flow than before and at or over its limit (`off_max`) | `off the flow` |
| no rush is read in the stretch in a window the part does not rush in: the rest of the body was hurried | `a new rush` |
| no window the part does not rush in steps further than its own largest step over the stretch as it played (`rest`: `windows`, `step_max_before`, `step_max_after`, in the usual steps of the loop the flow groups left, and `faster`) | `the rest faster` |
| every window the part does not rush in steps, in each step of the stretch, as far as it did in that step as the loop played, to within a quarter of its usual step (`EASE_BEAT`; `beat`: `bound`, `off` per step in play order, its largest over the windows, and `off_max`) | `the body off its beat` |

A refused stretch keeps the loop as the flow groups left it; the loop keeps its length and every
frame outside the stretch is as it was.

**The body's beat.** A frame RIFE makes is the whole picture at one time, the legs, the arms and
the head with the part. A time that gives the part's rush more steps gives the body the same: where
the part is given its time the legs slow to a stop, and after it they hurry on. In such a frame the
part's step and the body's are in the ratio they have at that time, so bounding the body's step
below as well as above (the search bounds it only above) leaves the rush to spread by no more than
the share of its own step the body may lose: a body held to three quarters of its step leaves the
part three quarters of its rush. The stretch is
therefore read on the frames made, step by step, against the loop as the flow groups left it: every
window of the rest of the body steps as far as it did there, to within a quarter of its usual step,
the walk's beat as `tests/video/test_loop_ease.py` reads it on a walker's legs (the centres of their
colours, a quarter of their ordinary step). A window of the rest of the body can hold some of the
part as well; a stretch that moved the part alone could be refused there, and the engine makes none —
its frames are made whole. Every stretch the stand-in makes on the synthetic walkers is refused for
it (below); the rush is recorded, `surge_after` names it, and the loop plays as the flow repair left
it, its frames each made at their own time and the legs on their steps as filmed. A part spread
apart from the body would have to be made apart from it, which this stage does not do.

**The rest of the body, read again.** The search reads each window of the rest of the body on the
line between the states of the frames as filmed. The frames made go the body's own way between
them, and where a part moves far between two frames — a leg through its swing, two legs crossing —
its window steps further than that line says, most where a step is stretched across a frame as
filmed. So the bound is read again on the frames made, on the flow reading's own steps, and a
stretch that goes past it in any window is refused whole: nothing is allowed over. Better readings
from the frames as filmed alone were weighed and none holds. Against the stand-in's frames made at
every time of the search on the 36-frame walker, over the steps a search step can take, a step each
reading allows is made as much as 1.48 times the bound on the line, 1.39 times along the window's
path through the frames, 1.35 times along it with a smoothly changing speed and 1.31 times with each
step lengthened by the circle through its bend; and the stand-in's own speed integrated, the ceiling
of every reading by speed, eases none of those walkers drawn every frame. Searching again with the
frames made added to the frames as filmed does not settle either: after five tries those walkers
were still refused.

| On the synthetic walker (the stand-in in-between of `tests/video/test_loop_ease.py`) | Part's largest step, on the frames made | Frames made | The rest of the body off its beat, at most (`beat`) · the legs off their steps as filmed, ordinary steps |
|---|---|---|---|
| 24 frames a cycle, the tail a quarter short, after the flow repair at the wrap | 5.3 → 3.4, no rush left | 8, at four a side, 8 calls | 1.52 · 2.70: refused |
| 16 frames, 15 % short (under the flow reading's limit) | 3.6 → 1.7, no rush left | 6, at three a side, 5 calls | 1.35 · 2.00: refused |
| 16 frames, a fifth short | 3.1 → 1.5, no rush left | 6, 6 calls | 2.42 · 2.63: refused |
| 14 frames, 15 % short | 3.1 → 1.5, no rush left | 6, 6 calls | 1.60 · 1.50: refused |
| 48 frames on twos, a quarter short, after the flow repair | 5.3 → 3.4 (on its drawings), no rush left | 8 drawings, 16 frames, 8 calls | 1.52 · 2.70: refused |
| 36 frames, 5 % to 30 % short drawn every frame, 10 % to 30 % on twos | refused: the rest of the body 1.5 % to 19 % past its own largest step on the frames made | 6 to 9 calls | refused |
| the flow repair alone, each frame made at its own time | | | the legs 0 off |

Every stretch of the stand-in is refused: the loop keeps the flow repair, `surge_after` names the
rush, and the calls the stretch made are spent. Up to 2.45.0 the first five were taken, and their
legs stopped where the tail was given its time. On a Mac's GPU a call is a
second or so; on Modal's CPU with `-x -z` twelve to thirty seconds a
280 × 520 frame (section 1), so a loop that rushes costs two to four and a half minutes more, and a
loop that does not, nothing.

`jump_repair` carries `surge` (the worst rush on the loop the flow groups left: `from`, `to`,
`steps`, `frames`, `windows`, `ratio`, `speed`, `around`), `surge_after` (on the loop as it now
plays, or `null`), and `ease_skipped` where a rush was read and no stretch was made (`no
interpolator`, `the stretch would leave more than half the loop made`, `the frames around it were
given way, refused, or flung against the way`, `no timing keeps the body in bound`). The round `why: ease` carries the
`surge` it eased, `width`, `anchors` (the frames kept at its ends), `frames` and `drawings` (the
stretch in play order), `sources` (the frames as filmed it was made from), `supersedes`, `span`
(frames from anchor to anchor), `made` (per drawing: `frames`, `time` and `own` in frames from the
first anchor, `from`, `t`, `proposal`, `faults`, `look`), `accel` (`before`, `chosen`), `part`
(`windows`, `step_max_before`, `step_max_after`), `rest` (`windows`, `step_max_before`,
`step_max_after`, `faster`), `beat` (`bound`, `off`, `off_max`), `drift`, `off_max`, `calls`, `faults`, `look` and `outcome`, and
`replaced` or `rejected_for`. The record's `replaced` holds the frames it changed, the
seam gate reads the rendered cells, and the summary names them (`eased`). Without RIFE the rush is
cut as filmed with a warning line (`why`: "RIFE not installed — the rush cut as filmed"), and
`--repair on` refuses.

### The overlapped wrap — a part on its own beat that no cut of two steps closes

A walk cut two steps long by the clip's crossings ([the step
count](#the-step-count--two-steps-read-off-the-clips-crossings), section 3) closes for the legs. A
part on a beat of its own — a tail swinging round every 41 frames on a walk of 26 — closes on no cut
of two steps: the cut's last frame and its first show it in two places the clip never filmed side by
side, and the seam gate refuses every one. Cutting again does not help, and the eased retime does not
reach it: where the part turns back at the wrap, the frames there read off the flow against the way,
which the retime never eases over.

The clip goes on past the cut. Its frame a cycle after one of the cut's shows the legs at the same
point of the walk, and the part where the clip carried it. `video-loop --anchor motion-auto` makes the
frames beside the wrap again between the two (`repair.overlap_wrap`), before the jump repair: the wrap
then plays a step the clip filmed, and the part goes over from the clip's way to the cut's over those
frames.

| Rule | Value | Why |
|---|---|---|
| When | `--repair auto` or `on`, and `--overlap-wrap on` (the default); a cut whose two steps the clip's crossings show (`cycle.steps.by: crossings`) and whose wrap the gate reads over `--seam-max` on its frames as corrected (`loop.wrap_on_cells`) | the legs close; what does not is a part on its own beat. A cut whose steps are not counted keeps the gate's word |
| The frames a cycle apart | the clip's frame a cycle after each of the cut's first frames (`after`), or before each of its last (`before`), one past the widest run, as far as the clip after its lead-in holds them; each moved by the analysis translation between the two — the drift the clip walks over a cycle — and corrected as its cut frame was (`loop.cycle_apart`) | placed where the cut frame is, the in-between moves the part, not the body |
| How many | four frames, then six, eight, ten and twelve (`OVERLAP_WIDTHS`), never past half the loop (`MADE_SHARE`), on the side whose frames a cycle apart differ least from the cut's over the run | the fewer made, the more of the loop is the clip's own; a longer run where a shorter one rushes the part or leaves the body no room, its weights going over in smaller steps |
| Each frame | made by RIFE between its two frames of one time — the late (the clip's frame a cycle after, or the loop's own before the wrap) and the early (the loop's own, or the clip's frame a cycle before) — at the weight of the early the search chooses (`repair.overlap_weights`, below), and read with RIFE's sixteen single passes for a part torn (`rife.Rife.read`, `repair.overlap_frame`, below); a weight of 0 or 1 shows that frame whole, no call, and a frame at one weight is made once, whatever the run | each frame at its own time, the legs where they were on both; next to the wrap near the frame that continues the clip across it |
| The weights | in play order, from 0 at the frame before the run to 1 at the frame after it, never going back, on a grid of a twentieth: of those that keep every window of the rest of the body within its own largest step over the stretch as the loop plays it and every body measure within the stretch's largest step and half a pixel (below), read on the line between the two frames' states, the one nearest the ease in and out x²(3 − 2x), x = p/(width + 1) at the run's p-th frame (`repair.overlap_weight`) | the part goes over in an ease in and out, held off it only in the steps where the body has no room: a window that closes all but a step still moves by what it differs a cycle apart, and a weight that moves there pushes it past its own step |

The stretch runs from the frame before the run to the frame after it, the wrap inside, and the body is
held to it as the loop plays it — the eased retime's bound, on the same torso ([the
torso](#the-eased-retime--a-part-that-rushes-on-its-way) above), never on the clip's frames a cycle
apart. A made frame next to the wrap stands near the clip's frame a cycle on: the part going over
pulls the body's mass, which is the move the run is made for, reported and not held
(`drift.body_mass`); a torso the clip's frame a cycle on shows elsewhere — the clip walked on over the
cycle — would slide, and the bound holds such a frame toward the cut's own, the wrap then closing only
as far as that lets it. The run is taken only where, on the frames made:

| Read on the frames made | Refused for (`rejected_for`) |
|---|---|
| no made frame carries an interpolation fault (section 4), a part torn among them (`torn`, below); the frames are made the weight nearest a half first, and the first with a fault ends the run, the rest of it not made (`unmade`) | `fault` |
| the seam gate's reading passes | `seam` |
| the loop, played round three times, still covers two steps: the crossings read on it, its middle turn judged `two` (`loop.ring_steps`) | `steps` |
| no body measure (`repair.drift_points`: the foot line, the torso, the ground) moves in a step more than a pixel past the largest step of the stretch as the loop plays it | `drift` |
| the part — the windows of the flow reading where the frames a cycle apart differ by a usual step or more somewhere in the run (`OVERLAP_APART`) — steps no further than it did over the stretch as the loop played it | `no gain` |
| no rush the loop did not have is read across the stretch (`repair.surges`) | `a new rush` |
| no window of the rest of the body steps further than its own largest step over the stretch as the loop plays it | `the rest faster` |
| no frame of the stretch is read further off the flow than before and at or over its limit | `off the flow` |

The first run taken ends the search; where none is, the cut is kept as filmed and the gate refuses it
as before. The jump repair after it makes nothing from, beside or again over a frame it made
(`jump_repair.overlapped`); a filmed ghost beside one gives way to the filmed frame on its other side.
A loop drawn on twos or threes is not overlapped: a frame made beside the wrap would split a drawing.

**A part torn.** A frame made between two frames a cycle apart bridges the part's whole move in one.
Where the part swings over a smaller one — a tail across a hand — RIFE's flow drags the small part apart
with it, and on art painted without a line no other reading sees it (section 4, "A part torn"). So each
frame the run makes is read with RIFE's sixteen single passes as well, and a frame they disagree on over
a part is refused (`torn`). The frame nearest a half is made first, where the part's move is bridged
furthest; the first frame refused ends the run. A clip whose part crosses another on every run is
refused the overlapped wrap and kept as filmed, for the gate to refuse: a torn hand is not delivered.
On the file's third walker — painted without a line, seen from behind, 480 × 640, its big tail swung
from 48 to −8 degrees across its hand between two frames a cycle apart — the real RIFE's frames at
weights 0.5 and 0.7 read 0.44 and 0.26, over the 0.21 that names a part torn, with no other fault;
swung 10 degrees past no hand, its frames at 0.3, 0.5 and 0.7 read 0.04 at most.

On the walker of `tests/video/test_loop_overlap.py` — seen from the front, 26 frames a cycle, its
tail swinging round on a beat of 41 frames, 73 frames, with the stand-in in-between (measured
2026-10-18):

| The cut 29/26 | Seam gate (2.0) | Jolt index | The loop played round |
|---|---|---|---|
| as filmed; every cut of two steps the gate was asked reads 6.0 to 6.2 | 6.23 | 0.71 | two steps |
| the jump repair alone: the flow repair takes the frame at the wrap, and the tail turns back there (turn −0.91), so the retime eases nothing | 3.17 | 0.44 | two steps |
| overlapped: four frames after the wrap refused (`a new rush`), six taken at the ease (weights 0.05, 0.2, 0.4, 0.6, 0.8, 0.95) | 1.04 | 0.30 | two steps |

The file's second walker tells the part from the torso: a tuft of 30 px on a short tail bobs up and
down on the loop, the walker in its place, and drops at the wrap (3.41 at the gate); a cycle on, the
clip carries the tuft on its bob and 8 px aside a frame.

| The second walker | The run | Seam gate (2.0) | Torso, largest step (stretch → made) | Body's mass (stretch → made) |
|---|---|---|---|---|
| the tuft alone carried over | four frames at the ease (0.1, 0.35, 0.65, 0.9) taken, 4 calls | 3.41 → 1.53 | 0.03 → 0.05 | 0.71 → 3.95, reported |
| the whole walker also 3 px aside a cycle on (its torso slides) | every run of four to twelve held at 0.85 of the cut's own frame next to the wrap, all refused (`seam`), 15 calls | 3.41 → 3.04 to 3.15 | 0.03 → 0.05 at most | 0.71 → 1.96 to 3.91 |

The report's `wrap_overlap` carries `seam` (the reading that asked for it), `seam_max`, `widths`,
`calls` (the frames made), and each try in `tries` — `side`, `width`, `frames`, `stretch`, `part`
(`windows`, `apart`), `eased` and `off_ease` (the ease and how far the weights left it), `weights` (those
the search chose), `made` (per frame made, in play order: `frame`, its weight `t`, `proposal` with its
`torn`, `faults`, `look`) and `made_order` (the frames in the order made); where a fault ended the run,
`unmade` (the frames it did not make), `faults`, `look`, `outcome` and `rejected_for` (`fault`); where the
run was made whole, `shown` (the frames shown otherwise than as cut), `seam`, `steps`, the part's
`step_max_before` and `step_max_after`, `rest` (`windows`, `step_max_bound`, `step_max_after`, `faster`),
`drift`, `off_max`, `rushes`, `faults`, `look`, `outcome` and `rejected_for` (`no weighting keeps the
body in bound` where the search found none and nothing was made); where a run was taken, `side`,
`width`, `frames` (those shown otherwise than as cut) and `seam_after`; `interpolator`, with the binary's
calls (`binary_calls`) where RIFE was located for it; and `skipped` where none was tried (drawn on twos, a loop too short
for a run that leaves half of it filmed, the clip holds no frames a cycle from the cut's beside the wrap,
no interpolator, turned off by `--overlap-wrap off`). The summary names it; a `warning:` line names the frame that ended each run refused for
a fault ("fault (torn at frame 3, the rest not made)"). A made frame
that cross-fades two drawings inside its outline is kept and named in a `warning:` line, as for the
jump repair. Without RIFE the wrap is cut as filmed with a warning line (`why`: "RIFE not installed —
the wrap cut as filmed"), and `--repair on` refuses. Each frame made costs the binary 34 calls (the frame
as every call makes it, and its sixteen passes in colour and coverage), a frame at one weight made once,
whatever the run; a run ends at its first fault, the frames past it not made, so a wrap whose every run
is refused for a part torn costs the frames each run makes up to its first torn one, and one whose every
run is made whole and refused for something else at most forty frames, outside the jump repair's — the
first walker above takes ten frames, the second four where it is taken and fifteen where its torso slides.

**Turned off.** Where RIFE runs on a CPU, those calls take minutes a frame: on two CPU cores with the
augmentation off (`SPRITE_GEN_RIFE_TTA=`), a frame of a 960 × 960 synthetic walker took 160 to 174 s, its
sixteen passes nearly all of it — so a wrap refused for a part torn at its first frame costs minutes a run,
and one made whole up to forty frames. A caller that stops the loop at a deadline would stop it there, and
the loop would not fail on its own seam. `video-loop --overlap-wrap off` turns the overlapped wrap off
alone: nothing is read or made for it, `wrap_overlap` says so (`skipped`: "turned off (--overlap-wrap
off)", no `tries`, `calls` 0) with the `warning:` line every skipped wrap has, and the gate refuses the cut
as filmed, the loop failing on its seam as where every run is refused. The jump repair, the eased retime and
the gate are as with it on; `--repair off` turns it off with the rest.

### The part pop: a hand or a foot that jumps at the wrap

The flow reading reads a walk loop in nine windows, each half the body's box across and down (`FLOW_GRID` 4), on the body's coverage and shading blurred by 3 % of the body height (`FLOW_BLUR`). The surge reading ([the eased retime](#the-eased-retime--a-part-that-rushes-on-its-way)) reads the same windows.

A part the size of a hand that jumps a few per cent of the body height at the wrap (the step from the loop's last frame to its first) and stays there passes both, for three reasons: first, the window is far larger than the hand, and its usual step is set by the tail and the legs that pass through it; second, the flow's `off` is half the change of a step, so a part that jumps once and goes on reads at half; third, the distance between two blurred states stops growing once the part is further than the blur from where it was, so even a window the size of a hand reads such a jump under the surge reading's speed limit of 3.0 usual steps.

Windows small enough to see the hand (a grid of 8 or 12, or windows the size of a hand) do read the jump, but they read many of the loop's ordinary steps over the same limits too.

Synthetic evidence in `tests/video/test_loop_part_pop.py` shows the failure: a walker seen from the side, 36 frames a cycle, with a forearm held out in front ending in a fist. Its fist slips 2.5, 3.5 or 5 % of its height over the loop and jumps back at the wrap. The flow reading names no frame (1.46 off the flow at most against the limit 3.0) and the surge reading names no rush. The part reading below names the fist.

The part reading (`part_pop.read`) runs on every walk and run (the states the jump repair reads), on the cut as filmed, after the anchor and the wrap blend and before any repair, whatever `--repair` says. It is read on the loop's drawings (`repair.flow_drawings`), as the flow reading: a loop on twos is read a drawing at a time.

Parts are squares 8 % of the body height on a side (`PART_PATCH`), seeded every 8 % over the body on the loop's first drawing, where the square is covered (coverage at least 0.15, `PART_SEEN`) and shaded (luma standard deviation at least 0.02, `PART_TEXTURE`). They are read at a body height of 400 px (`PART_READ`). Each square is found drawing to drawing by normalised cross-correlation of its luma (over a mid grey by coverage) within 8 % of the body height of where it was (`PART_SEARCH`), to a fraction of a pixel on the parabola through the correlations either side.

A part counts only where every one of its steps reads back: the square where it landed, looked for in the drawing before, lands within 1.5 px (at 400 px) of where the part was (`PART_FOLLOW`), and no search reaches its edge. A part is dropped at its first step that does not read back, and nothing is read on it. Fur whose pattern repeats, a part whose drawing changes, and a part that moves further than the search in one drawing (the legs of a run) are not followed. An eye that blinks can still be followed both ways; it is then read like any other part and the bounds below leave it, so the read-back check does not by itself drop every blink.

A part's usual step is its median step, no less than the median of the followed parts' medians (the flow reading's `_flow_scale` rule, so a part that hardly moves is not read on its noise) nor 0.5 px (`PART_NOISE`).

A part does not move evenly: a tail swings slow, fast, slow again. So a run of one or two steps (`SURGE_SPAN`) is read against the curve the part's own steps either side of it draw, not against their mean: the three steps before the run and the three after (`SURGE_AROUND`), each a step vector, fitted x and y apart by a second-degree polynomial in time (least squares: the local fit of a Savitzky-Golay filter), and the run's steps predicted on that curve. How far the run's steps leave their prediction, as a mean length, is its `jerk`. A part that eases steps where its curve goes, however fast; a frame that jumps does not.

A part pops where the run is, all at once:

| Bound | Value | Read on | Why |
|---|---|---|---|
| `PART_POP_FLOOR` | 2 % of the body height | the run's largest step | a tremor stays under it |
| `SURGE_LIMIT` (the surge reading's own) | 3.0 | the jerk over the part's usual step | as the surge reading bounds a window's speed |
| `PART_POP_CURVE` | 5.0 | the jerk over the larger of: the step the curve predicts there, the curve's own scatter over the six steps it was fitted to (root mean square), 0.5 px | a part that eases fast steps where its curve goes |
| `PART_POP_LEAD` | 2.0 | the jerk over the part's largest jerk on any other run as long whose curve was fitted without this run's steps | a part that moves far inside the loop as well (a tail's flick, a foot snapping up) leaves its curve there too |

Its way is not read. Its kind is recorded: `flung` where the step after the run takes back at least half of its move, or the run takes back that share of the step before (`PART_FLUNG` 0.5); otherwise `stays`.

At the wrap the runs read are those that touch the step from the last drawing to the first. A loop under 8 drawings is not read (`skipped`): a run and the three steps either side need eight. A loop too short to hold another run whose curve leaves this one out has nothing to lead, and does not pop.

The choice of the curve was measured 2026-10-26 on synthetic paths: 50 ordinary paths (a slow part of about 1 px a step at a body height of 400 px, on a gentle wobble of 36 steps, whose speed rises and falls in a sine-squared bell over 3, 4, 5, 6 or 8 steps, to 6, 12, 20, 30 or 40 px a step at its fastest, up to a tenth of the body, placed across the wrap and inside the loop) and 24 broken paths (the slow part jumping 8, 12, 16 or 20 px in one step and staying there, or coming back the next step, or jumping inside an ease of seven steps that peaks at 8 px; at the wrap and inside).

| Baseline | Ordinary paths named | Broken paths missed |
|---|---|---|
| the mean of the three steps either side, 6 times it, and 1.5 times the part's largest step elsewhere (the reading measured first) | 15 of 50: the bells of 3 and 4 steps from 12 px a step at the wrap; inside, those of 3 steps from 20 px and of 4 steps from 12 px | 6 of 24: the jumps of 8 to 16 px inside an ease |
| the curve, 4 times the predicted step, lead 2.0 | 3 of 50: the bells of 4 steps at 30 and 40 px at the wrap and at 40 px inside | 8 of 24: every jump inside an ease |
| the curve, 5 times, lead 2.0 (taken) | none | 8 of 24: every jump inside an ease |
| the curve, 6 times, lead 2.0 | none | 8 of 24 |

5.0 is the least multiple of the predicted step that names none of the bells. A jump that rides a fast motion and is less than five times the step its curve gives there is not named (a 20 px jump inside an ease of 8 px is about 2.2 times it). The mean baseline names the largest of those (20 px) only because it also names fast eases.

The reading has four named gaps. First, a part that is not followed is not read: on a fast run the fastest parts leave the search between drawings (the test walker drawn at 16 frames a cycle, a run, its legs swinging 12 % of the body a frame, has many squares unfollowed and none of those followed pops); where a part's drawing changes at the wrap, it is not followed there and its jump is not named. Second, a detail smaller than 8 % of the body (a finger) is read with what is round it. Third, a loop under 8 drawings is not read. Fourth, a jump riding a fast motion, under five times its curve's step.

The cut is kept: the engine takes no other cut for a part pop, whichever cut it is (the search's or one given). Frames made between two at the wrap do not mend it (the part's jump is still in the clip, spread over a step or two), so a loop a part pops in is cut to the same bytes as a loop where it is not read (`cycle/`, the strip, the GIF, the WebP). Taking another cut is not done because the part reading reads parts the size of a hand, not the whole body's change at the wrap: a cut chosen on it alone is not held to the whole body. It waits on a gate that reads the whole body's change between frames at the wrap.

The loop as cut is read again (the part reading on the frames as anchored); `part_pop` records `outcome`: `none` where no part pops, `closed` where the jump repair changed frames of the cut and no part pops on the loop as it plays (`after`, the reading again), `named` where it plays as filmed; and `left`, the pops that play.

A take to film again: where a part pops on the loop as it plays, the report's top-level `retake` is `{reason: "part-pop", reasons: ["part-pop"], part_pop: {pops, cut, searched}, limits: {floor, speed, curve, lead}, remedy}`, else `null`. `pops` each: `seed`, `from`, `to`, `at`, `body_pct`, `jerk_pct`, `speed`, `curve`, `lead`, `kind`, `margin`. `remedy` is `retake` where the engine's search chose the cut, and `recut-or-retake` where the cut was given (`--cycle fixed`, or `pinned`); `searched` says which. The shape is `video-cycle-align`'s per-loop `retake` (section 4): `reason` in kebab-case, the numbers under the reason's snake_case key, `limits`, and `remedy` (`retake`, `recut`, `recut-or-retake`), so a caller reads both alike.

`--unfixed refuse` refuses a loop with such a `retake`, whatever `--repair` says (the repair makes no frame for a part pop and the cut stays, so whatever `--repair` says the pop plays as filmed). `--unfixed warn` (the default) keeps it with a warning line.

A take to film again where the walk starts or stops: where the clip shows its steps, it stands still somewhere (`stands`), and the cut kept is not counted two steps (its `verdict` is not `two`), no cut of two steps closes between the stands: `loop.stand_retake` sets the report's `retake` to `{reason: "start-stop", reasons: ["start-stop"], start_stop: {stands, run_limit, crossings, cut, verdict, code, why}, remedy: "retake"}`. Where a part also pops, one `retake` names both: the first reason's record, `reasons` lists both (`["part-pop", "start-stop"]`), and each reason's numbers sit under its own key (`part_pop`, `start_stop`) (`loop.retakes`). The cut is kept as cut (nothing else changes in the loop). `--unfixed refuse` does not refuse a `start-stop` loop (it refuses jump-repair leftovers and `part-pop` only, as before); the loop is kept and named.

A loop is cut to the same bytes whether a part pops in it or not (`cycle/`, the strip, the GIF, the WebP); every walk and run report gains `part_pop` and `retake` (`retake` is `null` where no part pops).

Cost: the reading is NumPy on the CPU, about a second a loop; nothing else is read.

Named gaps of the take to film again: no other cut is read or taken (a pop the search's cut has may be absent at another cut of the clip; finding it waits on the whole body's gate); the pop is named for a cut given too (`remedy` `recut-or-retake`).

Synthetic outcomes (tests in `tests/video/test_loop_part_pop.py`, measured 2026-10-26 on alex-macbook-m1, except the third, a probe beside the tests):

On the front walker of `tests/video/test_loop_overlap.py` with its tail on the walk's beat and a fist held out (73 frames, 26 a cycle, `--anchor motion-auto`), the fist held still: no part pops, `retake` is `null`, and the loop is the bytes it is with no part read at all.

On the same walker with its fist drifting 0.18 px a frame through the clip: every 26-frame cut jumps it back about 4.7 px at its wrap (3 % of the body). The search's cut (two steps by the crossings) is kept and named (margin 1.39); `retake` `remedy` `retake`, `searched` true; the loop is the bytes it is with no part read.

On the walker with its fist drifting only through the clip's first 12 to 24 frames (a probe): the search takes a cut past the drift (33/26), its repeat error there being least; no part pops.

On the side walker cut whole (`--cycle fixed`), its fist slipping 3.5 % back at the wrap: five squares on the fist pop together (the worst a jerk of 3.43 % of the body, 10.2 usual steps, 14.6 times its curve's step, lead 7.4, stays); one warning line names the worst and the four beside it; `retake` `remedy` `recut-or-retake`, `searched` false; `--unfixed refuse` refuses the loop; under `warn` the loop is the bytes it is with no part read.

On the side walker whose tail swings, whose far foot lifts 16 px and whose eye shuts across the wrap, and the same walker drawn as a run (16 frames a cycle): no part pops anywhere; the walk is cut to the bytes it was.

`part_pop` in the report carries the bounds (`read_px`, `patch`, `search`, `follow_px`, `floor`, `speed`, `curve`, `lead`), `drawings`, `hold`, `body_px`, `parts` (seeded), `followed`, `usual_floor_px`, `pops` (each: `seed`, `from`, `to`, `steps`, `at`, `body_pct`, `jerk_pct`, `usual_px`, `speed`, `curve`, `lead`, `kind`, `back`, `margin`; worst first, at most one a part; `margin` is the least of the four terms over their bounds, 1 or more pops), `nearest` (the run nearest to popping, popped or not), `skipped` where it was not read, `outcome` (`none`, `closed`, `named`), `left` (the pops that play as filmed), and `after` (the reading of the loop as it plays, where a frame was changed).

The summary line's JSON carries `part_pop` (`pops` with `from`, `to`, `at`, `body_pct`, `kind`, `margin`; `outcome`; `left`) and `retake` (`reason`, `remedy`).

The engine prints these warning lines on stderr, in its own words, for a pop left as filmed:

```
video-loop: warning: film this direction again (part-pop) — the part at (<x>, <y>) px of frame <from> moves <body> % of the body's height into frame <to>, <jerk> % off the curve its steps either side draw (<speed>x its usual step, <curve>x the step that curve gives, <lead>x its largest such jump elsewhere; it stays (or: comes back); <n> parts beside it pop with it): <why>; --unfixed refuse refuses such a loop (docs/loop-repair.md section 2, "The part pop")
```

```
<why>:
the search's cut is kept as cut, and no other cut is taken for a part: film it again
the cut was given (--cycle fixed or pinned): cut it where no part pops, or film it again
```

`--unfixed refuse` fails the loop with this error line:

```
video-loop: a part pops at the loop's wrap — film this direction again (part-pop) — <the warning line's words from `the part at` to the why> (<kind> length <L> frames from <start>); the motion does not play smoothly
```

**Beyond walks: a strike.** The jump repair and the part reading run on walks and runs only. An attack has a strike: an intended rush, the largest acceleration of the loop at the impact frame, larger still with a sword. Read as a part pop, it would be named. Ways to widen it later, not built: the state declares its strike frames (or a window round them) and the reading leaves them out; the lead is read against the same part's like accelerations elsewhere in the loop, so a strike the loop repeats is its own motion; a one-shot cut (`--cycle once`) has no wrap to read.

**The body's surge reading.** The body's surge reading (`repair.surges`, `SURGE_LIMIT`, `SURGE_RATIO` 3.0 over the mean of the `SURGE_AROUND` steps either side) reads a window's run against the mean of its neighbours, the baseline the synthetic paths above show naming fast eases. Whether the curve can serve there is not measured; the flow and surge readings are unchanged.

### The seam bridge: a loop that is not a walk, whose wrap jumps

A user expects an idle loop, jump, attack, or generic in-place action to be smoothed at its wrap like a walk. A walk's flow repair reads every frame against the line between its neighbours. An idle's own motion (a blink, an eye half shut, a mouth that opens) leaves that line too, so flow repair would remake those frames and take out the motion. A loop that is not a walk is therefore not read for its flow; its wrap alone is read, leaving the walk path unchanged.

The seam bridge (`repair.seam_bridge`, driven from `loop.run_loop`) runs under `--repair auto` (the default) or `on` for any state that is not a gait (not walk or run). It runs on a cut that plays as a loop, and skips cuts played once (`--cycle once`, `once-return`, or an effect's lifetime), which retain `"why": "not a gait state (...)"`. A pinned clip (`--cycle pinned`) whose pin the gate refuses is skipped (`skipped`: "the pin does not close: the clip is refused on it"), because the clip is refused on its pin whatever its wrap shows. It runs after the anchor (`--anchor`) and the wrap blend (section 6), reads the wrap as blended, and runs before `cycle/`, the strip, the GIF and the WebP are written.

| Rule | Value | Why |
|---|---|---|
| When | `--repair auto` or `on`; a state other than walk or run; a cut played as a loop; not a pinned clip whose pin the gate refuses | a cut played once (`--cycle once`, `once-return`, an effect's lifetime) needs no wrap; a clip refused on its pin is refused whatever its wrap shows |
| The reading | On the frames as they play, cropped to their union box, on the seam gate's analysis thumbnail (`loop.cell_features`, the reading `loop.wrap_on_cells` uses); the wrap step is compared with the mean of the three steps either side of it (`repair.SURGE_AROUND`, 3) | a loop whose steps are uneven (a body moving only in whole pixels, with many zero-step frames) would otherwise read an ordinary wrap as a jump |
| Not a jump | The wrap ratio is at most `--seam-max` (default 2.0), or the wrap step is at most `loop.PIN_NOISE_MAX` (0.005) | `skipped`: "the wrap reads as a step of the loop"; a wrap step at most 0.005 is re-render noise on a near-still loop where large ratios show nothing; nothing is made, no interpolator is looked for, and the loop is the bytes `--repair off` gives |
| Drawn on twos or threes | The cut is drawn on twos or threes (`cycle_drawings.hold` over 1, read on the cut) | `skipped`: "drawn on twos or threes: a frame made round the wrap would split a drawing"; nothing is made, no interpolator is looked for, and the loop is the bytes `--repair off` gives |
| Too short | A loop under 12 frames (`repair.BRIDGE_MIN_FRAMES`) | `skipped`: "a loop of N frames, under 12: no steps either side of the widest stretch"; under 12 frames cannot fit the widest stretch and three steps either side without sharing; nothing is made, no interpolator is looked for, and the loop is the bytes `--repair off` gives |
| How many frames and which | One frame, then two, then four (`repair.BRIDGE_WIDTHS`, fewest first), ceil(m/2) frames before the wrap and floor(m/2) after (`repair.bridge_run`): for a 48-frame loop, frame 47; frames 47 and 0; frames 46, 47, 0, and 1 | testing the fewest frames first keeps as much filmed motion as possible while centring the repair across the wrap |
| The straight line | The straight line between the two anchors (their thumbnails mixed in equal steps); rejected if any step along that line exceeds `--seam-max` times the mean of the three steps either side (`rejected_for: "line"`) | stops before making frames if even an even blend across the anchors jumps |
| How each frame is made | Made by RIFE between the anchors either side of the run, at its own time between them (`repair.between_time`) | the loop keeps its length and every frame its time |
| Faults | Checked for smear, outline, ghost (ghost screen on the stretch and three frames either side, empty); `rejected_for: "fault"`. Frames with `crossfade` over 0.015 are kept with a `warning:` line | avoids taking interpolation artifacts, matching the shared policy of section 4, while keeping slight cross-fading inside outlines |
| Taken | The first run where no frame has an interpolation fault and every step of the stretch is within `--seam-max` times the mean of the three steps either side (`rejected_for: "seam"` otherwise) | ends search on the smallest valid bridge; if none is taken, the filmed loop is kept for the seam gate to judge as before |
| How many calls | At most 1 + 2 + 4 = 7 plain RIFE calls per loop | at most one call per frame across candidate widths |
| Without RIFE | Under `--repair auto`, wrap is cut as filmed, `jump_repair` records `why`, `rife`, `install`, and `unmade`, and a warning line names `sprite-gen rife install`; under `--repair on`, fails with the install command | RIFE is looked for only when a frame is to be made |

| Case | Wrap reading | Bridge action | Gate result |
|---|---|---|---|
| J = 2.6 | 2.65 against steps beside it | Bridged by one frame (frame 47, halfway between frames 46 and 0; stretch reads 1.65) | Passes at 1.55 on rendered cells (refused at 2.41 with `--repair off`) |
| J = 5 | 4.63 against steps beside it | One frame: straight line reads 2.77, none made. Two frames: made, stretch reads 2.35, refused. Four frames (46, 47, 0, 1 at times 0.2, 0.4, 0.6, 0.8 between frames 45 and 2): stretch reads 1.85, taken | Passes at 1.84 (refused at 4.30 as filmed) |
| J = 12 | 10.68 against steps beside it | Straight lines read 5.43, 3.76, 2.57; nothing made | Refused at 10.22 as before |
| J = 1 (closed loop) | 1.10 against steps beside it | Not bridged; no interpolator looked for | Same bytes as `--repair off` |
| Whole pixels (x = round(6 sin), closed loop) | 2.06 against loop mean step, 1.51 against steps beside it | Not bridged | Read by the bridge alone |
| Near-still loop (shading patch) | 57 times steps beside it, wrap step is 0.00035 (under 0.005) | Not bridged | Read by the bridge alone |
| Drawn on twos (J = 2.6) | 4.47 against steps beside it | Skipped: drawn on twos or threes | Refused at 3.00 as filmed |
| Real RIFE (rife-v4.6 with augmentation, J = 2.6) | 2.65 against steps beside it | Closed by one frame without faults | Passes gate |

Cost: at most seven plain RIFE calls per loop, one per frame. From the RIFE table in section 1 (a 960 x 960 frame on Modal `cpu=2`), a frame takes 4.3 to 6.2 s with no augmentation, and 44.0 to 60.5 s with `-x -z`. Seven frames take 30 to 43 s with augmentation off, and five to seven minutes with `-x -z`. A CPU host sets `SPRITE_GEN_RIFE_TTA=` (empty: no augmentation). The readings take little time, evaluating a few thumbnails and running the ghost screen on the stretch and the three frames either side rather than the whole loop.

Limits:
- Anchors up to five frames apart can tear a small part that a large part swings across, and no fault check above detects it (on the third walker of `tests/video/test_loop_overlap.py`, a big tail across a hand, the reading `rife.torn` names it; it costs 34 calls of the binary a frame and the bridge does not run it). The walk flow repair, over two or three frames, takes the same risk; that is why the bridge stops at four frames.
- A wrap where only a small part changes (such as a mouth alone) reads as a step on the whole body thumbnail and is not bridged.
- A blink or mouth movement inside the frames made is not kept: the frames made show the move between the anchors (at most four frames, or a sixth of a second at 24 fps).
- A wrap that needs more than four frames to close is not bridged: the loop is refused by the gate as before; regenerate the clip.

The report record stays `jump_repair` (so source projection and restoration, which read `jump_repair.replaced` and `jump_repair.facing`, read it as they read a walk's), with `reading: "seam"`, `limit`, `floor`, `around` (3), `widths` ([1, 2, 4]), `facing`, `replaced` (the frames made, in play order), `calls`, and `applied`. Where it read the wrap, it records `seam` (the wrap over the mean of the steps beside it), `wrap` (the wrap step itself), and `hold`. Where it was not tried, it records `skipped`. Each try in `tries` records `width`, `frames`, `anchors`, `line`, and where frames were made, `made` (per frame: `frame`, `t`, `proposal`, `faults`, `look`), `faults`, `look`, and `after` (the stretch's largest step over the mean of the steps beside it, read again), along with `outcome` (`accepted` or `rejected`) and `rejected_for` (`line`, `fault`, `seam`). Where a run was taken, it records `width` and `seam_after`. Where a frame was made, `interpolator` records `{"kind": "rife-ncnn-vulkan", binary, model, tta, binary_calls}` or `{"kind": "injected"}`.

The CLI summary carries `jump_repair` with `reading`, `seam`, `replaced`, `seam_after` (and `why`) where the wrap was read as a jump. `video-set`'s item record for a loop left unrepaired for want of RIFE carries `seam` where a walk's carries `off_max_before`.

Warning lines: where frames were made, one line names them, the anchors, and says to look at them in the loop or cut it with `--repair off`. Where the wrap jumps and nothing was taken, one line says which widths were tried and why each was refused, cut as filmed. Where RIFE was not found under `--repair auto`, one line says no frame was made round the wrap and names `sprite-gen rife install`. Where a frame made cross-fades two drawings inside its outline (`crossfade` over 0.015), it is kept and named in a `warning:` line.

## 3. The jolt index — reported on every walk, a gate only when asked

A loop whose steps change unevenly jolts even when no single step is a jump. Every walk or run
loop's report carries `jolt`, measured on the loop as it will play (after section 2's repair):

| Field | What |
|---|---|
| `index` | the **jolt index**: for every step, how far its coverage change strays from the mean of its two neighbours' changes; the mean of that over the median step. 0 for even steps; alternating big and small steps read high |
| `hair_index` | the same inside the hair box |
| `step_max_over_median` | the largest step over the median (what section 2 repairs) |
| `head.x`, `head.y` | the head's place frame by frame in % of the body's height over the loop: x (sideways) the coverage centroid of the top fifth, y (up and down) the body's top line. Each: `step_max_pct` (largest move in one frame), `step_median_pct`, `max_over_median`, `range_pct`, `worst_into_frame` |
| `reference`, `warnings` | the reference bounds (jolt index **0.43**, head sideways step **0.75** % of the body height) and what exceeds them, in words |
| `seam_pop` | the top band's jump into the loop's first frame over the loop's median step ("The seam pop" below); a pop over `--seam-pop-max` refuses the loop, the gate's word in `seam_pop_gate` ("The seam pop gate" below). |
| `gate`, `gated`, `over`, `passed` | the bounds the caller passed, whether they were enforced, what exceeded them |
| `measured` | `after the jump repair` or `as filmed` |

**By default nothing fails on the jolt.** A loop beyond a reference bound is kept, its report
lists the reason under `warnings`, and `video-loop` prints a
`video-loop: warning: … (reference bound; the loop is kept …)` line on stderr. The aim is fewer
refilms — section 2 repairs what it can without a new clip — and the reference bounds rest on
too few judged takes to refilm on (below). The seam pop is the exception: it is a gate by default
("The seam pop gate" below).

**A gate only when asked.** `--jolt-max` and/or `--head-step-max` turn the bound passed into a
gate on the repaired loop: beyond it the loop fails with
`video-loop: loop jolts — … regenerate the clip`, which a caller that buys a new clip can match.
Only the bounds passed are checked. With `--repair off` the loop is cut and reported as filmed,
never gated. A frame the head cannot be tracked in is named rather than read as smooth.

### Where the reference bounds come from — 46 takes, 2026-10-03

Eight-direction SD set, five generated views, Pro and Lite, four filming rounds (46 loops cut by
`video-loop --anchor motion-auto`), measured on the strip cells before and after section 2's
repair. Eleven of them were judged by the maintainer: nine kept for the final sets, two
refused for how they move — a Lite side walk whose every step alternated (its sibling take was
"completely natural"), and a Lite back-diagonal walk whose head "jumps".

| After the repair | min | p25 | median | p75 | p90 | max | the 9 kept reach | the 2 refused |
|---|---|---|---|---|---|---|---|---|
| jolt index | 0.055 | 0.178 | 0.212 | 0.243 | 0.260 | 0.414 | 0.414 (Pro back diagonal) | 0.237 (side), 0.196 (back diagonal) |
| head sideways step, % | 0.18 | 0.43 | 0.68 | 0.83 | 1.07 | 1.71 | 0.68 | 0.27 (side), **0.82** (back diagonal) |
| head top-line step, % | 0.49 | 0.98 | 1.24 | 1.71 | 2.47 | 3.17 | 2.42 (Lite back view) | 0.75, 1.71 |

Before the repair the jolt index's median was 0.258 (max 0.456) and the head sideways step's
0.70 % (max 2.88 %): the repair lowers both.

Read:

- **The head's sideways step is the only measure that separates the judged takes**: every kept
  take stays at or under 0.68 %, the back-diagonal take refused for its head moves 0.82 %. The
  reference 0.75 sits between them.
- **The jolt index does not separate them.** The refused side walk (0.237) sits inside the kept
  range, and a kept Pro back-diagonal walk reads 0.414 here — 0.4225 on the full-size cut frames
  `video-loop` itself reads (section 5). Any bound under that would flag a kept take, so the
  reference (0.43) sits at the kept takes' edge and flags nothing in this set. An alternating jolt
  like that side walk's is a known miss of this index.
- **The top line is reported, not bounded**: a walk seen from behind bobs more (the kept Lite
  back view reaches 2.42 %), so one bound would flag kept back views or pass the refused one Only its
  step into the first frame is weighed, against the loop's own steps (below).
- **Why no gate by default**: at the reference bounds, 18 of the 46 takes exceed one — 16 of 30
  Lite, 2 of 16 Pro, none of the nine kept. Lite's head sways about three times as far as Pro's,
  so as a default gate it would refilm about every other Lite walk: the opposite of the aim, on
  the strength of two refusals. The bounds stay a reference until more takes are judged.

### The seam pop — a part held above the head, swinging on its own beat

A staff, a flag or a raised spear can sway on a beat of its own, slower than the steps. A cut one
step long then ends with it somewhere else: the body closes at the wrap and the held part jumps.
The seam ratio does not see it — it is an area measure, and a thin rod moved many times its usual
step changes few pixels — and the head's sideways bound above sees it only when the jump is large
in absolute terms. What sees it is the top band of `head.x`/`head.y`: on a body holding something
higher than its head, that band is the tip of what it holds.

Every walk and run loop's `jolt` carries `seam_pop` (`repair.seam_pop`), measured on the loop as it
plays:

| Field | What |
|---|---|
| `x`, `y` | per axis of the top band: `wrap_pct` (its step from the last frame into the first, % of the body height), `step_median_pct` (the loop's median step), `wrap_over_median` (the wrap over the median, the median a pixel at least: a step under a pixel is the tracker's rounding) and `wrap_is_largest` |
| `pop` | the larger `wrap_over_median` of the axes whose largest step is the wrap; 0 when neither's is |
| `reference`, `pops` | the bound (**10**, `repair.SEAM_POP_REFERENCE`) and whether `pop` exceeds it |

- **Only the wrap, and only when it is the largest step.** The question is whether the cut closes,
  not whether the top moves: a walk seen from behind bobs, and hair redrawn every frame jumps
  inside the loop as much as at the wrap — for either the wrap is ordinary, and `pop` stays 0.
- **`--anchor motion-auto` chooses again.** The search measures the same thing on the frames it
  chooses from (moved by the analysis translation, `loop.wrap_pop_on`), for its first choice only.
  If that pops, every candidate is measured, and among those that do not pop it takes the scores
  within the usual 15 % of the best and the smallest pop (`cycle.seam_pop`: `first_choice`,
  `applied`, `measured`, `unread`, `chosen`). A first choice that does not pop is kept, and nothing past it
  is measured, so a loop without such a part is cut exactly as before. If every candidate read pops,
  the first choice is kept (`why`), and the gate then reads it as every motion-auto cut is read before
  it is cut ("The seam pop gate" below). Once the first choice pops, the held
  part is read below its top too, and the cut may come out two steps long ("The held side" below).
- **A cut whose top cannot be read is not one that closes.** A frame with nothing in the top band
  (a tip flung far above the rest in one frame) leaves the cut without a jump to read: it is
  recorded `skipped` (why) in place of `pop` — `first_choice.skipped`, or on a candidate row
  `seam_pop: null` with `seam_pop_skipped` — and counted under `unread`, apart from `measured`.
  It is never chosen on; a first choice that cannot be read is kept, nothing past it is measured,
  and `jolt.warnings` says the head could not be tracked.
- **A pop over the bound fails the loop:** a walk or run loop whose pop is over the bound is refused
  ("The seam pop gate" below). When the gate refuses, the pop is not also printed as a warning line
  and is not in `jolt.warnings`: the refusal says it. With the gate off, a pop over 10 is the warning
  line `video-loop: warning: the top of the silhouette jumps into the loop's first frame up or down …
  (over 10x): a part held above the head swings on its own beat and does not close at this cut` in
  `jolt.warnings`, as before. A sideways jump into frame 0 that the head bound already names is not
  said twice. A fixed or `--cycle periodic` cut is not chosen again, and the gate refuses it.
- **The bound** sits between walks holding a staff that swings on its own beat and walks without
  one, both on the cut as it plays and on the source frames the cut is chosen from. The synthetic
  walker of `tests/video/test_loop_prop_seam.py` — steps every 24 frames, a staff above its head swaying every 61 — reads 12 to 18 at the cuts the area
  measure alone chose, and 0 at the cuts chosen again.
- **What it does not see**: a held part that never reaches the top band (a spear held level, a
  sword at the hip). The lower end of a staff whose top closes while the shaft still swings about the
  hand is read by the held side below, behind this pop.

### The seam pop gate

The seam pop gate refuses any walk or run loop whose seam pop exceeds `--seam-pop-max BOUND|off` (default 10, the existing constant `repair.SEAM_POP_REFERENCE`). The metric `jolt.seam_pop` measures how far the top band of the silhouette (the head, or a part held above it such as a staff, a flag, or a raised spear) jumps from the loop's last frame into its first, over the loop's median step (`pop`), read on the loop as it plays, after jump repair. A pop exactly at the bound passes. The gate applies to every walk or run loop whose cut is gated by its seam ratio, whatever chose the cut (`--cycle fixed`, a periodic search, `--anchor motion-auto`) and whether or not the repair ran (`--repair off` included). When the gate refuses, the failure line is `video-loop: loop seam pop <pop> exceeds <bound> (<kind> length <L> frames from <start>): the top of the silhouette jumps into the loop's first frame up or down <percent> % of the body height, <ratio>x its median step — the loop does not close at its top; cut it where it does, or regenerate the clip`. It names the pop, the bound, the cut (kind, length, first frame) and, in words, how far and how many times its median step the top jumps, up or down and/or sideways: each axis whose largest step is the wrap and whose wrap is over the bound. The pop is not also printed as a warning line and is omitted from `jolt.warnings`.

The report gains `seam_pop_gate` on every walk or run loop, printed in the CLI summary and retained in failed reports:

| Field | What |
|---|---|
| `verdict` | `passed`, `refused`, `unreadable`, `off`, or `not-applicable` |
| `max` | The bound, or `null` when off |
| `pop` | The pop read, or `null` when unreadable |
| `why` | Explains why the top band could not be read for `unreadable`, or `a pinned cut is gated by its pin` for `not-applicable` |

- **Check order:** The seam pop gate is checked after the seam gate and the step gate. A cut those refuse is refused by them, with the same line as before.
- **Unreadable top band:** A loop whose top band cannot be read (a frame with nothing in the top band, for example `a loop frame has no head to track`) is not refused: `seam_pop_gate.verdict` is `unreadable` with `why`, and the existing warning line that the head could not be tracked is still printed. It is never passed quietly.
- **Pinned clips:** A pinned clip (`--cycle pinned`) is not gated by it: its gate is its pin, as for the seam ratio. Its verdict is `not-applicable`, with `why` set to `a pinned cut is gated by its pin`.
- **Disabling and `--seam-max`:** `--seam-pop-max off` turns the gate off: the pop is reported, and a pop over 10 is the warning line `video-loop: warning: the top of the silhouette jumps into the loop's first frame up or down … (over 10x): a part held above the head swings on its own beat and does not close at this cut` as before. The flag `--seam-max` does not lift this gate. A `--seam-max` past 2.5 lifts the step gate and the fixed cut's step count refusal because those read the same distance the seam ratio reads; the seam pop reads the place of the top band, a measure in the family of `--jolt-max` and `--head-step-max`.
- **Pre-cut checks under `--anchor motion-auto`:** Before a cut is taken, candidate cuts are read as the gate reads the loop, their frames corrected as the cut will be (`loop.corrected_cut`, one correction a cut for the seam and the pop: `loop.CutOnCells`) and checked for wrap jump (`repair.seam_pop_of`), short of jump repair. First, the search cut, after the top band has chosen again where it popped, is read; where the gate would refuse it, the next candidate in search order (equivalent cuts earliest first, then the rest best first) is read, taking the first of up to four cuts (`local_cycle.SEAM_TRIES`) the gate does not refuse. Where the gate would refuse all four, the first choice stays and the gate refuses it on the loop. This is recorded in `cycle.seam_pop_gate` (`max`, `tried`, `applied`, `why`). Second, among cuts of two steps chosen by foot crossings, cuts passing the seam gate are read for pop; the first of four cuts passing both gates is taken, adding `pop`, `pop_gate`, and `pop_skipped` to `step_count.seam_gate.tried` and `step_count.search`. Every motion-auto run corrects the search cut once before cutting it.
- **Byte for byte identity and synthetic results:** With the gate off, or wherever the gate passes, the gate changes no cut and no file: loop files are byte for byte what the previous engine gives, save an `--anchor motion-auto` cut taken in place of a first choice the gate would have refused before the cut. In synthetic tests on a standing rod walk, a fixed cut whose tip drops 11 px reads a pop of 11.0 and is refused, while a 9 px drop reads 9.0 and passes. Under `--anchor motion-auto`, the pop read before the cut equals the loop's own reading (6.0 drawn a frame each, 5.0 on twos), allowing a bound of 3 to drop that cut for the next candidate with pop 0.0.

### The held side — a staff that closes at the top and swings below the hand

A staff turns about the hand as the arm that holds it swings: once a cycle, two steps. One step on
it is back in the same place at the top band, moving the other way. Where the legs are drawn alike
the search reads that step as the cycle (the legs are back in place, near and far swapped), and a
cut one step long taken there closes the top — `seam_pop` reads nothing — while the shaft under the
hand swings back at the wrap. No cut one step long closes such a staff; a cut two steps long — the
cycle — does.

Nothing that reads the whole silhouette sees it on its own. On a walk without a held part the
silhouette's outer edges are hair, an arm or a foot, and a local jump at the wrap — read per tile,
per row edge, on thin parts only, or as a repeat one cycle on against two — reads as high on some
walks without a staff as on a staff that does not close. So the held side is read **only behind
the seam pop**: on a search whose first choice popped at the top, where the top band has found that
something is held, and on which side of the body.

`repair.held_edge`, on the frames the search chooses from (moved by the analysis translation,
`loop.HeldEdge`):

| Step | What |
|---|---|
| side | the side of the body's centre the top band sits on, for the popping first choice (`repair.held_side`) |
| edge | per row of the silhouette, its outermost column on that side (`repair.side_edges`) |
| per row | a loop closes where its last frame is the clip's frame before its first, and its first the clip's frame after its last: those two places, and the steps into and out of each against the clip's own steps there (a part back in its place but moving the other way swings back at the wrap), their mean over the row's median step inside the loop — a floor of 0.5 % of the body height, a pixel at least |
| `seam` | the 80th percentile over the rows; the held side `closes` under **1.2** (`repair.HELD_EDGE_REFERENCE`) |

A cut with fewer than two clip frames on either side has nothing to read its wrap against: it is
`skipped` (why), counted under `unread`, and never chosen.

`--anchor motion-auto`, once its first choice pops (`cycle.seam_pop.held_edge`: `side`, `reference`,
`measured`, `unread`, `doubled`, `chosen`, and `two_step` when it looked there):

1. Among the candidates whose top does not pop, those whose held side closes: the scores within the
   usual 15 % of the best, then the smallest held seam (`doubled: false`). A candidate row carries
   `held_edge` (`null` with `held_edge_skipped` where it could not be read).
2. If no candidate closes it: **windows twice a candidate's length** (every length 2L − 1 to 2L + 1
   for a candidate length L, at every start the clip holds). The candidate was one step; such a
   window is two — **one cycle** — of a repeat the search confirmed, not itself seen repeating: a
   three-second clip cannot show a window that long twice. One whose held side closes and whose top
   does not pop (a top that cannot be read is not taken) is cut, the smallest held seam first
   (`doubled: true`). The report says what it is: `cycle.steps` is `{"count": 2, "by": "held-edge",
   "step_lengths": [...]}` — the candidate lengths it is two of, each one step — `cycle.period_local`
   is its own length (it is screened as the one cycle it is), `cycle.coverage` says how much of a
   second cycle the clip holds past it ("The step screen" below), `cycle.seam_pop.chosen.steps` is 2
   and `strip.json` says `steps: 2`, which the set alignment reads (section 4). `two_step` records
   the lengths tried and how many were `measured`, `unread`, `top_pops` and `closing`. An explicit
   `--max-len` is the caller's ceiling on the cut and stays one: a window two lengths long over it is
   not read (`two_step_capped`: `max_len`, `lengths_over`), and where every such length is over it,
   nothing two lengths long is read at all. The state's default window is no ceiling here: without
   `--max-len` every length is read.
3. If no window one or two lengths long closes it, the top band's choice stands (as above),
   `held_edge.chosen` records its seam, and `jolt.warnings` and stderr gain
   `video-loop: warning: the held part does not close at the wrap on the right side of the
   silhouette, below its top: 5.50x its rows' median step (over 1.2); no window one or two lengths
   long closes it — it swings on a beat of its own`. Where `--max-len` kept windows two lengths long
   from being read, the line ends `no window one length long closes it, and windows two lengths long
   (47-49 frames) are over --max-len 25, so they were not read` instead; where the steps were counted
   (`--steps`, below), `no window of the counted length closes it`. A side that could not be read is
   said as well.

A first choice whose top does not pop reads nothing here: a walk without a part held above its head
is cut, written and warned about exactly as before.

The bound sits between cuts that close a held staff and cuts where it swings back at the wrap. The
synthetic walker of `tests/video/test_loop_prop_seam_held.py` — legs back every 24 frames, a staff
turning about the hand every 48 — reads 2.0 to 5.2 at every cut one step long whose top closes and
0 at the window two steps long taken; with the staff swinging every step instead it reads 0 to 0.5,
and the cut stays one step long. Below a staff on a beat neither one step nor two closes (every 37
frames) every cut one step long whose top closes reads 4.25 or more and no window two steps long
closes: the top band's cut stands, and it is warned about.

- **What it does not see**: a held part on the other side of the body from the top band (the top
  band names the side), and a held part whose cut needs more than two steps. A window two steps long
  needs the clip to hold it with two frames either side: a three-second clip of 30-frame steps has
  few starts for one. A one-step cut without a held part above the head is the step screen's
  (below). `--cycle fixed` cuts any window ([loop review](loop-review.md)); a two-step cut made that
  way carries no `steps`, and the set alignment asks for its count.

### The step count — two steps, read off the clip's crossings

A walk cycle is two steps: the left foot forward once and the right once. Seen from the side or a
diagonal, left foot forward and right foot forward are nearly the same silhouette, so a cut chosen by
how its shape matches at the wrap can be one step (a half step loop: one foot goes forward) or three
(the wrap puts the nearer foot back: a stomp in place), and a walk whose cycle the search window cannot
hold, or whose tail repeats on its own beat, is cut one step long or not at all. Pixels cannot count
the steps. The feet can: each step they pass each other once, a **crossing**, and a cut that covers two
crossings, joined at the wrap where the walk itself goes on, is one cycle (`crossings.py`).

`--anchor motion-auto` reads the crossings of the frames it searched and records them in the report
(`cycle.step_count`), whatever it cuts:

| Field | What |
|---|---|
| `mode` | `gap`: the feet are seen apart (the foot band's widest gap, `legs.FEET_APART`, in at least a fifth of the frames: side and diagonal views); `sign`: they are not, and the foot band's lean about the body's axis says which foot is nearer (front and back views) |
| `crossings` | where the feet pass, in the search's frames (`read_from`: after a lead-in); a pair is a crossing whose middle the clip bounds but does not show |
| `unconfirmed` | frames no reading confirms: a run of feet together cut by the clip's first or last frame that its twin does not confirm, the frames before the first side is seen, a change the clip ends on |
| `step_range` | the clip's own step (the median between crossings) and the range a step must lie in, a factor of √2 either way |
| `readable`, `why`, `alternation` | whether the clip shows its steps: three crossings or more, and in sign mode a lean that turns over a step on (correlation one step on at most **−0.7**, `crossings.ALTERNATION_MAX`) |
| `cut`, `search` | the cut taken and the pixel search's own cut (or why it found none), each with its `verdict`, `code`, `k` (steps covered), `phase` and `ring` |
| `cycles` | whether crossings mark cycles: `asked`, `interval`, `overlap`, `half`, `one`, `two`, `slack`, `cue` (`range`, `floor`, `swapped`, `one`, `says`, `why`; only with a view; `one` absent where `"alike"`; `says` is `"steps"`, `"alike"` or null), `bob` (`swing`, `rows`, `floor`, `twice`, `half`, `quarter`, `says`, `why`; `says` is `"cycles"` or null), `cycles` (`true` or `false`), `unwitnessed` (`true` only where nothing witnessed), and `why`; absent where not asked |
| `stands`, `run_limit` | gap mode, the runs of the feet together longer than any pass (over `run_limit`, √2 times the median run the clip shows whole: whole, or cut by the first or last frame), recorded as pairs of frames; only where there is one |
| `chosen_by` | `search` (kept), `two steps` (chosen on the count), and the search again where the count refutes its cut (`searched_again`) |
| `declined` | the best two-step cut, where none closed and the search's cut stood: its `cut`, `phase`, whether it passed the gates (`gated`) and its `seam` |

**Counting a cut.** Every crossing counted is one the clip shows whole. A cut s/L plays frames s to
s+L−1 and then frame s again where the clip itself plays frame s+L; the wrap joins two frames the clip
never filmed side by side, so nothing the loop shows across it counts as a step. Every step the cut
plays, on both sides of the wrap, lies in the step range (a stall, a stand with the feet together, a
stomp fail it: `step-long`, `step-short`, `stand`, `head-stall`, `tail-stall`); the walk's phase
(crossings so far, plus how far into the step) advances by k steps over the cut, and the wrap joins two
points of a step that far apart, which must lie within [1/√2 − 1, √2 − 1] of a step (`seam`). What the
clip does not show — a bounded crossing's middle, an open step's other end — is tried across its bound,
and the verdict is the one every value gives (`bounds`, `open-count`). A **two** is k = 2 that way,
with the loop as played showing exactly two crossings as well (`ring`); **refuted** is k steps, each
seen (`one step`, `three steps`); anything else is **uncertain** and never taken as two. `phase` is how
far the wrap is from joining the same point of the walk, in frames, the worst over the bounds.

**Crossings that mark cycles.** In gap mode (side and diagonal views) the step count reads a crossing
where the feet meet on the screen, taking one crossing interval to be one step. That is wrong where the
feet do not meet on the screen at every pass: the far foot passes hidden behind the near one (seen from
behind on a diagonal), or a foot steps toward or away from the camera and passes the other apart (a front
diagonal, a front view). There the feet meet once a cycle, the engine called a cycle one step, and two
steps were two cycles: a loop twice as long as the walk's cycle, which then plays at half the walk's
speed beside the rest of a set. Before any cut is judged,
`local_cycle.read_cycles(reading, distances, view=None, cue=None, cue_step=None, signals=None)` asks
whether the crossings mark cycles instead of steps. It is asked only in gap mode, on a readable reading,
and only where a pass can hide (`reading.overlap` over `crossings.HIDE_SHARE`, 0.5: the feet seen
together over half the interval at a pass) or where the clip's view is given
(`--view front|back|side|front_diagonal|back_diagonal`, default none) and is not `side`. From the side
the legs a step on are one silhouette, as near as a cycle on, and the picture cannot tell; so a side clip
with brief passes, or a clip with no view and brief passes, is not asked (the count stands exactly as
before). `repeat_at(distances, span)` measures how far the clip lies from itself `span` frames on: the
mean distance of every frame to the one a lag on, over the clip's mean step from one frame to the next,
the nearer of the two whole lags about `span` (none where the clip holds under half a lag of frames past
it). It is read at half the crossing interval (`half`), one interval (`one`) and two intervals (`two`).
The picture passes where `one < half` (one interval on is nearer than the pose a half interval on) and
`one <= two + CYCLE_SLACK` (`CYCLE_SLACK` = 0.5 of the mean step: one interval on is as near as two on).
But the picture is the same a step on as a cycle on wherever the legs are drawn alike, so the picture
cannot witness that an interval holds two steps. Neither can a foot cue's turn half an interval on: a
light fixed on the screen shades the feet by how far apart they are drawn, whichever leg leads, so a walk
whose crossings are its steps, its legs drawn alike, turns its cue over half an interval on just as a
walk whose other leg leads a step on does; and a cue that merely moves (the no-view shade) says nothing
about which leg leads. Read that way, a walk whose crossings are its steps was cut one step long and
counted two steps: a half-cycle loop, with no warning. The crossings mark cycles only where a witness
that reads no leg says the interval holds two steps: one line of the body, its top line
(`legs.strike_signals` `top`, one row per frame) or its first solid row (`legs.body_signals` `first_row`),
rising and falling twice an interval on the screen and over its feet, the same line in both places. The
body rises and falls once a step whichever leg leads, so two rises an interval are two steps an interval.
`local_cycle.bob_word(signals, interval)` reads the body's top line less its flow
(`off_flow(top, interval)`), not the raw line. `local_cycle.off_flow(values, interval)` returns `values`
(per frame) less their flow: each frame minus the mean of the interval about it (a moving average one
interval long, its two end frames weighted by the part of a frame the interval covers), kept only over
the frames the interval fits about (the first and last `ceil(interval/2 - 0.5)` frames drop out), and
what is left minus its least-squares straight line. A rise and fall once or twice an interval averages to
nothing over one interval and is left whole. A flow slower than the interval is taken out: wholly where
it bends no more than a cubic (a constant, a straight drift, an arc, a cubic), in part where it is a slow
sway a few intervals long. Its record: `swing` is the range of the line left over the body's median
height; `rows` is that range in pixel rows. Where the clip is under an interval long so nothing is left:
`says` null, `why` says its top line less its flow is not read. Where `swing` is under `BOB_FLOOR`
(`0.005`) or `rows` is under `BOB_ROWS` (`2`): `says` null, `why` says the line's range less its flow is
under 0.5 % of the body's height or 2 rows, so it does not rise and fall; `half` and `quarter` are not
read. The top line is read in whole pixel rows, so a picture drifting up or down makes the line step a
row up and down as it rounds, and that rounding, less its own mean over an interval, stays under 2 rows.
Otherwise it reads the line left's correlation half an interval on (`half`) and a quarter of an interval
on (`quarter`), both with `crossings.correlation_on` (each the larger of the two whole lags about the
span). It says `"cycles"` only where `half` is `BOB_TWICE` (`0.28`) or more and `quarter` is `-BOB_TWICE`
(`-0.28`) or less: the line is as it was half an interval on and turned over a quarter on, which is a
rise and fall twice an interval. Otherwise null, and `why` gives both values. A rise and fall twice an
interval is as it was half an interval on and turned over a quarter on; a step's bob (once an interval)
is turned over half an interval on; any flow left over (slower than the interval) is as it was at both
half and a quarter of an interval on, so it cannot pass the quarter test. A sway of the whole picture as
large as the bob or larger, between about one and three intervals long, can hide a true bob, and then
the crossings stay on steps with the warning (two intervals, named), not one cycle. It next reads `edge`,
the number of frames whose lowest solid row is the frame's last row (`legs.body_signals` `edge`). Where
any frame's is: `says` null, `why`: "its lowest row is the frame's last row in {n} of {N} frames: the
feet may run off the frame, and the picture moving is not read off them". There the lowest row is the
frame's, not the feet's. `local_cycle.twice_an_interval(values, interval)`: `values` (per frame) less
their flow (`off_flow`), then a least-squares fit of a mean, the harmonic twice an interval (cos and sin
of 4 pi k / `interval`) and the harmonic once an interval (cos and sin of 2 pi k / `interval`), k the
frames left counted from the first; it returns the twice-an-interval harmonic as a complex amplitude
(its cos coefficient less i times its sin coefficient), in the values' units: a line rising and falling
a rows either way twice an interval reads a in magnitude. Lines read so over the same frames and
interval subtract as they move. None where fewer than `BEAT_FRAMES` (`8`) frames are left. Where none:
`says` null, `why`: "{name}'s rise and fall twice an interval is not read against its lowest row and
upper body: under 8 frames are left less the flow". `{name}` in each `why` is the line's name, from
`local_cycle.TOP_LINES` = `{'top': 'its top line', 'first_row': 'its first solid row'}`, the `line`
`bob_word(signals, interval, feet=False, line='top')` reads. `local_cycle.own_beat(signals, interval)`
reads the line (`top` or `first_row`), the lowest solid row (`reach`) and the lowest opaque row (`sole`)
with `twice_an_interval`. What is left of the line's rise and fall against the lowest row (`less_lowest`,
the line's less the lowest solid row's or less the lowest opaque row's, whichever leaves less) gives the
zoom that would make it: the median frame zoomed in by 1 + `less_lowest` / the body's median height
(`legs.zoomed`). Each band's `zoom` is how far that moves the band's width, in pixels, measured on the
body as it is drawn, not assumed of its outline: a band whose sides run straight down moves by the zoom's
whole part, a band across a top that widens as it goes down (a pointed head, an ear's tip, a hat's point)
by less, a band under a point by none. A band is seen where its `zoom` is `BAND_FLOOR` or more. Each
band's `beat` is its width's rise and fall twice an interval (`twice_an_interval`), in pixels up and
down. `reads`, for a band seen, is the band's `beat` over its `zoom` (its width's rise and fall twice an
interval, in pixels up and down, over how far the zoom moves its width). It is read whatever its turn:
a zoom moves a band's width in step with the line it lifts, but the band's own parts swinging across it
(arms, a cape) move it too, in step with the line, against it, or a quarter of the beat off, and the
zoom's part is not told from theirs. As much of the zoom as a band reads is taken out whatever its turn,
so a band whose width moves against the line adds nothing to what is left. `less`, for a band seen, is
what is left of the line's rise and fall twice an interval less as much of the zoom as the band reads:
`less_lowest` times (1 - `reads`), and nothing where `reads` is 1 or more. It returns `beat`, `lowest`,
`sole`, `less_lowest`, `bands` (`{rows, width, zoom, beat, reads, less}`, per band: its rows below the
first solid row, its width in the median frame and `zoom`, in pixels, `beat` in pixels, `reads` and
`less` null where the band is not seen), `less_bands` (the least `less`, null where no band is seen),
`still_bands` (the number of bands seen whose `reads` is at most 1 - `BOB_SCALE`, 0.4: they hold still
on the beat), `depth` and `whole`, the reading of the rows down the middle of the body, and `own`,
`less_lowest` where `whole` is True or no band is seen, else the smaller of `less_lowest` and
`less_bands`. None where a line is not read. `depth` is `local_cycle.body_depth(signals, interval)`,
which reads the rows down the middle of the body (`legs.body_signals` `strips`) at the top of the rise
against the bottom: the frames where the rise and fall twice an interval of the line over its lowest row
(`over_feet`) stands a half or more of the way to the top, and those where it stands a half or more of
the way to the bottom, each set's strips in a median. The strip at the bottom, as it stands and zoomed by
`need`, is fit to the one at the top, each fit moved as it fits best, in steps of a twelfth of the most
and at least half a row, at most `STRIP_SHIFT` (0.03) of the body's height either way and at least
`STRIP_SHIFT_MIN` (3) rows. It returns `need` (the zoom those frames' rise would be, of the body's
height), `risen` (in rows), `rigid` (how far the strip at the top lies from the one at the bottom as it
stands), `zoom` (how far from that one zoomed by `need`), `ratio` (`zoom` of `rigid`), `moved` (how far
the zoom moves the strip at the bottom from itself, the most `zoom` could tell) and `frames` (how many
frames stand in each set). None where no strip is read, the rise and fall is none, a set holds under two
frames or the frames at the top are not the taller. `whole` is True where `ratio` is `BOB_RIGID` (1.0)
or more, False under it, and None where it is not read: no `depth`, or `moved` under `STRIP_FLOOR`.
`STRIP_FLOOR` = `CUE_FLOOR` * `legs.TONE`, half the step the colours are drawn in, about 0.00196, as
`CUE_FLOOR` reads a cue: under it the strip is as good as plain to that zoom, which of the two fits
better is the rounding of its colours, and it is not read. A body rising whole carries those rows as
they are; a zoom as far as the rise draws them longer. They read the body's own where that zoom fits
them no better than they fit as they stand. Arms swinging across the bands hardly touch them. The floor
is the larger of `BOB_ROWS` and `BOB_FLOOR` of the body's median height. Where `less_lowest` is under
the larger of `BOB_OWN` (`0.3`) times `beat` and the floor: `says` null, `why`: "its lowest row rises
and falls with it: less the lowest row's, {name}'s rise and fall twice an interval is {x} rows of {beat}
({percent}; at least 30% and {floor} rows): the picture moves with the body, and the body's own is not
told from it". Then where `beat` is under `BOB_OWN` (0.3) times `less_lowest`: `says` null, `why`: "its
lowest row does the rising and falling: {name} does so twice an interval {beat} rows on the screen,
{percent} of the {less_lowest} left against its lowest row (at least 30%), and the body's own is not
told from it": read every four frames, a line on a beat of twelve leaves nothing of it twice an interval
of 24, and still correlates there, and what is left against the lowest row is then all the lowest row's.
Where no band is seen (`less_bands` null): `says` null, `why`: "no band of its upper body would show the
picture zooming: zoomed in as far as {name} rises and falls twice an interval against its lowest row
({less_lowest} rows of {height}), no band's width moves 1 px (at most {largest zoom}): the picture
zooming with the body is not told from the body's own". Then where `whole` is False: `says` null, `why`:
"the rows down the middle of its body fit a zoom as well as they fit standing: at the top of its rise
they lie {zoom} off those at the bottom zoomed {need}, the rise of {risen} rows, and {rigid} off them as
they stand ({ratio} of it, at least 1): the picture may zoom with the body, and the body's own is not
told from it". Then, only where `whole` is None, where `less_bands` is under the largest of `BOB_OWN`
(0.3) times `beat`, the floor, and `BOB_SCALE` (0.6) times `less_lowest`: `says` null, `why`: "its upper
body's width changes with it: less as much of the zoom as its band {a}% to {b}% of its height below its
top reads, {name}'s rise and fall twice an interval is {x} rows of {beat} ({percent}), {ratio} of the
{less_lowest} left against the lowest row (at least 30%, {floor} rows and 0.6), and the rows down the
middle of its body do not tell (a zoom as far as that moves them {moved}, under {STRIP_FLOOR}, or: they
are not read): the picture may zoom with the body, and the body's own is not told from it". Where
`whole` is True the bands are recorded and left out. Where `beat` is under `BEAT_STILL` (`1e-9` rows),
as for a body whose top line holds still on the screen while only its lowest row rises and falls (a body
that does not rise and fall whose foot drawn nearer is drawn lower each step), `{percent}` now reads
"{name} holding still" (`local_cycle._share`). The test is against `BEAT_STILL` rather than zero because
a top line that does not rise and fall fits to round-off, which is 0 under one machine's numpy and a tiny
nonzero value under another's. What the witness says is unchanged; before, `video-loop` stopped there on
a division by zero. Since every band's `less` is `less_lowest` times (1 - `reads`), this hold catches
every band seen that moves on the beat (its `reads` over 0.4) where the rows down the middle of the body
do not tell: there the body's own is witnessed only where every band seen holds still on the beat and
what is left is still at least 30% of `beat` and over the floor. Otherwise `says` `"cycles"`. A body
rising and falling on its legs leaves its planted foot, the lowest solid row, on the ground, carries its
upper body's bands whole, leaving their widths, and carries the rows down the middle of its body as they
are. The picture moved up and down carries the lowest row with the line by the same rows, so nothing is
left of the line's rise against it. The picture zoomed in and out widens each band as it lifts the line,
as far as the band's outline lets it, and draws the rows down the middle of the body longer. Where no
band would show the zoom, nothing tells the picture zooming from the body's own, and nothing is
witnessed. Where a band that would moves on the beat (arms swinging across it, a cape, or the picture
zooming), the rows down the middle of the body tell the two apart where they are read: a zoom as far as
the rise fits a body rising whole no better than it fits as it stands, and fits a picture zoomed with the
body better. Where they are not read, nothing tells the picture zooming from the body's own, and nothing
is witnessed. One band holding still says only that no zoom shows in that band: a head drawn narrower as
the picture zooms in holds still so. A body rising and falling so little that no band would move a pixel
is not told from a zoom either. Two cases this does not tell apart: (a) a picture zoomed on the beat
with every band seen drawn against the zoom, so that each band holds still, and the rows down its middle
as good as plain to the zoom (a zoom as far as the rise moves them under `STRIP_FLOOR`): its band widths
are those of an upper body rising whole on its legs with no zoom (measured on one such drawing, every
band within 0.78 px of such a body), and it is read as the body's own; (b) a body that rises and falls
twice a step on its own legs: the witness rests on a body rising and falling once a step, and such a
body reads as two steps an interval of one. A walk whose arms swing past its sides on the beat is
witnessed where the rows down its middle read a body rising whole, and is not witnessed where a band
seen moves on the beat (its `reads` over 0.4) and those rows do not tell; a body one flat colour down
its middle has no strip to read, and the bands decide. Where a walk is not witnessed, its crossings stay
on steps, or its cut is held, and either way it is named, never handed out as a cycle unnamed.
`legs.body_signals(frames)`: `bands` (per frame, each band's mean width in alpha, in pixels, read on the same
rows in every frame, those of the clip's median body height), `band_rows`, `median_frame` (the alpha of the
frame whose body height is nearest the clip's median), `edge`, 1 where the lowest solid row is the frame's
last row, else 0, and `sole`, the lowest row with a fully opaque pixel (alpha `legs.OPAQUE` = 255), or the
lowest solid row where a frame has none, because a shadow drawn translucent under the feet is solid to the
legs' reading (alpha 128 or more) and, drawn fixed on the screen, stays while the picture moves, so a
picture shaken twice an interval would otherwise be read as the body's own rise against it; the feet are
drawn opaque and move with the picture. It also returns `first_row` (per frame, the first solid row) and
`strips` (per frame, the rows down the middle of the body: `legs.DEEP` = 0.7 of its median height from the
first solid row, `legs.STRIP` = 0.12 of that height either side of the mean column of the solid pixels among
them, each row's mean premultiplied colour and mean cover, shape frames by rows by 4, values 0..1). Neither
is recorded in any report. `signals` are the frames' `legs.strike_signals` merged with
`legs.body_signals`; the body's median height is the median of `signals["height"]`. Only the step count's
witness reads them: `loop.read_steps` merges them into the signals it passes to `local_cycle.read_cycles`.
`legs.strike_signals` is unchanged, so the loop report's `gait.silhouette.signals` is unchanged. The
view's foot cue can only refuse cycles (`local_cycle.cue_word(cue, interval, step=None)`):
`legs.shade_cue` from the side or a diagonal, `legs.depth_cue` from the front or back, per frame. A cue
whose range over the clip is under `CUE_FLOOR` (0.5) of the step it is drawn in (`legs.TONE` of luma, or
one pixel row of the body's median height) does not move: it says `"alike"`, its correlation is not read
(`one` absent), and `why` gives its range and the step. Otherwise its correlation one interval on (`one`)
is read with `crossings.correlation_on` (trend removed): the cue says `"steps"` where it turns over one
interval on (correlation at `CUE_SWAPPED`, `-0.2`, or under: the other leg leads there, so the interval
is a step); and null otherwise (`why`: it does not turn over one interval on). It no longer reads its
correlation half an interval on and never says `"cycles"`. With no view given, no cue is read at all: the
shade of the feet's two halves is no longer read. Where the picture passes, the view's cue is checked
first: where it says `"steps"`, `cycles` is false, the count stands (two steps), `why` says the view's
foot cue turns over there: the other leg leads, a step, and no warning is given. Else where the bob says
`"cycles"`, `cycles` is true, and `why` says the line that stood (its top line or its first solid row) less
its flow rises and falls twice an interval on the screen and over its feet (its `half`, at least
`BOB_TWICE`; its `quarter`, at most `-BOB_TWICE`, on the screen), the body's own ({own} rows of {beat} left
against its lowest row and upper body, {share}; at least 30%): two steps an interval, the feet meet once a
cycle. Else `cycles` is false and `unwitnessed` is true: the crossings stay on steps, as from the side;
`why` says nothing that reads no leg witnesses two steps an interval (the bob's
own `why` in parentheses, or "no top line read"): a step on draws as a cycle on, as from the side, and
the crossings mark steps; and the loop's warnings (`jolt.warnings`, from `local_cycle.step_verdict`) gain
this line (`s/L` being the cut): "the clip one interval on is the same picture, as a walk whose feet meet
once a cycle draws it, but nothing that reads no leg witnesses it (<the bob's why>): its crossings are
counted as steps, as from the side", where its feet do meet once a cycle, each step counted in the cut
`s/L` is a cycle (`cycle-unwitnessed`). Where the picture fails, `cycles` is false and `why` says the
crossings mark steps, and neither `cue` nor `bob` is recorded. Where they mark cycles the reading holds
two steps an interval (`Reading.mark_cycles`: `per` 2, `step` half the interval, `step_range` halved): a
cut one interval long is two steps (one cycle) and is taken as such (`steps: {count: 2, by: crossings}`
written). Recorded in `cycle.step_count.cycles`:
`{asked, interval, overlap, half, one, two, slack, cue, bob, cycles, unwitnessed, why}`;
`cue: {range, floor, swapped, one, says, why}` (only with a view; `one` absent where `says` is `"alike"`;
`says` is `"steps"`, `"alike"` or null); `bob: {swing, rows, floor, twice, half, quarter, edge, body, says,
top_line, feet, why}` (`half`, `quarter`, `edge` (frames whose lowest row is their last) and `body` are absent
under the floors; `body` is also absent where under 8 frames are left; `body` is `{beat, lowest, sole,
less_lowest, less_bands, still_bands, own, bands: {rows, width, zoom, beat, reads, less}, depth, share, scale,
least, scale_least, band_floor}`: `share` = `own` / `beat`, `scale` = `less_bands` / `less_lowest` (`share`
and `scale` read 0.0 where the value they are divided by is under `BEAT_STILL`, null where no band is seen),
`depth` null or `{need, risen, rigid, zoom, moved, ratio, frames}` (`local_cycle.body_depth`'s; `need`,
`rigid`, `zoom` and `moved` rounded to 5 places, `risen` to 2, `ratio` to 3: a measure, recorded as the other
measures are), `least` = `BOB_OWN`, `scale_least` = `BOB_SCALE`, `band_floor` = `BAND_FLOOR`; the other
values rounded to 0.01; `top_line`, the line that said so, only where it says `"cycles"`; `feet`, the same
record read over its feet (with `over`, the row it was read over, `reach` or `sole`), its `says` and
`top_line` the same as the screen's; `why` absent where it says `"cycles"`; `says` is
`"cycles"` or null; no key tells a right hold from a false one); `unwitnessed` true only where nothing
witnessed; absent where not asked. `cue` and
`bob` are recorded only where the picture passes. The same reading is used for the search's cut
(`--anchor motion-auto`), a fixed cut
(`--cycle fixed`, `fixed_count`), and the loop as it plays round (`ring_steps`, told the clip's `cycles`
since a loop alone is too short to read two intervals on). `video-set` passes each item's view. On the
synthetic walkers of `tests/video/test_walk_steps.py` (bodies rising and falling 3 px either way once a
step on legs whose soles stay on the ground unless held still or drawn carried whole; the walkers that
are cut one cycle swing their arms within the body's sides: each arm 8 px either way of the body's middle
(`ARMS_IN` in the tests), the body's sides 28 px apart, so every band of the upper body a zoom would move
holds still on the beat; the other walkers of the passage swing them 16 px either way, past the body's
sides), a walker seen from behind whose far foot hides behind the near one (feet together over half the
interval) has a cycle of 20 frames drawn on twos, 24 on threes, and 17.5 and 18.5 frames on ones: its top
line rises and falls twice an interval; cut one cycle (within a frame of it), `cycles` true, `steps` 2;
with no view no cue is read. The same walker with its far leg drawn as the near one (legs alike), with no
view and with `--view back_diagonal`, is still cut one cycle (the view's cue says `"alike"`, which no
longer refuses, and the bob witnesses). A front-diagonal walker whose near foot steps out toward the
camera and passes the far one apart (feet together under half the interval), cycle 16 and 18 frames: with
`--view front_diagonal` cut one cycle (16, 18 frames); its foot cue moves over the floor and does not
turn over one interval on (`says` null), and its top line rises and falls twice an interval; without a
view it is not asked and is cut as before (32, 36 frames: two cycles). The walker seen from behind (17.5
on ones; no view and `--view back_diagonal`) and the front-diagonal walker (16, `--view front_diagonal`)
with their bodies held still have their top line not rising and falling (the line left (less its flow)
ranges under `BOB_FLOOR` of the body's height and under `BOB_ROWS` rows): two intervals (two cycles),
`cycles` false, `unwitnessed` true, and the warning naming the cut. The walker seen from behind whose far
foot hides (17.5 on ones, no view) and the front-diagonal walker whose near foot steps out (16,
`--view front_diagonal`), rising and falling 3 px on their legs once a step, their arms swinging 16 px
either way of the body's middle, past its sides (the upper body widens and narrows twice an interval across
the bands the arms swing over), are cut one cycle: the bob says `"cycles"`, its `body.scale` is under
`0.6`, its largest band `reads` is over `0.4`, and its `body.depth.ratio` is at least `BOB_RIGID` and its
`body.depth.moved` at least `STRIP_FLOOR`. The walkers the tests witness, or whose cut the cut's witness
reads one step, drawn instead with their arms swinging 16 px either way of the body's middle, past its
sides (nineteen forms, `PAST_ITS_SIDES`), read as the walkers with their arms within their sides do: none
is a silent half cycle. A walk whose feet meet once a cycle (the walker whose far foot hides, 18.5 frames on
ones, 20 on twos and 24 on threes; the same with its legs drawn alike, 17.5 and 20, with no view and with
`--view back_diagonal`; the front-diagonal walker whose near foot steps out, 18, `--view front_diagonal`;
the walker whose far foot hides, legs alike, carried along the arc and the cubic; the same walker turned 15
degrees about its feet over the clip) is cut one cycle. The wide walker with its far leg four tones off
(`--view side` and `--view front_diagonal`) stands at 46 frames, witnessed, nothing named. Held as chosen,
named (`cut_witness.held`, the warning): 23 frames on the word `one step` for the sliding foot, 30 frames
on the word `one step` for the limp, 23 with no word for the walker held still over a shadow on a picture
shaken twice a step, and a cycle with no word for the walks on a picture swaying once a cycle or slower. A
side walker whose two steps are one silhouette (46-frame cycle, near and far a tone or four apart), passes
brief, is not asked with no view or with `--view side`, and is cut two steps (46 frames) as before,
witnessed, nothing named. The same 46-frame walker filmed as a front diagonal, its far leg four tones off
the near one, is asked with `--view front_diagonal`: the picture passes, but the foot cue turns over one
interval on (`says: "steps"`): two steps, 46 frames, witnessed, nothing named. The
same 46-frame walker with its legs drawn alike (arms still four tones apart), asked with `--view` set to
`front`, `front_diagonal`, `back` and `back_diagonal`: the picture passes, the cue says `"alike"`, and
its top line rises and falls once an interval (a step), so its `half` correlation is negative: nothing
witnesses, two steps, 46 frames, `unwitnessed`, and the warning. The same with its legs drawn alike and
each leg twice as wide (10 px either side of its line instead of 5), so the feet are seen together over
half the interval at each pass and a pass could hide: asked with no view and with `--view` set to `side`,
`front_diagonal` and `back`: two steps, 46 frames, `unwitnessed`, warning; with a view the cue says
`"alike"`, with no view no cue is read. The wide alike walker lit by a light fixed on the screen, lighter
to the right (up to 12 tones across the feet), asked with no view and with `--view` set to `side`,
`front_diagonal` and `back_diagonal`: the shade of the feet moves with how far apart they are drawn and
turns over half an interval on; nothing witnesses: two steps, 46 frames, warning. The alike walker (legs
5 px) with the leg swinging forward lifted 6 px at its pass and a light fixed on the screen varying top
to bottom (12 tones), asked with `--view` set to `front`, `front_diagonal`, `back` and `back_diagonal`:
the cue moves with the swing whichever leg swings and turns over half an interval on; nothing witnesses:
46 frames, warning. The wide walker with its far leg four tones off the near one (a pass can hide): with
`--view side` or `front_diagonal` the cue turns over one interval on (`"steps"`): two steps, 46 frames,
no warning; with no view no cue is read and nothing witnesses: 46 frames, `unwitnessed`, warning. The
wide legs-alike side walker (46-frame cycle, crossings every 23 frames, a pass can hide), carried up and
down with the whole picture along a smooth arc 24 px deep, and, with its body held still, along the same
arc, along a sway 12 px either way, four intervals long (92 frames), and down a ramp 24 px over the clip,
a row every three frames: each is cut two steps, 46 frames, with `unwitnessed` and the warning. For the
bobbing walker its `half` is `-0.28` or less (less its flow its line rises and falls once an interval);
for the still one its `rows` is under `BOB_ROWS`. Before this change every one of the four was cut one
step, counted two, and no warning said so. The walker seen from behind whose far foot hides behind the
near one (cycle 17.5 frames on ones, legs drawn alike), carried along the arc and the cubic: cut one
cycle; the bob says `"cycles"` with its `quarter` at `-0.28` or less. The walker seen from behind whose
far foot hides behind the near one (a cycle of 20 frames drawn on twos, 24 on threes, and 17.5 and 18.5
frames on ones) also has `body.share` at least `0.3`, `body.scale` at least `0.6`, `edge` 0, every band
seen holds still on the beat (`body.still_bands` equals the number of bands seen), and its `why` names
the body's own. The walker seen from behind whose far foot hides (cycle 17.5 frames on ones, no view) and
the front-diagonal walker whose near foot steps out (cycle 16, `--view front_diagonal`), their bodies
rising and falling twice an interval carried whole, feet and all: cut two intervals, `unwitnessed`, the
warning; the bob's `half` is `0.28` or more and its `quarter` `-0.28` or less (the top line alone rises
and falls twice an interval), its `body.share` is under `0.3`, and its `why` says its lowest row rises
and falls with it. The wide legs-alike side walker (46-frame cycle, its crossings its steps every 23
frames, a pass can hide), held still or rising and falling on its legs once a step, on a picture shaken
up and down 3 px twice an interval (dy = round(3 sin(4 pi k / 23))): two steps, 46 frames, `unwitnessed`,
the warning; held still, its bob's `half` is `0.28` or more and `quarter` `-0.28` or less, and its `why`
says its lowest row rises and falls with it. The walker seen from behind (17.5, rising and falling twice
an interval on its legs), its frames cut at the soles' row so the lowest row is each frame's last: two
intervals, `unwitnessed`, the warning, `edge` over 0 and a `why` naming the frame's last row. Read off
drawn frames with `legs.strike_signals` and `legs.body_signals`, five walkers that do not rise and fall
twice an interval on their legs (the wide legs-alike side walker, interval 23, held still, carried whole
bobbing once a step, or rising and falling on its legs once a step; the walker seen from behind, legs
alike, interval 17.5, held still or carried whole rising and falling twice an interval), each moved up
and down with the whole picture 3, 6 or 12 px either way every d/4, d/2, d or 2d frames (d its interval)
at four phases: none is witnessed. Three of them (the side walker held still and on its legs, the walker
from behind held still) zoomed in and out 0.5, 1, 2 or 4 % either way every d/4, d/2 or d frames about
the feet, the middle or the head, at two phases: none is witnessed. The walker with its head drawn flat,
round, pointed or with ears (held still or rising and falling on its legs once a step), zoomed in and out
4 % either way with the picture about its feet, middle or head every quarter, half or whole cycle (11.5,
23 or 46 frames): none is witnessed two steps an interval of a step (23 frames), nor a cut of a step said
to be a cycle or two, nor a cut of two cycles (92) said to be one. The wide walker held still on its
legs, zoomed in and out 4 % either way with the picture about its feet twice a step (every 11.5 frames),
its head drawn narrower as the picture zooms in (so the bands across its head hold still on the screen)
and the body under it swinging on the beat: either its torso, whose half width swings 4 px either way a
quarter of the beat off the zoom, in step with it, or against it; or a crown 13 rows deep drawn narrower
as the picture zooms in, over a cape widening and narrowing 4 px either way a quarter of the beat before
the zoom. Through the loop nothing witnesses it (`unwitnessed`, the warning), and it is never cut 23
frames (half a cycle) as two steps unnamed; read off its frames at an interval of 23 frames, its top line
rises and falls twice an interval (`half` `0.28` or more, `quarter` `-0.28` or less), at least one band
holds still on the beat, and nothing is witnessed, the `why` naming the upper body's width. Read off
drawn frames (128 frames) at an interval of 16 frames: a column 20 px wide on feet planted at row 140,
its top rising and falling 3 rows either way twice an interval, its crown (11 rows) and the column under
its shoulders holding still, its shoulders (the 41 rows under the crown) widening and narrowing 2 px
either way a quarter of the beat on: five bands are seen, one holds still on the beat and four move on
it; nothing is witnessed, the `why` naming the upper body's width. The side walker rising and falling on
its legs once a step (its crossings its steps), running: both soles lifted up to 1, 2, 3 or 6 px off the
ground while the body is high; still or on a picture moved 6 px either way every d/2 (two phases) or
every d frames: never witnessed. The side walker drawn as a view from behind or a diagonal draws feet,
each sole up to 6, 12 or 24 px lower the further back its foot is, the foot moving forward lifted 8 px
(the lowest row is the planted foot, sliding down the screen through its step); still, or on a picture
moved 3, 6 (four phases) or 12 px either way every d/2 frames, or 6 px every d: never witnessed.

**The order the feet are drawn in.** `legs.feet_order(frame)` reads the order a frame draws its two feet in.
`ground` records the first and last column of each run of solid pixels within `legs.CONTACT` (0.005 of the body's
height, at least one row) of the frame's lowest row, and `height` records the body height. The feet are seen
`apart` when the foot band (the lowest `legs.FOOT_BAND`, 0.08, of the height) parts at its widest empty run, and
that run spans at least `legs.FEET_APART` (0.02) of the height. Where they are apart, `depth` measures how many
rows lower the right foot's lowest solid row is drawn than the left's, `left` and `right` record each foot's
contact columns within `CONTACT` of its own lowest row, and `sway` records how far right of the middle of that
empty run the mean column of the upper half of the body lies. From the front, the back, or a diagonal, the foot
nearer the viewer is drawn lower, and the nearer foot changes sides once a step, twice a cycle. Counted over a
crossing interval or a cut, those changes say how many steps it holds. The view's label is not taken for it,
because a generated walk's label may be wrong: the clip must show that its two sides of feet are its two legs, or
nothing is witnessed. Before its gates, `local_cycle.order_word(order, span)` leaves out
(`local_cycle._off_ground(order)`) every frame whose feet are read apart but either of whose two sides (its `left` or
`right` contact columns) shares or touches no column of anything standing on the lowest row (`ground`, the runs on
the lowest rows) in any frame of the clip, treating those frames as not seen apart. A foot is drawn over the ground
the walk stands on; a tail whose tip dips into the foot band beside the feet parts the band at its widest gap from
both feet and, read as a foot, puts the foot drawn lower on the side it is not and back again, changes of side that
no foot makes and that could count half a cycle as a whole where previously only subsequent gates held it by chance.
A foot lifted over columns where nothing ever stands is left out with those frames; the order then reads fewer
frames, never more.
`local_cycle.order_word(order, span)` tests the frames across a span through a sequence of
gates in order: the feet must be seen apart in at least `legs.FEET_APART_SHARE` (20 %) of the frames; the depth's
swing (10th to 90th percentile) must reach at least `ORDER_ROWS` (2) rows and `legs.REACH_MIN` (0.015) of the
height, or the feet are drawn level; each frame's lower side is read where one foot is at least an eighth of the
swing (at least one row) lower; no change of side may have the lower foot's contact before it and the other
side's contact after it share or touch a column, which marks one foot sliding under the body from one side to the
other as seen from the side; nothing standing on the lowest row may slide along it `ORDER_TRAVEL` (0.8) of the
feet's median distance apart or more (`_travel` follows each run frame to frame while it touches exactly one run
of the next frame, starting afresh where runs meet or part); and the upper body's sway (10th to 90th percentile)
must stay under `ORDER_SWAY` (0.5) of what a tilt about the feet would need to draw one foot that much lower (the
swing times `ORDER_REACH`, 0.75, of the height, over the feet's distance apart), because a picture rocking on its
feet draws one foot lower and then the other. Then at least three changes are required, and their rate over the
span (the span over the mean distance between changes) must fall within `ORDER_SLACK` (0.2) of two, four, or one,
with no two changes more than twice their mean apart (`ORDER_WORDS`), giving `"two steps"`, `"four steps"`, or
`"one step"`, or null with `why`. The record keeps `apart` (the share of frames seen apart after leaving those frames
out), `swing`, `floor`, `changes`, `slides`, `spacing`, `travel`, `sway`, `tilt`, `per`, `says`, `why`, and
`off_ground` (the list of frames left out, present only where any frame is left out).
It is not read from the side (`--view side`,
`local_cycle.SIDE_VIEW`), where the two sides of the feet are the front foot and the back foot rather than two
legs. In `local_cycle.read_cycles(..., order=None)`, `video-loop` passes `order=[legs.feet_order(f) for f in
frames]`. After the foot cue's refusal and the body's word, where the body does not witness two steps an interval
and the order says `"two steps"` over the crossing interval, the crossings mark cycles: `cycles` is true, and
`why` records that the foot drawn nearer changes sides `<per>` times an interval, the clip's two sides of feet
its two legs (none slides from one to the other, the upper body sways `<sway>` px of the `<tilt>` a tilt would
need): two steps an interval, the feet meet once a cycle. The record is kept as `cycle.step_count.cycles.order`.
Where nothing witnesses, `why` adds the order's own reason ("the order of the feet is not read from the side"
from the side). A diagonal walk whose feet pass each other on the screen, one foot always drawn lower and the
other passing it on either side, shows the same changes a side walk does (a planted foot sliding under the body
past the other), and the order does not witness it: its crossings stay on steps or its cut is held, named. A side
walk is never read on the order, and reading which foot passes in front of which on the screen is not done here.
As a named limit, a tail hanging over the columns the feet stand on is still read as a foot; the gates after the
reading (the sliding check, the ground travelled, the upper body's sway) are what hold it.

**Choosing the cut.**

- The clip does not show its steps (no feet seen, a legless body, a lean that does not turn over, under
  three crossings): the search's cut is kept as the search cut it, and then held to the body's word like
  every other cut (see "The cut's witness" below); `cycle.step_count` and `cycle.cut_witness` are recorded.
- The search's cut is a two within **1.5** frames of phase (`crossings.PHASE_TOLERANCE`): kept, every
  image and frame as the search alone cuts it.
- Otherwise the cut is among the cuts that cover two steps — any start, any length the count allows,
  inside an explicit `--min-len`/`--max-len` but not the state's window, half the clip or the gait
  fallback's ceiling (a walk slower than the window is cut two steps long). Those in phase, if any, are
  read at the wrap: the flow off of the cut's last and first frames (section 2, "The flow reading", on
  the clip's own path and box, its frames moved as the cut will be corrected, read once:
  `repair.clip_flow_states`, `repair.ring_scores`), the top band's jump (`repair.seam_pop`, "The seam
  pop" below) and the wrap over the cut's step (`ratio`, what the seam gate holds a loop to). The cuts in
  phase whose top does not pop, whose `ratio` is at most the seam gate's **2.0** (`--seam-max`) and whose
  two seam frames are both under **3.0** (`repair.FLOW_LIMIT`), the worse within **0.5** of the smoothest
  such cut's (`local_cycle.SEAM_FLOW_BAND`), are taken nearest the clip's own way on at the wrap
  (`closure`, as for a counted cut); where none passes, in phase first, then a calm top, then the flow
  limit, then the seam gate, then the smoothest seam (`method: observed-two-steps-v1`, `cycle.candidates`
  ranked, `review_recommended` when none passed). Of the first four (`local_cycle.SEAM_TRIES`) the first
  whose seam the gate will pass is taken, read as the gate reads it before anything is cut
  (`loop.seam_on_cells`: the cut corrected as it will be, its wrap over its step on the analysis
  thumbnail; `step_count.seam_gate.tried`): the correction of one cut can leave a jump at its wrap that
  no reading of the clip shows. For the cuts of two steps that foot crossings choose among, a cut whose
  seam passes the seam gate is now read for its pop too: of the first four cuts, the first that both
  gates pass is taken, the search's own cut stands only where both pass it, and
  `step_count.seam_gate.tried` rows and `step_count.search` gain `pop`, `pop_gate`, and, where
  unreadable, `pop_skipped` ("The seam pop gate" above). Each reading is a whole correction, six fine searches of the motion
  anchor, and the cut taken is corrected once more, so a gate that tries all four corrects five cuts;
  the search reads a row of its shifts at once, to the values one patch at a time gives ([loop
  review](loop-review.md#explicit-xy-motion-correction)). A part on a beat of its own — a tail, a staff — that closes on no cut of
  two steps leaves every one with a wrap that jumps: the first is cut, still two steps; where the gate
  would refuse its wrap, the frames beside it are made again from the clip's frames a cycle apart ([the
  overlapped wrap](#the-overlapped-wrap--a-part-on-its-own-beat-that-no-cut-of-two-steps-closes),
  section 2), and the seam gate says whether it closes, as for any cut.
  The pixel search's repeat rule is not asked: a part off the legs' beat — a tail on its own beat —
  repeats on its own, and its seam is the flow repair's (section 2). `step_count.repeat` records the
  search's word on the cut, and a cut it would refuse, or one that passed no gate, is a warning line.
- A search's cut the count does not refute — a two out of phase, or uncertain — and whose own seam the
  gate passes (`step_count.search.seam`) gives way only to a two-step cut that closes: one of the cuts that
  passed the gates above (in phase, its top calm, its wrap within the seam gate of its step, its seam under
  the flow limit) whose seam the gate passes. Where none does, the search's cut is kept, every image as the
  search alone cuts it (`steps` written where it is a two), and `step_count.declined` names the best
  two-step cut, its phase, whether it passed the gates and its seam, with a warning line. A cut that plays
  a step a clip shows unevenly, or a part on its own beat that closes on no two steps, would otherwise
  trade a loop that passes for one the seam gate refuses. A search's cut the gate refuses as well gives way
  as before — the two-step cut's wrap may yet be closed by the overlapped wrap (section 2) — and so does
  one the count refutes, one step or three, each seen; the seam gate says whether the loop passes.
- The clip shows its steps but no cut of two: the search's cut, unless the count refutes it — then the
  search runs again refusing what the crossings refute, and a search that finds nothing lists each
  refused window's count (`cycle.candidates[].step_count`), those the crossings refute last.

A cut taken on two steps records `cycle.steps` (`{"count": 2, "by": "crossings"}`) and `strip.json`
says `steps: 2`: `video-cycle-align` reads its return half way as the step it is (section 4). The step
screen below then has its word only where the count does not: its pixel suspicion is kept as
`pixel_suspect`, `suspect` false. A count given (`--steps`) is the word on the search's cut, and the
crossings are not asked.

**The cut's witness.** In `video-loop --anchor motion-auto` (a walk or run whose cycle is searched: `--cycle auto`
or `periodic`), right after the step count (`loop._by_steps`) has chosen the cut and before any frame is cut,
`loop._witnessed(cycle, frames=...)` holds the chosen cut s/L to the body's word, whichever way it was chosen:
the pixel search's cut, two steps by the clip's crossings, crossings read or not read; it does not run for a count
given (`--steps`), a fixed cut, `--anchor none`, or any state that is not a walk or a run (motion-auto refuses
those). This witness is needed because legs drawn alike loop as well on one step as on two; crossings read off
feet that slide down the screen through their step (as a view from behind or a diagonal draws a planted foot),
or off a shadow under the feet on a picture that moves, count half steps or quarter steps; and a shadow under the
feet can leave the step count no feet to read, handing out the search's one-step cut with nothing named.
`local_cycle.cut_word(signals, length)` reads the body's word, `local_cycle.body_word`, one line in both
places of `local_cycle.CUT_LINES` = `{'screen': 'on the screen', 'feet': 'over its feet'}`: the line on the
screen, less its flow (`bob_word(signals, L)`), and the line over its feet (`bob_word(..., feet=True)`,
`local_cycle.over_feet`, taking the line less the lowest solid row, `reach`, or less the lowest opaque row,
`sole`, whichever leaves less of its rise and fall twice an interval; there each `why` names it "{name} over
its lowest {solid|opaque} row"). On the screen alone, a head rising twice in one step
reads as two steps, and the feet leave no such rise and fall over them; over the feet alone, the two feet's
heights, taken in by turns, read as the body's, and the screen line has none of them. The price is the
design's: a walk whose rise and fall shows in one place only is not witnessed. A picture swaying up and down
once a cut or slower, which the flow taken out at the cut's length leaves whole, hides the rise and fall on
the screen while the line over the feet reads it, and such a walk's cut is held, named; a walk leaning
further on its feet as it goes can do the same over its feet on a cut a frame longer than the one it is cut.
The body's word is read (`local_cycle._cut_lines`) on the cut L, on half of it (L/2) and on twice it (2L),
and the cut's word is the first it rises and falls twice on, the same line in both places: `"two steps"` on
L (the cut is a cycle); `"four steps"` on L/2 (two cycles); `"one step"` on 2L (once in the cut); null where
none. It returns `{says, by, lines, on, why, mark}`: `by`, `["screen", "feet"]` where the body said the
word and `["order"]` where the order did, never one place, only where it says one; `lines`, each place's
record, `{screen: {...}, feet: {...}}`, each `{says, bob, on, why}` (`says` in both places the cut's word,
`bob` that place's reading on the cut, `on` its reading at L/2 or 2L that said four steps or one step, with
its `interval`, only for four steps or one step, `why` only where `says` is null; the record over the feet's
`bob` and `on` record `over`, the row it was read over, `reach` or `sole`); `on`, for four steps or one step,
the screen's reading that said it, with its `line` (`"screen"`), `interval` and `top_line`; `why`, only
where nothing says a word: each place's why, repeated ones once, joined by "; "; `mark`, only where marked.
Where the cut stands (two steps or four steps) and its lowest row (the one the line over the feet is read
over: `sole` where the record over the feet was read over `sole`, else the lowest solid row) rises and falls
twice the cut more than the line that stood (`top_line`) does on the screen (`local_cycle.own_beat`'s `sole`
or `lowest` greater than its `beat`, both rounded to two places), the record carries `mark: {over, feet,
beat}` (`local_cycle._feet_mark`), in rows up and down to two places. The line over the feet is then the
feet's as much as the body's: a foot hidden behind the other shakes it so once a cycle, and a cut of two
cycles could be stood on it as one. A mark holds nothing: the cut stands as its word says, and nothing is
warned or left out for it. Two steps or four steps: the cut stands, and `cycle.cut_witness` records
`{cut: [s, L], ...cut_word's record}`.
`local_cycle.cut_word(signals, length, order=None, declined=None)` reads the order on the cut: `loop._witnessed`
passes it the order (none from the side, none where the frames give no top line) and the step count's `declined`
record. Where the body says neither two steps nor four steps, the order is read on the cut: if it says two
steps or
four steps, the cut stands, `by` records `["order"]`, and the record is placed under `order`. Otherwise the
lines' word stands, the order's record is kept under `order`, and its reason is joined to the cut's why. A cut of
four steps read on the order is two cycles exactly as one read by the body: `local_cycle.four_steps` is true,
`video-set` leaves it out (`cycle-four-steps`), and the `video-set` warning reads "its body rises and falls, or
its foot drawn nearer changes sides, four times in its cut (cycle-four-steps)". Because the order counts the
changes in a cut rather than whether the cut's length closes them, a cut a sixth short of a cycle still reads two
steps. Where the cut is the search's, kept because none of the clip's two-step cuts closes (`declined`, the step
count's record of the best of them), the order does not stand a length the closing refused: `order.says` is null,
`order.read` keeps the word it read, its why says the order "counts its steps, not whether its length closes, and
does not stand a length the closing refused", and the cut is held (`cut_witness.held` "unwitnessed",
`cycle-unwitnessed`). The body's word on such a cut is unchanged.
A cut of four steps (two cycles) still has its strip counting two steps (`steps: 2`), which `video-cycle-align`
reads as one cycle; so `local_cycle.four_steps(cycle)` is true and `video-set` leaves the item out of its state's
cycle alignment (`left_out` `["cycle-four-steps"]`, `local_cycle.FOUR_STEPS`), its loop as cut.
`video-cycle-align` run on its own reads the cut report beside each loop through `align.held_cut(loop_dir,
meta_path, meta)`. Where nothing witnessed the cut whole cycles (`cycle-unwitnessed`) or the cut spans four steps
(`cycle-four-steps`), the loop is named under `held` and the aligner stops under `--multi-cycle fail` before
rewriting any files, adding "and nothing witnessed some of the cuts whole cycles". Under `--multi-cycle warn` it
aligns the loop with a warning, matching `video-set` instead of aligning held loops as one cycle without a word.
Anything else: the cut is held: kept exactly as it was chosen (two of the clip's crossing intervals where the
step count counted them, else the search's cut), `cut_witness.held` is `"unwitnessed"`, a warning line names
`cycle-unwitnessed` (`local_cycle.held_verdict(cycle)`, said through `local_cycle.step_verdict`), and
`local_cycle.unwitnessed(cycle)` is true, so `video-set` leaves the item out of its state's cycle alignment
(`left_out` `["cycle-unwitnessed"]`); its warning says nothing witnesses its cut whole cycles, so it may hold
half a cycle or two, and, where its step screen suspects the cut is one step, adds what settles that
(`video-loop --steps 1`); a cut the step count had already held (crossings left on steps,
`step_count.cycles.unwitnessed`) is recorded `{cut, held: "step count"}` and named as before.
A cut two lengths long comes only from the picture read before this step (`loop._two_lengths`,
docs/video-pipeline.md), and the cut read here is the one that step handed on, so `"one step"` is held,
never cut again two of its lengths long: a body that rises and falls once in the cut may be one step of a
walk whose legs are drawn alike (half its cycle), or a whole cycle of a body that rises and falls once a
cycle: a limp, or a legless body bouncing as it goes (the walk window already admits a legless body's fast
bounce); nothing that reads no leg tells the two apart, and cutting again two long would loop two cycles of
the second.
The warning of a cut held on one step names the line that rose and fell once ("its top line" or "its first
solid row"), "on the screen and over its feet"; the word being one line in both places, there is no other
line to say nothing of. `local_cycle.held_verdict` refuses by name, with a
`ValueError`, a cut held on one step whose record names no line it was read on (no `by` or `lines`), and
`step_verdict` with it; no engine path hands it one, its one caller saying the record the same run has just
written. The loop report's `cycle.cut_witness` records `{cut, says, by, lines, on, why, held, mark}` (`by`
only where it says a word, `on` only for four steps or one step, `why` only where `says` is null, `held`
only where held, `mark` only where marked); the printed summary's `cycle.cut_witness` records
`{cut, says, by, held, mark}` (each where present). `by` is `["screen", "feet"]` or `["order"]`, never one
place; `lines.screen.says` and `lines.feet.says` are the cut's word; `lines.*.bob`, `lines.*.on` and `on`
carry `top_line`, the line that stood. The record over the feet's `bob` and `on` record `over`, the row it
was read over (`reach` or `sole`). No key in the record tells a right hold from a false one.

**What crossings cannot see**: a hold the step range absorbs (a 13-frame step held to 17), a still run of
the feet together inside the clip as long as a pass, and a wobble that reaches the reading's sides at a
step's beat read as steps; a narrow step that opens the feet only just past the reading's threshold reads
at that threshold. Where a pass is hidden behind the other foot, or where the view is given and is not
`side`, the cycles reading (`local_cycle.read_cycles`) sees whether the crossings mark cycles: where the
clip repeats one interval on as near as two on, the crossings mark cycles only where the body's top line
rises and falls twice an interval of the body's own (against its lowest row and upper body) unless the
view's foot cue says `"steps"`; where they mark cycles, the reading cuts one cycle as two steps. What it
still cannot tell: a side walk with brief passes whose two steps are one silhouette is not asked, because
from the side the legs a step on are one silhouette, as near as a cycle on, and the picture cannot tell; a
clip with no view and brief passes is not asked, so a front diagonal whose near foot steps out apart is then
cut two cycles, so give `--view`; a walk whose feet do meet once a cycle but no line of whose body, its top
line or its first solid row, rises and falls twice an interval of the body's own on the screen and over its
feet (a body held still, a figure carried up and down whole, a picture moved with the body so that less than
`BOB_OWN` of the rise is left against its lowest row, a line on a beat of its own over a lowest row that
does the rising and falling (`beat` under `BOB_OWN` times `less_lowest`), a picture zoomed with the body so
that the rows down the middle of the body fit a zoom as well as they fit standing (`ratio` under
`BOB_RIGID`), or zoomed with it so that, where those rows do not tell, taking out, band by band, as much of
the zoom as each band's width reads, whatever its turn, leaves too little of the rise (`less_bands` under
the largest of `BOB_OWN` times `beat`, the floor, and `BOB_SCALE` times `less_lowest`), a walk with no band
of the upper body that a zoom as far as the rise would move a pixel or more (`BAND_FLOOR`), as for a body
rising and falling so little, or one narrowing from a point at its top all the way down (a cone), a walk
with any such band moving on the beat where the rows down the middle of its body do not tell, as where its
arms swing past its sides and those rows are not read or a zoom as far as the rise moves them under
`STRIP_FLOOR` (one such band is enough; where they read a body rising whole, the bands hold nothing), a
line that says so in one place only, as a head rising twice in one step does on the screen and not over the
feet, or a tail that takes the top line off the head while the first solid row says so only on the screen,
a run whose feet leave the ground so far with the body that too little of the
rise is its own, feet at the frame's last row, or a top line that a part held over the head moves on a beat
of its own) has its crossings left on steps: cut two cycles long, with the warning, and `video-set` leaves
it out of the cycle alignment (`video-cycle-align` run on its own stops on it); a picture zoomed on the beat
with every band seen drawn against the zoom, so that each band holds still on the screen, and the rows down
its middle as good as plain to the zoom, is read as the body's own: its band widths are those of an upper
body rising whole on its legs (measured on one such drawing, every band within 0.78 px of such a body); and
a walk whose crossings are its steps, whose legs are drawn alike so its picture repeats a step on, and whose
line rises and falls twice a step on the screen and over its feet (a body rising and falling twice a step
on its own legs) would still be read as cycles (the witness rests on a body rising and falling once a step,
and nothing that reads no leg tells the two apart); a part over the head on a beat twice the step's says so
on the screen alone, the shape of a head rising twice in one step, and is held. What stays held and named:
a walk whose rows down its middle fit a zoom as well as they fit standing; a walk whose top line a tail
takes and whose first solid row says so only on the screen, the shape of a head rising twice in one step; a
walk whose body rises and falls once in the cut; a cut whose line over the feet does not read twice the
cut; a search's cut kept where no two-step cut closes. A walk whose body rises and falls once a cycle (a
limp) and a legless body bouncing once in its cut are held and named even where the cut is a whole cycle,
and a walk carried up and down whole, feet and all, with no rise of its own, is held.
`video-cycle-align --cycles <loop>=1` settles a held loop that is one cycle. The witness never lengthens or
shortens a cut.
Four-legged gaits are read as one foot band (`legs.strike_signals`), as before.

### The step gate: every step between drawings, and a fixed cut's count

On every walk or run loop that plays round (not a single play cut), after the seam gate passes, the strip cells are read between their drawings: a loop drawn on twos or threes shows each drawing for two or three frames (`repair.flow_drawings` finds them), so read frame by frame it steps nothing and then two or three steps at each drawing, making its largest step two or three times its mean even when playing evenly. Each drawing first cell is read on analysis features the seam gate reads (`loop._small_features`), taking the step from each drawing to the next and from the last drawing into the first across the wrap. The largest of these steps, wrap included, over the mean of the others must be at most `STEP_RATIO_MAX` (2.5) (`loop.drawing_steps`). Because a pixel of jitter can look large against a tiny mean step, a step over 2.5 is exempt from the bound when it is a pixel's jitter (`loop.step_jitter`): the move that aligns its drawing with the previous drawing while producing the least coverage difference (`stop.register`, read on alpha so colour has no effect) is at most `STEP_JITTER_PX` (1.5) pixels of the frames as filmed. Each such pixel is the strip's `scale` in cell pixels, and the allowed move is never less than one cell pixel. With that move taken back, the step must also be within 2.5 of the mean. The gate holds the largest step that is not a pixel's jitter to the bound. On synthetic frames, a walk going away from the camera has a step where the feet rise a whole crop row that measures 2.5 to 3.6 times the mean as drawn, black or grey. Its move is one pixel, the step taken back is at most 1.3 times the mean, and the loop passes. A frame of a walk cut 400 px tall moved 2 px and back moves 6 cell pixels, or 2.0 pixels of the frames as filmed, and fails in every colour. Measured instead as a mean RGBA difference, that jump reads 0.057 in colour, 0.025 in a grey near the backdrop and 0.021 in black. Past the bound, the loop fails with `video-loop: loop step ratio <ratio> exceeds 2.5 (<kind> length <L> frames from <start>, into frame <frame>, read between its <n> drawings); the loop jumps inside — regenerate the clip`. Where jump repair says why the step jumps, the line says it before ` — regenerate the clip`: `: frame <frame> lies off the walk's flow, and the jump repair's frame there was refused (<faults>), so it plays as filmed`, or `: frame <frame> lies off the walk's flow, and no group of up to 2 drawings brings it back`. The report records `step_gate` on every walk or run loop, passed or failed: `read` (`drawings`), `drawings`, `hold`, `mean_step`, `largest_ratio`, `largest_from` and `largest_into` (the cut frames from and into which the largest step that is not a pixel's jitter runs, each the first frame of its drawing), `wrap_ratio`, `max` (the bound used), `jitter` for steps over 2.5 that are a pixel's jitter (each step's `from` and `into` frames, its `ratio`, its `move` in cell pixels and `ratio_moved_back`, with the larger first), `lifted`, and `kept` when a lifted gate kept a loop over 2.5; a loop with fewer than three drawings records `skipped` instead.

`--cycle fixed --start S --length L` of a walk or run now reads clip foot crossings (`crossings.read`) and judges the cut (`Reading.judge`), as `--anchor motion-auto` already does for the cut it takes. The report records this as `cycle.step_count` (the reading record, `chosen_by: caller`, and `cut` with `verdict`, `code`, `k`, `phase`, `ring`). A walk loops on two steps; a cut whose crossings count an odd number of steps (one, three) swaps legs at the wrap however close frames look, failing with `video-loop: loop step count refutes the fixed cut <start>/<L> — the clip's crossings count one step, each seen where a walk loops on two, and an odd count swaps the legs at the wrap; cut it again two steps long (about <2 steps> frames)`. An even count of four steps is not refused. The fixed cut's count reads the same cycles reading (`fixed_count`, with the view), so a cut one interval long where the crossings mark cycles counts two steps and is not refused. A fixed cut gets no `steps` field and leaves its strip unchanged.

The step gate stands where the seam gate does. Its bound is the larger of 2.5 and `--seam-max`. Setting `--seam-max` past 2.5 lifts it with the seam: every step bound is then `--seam-max`, a loop whose largest step is over 2.5 is kept with the warning line `video-loop: warning: the loop's largest step between drawings is <ratio>x their mean (into frame <frame>), over 2.5: it jumps inside — kept, as --seam-max <seam-max> lifts the step gate` (and `step_gate.kept`, `step_gate.lifted: true`), and a fixed cut counted an odd number of steps is kept with `video-loop: warning: the clip's crossings count the fixed cut <start>/<L> one step, each seen: a walk loops on two steps, and an odd count swaps the legs at the wrap — kept, as --seam-max <seam-max> lifts the step gate` (and `cycle.step_count.lifted`, which records `seam_max`). A forced delivery with `--seam-max 1000` therefore passes as before, with these lines.

The seam gate failure line keeps its first words up to `the cycle does not close — `. Where warning lines name a part on a beat of its own (an `--anchor motion-auto` cut two steps long by clip crossings whose wrap the gate refuses while asking for an overlapped wrap, or a step count warning that no two step cut joins the walk within its phase), the rest of the line says so instead of `regenerate with a complete return to the starting pose`, for example: `video-loop: loop seam ratio <ratio> exceeds 2.0 (periodic length <L> frames from <start>); the cycle does not close — the cut covers two steps by the clip's crossings, and a part on a beat of its own (a tail, a held prop) closes on no cut of two steps: the overlapped wrap was not made (turned off (--overlap-wrap off)) — regenerate with that part moving on the walk's beat`. The middle part names what became of the overlapped wrap: not made (and why, as `wrap_overlap.skipped` says), RIFE not installed, refused (each run reasons, as warning lines give them), or made and still not closing. Elsewhere the line is as before. Every refusal by one of these three readings records `seam_refusal` in the report: `{"reading": "wrap", "ratio", "max", "reason": "own-beat" or null, "overlap_wrap": "made" | "skipped" | "rife not installed" | "refused" | null}`, `{"reading": "steps", "ratio", "max", "into", "reason": "repair-refused" | "unfixed" | null, "frames", "refused"}` (the last two where a reason was found), and `{"reading": "count", "cut", "verdict", "k", "why", "two_steps"}`.

### The step screen — a slow walk cut one step long

The walk window is 0.5 to 1.6 s, and at most half the clip: a cycle has to be seen repeating. A
slow walk can take longer than that a cycle, and then its cycle is outside the window. Where the legs are drawn alike, one step on they are back in place with near and far
swapped, so the search finds the step as the repeat and cuts it. The loop walks on one leg: its arms
— and anything they hold — jump at the wrap, a set aligned with it plays one step where the other
directions play two, and nothing said so: the two-cycle screen looks only below the cut ("The
fundamental period", [video pipeline](video-pipeline.md) section 4), and the gait fallback's longer
window runs only when the first search finds nothing.

Where the clip shows its steps the count above settles it, and a walk slower than the window is cut two
steps long. Where it does not (the feet hidden, a lean that does not turn over),
`--anchor motion-auto` screens the cut it takes the other way, over the whole clip rather than the
window (`cycle.step_screen`, `period.step_screen`):

| Field | What |
|---|---|
| `lag` | within 3 frames of twice the cut (`searched`), the lag whose mean distance over the clip is least (at least 4 frame pairs) |
| `pairs`, `ratio` | over every frame with both lags in the clip, the distance `lag` on over the distance one cut on |
| `depth` | how far `lag` dips under the clip's lag profile from half the cut up |
| `coverage` | (n − `lag`) / `lag`: how much of a second cycle the clip holds past one of that length |
| `leg_peaks` | the step signal's peaks over the cut (`legs.step_signal`, read as a ring) — a record only, never decided by |
| `suspect`, `why` | a repeat at `lag` (`depth` at least 0.15, `periodicity_min`) that is better than at the cut (`ratio` at most **0.85**, `period.STEP_RATIO_MAX`) |

A true cycle repeats about as well a cycle on as at its own length (its drift makes the longer lag a
little worse: `ratio` 1.0 and up); a step repeats better two cut lengths on, where the arms come
back too. **Nothing is cut on it.** Pixels cannot tell a step from a cycle on a frame count that is
not whole — a cycle of 23.5 frames cut at 24 repeats better at 47, being whole there — so the screen
only names the cut, and the count comes from whoever looks at the loop (how often each foot lands:
by eye, or a vision call). Its images, `strip.json`, warnings and stderr are what they were without
it; the report's `cycle`, and the summary `video-loop` prints, gain `step_screen`. A twice-the-cut
lag past the clip (fewer than 4 pairs) is not read: `lag` null, not a suspect. A cut under the gait
floor is the exception: it is one step by its length, and the clip is asked whether it holds two about
the doubled cut's wrap, on the frames as filmed — not over the whole clip — and cut two lengths long
where it does ([video pipeline](video-pipeline.md) section 4, "A cut under the gait floor"). The synthetic walker
of `tests/video/test_one_step_cut.py` — a 42-frame cycle, legs drawn alike and arms shaded apart, 73
frames, its feet read as unseen — is cut at 21 (`ratio` 0.48, a suspect); the same drawing on a
25-frame cycle is cut at 25 (`ratio` 1.53), and on 23.5 frames at 24 (`ratio` 0.66: a suspect that is a
cycle), each written exactly as with no screen at all. With its feet read, the 42-frame walker is cut
two steps long, 42 frames, on the count.

**`video-loop --steps 1|2`** is the count coming back, with `--anchor motion-auto`: how many steps
the cut it takes without `--steps` holds.

- **`--steps 1`**: the cut is one step, so the walk is cut again two of its lengths long. The
  window is [2L − 1, 2L + 1] for the cut's length L — not bounded by half the clip, but held inside an
  explicit `--min-len`/`--max-len` (below) — and searched as any window (a repeat seen in the clip, the top band and the held side read again, but
  no window twice that); among the equivalent cuts the one whose wrap the clip itself plays is taken
  (`choice: "closure"`: its last frame against the clip's frame before its first, and the step into
  it, over its playback step), not the earliest. A clip too short to see a window that long repeat is
  cut all the same — the count stands for the repeat — at the window whose wrap the clip plays
  (`method: "counted-window-v1"`, `unseen` saying why the search found none). `cycle.steps` is
  `{"count": 2, "by": "given", "given": 1, "first_cut": {start, length}, "window": [lo, hi]}`, with
  `counted_window` beside `window` where an explicit window left only part of [2L − 1, 2L + 1]. A cut
  under the gait floor that the clip itself gives two steps is cut the same way, one length at a time,
  and recorded `"by": "two-lengths"` without `given`.
- **`--steps 2`**: the cut is a cycle already; it is kept, and recorded `{"count": 2, "by":
  "given", "given": 2}`.

Either way `strip.json` says `steps: 2` and the report records `cycle.coverage`, (n − L) / L. A cut
two steps long whose clip holds under **0.25** of a cycle past it (`loop.COVERAGE_MIN`) is cut and
warned about, not refused: `the cut is two steps long (42 frames, one cycle) and the clip holds 0.19
of a cycle past it (under 0.25): its repeat is barely seen — the seam says whether it closes; a
longer take shows it repeat`. Whether it closes is the seam gate's word, as for any cut. On the
synthetic walker `--steps 1` cuts 15/42, the arm's wrap 0 where the one-step cut's was 7.6 times
its largest step inside the loop; at 50 frames the counted window 8/42 (coverage 0.19) is cut and
warned about.

An explicit `--min-len`/`--max-len` window is the window as given too: the half-clip bound holds
only for the state's default window. v2.35.0 cut it back, so `--min-len 41 --max-len 43` on a
73-frame clip asked for the window [41, 36] and found no cycle. And it is the window on every path
that sets a cut's length — the first search, a window two steps long (the held side), the count
(`--steps 1`), the gait fallback, the one-shot failover — each of which reads its window off one
place (`loop.CutWindow`) and none of which sets a bound of its own; whatever chose the cut, even
`--cycle fixed` or `pinned`, a cut outside an explicit bound fails (`CutWindow.hold`, `the cut is …
frames long (…), outside --max-len N`). A count and a window that leave no length in common are two
instructions that disagree, and neither is followed: `--steps 1` on a 21-frame step with
`--max-len 25` fails with `the cut of 21 frames from 18 is one step, so the cycle is 41-43 frames
long, and --max-len 25 leaves none of that: the count and the window disagree — drop one, or give a
window that holds the cycle` (the failed report's `cycle.steps`: `counted_window`, `window: null`,
`refused`). Where they share part of it, that part is read (`--max-len 42`: [41, 42]).

- **What it does not see**: a walk whose arms are drawn alike as well — then a step is a cycle in
  every pixel and `ratio` reads 1; a cut whose twice is past the clip; and a cut on any path but
  `--anchor motion-auto` (`--steps` needs it). A set loop whose cut's report is not beside it is not
  screened at the set stage (section 4).

## 4. One cycle for a direction set — `video-cycle-align`, `video-set --align-cycles auto`

Each direction of a walk is filmed on its own and comes out its own length (one Lite set:
16, 19, 21, 25 and 27 frames). A game that turns a character mid-stride wants every direction the
same number of frames, starting on the same step.

```bash
sprite-gen video-cycle-align --loop-dir set/front-walk/loop --loop-dir set/side-walk/loop \
  --loop-dir set/back-walk/loop [--view front --view side@right --view back] [--start-foot right] \
  [--between auto|rife|nearest] [--length N] [--cycles back-walk=2] [--multi-cycle fail|warn] \
  [--foot side-walk=left] [--state walk] [--between-cache DIR] [--same-foot warn|refuse] \
  [--report set/walk.cycle-align.json]
```

- **A loop whose steps are counted is one cycle.** A cut two steps long — taken so a held part
  closes (section 3, "The held side") or cut again on a count (`video-loop --steps`, "The step
  screen") — says `steps: 2` in `strip.json`. It returns half way, at its second step, and that
  return is no second cycle: the suspect it makes is recorded with `status: "steps"` (its `steps`,
  its `candidates`) and does not stop the set, and the loop is resampled to the set's length like
  every other. A `--cycles` count given for it is still the last word: `--cycles <loop>=2` takes one
  half out of it (one step — its `steps` is dropped from `strip.json`), `=1` aligns it as it is.
- **A loop that may be one step is stopped too.** Nothing in the loop shows it — its next step is not
  in it — so the screen is the one `video-loop --anchor motion-auto` ran over the whole clip
  (section 3, "The step screen"). The alignment reads it from the cut's report beside the loop
  (`align.cut_report`: a `sprite-gen-video-loop-report`, passed, in the loop's directory or the one
  above it — a `video-set` item keeps it there — whose strip is the loop's and whose cut is the one
  `strip.json` records in `cycle_drawings`). A loop whose cut was suspected, and whose steps nobody
  counted, is a suspect the same way (`one_step`: `length`, `lag`, `seconds`, `ratio`, `depth`,
  `pairs`, `coverage`, `leg_peaks`, `why`, `report`): stopped under `--multi-cycle fail`, aligned and
  named in the warnings under `warn` (`plays one step where the rest play two, if it is one`). The
  alignment cannot settle it — the next step is not in the loop: count its steps, and if it is one,
  cut it again with `video-loop … --steps 1` (the arguments are in its report) and align the set
  anew; if it is two, `--cycles <loop>=1` aligns it as it is. A loop with no report beside it is not
  screened this way.
  In `video-set`, a walk whose cut its cut witness held is left out of the alignment
  (`cycle-unwitnessed`) before it is screened, and where its step screen suspects one step, its
  warning gives the same advice (cut it again with `video-loop --steps 1` if it is one).
  `video-cycle-align` run on its own reads the cut report beside each loop through
  `align.held_cut(loop_dir, meta_path, meta)` (using `align.cut_report` in the loop's directory or
  the directory above it, matching the strip metadata). When nothing witnessed the cut whole cycles
  (`local_cycle.unwitnessed`, reason `cycle-unwitnessed`) or a loop with two strip steps was cut two
  cycles long (`local_cycle.four_steps`, reason `cycle-four-steps`), `held` records `{reason,
  report}`. Under `--multi-cycle fail` (the default), the command stops before anything is rewritten,
  adding "and nothing witnessed some of the cuts whole cycles". A loop counted two steps whose cut is
  held receives no `steps` status and its return half way is not read as its second step; `settle`
  offers `--cycles <loop>=1` to align it as one cycle and `--cycles <loop>=2` to take one cycle out
  of two, and a held loop that may be one step is cut again with `video-loop ... --steps 1`. Under
  `--multi-cycle warn`, it is aligned and warned that "at the set's length it plays half or twice as
  fast as the rest, if it is not one cycle" where no return was found, bringing standalone alignment
  into agreement with `video-set` instead of aligning held loops as one cycle without a word.
- **Two cycles in one loop are stopped, and counted by whoever looks**: one length for the set is
  one beat only if every loop holds one cycle. A loop that holds two strides, resampled to the
  set's length, walks twice as fast as the rest, arms and legs alike — and pixels cannot tell it
  from a loop of one stride whose two steps look alike ([video pipeline](video-pipeline.md)
  section 4, "The fundamental period"). So before anything is resampled each loop is screened as a
  ring (`align.cycle_screen`, the rule `video-loop` records by, `period.verdict`): a half or a
  third of it that repeats and is no shorter than the state's gait floor makes the loop a
  **suspect** — whatever its pose, which is recorded as evidence. A run set stops nearly every
  time. The state is the one `video-loop` writes in
  `strip.json` (`state`); `--state` gives it for loops cut before.
  - A set with a suspect is **stopped before anything is rewritten** (`--multi-cycle fail`, the
    default): the message and the report name each loop, where it returns (frames and seconds),
    the evidence (`pose`, `steps`), the arguments that settle it, and the command to run once
    they are counted (`command`: the same alignment with `--cycles <loop>=<1|2>` for each, to
    fill in). With no vision call to ask, the agent looks at the loop's frames and gives the count. The report is written with
    `applied: false`, `refused: "cycle-suspects"` and `suspects` (per loop: `dir`, `name`,
    `length`, `status`, `candidates`, `settle`); `align.CycleSuspects` carries the same list.
  - **`--cycles <loop>=k`** is the count that comes back (repeatable; `<loop>` is the
    `--loop-dir`, its strip's name, its directory's name, or for a `video-set` item's `loop`
    directory the item's name). `1`: it holds one cycle, aligned as it is. `2` or `3`: one cycle is
    taken out of the loop as filmed (`cycle.source/`) — round(L/k) frames, from the start whose
    frame one cycle on is the most like it, read as a ring (`align.take_cycle`) — and that is what
    is resampled; the loop row records `cycles_given` and `cycle_taken` (`from`, `start`,
    `length`, `exact`, `repeat_over_step`, `seam_ratio`). `video-loop` is not run again, and a
    later alignment reads the same filmed frames. A count given where the screen found nothing at
    1/k is still honoured, with a warning.
  - Nothing else changes how many cycles a loop holds: without `--cycles` every loop is resampled
    from all its filmed frames.
  - `--multi-cycle warn` aligns a suspect set as it is, a warning per suspect.
  - Who counts: a person looking at the loop, or a vision call asked how often each foot lands.
    A loop counted once is not remembered — the next alignment needs the same `--cycles`.

- **Length**: the median of the set's own lengths (`--length` overrides). The median is the
  length that needs the fewest made frames across the set; a loop already that long is not
  resampled — every frame is its own, and only the ghost screen (below) may give one way.
- **Resample, offset 0**: frame k of a loop of L frames resampled to L* is the source at time
  k·L/L*, cyclic. A time within 0.03 of a source frame takes that frame as filmed; only a time
  between two frames is made, by RIFE at that fraction (section 1). An offset of half a frame,
  which would remake every frame, is not offered (section 2).
- **Between two source frames** (`--between`): `auto` (default) makes the frame with RIFE,
  measures it (below) and keeps it unless it has a fault, where the nearer source frame is taken
  instead and named (the other one beside it where the nearer is a filmed ghost, below); under `auto`
  a frame given way is kept on the loop's beat (the next bullets), so the nearer source frame is no
  longer always what is taken; `rife` keeps every made frame and names the faulty ones; `nearest` takes
  the nearer source frame every time (the other where the nearer is a filmed ghost), so nothing is made
  and no RIFE is needed, and the motion keeps the filmed frames at up to half a frame off their time
  (a loop stretched longer shows a frame twice, which the GIF and WebP hold as one frame of twice the
  delay). Under `nearest` the beat is recorded and not warned. `video-set` passes `--align-between`.
  `auto` runs RIFE for every frame between two source frames, as `rife` does, and needs it installed the
  same way.
- **The beat, read on every aligned loop**: the cycle's source frames (`cycle.source/`, as aligned)
  are read where the feet are: the lowest `LEG_PATH_BAND` = 0.10 of the box every frame's figure fits
  in (union of solid pixels, alpha 128 and up). Each 8 x 8 px block (`BLOCK`) holding a part of the
  figure there (a quarter of its pixels solid in this frame or the next) is looked for in the next
  frame within 12 px (`SEARCH`), with a small bias toward no shift so a flat block does not wander;
  the step from frame i to i + 1 is the blocks' mean shift length, in % of the box's height. A block
  that matches nowhere (mean |RGBA| difference over `MATCH_BAD` = 40 of 255) changed shape, a sole
  turning, and counts its residual (scaled to px) instead. Read at a scale where the first frame's
  figure ends at most 410 px down (`READ_HEIGHT`): a cut's frames are drawn about twice a strip's
  size, so a block and the search reach the same part of the figure as on a strip. A cape or tail
  held still over the band adds no shift, where a brightness difference would add its whole change.
  Summed from frame 0 the steps give each source frame a place M(i) on the path, and the cycle its
  length P = M(n). A frame made at fraction t between source frames i and i + 1 stands at
  M(i) + t (M(i + 1) - M(i)). A cycle of n source frames resampled to L frames asks frame k for the
  time k n / L (offset 0); standing there it would be at e_k = M(k n / L). beat = P / L. For every
  gap k to k + 1 (the last to the first): jolt_k = ((p_(k+1) - p_k) mod P - (e_(k+1) - e_k) mod P) /
  beat, in beats, where p_k is where frame k actually stands. It measures how much more (or less) the
  legs move from frame k to k + 1 than the even resample would move them. `BEAT_JOLT` = 0.25 beat: a
  jolt of 0.25 or more (absolute) is a broken beat, a skip into a frame and a halt out of it (or the
  other way). Why 0.25: an aligned frame may then move the legs into it or out of it by up to a quarter
  of a beat more than its own time would, so two even gaps beside it stay within 5 to 3 of each other.
  For example, a cut drawn on threes resampled so a frame falls half way between two drawings leaves
  the nearer source frame as the next drawing, two thirds of a beat early, a jolt of 0.67. Recorded on
  every aligned loop as `cycle_align.beat` in `strip.json` and in the report row's `beat`, as frames of
  the turned loop: `jolt` (largest, 3 decimals), `gap` ([k, k + 1] where it is), `over` (every gap at
  `BEAT_JOLT` or more), `jolts` (every gap's), `limit` (0.25), `band` (0.1), and `path`. `path` is
  `read`; or `on-time` where every frame stands at its own time (jolt 0, the legs not read at all, no
  cost); or `unread` (with `why`) where the legs do not move on the band (a path of no length has no
  beat; the frames given way are then left as before). Only what the alignment moved off its time
  counts: the cut's own rhythm does not, so drawings held two or three frames or one step quicker than
  the next give every jolt 0 when every frame is at its own time. Under `--between nearest` the beat is
  recorded, not warned, and under `rife` every made frame is at its own time.
- **A frame given way, kept on the beat** (`--between auto`): a loop whose frames given way keep the
  beat, and a loop with no frame given way, is delivered byte for byte as before (strip, GIF, WebP,
  every cycle frame; only its `beat` record and, where its `--view` is back or back_diagonal, its
  `same_foot` record are new). Otherwise the loop is kept on the beat in this order:
  - 1. The loop as before (every frame with a fault given way to the nearer source frame, or to the
    other frame beside its time where the nearer is a filmed ghost) is read for its beat. Where no
    gap jolts `BEAT_JOLT` = 0.25 beat or more, it is delivered as before, byte for byte (strip, GIF,
    WebP, every cycle frame; gaining the `beat` record and, where its `--view` is back or
    back_diagonal, the `same_foot` record).
  - 2. Otherwise the frames given way are taken in runs of neighbouring frames (a run across the last
    frame and the first is one run). Each frame of a run has these choices: the nearer source frame as
    before; or the same two source frames made again at another fraction from `align.RETIME_T` = 0.15,
    0.20, ... 0.85 (step 0.05; the frame's own fraction is not made again), tried nearest its own
    fraction first, kept only where the frame made has no fault (the same readings and `faults()` as
    every made frame) and stands within `BEAT_JOLT` = 0.25 beat of its own time on the leg path
    (fractions further off are not made at all). Over the run, the engine takes the choice that keeps
    every gap from the frame before the run to the frame after it under `BEAT_JOLT`: the fewest
    frames made again, then those nearest their own fractions. Why the run and not each frame: two
    frames side by side each made again nearest its own fraction can each stand within `BEAT_JOLT` of
    its time and still jolt the gap between them past it. For example, one frame made again an eighth
    of a beat late (+0.125) and the next a fifth early (-0.2) jolt the gap between them -0.325, over
    the limit; the first made at another clean fraction instead (-0.175) keeps every gap under it.
    So the gaps are judged with the same definition over the choice, and the whole loop's beat is
    read again after it.
  - 3. A run no such choice keeps has its jolt spread over the frames beside it (`align._widen`,
    widths `align.WIDEN` = 2, then 3, frames on each side). The frames beside it must all stand at
    their own time and not be given way. The run takes, of the same choices (the nearer source frame
    however far off its time, or a fraction made again), the one whose largest jolt is least: inside
    the run, and the jolt from the frame width + 1 away on each side shared evenly over the width
    frames between. Each frame beside the run is moved off its own time by its share (a third and a
    sixth of the run's offset at width 2), to the time nearest its own where the leg path puts that
    place, and made there by RIFE (or taken where that time falls within 0.03 of a source frame). It
    is refused where that least jolt is `BEAT_JOLT` = 0.25 beat or more, a frame beside it would be a
    filmed ghost or is made with a fault, or the loop it leaves still jolts `BEAT_JOLT` or more
    anywhere between the two frames held. Why two or three frames and not one: moving one frame each
    side halves the jolt at best and moves that frame far toward the hard pair, where RIFE often loses
    its outline; two or three share it in smaller steps. For example, a frame whose pair breaks at
    every fraction within `BEAT_JOLT`, the nearer source frame half a beat late: two frames each side
    moved a third and a sixth of a beat late: every gap jolts a sixth of a beat. Recorded as
    `beat.widened`: each `run`, its `width`, and the frames `moved` (`at`, `off` in beats, and the
    source frame or the pair and fraction it now comes from). A moved frame's `smear` row is
    `method: "moved"` with `moved` (its new source or pair and fraction, its off, the new frame's
    measures); it counts in `made_at` when made.
  - 4. A run no choice keeps stays on the nearer source frame and is named: `beat.named` lists it
    (frames of the turned loop). Under `--between auto`, where the loop's beat still has gaps at
    `BEAT_JOLT` or more after the alignment kept what it could, `retake` carries the reason
    `broken-beat` (details `broken_beat`: `jolt`, `gap`, `over`, `named` for the frames that kept
    the nearer source frame, `remedy` "recut-or-retake"; limit `beat_jolt`); under `--between nearest`
    a broken beat is recorded in `beat` but is no reason (that choice takes the nearer frame on
    purpose). Each reason has one warning line, which replaces the earlier separate beat and same-foot
    lines: "<loop>: cut it again or film this direction again (broken-beat): its beat breaks, the legs
    jolt ...", giving the largest jolt, between which frames, every gap over, the frames that took
    the nearer source frame, and the remedy (another cut may land its times on frames RIFE can make;
    a new take may not need them made). The report's `retake` list carries these loops like the others;
    so a loop drawn every frame whose replaced frames break the beat is now named (`broken-beat`),
    where before only held loops and kept filmed ghosts were.
  Report fields: a frame made again has `smear` `method: "retimed"` (beside `rife` kept and
  `nearest` given way), keeps its first frame's measures and `faults` (why it was given way), and
  gains `retimed`: `t` (the fraction it was made at), `from_t` (its own), `off` (how far off its
  time, beats), the new frame's every measure, `faults: []`, `look`. Every frame given way in a loop
  whose beat broke gains `retime`: `between` (its pair of source frames), `tried` (each fraction made
  again, its `off`, its `faults`), `off_nearest` (the nearer source frame's off its time). It counts
  in `made_at` and `made_by_rife`, not `nearest_at` or `replaced`; the report's new `retimed` counts
  such frames over the set. `beat.retimed` lists them per loop. A warning line for it says RIFE's
  frame at its own fraction had these faults, "so the same two source frames were made again at <t>,
  the legs <off> of a beat off its time, where the nearer source frame broke the beat
  (--between auto)".
- **Same foot twice: named, refused when asked** (`--same-foot warn|refuse`): read only where the feet
  stand side by side and the stepping foot is drawn higher: `STEP_VIEWS` = back, back_diagonal (any
  facing). Seen from behind, the picture's left foot is the character's own left. Other views are a
  named gap (`read: false` with `why`): front is not read because the foot stepping toward the viewer
  is drawn lower, so the higher half is not the lifted foot (the same rule the engine's `strike_foot`
  uses), and a front walk whose feet cross one line reads the same half lifting twice when it
  alternates; side and front_diagonal are not read because the feet overlap one in front of the other,
  so a half is not a foot. It is also not read where only one half steps or neither does. Readings
  considered and not used: splitting the feet frame by frame at the middle of each frame's own foot
  band (a tail or a skirt over the band counts as a foot), lift against the loop's lowest row (a body
  that bobs reads its planted foot as lifted), and counting stride or reach peaks (the count is not
  steady from one view to another). The feet split at the middle of the solid columns of the box's
  lowest `FOOT_ROWS` = 0.03 over the loop. For each frame and half, its lift is how far the half's
  lowest solid row ends above the frame's own lowest row, as a fraction of the box's height. A step is
  a run of `LIFT_CELLS` = 2 or more frames (round the ring) lifted `LIFT_MIN` = 0.010 (1 %) or more. Two
  runs of one half are two steps only where its lift comes down to `DOWN_MAX` = 0.006 (0.6 %) or under
  between them (a foot that only dips is one step). Read only where both halves step; the steps in ring
  order by their highest frame; same foot twice where two neighbours on the ring (the last and the
  first among them) are one half. `video-cycle-align` reads it on every aligned loop whose `--view` is
  back or back_diagonal: report row and `cycle_align.same_foot` record `read`, `order` (for example
  "RRL", picture halves L and R), `steps` (each: `half`, `frames`, `peak`, `lift`), `twice` (pairs of
  neighbouring steps of one foot, by their highest frames), and `clip`. `video-loop` reads every cut of
  the cut's length the clip holds (after any lead-in), each as the loop it would make, on the keyed
  frames of every walk or run it cuts, and records it as `clip_feet` in `strip.json` and its report:
  `length`, `cuts`, `same_foot` (how many step the same foot twice), `alternate`, `unread`,
  `seam_in_step`, and `same_foot_at` (the first clip frame of each that does). A cut whose seam falls
  inside a step, one half lifted at both its last and its first frame, is counted apart, neither as
  alternating nor as stepping the same foot twice, and is not in `same_foot_at`; looped, it joins two
  lifts across its seam into one, so where the take lifts one foot twice it hides the second lift in a
  seam that jumps mid-step and reads as alternating; `same_foot`, `alternate` and `unread` count the
  other cuts. Recorded whatever the view; the alignment reads it where its view reads the feet. Under
  the default `--same-foot warn`, the warning says: "<loop>: film this direction again (same-foot):
  steps the same foot twice in a row: its own right foot lifts at frames a and b, no step of the other
  foot between (RRL round the loop ...); the alignment does not change the steps a take shows" (with
  "cut it again (same-foot)" where the remedy is recut, and "cut it again or film this direction
  again (same-foot)" where it is recut-or-retake) and then, from `clip`: every cut of its clip N frames
  long that reads steps so (S of C; J more with their seam inside a step, which hides the second lift
  in the seam): the take steps that foot twice every cycle, so cutting it again does not help, film this
  direction again (the part after the semicolon only when J is not 0); or: m of n cuts alternate, a cut
  elsewhere in the clip may alternate; or: the clip's cuts were not read (cut before video-loop recorded
  clip_feet), so whether another cut alternates is not known. Under `--same-foot refuse`, such a loop
  stops the set before anything is rewritten; the message names each
  loop as above plus "Stopped before anything was rewritten (--same-foot refuse)", and the report is
  written with `applied: false`, `refused: "same-foot"`, and `same_foot` (each loop's dir, name and
  reading). The default does not refuse because the app does not know this refusal yet and will turn it
  on with its retake rule. The report's `same_foot` key names the mode.
- **A take that jumps: named** (`cadence.screen_steps`, `cadence.screen_record`): for each step of the
  cut's own frames (`cycle.source/`, step `i` from frame `i` to `i + 1`, the last step from the last
  frame to the first, which is the cut's seam), `cadence.screen_steps(frames)` reads the mean absolute
  luminance difference over white (0 to 255) of the box every frame's figure fits in (`whole`) and of its
  lowest `cadence.SCREEN_ROWS` = 0.30 (`legs`; a strip cell's lowest 125 of its 406 px), read at
  `READ_HEIGHT` 410 px. Why: the beat reads only the leg path where the feet go (the lowest 10 % of the
  box); two drawings filmed a pose apart (legs wide apart in one, crossed in the next, the tail up) move
  the feet little and change the picture much, so the beat passes them, while the screen sees them. Jump
  rule (`cadence.screen_record`): a step is a jump when, over the box or on its leg rows, it is at least
  `cadence.SCREEN_JUMP` = 2.0 times the cycle's typical step and at least `cadence.SCREEN_ALONE` = 1.3
  times every other step of the cycle. The typical step is the upper quartile of the cycle's steps: on
  ones every step is a drawing change, on twos or threes most steps change nothing and the upper quartile
  is a drawing change. A cycle whose drawing changes are all alike names none; so does a cycle with two
  steps alike however large (neither is 1.3 times the other). Read where: on the cut's own frames, not on
  the aligned frames: an aligned frame spans two or three of the cut's frames, which spreads a jump over
  the drawing changes beside it and hides it. Read on every aligned loop, whatever its view and whatever
  `--between`. Record: each loop row and `strip.json` `cycle_align` gain `screen`: `whole` and `legs`
  (every step), `typical` (per region), `jumps` (each: `step` [i, i + 1] as frames of `cycle.source/`,
  `seam` true for the last step, `whole` and `legs` each [change, times the typical step, times the next
  largest step], `at` the gaps [k, k + 1] of the aligned (turned) loop that show it), and `limits`
  (`jump`, `alone`, `rows`). Reasons in `retake`: a jump inside the cut gives the reason `filmed-jump`
  (details `filmed_jump`: `steps`, `at`, `whole`, `legs`, `remedy` "recut-or-retake"): the take jumps
  between two poses there, and the alignment does not change the poses a take shows; where the clip films
  another cycle without the jump, a cut of the same clip from there removes it, and where it does not, a
  new take does. A jump at the seam gives `seam-jump` (details `seam_jump`, same keys, `remedy`
  "recut-or-retake"): another cut of the clip may close on a pose it holds; a new take may not need the
  cut to close there. Limits `screen_jump`, `screen_alone`, `screen_rows`. Order of reasons now:
  `held-drawings`, `filmed-ghost`, `filmed-jump`, `seam-jump`, `broken-beat`, `same-foot`. Warning lines:
  "<loop>: cut it again or film this direction again (filmed-jump): the take jumps between two of its
  frames, from frame 30 to 31 of cycle.source/ the picture changes 22 of 255 over the figure's box and 36
  on its legs, 2.10 and 2.50 times the cut's typical step and 1.50 and 1.80 times any other step (the
  loop's frames 13 to 14); the alignment does not change the poses a take shows. Where the clip films
  another cycle without the jump, a cut there fixes it; where it does not, a new take does". Seam:
  "<loop>: cut it again or film this direction again (seam-jump): the cut's seam jumps, from frame ... ;
  another cut of the clip may close on a pose it holds, a new take may not need the cut to close there".
  Python callers: `cadence.screen_steps`, `cadence.screen_record`, `cadence.SCREEN_ROWS` (0.30),
  `cadence.SCREEN_JUMP` (2.0), `cadence.SCREEN_ALONE` (1.3), `align.RETAKE_JUMP_REASON` ("filmed-jump"),
  `align.RETAKE_SEAM_REASON` ("seam-jump"), `align.retake(..., beat=None, feet=None, screen=None)`.
- **Smear and melt, per made frame**: `cycle_align.smear` lists every frame RIFE made, with its
  `method` — `rife` (kept) or `nearest` (a source frame beside it taken instead) — its `faults`, its
  own ghost reading (`ghost`, below; `null` where the screen does not read the loop), its cross-fade
  reading and what to `look` at (`crossfade`, below — named, never judged), the holes it opened
  inside its silhouette that neither source frame has there (`hole`, below), the line one source
  frame draws that runs at part strength in its own place in the made frame as a double outline
  (`doubled`, below), and what it has that neither source frame beside it has (`rife.smear`):
  - `dark_excess`: dark pixels (luma under 70/255) inside the body beyond the darker neighbour's
    count, as a fraction of its solid pixels — a black smear raises it, a dark part that only
    moved (a hat, a watch) does not. Over 0.1 % it is the fault `smear`.
  - `outline_loss`: of the frame's coverage edge (alpha from 0.1, so a pale ghost limb's edge
    counts), the share with no dark solid pixel within 2 px, beyond the less outlined
    neighbour's, as a fraction of its edge. Legs that crossed too far for the flow melt into one
    shape of fill and lose their outline where they meet the air; a limb left as a ghost has none.
    Over 5 % it is the fault `outline` — where `outline_shift` is read, over 5 % too (below). A step
    the flow follows keeps its outline; a figure drawn without outlines loses none against neighbours
    that have none, and is not judged by it.
  - `outline_shift` (`rife.outline_shift`): the same edge read against the neighbours rank by rank —
    each pixel of the frame's coverage edge by how dark the darkest solid pixel within 2 px of it is,
    in order, against the lighter neighbour's edge at the same rank, counted where it is lighter by
    more than **50/255** (`rife.OUTLINE_SHIFT`), as a fraction of its edge. Where it is read, the
    fault `outline` needs both readings over 5 %. The dark line alone reads an art outlined in a mid tone — a brown or a
    painted line whose tone lies about 70/255 — as lost wherever the flow softened it: a line a few
    tones lighter falls on the far side of 70/255 all along the figure, and a frame made between two
    such drawings reads as melted however cleanly the flow followed them. Rank by rank the softened line is lighter by less than 50/255
    and is kept, whatever tone the art outlines in; a line gone to the fill beside it is lighter by the
    line's whole contrast and is lost. Art outlined near-black reads the same on both, and the fault
    is the dark line's as before; art outlined lighter than 70/255 is still not judged by the outline
    (the cross-fade reading below names what it hides). On the synthetic walker of
    `tests/video/test_outline_shift.py`, outlined in two tones either side of 70/255 (luma 57 and 77),
    the drawing between with its line drawn 20 to 45 tones lighter reads `outline_loss` 57 % and
    `outline_shift` 0; the same drawing with its legs' line turned to fill reads 24 % and 48 %.
    The 50/255 is a reference, like the 5 %, not a measured optimum: it lies between the lift a
    softened line takes and the lift of a line gone to fill, and a frame near either is worth a look.
    What it does not see: a line less than 50/255 darker than the fill it melts into (a dark grey
    shoe on a mid blue) is no further from its neighbours' than a softened line, and is not read as
    lost — on art outlined elsewhere; art with no darker line on most of its edge is not read this
    way at all (below).
    It is read only between **outlined** neighbours (`rife.outlined`; `null` otherwise, and the dark
    line judges alone): on each, the line of at least **75 %** (`rife.OUTLINED`) of its coverage
    edge — the darkest solid pixel within 2 px — darker by more than the same 50/255 than its fill,
    its colour from `rife.LINE_DEPTH` inside, pushed out over the edge as the colour around the body
    is pushed out for RIFE (section 1). Art painted without a line has no line to lose: its edge is
    its fill, and a part the flow melts — a boot washed pale, a shin left a ghost — comes out lighter
    by less than 50/255 at every rank where its tones run close together, so read rank by rank it
    would be kept. There the dark line judges alone, as it did before this reading: a frame it
    refuses is replaced by the nearer filmed one, a little less smooth, where a melted frame kept
    would be delivered. On the synthetic walkers of
    `tests/video/test_outline_shift.py`, the two-tone walker and the walker outlined in black read
    outlined on all their edge; the walker painted without a line (skin legs, dark boots, a body of
    one mid tone) reads 4 %, and its drawing between with the near boot washed 40 tones lighter reads
    `outline_loss` 7.8 % and, rank by rank, 0 — refused on the dark line, where the rank reading
    alone kept it. The 75 % is a reference, set on frames made between drawn walks and judged by
    eye: with it, no frame seen as broken that the dark line alone refused is kept.
    `rife.LINE_DEPTH` is twice the bleed's depth (`rife.BLEED_DEPTH`): the walkers of the tests,
    outlined 2 px on a figure 153 px tall, read about half their edge outlined at the bleed's depth,
    whose core still takes the line's inner pixel, and all of it at twice that.
    The jump repair (section 2) and the alignment read it on every frame they make; the versioned
    measures of `video-loop-compare` and `video-loop-repair` do not, and judge the outline on the
    dark line alone, as before.
  - `partial_excess`: part-covered pixels beyond the more ragged neighbour's, as a fraction of
    its solid pixels. Reported, not judged: a melted frame and a clean one read alike on it, and a
    frame made beside a filmed ghost carries the ghost without adding to it — the ghost screen
    reads part coverage on the frame alone.

  Every fault is a line in the report's `warnings` (and on stderr, and in `video-set`'s
  `warnings`), whichever `--between`: under `auto` it says the nearer source frame was taken
  there (or the other, the nearer being a filmed ghost); under `rife` that the frame is kept, to
  look at in `cycle/`. The report counts the
  frames kept from RIFE (`made_by_rife`) and those replaced (`replaced`); a loop row lists both
  (`made_at`, `nearest_at`).

  Why `outline_loss` and not the dark count's other side: a frame half way between two drawings
  has fewer dark pixels than the darker one wherever the two differ — a watch half hidden, an
  outline between overlapping legs — so a clean frame reads below zero too. On two outlined legs
  (`tests/video/test_rife.py`) RIFE's clean frames of a short step read `dark_excess` −1.3 %,
  lower than its melted frame of a long crossing (−1.1 %); `outline_loss` reads under 3 % for the
  short step at every fraction and 32 % for the melted frame. The 5 % line is where a frame stops
  reading as a little soft at a foot and starts reading as melted; it is a reference, not a
  measured optimum, and frames near it are worth a look either way.
- **The ghost screen, every frame delivered** (`interpolation_quality.ghost_screen`, `rife.ghost`):
  a video model filming a walk held on twos or threes sometimes leaves a frame half way between two
  drawings — a band of part coverage where a leg was and is going.
  Taken at its own time it was delivered as filmed, never measured; a frame RIFE made beside it
  carried the band, and measured against that neighbour it added nothing. So every frame the
  alignment delivers is read on its own coverage, not against its neighbours:
  - `ghost`: part-covered pixels (alpha 0.1 to 0.9) inside regions at least **5 px** thick
    (`rife.GHOST_THICK`; an antialiased edge or a keyed strand of hair is a pixel or two wide and
    erodes away), as a fraction of the frame's solid pixels. Over **0.3 %**
    (`interpolation_quality.GHOST_WARN`) the frame is a ghost: on a made frame the fault `ghost`.
  - `ghost_window`: a made frame's ghost band is now also read in windows of the body (new
    `rife.ghost_window`, constant `rife.GHOST_BAND` = 0.10): the same part-covered pixels in regions
    at least `rife.GHOST_THICK` (5 px) thick that `rife.ghost` counts, but counted in windows a tenth
    of the body high, sliding a quarter window at a time down the body, each as a share of the solid
    pixels in that window; the reading is the largest window's share, and where that window lies as
    fractions of the body from its top. Over **1.4 %**
    (`interpolation_quality.GHOST_WINDOW_WARN` = 0.014, 1.4 % of a window's solid pixels) the made frame
    has the fault `ghost`, the same fault name as the whole-body reading, though its whole-body
    `ghost` stays under 0.3 %. Why: where one source frame has a gap beside a limb and the other has the limb
    there, RIFE can blend the two coverages, leaving the limb part-covered over the gap so the
    background shows through it (a "limb seen through", which reads as a hole in the leg at a strip's
    size); over the whole figure that band is a small share and passes the 0.3 % limit, and the hole
    reading excuses it as the first source frame's own gap; in the rows it lies in it is a large share.
    A faint band can read just under 1.4 %, so a frame near it is worth a look. It is read only on the
    frames the cycle alignment makes (the measure carries it), not on `video-loop`'s jump repair frames
    and not by the ghost screen of filmed frames. Each `smear` row (and a frame made again, under `retimed`)
    records `ghost_window` and `ghost_window_at` ([from, to] fractions of the body); both are `null` where
    the ghost screen does not read the loop. A frame judged a ghost by its window has a warning line saying
    "carries a part-covered band over 2.40 % of the solid pixels in a band 10 % of its body high (60 to 70 %
    down it), at least 5 px thick, though 0.18 % of its whole body, a limb seen through, a ghost" (an example
    in this form), and gives way, or is made again at another fraction, like any faulty made frame.
    Python callers: new `align.measure_made(frame, a, b, t, reads=...)` returns every measure a made
    frame is judged by. A frame made only to keep the beat (a fraction made again, or a frame moved
    for a spread) is taken only where its window reading is under `align.RETIME_WINDOW_MAX` = 0.006,
    well under the fault's 1.4 %, besides having no fault. Why: such a frame is optional, the nearer
    source frame (with the broken-beat reason and its warning line) is always there instead, and a frame
    seen through is worse than a beat that jolts; a fraction next to one refused for a limb seen through
    often carries the same patch with more of it wholly clear (alpha under 0.1, so not part-covered),
    which reads under 1.4 % and is seen all the same. Each fraction tried records `ghost_window`, and
    `window_over` (the bar) where it had no fault but read over the bar.
  - The screen reads the loop as filmed (`cycle.source/`): `ghost_screen` per loop row and in
    `cycle_align` — `limit`, `thick_px`, the loop's own `level` (its median frame's reading), `reads`,
    and `ghosts` (each `frame` of `cycle.source/` over the limit, with its reading). A loop whose
    level is itself over the limit is drawn part-covered — a glow, a translucent cape — and is not
    judged (`reads: false`, with `why`): every made frame's `ghost` is `null`, nothing gives way, and
    a line in `warnings` (on stderr, and in `video-loop`'s own output for the cut) says the screen
    does not read it, so whoever delivers it looks at `cycle/` instead.
  - A time on a filmed ghost takes the source frame after it, or the one before it where that one
    is a ghost too, under `auto` and `nearest`; a made frame that is a ghost gives way like any
    faulty one, to the nearer source frame, or to the other beside its time where the nearer is a
    filmed ghost. `rife` keeps both, named. Where both frames beside it are ghosts the ghost is
    kept, named, and the loop is a take to film again (`retake`, reason `filmed-ghost`, below). A
    loop already the set's length is screened the same way.
  - Each cell where a filmed ghost would have been delivered is listed in `ghost_at` (`at`, the
    ghost `source` as a frame of `cycle.source/`, its `ghost` reading, and the frame `taken` — the
    ghost itself where it was kept, with why: `kept` is `no-clean-frame` where neither source frame
    beside its time is clean, under any `--between`, and `between-rife` where `rife` kept it beside
    a clean one), with a line in `warnings` ("source frame 5 is a filmed ghost (…), so source frame
    6 beside it was taken there"). `cells_from` says the same per cell. A made frame given way at
    such a cell says which: the other frame beside its time taken, "the nearer being a filmed
    ghost", or the nearer kept, "the frames beside it being filmed ghosts too".
  - Where 0.3 % sits: set by eye on drawn walks, between the half-drawn frames (and the frames
    made beside them) that a person sees as ghosts, which read well above it, and the frames seen
    as clean, which read well under it; a sliver of the same kind, too small to see at a strip's
    size, reads under it too. On the synthetic legs of `tests/video/test_ghost_screen.py` a keyed
    edge softened over two or three pixels reads 0.04 % and a band of 0.3 coverage between the
    feet 2 %. It is a reference, not a measured optimum, and a frame near it is worth a look either way.
  - What it does not see: a frame RIFE made that blends two drawings inside one silhouette — the
    near and far boots cross-faded while the outline stays whole — has no part coverage to read
    and is not a ghost here; the cross-fade reading below names it.
  - `video-loop` reads the same screen on the cut as filmed, for a walk or run (`ghost_screen` in
    its report), before its jump repair, and gives each ghost it names way there (section 2): a
    loop delivered as cut — one direction, never aligned, as a run of one direction is — would
    show it otherwise. RIFE's frame between the two clean frames beside it, else the clean frame
    beside it, else the ghost kept and named (`ghost_given_way`, `ghost_kept`, a `warning:` line
    each). The cut it writes is the alignment's `cycle.source/`, so a set aligned later reads a cut
    already given way; the strip metadata says what was. Under `--repair off` the cut is delivered
    as filmed and each ghost is a `warning:` line naming `cycle/frame-NNN.png`; the alignment
    screens `cycle.source/` again and takes a clean frame beside it. The jump repair also rejects
    a proposal that is a ghost. `video-loop-compare` keeps its versioned measure (`rife.smear`,
    [loop comparison](loop-comparison.md)) and does not read it; `video-loop-repair` reads it since
    `source-restoration-v5` ([the ghost guard](loop-comparison.md#the-ghost-guard)): a cell the cut
    gave a ghost way is never restored to it. Its evidence and receipts of `source-restoration-v4`
    are refused, never read as v5, as v3's were by v4.
- **A cross-fade inside the silhouette — named, not judged** (`rife.crossfade`,
  `interpolation_quality.looks`): where two drawings differ more than the flow can follow — the legs
  trading places across a gap two or three frames wide — RIFE can move the coverage and cross-fade
  the colour: both drawings' boots at part strength inside one whole outline. No band of part
  coverage, nothing darker inside the body, and an outline the outline rule reads only where the art
  outlines in near-black (it counts pixels under 70/255 as outline): outlined lighter than 70/255
  (grey, a light brown), the same frame passes every fault.
  - `crossfade`: in windows 8 % of the body's height over the frame where the two source frames
    differ, two things in one window — pixels of the made frame's solid body at the plain blend of
    the two at its own fraction and well away from each (in patches 5 px thick, past an edge's soft
    rim), and the made frame keeping under 60 % of the source frames' strong edges there (local range
    over 7 px, wider than RIFE's blur, above half the sources' 95th percentile there): a blend shows
    each edge at part height, a flow that moved the part shows it whole, only softer. The strong-edge
    pixels lost in such windows, over the frame's solid pixels — the windows step by half their size,
    so one lost pixel is counted in up to four of them (nine where the window is an odd number of
    pixels wide); 0 where it shows no blend. Relative to the source frames' own edges, so the
    palette's lightness does not decide it.
  - Over **0.015** (`interpolation_quality.CROSSFADE_LOOK`) the frame is named to look at: its
    `smear` row's `look` is `["crossfade"]`, and a kept frame (`method: rife`) gets a line in
    `warnings` ("frame 14 (made by RIFE) may cross-fade two drawings inside its outline …; kept").
    It is no fault: `auto` keeps it, it gives nothing way, and the retake rule does not count it. A
    cross-fade that leaves the two drawings' lines at part strength inside the silhouette over a
    long enough run is now a double outline (`doubled`, the bullet below), a fault of its own, and
    gives way for that; the cross-fade reading still only names it. Before the double outline was
    read, the alignment kept such frames because the cross-fade reading is no fault, for the reason
    in the next sub-bullet, which is about that reading and still holds; it was never a choice to
    keep frames with doubled lines. The clean drawing between far-apart drawings, which the
    cross-fade reading reads over its line, reads no double outline (0 in grey and in black
    outlines) and is kept; separately, on the synthetic legs of `tests/video/test_crossfade.py`, the
    frames a RIFE stand-in cross-fades across the legs' long steps (its colour run blends, its
    coverage run is the union) now give way under `auto`, each still named to look at; under `rife`
    they are kept and named.
  - Why a reading and not a fault: checked against frames labelled by eye, it does not part them.
    On drawn walks the frame seen cross-faded read well over the line and the frames seen clean
    under it; on synthetic legs (the walker of `tests/video/test_crossfade.py`, its palette varied
    32 ways — the outline black, brown, grey or none, the boots dark, mid, light or none, two leg
    fills — four steps, three fractions, 1,536 frames) the colour-blended, coverage-whole frame reads over it in
    209 of 384 — 74 of 96 outlined in grey, where the fault rules catch 2, but 11 of 96 drawn
    without outlines — and the clean drawing between, drawn, reads over it in 29 of 384, every one
    a step of 13 degrees or more between far-apart drawings, most outlined in grey. A fault would
    give way for clean frames and still pass blends; a line to look at costs neither. 0.015 is set
    by eye between the cross-faded frames and the clean ones, a reference, not a measured optimum.
- **A part torn — the overlapped wrap's frames** (`rife.passes`, `rife.torn`): between two frames a
  cycle apart a part on its own beat moves its whole way at once (section 2, "The overlapped wrap"),
  and where it swings over a smaller part — a tail across a hand — RIFE's flow drags the small part
  apart with it: the hand broken into blocks, much of its coverage gone, what is left part-covered. On
  art painted without a line nothing above reads it: there is no line to lose (and the outline
  readings judge against the less outlined neighbour, the one whose tail meets the air), the coverage
  goes rather than staying part-covered in a band, and nothing goes darker. RIFE's augmentation runs
  the pair eight ways (every flip and transpose) and both ways in time, combines the sixteen inside
  the binary and gives back one frame; run one at a time with no augmentation, each turned back, the
  passes guess differently exactly where the flow finds no one way through.
  - `torn`: per pixel, how far the mean of the eight forward passes and the mean of the eight
    backward ones put the premultiplied colour and coverage apart (`rife.passes`); the pixels where
    they are more than **0.05** apart (`rife.TORN_LEVEL`), in regions at least **3 px** thick
    (`rife.TORN_THICK`: an edge the passes place a pixel or two apart opens away, as does hair they
    redraw strand by strand); and of those, the largest share of a window **a tenth** of the made
    frame's body height on a side (`rife.TORN_WINDOW`, about a hand on a walk; 8 px at least, the
    windows stepping by a quarter of it). Read in a window rather than over the body, so a small part
    torn on a large body is not lost in it.
  - Over **0.21** (`interpolation_quality.TORN_WARN`) the frame is torn: the fault `torn`. Set by eye
    on drawn walks, between the frames seen torn — a hand dragged apart beside a tail swung far, legs
    melted into one shape, a part doubled — which read over it, and the frames seen as clean, which
    read under it; a reference, not a measured optimum. On the painted walker of
    `tests/video/test_loop_overlap.py` (section 2) the frames made across the hand read 0.44 and
    0.26 and those of a swing past no hand 0.04 at most.
  - Read only on the overlapped wrap's frames: each is made as every call makes it and read with its
    passes, 34 calls of the binary against two for a frame anywhere else. The jump repair, the eased
    retime and the cycle alignment make their frames between a walk's neighbours, a move RIFE carries,
    and do not read it.
  - The frame delivered is the binary's, not the passes' mean: where the passes disagree the mean
    blends their guesses part-covered, which the augmentation, combining them inside the binary, does
    not. Nor can the binary's own augmented calls be read for it: a call with `-x` from a to b and one
    from b to a agree where the single passes do not.
- **A hole through a limb, per made frame** (`rife.hole`): RIFE runs a frame's colour and its
  coverage apart (section 1), and where a step opens a gap between the legs in one source frame and
  not the other, the coverage run can carry that gap into a leg the colour run keeps whole: a
  transparent band through the leg, its fill on both sides. Nothing is added, no part coverage is
  left to read, and the leg's own fill is all the line round the band; a fill darker than 70/255
  (dark tights, a boot) is outline to the dark line, and one lighter than that is lost rank by rank
  only where it is 50/255 lighter than the source frames' lines at the same rank, which light lines
  elsewhere on the art (fur, hair drawn without a dark line) hide; the fault `outline` needs both.
  Neither reading is about a hole.
  - `hole`: of the made frame's pixels under solid (alpha under 0.5) that the outside of the picture
    cannot reach without crossing its solid body (enclosed holes), a pixel is the source frames' own
    in either of two readings, where either source frame has a pixel under solid within **4 px**
    (`rife.HOLE_REACH`, a square neighbourhood) of where that pixel lay at that frame's time: in
    place (the background, still, and a gap kept, or opened by a step where the frame showing it
    open has it); or carried with the whole figure along the motion the two source frames show for
    the whole figure. Where the made frame sits at fraction `t` from source frame a to source frame
    b, a pixel of it lay in a at `t` of the motion back and lies in b at `1 - t` of it on (rounded
    so the two are the whole motion apart). The whole figure's motion is measured only from the two
    source frames, never from the made frame or RIFE's flow: over the box round all three frames'
    solid bodies, each pixel of the box is read in a where it lay and in b where it lies, and the
    motion with the least mean absolute difference of premultiplied colour and coverage is taken
    (ties going to the smaller motion). It is followed up to 20 % of the body's height each way
    (`rife.HOLE_TRAVEL`, 0.2), searched coarse to fine: pooled in square blocks of a power of two
    where the range is at most 24 px each way (`rife.HOLE_COARSE`, 24), then within 2 px of twice
    the coarser motion on each finer level down to the pixel. Third, whatever carried it, a hole
    those two readings leave is the source frames' own, all its pixels, where it is one hole of each
    source frame: the pairing. It reads holes, not a motion. A hole here is one 8-connected piece of
    a frame's enclosed pixels: the made frame's count where they keep a pixel when opened by a 5 px
    square (`rife.HOLE_THICK`); a source frame's where they keep one opened by a 3 px square
    (`rife.HOLE_SOURCE_THICK`, 3), since a gap can be a little thinner at a source frame's time.
    Each hole is read for its area (its pixels), its centroid, its shape (the logarithms of its
    pixels' two principal second moments, each plus 1/12, over its area, so turning and growing
    leave it as it is) and the colour round it (the mean premultiplied colour of that frame's solid
    pixels from 5 to 8 px out). A hole of the made frame is paired with a hole of a and a hole of b
    where the two source frames' holes have areas within 4 times of each other
    (`rife.HOLE_PAIR_AREA`, 4.0), shapes within 2 times on each moment (`rife.HOLE_PAIR_SHAPE`, 2.0)
    and colours round them within 0.12 on each channel (`rife.HOLE_COLOUR`, 0.12, on 0 to 1), and
    their centroids lie at most sqrt(2) times the reach the figure's motion is followed (20 % of the
    body's height each way, so diagonally) plus 4 px apart; and where the made frame's hole has its
    area within 25 % of their range (`rife.HOLE_AREA`, 0.25: at least 0.75 times the smaller, at
    most 1.25 times the larger), its shape within 1.3 times of theirs on each moment
    (`rife.HOLE_SHAPE`, 1.3), the colour round it within 0.12 of each, and its centroid within 4 px
    plus 0.1 of their distance (`rife.HOLE_PATH`, 0.1) of the point at `t` on the line from the
    centroid of a's hole to that of b's. The pairing reads only the made frame's holes that the
    first two readings (in place, and carried with the whole figure) leave, that is, holes keeping a
    pixel those two readings do not give the source frames. A hole every pixel of which those two
    readings give the source frames takes no pair: it is theirs already, whichever of their holes it
    lies by. Pairs are taken one to one: no hole of the made frame, of a or of b is in two pairs
    taken, so each source frame's hole explains one of the holes paired. Of all sets of pairs that
    can be taken together, the set taken pairs the most holes; of those, the one with the least
    summed cost. The same frames always give the same set. The cost of a pair is the sum of: the
    logarithm of how far the made frame's hole's area is out of the range of the two source frames'
    holes' areas, its distance from the point at `t` over what it may be, how far its shape is out,
    the colour differences of the made frame's hole and of the two source frames' holes each over
    0.12, and the two source frames' holes' distance over what it may be. So alike holes moving
    together each keep their own two holes, however near their ways run. Pairs that share a hole,
    directly or through other pairs, form a group, searched apart. First an exact depth first search
    asks whether one set pairs every hole of the group: from the hole with the fewest pairs still
    free, it tries each of that hole's free pairs, cheapest first, and leaves no hole unpaired; it
    cuts a branch where a hole left has no free pair, or where the holes left cannot each keep a
    hole of a, or a hole of b, of its own among the holes their free pairs hold (a bipartite
    matching, checked for each source frame). Where it finds such a set, every hole of the group is
    paired. Where that search ends without such a set, a second exact depth first search finds the
    set pairing the most: from the hole with the fewest pairs still free, it tries each free pair
    cheapest first and then leaving the hole unpaired; it cuts branches that cannot pair more holes
    than the best set found, or as many at no less cost, and stops at a set pairing every hole. Each
    search takes at most 5000 steps per group (`rife.HOLE_PAIR_STEPS`, 5000). Where the first runs
    past them, the second runs; a group whose second search runs past them pairs none of its holes,
    and the frame gives way to the nearer source frame. Where every hole of a row or grid of alike
    holes pairs, the first search takes one step per hole and one more (a grid of 196 holes, 197
    steps). On the synthetic frames no group runs past 5000 steps; the most taken is 258, by the
    second search, on a grid of 36 holes 24 px apart one of whose holes a source frame shuts (that
    frame gives way in any case). On random groups of 8 to 96 alike holes, each with its own two
    holes and up to five more pairs within three places of its own, at random costs, the second
    search alone runs past 5000 steps before finding a set pairing all of them on 651 of 4000
    groups, and such a frame would give way though every hole has its own two; with the first search
    before it, on 17. That remaining case is a named bound of the reading. The made frame is never
    used to measure a motion and RIFE's flow is never read. The motion and the pairing are read only
    on frames keeping a hole after the in-place reading, and the pairing only on frames keeping one
    after the second reading. Regions at least **5 px** thick (`rife.HOLE_THICK`, opened by a square
    that wide: a pinhole or an antialiased crevice a pixel or two wide opens away) are taken after
    each step, as a fraction of its solid pixels.
  - Over **0** (`interpolation_quality.HOLE_WARN`) the frame has a hole through a limb: the fault
    `hole`, and `auto` takes the nearer source frame there, with a line in `warnings` (it says the
    frame opened a transparent hole inside its outline over the given share of its body, at least 5
    px thick, where neither source frame beside it has one, a gap through a limb, and that the
    nearer source frame was taken there); `rife` keeps it, named. A gap both source frames have,
    carried by the body's motion, lies in the frame between on the way from where one source frame
    has it to where the other has it; moved far enough it lies 5 px and more from where either
    source frame has it in place, and a reading in place alone would take it for a hole and give a
    correct frame way. A gap one source frame has, half open in the frame between while the figure
    moves, lies beside where that frame has it in the same way. A hole both source frames have that
    a part carries, while the figure's motion does not, is kept by the pairing: a part moving over a
    body that stays, plain or patterned, a part turning, growing, sheared or swinging. No motion of
    the whole figure carries it: the body that stays outweighs the small part, and a turned or grown
    gap lies where no motion of the whole picture puts it. It is one hole of each source frame,
    alike in area, shape and the colour round it, and it lies on its way between theirs, so whatever
    carried it, it is theirs. The pairing tells the hole by what it is, not by where a motion takes
    it. Following the whole figure's motion and pairing the holes keep those gaps as the source
    frames' own; a gap the flow opens through a lock of hair is read the same way as one through a
    leg.
  - What it does not read: a band cut through a leg's edge rather than inside it reaches the outside
    and is no hole (the outline readings read its edge as before); a hole within 4 px of a hole a
    source frame has, in place or where the whole figure's motion carries it, is read as that hole,
    whatever it is: in a row of alike holes moving along the row about as far as they are apart, a
    hole more lies within 4 px of a source frame's hole in place and is read as theirs (a row of
    four holes 8 px square, 24 px apart, on a plate moving 32 px along the row); and in a column of
    three holes 8 px square, 24 px apart, on a plate moving 32 px down, the middle hole shut in the
    second source frame, in the frame between the middle hole lies against holes of both source
    frames, so the in-place reading reads it as theirs, though it is one hole more than the second
    source frame has (the last hole pairs with its own two; the frame between is kept: figure 10). A
    hole the pairing's bounds let through is the source frames' own: one moved off its way by no
    more than 4 px and 0.1 of the distance between the two source frames' holes (6 px across and 6
    px along the way of a plate moving 48 px is 8.5 px, where 8.8 px is let through), or one larger
    or smaller than both of theirs by up to a quarter. The pairing's bounds are where a hole stops
    being theirs, and past them the frame gives way to the nearer source frame: a hole unlike both
    source frames' holes (its area more than a quarter out of the range of theirs, or its shape more
    than 1.3 times out of theirs); a hole off its way between theirs (farther from the point at `t`
    than 4 px and 0.1 of their distance, as a plate drawn 12 px across its way, or drawn where it is
    at t 0.8 in a frame made at t 0.5); two source frames' holes farther apart than sqrt(2) times
    the reach and 4 px (77.5 px on a body 260 px high: a plate whose centre moves 80 px, or one
    swung 60 degrees from a point 100 px above it, its centre moving 100 px); and two source frames'
    holes whose areas differ more than 4 times (a plate grown 2.2 times as it moves). A gap only one
    source frame has, that neither the in-place reading nor the whole figure's motion carries, has
    nothing to pair with and gives way: a gap only one source frame opens (closed in the other, half
    open in the frame between) on a part moving over a body that stays, plain or patterned (pinned
    by a test). The reading cannot tell such a gap from a band the coverage run carried out of one
    source frame's gap into a limb: both show one source frame's gap and nothing in the other. The
    pairing reads only the holes the first two readings leave. Among them each source frame's hole
    explains one: a hole more than the source frames have among them gives way, as a hole more in a
    row of holes moving with their plate, midway between two of them or past the row's end, or one
    of the row moved to a midway (figure 8). A hole more than the source frames have, left by the
    first two readings, is read as theirs where it pairs with one source frame's hole and the
    other's alike hole whose own holes in the frame between the first two readings already give the
    source frames (those holes take no pair, so their source holes are free for it). One more midway
    between two holes of a row standing still, or of a row the whole figure carries, lies where one
    source frame's hole and the other's next one put it, and pairs with them (figure 12; and figure
    8's row standing still, drawn a pixel taller, with one hole more midway). A twin of a gap the
    whole figure carries pairs with the gap's own two where it lies within the gap's way; being 5 px
    or more from the gap (or the second reading would take it for the gap), it needs the gap's width
    and 5 px to be within 4 px and 0.1 of how far the gap moves, so only a narrow gap moving far has
    one: a gap 5 px wide moving 67.9 px (48 px across and 48 down), its twin 10 px beside it; 11 px
    beside, it gives way, and a gap 8 px wide with its twin 13 px beside it gives way (figure 12).
    On that frame the outline readings give the frame way, readings of their own. A half-open gap
    the whole figure carries (figure 3) is kept by the second reading.
  - Read only on the frames the alignment makes (`hole` in each `smear` row). The jump repair of
    `video-loop`, its eased retime and overlapped wrap (section 2) do not read it, and neither do the
    versioned measures of `video-loop-compare` and `video-loop-repair`.
  - On the synthetic figures of `tests/video/test_inbetween_hole.py`: figure 1 (rectangles outlined
    in black, legs filled darker than 70/255, one source frame with legs side by side and the other
    with a gap between them) has the frame between with a band 8 px wide and 40 px tall through its
    far leg read `outline_loss` 0, `outline_shift` 11.3 %, `ghost` 0, and `hole` 2.69 % (its own 320
    pixels as a share of the body); the whole figure's motion is 0, so neither of the first two
    readings shows a gap where the band lay; the only enclosed hole both source frames have is the
    gap between the arm and the body (320 pixels), which the made frame keeps where it was, so the
    in-place reading gives the made frame's arm gap to the source frames, and it takes no pair; the
    band is left by the first two readings, and its only possible pair, the two source frames' arm
    gaps, does not fit: the band lies 25 times farther from the point at t 0.5 between those gaps
    than its way lets through, and the colour round it differs from theirs 6 times as much as is let
    through, so the band has no pair and is read whole. The drawn frame, the arm gap moved 3 px, a
    pinhole 3 px wide, and an empty frame read `hole` 0. In figure 2 (a 120 by 180 px body with an 8
    by 40 px gap, moved 0 to 32 px across and diagonally), the gap from 24 px on lies 5 px and more
    from where either source frame has it in place (1.50 % in place alone), but whole figure motion
    carries it: `hole` 0. In figure 3 (a body 90 px wide on legs in boots closing the gap below), a
    half-open gap when moving 30 px lies 8 px and more beside where the parted frame has it (300 px
    in place), but whole figure motion carries it: `hole` 0; with an 8 by 40 px band cut through the
    moving body it reads `hole` 2.88 % (320 pixels) and `faults: ["outline", "hole"]`. In figure 4
    (a 200 by 180 px still body with a 40 px square part and 14 px square hole moving 32 px), the
    whole figure's motion is 0, so the first two readings leave the hole; it is one hole of each
    source frame, alike and on its way 32 px between theirs, so it is paired: `hole` 0. In figure 5
    (a body that stays, patterned, with a part on it and a hole through the part's middle, the part
    moved from one source frame to the next, each frame between drawn exactly at its place): (i) the
    checkpoint validator's frame: a body 200 by 180 px with stripes 3 px wide every 6 px (a lighter
    colour by 40 levels), on a 260 by 220 px canvas, a part 24 px square with a hole 10 px square,
    moved 32 px across: the whole figure's motion is 0 and the in-place reading leaves the hole
    whole, 100 pixels or 0.28 % of the body (a motion read over the box round the hole would take
    one that lays the stripes on themselves); the hole is one hole of each source frame, alike and
    on its way, so it is paired: `hole` 0, and both made cells are kept as made, byte for byte. (ii)
    A body 240 by 260 px on a 300 by 300 px canvas in five patterns (stripes 3, 2 and 6 px wide
    every 6, 4 and 12 px running up and down; stripes 3 px wide every 6 px running across; checks 4
    px square) at contrasts 15, 40 and 120, parts 24, 40 and 60 px square with holes 10, 17 and 25
    px square: moved 32 and 48 px across and diagonally at `t` 0.5, and moved 16, 32 and 48 px
    across and diagonally at `t` 0.25 and 0.75 over the 3 px stripes at contrast 40, every one of
    these frames reads `hole` 0 (the moves listed in the test are those where a motion read over the
    box round the hole takes the body's stillness, or the stripes laid on themselves, on some
    pattern). Resampled to eight frames (`t` 0.25, 0.5 and 0.75 each way), parts of 24, 40 and 60 px
    moved 32 px diagonally and the 24 px part moved 48 px over stripes at contrast 120 keep every
    made frame, byte for byte. In figure 6 (a torso 120 by 260 px that stays, patterned in blocks 4
    px square each lighter by its own whole number up to 200 levels, with an arm beside its right
    edge: the upper arm and the forearm lie along that edge and are joined by the arm's outer side,
    so the gap between arm and torso, 16 by 12 px, has the torso's edge on its left and the arm on
    its other three sides; the arm reaches 48 px above and 52 px below the gap and moves 40 px down
    from one source frame to the next), the gap lies where the arm covers it in the other source
    frame, so the in-place reading leaves it, and the whole figure's motion is 0 (a motion read over
    the box round it would take the torso's stillness); it is one gap of each source frame, alike
    and on the arm's way, so it is paired: `hole` 0 at `t` 0.25, 0.5 and 0.75, and resampled to
    eight every made frame is kept, byte for byte. The boundary, pinned by a test: a part 30 px
    square moving 32 px across a body that stays, a hole 14 px square open through it in one source
    frame and closed in the other, 8 px square in the frame between (half open): over a plain body
    and over the 3 px stripes alike it reads `hole` 0.10 % (its 64 pixels) and both made cells are
    given way to the nearer source frame, with `faults: ["hole"]`; the second source frame has no
    hole on the part to pair it with. In figure 7 (a body 240 by 260 px on a 300 by 300 px canvas
    that stays, in stripes 3 px wide every 6 px (a lighter colour by 40 levels) or plain, with a
    plate on it (|u| < 24 and |v| < 28 px in its own axes) and a gap through the plate (|u| < 6, |v|
    < 16, so 11 by 31 px on whole pixels); each frame between drawn exactly at its place in one
    motion from one source frame to the next: the plate's centre moves across, and the plate turns,
    grows or is sheared about it, or the plate swings from a point 100 px above its centre), (i) the
    third checkpoint validator's frame: the plate turns 60 degrees as it moves 48 px over the
    stripes. No motion of the whole figure carries the turned gap, and the in-place reading leaves
    it whole, 0.55 % of the body; it is one hole of each source frame, alike and on its way, so it
    is paired: `hole` 0, and resampled to four, and to eight (`t` 0.25, 0.5 and 0.75 each way),
    every made frame is kept, byte for byte. The plate turned 90 degrees, grown 2 times and sheared
    by 0.6 as it moves 48 px, and swung 45 degrees (its centre moving 76.5 px, within sqrt(2) times
    52 plus 4, or 77.5 px, on this body), are kept the same way over the stripes; over the plain
    body each reads `hole` 0 (plain, the plate swung 45 degrees is given way for `smear`, a reading
    of its own). (ii) The plate turned 0 to 90 degrees, grown 1.1 to 2 times, or sheared by 0.15 to
    0.6 as it moves 0, 24 or 48 px, or swung 10 to 45 degrees, over the stripes and over the plain
    body at `t` 0.5, and turned as it moves over the stripes at `t` 0.25: all 125 frames read `hole`
    0. (iii) The boundaries, over either body: the plate moving 48 px, drawn with its gap 16 by 40
    px (1.9 times their area, where a quarter more is let through) it reads `hole` 1.04 %; with its
    gap 18 by 18 px (its larger moment 2.8 times smaller over its area, where 1.3 times is let
    through), 0.52 %; the plate drawn 12 px across its way, 0.55 %; the plate drawn where it is at
    `t` 0.8 in a frame made at `t` 0.5 (14.4 px along its way, where 8.8 px is let through), 0.30 %;
    the plate growing 2.2 times as it moves 48 px (the source frames' gaps more than 4 times apart
    in area, where 4 times is let through), 0.55 %; the plate moving 80 px (its centre 80 px from
    one source frame to the next, where 77.5 px is let through on this body), 0.55 %; and the plate
    swung 60 degrees, its centre moving 100 px, 0.55 %. Each frame gives way to the nearer source
    frame, with `hole` among its faults. In figure 8 (the same body with a plate 120 by 40 px on it
    and a row of four holes 8 px square through the plate, 24 px apart, the plate moving 32 px down
    from one source frame to the next), the frame between drawn exactly reads `hole` 0 and is kept
    as made; so is the frame between with the row drawn a pixel taller, where a hole of one source
    frame and the next but one of the other have their midpoint as near each of the made frame's
    holes as its own two: the pairing takes the set that pairs the most holes, so each hole keeps
    its own two. Drawn with one hole more, midway between two of the row's holes, or between the
    last two, or past the row's end, or with one of the row's holes moved 12 px to a midway, the
    frame between reads `hole` 0.10 % (the 64 pixels of one hole) and gives way to the nearer source
    frame: each source frame's hole explains one hole of the made frame. Standing still, with the
    row drawn a pixel taller in the frame between, the in-place reading gives the row's holes to the
    source frames, and the frame between reads `hole` 0 and is kept as made; the frame with one hole
    more midway is described with figure 12. In figure 9 (the same body with a plate on it and a row
    of two, three or four holes 8 px square through the plate, 12 px apart, moving on a curved way
    from one source frame (t 0) to the next (t 1): int(40 t + 20 t (1 - t)) px across and int(32 t)
    px down at time t, the plate moving with the holes, or staying while the holes move through it;
    with two holes it is the fourth checkpoint validator's frame), the frame between is drawn
    exactly, at t 0.5: each of its holes lies 5 px beside the midpoint of where the two source
    frames have that hole; 4 px and 0.1 of their distance (51 px), 9.1 px in all, are let through.
    The first hole lies 1 px from the midpoint of the second hole in the first source frame and the
    first hole in the second source frame: that pair of source holes is the cheaper for it, and
    taken, it would leave the second hole with nothing to pair with. The set of pairs taken pairs
    the most holes, so each hole keeps its own two: the frame between reads `hole` 0, and resampled
    to four frames, both made cells are kept, byte for byte. This holds for two, three and four
    holes, the plate moving or staying (with three and four holes, each hole but the last is
    likewise cheaper with the next one's hole in the first source frame). Boundaries, pinned by a
    test, with two and four holes, the plate moving or staying: with one hole more before the row,
    on the first hole's way (it could take the first hole's two, but then the first and the second
    cannot both keep theirs: each source frame's holes pair as many of the made frame's holes as
    they number, and no more), the frame between reads `hole` 0.10 % (the 64 pixels of one hole) and
    gives way to the nearer source frame; with the row's last hole shut in the second source frame
    (it has nothing there to pair with), it reads 0.10 % and gives way. With both searches limited
    to one step (`rife.HOLE_PAIR_STEPS` set to 1), neither finds a set within it, so neither hole of
    figure 9's two-hole frame is paired: it reads `hole` 0.21 % (both holes, 128 pixels) and gives
    way. In figure 10 (the same body with a plate 40 by 90 px on it and a column of three holes 8 px
    square through the plate, 24 px apart, the plate moving 32 px down from one source frame to the
    next; the middle hole shut in the second source frame), the frame between is drawn exactly at t
    0.5, its holes 16 px down. Each of its holes lies against the next hole down in the first source
    frame and the hole before it in the second: the in-place reading gives the frame between's first
    and middle holes to the source frames (of the first it leaves only a strip 4 px high, thinner
    than 5 px, which does not count; the middle one lies against the first source frame's last hole
    and the second source frame's first hole), and leaves the last hole whole, which lies against
    nothing in the second source frame, where the middle hole is shut. Those two take no pair; the
    last hole pairs with its own two holes (the last hole of each source frame), and the frame
    between reads `hole` 0 and is kept as made. The middle hole is one hole more than the second
    source frame has, which the in-place reading takes for theirs because it lies within 4 px of
    their holes: what the reading does not tell (see figure 12 and the sub-item on what it does not
    read), pinned by a test. In figure 11 (the same body with a plate 105 px square on it; through
    the plate a hole 8 px square that the first source frame and the frame between have in place and
    the second source frame shuts, and beside it a hole 8 px square moving from (100, 100) to (152,
    148), 52 px across and 48 px down; it is the fifth checkpoint validator's frame), the frame
    between is drawn at t 0.5, the moving hole at its exact midpoint, 10 px from the hole in place:
    within the moving hole's way (4 px and 0.1 of its 70.8 px, 11.1 px in all). The in-place reading
    gives the hole in place to the source frames, so it takes no pair, and the moving hole pairs
    with its own two; the frame between reads `hole` 0, and resampled to four frames both made cells
    are kept, byte for byte; so are they with the hole in place kept by the second source frame
    instead (open in the frame between and the second source frame, shut in the first). Figure 12 (a
    body 200 by 260 px outlined in black on a 400 by 400 px canvas, moving whole from one source
    frame to the next, with holes through it) pins what the reading does not tell: with a row of
    four holes 8 px square, 24 px apart, the body moving 32 px down, and one hole more midway
    between the middle two in the frame between, the second reading carries the row's holes to the
    source frames without pairs, and the hole more pairs with one source frame's hole and the
    other's next one, whose midpoint it is, reading `hole` 0 and kept as made (so is figure 8's row
    standing still, drawn a pixel taller, with one hole more midway, where the in-place reading
    gives the row to the source frames and the hole more pairs the same way, and figure 10's
    column); with a gap 5 by 30 px, moving 48 px across and 48 px down (67.9 px), and an alike gap
    10 px beside it in the frame between (5 px of the body between them), the second reading carries
    the gap, while the gap more lies 10 px from the point at t 0.5 between the source gaps, within 4
    px and 0.1 of their 67.9 px (10.8 px), pairing to read `hole` 0 either way, though outline
    readings give this frame way to the nearer source frame; a gap more 11 px beside is off that
    way, reading `hole` 0.29 % and giving way with `hole` among its faults, as does an 8 px wide gap
    with its twin 13 px beside it (5 px of the body between them again), at 0.45 %. In the test on
    triples alone (no frame), twenty alike holes, each with its own two holes and up to five more
    pairs with holes of the source frames within three places of its own at seeded random costs,
    have one set pairing every hole: each group of pairs sharing a hole is searched first for a set
    pairing every hole, then, where there is none, for the set pairing the most, each search at most
    5000 steps (`rife.HOLE_PAIR_STEPS`); the search for the set pairing the most, cheapest first,
    runs past 5000 steps and would pair none, whereas the search for a set pairing every hole finds
    one in 29 steps, pairing all twenty; where that search too runs past the steps, the other
    answers, and here pairs none.
- **A double outline, per made frame** (`rife.doubled`): Where RIFE's flow does not follow a line
  (on drawn art, the top of a boot that one source frame tilts, showing the oval of its opening, and
  the other plants flat, showing a straight band), it can fade the line out in place instead of
  moving it. The made frame then shows that source frame's line, lighter, in the source frame's own
  place, beside the line the made frame drew: a double outline. Its coverage is whole, so the ghost
  screen (part coverage, alpha 0.1 to 0.9, in regions **5 px** thick) reads nothing; the faded line
  is lighter than 70/255, so the dark count (`dark_excess`) adds nothing; the line is inside the
  body, not on the coverage edge, so both outline readings (`outline_loss`, `outline_shift`, which
  read the coverage edge) pass it; it has no enclosed transparent pixels, so `hole` reads 0. The
  cross-fade reading names whole drawings blended where the made frame keeps under 60 % of the
  source frames' strong edges in a window; a frame that moved most of a part and left one line faded
  keeps its edges, and the cross-fade reading is a reading to look at, never a fault, in any case.
  - `doubled`: `rife.doubled(made, a, b)` takes the made frame between source frame a and source
    frame b, with no fraction needed. A frame's line is its solid pixels (alpha 0.5 and over) darker
    by more than 50/255 (`rife.OUTLINE_SHIFT`, the same contrast the rank outline reading uses) than
    its own luma closed by a square 2.5 % of the made frame's body height wide (`rife.DOUBLED_WIDE`,
    0.025; off the solid body the luma counts as white). A dark stroke narrower than that window
    goes in the closing and is a line; a dark fill wider than it stays and is no line. A pixel of
    one source frame's line is left at part strength in the made frame when all of these hold: the
    three frames are solid there; the other source frame draws no line within **4 px** of it
    (`rife.DOUBLED_SHARED`, 4, a square neighbourhood): a line both draw is theirs at any strength;
    the other source frame is lighter there than this line by more than 50/255; the made frame is
    darker there than the other source frame by more than 50/255 (the line is still seen); and the
    made frame is darker than the other by no more than 0.8 of the line's contrast (the line's
    contrast is how much lighter the other source frame is than the line there; `rife.DOUBLED_PART`,
    0.8), both at the pixel and at its darkest pixel within **2 px** (`rife.OUTLINE_REACH`, 2): no
    line at full strength beside it, as there is beside the soft edge of a line the flow moved. Such
    pixels count where they make a region **3 px** thick (opened by a 3 px square,
    `rife.DOUBLED_THICK`, 3). Widened by **3 px**, those that touch make one run. A run's length is
    sqrt(12) times the square root of the larger second moment of its pixels, which is a straight
    run's own length. The reading is the longest run's length as a share of the made frame's body
    height: 0 where the made frame has no solid body, or where the two source frames nowhere differ
    by more than 50/255 inside all three bodies. It is read inside the box round where the source
    frames differ, padded so that every pixel there reads as in the whole frame.
  - Over **0.03** (`interpolation_quality.DOUBLED_WARN`, 0.03 of the body's height) the frame shows
    a double outline: the fault `doubled`. `auto` takes the nearer source frame there and writes a
    line in `warnings`; the line says the made frame left a line one source frame beside it draws at
    part strength in its own place, over a run of the given share of its body high, a double
    outline, and that the nearer source frame was taken there (`--between auto`). `rife` keeps it,
    named. The report's `smear` rows carry `doubled` on every made frame.
  - What is the source frames' own, kept (each a named bound): a line both source frames draw within
    **4 px** of each other is theirs, at any strength in the made frame (a static line the flow
    softened, a hem both draw with two lines); a line the made frame carries at more than 0.8 of its
    contrast is that source frame's own drawing there (a light crease one source frame draws,
    carried at its own strength, is kept); the soft edge of a line the flow moved lies within **2
    px** of the line at full strength, so it is not read; a line left less than 50/255 darker than
    what the other source frame shows there is not seen and not read; a dark patch wider than the
    closing window is no line; a run 3 % of the body high or shorter is not a fault; and only pixels
    where all three frames are solid are read, as part coverage is the ghost screen's.
  - A line only one source frame draws, with no line of the other's within **4 px** (a crease that
    comes or goes, an eyelid's line, a line a passing leg covers or uncovers), left at part
    strength, is read the same way and is a double outline: the flow did not follow it and faded it.
    This is a fault by the definition, not an exception. What the reading does not tell: a part the
    made frame draws smeared along its way, where the smear reaches back over a line one source
    frame draws there at part strength, reads as that line left. And lines left where one source
    frame is not solid (on the silhouette's edge) are not read, so a cross-fade whose doubled lines
    lie mostly on the silhouette's edge can read under the line.
  - Where **0.03** sits: set by eye on drawn walks, between the frames seen with a line doubled (a
    boot's top faded out where one source frame tilts it, beside the top the frame drew), which read
    over it, and the frames seen as clean or only soft, which read under it. It is a reference, not
    a measured optimum, and a frame near it is worth a look.
  - Read only on the frames the alignment makes (`doubled` in each `smear` row). The jump repair of
    `video-loop`, its eased retime and overlapped wrap (section 2) do not read it, and neither do
    the versioned measures of `video-loop-compare` and `video-loop-repair`.
  - Synthetic numbers (`tests/video/test_inbetween_doubled.py`): a figure on a 200 by 260 px canvas,
    a coat over two legs in boots, outlined **3 px** in near-black, a hand beside the coat; the near
    boot lifted 18 px in the first source frame and planted in the second. The boot's line is 0.69
    darker than the boot's fill. The frame drawn between, with the first source frame's near-boot
    lines laid over it at 0.45 of their strength: `outline_loss` 0, `outline_shift` 0, `ghost` 0,
    `hole` 0, `crossfade` 0, no fault before; `doubled` 0.102 (the run's own length is the boot's
    width). Resampled from two frames to four under `auto`, both made cells (1 and 3) take the
    nearer source frame; under `rife` they are kept, named. The same with both source frames' lines
    at half, and the frame whose colour is the plain blend of the two inside the drawing's coverage,
    read over the line too. A crease the first source frame draws across the still near boot and the
    second does not: left at 0.3, 0.45 or 0.6 of its contrast it reads `doubled` and gives way; left
    at 0.25 it is lighter than 50/255 under the boot and reads 0; the frame that draws it, and the
    frame that does not, read 0 and keep their bytes. Read 0 and delivered as the very frame made:
    the drawing between; the same blurred (radius 1.5 px) or smeared along the step; a hand moving
    60 px over the coat, drawn, blurred or smeared along its way (t 0.35 to 0.65); boots both source
    frames draw with a second line; a hem both draw with two lines, softened to 60 % in the frame
    between; a light crease one source frame draws, carried at its own strength. The bounds, where
    each turns: left at 0.28 of its contrast the made frame there is 0.1960 darker than the boot,
    just under 50/255, which is 0.1961, and reads 0; at 0.30 it is 0.2076 darker and is read; at
    0.80 it reads, at 0.82 it is the source frame's own line and reads 0; with the boot moved 8 px,
    the pixels of its old line that the reading keeps are too thin to hold a 3 px square, so the
    opening by 3 px removes them and it reads 0; moved 9 px they hold one and it reads (0.092); a
    dark patch 10 px square one source frame draws is no line, a stroke **3 px** wide is; left over
    6 columns the run is 0.0294 of the body, under the line, over 7 columns 0.0343, over it. What it
    does not tell: the hand smeared along its way from t 0.3 to 0.7 of its 60 px move reaches back
    over the line the first source frame draws there and reads 0.063. The walker of
    `tests/video/test_crossfade.py`, legs closing from 16 to 3 degrees: its drawing between, which
    the cross-fade reading reads over its line in grey (the reason that reading is no fault), reads
    `doubled` 0 in grey and in black outlines and keeps its bytes; its colour blended inside the
    drawing's coverage reads 0.098 in grey and 0.37 in black and gives way. Legs closing from 10 to
    5 degrees, the cross-fade's lines left inside all three silhouettes run 0.0195 of the body (the
    rest of its doubled edge lies on the silhouette, where one source frame is not solid): under the
    line, kept.
- **What `auto` costs**: a frame replaced shows the nearer source frame, so the motion there
  steps as filmed, up to half a frame off its time, and the loop shows fewer distinct drawings a
  second than `rife` (but no melted one). A clip drawn on twos has a long step between drawings
  everywhere, so it is replaced most; the frames that land on a small step stay RIFE's. The legs are
  read for the beat only where a frame stands off its own time, costing nothing when every frame
  stands on time. For each frame given way in a loop whose beat broke, the engine tries every other
  fraction of `align.RETIME_T` (0.15 to 0.85 in steps of 0.05, fifteen fractions; the frame's own is
  not made again) whose place stands within `BEAT_JOLT` = 0.25 beat of the frame's own time, nearest
  its own fraction first, and makes each of those with RIFE. How many that is depends on how far the
  legs move between the two source frames against one beat: about ten where the pair's step is about
  one beat long, all fourteen others where it is much shorter than a beat. Fractions standing further
  off are not made. Where a spread is taken, frames beside a run are moved off their own times and
  made by RIFE. `--between-cache` keeps and reuses them like any made frame. The result: where the
  beat breaks, the alignment runs RIFE several times over for each frame given way, and takes longer;
  elsewhere it costs nothing more.
- **Held drawings — a take to film again** (`retake`, reason `held-drawings`): a loop whose
  clip shows each drawing for two or three frames has a gap between drawings two or three frames
  wide. Played at its own rate that is limited animation, 12 or 8 drawings a second. Made longer,
  or merely resampled, the frames in each gap are made across a step two or three times as long
  as an every-frame clip's, and where the legs swap places inside it — the near leg passing behind
  — no interpolator draws them: a flow model blends the two drawings into a ghost or a melted
  shape, which `auto` replaces with the nearer source frame, and that drawing is held a frame
  longer: the loop halts there. A better interpolator is not the fix; a take drawn every frame is.
  - **The hold is the cut's**: `video-loop` measures it on the clip's steps as keyed, before any
    anchor moves a frame, twice, and writes both into `strip.json` ([video
    pipeline](video-pipeline.md) section 4): over the cut (`cycle_drawings`: `start`, `length`,
    `steps`, `hold`, `drawings_per_second`, `contrast`, and a `why` where it was not read) and over
    the whole clip (`drawings`: `hold`, `drawings_per_second`, `contrast`, `frames`). The alignment
    reads the cut's (`source: "span"`, the cycle's drawings its frames at the cut's rate, the
    clip's reading beside it under `clip`): a clip held for part of its length and drawn every
    frame for the rest is held where it was cut, so a cycle cut from its every-frame part is not
    named because the clip reads as held, and one cut from its held part is not passed because the
    clip reads as drawn every frame. The cut's steps run from its first frame into the frame after
    its last, which the cycle returns to, so a cut that starts mid-pair reads as the pairs it holds.
    A cut of fewer than 12 steps (`held.SPAN_MIN_STEPS`, one window of the clip's reading) is not
    read on its own: the clip's reading stands (`source: "clip"`), and the row's `span_why` says
    why — as it does for a loop cut before the cut's record. Both are kept through a rebuild. The
    cut frames themselves are not read while a record exists: `--anchor motion-auto` shifts each,
    so a pair's repeat may no longer read as one; only a loop cut before either record is measured
    on its own cycle, read as a ring (`source: "cycle"`).
  - **The rule**: a loop is named when its cut is held (`hold` 2 or 3), the set's length leaves
    it under **13 drawings a second** (`align.RETAKE_DRAWINGS_MIN`), and **one or more** frames
    between its drawings were not made (`align.RETAKE_UNMADE_MIN`) — taken from the nearer source
    frame (`auto`'s replacements, a made ghost's among them, every made time under `nearest`) or
    made with a fault and kept (`rife`); a filmed ghost given way at a whole time is no frame
    between drawings left unmade, and is named in `ghost_at` instead. Each loop row carries `drawings` and `retake` (the record, or `null`): `reason`,
    `hold`, `source`, `drawings` (in the cycle), `drawings_per_second_filmed`,
    `drawings_per_second` (at the set's length), `frames_per_drawing` (the gap, in frames of the
    aligned loop), `unmade`, and the `limits`. The report's `retake` lists every such loop (`dir`,
    `name` and the same numbers), a warning line names it ("film this direction again
    (held-drawings) — …", by its directory where two loops share a strip name), and `video-set`
    carries both into its record.
  - **A filmed ghost with no clean frame beside it** (reason `filmed-ghost`): a ghost the screen
    names at a time whose source frames on both sides are ghosts too (three in a row at a whole
    time, two at a time between them) is delivered as filmed — no frame of the take shows the
    drawing there, and nothing but a new take fixes it. A loop with **one or more** such cells
    (`ghost_at` `kept: "no-clean-frame"`, `align.RETAKE_GHOST_KEPT_MIN`) is named whatever its
    hold; a ghost `--between rife` kept beside a clean frame (`between-rife`) is the choice of
    whoever aligned it and is not counted. Its numbers are under `ghost`: the cells `kept`, their
    frames of `cycle.source/` (`sources`), the highest reading (`ghost_max`) and the screen's
    `limit`, with `limits.ghost_kept_min`. Its line: "film this direction again (filmed-ghost) —
    frame(s) 5 show source frame(s) 10 of cycle.source/, filmed ghosts (…), and the source frames
    beside each are filmed ghosts too …".
  - **One record per loop**: `retake` is one object (or `null`). `reason` is the first reason that
    applies (order of reasons: `held-drawings`, `filmed-ghost`, `filmed-jump`, `seam-jump`,
    `broken-beat`, `same-foot`), and `reasons` lists every one; the held-drawings numbers (`hold`,
    `source`, `drawings`, `drawings_per_second_filmed`, `drawings_per_second`, `frames_per_drawing`,
    `unmade`) stand at the top level of the `retake` record, as before (only where `held-drawings`
    applies), details stand under each reason's snake_case key (`ghost` only where `filmed-ghost` does;
    `filmed_jump` with `steps`, `at`, `whole`, `legs`, `remedy`; `seam_jump` with `steps`, `at`, `whole`,
    `legs`, `remedy`; `broken_beat` with `jolt`, `gap`, `over`, `named`, `remedy`; `same_foot` with
    `order`, `twice`, `clip`, `remedy`), and `limits` carries each one's (`screen_jump`, `screen_alone`,
    `screen_rows` for `filmed-jump` and `seam-jump`; `beat_jolt` for `broken-beat`; `lift_min`,
    `lift_cells`, `down_max` for `same-foot`). `remedy` says what is likely to fix it: "retake" (film the
    direction again) where every cut of the clip that reads steps the same foot twice; "recut" (cut the
    loop again from the same clip) where some cut alternates; "recut-or-retake" for `seam-jump` (another
    cut of the clip may close on a pose it holds; a new take may not need the cut to close there), for
    `filmed-jump` (where the clip films another cycle without the jump, a cut of the same clip from there
    removes it; where it does not, a new take does), for a broken beat (another cut may land its times on
    frames RIFE can make; a new take may not need them made) and for the same foot where the clip's cuts
    were not read. Under `--between nearest` a broken beat is recorded in `beat` but is no reason (that
    choice takes the nearer frame on purpose). Each reason has one warning line, which replaces the
    earlier separate beat and same-foot lines, and the report's `retake` list carries these loops like
    the others. A held loop that also keeps a ghost reads `reason: "held-drawings"` with `filmed-ghost`
    among its `reasons`, with a line for each reason, one new take fixing both. Python callers:
    `align.retake(..., beat=None, feet=None, screen=None)`, `align.RETAKE_JUMP_REASON`,
    `align.RETAKE_SEAM_REASON`, `align.RETAKE_BEAT_REASON`, `align.RETAKE_FEET_REASON`, `align.REMEDIES`.
  - **Not named**: a held loop at its own length (nothing is made: on twos it plays at 12 a
    second, as filmed), a held loop whose made frames all kept their outline, a held loop squeezed
    to 13 drawings a second or more (its drawings come closer than on twos), a loop drawn every
    frame with a replaced frame whose beat does not break (one soft step, not a gap the take cannot
    fill; if replaced frames break the beat under `--between auto`, it is now named `broken-beat`),
    and under `--between nearest` a broken beat (recorded in `beat`, but no reason).
  - **Exit code and words** (both reasons): the set is still aligned and written (`applied: true`, exit 0) — the
    loop is the best this take allows, and the caller decides: a pipeline with a retake budget
    films that direction again and aligns the set anew; one without delivers it with the warning.
    Nothing passes quietly: the line is on stderr and in `warnings`, and `retake` is the
    machine-readable reason.
  - **Why 13**: a clip on twos shows 12 drawings a second. At that rate or slower each frame not
    made is a drawing held a frame longer at the moment the legs cross, which reads as a halt; a
    line a little above 12 keeps a cut that is not all pairs, or a cycle made a frame or two
    longer, on the same side. The lines are references, not measured optima, and a loop near them
    is worth a look either way.
- **Foot strike**: each loop is then turned to start as a heel lands (`align.foot_strike`), read
  off one signal smoothed 1-2-1 — never off the frame's top edge, which a long ear, a hat's
  point or an antenna owns and which flops on its own rhythm:
  - `stride` — a side or diagonal walk: the width of the foot band (the lowest 8 % of the frame)
    is widest as the front heel lands.
  - `reach` — a front or back walk, whose feet pass one behind the other: the foot nearer the
    viewer is drawn lowest, and lowest when the feet are furthest apart, so the lowest solid row is.
  - `body_low` — a body with no legs to read: its top line lowest, the top line being the first
    row whose longest solid run is at least half the frame's widest, so a narrow ear or antenna is
    passed over.

  With a view (`--view`, below) the view picks the signal, and the swings only say whether there
  are legs to read: a side or diagonal view turns on `stride` where the foot band swings by 15 %
  of the body's height or the lowest row by 1.5 %; a front or back view turns on `reach` where the
  lowest row swings by 1.5 %. A short-legged figure's swings mislead a threshold either way: from
  the front its foot band widens past 15 % as the foot behind lifts out of the band, mid-step, and
  from the side a short step opens the feet by less than 15 %. Without a view the picture alone
  says: `stride` where the foot band swings by 15 % or more, else `reach` where the lowest row
  swings by 1.5 %, else `body_low` (and the report says no view was given). A loop with a view and
  no legs to read has `foot_why` with both swings.

  A body that hardly bobs still turns on its legs; the body's bob is read only where there are
  no legs. The loop row says which (`turned_on`) and the two swings (`stride_swing`,
  `reach_swing`).
- **The same foot in every view** (`--view`, one per `--loop-dir` in order: `front`, `back`, or
  `side|front_diagonal|back_diagonal@right|left` for the way it faces; `video-set` passes each
  item's own): a walk lands twice a cycle, half a cycle apart. Each loop starts as the same own
  foot lands (`--start-foot`, default right), told apart by how the view draws the feet
  (`align.strike_foot`; which own side is where is `handedness.placement`):
  - front or back (`depth`): the feet are side by side in the picture, the one nearer the viewer
    drawn lower. From the front the landing foot is the one stepping toward the viewer, the
    lower; from the back it is the one stepping away, the higher.
  - side or diagonal (`shade`): the feet are one in front of the other, so the picture cannot
    place them, but the far leg is drawn in shade. At the strike where the front foot is the
    lighter against the back one, the near leg is in front.

  The cue must follow the step — its once-a-cycle swing (first harmonic) at least a quarter of
  its spread over the cycle, or it is the drawing's own flicker — and, averaged over each strike
  frame and its two neighbours, the two strikes must differ by 0.025 in luma (shade,
  `align.SHADE_MARGIN`) or 1 % of the body's height (depth, `align.FOOT_MARGIN`); otherwise the
  foot is not named and `foot_why` starts **`low-margin`** (with the margin and the bar). A view seen
  from behind a diagonal can shade its legs too evenly for this, and is then left unnamed rather than
  guessed. The shade's bar is set from drawn walks whose feet were checked by eye: it lies above the
  margins at which the shade named a foot wrong and under those at which it named one right. A foot
  named wrong turns the loop half a cycle off with nobody asked; a foot left unnamed costs one look
  (a person or a vision call, `unnamed_feet` below), so a margin near the bar is left for the look.
  A loop row carries `view`, `start_foot` and `foot` (the cue, the strikes, their values, the
  margin, the feet); a loop whose foot is not named starts on its larger strike with
  `start_foot: null` and `foot_why`, and the report's `warnings` say so — a set whose loops do
  not all name their foot may not start on one foot. Without `--view` no foot is named and the
  report says that once. A character drawn without shading on the far leg leaves its side and
  diagonal views unnamed; mirroring a loop afterwards (a left view made from a right one) swaps
  its own feet. Every loop row says who named its foot: `start_foot_source` `"engine"` (the view's
  cue), `"given"` (`--foot`, below) or `null` (nobody), and `strikes` — its two strikes as frames of
  the rebuilt `cycle/`, the larger first.
- **A foot the engine cannot name is told, one loop at a time**: each loop whose foot is not named
  — its view's cue does not follow the step, parts the strikes by less than the margin
  (`low-margin`), or there are no legs to read — is listed in the report's **`unnamed_feet`**:
  per loop `dir`, `name`, `view`, `foot_why`,
  `candidates` (the two strikes: `strike` 0 and 1, `frame` in `cycle/`, `path` to that frame; the
  first is the frame the loop now starts on, the second half a cycle on) and `settle` (the two
  `--foot` arguments). Whoever looks — a person, the agent, or a vision call shown the two frames —
  says which own foot lands on the first candidate, and that comes back as
  **`--foot <loop>=left|right`** (repeatable; `<loop>` is named as for `--cycles`: the `--loop-dir`,
  its strip's name, its directory's name, or a `video-set` item's name). That loop alone starts as
  `--start-foot` lands — on the first candidate if it is that foot, half a cycle on if it is the
  other — and every other loop comes out byte for byte as it would have. The row records
  `start_foot_source: "given"`, `foot_given` (the feet at the two strikes) and, where the engine had
  not named it, `foot_unnamed_why`; the report records `feet_given`. A foot given for a loop the
  view did name is still taken, and where it disagrees the row carries `foot_disagrees` (the cue's
  reading) and a warning says so. A `--foot` that names no loop, two loops, or something other than
  `left`/`right` is refused before anything is rewritten. The answer is about the loop as filmed —
  the strikes are read from `cycle.source/` resampled again — so it holds for every later alignment
  at the same length, and like `--cycles` it is not remembered: the next alignment needs the same
  `--foot`. The algorithm that names feet is not changed by it.
- **Rebuilt in place**: `cycle/`, `<name>.strip.png` / `.strip.json`, `.gif`, `.webp` are rewritten at the
  loop's own cell rules (`cell_height_cap`, body-height target, anchor), at the loop's frame rate, so the
  aligned cycle lasts L*/fps seconds. `strip.json` gains `cycle_align` (`from`, `to`, `between`, `taken`,
  `made_by_rife`, `made_at`, `smear` and/or `nearest_at`, `ghost_screen`, `ghost_at`, `drawings`, `beat`,
  `same_foot` (back and back_diagonal views), `screen`, `retake`, `turned_by`, `turned_on`, `view`,
  `start_foot`, `start_foot_source`, `strikes`, the seam ratio of the rebuilt cells, `cells_from`, `origin`,
  and the re-verified GIF/WebP).
- **Where every cell is from**: `cycle_align.cells_from` holds, per cell of the rebuilt strip,
  `{"source": i}` — frame i of `cycle.source/`, taken as filmed (a turn, a snap, or the nearer
  frame), as many times as it is taken — or `{"between": [i, j], "t": t}`, a frame made between
  two. The report's loop rows carry it too.
- **The cut as filmed is kept** in `cycle.source/` on the first alignment, and every later
  alignment reads from there: running it again, or at another length, never resamples a
  resampled loop. Every loop of the set is resampled before any is rewritten, so a loop that
  cannot be made leaves the set as it was. The first alignment also records the strip as cut,
  before anything is rewritten (`cycle_align.origin`: its file's sha256 — `follow.source.png`'s
  where a follow-through moved it —, cell size, scale, crop origin, `sample_indices`, each cell's
  pixels by sha256, and the body's motion as `video-sway` reads it there, `reading`), and every
  later alignment keeps it as it is. A loop aligned before this record existed has none, and gets
  none: its cut is no longer there to read. With `cells_from` it lets a follow-through whose
  regions were read on the cut be carried to the aligned loop (`video-sway --read-on`,
  docs/video-pipeline.md section 6). Neither record changes a pixel of the strip, GIF or WebP.
- **Frames between are kept for later passes** (`--between-cache DIR`): every frame RIFE makes between
  two source frames is stored in `DIR`. A later alignment given the same `DIR` takes a kept frame instead
  of making it again, such as when aligning again with `--foot` or after retaking one loop. A frame is
  taken only for identical inputs: it is keyed by the sha256 of the format, both source frames (which
  must be RGBA: mode, size, and pixel bytes; other modes are refused), the exact fraction `t`, and the
  interpolator identity. The identity is read again for every frame asked for, and nothing of it is
  remembered: the RIFE binary file and each file in the model directory, by sha256; the model directory's
  path as given to the binary (the binary reads the model's version from the directory name); the
  augmentation every call asks for at that moment (`rife.tta()`: `SPRITE_GEN_RIFE_TTA`, or the default
  `-x -z` when it is not set — section 1, "Which setting"), as another augmentation makes another frame;
  the engine code as loaded, which is the sha256 of `sprite_gen/video/rife.py` read once at import
  (`rife.py` written over on disk during a run changes no code that is running, so a frame made by the
  loaded code is kept under that code, and a later process that loads other code gets another key and
  makes the frames again); the NumPy and Pillow versions; and the host as `os.uname()` reports it at that
  moment (host name, OS release, build, and architecture), with the OS platform. With the torch backend
  (section 1) the binary's and the model's files give way to what the backend loaded and runs on, read
  once when it loads and kept for its life: the sha256 of the model bytes it read, its network code
  (`rife_torch.py`, read at import), PyTorch's version, the device, and on CUDA the GPU's name and
  capability, the driver, CUDA's and cuDNN's versions and the determinism settings as they read back, on
  the CPU its instruction set; the key names the backend either way, so a frame one backend made is never
  taken for the other's. On an executor (section 1, "A GPU elsewhere") that part is what the executor
  answered as the RIFE was made, held for its life, and the executor's name: an executor that changes
  under the RIFE fails it rather than make a frame of another identity. A binary or model file that cannot be read fails the alignment, as a RIFE that
  fails does. Any change in cycle length, `--cycles`, source pixels, binary, model, augmentation, engine
  code, or host makes the frame again. The key does not read the GPU driver or its environment; a caller
  that changes those between passes gives each pass its own directory. Reading the identity hashes 36.5
  MB of binary and model files in about 17 ms on an Apple M1: once for a frame taken, twice for a frame
  made (making a frame with RIFE takes on the order of a second). After a frame is made, its key is read
  again; if anything it is made from changed while it was being made, the frame is handed on but not
  kept, and counted as `not_kept`. Each kept frame is a PNG file named by its key, with its pixel sha256
  in a text chunk, written to a temporary file and renamed into place. A kept file that does not read
  back as that frame is made again, written over, and counted as `unreadable`. A taken frame is a new
  image of exactly the kept pixels, so the aligned loop is the same, byte for byte, as an alignment
  without the directory. Nothing is kept without `--between-cache` (there is no default directory). The
  caller gives each alignment its own directory and removes it when that alignment is done. The engine
  creates `DIR` mode 0700 and files mode 0600, never deletes `DIR`, and records `dir`, `hits`, `misses`,
  `unreadable`, and `not_kept` under `between_cache` in the summary and report.
- Loops at different frame rates are refused (one length in frames would mean different
  durations), and so is a loop cut before `cell_height_cap` was recorded (cut it again). So is a
  loop never aligned whose strip as cut is missing — its `<name>.strip.png`, or `follow.source.png`
  where a follow-through moved it: the first alignment reads `cycle_align.origin` there, and no
  later one could. It is named before any loop of the set is touched, `cycle.source/` included; put
  the strip back or cut the loop again. A loop aligned before keeps the origin it recorded and
  needs no strip.

`video-set` runs this after its loops are cut, once per walk or run state filmed in two or more
directions (`--align-cycles auto`, default; `off` keeps each loop's own length). Its report
carries `cycle_align` per state and `<state>.cycle-align.json`; a failed alignment is listed as
`cycle-align:<state>` and the loops stay as cut. Without RIFE the alignment is skipped, not
failed (section 1, "Without RIFE"); install it and run `video-cycle-align` on the set's loops.
A set stopped on a suspect is skipped the same way (`reason: "cycle-suspects"`, the loops under
`suspects`, a warning naming them): count their cycles and run `video-cycle-align --cycles`.
A state whose loops leave a foot unnamed carries them under `cycle_align.<state>.unnamed_feet`; the
answer goes back as `video-set --align-foot <item>=left|right` (the item's name, e.g. `side-walk`;
only an item of a walk or run filmed in two or more directions is taken, any other is refused before
filming), or as `video-cycle-align --foot` on the set's loops. A foot given for an item that failed,
or whose state was not aligned, is named in that state's `feet_unused` and a warning.
Cutting a loop again with `video-loop` removes its `cycle.source/`, so the next alignment reads
the new cut. An alignment also clears a follow-through (`video-sway`, which moved the old
cells): `follow.source.png` and the strip's `follow` record are removed and the loop's row says
`follow_cleared`; run `video-sway` again after it, with `--read-on` naming the strip the regions
were read on.

How much RIFE that is, on the 2026-10-03 sets (the experiment's `finalize.py`): resampling to
the median made 18 of 21 frames for a Lite side loop of 27, 20 of 21 for a back-diagonal loop of
19, and none for the loop already 21 long; Pro, 22 of 24 for a front-diagonal loop of 34. The
final set showed a Lite back-diagonal walk that jumped; seen at three stages (as filmed, jumps
repaired, aligned), it jumped at all three, so the take — not the resampling — was the cause
(maintainer's judgement, 2026-10-03).

## 5. Regression — the 2026-10-03 eight-direction set through this engine

The kept picks of both sets (Pro and Lite, five views each) and the refused Lite side take, from
their keyed frames through `video-loop --state walk --anchor motion-auto --body-height 400
--strip-height 640` and then `video-cycle-align` per set, on an M4 Max with the Apple GPU. The
experiment's own scripts (`retime.py`, `finalize.py`) are the reference. This is the 2.17 engine,
whose jump repair picked its frames by the size reading (section 2, the end of its rules): the
"Frames repaired" and "Jump score" columns are that reading's. The flow reading has not been run on
this set.

| | Cut (start, length) | Frames repaired | Jump score before → after | Jolt index | Head sideways step, % | Warning |
|---|---|---|---|---|---|---|
| Pro front | 26, 23 (same) | [9] (same) | 1.42 → 1.37 | 0.221 | 0.51 | — |
| Pro front diagonal | 1, 34 (same) | [0, 10] (experiment [0, 11]) | 2.00 → 1.66 | 0.079 | 0.18 | — |
| Pro side | 23, 24 (same) | [3, 15, 19] (same) | 1.81 → 1.64 | 0.244 | 0.44 | — |
| Pro back diagonal | 17, 24 (same) | [9] (same) | 2.53 → 2.08 | 0.4225 | 0.19 | — |
| Pro back | 7, 20 (same) | [6, 12] (same) | 1.72 → 1.44 | 0.245 | 0.42 | — |
| Lite front | 4, 25 (same) | [0, 11] (experiment [0, 11, 14]) | 1.69 → 1.38 | 0.230 | 0.50 | — |
| Lite front diagonal | 19, 21 (same) | [8, 18] (same) | 1.42 → 1.33 | 0.175 | 0.69 | — |
| Lite side | 25, 27 (same) | none (same) | 1.35 | 0.055 | 0.33 | — |
| Lite back diagonal (refused: "it jumps") | 30, 19 (same) | [13, 0] (same) | 1.60 → 1.42 | 0.192 | **0.82** | head sideways 0.82 % |
| Lite back | 50, 16 (same) | [15, 3] (same) | 1.82 → 1.50 | 0.163 | 0.57 | — |
| Lite side, refused take | 39, 23 (same) | [0, 16, 8] (same) | 1.72 → 1.42 | 0.238 | 0.28 | — |

| Set | Lengths as cut | One cycle | Frames made by RIFE (per view) | Experiment |
|---|---|---|---|---|
| Pro | 23, 34, 24, 24, 20 | 24 | 65 (23, 22, 0, 0, 20) | 24; 23, 22, 0, 0, 20 |
| Lite | 25, 21, 27, 19, 16 | 21 | 78 (20, 0, 18, 20, 20) | 21; 20, 0, 18, 20, 20 |

- Every cut is the one the 2.17 engine made; the set lengths and the frames RIFE made match the
  experiment view by view.
- The repair replaces the same frames as the experiment in 8 of 10 views. The two that differ are
  read at full size here and on the scaled-down strip there; both differences are one frame.
- At the reference bounds the only warning is the Lite back-diagonal take the maintainer refused —
  for its head, 0.82 % sideways in one frame.
- About 12–17 s a loop (mostly the motion analysis) and about 1 s per frame RIFE made.

A side-by-side video of the takes as filmed and as this engine leaves them, the numbers, and the
scripts that made them are kept with the experiment's material in the maintainer's notes.

## 6. The redrawn wrap: a pinned clip whose last frame the model drew again

When `video-loop --cycle pinned` cuts a clip generated with its first frame given to the video model as the last frame too, the cut retains every frame except the last. The pin gate checks that the last frame lands near the first on a small thumbnail and passes, because the pinned last frame holds the first frame's pose. The video model draws that frame again: softer or sharper, in another tone, or with hair strands a pixel over. Because the redraw remains, the wrap from the last cell back to the first shows this redraw as a single jump in sharpness, whereas every other step in the loop shows motion.

The wrap blend (`video-loop --wrap-blend auto`, the default) takes the first frame's drawing back over the cut's last half second, applying only where the two ends differ by a redraw. Passing `--wrap-blend off` cuts as filmed, but the seam bridge (section 2) can still make frames round the wrap of a loop that is not a walk or run; for such a loop, pass `--repair off --wrap-blend off` for the earlier bytes. The step is implemented in `sprite_gen/video/wrap_blend.py` (`measure`, `gate`, `blend`, `apply`) and called from `loop.run_loop`. It runs on the frames as filmed after the anchor and after `source_projection`, before any repair reads the wrap, and before `cycle/`, the strip, the GIF and the WebP are written.

| Rule | Value | Why |
|---|---|---|
| When | `--cycle pinned`, `--anchor none`, the pin gate passes, `--wrap-blend auto` | Idle and attack clips that `video-set` films pinned are cut this way. An anchor that moves the cells (`--anchor body`) puts them off the place the pinned frame holds, so they are not blended and the report says why. A pin the gate refuses fails the clip whatever its wrap shows, so it is not tried (the cut is written as filmed and the failure message is unchanged). Other cuts (auto, periodic, fixed, one-shot, once, once-return, lifetime) are not read and their report has no `wrap_blend`. |
| The ruler (`measure`) | The first cell against the pinned last frame (the pinned frame cleaned as the cut's frames are: RGB under alpha 0 scrubbed, detached specks dropped), at full size, over the first cell's opaque inside (alpha 250 and up, eroded 3 px), composited on a dark ground (RGB 20, 16, 28), the mean of the largest channel difference; divided by the cut's median step read the same way (the wrap left out). This is the residual ratio. | The difference between the two drawings of one pose is the redraw itself; a sharpness measure such as the variance of the Laplacian does not separate this jump from the clip's own frame to frame changes in sharpness. The ruler reads one frame at a time, so a long cut at full size is never held whole. |
| Not a redraw | Residual ratio at most `--seam-max` (default 2.0) | Nothing is done, the bytes are the same as `off`, and `wrap_blend.why` says so, because a pin that draws the first frame as it is needs nothing. |
| The ramp | The last K cells, K = min(round(0.5 s x fps), floor(L / 4)) for a cut of L cells (12 at 24 fps); K under 2: nothing | Half a second takes the jump over a dozen ordinary steps; a short cut keeps three quarters of itself as filmed. |
| The shape (`blend`) | The residual ramp: the difference e = first cell minus pinned frame, premultiplied RGBA, is added to the ramp cells: j/(K+1) of e on the j-th ramp cell (j = 1..K). Per pixel the ramp starts from the first ramp cell on which the pixel stands through to the end, and ramps over the cells left. | Each pose stays the cell's own; its drawing turns into the first cell's by the wrap. Mixing the ramp cells toward the first cell moves the pose as well as the drawing, producing ghost copies of moving parts. |
| What the two ends hold apart (`gate`, the two ends) | Never ramped: a pixel is held where (a) after one softness match for the clip, the values one end holds lie more than 12 levels outside what the other end holds within 1 px, on a 7x7 mean or along a 7 px run (across, down or along either diagonal), over black or over white; or (b) presence: a value one end holds lies more than 12 outside the other end's 1 px range while the other end's own 1 px range is under half (kappa 0.5) of this end's, in any of seven channels (premultiplied RGBA and RGB over white), raw values, no window. The softness match (`equalize`) blurs the sharper end by 0, 0.35, 0.5 or 0.7 px, whichever brings the two ends nearest over the figure. | A ramp of the difference draws whatever the two ends hold in different places a second time. A redraw (a line drawn softer or half a pixel over) keeps a share of its range in the other frame; a line, a dot or a part moved 2 px or more does not, whatever its direction, length, spacing or width. The presence test has no window and no list of directions because an earlier gate with only (a) let a 1 px line at slope 1/3 through, thinning under 12 in the 7x7 mean and in every one of the four runs, and drew it twice. |
| Over black and over white | Gate evaluated over both black and white grounds | A sprite lands on any ground. Over black alone, a silhouette's transparent neighbour (0, 0, 0, 0) puts every dark value in range: a dark strand against the silhouette that moved out on the transparent ground would be drawn twice. |
| Ramp cell standing where pin stands (`gate`, each ramp cell) | Cell 7x7 mean difference to the pinned frame is within 1.5 x the redraw's + 2, and tests (a) without the softness match and (b) hold against the pinned frame; a pixel that does not stand on a ramp cell is not ramped on it | A part that is still moving inside the last half second (an arm coming down, a blink) is the filmed part. |
| Widen and feather | What is held is widened 3 px and the weight is feathered with a 7x7 mean; a pixel whose weight is 0 is the filmed pixel, byte for byte | The 3 px widening is exactly the feather's reach (a 7x7 mean reaches 3 px), so a pixel the gate holds keeps weight 0 under the feather and no ramp seeps into it; the feather then lets the ramp fade out at the edge of what is held instead of stopping in a hard edge. |
| The after check | The blend is used only where every step into and through the ramp is within the larger of the cut's own largest step and `--seam-max` x the median step, and the wrap shrinks. Otherwise the cut is written as filmed, `wrap_blend.after_check` is "refused", and a `video-loop: warning:` line says the wrap blend was not used, why, with the numbers. | A blend that steps more than the cut does trades the wrap's jump for a jump inside the loop. |
| Wrap still over `--seam-max` after blend | Blended, and a `warning:` line says so with the share of the figure held as filmed | The blend passed the after check (no step past the cut's own, the wrap smaller), so it is kept; what the two ends hold apart is filmed and still jumps at the wrap, which the line names so a person looks at the wrap. |
| Order in `video-loop` | After the anchor and after `source_projection` (which records the cut before the blend), on the frames as filmed, before any repair reads the wrap, and before `cycle/`, the strip, the GIF and the WebP are written | The blend reads where each cell stands against the pinned frame, so it runs on the frames as filmed, after the anchor has placed them; `source_projection` keeps the record of the cut before it; any repair that reads the wrap then reads the wrap the blend leaves, not the redraw's jump; `cycle/`, the strip, the GIF and the WebP all carry the same blended cells. |

Mixing the ramp cells toward the first cell (the conventional wrap blend) moves the pose as well as the drawing, so a part that moves inside the ramp receives a translucent second copy. On a synthetic attack whose arm is raised until four frames before the pin, mixing toward the first cell lays a resting-arm ghost over the raised arm (128 levels from the ideal frame), and a synthetic eye blink near the end gives an eye ghost (133 levels). Adding only the residual difference e (first cell minus pinned frame) as j/(K+1) ensures each pose stays the cell's own while its drawing turns into the first frame's drawing by the wrap.

The test character in `tests/video/test_wrap_blend.py` is a synthetic pinned idle of 73 frames (72 cells and the pin, K = 12 at 24 fps), 160 x 224, whose frames after the first are the model's drawing of it (another cloth texture, hair strands up to 0.6 px over, 0.5 px softer, another tone), so the pin, the ruler and the after check pass and the gate alone stands between a structure on it and the ramp. A flat figure has no redraw, so its cases are read on the gate and the ramp alone.

| Case | Reading |
|---|---|
| Tone and 0.5 px softness redrawn, the character breathing 6 px | residual 7.24x the median step; wrap 7.31x to 0.57x; largest ramp step 1.56x against the bound 2.0x; only the 12 ramp cells, the strip, the GIF and the WebP differ from `off` |
| The whole redraw, with a 1 px line of contrast 24 at slope 1/3 moved 8 px on a flat patch of the torso | wrap 19.90x to 4.26x; the line's pixels are the filmed bytes |
| An arm raised until four frames before the pin | where the arm is 40 degrees or more from the pin's it is the filmed arm; on the last cell (20 degrees off) its fringe changes by 1 level at most |
| A 14x14 part moved 2 to 64 px on the flat figure, or only in the first frame, or only from frame 6 | byte for byte |
| A 10x10 part moved 2 to 32 px on the redrawn character, or only in one end | byte for byte, the rest blended |
| The 14x14 part (contrast 120) moved 16 px on the flat figure, nothing else redrawn | residual 2.34x; the gate holds the part, the ramp does not shrink the wrap: cut as filmed, warning line |
| A 1 px line of contrast 24, 48, 96 moved 2, 8, 32 px on the flat figure | byte for byte |
| Stripes every 5 px redrawn 0.8 px over and swinging 3 px inside the ramp | the swing is filmed |
| A dark strand against the silhouette moved 7 px out on the transparent ground | byte for byte; over black alone it would be ramped |
| Dots of 1 to 3 px, dotted lines, a ring, a wave, a cross, an anti-aliased, a light and a one-channel line moved 8 px on a flat patch, a line only in the first frame or only from frame 6 | 2 levels at most (0 for most; 1 for the ring and the wave; 2 for the anti-aliased line) |
| The slope 1/3 line over decode noise of 4 levels on every frame, a 4 level/px gradient, the widest softness match (0.7 px), static grain of 3, a 1 px checker of plus or minus 4 | 3 levels at most (0 or 1 measured); with the noise the median step rises and the ruler alone keeps that clip as filmed (residual 1.44x) |
| A strand on the transparent ground off the silhouette moved 3 and 8 px | byte for byte |
| The character sliding into the model's drawing over the whole clip (each step a 72nd of the redraw) | largest ramp step 6.66x against the bound 3.37x: not used, cut as filmed, warning line |
| The idle-still fixture, whose pin draws the first frame as it is | residual 0.0x: nothing done, every output the same bytes |

Cost: On a synthetic cut of 145 frames at 800 x 1120 (the test character scaled 5x), on an Apple M4 Max under a 1-minute load of about 15: the whole step takes 6.3 to 7.1 s, of which the ruler (every step of the cut at full size) takes 2.9 s. The pinned frame's grounds and 1 px ranges are computed once a clip; the K ramp cells are held as float RGBA (15 MB each at that size), one 7-channel ground and its 1 px range taking three 26 MB arrays a frame. No interpolator is called.

Limits (what the gate reads as a redraw, and so ramps):
1. A structure of contrast 12 or less, or one that does not stand 12 above its ground's own 1 px range: a faint part of contrast 8 changes by 7, of 11 by 10; a contrast 12 line by 11; on a 1 px checker of plus or minus 6 a contrast 24 line by 9 and a dotted line by 22.
2. A move under 1 px in both x and y: a 14x14 part moved 1 px changes its edge column by 111; a 45 degree line moved (1, -1) by 22.
3. Masking: where the other end has drawing of its own within 1 px of at least half this end's range (texture, an edge, a crossing structure): a 1 px line moved on the textured cloth keeps up to 36 levels (contrast 24), 7 (48), 3 (96), mostly hidden in the texture.
4. Steep gradients: on 8 levels/px a contrast 24 line changes by 5 to 9, on 12 levels/px by 22.
5. Noise only on the frames after a clean first frame (6 levels): 8 to 10 on a line. The pipeline does not make this case: the cut's first frame is the clip's own decoded first frame.
6. The softness match blurs 0.7 px at most: a line moved on a redraw 1.0 px softer than the first frame keeps 8.6 (contrast 24), 1.9 (48), 0.6 (96).
7. Redrawn hair strands are held by the presence test, so the wrap shrinks less than a ramp of everything would: on the synthetic idle the wrap ends at 7.35x where ramping all but the window test's holds would end at 2.2x.

The loop report records `wrap_blend` on every pinned cut: `applied`, `why`, `K`, `cells` (the cells changed), `residual_ratio`, `wrap_ratio_before`, `wrap_ratio_after`, `ramp_step_max_ratio`, `bound_ratio` (all over the median step), `pose_share` (the share of the figure the two ends hold apart), `presence_share` (of the figure, what the presence test holds), `never_settled` (the share of the figure that never stands where the pin stands in the ramp), `equalize` (`sigma`, `blurred`: "still", "pin" or "none"), `after_check` ("passed" or "refused"), and `seconds`. Where it was not tried, `applied` is false and `why` records the reason ("--wrap-blend off", "--anchor body moves the cells off the place the pinned frame holds", or "the pin does not close: the clip is refused on it"). Other cuts (auto, periodic, fixed, one-shot, once, once-return, lifetime) are not read and their report has no `wrap_blend`. Strip metadata (`<name>.strip.json`) carries `wrap_blend: {cells, K}` where it applied, and the CLI summary carries `wrap_blend` (`applied`, `why`, `residual_ratio`, `wrap_ratio_before`, `wrap_ratio_after`). Because `source_projection` records the cut before the blend, reading a strip whose cells were changed by the blend is reported as processing that cannot be reproduced (`source-processing-unsupported`).

A `video-loop: warning:` line is emitted under two conditions. When the after check refuses the blend because a step into or through the ramp exceeds the bound or the wrap does not shrink, the cut is written as filmed, `wrap_blend.after_check` is set to "refused", and the warning states that the wrap blend was not used, why, and the measured numbers. When the wrap remains over `--seam-max` after blending because the two ends hold a part apart, the blend is applied anyway and a warning line reports that condition along with the share of the figure held as filmed.

## 7. Held cells: a picture shown again, and a cut at a higher rate

A clip can show one frame two or three times in a row: a video model that stalls, a rate carried to another by
repeating frames (20 fps carried at 24 repeats one frame in six), or a cut mapped to a higher rate by taking the
nearer frame. Played, the motion stops on that picture and jumps on. `video-loop` reads every cut it makes for held
cells (loops that are periodic, fixed, or pinned; one-shot, once, and once-return cuts; and so every cut `video-set`
makes and every clip of `video` cut through `video-frames` and `video-loop`). By default, it makes those cells again
as the frames between the pose before them and the pose after (`sprite_gen/video/held_fill.py`). The default step
of the pipeline is: find the held cells, fill them, read the cut again; the cut's line to the person is its poses
over its cells.

### The reading

| Rule | Value | Why |
|---|---|---|
| The same picture | A step where at most `CHANGED_MAX` 0.5 % of the subject's pixels change by more than `PIXEL_SAME` 0.1 (premultiplied RGBA). This is read twice: on the analysis thumbnail used to choose the cut (96 px on the longer side) and on the frames as cut. It is the same picture only where both readings say so. | A textured synthetic figure, each pose shown three times, is encoded by libx264 at crf 18, 23 and 28 and keyed by `video-frames`. A frame shown again changes no thumbnail pixel by more than 0.008, 0.020 and 0.051, and changes 0.012 %, 0.049 % and 0.11 % of the frame's subject pixels by more than 0.1. An arm 8 px thick on a 120 x 140 figure grown by one pixel changes 1.2 % of the thumbnail's subject pixels over 0.1 and 0.38 % of the frame's: the thumbnail separates it. |
| Not a distance over the mass | `nonloop.STABLE_MAX` (0.025), the once cut's settled end, is not the rule | The arm's pixel reads 0.003 of the subject's mass: under it, so the rule would call it held. |
| Where read | On the cut's frames as loaded, before an anchor moves each frame apart, and again on the cells delivered | An anchor shifts each frame of a pair by its own amount: a picture shown again would no longer read as one. |
| Not read across the wrap | A loop's last cell and its first are not a run | The wrap is the seam gate's, not a hold. |
| A move under a thumbnail pixel | A whole figure that moves less than a thumbnail pixel is read on the frame as cut. It is not the same picture. A small part moved a pixel or so reads as the same picture on both. | A synthetic walker moving away in a frame 280 px wide changes 4.9 % of the frame's subject pixels over 0.1 in a step that changes none of the thumbnail's. Two or three pixels of a hand on a large frame play as a hold. Made again between its neighbours, it plays the same. |

The record, `held_fill` in the loop report and in the strip metadata (the strip's without each try): `cells`,
`poses` (the cells less the ones showing the picture before them) and `held_runs` (each run's first cell and how
many) of the cells as delivered; `before`, the same of the cut as filmed with each run `filled` or `why` not (and
the `faults` its frames between were refused for); `made`, the cells made; `unmade`, the cells left for want of
RIFE with the install line; `out_fps`, the rate asked (below). `video-loop` prints `held_fill.poses_cells` (`27/27`)
and `before` in its summary; `video-set` puts `loop.poses` on each item. In the curation view (a loop's `cycle/`
imported with `unpack-atlas --pngs-dir`), the card of each cell made says `(made)` and of each cell still showing the
picture before it `(held)`. A stop (`video-stop`) is read the same way and records `held_fill` with `mode` off: it
plays the clip's filmed frames, none made between two others, and names a run it shows in a warning.

### The fill: `--fill-held auto` (default), `off`

| Rule | Value | Why |
|---|---|---|
| A run's cells made | Cell c of a run from cell a to the next pose b, at t = (c - a)/(b - a) between a and b: a run of two half way, of three a third and two thirds | The cut keeps its length, its beat, its first and last cells and its wrap: a made cell stands where a held cell stood, on its own time. |
| Kept: a hold the motion means | A run longer than `HOLD_MAX` 3 cells | A drawing held on threes is three frames ([held.py](../sprite_gen/video/held.py)); longer is a pause the motion makes. A shorter pause meant as one is filled: `--fill-held off` keeps it. |
| Kept: a cut drawn on twos or threes (`drawn`) | Every run, where the cut's held-drawings reading (`cycle_drawings`, else the clip's `drawings`: [held.py](../sprite_gen/video/held.py), which `video-cycle-align` reads) gives a hold of 2 or 3 and every run is that many cells, but one cut short at the cut's first or last cell | Limited animation: each drawing shown as long as the next, played at its own rate as filmed (section 4). A clip carried to a higher rate by repeating frames reads a hold too (a synthetic walk at 24 fps carried to 60 by the frame under each time reads a hold of 2), but its runs are two and three cells by turns: it is filled (60 poses over 60 cells). A clip that stalls now and then reads no hold (hold 1). |
| Kept: the ends of a cut played once | A run that opens or closes a one-shot, once or once-return cut | The rest before the action and the pose it ends on are the cut's. |
| Kept: a loop's last cells | A run that ends on a loop's last cell | Its next pose is across the wrap: made, the last cell would change and with it the wrap. A run that opens a loop is made (its first cell stays). A periodic cut chooses its wrap where the steps either side read alike, so a held run seldom closes it; a fixed cut whose last cells are held keeps them, and its wrap then steps from a held picture (cut it elsewhere, or `--fill-held off` to keep the whole cut as filmed). |
| A frame made, judged | `align.measure_made` and `interpolation_quality.faults` (smear, outline, ghost, hole, doubled), the alignment's own | One acceptance policy for every frame RIFE makes (section 4). |
| A part standing still, kept still (`still_kept`) | The outline pixels both frames draw in one place in one colour (solid at alpha 128, within 24 levels) must be drawn there in the frame made, within a pixel, for at least `STILL_KEPT_MIN` 0.8 of them in every window a tenth of the body high that holds 12 or more; else the fault `contact` | A foot planted or a hand on a ledge stays where it stood. A synthetic in-between that drags a planted foot 3 px along the floor keeps 0.50 of its window; one off by a pixel, 1.00. |
| A picture of its own (`same_picture`) | The frame made must not read as the same picture (the reading above) as either frame it is made between or as the cell delivered before it (in a run of three, at `--out-fps` or in an air retimed with `--air-retime`, that can be a frame made too); else the fault `held` | An interpolator that gives back a frame it was handed, or one picture at two times, makes no frame between. Taken, the cell would be counted as made and still show the picture beside it. On a synthetic stalled walk (runs of two and three cells) whose interpolator gives back the frame before, both runs are kept as filmed (24/27 poses/cells). Each first cell is tried at its own time and then only later within half a cell (0.5 to 0.7, and 0.3333 to 0.45). `warning:` lines name cells 5-6 and 14-16. A synthetic fall that sinks less than a pixel in half a frame shows the frame before it again at 0.5 and 0.55, and is made at 0.6. |
| A frame refused | Made again at the fraction of `RETIME_T` nearest its own within half a cell of it, its ghost band under `RETIME_WINDOW_MAX` (section 4's bar for a frame made off its time). One refused as `held` is made again only on the side away from the picture it showed (later where it showed the frame before or the cell before it, earlier where it showed the frame after), and not again where it showed both. Else the run is kept as filmed, with a warning naming the cells and the faults | A frame half way that RIFE cannot draw is often clean nearer one side. The frames between run from one pose to the next, so a fraction nearer a picture a frame showed again shows it too. On the cuts of eight tests that reach the binary through the default fill, trying every fraction within half a cell ran the binary 214 times. The side rule runs it 124 times and makes the same cells (3.10.0, which took such frames, ran it 90 times). |
| Whole or not at all | A run is made whole or kept whole | Half made, it would step back to the held picture and on again. |
| Order | After the anchor and `source_projection`, before the ghost screen, the wrap blend, the part pop and the jump repair | Everything that reads the flow and the wrap reads the cut as it will play. |
| No RIFE | The held cells are delivered as filmed, the cut passes, and a `warning:` line names the motion, the cells and `sprite-gen rife install`; `held_fill.unmade` records them | Never a failure, never silent. |
| `--fill-held off` | Cut as filmed, byte for byte as before; the reading is recorded all the same | |
| Byte for byte | A cut with no held cell is delivered as before: six synthetic cuts (a fixed and a periodic walk, a one-shot hop, a pinned swing, a once fall, a once-return swing) are pinned to their decoded pictures on the engine before the change (commit 886936e): every frame of the strip PNG, the GIF, the WebP and the `cycle/` cells, decoded to RGBA, with each frame's size and the frame count. The test no longer pins the file bytes because the same pixels are written as different PNG bytes on Linux and on macOS (zlib differs), though on each platform the engine before the change and this one still write identical files (`tests/video/test_held_fill.py`). | |

A cut whose cells were made is read by the seam gate on its rendered cells (`seam_measurement`), restoration from
the source frames (`video-loop-repair`) reads it as `source-processing-unsupported`, and `video-cycle-align` reads
its drawings on its own cells, not on the clip's hold.

### A rate above the clip's: `--out-fps F`

`video-loop --out-fps F` (and `video-set --out-fps F`) gives the cut at F, above the clip's `--fps`: every cell at a
time of its own, made by RIFE between the frames beside it, judged as the fill's frames are, never showing a frame
again. A loop has round(L x F / fps) cells over its cycle's seconds, at times k x L / cells; a cut played once runs
from its first frame to its last, round((L - 1) x F / fps) + 1 cells, each 1/F. A cell on a frame's time is that
frame; one between two frames showing one picture (a hold kept) is that picture; one no frame made is taken for (a
fault, or `held`: the frame shows the picture of a frame beside it, or of the cell before it, again) is the nearer
frame, listed in `out_fps.shown_again` and named in a `warning:` line. The record's `out_fps.source_frames` gives the
frame each cell is (null where it was made), which the strip record's `cell_record` reads for its `source_frame`
(`docs/video-pipeline.md` section 6). `cycle/` holds every cell; the strip holds them
up to its cell and width caps, subsampled past them as any strip is (`subsampled`). The strip metadata says `out_fps`
and `source_fps`, and `source_cut.times` in place of `samples`. With no RIFE the cut is delivered at its own rate with
a `warning:` line and `out_fps.applied` false. `video-cycle-align` and `video-stop` refuse such a loop (each makes its
own frames between from the cut at the clip's rate), and restoration reads it as `source-processing-unsupported`.

Where sprite-gen gives frames at a higher rate than it was given them:

| Path | Before | Now |
|---|---|---|
| `video-loop --gif-fps`, `--n-out` | The GIF and WebP never hold more frames than the cut (a higher request is capped at the cut's frames, never repeated) | The same; `--out-fps` makes the cells themselves at the higher rate |
| `video-cycle-align --length` | RIFE's frames between; a frame shown again only with `--between nearest`, or where a frame made is refused and the nearer source frame is taken (named) | Unchanged: it already makes frames between |
| `video-loop --out-fps` (new) | None | RIFE's frames between, never a frame shown again |
| `scene-render` at a scene rate above an asset's | A cell of the asset shown over several scene frames | Unchanged: give the asset at the scene's rate (`--out-fps`) |
| `video-stop --fps` | The rate of the frames given, not an output rate | Unchanged |

### What it takes from sprite-gen

| Part | Taken from | Why a part is its own |
|---|---|---|
| The thumbnail the steps are read on | `loop._small_features`, the analysis thumbnail the cut is chosen by | |
| What a drawing held on threes is | `held.HOLDS` | |
| Whether the cut is drawn on twos or threes | `held.measure_cycle` (`cycle_drawings`), `held.measure` (`drawings`) | |
| The judgement of a frame made | `align.measure_made`, `interpolation_quality.faults`, `looks` | |
| A frame made again at another fraction | `align.RETIME_T`, `align.RETIME_WINDOW_MAX` | |
| RIFE located only once a frame is made; no RIFE as a warning | The pattern of `_repair_jumps` and `_bridge_seam` | |
| The ghost screen's reading | `interpolation_quality.ghost_screen` | |
| Solid pixels | `legs.LOW_ALPHA` | |
| The window a tenth of the body high | `rife.GHOST_BAND` | |
| The same picture (`same_steps`) | New | A distance over the mass (`nonloop.STABLE_MAX`, `held.measure`'s steps) reads a one-pixel move of a small part as held; `held.measure` reads a rhythm of redrawn drawings, not a frame shown again. |
| Whether a frame made shows a picture of its own (`same_picture`, the fault `held`) | `same_steps`, the reading above, on each pair | |
| A part standing still (`still_kept`) | New | `rife.hole` reads a hole through a limb, `inspect-motion` a foot's place in spans a person marks; neither reads whether an in-between keeps what both its frames hold still. |
| The times of a higher rate (`times`, `upsample`) | New | `align.resample` resamples a loop as a ring to a length; a cut played once runs from its first frame to its last. |

## Related

- [video-pipeline.md](video-pipeline.md) — the pipeline these repairs run inside
- [loop-review.md](loop-review.md) — the decisions a loop records for review
- [docs/README.md](README.md) — documentation index
