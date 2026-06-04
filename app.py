"""Entry point — installs the C-level fault handler, then hands off to ui.app.

Faulthandler MUST be installed BEFORE any C-extension import. The cloud-only
build's native deps (soundfile, sounddevice) can SIGSEGV during shutdown;
without an early fault handler the process vanishes with no diagnostic trail.
Importing ui.app pulls those C extensions in, so we enable faulthandler first
and only then import the rest. (runtime_hook_imports.py is the frozen-app twin
that also redirects None stdio under PyInstaller windowed mode.)
"""

import faulthandler
import os

_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)
_FAULT_LOG = open(os.path.join(_LOGS_DIR, "faulthandler.log"), "w", encoding="utf-8")
faulthandler.enable(file=_FAULT_LOG, all_threads=True)

from ui.app import main  # noqa: E402  (must follow faulthandler.enable)

if __name__ == "__main__":
    main()
