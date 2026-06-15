"""Source-slice wiring tests for the PR-C1b main-bar project selector.

No ui.app import — sounddevice/PortAudio would break Linux CI. Mirrors
test_ui_queue_wiring.py: read the module text and assert on it.
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CONST = (_ROOT / "ui" / "app" / "constants.py").read_text(encoding="utf-8")
_BUILDER = (_ROOT / "ui" / "app" / "builder.py").read_text(encoding="utf-8")
_QUEUE = (_ROOT / "ui" / "app" / "queue_mixin.py").read_text(encoding="utf-8")
_INIT = (_ROOT / "ui" / "app" / "__init__.py").read_text(encoding="utf-8")
_DIALOGS = (_ROOT / "ui" / "app" / "dialogs_mixin.py").read_text(encoding="utf-8")


def test_no_project_label_constant_defined():
    assert "NO_PROJECT_LABEL" in _CONST


def test_config_example_has_last_project_id():
    example = json.loads((_ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert "last_project_id" in example, (
        "config.example.json must list last_project_id (seeded into user config)"
    )


def test_builder_creates_project_selector():
    assert "_project_var" in _BUILDER
    assert "_project_menu" in _BUILDER
    assert '"Проект"' in _BUILDER or "'Проект'" in _BUILDER
    assert "_on_project_changed" in _BUILDER  # menu command wired


def test_queue_mixin_has_project_selector_api():
    for name in ("_refresh_project_selector", "_on_project_changed"):
        assert f"def {name}" in _QUEUE


def test_build_options_reads_project_not_hardcoded_none():
    # project_id must come from the selector map, not a literal None.
    assert '"project_id": None' not in _QUEUE
    assert "_project_choices" in _QUEUE


def test_on_project_changed_persists_last_project_id():
    assert '"last_project_id"' in _QUEUE
    assert "save_config" in _QUEUE


def test_init_refreshes_selector_after_dir_store_load():
    assert "_refresh_project_selector" in _INIT


def test_directory_dialog_close_reloads_and_refreshes():
    # Editing projects in Справочники must refresh the selector on close.
    assert "<Destroy>" in _DIALOGS
    assert "_refresh_project_selector" in _DIALOGS
    assert "_dir_store.load()" in _DIALOGS
