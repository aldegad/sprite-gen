<p align="center">
  <img src="docs/assets/claudecy-idle.gif" width="110" alt="claudecy idle" />
  <img src="docs/assets/claudecy-running.gif" width="110" alt="claudecy running" />
  <img src="docs/assets/claudecy-success.gif" width="110" alt="claudecy success" />
  <img src="docs/assets/claudecy-talking.gif" width="110" alt="claudecy talking" />
  <img src="docs/assets/howl-idle.gif" width="110" alt="howl idle" />
  <img src="docs/assets/howl-running.gif" width="110" alt="howl running" />
  <img src="docs/assets/howl-success.gif" width="110" alt="howl success" />
</p>

<h1 align="center">sprite-gen</h1>

<p align="center"><b>输入一张图。输出可直接用于游戏的精灵图集。</b></p>

<p align="center">

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

</p>

---

让图像模型生成一张“sprite sheet”，你很清楚会得到什么：每一帧脸都在变的角色、无法抠掉的背景、互相重叠并偏离网格的姿势，以及游戏引擎根本无法实际使用的 PNG。可爱的演示，没用的资产。

`sprite-gen` 是一个 Codex/Claude skill，用来补上这段缺口。给它**一张基础图像**和一组动作列表，它会逐行驱动生成、锁定角色身份、把色键背景剥离成真正的 alpha、将每个姿势提取为干净的透明帧，并烘焙出运行时图集，附带机器可读的 **`manifest.json.frame_layout`**。上面的每一个精灵都是这样制作的。

而对于生成永远做不准的最后 10%，这里还有一个**筛选 webview**：并排比较帧，拒绝坏掉的帧，以非破坏方式微调旋转/缩放/位置，实时观看循环，然后烘焙。流水线负责苦活；你保留审美判断。

```text
sprite-request.json → layout guides + prompts → sprite-gen gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

```mermaid
flowchart LR
    REQ["sprite-request.json<br/>(numeric SSoT)"] --> GUIDES["layout guides<br/>+ prompts"]
    GUIDES --> GEN["sprite-gen gen<br/>state row strips"]
    GEN --> EXTRACT["chroma alpha →<br/>connected components"]
    EXTRACT --> FRAMES["transparent frames"]
    FRAMES --> ATLAS["sprite-sheet-alpha.png<br/>+ manifest.json.frame_layout"]
    FRAMES -. "curation webview (optional)" .-> ATLAS
```

> 完整架构：[`docs/architecture.md`](docs/architecture.md)

## 你实际会得到什么

- **透明精灵图集**（`sprite-sheet-alpha.png`）——真正的 alpha，没有残留色键边缘，并已在白色背景上验证。
- **运行时 manifest**（`manifest.json.frame_layout`）——绝对帧矩形、每个 state 的 fps 和循环标记。你的引擎采样矩形；它不需要猜网格。
- **可观看的 QA** ——每个 state 的 GIF 和联系表，因此在交付任何内容之前，动作会作为动作来评判。
- **诚实的标签** ——短而可读的动作（idle、jump、attack、wave）是稳定路径；循环位移动作（walk/run）会标为实验性，除非动作 QA 确实通过。不会悄悄过度承诺。

## Chroma alpha 质量

提取器保持色键清理的确定性：soft-alpha unmix 会保留抗锯齿的发丝和细轮廓，而不是在覆盖率可被求解之前就把它们剥掉。

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-magenta.png" width="640" alt="full-body chroma comparison: illustration on magenta key" /><br />
  <em>插画，洋红色键：源图、v1.12.0 peel、v1.13.0 soft-alpha unmix。</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-green.png" width="640" alt="full-body chroma comparison: illustration on green key" /><br />
  <em>插画，绿色键：源图、v1.12.0 peel、v1.13.0 soft-alpha unmix。</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-magenta.png" width="640" alt="full-body chroma comparison: pixel art on magenta key" /><br />
  <em>像素画，洋红色键：源图、v1.12.0 peel、v1.13.0 二值化输出。</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-green.png" width="640" alt="full-body chroma comparison: pixel art on green key" /><br />
  <em>像素画，绿色键：源图、v1.12.0 peel、v1.13.0 二值化输出。</em>
</p>

下面的特写裁剪展示了全身对比背后的边缘细节。

![chroma peel 前后对比 — 插画发丝](docs/assets/chroma-peel-illustration-before-after.png)

![chroma peel 前后对比 — 像素画轮廓](docs/assets/chroma-peel-pixelart-before-after.png)

## 筛选 webview

生成能做到 90%。webview 是人类把它带到*可交付*状态的地方——独立运行，不依赖 Studio 或框架，skill 安装在哪里就能在哪里运行（Claude Code Desktop、Codex app、普通终端）。

![curation webview — characters](docs/assets/demo-character.gif)

- **每个 state 两行：**上方是**播放序列**，下方是**候选池**（例如第二次或第三次生成结果）。拖动帧的 ⠿ 把手来重排序列，或从候选池里把一个片段拉上来——从多次生成中挑选最佳帧，重建一个干净的奔跑循环。排列会被保存，因此重新打开时会恢复。
- **每帧非破坏式变换**：拖动 = 移动，滚轮 = 缩放，顶部把手 = 旋转，左下角 = 剪切，再加上用于左右反转输出的水平翻转开关。编辑保存在 `curation.json` sidecar 中——源 PNG 永远不会被重写，compose 步骤会确定性地烘焙结果。预览和烘焙共用同一个仿射矩阵，因此你对齐的就是你得到的。
- **实时预览**会以该 state 的 fps 播放序列，带有播放/暂停、逐帧步进，以及 0.25×–4× 速度控制。
- 不仅适用于精灵：用 `unpack_atlas_run.py --pngs-dir` 指向任意图像候选文件夹（图标、logo、生成草稿），就能把它当作通用的优胜者挑选视图。

### 等距地面网格

对于等距图集，webview 会叠加地板网格（来自 `meta.json` tile/anchor），这样你就可以使用剪切把手把家具对齐到菱形轴线上。

![curation webview — isometric furniture](docs/assets/demo-furniture.gif)

<img src="docs/assets/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### 语言

webview 随附英语和韩语。启动时传入 `--lang en|ko`，或使用应用内切换：

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## Python 支持

`sprite-gen` 支持 CPython 3.10+。CI 会在 GitHub-hosted runners 上运行最低支持版本（3.10）和最新覆盖版本（3.14）。

快速开始需要一个可正常使用 `venv`/`ensurepip` 的 Python 安装。如果本地发行版在安装包之前执行 `python3 -m venv` 失败，请使用任意受支持版本的标准 CPython 构建，然后重新运行相同命令。

## 快速开始

```bash
# 0. install dependencies (Pillow) into a fresh virtualenv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. prepare a run from a base image
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. generate one row image per state with the engine-owned provider CLI
python3 scripts/generate_sprite_image.py --provider codex \
  --prompt-file <run-dir>/prompts/<state>.txt \
  --out <run-dir>/raw/<state>.png \
  --ref <run-dir>/base-source.png \
  --ref <run-dir>/references/layout-guides/<state>.png
# 3. extract frames
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (optional) curate frames in the webview
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. bake the runtime atlas
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### 编辑已完成的 sheet

当只剩下合并后的 sheet 时，重建一个可供 curator 使用的 run dir，然后筛选并导出：

```bash
# rebuild frames: explicit --grid, --manifest rectangles, or alpha auto-detect (default)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# after curating, bake corrections back to named PNGs
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

输出默认会放在输入旁边一个容易找到的 `<source>-curator` 文件夹中。

完整的面向 agent 的工作流和契约位于 [`SKILL.md`](SKILL.md)。

## 安装

在 Codex skill installer 工作流中，将此仓库作为 root skill 安装：

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

### 图像生成所有权

由 provider 支持的生成属于此引擎（`sprite_gen.gen`）的一部分，支持的 provider 为
`codex` 和 `grok`。通用 `image-gen` skill 只是通往同一命令的一层薄转发，因此不需要第二套 provider
实现。有关 CLI 和验证
契约，请参阅 [`docs/gen.md`](docs/gen.md)。

## 归属

component-row 工作流受 Apache-2.0 许可的 `hatch-pet` skill 启发，但目标是通用游戏精灵图集，并且不包含任何宠物包或宠物视觉资产。

## 许可证

Apache-2.0