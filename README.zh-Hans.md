# sprite-gen

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · **简体中文** · [Español](README.es.md) · [Français](README.fr.md)

用于生成干净的 2D 游戏精灵和动画图集的 Codex/Claude 技能，采用组件行流水线，并配有一个独立的 webview，用于在烘焙前审查、筛选和修正帧。

```text
sprite-request.json → layout guides + prompts → image-gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

## 输出示例

使用该技能生成并整理的精灵（`claudecy`、`howl`）：

<p>
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy 空闲" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy 奔跑" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy 成功" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy 说话" />
  <img src="docs/howl-idle.gif" width="110" alt="howl 空闲" />
  <img src="docs/howl-running.gif" width="110" alt="howl 奔跑" />
  <img src="docs/howl-success.gif" width="110" alt="howl 成功" />
</p>

## 筛选 webview

帧提取完成后，启动一个独立的本地 webview 进行审查——无需 Studio 或框架依赖，因此可在安装该技能的任何环境运行（Claude Code Desktop、Codex 应用、普通终端）。

![curation webview — characters](docs/demo-character.gif)

- **按状态并排对比** 帧，并可对单个帧进行**选择/拒绝**。
- **逐帧非破坏性变换**：拖拽=移动，滚轮=缩放，顶部手柄=旋转，左下角手柄=剪切。编辑会保存到 `curation.json` 侧车文件中——源 PNG 永远不会被重写，compose 步骤会以确定性方式烘焙结果。预览（CSS + canvas）和烘焙共用同一个仿射矩阵，因此你对齐的就是最终得到的效果。
- **实时预览** 按各状态的 fps 播放选中的帧。

### 等轴测地面网格

对于等轴测集合，webview 会从 `meta.json` 的 tile/anchor 覆盖地面网格，便于你使用剪切手柄将家具吸附到菱形轴线上。

![curation webview — isometric furniture](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### 语言

webview 支持英文和韩文。启动时传入 `--lang en|ko`，或使用应用内切换器：

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## 快速开始

```bash
# 0. 在全新的 virtualenv 中安装依赖（Pillow）
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. 从基础图像准备一次运行
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. 使用 image-gen 为每个状态生成一行图像，保存为 raw/<state>.png
# 3. 提取帧
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4.（可选）在 webview 中筛选帧
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. 烘焙运行时图集
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### 编辑已完成的图集

当只剩下合并后的图集时，重建可供 curator 使用的运行目录，再进行筛选并导出：

```bash
# 重建帧：显式 --grid、--manifest 矩形，或 alpha 自动检测（默认）
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# 筛选后，将修正结果烘焙回指定 PNG
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

输出默认到输入旁边可找到的 `<source>-curator` 文件夹。

完整的面向代理的工作流与契约见 [`SKILL.md`](SKILL.md)。

## 安装

在 Codex 技能安装器工作流中，将该仓库安装为根技能：

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

## 归属

组件行工作流受 Apache-2.0 许可的 `hatch-pet` 技能启发，但面向通用游戏精灵图集，不包含任何宠物包或宠物视觉素材。

## 许可证

Apache-2.0
