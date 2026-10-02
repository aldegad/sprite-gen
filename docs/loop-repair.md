# Loop repair — RIFE in-betweens for jump frames, the jolt index, one cycle per set

> Owns: Where RIFE runs and what it costs, jump-frame repair in `video-loop`, the jolt index and its gate, cycle alignment across a direction set · Index: [docs/README.md](README.md)

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
tool, like `ffmpeg` and `img2webp`; nothing is vendored into the package.

| Release zip | SHA-256 (measured 2026-10-03) |
|---|---|
| `rife-ncnn-vulkan-20221029-ubuntu.zip` | `1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68` |
| `rife-ncnn-vulkan-20221029-macos.zip` | `4a63a1f3c9c715773c57d2ee51df1b315ed20cd6c63103e45c483ecc4400b595` |

The engine finds it by `SPRITE_GEN_RIFE` (the binary's path) or `rife-ncnn-vulkan` on `PATH`,
and the model by `SPRITE_GEN_RIFE_MODEL` or `rife-v4.6/` next to the binary. A loop that needs
a repair and finds no RIFE stops with the install line; it is never cut unrepaired in silence
(`--repair off` is the explicit way to skip it).

RIFE reads three colour channels and no alpha. A frame is therefore interpolated as two
images — its colour premultiplied over black, and its coverage as a grey image — and put back
together unpremultiplied (`sprite_gen/video/rife.py`). Coverage below 2/255 is dropped, so no
faint halo is invented around the body.

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

### Decision

**RIFE runs where the engine runs.** On a Mac that is the Apple GPU. In the app it is the
Modal job container itself, on CPU through llvmpipe — no GPU function and no second service:

```
apt_install("libvulkan1", "mesa-vulkan-drivers")    # the Vulkan loader + llvmpipe
curl -fsSL -o /tmp/rife.zip https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-ubuntu.zip
echo '1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68  /tmp/rife.zip' | sha256sum -c -
unzip -q /tmp/rife.zip -d /opt && ln -s /opt/rife-ncnn-vulkan-20221029-ubuntu/rife-ncnn-vulkan /usr/local/bin/
```

Cost on Modal (prices read from modal.com/pricing on 2026-10-03: CPU $0.0000131 per core per
second, memory $0.00000222 per GiB per second): one repaired frame is two calls, about 6 s; a
loop repairs at most three frames, so at most about 20 s of one container — well under a cent.
Aligning a set's cycles (section 4) makes more frames: about 6 s per frame RIFE makes, so a
view that needs 20 made frames adds about two minutes on Modal and a few seconds on a Mac.
That is the reason section 4 keeps every frame that lands on a source frame.

A PyTorch RIFE was not chosen: it would add a framework of hundreds of megabytes to the image
for the same model, and llvmpipe already gives the GPU's frame.

### Licences

| Part | Licence | Source (read 2026-10-03) |
|---|---|---|
| rife-ncnn-vulkan (code, release binaries) | MIT, © 2020 nihui | `LICENSE` in the repository and in the release zip |
| RIFE (the network) | MIT | github.com/hzwer/ECCV2022-RIFE; the authors add that they "respect the commercial behavior of other developers" |
| RIFE v4.x weights (Practical-RIFE) | MIT — "The content of these links is under the same MIT license as this project." | github.com/hzwer/Practical-RIFE |
| ncnn | BSD 3-Clause | github.com/Tencent/ncnn |

sprite-gen ships none of these. A deployment that bakes the release zip into an image ships
the zip's `LICENSE` with it.

## Related

- [video-pipeline.md](video-pipeline.md) — the pipeline these repairs run inside
- [loop-review.md](loop-review.md) — the decisions a loop records for review
- [docs/README.md](README.md) — documentation index
