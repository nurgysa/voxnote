"""Tests for the LLM-driven 5-block MoM generator (Task 5 / Subtask 5b).

The generator orchestrates: build_prompt → openrouter.complete →
parse_llm_response → substitute(template). All 5 tests mock the
OpenRouterClient so no live HTTP happens.
"""
from unittest.mock import Mock

import pytest

from tasks.openrouter_client import OpenRouterError
from tasks.protocol_generator import (
    ProtocolGenerationError,
    ProtocolResult,
    build_prompt,
    generate,
    parse_llm_response,
)
from tasks.protocol_template import Placeholders


def test_build_prompt_includes_transcript_speakers_date_lang():
    """Prompt body carries all the inputs the LLM needs to write the protocol."""
    prompt = build_prompt(
        transcript="Иван: Поехали.\nАнна: Готова.",
        speakers=["Иван", "Анна"],
        meeting_date="2026-05-28",
        lang="ru",
    )
    # Speakers + date echoed for the LLM's metadata extraction
    assert "Иван" in prompt
    assert "Анна" in prompt
    assert "2026-05-28" in prompt
    # Transcript verbatim — LLM needs the raw content
    assert "Поехали" in prompt
    assert "Готова" in prompt
    # Language hint somewhere (Russian word or code)
    assert "русск" in prompt.lower() or "ru" in prompt.lower()


def test_parse_llm_response_extracts_five_blocks():
    """A well-formed LLM response splits cleanly into 5 Placeholders fields."""
    response = """## meeting_type
Sprint Planning

## participants
Иван, Анна

## agenda
- Sprint goal
- Capacity check

## theses_and_decisions
**Решение:** ship by Friday.

## action_items
- @Иван: спецификация (срок 2026-06-04)
- @Анна: дизайн (срок 2026-06-05)
"""
    p = parse_llm_response(response)
    assert isinstance(p, Placeholders)
    assert p.meeting_type == "Sprint Planning"
    assert p.participants == "Иван, Анна"
    assert "Sprint goal" in p.agenda
    assert "Capacity check" in p.agenda
    assert "Решение" in p.theses_and_decisions
    assert "ship by Friday" in p.theses_and_decisions
    assert "@Иван: спецификация" in p.action_items
    assert "@Анна: дизайн" in p.action_items
    # meeting_date is caller-provided, NOT extracted by LLM —
    # parse_llm_response leaves it as empty string by design.
    assert p.meeting_date == ""


def test_parse_llm_response_missing_block_raises_with_diagnostic():
    """If LLM skips a required block, error message names what's missing."""
    response = """## meeting_type
Sprint

## agenda
- thing
"""
    # Missing: participants, theses_and_decisions, action_items
    with pytest.raises(ProtocolGenerationError) as exc_info:
        parse_llm_response(response)
    msg = str(exc_info.value)
    # Error should name at least one missing block to aid debugging
    assert "participants" in msg or "theses" in msg or "action" in msg


def test_generate_end_to_end_with_mock_llm():
    """Full generate() flow with a mocked OpenRouterClient — no real HTTP."""
    mock_llm_content = """## meeting_type
Customer Call

## participants
Иван (нам), Алёна (клиент)

## agenda
- Demo of v0.1
- Q&A about pricing

## theses_and_decisions
**Решение:** клиент подключается с июня.

## action_items
- @Иван: отправить договор (срок 2026-05-30)
"""
    mock_client = Mock()
    mock_client.complete.return_value = {
        "content": mock_llm_content,
        "usage": {"prompt_tokens": 320, "completion_tokens": 180},
        "model": "anthropic/claude-sonnet-4.5",
    }

    result = generate(
        transcript="Иван: Демо v0.1...",
        speakers=["Иван", "Алёна"],
        meeting_date="2026-05-28",
        lang="ru",
        model="anthropic/claude-sonnet-4.5",
        openrouter_client=mock_client,
    )

    # Result shape
    assert isinstance(result, ProtocolResult)
    assert isinstance(result.placeholders, Placeholders)
    assert result.raw_llm_response == mock_llm_content
    # Markdown is rendered template with placeholders filled
    assert "Customer Call" in result.markdown
    assert "@Иван: отправить договор" in result.markdown
    # meeting_date threaded through from caller, NOT from LLM
    assert "2026-05-28" in result.markdown
    assert result.placeholders.meeting_date == "2026-05-28"
    # LLM was called once with json_mode=False (markdown output, not JSON)
    assert mock_client.complete.call_count == 1
    call_kwargs = mock_client.complete.call_args.kwargs
    assert call_kwargs.get("json_mode") is False
    assert call_kwargs.get("model") == "anthropic/claude-sonnet-4.5"


def test_generate_wraps_openrouter_error_as_protocol_error():
    """OpenRouterError (network, 429, etc.) surfaces as ProtocolGenerationError.

    Caller (extract_tasks dialog) catches one exception type and renders
    a Russian error message — wrapping prevents leaking provider-specific
    exception types into the UI layer.
    """
    mock_client = Mock()
    mock_client.complete.side_effect = OpenRouterError(
        "OpenRouter 429 rate-limit (retry after 30s)"
    )

    with pytest.raises(ProtocolGenerationError) as exc_info:
        generate(
            transcript="x",
            speakers=[],
            meeting_date="2026-05-28",
            lang="ru",
            model="any/model",
            openrouter_client=mock_client,
        )

    # The original OpenRouter message survives via str(); __cause__ keeps the
    # full exception chain for debug logs.
    msg = str(exc_info.value)
    assert "429" in msg or "rate" in msg.lower() or "openrouter" in msg.lower()
    assert isinstance(exc_info.value.__cause__, OpenRouterError)
