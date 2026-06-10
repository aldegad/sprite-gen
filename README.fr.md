# sprite-gen

<p align="center">
  <img src="docs/claudecy-running.gif" width="160" alt="claudecy running" />
</p>

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · **Français**

Une skill Codex/Claude pour générer des sprites 2D de jeux propres et des atlas d’animation avec un pipeline de lignes de composants, ainsi qu’une vue web autonome pour revoir, sélectionner et corriger les frames avant qu’elles ne soient fusionnées.

```text
sprite-request.json → layout guides + prompts → image-gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

## Exemple de sortie

Sprites générés et curés avec cette skill (`claudecy`, `howl`) :

<p>
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy inactif" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy en course" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy réussi" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy en train de parler" />
  <img src="docs/howl-idle.gif" width="110" alt="howl inactif" />
  <img src="docs/howl-running.gif" width="110" alt="howl en course" />
  <img src="docs/howl-success.gif" width="110" alt="howl réussi" />
</p>

## Vue de curation

Après extraction des frames, lancez une webview locale autonome pour les examiner — sans dépendance à Studio ou à un framework, donc elle fonctionne partout où la skill est installée (Claude Code Desktop, l’application Codex, un terminal simple).

![curation webview — characters](docs/demo-character.gif)

- **Comparez les frames côte à côte** par état, et **sélectionnez / rejetez** les frames individuellement.
- **Transformation non destructive** par frame : glisser = déplacer, molette = mettre à l’échelle, poignée supérieure = faire pivoter, poignée bas-gauche = ciseler. Les modifications sont enregistrées dans un fichier `curation.json` annexe — les PNG source ne sont jamais réécrits, et l’étape de composition applique le résultat de façon déterministe. L’aperçu (CSS + canvas) et la cuisson partagent une même matrice affine, donc ce que vous alignez est exactement ce que vous obtenez.
- **L’aperçu en direct** anime les frames sélectionnées selon le fps de l’état.

### Grille de sol isométrique

Pour les sets isométriques, la webview superpose la grille de sol (depuis `meta.json` tile/anchor) afin que vous puissiez accrocher le mobilier aux axes en losange avec la poignée de cisaillement.

![curation webview — isometric furniture](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="superposition de grille de sol isométrique" />

### Langues

La webview est disponible en anglais et en coréen. Passez `--lang en|ko` au lancement, ou utilisez le basculeur intégré :

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## Démarrage rapide

```bash
# 0. installer les dépendances (Pillow) dans un virtualenv neuf
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. préparer une exécution à partir d'une image de base
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. générer une image par ligne et par état avec image-gen, enregistrer dans raw/<state>.png
# 3. extraire les frames
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (optionnel) curation des frames dans la webview
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. compiler l’atlas runtime
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### Modifier une feuille terminée

Quand seule la feuille combinée subsiste, reconstruisez un dossier d’exécution prêt pour la curation, puis curate et exportez :

```bash
# rebuild frames: explicit --grid, --manifest rectangles, or alpha auto-detect (default)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# after curating, bake corrections back to named PNGs
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

Par défaut, la sortie se crée dans un dossier repérable `<source>-curator` à côté de l’entrée.

Le workflow complet orienté agent et les contrats se trouvent dans [`SKILL.md`](SKILL.md).

## Installation

Depuis les workflows d’installation de skill Codex, installez ce dépôt comme skill racine :

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

## Attribution

Le flux de travail basé sur des lignes de composants s’inspire de la skill `hatch-pet` sous licence Apache-2.0, mais cible des atlas de sprites de jeux génériques et ne contient ni paquets liés à des pets ni assets visuels de pets.

## Licence

Apache-2.0
