#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Serve the sprite-gen curation webview for a single run directory.

Standalone, dependency-free (Python standard library + the PIL already used by
the pipeline). Launch it against any sprite-gen run folder and open the printed
URL in a browser to compare frames per state, select/reject frames, and apply a
non-destructive per-frame transform (rotate/scale/move). All edits are persisted
to `curation.json` in the run directory; the original frame PNGs are never
touched. The compose scripts read that sidecar and bake the result.

    python3 serve_curation.py --run-dir <run-folder>

This is intentionally a standalone skill tool (not a Studio panel) so it works
from Claude Code Desktop, the Codex app, or any environment where the skill is
installed.

API:
    GET  /                    -> curator SPA
    GET  /api/run             -> run state (cell, states, frames, current curation)
    GET  /frames/<state>/<f>  -> a frame PNG
    GET  /run/<relpath>       -> a file inside the run dir (atlas/qa previews)
    POST /api/curation        -> atomically write curation.json (request body)
    POST /api/compose         -> re-run compose_sprite_atlas.py, return its result
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

try:
    from PIL import Image
except ImportError:  # pragma: no cover — 파이프라인 필수 의존성이지만 서버는 살아있게
    Image = None
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from curation import CURATION_FILENAME, SCHEMA_VERSION, empty_curation, load_curation

SCRIPTS_DIR = Path(__file__).resolve().parent
CURATOR_DIR = SCRIPTS_DIR / "curator"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


def build_run_state(run_dir: Path) -> dict:
    """Assemble the run snapshot the SPA needs, from the canonical SSoT files."""
    request = json.loads((run_dir / "sprite-request.json").read_text(encoding="utf-8"))
    frames_manifest_path = run_dir / "frames" / "frames-manifest.json"
    frames_manifest = (
        json.loads(frames_manifest_path.read_text(encoding="utf-8"))
        if frames_manifest_path.is_file()
        else {"rows": []}
    )
    rows_by_state = {row["state"]: row for row in frames_manifest.get("rows", [])}

    cell = request["cell"]
    cell_state = {
        "width": int(cell.get("width", cell.get("size", 0))),
        "height": int(cell.get("height", cell.get("size", 0))),
    }

    # 픽셀퍼펙트 격자: 논리 픽셀 1칸이 셀 픽셀 몇 칸인가. extract 의 pp_scale 과 같은 식이어야
    # 큐레이터 오버레이가 "실제로 스냅된 격자"를 그린다 (셀 래스터가 아니라).
    fit = request.get("fit") or {}
    pixel_perfect = None
    if fit.get("pixel_perfect"):
        logical_height = int(fit.get("logical_height", cell_state["height"]))
        scale = max(1, cell_state["height"] // max(1, logical_height))
        pixel_perfect = {"logicalHeight": logical_height, "scale": scale}

    states = []
    for state, entry in request["states"].items():
        row = rows_by_state.get(state, {})
        files = row.get("files", [])
        labels = row.get("labels", [])
        frame_count = int(entry["frames"])
        frames = []
        for index in range(frame_count):
            rel = f"frames/{state}/frame-{index}.png"
            present = (run_dir / rel).is_file()
            frame = {"index": index, "url": f"/{rel}", "present": present}
            # 픽셀퍼펙트 전 쌍둥이(.plain.png) — 있으면 큐레이터가 전/후 토글을 켠다
            plain_rel = f"frames/{state}/frame-{index}.plain.png"
            if (run_dir / plain_rel).is_file():
                frame["plainUrl"] = f"/{plain_rel}"
            if index < len(labels):
                frame["label"] = labels[index]
            if present and Image is not None:
                # 실제 스프라이트 픽셀 크기(투명 패딩 제외 알파 bbox) — 사이즈 통일 검수용
                try:
                    with Image.open(run_dir / rel) as im:
                        frame["size"] = [im.width, im.height]
                        alpha = im.getchannel("A") if "A" in im.getbands() else None
                        bbox = alpha.getbbox() if alpha is not None else im.getbbox()
                        if bbox:
                            frame["contentSize"] = [bbox[2] - bbox[0], bbox[3] - bbox[1]]
                except OSError:
                    pass
            frames.append(frame)
        states.append(
            {
                "name": state,
                "fps": int(entry.get("fps", 6)),
                "loop": bool(entry.get("loop", True)),
                "action": entry.get("action", ""),
                "requestFrames": frame_count,
                "extractOk": bool(row.get("ok", bool(files))),
                "frames": frames,
            }
        )

    curation = load_curation(run_dir) or empty_curation()
    # 원본 베이스(아이덴티티 truth)가 있으면 큐레이터 최상단에 참조 줄로 노출
    base_url = None
    for candidate in sorted(run_dir.glob("base-source.*")):
        if candidate.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            base_url = f"/run/{candidate.name}"
            break
    return {
        "characterId": request["character"]["id"],
        "runDir": str(run_dir),
        "baseUrl": base_url,
        "cell": cell_state,
        "pixelPerfect": pixel_perfect,
        "schemaVersion": SCHEMA_VERSION,
        "states": states,
        "curation": curation,
        "iso": request.get("iso"),
        "lang": CurationHandler.lang,
        "hasAtlas": (run_dir / "sprite-sheet-alpha.png").is_file(),
        "fitPixelPerfect": bool((request.get("fit") or {}).get("pixel_perfect")),
    }


def write_curation_atomic(run_dir: Path, payload: dict) -> None:
    """Atomically replace curation.json (temp file in the same dir + os.replace)."""
    if payload.get("kind") != "sprite-gen-curation":
        raise ValueError("payload is not a sprite-gen-curation document")
    target = run_dir / CURATION_FILENAME
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(run_dir), prefix=".curation-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _run_script(name: str, run_dir: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), "--run-dir", str(run_dir)],
        capture_output=True,
        text=True,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def run_compose(run_dir: Path) -> dict:
    """Re-run the atlas compose step so curation bakes into atlas/manifest."""
    return _run_script("compose_sprite_atlas.py", run_dir)


def run_export(run_dir: Path) -> dict:
    """Export curated frames back to named PNGs under <run-dir>/curated/."""
    result = _run_script("export_curated_pngs.py", run_dir)
    if result["ok"] and result["stdout"]:
        try:
            result["export"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return result


def run_export_gif(run_dir: Path) -> dict:
    """Export one clean transparent GIF per state under <run-dir>/exports/.

    Reuses compose_sprite_gif.py --run-dir, which applies the same curation
    selection/order/transform as the atlas compose (curation.py SSoT)."""
    result = _run_script("compose_sprite_gif.py", run_dir)
    if result["ok"] and result["stdout"]:
        try:
            result["gif"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return result


class CurationHandler(BaseHTTPRequestHandler):
    run_dir: Path = Path(".")
    lang: str = "en"

    def log_message(self, *_args):  # quieter console
        pass

    # --- helpers -------------------------------------------------------------

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "not found", "path": str(path)}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _safe_path(base: Path, rel: str) -> Path | None:
        """Resolve `rel` under `base`, refusing anything that escapes it."""
        base = base.resolve()
        candidate = (base / unquote(rel)).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        return candidate

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    # --- routes --------------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_file(CURATOR_DIR / "index.html")
            return
        if path == "/api/run":
            try:
                self._send_json(build_run_state(self.run_dir))
            except Exception as exc:  # surface the real error, no silent fallback
                self._send_json({"error": str(exc)}, 500)
            return
        if path.startswith("/curator/"):
            resolved = self._safe_path(CURATOR_DIR, path[len("/curator/"):])
            if resolved is None:
                self._send_json({"error": "path escapes curator dir"}, 403)
                return
            self._send_file(resolved)
            return
        if path.startswith("/frames/") or path.startswith("/run/"):
            rel = path[len("/run/"):] if path.startswith("/run/") else path[1:]
            resolved = self._safe_path(self.run_dir, rel)
            if resolved is None:
                self._send_json({"error": "path escapes run dir"}, 403)
                return
            self._send_file(resolved)
            return
        # bare static asset (curator.js / curator.css served from /)
        asset = self._safe_path(CURATOR_DIR, path.lstrip("/"))
        if asset is None:
            self._send_json({"error": "path escapes curator dir"}, 403)
            return
        if asset.is_file():
            self._send_file(asset)
            return
        self._send_json({"error": "not found", "path": path}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/curation":
                payload = self._read_body()
                write_curation_atomic(self.run_dir, payload)
                self._send_json({"ok": True})
                return
            if path == "/api/compose":
                result = run_compose(self.run_dir)
                self._send_json(result, 200 if result["ok"] else 500)
                return
            if path == "/api/export":
                result = run_export(self.run_dir)
                self._send_json(result, 200 if result["ok"] else 500)
                return
            if path == "/api/export-gif":
                result = run_export_gif(self.run_dir)
                self._send_json(result, 200 if result["ok"] else 500)
                return
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)
            return
        self._send_json({"error": "not found", "path": path}, 404)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--no-open", action="store_true", help="do not auto-open the browser")
    parser.add_argument("--lang", choices=["en", "ko"], default="en", help="initial UI language (toggleable in the webview)")
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    if not (run_dir / "sprite-request.json").is_file():
        raise SystemExit(f"not a sprite-gen run dir (no sprite-request.json): {run_dir}")
    if not CURATOR_DIR.is_dir():
        raise SystemExit(f"missing curator SPA dir: {CURATOR_DIR}")

    handler = partial(CurationHandler)
    CurationHandler.run_dir = run_dir
    CurationHandler.lang = args.lang
    server = ThreadingHTTPServer((args.host, args.port), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"sprite-gen curation webview: {url}")
    print(f"  run-dir: {run_dir}")
    print("  Ctrl-C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
