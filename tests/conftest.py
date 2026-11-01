# SPDX-License-Identifier: Apache-2.0
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURE_RUN = Path(__file__).resolve().parent / "fixtures" / "run"


def help_options(*argv: str) -> set[str]:
    """Return flags advertised by ``<argv> --help`` without ANSI styling.

    Python 3.14 colorizes argparse output when the surrounding environment asks
    for color. These tests compare the declared option surface, so they pin the
    interpreter-specific override rather than depending on the invoking shell.
    """
    env = {**os.environ, "PYTHON_COLORS": "0"}
    proc = subprocess.run(
        [sys.executable, *argv, "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return set(re.findall(r"(?<![\w-])--[a-z][\w-]*", proc.stdout))


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke a pipeline script exactly as the quickstart does."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / name), *args],
        capture_output=True,
        encoding="utf-8",
    )


def symlink_or_skip(link: Path, target: Path) -> None:
    """Create ``link`` -> ``target``, skipping when the OS refuses symlinks.

    Windows only lets accounts with SeCreateSymbolicLinkPrivilege (or Developer
    Mode) create symlinks; otherwise the call fails with WinError 1314. The skip
    is limited to that creation failure, so a product that mishandles a link
    that *was* created still fails the test.
    """
    try:
        link.symlink_to(target)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("symlink creation needs SeCreateSymbolicLinkPrivilege or Developer Mode on Windows")
        raise


# Tests that skip where no RIFE is reachable for a reason other than RIFE: an input the lane does not
# give. Named without their parameters, or with them where one case alone skips. In the lane's RIFE-less
# run (`pytest --rife-unmeasured`) these and the tests marked real_rife or torch_rife may skip; any other skip
# fails (docs/release.md).
NOT_RIFE_SKIPS = {
    # SPRITE_GEN_CHROMA_REAL_STILLS names model stills, which the repository does not hold.
    "tests/frames/test_chroma_key_relative.py::test_real_still_keys_out_its_painted_border",
    # A folder spelt in capitals is the same folder only on a volume that ignores case (macOS, Windows); the
    # lane's Linux volume tells the two spellings apart, so the layout the case measures cannot be made there.
    "tests/effects/test_breathe_publish.py::test_overlapping_folders_are_refused_before_anything_is_made"
    "[out_inside_frames_spelt_in_capitals]",
    "tests/video/test_loop_inputs_kept.py::test_a_cut_never_writes_over_or_deletes_the_frames_it_reads"
    "[out_spelt_in_capitals]",
    "tests/video/test_frames_inputs_kept.py::test_frames_never_write_over_or_delete_the_clip_or_still_they_read"
    "[source_manifest_spelt_in_capitals]",
    "tests/gen/test_logo.py::test_a_given_lettering_spelt_in_capitals_is_its_own_file_and_is_cut_again",
    "tests/gen/test_video_modes.py::test_the_result_is_never_published_over_the_clip_it_is_made_from[extend-capitals]",
    "tests/gen/test_video_modes.py::test_the_result_is_never_published_over_the_clip_it_is_made_from[edit-capitals]",
    "tests/video/test_follow_inspect.py::test_the_loop_directory_spelt_in_capitals_is_the_loop_directory",
    "tests/video/test_loop_compare.py::test_a_report_spelt_in_capitals_of_an_input_is_refused",
    "tests/video/test_source_restoration.py::test_a_report_spelt_in_capitals_of_an_input_is_refused",
    "tests/video/test_source_restoration.py::test_a_manifest_spelt_in_capitals_of_an_input_is_refused",
    "tests/effects/test_shadow.py::test_an_output_spelt_in_capitals_of_the_source_is_refused",
    # os.kill(pid, 0) is a Ctrl+C to the console only on Windows, so only a Windows console holds what the case
    # measures; the lane's Linux host has none (its POSIX twin, the signal-0 case, runs there).
    "tests/spec/test_run_dir_lock.py::test_a_lock_holders_pid_is_judged_on_windows_without_a_ctrl_c_to_its_console",
    # Only Windows makes the lock entry on a CreateFileW handle handed to a descriptor; on POSIX os.open gives the
    # descriptor itself, so the lane's Linux host has no handle to close (its cross-platform twin, the descriptor case, runs).
    "tests/spec/test_run_dir_lock.py::test_a_lock_entry_handle_that_cannot_become_a_descriptor_is_closed_on_windows",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--rife-unmeasured",
        action="store_true",
        help="the RIFE-less run of scripts/linux_lane.sh: a skip fails unless its test is marked real_rife "
        "or torch_rife, or named in NOT_RIFE_SKIPS (tests/conftest.py)",
    )
    parser.addoption(
        "--full",
        action="store_true",
        help="run every test; without it a run is the fast bundle, the tests marked slow deselected. "
        "The release lane (scripts/linux_lane.sh) runs with it",
    )


def _torch_rife_missing() -> str | None:
    """Why the torch backend cannot run here, None where it can: PyTorch, the model it reads (`rife.locate_model()`,
    rife-v4.25 unless another is named) and the device SPRITE_GEN_RIFE_DEVICE names."""
    from sprite_gen.video import rife

    try:
        import torch
    except ImportError as exc:
        return f'PyTorch not installed (pip install "sprite-gen[torch]"): {exc}'
    try:
        rife.locate_model()
        named = rife.device()
    except rife.RifeUnavailable as exc:
        return f"RIFE's torch backend cannot run here: {exc}"
    if named == "cuda" and not torch.cuda.is_available():
        return "SPRITE_GEN_RIFE_DEVICE is cuda (the default) and PyTorch finds no CUDA here; name SPRITE_GEN_RIFE_DEVICE=cpu"
    return None


def pytest_report_header(config: pytest.Config) -> str | None:
    if not config.getoption("full"):
        return "fast bundle: the tests marked slow are deselected; `pytest --full` runs every test"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Without `--full` the tests marked `slow` are deselected: a plain `pytest` is the fast bundle.

    A test marked `real_rife` runs the real binary: skipped where `rife.locate()` finds none, and run
    by `scripts/linux_lane.sh --rife-only` (`-m real_rife`). A test marked `torch_rife` runs RIFE's torch
    backend: skipped where PyTorch, the model or the device is missing (`_torch_rife_missing`), and run by
    the lanes with none skipped (`-m torch_rife`, docs/release.md)."""
    if not config.getoption("full"):
        slow = [item for item in items if item.get_closest_marker("slow")]
        if slow:
            config.hook.pytest_deselected(items=slow)
            items[:] = [item for item in items if not item.get_closest_marker("slow")]
    marked = [item for item in items if item.get_closest_marker("real_rife")]
    torched = [item for item in items if item.get_closest_marker("torch_rife")]
    if marked:
        from sprite_gen.video import rife

        try:
            rife.locate()
        except rife.RifeUnavailable:
            for item in marked:
                item.add_marker(pytest.mark.skip(reason="rife-ncnn-vulkan not installed (SPRITE_GEN_RIFE / PATH / sprite-gen rife install)"))
    why = _torch_rife_missing() if torched else None
    if why:
        for item in torched:
            item.add_marker(pytest.mark.skip(reason=why))


def _fail_unmeasured_skip(report: pytest.TestReport | pytest.CollectReport, node: pytest.Item | pytest.Collector) -> None:
    """In the lane's RIFE-less run a skip that `--rife-only` will not run fails, by name: neither run would
    measure it. `--rife-only` runs the tests marked real_rife or torch_rife; a skip of any other test, or of
    a whole file, stands only where NOT_RIFE_SKIPS names it. Whatever skipped it: a skip mark, a fixture, the
    test body, the file at import."""
    if not report.skipped or hasattr(report, "wasxfail") or not node.config.getoption("rife_unmeasured", False):
        return
    if (node.get_closest_marker("real_rife") or node.get_closest_marker("torch_rife")
            or {report.nodeid, report.nodeid.partition("[")[0]} & NOT_RIFE_SKIPS):
        return
    path, line, reason = report.longrepr
    report.outcome = "failed"
    report.longrepr = (
        "skipped where no RIFE is reachable, but not marked real_rife or torch_rife: `scripts/linux_lane.sh --rife-only` "
        f"runs only the marked tests, so neither run measures it\n{path}:{line}: {reason}\n"
        "A test that needs the real rife-ncnn-vulkan is marked `real_rife`; one that skips for another "
        "reason is named in NOT_RIFE_SKIPS (tests/conftest.py)."
    )


@pytest.fixture(autouse=True)
def _engine_tta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs the model and augmentation the engine ships (`rife.MODEL`, its `rife.MODELS` augmentation) and its
    backend, the binary, in this process: a SPRITE_GEN_RIFE_MODEL_NAME, SPRITE_GEN_RIFE_TTA, SPRITE_GEN_RIFE_BACKEND or
    SPRITE_GEN_RIFE_EXECUTOR left in the calling shell would change what the real-RIFE tests measure.
    SPRITE_GEN_RIFE_DEVICE stays: it names where the torch backend's tests run, and SPRITE_GEN_RIFE_MODEL where the model
    is (a lane names the default model's directory)."""
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL_NAME", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_TTA", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_BACKEND", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_EXECUTOR", raising=False)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    _fail_unmeasured_skip(report, item)
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    report = yield
    _fail_unmeasured_skip(report, collector)
    return report


@contextmanager
def feet_unseen() -> Generator[None, None, None]:
    """`video-loop`'s step count reads no feet (sprite_gen/video/crossings.py): the cut is the pixel search's alone.
    For the tests of that search, whose walker is drawn for it — legs that open and close once a drawing and never
    pass each other, a scissor no walk makes, which the count reads as one step a drawing — and of the step screen,
    the word where a clip does not show its steps."""
    from sprite_gen.video import crossings

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(crossings, "read", lambda sig: crossings.Reading(len(sig["gap"]), "gap", [], why="the feet read as unseen (test)"))
        yield


def rife_located() -> bool:
    """Whether `rife.locate()` finds rife-ncnn-vulkan here, as the tests marked real_rife ask before they run: where it
    does, a cut's default fill (`--fill-held auto`) makes its frames between with it."""
    from sprite_gen.video import rife

    try:
        rife.locate()
    except rife.RifeUnavailable:
        return False
    return True


@pytest.fixture
def rife_runs(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """The runs of RIFE asked for while the test runs, over every `rife.Rife` located in it (`Rife.calls`: the binary's
    calls), read when called. A test that reaches the binary through a cut's default fill pins its count: each run is
    seconds on a CPU's Vulkan driver, so a change that asks for more fails as a count, not as a test out of time."""
    from sprite_gen.video import rife

    located: list[rife.Rife] = []
    init = rife.Rife.__init__

    def counted(self: rife.Rife, *args: object, **kwargs: object) -> None:
        init(self, *args, **kwargs)
        located.append(self)

    monkeypatch.setattr(rife.Rife, "__init__", counted)
    return lambda: sum(r.calls for r in located)


# The models whose count of the binary's calls a test may hold between two ends, not exactly: rife-v4.25 runs with no
# augmentation, in float16, so its frames differ with the GPU's arithmetic (an Apple GPU and llvmpipe made different ones),
# and with them the frames refused and made again at another fraction. rife-v4.6's counts were the same on every machine
# measured, and are held exactly.
RUNS_BETWEEN = frozenset({"rife-v4.25"})

Pin = int | tuple[int, int]


def as_pinned(called: int, pin: Pin | dict[str, Pin]) -> bool:
    """Whether `called`, the binary's calls a test made (`rife_runs()`, `Rife.calls`), keeps the test's pin for the model
    it ran (`rife.model_name()`; a pin not by model pins every model alike): none where the binary is not found here;
    else an int exactly, and `(floor, top)` from floor to top, both kept. Only a model in `RUNS_BETWEEN` is held between
    two ends, any other is refused one (ValueError). The floor is the calls the cut makes where no frame is refused: two
    for each frame it asks for, each asked once at its own time, the frames a refused run never asked for counted in.
    The top is the most measured on an Apple GPU and on llvmpipe."""
    from sprite_gen.video import rife

    if not rife_located():
        return called == 0
    model = rife.model_name()
    want = pin[model] if isinstance(pin, dict) else pin
    if isinstance(want, int):
        return called == want
    if model not in RUNS_BETWEEN:
        raise ValueError(f"{model}'s calls are pinned exactly, not between {want[0]} and {want[1]}")
    floor, top = want
    return floor <= called <= top


def pin_id(value: object) -> str | None:
    """A parametrized pin by model named by its rife-v4.6 count, the count it pinned before there were two models, so a
    case keeps its id; anything else named as pytest names it."""
    return str(value["rife-v4.6"]) if isinstance(value, dict) else None


@pytest.fixture(autouse=True)
def own_settings(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads the saved preferences or the provider choices of the machine it runs on: what a call that
    names no provider runs on (SPRITE_GEN_DEFAULT_PROVIDER) and what a default codex that cannot run turns into
    (SPRITE_GEN_CODEX_UNAVAILABLE, `sprite-gen defaults`) are each test's own. Child processes inherit both."""
    monkeypatch.setenv("SPRITE_GEN_CONFIG_DIR", str(tmp_path_factory.mktemp("sprite-gen-config")))
    for name in ("SPRITE_GEN_DEFAULT_PROVIDER", "SPRITE_GEN_CODEX_UNAVAILABLE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fixture_run_dir(tmp_path: Path) -> Path:
    """A throwaway copy of the golden fixture run dir."""
    run_dir = tmp_path / "run"
    shutil.copytree(FIXTURE_RUN, run_dir)
    return run_dir
