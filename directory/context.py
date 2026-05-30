"""Render the «КОНТЕКСТ ВСТРЕЧИ» block injected into protocol/task prompts.

Pure function, no I/O. Output is user-facing Russian (repo convention).
Returns "" when there is nothing to add, so callers pass context=None and the
downstream prompt is unchanged.
"""
from __future__ import annotations

from directory.schema import Person, Project


def render_meeting_context(people: list[Person], project: Project | None) -> str:
    lines: list[str] = []

    if project is not None and project.name.strip():
        lines.append(f"Проект: {project.name.strip()}")
        if project.description.strip():
            lines.append(f"Описание: {project.description.strip()}")
        lines.append("")  # blank line before participants

    named = [p for p in people if p.full_name.strip()]
    if named:
        lines.append("Участники:")
        for p in named:
            role = p.role.strip()
            if role:
                lines.append(f"- {p.full_name.strip()} — {role}")
            else:
                lines.append(f"- {p.full_name.strip()}")

    body = "\n".join(lines).strip()
    if not body:
        return ""
    return f"=== КОНТЕКСТ ВСТРЕЧИ ===\n{body}\n=== КОНЕЦ КОНТЕКСТА ==="


def default_participants(
    people: list[Person], project_id: str | None
) -> list[Person]:
    """People whose project_ids include project_id (preserving input order).

    Returns [] when project_id is None or matches no one — the dialog uses this
    to pre-check participant boxes when a project is chosen.
    """
    if not project_id:
        return []
    return [p for p in people if project_id in p.project_ids]
