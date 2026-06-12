"""Verifies the history → meetings rename across UI surface.

Source-text checks only (no UI imports — sounddevice on Linux CI).
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_history_file_renamed_to_meetings():
    """Old file must not exist; new file must exist."""
    assert not (REPO / "ui" / "dialogs" / "history.py").exists(), (
        "ui/dialogs/history.py must be removed (renamed to meetings.py)"
    )
    assert (REPO / "ui" / "dialogs" / "meetings.py").exists(), (
        "ui/dialogs/meetings.py must exist after rename"
    )


def test_meetings_module_defines_meetings_dialog_class():
    src = (REPO / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
    assert "class MeetingsDialog" in src, (
        "MeetingsDialog class must be defined in ui/dialogs/meetings.py"
    )
    assert "class MeetingViewerDialog" in src, (
        "MeetingViewerDialog class must be defined"
    )
    assert "class HistoryDialog" not in src, (
        "Old HistoryDialog name must be gone (clean rename)"
    )


def test_meetings_dialog_title_is_meetings():
    """Window title must be «Встречи» (terminology unified 2026-06-11)."""
    src = (REPO / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
    assert "История транскрипций" not in src, (
        "Old window title must be gone"
    )
    assert '"Встречи"' in src or "'Встречи'" in src, (
        "Window title must be «Встречи»"
    )


def test_meetings_footer_label_renamed():
    """«Записей: N» → «Встреч: N» (fixed genitive — declension-safe)."""
    src = (REPO / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
    assert "Записей:" not in src, "Old «Записей:» label must be gone"
    assert "Встреч:" in src, "New «Встреч:» label required"


def test_meetings_empty_state_renamed():
    """«Нет транскрипций» → «Нет встреч»."""
    src = (REPO / "ui" / "dialogs" / "meetings.py").read_text(encoding="utf-8")
    assert "Нет транскрипций" not in src
    assert "Нет встреч" in src


def test_builder_uses_meetings_button_text():
    """Main-window button text is «Встречи»."""
    builder = (REPO / "ui" / "app" / "builder.py").read_text(encoding="utf-8")
    assert '"Встречи"' in builder or "'Встречи'" in builder, (
        "Main window button must read «Встречи»"
    )
    # The OLD «История» text must be gone (the underscore name
    # _btn_history is a stable Python identifier — only label text changes).
    assert 'text="История"' not in builder, (
        "Old «История» button text must be gone from builder.py"
    )


def test_dialogs_mixin_has_open_meetings_dialog():
    """dialogs_mixin defines _open_meetings_dialog and imports MeetingsDialog."""
    mixin = (
        REPO / "ui" / "app" / "dialogs_mixin.py"
    ).read_text(encoding="utf-8")
    assert "_open_meetings_dialog" in mixin, (
        "DialogsMixin must define _open_meetings_dialog"
    )
    assert "_open_history_dialog" not in mixin, (
        "Old _open_history_dialog must be renamed"
    )
    assert "MeetingsDialog" in mixin, (
        "DialogsMixin must import MeetingsDialog from ui.dialogs.meetings"
    )
    assert "from ui.dialogs.history" not in mixin, (
        "Old import path must be gone"
    )


def test_app_init_schedules_migration_check():
    """App.__init__ must invoke detect_old_locations on startup."""
    src = (REPO / "ui" / "app" / "__init__.py").read_text(encoding="utf-8")
    assert "detect_old_locations" in src, (
        "App.__init__ must call detect_old_locations to find legacy meetings"
    )
    assert "MigrationPromptDialog" in src, (
        "App.__init__ must reference MigrationPromptDialog for first-launch flow"
    )


def test_no_miting_terminology_left_in_ui():
    """Terminology unified to «встречи» (2026-06-11) — no «митинг» strings
    remain anywhere under ui/ (titles, labels, docstrings)."""
    import re

    offenders = []
    for path in sorted((REPO / "ui").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if re.search(r"[Мм]итинг", src):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"«митинг» terminology found in: {offenders}"
