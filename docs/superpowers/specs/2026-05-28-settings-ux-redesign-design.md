# Settings dialog UX redesign

**Date**: 2026-05-28
**Status**: Draft — ready for implementation planning
**Scope**: `ui/dialogs/settings.py` + `ui/widgets.py` (helper extraction)
**Target effort**: ~1 day (single PR, multi-commit OK)

> Restructure the Settings dialog from a 9-section vertical scroll into a 3-tab
> layout with a global first-run banner and a unified API-key widget. Purely
> visual / structural — zero changes to config.json schema, App-level
> `StringVar`/`BooleanVar` instances, or `_on_*_changed` callbacks.

## Context

The current Settings dialog ([ui/dialogs/settings.py](../../../ui/dialogs/settings.py))
packs 9 sections — Внешний вид, Транскрипция, Аудио, Транскрибация (cloud API),
Словари, OpenRouter, Linear, Glide, Google Drive — into a single
`CTkScrollableFrame`. Four of these sections (Cloud STT, OpenRouter, Linear,
Glide) repeat the same widget cluster: labeled `CTkEntry`, "Вставить" paste
button, "Проверить ключ" validator, status label. Total LOC ~900 with
significant duplication.

User-facing pain (confirmed via brainstorming on 2026-05-28):
1. Long scroll — 9 sections in one window, no jump-to-section navigation
2. Repetitive `API key + Вставить + Проверить` × 4 — visual noise + code duplication
3. First-run UX — no guidance on "which field is required to get started"
4. Visual nitpicks — duplicate "Настройки" heading, "Транскрипция" vs "Транскрибация" naming inconsistency, blue "Закрыть" looks like a primary CTA

Target user is mixed: paying clients open Settings once during initial setup
(must be obvious where to start), the developer (nurgisa) returns periodically
for tweaks (must support fast navigation between concerns).

## Architecture

### File-level changes

| File | Change |
|---|---|
| `ui/dialogs/settings.py` | Refactor `SettingsDialog`. Body becomes `CTkTabview` with 3 tabs. Existing `_build_*_section` methods get rehomed under their tab — internals unchanged, only `parent` argument differs. |
| `ui/widgets.py` | Add new helper `api_key_row(...)` — unified API-key row component (entry + eye-toggle + validate button + status label). |
| `ui/app/*` | **No changes.** All Vars stay on App. |
| `config.json` schema | **No changes.** No migration. |
| `providers/*.py` | **No changes** (Cloud STT validation deferred — see Out of scope). |

### Tab structure

| Tab | Sections inside | Why this tab |
|---|---|---|
| **Транскрипция** (default) | Внешний вид · Транскрипция (язык) · Аудио · Облачное распознавание · Словари | Minimal sufficient set for a first-time client: enter STT key → transcribe. Includes appearance because it's "the view of the window you work in" |
| **Интеграции** | OpenRouter · Linear · Glide | Optional extras: protocol generation + task export. All three are LLM-side API integrations — conceptually homogeneous |
| **Резервная копия** | Google Drive | Independent housekeeping concern; orthogonal to transcription |

### Tab choice rationale

- **3 tabs not 4**: appearance as its own tab is overkill (1 dropdown)
- **Default tab = Транскрипция**: where 80%+ of first-run interactions happen
- **Two tab-switches separation**: a client never has to touch tabs 2 and 3 to
  start transcribing; nurgisa as power user gets `Ctrl+Tab` between concerns
  (CTk natively binds it)

### Component: `api_key_row`

Lives in `ui/widgets.py` next to existing helpers (`card`, `label`,
`option_menu`, `primary_button`, `tonal_button`).

**Signature:**

```python
def api_key_row(
    parent,
    *,
    label_text: str,                                       # "API ключ" / "OpenRouter ключ"
    key_var: tk.StringVar,
    placeholder: str,                                      # "sk-or-...", "lin_api_...", etc.
    on_validate: Callable[[str], dict] | None = None,      # None → no validate button
    on_key_persisted: Callable[[str, dict], None] | None = None,
    enabled_var: tk.BooleanVar | None = None,              # Linear/Glide
    enabled_label: str | None = None,                      # "Использовать Linear"
    on_enabled_changed: Callable | None = None,
    format_success: Callable[[dict], str] = lambda d: "✓ Активен",
    row: int = 0,
) -> dict:                                                 # {"entry": ..., "validate_btn": ..., "status": ...}
```

Returns dict with widget references so the caller can `focus_set()` the entry
(used by the clickable banner — see below).

**Visual layout:**

```
[✓] Использовать Linear                                 ← optional row if enabled_var given

API ключ:  [••••••••••••••••••]  [👁]  [Проверить]   ✓ Активен (баланс $5.23)
            entry, expandable    eye   button         status label
```

**States:**

| State | entry | validate btn | status label |
|---|---|---|---|
| Idle empty | placeholder | enabled | hidden |
| Idle filled | masked •••• | enabled | hidden or last-validation result |
| Validating | readonly | disabled, "Проверка..." | "Проверка..." in `TEXT_SECONDARY` |
| Success | masked | enabled | `format_success(info)` in `GREEN` |
| Failure | masked | enabled | `✗ {error[:100]}` in `RED` |

**Threading contract:** `on_validate(key)` is **blocking** — caller-supplied
function that may make network calls. The widget runs it in a daemon thread
and marshals all UI updates via `parent.after(0, ...)`. This centralises the
"daemon thread + after marshal" pattern that is currently hand-rolled in 3
places ([settings.py:494-522, 598-633, 655-692](../../../ui/dialogs/settings.py)).

**The 4 call sites:**

| Site | enabled_var | on_validate | format_success |
|---|---|---|---|
| Облачное распознавание (Tab 1) | — | None (deferred) | (n/a — no validate button) |
| OpenRouter (Tab 2) | — | existing `OpenRouterClient.validate_key` | `f"✓ Активен (баланс ${d['balance']:.2f})"` |
| Linear (Tab 2) | `_linear_enabled_var` | existing `LinearClient.validate_key` | `f"✓ Подключено: {d['name']}"` |
| Glide (Tab 2) | `_glide_enabled_var` | existing `GlideClient.validate_key` | `f"✓ {d['board_count']} досок"` |

**Paste button dropped.** Native `Ctrl+V` works in masked `CTkEntry` (verified).
The current "Вставить" button across 4 sections was user-explicitly named as a
pain point. Removing it saves one grid column and ~40 LOC.

**Eye-toggle 👁 added** as a small tonal button next to the entry. Replaces the
"can I check if I pasted correctly" workflow that "Вставить" partially served.
Toggles `entry.configure(show="" if shown else "•")`. Default state: masked.

## First-run status banner

### Position
Between the header and the tab bar — **global**, visible on all tabs. One
widget, one state machine.

### States (priority top-down — show first match)

| Condition | Banner text | Style |
|---|---|---|
| `_cloud_api_key_var.get().strip() == ""` | `⚠ Введите ключ провайдера STT →` | `RED` text, transparent bg, hover-tint |
| `lang == "mixed"` AND `provider.supports_mixed == False` | `⚠ {provider} не поддерживает «Смешанный (KZ+RU+EN)» →` | `RED` text, transparent bg |
| else | (hidden) | — |

### Non-states (explicit)
- ✗ No `✓ Всё готово` success banner — silence-is-OK pattern; positive banners
  train users to ignore the banner area.
- ✗ No `ℹ Включи Linear для задач` suggestions — patronising for nurgisa,
  discoverable for clients via the main-screen "Извлечь задачи" button error
  message.

### Clickable behaviour
Banner is a `CTkButton` with `fg_color="transparent"`, `text_color=RED`,
`hover_color=SURFACE_BRIGHT`. Click handler:
- For empty-STT-key state: `self._tabview.set("Транскрипция")` →
  `self._cloud_api_key_entry.focus_set()`
- For mixed-language state: `self._tabview.set("Транскрипция")` →
  `self._lang_menu.focus_set()` (so user can immediately re-pick)

Trailing `→` arrow in banner text signals click-affordance.

### Reactivity
Subscribed via `trace_add("write", ...)` on three App-level StringVars:
`_cloud_api_key_var`, `_lang_var`, `_cloud_provider_var`. Trace tokens
stored as dialog attrs, unregistered in `destroy()` (extends the existing
PR #25 pattern at [settings.py:130-152](../../../ui/dialogs/settings.py)).

`_update_banner()` is also called once at the end of `__init__` so a config
loaded with an empty key shows the banner immediately — not only after the
first user interaction.

### Replaces
Current inline `_mixed_warning` widget ([settings.py:279-289](../../../ui/dialogs/settings.py))
is **removed**. The banner subsumes its condition. This eliminates dual-warning
duplication (mixed-warning at section level + STT-key-missing at top would
otherwise be redundant).

## Visual polish (mechanical)

1. **Remove inner H1 "Настройки"** ([settings.py:75-81](../../../ui/dialogs/settings.py)).
   Title bar text remains; header strip becomes ~30px tall. Reclaims ~60px of
   vertical space.
2. **Esc key binding** → `self.bind("<Escape>", lambda _e: self.destroy())`.
   Standard for modal dialogs.
3. **Footer button: `primary_button` → `tonal_button`** ([settings.py:125-127](../../../ui/dialogs/settings.py)).
   "Закрыть" is a cancel action, not primary; visual weight should match.
4. **Naming consistency**: section title changes from "Транскрибация (cloud
   API)" to **"Облачное распознавание"** (drop both the inconsistent
   "Транскрибация" form AND the parenthetical "cloud API" — Russian-only UI
   convention from CLAUDE.md).
5. **Dialog icon**: `SettingsDialog.__init__` to call `self.after(200, lambda:
   self.iconbitmap(<path>))` — works around CTkToplevel WM-handshake race
   where immediate `iconbitmap()` is silently dropped on Windows.
6. **Reorder within Cloud STT section** ([settings.py:265-323](../../../ui/dialogs/settings.py)):
   move "audio leaves your machine" warning and pricing summary line below the
   API-key row, so they read as context after the field they apply to.
7. **No changes** to: provider dropdown values, normalize/denoise checkbox
   labels, OpenRouter model dropdown contents (currently locked to
   `google/gemini-3.5-flash` for v0.1 ship per commit `5fa5cc1`).

## Testing strategy

### Constraint
Per `feedback_ui_app_import_breaks_linux_ci`: tests must not import
`ui.app` or `ui.dialogs.settings` directly — `sounddevice` loads PortAudio at
import time, Ubuntu CI runner lacks it. All automated tests are
**source-text checks** (read the file, regex/AST inspect).

### New tests

| File | What it asserts |
|---|---|
| `tests/test_settings_dialog_has_tabview.py` | `CTkTabview` imported in settings.py; exactly 3 `tabview.add(...)` calls; tab names match the spec ("Транскрипция", "Интеграции", "Резервная копия") |
| `tests/test_settings_dialog_naming.py` | Source contains no "Транскрибация"; "Облачное распознавание" appears at least once |
| `tests/test_api_key_row_helper.py` | `api_key_row` defined in `ui/widgets.py`; AST inspect confirms required kwargs (`key_var`, `placeholder`, `on_validate`, `enabled_var`, `format_success`) |
| `tests/test_settings_dialog_uses_api_key_row.py` | `api_key_row(` call appears ≥ 4 times in settings.py |
| `tests/test_settings_dialog_banner.py` | `trace_add("write", ...)` subscribed on `_cloud_api_key_var`, `_lang_var`, `_cloud_provider_var`; matching `trace_remove` calls present in `destroy()` |
| `tests/test_settings_dialog_no_inner_h1.py` | No second `text="Настройки"` after the `self.title("Настройки")` line |

### Regression protection

`grep -r "from ui.dialogs.settings" tests/` must return empty — if any
existing test imports `SettingsDialog`, it's already broken in CI; flag
during self-review.

### Manual smoke checklist (Windows-only)

After implementation:
- [ ] `python app.py` launches without ImportError
- [ ] Open Настройки → default tab is "Транскрипция"
- [ ] Clear cloud_api_key in config → reopen → red banner visible
- [ ] Click banner → tab switches to "Транскрипция", entry receives focus
- [ ] Esc closes dialog
- [ ] Eye-toggle 👁 flips masked/unmasked on all API key rows
- [ ] OpenRouter "Проверить" still works (real network call with a real key)
- [ ] Switch theme Тёмная↔Светлая mid-dialog — no layout breakage in tab bar
- [ ] Open/close dialog 3× in a row — no duplicate trace warnings in logs
- [ ] PyInstaller bundle (`dist/AudioTranscriber/AudioTranscriber.exe`)
      shows the same UI as `python app.py`

### Pre-merge contract (CLAUDE.md)
- `pytest` green (baseline ≥ 370, expect +6 new tests → ~376)
- `python -m ruff check .` clean
- Manual smoke checklist all ticked
- PyInstaller rebuild + visual smoke = identical to dev-run

## Out of scope

Explicitly NOT in this spec:

- **Cloud STT key validation** — adding `validate_key()` to each provider
  class. Deferred to a follow-up PR. `api_key_row` is designed with
  `on_validate=None` to accommodate this.
- **Search box in Settings** — over-engineering for 9 options.
- **Save/Apply buttons** — auto-save via existing callbacks is fine; no
  need to change persistence model.
- **Reset to defaults button** — out of scope.
- **Advanced/Basic mode toggle** — one new mechanic per PR.
- **Additional keyboard shortcuts beyond Esc** — `Ctrl+Tab` between tabs
  comes free from CTkTabview.
- **Drag-to-reorder sections** — out of scope.
- **Per-tab persistence** ("last opened tab" in config) — over-engineering
  for the diminishing return.
- **Tooltips** on every field — existing Russian helper text is sufficient.
- **Main window changes** — the "Извлечь задачи" / "История" / "Audio Cutter"
  buttons stay where they are.

## Open questions

None. All design decisions made during brainstorming on 2026-05-28.

## References

- Brainstorming session: 2026-05-28 (this spec is the direct output)
- Prior art for trace cleanup: PR #25 ([settings.py:130-152](../../../ui/dialogs/settings.py))
- CI constraint memory: `feedback_ui_app_import_breaks_linux_ci.md`
- No-hex-in-CTk constraint: `feedback_no_hex_in_ctk_styles.md`
- PyInstaller windowed-mode gotcha: `feedback_pyinstaller_windowed_stderr_none.md`
- MVP context: `project_mvp_3_clients_this_week.md`
