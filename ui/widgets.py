"""Shared CustomTkinter widget factories.

Three styles of button (primary blue, tonal blue-on-surface, danger red),
a card frame, a labeled text helper, the option-menu used everywhere for
dropdowns, and a text entry. Keeps the theme palette confined to one
import surface — palette changes don't ripple through every dialog.
"""

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


def card(parent, **kwargs) -> ctk.CTkFrame:
    """A surface-tinted rounded frame used for grouping controls."""
    return ctk.CTkFrame(
        parent, fg_color=SURFACE, corner_radius=16, border_width=0, **kwargs,
    )


def label(parent, text, size=13, color=TEXT_SECONDARY, **kwargs) -> ctk.CTkLabel:
    """Plain text label using the project font and the secondary color by default."""
    return ctk.CTkLabel(
        parent, text=text,
        font=ctk.CTkFont(family=FONT, size=size),
        text_color=color, **kwargs,
    )


def primary_button(parent, text, command, width=160, **kwargs) -> ctk.CTkButton:
    """Solid blue pill button — for the main action in a frame (Транскрибировать, Готово)."""
    return ctk.CTkButton(
        parent, text=text, command=command, width=width,
        height=40, corner_radius=20,
        font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
        fg_color=BLUE, hover_color=BLUE_DIM, text_color="#FFFFFF",
        **kwargs,
    )


def tonal_button(parent, text, command, width=130, **kwargs) -> ctk.CTkButton:
    """Quieter blue-on-surface button — for secondary actions (Открыть, Копировать)."""
    return ctk.CTkButton(
        parent, text=text, command=command, width=width,
        height=40, corner_radius=20,
        font=ctk.CTkFont(family=FONT, size=13),
        fg_color=BLUE_SURFACE, hover_color=SURFACE_BRIGHT,
        text_color="#8AB4F8",
        **kwargs,
    )


def danger_button(parent, text, command, width=130, **kwargs) -> ctk.CTkButton:
    """Solid red button — for destructive or recording actions."""
    return ctk.CTkButton(
        parent, text=text, command=command, width=width,
        height=40, corner_radius=20,
        font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
        fg_color="#D93025", hover_color="#B3261E", text_color="#FFFFFF",
        **kwargs,
    )


def option_menu(parent, variable, values, command=None, width=175, **kwargs) -> ctk.CTkOptionMenu:
    """Themed dropdown — the same styling repeated 3× in the original app.py."""
    return ctk.CTkOptionMenu(
        parent, variable=variable, values=values, command=command,
        width=width, height=36, corner_radius=10,
        font=ctk.CTkFont(family=FONT, size=13),
        fg_color=INPUT_BG, button_color=BORDER, button_hover_color=BLUE_SURFACE,
        text_color=TEXT_PRIMARY, dropdown_fg_color=SURFACE_BRIGHT,
        dropdown_text_color=TEXT_PRIMARY, dropdown_hover_color=BLUE_SURFACE,
        **kwargs,
    )


def text_entry(parent, textvariable=None, placeholder="", **kwargs) -> ctk.CTkEntry:
    """Themed text input."""
    return ctk.CTkEntry(
        parent, textvariable=textvariable, height=36,
        corner_radius=10, border_color=BORDER, border_width=1,
        fg_color=INPUT_BG, text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT, size=13),
        placeholder_text=placeholder, **kwargs,
    )


def dialog_chrome(toplevel: ctk.CTkToplevel, title: str) -> ctk.CTkFrame:
    """Configure a CTkToplevel with project background + a header row.

    Returns the header frame so the caller can pack additional widgets
    (e.g. a count label on the right). The dialog itself gets ``fg_color``
    set, ``transient(parent)``, and ``grab_set()``.
    """
    toplevel.title(title)
    toplevel.configure(fg_color=BG)
    parent = toplevel.master
    if parent is not None:
        toplevel.transient(parent)
    toplevel.grab_set()

    header = ctk.CTkFrame(toplevel, fg_color=SURFACE, corner_radius=0, height=48)
    ctk.CTkLabel(
        header, text=title,
        font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
        text_color=TEXT_PRIMARY,
    ).grid(row=0, column=0, padx=20, pady=12, sticky="w")
    header.grid_columnconfigure(0, weight=1)
    return header


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
    + eye-toggle + (optional) Validate button + status label.

    Grids itself into ``parent`` starting at row ``row``. Returns a dict
    with refs {"entry", "validate_btn", "status"} so callers can wire
    external focus (e.g. the Settings first-run banner focuses entry).

    Threading: ``on_validate(key)`` is a BLOCKING caller-supplied function
    (typically a network call). The helper runs it in a daemon thread and
    marshals all UI updates back via ``parent.after(0, ...)`` — CTk widgets
    are not thread-safe, direct .configure() from a worker thread causes
    intermittent rendering bugs on Windows.

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
        show="•",  # bullet — masked-entry standard
    )
    entry.grid(row=current_row, column=1, padx=4, pady=6, sticky="ew")
    refs["entry"] = entry

    # Eye-toggle (small tonal button)
    eye_state = {"masked": True}

    def _toggle_eye() -> None:
        eye_state["masked"] = not eye_state["masked"]
        entry.configure(show="•" if eye_state["masked"] else "")

    tonal_button(parent, text="\U0001f441", command=_toggle_eye, width=40).grid(
        row=current_row, column=2, padx=(4, 4), pady=6,
    )

    if on_validate is None:
        # No validate button → done. status slot stays None.
        return refs

    # Validate button + status label
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
