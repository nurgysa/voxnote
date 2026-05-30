from directory.context import default_participants, render_meeting_context
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


def test_default_participants_filters_by_project():
    a = Person(full_name="A", project_ids=["p1"])
    b = Person(full_name="B", project_ids=["p2"])
    c = Person(full_name="C", project_ids=["p1", "p2"])
    out = default_participants([a, b, c], "p1")
    assert [p.full_name for p in out] == ["A", "C"]


def test_default_participants_none_project_is_empty():
    a = Person(full_name="A", project_ids=["p1"])
    assert default_participants([a], None) == []


def test_default_participants_unknown_project_is_empty():
    a = Person(full_name="A", project_ids=["p1"])
    assert default_participants([a], "nope") == []
