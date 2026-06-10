# sprite-gen

<p align="center">
  <img src="docs/claudecy-running.gif" width="160" alt="claudecy running" />
</p>

[English](README.md) · [한국어](README.ko.md) · **日本語** · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

コンポーネント行パイプラインでクリーンな2Dゲームスプライトとアニメーションアトラスを生成するためのCodex/Claudeスキル。焼き込み前にフレームをレビューし、キュレーションし、修正するためのスタンドアロンWebビューも提供します。

```text
sprite-request.json → レイアウトガイド + プロンプト → image-gen state rows
→ クロマアルファ → 連結成分 → 透過フレーム
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

## 出力例

このスキルで生成・キュレーションされたスプライト（`claudecy`、`howl`）:

<p>
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy アイドル" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy 走行" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy 成功" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy 会話" />
  <img src="docs/howl-idle.gif" width="110" alt="howl アイドル" />
  <img src="docs/howl-running.gif" width="110" alt="howl 走行" />
  <img src="docs/howl-success.gif" width="110" alt="howl 成功" />
</p>

## キュレーションWebビュー

フレームを抽出した後、スタンドアロンのローカルWebビューを起動してレビューできます。Studioやフレームワークへの依存はないため、スキルがインストールされていればどこでも動作します（Claude Code Desktop、Codexアプリ、通常のターミナル）。

![curation webview — characters](docs/demo-character.gif)

- 状態ごとにフレームを**並べて比較**し、個々のフレームを**選択 / 除外**できます。
- フレームごとの**非破壊トランスフォーム**: ドラッグ=移動、ホイール=拡大縮小、上側ハンドル=回転、左下ハンドル=せん断。編集結果は`curation.json`サイドカーに保存され、元のPNGは書き換えられず、compose段階で決定論的に焼き込まれます。プレビュー（CSS + キャンバス）と焼き込みでは同じアフィン行列を使用するため、見た目通りに反映されます。
- **ライブプレビュー**では、選択したフレームをその状態のfpsで再生します。

### アイソメトリック地面グリッド

アイソメトリックセットでは、Webビューが床グリッド（`meta.json`のタイル/アンカー）を重ねて表示し、せん断ハンドルで家具をダイアモンド軸にスナップできます。

![curation webview — isometric furniture](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### 言語

Webビューは英語と韓国語をサポートしています。起動時に`--lang en|ko`を指定するか、アプリ内トグルを使用してください。

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # または ko
```

## クイックスタート

```bash
# 0. 新しいvirtualenvに依存関係（Pillow）をインストール
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. ベース画像から実行ディレクトリを準備
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. image-genで状態ごとの1行画像を生成し、raw/<state>.pngとして保存
# 3. フレームを抽出
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. （任意）Webビューでフレームをキュレーション
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. ランタイム用アトラスを焼き込み
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### 完成したシートの編集

結合済みシートのみが残っている場合は、キュレーター用の実行ディレクトリを再構築してから、キュレーションしてエクスポートします。

```bash
# フレームを再構築: --grid、--manifestの矩形、またはアルファ自動検出（デフォルト）
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # 自動検出
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # 正確な矩形指定
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # 単体PNGセットをインポート

# キュレーション後、修正内容を名前付きPNGに焼き戻し
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

出力は既定で、入力の隣に見つけやすい`<source>-curator`フォルダとして作成されます。

エージェント向けの完全なワークフローと契約は [`SKILL.md`](SKILL.md) にあります。

## インストール

Codexスキルインストーラのワークフローから、このリポジトリをルートスキルとしてインストールします。

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

## 帰属

コンポーネント行のワークフローはApache-2.0ライセンスの`hatch-pet`スキルから着想を得ていますが、汎用ゲームスプライトアトラスを対象としており、ペットパッケージやペットのビジュアルアセットは含みません。

## ライセンス

Apache-2.0
