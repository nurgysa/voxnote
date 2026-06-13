"""Widget-tree constructor for the Settings dialog.

Extracted from ``ui/dialogs/settings.py`` (widget-tree split, 2026-06-10
spec). Mirrors the ``ui/app/builder.py`` contract: each ``build_*_section``
free function takes the live ``SettingsDialog`` instance, creates that
section's widgets inside ``parent`` (a per-tab scroll frame), and sets any
captured refs on ``dialog`` under their original names
(``dialog._lang_menu``, ``dialog._cloud_api_key_entry``, …) so the banner
jump / status handlers that remain on the class keep working. No business
logic lives here; handlers and workers stay on ``SettingsDialog``.

Import discipline (cycle guard): this module may import theme, ui.widgets,
ui.app.constants, providers, settings_helpers and utils — never
``ui.dialogs.settings`` and never module-level ``ui.app`` (the
``APPEARANCE_MODES`` import stays lazy inside ``build_appearance_section``).
"""

from __future__ import annotations

import customtkinter as ctk

from providers import PROVIDERS
from theme import (
    BLUE,
    BLUE_DIM,
    BORDER,
    FONT,
    INPUT_BG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.app.constants import LANGUAGES
from ui.dialogs.settings_helpers import (
    format_glide_success,
    format_linear_success,
    format_openrouter_success,
    format_trello_success,
)
from ui.widgets import (
    api_key_row,
    card,
    label,
    option_menu,
    primary_button,
    text_entry,
    tonal_button,
)
from utils import get_meetings_dir, save_config

# Curated dropdown for OpenRouter default model. Slug → display label.
# Display label keeps the slug visible — power users recognize 'sonnet-4.5'
# faster than 'Anthropic Claude Sonnet 4.5 (latest)'.
_CURATED_MODELS = {
    "google/gemini-3.5-flash":        "google/gemini-3.5-flash",
}


def section_card(dialog, parent, title: str, row: int) -> ctk.CTkFrame:
    """A titled card. Returns the inner content frame (already gridded)."""
    wrapper = card(parent)
    wrapper.grid(row=row, column=0, padx=4, pady=8, sticky="ew")
    wrapper.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(
        wrapper, text=title,
        font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
        text_color=TEXT_SECONDARY,
    ).grid(row=0, column=0, padx=16, pady=(12, 4), sticky="w")
    inner = ctk.CTkFrame(wrapper, fg_color="transparent")
    inner.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="ew")
    inner.grid_columnconfigure(1, weight=1)
    return inner


def build_appearance_section(dialog, parent) -> None:
    # Lazy import — APPEARANCE_MODES lives in ui.app, importing at
    # module-load would create a circular dependency.
    from ui.app import APPEARANCE_MODES

    section = section_card(dialog, parent, "Внешний вид", row=0)

    label(section, "Тема").grid(
        row=0, column=0, padx=(4, 8), pady=6, sticky="w",
    )
    option_menu(
        section, dialog._parent._appearance_var, list(APPEARANCE_MODES.keys()),
        command=dialog._parent._on_appearance_changed,
    ).grid(row=0, column=1, padx=4, pady=6, sticky="w")
    label(
        section,
        "«Системная» следует за настройкой Windows (Light/Dark mode).",
        anchor="w",
    ).grid(row=1, column=0, columnspan=2, padx=4, pady=(0, 4), sticky="w")


def build_transcription_section(dialog, parent) -> None:
    section = section_card(dialog, parent, "Транскрипция", row=1)

    label(section, "Язык").grid(row=0, column=0, padx=(4, 8), pady=6, sticky="w")
    # Capture ref so the banner's _jump_to_lang can focus_set() it.
    dialog._lang_menu = option_menu(
        section, dialog._parent._lang_var, list(LANGUAGES.keys()),
        command=dialog._parent._on_language_changed,
    )
    dialog._lang_menu.grid(row=0, column=1, padx=4, pady=6, sticky="w")


def build_audio_section(dialog, parent) -> None:
    section = section_card(dialog, parent, "Аудио", row=2)
    # No loudness-normalization toggle here on purpose: the cloud path
    # hardcodes ensure_wav(normalize=False) — provider gateways apply
    # their own gain normalization, so a checkbox would control nothing.

    # RNNoise (arnndn) — opt-in noise suppression. Default off; the
    # neural denoiser can clip soft consonants on already-clean
    # recordings. ~85 KB model lazy-downloaded on first use.
    denoise_check = ctk.CTkCheckBox(
        section, text="Подавлять шум (RNNoise — для записей с фоном)",
        variable=dialog._parent._denoise_var,
        command=dialog._parent._on_denoise_changed,
        font=ctk.CTkFont(family=FONT, size=13),
        text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
        border_color=BORDER, corner_radius=4,
        checkbox_height=20, checkbox_width=20,
    )
    denoise_check.grid(
        row=0, column=0, columnspan=2, padx=4, pady=6, sticky="w",
    )


def build_meetings_section(dialog, parent) -> None:
    """Meetings folder picker — path entry + Выбрать + Default + stats.

    On path change: triggers MigrationPromptDialog if the current
    folder has entries (mode="settings"). Otherwise silent save.
    """
    section = section_card(dialog, parent, "Встречи", row=4)

    label(section, "Папка хранения").grid(
        row=0, column=0, padx=(4, 8), pady=6, sticky="w",
    )

    dialog._meetings_path_var = ctk.StringVar(value=get_meetings_dir())
    dialog._meetings_entry = ctk.CTkEntry(
        section, textvariable=dialog._meetings_path_var,
        height=36, corner_radius=10,
        border_color=BORDER, border_width=1,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT, size=12),
        state="readonly",
    )
    dialog._meetings_entry.grid(
        row=0, column=1, columnspan=2, padx=4, pady=6, sticky="ew",
    )

    tonal_button(
        section, text="\U0001f4c1 Выбрать",
        command=dialog._on_pick_meetings_folder, width=130,
    ).grid(row=0, column=3, padx=(4, 4), pady=6)

    tonal_button(
        section, text="↻ Default",
        command=dialog._on_reset_meetings_folder, width=120,
    ).grid(row=1, column=3, padx=(4, 4), pady=(0, 6))

    # Stats label — refreshed on dialog open and after path change
    dialog._meetings_stats_label = label(section, "", anchor="w")
    dialog._meetings_stats_label.grid(
        row=1, column=0, columnspan=3, padx=4, pady=(0, 6), sticky="w",
    )
    dialog._refresh_meetings_stats()


def build_dictionaries_section(dialog, parent) -> None:
    section = section_card(dialog, parent, "Словари", row=5)

    tonal_button(
        section, text="Словарь терминов",
        command=dialog._parent._open_terms_dialog, width=200,
    ).grid(row=0, column=0, padx=4, pady=6, sticky="w")
    # Compact summary of what's saved — same source as the main-window
    # label (kept in sync via _update_terms_label, which we reuse below).
    dialog._terms_summary = label(section, "", anchor="w")
    dialog._terms_summary.grid(row=0, column=1, padx=(8, 4), pady=6, sticky="ew")

    dialog._refresh_summaries()


def build_cloud_section(dialog, parent) -> None:
    """Cloud STT provider + API key + privacy + pricing disclosure.

    Key handling via api_key_row; «Проверить» runs the selected
    provider's cheap auth check (validate_key) on the row's worker
    thread. Provider dropdown stays as a separate row because its
    callback wires into the first-run banner's mixed-lang condition.
    Captures dialog._cloud_api_key_entry so the banner can focus_set() it.
    """
    section = section_card(dialog, parent, "Облачное распознавание", row=3)

    # Provider dropdown
    label(section, "Провайдер").grid(
        row=0, column=0, padx=(4, 8), pady=6, sticky="w",
    )
    option_menu(
        section, dialog._parent._cloud_provider_var, list(PROVIDERS.keys()),
        command=dialog._parent._on_cloud_provider_changed,
    ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

    def _on_validate(key: str) -> dict:
        # Lazy import — keeps providers/ HTTP plumbing off the
        # dialog-construction path. Dispatches on whatever provider
        # is selected at click time; an empty key raises the
        # provider's own Russian "ключ не задан" ProviderError.
        from providers import get_provider
        provider = get_provider(dialog._parent._cloud_provider_var.get(), key)
        return provider.validate_key()

    def _persist(key: str, _info: dict) -> None:
        name = dialog._parent._cloud_provider_var.get()
        dialog._parent._cloud_api_keys[name] = key
        dialog._parent._config["cloud_api_keys"] = dialog._parent._cloud_api_keys
        save_config(dialog._parent._config)

    # API key row — capture entry ref so the global first-run banner
    # can focus_set() it on click.
    refs = api_key_row(
        section,
        label_text="API ключ",
        key_var=dialog._parent._cloud_api_key_var,
        placeholder="API ключ провайдера",
        on_validate=_on_validate,
        on_key_persisted=_persist,
        format_success=lambda _info: "✓ Ключ действителен",
        row=1,
    )
    dialog._cloud_api_key_entry = refs["entry"]

    # Disclosure: audio leaves the user's machine and ends up on a
    # third-party server. Surfacing this in the cloud section is the
    # cheapest mitigation. Placed AFTER the key field so it reads
    # as context for the field it applies to.
    label(
        section,
        "⚠ Аудио загружается на сервер провайдера. "
        "Не используй для конфиденциальных записей.",
        anchor="w",
    ).grid(row=3, column=0, columnspan=4, padx=4, pady=(2, 6), sticky="w")
    # Static price summary. Cheapest with diarization first.
    label(
        section,
        "ℹ Цены с диаризацией: AssemblyAI ~$0.17/ч • "
        "Deepgram ~$0.43/ч • Gladia ~$0.61/ч • "
        "Speechmatics ~$1.04/ч.",
        anchor="w",
    ).grid(row=4, column=0, columnspan=4, padx=4, pady=(0, 4), sticky="w")


def build_openrouter_section(dialog, parent) -> None:
    """OpenRouter API key + default model.

    Key handling delegated to ui.widgets.api_key_row (entry + eye-toggle
    + Проверить + status). Default-model dropdown stays as a separate
    row below.
    """
    section = section_card(dialog, parent, "OpenRouter", row=0)

    def _persist(key: str, _info: dict) -> None:
        dialog._parent._config["openrouter_api_key"] = key
        save_config(dialog._parent._config)

    def _on_validate(key: str) -> dict:
        # Lazy import — keeps tasks/openrouter_client (and transitively
        # requests) off the dialog-construction path.
        from tasks.openrouter_client import OpenRouterClient
        client = OpenRouterClient(key)
        try:
            return client.validate_key()
        finally:
            client.close()

    refs = api_key_row(
        section,
        label_text="API ключ",
        key_var=dialog._parent._openrouter_key_var,
        placeholder="sk-or-...",
        on_validate=_on_validate,
        on_key_persisted=_persist,
        format_success=format_openrouter_success,
        row=0,
    )
    dialog._openrouter_status = refs["status"]

    # Default model dropdown (unchanged — separate row below the key)
    label(section, "Модель по умолчанию").grid(
        row=2, column=0, padx=(4, 8), pady=6, sticky="w",
    )
    option_menu(
        section, dialog._parent._openrouter_default_model_var,
        list(_CURATED_MODELS.keys()),
    ).grid(row=2, column=1, columnspan=2, padx=4, pady=6, sticky="ew")


def build_linear_section(dialog, parent) -> None:
    """Linear API key + connection status.

    enable-checkbox + API key handling delegated to api_key_row.
    No team picker here — that's per-run in ExtractTasksDialog.
    """
    section = section_card(dialog, parent, "Linear", row=1)

    def _persist(key: str, _info: dict) -> None:
        dialog._parent._config["linear_api_key"] = key
        save_config(dialog._parent._config)

    def _on_validate(key: str) -> dict:
        from tasks.linear_client import LinearClient
        client = LinearClient(key)
        try:
            return client.validate_key()
        finally:
            client.close()

    refs = api_key_row(
        section,
        label_text="API ключ",
        key_var=dialog._parent._linear_key_var,
        placeholder="lin_api_...",
        on_validate=_on_validate,
        on_key_persisted=_persist,
        enabled_var=dialog._parent._linear_enabled_var,
        enabled_label="Использовать Linear",
        on_enabled_changed=dialog._parent._on_linear_enabled_changed,
        format_success=format_linear_success,
        row=0,
    )
    dialog._linear_status = refs["status"]


def build_glide_section(dialog, parent) -> None:
    """Glide API key + connection status (Phase 6.4).

    Mirrors the Linear section pattern (enable-checkbox + validate
    through api_key_row).
    """
    section = section_card(dialog, parent, "Glide", row=2)

    def _persist(key: str, _info: dict) -> None:
        dialog._parent._config["glide_api_key"] = key
        save_config(dialog._parent._config)

    def _on_validate(key: str) -> dict:
        from tasks.glide_client import GlideClient
        client = GlideClient(key)
        try:
            return client.validate_key()
        finally:
            client.close()

    refs = api_key_row(
        section,
        label_text="API ключ",
        key_var=dialog._parent._glide_key_var,
        placeholder="glide_pk_<workspace>_...",
        on_validate=_on_validate,
        on_key_persisted=_persist,
        enabled_var=dialog._parent._glide_enabled_var,
        enabled_label="Использовать Glide",
        on_enabled_changed=dialog._parent._on_glide_enabled_changed,
        format_success=format_glide_success,
        row=0,
    )
    dialog._glide_status = refs["status"]


def build_trello_section(dialog, parent) -> None:
    """Trello API key + token + connection status (spec 2026-05-29).

    Trello needs two secrets (key + token). The shared api_key_row
    helper renders one masked field, so we compose two calls:
    - key row: enable-checkbox + masked key field (no Validate)
    - token row: masked token field + Validate + status badge

    api_key_row only persists on Validate success, and only the token
    row has a Validate button — so the token row's _persist saves BOTH
    credentials, and its _on_validate reads BOTH vars.
    """
    section = section_card(dialog, parent, "Trello", row=3)

    key_frame = ctk.CTkFrame(section, fg_color="transparent")
    key_frame.grid(row=0, column=0, sticky="ew")
    key_frame.grid_columnconfigure(1, weight=1)

    token_frame = ctk.CTkFrame(section, fg_color="transparent")
    token_frame.grid(row=1, column=0, sticky="ew")
    token_frame.grid_columnconfigure(1, weight=1)

    def _persist(_token: str, _info: dict) -> None:
        dialog._parent._config["trello_api_key"] = dialog._parent._trello_key_var.get().strip()
        dialog._parent._config["trello_token"] = dialog._parent._trello_token_var.get().strip()
        save_config(dialog._parent._config)

    def _on_validate(token: str) -> dict:
        from tasks.trello_client import TrelloClient
        api_key = dialog._parent._trello_key_var.get().strip()
        client = TrelloClient(api_key, token)
        try:
            return client.validate_key()
        finally:
            client.close()

    # Key row — owns the enable-checkbox; no Validate button.
    api_key_row(
        key_frame,
        label_text="API ключ",
        key_var=dialog._parent._trello_key_var,
        placeholder="(ключ Trello — trello.com/app-key)",
        enabled_var=dialog._parent._trello_enabled_var,
        enabled_label="Использовать Trello",
        on_enabled_changed=dialog._parent._on_trello_enabled_changed,
        row=0,
    )

    # Token row — owns Validate + status; persists both credentials.
    refs = api_key_row(
        token_frame,
        label_text="Токен",
        key_var=dialog._parent._trello_token_var,
        placeholder="(токен Trello)",
        on_validate=_on_validate,
        on_key_persisted=_persist,
        format_success=format_trello_success,
        row=0,
    )
    dialog._trello_status = refs["status"]


def build_dedup_section(dialog, parent) -> None:
    """Dedup on/off — the only user-facing dedup knob (spec 2026-06-11).

    dedup_fuzzy_high / dedup_fuzzy_low stay config-only expert knobs
    (tasks.dedup.resolve_thresholds guards garbage). The consumer gate
    is the Extract dialog reading config.get("dedup_enabled", True).
    """
    section = section_card(dialog, parent, "Дубли задач", row=4)

    dialog._dedup_enabled_var = ctk.BooleanVar(
        value=bool(dialog._parent._config.get("dedup_enabled", True)),
    )

    def _on_toggled() -> None:
        dialog._parent._config["dedup_enabled"] = bool(
            dialog._dedup_enabled_var.get(),
        )
        save_config(dialog._parent._config)

    ctk.CTkCheckBox(
        section,
        text="Проверять дубли перед отправкой (комментарий вместо новой карточки)",
        variable=dialog._dedup_enabled_var,
        command=_on_toggled,
        font=ctk.CTkFont(family=FONT, size=13),
        text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
        border_color=BORDER, corner_radius=4,
        checkbox_height=20, checkbox_width=20,
    ).grid(row=0, column=0, columnspan=2, padx=4, pady=6, sticky="w")


def build_gdrive_section(dialog, parent) -> None:
    """Google Drive backup: sign-in/out + status badge.

    Phase 7.0 surface only — no backup-now button (7.1), no
    frequency dropdown (7.3), no audio opt-in (7.4). Adding those
    widgets later just extends this method.

    Threading lives on the class: SettingsDialog._handle_gdrive_signin
    runs sign_in() in a daemon thread and routes results back to the Tk
    loop via dialog.after(0, ...); this builder only creates the widgets
    and sets their initial state.
    """
    section = section_card(dialog, parent, "Google Drive", row=0)

    # Status row — badge bound to the App's _gdrive_status_var.
    label(section, "Статус").grid(
        row=0, column=0, padx=(4, 8), pady=6, sticky="w",
    )
    dialog._gdrive_status_label = ctk.CTkLabel(
        section,
        textvariable=dialog._parent._gdrive_status_var,
        anchor="w",
        text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT, size=12),
    )
    dialog._gdrive_status_label.grid(
        row=0, column=1, columnspan=2, padx=4, pady=6, sticky="ew",
    )

    # Action row — Войти + Выйти (one of them disabled at any time).
    dialog._gdrive_signin_btn = primary_button(
        section, text="Войти через Google",
        command=dialog._handle_gdrive_signin, width=180,
    )
    dialog._gdrive_signin_btn.grid(
        row=1, column=0, columnspan=2, padx=4, pady=6, sticky="w",
    )

    dialog._gdrive_signout_btn = tonal_button(
        section, text="Выйти",
        command=dialog._handle_gdrive_signout, width=100,
    )
    dialog._gdrive_signout_btn.grid(row=1, column=2, padx=(4, 4), pady=6, sticky="e")

    # Backup-now row (Phase 7.1) — button + status label. Status is
    # local (not bound to a parent Var) because backup status is a
    # transient dialog-only concern; persistence of
    # gdrive_last_backup happens on success via the mixin callback.
    dialog._gdrive_backup_btn = tonal_button(
        section, text="Сделать backup сейчас",
        command=dialog._handle_gdrive_backup_now, width=200,
    )
    dialog._gdrive_backup_btn.grid(
        row=2, column=0, columnspan=2, padx=4, pady=6, sticky="w",
    )
    dialog._gdrive_backup_status = label(section, "", anchor="w")
    dialog._gdrive_backup_status.grid(
        row=2, column=2, padx=(8, 4), pady=6, sticky="ew",
    )

    # Initial button enabled-state reflects current sign-in state.
    dialog._refresh_gdrive_button_state()


def build_diagnostics_section(dialog, parent) -> None:
    """Diagnostics export: bundle logs/ + a redacted config.json into a
    zip the user can send to support. No telemetry backend (D4) — the
    user picks where to save and ships it themselves."""
    section = section_card(dialog, parent, "Диагностика", row=1)
    label(section, "Логи").grid(
        row=0, column=0, padx=(4, 8), pady=6, sticky="w",
    )
    dialog._send_log_btn = tonal_button(
        section, text="Сохранить лог для отправки",
        command=dialog._handle_send_log, width=240,
    )
    dialog._send_log_btn.grid(row=0, column=1, padx=4, pady=6, sticky="w")
    dialog._send_log_status = label(section, "", anchor="w")
    dialog._send_log_status.grid(
        row=0, column=2, padx=(8, 4), pady=6, sticky="ew",
    )


def build_hermes_section(dialog, parent) -> None:
    """Hermes Agent webhook: enable + URL + secret + test delivery (spec 2026-06-13).

    GUI surface for the #146 webhook (integrations/hermes/). The capability
    (client + emit-after-transcription) already ships; this section just lets
    the user enable and configure it from the GUI instead of editing
    config.json. timeout_seconds / routing_hint stay config-only expert knobs.

    Persistence mirrors build_dedup_section: dialog-local vars, immediate
    save_config to dialog._parent._config (the live object _emit_hermes_event
    reads via get_hermes_webhook_config). Saved on toggle, on field FocusOut,
    and on a successful «Проверить». Webhook is opt-in — default OFF.
    """
    section = section_card(dialog, parent, "Hermes Agent (вебхук)", row=5)

    cfg = dialog._parent._config
    dialog._hermes_webhook_enabled_var = ctk.BooleanVar(
        value=bool(cfg.get("hermes_webhook_enabled", False)),
    )
    dialog._hermes_webhook_url_var = ctk.StringVar(
        value=cfg.get("hermes_webhook_url")
        or "http://localhost:8644/webhooks/audio-transcribed",
    )
    dialog._hermes_webhook_secret_var = ctk.StringVar(
        value=cfg.get("hermes_webhook_secret", ""),
    )

    def _persist_hermes(*_event) -> None:
        # *_event swallows the Tk event object passed by <FocusOut> binds;
        # the checkbox command and validate callback pass nothing.
        c = dialog._parent._config
        c["hermes_webhook_enabled"] = bool(dialog._hermes_webhook_enabled_var.get())
        c["hermes_webhook_url"] = dialog._hermes_webhook_url_var.get().strip()
        c["hermes_webhook_secret"] = dialog._hermes_webhook_secret_var.get()
        save_config(c)

    # row 0 — enable checkbox (persists immediately, like dedup)
    ctk.CTkCheckBox(
        section,
        text="Отправлять расшифровки в Hermes",
        variable=dialog._hermes_webhook_enabled_var,
        command=_persist_hermes,
        font=ctk.CTkFont(family=FONT, size=13),
        text_color=TEXT_PRIMARY, fg_color=BLUE, hover_color=BLUE_DIM,
        border_color=BORDER, corner_radius=4,
        checkbox_height=20, checkbox_width=20,
    ).grid(row=0, column=0, columnspan=4, padx=4, pady=(2, 8), sticky="w")

    # row 1 — URL (plain, non-secret). Labelled (not placeholder-only:
    # CTkEntry hides placeholder_text once a textvariable is set).
    label(section, "URL вебхука").grid(
        row=1, column=0, padx=(4, 8), pady=6, sticky="w",
    )
    url_entry = text_entry(
        section,
        textvariable=dialog._hermes_webhook_url_var,
        placeholder="http://localhost:8644/webhooks/audio-transcribed",
    )
    url_entry.grid(row=1, column=1, columnspan=3, padx=4, pady=6, sticky="ew")
    url_entry.bind("<FocusOut>", _persist_hermes)

    # rows 2-3 — secret (masked) + eye-toggle + «Проверить» + status.
    def _test(secret: str) -> dict:
        # Build an enabled config from the live URL + the entered secret and
        # POST one marked test event through the shipped client. Runs on
        # api_key_row's worker thread; api_key_row marshals UI updates.
        from integrations.hermes.client import (
            HermesWebhookConfig,
            emit_audio_transcribed_event,
        )
        c = dialog._parent._config
        hermes_cfg = HermesWebhookConfig(
            enabled=True,
            url=dialog._hermes_webhook_url_var.get().strip(),
            secret=secret,
            timeout_seconds=float(c.get("hermes_webhook_timeout_seconds", 10) or 10),
            routing_hint=c.get("hermes_webhook_routing_hint") or "obsidian_inbox",
        )
        result = emit_audio_transcribed_event(
            config=hermes_cfg,
            transcript_text="[ТЕСТ] Проверка связи audio-transcriber → Hermes",
            provider="(test)",
        )
        if not result.sent:
            # api_key_row paints the raised message as «✗ …» (red).
            raise RuntimeError(result.error or f"HTTP {result.status_code}")
        return {"status_code": result.status_code}

    refs = api_key_row(
        section,
        label_text="Секрет (HMAC)",
        key_var=dialog._hermes_webhook_secret_var,
        placeholder="(HMAC secret)",
        on_validate=_test,
        on_key_persisted=lambda _secret, _info: _persist_hermes(),
        format_success=lambda info: f"✓ Доставлено (HTTP {info['status_code']})",
        row=2,
    )
    refs["entry"].bind("<FocusOut>", _persist_hermes)
    dialog._hermes_status = refs["status"]

    # row 4 — help line
    label(
        section,
        "ℹ Событие audio.transcribed уходит автоматически после успешной "
        "транскрипции. Маршрут настраивается на стороне Hermes (см. docs).",
        anchor="w",
    ).grid(row=4, column=0, columnspan=4, padx=4, pady=(2, 6), sticky="w")
