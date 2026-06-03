"""Source-text contract tests for markitdown document grounding in the extract
dialog.

Per [[feedback_ui_app_import_breaks_linux_ci]] these scan the dialog source via
Path.read_text instead of importing ui.app (sounddevice/PortAudio is missing on
the Linux CI runner, so importing the dialog crashes collection). Mirrors
tests/test_extract_dialog_protocol_checkbox.py.
"""
from pathlib import Path

_DIALOG_FILE = Path("ui/dialogs/extract_tasks/__init__.py")


def test_dialog_imports_doc_context_helpers():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    assert "from tasks.doc_context import" in src
    assert "convert_documents" in src
    assert "combine_context" in src


def test_dialog_declares_doc_paths_state():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    assert "self._context_doc_paths" in src


def test_dialog_has_attach_handler_with_multiselect_picker():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    assert "_on_attach_documents" in src
    assert "askopenfilenames" in src  # multi-select, not single askopenfilename


def test_doc_paths_captured_on_main_thread_and_threaded_into_worker():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    # Captured from dialog state on the main thread (state isn't thread-safe)...
    assert "doc_paths = list(self._context_doc_paths)" in src
    # ...and passed as a worker arg, not read from a Tk var inside the thread.
    assert "name_by_label, doc_paths" in src


def test_conversion_merges_into_existing_context_slot():
    src = _DIALOG_FILE.read_text(encoding="utf-8")
    # Must COMBINE with the directory grounding, not replace it.
    assert "render_meeting_context" in src
    assert "combine_context(" in src
