# sprite-gen

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · **Español** · [Français](README.fr.md)

Una habilidad de Codex/Claude para generar sprites limpios de juegos 2D y atlas de animación con una pipeline de filas de componentes, y una webview independiente para revisar, curar y corregir los frames antes de renderizarlos.

```text
sprite-request.json → guías de diseño + prompts → filas de estado de image-gen
→ alpha de cromado → componentes conectados → frames transparentes
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

## Ejemplo de salida

Sprites generados y curados con esta habilidad (`claudecy`, `howl`):

<p>
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy inactivo" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy corriendo" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy éxito" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy hablando" />
  <img src="docs/howl-idle.gif" width="110" alt="howl inactivo" />
  <img src="docs/howl-running.gif" width="110" alt="howl corriendo" />
  <img src="docs/howl-success.gif" width="110" alt="howl éxito" />
</p>

## Webview de curación

Después de extraer los frames, abre una webview local independiente para revisarlos; no depende de Studio ni de ningún framework, por lo que funciona donde esté instalada la habilidad (Claude Code Desktop, la app de Codex, una terminal simple).

![curation webview — characters](docs/demo-character.gif)

- **Comparar frames lado a lado** por estado y **seleccionar/rechazar** frames individuales.
- **Transformaciones no destructivas** por frame: arrastrar = mover, rueda = escalar, control superior = rotar, control inferior izquierdo = sesgar. Los cambios se guardan en un sidecar `curation.json`; los PNG de origen nunca se reescriben, y el paso de combinación hornea el resultado de forma determinística. La vista previa (CSS + canvas) y el bake comparten una sola matriz afín, por lo que lo que alineas es lo que obtienes.
- **Previsualización en vivo** que anima los frames seleccionados a los fps del estado.

### Cuadrícula de suelo isométrico

Para conjuntos isométricos, la webview superpone la cuadrícula del suelo (desde el `meta.json` de tile/anchor) para que puedas ajustar los muebles a los ejes de diamante con el control de sesgo.

![curation webview — isometric furniture](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="superposición de cuadrícula de suelo isométrica" />

### Idiomas

La webview se incluye con inglés y coreano. Pasa `--lang en|ko` al iniciarla, o usa el conmutador dentro de la app:

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # o ko
```

## Guía rápida

```bash
# 0. instala las dependencias (Pillow) en un virtualenv nuevo
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. prepara una ejecución a partir de una imagen base
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. genera una imagen por fila para cada estado con image-gen, guarda como raw/<state>.png
# 3. extrae los frames
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (opcional) edita los frames en la webview
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. hornea el atlas en runtime
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### Edición de una hoja terminada

Cuando solo queda la hoja combinada, reconstruye un run dir listo para curación, luego curas y exportas:

```bash
# reconstruir frames: --grid explícito, rectángulos --manifest, o detección automática alpha (predeterminado)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # auto-detect
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # rectángulos exactos
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # importar un conjunto suelto de PNG

# después de curar, hornea las correcciones de vuelta a PNGs con nombre
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

La salida por defecto es una carpeta `<source>-curator` localizable junto a la entrada.

El flujo y contratos completos orientados al agente viven en [`SKILL.md`](SKILL.md).

## Instalación

Desde los flujos de instalación de habilidades de Codex, instala este repositorio como una habilidad raíz:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

## Atribución

El flujo de fila de componentes está inspirado por la habilidad `hatch-pet` con licencia Apache-2.0, pero está orientado a atlas de sprites de juegos genéricos e incluye ninguna paquete ni recurso visual de mascotas.

## Licencia

Apache-2.0
