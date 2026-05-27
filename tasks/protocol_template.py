"""5-block Minutes-of-Meeting (MoM) template + Placeholders dataclass + substitute().

The template is the structural skeleton of a Russian-language meeting
protocol per Tauri SaaS spec §7.9 (and the v4 MVP plan decision row about
«5-block MoM»). LLM extracts content fields from the transcript;
`substitute()` fills them into the skeleton.

Why 6 fields, not 5: the 5 *blocks* are content sections of the rendered
protocol, but Block 1 (Metadata) splits into 3 atomic fields
(meeting_type / meeting_date / participants) — `meeting_date` comes from
caller context (UI dialog), the other two from LLM extraction. Keeping
them separate lets the generator fill only what the LLM produced and let
the caller pass `meeting_date` verbatim without re-prompting.

Block 5 («Следующая встреча и материалы») is intentionally NOT a
placeholder in v1.0 — it's static fallback text that says «add manually if
needed». Phase 2 may extract this from transcript via a separate
`next_meeting` pass per spec §7.9.
"""
from dataclasses import dataclass


MOM_5_BLOCK_TEMPLATE = """# Протокол встречи

## Метаданные
- **Тип встречи:** {meeting_type}
- **Дата:** {meeting_date}
- **Участники:** {participants}

## Повестка дня

{agenda}

## Ключевые тезисы и решения

{theses_and_decisions}

## План действий

{action_items}

## Следующая встреча и материалы

*(не зафиксировано в транскрипте — добавьте вручную при необходимости)*
"""


@dataclass(frozen=True)
class Placeholders:
    """Six content fields filled by the LLM (or caller) and rendered into the template.

    All values are strings — the LLM is instructed to return ready-to-render
    markdown for the multi-line fields (`agenda`, `theses_and_decisions`,
    `action_items`). Empty strings render as empty blocks; the dialog can
    detect emptiness post-render and offer to regenerate with a different
    model.

    Frozen so the dataclass acts as an immutable value object — caller can
    safely cache or pass it through pipelines without worrying about
    mutation-from-elsewhere.

    Field-to-block mapping:
      Block 1 (Метаданные): meeting_type, meeting_date, participants
      Block 2 (Повестка):    agenda
      Block 3 (Тезисы):      theses_and_decisions
      Block 4 (Действия):    action_items
      Block 5 (Следующая):   not a placeholder — static fallback (see template)
    """

    meeting_type: str
    meeting_date: str
    participants: str
    agenda: str
    theses_and_decisions: str
    action_items: str


def substitute(template: str, placeholders: Placeholders) -> str:
    """Fill `{field_name}` placeholders in `template` from `placeholders`.

    Unknown placeholders (e.g. `{some_other_thing}` not in Placeholders)
    are left intact in the output — no KeyError, no crash. This is
    deliberately permissive: a future template revision might add new
    placeholders before all callers update, and leaving the literal in
    the output is more debuggable than failing the whole render.

    None values are coerced to empty string (defensive; the Placeholders
    dataclass annotates `str` so None shouldn't normally appear, but a
    miswired test fixture could pass None and crashing here would be
    unhelpful).
    """
    out = template
    for field_name, value in placeholders.__dict__.items():
        out = out.replace("{" + field_name + "}", value if value is not None else "")
    return out
