"""PyInstaller runtime hook — CLAUDE.md invariant #1 (faulthandler) + windowed-mode survival.

Runs INSIDE the frozen process BEFORE the bundled `app.py` executes, so
native deps that the cloud-only build still pulls (soundfile, sounddevice,
numpy) get faulthandler protection from the very first instruction. The
2026-05-28 rip-out removed the ctranslate2-before-torch DLL-ordering
concern (old invariant #2) — torch and ctranslate2 are no longer in the
bundle — so this hook is now the entire startup-time invariant surface.

PyInstaller wires this via `runtime_hooks=[...]` in the spec (see
voxnote.spec). The hook is part of the bootstrap pre-amble
PyInstaller injects before importing app.py — there is no Python user
code we can run earlier than this.

Windowed-mode gotcha (Task 4 build #1, 2026-05-28): a vanilla
`faulthandler.enable()` raises `RuntimeError: sys.stderr is None` when
the bundle is built with `console=False` (PyInstaller's `runw.exe`
bootloader). That early-bootstrap exception manifests as the generic
"Unhandled exception in script" Windows dialog with no message body —
near-impossible to debug. Two-layer fix:

  1. Redirect sys.stdout / sys.stderr to a sidecar log file in
     %TEMP% if they are None (windowed mode). The app's existing
     `logging_setup.init_logging()` will redirect to `logs/app.log`
     beside the exe a few imports later — but until then, anything
     that prints (PyInstaller bootstrap warnings, faulthandler dumps,
     uncaught exception tracebacks) needs a destination.
  2. Pass the file explicitly to `faulthandler.enable(file=...)` so
     a future native crash (segfault in soundfile, etc.) is captured
     even in windowed mode.

The sidecar log path is intentionally NOT next to the exe — the bundle
folder may be read-only (e.g. installed under Program Files via UAC,
which the v5 plan explicitly advises against but defensive coding
costs nothing). %TEMP% is always writable for the launching user.
"""
import faulthandler
import os
import sys
import tempfile

# In `console=False` PyInstaller bundles (runw.exe), sys.stdout and
# sys.stderr come back as None — printing to them raises AttributeError
# and `faulthandler.enable()` raises RuntimeError. The `import faulthandler`
# above is safe (the module's import has no side effects), but its
# `.enable()` call must wait until sys.stderr is guaranteed non-None.
# Open a sidecar file and assign it BEFORE the enable call below.
if sys.stderr is None or sys.stdout is None:
    _log_path = os.path.join(
        tempfile.gettempdir(),
        "voxnote-bootstrap.log",
    )
    # line_buffering=True so the file flushes after each line — important
    # for crash investigation when the process dies before atexit runs.
    _bootstrap_log = open(_log_path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _bootstrap_log
    if sys.stderr is None:
        sys.stderr = _bootstrap_log
    # Mark the file so debugging is obvious — different runs append.
    print(f"\n=== voxnote bootstrap @ pid={os.getpid()} ===",
          file=sys.stderr, flush=True)

# Now safe to enable faulthandler; sys.stderr is guaranteed non-None.
faulthandler.enable(file=sys.stderr)
