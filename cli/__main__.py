"""``python -m cli`` entry point.

Installs the C-level faulthandler BEFORE importing ``cli.app`` (whose handlers
lazily pull ``transcriber`` → native audio C-extensions soundfile/sounddevice).
Mirrors ``app.py:13-16`` — CLAUDE.md hard-invariant #1: faulthandler must
initialise before any C-extension import. faulthandler is process-global, so
enabling it here covers the later lazy imports too.
"""
from __future__ import annotations

import faulthandler
import os
import sys

_LOGS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs",
)
os.makedirs(_LOGS_DIR, exist_ok=True)
_FAULT_LOG = open(  # noqa: SIM115  (lives for process lifetime, like app.py)
    os.path.join(_LOGS_DIR, "faulthandler-cli.log"), "w", encoding="utf-8",
)
faulthandler.enable(file=_FAULT_LOG, all_threads=True)

from cli.app import main  # noqa: E402  (must follow faulthandler.enable)

if __name__ == "__main__":
    sys.exit(main())
