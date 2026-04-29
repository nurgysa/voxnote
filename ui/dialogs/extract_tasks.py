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

import json
import os
import threading
from collections import deque
from datetime import datetime, timedelta
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from theme import (
    BG, BLUE_DIM, BORDER, FONT, GREEN, INPUT_BG, RED, SURFACE,
    TEXT_PRIMARY, TEXT_SECONDARY,
)
# Note: BLUE_DIM is reserved for the _TaskRow checkbox accent in Task 6.2-3.
from ui.widgets import label, option_menu, primary_button, tonal_button
from utils import save_config


# Same curated list as Settings → OpenRouter section, kept in sync manually.
# (Phase 6.4 may replace both with a live /models browser.)
_CURATED_MODELS = [
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5",
    "openai/gpt-4o",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-v3",
]

_TEAMS_CACHE_KEY = "linear_teams_cache"
_TEAMS_CACHE_TTL = timedelta(hours=24)
_RECENT_MODELS_KEY = "tasks_recent_models"
_RECENT_MODELS_LIMIT = 5

# Sonnet-4.5 input price per 1M tokens. Used for the cost-estimate hint.
# Imprecise (we don't know the actual model's price) but useful as a sanity-check.
_COST_PER_1M_INPUT_TOKENS_USD = 3.0

_PRIORITY_GLYPHS = {
    "none":   "⚪",
    "low":    "🔵",
    "medium": "🟡",
    "high":   "🟠",
    "urgent": "🔴",
}


class _TaskRow(ctk.CTkFrame):
    """One row in the left task list. Clicking the row body selects;
    clicking the checkbox toggles selected without selecting.
    """

    def __init__(
        self, parent, task, *, on_select, on_toggle,
    ):
        super().__init__(parent, fg_color="transparent", corner_radius=6)
        self._task = task
        self._on_select = on_select
        self._on_toggle = on_toggle
        self._selected_visual = False

        self.grid_columnconfigure(1, weight=1)

        self._var_checked = ctk.BooleanVar(value=task.selected)
        self._check = ctk.CTkCheckBox(
            self, text="", variable=self._var_checked,
            command=self._handle_toggle,
            checkbox_height=18, checkbox_width=18,
            fg_color=BLUE_DIM, hover_color=BLUE_DIM, border_color=BORDER,
        )
        self._check.grid(row=0, column=0, padx=(8, 6), pady=4, sticky="w")

        self._lbl_title = ctk.CTkLabel(
            self, text=task.title or "(без заголовка)",
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        )
        self._lbl_title.grid(row=0, column=1, padx=2, pady=(4, 0), sticky="ew")

        self._lbl_summary = ctk.CTkLabel(
            self, text=self._summary_text(),
            font=ctk.CTkFont(family=FONT, size=11),
            text_color=TEXT_SECONDARY, anchor="w",
        )
        self._lbl_summary.grid(row=1, column=1, padx=2, pady=(0, 4), sticky="ew")

        # Click anywhere on the body (except the checkbox) to select.
        for w in (self, self._lbl_title, self._lbl_summary):
            w.bind("<Button-1>", self._handle_click)

    def _handle_click(self, _event=None):
        self._on_select(self._task)

    def _handle_toggle(self):
        self._task.selected = bool(self._var_checked.get())
        self._on_toggle()

    def set_selected_visual(self, selected: bool) -> None:
        self._selected_visual = selected
        self.configure(fg_color=SURFACE if selected else "transparent")

    def refresh_from_task(self) -> None:
        """Re-render summary + title from the underlying task. Called after
        edits to keep the row in sync with the form."""
        self._lbl_title.configure(text=self._task.title or "(без заголовка)")
        self._lbl_summary.configure(text=self._summary_text())
        self._var_checked.set(self._task.selected)

    def _summary_text(self) -> str:
        glyph = _PRIORITY_GLYPHS.get(self._task.priority.name.lower(), "⚪")
        assignee = self._task.assignee_name or "—"
        return f"👤 {assignee}  ·  {glyph} {self._task.priority.name.lower()}"


class ExtractTasksDialog(ctk.CTkToplevel):
    """Phase-6.2 master-detail editor scaffold (interactivity in Tasks 3–4)."""

    def __init__(
        self,
        parent,
        *,
        transcript: str,
        history_folder: str,
        transcript_lang: Optional[str],
        config: dict,
    ):
        super().__init__(parent)
        self._parent = parent
        self._transcript = transcript
        self._history_folder = history_folder
        self._transcript_lang = transcript_lang
        self._config = config

        # Worker-thread plumbing: cancel_event flips on close;
        # active_client is the in-flight client we close to interrupt sockets.
        self._cancel_event = threading.Event()
        self._active_clients: list = []   # OpenRouter + Linear clients in flight
        self._teams: list[dict] = []      # populated by bootstrap

        # Editor state. _tasks is the canonical in-memory list; right form
        # binds to _tasks[_selected_index]. _meta carries extract context for
        # save_tasks (extracted_at, model, team_id, team_name, transcript_lang).
        self._tasks: list = []      # list[Task]
        self._task_rows: list = []  # list[_TaskRow] — populated by _render_task_list
        self._selected_index: Optional[int] = None
        self._meta: dict = {}       # populated post-extract or post-load
        # Undo stack (5 deep) of deepcopy(self._tasks) snapshots before destructive ops.
        self._undo_stack: deque = deque(maxlen=5)

        # If tasks.json exists in the history folder (e.g., user re-opened the
        # dialog after a half-finished edit), load it instead of waiting for a
        # fresh extract.
        self._try_load_existing_tasks()

        self.title("Извлечение задач")
        self.geometry("640x520")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.grab_set()

        self._build_ui()

        # Bind undo (both lower and upper case — tkinter distinguishes them).
        self.bind("<Control-z>", self._undo)
        self.bind("<Control-Z>", self._undo)

        # If we loaded existing tasks above, render them now that widgets exist.
        if self._tasks:
            self._render_task_list()
            self._set_selection(0)

        self._load_teams_async()

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)   # editor row stretches

        # --- Header row: model + team + refresh + extract ---
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=16, pady=(14, 6), sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        header.grid_columnconfigure(3, weight=1)

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
            width=280, height=32,
            font=ctk.CTkFont(family=FONT, size=12),
            border_color=BORDER, button_color=BORDER,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        )
        self._model_combo.grid(row=0, column=1, padx=(0, 12), sticky="ew")

        label(header, "Команда").grid(row=0, column=2, padx=(0, 6), sticky="w")
        self._team_var = ctk.StringVar(value="(загрузка...)")
        self._team_menu = ctk.CTkComboBox(
            header, variable=self._team_var, values=["(загрузка...)"],
            width=200, height=32, state="readonly",
            font=ctk.CTkFont(family=FONT, size=12),
            border_color=BORDER, button_color=BORDER,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        )
        self._team_menu.grid(row=0, column=3, padx=(0, 4), sticky="ew")

        self._btn_refresh = tonal_button(
            header, text="↻", command=self._refresh_teams, width=36,
        )
        self._btn_refresh.grid(row=0, column=4, padx=(0, 8))

        self._btn_extract = primary_button(
            header, text="Извлечь", command=self._on_extract, width=120,
        )
        self._btn_extract.grid(row=0, column=5)

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

        # Right: form for editing selected task.
        self._form_panel = ctk.CTkFrame(editor, fg_color=SURFACE, corner_radius=10)
        self._form_panel.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        self._form_panel.grid_columnconfigure(0, weight=1)
        self._build_form()

        # Disable buttons that need a selection until something is selected.
        self._set_editor_buttons_state(empty=True)

        # --- Footer: saved-path + close ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(2, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self._saved_label = label(footer, "", anchor="w")
        self._saved_label.grid(row=0, column=0, sticky="ew")
        tonal_button(
            footer, text="Закрыть", command=self._on_close, width=110,
        ).grid(row=0, column=1, sticky="e")

    def _update_cost_hint(self) -> None:
        """Heuristic: ~chars/4 input tokens × Sonnet pricing × 1.3 (output)."""
        chars = len(self._transcript or "")
        approx_tokens = max(chars // 4, 1)
        cost = approx_tokens / 1_000_000 * _COST_PER_1M_INPUT_TOKENS_USD * 1.3
        self._status_label.configure(
            text=f"Стоимость ≈ ${cost:.2f} (≈ {approx_tokens:,} токенов)",
            text_color=TEXT_SECONDARY,
        )

    # ── Team bootstrap (cached 24h) ──────────────────────────────

    def _load_teams_async(self) -> None:
        """Use cache if fresh; else fetch from Linear in a worker."""
        cache = self._config.get(_TEAMS_CACHE_KEY) or {}
        fetched_at = cache.get("fetched_at")
        if fetched_at:
            try:
                age = datetime.now() - datetime.fromisoformat(fetched_at)
            except ValueError:
                age = _TEAMS_CACHE_TTL + timedelta(seconds=1)
            if age <= _TEAMS_CACHE_TTL and cache.get("data"):
                self._teams = list(cache["data"])
                self._populate_team_dropdown()
                return

        self._fetch_teams_in_worker()

    def _refresh_teams(self) -> None:
        """[↻] forces a fetch regardless of cache age."""
        self._team_var.set("(обновление...)")
        self._team_menu.configure(values=["(обновление...)"])
        self._fetch_teams_in_worker()

    def _fetch_teams_in_worker(self) -> None:
        api_key = (self._config.get("linear_api_key") or "").strip()
        if not api_key:
            self._team_var.set("(нет ключа Linear)")
            return

        def worker():
            try:
                from tasks.linear_client import LinearClient, LinearError
                client = LinearClient(api_key)
                self._active_clients.append(client)
                try:
                    result = client.bootstrap()
                finally:
                    self._active_clients.remove(client)
                    client.close()
            except Exception as e:
                if self._cancel_event.is_set():
                    return  # dialog already closing; ignore
                self.after(0, self._on_teams_error, str(e))
                return

            if self._cancel_event.is_set():
                return
            teams = result.get("teams", [])
            self._config[_TEAMS_CACHE_KEY] = {
                "data": teams,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_config(self._config)
            self.after(0, self._on_teams_loaded, teams)

        threading.Thread(target=worker, daemon=True).start()

    def _on_teams_loaded(self, teams: list[dict]) -> None:
        self._teams = teams
        self._populate_team_dropdown()

    def _on_teams_error(self, msg: str) -> None:
        self._team_var.set("(ошибка)")
        self._team_menu.configure(values=["(ошибка)"])
        self._status_label.configure(text=f"✗ {msg}", text_color=RED)

    def _populate_team_dropdown(self) -> None:
        if not self._teams:
            self._team_var.set("(нет команд)")
            self._team_menu.configure(values=["(нет команд)"])
            return
        labels = [f"{t['name']} ({t['key']})" for t in self._teams]
        self._team_menu.configure(values=labels)
        self._team_var.set(labels[0])

    # ── Извлечение ───────────────────────────────────────────────

    def _on_extract(self) -> None:
        team = self._selected_team()
        if not team:
            messagebox.showwarning(
                "Нет команды",
                "Выберите команду или нажмите [↻] для загрузки списка.",
            )
            return

        model = self._model_var.get().strip()
        if not model:
            messagebox.showwarning("Нет модели", "Введите slug модели OpenRouter.")
            return

        self._set_busy(True)
        self._status_label.configure(
            text="Запрос к Linear (team_context)...", text_color=TEXT_SECONDARY,
        )
        # Clear the editor; will be re-populated by _on_extract_success.
        self._tasks = []
        self._selected_index = None
        self._render_task_list()
        self._clear_form_vars()
        self._saved_label.configure(text="")

        threading.Thread(
            target=self._run_extraction,
            args=(team, model),
            daemon=True,
        ).start()

    def _selected_team(self) -> Optional[dict]:
        label_value = self._team_var.get()
        for t in self._teams:
            if f"{t['name']} ({t['key']})" == label_value:
                return t
        return None

    def _run_extraction(self, team: dict, model: str) -> None:
        from tasks.extractor import extract, ExtractionError
        from tasks.linear_client import LinearClient, LinearError
        from tasks.openrouter_client import OpenRouterClient, OpenRouterError
        from tasks.persistence import save_tasks_raw

        linear = openrouter = None
        try:
            linear     = LinearClient(self._config["linear_api_key"])
            openrouter = OpenRouterClient(self._config["openrouter_api_key"])
            self._active_clients.extend([linear, openrouter])

            if self._cancel_event.is_set():
                return

            if not self._cancel_event.is_set():
                self.after(0, self._status_label.configure, {
                    "text": f"Запрос к OpenRouter ({model})...",
                    "text_color": TEXT_SECONDARY,
                })

            result = extract(
                transcript=self._transcript,
                team_id=team["id"],
                model=model,
                lang=self._transcript_lang,
                linear_client=linear,
                openrouter_client=openrouter,
            )

            if self._cancel_event.is_set():
                return

            meta = {
                "extracted_at": datetime.now().isoformat(timespec="seconds"),
                "model": result["model"],
                "team_id": team["id"],
                "team_name": team["name"],
                "transcript_lang": self._transcript_lang or "auto",
            }
            save_tasks_raw(self._history_folder, result["tasks"], meta)

            self._remember_recent_model(model)

            if not self._cancel_event.is_set():
                self.after(0, self._on_extract_success, result, meta)

        except ExtractionError as e:
            # ExtractionError carries `raw_response` when extract() got a
            # successful network round-trip but the payload was unusable.
            if not self._cancel_event.is_set():
                self.after(
                    0, self._on_extract_error, str(e), e.raw_response,
                )
        except (OpenRouterError, LinearError) as e:
            if not self._cancel_event.is_set():
                self.after(0, self._on_extract_error, str(e), None)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("extract failed")
            if not self._cancel_event.is_set():
                self.after(0, self._on_extract_error, f"{type(e).__name__}: {e}", None)
        finally:
            for c in (linear, openrouter):
                if c is not None:
                    try:
                        c.close()
                    except Exception:
                        pass
                    if c in self._active_clients:
                        self._active_clients.remove(c)
            # Guard the final UI update — if the user closed the dialog mid-run
            # the toplevel is destroyed and self.after would raise TclError.
            if not self._cancel_event.is_set():
                self.after(0, self._set_busy, False)

    # ── UI updates marshalled from worker thread ─────────────────

    def _on_extract_success(self, result: dict, meta: dict) -> None:
        n = len(result["tasks"])
        corr = result["corrections"]
        if corr:
            self._status_label.configure(
                text=f"✓ Извлечено {n} задач ({corr} полей скорректированы)",
                text_color=GREEN,
            )
        else:
            self._status_label.configure(
                text=f"✓ Извлечено {n} задач",
                text_color=GREEN,
            )

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

    def _on_extract_error(self, msg: str, raw_response: Optional[str]) -> None:
        self._status_label.configure(text=f"✗ {msg}", text_color=RED)
        if raw_response:
            import logging
            logging.getLogger(__name__).warning(
                "extract failed; raw LLM response logged for review:\n%s",
                raw_response[:2000],
            )

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for btn in (self._btn_extract, self._btn_refresh,
                    self._btn_add, self._btn_select_all,
                    self._btn_select_none, self._btn_delete):
            btn.configure(state=state)

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
        except Exception:
            import logging
            logging.getLogger(__name__).exception("persist on close failed")
        self._cancel_event.set()
        # Closing the requests.Session sockets interrupts any blocked .post()
        # in the worker; it raises ConnectionError, which the worker catches
        # and exits silently because cancel_event is set.
        for c in list(self._active_clients):
            try:
                c.close()
            except Exception:
                pass
        try:
            self.grab_release()
        except Exception:
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

        row = 0
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
            except Exception:
                pass

    # ── Editor handlers ──────────────────────────────────────────

    def _on_add_task(self) -> None:
        from tasks.schema import Task
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
        for child in self._task_list.winfo_children():
            child.destroy()
        self._task_rows: list = []
        for task in self._tasks:
            row = _TaskRow(
                self._task_list, task,
                on_select=self._select_task,
                on_toggle=self._on_row_toggle,
            )
            row.grid(sticky="ew", padx=2, pady=1)
            self._task_rows.append(row)
        # Re-apply visual selection if applicable.
        if self._selected_index is not None and 0 <= self._selected_index < len(self._task_rows):
            self._task_rows[self._selected_index].set_selected_visual(True)

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

    def _set_selection(self, new_index: Optional[int]) -> None:
        """Update visual selection + form binding."""
        # Clear previous visual.
        if self._selected_index is not None and self._selected_index < len(getattr(self, "_task_rows", [])):
            try:
                self._task_rows[self._selected_index].set_selected_visual(False)
            except Exception:
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
            except Exception:
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

    def _teams_context_members(self) -> list:
        """Return the members list from the most recent team_context fetch.

        Cached after extract. If team_context wasn't fetched, return [].
        """
        return getattr(self, "_cached_members", [])

    def _teams_context_labels(self) -> list:
        return getattr(self, "_cached_labels", [])

    def _save_tasks_to_disk(self) -> None:
        """Write tasks.json. Errors logged but not raised — auto-save is best-effort."""
        if not self._tasks or not self._meta:
            return
        try:
            from tasks.persistence import save_tasks
            save_tasks(self._history_folder, self._tasks, self._meta)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("auto-save tasks.json failed")

    # ── Undo stack ────────────────────────────────────────────────

    def _push_undo_snapshot(self) -> None:
        """Snapshot _tasks BEFORE a destructive op. Capped at 5 deep."""
        import copy
        self._undo_stack.append(copy.deepcopy(self._tasks))

    def _undo(self, _event=None) -> None:
        """Ctrl+Z handler. Restore the last snapshot if any."""
        if not self._undo_stack:
            return
        prior = self._undo_stack.pop()
        # Persist current selection first so it isn't lost mid-restore.
        self._persist_current_task()
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
        except Exception:
            import logging
            logging.getLogger(__name__).exception("could not load existing tasks.json")
            return
        self._tasks = list(loaded.get("tasks", []))
        self._meta = {k: v for k, v in loaded.items() if k != "tasks"}
        # We don't have team_context for an offline-loaded session; leave
        # _cached_members/_cached_labels empty. The form will still work for
        # title/priority/description/due_date — assignee/labels just show what
        # was saved without re-resolving.
