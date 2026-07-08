<p align="center">
  <img src="docs/claudecy-idle.gif" width="110" alt="claudecy inactivo" />
  <img src="docs/claudecy-running.gif" width="110" alt="claudecy corriendo" />
  <img src="docs/claudecy-success.gif" width="110" alt="claudecy éxito" />
  <img src="docs/claudecy-talking.gif" width="110" alt="claudecy hablando" />
  <img src="docs/howl-idle.gif" width="110" alt="howl inactivo" />
  <img src="docs/howl-running.gif" width="110" alt="howl corriendo" />
  <img src="docs/howl-success.gif" width="110" alt="howl éxito" />
</p>

<h1 align="center">sprite-gen</h1>

<p align="center"><b>Un dibujo entra. Un atlas de sprites listo para juego sale.</b></p>

<p align="center">

**Inglés** · [Coreano](README.ko.md) · [Japonés](README.ja.md) · [Chino simplificado](README.zh-Hans.md) · [Español](README.es.md) · [Francés](README.fr.md)

</p>

---

Pídele a un modelo de imagen una "hoja de sprites" y ya sabes lo que obtienes: un personaje cuya cara cambia en cada fotograma, un fondo que no se puede eliminar por clave, poses que se solapan y se desplazan fuera de la cuadrícula, y un PNG que tu motor de juego no puede consumir de verdad. Demo bonita, recurso inútil.

`sprite-gen` es una skill de Codex/Claude que cierra esa brecha. Dale **una imagen base** y una lista de acciones: conduce la generación fila por fila, fija la identidad del personaje, elimina el fondo de croma para convertirlo en alfa real, extrae cada pose como un fotograma transparente limpio y hornea un atlas de ejecución **con un `manifest.json.frame_layout` legible por máquina**. Todos los sprites de arriba se hicieron así.

Y para el último 10% que la generación nunca acierta, hay una **webview de curación**: compara fotogramas lado a lado, rechaza los rotos, ajusta rotación/escala/posición de forma no destructiva, mira el bucle en vivo y luego hornea. El pipeline hace el trabajo; tú conservas el criterio.

```text
sprite-request.json → guías de layout + prompts → filas de estado de image-gen
→ alfa de croma → componentes conectados → fotogramas transparentes
→ sprite-sheet-alpha.png + manifest.json.frame_layout
```

<p align="center">
  <img src="docs/architecture-diagram.png" width="640" alt="arquitectura de sprite-gen — pipeline de filas por componente" />
</p>

> Arquitectura completa: [`docs/architecture.md`](docs/architecture.md) · fuente del diagrama: [`docs/architecture-diagram.html`](docs/architecture-diagram.html)

## Lo que realmente obtienes

- **Un atlas de sprites transparente** (`sprite-sheet-alpha.png`): alfa real, sin restos de borde de croma, verificado contra fondos blancos.
- **Un manifiesto de ejecución** (`manifest.json.frame_layout`): rectángulos absolutos de fotogramas, fps y flags de bucle por estado. Tu motor muestrea rectángulos; nunca adivina una cuadrícula.
- **QA que puedes ver**: GIFs por estado y hojas de contacto, para que el movimiento se evalúe como movimiento antes de enviar nada.
- **Etiquetas honestas**: las acciones cortas y legibles (idle, jump, attack, wave) son el camino estable; la locomoción cíclica (walk/run) se marca como experimental salvo que el QA de movimiento realmente pase. Sin promesas silenciosas de más.

## Webview de curación

La generación te lleva al 90%. La webview es donde una persona lo lleva a *publicado*: independiente, sin dependencia de Studio ni de framework, funciona en cualquier lugar donde la skill esté instalada (Claude Code Desktop, la app de Codex, una terminal normal).

![webview de curación — personajes](docs/demo-character.gif)

- **Dos filas por estado:** la **secuencia de reproducción** arriba y un **pool de candidatos** abajo (por ejemplo, una segunda o tercera toma generada). Arrastra el agarre ⠿ de un fotograma para reordenar la secuencia, o sube un corte desde el pool: reconstruye un bucle de carrera limpio con los mejores fotogramas de distintas tomas. La disposición se guarda, así que al reabrir se restaura.
- **Transformación no destructiva** por fotograma: arrastrar = mover, rueda = escalar, tirador superior = rotar, inferior izquierdo = sesgar, más un toggle de volteo horizontal para salida invertida izquierda-derecha. Las ediciones viven en un sidecar `curation.json`: los PNG fuente nunca se reescriben, y el paso de composición hornea el resultado de forma determinista. La vista previa y el horneado comparten una sola matriz afín, así que lo que alineas es lo que obtienes.
- **Vista previa en vivo** anima la secuencia a los fps del estado, con reproducir/pausar, avance fotograma a fotograma y control de velocidad de 0.25×–4×.
- No solo para sprites: apúntala a cualquier carpeta de candidatos de imagen (iconos, logos, borradores generados) con `unpack_atlas_run.py --pngs-dir` y úsala como una vista general para elegir el ganador.

### Cuadrícula de suelo isométrica

Para conjuntos isométricos, la webview superpone la cuadrícula del suelo (desde tile/anchor de `meta.json`) para que puedas ajustar muebles a los ejes de diamante con el tirador de sesgo.

![webview de curación — muebles isométricos](docs/demo-furniture.gif)

<img src="docs/curator-iso.png" width="520" alt="superposición de cuadrícula de suelo isométrica" />

### Idiomas

La webview incluye inglés y coreano. Pasa `--lang en|ko` al lanzarla, o usa el toggle dentro de la app:

```bash
python3 scripts/serve_curation.py --run-dir <run-dir> --lang en   # o ko
```

## Soporte de Python

`sprite-gen` admite CPython 3.10+. CI ejecuta la versión mínima admitida (3.10) y la última versión cubierta (3.14) en runners alojados en GitHub.

El inicio rápido requiere una instalación de Python con `venv`/`ensurepip` funcional. Si `python3 -m venv` falla antes de la instalación de paquetes en una distribución local, usa una compilación estándar de CPython para cualquier versión admitida y vuelve a ejecutar los mismos comandos.

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

# 5. hornear el atlas de ejecución
python3 scripts/compose_sprite_atlas.py --run-dir <run-dir>
```

### Editar una hoja terminada

Cuando solo sobrevive la hoja combinada, reconstruye un run dir listo para el curador, luego cura y exporta:

```bash
# reconstruir fotogramas: --grid explícito, rectángulos de --manifest, o autodetección por alfa (predeterminado)
python3 scripts/unpack_atlas_run.py --atlas sheet.png            # autodetectar
python3 scripts/unpack_atlas_run.py --manifest manifest.json     # rectángulos exactos
python3 scripts/unpack_atlas_run.py --pngs-dir furniture/        # importar un conjunto suelto de PNG

# después de curar, hornear correcciones de vuelta a PNGs con nombre
python3 scripts/export_curated_pngs.py --run-dir <run-dir>
```

La salida usa por defecto una carpeta localizable `<source>-curator` junto a la entrada.

El flujo de trabajo completo orientado a agentes y los contratos viven en [`SKILL.md`](SKILL.md).

## Instalación

Desde los flujos de trabajo del instalador de skills de Codex, instala este repositorio como una skill raíz:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/sprite-gen --path .
```

### Dependencia de skill requerida

Las imágenes de filas sin procesar (paso 2 del inicio rápido) las genera la skill separada [`image-gen`](https://github.com/aldegad/image-gen) (declarada como `kuma:image-gen` en `depends_on` de `SKILL.md`). Instálala de la misma manera:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo aldegad/image-gen --path .
```

## Atribución

El flujo de trabajo por filas de componentes está inspirado en la skill `hatch-pet` con licencia Apache-2.0, pero apunta a atlas genéricos de sprites para juegos y no incluye paquetes de mascotas ni recursos visuales de mascotas.

## Licencia

Apache-2.0