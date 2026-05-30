from directory.context import render_meeting_context
from directory.schema import Person, Project


def test_full_context_exact():
    people = [
        Person(full_name="Айбек Нурланов", role="тимлид бэкенда"),
        Person(full_name="Дана Сапарова", role="продакт"),
    ]
    project = Project(name="Миграция биллинга", description="Перенос на Stripe")
    assert render_meeting_context(people, project) == (
        "=== КОНТЕКСТ ВСТРЕЧИ ===\n"
        "Проект: Миграция биллинга\n"
        "Описание: Перенос на Stripe\n"
        "\n"
        "Участники:\n"
        "- Айбек Нурланов — тимлид бэкенда\n"
        "- Дана Сапарова — продакт\n"
        "=== КОНЕЦ КОНТЕКСТА ==="
    )


def test_no_project_omits_project_lines():
    out = render_meeting_context([Person(full_name="A", role="r")], None)
    assert "Проект:" not in out
    assert out.startswith("=== КОНТЕКСТ ВСТРЕЧИ ===")
    assert "- A — r" in out


def test_empty_role_omits_dash():
    out = render_meeting_context([Person(full_name="Иван")], None)
    assert "- Иван" in out
    assert "—" not in out


def test_nothing_to_render_returns_empty_string():
    assert render_meeting_context([], None) == ""
    assert render_meeting_context([Person(full_name="   ")], None) == ""
