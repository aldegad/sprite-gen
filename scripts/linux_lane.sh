#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The test gates, run per commit in GitHub-hosted runner jobs the maintainer starts by hand
# (aldegad/sprite-gen-lane's workflow, docs/release.md). Nothing runs on push or pull request.
#
# What the CI job ran, in the same order: a clean virtual environment installed the way the
# quickstart does (`pip install -e ".[dev]"`), the quickstart smoke (prepare -> extract -> compose)
# and the test suite, every test (`--full`) but those of RIFE. The runner has no GPU: the tests
# marked real_rife (the rife-ncnn-vulkan binary) or torch_rife (RIFE's torch backend) are the GPU
# lane's (`scripts/cuda_lane.py` on the maintainer's NVIDIA host), which runs where a change reaches
# them (`scripts/release_gates.py`); the release record says when it did not (docs/release.md).
#
# Run it from a clean clone of the commit being measured, on x86-64 Linux with:
#   - Python 3.14 (`python3.14` on PATH, or named by SPRITE_GEN_LANE_PYTHON),
#   - ffmpeg, and img2webp from libwebp >= 1.5 (its `-exact` flag),
#   - Node.js (`node` on PATH), which runs the curator's JavaScript in tests/effects/test_breathe_*.py.
# The host's tools are not installed by this script: a missing one ends the run by name. The
# environment it creates lives in the clone (.venv, ignored), as the quickstart's does.
#
#   --shard K/N        the K-th of N shares of the tests (tests/conftest.py places each test in one share
#                      by its id), so N jobs side by side run every test once. Each share runs over the
#                      host's processors (`pytest -n auto`, SPRITE_GEN_LANE_WORKERS to name a number).
#                      Without it, one run holds every test.
#   --package <dir>    no tests: package the commit, the wheel and the sdist built from a second, untouched
#                      clone, SHA256SUMS over both, and SOURCE_SHA naming the commit. The workflow runs it
#                      only once every share of the same run passed. `scripts/release_publish.py --prebuilt
#                      <dir>` attaches exactly that (docs/release.md). <dir> must be outside the checkout and
#                      empty.
#
# Where no RIFE is reachable the tests that need one skip for that reason; any other skip fails the run
# (`pytest --rife-unmeasured`) unless tests/conftest.py names it as not RIFE's. A commit has passed the
# lane when every share of one run exited 0.
set -euo pipefail

package_dir=""
shard=""
while [ $# -gt 0 ]; do
  case "$1" in
    --package)
      [ $# -ge 2 ] || { echo "linux_lane: --package needs a directory" >&2; exit 2; }
      package_dir=$2
      shift 2
      ;;
    --shard)
      [ $# -ge 2 ] && [[ "$2" =~ ^[1-9][0-9]*/[1-9][0-9]*$ ]] || { echo "linux_lane: --shard needs K/N" >&2; exit 2; }
      shard=$2
      shift 2
      ;;
    *)
      echo "linux_lane: unknown argument $1" >&2
      exit 2
      ;;
  esac
done

if [ -n "$package_dir" ] && [ -n "$shard" ]; then
  echo "linux_lane: --package runs no tests; it takes no --shard" >&2
  exit 2
fi

PINNED_PYTHON=3.14
PYTHON=${SPRITE_GEN_LANE_PYTHON:-python$PINNED_PYTHON}
WORKERS=${SPRITE_GEN_LANE_WORKERS:-auto}

fail() {
  echo "linux_lane: $*" >&2
  exit 1
}
step() {
  echo "== $*"
}

cd "$(git rev-parse --show-toplevel)"
root=$(pwd -P)

step "preflight"
[ "$(uname -s)" = Linux ] || fail "this lane runs on Linux, not $(uname -s)"
[ "$(uname -m)" = x86_64 ] || fail "this lane runs on x86-64, not $(uname -m)"
command -v "$PYTHON" >/dev/null || fail "$PYTHON is not on PATH (or set SPRITE_GEN_LANE_PYTHON)"
case "$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" in
  "$PINNED_PYTHON") ;;
  *) fail "$PYTHON is $("$PYTHON" --version 2>&1); the lane pins $PINNED_PYTHON" ;;
esac
git diff --quiet && git diff --cached --quiet || fail "the checkout has uncommitted changes"
[ ! -e .venv ] || fail ".venv already exists; run from a fresh clone"
sha=$(git rev-parse HEAD)
echo "commit $sha"

if [ -n "$package_dir" ]; then
  mkdir -p "$package_dir"
  package_dir=$(cd "$package_dir" && pwd -P)
  case "$package_dir/" in
    "$root/"*) fail "--package must be outside the checkout" ;;
  esac
  [ -z "$(ls -A "$package_dir")" ] || fail "--package $package_dir is not empty"
  step "package"
  "$PYTHON" -m venv .venv
  .venv/bin/pip install --quiet build
  # From a second clone of the same commit, so nothing this run wrote (.venv) can reach the archives.
  pkg=$(mktemp -d)
  trap 'rm -rf "$pkg"' EXIT
  git clone -q --no-hardlinks . "$pkg/src"
  git -C "$pkg/src" checkout -q "$sha"
  (cd "$pkg/src" && "$root/.venv/bin/python" -m build --outdir "$package_dir" > "$pkg/build.log" 2>&1) ||
    { tail -20 "$pkg/build.log" >&2; fail "python -m build failed"; }
  (cd "$package_dir" && sha256sum -- *.whl *.tar.gz > SHA256SUMS)
  echo "$sha" > "$package_dir/SOURCE_SHA"
  ls -l "$package_dir"
  cat "$package_dir/SHA256SUMS"
  echo "linux_lane: packaged $sha"
  exit 0
fi

command -v ffmpeg >/dev/null || fail "ffmpeg is not on PATH"
command -v img2webp >/dev/null || fail "img2webp (libwebp >= 1.5) is not on PATH"
# img2webp -version exits 1 without an output file; -h is the honest probe. The probes read
# their whole input (no `grep -q`): under pipefail an early-exiting grep kills the writer with
# SIGPIPE and the pipeline reads as a failure.
img2webp -h 2>&1 | grep -- -exact >/dev/null || fail "img2webp has no -exact flag; libwebp must be 1.5 or newer"
# Without node the curator's JavaScript tests skip, which no run of this lane would measure.
command -v node >/dev/null || fail "node (Node.js) is not on PATH; the curator's JavaScript tests run it"
# Nowhere the engine looks may hold a RIFE: an empty data directory for the suite, nothing on PATH, no
# SPRITE_GEN_RIFE. The tests that need the real binary or the torch backend then skip for the reason they name.
[ -z "${SPRITE_GEN_RIFE:-}" ] || fail "SPRITE_GEN_RIFE is set; this lane runs without RIFE"
! command -v rife-ncnn-vulkan >/dev/null || fail "rife-ncnn-vulkan is on PATH; this lane runs without RIFE"
echo "$("$PYTHON" --version), $(ffmpeg -version | head -1 | cut -d' ' -f1-3), node $(node --version), $(nproc) processors"

step "install (clean environment, as the quickstart does)"
"$PYTHON" -m venv .venv
.venv/bin/pip install --quiet -e ".[dev]"
.venv/bin/pip freeze --exclude-editable

no_rife_data=$(mktemp -d)
export XDG_DATA_HOME=$no_rife_data

step "quickstart smoke (prepare -> extract -> compose)"
smoke=$(mktemp -d)
trap 'rm -rf "$smoke" "$no_rife_data"' EXIT
.venv/bin/python scripts/prepare_sprite_run.py --help > /dev/null
cp -r tests/fixtures/run "$smoke/run"
.venv/bin/python scripts/extract_sprite_row_frames.py --run-dir "$smoke/run"
.venv/bin/python scripts/compose_sprite_atlas.py --run-dir "$smoke/run"
test -f "$smoke/run/sprite-sheet-alpha.png"
test -f "$smoke/run/manifest.json"

step "tests${shard:+ (share $shard)}"
# Every test (--full): a plain pytest is the fast bundle, the tests marked slow deselected. Without RIFE, a
# skip of a test not marked real_rife or torch_rife fails unless tests/conftest.py names it.
# -n: the share over the host's processors; loadgroup keeps a module marked xdist_group on one worker, so what it makes once
# for the module (a pack, a cut) is made once and not once a worker.
.venv/bin/python -m pytest -q --full --rife-unmeasured -n "$WORKERS" --dist loadgroup ${shard:+--shard "$shard"}

echo "linux_lane: every gate${shard:+ of share $shard} passed at $sha"
echo "linux_lane: RIFE is not this lane's: the GPU lane (scripts/cuda_lane.py) runs it where scripts/release_gates.py says a change reaches it"
