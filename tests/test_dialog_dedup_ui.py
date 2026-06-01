"""Source-text checks for the dedup UI wiring (PR-3).

CustomTkinter / ui.app must NOT be imported on Linux CI (sounddevice loads
PortAudio at import time — see feedback_ui_app_import_breaks_linux_ci). We
assert on the FILE TEXT instead: structural guarantees that the badge,
toggle, worker-thread driver, and config/settings plumbing are present and
wired — without importing any Tk module.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROW = (ROOT / "ui" / "dialogs" / "extract_tasks" / "task_row.py").read_text("utf-8")
DIALOG = (ROOT / "ui" / "dialogs" / "extract_tasks" / "__init__.py").read_text("utf-8")
CONFIG = (ROOT / "config.example.json").read_text("utf-8")


# ── task_row badge + toggle (Task 4) ─────────────────────────────────


def test_task_row_has_dedup_badge_and_toggle():
    assert "set_dup_visual" in ROW
    assert "возможный дубль" in ROW
    assert "CTkSegmentedButton" in ROW
    assert "Закомментировать" in ROW and "Создать новую" in ROW
    assert "dup_action" in ROW


def test_task_row_renders_commented_badge():
    # set_status_visual must handle the COMMENTED state explicitly.
    assert "COMMENTED" in ROW


# ── dialog driver + wiring (Task 5) ──────────────────────────────────


def test_dialog_runs_dedup_on_worker_before_success_dispatch():
    assert "_run_dedup" in DIALOG
    assert "build_board_registry" in DIALOG
    assert "select_match" in DIALOG
    assert "supports_comments" in DIALOG          # gated on capability
    assert "dedup_enabled" in DIALOG              # gated on config
    # the driver runs before the success dispatch (so badges exist at render)
    assert DIALOG.index("self._run_dedup(") < DIALOG.index(
        "self.after(0, self._on_extract_success")


def test_dialog_renders_dup_badge_after_row_build():
    assert "set_dup_visual" in DIALOG


def test_dialog_passes_meeting_label_to_send():
    assert "meeting_label=" in DIALOG


# ── best-effort guard hardening (Codex follow-up) ────────────────────


def test_run_dedup_parses_thresholds_via_safe_resolver():
    # Hand-edited non-numeric dedup_fuzzy_* must not raise out of the worker
    # and surface as a fake "extraction failed". The bare float() was replaced
    # by the best-effort resolve_thresholds() helper (unit-tested separately).
    assert "resolve_thresholds" in DIALOG
    assert 'float(self._config.get("dedup_fuzzy_high"' not in DIALOG


def test_run_dedup_registry_guard_covers_schema_errors():
    # build_board_registry can raise LinearError / TrelloError (backend failures),
    # as well as ValueError / KeyError on malformed board data; the guard must
    # swallow all of them so a board-listing failure can't sink a successful
    # extraction (badges simply won't appear).
    assert "(OSError, LinearError, TrelloError, ValueError, KeyError)" in DIALOG


# ── config keys (Task 6) ─────────────────────────────────────────────


def test_config_example_has_dedup_keys():
    import json
    cfg = json.loads(CONFIG)
    assert cfg["dedup_enabled"] is True
    assert 0.0 < cfg["dedup_fuzzy_low"] < cfg["dedup_fuzzy_high"] < 1.0
