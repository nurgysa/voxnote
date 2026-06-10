"""SettingsDialog must not duplicate the "Настройки" heading inside the
window body — the OS title bar already shows it."""
from __future__ import annotations

from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)
BUILDER_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings_builder.py"
)


def test_no_inner_h1_label():
    """No inline CTkLabel with text='Настройки' inside the dialog body —
    the OS title bar already shows it. Specifically: no `text="Настройки"`
    or `text='Настройки'` widget-construction argument."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    inline = (
        source.count('text="Настройки"')
        + source.count("text='Настройки'")
    )
    assert inline == 0, (
        f"Expected 0 widget text='Настройки' (title is in OS bar), got {inline}"
    )


def test_no_inner_h1_label_in_builder():
    """settings_builder.py must also not introduce a text='Настройки' widget."""
    source = BUILDER_PATH.read_text(encoding="utf-8")
    inline = (
        source.count('text="Настройки"')
        + source.count("text='Настройки'")
    )
    assert inline == 0, (
        f"Expected 0 widget text='Настройки' in settings_builder.py "
        f"(title is in OS bar), got {inline}"
    )


def test_esc_key_binding_present():
    """Pressing Esc should close the dialog — standard modal convention."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    assert '"<Escape>"' in source or "'<Escape>'" in source, (
        "Esc key binding (<Escape>) must be wired to dialog close"
    )
