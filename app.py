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
import sys

# In a PyInstaller build, runtime_hook_imports.py has ALREADY redirected the
# None stdio streams and enabled faulthandler to a %TEMP% sidecar before this
# entry script runs. Re-opening _internal/logs/faulthandler.log here would be
# redundant AND risky: if that dir is non-writable (Program Files, a locked
# corporate profile, AV quarantine of _internal), the open() raises at import
# — before main()'s try/except — producing the generic "Unhandled exception"
# dialog the hook exists to prevent. So set up the handler only in dev (source)
# mode, and even there never let a log-open failure block startup.
if not getattr(sys, "frozen", False):
    try:
        _LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(_LOGS_DIR, exist_ok=True)
        _FAULT_LOG = open(
            os.path.join(_LOGS_DIR, "faulthandler.log"), "w", encoding="utf-8"
        )
        faulthandler.enable(file=_FAULT_LOG, all_threads=True)
    except OSError:
        faulthandler.enable(all_threads=True)  # fall back to stderr; never block

from ui.app import main  # noqa: E402  (must follow faulthandler setup)

if __name__ == "__main__":
    main()
