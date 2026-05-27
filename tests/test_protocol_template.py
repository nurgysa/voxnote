"""Tests for the 5-block MoM template substitution helper (Task 5 / Subtask 5a).

`substitute()` fills a markdown skeleton with content from a Placeholders
dataclass. Missing or unknown placeholders are left intact (no exception)
so the caller can detect them in the output if needed.
"""
from tasks.protocol_template import (
    MOM_5_BLOCK_TEMPLATE,
    Placeholders,
    substitute,
)


def test_substitute_replaces_all_known_placeholders():
    """Each field value from Placeholders appears in the rendered output."""
    p = Placeholders(
        meeting_type="Sprint Planning",
        meeting_date="2026-05-28",
        participants="Иван, Анна",
        agenda="- Sprint goal\n- Capacity check",
        theses_and_decisions="Решено: запускаем Phase 2",
        action_items="- Иван: подготовить дизайн (срок 2026-06-04)",
    )
    out = substitute(MOM_5_BLOCK_TEMPLATE, p)
    assert "Sprint Planning" in out
    assert "2026-05-28" in out
    assert "Иван, Анна" in out
    assert "Sprint goal" in out
    assert "Решено: запускаем Phase 2" in out
    assert "Иван: подготовить дизайн" in out
    # No raw placeholders remain for fields we filled
    for name in Placeholders.__dataclass_fields__:
        assert "{" + name + "}" not in out


def test_substitute_leaves_unknown_placeholders_intact():
    """A template with `{not_a_field}` doesn't crash — the literal stays."""
    template = "Hello {meeting_type}, world {unknown_field}"
    p = Placeholders(
        meeting_type="X", meeting_date="X", participants="X",
        agenda="X", theses_and_decisions="X", action_items="X",
    )
    out = substitute(template, p)
    # Known field replaced
    assert "Hello X" in out
    # Unknown placeholder left literal — no KeyError, no silent removal
    assert "{unknown_field}" in out


def test_template_declares_all_six_placeholders():
    """Every Placeholders field name must appear in the template as {name}.

    Catches drift where a new field is added to Placeholders but the
    template isn't updated to consume it.
    """
    field_names = list(Placeholders.__dataclass_fields__.keys())
    assert len(field_names) == 6, (
        f"Placeholders must have exactly 6 fields, got {len(field_names)}: "
        f"{field_names}"
    )
    for name in field_names:
        assert "{" + name + "}" in MOM_5_BLOCK_TEMPLATE, (
            f"Template is missing placeholder {{{name}}}"
        )


def test_substitute_handles_empty_values():
    """All-empty Placeholders renders without raising; placeholders become ``."""
    p = Placeholders(
        meeting_type="", meeting_date="", participants="",
        agenda="", theses_and_decisions="", action_items="",
    )
    out = substitute(MOM_5_BLOCK_TEMPLATE, p)
    # Every placeholder consumed (replaced with "") — none remain literal
    for name in Placeholders.__dataclass_fields__:
        assert "{" + name + "}" not in out
    # Template structure (H2 headers) preserved even with empty content
    assert "##" in out


def test_template_has_five_block_structure():
    """Sanity check: the 5-block MoM skeleton (per spec §7.9) is preserved.

    Russian H2 headers for each of the 5 blocks must be in the template.
    Loose matching (substring) so minor wording tweaks don't break this.
    """
    # Block 1: Metadata, Block 2: Agenda, Block 3: Theses+decisions,
    # Block 4: Action items, Block 5: Links + next meeting.
    # Substring match — case-insensitive to survive minor wording edits
    # ("Ключевые тезисы" vs "Тезисы и решения" vs "ТЕЗИСЫ" etc.).
    block_markers = [
        "метаданные",
        "повестка",
        "тезис",        # «Ключевые тезисы и решения» — match stem
        "действ",       # «План действий» — match stem
        "следующ",      # «Следующая встреча» / «Следующие шаги»
    ]
    template_lower = MOM_5_BLOCK_TEMPLATE.lower()
    for marker in block_markers:
        assert marker in template_lower, (
            f"Template missing 5-block marker {marker!r}"
        )
