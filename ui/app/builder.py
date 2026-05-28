"""Widget-tree constructor for the main App window.

Extracted from ``ui/app/__init__.py`` (F4-PR-2b). The original
``App._build_ui`` method was 274 lines of CustomTkinter calls that
populate widget references and persistence ``StringVar`` / ``BooleanVar``
fields on the App instance. The body is purely mechanical wiring (no
business logic) so it lives as a free function — ``App.__init__`` calls
``build_ui(self)`` once after state fields have been initialized.

The function mutates ``app`` (sets ``app._lbl_status``, ``app._btn_file``,
``app._lang_var``, …); it does NOT construct ``app`` itself. Must be
called exactly once per App lifetime, after ``app._config`` and the
recorder/transcriber state have been set in ``App.__init__``.
"""
from __future__ import annotations

import customtkinter as ctk

from theme import (
    BANNER_TEXT_ON_YELLOW,
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    GREEN,
    PROGRESS_BG,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    YELLOW,
)
from ui.widgets import (
    card,
    label,
    option_menu,
    primary_button,
    tonal_button,
)

from .constants import (
    APPEARANCE_MODES,
    LANGUAGES,
    SPEAKER_COUNTS,
)


def build_ui(app):
    """Build the main window's widget tree and bind state vars onto ``app``."""
    app.grid_columnconfigure(0, weight=1)
    # Text result row shifted +1 to make room for the first-run banner.
    # Without the banner present the row is just empty; with banner it
    # occupies row=0 and everything below shifts down by 1.
    app.grid_rowconfigure(7, weight=1)

    # --- First-run banner (row=0, conditional) ---
    # Shown when no AssemblyAI key is configured — pushes the user toward
    # Settings on first launch. Yellow strip with a "Открыть настройки →"
    # shortcut. State flag is set in App.__init__ before build_ui runs.
    # When the banner isn't rendered, row=0 is empty (zero height) so the
    # rest of the layout looks identical to pre-Task-7.
    if getattr(app, "_first_run", False):
        banner = ctk.CTkFrame(app, fg_color=YELLOW, corner_radius=0, height=42)
        banner.grid(row=0, column=0, sticky="ew")
        banner.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            banner,
            text=(
                "Первый запуск. Откройте Настройки → введите "
                "AssemblyAI API key + OpenRouter ключ."
            ),
            text_color=BANNER_TEXT_ON_YELLOW,
            font=ctk.CTkFont(family=FONT, size=12),
            anchor="w",
        ).grid(row=0, column=0, padx=16, pady=10, sticky="w")
        ctk.CTkButton(
            banner, text="Открыть настройки →",
            command=app._open_settings_dialog,
            width=180, height=28,
            fg_color=SURFACE, hover_color=BORDER,
            text_color=BANNER_TEXT_ON_YELLOW,
        ).grid(row=0, column=1, padx=8, pady=6)
        app._first_run_banner = banner

    # --- Header (row=1 — was row=0 before banner) ---
    header = ctk.CTkFrame(app, fg_color=SURFACE, corner_radius=0, height=52)
    header.grid(row=1, column=0, sticky="ew")
    header.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(
        header, text="Audio Transcriber",
        font=ctk.CTkFont(family=FONT, size=17, weight="bold"),
        text_color=TEXT_PRIMARY,
    ).grid(row=0, column=0, padx=24, pady=12)

    app._lbl_status = ctk.CTkLabel(
        header, text="", anchor="e",
        font=ctk.CTkFont(family=FONT, size=12),
        text_color=TEXT_SECONDARY,
    )
    app._lbl_status.grid(row=0, column=1, padx=24, pady=12, sticky="e")

    # --- File card (row=2 — was row=1 before banner) ---
    file_card = card(app)
    file_card.grid(row=2, column=0, padx=16, pady=(12, 6), sticky="ew")
    file_card.grid_columnconfigure(1, weight=1)

    app._btn_file = tonal_button(
        file_card, text="Выбрать файл", command=app._select_file, width=150,
    )
    app._btn_file.grid(row=0, column=0, padx=16, pady=14)

    app._lbl_file = label(file_card, text="Файл не выбран", anchor="w")
    app._lbl_file.grid(row=0, column=1, padx=(0, 12), pady=14, sticky="ew")

    app._btn_transcribe = primary_button(
        file_card, text="Транскрибировать",
        command=app._start_transcription, width=190, state="disabled",
    )
    app._btn_transcribe.grid(row=0, column=2, padx=16, pady=14)

    # --- Recorder card (row=3 — was row=2 before banner) ---
    rec_card = card(app)
    rec_card.grid(row=3, column=0, padx=16, pady=6, sticky="ew")
    rec_card.grid_columnconfigure(2, weight=1)

    app._btn_rec = ctk.CTkButton(
        rec_card, text="⏺  Запись", width=130, height=40, corner_radius=20,
        font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
        fg_color="#D93025", hover_color="#B3261E", text_color="#FFFFFF",
        command=app._toggle_recording,
    )
    app._btn_rec.grid(row=0, column=0, padx=16, pady=14)

    app._btn_rec_pause = tonal_button(
        rec_card, text="Пауза", command=app._toggle_pause, width=100,
        state="disabled",
    )
    app._btn_rec_pause.grid(row=0, column=1, padx=(0, 8), pady=14)

    app._lbl_rec_time = label(rec_card, text="00:00", size=22, color=TEXT_PRIMARY)
    app._lbl_rec_time.grid(row=0, column=2, padx=8, pady=14, sticky="w")

    app._rec_level = ctk.CTkProgressBar(
        rec_card, height=8, corner_radius=4, width=180,
        fg_color=PROGRESS_BG, progress_color=GREEN,
    )
    app._rec_level.grid(row=0, column=3, padx=(8, 16), pady=14, sticky="e")
    app._rec_level.set(0)

    # --- Persistent state vars ---
    # All settings StringVar/BooleanVar live on App as the source of truth.
    # The Settings dialog binds widgets to these vars on demand; closing
    # the dialog destroys widgets but leaves vars (and config.json state)
    # intact, so _start_transcription always reads consistent values.
    saved_lang = app._config.get("language", "Авто-определение")
    app._lang_var = ctk.StringVar(
        value=saved_lang if saved_lang in LANGUAGES else "Авто-определение",
    )
    # Diarization default ON for the cloud-only build — AssemblyAI's built-in
    # diarization is the whole point of choosing it. Clients shouldn't have to
    # toggle a checkbox to get speaker labels.
    app._diar_var = ctk.BooleanVar(value=True)
    saved_spk = app._config.get("speaker_count", "Авто")
    app._spk_count_var = ctk.StringVar(
        value=saved_spk if saved_spk in SPEAKER_COUNTS else "Авто",
    )
    app._normalize_var = ctk.BooleanVar(
        value=bool(app._config.get("normalize_audio", True)),
    )
    # RNNoise (arnndn) — opt-in, default off. When enabled, the cloud
    # path runs a pre-denoise pass via ffmpeg before sending audio to
    # the provider.
    app._denoise_var = ctk.BooleanVar(
        value=bool(app._config.get("denoise_audio", False)),
    )

    # Cloud provider state. Whisper-model / GPU-device pickers and the
    # cloud-enabled toggle were removed in the 2026-05-28 rip-out — cloud
    # is now the only mode, AssemblyAI is the default provider.
    app._cloud_provider_var = ctk.StringVar(
        value=app._config.get("cloud_provider", "AssemblyAI"),
    )
    # Visible API-key field — populated from the per-provider dict
    # for whichever provider is currently selected. _on_cloud_provider_changed
    # swaps it on dropdown change; _paste_cloud_api_key writes it back
    # into the dict for the active provider.
    app._cloud_api_key_var = ctk.StringVar(
        value=app._cloud_api_keys.get(
            app._cloud_provider_var.get(), ""
        ),
    )

    # Tasks pipeline (Phase 6.0+) — OpenRouter API key + default model slug.
    # The default model is persisted on every change via trace_add (the
    # CTk OptionMenu doesn't expose a `command=` for option selection that
    # reaches App's save_config, so a Var-level write trace is the cleanest
    # hook). The slug is stored as-is — Phase 6.4 may extend the curated
    # list with custom user-typed slugs prefixed `(custom) `.
    app._openrouter_key_var = ctk.StringVar(
        value=app._config.get("openrouter_api_key", ""),
    )
    app._openrouter_default_model_var = ctk.StringVar(
        value=app._config.get(
            "tasks_default_model", "google/gemini-3.5-flash",
        ),
    )
    app._openrouter_default_model_var.trace_add(
        "write", lambda *_: app._on_openrouter_default_model_changed(),
    )

    # Linear API key (Phase 6.0+). No team picker here — that's per-run
    # in the ExtractTasksDialog (Phase 6.1). Settings only persists the
    # key. The key is saved on Validate success, not on every keystroke
    # (same pattern as OpenRouter above and HF token below).
    app._linear_key_var = ctk.StringVar(
        value=app._config.get("linear_api_key", ""),
    )

    # Glide API key (Phase 6.4). Parallel backend to Linear. The board
    # picker lives in ExtractTasksDialog (Phase 6.4.1) — Settings just
    # persists the key. Same save-on-Validate-success discipline.
    app._glide_key_var = ctk.StringVar(
        value=app._config.get("glide_api_key", ""),
    )

    # Backend enabled flags (Phase 6.4). Per-backend preference whether
    # to expose it in the ExtractTasksDialog dropdown (Phase 6.4.1).
    # Default True for both — preserves prior behaviour for users who
    # haven't touched Settings since 6.0. Persisted instantly on toggle
    # (no save-on-Validate dance — these flags are standalone).
    app._linear_enabled_var = ctk.BooleanVar(
        value=bool(app._config.get("linear_enabled", True)),
    )
    app._glide_enabled_var = ctk.BooleanVar(
        value=bool(app._config.get("glide_enabled", True)),
    )

    # Google Drive (Phase 7.0). GDriveAuth instance + 3 Vars for the
    # Settings dialog to bind. load_tokens() at startup is safe even
    # when no token exists yet — returns False and leaves the instance
    # unsigned. Cost: one stat() on the token file (negligible).
    #
    # Import is local to keep app startup independent of Google libs
    # when the user hasn't enabled GDrive yet — sign_in() does the
    # heavy InstalledAppFlow import lazily; constructor + load_tokens
    # only touch stdlib + requests (already a top-level dep).
    from gdrive.auth import GDriveAuth
    app._gdrive_auth = GDriveAuth()
    app._gdrive_auth.load_tokens()
    app._gdrive_enabled_var = ctk.BooleanVar(
        value=bool(app._config.get("gdrive_enabled", False)),
    )
    app._gdrive_account_email_var = ctk.StringVar(
        value=app._gdrive_auth.get_account_email() or "",
    )
    app._gdrive_status_var = ctk.StringVar(
        value=app._compute_gdrive_status_text(),
    )

    # Appearance mode (light/dark/system). The actual ctk.set_appearance_mode
    # call already happened above with the saved value; this StringVar
    # just drives the Settings dialog dropdown and the change callback.
    saved_appearance_label = app._config.get("appearance_mode", "Системная")
    app._appearance_var = ctk.StringVar(
        value=saved_appearance_label
        if saved_appearance_label in APPEARANCE_MODES else "Системная",
    )

    # --- Run controls card ---
    # Slim card with only per-run controls: diarization toggle, speaker
    # count hint, and the Settings button. Everything else (language,
    # model, HF token, normalize, devices, dictionaries) lives in the
    # Settings dialog, opened via the button on the right.
    run_card = card(app)
    run_card.grid(row=4, column=0, padx=16, pady=6, sticky="ew")
    run_card.grid_columnconfigure(2, weight=1)

    app._diar_check = ctk.CTkCheckBox(
        run_card, text="Диаризация",
        variable=app._diar_var, command=app._toggle_diarization,
        font=ctk.CTkFont(family=FONT, size=13),
        text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
        border_color=BORDER, corner_radius=4,
        checkbox_height=20, checkbox_width=20,
    )
    app._diar_check.grid(row=0, column=0, padx=(16, 16), pady=14, sticky="w")

    # Speaker-count hint. Values map to pyannote hints:
    #   "Авто"  → no hint (pyannote auto-detects)
    #   "2".."4"→ num_speakers=K (exact hint; ~2× DER improvement when correct)
    #   "5+"    → min_speakers=5 (open upper bound)
    # Greyed out when diarize is off.
    label(run_card, "Число спикеров").grid(row=0, column=1, padx=(0, 8), pady=14)
    app._spk_count_menu = option_menu(
        run_card, app._spk_count_var, list(SPEAKER_COUNTS.keys()),
        command=app._on_speaker_count_changed, state="disabled",
    )
    app._spk_count_menu.grid(row=0, column=2, padx=(0, 12), pady=14, sticky="w")

    # Monitor button: opens the non-modal system stats window.
    # Sits to the LEFT of Settings — same row, two clickable buttons
    # at the right edge of the run card.
    app._btn_monitor = tonal_button(
        run_card, text="Монитор",
        command=app._open_monitor_dialog, width=110,
    )
    app._btn_monitor.grid(row=0, column=3, padx=(0, 8), pady=14, sticky="e")

    app._btn_settings = tonal_button(
        run_card, text="Настройки",
        command=app._open_settings_dialog, width=140,
    )
    app._btn_settings.grid(row=0, column=4, padx=(0, 16), pady=14, sticky="e")

    # --- Progress bar ---
    app._progress = ctk.CTkProgressBar(
        app, height=4, corner_radius=2,
        fg_color=PROGRESS_BG, progress_color=BLUE,
    )
    app._progress.grid(row=6, column=0, padx=16, pady=(10, 0), sticky="ew")
    app._progress.set(0)

    # --- Text result ---
    app._textbox = ctk.CTkTextbox(
        app, wrap="word", corner_radius=16,
        fg_color=SURFACE, text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT, size=14),
    )
    app._textbox.grid(row=7, column=0, padx=16, pady=(8, 8), sticky="nsew")

    # --- Action buttons ---
    btn_frame = ctk.CTkFrame(app, fg_color="transparent")
    btn_frame.grid(row=8, column=0, padx=16, pady=(0, 14), sticky="ew")

    app._btn_save = tonal_button(
        btn_frame, text="Сохранить (TXT/SRT/VTT)", command=app._save_txt,
        width=200, state="disabled",
    )
    app._btn_save.grid(row=0, column=0, padx=(0, 8), pady=4)

    app._btn_copy = tonal_button(
        btn_frame, text="Копировать", command=app._copy_text,
        width=150, state="disabled",
    )
    app._btn_copy.grid(row=0, column=1, padx=8, pady=4)

    app._btn_extract_tasks = tonal_button(
        btn_frame, text="Извлечь задачи",
        command=app._open_extract_tasks_dialog,
        width=160, state="disabled",
    )
    app._btn_extract_tasks.grid(row=0, column=2, padx=8, pady=4)

    app._btn_history = tonal_button(
        btn_frame, text="История", command=app._open_history_dialog,
        width=130,
    )
    app._btn_history.grid(row=0, column=3, padx=8, pady=4)

    app._btn_cutter = tonal_button(
        btn_frame, text="Audio Cutter", command=app._open_cutter,
        width=140,
    )
    app._btn_cutter.grid(row=0, column=4, padx=8, pady=4)
