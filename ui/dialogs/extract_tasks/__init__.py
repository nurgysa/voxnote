"""Extract Tasks dialog — Phase 6.1 minimal version.

Layout (~640×520):
    [Модель ▾] [Команда ▾] [↻]   [Извлечь]    ← header row
    ─────────────────────────────────────────
    Стоимость ≈ $0.09                          ← cost hint (above textbox)
    ✓ Извлечено 12 задач (3 поля скорректированы)
    ┌─────────────────────────────────────────┐
    │ {                                        │
    │   "tasks": [...]                        │   ← raw JSON, read-only
    │ }                                        │
    └─────────────────────────────────────────┘
    Сохранено: history/.../tasks_raw.json    [Закрыть]

Phase 6.2 will replace the JSON textbox with a master-detail editor;
this dialog deliberately keeps the JSON view minimal so the swap is
isolated.
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from collections import deque
from datetime import datetime, timedelta
from tkinter import messagebox

import customtkinter as ctk

from theme import (
    BG,
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    GREEN,
    INPUT_BG,
    RED,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.widgets import label, primary_button, tonal_button
from utils import save_config

from .constants import (
    _CACHE_KEY_BY_BACKEND,
    _CONTAINER_ACCUSATIVE_BY_BACKEND,
    _CONTAINER_CACHE_TTL,
    _CONTAINER_LABEL_BY_BACKEND,
    _COST_PER_1M_INPUT_TOKENS_USD,
    _CURATED_MODELS,
    _DISPLAY_TO_NAME,
    _EMPTY_CONTAINER_LABEL_BY_BACKEND,
    _MODEL_PRICING_USD_PER_M,
    _NAME_TO_DISPLAY,
    _RECENT_MODELS_KEY,
    _RECENT_MODELS_LIMIT,
    _REQUIRED_KEYS_BY_BACKEND,
    _TEAMS_CACHE_KEY,
)
from .task_row import _TaskRow

# CTkComboBox sentinel for the «Кто говорит» speaker rows — the dropdown's
# "no person" option AND the guard value in _person_by_name. One definition
# keeps those two uses from drifting apart on a future wording change.
_NO_SELECTION = "— не выбрано —"


def _backend_is_configured(name: str, config: dict) -> bool:
    """True if every credential the backend needs is present + non-empty.

    Trello needs two (key + token); Linear/Glide need one. Replaces the old
    `"linear_api_key" if linear else "glide_api_key"` binary that silently
    picked the Glide key for any non-Linear backend.
    """
    keys = _REQUIRED_KEYS_BY_BACKEND.get(name, ())
    return bool(keys) and all((config.get(k) or "").strip() for k in keys)


class ExtractTasksDialog(ctk.CTkToplevel):
    """Phase-6.2 master-detail editor scaffold (interactivity in Tasks 3–4)."""

    def __init__(
        self,
        parent,
        *,
        transcript: str,
        history_folder: str,
        transcript_lang: str | None,
        config: dict,
    ):
        super().__init__(parent)
        self._parent = parent
        self._transcript = transcript
        self._history_folder = history_folder
        self._transcript_lang = transcript_lang
        self._config = config

        # Phase A UI part 2: directory for meeting-context grounding. The store
        # is constructed here but loaded in _build_ui (after the window geometry
        # is set), and any corrupt-file warning is deferred via after() — so the
        # modal never stacks on a half-built, unrealized window. A load failure
        # degrades to an empty directory; it never crashes the dialog.
        from directory.store import DirectoryStore
        self._dir_store = DirectoryStore()
        self._dir_load_error: str | None = None
        self._context_project_var = ctk.StringVar(value="— нет —")
        self._context_person_vars: dict[str, ctk.BooleanVar] = {}
        self._speaker_row_vars: dict[str, ctk.StringVar] = {}
        self._speaker_friendly: dict[str, str] = {}

        # Worker-thread plumbing: cancel_event flips on close;
        # active_client is the in-flight client we close to interrupt sockets.
        self._cancel_event = threading.Event()
        self._active_clients: list = []   # OpenRouter + backend clients in flight
        self._containers: list = []       # list[Container] from backend.bootstrap()

        # Phase 6.4.1: backend selection. Initial value picks the first
        # enabled backend (per Settings checkboxes). If the loaded
        # tasks.json has a backend recorded, prefer that to keep the
        # editor consistent with what's already on disk.
        self._enabled_backends: list[str] = self._compute_enabled_backends()

        # Editor state. _tasks is the canonical in-memory list; right form
        # binds to _tasks[_selected_index]. _meta carries extract context for
        # save_tasks (extracted_at, model, team_id, team_name, transcript_lang).
        self._tasks: list = []      # list[Task]
        self._task_rows: list = []  # list[_TaskRow] — populated by _render_task_list
        self._selected_index: int | None = None
        self._meta: dict = {}       # populated post-extract or post-load
        # Undo stack (5 deep) of deepcopy(self._tasks) snapshots before destructive ops.
        self._undo_stack: deque = deque(maxlen=5)

        # Task 6 (MVP v5): opt-in protocol-generation pass. Default ON so
        # first-time users see protocol.md without opting in. _run_extraction
        # checks this flag AFTER save_tasks_raw and reuses the same OpenRouter
        # client + model the user picked for task extraction.
        self.generate_protocol = ctk.BooleanVar(value=True)

        # If tasks.json exists in the history folder (e.g., user re-opened the
        # dialog after a half-finished edit), load it instead of waiting for a
        # fresh extract.
        self._try_load_existing_tasks()

        self.title("Извлечение задач")
        # Position+size to roughly match the parent window so the dialog
        # feels related to it without going full-borderless (which traps
        # the user — verified 2026-05-28 when the maintainer's own session
        # had to be terminated via Stop-Process because overrideredirect
        # had stripped both the X button AND the Task Manager Apps-view
        # entry). Keep the normal title bar — that's the user's exit.
        parent.update_idletasks()
        # Match parent's geometry but reserve vertical room for the
        # dialog's own title bar — otherwise dialog.height = parent.height
        # but the actual window (incl. our title bar) overflows below
        # the taskbar, hiding the footer row (Send / Retry / Close).
        # 60 px buffer = ~30 title bar + ~30 safety margin if parent
        # somehow used full screen height instead of work-area height.
        # User feedback 2026-05-28: PR #74's no-cap version hid the
        # footer; this re-introduces a small vertical breathing room.
        w = max(960, parent.winfo_width())
        h = max(680, parent.winfo_height() - 60)
        x = parent.winfo_rootx()
        y = parent.winfo_rooty()
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(960, 680)
        self.configure(fg_color=BG)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.grab_set()

        self._build_ui()

        # ── Keyboard shortcuts (Phase 6.5 C) ──
        # tkinter case-quirk: <Control-z> ловит lat-raskladka без CapsLock,
        # <Control-Z> нужен с CapsLock-ON или Shift'ом — биндим обе формы.
        self.bind("<Control-z>", self._undo)
        self.bind("<Control-Z>", self._undo)
        # Esc — закрыть диалог (через _on_close → cancel_event + grab_release)
        self.bind("<Escape>", lambda _e: self._on_close())
        # F5 — обновить teams/boards (мнемоника как в Files Explorer)
        self.bind("<F5>", lambda _e: self._refresh_containers())
        # Ctrl+N — + Добавить новую пустую задачу
        self.bind("<Control-n>", lambda _e: self._on_add_task())
        self.bind("<Control-N>", lambda _e: self._on_add_task())
        # Ctrl+Shift+E — Извлечь из транскрипта (если кнопка активна)
        self.bind("<Control-Shift-E>", self._kbd_extract)
        self.bind("<Control-Shift-e>", self._kbd_extract)
        # Ctrl+Shift+S — Отправить выбранные (Send mnemonic)
        self.bind("<Control-Shift-S>", self._kbd_send)
        self.bind("<Control-Shift-s>", self._kbd_send)

        # If we loaded existing tasks above, render them now that widgets exist.
        if self._tasks:
            self._render_task_list()
            self._set_selection(0)

        self._load_containers_async()

        # Ctrl+Enter в Söyle textbox → autofill (widget-scoped, чтобы
        # обычный Enter мог делать перенос строки, а Ctrl+Enter — submit).
        self._textbox_autofill_hint.bind(
            "<Control-Return>", lambda _e: (self._on_autofill_clicked(), "break")[1],
        )

    def _compute_enabled_backends(self) -> list[str]:
        """Read Settings checkboxes (Phase 6.4) → list of enabled names.

        Order is significant: first enabled is the default selection.
        Falls back to ["linear"] if both flags are missing/false (back-
        compat with pre-6.4 configs that have no flags written yet)."""
        enabled = []
        if bool(self._config.get("linear_enabled", True)):
            enabled.append("linear")
        if bool(self._config.get("glide_enabled", True)):
            enabled.append("glide")
        if bool(self._config.get("trello_enabled", False)):
            enabled.append("trello")
        return enabled or ["linear"]

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)   # editor row stretches

        # --- Header row: model + backend + container + refresh + extract ---
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=16, pady=(14, 6), sticky="ew")
        header.grid_columnconfigure(1, weight=1)   # model
        header.grid_columnconfigure(5, weight=1)   # container

        label(header, "Модель").grid(row=0, column=0, padx=(0, 6), sticky="w")
        default_model = self._config.get(
            "tasks_default_model", _CURATED_MODELS[0],
        )
        recent = self._config.get(_RECENT_MODELS_KEY, []) or []
        all_models = list(_CURATED_MODELS)
        for slug in recent:
            if slug not in all_models:
                all_models.append(slug)
        self._model_var = ctk.StringVar(value=default_model)
        # CTkComboBox lets the user type custom slugs that aren't in the list.
        self._model_combo = ctk.CTkComboBox(
            header, variable=self._model_var, values=all_models,
            width=240, height=32,
            font=ctk.CTkFont(family=FONT, size=12),
            border_color=BORDER, button_color=BORDER,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        )
        self._model_combo.grid(row=0, column=1, padx=(0, 12), sticky="ew")

        # Phase 6.4.1: backend selection. Values come from Settings flags;
        # changing it triggers re-fetch of containers.
        label(header, "Backend").grid(row=0, column=2, padx=(0, 6), sticky="w")
        backend_display = [_NAME_TO_DISPLAY[n] for n in self._enabled_backends]
        self._backend_var = ctk.StringVar(
            value=backend_display[0] if backend_display else "Linear",
        )
        self._backend_menu = ctk.CTkComboBox(
            header, variable=self._backend_var, values=backend_display or ["Linear"],
            width=110, height=32, state="readonly",
            font=ctk.CTkFont(family=FONT, size=12),
            border_color=BORDER, button_color=BORDER,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            command=self._on_backend_changed,
        )
        self._backend_menu.grid(row=0, column=3, padx=(0, 12), sticky="w")

        # Container dropdown — label changes per backend (Команда / Доска).
        self._container_label = label(
            header, _CONTAINER_LABEL_BY_BACKEND.get(self._current_backend_name(), "Команда"),
        )
        self._container_label.grid(row=0, column=4, padx=(0, 6), sticky="w")
        self._team_var = ctk.StringVar(value="(загрузка...)")
        self._team_menu = ctk.CTkComboBox(
            header, variable=self._team_var, values=["(загрузка...)"],
            width=180, height=32, state="readonly",
            font=ctk.CTkFont(family=FONT, size=12),
            border_color=BORDER, button_color=BORDER,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        )
        self._team_menu.grid(row=0, column=5, padx=(0, 4), sticky="ew")

        self._btn_refresh = tonal_button(
            header, text="↻", command=self._refresh_containers, width=36,
        )
        self._btn_refresh.grid(row=0, column=6, padx=(0, 8))

        self._btn_extract = primary_button(
            header, text="Извлечь", command=self._on_extract, width=120,
        )
        self._btn_extract.grid(row=0, column=7)

        # Task 6 (MVP v5): protocol-generation opt-in checkbox.
        # Spans the full header width so the long Russian label has room.
        # State var `self.generate_protocol` was created in __init__ (kept
        # together with other state); only the widget binding lives here.
        ctk.CTkCheckBox(
            header,
            text="Также сгенерировать протокол встречи (protocol.md)",
            variable=self.generate_protocol,
            font=ctk.CTkFont(family=FONT, size=12),
            text_color=TEXT_SECONDARY,
            checkbox_height=18, checkbox_width=18,
        ).grid(row=1, column=0, columnspan=8, padx=0, pady=(8, 0), sticky="w")

        # Phase A UI part 2: «Контекст встречи» — project + participants feed
        # render_meeting_context() → context= for both protocol and tasks.
        # Nested in `header` (row=2) so no self-level row renumbering is needed.
        ctx_frame = ctk.CTkFrame(header, fg_color="transparent")
        ctx_frame.grid(row=2, column=0, columnspan=8, padx=0, pady=(8, 0), sticky="ew")
        ctx_frame.grid_columnconfigure(1, weight=1)

        label(ctx_frame, "Проект").grid(row=0, column=0, padx=(0, 6), sticky="w")
        # Load now — window geometry is already set; the warning below is
        # deferred via after() so it never stacks on a half-built window.
        from directory.store import DirectoryError
        try:
            self._dir_store.load()
        except DirectoryError as exc:
            self._dir_load_error = str(exc)
        self._dir_projects = self._dir_store.projects()
        project_labels = ["— нет —"] + [p.name for p in self._dir_projects]
        self._context_project_menu = ctk.CTkComboBox(
            ctx_frame, variable=self._context_project_var, values=project_labels,
            width=240, height=30, state="readonly",
            font=ctk.CTkFont(family=FONT, size=12),
            border_color=BORDER, button_color=BORDER,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            command=self._on_context_project_changed,
        )
        self._context_project_menu.grid(row=0, column=1, padx=(0, 12), sticky="ew")

        label(ctx_frame, "Участники").grid(
            row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="nw",
        )
        self._context_participants_frame = ctk.CTkScrollableFrame(
            ctx_frame, fg_color=INPUT_BG, height=90, corner_radius=8,
        )
        self._context_participants_frame.grid(
            row=1, column=1, padx=0, pady=(6, 0), sticky="ew",
        )

        label(ctx_frame, "Кто говорит").grid(
            row=2, column=0, padx=(0, 6), pady=(6, 0), sticky="nw",
        )
        self._speaker_rows_frame = ctk.CTkFrame(ctx_frame, fg_color="transparent")
        self._speaker_rows_frame.grid(
            row=2, column=1, padx=0, pady=(6, 0), sticky="ew",
        )

        self._rebuild_context_participants(set())
        self._build_speaker_rows()
        self._restore_context_selection()
        if self._dir_load_error:
            # Defer to the event loop so the modal stacks on the fully-built,
            # realized window rather than mid-construction.
            self.after(0, lambda: messagebox.showwarning(
                "Справочник",
                "Не удалось прочитать справочник — контекст недоступен."
                f"\n\n{self._dir_load_error}",
                parent=self,
            ))

        # --- Status / cost hint row ---
        self._status_label = label(self, "", anchor="w")
        self._status_label.grid(row=1, column=0, padx=18, pady=(2, 4), sticky="ew")
        self._update_cost_hint()

        # --- Editor: master-detail layout ---
        editor = ctk.CTkFrame(self, fg_color="transparent")
        editor.grid(row=2, column=0, padx=16, pady=(2, 4), sticky="nsew")
        editor.grid_columnconfigure(0, weight=1, minsize=180)
        editor.grid_columnconfigure(1, weight=2, minsize=360)
        editor.grid_rowconfigure(0, weight=1)

        # Left: scrollable list of task rows + bottom action bar.
        left_panel = ctk.CTkFrame(editor, fg_color=SURFACE, corner_radius=10)
        left_panel.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        left_panel.grid_rowconfigure(0, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)

        self._task_list = ctk.CTkScrollableFrame(
            left_panel, fg_color="transparent", corner_radius=0,
        )
        self._task_list.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")
        self._task_list.grid_columnconfigure(0, weight=1)

        # Action bar inside left panel: Add / SelectAll / SelectNone / Delete
        list_actions = ctk.CTkFrame(left_panel, fg_color="transparent")
        list_actions.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="ew")
        list_actions.grid_columnconfigure(0, weight=1)
        list_actions.grid_columnconfigure(1, weight=1)
        list_actions.grid_columnconfigure(2, weight=1)
        list_actions.grid_columnconfigure(3, weight=1)
        self._btn_add = tonal_button(
            list_actions, text="+ Добавить", command=self._on_add_task, width=110,
        )
        self._btn_add.grid(row=0, column=0, padx=2, sticky="ew")
        self._btn_select_all = tonal_button(
            list_actions, text="✓ Все", command=self._on_select_all, width=70,
        )
        self._btn_select_all.grid(row=0, column=1, padx=2, sticky="ew")
        self._btn_select_none = tonal_button(
            list_actions, text="✗ Снять", command=self._on_select_none, width=80,
        )
        self._btn_select_none.grid(row=0, column=2, padx=2, sticky="ew")
        self._btn_delete = tonal_button(
            list_actions, text="🗑 Удалить", command=self._on_delete_task, width=100,
        )
        self._btn_delete.grid(row=0, column=3, padx=2, sticky="ew")

        # Right: form for editing selected task. The form has ~7 fields
        # stacked vertically (autofill textbox + title + priority +
        # assignee + labels + date + description) that overflow at
        # smaller dialog heights. Wrap in a CTkScrollableFrame so the
        # footer (Send/Retry/Close) always stays visible regardless of
        # form height.
        form_outer = ctk.CTkFrame(editor, fg_color=SURFACE, corner_radius=10)
        form_outer.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        form_outer.grid_columnconfigure(0, weight=1)
        form_outer.grid_rowconfigure(0, weight=1)
        self._form_panel = ctk.CTkScrollableFrame(
            form_outer, fg_color="transparent", corner_radius=0,
        )
        self._form_panel.grid(row=0, column=0, sticky="nsew")
        self._form_panel.grid_columnconfigure(0, weight=1)
        self._build_form()

        # Disable buttons that need a selection until something is selected.
        self._set_editor_buttons_state(empty=True)

        # --- Footer: saved-path + Send / Retry / Close ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(2, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self._saved_label = label(footer, "", anchor="w")
        self._saved_label.grid(row=0, column=0, sticky="ew")

        self._btn_send = primary_button(
            footer, text="Отправить выбранные (0)",
            command=self._on_send_clicked, width=220, state="disabled",
        )
        self._btn_send.grid(row=0, column=1, padx=(8, 4), sticky="e")

        self._btn_retry = tonal_button(
            footer, text="Повторить упавшие",
            command=self._on_retry_clicked, width=170, state="disabled",
        )
        self._btn_retry.grid(row=0, column=2, padx=(0, 4), sticky="e")

        tonal_button(
            footer, text="Закрыть", command=self._on_close, width=110,
        ).grid(row=0, column=3, sticky="e")

    def _rebuild_context_participants(self, checked_ids: set[str]) -> None:
        """Render a checkbox per directory person, ticking checked_ids."""
        for w in self._context_participants_frame.winfo_children():
            w.destroy()
        self._context_person_vars = {}
        people = self._dir_store.people()
        if not people:
            label(
                self._context_participants_frame,
                "(справочник пуст — добавьте людей в «Справочники»)",
            ).grid(row=0, column=0, padx=4, pady=2, sticky="w")
            return
        for i, p in enumerate(people):
            var = ctk.BooleanVar(value=p.id in checked_ids)
            self._context_person_vars[p.id] = var
            text = p.full_name + (f" — {p.role}" if p.role else "")
            ctk.CTkCheckBox(
                self._context_participants_frame, text=text, variable=var,
                fg_color=BLUE, hover_color=BLUE_DIM, text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(family=FONT, size=12),
                checkbox_height=16, checkbox_width=16,
            ).grid(row=i, column=0, padx=4, pady=1, sticky="w")

    def _on_context_project_changed(self, _choice=None) -> None:
        """Project change → pre-check that project's members."""
        project = self._selected_context_project()
        pid = project.id if project else None
        from directory.context import default_participants
        defaults = {p.id for p in default_participants(self._dir_store.people(), pid)}
        self._rebuild_context_participants(defaults)

    def _selected_context_project(self):
        """Return the Project chosen in the dropdown, or None for «— нет —».

        Matches by name (the dropdown shows names). Assumes project names are
        unique; on a duplicate name the first match wins, so the wrong id could
        flow into save_speakers. Acceptable for now — the «Справочники» CRUD is
        where uniqueness would be enforced.
        """
        chosen = self._context_project_var.get()
        for p in self._dir_projects:
            if p.name == chosen:
                return p
        return None

    def _selected_context_people(self) -> list:
        """Resolve the ticked participant ids to Person objects (skip stale)."""
        out = []
        for pid, var in self._context_person_vars.items():
            if var.get():
                person = self._dir_store.get_person(pid)
                if person is not None:
                    out.append(person)
        return out

    def _build_speaker_rows(self) -> None:
        """Render one «Спикер N → person» dropdown per diarized speaker label.

        Reads <meeting>/segments.json and maps raw labels to the same
        friendly «Спикер N» the transcript shows (via _build_speaker_map).
        No segments / no diarization / empty directory → a muted hint and
        no rows (pure manual mapping is impossible; the dialog still works).
        """
        # _build_speaker_map is transcript_format-internal but stable: it is the
        # single source of the «Спикер N» labels the transcript shows and is
        # already covered by test_transcript_format. Reuse keeps the panel's
        # labels identical to the rendered transcript.
        from transcript_format import _build_speaker_map
        from utils import load_segments

        for w in self._speaker_rows_frame.winfo_children():
            w.destroy()
        self._speaker_row_vars = {}
        self._speaker_friendly = {}

        label_map = _build_speaker_map(load_segments(self._history_folder))
        people = self._dir_store.people()
        if not label_map or not people:
            hint = (
                "(нет данных о спикерах)"
                if not label_map
                else "(справочник пуст — добавьте людей в «Справочники»)"
            )
            label(self._speaker_rows_frame, hint).grid(
                row=0, column=0, padx=4, pady=2, sticky="w",
            )
            return

        names = [_NO_SELECTION] + [p.full_name for p in people]
        for i, (raw, friendly) in enumerate(label_map.items()):
            self._speaker_friendly[raw] = friendly
            var = ctk.StringVar(value=_NO_SELECTION)
            self._speaker_row_vars[raw] = var
            label(self._speaker_rows_frame, friendly).grid(
                row=i, column=0, padx=(4, 8), pady=2, sticky="w",
            )
            ctk.CTkComboBox(
                self._speaker_rows_frame, variable=var, values=names,
                width=220, height=28, state="readonly",
                font=ctk.CTkFont(family=FONT, size=12),
                border_color=BORDER, button_color=BORDER,
                fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
                command=lambda _v, r=raw: self._on_speaker_bound(r),
            ).grid(row=i, column=1, padx=0, pady=2, sticky="w")

    def _person_by_name(self, full_name: str):
        """First directory person whose full_name matches, else None.

        «— не выбрано —» / unknown → None. Duplicate names resolve to the
        first match (same caveat as _selected_context_project).
        """
        if not full_name or full_name == _NO_SELECTION:
            return None
        for p in self._dir_store.people():
            if p.full_name == full_name:
                return p
        return None

    def _on_speaker_bound(self, raw_label: str) -> None:
        """Auto-tick the chosen person as a participant (D-2 auto-sync)."""
        person = self._person_by_name(self._speaker_row_vars[raw_label].get())
        if person is not None and person.id in self._context_person_vars:
            self._context_person_vars[person.id].set(True)

    def _selected_speaker_maps(self) -> tuple[dict, dict]:
        """Resolve speaker rows → (speaker_map, name_by_label).

        speaker_map:  raw label  → person_id   (persisted to speakers.json)
        name_by_label: «Спикер N» → ФИО         (rewrites the LLM transcript)

        MUST be called on the main thread — Tk vars are not thread-safe; the
        result is passed into the _run_extraction worker.
        """
        speaker_map: dict[str, str] = {}
        name_by_label: dict[str, str] = {}
        for raw, var in self._speaker_row_vars.items():
            person = self._person_by_name(var.get())
            if person is not None:
                speaker_map[raw] = person.id
                name_by_label[self._speaker_friendly[raw]] = person.full_name
        return speaker_map, name_by_label

    def _restore_context_selection(self) -> None:
        """Re-apply a previously saved project + participants from speakers.json."""
        from utils import load_speakers
        data = load_speakers(self._history_folder)
        if not data:
            return
        project = self._dir_store.get_project(data.get("project_id") or "")
        if project is not None:
            self._context_project_var.set(project.name)
        checked = set(data.get("participants") or [])
        if checked:
            self._rebuild_context_participants(checked)
        # PR-2: restore per-speaker bindings (raw label → person_id). Setting
        # the StringVar does not fire the combobox command, so no auto-sync
        # re-runs here — participants were already restored above.
        for raw, person_id in (data.get("speakers") or {}).items():
            person = self._dir_store.get_person(person_id)
            if person is not None and raw in self._speaker_row_vars:
                self._speaker_row_vars[raw].set(person.full_name)

    def _update_cost_hint(self) -> None:
        """Initial status: cost-of-extract heuristic if a transcript is
        present, otherwise a welcome that points to the manual paths.

        Phase 6.5 D — adaptive welcome. When the dialog is opened with
        no transcript (e.g., user wants to add tasks by hand or via
        Söyle dictation), the old «Стоимость ≈ $0.00 (≈ 1 токенов)»
        line was both wrong and confusing. Now we show a one-liner
        that mirrors the empty-state placeholder in the left pane.
        """
        chars = len(self._transcript or "")
        if chars < 50:
            # No transcript → manual-only flow; skip the cost line.
            self._status_label.configure(
                text="Готов к работе. Извлеките из транскрипта или добавьте задачу вручную.",
                text_color=TEXT_SECONDARY,
            )
            return
        approx_tokens = max(chars // 4, 1)
        cost = approx_tokens / 1_000_000 * _COST_PER_1M_INPUT_TOKENS_USD * 1.3
        self._status_label.configure(
            text=f"Стоимость ≈ ${cost:.2f} (≈ {approx_tokens:,} токенов)",
            text_color=TEXT_SECONDARY,
        )

    # ── Backend / container bootstrap (cached 24h per backend) ──────

    def _current_backend_name(self) -> str:
        """Map dropdown display value → internal backend name.

        Returns "linear" / "glide" / "trello". Defaults to first enabled if
        the UI hasn't been built yet (called from _build_ui pre-init)."""
        var = getattr(self, "_backend_var", None)
        display = var.get() if var is not None else None
        name = _DISPLAY_TO_NAME.get(display)
        if name:
            return name
        # Pre-build / unknown — first enabled.
        return self._enabled_backends[0] if self._enabled_backends else "linear"

    def _backend_cache_key(self) -> str:
        return _CACHE_KEY_BY_BACKEND.get(self._current_backend_name(), _TEAMS_CACHE_KEY)

    def _on_backend_changed(self, _value: str = "") -> None:
        """User picked a different backend in the dropdown.

        Swap the container-label (Команда / Доска) and re-fetch the
        container list for the new backend. Clear the editor — task list
        from a Linear extract isn't meaningful in a Glide context."""
        self._container_label.configure(
            text=_CONTAINER_LABEL_BY_BACKEND.get(self._current_backend_name(), "Команда"),
        )
        self._team_var.set("(загрузка...)")
        self._team_menu.configure(values=["(загрузка...)"])
        self._load_containers_async()

    def _load_containers_async(self) -> None:
        """Use cache if fresh; else fetch from the selected backend in a worker."""
        cache_key = self._backend_cache_key()
        cache = self._config.get(cache_key) or {}
        fetched_at = cache.get("fetched_at")
        if fetched_at:
            try:
                age = datetime.now() - datetime.fromisoformat(fetched_at)
            except ValueError:
                age = _CONTAINER_CACHE_TTL + timedelta(seconds=1)
            if age <= _CONTAINER_CACHE_TTL and cache.get("data"):
                # Cache stores plain dicts; rebuild Container objects.
                from tasks.backends.base import Container
                self._containers = [
                    Container(id=d["id"], name=d.get("name", "?"), key=d.get("key"))
                    for d in cache["data"]
                ]
                self._populate_container_dropdown()
                return

        self._fetch_containers_in_worker()

    def _refresh_containers(self) -> None:
        """[↻] forces a fetch regardless of cache age."""
        self._team_var.set("(обновление...)")
        self._team_menu.configure(values=["(обновление...)"])
        self._fetch_containers_in_worker()

    def _fetch_containers_in_worker(self) -> None:
        from tasks.backends import backend_from_name

        backend_name = self._current_backend_name()
        if not _backend_is_configured(backend_name, self._config):
            self._team_var.set(
                f"(нет ключа {_NAME_TO_DISPLAY.get(backend_name, backend_name)})",
            )
            return

        def worker():
            backend = None
            try:
                backend = backend_from_name(backend_name, self._config)
                self._active_clients.append(backend)
                try:
                    containers = backend.bootstrap()
                finally:
                    self._active_clients.remove(backend)
                    backend.close()
            except Exception as e:
                if self._cancel_event.is_set():
                    return
                self.after(0, self._on_containers_error, str(e))
                return

            if self._cancel_event.is_set():
                return
            # Cache as plain dicts for JSON-safety.
            self._config[self._backend_cache_key()] = {
                "data": [
                    {"id": c.id, "name": c.name, "key": c.key}
                    for c in containers
                ],
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_config(self._config)
            self.after(0, self._on_containers_loaded, containers)

        threading.Thread(target=worker, daemon=True).start()

    def _on_containers_loaded(self, containers: list) -> None:
        self._containers = containers
        self._populate_container_dropdown()

    def _on_containers_error(self, msg: str) -> None:
        from tasks.errors import humanize
        self._team_var.set("(ошибка)")
        self._team_menu.configure(values=["(ошибка)"])
        self._status_label.configure(
            text=f"✗ {humanize(msg)}", text_color=RED,
        )

    def _populate_container_dropdown(self) -> None:
        if not self._containers:
            empty_label = _EMPTY_CONTAINER_LABEL_BY_BACKEND.get(
                self._current_backend_name(), "(нет команд)",
            )
            self._team_var.set(empty_label)
            self._team_menu.configure(values=[empty_label])
            return
        # Backend-specific label format. Linear: "Engineering (ENG)"; Glide: "Inbox".
        labels = [
            f"{c.name} ({c.key})" if c.key else c.name
            for c in self._containers
        ]
        self._team_menu.configure(values=labels)
        self._team_var.set(labels[0])

    # ── Извлечение ───────────────────────────────────────────────

    def _on_extract(self) -> None:
        container = self._selected_container()
        if not container:
            backend_name = self._current_backend_name()
            label_word = _CONTAINER_ACCUSATIVE_BY_BACKEND.get(backend_name, "команду")
            messagebox.showwarning(
                "Нет контейнера",
                f"Выберите {label_word} или нажмите [↻] для загрузки списка.",
            )
            return

        model = self._model_var.get().strip()
        if not model:
            messagebox.showwarning("Нет модели", "Введите slug модели OpenRouter.")
            return

        self._set_busy(True)
        backend_name = self._current_backend_name()
        # Status text mentions which backend we're talking to so the user
        # knows whether a slow first-call is going to Linear or Glide.
        self._status_label.configure(
            text=f"Запрос к {_NAME_TO_DISPLAY.get(backend_name, backend_name)}...",
            text_color=TEXT_SECONDARY,
        )
        # Clear the editor; will be re-populated by _on_extract_success.
        self._tasks = []
        self._selected_index = None
        self._render_task_list()
        self._clear_form_vars()
        self._saved_label.configure(text="")

        # Capture on the main thread — Tk vars are not thread-safe (see
        # _selected_speaker_maps); the worker only receives plain dicts/lists.
        project = self._selected_context_project()
        people = self._selected_context_people()
        speaker_map, name_by_label = self._selected_speaker_maps()
        threading.Thread(
            target=self._run_extraction,
            args=(container, model, backend_name, project, people,
                  speaker_map, name_by_label),
            daemon=True,
        ).start()

    def _selected_container(self):
        """Return the Container the user selected in the dropdown, or None."""
        label_value = self._team_var.get()
        for c in self._containers:
            display = f"{c.name} ({c.key})" if c.key else c.name
            if display == label_value:
                return c
        return None

    def _run_extraction(
        self, container, model: str, backend_name: str, project, people: list,
        speaker_map: dict, name_by_label: dict,
    ) -> None:
        """Worker thread: extract tasks (+ optional protocol generation).

        All Tk-derived args (project / people / speaker_map / name_by_label)
        are captured on the main thread by the caller before .start() — do
        not read Tk vars here.
        """
        from directory.context import render_meeting_context
        from tasks.backends import backend_from_name
        from tasks.extractor import ExtractionError, extract
        from tasks.glide_client import GlideError
        from tasks.linear_client import LinearError
        from tasks.openrouter_client import OpenRouterClient, OpenRouterError
        from tasks.persistence import save_tasks_raw
        from tasks.trello_client import TrelloError
        from transcript_format import apply_speaker_names
        from utils import save_speakers
        meeting_context = render_meeting_context(people, project) or None
        # PR-2: substitute bound ФИО into the transcript labels before the LLM
        # sees it. Empty name_by_label → identity (no diarization / no binding).
        transcript_for_llm = apply_speaker_names(self._transcript, name_by_label)

        backend = openrouter = None
        try:
            backend    = backend_from_name(backend_name, self._config)
            openrouter = OpenRouterClient(self._config["openrouter_api_key"])
            self._active_clients.extend([backend, openrouter])

            if self._cancel_event.is_set():
                return

            # Phase 6.4.1: Glide backend returns empty members/labels (no
            # LLM grounding). Linear continues to fetch members + labels
            # for prompt context.
            ctx = backend.context(container.id)
            members = ctx.get("members") or []
            labels  = ctx.get("labels")  or []

            if not self._cancel_event.is_set():
                self.after(0, self._status_label.configure, {
                    "text": f"Запрос к OpenRouter ({model})...",
                    "text_color": TEXT_SECONDARY,
                })

            result = extract(
                transcript=transcript_for_llm,
                model=model,
                lang=self._transcript_lang,
                openrouter_client=openrouter,
                members=members,
                labels=labels,
                context=meeting_context,
            )

            if self._cancel_event.is_set():
                return

            # Phase 6.4.1: meta carries `backend` discriminator so re-open
            # of an existing tasks.json knows which backend originally fed
            # the editor. Old files without `backend` fall back to "linear"
            # in callers that need to dispatch.
            meta = {
                "extracted_at": datetime.now().isoformat(timespec="seconds"),
                "model": result["model"],
                "backend": backend_name,
                "team_id": container.id,        # legacy field name; holds container id
                "team_name": container.name,
                "transcript_lang": self._transcript_lang or "auto",
            }
            save_tasks_raw(self._history_folder, result["tasks"], meta)

            # Phase A UI part 2: remember the meeting's context selection so a
            # re-open restores it (and PR-2 can extend speakers.json with the
            # per-speaker map). Only write when something was selected; a write
            # failure must not block the committed task extraction.
            if project is not None or people or speaker_map:
                try:
                    save_speakers(
                        self._history_folder,
                        project.id if project else None,
                        [p.id for p in people],
                        speaker_map=speaker_map,
                    )
                except OSError as exc:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "speakers.json write failed: %s", exc,
                    )

            # Task 6 (MVP v5): opt-in protocol generation, reusing the same
            # OpenRouter client and user-chosen model. Runs AFTER save_tasks_raw
            # so a protocol-generation failure never blocks the (successful)
            # task-extraction commit. Logs warning + continues on
            # ProtocolGenerationError — the user still gets their tasks.
            #
            # Lazy imports match the existing pattern in this method
            # (line 531-536): keep tasks.* off the import chain of dialogs that
            # don't extract. The source-text test test_dialog_imports_protocol_generator
            # scans the whole file, so lazy-import placement still satisfies it.
            #
            # No module-level logger exists in this file — use inline
            # logging.getLogger(__name__) per Codex sanity-check #6 in the v4
            # plan (a bare `logger.warning(...)` would NameError and crash
            # the worker thread after save_tasks_raw already succeeded).
            if self.generate_protocol.get() and not self._cancel_event.is_set():
                import logging as _logging
                from pathlib import Path

                from tasks import protocol_generator
                from tasks.protocol_generator import ProtocolGenerationError

                _proto_logger = _logging.getLogger(__name__)
                self.after(0, self._status_label.configure, {
                    "text": f"Генерация протокола ({model})...",
                    "text_color": TEXT_SECONDARY,
                })
                try:
                    proto_result = protocol_generator.generate(
                        transcript=transcript_for_llm,
                        speakers=[p.full_name for p in people],
                        meeting_date="",  # not tracked at dialog level in v1.0
                        lang=self._transcript_lang,
                        model=model,
                        openrouter_client=openrouter,
                        context=meeting_context,
                    )
                    proto_path = Path(self._history_folder) / "protocol.md"
                    proto_path.write_text(
                        proto_result.markdown, encoding="utf-8",
                    )
                    _proto_logger.info("protocol saved to %s", proto_path)
                except ProtocolGenerationError as e:
                    # Don't block task extraction on protocol failure.
                    _proto_logger.warning("protocol generation failed: %s", e)
                except OSError as e:
                    # Disk full / permission denied writing protocol.md.
                    _proto_logger.warning("protocol.md write failed: %s", e)

            self._remember_recent_model(model)

            # PR-3: best-effort dedup pass on THIS worker thread (backend +
            # openrouter still open) so matches are ready before the rows
            # render. Never blocks the success dispatch on failure.
            if not self._cancel_event.is_set():
                self._run_dedup(
                    result["tasks"], backend=backend, backend_name=backend_name,
                    container_id=container.id, openrouter=openrouter, model=model,
                )

            if not self._cancel_event.is_set():
                self.after(0, self._on_extract_success, result, meta)

        except ExtractionError as e:
            if not self._cancel_event.is_set():
                self.after(
                    0, self._on_extract_error, str(e), e.raw_response,
                )
        except (OpenRouterError, LinearError, GlideError, TrelloError) as e:
            if not self._cancel_event.is_set():
                self.after(0, self._on_extract_error, str(e), None)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("extract failed")
            if not self._cancel_event.is_set():
                self.after(0, self._on_extract_error, f"{type(e).__name__}: {e}", None)
        finally:
            for c in (backend, openrouter):
                if c is not None:
                    try:
                        c.close()
                    except OSError:
                        # Best-effort socket cleanup — pool may already be torn down.
                        pass
                    if c in self._active_clients:
                        self._active_clients.remove(c)
            if not self._cancel_event.is_set():
                self.after(0, self._set_busy, False)

    def _run_dedup(self, tasks, *, backend, backend_name, container_id,
                   openrouter, model) -> None:
        """Worker-thread best-effort dedup: set ``task.dup_match`` on
        recurring tasks so the editor can offer "comment instead of dupe".

        Skipped when the backend can't comment (Glide) or the user disabled
        dedup. Any registry/LLM failure is logged and swallowed — a dedup
        hiccup must never block showing the freshly-extracted tasks (badges
        simply won't appear).
        """
        if not getattr(backend, "supports_comments", False):
            return
        if not bool(self._config.get("dedup_enabled", True)):
            return
        import logging as _logging

        from tasks.dedup import (
            build_board_registry,
            resolve_thresholds,
            select_match,
        )
        from tasks.linear_client import LinearError
        from tasks.openrouter_client import OpenRouterError
        from tasks.trello_client import TrelloError

        high, low = resolve_thresholds(self._config)
        try:
            registry = build_board_registry(backend, container_id)
        except (OSError, LinearError, TrelloError, ValueError, KeyError) as e:
            # Best-effort: a board-listing failure must never block showing
            # the freshly-extracted tasks (badges simply won't appear).
            _logging.getLogger(__name__).warning("dedup board registry failed: %s", e)
            return
        _logging.getLogger(__name__).info(
            "dedup registry: backend=%s container=%s size=%d",
            backend_name, container_id, len(registry),
        )
        for task in tasks:
            if self._cancel_event.is_set():
                return
            try:
                task.dup_match = select_match(
                    task, registry, backend=backend_name,
                    container_id=container_id, openrouter_client=openrouter,
                    model=model, high=high, low=low,
                )
            except OpenRouterError as e:
                _logging.getLogger(__name__).warning("dedup match failed: %s", e)

    # ── UI updates marshalled from worker thread ─────────────────

    def _on_extract_success(self, result: dict, meta: dict) -> None:
        n = len(result["tasks"])
        corr = result["corrections"]
        # Phase 6.5 B — real LLM cost display from response.usage.
        # Replaces the upfront «Стоимость ≈ $X.XX» heuristic with the
        # authoritative number for THIS extract.
        usage = result.get("usage") or {}
        used_model = result.get("model") or ""
        cost_str = self._format_real_cost(usage, used_model)

        parts = [f"✓ Извлечено {n} задач"]
        if corr:
            parts.append(f"({corr} полей скорректированы)")
        if cost_str:
            parts.append(f"·  {cost_str}")
        self._status_label.configure(text="  ".join(parts), text_color=GREEN)

        # Promote in-memory tasks to dialog state for the editor.
        self._tasks = list(result["tasks"])
        self._meta = dict(meta)
        self._cached_members = result.get("members", [])
        self._cached_labels = result.get("labels", [])
        self._render_task_list()
        if self._tasks:
            self._set_selection(0)
        # Persist tasks.json once with the fresh list.
        self._save_tasks_to_disk()

        rel = os.path.relpath(
            os.path.join(self._history_folder, "tasks_raw.json"),
        )
        self._saved_label.configure(
            text=f"Сохранено: {rel}", text_color=TEXT_SECONDARY,
        )

    def _format_real_cost(self, usage: dict, model: str) -> str:
        """Build a "X tokens · $0.0123" string from response.usage.

        Returns "" if usage is empty (defensive — all callers should
        already guard, but defensive helps composability).

        Cost source priority:
          1. usage["cost"] if OpenRouter included it (authoritative).
          2. computed: prompt × in_rate + completion × out_rate, where
             rates come from _MODEL_PRICING_USD_PER_M for the actual
             model that served the request.
          3. token count only — for unknown models we still show
             throughput so the user knows extraction did something.

        Format examples:
            "1,234↑ + 567↓ т.  ·  $0.0234"      (full, known model)
            "1,234↑ + 567↓ т."                    (unknown model)
        """
        if not usage:
            return ""
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        if prompt == 0 and completion == 0:
            return ""

        cost: float | None = None
        if "cost" in usage and isinstance(usage.get("cost"), (int, float)):
            cost = float(usage["cost"])
        else:
            rates = _MODEL_PRICING_USD_PER_M.get(model)
            if rates is not None:
                in_rate, out_rate = rates
                cost = (prompt * in_rate + completion * out_rate) / 1_000_000.0

        # Compose. Russian commas via .format spec; locale-agnostic comma
        # (1,234) is intentional — easier to read than 1234.
        toks_part = f"{prompt:,}↑ + {completion:,}↓ т."
        if cost is None:
            return toks_part
        return f"{toks_part}  ·  ${cost:.4f}"

    def _on_extract_error(self, msg: str, raw_response: str | None) -> None:
        from tasks.errors import humanize
        self._status_label.configure(text=f"✗ {humanize(msg)}", text_color=RED)
        if raw_response:
            import logging
            logging.getLogger(__name__).warning(
                "extract failed; raw LLM response logged for review:\n%s",
                raw_response[:2000],
            )

    # ── Keyboard shortcut handlers (Phase 6.5 C) ──────────────────

    def _kbd_extract(self, _event=None) -> str:
        """Ctrl+Shift+E → trigger Извлечь if the button is currently
        enabled. Returns 'break' so the default Tk bindings don't also
        fire (e.g., a focused textbox would get a literal 'E')."""
        try:
            state = str(self._btn_extract.cget("state"))
        except Exception:
            state = "disabled"
        if state == "normal":
            self._on_extract()
        return "break"

    def _kbd_send(self, _event=None) -> str:
        """Ctrl+Shift+S → trigger Отправить выбранные if enabled."""
        try:
            state = str(self._btn_send.cget("state"))
        except Exception:
            state = "disabled"
        if state == "normal":
            self._on_send_clicked()
        return "break"

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for btn in (self._btn_extract, self._btn_refresh,
                    self._btn_add, self._btn_select_all,
                    self._btn_select_none, self._btn_delete):
            btn.configure(state=state)
        # Autofill button (Phase 6.5) — disabled while busy, but doesn't
        # follow _set_editor_buttons_state because it's valid even when
        # no task is selected (auto-creates a fresh one).
        autofill_btn = getattr(self, "_btn_autofill", None)
        if autofill_btn is not None:
            autofill_btn.configure(state=state)
        # Send/Retry are only force-disabled while busy; their re-enable
        # state comes from _refresh_send_button_label (pending/failed counts).
        if busy:
            for btn in (self._btn_send, self._btn_retry):
                btn.configure(state="disabled")

    def _remember_recent_model(self, slug: str) -> None:
        """If `slug` is custom (not in curated list), prepend to FIFO-5 list."""
        if slug in _CURATED_MODELS:
            return
        recent = list(self._config.get(_RECENT_MODELS_KEY, []) or [])
        if slug in recent:
            recent.remove(slug)
        recent.insert(0, slug)
        recent = recent[:_RECENT_MODELS_LIMIT]
        self._config[_RECENT_MODELS_KEY] = recent
        save_config(self._config)

    def _on_close(self) -> None:
        """Cancel any in-flight worker, release the grab, destroy the toplevel."""
        # Persist any pending form edits before tearing down.
        try:
            self._persist_current_task()
        except OSError:
            import logging
            logging.getLogger(__name__).exception("persist on close failed")
        self._cancel_event.set()
        # Closing the requests.Session sockets interrupts any blocked .post()
        # in the worker; it raises ConnectionError, which the worker catches
        # and exits silently because cancel_event is set.
        for c in list(self._active_clients):
            try:
                c.close()
            except OSError:
                # Best-effort socket cleanup during dialog teardown.
                pass
        try:
            self.grab_release()
        except tk.TclError:
            # Toplevel already destroyed (e.g. window closed via X) — no grab to release.
            pass
        self.destroy()

    # ── Right-form builder ────────────────────────────────────────

    def _build_form(self) -> None:
        """Build the right-side form. Variables are owned by the form
        and bound to the selected task via _bind_form_to / _form_to_task."""
        f = self._form_panel

        # StringVar/BooleanVar instances (re-bound on selection change).
        self._var_title       = ctk.StringVar()
        self._var_priority    = ctk.StringVar(value="none")
        self._var_assignee    = ctk.StringVar(value="(нет)")
        self._var_due_date    = ctk.StringVar()

        # ── Autofill-from-text section (Phase 6.5, Söyle-friendly) ──
        # User dictates a free-form description (via Söyle or by typing)
        # into this textbox; clicking the button below runs the text
        # through the LLM (extract_one_task) and overwrites the form
        # fields. Sits at the TOP of the form so it's the first thing
        # the user sees when they click + Добавить on a fresh task.
        label(f, "Подсказка для AI (можно надиктовать через Söyle)").grid(
            row=0, column=0, padx=12, pady=(12, 2), sticky="w",
        )
        self._textbox_autofill_hint = ctk.CTkTextbox(
            f, wrap="word", height=64,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        )
        self._textbox_autofill_hint.grid(
            row=1, column=0, padx=12, pady=(0, 6), sticky="ew",
        )
        self._btn_autofill = tonal_button(
            f, text="Заполнить из текста",
            command=self._on_autofill_clicked, width=200,
        )
        self._btn_autofill.grid(row=2, column=0, padx=12, pady=(0, 14), sticky="w")

        row = 3
        label(f, "Заголовок").grid(row=row, column=0, padx=12, pady=(12, 2), sticky="w")
        row += 1
        self._entry_title = ctk.CTkEntry(
            f, textvariable=self._var_title, height=36,
            font=ctk.CTkFont(family=FONT, size=13),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
        )
        self._entry_title.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        self._var_title.trace_add("write", lambda *_: self._on_form_changed())

        row += 1
        label(f, "Приоритет").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        self._dropdown_priority = ctk.CTkOptionMenu(
            f, variable=self._var_priority,
            values=["none", "low", "medium", "high", "urgent"],
            command=lambda _v: self._on_form_changed(),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, button_color=BORDER,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self._dropdown_priority.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")

        row += 1
        label(f, "Исполнитель").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        self._dropdown_assignee = ctk.CTkOptionMenu(
            f, variable=self._var_assignee,
            values=["(нет)"],
            command=lambda _v: self._on_form_changed(),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, button_color=BORDER,
            font=ctk.CTkFont(family=FONT, size=12),
        )
        self._dropdown_assignee.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")

        row += 1
        label(f, "Метки").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        # For Phase 6.2, labels are displayed as a comma-joined string in an
        # entry. Toggle UI (chips with X buttons) is post-6.4 polish.
        self._var_labels_csv = ctk.StringVar()
        self._entry_labels = ctk.CTkEntry(
            f, textvariable=self._var_labels_csv, height=36,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
            placeholder_text="метка1, метка2 (только из team-labels)",
        )
        self._entry_labels.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        self._var_labels_csv.trace_add("write", lambda *_: self._on_form_changed())

        row += 1
        label(f, "Дата (YYYY-MM-DD)").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        self._entry_due = ctk.CTkEntry(
            f, textvariable=self._var_due_date, height=36,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
            placeholder_text="напр. 2026-05-15",
        )
        self._entry_due.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        self._var_due_date.trace_add("write", lambda *_: self._on_form_changed())

        row += 1
        label(f, "Описание").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
        row += 1
        f.grid_rowconfigure(row, weight=1)
        self._textbox_description = ctk.CTkTextbox(
            f, wrap="word", height=80,
            font=ctk.CTkFont(family=FONT, size=12),
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        )
        self._textbox_description.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="nsew")
        # CTkTextbox doesn't take a textvariable — we read it on save.
        self._textbox_description.bind("<<Modified>>", self._on_description_modified)

    def _set_editor_buttons_state(self, *, empty: bool) -> None:
        """Toggle right-form widget enable + delete-button enable based on selection."""
        state = "disabled" if empty else "normal"
        for w in (
            self._entry_title, self._dropdown_priority,
            self._dropdown_assignee, self._entry_labels, self._entry_due,
            self._textbox_description, self._btn_delete,
        ):
            try:
                w.configure(state=state)
            except tk.TclError:
                # Widget destroyed mid-busy-toggle — UI already going away.
                pass

    # ── Editor handlers ──────────────────────────────────────────

    def _ensure_meta(self) -> bool:
        """Populate self._meta from current backend+container selection
        if it's empty (i.e., user is doing manual-add without a prior
        extract). Returns True if meta is ready, False if user needs to
        pick a container first.

        Phase 6.5: closes a gap in 6.4.1 where manual-add path silently
        relied on an earlier extract having populated _meta. Autofill +
        Send without an extract used to fail with «потерян контекст команды/доски».
        """
        if self._meta:
            return True
        container = self._selected_container()
        if not container:
            backend_name = self._current_backend_name()
            label_word = _CONTAINER_ACCUSATIVE_BY_BACKEND.get(backend_name, "команду")
            messagebox.showwarning(
                "Нет контейнера",
                f"Выберите {label_word} в шапке диалога перед добавлением задач.",
            )
            return False
        backend_name = self._current_backend_name()
        self._meta = {
            "extracted_at": datetime.now().isoformat(timespec="seconds"),
            "model": self._model_var.get().strip(),
            "backend": backend_name,
            "team_id": container.id,           # legacy field name; container id
            "team_name": container.name,
            "transcript_lang": self._transcript_lang or "auto",
        }
        # Manual-add path doesn't fetch members/labels from the backend
        # (would need an extra round-trip just for assignee grounding).
        # Autofill from text still works without grounding — LLM fills
        # title/description/priority and skips assignee/labels.
        self._cached_members = []
        self._cached_labels = []
        return True

    def _on_add_task(self) -> None:
        from tasks.schema import Task
        if not self._ensure_meta():
            return
        self._push_undo_snapshot()
        new_task = Task(title="")
        self._tasks.append(new_task)
        self._render_task_list()
        self._set_selection(len(self._tasks) - 1)
        self._save_tasks_to_disk()

    def _on_select_all(self) -> None:
        self._push_undo_snapshot()
        for t in self._tasks:
            t.selected = True
        for r in getattr(self, "_task_rows", []):
            r.refresh_from_task()
        self._save_tasks_to_disk()

    def _on_select_none(self) -> None:
        self._push_undo_snapshot()
        for t in self._tasks:
            t.selected = False
        for r in getattr(self, "_task_rows", []):
            r.refresh_from_task()
        self._save_tasks_to_disk()

    def _on_delete_task(self) -> None:
        if self._selected_index is None:
            return
        self._push_undo_snapshot()
        del self._tasks[self._selected_index]
        new_index = min(self._selected_index, len(self._tasks) - 1) if self._tasks else None
        self._render_task_list()
        self._set_selection(new_index)
        self._save_tasks_to_disk()

    # ── List rendering and selection ─────────────────────────────

    def _render_task_list(self) -> None:
        """Re-create row widgets from `self._tasks`. Called after extract,
        load, add, or delete."""
        from tasks.schema import TaskStatus
        for child in self._task_list.winfo_children():
            child.destroy()
        self._task_rows: list = []

        # Empty-state placeholder: when there are no tasks, show a hint
        # pointing the user to the two manual-entry paths (Add button +
        # Söyle dictation in the right form). Phase 6.5 D — first-time
        # discoverability fix. Helps users who open the dialog without
        # a prior extract realize that manual-add IS supported.
        if not self._tasks:
            placeholder = ctk.CTkLabel(
                self._task_list,
                text=(
                    "📋  Список задач пуст\n\n"
                    "• «Извлечь» из транскрипта\n"
                    "  (Ctrl+Shift+E)\n\n"
                    "• «+ Добавить» вручную\n"
                    "  (Ctrl+N)\n\n"
                    "• Или надиктуйте через Söyle\n"
                    "  в «Подсказка для AI» справа\n"
                    "  (Ctrl+Enter в textbox'e)"
                ),
                font=ctk.CTkFont(family=FONT, size=12),
                text_color=TEXT_SECONDARY,
                justify="left", anchor="w",
            )
            placeholder.grid(row=0, column=0, padx=14, pady=14, sticky="nw")
            self._refresh_send_button_label()
            return

        for task in self._tasks:
            row = _TaskRow(
                self._task_list, task,
                on_select=self._select_task,
                on_toggle=self._on_row_toggle,
            )
            # Generous spacing — padx=8 lets cards breathe against the
            # scrollable-frame edge, pady=4 puts visible separation between
            # adjacent task cards instead of the cramped 1px default.
            row.grid(sticky="ew", padx=8, pady=4)
            # Re-apply non-PENDING status badges so re-opened/restored sessions
            # render their badges instead of a fresh checkbox.
            if task.status is not TaskStatus.PENDING:
                row.set_status_visual(
                    task.status,
                    identifier=task.linear_issue_id,
                    error_code=task.send_error,
                )
            self._task_rows.append(row)
            # PR-3: show the dedup badge+toggle for pre-send matches.
            if task.status is TaskStatus.PENDING and task.dup_match is not None:
                row.set_dup_visual()
        # Re-apply visual selection if applicable.
        if self._selected_index is not None and 0 <= self._selected_index < len(self._task_rows):
            self._task_rows[self._selected_index].set_selected_visual(True)
        # Refresh Send/Retry counts after the list shape changes.
        self._refresh_send_button_label()

    def _select_task(self, task) -> None:
        """User clicked a task row. Persist the previous selection's form
        edits, then bind the form to the new task."""
        # Persist current selection's pending edits before switching.
        self._persist_current_task()

        try:
            new_index = self._tasks.index(task)
        except ValueError:
            return  # task no longer in list

        self._set_selection(new_index)

    def _set_selection(self, new_index: int | None) -> None:
        """Update visual selection + form binding."""
        # Clear previous visual.
        rows = getattr(self, "_task_rows", [])
        if self._selected_index is not None and self._selected_index < len(rows):
            try:
                self._task_rows[self._selected_index].set_selected_visual(False)
            except tk.TclError:
                # Row widget destroyed (rerender during selection switch).
                pass

        self._selected_index = new_index

        if (
            new_index is None
            or not (0 <= new_index < len(self._tasks))
            or new_index >= len(self._task_rows)
        ):
            self._set_editor_buttons_state(empty=True)
            self._clear_form_vars()
            return

        self._task_rows[new_index].set_selected_visual(True)
        self._set_editor_buttons_state(empty=False)
        self._bind_form_to(self._tasks[new_index])

    def _on_row_toggle(self) -> None:
        """A row's checkbox was toggled. The underlying task is already
        updated; just save."""
        self._save_tasks_to_disk()

    # ── Form binding ─────────────────────────────────────────────

    def _bind_form_to(self, task) -> None:
        """Read `task` into form vars. Called on selection change."""
        # Suspend trace handlers temporarily by setting a guard flag.
        self._form_binding_in_progress = True
        try:
            self._var_title.set(task.title or "")
            self._var_priority.set(task.priority.name.lower())
            # Assignee dropdown values come from team_context. Refresh choices.
            ctx_members = self._teams_context_members()
            assignee_values = ["(нет)"] + [
                m.get("displayName") or m.get("name", m["id"]) for m in ctx_members
            ]
            self._dropdown_assignee.configure(values=assignee_values)
            current_assignee_label = "(нет)"
            if task.assignee_id and task.assignee_name:
                current_assignee_label = task.assignee_name
            self._var_assignee.set(current_assignee_label)
            # Labels: comma-separated names.
            self._var_labels_csv.set(", ".join(task.label_names))
            self._var_due_date.set(task.due_date or "")

            # Description goes through textbox, not StringVar.
            self._textbox_description.delete("1.0", "end")
            self._textbox_description.insert("1.0", task.description or "")
            # Reset the modified flag so the trace doesn't fire spuriously.
            self._textbox_description.edit_modified(False)
        finally:
            self._form_binding_in_progress = False

    def _clear_form_vars(self) -> None:
        self._form_binding_in_progress = True
        try:
            self._var_title.set("")
            self._var_priority.set("none")
            self._var_assignee.set("(нет)")
            self._var_labels_csv.set("")
            self._var_due_date.set("")
            self._textbox_description.delete("1.0", "end")
            self._textbox_description.edit_modified(False)
        finally:
            self._form_binding_in_progress = False

    def _form_to_task(self, task) -> None:
        """Write form vars into `task`. Called from _persist_current_task."""
        from tasks.schema import priority_from_string

        task.title = self._var_title.get().strip()
        task.priority = priority_from_string(self._var_priority.get())

        # Assignee: resolve label back to id via team_context.
        assignee_label = self._var_assignee.get()
        if assignee_label == "(нет)" or not assignee_label.strip():
            task.assignee_id = None
            task.assignee_name = None
        else:
            for m in self._teams_context_members():
                name = m.get("displayName") or m.get("name", m["id"])
                if name == assignee_label:
                    task.assignee_id = m["id"]
                    task.assignee_name = name
                    break
            else:
                # Loop exhausted without finding a match — clear stale assignee_id
                # to prevent the saved task from referencing a non-existent member.
                task.assignee_id = None
                task.assignee_name = None

        # Labels: comma-split, intersect with team-context label names.
        wanted_names = [
            n.strip() for n in self._var_labels_csv.get().split(",")
            if n.strip()
        ]
        ctx_labels = self._teams_context_labels()
        name_to_id = {lbl["name"]: lbl["id"] for lbl in ctx_labels}
        clean_ids = []
        clean_names = []
        for n in wanted_names:
            if n in name_to_id:
                clean_ids.append(name_to_id[n])
                clean_names.append(n)
        task.label_ids = clean_ids
        task.label_names = clean_names

        due = self._var_due_date.get().strip()
        task.due_date = due if due else None

        task.description = self._textbox_description.get("1.0", "end").rstrip("\n")

    def _persist_current_task(self) -> None:
        """If a task is selected, write form back to it and save tasks.json.

        Called on selection change, form change, and dialog close.
        Also refreshes the task row so left list stays consistent.
        """
        if self._selected_index is None:
            return
        if not (0 <= self._selected_index < len(self._tasks)):
            return
        task = self._tasks[self._selected_index]
        self._form_to_task(task)
        # Refresh the visual row so title/summary stays in sync.
        if self._selected_index < len(getattr(self, "_task_rows", [])):
            try:
                self._task_rows[self._selected_index].refresh_from_task()
            except tk.TclError:
                # Row widget destroyed mid-refresh — title/summary updates lost
                # but next render rebuilds from self._tasks.
                pass
        self._save_tasks_to_disk()

    def _on_form_changed(self) -> None:
        """Trace callback fired by var.trace_add. Suspends during programmatic
        bind to avoid a save-loop on selection switch."""
        if getattr(self, "_form_binding_in_progress", False):
            return
        self._persist_current_task()

    def _on_description_modified(self, _event=None) -> None:
        if getattr(self, "_form_binding_in_progress", False):
            self._textbox_description.edit_modified(False)
            return
        if not self._textbox_description.edit_modified():
            return
        self._textbox_description.edit_modified(False)
        self._persist_current_task()

    # ── Autofill from free-form text (Phase 6.5) ────────────────────

    def _on_autofill_clicked(self) -> None:
        """Read free text from the autofill textbox, run extract_one_task,
        populate the form fields on success.

        If no task is currently selected, auto-creates an empty one first
        (mirrors the natural «+ Добавить → надиктовать → заполнить» flow).
        Push undo snapshot so user can Ctrl+Z to revert if the LLM
        misinterprets the input."""
        free_text = self._textbox_autofill_hint.get("1.0", "end").strip()
        if not free_text:
            self._status_label.configure(
                text="Введите текст в поле подсказки",
                text_color=RED,
            )
            return

        api_key = (self._config.get("openrouter_api_key") or "").strip()
        if not api_key:
            messagebox.showwarning(
                "Нет ключа OpenRouter",
                "Добавьте OpenRouter API ключ в Settings и повторите.",
            )
            return

        # Phase 6.5 fix: ensure _meta is populated BEFORE any mutation.
        # If user opened the dialog without a prior extract and goes
        # directly to autofill, _meta is empty → send would fail later
        # with «потерян контекст команды/доски». _ensure_meta builds
        # synthetic meta from the header backend+container dropdowns.
        if not self._ensure_meta():
            return

        # Auto-create a task if none selected (lets user click + Добавить
        # OR just type into the hint and click — both flows work).
        if self._selected_index is None or not self._tasks:
            self._on_add_task()

        self._push_undo_snapshot()
        self._set_busy(True)
        self._status_label.configure(
            text="Заполнение из текста...", text_color=TEXT_SECONDARY,
        )
        model = self._model_var.get().strip() or "google/gemini-3.5-flash"
        threading.Thread(
            target=self._run_autofill_worker,
            args=(free_text, model, api_key),
            daemon=True,
        ).start()

    def _run_autofill_worker(
        self, free_text: str, model: str, api_key: str,
    ) -> None:
        """Worker thread: drive extract_one_task. Marshalls success or
        error back to the main thread via self.after."""
        from tasks.extractor import ExtractionError, extract_one_task
        from tasks.openrouter_client import OpenRouterClient, OpenRouterError

        openrouter = None
        result: object | None = None
        error_msg: str | None = None
        try:
            openrouter = OpenRouterClient(api_key)
            self._active_clients.append(openrouter)
            members = list(getattr(self, "_cached_members", []) or [])
            labels  = list(getattr(self, "_cached_labels",  []) or [])
            result = extract_one_task(
                free_text=free_text,
                members=members, labels=labels,
                lang=self._transcript_lang,
                model=model,
                openrouter_client=openrouter,
            )
        except (OpenRouterError, ExtractionError) as e:
            error_msg = str(e)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("autofill worker crashed")
            error_msg = f"{type(e).__name__}: {e}"
        finally:
            if openrouter is not None:
                try:
                    openrouter.close()
                except Exception:
                    pass
                if openrouter in self._active_clients:
                    self._active_clients.remove(openrouter)

        if self._cancel_event.is_set():
            return
        if error_msg is not None:
            self.after(0, self._on_autofill_error, error_msg)
        else:
            self.after(0, self._on_autofill_success, result)

    def _on_autofill_success(self, task) -> None:
        """Main-thread callback: write LLM-derived fields into the
        currently selected task and refresh both the form and the list row."""
        self._set_busy(False)
        if task is None:
            self._status_label.configure(
                text="✗ LLM не смог распознать задачу из текста — попробуйте перефразировать",
                text_color=RED,
            )
            return
        if self._selected_index is None or self._selected_index >= len(self._tasks):
            return

        current = self._tasks[self._selected_index]
        # Overwrite LLM-extracted fields. local_id / status / send_* /
        # selected stay as they were on the in-memory task.
        current.title         = task.title
        current.description   = task.description
        current.priority      = task.priority
        current.assignee_id   = task.assignee_id
        current.assignee_name = task.assignee_name
        current.label_ids     = list(task.label_ids)
        current.label_names   = list(task.label_names)
        current.due_date      = task.due_date

        # Re-bind form vars to the (now mutated) task — refresh display.
        self._bind_form_to(current)
        if self._selected_index < len(getattr(self, "_task_rows", [])):
            try:
                self._task_rows[self._selected_index].refresh_from_task()
            except Exception:
                pass
        self._save_tasks_to_disk()

        # Clear the hint textbox so the next dictation starts clean.
        self._textbox_autofill_hint.delete("1.0", "end")
        self._status_label.configure(
            text="✓ Поля заполнены", text_color=GREEN,
        )

    def _on_autofill_error(self, msg: str) -> None:
        from tasks.errors import humanize
        self._set_busy(False)
        self._status_label.configure(text=f"✗ {humanize(msg)}", text_color=RED)

    def _teams_context_members(self) -> list:
        """Return the members list from the most recent team_context fetch.

        Cached after extract. If team_context wasn't fetched, return [].
        """
        return getattr(self, "_cached_members", [])

    def _teams_context_labels(self) -> list:
        return getattr(self, "_cached_labels", [])

    def _save_tasks_to_disk(self) -> None:
        """Write tasks.json. Errors logged but not raised — auto-save is best-effort."""
        if self._tasks and self._meta:
            try:
                from tasks.persistence import save_tasks
                save_tasks(self._history_folder, self._tasks, self._meta)
            except OSError:
                import logging
                logging.getLogger(__name__).exception("auto-save tasks.json failed")
        # Refresh Send/Retry button labels regardless of disk-save outcome
        # — central post-mutation hook (toggle, edit, add, delete, undo).
        self._refresh_send_button_label()

    def _refresh_send_button_label(self) -> None:
        """Update Send button text+state from pending+selected count, and
        Retry button state from failed count. Safe to call before _build_ui
        completes — silently skips if buttons aren't built yet."""
        from tasks.schema import TaskStatus
        btn_send = getattr(self, "_btn_send", None)
        btn_retry = getattr(self, "_btn_retry", None)
        if btn_send is None or btn_retry is None:
            return
        pending = sum(
            1 for t in self._tasks
            if t.selected and t.status is TaskStatus.PENDING
        )
        failed = sum(
            1 for t in self._tasks if t.status is TaskStatus.FAILED
        )
        btn_send.configure(
            text=f"Отправить выбранные ({pending})",
            state="normal" if pending > 0 else "disabled",
        )
        btn_retry.configure(
            state="normal" if failed > 0 else "disabled",
        )

    # ── Send to Linear (Phase 6.3) ────────────────────────────────

    def _on_send_clicked(self) -> None:
        self._start_send(retry_failed=False)

    def _on_retry_clicked(self) -> None:
        self._start_send(retry_failed=True)

    def _start_send(self, *, retry_failed: bool) -> None:
        """Spin up the send worker. Saves any pending form edits first so
        the in-memory tasks list matches what's on disk before sending."""
        container_id = self._meta.get("team_id") if self._meta else None
        if not container_id:
            messagebox.showwarning(
                "Нет контейнера",
                "Не могу отправить — потерян контекст команды/доски. "
                "Перезапустите извлечение.",
            )
            return

        # Phase 6.4.1: backend comes from meta (set at extract time). For
        # legacy tasks.json (pre-6.4) no `backend` key → assume Linear.
        backend_name = (self._meta.get("backend") if self._meta else None) or "linear"
        if not _backend_is_configured(backend_name, self._config):
            display = _NAME_TO_DISPLAY.get(backend_name, backend_name)
            messagebox.showwarning(
                f"Нет ключа {display}",
                f"Добавьте ключ {display} в Settings и повторите.",
            )
            return

        # Flush any pending form edits into the in-memory task before sending.
        try:
            self._persist_current_task()
        except OSError:
            import logging
            logging.getLogger(__name__).exception("persist before send failed")

        self._set_busy(True)
        self._status_label.configure(
            text=f"Отправка в {backend_name.title()}...", text_color=TEXT_SECONDARY,
        )
        threading.Thread(
            target=self._run_send_worker,
            args=(container_id, backend_name, retry_failed),
            daemon=True,
        ).start()

    def _run_send_worker(
        self, container_id: str, backend_name: str, retry_failed: bool,
    ) -> None:
        """Worker thread: drive ``send_tasks_iter`` against the selected backend.

        Status updates flow through ``_on_send_status_change`` (worker thread:
        atomic save_tasks + ``self.after`` for UI). Final completion is
        marshalled to ``_on_send_finished`` on the main thread."""
        from tasks.backends import backend_from_name
        from tasks.sender import send_tasks_iter

        backend = backend_from_name(backend_name, self._config)
        self._active_clients.append(backend)
        error_msg: str | None = None
        try:
            for _ in send_tasks_iter(
                self._tasks,
                container_id=container_id,
                backend=backend,
                on_status_change=self._on_send_status_change,
                cancel_check=self._cancel_event.is_set,
                retry_failed=retry_failed,
                meeting_label=os.path.basename(self._history_folder),
            ):
                pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("send worker crashed")
            error_msg = f"{type(e).__name__}: {e}"
        finally:
            try:
                backend.close()
            except OSError:
                # Best-effort socket cleanup after send finishes/fails.
                pass
            if backend in self._active_clients:
                self._active_clients.remove(backend)

        if not self._cancel_event.is_set():
            self.after(0, self._on_send_finished, error_msg)

    def _on_send_status_change(self, task, new_status, **_kw) -> None:
        """Callback from sender (worker thread). Save tasks.json after
        every transition so a crash mid-send leaves accurate state on disk,
        then marshal a UI update onto the main thread."""
        if self._cancel_event.is_set():
            return
        # Atomic disk write — safe to call from any thread.
        if self._tasks and self._meta:
            try:
                from tasks.persistence import save_tasks
                save_tasks(self._history_folder, self._tasks, self._meta)
            except OSError:
                import logging
                logging.getLogger(__name__).exception(
                    "save_tasks during send failed",
                )
        if not self._cancel_event.is_set():
            self.after(0, self._update_row_status, task)

    def _update_row_status(self, task) -> None:
        """Main-thread UI update for a single row's status badge."""
        for row in getattr(self, "_task_rows", []):
            if row._task is task:
                row.set_status_visual(
                    task.status,
                    identifier=task.linear_issue_id,
                    error_code=task.send_error,
                )
                break
        # Live update of the count on the Send button as tasks transition.
        self._refresh_send_button_label()

    def _on_send_finished(self, error_msg: str | None) -> None:
        """Main-thread completion callback for the send worker."""
        self._set_busy(False)
        self._refresh_send_button_label()
        from tasks.schema import TaskStatus
        if error_msg:
            from tasks.errors import humanize
            self._status_label.configure(
                text=f"✗ Отправка прервана: {humanize(error_msg)}",
                text_color=RED,
            )
            return
        sent = sum(1 for t in self._tasks if t.status is TaskStatus.SENT)
        failed = sum(1 for t in self._tasks if t.status is TaskStatus.FAILED)
        self._status_label.configure(
            text=f"✓ Отправлено: {sent} · ✗ Ошибок: {failed}",
            text_color=GREEN if failed == 0 else RED,
        )

    # ── Undo stack ────────────────────────────────────────────────

    def _push_undo_snapshot(self) -> None:
        """Snapshot _tasks BEFORE a destructive op. Capped at 5 deep."""
        import copy
        self._undo_stack.append(copy.deepcopy(self._tasks))

    def _undo(self, _event=None) -> None:
        """Ctrl+Z handler. Restore the last snapshot if any."""
        if not self._undo_stack:
            return
        # Persist current selection first so any pending form edits flush
        # to disk before we restore the prior snapshot.
        self._persist_current_task()
        prior = self._undo_stack.pop()
        self._tasks = prior
        self._render_task_list()
        self._set_selection(0 if self._tasks else None)
        self._save_tasks_to_disk()

    # ── Load existing tasks on dialog open ───────────────────────

    def _try_load_existing_tasks(self) -> None:
        from pathlib import Path

        from tasks.persistence import MUTABLE_FILENAME, load_tasks
        path = Path(self._history_folder) / MUTABLE_FILENAME
        if not path.is_file():
            return
        try:
            loaded = load_tasks(self._history_folder)
        except (OSError, ValueError, KeyError):
            # OSError = file I/O; ValueError = JSON decode; KeyError = schema
            # mismatch (older format, hand-edited file). All recoverable —
            # treat as "no existing session" and let the user re-extract.
            import logging
            logging.getLogger(__name__).exception("could not load existing tasks.json")
            return
        self._tasks = list(loaded.get("tasks", []))
        self._meta = {k: v for k, v in loaded.items() if k != "tasks"}
        # We don't have team_context for an offline-loaded session; leave
        # _cached_members/_cached_labels empty. The form will still work for
        # title/priority/description/due_date — assignee/labels just show what
        # was saved without re-resolving.
