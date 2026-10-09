#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The test gates, run on the maintainer's own Linux runner. GitHub-hosted CI is not used.
#
# What the CI job ran, in the same order: a clean virtual environment installed the way the
# quickstart does (`pip install -e ".[dev]"`), RIFE installed through the user's own path
# (`sprite-gen rife install`, its check frame on the host's Vulkan driver), the quickstart smoke
# (prepare -> extract -> compose) and the whole test suite.
#
# Run it from a clean clone of the commit being measured, on x86-64 Linux with:
#   - Python 3.14 (`python3.14` on PATH, or named by SPRITE_GEN_LANE_PYTHON),
#   - ffmpeg, and img2webp from libwebp >= 1.5 (its `-exact` flag),
#   - a Vulkan loader with a CPU driver (Mesa lavapipe is enough).
# The host's tools are not installed by this script: a missing one ends the run by name. The
# environment it creates lives in the clone (.venv, ignored), as the quickstart's does.
#
# `--artifact <dir>` also packages the commit after the gates passed: the wheel and the sdist
# built from a second, untouched clone, SHA256SUMS over both, and SOURCE_SHA naming the commit.
# `scripts/release_publish.py --prebuilt <dir>` attaches exactly that (docs/release.md). <dir>
# must be outside the checkout and empty.
#
# RIFE needs a Vulkan driver that can run rife-ncnn-vulkan. On a host whose driver cannot (Mesa
# lavapipe under WSL1 crashes in rife-ncnn-vulkan's first upload), the RIFE gates are split off,
# never passed over in silence:
#   --rife-unmeasured "<why>"  every gate but RIFE: no `rife install`, and the suite runs with an
#                              empty data directory and no RIFE on PATH, so the tests that need
#                              the real binary (marked `real_rife`) skip for that stated reason. The
#                              run ends by naming what it did not measure and exits 3, not 0.
#   --rife-only                the RIFE gates alone, on a host where Vulkan works: `rife install`
#                              (its check frame) and every test of the suite marked `real_rife`,
#                              none of it skipped. The mark is the suite's only RIFE guard
#                              (tests/conftest.py), so these are the tests `--rife-unmeasured`
#                              skips for RIFE; tests/release/test_lane_rife_collection.py fails on
#                              a test RIFE decides that is not marked.
# A commit has passed the lane when one run exits 0, or when a `--rife-unmeasured` run (exit 3)
# and a `--rife-only` run (exit 0) of the same commit both did.
set -euo pipefail

artifact_dir=""
rife_unmeasured=""
rife_only=0
while [ $# -gt 0 ]; do
  case "$1" in
    --artifact)
      [ $# -ge 2 ] || { echo "linux_lane: --artifact needs a directory" >&2; exit 2; }
      artifact_dir=$2
      shift 2
      ;;
    --rife-unmeasured)
      [ $# -ge 2 ] && [ -n "$2" ] || { echo "linux_lane: --rife-unmeasured needs the reason" >&2; exit 2; }
      rife_unmeasured=$2
      shift 2
      ;;
    --rife-only)
      rife_only=1
      shift
      ;;
    *)
      echo "linux_lane: unknown argument $1" >&2
      exit 2
      ;;
  esac
done

if [ "$rife_only" = 1 ] && { [ -n "$rife_unmeasured" ] || [ -n "$artifact_dir" ]; }; then
  echo "linux_lane: --rife-only measures RIFE alone; it takes neither --rife-unmeasured nor --artifact" >&2
  exit 2
fi

PINNED_PYTHON=3.14
PYTHON=${SPRITE_GEN_LANE_PYTHON:-python$PINNED_PYTHON}

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
command -v ffmpeg >/dev/null || fail "ffmpeg is not on PATH"
command -v img2webp >/dev/null || fail "img2webp (libwebp >= 1.5) is not on PATH"
# img2webp -version exits 1 without an output file; -h is the honest probe. The probes read
# their whole input (no `grep -q`): under pipefail an early-exiting grep kills the writer with
# SIGPIPE and the pipeline reads as a failure.
img2webp -h 2>&1 | grep -- -exact >/dev/null || fail "img2webp has no -exact flag; libwebp must be 1.5 or newer"
/sbin/ldconfig -p | grep 'libvulkan\.so\.1' >/dev/null || fail "no Vulkan loader (libvulkan.so.1)"
git diff --quiet && git diff --cached --quiet || fail "the checkout has uncommitted changes"
[ ! -e .venv ] || fail ".venv already exists; run from a fresh clone"
if [ -n "$artifact_dir" ]; then
  mkdir -p "$artifact_dir"
  artifact_dir=$(cd "$artifact_dir" && pwd -P)
  case "$artifact_dir/" in
    "$root/"*) fail "--artifact must be outside the checkout" ;;
  esac
  [ -z "$(ls -A "$artifact_dir")" ] || fail "--artifact $artifact_dir is not empty"
fi
sha=$(git rev-parse HEAD)
echo "commit $sha"
echo "$("$PYTHON" --version), $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"

step "install (clean environment, as the quickstart does)"
"$PYTHON" -m venv .venv
.venv/bin/pip install --quiet -e ".[dev]"
.venv/bin/pip freeze --exclude-editable

if [ "$rife_only" = 1 ]; then
  step "RIFE (sprite-gen rife install)"
  .venv/bin/sprite-gen rife install
  step "RIFE tests (every test marked real_rife, none skipped)"
  rife_log=$(mktemp)
  trap 'rm -f "$rife_log"' EXIT
  # No test marked: pytest exits 5 ("no tests ran"), and the lane with it.
  .venv/bin/python -m pytest -q -rs -m real_rife | tee "$rife_log"
  ! grep -E '^SKIPPED' "$rife_log" >/dev/null || fail "a RIFE test skipped; this run is meant to measure them"
  echo "linux_lane: the RIFE gates passed at $sha"
  exit 0
fi

if [ -n "$rife_unmeasured" ]; then
  step "RIFE: not measured on this host ($rife_unmeasured)"
  # Nowhere the engine looks may hold a RIFE: an empty data directory for the suite, nothing on
  # PATH, no SPRITE_GEN_RIFE. The real-binary tests then skip for the reason they name.
  [ -z "${SPRITE_GEN_RIFE:-}" ] || fail "SPRITE_GEN_RIFE is set; --rife-unmeasured runs without RIFE"
  ! command -v rife-ncnn-vulkan >/dev/null || fail "rife-ncnn-vulkan is on PATH; --rife-unmeasured runs without RIFE"
  no_rife_data=$(mktemp -d)
  export XDG_DATA_HOME=$no_rife_data
else
  step "RIFE (sprite-gen rife install)"
  .venv/bin/sprite-gen rife install
fi

step "quickstart smoke (prepare -> extract -> compose)"
smoke=$(mktemp -d)
trap 'rm -rf "$smoke" "${no_rife_data:-}"' EXIT
.venv/bin/python scripts/prepare_sprite_run.py --help > /dev/null
cp -r tests/fixtures/run "$smoke/run"
.venv/bin/python scripts/extract_sprite_row_frames.py --run-dir "$smoke/run"
.venv/bin/python scripts/compose_sprite_atlas.py --run-dir "$smoke/run"
test -f "$smoke/run/sprite-sheet-alpha.png"
test -f "$smoke/run/manifest.json"

step "tests"
.venv/bin/python -m pytest -q

if [ -n "$rife_unmeasured" ]; then
  echo "linux_lane: every gate but RIFE passed at $sha"
  echo "linux_lane: RIFE NOT MEASURED here ($rife_unmeasured): run \`scripts/linux_lane.sh --rife-only\` at $sha on a host where Vulkan works"
else
  echo "linux_lane: every gate passed at $sha"
fi

if [ -n "$artifact_dir" ]; then
  step "package"
  # From a second clone of the same commit, so nothing the gates wrote (.venv, egg-info, run
  # outputs) can reach the archives.
  pkg=$(mktemp -d)
  trap 'rm -rf "$smoke" "$pkg" "${no_rife_data:-}"' EXIT
  git clone -q --no-hardlinks . "$pkg/src"
  git -C "$pkg/src" checkout -q "$sha"
  .venv/bin/pip install --quiet build
  (cd "$pkg/src" && "$root/.venv/bin/python" -m build --outdir "$artifact_dir" > "$pkg/build.log" 2>&1) ||
    { tail -20 "$pkg/build.log" >&2; fail "python -m build failed"; }
  (cd "$artifact_dir" && sha256sum -- *.whl *.tar.gz > SHA256SUMS)
  echo "$sha" > "$artifact_dir/SOURCE_SHA"
  ls -l "$artifact_dir"
  cat "$artifact_dir/SHA256SUMS"
fi

if [ -n "$rife_unmeasured" ]; then
  exit 3
fi
