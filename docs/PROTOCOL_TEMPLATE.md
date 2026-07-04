# Meeting protocol template (5-block MoM)

**Version:** v1.0 (Task 5 of MVP v5 plan, 2026-05-28)
**Source:** Tauri SaaS spec section 7.9, embedded in `tasks/protocol_template.py`

This document describes the template VoxNote uses to generate
`<history>/<run>/protocol.md` after the user runs task extraction in the Extract
dialog.

## Why five blocks

The Minutes of Meeting (MoM) format is split into five structural sections, each
answering one category of questions:

1. **Metadata** - who, when, and what type of meeting. This gives future readers context.
2. **Agenda** - what topics were discussed. This is the meeting skeleton.
3. **Key theses and decisions** - what was said and what was agreed. This is the substance.
4. **Action plan** - who does what and by when. This is the actionable output.
5. **Next meeting and materials** - where the work continues. This preserves continuity.

This structure is intentionally generic. The same five-block protocol can be
read across different meeting types, such as Sprint Planning, 1-on-1, Customer
Call, or Interview.

## How blocks are filled

| Block | Content source | Template placeholder |
|---|---|---|
| 1. Metadata | `meeting_type` and `participants` from the LLM; `meeting_date` from the UI form | `{meeting_type}` / `{meeting_date}` / `{participants}` |
| 2. Agenda | The LLM extracts topics from the beginning and overall structure of the transcript | `{agenda}` |
| 3. Key theses and decisions | The LLM analyzes the transcript and marks decisions in bold | `{theses_and_decisions}` |
| 4. Action plan | The LLM extracts action items such as owner, task and deadline | `{action_items}` |
| 5. Next meeting and materials | Static v1.0 reminder to add the next meeting/materials manually | no placeholder |

Block 5 is static in v1.0 for two reasons:

- extracting the next meeting date would require an additional LLM pass for a
  relatively rare case;
- the original spec describes a later Phase 2 `next_meeting` pass with
  `{date, topic, confidence}` fields, which should be implemented deliberately
  instead of half-implemented here.

## `Placeholders` dataclass structure

`tasks/protocol_template.py` defines a frozen dataclass with six fields:

```python
@dataclass(frozen=True)
class Placeholders:
    meeting_type: str
    meeting_date: str
    participants: str
    agenda: str
    theses_and_decisions: str
    action_items: str
```

The template has five blocks but six fields because Metadata is split into three
atomic fields. `meeting_date` comes directly from the UI when the user already
knows it; the rest of the fields map to the remaining protocol sections.

## LLM contract

`tasks/protocol_generator.py` sends the following to OpenRouter:

- **System message** of about 1.5 KB, designed to be cache-friendly, instructing
  the model to return exactly five H2 sections in the required order.
- **User message** containing `meeting_date`, `speakers`, `lang_label`, and the
  transcript text between transcript boundary markers.

The LLM returns Markdown like this:

```markdown
## meeting_type
Sprint Planning

## participants
Ivan, Anna, ...

## agenda
- ...
- ...

## theses_and_decisions
**Decision:** ...

## action_items
- @Ivan: ... (deadline 2026-06-04)
```

The parser (`parse_llm_response`) splits the response with the regex
`^## (\w+)\n(.*?)(?=\n##|\Z)` and fills `Placeholders`. If any required block is
missing, it raises `ProtocolGenerationError` with diagnostics so the user can try
another model or rerun extraction.

## LLM parameters

- `model`: selected in the Extract dialog. Use the repository defaults unless a
  deliberate model change is being tested.
- `temperature`: **0.3**. This is slightly higher than the task extractor default
  of 0.2 because protocol wording benefits from light variation while staying
  grounded in the transcript.
- `json_mode`: **False**. The output is Markdown, not JSON.
- `timeout`: 60 seconds, using the standard `OpenRouterClient` timeout.

Cost depends on model, transcript length and provider pricing. Verify current
pricing before long or sensitive runs.

## Regeneration

In v1.0, if the user dislikes the protocol:

1. Run task extraction again; the protocol is recomputed completely.
2. Optionally choose a different model before rerunning.

Phase 2 can add per-block regeneration through the `Placeholders` object, for
example regenerating only `action_items` with another model. The
`ProtocolResult.placeholders` design already supports that direction.

## Changing the template

If the structure needs to change:

1. Edit the `MOM_5_BLOCK_TEMPLATE` constant in `tasks/protocol_template.py`.
2. If fields are added or removed, also update the `Placeholders` dataclass, the
   system prompt in `_SYSTEM_PROMPT` (`tasks/protocol_generator.py`), and the
   `_REQUIRED_BLOCKS` tuple.
3. `test_template_declares_all_six_placeholders` catches mismatches between the
   dataclass fields and `{name}` placeholders in the template.
4. `test_template_has_five_block_structure` checks that the template keeps five
   H2 sections using a case-insensitive marker scan.
5. Any field add/remove in `Placeholders` must update this document.

The original spec describes 10 type-specific seeded templates such as Standup,
Customer Call, and Sprint Retro. Those can later live under
`<vault>/.voxnote/protocol_templates/<Type>.md`. In v1.0, one universal 5-block
skeleton covers all meeting types.
