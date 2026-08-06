#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Serve the sprite-gen composition canvas — the "blank screen" assembly surface.

This is the pre-run half of curation. The curation webview (`serve_curation.py`)
edits ONE already-assembled run directory; this surface is what a human uses to
*assemble* one in the first place, the step that until now only an agent could do
by arranging a `--pngs-dir` folder and running `unpack_atlas_run`.

The mental model is Obsidian-like: mount a local folder as a read-only candidate
library, browse its tree, and drag image files onto named rows to compose a
sprite. The composition is held virtually in the browser session as row->file
*references* — no bytes are copied and the mounted originals are never touched.
Materializing that session into a real run dir (the "build" seam that hands off
to the curation view) is a later slice; this module owns only browse + canvas.

    sprite-gen compose [--dir <folder>]

Separate handler by design (SoC): the curation server is run-dir bound from the
root (`CurationHandler.run_dir`), and assembly is a different concern that has no
run dir yet. Keeping it a distinct surface avoids threading an "unbound" branch
through every run-dir-assuming route.

API:
    GET  /                     -> composer SPA
    GET  /composer/<asset>     -> SPA static asset
    POST /api/mount            -> {dir} set the read-only library root, return its listing
    GET  /api/browse?dir=<abs> -> one directory level under the mounted root (lazy tree)
    GET  /api/browse-img?path= -> serve one image file under the mounted root
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from sprite_gen.frames.unpack_atlas import import_png_groups

# The SPA assets are package data (declared in pyproject's `package-data`), so the one
# path that finds them is relative to this module — the same place in a repo checkout
# and in an installed wheel. There is no second location to try.
COMPOSER_DIR = Path(__file__).resolve().parent / "composer"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


def _is_under(path: Path, root: Path) -> bool:
    """True iff `path` resolves inside `root`. The traversal guard for browse/img:
    the client names paths, so every filesystem read is confined to the mounted
    root. Uses relative_to rather than string prefix so `/a/b2` is not treated as
    under `/a/b`."""
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _list_dir(directory: Path, root: Path) -> dict:
    """One directory level: subfolders first, then image files. Lazy — the UI
    expands a folder by calling back with its path, so a deep library is not
    walked up front. Non-image files and unreadable entries are omitted (the
    library is a sprite candidate source, not a general file manager)."""
    directory = directory.resolve()
    if not _is_under(directory, root):
        raise PermissionError(f"outside mounted root: {directory}")
    if not directory.is_dir():
        raise FileNotFoundError(f"not a directory: {directory}")
    dirs, images = [], []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": str(entry), "type": "dir"})
            elif entry.suffix.lower() in IMAGE_SUFFIXES and entry.is_file():
                images.append({"name": entry.name, "path": str(entry), "type": "image"})
        except OSError:
            continue
    return {"dir": str(directory), "entries": dirs + images}


def _sanitize_state(name: str) -> str:
    """A row name becomes a `frames/<state>` directory, so it must be a safe path
    segment — no slashes (escape), no spaces. Non `[A-Za-z0-9_-]` runs collapse to
    a single hyphen; an empty result falls back to `state`."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-")
    return slug or "state"


def _build_groups(rows: list[dict], mount_root: Path) -> list[dict]:
    """Turn the session's rows into `import_png_groups` groups. Empty rows are
    skipped; every referenced file is re-confirmed under the mounted root (the
    client names the paths). Sanitized names are de-duplicated so two rows cannot
    collide onto one frames/<state> directory."""
    groups: list[dict] = []
    used: set[str] = set()
    for row in rows:
        cells = row.get("cells") or []
        if not cells:
            continue
        paths = []
        for cell in cells:
            p = Path(str(cell.get("path", ""))).resolve()
            if not _is_under(p, mount_root):
                raise PermissionError(f"outside mounted root: {p}")
            if p.suffix.lower() not in IMAGE_SUFFIXES or not p.is_file():
                raise FileNotFoundError(f"not an image file: {p}")
            paths.append(p)
        name = _sanitize_state(str(row.get("name", "")))
        base, n = name, 2
        while name in used:
            name, n = f"{base}-{n}", n + 1
        used.add(name)
        groups.append({"name": name, "paths": paths, "labels": [p.name for p in paths]})
    return groups


# One osascript per kind — `choose folder` and `choose file` are distinct macOS
# primitives, and a single dialog that reliably selects both files and folders is
# not available, so the two entry points map one-to-one instead. `choose file` for
# images returns a newline-joined list of POSIX paths (multiple selections allowed).
_PICK_SCRIPTS = {
    "folder": 'POSIX path of (choose folder with prompt "Open a folder of images")',
    "image": (
        'set picks to (choose file of type {"public.image"} '
        'with prompt "Open image(s)" with multiple selections allowed)\n'
        'set out to ""\n'
        'repeat with f in picks\n'
        '  set out to out & POSIX path of f & linefeed\n'
        'end repeat\n'
        'return out'
    ),
}


def native_pick(kind: str = "folder") -> dict:
    """Pop the OS chooser and return a folder to mount.

    kind="folder" -> {"dir": <abs path>}; kind="image" -> the picked image(s)'
    parent folder {"dir": <parent>, "files": [<abs image paths>]} ("grab the
    image's folder"). Cancel -> {"cancelled": True}. The browser cannot hand a
    script an absolute path, but this server is local, so it asks the OS. Non-macOS
    raises NotImplementedError so the caller can fall back explicitly (no silent
    degradation)."""
    if sys.platform != "darwin":
        raise NotImplementedError("native picker is macOS-only")
    script = _PICK_SCRIPTS.get(kind)
    if script is None:
        raise ValueError(f"unknown pick kind: {kind}")
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if proc.returncode != 0:
        if "-128" in (proc.stderr or ""):  # user canceled
            return {"cancelled": True}
        raise RuntimeError(proc.stderr.strip() or "osascript failed")
    if kind == "folder":
        picked = proc.stdout.strip()
        return {"dir": picked} if picked else {"cancelled": True}
    files = [line for line in proc.stdout.splitlines() if line.strip()]
    if not files:
        return {"cancelled": True}
    # Mount the image's containing folder; multiple picks are assumed to share one.
    return {"dir": str(Path(files[0]).parent), "files": files}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(port: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


class ComposeHandler(BaseHTTPRequestHandler):
    # Set by run(); the read-only library root. A mount replaces it in-process.
    mount_root: Path | None = None
    lang: str = "en"

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    # ── helpers ──────────────────────────────────────────────────────
    def _send_json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str | None = None) -> None:
        data = path.read_bytes()
        ctype = content_type or CONTENT_TYPES.get(path.suffix.lower()) \
            or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_spa_asset(self, rel: str) -> None:
        # rel is like "src/tree.js" or "composer.css"; confine to COMPOSER_DIR.
        target = (COMPOSER_DIR / rel).resolve()
        if not _is_under(target, COMPOSER_DIR) or not target.is_file():
            self._send_json({"error": "not found", "path": rel}, 404)
            return
        self._send_file(target)

    # ── routes ───────────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            index = COMPOSER_DIR / "index.html"
            if not index.is_file():
                self._send_json({"error": f"missing composer SPA: {index}"}, 500)
                return
            self._send_file(index)
            return
        if path == "/api/state":
            self._send_json({
                "lang": self.lang,
                "mount": str(self.mount_root) if self.mount_root else None,
            })
            return
        if path == "/api/browse":
            query = parse_qs(urlparse(self.path).query)
            if self.mount_root is None:
                self._send_json({"error": "no folder mounted"}, 409)
                return
            requested = query.get("dir", [str(self.mount_root)])[0]
            try:
                self._send_json(_list_dir(Path(unquote(requested)), self.mount_root))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, 403)
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, 404)
            return
        if path == "/api/browse-img":
            query = parse_qs(urlparse(self.path).query)
            if self.mount_root is None:
                self._send_json({"error": "no folder mounted"}, 409)
                return
            requested = query.get("path", [""])[0]
            target = Path(unquote(requested)).resolve()
            if not _is_under(target, self.mount_root):
                self._send_json({"error": "outside mounted root"}, 403)
                return
            if target.suffix.lower() not in IMAGE_SUFFIXES or not target.is_file():
                self._send_json({"error": "not an image", "path": requested}, 404)
                return
            self._send_file(target)
            return
        if path.startswith("/composer/"):
            self._serve_spa_asset(path[len("/composer/"):])
            return
        # bare "/src/..." and "/composer.css" convenience roots so index.html can
        # reference assets without the /composer/ prefix (matches curator layout).
        if path.startswith("/src/") or path == "/composer.css":
            self._serve_spa_asset(path.lstrip("/"))
            return
        self._send_json({"error": "not found", "path": path}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/mount":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            raw = (payload.get("dir") or "").strip()
            if not raw:
                self._send_json({"error": "dir required"}, 400)
                return
            candidate = Path(raw).expanduser().resolve()
            if not candidate.is_dir():
                self._send_json({"error": f"not a directory: {candidate}"}, 404)
                return
            ComposeHandler.mount_root = candidate
            self._send_json({"mount": str(candidate), **_list_dir(candidate, candidate)})
            return
        if path == "/api/pick":
            kind = parse_qs(urlparse(self.path).query).get("kind", ["folder"])[0]
            try:
                self._send_json(native_pick(kind))
            except NotImplementedError as exc:
                self._send_json({"error": str(exc), "code": "unsupported-platform"}, 501)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, 500)
            return
        if path == "/api/build":
            # Materialize the virtual session into a real run dir via the SSoT
            # importer (import_png_groups). The session is the unsaved buffer; this
            # is the one place references become bytes on disk.
            if self.mount_root is None:
                self._send_json({"error": "no folder open"}, 409)
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            raw_out = (payload.get("outDir") or "").strip()
            if not raw_out:
                self._send_json({"error": "outDir required"}, 400)
                return
            out_dir = Path(raw_out).expanduser().resolve()
            if out_dir.exists() and any(out_dir.iterdir()):
                self._send_json({"error": f"output dir exists and is not empty: {out_dir}",
                                 "code": "out-dir-not-empty"}, 409)
                return
            try:
                groups = _build_groups(payload.get("rows") or [], self.mount_root)
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, 403)
                return
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, 404)
                return
            if not groups:
                self._send_json({"error": "no rows with files to build"}, 400)
                return
            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                summary = import_png_groups(out_dir, groups)
            except Exception as exc:  # surface the importer's failure, do not swallow
                self._send_json({"error": f"build failed: {exc}"}, 500)
                return
            self._send_json({"runDir": str(out_dir), "states": summary.get("states", []),
                             "frames": summary.get("frames", 0), "cell": summary.get("cell")})
            return
        if path == "/api/open-curation":
            # Hand off to the curation view: launch a serve_curation process on the
            # built run dir and return its URL. A curation server is a legitimate
            # long-lived per-run-dir surface, not a workaround.
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, 400)
                return
            run_dir = Path(str(payload.get("runDir", ""))).expanduser().resolve()
            if not (run_dir / "sprite-request.json").is_file():
                self._send_json({"error": f"not a run dir (no sprite-request.json): {run_dir}"}, 404)
                return
            port = _free_port()
            subprocess.Popen(
                [sys.executable, "-m", "sprite_gen.serve.serve_curation",
                 "--run-dir", str(run_dir), "--port", str(port), "--no-open"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if not _wait_ready(port):
                self._send_json({"error": "curation server did not become ready"}, 504)
                return
            self._send_json({"url": f"http://127.0.0.1:{port}/"})
            return
        self._send_json({"error": "not found", "path": path}, 404)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Argument surface for `sprite-gen compose`, `python -m sprite_gen.serve.serve_compose`,
    and any wrapper — declared once so no launch form can drift from another."""
    parser.add_argument("--dir", type=Path, default=None,
                        help="initial folder to mount as the read-only library (optional; can also be mounted in the UI)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--no-open", action="store_true", help="do not auto-open the browser")
    parser.add_argument("--lang", choices=["en", "ko"], default="en",
                        help="initial UI language (toggleable in the webview)")


def run(*, dir: Path | None = None, host: str = "127.0.0.1", port: int = 0,
        no_open: bool = False, lang: str = "en") -> int:
    """Serve the composition canvas until interrupted. Keyword-only, matching the
    `cli.COMMANDS` run-fn convention (`run_fn(**vars(args))`)."""
    if not COMPOSER_DIR.is_dir():
        raise SystemExit(f"missing composer SPA dir: {COMPOSER_DIR}")
    ComposeHandler.mount_root = dir.expanduser().resolve() if dir else None
    ComposeHandler.lang = lang
    if ComposeHandler.mount_root and not ComposeHandler.mount_root.is_dir():
        raise SystemExit(f"not a directory: {ComposeHandler.mount_root}")

    handler = partial(ComposeHandler)
    server = ThreadingHTTPServer((host, port), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"sprite-gen composition canvas: {url}")
    print(f"  mounted: {ComposeHandler.mount_root or '(none — mount a folder in the UI)'}")
    print("  Ctrl-C to stop.")
    if not no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """`python -m sprite_gen.serve.serve_compose` entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
