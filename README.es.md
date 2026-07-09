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

<p align="center"><b>Entra un dibujo. Sale un atlas de sprites listo para juego.</b></p>

<p align="center">

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-Hans.md) · [Español](README.es.md) · [Français](README.fr.md)

</p>

---

Pídele a un modelo de imagen una "hoja de sprites" y ya sabes lo que obtienes: un personaje cuya cara cambia en cada fotograma, un fondo que no se puede recortar por clave, poses que se solapan y se desplazan fuera de la cuadrícula, y un PNG que tu motor de juego en realidad no puede consumir. Demo bonita, asset inútil.

`sprite-gen` es una skill de Codex/Claude que cierra esa brecha. Dale **una imagen base** y una lista de acciones: dirige la generación fila por fila, fija la identidad del personaje, elimina el fondo chroma hasta alfa real, extrae cada pose como un fotograma transparente limpio y hornea un atlas de runtime **con un `manifest.json.frame_layout` legible por máquina**. Todos los sprites de arriba se hicieron así.

Y para ese último 10% que la generación nunca acierta, hay una **webview de curación**: compara fotogramas lado a lado, rechaza los rotos, ajusta rotación/escala/posición de forma no destructiva, mira el loop en vivo y luego hornea. El pipeline hace el trabajo; tú conservas el criterio.

```text
sprite-request.json → guías de layout + prompts → filas de estado de image-gen
→ alfa chroma → componentes conectados → fotogramas transparentes
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

```mermaid
flowchart LR
    REQ["sprite-request.json<br/>(SSoT numérico)"] --> GUIDES["guías de layout<br/>+ prompts"]
    GUIDES --> GEN["image-gen<br/>tiras de filas de estado"]
    GEN --> EXTRACT["alfa chroma →<br/>componentes conectados"]
    EXTRACT --> FRAMES["fotogramas transparentes"]
    FRAMES --> ATLAS["sprite-sheet-alpha.png<br/>+ manifest.json.frame_layout"]
    FRAMES -. "webview de curación (opcional)" .-> ATLAS
```

> Arquitectura completa: [`docs/architecture.md`](docs/architecture.md)

## Lo que realmente obtienes

- **Un atlas de sprites transparente** (`sprite-sheet-alpha.png`): alfa real, sin restos de borde chroma, verificado contra fondos blancos.
- **Un manifiesto de runtime** (`manifest.json.frame_layout`): rectángulos de fotograma absolutos, fps por estado y flags de loop. Tu motor muestrea rectángulos; nunca adivina una cuadrícula.
- **QA que puedes ver**: GIFs por estado y hojas de contacto, para que el movimiento se evalúe como movimiento antes de enviar nada.
- **Etiquetas honestas**: acciones cortas y legibles (idle, jump, attack, wave) son la ruta estable; la locomoción cíclica (walk/run) se marca como experimental salvo que el QA de movimiento realmente pase. Sin promesas silenciosas de más.

## Calidad del alfa chroma

El extractor mantiene la limpieza chroma determinista: el soft-alpha unmix conserva el antialiasing de mechones finos y contornos delgados, en lugar de pelarlos antes de resolver la cobertura.

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-magenta.png" width="640" alt="comparación chroma de cuerpo completo: ilustración sobre clave magenta" /><br />
  <em>Ilustración, clave magenta: fuente, peel v1.12.0, soft-alpha unmix v1.13.0.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-illustration-green.png" width="640" alt="comparación chroma de cuerpo completo: ilustración sobre clave verde" /><br />
  <em>Ilustración, clave verde: fuente, peel v1.12.0, soft-alpha unmix v1.13.0.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-magenta.png" width="640" alt="comparación chroma de cuerpo completo: pixel art sobre clave magenta" /><br />
  <em>Pixel art, clave magenta: fuente, peel v1.12.0, salida binarizada v1.13.0.</em>
</p>

<p align="center">
  <img src="docs/assets/chroma-fullbody-pixelart-green.png" width="640" alt="comparación chroma de cuerpo completo: pixel art sobre clave verde" /><br />
  <em>Pixel art, clave verde: fuente, peel v1.12.0, salida binarizada v1.13.0.</em>
</p>

Los recortes ampliados de abajo muestran el detalle de borde detrás de las comparaciones de cuerpo completo.

![comparación antes y después del chroma peel — mechón ilustrado](docs/assets/chroma-peel-illustration-before-after.png)

![comparación antes y después del chroma peel — contorno pixel-art](docs/assets/chroma-peel-pixelart-before-after.png)

## Webview de curación

La generación te lleva al 90%. La webview es donde una persona lo lleva a *enviado*: independiente, sin dependencia de Studio ni de ningún framework, funciona dondequiera que la skill esté instalada (Claude Code Desktop, la app de Codex, una terminal simple).

![webview de curación — personajes](docs/assets/demo-character.gif)

- **Dos filas por estado:** la **secuencia de reproducción** arriba y un **pool de candidatos** debajo (por ejemplo, una segunda o tercera toma generada). Arrastra el grip ⠿ de un fotograma para reordenar la secuencia, o sube un corte desde el pool: reconstruye un loop de carrera limpio con los mejores fotogramas de varias tomas. La disposición se guarda, así que al reabrir se restaura.
- **Transformación no destructiva** por fotograma: arrastrar = mover, rueda = escalar, asa superior = rotar, inferior izquierda = sesgar, además de un toggle de volteo horizontal para salida invertida izquierda-derecha. Las ediciones viven en un sidecar `curation.json`: los PNG de origen nunca se reescriben, y el paso de composición hornea el resultado de forma determinista. La vista previa y el horneado comparten una matriz afín, así que lo que alineas es lo que obtienes.
- **Vista previa en vivo** anima la secuencia a los fps del estado, con reproducir/pausar, avance fotograma a fotograma y control de velocidad de 0.25× a 4×.
- No solo para sprites: apúntala a cualquier carpeta de candidatos de imagen (iconos, logotipos, borradores generados) con `unpack_atlas_run.py --pngs-dir` y úsala como vista general para elegir el ganador.

### Cuadrícula de suelo isométrica

Para sets isométricos, la webview superpone la cuadrícula del suelo (desde `meta.json` tile/anchor) para que puedas ajustar muebles a los ejes del diamante con el asa de sesgo.

![webview de curación — muebles isométricos](docs/assets/demo-furniture.gif)

<img src="docs/assets/curator-iso.png" width="520" alt="superposición de cuadrícula de suelo isométrica" />

### Idiomas

La webview incluye inglés y coreano. Pasa `--lang en|ko` al lanzarla, o usa el toggle dentro de la app:

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # o ko
```

## Soporte de Python

`sprite-gen` soporta CPython 3.10+. CI ejecuta la versión mínima soportada (3.10) y la versión cubierta más reciente (3.14) en runners alojados en GitHub.

El inicio rápido requiere una instalación de Python con `venv`/`ensurepip` funcionales. Si `python3 -m venv` falla antes de la instalación de paquetes en una distribución local, usa una build estándar de CPython de cualquier versión soportada y vuelve a ejecutar los mismos comandos.

## Inicio rápido

```bash
# 0. instalar dependencias (Pillow) en un virtualenv nuevo
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 1. preparar una ejecución a partir de una imagen base
python3 scripts/prepare_sprite_run.py --out-dir <run-dir> --character-id <id> --base-image base.png

# 2. generar una imagen de fila por estado con image-gen, guardar como raw/<state>.png
# 3. extraer fotogramas
python3 scripts/extract_sprite_row_frames.py --run-dir <run-dir>

# 4. (opcional) curar fotogramas en la webview
python3 scripts/serve_curation.py --run-dir <run-dir>

# 5. hornear el atlas de runtime
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### Editar una hoja terminada

Cuando solo sobrevive la hoja combinada, reconstruye un run dir listo para curador, luego cura y exporta:

```bash
# reconstruir fotogramas: --grid explícito, rectángulos de --manifest, o autodetección por alfa (predeterminado)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # autodetectar
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # rectángulos exactos
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # importar un set suelto de PNG

# después de curar, hornear correcciones de vuelta a PNG con nombre
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

La salida por defecto va a una carpeta localizable `<source>-curator` junto a la entrada.

El flujo de trabajo completo orientado a agentes y los contratos viven en [`SKILL.md`](SKILL.md).

## Instalación

Desde los flujos del instalador de skills de Codex, instala este repositorio como una skill raíz:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

### Dependencia de skill requerida

Las imágenes de filas raw (paso 2 del inicio rápido) se generan con la skill separada [`image-gen`](https://github.com/aldegad/image-gen) (declarada como `kuma:image-gen` en `depends_on` de `SKILL.md`). Instálala de la misma manera:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/image-gen --path .
```

## Atribución

El flujo de trabajo de filas de componentes está inspirado en la skill `hatch-pet` con licencia Apache-2.0, pero apunta a atlas genéricos de sprites para juegos y no incluye paquetes de mascotas ni assets visuales de mascotas.

## Licencia

Apache-2.0
