"""Forecast-vs-actual wiring in the Extract dialog (spec 2026-06-11, PR-4).

The pure math is unit-tested in test_extract_pricing.py; these slices pin
the dialog wiring (no ui imports — sounddevice on Linux CI).
"""
from pathlib import Path

EXTRACT = Path("ui/dialogs/extract_tasks/__init__.py").read_text(encoding="utf-8")
EXTRACT_BUILDER = Path(
    "ui/dialogs/extract_tasks/builder.py"
).read_text(encoding="utf-8")


def _method_block(src: str, name: str) -> str:
    start = src.index(f"def {name}")
    return src[start:src.index("\n    def ", start + 1)]


def test_cost_hint_uses_selected_model_and_remembers_forecast():
    block = _method_block(EXTRACT, "_update_cost_hint")
    assert "_model_var.get()" in block
    assert "_last_cost_forecast" in block


def test_model_change_reestimates_hint():
    # ComboBox edits (picked or typed) must re-run the forecast.
    assert "dialog._model_var.trace_add" in EXTRACT_BUILDER
    idx = EXTRACT_BUILDER.index("dialog._model_var.trace_add")
    assert "_update_cost_hint" in EXTRACT_BUILDER[idx:idx + 200]


def test_success_line_appends_forecast():
    block = _method_block(EXTRACT, "_on_extract_success")
    assert "прогноз $" in block
    assert "_last_cost_forecast" in block
