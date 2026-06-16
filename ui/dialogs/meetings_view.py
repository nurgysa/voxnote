"""Pure presentation helpers for the «Встречи» dialog (no Tk).

Split out so the grouping/status/elapsed logic gets real unit tests — the
dialog itself (ui/dialogs/meetings.py) can't be imported under Linux CI
(customtkinter → PortAudio). The dialog is the thin Tk renderer over these.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from processing.model import QueueItem, StageStatus

NO_PROJECT_LABEL = "Без проекта"


def format_elapsed(started_at: str | None, now_iso: str) -> str:
    """'mm:ss' (or 'h:mm:ss' past an hour) between started_at and now_iso.
    Empty string when either timestamp is missing/unparseable; negative clamps."""
    try:
        start = datetime.fromisoformat(started_at)
        now = datetime.fromisoformat(now_iso)
    except (ValueError, TypeError):
        return ""
    total = max(0, int((now - start).total_seconds()))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def queue_position(rows: list[QueueItem], item: QueueItem) -> int | None:
    """1-based position of `item` among the active (auto) PENDING rows, in
    order; None if `item` is not an active PENDING row."""
    pending = [r for r in rows if r.auto and r.status == StageStatus.PENDING]
    for i, row in enumerate(pending, start=1):
        if row.id == item.id:
            return i
    return None


def format_status(
    item: QueueItem, now_iso: str, position: int | None
) -> tuple[str, str]:
    """(display text, color_key) for a row. color_key is one of
    'pending'/'running'/'done'/'error' — the dialog maps it to a theme color."""
    if item.status == StageStatus.RUNNING:
        elapsed = format_elapsed(item.started_at, now_iso)
        return (f"идёт {elapsed}" if elapsed else "идёт…", "running")
    if item.status == StageStatus.ERROR:
        return ("ошибка", "error")
    if item.status == StageStatus.DONE:
        return ("готово", "done")
    if position is not None and position > 1:
        return (f"в очереди ({position}-й)", "pending")
    return ("в очереди", "pending")


def group_by_project(
    rows: list[QueueItem], name_of: Callable[[str | None], str]
) -> list[tuple[str, list[QueueItem]]]:
    """Group rows by project display name (name_of(project_id)), preserving each
    group's first-appearance order, with the «Без проекта» group forced last."""
    groups: dict[str, list[QueueItem]] = {}
    order: list[str] = []
    for row in rows:
        name = name_of(row.project_id)
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(row)
    ordered = [n for n in order if n != NO_PROJECT_LABEL]
    if NO_PROJECT_LABEL in groups:
        ordered.append(NO_PROJECT_LABEL)
    return [(n, groups[n]) for n in ordered]
