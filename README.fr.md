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

<p align="center"><b>Un dessin en entrée. Un atlas de sprites prêt pour le jeu en sortie.</b></p>

<p align="center">

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

</p>

---

Demandez à un modèle d'image une « sprite sheet » et vous savez ce que vous obtenez : un personnage dont le visage change à chaque image, un arrière-plan impossible à détourer, des poses qui se chevauchent et dérivent hors grille, et un PNG que votre moteur de jeu ne peut pas réellement consommer. Démo mignonne, asset inutile.

`sprite-gen` est une compétence Codex/Claude qui comble cet écart. Donnez-lui **une image de base** et une liste d'actions — il pilote la génération ligne par ligne, verrouille l'identité du personnage, retire l'arrière-plan chroma pour obtenir un véritable alpha, extrait chaque pose sous forme d'image transparente propre, et produit un atlas d'exécution **avec un `manifest.json.frame_layout` lisible par machine**. Tous les sprites ci-dessus ont été créés ainsi.

Et pour les derniers 10 % que la génération ne réussit jamais parfaitement, il existe une **webview de curation** : comparez les images côte à côte, rejetez celles qui sont cassées, ajustez rotation/échelle/position de manière non destructive, regardez la boucle en direct — puis produisez l'atlas. Le pipeline fait le travail ; vous gardez le goût.

```text
sprite-request.json → guides de disposition + prompts → sprite-gen gen state rows
→ alpha chroma → composants connexes → images transparentes
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

```mermaid
flowchart LR
    REQ["sprite-request.json<br/>(SSoT numérique)"] --> GUIDES["guides de disposition<br/>+ prompts"]
    GUIDES --> GEN["sprite-gen gen<br/>bandes de lignes d'état"]
    GEN --> EXTRACT["alpha chroma →<br/>composants connexes"]
    EXTRACT --> FRAMES["images transparentes"]
    FRAMES --> ATLAS["sprite-sheet-alpha.png<br/>+ manifest.json.frame_layout"]
    FRAMES -. "webview de curation (facultatif)" .-> ATLAS
```

> Architecture complète : [`docs/architecture.md`](docs/architecture.md)

## Ce que vous obtenez vraiment

- **Un atlas de sprites transparent** (`sprite-sheet-alpha.png`) — véritable alpha, aucun liseré chroma résiduel, vérifié sur des arrière-plans blancs.
- **Un manifeste d'exécution** (`manifest.json.frame_layout`) — rectangles d'image absolus, fps par état et indicateurs de boucle. Votre moteur échantillonne des rectangles ; il ne devine jamais une grille.
- **Une QA que vous pouvez regarder** — GIFs par état et planches-contact, pour que le mouvement soit jugé comme mouvement avant toute livraison.
- **Des étiquettes honnêtes** — les actions courtes et lisibles (idle, jump, attack, wave) constituent le chemin stable ; la locomotion cyclique (walk/run) est marquée expérimentale sauf si la QA du mouvement passe réellement. Aucune promesse excessive silencieuse.

## Qualité de l'alpha chroma

L'extracteur garde le nettoyage chroma déterministe : le démélange en alpha doux préserve les mèches de cheveux anticrénelées et les contours fins au lieu de les arracher avant que la couverture puisse être résolue.

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-magenta.png" width="640" alt="full-body chroma comparison: illustration on magenta key" /><br />
  <em>Illustration, clé magenta : source, peel v1.12.0, démélange alpha doux v1.13.0.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-green.png" width="640" alt="full-body chroma comparison: illustration on green key" /><br />
  <em>Illustration, clé verte : source, peel v1.12.0, démélange alpha doux v1.13.0.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-magenta.png" width="640" alt="full-body chroma comparison: pixel art on magenta key" /><br />
  <em>Pixel art, clé magenta : source, peel v1.12.0, sortie binarisée v1.13.0.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-green.png" width="640" alt="full-body chroma comparison: pixel art on green key" /><br />
  <em>Pixel art, clé verte : source, peel v1.12.0, sortie binarisée v1.13.0.</em>
</p>

Les recadrages rapprochés ci-dessous montrent le détail des bords derrière les comparaisons en corps entier.

![chroma peel avant et après — mèche de cheveux illustrée](docs/assets/chroma-peel-illustration-before-after.png)

![chroma peel avant et après — contour pixel-art](docs/assets/chroma-peel-pixelart-before-after.png)

## Webview de curation

La génération vous amène à 90 %. La webview est l'endroit où un humain l'amène jusqu'à *livré* — autonome, sans dépendance à Studio ni à un framework, utilisable partout où la compétence est installée (Claude Code Desktop, l'application Codex, un terminal simple).

![webview de curation — personnages](docs/assets/demo-character.gif)

- **Deux lignes par état :** la **séquence de lecture** en haut et un **pool de candidats** en dessous (par exemple une deuxième ou troisième génération). Faites glisser la poignée ⠿ d'une image pour réordonner la séquence, ou remontez une coupe depuis le pool — reconstruisez une boucle de course propre à partir des meilleures images issues de plusieurs essais. L'arrangement est enregistré, donc sa réouverture le restaure.
- **Transformation non destructive** par image : glisser = déplacer, molette = redimensionner, poignée supérieure = pivoter, bas-gauche = cisaillement, plus un bouton de bascule de retournement horizontal pour une sortie inversée gauche-droite. Les modifications vivent dans un fichier compagnon `curation.json` — les PNG sources ne sont jamais réécrits, et l'étape de composition produit le résultat de manière déterministe. L'aperçu et la production partagent une seule matrice affine, donc ce que vous alignez est ce que vous obtenez.
- **Aperçu en direct** anime la séquence au fps de l'état, avec lecture/pause, avancée image par image, et un contrôle de vitesse 0.25×–4×.
- Pas seulement pour les sprites : pointez-le vers n'importe quel dossier de candidats image (icônes, logos, brouillons générés) avec `unpack_atlas_run.py --pngs-dir` et utilisez-le comme vue générale de sélection du gagnant.

### Grille de sol isométrique

Pour les ensembles isométriques, la webview superpose la grille du sol (depuis la tuile/l'ancre de `meta.json`) afin que vous puissiez accrocher les meubles aux axes du losange avec la poignée de cisaillement.

![webview de curation — mobilier isométrique](docs/assets/demo-furniture.gif)

<img src="docs/assets/curator-iso.png" width="520" alt="isometric ground grid overlay" />

### Langues

La webview est fournie avec l'anglais et le coréen. Passez `--lang en|ko` au lancement, ou utilisez le bouton de bascule dans l'application :

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # or ko
```

## Prise en charge de Python

`sprite-gen` prend en charge CPython 3.10+. La CI exécute la version minimale prise en charge (3.10) et la dernière version couverte (3.14) sur des runners hébergés par GitHub.

Le démarrage rapide exige une installation Python avec `venv`/`ensurepip` fonctionnels. Si `python3 -m venv` échoue avant l'installation des paquets dans une distribution locale, utilisez une build CPython standard pour n'importe quelle version prise en charge et relancez les mêmes commandes.

## Démarrage rapide

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

### Modifier une feuille terminée

Quand seule la feuille combinée subsiste, reconstruisez un dossier d'exécution prêt pour le curateur, puis faites la curation et l'export :

```bash
# rebuild frames: explicit --grid, --manifest rectangles, or alpha auto-detect (default)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # exact rectangles
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # import a loose PNG set

# after curating, bake corrections back to named PNGs
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

La sortie par défaut est un dossier trouvable `<source>-curator` à côté de l'entrée.

Le workflow complet destiné aux agents et les contrats se trouvent dans [`SKILL.md`](SKILL.md).

## Installation

Depuis les workflows d'installation de compétences Codex, installez ce dépôt comme compétence racine :

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

### Propriété de la génération d'images

La génération adossée à des fournisseurs fait partie de ce moteur (`sprite_gen.gen`), avec
`codex` et `grok` comme fournisseurs pris en charge. La compétence générale `image-gen` n'est
qu'une navette légère vers la même commande, elle n'a donc pas besoin d'une seconde
implémentation de fournisseur. Voir [`docs/gen.md`](docs/gen.md) pour la CLI et le contrat de
vérification.

## Attribution

Le workflow par lignes de composants s'inspire de la compétence `hatch-pet` sous licence Apache-2.0, mais cible des atlas de sprites de jeu génériques et n'inclut aucun package de familier ni aucun asset visuel de familier.

## Licence

Apache-2.0