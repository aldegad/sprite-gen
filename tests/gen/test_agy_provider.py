# SPDX-License-Identifier: Apache-2.0
"""Antigravity (`agy`) provider contract, offline: no CLI, no Google account, no network.

The live end-to-end run is exercised by hand on a machine with `agy` installed and
signed in (see docs/gen.md). Here we lock the deterministic seams: the spawn shape,
the JSON-envelope contract, the prose path parser, the "bytes on disk decide"
publication rule, and the orchestrator wiring (registration + transparency strategy).
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen import gen
from sprite_gen.gen import agy_provider as agy
from sprite_gen.gen.base import (
    TRANSPARENCY_CHROMA,
    TRANSPARENCY_NATIVE,
    GenRequest,
    GenTimeoutError,
)


def _png_bytes(color=(10, 200, 40, 255), size=(4, 4)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _envelope(response: str, *, status: str = "SUCCESS") -> str:
    return json.dumps(
        {
            "conversation_id": "479d0f29-6a7b-4250-bb21-4888554201b6",
            "status": status,
            "response": response,
            "duration_seconds": 62.96,
            "num_turns": 1,
            "usage": {"input_tokens": 77463, "output_tokens": 3715, "total_tokens": 81178},
        }
    )


class _Completed:
    def __init__(self, stdout: str, *, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def spawn(monkeypatch):
    """Stand in for `agy`. `state["writes"]` decides what lands on disk."""
    state: dict = {"cmd": None, "kwargs": None, "writes": [], "stdout": None, "raise": None}

    def fake_run(cmd, **kwargs):
        state["cmd"] = cmd
        state["kwargs"] = kwargs
        if state["raise"] is not None:
            raise state["raise"]
        for path, data in state["writes"]:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(data)
        return _Completed(state["stdout"] or _envelope("done"))

    monkeypatch.setattr(agy.subprocess, "run", fake_run)
    return state


# --- registration and strategy ------------------------------------------------


def test_agy_is_a_registered_provider_and_builds() -> None:
    assert "agy" in gen.PROVIDERS
    backend = gen._make_provider("agy", keep_session=False)
    assert isinstance(backend, agy.AgyProvider)
    assert backend.name == "agy"


def test_transparency_is_chroma_so_sprite_gen_owns_the_matte() -> None:
    """agy cannot return alpha; `key_transparent` — our tested keyer — makes it.

    The rejected alternative was `native`, letting the agent run rembg itself. It
    worked once and is non-deterministic by construction, so the declaration routes
    the raw PNG through sprite-gen's one extraction path instead.
    """
    backend = agy.AgyProvider()
    assert backend.transparency == TRANSPARENCY_CHROMA
    assert gen.resolve_transparency_strategy(backend, gen.ALPHA_MODE_AUTO) == (
        TRANSPARENCY_CHROMA,
        gen.STRATEGY_SOURCE_PROVIDER,
    )
    # refs change nothing: the step-down exists to avoid unreliable native alpha,
    # and there is no native alpha here to step down from.
    assert gen.resolve_transparency_strategy(
        backend, gen.ALPHA_MODE_AUTO, refs=[Path("ref.png")]
    ) == (TRANSPARENCY_CHROMA, gen.STRATEGY_SOURCE_PROVIDER)


def test_alpha_mode_native_is_refused_before_any_model_call() -> None:
    with pytest.raises(SystemExit, match="not a capability of provider 'agy'"):
        gen.resolve_transparency_strategy(agy.AgyProvider(), TRANSPARENCY_NATIVE)


def test_a_direct_native_alpha_request_is_refused_without_spawning(tmp_path, spawn) -> None:
    with pytest.raises(SystemExit, match="cannot return an alpha channel"):
        agy.AgyProvider().generate(
            GenRequest(prompt="p", raw=tmp_path / "raw.png", native_alpha=True), tmp_path
        )
    assert spawn["cmd"] is None


# --- spawn shape --------------------------------------------------------------


def test_spawn_shape_names_the_destination_and_adds_its_directory(tmp_path, spawn) -> None:
    raw = tmp_path / "work" / "raw.png"
    spawn["writes"] = [(raw, _png_bytes())]
    run = agy.AgyProvider().generate(
        GenRequest(prompt="a red apple", raw=raw, model="some-model"), tmp_path
    )

    cmd = spawn["cmd"]
    # `provider_binary` resolves PATH/PATHEXT shims, so the argv[0] is a real path on a
    # machine that has agy installed and the bare name on one that does not.
    assert "agy" in Path(cmd[0]).stem.lower()
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--add-dir") + 1] == str(raw.parent)
    assert cmd[cmd.index("--model") + 1] == "some-model"
    prompt = cmd[cmd.index("-p") + 1]
    assert str(raw) in prompt, "the exact destination is stated in the prompt"
    assert "a red apple" in prompt, "the caller's prompt is passed through verbatim"
    # The never-self-matte rule rides every run (see the non-transparent test below);
    # only the colour-naming half is conditional, and no key was requested here.
    assert "Do NOT run rembg" in prompt
    assert "chroma-key backdrop" not in prompt
    assert spawn["kwargs"]["timeout"] == agy.AGY_GEN_TIMEOUT_SECONDS
    assert spawn["kwargs"]["cwd"] == str(tmp_path)
    assert run.provider == "agy" and run.model == "some-model"


@pytest.mark.parametrize("key, hex_value", [("magenta", "#FF00FF"), ("green", "#00FF00")])
def test_chroma_run_demands_a_flat_key_backdrop_and_forbids_self_matting(
    tmp_path, spawn, key, hex_value
) -> None:
    raw = tmp_path / "raw.png"
    spawn["writes"] = [(raw, _png_bytes())]
    agy.AgyProvider().generate(
        GenRequest(prompt="a red apple", raw=raw, chroma_key=key), tmp_path
    )
    prompt = spawn["cmd"][spawn["cmd"].index("-p") + 1]
    # Positive half: the exact key the orchestrator will matte out, named and hexed.
    assert hex_value in prompt and key in prompt
    assert "FLAT" in prompt and "UNIFORM" in prompt
    # Negative half: the measured failure mode is the agent helpfully matting for us.
    assert "Do NOT run rembg" in prompt
    assert "OPAQUE" in prompt
    assert "checkerboard" in prompt


def test_a_non_transparent_run_still_forbids_self_matting(tmp_path, spawn) -> None:
    """Regression: the anti-rembg half must NOT be gated on `chroma_key`.

    `gen_set.run_gen_cli`, `reroll` and `interpolate.gen_interpolator` all call
    `generate_image` without `transparent=True` (their prompts carry the run's own key
    and `extract` mattes later), so gating this half on the key left every pipeline row
    unprotected against the exact unasked-for segmentation this design exists to stop.
    """
    raw = tmp_path / "raw.png"
    spawn["writes"] = [(raw, _png_bytes())]
    agy.AgyProvider().generate(GenRequest(prompt="a red apple on #00FF00", raw=raw), tmp_path)
    prompt = spawn["cmd"][spawn["cmd"].index("-p") + 1]
    assert "Do NOT run rembg" in prompt and "OPAQUE" in prompt
    # …but no colour is named: the caller's prompt owns the backdrop here, and gen's
    # own `chroma_key` default would be a guess that could contradict a green run.
    assert "chroma-key backdrop" not in prompt
    assert "#FF00FF" not in prompt and "magenta" not in prompt
    assert "a red apple on #00FF00" in prompt


def test_the_row_pipeline_default_call_still_carries_the_anti_rembg_rule(tmp_path, monkeypatch) -> None:
    """End-to-end through `generate_image` with NO `transparent` argument at all.

    This is exactly how `gen_set` / `reroll` / `interpolate` invoke it.
    """
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["prompt"] = cmd[cmd.index("-p") + 1]
        Path(kwargs["cwd"], "raw.png").write_bytes(_png_bytes())
        return _Completed(_envelope("saved"))

    monkeypatch.setattr(agy.subprocess, "run", fake_run)
    gen.generate_image("agy", "a walk row on flat green", tmp_path / "out.png")
    assert "Do NOT run rembg" in seen["prompt"]
    assert "segmentation, matting or cutout tool" in seen["prompt"]


def test_an_unknown_chroma_key_fails_before_the_model_runs(tmp_path, spawn) -> None:
    with pytest.raises(SystemExit, match="unknown chroma key 'cyan'"):
        agy.AgyProvider().generate(
            GenRequest(prompt="p", raw=tmp_path / "raw.png", chroma_key="cyan"), tmp_path
        )
    assert spawn["cmd"] is None


def test_the_orchestrator_hands_the_provider_the_key_it_will_matte_out(tmp_path, monkeypatch) -> None:
    """The painted backdrop and the matte must come from one decision, not two."""
    seen: dict = {}

    class _Recorder:
        name = "agy"
        transparency = TRANSPARENCY_CHROMA

        def generate(self, request, workdir):
            seen["chroma_key"] = request.chroma_key
            seen["native_alpha"] = request.native_alpha
            # Paint the key the orchestrator asked for, so key_transparent succeeds.
            Image.new("RGB", (8, 8), (0, 255, 0)).save(request.raw)
            from sprite_gen.gen.base import ProviderRun

            return ProviderRun(provider="agy", elapsed_seconds=0.1)

    monkeypatch.setattr(gen, "_make_provider", lambda *a, **k: _Recorder())
    result = gen.generate_image(
        "agy", "p", tmp_path / "out.png", transparent=True, chroma_key="green"
    )
    assert seen == {"chroma_key": "green", "native_alpha": False}
    assert result.alpha["strategy"] == "chroma" and result.chroma["key"] == "green"


def test_no_chroma_key_is_passed_when_the_run_is_not_transparent(tmp_path, spawn) -> None:
    raw = tmp_path / "raw.png"
    spawn["writes"] = [(raw, _png_bytes())]
    run = agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), tmp_path)
    assert run.extra["chroma_key_requested"] is None


def test_references_are_added_to_the_workspace_and_named_in_the_prompt(tmp_path, spawn) -> None:
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    ref = refs_dir / "identity.png"
    ref.write_bytes(_png_bytes())
    raw = tmp_path / "raw.png"
    spawn["writes"] = [(raw, _png_bytes())]

    agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw, refs=[ref]), tmp_path)

    cmd = spawn["cmd"]
    add_dirs = [cmd[i + 1] for i, token in enumerate(cmd) if token == "--add-dir"]
    assert add_dirs == [str(raw.parent), str(refs_dir)], "output dir plus each ref dir, deduplicated"
    assert str(ref.resolve()) in cmd[cmd.index("-p") + 1]


def test_missing_reference_fails_before_the_spawn(tmp_path, spawn) -> None:
    with pytest.raises(SystemExit, match="reference image not found"):
        agy.AgyProvider().generate(
            GenRequest(prompt="p", raw=tmp_path / "raw.png", refs=[tmp_path / "nope.png"]),
            tmp_path,
        )
    assert spawn["cmd"] is None


def test_provider_run_uses_the_scrubbed_env_and_resolved_binary(tmp_path, spawn, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_RUNTIME_ENDPOINT_ID", "synthetic-endpoint")
    monkeypatch.setattr(agy, "provider_binary", lambda _name: "C:/bin/agy.CMD")
    raw = tmp_path / "raw.png"
    spawn["writes"] = [(raw, _png_bytes())]

    agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), tmp_path)

    assert spawn["cmd"][0] == "C:/bin/agy.CMD"
    assert "ORCHESTRATOR_RUNTIME_ENDPOINT_ID" not in spawn["kwargs"]["env"]
    assert spawn["kwargs"]["encoding"] == "utf-8"


# --- success reporting --------------------------------------------------------


def test_success_publishes_the_named_file_and_reports_the_envelope(tmp_path, spawn) -> None:
    raw = tmp_path / "raw.png"
    spawn["writes"] = [(raw, _png_bytes())]
    spawn["stdout"] = _envelope(f"Saved it.\n{raw}\n")

    run = agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), tmp_path)

    assert raw.read_bytes() == _png_bytes()
    assert run.session_id == "479d0f29-6a7b-4250-bb21-4888554201b6"
    assert run.extra["transport"] == "agy-cli"
    assert run.extra["num_turns"] == 1
    assert run.extra["usage"]["total_tokens"] == 81178
    assert run.extra["agy_duration_seconds"] == 62.96
    assert run.extra["reported_paths"] == [str(raw)]
    assert run.extra["recovered_from"] is None
    assert run.extra["chroma_key_requested"] is None
    assert run.elapsed_seconds >= 0


def test_a_stale_destination_from_an_earlier_run_is_never_republished(tmp_path, spawn) -> None:
    """`--workdir` can be reused; only a file THIS run wrote may be published."""
    raw = tmp_path / "raw.png"
    raw.write_bytes(_png_bytes(color=(255, 0, 0, 255)))  # left over from a previous attempt
    spawn["writes"] = []  # this run writes nothing

    with pytest.raises(SystemExit, match="no usable generated image"):
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), tmp_path)
    assert not raw.exists()


def test_a_file_saved_elsewhere_is_recovered_from_the_reported_path(tmp_path, spawn) -> None:
    raw = tmp_path / "raw.png"
    elsewhere = tmp_path / "image test" / "boy.png"
    spawn["writes"] = [(elsewhere, _png_bytes())]
    spawn["stdout"] = _envelope(
        "Here you go: [boy.png](file:///"
        + str(elsewhere).replace("\\", "/").replace(" ", "%20")
        + ")\n"
    )

    run = agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), tmp_path)

    assert raw.read_bytes() == _png_bytes()
    assert run.extra["recovered_from"], "the recovery route is recorded, not silent"


# --- failure modes ------------------------------------------------------------


def test_non_success_status_fails_loudly(tmp_path, spawn) -> None:
    spawn["stdout"] = _envelope("quota exhausted", status="ERROR")
    with pytest.raises(SystemExit, match="status='ERROR'"):
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=tmp_path / "raw.png"), tmp_path)


def test_success_without_any_file_fails_and_names_what_was_claimed(tmp_path, spawn) -> None:
    spawn["stdout"] = _envelope("All done! Saved to C:/nowhere/ghost.png\n")
    with pytest.raises(SystemExit) as exc:
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=tmp_path / "raw.png"), tmp_path)
    assert "no usable generated image" in str(exc.value)
    assert "ghost.png" in str(exc.value)


def test_non_png_bytes_at_the_destination_are_refused(tmp_path, spawn) -> None:
    raw = tmp_path / "raw.png"
    spawn["writes"] = [(raw, b"JFIF not a png")]
    with pytest.raises(SystemExit, match="not a PNG"):
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), tmp_path)


def test_non_zero_exit_reports_stderr(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        agy.subprocess,
        "run",
        lambda cmd, **kw: _Completed("", returncode=2, stderr="not signed in"),
    )
    with pytest.raises(SystemExit, match="agy exited 2"):
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=tmp_path / "raw.png"), tmp_path)


def test_missing_json_envelope_fails_instead_of_guessing(tmp_path, spawn) -> None:
    spawn["stdout"] = "I saved the image, trust me.\n"
    with pytest.raises(SystemExit, match="printed no JSON envelope"):
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=tmp_path / "raw.png"), tmp_path)


def test_envelope_survives_a_banner_line_before_the_json(tmp_path, spawn) -> None:
    raw = tmp_path / "raw.png"
    spawn["writes"] = [(raw, _png_bytes())]
    spawn["stdout"] = "A new version of agy is available!\n" + _envelope(str(raw))
    run = agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), tmp_path)
    assert run.extra["num_turns"] == 1


def test_timeout_raises_the_observable_gen_timeout(tmp_path, spawn) -> None:
    spawn["raise"] = subprocess.TimeoutExpired(cmd="agy", timeout=agy.AGY_GEN_TIMEOUT_SECONDS)
    with pytest.raises(GenTimeoutError) as exc:
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=tmp_path / "raw.png"), tmp_path)
    # The message must name the measured normal duration and the real stall cause.
    assert "~63s" in str(exc.value) and "always-proceed" in str(exc.value)


def test_the_timeout_clears_agys_own_print_timeout(tmp_path) -> None:
    """agy's own `--print-timeout` default is 5m; our hard kill must sit above it."""
    assert agy.AGY_GEN_TIMEOUT_SECONDS > 300


# --- prose path parsing -------------------------------------------------------


@pytest.mark.parametrize(
    "response, expected",
    [
        ("D:\\runs\\raw.png\n", "D:\\runs\\raw.png"),
        ("  `/home/u/out/raw.png`  \n", "/home/u/out/raw.png"),
        ("see [boy.png](file:///D:/a/image%20test/boy.png)", "D:/a/image test/boy.png"),
        ("**File:** boy.png\n**Folder:** D:/a/out\n", "D:/a/out/boy.png"),
        ("saved [it](D:/a/out/boy.webp) ok", "D:/a/out/boy.webp"),
    ],
)
def test_reported_paths_reads_every_observed_prose_shape(response, expected) -> None:
    found = agy.reported_paths(response)
    assert any(Path(p) == Path(expected) for p in found), found


def test_reported_paths_is_deduplicated_and_ignores_non_images() -> None:
    response = (
        "I ran `pip install rembg` then wrote /out/raw.png\n"
        "[raw.png](file:///out/raw.png)\n"
        "Logs are at /out/session.log\n"
    )
    found = agy.reported_paths(response)
    assert [Path(p) for p in found] == [Path("/out/raw.png")]


def test_file_folder_details_pair_by_position_not_by_cross_product() -> None:
    """Regression: two details blocks must not produce file A under folder B.

    A cross-joined path that happens to name a real file inside an `--add-dir`
    workspace would be copied out and published as the result, and `verify_png` only
    proves it is a PNG — not that it is the right one.
    """
    response = "\n".join(
        [
            "First save:",
            "**File:** apple.png",
            "**Folder:** D:/out/one",
            "",
            "Second save:",
            "**File:** pear.png",
            "**Folder:** D:/out/two",
            "",
        ]
    )
    found = [Path(p) for p in agy.reported_paths(response)]
    assert Path("D:/out/one/apple.png") in found
    assert Path("D:/out/two/pear.png") in found
    assert Path("D:/out/two/apple.png") not in found
    assert Path("D:/out/one/pear.png") not in found


def test_folder_before_file_still_pairs_correctly() -> None:
    response = "\n".join(["**Folder:** /out/a", "**File:** one.png", ""])
    assert Path("/out/a/one.png") in [Path(p) for p in agy.reported_paths(response)]


def test_an_absolute_file_detail_is_not_joined_to_a_folder() -> None:
    response = "\n".join(["**File:** D:/elsewhere/boy.png", "**Folder:** D:/out/one", ""])
    found = [Path(p) for p in agy.reported_paths(response)]
    assert found == [Path("D:/elsewhere/boy.png")]


# --- recovery containment (untrusted paths out of agent prose) -----------------


def test_a_real_file_outside_the_workspace_is_refused_not_copied(tmp_path, spawn) -> None:
    """Regression: recovery must not publish a file from outside the workspace.

    `verify_png` proves a file is a PNG, never that it is the right PNG — so an
    absolute path in the agent's prose naming any readable image on the machine
    would otherwise be copied in and published as the sprite.
    """
    work = tmp_path / "work"
    work.mkdir()
    outsider = tmp_path / "elsewhere" / "secret.png"
    outsider.parent.mkdir()
    outsider.write_bytes(_png_bytes(color=(1, 2, 3, 255)))

    raw = work / "raw.png"
    spawn["writes"] = []  # the instructed destination is never written
    spawn["stdout"] = _envelope(f"Saved it to:\n{outsider}\n")

    with pytest.raises(SystemExit) as exc:
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), work)

    assert not raw.exists(), "nothing may be published from outside the workspace"
    message = str(exc.value)
    assert "outside the workspace" in message
    assert "no usable generated image" in message


def test_dot_dot_traversal_out_of_the_workspace_is_refused(tmp_path, spawn) -> None:
    work = tmp_path / "work"
    work.mkdir()
    outsider = tmp_path / "outside.png"
    outsider.write_bytes(_png_bytes())
    raw = work / "raw.png"
    spawn["writes"] = []
    spawn["stdout"] = _envelope("Saved it: [out](../outside.png)\n")

    with pytest.raises(SystemExit, match="no usable generated image"):
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), work)
    assert not raw.exists()


def test_a_symlink_inside_the_workspace_pointing_outside_is_refused(tmp_path, spawn) -> None:
    """`_resolve_candidate` resolves before the check, so the target decides."""
    work = tmp_path / "work"
    work.mkdir()
    outsider = tmp_path / "outside.png"
    outsider.write_bytes(_png_bytes())
    link = work / "linked.png"
    try:
        link.symlink_to(outsider)
    except (OSError, NotImplementedError):  # Windows without developer mode
        pytest.skip("symlink creation is not permitted in this environment")

    raw = work / "raw.png"
    spawn["writes"] = []
    spawn["stdout"] = _envelope(f"Saved it to:\n{link}\n")

    with pytest.raises(SystemExit, match="outside the workspace"):
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), work)
    assert not raw.exists()


def test_a_reference_image_is_never_recovered_as_the_output(tmp_path, spawn) -> None:
    """Republishing an input as this run's generation is the wrong-file outcome."""
    work = tmp_path / "work"
    work.mkdir()
    ref = work / "identity.png"  # deliberately inside the workspace
    ref.write_bytes(_png_bytes())
    raw = work / "raw.png"
    spawn["writes"] = []
    spawn["stdout"] = _envelope(f"Used this file:\n{ref}\n")

    with pytest.raises(SystemExit) as exc:
        agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw, refs=[ref]), work)
    assert "reference image, not the output" in str(exc.value)
    assert not raw.exists()


def test_recovery_still_works_for_a_file_inside_the_workspace(tmp_path, spawn) -> None:
    """The containment check must not break the legitimate recovery path."""
    work = tmp_path / "work"
    work.mkdir()
    elsewhere = work / "renders" / "apple.png"
    spawn["writes"] = [(elsewhere, _png_bytes())]
    spawn["stdout"] = _envelope(f"Saved it to:\n{elsewhere}\n")

    raw = work / "raw.png"
    run = agy.AgyProvider().generate(GenRequest(prompt="p", raw=raw), work)
    assert raw.read_bytes() == _png_bytes()
    assert run.extra["recovered_from"]
