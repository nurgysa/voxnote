"""Whisper ``initial_prompt`` builder.

The prompt pairs two signals:
  1. A natural-language frame ("Transcript of a conversation in X...") —
     anchors stylistic register and orthography;
  2. A comma-separated list of domain terms — biases spelling of names
     and jargon (e.g. "Kubernetes" not "Kuber Netting", "Нургиса" not
     "Нур Гиса"). Redundant with the ``hotwords=`` parameter but works
     via a different mechanism (decode context vs CTC-style biasing) and
     is more reliable for proper-noun casing.

Pure module — no Whisper, no I/O. Tested by ``tests/test_transcriber_pure``.
"""
from __future__ import annotations

# Safe upper bound on initial_prompt length. Whisper enforces a 224-token
# prompt limit; truncating at this many CHARS before tokenization keeps us
# comfortably under even in worst-case Cyrillic BPE (2-3 bytes per token).
# If the truncated string cuts a term in half the term is simply dropped
# from the prompt — hotwords= still biases for it at token level.
_MAX_PROMPT_CHARS = 400


# Language-specific prompt frames. Whisper uses these as decode-time context:
# mentioning the language and framing it as "transcript of a meeting" subtly
# biases punctuation, capitalization and word choice toward that register.
# Leaving a language out (e.g. "auto") yields None → prompt is skipped.
_PROMPT_FRAMES: dict[str, dict[str, str]] = {
    "ru": {
        "prefix": "Расшифровка разговора на русском языке.",
        "terms_label": "Упомянутые термины",
    },
    "kk": {
        "prefix": "Қазақ тіліндегі әңгіменің жазбасы.",
        "terms_label": "Аталған терминдер",
    },
    "en": {
        "prefix": "Transcript of a spoken conversation in English.",
        "terms_label": "Terms mentioned",
    },
}


def _build_initial_prompt(
    language: str | None,
    hotwords_str: str | None,
) -> str | None:
    """
    Assemble Whisper's ``initial_prompt`` from the language hint and the user's
    hotword dictionary.

    Returns None when neither signal is available, so the caller can pass None
    straight through to faster-whisper (which treats None as "no prompt").
    """
    frame = _PROMPT_FRAMES.get(language) if language else None
    has_terms = bool(hotwords_str and hotwords_str.strip())
    if frame is None and not has_terms:
        return None

    parts: list[str] = []
    if frame is not None:
        parts.append(frame["prefix"])
    if has_terms:
        label = frame["terms_label"] if frame is not None else "Terms"
        parts.append(f"{label}: {hotwords_str.strip()}.")

    prompt = " ".join(parts)
    if len(prompt) <= _MAX_PROMPT_CHARS:
        return prompt

    # Truncate on the last comma before the limit so we don't cut a term in
    # half. If no comma is found (shouldn't happen for multi-term prompts),
    # hard-truncate at the limit.
    head = prompt[:_MAX_PROMPT_CHARS]
    cut = head.rfind(",")
    if cut > 0:
        return head[:cut] + "."
    return head
