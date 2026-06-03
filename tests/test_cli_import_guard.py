"""The CLI must import on a headless host (no display, no PortAudio).

Hermes Agent runs on a "$5 VPS"; importing the CLI must NOT pull CustomTkinter
or sounddevice (PortAudio) — the same failure class as the documented "UI tests
must not import ui.app on Linux CI" lesson. We run the probe in a CLEAN
subprocess so other tests in the suite (which may import the GUI) can't pollute
this process's sys.modules and mask a regression.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cli_import_is_headless():
    probe = (
        "import sys\n"
        "import cli.core, cli.app, cli.config\n"
        "bad = [m for m in ('customtkinter', 'sounddevice', 'ui.app') "
        "if m in sys.modules]\n"
        "print(','.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"importing cli.* pulled forbidden modules: {result.stdout.strip()!r}\n"
        f"stderr:\n{result.stderr}"
    )
