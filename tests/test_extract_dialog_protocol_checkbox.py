"""Source-text tests for the protocol-generation checkbox in Extract dialog.

Per memory [[feedback_ui_app_import_breaks_linux_ci]] — Linux CI doesn't
have PortAudio, so importing `ui.app` (which loads sounddevice) crashes
collection. These tests scan the dialog source text directly via Path.read_text,
avoiding any runtime CTk widget instantiation.

The 4 tests pin the Task 6 contract:
1. Dialog imports `protocol_generator` symbol (either form acceptable)
2. Dialog declares `generate_protocol` BooleanVar
3. `_run_extraction` calls `protocol_generator.generate(...)` using the
   dialog's REAL instance state (self._transcript / self._history_folder /
   self._transcript_lang)
4. Checkbox defaults to True (so first-time users see protocol.md by default)
"""
import re
from pathlib import Path

_DIALOG_FILE = Path("ui/dialogs/extract_tasks/__init__.py")


def test_dialog_imports_protocol_generator():
    """Either `from tasks.protocol_generator import …` or `from tasks import protocol_generator`."""
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    assert (
        "from tasks.protocol_generator import" in src
        or "from tasks import protocol_generator" in src
        or "import tasks.protocol_generator" in src
    ), "Extract dialog must import the protocol_generator module"


def test_dialog_declares_generate_protocol_booleanvar():
    """A BooleanVar named generate_protocol must be declared on the dialog."""
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    assert "generate_protocol" in src
    # Either tk.BooleanVar or ctk.BooleanVar (CTk re-exports tk's classes).
    assert (
        "tk.BooleanVar" in src or "ctk.BooleanVar" in src
    ), "generate_protocol must be bound to a (c)tk.BooleanVar"


def test_dialog_runs_protocol_using_real_instance_state():
    """The protocol-generation call site must use the dialog's existing
    instance state (self._transcript / self._history_folder /
    self._transcript_lang) — NOT invented variable names.

    Catches the v4 plan-pseudocode failure mode where the plan author
    referenced fields like _transcript_text / _known_speakers that don't
    exist (Codex finding #22 on PR #65 lineage). The real fields below
    are set in ExtractTasksDialog.__init__ at lines 72-74.
    """
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    # All three must appear (they're real fields from __init__).
    assert "self._transcript" in src
    assert "self._history_folder" in src
    assert "self._transcript_lang" in src
    # The actual generate() call — module-qualified or aliased form.
    assert (
        "protocol_generator.generate(" in src
        or re.search(r"\bgenerate\s*\(\s*(?:transcript|\n)", src)
    ), "Extract dialog must call protocol_generator.generate(...)"
    # Output path: protocol.md inside the history folder.
    assert "'protocol.md'" in src or '"protocol.md"' in src


def test_dialog_protocol_checkbox_defaults_to_on():
    """value=True so first-time users get a protocol.md without opting in."""
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    # Match: generate_protocol = (c)tk.BooleanVar(...value=True...)
    # Accept either tk.BooleanVar or ctk.BooleanVar, value=True anywhere
    # in the constructor's kwargs.
    m = re.search(
        r"generate_protocol\s*=\s*c?tk\.BooleanVar\([^)]*value\s*=\s*True",
        src,
    )
    assert m, (
        "generate_protocol must default to True — search for "
        "`generate_protocol = (c)tk.BooleanVar(value=True)`"
    )
