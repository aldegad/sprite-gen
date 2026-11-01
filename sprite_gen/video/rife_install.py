# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen rife install` — put the pinned RIFE where the engine finds it.

Downloads TNTwise's rife-ncnn-vulkan release zip for this platform (pinned release and SHA-256,
docs/loop-repair.md section 1), checks the hash before anything is unpacked, and keeps only what
the engine runs: the binary, the models `rife-v4.25/` (the default) and `rife-v4.6/`, the licence and
readme (and on Windows the OpenMP runtime the binary loads). The install lands in `rife.install_root()` — the user data
directory — which `rife.locate()` searches after SPRITE_GEN_RIFE and PATH. `--dir` installs
somewhere else and prints the SPRITE_GEN_RIFE line that points the engine at it.

Unpacked into a staging directory and renamed into place, so an interrupted install never leaves
a half-written RIFE where the engine looks; each model's two files are checked against their pinned
SHA-256 (`rife.MODEL_SHA256`) before it is. Running it again over a finished install of the same
zip does nothing but the check. The check makes one frame between two discs through the
installed binary and the model the engine runs (`rife.model_name()`), so a machine that has the files
but no working Vulkan finds out here.

`--backend torch` (the default where SPRITE_GEN_RIFE_BACKEND names torch) installs the same zip for the
torch backend, which reads the model only (`rife.locate_model()`): a Linux machine the binary does not run
on (aarch64) is not refused, and the check frame is made through PyTorch on SPRITE_GEN_RIFE_DEVICE
(`pip install "sprite-gen[torch]"` first; an image built without a GPU checks with SPRITE_GEN_RIFE_DEVICE=cpu).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform as platform_mod
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import rife
from sprite_gen.util.console import utf8_streams

# SHA-256 of each release zip, measured on the downloaded files (docs/loop-repair.md section 1).
ZIP_SHA256 = {
    "macos": "543bf845a6160d8ad29a10c2b7a0713e78781dc2505c62185f7d14c3265bb4c0",
    "ubuntu": "a4166da15a9839350a3695c35d9e19288c5c48c0fc9afc5aacb766d3d7f8ab62",
    "windows": "42ed35e115b026f222386648920218cb8a9c7ae1e23698a7363bdd2e1455aba3",
}
# The folder each zip holds the release in (the Windows zip's is named after the branch it was built from).
ZIP_TOP = {"macos": "macos", "ubuntu": "ubuntu", "windows": "rife-ncnn-vulkan-refs/heads/master-windows"}
# The machines each zip's binary runs on (`platform.machine()`, lower case). The macOS binary is
# universal; the Linux and Windows ones are x86-64 only.
ZIP_MACHINES = {"macos": None, "ubuntu": {"x86_64", "amd64"}, "windows": {"amd64", "x86_64"}}
SOURCE = "github.com/TNTwise/rife-ncnn-vulkan"
URL = "https://github.com/TNTwise/rife-ncnn-vulkan/releases/download/{release}/{name}"
MARKER = ".sprite-gen-install.json"
CHUNK = 1 << 20
TIMEOUT_SECONDS = 60


def zip_name(platform: str) -> str:
    """The release zip's name for a platform, as the release names it."""
    return f"{platform}.zip"


def _platform(platform: str | None, backend: str = "ncnn") -> str:
    name = platform or rife.platform_release()
    if name not in ZIP_SHA256:
        raise SystemExit(f"rife install: rife-ncnn-vulkan {rife.RELEASE} ships no build for {sys.platform}; build it from "
                         f"{SOURCE} and set SPRITE_GEN_RIFE to the binary (docs/loop-repair.md)")
    machines = ZIP_MACHINES[name]
    machine = platform_mod.machine().lower()
    if backend == "ncnn" and platform is None and machines is not None and machine not in machines:
        raise SystemExit(f"rife install: the {name} build of rife-ncnn-vulkan {rife.RELEASE} is x86-64 only and this machine "
                         f"is {machine}; build it from {SOURCE} and set SPRITE_GEN_RIFE to the binary")
    return name


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    """`url` to `dest`, with a progress line on stderr every tenth of the way."""
    request = urllib.request.Request(url, headers={"User-Agent": "sprite-gen"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, dest.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done, shown = 0, 0
        for block in iter(lambda: response.read(CHUNK), b""):
            out.write(block)
            done += len(block)
            if total and done * 10 // total > shown:
                shown = done * 10 // total
                print(f"rife install: {done / 1e6:.0f} / {total / 1e6:.0f} MB", file=sys.stderr, flush=True)


def _kept(member: str, top: str, platform: str) -> bool:
    """Whether a zip member is part of what the engine runs (the rest are other models)."""
    if not member.startswith(top + "/") or member.endswith("/"):
        return False
    rel = member[len(top) + 1:]
    if any(part in ("", ".", "..") for part in rel.split("/")):
        return False
    folder, _, rest = rel.partition("/")
    if rest:
        return folder in rife.MODELS and "/" not in rest
    return rel in (rife.BINARY, rife.BINARY + ".exe", "LICENSE", "README.md") or (platform == "windows" and rel.lower().endswith(".dll"))


def _models_whole(where: Path) -> bool:
    return all((where / m / f).is_file() for m in rife.MODELS for f in ("flownet.param", "flownet.bin"))


def _unpack(zip_path: Path, staging: Path, platform: str) -> list[str]:
    top = ZIP_TOP[platform]
    kept: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if not _kept(info.filename, top, platform):
                continue
            rel = info.filename[len(top) + 1:]
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            kept.append(rel)
    binary = staging / rife.installed_binary(Path("."), platform).name
    if not binary.is_file() or not _models_whole(staging):
        models = " and ".join(f"{m}/flownet.param and flownet.bin" for m in rife.MODELS)
        raise SystemExit(f"rife install: {zip_path.name} has no {top}/{binary.name} with {models}")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return sorted(kept)


def _complete(target: Path, platform: str, sha: str) -> bool:
    try:
        marker = json.loads((target / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    binary = target / rife.installed_binary(Path("."), platform).name
    return marker.get("sha256") == sha and binary.is_file() and _models_whole(target)


def _disc(cx: int) -> Image.Image:
    a = np.zeros((64, 96, 4), dtype=np.uint8)
    yy, xx = np.mgrid[:64, :96]
    a[(yy - 32) ** 2 + (xx - cx) ** 2 <= 12 ** 2] = (200, 60, 40, 255)
    return Image.fromarray(a, "RGBA")


def check(binary: Path, model: Path, *, backend: str = "ncnn") -> dict[str, Any]:
    """One frame half way between two discs through `binary` running `model`, or through the torch backend from `model`:
    the disc must land in the middle."""
    try:
        made = rife.Rife(binary, model) if backend == "ncnn" else rife.Rife(runner=rife.torch_runner(model))
        mid = made(_disc(30), _disc(66), 0.5)
    except rife.RifeUnavailable as exc:
        linux = (" — on Linux without a GPU install `libvulkan1 mesa-vulkan-drivers`"
                 if backend == "ncnn" and sys.platform.startswith("linux") else "")
        raise SystemExit(f"rife install: installed, but the check frame failed: {exc}{linux}") from exc
    xs = np.nonzero(np.asarray(mid)[..., 3] > 127)[1]
    centre = float(xs.mean()) if xs.size else None
    if centre is None or abs(centre - 48) > 4:
        raise SystemExit(f"rife install: installed, but the check frame is wrong (disc centre {centre}, expected 48)")
    return {"passed": True, "disc_centre_x": round(centre, 2)}


def install(*, dest: Path | None = None, zip_path: Path | None = None, platform: str | None = None,
            force: bool = False, run_check: bool = True, backend: str | None = None) -> dict[str, Any]:
    try:
        backend = backend or rife.backend()
    except rife.RifeUnavailable as exc:
        raise SystemExit(f"rife install: {exc}") from exc
    if backend not in rife.BACKENDS:
        raise SystemExit(f"rife install: backend {backend!r}: name ncnn or torch")
    try:
        model_name = rife.model_name()
    except rife.RifeUnavailable as exc:
        raise SystemExit(f"rife install: {exc}") from exc
    name = _platform(platform, backend)
    sha = ZIP_SHA256[name]
    root = (dest or rife.install_root()).expanduser().resolve()
    binary = rife.installed_binary(root, name)
    target = binary.parent
    report: dict[str, Any] = {"backend": backend, "release": rife.RELEASE, "model": model_name, "platform": name,
                              "zip": zip_name(name), "sha256": sha, "dir": str(target), "binary": str(binary)}
    if not force and _complete(target, name, sha):
        report["installed"] = "already"
    else:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".rife-install-", dir=root) as td:
            work = Path(td)
            if zip_path is None:
                url = URL.format(release=rife.RELEASE, name=zip_name(name))
                print(f"rife install: downloading {url}", file=sys.stderr, flush=True)
                zip_path = work / zip_name(name)
                _download(url, zip_path)
                report["source"] = url
            else:
                report["source"] = str(zip_path)
            got = _sha256(zip_path)
            if got != sha:
                raise SystemExit(f"rife install: {zip_path.name} has SHA-256 {got}, expected {sha}; nothing was installed")
            staging = work / target.name
            report["files"] = _unpack(zip_path, staging, name)
            try:
                for model in rife.MODELS:
                    rife.model_bytes(staging / model)
            except rife.RifeUnavailable as exc:
                raise SystemExit(f"rife install: {exc}; nothing was installed") from exc
            (staging / MARKER).write_text(json.dumps({"release": rife.RELEASE, "zip": zip_name(name), "sha256": sha}) + "\n",
                                          encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        report["installed"] = "now"
    if run_check:
        report["check"] = check(binary, target / model_name, backend=backend)
    if backend == "torch":
        # What the torch backend will read: SPRITE_GEN_RIFE_MODEL before the install root.
        try:
            found = str(rife.locate_model())
        except rife.RifeUnavailable:
            found = None
        report["engine_finds"] = found
        if found != str((target / model_name).resolve()):
            report["note"] = (f"the torch backend reads SPRITE_GEN_RIFE_MODEL before {rife.install_root()}; to read this "
                              f"install set SPRITE_GEN_RIFE_MODEL={target / model_name}" + (f" (it now reads {found})" if found else ""))
        return report
    # What the engine will run: SPRITE_GEN_RIFE and PATH are searched before the install root.
    try:
        found = str(rife.locate()[0])
    except rife.RifeUnavailable:
        found = None
    report["engine_finds"] = found
    if found != str(binary.resolve()):
        report["note"] = (f"the engine looks in {rife.install_root()} only after SPRITE_GEN_RIFE and PATH; to run this "
                          f"install set SPRITE_GEN_RIFE={binary}" + (f" (it now finds {found})" if found else ""))
    return report


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="<action>")
    p = sub.add_parser("install", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, help=f"install root (default: the user data directory, {rife.install_root()}; "
                                            "another directory needs SPRITE_GEN_RIFE, which the command prints)")
    p.add_argument("--zip", type=Path, help=f"use this already-downloaded release zip ({SOURCE} {rife.RELEASE}, "
                                            f"{zip_name('<platform>')}) instead of downloading it; "
                                            "its SHA-256 is checked all the same")
    p.add_argument("--force", action="store_true", help="unpack again over a finished install")
    p.add_argument("--no-check", action="store_true", help="skip the check frame made through the installed binary")
    p.add_argument("--backend", choices=rife.BACKENDS, help="what will run RIFE: ncnn (the binary) or torch (the model only, "
                                                            "its check frame through PyTorch on SPRITE_GEN_RIFE_DEVICE); "
                                                            "default SPRITE_GEN_RIFE_BACKEND, else ncnn")


def run(**kwargs: object) -> int:
    if kwargs.get("action") != "install":
        raise SystemExit(f"rife: unknown action {kwargs.get('action')!r}; expected install")
    report = install(dest=kwargs.get("dir"), zip_path=kwargs.get("zip"), force=bool(kwargs.get("force")),  # type: ignore[arg-type]
                     run_check=not kwargs.get("no_check"), backend=kwargs.get("backend"))  # type: ignore[arg-type]
    if report.get("note"):
        print(f"rife install: note: {report['note']}", file=sys.stderr)
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog="sprite-gen rife", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
