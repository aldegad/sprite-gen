# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen curation` is the same program as the two older launch forms.

The console script removes the "which interpreter?" question for the webview, but only if
the subcommand is the *same* declaration and the *same* implementation as
`python -m sprite_gen.serve.serve_curation` and the `scripts/serve_curation.py` wrapper. A copied
argument list would pass a smoke test on the day it is written and drift on the next flag.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from sprite_gen import cli
from sprite_gen.serve import serve_curation

ROOT = Path(__file__).resolve().parents[2]


def _help_options(*argv: str) -> set[str]:
    proc = subprocess.run([sys.executable, *argv, "--help"], cwd=ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return set(re.findall(r"(?<![\w-])--[a-z][\w-]*", proc.stdout))


def test_curation_subcommand_reuses_the_webview_declaration() -> None:
    """Identity, not equality: the CLI table points at the webview's own functions."""
    _description, add_args, run_fn = cli.COMMANDS["curation"]

    assert add_args is serve_curation.add_arguments
    assert run_fn is serve_curation.run


def test_every_launch_form_exposes_the_same_flags() -> None:
    subcommand = _help_options("-m", "sprite_gen.cli", "curation")
    module = _help_options("-m", "sprite_gen.serve.serve_curation")
    wrapper = _help_options(str(ROOT / "scripts" / "serve_curation.py"))

    assert subcommand == {"--run-dir", "--host", "--port", "--no-open", "--lang", "--help"}
    assert subcommand == module == wrapper


def test_subcommand_hands_the_parsed_arguments_to_the_server(fixture_run_dir, monkeypatch, capsys) -> None:
    """The whole chain, unmocked except for the socket: parse -> kwargs -> serve.

    `cli.main` calls `run_fn(**vars(args))`, so a renamed parameter or a dest mismatch
    (`--no-open` -> `no_open`) is a TypeError here rather than at a user's first launch.
    """
    seen: dict = {}

    class FakeServer:
        def __init__(self, address, handler):
            seen["address"] = address
            self.server_address = address

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    opened: list[str] = []
    # The handler's config is class state; register it with monkeypatch so this test's
    # `--lang ko` is restored and cannot leak into another test's run snapshot.
    monkeypatch.setattr(serve_curation.CurationHandler, "lang", serve_curation.CurationHandler.lang)
    monkeypatch.setattr(serve_curation.CurationHandler, "run_dir", serve_curation.CurationHandler.run_dir)
    monkeypatch.setattr(serve_curation, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(serve_curation.webbrowser, "open", opened.append)

    code = cli.main(["curation", "--run-dir", str(fixture_run_dir),
                     "--host", "127.0.0.1", "--port", "8797", "--no-open", "--lang", "ko"])

    assert code == 0
    assert seen["address"] == ("127.0.0.1", 8797)
    assert seen.get("closed") is True
    assert opened == [], "--no-open still opened a browser"
    assert serve_curation.CurationHandler.lang == "ko"
    assert serve_curation.CurationHandler.run_dir == fixture_run_dir.resolve()
    assert "http://127.0.0.1:8797/" in capsys.readouterr().out
