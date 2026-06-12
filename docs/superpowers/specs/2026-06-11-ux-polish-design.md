# UX polish (Variant 4) — design

**Date:** 2026-06-11
**Status:** approved by user (terminology direction, dedup scope, cost
approach and PR slicing all confirmed in the brainstorm session)
**Source:** 2026-06-10 improvement audit, Variant 4 — the last open
variant. Four independent UX items, shipped as four serial micro-PRs.

## Goals

1. One consistent Russian term for a recorded meeting across the UI.
2. Let a non-technical user turn task dedup off without editing JSON.
3. Settings must open instantly regardless of meetings-folder size.
4. The upfront LLM cost estimate must match the post-run actual cost.

Non-goals: dedup threshold UI (stays config-only), pluralization beyond
the meeting counters, any change to pricing of transcription providers
(this is OpenRouter extraction cost only), renaming files/modules/
functions that contain "meeting(s)" in English.

## Decisions (user-confirmed)

- **Terminology direction:** «Встречи» everywhere. Rationale: protocol.md
  already says «Протокол встречи», the Extract dialog says «Контекст
  встречи», the dedup comment says «обсуждалась на встрече», and the
  user's vault folder is «Транскриб встреч». «Митинг» formally means a
  street rally in Russian.
- **Dedup UI scope:** a single checkbox bound to `dedup_enabled`.
  `dedup_fuzzy_high` / `dedup_fuzzy_low` remain config-only expert knobs
  (the safe resolver `tasks.dedup.resolve_thresholds` already guards
  garbage values).
- **Cost approach:** honest per-model forecast + show the forecast next
  to the actual after a run («$0.012 (прогноз $0.015)»).
- **Slicing:** four micro-PRs, strictly serial via main, in the order
  below (PR-1/2/3 all touch `settings.py`/`settings_builder.py`;
  terminology first so later PRs write «встреч» strings natively).

---

## PR-1 — terminology «Встречи» + Russian pluralization

Pure user-facing string rename, zero logic. Verified touch points:

| Site | Now | Becomes |
|---|---|---|
| `ui/app/builder.py:357` main-window button | «Митинги» | «Встречи» |
| `ui/dialogs/meetings.py:167,186` title + header | «Митинги» | «Встречи» |
| `ui/dialogs/meetings.py:258` counter | «Митингов: N…» | pluralized counter (below) |
| `ui/dialogs/meetings.py:261` empty/search | «Нет митингов» | «Нет встреч» |
| `ui/dialogs/settings_builder.py:135` section card | «Митинги» | «Встречи» |
| `ui/dialogs/settings.py:301,308` stats label | «В этой папке: N митингов • X GB» | pluralized |
| `ui/dialogs/settings.py:314` dir-picker title | «Папка для хранения митингов» | «…встреч» |
| `ui/dialogs/migration.py:84,98,99,118,120,215,279` | «Перенос митингов» etc. | «Перенос встреч», «Найдено N встреч…», «Переношу встречу X / Y» |

Also update docstrings/comments that quote the old UI strings
(`meetings.py` module docstring, `settings.py:301` docstring). Module /
file / function names (`meetings.py`, `count_meetings`,
`meetings_migration.py`, `meetings_dir`) are NOT renamed.

**Pluralization helper** — new in `utils.py` (headless, unit-testable):

```python
def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Return the WORD FORM ONLY for n: встреча / встречи / встреч.
    Handles 11–14 and 111-style exceptions. Callers compose:
    f"{n} {plural_ru(n, 'встреча', 'встречи', 'встреч')}"."""
```

Exact resulting strings (to remove ambiguity):
- `meetings.py:258` → `f"Встреч: {n}{suffix}"` — label form, fixed
  genitive, no helper needed here;
- `settings.py:308` → `f"В этой папке: {n} {plural_ru(…)} • {size}"`;
- `migration.py:118/120` → `f"Найдено {n} {plural_ru(…)} в старой папке:"`
  / `f"В текущей папке {n} {plural_ru(…)}:"`.

Fixes the existing «1 митингов» bug class in the latter two.

**Tests:**
- Flip the two lock files in the same PR: `test_meetings_dialog_rename.py`
  (assertions become «Встречи»/«Встреч»; keep the file name — it locks
  the dialog naming, direction is irrelevant) and
  `test_settings_dialog_meetings_section.py`.
- New unit tests for `plural_ru` (1, 2, 5, 11, 14, 21, 111).
- New grep-guard in `test_meetings_dialog_rename.py`: no `[Мм]итинг`
  remains anywhere under `ui/` (source scan, Linux-CI safe).

Commit style: `refactor(ui): unify meeting terminology to «Встречи»`.

---

## PR-2 — async folder-stats in Settings

**Problem:** `settings.py:300 _refresh_meetings_stats` runs
`count_meetings` (cheap `listdir`) + `_folder_size_bytes` (full
`os.walk` + per-file `getsize`) synchronously on the Tk thread. It is
called during dialog build (`settings_builder.py:169`) and after folder
change/migration (`settings.py:361,369`). Since #93 the meetings folder
also holds `recordings/` with multi-GB WAVs → Settings visibly freezes
on open.

**Design:**
- `_refresh_meetings_stats()` becomes non-blocking:
  1. bump `self._stats_gen` (int generation counter, init 0);
  2. set label to «Подсчёт…»;
  3. spawn a daemon `threading.Thread` carrying `(gen, path)`.
- Worker computes `n = count_meetings(path)`, `size =
  _folder_size_bytes(path)` (both already swallow per-file `OSError`;
  no broad except needed — the #140 ratchet baseline for `settings.py`
  stays at 3), then posts `self.after(0, apply)` wrapped in
  `except tk.TclError: pass` — the dialog may be destroyed while the
  walk was running.
- `apply()` checks `gen == self._stats_gen` before touching the label —
  a stale walk result for a folder the user has already switched away
  from is dropped (race (а) from the design discussion).

**Tests:** window-sliced source-text checks per the existing
`test_settings_*` pattern (settings.py imports CTk — not importable on
Linux CI): thread is created in `_refresh_meetings_stats`, placeholder
text present, generation guard present, `tk.TclError` guard present.
The counting itself is already covered by `meetings_migration` tests.

Commit style: `fix(settings): compute folder stats off the Tk thread`.

---

## PR-3 — dedup checkbox in Settings

New mini-section «Дубли задач» on the «Интеграции» tab, placed after
the Trello section. Follows the settings_builder contract exactly
(free function, mutates dialog, captures refs on dialog):

- `build_dedup_section(dialog, parent)` in `settings_builder.py`:
  `section_card` + one `CTkCheckBox`
  «Проверять дубли перед отправкой (комментарий вместо новой карточки)»
  bound to a `BooleanVar` initialized from
  `config.get("dedup_enabled", True)`.
- Change handler writes `dialog._parent._config["dedup_enabled"] =
  bool(var.get())` and calls `save_config(dialog._parent._config)` —
  the same immediate-save pattern as the sibling sections
  (`build_linear_section:311`, `build_trello_section:398`).
- The consumer gate already exists and needs no change:
  `extract_tasks/__init__.py:725` reads
  `self._config.get("dedup_enabled", True)`.

**Tests:** source-slice checks (checkbox label text, config key,
save_config call inside the section); `config.example.json` already
carries `dedup_enabled: true` (locked by `test_dialog_dedup_ui.py:82`).
If `test_widget_tree_split` / `test_bundle_ui_only` enumerate builder
functions or scan sets, extend them in the same PR (same-PR rule from
the tree-split series).

Commit style: `feat(settings): dedup on/off checkbox (Интеграции tab)`.

---

## PR-4 — honest cost forecast + forecast-vs-actual

**Problem:** the upfront hint (`pricing.py:37 estimate_cost_hint`) uses
a flat `$3/1M × 1.3` while the only curated model (gemini-3.5-flash) is
$1.50/$9.00 — the forecast systematically overshoots ~2× vs the actual
shown by `format_real_cost` (which uses authoritative `usage.cost` or
per-model rates).

**Design — pricing.py (pure leaf, unit-tested on Linux CI):**
- New `estimate_cost(char_count: int, model: str) -> float` — always
  returns a number; the manual-flow (<50 chars → no forecast) decision
  stays in the callers, so `_last_cost_forecast` is `None` only when
  the hint never showed a forecast:
  - input tokens ≈ `char_count // 4` (unchanged heuristic);
  - rates from `_MODEL_PRICING_USD_PER_M[model]`; unknown model →
    fall back to the current flat default rate (keeps the hint useful
    for custom OpenRouter slugs);
  - output tokens ≈ `input_tokens × _EST_OUTPUT_RATIO` with
    `_EST_OUTPUT_RATIO = 0.12` — one named constant with a comment
    (calibration: a typical 1-hour meeting ≈ 12.5k input tokens →
    ~1.5k tokens of task-JSON output);
  - forecast = `in_tok × in_rate/1M + out_tok × out_rate/1M`.
- `estimate_cost_hint(char_count, model)` becomes a thin string wrapper
  over `estimate_cost` (welcome line for <50 chars unchanged). The ×1.3
  fudge dies — the output term replaces it honestly.

**Design — dialog wiring (`extract_tasks/__init__.py` + `builder.py`):**
- `_update_cost_hint` passes `self._model_var.get().strip()` and stores
  `self._last_cost_forecast: float | None` (None in the manual flow).
- `builder.py` adds `dialog._model_var.trace_add("write", …)` →
  re-run `_update_cost_hint` when the model changes (initial call at
  `builder.py:206` already exists).
- `_on_extract_success` (`__init__.py:772`): when
  `self._last_cost_forecast` is not None, append
  ` (прогноз ${forecast:.4f})` — hidden entirely in the manual flow,
  matching today's behavior where no forecast was shown.

**Tests:** unit tests for `estimate_cost` (known model, unknown model
fallback, zero/short input) and the hint string; source-slice for the
trace wiring + forecast tail in `_on_extract_success`.

Commit style: `feat(extract): per-model cost forecast + forecast-vs-actual`.

---

## Cross-cutting

- **Gate per PR:** full `pytest` + `python -m ruff check .` locally;
  CI (ubuntu + windows legs) green before merge; user runs a ~60-second
  GUI smoke per PR (Settings open speed for PR-2 is the one to feel).
- **Order:** PR-1 → PR-2 → PR-3 → PR-4, each branched off main after
  the previous merge (PR-1/2/3 overlap in settings files; no stacking).
- **Risk notes:** PR-1 is diff-noisy but logic-free; PR-2 introduces a
  thread into a dialog that previously had none on this path — the
  generation counter + TclError guard are the entire risk surface;
  PR-3/PR-4 are additive.
- **Error handling:** no new broad `except` anywhere; the #140 ratchet
  baseline is untouched by all four PRs.
