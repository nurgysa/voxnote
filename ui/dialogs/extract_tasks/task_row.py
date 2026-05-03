"""``_TaskRow`` widget — one row in the Extract Tasks dialog's left list.

Self-contained Tk widget: holds a reference to a ``tasks.schema.Task`` and
two callbacks (on_select, on_toggle). Status badges (PENDING ↔ SENDING ↔
SENT ↔ FAILED) are rendered in place of the checkbox once a send begins.

Lifted out of the monolithic dialog file so the row's render logic can
evolve independently of the dialog's wiring.
"""
from __future__ import annotations

import tkinter as tk
import webbrowser

import customtkinter as ctk

from theme import (
    BLUE_DIM,
    BORDER,
    FONT,
    GREEN,
    RED,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

from .constants import _PRIORITY_GLYPHS


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
        # If the task has been sent to Linear, a click opens the issue page;
        # the editor form is irrelevant at that point.
        from tasks.schema import TaskStatus
        if (
            self._task.status is TaskStatus.SENT
            and self._task.linear_issue_url
        ):
            webbrowser.open(self._task.linear_issue_url)
            return
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
        # Re-apply any status badge so the row stays consistent after a
        # destructive op (delete + undo, in particular).
        self.set_status_visual(
            self._task.status,
            identifier=self._task.linear_issue_id,
            error_code=self._task.send_error,
        )

    def set_status_visual(
        self, status, *,
        identifier: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Replace the checkbox with a status badge after send begins.

        PENDING → restore checkbox; SENDING/SENT/FAILED/SKIPPED → badge.
        Identifier (e.g. ``ENG-1234``) appended to the summary when SENT.
        """
        from tasks.schema import TaskStatus
        if status is TaskStatus.PENDING:
            if hasattr(self, "_status_badge"):
                self._status_badge.grid_remove()
            self._check.grid()
            # Restore the plain summary in case it had ``· ENG-…`` appended.
            self._lbl_summary.configure(text=self._summary_text())
            return

        if not hasattr(self, "_status_badge"):
            self._status_badge = ctk.CTkLabel(
                self, text="", width=28,
                font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
                anchor="center",
            )
            self._status_badge.grid(
                row=0, column=0, padx=(8, 6), pady=4, sticky="w",
            )

        self._check.grid_remove()
        self._status_badge.grid()

        if status is TaskStatus.SENDING:
            self._status_badge.configure(text="⏳", text_color=BLUE_DIM)
            self._lbl_summary.configure(text=self._summary_text())
        elif status is TaskStatus.SENT:
            self._status_badge.configure(text="✓", text_color=GREEN)
            base = self._summary_text()
            if identifier:
                self._lbl_summary.configure(text=f"{base}  ·  {identifier}")
            else:
                self._lbl_summary.configure(text=base)
        elif status is TaskStatus.FAILED:
            code = error_code or "?"
            self._status_badge.configure(text=f"⚠{code}", text_color=RED)
            self._lbl_summary.configure(text=self._summary_text())
        elif status is TaskStatus.SKIPPED:
            self._status_badge.configure(text="—", text_color=TEXT_SECONDARY)
            self._lbl_summary.configure(text=self._summary_text())

    def _summary_text(self) -> str:
        glyph = _PRIORITY_GLYPHS.get(self._task.priority.name.lower(), "⚪")
        assignee = self._task.assignee_name or "—"
        return f"👤 {assignee}  ·  {glyph} {self._task.priority.name.lower()}"


# Re-export tk for callers that still narrow except clauses to tk.TclError
# while operating on the row widget.
__all__ = ["_TaskRow", "tk"]
