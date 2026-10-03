# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen rife install` — put the pinned RIFE where the engine finds it.

Downloads the rife-ncnn-vulkan release zip for this platform (pinned release and SHA-256,
docs/loop-repair.md section 1), checks the hash before anything is unpacked, and keeps only what
the engine runs: the binary, the model `rife-v4.6/`, the licence and readme (and on Windows the
OpenMP runtime the binary loads). The install lands in `rife.install_root()` — the user data
directory — which `rife.locate()` searches after SPRITE_GEN_RIFE and PATH. `--dir` installs
somewhere else and prints the SPRITE_GEN_RIFE line that points the engine at it.

Unpacked into a staging directory and renamed into place, so an interrupted install never leaves
a half-written RIFE where the engine looks. Running it again over a finished install of the same
zip does nothing but the check. The check makes one frame between two discs through the
installed binary, so a machine that has the files but no working Vulkan finds out here.
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

# SHA-256 of each release zip, measured on the downloaded files (docs/loop-repair.md section 1).
ZIP_SHA256 = {
    "macos": "4a63a1f3c9c715773c57d2ee51df1b315ed20cd6c63103e45c483ecc4400b595",
    "ubuntu": "1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68",
    "windows": "d8e4d772d26cd8006ef0ad0bc82eb191b53c68677d1ae2f42506d74cbbbea606",
}
# The machines each zip's binary runs on (`platform.machine()`, lower case). The macOS binary is
# universal; the Linux and Windows ones are x86-64 only.
ZIP_MACHINES = {"macos": None, "ubuntu": {"x86_64", "amd64"}, "windows": {"amd64", "x86_64"}}
URL = "https://github.com/nihui/rife-ncnn-vulkan/releases/download/{release}/{name}"
MARKER = ".sprite-gen-install.json"
CHUNK = 1 << 20
TIMEOUT_SECONDS = 60


def zip_name(platform: str) -> str:
    return f"{rife.BINARY}-{rife.RELEASE}-{platform}.zip"


def _platform(platform: str | None) -> str:
    name = platform or rife.platform_release()
    if name not in ZIP_SHA256:
        raise SystemExit(f"rife install: rife-ncnn-vulkan {rife.RELEASE} ships no build for {sys.platform}; build it from "
                         f"github.com/nihui/rife-ncnn-vulkan and set SPRITE_GEN_RIFE to the binary (docs/loop-repair.md)")
    machines = ZIP_MACHINES[name]
    machine = platform_mod.machine().lower()
    if platform is None and machines is not None and machine not in machines:
        raise SystemExit(f"rife install: the {name} build of rife-ncnn-vulkan {rife.RELEASE} is x86-64 only and this machine "
                         f"is {machine}; build it from github.com/nihui/rife-ncnn-vulkan and set SPRITE_GEN_RIFE to the binary")
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
    if rel.startswith(rife.MODEL + "/"):
        return "/" not in rel[len(rife.MODEL) + 1:]
    if "/" in rel:
        return False
    return rel in (rife.BINARY, rife.BINARY + ".exe", "LICENSE", "README.md") or (platform == "windows" and rel.lower().endswith(".dll"))


def _unpack(zip_path: Path, staging: Path, platform: str) -> list[str]:
    top = f"{rife.BINARY}-{rife.RELEASE}-{platform}"
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
    model = staging / rife.MODEL
    if not binary.is_file() or not (model / "flownet.param").is_file() or not (model / "flownet.bin").is_file():
        raise SystemExit(f"rife install: {zip_path.name} has no {top}/{binary.name} with {rife.MODEL}/flownet.param and flownet.bin")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return sorted(kept)


def _complete(target: Path, platform: str, sha: str) -> bool:
    try:
        marker = json.loads((target / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    binary = target / rife.installed_binary(Path("."), platform).name
    return (marker.get("sha256") == sha and binary.is_file()
            and all((target / rife.MODEL / f).is_file() for f in ("flownet.param", "flownet.bin")))


def _disc(cx: int) -> Image.Image:
    a = np.zeros((64, 96, 4), dtype=np.uint8)
    yy, xx = np.mgrid[:64, :96]
    a[(yy - 32) ** 2 + (xx - cx) ** 2 <= 12 ** 2] = (200, 60, 40, 255)
    return Image.fromarray(a, "RGBA")


def check(binary: Path, model: Path) -> dict[str, Any]:
    """One frame half way between two discs through `binary`: the disc must land in the middle."""
    try:
        mid = rife.Rife(binary, model)(_disc(30), _disc(66), 0.5)
    except rife.RifeUnavailable as exc:
        linux = " — on Linux without a GPU install `libvulkan1 mesa-vulkan-drivers`" if sys.platform.startswith("linux") else ""
        raise SystemExit(f"rife install: installed, but the check frame failed: {exc}{linux}") from exc
    xs = np.nonzero(np.asarray(mid)[..., 3] > 127)[1]
    centre = float(xs.mean()) if xs.size else None
    if centre is None or abs(centre - 48) > 4:
        raise SystemExit(f"rife install: installed, but the check frame is wrong (disc centre {centre}, expected 48)")
    return {"passed": True, "disc_centre_x": round(centre, 2)}


def install(*, dest: Path | None = None, zip_path: Path | None = None, platform: str | None = None,
            force: bool = False, run_check: bool = True) -> dict[str, Any]:
    name = _platform(platform)
    sha = ZIP_SHA256[name]
    root = (dest or rife.install_root()).expanduser().resolve()
    binary = rife.installed_binary(root, name)
    target = binary.parent
    report: dict[str, Any] = {"release": rife.RELEASE, "model": rife.MODEL, "platform": name, "zip": zip_name(name),
                              "sha256": sha, "dir": str(target), "binary": str(binary)}
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
            (staging / MARKER).write_text(json.dumps({"release": rife.RELEASE, "zip": zip_name(name), "sha256": sha}) + "\n",
                                          encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        report["installed"] = "now"
    if run_check:
        report["check"] = check(binary, target / rife.MODEL)
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
    p.add_argument("--zip", type=Path, help=f"use this already-downloaded release zip ({zip_name('<platform>')}) instead of downloading it; "
                                            "its SHA-256 is checked all the same")
    p.add_argument("--force", action="store_true", help="unpack again over a finished install")
    p.add_argument("--no-check", action="store_true", help="skip the check frame made through the installed binary")


def run(**kwargs: object) -> int:
    if kwargs.get("action") != "install":
        raise SystemExit(f"rife: unknown action {kwargs.get('action')!r}; expected install")
    report = install(dest=kwargs.get("dir"), zip_path=kwargs.get("zip"), force=bool(kwargs.get("force")),  # type: ignore[arg-type]
                     run_check=not kwargs.get("no_check"))
    if report.get("note"):
        print(f"rife install: note: {report['note']}", file=sys.stderr)
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen rife", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
