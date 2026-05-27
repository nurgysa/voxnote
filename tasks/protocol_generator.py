"""LLM-driven 5-block MoM protocol generator (Task 5 / Subtask 5b).

Public API:
- generate(transcript, speakers, meeting_date, lang, model, openrouter_client)
    -> ProtocolResult
- build_prompt(transcript, speakers, meeting_date, lang) -> str
- parse_llm_response(content) -> Placeholders
- ProtocolGenerationError: all failures (LLM error, parse error, missing
  blocks) surface as this single exception type — UI handles one branch.
- ProtocolResult: dataclass with markdown + raw_llm_response + placeholders.
  `markdown` is what gets written to `<history>/protocol.md`;
  `raw_llm_response` is for debug logs; `placeholders` is the parsed
  intermediate exposed for future per-block regeneration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from tasks.openrouter_client import OpenRouterClient, OpenRouterError
from tasks.protocol_template import MOM_5_BLOCK_TEMPLATE, Placeholders, substitute


class ProtocolGenerationError(Exception):
    """All protocol-generation failures (LLM 4xx/5xx, network, parse) bubble up here.

    The dialog catches ONE exception type and renders a Russian error
    message — wrapping prevents leaking provider-specific exception types
    (OpenRouterError, requests.RequestException, ValueError) into the UI
    layer. Original cause is preserved via `__cause__` for debug logs.
    """


@dataclass(frozen=True)
class ProtocolResult:
    """Generated protocol artifact returned by `generate()`.

    Attributes:
        markdown: rendered MOM_5_BLOCK_TEMPLATE with placeholders filled.
            This is what gets written to ``<history>/protocol.md``.
        raw_llm_response: the exact text returned by the LLM. Persisted
            to debug logs only; not shown to the user.
        placeholders: the parsed intermediate. Exposed so a future Phase 2
            «regenerate just the action items» feature can mutate one block
            without re-running the full LLM call.
    """

    markdown: str
    raw_llm_response: str
    placeholders: Placeholders


# System message is split out so OpenRouter prompt-caching can de-duplicate
# the ~1.5KB instruction block across many calls (per Anthropic prompt-cache
# docs — system messages are cacheable, user messages are not).
_SYSTEM_PROMPT = """Ты — ассистент по подготовке протоколов встреч.
Дан транскрипт встречи. Извлеки 5 блоков информации и верни их строго в указанном формате.

Формат ответа — ровно 5 H2-секций, каждая начинается с `## <имя_секции>`:

## meeting_type
<Одной фразой: тип встречи. Один из: Sprint Planning / 1-on-1 / Customer Call / Design Review / Sprint Retro / Demo / Interview / Workshop / Standup / Other. Если непонятно — Other.>

## participants
<Запятая-разделённый список упомянутых имён участников. Если только Speaker A/B/C — перечисли их так. Если в системной части передан список заявленных участников — дополни его теми, кого реально слышно в транскрипте.>

## agenda
<Маркированный список (markdown `-`) пунктов повестки. Если повестка явно не озвучена в начале — извлеки темы из общего содержания. Минимум 2 пункта.>

## theses_and_decisions
<Markdown-проза. Тезисы → решения → разногласия. Выделяй жирным (`**текст**`) принятые решения. Цитируй ключевые формулировки. Если разногласий не было — раздел опусти, но H2-заголовок оставь.>

## action_items
<Маркированный список действий. Формат каждой строки: `- @Исполнитель: задача (срок, если был назван)`. Если исполнитель не назван — `@(?)`. Если срок не назван — без `(срок)`. Если действий нет — `- *(действий не зафиксировано)*`.>

Жёсткие правила:
- Отвечай ТОЛЬКО на русском, даже если транскрипт смешанный (KZ/RU/EN).
- Не добавляй преамбулу или комментарии — только 5 H2-секций в указанном порядке.
- Не используй H1 (`#`) — только H2 (`##`) для разделов.
- Если какой-то блок невозможно извлечь — заполни его текстом `*(не зафиксировано в транскрипте)*`, но H2-заголовок всё равно укажи.
"""


_LANG_LABELS = {
    "ru": "русский",
    "kk": "казахский",
    "en": "английский",
    "mixed": "смешанный (KZ+RU+EN)",
}


def build_prompt(
    transcript: str,
    speakers: list[str],
    meeting_date: str,
    lang: str | None,
) -> str:
    """Build the user-message body for the OpenRouter call.

    The format contract lives in `_SYSTEM_PROMPT`. The user message carries
    runtime inputs: transcript + metadata. Keeping the format contract in
    the system message lets prompt-caching work across many calls.
    """
    speakers_str = ", ".join(speakers) if speakers else "(не указаны заранее)"
    lang_label = _LANG_LABELS.get(lang or "", "не определён")

    return (
        f"Дата встречи: {meeting_date or '(не указана)'}\n"
        f"Заявленные участники: {speakers_str}\n"
        f"Язык транскрипта: {lang_label}\n"
        f"\n"
        f"=== ТРАНСКРИПТ ===\n"
        f"{transcript}\n"
        f"=== КОНЕЦ ТРАНСКРИПТА ===\n"
        f"\n"
        f"Извлеки 5 блоков по формату из системной инструкции."
    )


# Order matters — used for «missing blocks» error message.
_REQUIRED_BLOCKS = (
    "meeting_type",
    "participants",
    "agenda",
    "theses_and_decisions",
    "action_items",
)

# Matches `## blockname\n<body>` non-greedily up to the next `## anything`
# or end of string. MULTILINE so `^` matches line starts; DOTALL so `.`
# spans newlines inside the body.
_BLOCK_PATTERN = re.compile(
    r"^##\s+(\w+)\s*\n(.*?)(?=\n##\s+\w+|\Z)",
    re.DOTALL | re.MULTILINE,
)


def parse_llm_response(response: str) -> Placeholders:
    """Split the LLM's H2-delimited markdown into Placeholders fields.

    `meeting_date` is intentionally left as empty string — it's a caller-
    provided value, not an LLM extraction. `generate()` fills it in before
    rendering.

    Raises ProtocolGenerationError if any of the 5 required blocks is
    missing from the response, with a diagnostic listing which blocks
    were found vs missing — helps the user pick a better model.
    """
    blocks: dict[str, str] = {
        match.group(1).strip(): match.group(2).strip()
        for match in _BLOCK_PATTERN.finditer(response)
    }

    missing = [b for b in _REQUIRED_BLOCKS if b not in blocks]
    if missing:
        found = ", ".join(blocks.keys()) if blocks else "(ни одного)"
        raise ProtocolGenerationError(
            f"LLM-ответ не содержит обязательные блоки: {', '.join(missing)}. "
            f"Найдено: {found}. Попробуй другую модель."
        )

    return Placeholders(
        meeting_type=blocks["meeting_type"],
        meeting_date="",  # caller-filled in generate(); LLM doesn't extract this
        participants=blocks["participants"],
        agenda=blocks["agenda"],
        theses_and_decisions=blocks["theses_and_decisions"],
        action_items=blocks["action_items"],
    )


def generate(
    transcript: str,
    speakers: list[str],
    meeting_date: str,
    lang: str | None,
    model: str,
    openrouter_client: OpenRouterClient,
) -> ProtocolResult:
    """Full pipeline: build prompt → LLM call → parse → render template.

    Args:
        transcript: full transcribed text (any length).
        speakers: pre-specified participant names. Empty list for the
            cloud-only build where no voice library exists.
        meeting_date: ISO date string (e.g. "2026-05-28") from the UI.
            Filled into Placeholders verbatim — the LLM does not extract it.
        lang: language code ("ru"/"kk"/"en"/"mixed"/None). Hints the LLM
            in the user prompt; output stays Russian regardless.
        model: OpenRouter model slug (e.g. "anthropic/claude-sonnet-4.5").
        openrouter_client: live OpenRouterClient instance — caller owns
            lifecycle (typically constructed once per dialog session).

    Returns:
        ProtocolResult with rendered markdown + raw response + parsed
        placeholders.

    Raises:
        ProtocolGenerationError on any failure (LLM error, empty response,
        missing required blocks).
    """
    user_message = build_prompt(transcript, speakers, meeting_date, lang)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    try:
        # json_mode=False — protocol output is structured markdown, not JSON.
        # temperature=0.3 — slightly above the extractor's default 0.2:
        # protocol phrasing benefits from a touch of variation while still
        # staying faithful to the source.
        response = openrouter_client.complete(
            model=model,
            messages=messages,
            json_mode=False,
            temperature=0.3,
        )
    except OpenRouterError as e:
        raise ProtocolGenerationError(f"OpenRouter: {e}") from e

    raw_content = response.get("content", "") or ""
    if not raw_content.strip():
        raise ProtocolGenerationError(
            "LLM вернул пустой ответ — попробуй другую модель."
        )

    # `parse_llm_response` leaves meeting_date empty — fill it from caller here.
    parsed = parse_llm_response(raw_content)
    placeholders = Placeholders(
        meeting_type=parsed.meeting_type,
        meeting_date=meeting_date,
        participants=parsed.participants,
        agenda=parsed.agenda,
        theses_and_decisions=parsed.theses_and_decisions,
        action_items=parsed.action_items,
    )

    markdown = substitute(MOM_5_BLOCK_TEMPLATE, placeholders)
    return ProtocolResult(
        markdown=markdown,
        raw_llm_response=raw_content,
        placeholders=placeholders,
    )
