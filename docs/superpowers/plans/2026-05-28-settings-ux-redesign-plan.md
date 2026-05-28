# Settings dialog UX redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Settings dialog from a 9-section vertical scroll into a 3-tab layout with a global first-run banner and a unified `api_key_row` helper.

**Architecture:** Wrap existing `_build_*_section` methods in a `CTkTabview` with three tabs (Транскрипция / Интеграции / Резервная копия). Extract the 4 repeated "API key + paste + validate" widget clusters into one `api_key_row` factory in `ui/widgets.py`. Add a clickable global banner above the tab bar that reactively warns about empty STT key or mixed-language/provider incompatibility. Zero changes to `config.json` schema, App-level `StringVar`/`BooleanVar` instances, or `_on_*_changed` callbacks.

**Tech Stack:** Python 3.12, CustomTkinter 5.2.2, Tkinter `trace_add` for reactive state, `threading.Thread(daemon=True)` + `parent.after(0, ...)` for non-blocking validate calls. Tests are source-text checks (AST inspect + regex grep) because `ui.app` import fails on Linux CI (sounddevice/PortAudio).

**Reference spec:** [docs/superpowers/specs/2026-05-28-settings-ux-redesign-design.md](../specs/2026-05-28-settings-ux-redesign-design.md)

---

## File structure

| Path | Action | Responsibility |
|---|---|---|
| `ui/widgets.py` | Modify | Add `api_key_row(...)` factory. Add `GREEN`, `RED` to imports. Add `threading` and `collections.abc.Callable` imports. |
| `ui/dialogs/settings.py` | Modify (major) | Wrap body in `CTkTabview`. Replace 4 inline API-key clusters with `api_key_row` calls. Add global banner widget. Drop inner H1, add Esc binding, swap footer to `tonal_button`. Rename "Транскрибация" → "Облачное распознавание". Add `after(200, iconbitmap)` workaround. |
| `tests/test_api_key_row_helper.py` | Create | AST checks: function exists, has required kwargs, uses threading. |
| `tests/test_settings_dialog_uses_api_key_row.py` | Create | Source-grep: `api_key_row(` appears ≥ 4 times in settings.py. |
| `tests/test_settings_dialog_has_tabview.py` | Create | Source-grep: `CTkTabview` imported, exactly 3 `tabview.add(...)` calls with expected names. |
| `tests/test_settings_dialog_banner.py` | Create | AST: `trace_add` subscribed on the 3 required vars; `trace_remove` calls in `destroy()`. |
| `tests/test_settings_dialog_naming.py` | Create | No "Транскрибация" remains; "Облачное распознавание" appears. |
| `tests/test_settings_dialog_no_inner_h1.py` | Create | No second `text="Настройки"` after `self.title("Настройки")`. |

**Out of scope (deferred to follow-up PRs):**
- Cloud STT provider validation (`validate_key()` methods on each provider class)
- Search box / Save-Apply buttons / Advanced mode / Reset-to-defaults

---

## Task 1: Add `api_key_row` helper to `ui/widgets.py`

**Files:**
- Create: `tests/test_api_key_row_helper.py`
- Modify: `ui/widgets.py` (add helper + imports)

### Step 1: Write the failing test

Create `tests/test_api_key_row_helper.py`:

```python
"""api_key_row helper exists in ui/widgets.py with the required signature.

Source-text + AST checks — we cannot import ui.widgets directly because
sounddevice (transitively imported through ui.app -> recorder) loads
PortAudio at import time, which is absent on Linux CI runners. See
~/.claude/memory/feedback_ui_app_import_breaks_linux_ci.md.
"""
from __future__ import annotations

import ast
from pathlib import Path

WIDGETS_PATH = Path(__file__).resolve().parent.parent / "ui" / "widgets.py"


def _get_function_def(source: str, name: str) -> ast.FunctionDef | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_api_key_row_function_exists():
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "api_key_row")
    assert fn is not None, "api_key_row not defined in ui/widgets.py"


def test_api_key_row_has_required_kwargs():
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "api_key_row")
    assert fn is not None

    kwonly_names = {a.arg for a in fn.args.kwonlyargs}
    expected = {
        "label_text", "key_var", "placeholder",
        "on_validate", "on_key_persisted",
        "enabled_var", "enabled_label", "on_enabled_changed",
        "format_success", "row",
    }
    missing = expected - kwonly_names
    assert not missing, f"api_key_row missing kwargs: {sorted(missing)}"


def test_api_key_row_uses_daemon_thread():
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "api_key_row")
    assert fn is not None

    body = ast.unparse(fn)
    assert "threading.Thread" in body, (
        "api_key_row must spawn a worker thread for non-blocking validation"
    )
    assert "daemon=True" in body, (
        "worker thread must be daemon so it doesn't block process exit"
    )


def test_api_key_row_marshals_via_after():
    """UI updates from the worker thread MUST go through parent.after(0, ...).
    CTk widgets are not thread-safe — direct .configure() from worker
    causes random crashes on Windows + intermittent rendering bugs."""
    source = WIDGETS_PATH.read_text(encoding="utf-8")
    fn = _get_function_def(source, "api_key_row")
    assert fn is not None

    body = ast.unparse(fn)
    assert "parent.after(0" in body, (
        "api_key_row must marshal worker-thread UI updates via parent.after(0, ...)"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_key_row_helper.py -v`
Expected: 4 FAIL with "api_key_row not defined in ui/widgets.py" (and missing-kwargs / threading assertions).

- [ ] **Step 3: Add helper + imports to `ui/widgets.py`**

Modify imports section (lines 9-25) of `ui/widgets.py`. Replace the existing import block with:

```python
from __future__ import annotations

import threading
from collections.abc import Callable

import customtkinter as ctk

from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BLUE_SURFACE,
    BORDER,
    FONT,
    GREEN,
    INPUT_BG,
    RED,
    SURFACE,
    SURFACE_BRIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
```

Then append this function at the end of `ui/widgets.py` (after `dialog_chrome`):

```python
def api_key_row(
    parent,
    *,
    label_text: str,
    key_var,
    placeholder: str,
    on_validate: Callable[[str], dict] | None = None,
    on_key_persisted: Callable[[str, dict], None] | None = None,
    enabled_var=None,
    enabled_label: str | None = None,
    on_enabled_changed: Callable[..., None] | None = None,
    format_success: Callable[[dict], str] = lambda _d: "✓ Активен",
    row: int = 0,
) -> dict:
    """API-key input row: optional enable-checkbox + label + masked entry
    + eye-toggle + (optional) Проверить button + status label.

    Grids itself into `parent` starting at row `row`. Returns a dict with
    refs {"entry", "validate_btn", "status"} — caller stores `entry` so
    the first-run banner can focus_set() it on click.

    Threading: `on_validate(key)` is a BLOCKING caller-supplied function
    (typically a network call). The helper runs it in a daemon thread and
    marshals all UI updates back via parent.after(0, ...) — CTk widgets
    are not thread-safe.

    See docs/superpowers/specs/2026-05-28-settings-ux-redesign-design.md.
    """
    refs: dict = {"entry": None, "validate_btn": None, "status": None}
    current_row = row

    # Optional enable-checkbox row (Linear/Glide pattern)
    if enabled_var is not None and enabled_label is not None:
        ctk.CTkCheckBox(
            parent, text=enabled_label,
            variable=enabled_var,
            command=on_enabled_changed,
            font=ctk.CTkFont(family=FONT, size=13),
            text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
            border_color=BORDER, corner_radius=4,
            checkbox_height=20, checkbox_width=20,
        ).grid(
            row=current_row, column=0, columnspan=4,
            padx=4, pady=(2, 8), sticky="w",
        )
        current_row += 1

    # Label
    label(parent, label_text).grid(
        row=current_row, column=0, padx=(4, 8), pady=6, sticky="w",
    )

    # Masked entry
    entry = ctk.CTkEntry(
        parent, textvariable=key_var, height=36,
        corner_radius=10, border_color=BORDER, border_width=1,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT, size=12),
        placeholder_text=placeholder,
        show="•",
    )
    entry.grid(row=current_row, column=1, padx=4, pady=6, sticky="ew")
    refs["entry"] = entry

    # Eye-toggle (small tonal button)
    eye_state = {"masked": True}

    def _toggle_eye() -> None:
        eye_state["masked"] = not eye_state["masked"]
        entry.configure(show="•" if eye_state["masked"] else "")

    tonal_button(parent, text="👁", command=_toggle_eye, width=40).grid(
        row=current_row, column=2, padx=(4, 4), pady=6,
    )

    if on_validate is None:
        # No validate button → done. status slot stays None.
        return refs

    # Validate button + status label (two-row layout: validate beside
    # entry on current_row, status spanning below on current_row+1)
    def _run_validate() -> None:
        key = key_var.get().strip()
        if not key:
            refs["status"].configure(text="Введите API ключ", text_color=RED)
            return

        refs["validate_btn"].configure(state="disabled", text="Проверка...")
        refs["status"].configure(text="Проверка...", text_color=TEXT_SECONDARY)

        def worker() -> None:
            try:
                info = on_validate(key)
            except Exception as e:
                # 100-char truncation prevents long Drive/HTTP errors
                # from breaking the row layout.
                error_msg = str(e)[:100]
                parent.after(0, lambda: refs["status"].configure(
                    text=f"✗ {error_msg}", text_color=RED,
                ))
                parent.after(0, lambda: refs["validate_btn"].configure(
                    state="normal", text="Проверить",
                ))
                return

            if on_key_persisted is not None:
                on_key_persisted(key, info)

            msg = format_success(info)
            parent.after(0, lambda: refs["status"].configure(
                text=msg, text_color=GREEN,
            ))
            parent.after(0, lambda: refs["validate_btn"].configure(
                state="normal", text="Проверить",
            ))

        threading.Thread(target=worker, daemon=True).start()

    validate_btn = tonal_button(
        parent, text="Проверить", command=_run_validate, width=120,
    )
    validate_btn.grid(row=current_row, column=3, padx=(4, 4), pady=6)
    refs["validate_btn"] = validate_btn

    current_row += 1
    status_w = label(parent, "", anchor="w")
    status_w.grid(
        row=current_row, column=1, columnspan=3,
        padx=4, pady=(0, 6), sticky="ew",
    )
    refs["status"] = status_w

    return refs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_key_row_helper.py -v`
Expected: 4 PASS.

Also run full suite to ensure no regression: `python -m pytest -q`
Expected: 374 passed (was 370 + 4 new).

Lint check: `python -m ruff check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add ui/widgets.py tests/test_api_key_row_helper.py
git commit -m "$(cat <<'EOF'
feat(ui/widgets): add api_key_row factory — unified API-key input row

Extracts the repeated "label + masked entry + paste + validate + status"
cluster used in 4 places in ui/dialogs/settings.py into a single factory.
Threading contract: on_validate is a blocking caller-supplied function;
worker runs it in a daemon thread and marshals UI updates via
parent.after(0, ...).

Spec: docs/superpowers/specs/2026-05-28-settings-ux-redesign-design.md
EOF
)"
```

---

## Task 2: Migrate OpenRouter section to use `api_key_row`

**Files:**
- Modify: `ui/dialogs/settings.py` (lines 356-413: `_build_openrouter_section` + `_paste_openrouter_key`)
- Test: `tests/test_settings_dialog_uses_api_key_row.py` (create, asserts ≥ 1 call so far)

### Step 1: Write the failing test

Create `tests/test_settings_dialog_uses_api_key_row.py`:

```python
"""SettingsDialog uses the unified api_key_row helper for each API key section.

Source-text check — we cannot import ui.dialogs.settings (sounddevice
PortAudio issue on Linux CI). See feedback_ui_app_import_breaks_linux_ci.
"""
from __future__ import annotations

from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)


def test_settings_imports_api_key_row():
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "api_key_row" in source, (
        "ui/dialogs/settings.py must import api_key_row from ui.widgets"
    )


def test_settings_calls_api_key_row_at_least_four_times():
    """Cloud STT + OpenRouter + Linear + Glide = 4 call sites. Counted
    via 'api_key_row(' rather than imports to catch lazy use."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    n_calls = source.count("api_key_row(")
    # First occurrence may be the import itself ("api_key_row,")
    # so count only the *call* form with the open paren.
    assert n_calls >= 4, (
        f"Expected ≥ 4 api_key_row(...) calls in settings.py, got {n_calls}"
    )
```

This test ASSERTS 4 calls. Task 2 alone won't reach 4 — that's expected. The test stays red until Task 5 (Cloud STT migration) completes. **Workaround for incremental TDD**: temporarily change the assertion to `>= 1` for Tasks 2-4, bump up at each step. Final state after Task 5 = `>= 4`.

In Task 2 specifically, the second test should read `assert n_calls >= 1`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_dialog_uses_api_key_row.py -v`
Expected: 2 FAIL ("api_key_row not in source").

- [ ] **Step 3: Migrate OpenRouter section**

Open `ui/dialogs/settings.py`. Replace lines 358-401 (`_build_openrouter_section` method body) with:

```python
    def _build_openrouter_section(self, parent) -> None:
        """OpenRouter API key + default model.

        API key handling delegated to ui.widgets.api_key_row.
        Default-model dropdown remains as a separate row below.
        """
        section = self._section_card(parent, "OpenRouter", row=5)

        def _persist_and_track(key: str, info: dict) -> None:
            self._parent._config["openrouter_api_key"] = key
            save_config(self._parent._config)

        def _on_validate(key: str) -> dict:
            from tasks.openrouter_client import OpenRouterClient
            client = OpenRouterClient(key)
            try:
                return client.validate_key()
            finally:
                client.close()

        def _format_success(info: dict) -> str:
            balance = info.get("balance_remaining")
            if balance is not None:
                return f"✓ Активен (баланс: ${balance:.2f})"
            return f"✓ Активен ({info.get('label') or 'unlimited'})"

        refs = api_key_row(
            section,
            label_text="API ключ",
            key_var=self._parent._openrouter_key_var,
            placeholder="sk-or-...",
            on_validate=_on_validate,
            on_key_persisted=_persist_and_track,
            format_success=_format_success,
            row=0,
        )
        # Status label still accessible if needed for future hooks
        self._openrouter_status = refs["status"]

        # Default model dropdown (unchanged)
        label(section, "Модель по умолчанию").grid(
            row=2, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        option_menu(
            section, self._parent._openrouter_default_model_var,
            list(_CURATED_MODELS.keys()),
        ).grid(row=2, column=1, columnspan=2, padx=4, pady=6, sticky="ew")
```

Then **delete** the now-orphan `_paste_openrouter_key` method (lines 402-415) and the standalone `_validate_openrouter` method (lines 635-692) — both are superseded by `api_key_row`'s internals.

Update imports at top of `ui/dialogs/settings.py`. Find the existing import:

```python
from ui.widgets import (
    card,
    label,
    option_menu,
    primary_button,
    tonal_button,
)
```

Replace with:

```python
from ui.widgets import (
    api_key_row,
    card,
    label,
    option_menu,
    primary_button,
    tonal_button,
)
```

Also in the test file `tests/test_settings_dialog_uses_api_key_row.py`, temporarily change the second assertion to:

```python
    assert n_calls >= 1, (
        f"Expected ≥ 1 api_key_row(...) call so far (Task 2), got {n_calls}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_settings_dialog_uses_api_key_row.py -v`
Expected: 2 PASS.

Full suite: `python -m pytest -q`
Expected: still passes (no regression).

Manual smoke (defer until Task 9 batches them).

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/settings.py tests/test_settings_dialog_uses_api_key_row.py
git commit -m "refactor(ui/settings): migrate OpenRouter section to api_key_row helper"
```

---

## Task 3: Migrate Linear section to use `api_key_row`

**Files:**
- Modify: `ui/dialogs/settings.py` (`_build_linear_section`, `_paste_linear_key`, `_validate_linear`)
- Modify: `tests/test_settings_dialog_uses_api_key_row.py` (bump assertion to ≥ 2)

- [ ] **Step 1: Bump the test assertion**

In `tests/test_settings_dialog_uses_api_key_row.py`, change the assertion to:

```python
    assert n_calls >= 2, (
        f"Expected ≥ 2 api_key_row(...) calls after Task 3, got {n_calls}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_dialog_uses_api_key_row.py::test_settings_calls_api_key_row_at_least_four_times -v`
Expected: FAIL (only 1 call so far from Task 2).

- [ ] **Step 3: Migrate Linear section**

Replace `_build_linear_section` (lines 419-462) with:

```python
    def _build_linear_section(self, parent) -> None:
        """Linear API key + connection status (Phase 6.4).

        enable-checkbox + API key handling delegated to api_key_row.
        No team picker here — that's per-run in ExtractTasksDialog.
        """
        section = self._section_card(parent, "Linear", row=6)

        def _persist(key: str, info: dict) -> None:
            self._parent._config["linear_api_key"] = key
            save_config(self._parent._config)

        def _on_validate(key: str) -> dict:
            from tasks.linear_client import LinearClient
            client = LinearClient(key)
            try:
                return client.validate_key()
            finally:
                client.close()

        def _format_success(info: dict) -> str:
            name = info.get("name") or info.get("email") or "(unknown)"
            return f"✓ Подключено: {name}"

        refs = api_key_row(
            section,
            label_text="API ключ",
            key_var=self._parent._linear_key_var,
            placeholder="lin_api_...",
            on_validate=_on_validate,
            on_key_persisted=_persist,
            enabled_var=self._parent._linear_enabled_var,
            enabled_label="Использовать Linear",
            on_enabled_changed=self._parent._on_linear_enabled_changed,
            format_success=_format_success,
            row=0,
        )
        self._linear_status = refs["status"]
```

**Delete** `_paste_linear_key` (lines 464-475) and `_validate_linear` (lines 477-523) — both superseded.

- [ ] **Step 4: Run tests to verify**

`python -m pytest tests/test_settings_dialog_uses_api_key_row.py -v` → PASS (2 calls now)
`python -m pytest -q` → no regressions
`python -m ruff check .` → clean

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/settings.py tests/test_settings_dialog_uses_api_key_row.py
git commit -m "refactor(ui/settings): migrate Linear section to api_key_row helper"
```

---

## Task 4: Migrate Glide section to use `api_key_row`

**Files:**
- Modify: `ui/dialogs/settings.py` (`_build_glide_section`, `_paste_glide_key`, `_validate_glide`)
- Modify: `tests/test_settings_dialog_uses_api_key_row.py` (bump to ≥ 3)

- [ ] **Step 1: Bump the test assertion**

In `tests/test_settings_dialog_uses_api_key_row.py`:

```python
    assert n_calls >= 3, (
        f"Expected ≥ 3 api_key_row(...) calls after Task 4, got {n_calls}"
    )
```

- [ ] **Step 2: Verify the test fails**

`python -m pytest tests/test_settings_dialog_uses_api_key_row.py -v` → FAIL (2 < 3).

- [ ] **Step 3: Migrate Glide section**

Replace `_build_glide_section` (lines 525-567) with:

```python
    def _build_glide_section(self, parent) -> None:
        """Glide API key + connection status (Phase 6.4).

        Mirrors the Linear section pattern (enable-checkbox + validate
        through api_key_row).
        """
        section = self._section_card(parent, "Glide", row=7)

        def _persist(key: str, info: dict) -> None:
            self._parent._config["glide_api_key"] = key
            save_config(self._parent._config)

        def _on_validate(key: str) -> dict:
            from tasks.glide_client import GlideClient
            client = GlideClient(key)
            try:
                return client.validate_key()
            finally:
                client.close()

        def _format_success(info: dict) -> str:
            count = info["board_count"]
            sample = info["sample_names"]
            base = f"✓ Подключено: {count} досок"
            if sample:
                base += f" ({', '.join(sample)})"
            return base

        refs = api_key_row(
            section,
            label_text="API ключ",
            key_var=self._parent._glide_key_var,
            placeholder="glide_pk_<workspace>_...",
            on_validate=_on_validate,
            on_key_persisted=_persist,
            enabled_var=self._parent._glide_enabled_var,
            enabled_label="Использовать Glide",
            on_enabled_changed=self._parent._on_glide_enabled_changed,
            format_success=_format_success,
            row=0,
        )
        self._glide_status = refs["status"]
```

**Delete** `_paste_glide_key` and `_validate_glide` methods (lines 569-633).

- [ ] **Step 4: Run tests**

`python -m pytest tests/test_settings_dialog_uses_api_key_row.py -v` → PASS
`python -m pytest -q` → green
`python -m ruff check .` → clean

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/settings.py tests/test_settings_dialog_uses_api_key_row.py
git commit -m "refactor(ui/settings): migrate Glide section to api_key_row helper"
```

---

## Task 5: Migrate Cloud STT section to use `api_key_row` (no validate)

**Files:**
- Modify: `ui/dialogs/settings.py` (`_build_cloud_section`)
- Modify: `tests/test_settings_dialog_uses_api_key_row.py` (bump to ≥ 4 — the spec target)
- Modify: `ui/app/settings_mixin.py` (`_paste_cloud_api_key` is no longer called from settings.py; keep it on App for any other caller, OR delete if unused — verify with grep first)

- [ ] **Step 1: Set test to final target**

In `tests/test_settings_dialog_uses_api_key_row.py`:

```python
    assert n_calls >= 4, (
        f"Expected ≥ 4 api_key_row(...) calls (Cloud STT + OpenRouter + "
        f"Linear + Glide), got {n_calls}"
    )
```

- [ ] **Step 2: Verify the test fails**

`python -m pytest tests/test_settings_dialog_uses_api_key_row.py -v` → FAIL (3 < 4).

- [ ] **Step 3: Migrate Cloud STT section**

Replace `_build_cloud_section` (lines 265-323) with:

```python
    def _build_cloud_section(self, parent) -> None:
        """Cloud STT provider + API key + privacy + price disclosure.

        Key handling via api_key_row (no validate — Cloud STT validation
        is deferred to a follow-up PR per spec). Provider dropdown stays
        as a separate row because its callback wires into the mixed-lang
        banner condition.
        """
        section = self._section_card(parent, "Облачное распознавание", row=3)

        # Provider dropdown
        label(section, "Провайдер").grid(
            row=0, column=0, padx=(4, 8), pady=6, sticky="w",
        )
        option_menu(
            section, self._parent._cloud_provider_var, list(PROVIDERS.keys()),
            command=self._parent._on_cloud_provider_changed,
        ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

        # API key row — no on_validate (deferred). Capture entry ref so
        # the first-run banner can focus_set() it on click.
        refs = api_key_row(
            section,
            label_text="API ключ",
            key_var=self._parent._cloud_api_key_var,
            placeholder="API ключ провайдера",
            on_validate=None,
            row=1,
        )
        self._cloud_api_key_entry = refs["entry"]

        # Privacy disclosure (moved below the key field — reads as context
        # AFTER the field it applies to)
        label(
            section,
            "⚠ Аудио загружается на сервер провайдера. "
            "Не используй для конфиденциальных записей.",
            anchor="w",
        ).grid(row=3, column=0, columnspan=4, padx=4, pady=(2, 6), sticky="w")

        # Static price summary
        label(
            section,
            "ℹ Цены с диаризацией: AssemblyAI ~$0.17/ч • "
            "Deepgram ~$0.43/ч • Gladia ~$0.61/ч • "
            "Speechmatics ~$1.04/ч.",
            anchor="w",
        ).grid(row=4, column=0, columnspan=4, padx=4, pady=(0, 4), sticky="w")
```

**IMPORTANT**: The old `_build_cloud_section` had inline `self._mixed_warning` widget. That widget is **removed** here — it gets reintroduced as the global banner in Task 7. The reactive `_update_mixed_warning` method also gets refactored in Task 7. For now, just delete the inline widget (lines 279-289 of the original).

**Verify** `_paste_cloud_api_key` on the App side (in `ui/app/settings_mixin.py`):

```bash
grep -rn "_paste_cloud_api_key" --include="*.py"
```

If it's only referenced inside `settings_mixin.py` (its definition) and the now-replaced `_build_cloud_section` body, it's unused after this change — delete the App method. If anything else references it, leave it.

- [ ] **Step 4: Run tests**

`python -m pytest tests/test_settings_dialog_uses_api_key_row.py -v` → PASS (4 calls)
`python -m pytest -q` → all green
`python -m ruff check .` → clean (unused imports should be flagged if `_paste_*` deletion left an orphan `from tk import ...`)

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/settings.py ui/app/settings_mixin.py tests/test_settings_dialog_uses_api_key_row.py
git commit -m "$(cat <<'EOF'
refactor(ui/settings): migrate Cloud STT section to api_key_row helper

Renames "Транскрибация (cloud API)" → "Облачное распознавание".
No validate button (deferred to a follow-up PR per spec). Reorders
audio-leaves-server warning + pricing summary to read AFTER the key
field they apply to. Captures entry ref on the dialog as
self._cloud_api_key_entry — Task 7's banner uses it.

Inline _mixed_warning widget removed here; it returns as a global
banner in the next task.
EOF
)"
```

---

## Task 6: Introduce `CTkTabview` + redistribute 9 sections into 3 tabs

**Files:**
- Modify: `ui/dialogs/settings.py` (`__init__`, scroll-body construction)
- Create: `tests/test_settings_dialog_has_tabview.py`

### Step 1: Write the failing test

Create `tests/test_settings_dialog_has_tabview.py`:

```python
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
    """Three tabs: Транскрипция, Интеграции, Резервная копия. We grep
    for `.add("<name>")` calls — flexible to either chained-call
    construction or post-construction tab adding."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")

    expected = ["Транскрипция", "Интеграции", "Резервная копия"]
    for name in expected:
        pattern = rf'\.add\(\s*[\'"]{re.escape(name)}[\'"]\s*\)'
        assert re.search(pattern, source), (
            f'Expected `.add("{name}")` call in settings.py'
        )
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_settings_dialog_has_tabview.py -v` → FAIL.

- [ ] **Step 3: Refactor `__init__` to use CTkTabview**

In `ui/dialogs/settings.py`, replace the body construction (lines 82-101 of the original — the `CTkScrollableFrame` and 9 `self._build_*_section(body)` calls) with this tabview-based version. Replace:

```python
        # --- Scrollable content ---
        body = ctk.CTkScrollableFrame(
            self, fg_color="transparent", corner_radius=0,
        )
        body.grid(row=1, column=0, padx=12, pady=(8, 4), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        self._build_appearance_section(body)
        self._build_transcription_section(body)
        self._build_audio_section(body)
        self._build_cloud_section(body)
        self._build_dictionaries_section(body)
        self._build_openrouter_section(body)
        self._build_linear_section(body)
        self._build_glide_section(body)
        self._build_gdrive_section(body)
```

with:

```python
        # --- Tab view ---
        # CTkTabview inherits Light/Dark from theme automatically
        # (unlike ttk.Notebook which needs manual ttk.Style for each mode).
        self._tabview = ctk.CTkTabview(
            self,
            fg_color="transparent",
            segmented_button_fg_color=SURFACE,
            segmented_button_selected_color=BLUE,
            segmented_button_selected_hover_color=BLUE_DIM,
            segmented_button_unselected_color=SURFACE,
            text_color=TEXT_PRIMARY,
        )
        self._tabview.grid(row=2, column=0, padx=12, pady=(4, 4), sticky="nsew")
        self.grid_rowconfigure(2, weight=1)

        tab_transcription = self._tabview.add("Транскрипция")
        tab_integrations = self._tabview.add("Интеграции")
        tab_backup = self._tabview.add("Резервная копия")

        tab_transcription.grid_columnconfigure(0, weight=1)
        tab_integrations.grid_columnconfigure(0, weight=1)
        tab_backup.grid_columnconfigure(0, weight=1)

        # Default tab — Транскрипция (where the STT key lives, the
        # first-run focal point).
        self._tabview.set("Транскрипция")

        # Tab 1: Транскрипция (core loop)
        self._build_appearance_section(tab_transcription)
        self._build_transcription_section(tab_transcription)
        self._build_audio_section(tab_transcription)
        self._build_cloud_section(tab_transcription)
        self._build_dictionaries_section(tab_transcription)

        # Tab 2: Интеграции (LLM + task export)
        self._build_openrouter_section(tab_integrations)
        self._build_linear_section(tab_integrations)
        self._build_glide_section(tab_integrations)

        # Tab 3: Резервная копия (housekeeping)
        self._build_gdrive_section(tab_backup)
```

Also update the row attribute in `_section_card` calls inside each `_build_*_section` method: the row numbers currently chain 0-8 across the whole scrolling body, but inside each tab the rows reset. Search each `_section_card(parent, "...", row=N)` call — for sections under the same tab, the rows should be 0, 1, 2... sequentially within that tab.

Specifically:
- `_build_appearance_section`: `row=0` (already 0 — OK)
- `_build_transcription_section`: change `row=1` → `row=1`
- `_build_audio_section`: change `row=2` → `row=2`
- `_build_cloud_section`: change `row=3` → `row=3`
- `_build_dictionaries_section`: change `row=4` → `row=4`
- `_build_openrouter_section`: change `row=5` → `row=0` (under Интеграции tab)
- `_build_linear_section`: change `row=6` → `row=1`
- `_build_glide_section`: change `row=7` → `row=2`
- `_build_gdrive_section`: change `row=8` → `row=0` (under Резервная копия tab)

Add `from theme import SURFACE` etc. if not already imported (likely they are — verify).

- [ ] **Step 4: Run tests**

`python -m pytest tests/test_settings_dialog_has_tabview.py -v` → PASS
`python -m pytest -q` → green
`python -m ruff check .` → clean

**Manual smoke (do once, not per-task)** — defer until Task 9.

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/settings.py tests/test_settings_dialog_has_tabview.py
git commit -m "$(cat <<'EOF'
refactor(ui/settings): replace 9-section scroll with 3-tab CTkTabview

Tab 1 «Транскрипция» (default): Внешний вид, Язык, Аудио, Облачное
распознавание, Словари — minimal sufficient set for a first-run client.
Tab 2 «Интеграции»: OpenRouter, Linear, Glide (LLM + task export).
Tab 3 «Резервная копия»: Google Drive (independent housekeeping).
EOF
)"
```

---

## Task 7: Add global first-run banner (clickable, reactive)

**Files:**
- Modify: `ui/dialogs/settings.py` (`__init__`, new `_update_banner`, `_jump_to_stt`, `_jump_to_lang` methods, `destroy` extension)
- Create: `tests/test_settings_dialog_banner.py`

### Step 1: Write the failing test

Create `tests/test_settings_dialog_banner.py`:

```python
"""SettingsDialog has a reactive banner subscribed to the three required vars.

Source/AST checks only.
"""
from __future__ import annotations

import ast
from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)


def _get_method_source(class_name: str, method_name: str) -> str | None:
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == class_name:
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == method_name:
                    return ast.unparse(fn)
    return None


def test_init_subscribes_trace_on_three_vars():
    init_src = _get_method_source("SettingsDialog", "__init__")
    assert init_src is not None

    for var_name in ("_cloud_api_key_var", "_lang_var", "_cloud_provider_var"):
        assert var_name in init_src, (
            f"__init__ must subscribe trace_add on {var_name}"
        )
    # Three trace_add calls (one per var)
    assert init_src.count("trace_add") >= 3, (
        f"Expected ≥ 3 trace_add calls in __init__ "
        f"(got {init_src.count('trace_add')})"
    )


def test_destroy_unregisters_trace_tokens():
    """The PR #25 pattern: store trace tokens as instance attrs, remove
    in destroy() to avoid stale-trace TclError on dialog re-open."""
    destroy_src = _get_method_source("SettingsDialog", "destroy")
    assert destroy_src is not None

    assert "trace_remove" in destroy_src, (
        "destroy() must unregister trace tokens to prevent stale-trace "
        "TclError on dialog re-open (PR #25 pattern)"
    )


def test_update_banner_method_exists():
    src = _get_method_source("SettingsDialog", "_update_banner")
    assert src is not None, (
        "_update_banner method must exist (subscribed via trace_add)"
    )


def test_banner_click_handler_exists():
    """Banner is clickable per spec — at least one jump handler must exist
    that calls _tabview.set("Транскрипция")."""
    src = SETTINGS_PATH.read_text(encoding="utf-8")
    assert 'self._tabview.set("Транскрипция")' in src, (
        "Banner click handler must switch to Транскрипция tab"
    )
    assert "focus_set()" in src, (
        "Banner click handler must focus the relevant widget"
    )
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_settings_dialog_banner.py -v` → multiple FAIL.

- [ ] **Step 3: Add banner widget + reactive logic + click handlers**

In `ui/dialogs/settings.py` `__init__`, AFTER the header (line ~82) and BEFORE the tabview construction, add the banner:

```python
        # --- Status banner (between header and tabs, visible on all tabs) ---
        # Click-to-jump: see _update_banner for state machine.
        self._banner = ctk.CTkButton(
            self,
            text="",
            command=self._handle_banner_click,
            fg_color="transparent",
            hover_color=SURFACE_BRIGHT,
            text_color=RED,
            anchor="w",
            font=ctk.CTkFont(family=FONT, size=12),
            corner_radius=0,
            height=32,
        )
        self._banner.grid(row=1, column=0, padx=12, pady=(0, 4), sticky="ew")
        self._banner.grid_remove()  # hidden until needed
        # State: what to do when banner is clicked ("stt" or "lang")
        self._banner_action: str | None = None
```

Update tabview row from `row=2` (set in Task 6) — leave at 2, banner sits between header (row=0) and tabs (row=2) with banner at row=1. So tabview row=2 is correct.

Update grid_rowconfigure: the tabview row should still expand. Just confirm `self.grid_rowconfigure(2, weight=1)` is in `__init__`. (Was originally `1`.)

Then at the END of `__init__` (after the existing `_update_mixed_warning()` call which is now obsolete — remove it), wire the traces:

```python
        # Reactive banner: subscribe to the three vars that affect its
        # condition. Tokens kept on self so destroy() can unregister them
        # (PR #25 pattern, extended).
        self._trace_lang = self._parent._lang_var.trace_add(
            "write", self._update_banner,
        )
        self._trace_provider = self._parent._cloud_provider_var.trace_add(
            "write", self._update_banner,
        )
        self._trace_api_key = self._parent._cloud_api_key_var.trace_add(
            "write", self._update_banner,
        )
        # Run once at construction so an already-loaded incompatible config
        # surfaces the banner immediately, not just after first interaction.
        self._update_banner()
```

(Remove the now-duplicate `self._trace_lang` and `self._trace_provider` that were set for `_update_mixed_warning` — they're replaced here.)

Add the three new methods to `SettingsDialog` class:

```python
    def _update_banner(self, *_args) -> None:
        """Show banner with the highest-priority actionable issue.

        Priority (top match wins):
          1. Cloud STT key empty → red "Введите ключ" + action=stt
          2. Mixed language + provider doesn't support it → red warning + action=lang
          3. No issue → hide banner
        """
        cloud_key = (self._parent._cloud_api_key_var.get() or "").strip()
        if not cloud_key:
            self._banner.configure(
                text="⚠ Введите ключ провайдера STT (вкладка «Транскрипция») →",
                text_color=RED,
            )
            self._banner_action = "stt"
            self._banner.grid()
            return

        lang_label = self._parent._lang_var.get()
        lang_code = LANGUAGES.get(lang_label)
        if lang_code == "mixed":
            provider_name = self._parent._cloud_provider_var.get()
            provider_cls = PROVIDERS.get(provider_name)
            if provider_cls is not None and not provider_cls.supports_mixed:
                self._banner.configure(
                    text=(
                        f"⚠ {provider_name} не поддерживает «Смешанный "
                        f"(KZ+RU+EN)». Выберите другой провайдер или язык →"
                    ),
                    text_color=RED,
                )
                self._banner_action = "lang"
                self._banner.grid()
                return

        # No actionable issue
        self._banner.grid_remove()
        self._banner_action = None

    def _handle_banner_click(self) -> None:
        """Banner is clickable — jump to relevant control."""
        if self._banner_action == "stt":
            self._jump_to_stt()
        elif self._banner_action == "lang":
            self._jump_to_lang()

    def _jump_to_stt(self) -> None:
        self._tabview.set("Транскрипция")
        # _cloud_api_key_entry is captured in _build_cloud_section (Task 5).
        if getattr(self, "_cloud_api_key_entry", None) is not None:
            self._cloud_api_key_entry.focus_set()

    def _jump_to_lang(self) -> None:
        self._tabview.set("Транскрипция")
        if getattr(self, "_lang_menu", None) is not None:
            self._lang_menu.focus_set()
```

Capture `_lang_menu` ref in `_build_transcription_section` — line ~194 of the original:

```python
    def _build_transcription_section(self, parent) -> None:
        section = self._section_card(parent, "Транскрипция", row=1)

        label(section, "Язык").grid(row=0, column=0, padx=(4, 8), pady=6, sticky="w")
        self._lang_menu = option_menu(   # ← captured for banner jump
            section, self._parent._lang_var, list(LANGUAGES.keys()),
            command=self._parent._on_language_changed,
        )
        self._lang_menu.grid(row=0, column=1, padx=4, pady=6, sticky="w")
```

Extend `destroy()` to unregister the new trace and remove the old `_update_mixed_warning` method entirely. The destroy loop becomes:

```python
    def destroy(self) -> None:
        """Remove app-level Var traces before the Toplevel is torn down."""
        for var, token in (
            (self._parent._lang_var, getattr(self, "_trace_lang", None)),
            (self._parent._cloud_provider_var, getattr(self, "_trace_provider", None)),
            (self._parent._cloud_api_key_var, getattr(self, "_trace_api_key", None)),
        ):
            if token is not None:
                try:
                    var.trace_remove("write", token)
                except tk.TclError:
                    pass
        super().destroy()
```

**Delete** the old `_update_mixed_warning` method (lines 199-234 of the original).

- [ ] **Step 4: Run tests**

`python -m pytest tests/test_settings_dialog_banner.py -v` → PASS
`python -m pytest -q` → green
`python -m ruff check .` → clean

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/settings.py tests/test_settings_dialog_banner.py
git commit -m "$(cat <<'EOF'
feat(ui/settings): global clickable first-run banner

Replaces the inline _mixed_warning widget with a global banner above
the tab bar. Two states (priority top-down):
  1. STT key empty → red "Введите ключ STT" + click jumps to tab 1, focuses entry
  2. Mixed lang + provider doesn't support it → red warning + click focuses lang menu

Reactive via trace_add on _cloud_api_key_var, _lang_var, _cloud_provider_var.
Trace tokens unregistered in destroy() (extends PR #25 pattern).
EOF
)"
```

---

## Task 8: Visual polish (H1 removal, Esc, tonal footer, iconbitmap, naming)

**Files:**
- Modify: `ui/dialogs/settings.py` (`__init__` header + footer)
- Create: `tests/test_settings_dialog_no_inner_h1.py`
- Create: `tests/test_settings_dialog_naming.py`

### Step 1: Write the failing tests

Create `tests/test_settings_dialog_no_inner_h1.py`:

```python
"""SettingsDialog must not duplicate the "Настройки" heading inside the
window body — the OS title bar already shows it."""
from __future__ import annotations

from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)


def test_only_one_settings_title_text():
    """Exactly one occurrence of `text="Настройки"` — the self.title(...) call.
    A second one inside an inline CTkLabel duplicates the title visually."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    # Pattern matches `text="Настройки"` and `text='Настройки'`
    count = source.count('"Настройки"') + source.count("'Настройки'")
    assert count == 1, (
        f"Expected exactly 1 'Настройки' string (the self.title() call), "
        f"got {count} — there's likely a duplicate H1 in the body"
    )
```

Create `tests/test_settings_dialog_naming.py`:

```python
"""Naming consistency: no 'Транскрибация' (use 'Транскрипция' / 'Облачное
распознавание' instead per the redesign spec)."""
from __future__ import annotations

from pathlib import Path

SETTINGS_PATH = (
    Path(__file__).resolve().parent.parent / "ui" / "dialogs" / "settings.py"
)


def test_no_transkribatsia_form():
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "Транскрибация" not in source, (
        "Use 'Транскрипция' or 'Облачное распознавание' — not 'Транскрибация'"
    )


def test_oblachnoe_raspoznavanie_present():
    """The Cloud STT section title was renamed to 'Облачное распознавание'."""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "Облачное распознавание" in source, (
        "Cloud STT section title must be 'Облачное распознавание' per spec"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

`python -m pytest tests/test_settings_dialog_no_inner_h1.py tests/test_settings_dialog_naming.py -v`
Expected: FAIL — the inner H1 still exists, and "Транскрибация" still in source if Task 5 wasn't perfectly clean.

(If Task 5's rename was clean, `test_no_transkribatsia_form` passes already. That's fine — TDD doesn't require ALL tests to be red, just the new behaviour assertions.)

- [ ] **Step 3: Apply visual polish**

In `ui/dialogs/settings.py`, find `__init__` header construction (lines 74-81 of the original):

```python
        # --- Header ---
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            header, text="Настройки",
            font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=20, pady=12)
```

**Delete this entire block.** The OS title bar already shows "Настройки" — duplication serves no purpose, and removing it gains ~60 px of vertical space.

(If you find you want a small visual divider where the header was, use a 1px-tall `CTkFrame` with `fg_color=BORDER` — but the spec doesn't require it.)

Then update the footer (lines 121-127 of the original):

```python
        # --- Footer ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(4, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        tonal_button(
            footer, text="Закрыть", command=self.destroy, width=120,
        ).grid(row=0, column=0, sticky="e")
```

Changes vs original: `primary_button` → `tonal_button`, footer grid row changed from `2` → `3` (because the banner now occupies row=1, tabview row=2).

Add Esc-key binding in `__init__` after the footer:

```python
        # Esc closes — standard modal-dialog convention.
        self.bind("<Escape>", lambda _e: self.destroy())
```

Add iconbitmap workaround in `__init__` after `self.grab_set()`:

```python
        # CTkToplevel quirk: immediate iconbitmap() is silently dropped
        # if the WM hasn't finished handshaking. Defer 200ms so the call
        # lands after Windows has assigned the WM_CLASS / icon slot.
        self.after(200, self._apply_dialog_icon)
```

And the helper method:

```python
    def _apply_dialog_icon(self) -> None:
        """Apply the audio_transcriber.ico to this Toplevel.

        Reads the icon path from utils (PyInstaller-aware resolver) so
        both dev and bundled execution paths work. Swallows TclError —
        on Linux/macOS .ico is not supported, but dialog still opens fine.
        """
        try:
            from utils import get_app_icon_path
            icon_path = get_app_icon_path()
            if icon_path:
                self.iconbitmap(icon_path)
        except (tk.TclError, ImportError, FileNotFoundError):
            pass
```

(Note: `get_app_icon_path()` must exist in `utils.py`. If it doesn't, check what App.__init__ uses to set its icon and reuse the same pattern. Per the PyInstaller spec there's `vendor/icons/audio_transcriber.ico` bundled — likely via `utils.get_app_icon_path()` or similar resolver. Verify with `grep "iconbitmap" -rn ui/ utils.py`.)

Update grid row config for the new layout:

```python
        self.grid_columnconfigure(0, weight=1)
        # row 0 = (removed header), row 1 = banner, row 2 = tabview, row 3 = footer
        self.grid_rowconfigure(2, weight=1)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_settings_dialog_no_inner_h1.py tests/test_settings_dialog_naming.py -v
python -m pytest -q
python -m ruff check .
```

All expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add ui/dialogs/settings.py tests/test_settings_dialog_no_inner_h1.py tests/test_settings_dialog_naming.py
git commit -m "$(cat <<'EOF'
fix(ui/settings): visual polish — drop inner H1, Esc, tonal footer, dialog icon

- Drops the duplicate "Настройки" CTkLabel inside the body
  (OS title bar already shows it). Reclaims ~60px vertical.
- Footer "Закрыть" switched from primary_button (blue CTA) to
  tonal_button — it's a cancel action, not the primary action.
- Esc key bound to dialog.destroy() — standard modal convention.
- iconbitmap applied via self.after(200, ...) to work around the
  CTkToplevel WM-handshake race that silently drops immediate
  iconbitmap() calls on Windows.
EOF
)"
```

---

## Task 9: Manual smoke + PyInstaller verification

**Files:** none modified — verification only.

This task batches all visual checks deferred from earlier tasks and ensures the PyInstaller build path still works.

- [ ] **Step 1: Run the full automated suite one last time**

```powershell
python -m pytest -q
python -m ruff check .
```

Expected: 376 passed (370 baseline + 6 new), ruff clean.

- [ ] **Step 2: Dev-mode smoke (`python app.py`)**

Launch: `python app.py`

Walk through the checklist:
- [ ] App opens without ImportError
- [ ] Click "Настройки" — dialog opens with title "Настройки" in title bar (no duplicate H1 in body)
- [ ] Dialog shows 3 tabs — Транскрипция (active), Интеграции, Резервная копия
- [ ] Default tab content: Внешний вид, Транскрипция (язык), Аудио, Облачное распознавание, Словари
- [ ] If `cloud_api_keys[AssemblyAI]` is empty (clear it in Settings or directly in config.json), red banner visible above tabs with "Введите ключ"
- [ ] Click banner → already on Транскрипция tab; entry receives keyboard focus (cursor visible)
- [ ] Eye-toggle 👁 button next to API key field flips masked ↔ unmasked
- [ ] Re-fill the key → banner disappears
- [ ] Switch language to "Смешанный (KZ+RU+EN)" + provider to "Deepgram" → red banner reappears with mixed-lang warning
- [ ] Click that banner → tab stays on Транскрипция; lang dropdown receives focus
- [ ] Switch theme Светлая ↔ Тёмная via Тема dropdown — tabs, banner, all controls re-color cleanly
- [ ] Tab 2: OpenRouter section — Проверить button still works (paste a real key, click, see "✓ Активен (баланс $X.YZ)")
- [ ] Tab 2: Linear/Glide — same Проверить flow (if test keys available)
- [ ] Tab 3: Google Drive — Войти/Выйти + Сделать backup сейчас buttons still functional
- [ ] Press Esc — dialog closes
- [ ] Re-open Settings 3 times in a row → no warnings in `logs/app.log` about stale traces

- [ ] **Step 3: PyInstaller bundle build**

```powershell
& '.\.venv-build\Scripts\Activate.ps1'; .\scripts\build_exe.ps1
```

Expected: build completes, bundle ~350 MB, no PyInstaller warnings about missing `api_key_row` or `CTkTabview`.

- [ ] **Step 4: Bundled smoke**

```powershell
Start-Process '.\dist\AudioTranscriber\AudioTranscriber.exe'
```

Wait 3 seconds, verify process alive. Open Settings, repeat the checklist above (or just spot-check the 3 tabs + banner + Esc).

Check sidecar log for boot-time errors:

```powershell
Get-Content (Join-Path $env:TEMP 'audio-transcriber-bootstrap.log') -Tail 5
```

Expected: only `=== audio-transcriber bootstrap @ pid=N ===` markers, no exceptions.

- [ ] **Step 5: Final commit (if anything tweaked during smoke)**

If smoke surfaced a small fix:
```bash
git add <fixed-files>
git commit -m "fix(ui/settings): <description from smoke finding>"
```

If smoke was clean: no commit needed for Task 9.

- [ ] **Step 6: Push branch + open PR**

```bash
git push -u origin fix/v0.1-dev-smoke-fixes-pt2
gh pr create --title "feat(ui/settings): 3-tab redesign + api_key_row helper + first-run banner" --body "$(cat <<'EOF'
## Summary
- Restructures the Settings dialog from a 9-section vertical scroll into a 3-tab `CTkTabview` layout
- Extracts the 4 repeated API-key clusters into a unified `api_key_row` helper in `ui/widgets.py`
- Adds a global clickable first-run banner above the tab bar that warns about empty STT key or mixed-lang/provider incompatibility
- Visual polish: drops duplicate "Настройки" H1, adds Esc binding, switches "Закрыть" to tonal button, renames "Транскрибация" → "Облачное распознавание", adds iconbitmap workaround for CTkToplevel WM race
- Zero changes to `config.json` schema, App-level Vars, or `_on_*_changed` callbacks — pure visual/structural refactor

## Test plan
- [x] `python -m pytest -q` → 376 passed
- [x] `python -m ruff check .` → clean
- [x] Dev smoke (`python app.py`) — full checklist passed
- [x] PyInstaller bundle smoke (`dist/AudioTranscriber/AudioTranscriber.exe`) — opens, no sidecar errors

Spec: [docs/superpowers/specs/2026-05-28-settings-ux-redesign-design.md](docs/superpowers/specs/2026-05-28-settings-ux-redesign-design.md)
Plan: [docs/superpowers/plans/2026-05-28-settings-ux-redesign-plan.md](docs/superpowers/plans/2026-05-28-settings-ux-redesign-plan.md)
EOF
)"
```

---

## Summary

9 tasks, each with TDD steps (test → fail → implement → pass → commit). Net result:

- **`ui/widgets.py`**: +1 helper (`api_key_row`), ~80 LOC added
- **`ui/dialogs/settings.py`**: ~−250 LOC (4× duplicated API-key code → 4× helper calls; `_mixed_warning` removed; `_update_mixed_warning`/`_validate_*`/`_paste_*` methods deleted). Net structure: 3 tabs + banner + cleaner sections.
- **`tests/`**: +6 source-text/AST test files (~150 LOC total)
- **`config.json` schema**: unchanged
- **`providers/*.py`**: unchanged
- **`ui/app/*`**: only `_paste_cloud_api_key` potentially removed if unused (verified in Task 5)
