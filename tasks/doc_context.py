"""Convert attached reference documents to Markdown for LLM grounding.

Wraps Microsoft markitdown (https://github.com/microsoft/markitdown) so meeting
materials — agenda, brief, prior protocol in PDF / DOCX / PPTX / XLSX — can be
folded into the same ``context`` slot the directory grounding already feeds
(``tasks/protocol_generator.py`` and ``tasks/extractor.py``).

Document extras ONLY. markitdown can also transcribe audio, but that path stays
with the cloud STT providers — using markitdown's ``[audio-transcription]``
extra would both duplicate that competency and risk pulling heavy ML deps that
invariant #2 forbids.

Sentinel-pattern lazy load (see ``gdrive/backup.py`` /
[[feedback_sentinel_lazy_load_for_testable_imports]]): ``MarkItDown`` starts as
``None`` and is bound on first real use. This keeps markitdown off the import
chain of dialogs that never attach a document AND lets tests swap it cleanly via
``patch("tasks.doc_context.MarkItDown", ...)`` without installing the package.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Sentinel — bound to markitdown.MarkItDown on first convert_documents() call
# that actually has work to do. Tests patch this attribute directly.
MarkItDown = None

# Combined character budget across all attached documents. A meeting transcript
# is already large; uncapped reference docs would blow the LLM context window
# and inflate cost. Tail-truncation with a visible marker is the safe default.
MAX_DOC_CONTEXT_CHARS = 16000


def convert_documents(
    paths: list[str], max_chars: int = MAX_DOC_CONTEXT_CHARS
) -> str:
    """Convert each path to Markdown and return one labelled context block.

    Each document becomes a ``### <filename>`` section. The whole thing is
    wrapped in ``=== ПРИЛОЖЕННЫЕ ДОКУМЕНТЫ ===`` / ``=== КОНЕЦ ДОКУМЕНТОВ ===``
    markers so the LLM can tell attached material from the transcript.

    Resilient by design: a file that fails to convert (corrupt, unsupported,
    unreadable) is logged and skipped — one bad document must never block the
    task extraction / protocol generation that runs after this. Returns ``""``
    when ``paths`` is empty or nothing converted to non-empty text, so callers
    can treat the result like any other optional context fragment.
    """
    if not paths:
        return ""

    global MarkItDown
    if MarkItDown is None:
        from markitdown import MarkItDown as _MarkItDown

        MarkItDown = _MarkItDown

    md = MarkItDown()
    blocks: list[str] = []
    for path in paths:
        name = os.path.basename(path)
        try:
            text = (md.convert(path).text_content or "").strip()
        # Broad on purpose: markitdown's converters raise heterogeneous,
        # largely undocumented exceptions on malformed input (its own
        # FileConversionException/UnsupportedFormatException plus raw
        # pdfminer / lxml / zipfile errors). Isolating per-file failure is
        # the explicit goal — a single bad doc must not crash the worker.
        except Exception as exc:
            logger.warning("doc convert failed for %s: %s", name, exc)
            continue
        if text:
            blocks.append(f"### {name}\n{text}")

    if not blocks:
        return ""

    body = "\n\n".join(blocks)
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n…[документы обрезаны]"
    return f"=== ПРИЛОЖЕННЫЕ ДОКУМЕНТЫ ===\n{body}\n=== КОНЕЦ ДОКУМЕНТОВ ==="


def combine_context(meeting_context: str | None, doc_context: str) -> str | None:
    """Merge directory grounding and document grounding into one context string.

    Either side may be empty/None. Returns ``None`` when both are empty so the
    downstream prompt is byte-for-byte unchanged from the no-context path
    (callers pass ``context=None`` to reproduce the pre-grounding prompt).
    """
    parts = [c for c in (meeting_context, doc_context) if c]
    return "\n\n".join(parts) if parts else None
