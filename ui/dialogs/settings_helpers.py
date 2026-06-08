"""Pure helpers for the Settings dialog.

Stdlib-only leaf module — the banner decision tree (and, in a later WS-4 PR,
the per-backend success formatters) live here so they're unit-testable on
Linux CI without the dialog's Tk/sounddevice import chain. The dialog injects
its LANGUAGES / PROVIDERS tables, so this module imports neither.
"""
from __future__ import annotations

_STT_KEY_MISSING = "⚠ Введите ключ провайдера STT (вкладка «Транскрипция») →"


def compute_banner_state(
    cloud_key: str,
    lang_label: str,
    provider_name: str,
    languages: dict,
    providers: dict,
) -> tuple[str | None, str]:
    """Pick the highest-priority Settings banner state.

    Verbatim FSM from the dialog's _update_banner, minus the widget calls.
    Returns ``(action, text)``:
      - ("stt", text) — cloud STT key empty (after strip). Priority 1.
      - ("lang", text) — language is "mixed" AND the selected provider's
        class declares ``supports_mixed = False``. Priority 2.
      - (None, "")     — no actionable issue; caller hides the banner.

    ``languages`` maps a language label → code; ``providers`` maps a provider
    name → its class (only ``.supports_mixed`` is read). Both are injected so
    this stays a pure, table-agnostic leaf. The caller applies the
    (always-RED) colour + grid()/grid_remove() for the returned action.
    """
    if not (cloud_key or "").strip():
        return ("stt", _STT_KEY_MISSING)

    if languages.get(lang_label) == "mixed":
        provider_cls = providers.get(provider_name)
        if provider_cls is not None and not provider_cls.supports_mixed:
            return (
                "lang",
                f"⚠ {provider_name} не поддерживает «Смешанный "
                f"(KZ+RU+EN)». Выберите другой провайдер или язык →",
            )

    return (None, "")


# ── per-backend validation success formatters (dict → status string) ──
# Each takes the dict returned by the backend client's validate_key() and
# renders the green "✓ ..." line shown next to the API-key row. Pure
# branch logic (fallback chains, Russian noun-count, is-not-None vs
# truthiness) — the dialog passes these as the api_key_row format_success
# callback.


def format_openrouter_success(info: dict) -> str:
    """OpenRouter: account balance if the API returned one, else the plan
    label (or 'unlimited'). Uses ``is not None`` so a $0.00 balance still
    renders rather than falling through to the label."""
    balance = info.get("balance_remaining")
    if balance is not None:
        return f"✓ Активен (баланс: ${balance:.2f})"
    return f"✓ Активен ({info.get('label') or 'unlimited'})"


def format_linear_success(info: dict) -> str:
    """Linear: viewer name, email fallback, else '(unknown)'."""
    name = info.get("name") or info.get("email") or "(unknown)"
    return f"✓ Подключено: {name}"


def format_glide_success(info: dict) -> str:
    """Glide: board count + a sample of board names.

    Russian noun-count is awkward; "{n} досок" is correct enough for all
    sizes. board_count / sample_names are guaranteed by validate_key().
    """
    count = info["board_count"]
    sample = info["sample_names"]
    base = f"✓ Подключено: {count} досок"
    if sample:
        base += f" ({', '.join(sample)})"
    return base


def format_trello_success(info: dict) -> str:
    """Trello: member name, else '(unknown)'."""
    return f"✓ Подключено: {info.get('name', '(unknown)')}"
