# Widget-tree split: settings.py + extract_tasks god-objects → builder modules

**Date:** 2026-06-10
**Status:** Approved (approach A; parked 2026-06-10 during the improvement audit, un-parked same day as Вариант 2)
**Baseline:** main @ `687a9ba` (post #133)

## Problem

Two dialog god-objects remain after the F4-PR-2 series split `ui/app/`:

| File | LOC @ 687a9ba | Widget-tree share |
|---|---|---|
| `ui/dialogs/settings.py` | 1053 | ~440 LOC across 12 `_build_*_section` methods + `_section_card` |
| `ui/dialogs/extract_tasks/__init__.py` | 2052 | ~430 LOC: `_build_ui` (229–479), `_build_form` (1245–1353), `_build_speaker_rows` (560–609), `_rebuild_context_participants` (480–502) |

Every feature touching these dialogs (e.g. #132, #133) pays a navigation tax and
review risk. The `ui/app/builder.py` refactor (372 LOC, PRs #12/#14–#18) proved
the cure: widget-tree construction as free functions in a sibling module.

## Approach A (approved)

Literal cut-paste extraction, `self` → `dialog`, following the
`ui/app/builder.py` contract verbatim:

> The function mutates `dialog` (sets `dialog._lang_menu`, …); it does NOT
> construct `dialog` itself. Called by `__init__` after state fields exist.

**New modules:**

1. `ui/dialogs/settings_builder.py` (~490 LOC) — `section_card(dialog, parent,
   title, row)` + 12 `build_*_section(dialog, parent)` free functions + the
   settings-local `_CURATED_MODELS` dict (its only consumer is
   `build_openrouter_section`). `settings.py` shrinks to ~615 LOC and stays a
   class: `__init__` skeleton (tabs/scrolls/banner — NOT moved), traces,
   banner state machine, meetings/gdrive/send-log handlers + workers,
   `destroy`.
2. `ui/dialogs/extract_tasks/builder.py` (~470 LOC) — `build_ui(dialog)`,
   `build_form(dialog)`, `build_speaker_rows(dialog)`,
   `rebuild_context_participants(dialog)`. `__init__.py` shrinks to ~1620 LOC.

**Already done (was a PR-2 subtask in the parked design, landed since):**
`_CURATED_MODELS` / `_RECENT_MODELS_KEY` and the backend dicts already live in
`ui/dialogs/extract_tasks/constants.py` — no constants migration needed.

## Rules (mined from past incidents)

1. **No import cycles:** builder modules import only `theme`, `ui.widgets`,
   `ui.app.constants`, `providers` (registry), `ui.dialogs.settings_helpers`,
   `utils`, and (extract) `.constants` / `.task_row` — never their own dialog
   module (`ui.dialogs.settings`, `ui.dialogs.extract_tasks.__init__`) and
   never module-level `ui.app`.
2. **Lazy imports stay lazy and in place:** `from ui.app import
   APPEARANCE_MODES` inside `build_appearance_section`; `from directory.store
   import DirectoryError` inside `build_ui`; provider/client lazy imports
   inside the validate/persist closures.
3. **Statement order is sacred:** in `SettingsDialog.__init__` the trace
   registrations stay AFTER the 12 build calls (lesson of PR #25); in
   `ExtractTasksDialog.__init__` the keyboard bindings stay after
   `build_ui(self)` (they reference widgets it creates).
4. **Captured refs keep their exact names** on the dialog instance —
   settings: `_lang_menu`, `_cloud_api_key_entry`, `_meetings_path_var`,
   `_meetings_entry`, `_meetings_stats_label`, `_terms_summary`,
   `_openrouter_status`, `_linear_status`, `_glide_status`, `_trello_status`,
   `_gdrive_status_label`, `_gdrive_signin_btn`, `_gdrive_signout_btn`,
   `_gdrive_backup_btn`, `_gdrive_backup_status`, `_send_log_btn`,
   `_send_log_status`; extract: `_model_var`, `_model_combo`, `_backend_var`,
   `_backend_menu`, `_container_label`, `_team_var`, `_team_menu`,
   `_btn_refresh`, `_btn_extract`, `_context_project_menu`,
   `_context_participants_frame`, `_speaker_rows_frame`, `_docs_count_label`,
   `_status_label`, `_task_list`, `_btn_add`, `_btn_select_all`,
   `_btn_select_none`, `_btn_delete`, `_form_panel`, `_saved_label`,
   `_btn_send`, `_btn_retry`, `_context_person_vars`, `_speaker_row_vars`,
   form vars/widgets (`_var_title`, `_entry_title`, …). Non-build methods
   (`_jump_to_stt`, `_update_cost_hint`, `_set_editor_buttons_state`, …) read
   them by these names.

## Out of scope (WS-4 critic CUTs = law)

- Recent-model relocation, `dedup_controller`, `backup_worker`, `validate_*`
  glue: NOT touched — handlers and workers stay on the dialog classes.
- Workers (`_run_extraction`, `_run_dedup`, gdrive/send-log workers): not in
  this workstream (cli.core convergence belongs to the processing-queue
  track).
- The ~60-LOC `SettingsDialog.__init__` skeleton (tabs/scrolls/banner): stays.
- No behavior change anywhere. No new widgets, no renames of user-visible
  anything.

## Verification (no GUI test rig exists)

- Full `pytest` (baseline 766 passed / 2 skipped) + `ruff check .` green at
  every commit.
- Source-text structural tests updated **in the same PR** that moves their
  target (verified breakage list, exact edits in the plan): PR-1 breaks
  `test_settings_dialog_naming`, `test_settings_dialog_no_inner_h1`,
  `test_settings_dialog_uses_api_key_row`, `test_settings_trello_section`,
  `test_settings_dialog_meetings_section`, `test_settings_cloud_validate`,
  `test_settings_gdrive`, `test_settings_dialog_banner`,
  `test_settings_diagnostics`, `test_bundle_ui_only`; PR-2 breaks
  `test_extract_dialog_close_persist` (slice boundary `def _build_form(`);
  PR-3 re-points `test_dialog_dedup_ui` only if its markers move (handlers
  stay — expected no-op, verify).
- **Absence-guards extended to the new files**: `test_bundle_ui_only.py`'s
  forbidden-string scans (incl. the normalize-guard) must also scan
  `settings_builder.py` and `extract_tasks/builder.py` so banned patterns
  can't resurrect there unnoticed.
- **New guard test (regression lock, added last in each PR):** the dialog
  modules contain no `def _build_` anymore; builder modules define no
  `class`; builder modules contain no forbidden imports (rule 1).
- User GUI smoke ≤60 s per PR (run the app from the main repo, not a
  worktree): PR-1 — open Настройки, walk 3 tabs, banner click-jump focuses
  the key entry, «Проверить» still works; PR-2 — Extract dialog: task form
  edits + speaker rows render; PR-3 — Extract dialog opens fully, extraction
  round-trip.

## Delivery

Three serial PRs (each lands on main before the next branches — stacked PRs
forbidden):

1. **PR-1** `refactor/settings-builder` — settings_builder.py extraction.
   Mechanically simplest: all 12 sections already isolated behind the
   `(self, parent)` signature.
2. **PR-2** `refactor/extract-builder-form` — extract `build_form` +
   `build_speaker_rows` + `rebuild_context_participants` into
   `extract_tasks/builder.py`.
3. **PR-3** `refactor/extract-builder-ui` — move `_build_ui` (251 LOC) into
   the same builder module; final guard flips on.

Accepted risk: `git blame` on moved lines resets (`git log --follow`
remains). Branch names use `refactor/`; commits `refactor(settings):` /
`refactor(extract):`.
