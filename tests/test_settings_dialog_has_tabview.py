"""SettingsDialog uses a CTkTabview with the three expected tabs.

Source-text checks only — see feedback_ui_app_import_breaks_linux_ci.
"""
from __future__ import annotations

import re
from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)


def test_imports_ctk_tabview_or_references_it():
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "CTkTabview" in source, (
        "ui/dialogs/settings.py must reference CTkTabview"
    )


def test_three_tabs_added_with_expected_names():
    """Three tabs: Транскрипция, Интеграции, Диагностика. We grep
    for `.add("<name>")` calls — flexible to either chained-call
    construction or post-construction tab adding."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")

    expected = ["Транскрипция", "Интеграции", "Диагностика"]
    for name in expected:
        pattern = rf'\.add\(\s*[\'"]{re.escape(name)}[\'"]\s*\)'
        assert re.search(pattern, source), (
            f'Expected `.add("{name}")` call in settings.py'
        )


def test_each_tab_has_scrollable_frame():
    """Each tab wraps content in a CTkScrollableFrame so taller sections
    (Tab 1 has 5) don't get clipped at small window heights. Counts
    CTkScrollableFrame occurrences — expect ≥ 3 (one per tab)."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    n = source.count("CTkScrollableFrame")
    assert n >= 3, (
        f"Expected ≥ 3 CTkScrollableFrame instances (one per tab), got {n}"
    )


def test_dialog_geometry_is_wide_enough():
    """Dialog must be at least 600px wide — the 4-widget API-key row
    (entry + 👁 + Проверить + status) needs room to breathe; 520 caused
    status labels to truncate."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    # Find self.geometry("WxH") and check W >= 600
    m = re.search(r'self\.geometry\(\s*[\'"](\d+)x(\d+)[\'"]\s*\)', source)
    assert m is not None, "self.geometry(...) call not found"
    width = int(m.group(1))
    assert width >= 600, (
        f"Dialog width {width} too narrow — API-key row needs ≥ 600 px"
    )
