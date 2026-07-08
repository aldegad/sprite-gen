<p align="center">
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy idle" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy running" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy success" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy talking" />
  <img src="docs/howl-idle.gif" width="110" alt="howl idle" />
  <img src="docs/howl-running.gif" width="110" alt="howl running" />
  <img src="docs/howl-success.gif" width="110" alt="howl success" />
</p>

<h1 align="center">sprite-gen</h1>

<p align="center"><b>Un dessin en entrée. Un atlas de sprites prêt pour le jeu en sortie.</b></p>

<p align="center">

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

</p>

---

Demandez à un modèle d’image une « feuille de sprites » et vous savez ce que vous obtenez : un personnage dont le visage change à chaque frame, un arrière-plan impossible à supprimer proprement par chroma key, des poses qui se chevauchent et dérivent hors grille, et un PNG que votre moteur de jeu ne peut pas réellement consommer. Démo mignonne, asset inutile.

`sprite-gen` est une skill Codex/Claude qui comble cet écart. Donnez-lui **une image de base** et une liste d’actions — elle pilote la génération ligne par ligne, verrouille l’identité du personnage, retire l’arrière-plan chroma en véritable alpha, extrait chaque pose comme une frame transparente propre, et construit un atlas runtime **avec un `manifest.json.frame_layout` lisible par machine**. Tous les sprites ci-dessus ont été créés ainsi.

Et pour les derniers 10 % que la génération ne réussit jamais tout à fait, il y a une **webview de curation** : comparez les frames côte à côte, rejetez celles qui sont cassées, ajustez rotation/échelle/position de manière non destructive, regardez la boucle en direct — puis bakez. Le pipeline fait le travail ; vous gardez le goût.

```text
sprite-request.json → layout guides + prompts → image-gen state rows
→ chroma alpha → connected components → transparent frames
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

<p align="center">
  <img src="docs/architecture-diagram.png" width="640" alt="architecture sprite-gen — pipeline component-row" />
</p>

> Architecture complète : [`docs/architecture.md`](docs/architecture.md) · source du diagramme : [`docs/architecture-diagram.html`](docs/architecture-diagram.html)

## Ce que vous obtenez réellement

- **Un atlas de sprites transparent** (`sprite-sheet-alpha.png`) — véritable alpha, aucun résidu chroma en bordure, vérifié sur fonds blancs.
- **Un manifeste runtime** (`manifest.json.frame_layout`) — rectangles de frames absolus, fps et indicateurs de boucle par état. Votre moteur échantillonne des rectangles ; il ne devine jamais une grille.
- **Une QA visible** — GIFs par état et planches-contact, pour juger le mouvement comme du mouvement avant toute livraison.
- **Des libellés honnêtes** — les actions courtes et lisibles (idle, jump, attack, wave) sont le chemin stable ; la locomotion cyclique (walk/run) est marquée expérimentale sauf si la QA de mouvement réussit réellement. Pas de promesse excessive silencieuse.

## Webview de curation

La génération vous amène à 90 %. La webview est l’endroit où un humain l’amène jusqu’à *livré* — autonome, sans dépendance à Studio ni à un framework, exécutable partout où la skill est installée (Claude Code Desktop, l’app Codex, un simple terminal).

![webview de curation — personnages](docs/demo-character.gif)

- **Deux lignes par état :** la **séquence de lecture** en haut et un **pool de candidats** en dessous (par exemple une deuxième ou troisième tentative générée). Faites glisser la poignée ⠿ d’une frame pour réordonner la séquence, ou remontez une coupe depuis le pool — reconstruisez une boucle de course propre à partir des meilleures frames de plusieurs tentatives. L’agencement est sauvegardé, donc il est restauré à la réouverture.
- **Transformation non destructive** par frame : glisser = déplacer, molette = mettre à l’échelle, poignée supérieure = faire pivoter, bas gauche = cisaillement, plus un interrupteur de retournement horizontal pour une sortie inversée gauche-droite. Les modifications vivent dans un sidecar `curation.json` — les PNG sources ne sont jamais réécrits, et l’étape de composition bake le résultat de manière déterministe. L’aperçu et le bake partagent une seule matrice affine, donc ce que vous alignez est ce que vous obtenez.
- **L’aperçu en direct** anime la séquence au fps de l’état, avec lecture/pause, avance frame par frame, et un contrôle de vitesse de 0,25× à 4×.
- Pas seulement pour les sprites : pointez-la vers n’importe quel dossier de candidats image (icônes, logos, brouillons générés) avec `unpack_atlas_run.py --pngs-dir` et utilisez-la comme vue générale pour choisir le gagnant.

### Grille de sol isométrique

Pour les ensembles isométriques, la webview superpose la grille de sol (depuis la tuile/l’ancre de `meta.json`) afin que vous puissiez aligner les meubles sur les axes du losange avec la poignée de cisaillement.

![webview de curation — mobilier isométrique](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="superposition de grille de sol isométrique" />

### Langues

La webview est fournie en anglais et en coréen. Passez `--lang en|ko` au lancement, ou utilisez l’interrupteur intégré à l’app :

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # ou ko
```

## Prise en charge de Python

`sprite-gen` prend en charge CPython 3.10+. La CI exécute la version minimale prise en charge (3.10) et la dernière version couverte (3.14) sur des runners hébergés par GitHub.

Le démarrage rapide nécessite une installation Python avec `venv`/`ensurepip` fonctionnels. Si `python3 -m venv` échoue avant l’installation des packages dans une distribution locale, utilisez une build CPython standard de n’importe quelle version prise en charge et relancez les mêmes commandes.

## Démarrage rapide

```bash
# 0. install dependencies (Pillow) into a fresh virtualenv
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. prepare a run from a base image
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. generate one row image per state with image-gen, save as raw/<state>.png
# 3. extract frames
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (optional) curate frames in the webview
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. bake the runtime atlas
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### Modifier une feuille terminée

Lorsqu’il ne reste que la feuille combinée, reconstruisez un run dir prêt pour le curateur, puis faites la curation et exportez :

```bash
# rebuild frames: explicit --grid, --manifest rectangles, or alpha auto-detect (default)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# after curating, bake corrections back to named PNGs
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

La sortie par défaut est un dossier `<source>-curator` facile à retrouver, placé à côté de l’entrée.

Le workflow complet destiné aux agents et les contrats se trouvent dans [`SKILL.md`](SKILL.md).

## Installation

Depuis les workflows d’installation de skills Codex, installez ce dépôt comme skill racine :

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

### Dépendance de skill requise

Les images de lignes brutes (étape 2 du démarrage rapide) sont générées par la skill séparée [`image-gen`](https://github.com/aldegad/image-gen) (déclarée comme `kuma:image-gen` dans `depends_on` de `SKILL.md`). Installez-la de la même manière :

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/image-gen --path .
```

## Attribution

Le workflow component-row s’inspire de la skill `hatch-pet` sous licence Apache-2.0, mais cible des atlas de sprites de jeu génériques et n’inclut aucun package pet ni asset visuel pet.

## Licence

Apache-2.0