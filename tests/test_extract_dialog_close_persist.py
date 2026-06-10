"""_on_close must not silently discard un-persisted task edits.

Source-text checks (Linux CI can't import ui/ — sounddevice loads PortAudio
at import time), window-sliced to the _on_close body so unrelated dialog
code can't satisfy the assertions.

The bug class: _persist_current_task() raising OSError (disk full, file
lock, permissions) was logged and the dialog closed anyway — the user's
last form edits evaporated with no visible signal.
"""
from pathlib import Path

_DIALOG = Path("ui/dialogs/extract_tasks/__init__.py")


def _on_close_body() -> str:
    src = _DIALOG.read_text(encoding="utf-8")
    start = src.index("def _on_close(")
    # Slice up to the next method definition so assertions are scoped.
    end = src.index("def _build_form(", start)
    return src[start:end]


def test_on_close_asks_before_discarding_unsaved_edits():
    body = _on_close_body()
    assert "askyesno" in body, (
        "_on_close must ask the user before closing when persist fails"
    )
    assert "Закрыть без сохранения" in body, (
        "the data-loss prompt must say closing discards the edits"
    )


def test_on_close_can_abort_the_close():
    body = _on_close_body()
    # The 'No' answer must abort the close path (early return before
    # cancel/destroy), giving the user a chance to fix disk/lock and retry.
    assert "return" in body.split("askyesno")[1].split("self._cancel_event.set()")[0], (
        "answering 'No' must return before the teardown begins"
    )
