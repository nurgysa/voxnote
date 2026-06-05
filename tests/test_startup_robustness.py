"""WS-3 startup robustness: first-run banner + frozen-mode faulthandler.

``constants.compute_first_run`` is loaded in isolation (spec_from_file_location)
because importing ``ui.app`` pulls recorder → sounddevice → PortAudio, absent on
the Linux CI runner (see test_ui_constants.py for the same pattern). ``app.py``
is checked at the SOURCE level — it is the process entry and runs faulthandler
setup at import time, so it cannot be imported inside a test.
"""
from __future__ import annotations

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_CONSTANTS_PATH = os.path.join(_REPO, "ui", "app", "constants.py")
_spec = importlib.util.spec_from_file_location("_ui_app_constants_fr", _CONSTANTS_PATH)
_constants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_constants)
compute_first_run = _constants.compute_first_run


def test_first_run_true_when_assemblyai_missing():
    assert compute_first_run({}, "or-key") is True
    assert compute_first_run({"AssemblyAI": ""}, "or-key") is True
    assert compute_first_run({"AssemblyAI": "   "}, "or-key") is True


def test_first_run_true_when_openrouter_missing():
    """The regression this fixes: AssemblyAI set but OpenRouter empty used to
    CLEAR the banner, leaving the user at a silent dead-end when they later hit
    «Извлечь задачи» (OpenRouter is mandatory for tasks/protocol)."""
    assert compute_first_run({"AssemblyAI": "asm-key"}, "") is True
    assert compute_first_run({"AssemblyAI": "asm-key"}, "   ") is True


def test_first_run_false_when_both_present():
    assert compute_first_run({"AssemblyAI": "asm-key"}, "or-key") is False


def test_app_py_guards_faulthandler_when_frozen():
    """app.py must NOT unconditionally open _internal/logs/faulthandler.log in a
    frozen build: runtime_hook_imports.py already enabled faulthandler to a
    %TEMP% sidecar, and re-opening in a possibly-non-writable dir crashes
    startup before main()'s try/except. Source-level check — the entry point
    can't be imported in a test."""
    app_src = open(os.path.join(_REPO, "app.py"), encoding="utf-8").read()
    # Match the real guard idiom (getattr(sys, "frozen", False)) — NOT a bare
    # "sys.frozen" literal, which the code doesn't contain.
    assert 'getattr(sys, "frozen"' in app_src, "faulthandler setup must be guarded on frozen"
    assert "except OSError" in app_src, "dev-mode log open() must not block startup"
