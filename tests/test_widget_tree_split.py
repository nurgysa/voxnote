"""Regression locks for the widget-tree split (spec 2026-06-10).

Source-text checks only — importing ui.* would load sounddevice, which
Linux CI cannot (no PortAudio). Encoding pinned: stock Windows defaults
to cp1252.
"""

from pathlib import Path

SETTINGS = Path("ui/dialogs/settings.py").read_text(encoding="utf-8")
BUILDER = Path("ui/dialogs/settings_builder.py").read_text(encoding="utf-8")
EXTRACT = Path("ui/dialogs/extract_tasks/__init__.py").read_text(encoding="utf-8")
EXTRACT_BUILDER = Path("ui/dialogs/extract_tasks/builder.py").read_text(encoding="utf-8")


def test_settings_class_has_no_build_methods():
    # The split's whole point: widget-tree construction lives in the
    # builder module, not on the dialog class.
    assert "def _build_" not in SETTINGS


def test_settings_builder_defines_no_class():
    # Free functions only — a class here means the god-object is regrowing.
    assert "\nclass " not in BUILDER and not BUILDER.startswith("class ")


def test_settings_builder_import_discipline():
    # Cycle guard: the builder must never import its own dialog module,
    # and ui.app only lazily (inside build_appearance_section).
    assert "from ui.dialogs.settings import" not in BUILDER
    assert "import ui.dialogs.settings" not in BUILDER
    head = BUILDER.split("\ndef ", 1)[0]  # module level = before first def
    assert "from ui.app import" not in head


def test_extract_dialog_has_no_build_methods():
    # Same lock as settings: widget-tree construction lives in the builder.
    assert "def _build_" not in EXTRACT
    assert "def _rebuild_" not in EXTRACT


def test_extract_builder_defines_no_class():
    assert "\nclass " not in EXTRACT_BUILDER and not EXTRACT_BUILDER.startswith("class ")


def test_extract_builder_import_discipline():
    # Cycle guard: the builder must never import the package __init__ back,
    # and directory.store only lazily (inside build_ui).
    assert "from ui.dialogs.extract_tasks import" not in EXTRACT_BUILDER
    head = EXTRACT_BUILDER.split("\ndef ", 1)[0]  # module level = before first def
    assert "from directory.store import" not in head
