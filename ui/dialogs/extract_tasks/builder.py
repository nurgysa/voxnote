"""Widget-tree constructor for the Extract-Tasks dialog.

Extracted from ``ui/dialogs/extract_tasks/__init__.py`` (widget-tree
split, 2026-06-10 spec). Same contract as ``ui/app/builder.py`` /
``ui/dialogs/settings_builder.py``: free functions take the live dialog,
create widgets, and set captured refs on it under their original names.
Handlers, workers (extraction/dedup/containers), and state stay on
``ExtractTasksDialog``.

Import discipline (cycle guard): may import theme, ui.widgets and the
sibling ``.constants`` / ``.task_row`` — never the package ``__init__``.
"""

from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from theme import (
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    INPUT_BG,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.widgets import label, primary_button, tonal_button

from .constants import (
    _CONTAINER_LABEL_BY_BACKEND,
    _CURATED_MODELS,
    _NAME_TO_DISPLAY,
    _NO_SELECTION,
    _RECENT_MODELS_KEY,
)


def build_ui(dialog) -> None:
    dialog.grid_columnconfigure(0, weight=1)
    dialog.grid_rowconfigure(2, weight=1)   # editor row stretches

    # --- Header row: model + backend + container + refresh + extract ---
    header = ctk.CTkFrame(dialog, fg_color="transparent")
    header.grid(row=0, column=0, padx=16, pady=(14, 6), sticky="ew")
    header.grid_columnconfigure(1, weight=1)   # model
    header.grid_columnconfigure(5, weight=1)   # container

    label(header, "Модель").grid(row=0, column=0, padx=(0, 6), sticky="w")
    default_model = dialog._config.get(
        "tasks_default_model", _CURATED_MODELS[0],
    )
    recent = dialog._config.get(_RECENT_MODELS_KEY, []) or []
    all_models = list(_CURATED_MODELS)
    for slug in recent:
        if slug not in all_models:
            all_models.append(slug)
    dialog._model_var = ctk.StringVar(value=default_model)
    # CTkComboBox lets the user type custom slugs that aren't in the list.
    dialog._model_combo = ctk.CTkComboBox(
        header, variable=dialog._model_var, values=all_models,
        width=240, height=32,
        font=ctk.CTkFont(family=FONT, size=12),
        border_color=BORDER, button_color=BORDER,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
    )
    dialog._model_combo.grid(row=0, column=1, padx=(0, 12), sticky="ew")

    # Phase 6.4.1: backend selection. Values come from Settings flags;
    # changing it triggers re-fetch of containers.
    label(header, "Backend").grid(row=0, column=2, padx=(0, 6), sticky="w")
    backend_display = [_NAME_TO_DISPLAY[n] for n in dialog._enabled_backends]
    dialog._backend_var = ctk.StringVar(
        value=backend_display[0] if backend_display else "Linear",
    )
    dialog._backend_menu = ctk.CTkComboBox(
        header, variable=dialog._backend_var, values=backend_display or ["Linear"],
        width=110, height=32, state="readonly",
        font=ctk.CTkFont(family=FONT, size=12),
        border_color=BORDER, button_color=BORDER,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        command=dialog._on_backend_changed,
    )
    dialog._backend_menu.grid(row=0, column=3, padx=(0, 12), sticky="w")

    # Container dropdown — label changes per backend (Команда / Доска).
    dialog._container_label = label(
        header, _CONTAINER_LABEL_BY_BACKEND.get(dialog._current_backend_name(), "Команда"),
    )
    dialog._container_label.grid(row=0, column=4, padx=(0, 6), sticky="w")
    dialog._team_var = ctk.StringVar(value="(загрузка...)")
    dialog._team_menu = ctk.CTkComboBox(
        header, variable=dialog._team_var, values=["(загрузка...)"],
        width=180, height=32, state="readonly",
        font=ctk.CTkFont(family=FONT, size=12),
        border_color=BORDER, button_color=BORDER,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
    )
    dialog._team_menu.grid(row=0, column=5, padx=(0, 4), sticky="ew")

    dialog._btn_refresh = tonal_button(
        header, text="↻", command=dialog._refresh_containers, width=36,
    )
    dialog._btn_refresh.grid(row=0, column=6, padx=(0, 8))

    dialog._btn_extract = primary_button(
        header, text="Извлечь", command=dialog._on_extract, width=120,
    )
    dialog._btn_extract.grid(row=0, column=7)

    # Task 6 (MVP v5): protocol-generation opt-in checkbox.
    # Spans the full header width so the long Russian label has room.
    # State var `dialog.generate_protocol` was created in __init__ (kept
    # together with other state); only the widget binding lives here.
    ctk.CTkCheckBox(
        header,
        text="Также сгенерировать протокол встречи (protocol.md)",
        variable=dialog.generate_protocol,
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
        dialog._dir_store.load()
    except DirectoryError as exc:
        dialog._dir_load_error = str(exc)
    dialog._dir_projects = dialog._dir_store.projects()
    project_labels = ["— нет —"] + [p.name for p in dialog._dir_projects]
    dialog._context_project_menu = ctk.CTkComboBox(
        ctx_frame, variable=dialog._context_project_var, values=project_labels,
        width=240, height=30, state="readonly",
        font=ctk.CTkFont(family=FONT, size=12),
        border_color=BORDER, button_color=BORDER,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        command=dialog._on_context_project_changed,
    )
    dialog._context_project_menu.grid(row=0, column=1, padx=(0, 12), sticky="ew")

    label(ctx_frame, "Участники").grid(
        row=1, column=0, padx=(0, 6), pady=(6, 0), sticky="nw",
    )
    dialog._context_participants_frame = ctk.CTkScrollableFrame(
        ctx_frame, fg_color=INPUT_BG, height=90, corner_radius=8,
    )
    dialog._context_participants_frame.grid(
        row=1, column=1, padx=0, pady=(6, 0), sticky="ew",
    )

    label(ctx_frame, "Кто говорит").grid(
        row=2, column=0, padx=(0, 6), pady=(6, 0), sticky="nw",
    )
    dialog._speaker_rows_frame = ctk.CTkFrame(ctx_frame, fg_color="transparent")
    dialog._speaker_rows_frame.grid(
        row=2, column=1, padx=0, pady=(6, 0), sticky="ew",
    )

    # markitdown document grounding (row=3): attach reference docs (agenda,
    # brief, prior protocol) — converted to Markdown in _run_extraction and
    # merged into the same context= slot as the directory grounding above.
    label(ctx_frame, "Документы").grid(
        row=3, column=0, padx=(0, 6), pady=(6, 0), sticky="nw",
    )
    docs_row = ctk.CTkFrame(ctx_frame, fg_color="transparent")
    docs_row.grid(row=3, column=1, padx=0, pady=(6, 0), sticky="ew")
    tonal_button(
        docs_row, "Приложить документы", dialog._on_attach_documents, width=180,
    ).grid(row=0, column=0, padx=(0, 8))
    dialog._docs_count_label = label(docs_row, "")
    dialog._docs_count_label.grid(row=0, column=1, sticky="w")
    tonal_button(
        docs_row, "Очистить", dialog._clear_attached_documents, width=90,
    ).grid(row=0, column=2, padx=(8, 0))

    rebuild_context_participants(dialog, set())
    build_speaker_rows(dialog)
    dialog._restore_context_selection()
    if dialog._dir_load_error:
        # Defer to the event loop so the modal stacks on the fully-built,
        # realized window rather than mid-construction.
        dialog.after(0, lambda: messagebox.showwarning(
            "Справочник",
            "Не удалось прочитать справочник — контекст недоступен."
            f"\n\n{dialog._dir_load_error}",
            parent=dialog,
        ))

    # --- Status / cost hint row ---
    dialog._status_label = label(dialog, "", anchor="w")
    dialog._status_label.grid(row=1, column=0, padx=18, pady=(2, 4), sticky="ew")
    dialog._update_cost_hint()

    # --- Editor: master-detail layout ---
    editor = ctk.CTkFrame(dialog, fg_color="transparent")
    editor.grid(row=2, column=0, padx=16, pady=(2, 4), sticky="nsew")
    editor.grid_columnconfigure(0, weight=1, minsize=180)
    editor.grid_columnconfigure(1, weight=2, minsize=360)
    editor.grid_rowconfigure(0, weight=1)

    # Left: scrollable list of task rows + bottom action bar.
    left_panel = ctk.CTkFrame(editor, fg_color=SURFACE, corner_radius=10)
    left_panel.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
    left_panel.grid_rowconfigure(0, weight=1)
    left_panel.grid_columnconfigure(0, weight=1)

    dialog._task_list = ctk.CTkScrollableFrame(
        left_panel, fg_color="transparent", corner_radius=0,
    )
    dialog._task_list.grid(row=0, column=0, padx=4, pady=4, sticky="nsew")
    dialog._task_list.grid_columnconfigure(0, weight=1)

    # Action bar inside left panel: Add / SelectAll / SelectNone / Delete
    list_actions = ctk.CTkFrame(left_panel, fg_color="transparent")
    list_actions.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="ew")
    list_actions.grid_columnconfigure(0, weight=1)
    list_actions.grid_columnconfigure(1, weight=1)
    list_actions.grid_columnconfigure(2, weight=1)
    list_actions.grid_columnconfigure(3, weight=1)
    dialog._btn_add = tonal_button(
        list_actions, text="+ Добавить", command=dialog._on_add_task, width=110,
    )
    dialog._btn_add.grid(row=0, column=0, padx=2, sticky="ew")
    dialog._btn_select_all = tonal_button(
        list_actions, text="✓ Все", command=dialog._on_select_all, width=70,
    )
    dialog._btn_select_all.grid(row=0, column=1, padx=2, sticky="ew")
    dialog._btn_select_none = tonal_button(
        list_actions, text="✗ Снять", command=dialog._on_select_none, width=80,
    )
    dialog._btn_select_none.grid(row=0, column=2, padx=2, sticky="ew")
    dialog._btn_delete = tonal_button(
        list_actions, text="🗑 Удалить", command=dialog._on_delete_task, width=100,
    )
    dialog._btn_delete.grid(row=0, column=3, padx=2, sticky="ew")

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
    dialog._form_panel = ctk.CTkScrollableFrame(
        form_outer, fg_color="transparent", corner_radius=0,
    )
    dialog._form_panel.grid(row=0, column=0, sticky="nsew")
    dialog._form_panel.grid_columnconfigure(0, weight=1)
    build_form(dialog)

    # Disable buttons that need a selection until something is selected.
    dialog._set_editor_buttons_state(empty=True)

    # --- Footer: saved-path + Send / Retry / Close ---
    footer = ctk.CTkFrame(dialog, fg_color="transparent")
    footer.grid(row=3, column=0, padx=16, pady=(2, 14), sticky="ew")
    footer.grid_columnconfigure(0, weight=1)
    dialog._saved_label = label(footer, "", anchor="w")
    dialog._saved_label.grid(row=0, column=0, sticky="ew")

    dialog._btn_send = primary_button(
        footer, text="Отправить выбранные (0)",
        command=dialog._on_send_clicked, width=220, state="disabled",
    )
    dialog._btn_send.grid(row=0, column=1, padx=(8, 4), sticky="e")

    dialog._btn_retry = tonal_button(
        footer, text="Повторить упавшие",
        command=dialog._on_retry_clicked, width=170, state="disabled",
    )
    dialog._btn_retry.grid(row=0, column=2, padx=(0, 4), sticky="e")

    tonal_button(
        footer, text="Закрыть", command=dialog._on_close, width=110,
    ).grid(row=0, column=3, sticky="e")


def rebuild_context_participants(dialog, checked_ids: set[str]) -> None:
    """Render a checkbox per directory person, ticking checked_ids."""
    for w in dialog._context_participants_frame.winfo_children():
        w.destroy()
    dialog._context_person_vars = {}
    people = dialog._dir_store.people()
    if not people:
        label(
            dialog._context_participants_frame,
            "(справочник пуст — добавьте людей в «Справочники»)",
        ).grid(row=0, column=0, padx=4, pady=2, sticky="w")
        return
    for i, p in enumerate(people):
        var = ctk.BooleanVar(value=p.id in checked_ids)
        dialog._context_person_vars[p.id] = var
        text = p.full_name + (f" — {p.role}" if p.role else "")
        ctk.CTkCheckBox(
            dialog._context_participants_frame, text=text, variable=var,
            fg_color=BLUE, hover_color=BLUE_DIM, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT, size=12),
            checkbox_height=16, checkbox_width=16,
        ).grid(row=i, column=0, padx=4, pady=1, sticky="w")


def build_speaker_rows(dialog) -> None:
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

    for w in dialog._speaker_rows_frame.winfo_children():
        w.destroy()
    dialog._speaker_row_vars = {}
    dialog._speaker_friendly = {}

    label_map = _build_speaker_map(load_segments(dialog._history_folder))
    people = dialog._dir_store.people()
    if not label_map or not people:
        hint = (
            "(нет данных о спикерах)"
            if not label_map
            else "(справочник пуст — добавьте людей в «Справочники»)"
        )
        label(dialog._speaker_rows_frame, hint).grid(
            row=0, column=0, padx=4, pady=2, sticky="w",
        )
        return

    names = [_NO_SELECTION] + [p.full_name for p in people]
    for i, (raw, friendly) in enumerate(label_map.items()):
        dialog._speaker_friendly[raw] = friendly
        var = ctk.StringVar(value=_NO_SELECTION)
        dialog._speaker_row_vars[raw] = var
        label(dialog._speaker_rows_frame, friendly).grid(
            row=i, column=0, padx=(4, 8), pady=2, sticky="w",
        )
        ctk.CTkComboBox(
            dialog._speaker_rows_frame, variable=var, values=names,
            width=220, height=28, state="readonly",
            font=ctk.CTkFont(family=FONT, size=12),
            border_color=BORDER, button_color=BORDER,
            fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
            command=lambda _v, r=raw: dialog._on_speaker_bound(r),
        ).grid(row=i, column=1, padx=0, pady=2, sticky="w")


def build_form(dialog) -> None:
    """Build the right-side form. Variables are owned by the form
    and bound to the selected task via _bind_form_to / _form_to_task."""
    f = dialog._form_panel

    # StringVar/BooleanVar instances (re-bound on selection change).
    dialog._var_title       = ctk.StringVar()
    dialog._var_priority    = ctk.StringVar(value="none")
    dialog._var_assignee    = ctk.StringVar(value="(нет)")
    dialog._var_due_date    = ctk.StringVar()

    # ── Autofill-from-text section (Phase 6.5, Söyle-friendly) ──
    # User dictates a free-form description (via Söyle or by typing)
    # into this textbox; clicking the button below runs the text
    # through the LLM (extract_one_task) and overwrites the form
    # fields. Sits at the TOP of the form so it's the first thing
    # the user sees when they click + Добавить on a fresh task.
    label(f, "Подсказка для AI (можно надиктовать через Söyle)").grid(
        row=0, column=0, padx=12, pady=(12, 2), sticky="w",
    )
    dialog._textbox_autofill_hint = ctk.CTkTextbox(
        f, wrap="word", height=64,
        font=ctk.CTkFont(family=FONT, size=12),
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
    )
    dialog._textbox_autofill_hint.grid(
        row=1, column=0, padx=12, pady=(0, 6), sticky="ew",
    )
    dialog._btn_autofill = tonal_button(
        f, text="Заполнить из текста",
        command=dialog._on_autofill_clicked, width=200,
    )
    dialog._btn_autofill.grid(row=2, column=0, padx=12, pady=(0, 14), sticky="w")

    row = 3
    label(f, "Заголовок").grid(row=row, column=0, padx=12, pady=(12, 2), sticky="w")
    row += 1
    dialog._entry_title = ctk.CTkEntry(
        f, textvariable=dialog._var_title, height=36,
        font=ctk.CTkFont(family=FONT, size=13),
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
    )
    dialog._entry_title.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
    dialog._var_title.trace_add("write", lambda *_: dialog._on_form_changed())

    row += 1
    label(f, "Приоритет").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
    row += 1
    dialog._dropdown_priority = ctk.CTkOptionMenu(
        f, variable=dialog._var_priority,
        values=["none", "low", "medium", "high", "urgent"],
        command=lambda _v: dialog._on_form_changed(),
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY, button_color=BORDER,
        font=ctk.CTkFont(family=FONT, size=12),
    )
    dialog._dropdown_priority.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")

    row += 1
    label(f, "Исполнитель").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
    row += 1
    dialog._dropdown_assignee = ctk.CTkOptionMenu(
        f, variable=dialog._var_assignee,
        values=["(нет)"],
        command=lambda _v: dialog._on_form_changed(),
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY, button_color=BORDER,
        font=ctk.CTkFont(family=FONT, size=12),
    )
    dialog._dropdown_assignee.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")

    row += 1
    label(f, "Метки").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
    row += 1
    # For Phase 6.2, labels are displayed as a comma-joined string in an
    # entry. Toggle UI (chips with X buttons) is post-6.4 polish.
    dialog._var_labels_csv = ctk.StringVar()
    dialog._entry_labels = ctk.CTkEntry(
        f, textvariable=dialog._var_labels_csv, height=36,
        font=ctk.CTkFont(family=FONT, size=12),
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
        placeholder_text="метка1, метка2 (только из team-labels)",
    )
    dialog._entry_labels.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
    dialog._var_labels_csv.trace_add("write", lambda *_: dialog._on_form_changed())

    row += 1
    label(f, "Дата (YYYY-MM-DD)").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
    row += 1
    dialog._entry_due = ctk.CTkEntry(
        f, textvariable=dialog._var_due_date, height=36,
        font=ctk.CTkFont(family=FONT, size=12),
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY, border_color=BORDER,
        placeholder_text="напр. 2026-05-15",
    )
    dialog._entry_due.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
    dialog._var_due_date.trace_add("write", lambda *_: dialog._on_form_changed())

    row += 1
    label(f, "Описание").grid(row=row, column=0, padx=12, pady=(0, 2), sticky="w")
    row += 1
    f.grid_rowconfigure(row, weight=1)
    dialog._textbox_description = ctk.CTkTextbox(
        f, wrap="word", height=80,
        font=ctk.CTkFont(family=FONT, size=12),
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
    )
    dialog._textbox_description.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="nsew")
    # CTkTextbox doesn't take a textvariable — we read it on save.
    dialog._textbox_description.bind("<<Modified>>", dialog._on_description_modified)
