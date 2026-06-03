"""Tests for tasks.doc_context — markitdown document → LLM-context conversion.

MarkItDown is patched (`patch("tasks.doc_context.MarkItDown", ...)`) so these
run without markitdown installed and without touching the filesystem. The
sentinel lazy-load (MarkItDown=None at module top, bound on first real use)
means the patched class is picked up instead of triggering a real import.
"""
from types import SimpleNamespace
from unittest.mock import patch

from tasks.doc_context import MAX_DOC_CONTEXT_CHARS, combine_context, convert_documents


def _fake_markitdown(mapping):
    """Return a MarkItDown-class stand-in.

    Its instance's .convert(path) returns SimpleNamespace(text_content=...)
    from `mapping`; a mapping value that is an Exception instance is raised
    instead, simulating a corrupt/unsupported file.
    """
    def convert(path, *args, **kwargs):
        val = mapping[path]
        if isinstance(val, Exception):
            raise val
        return SimpleNamespace(text_content=val)

    instance = SimpleNamespace(convert=convert)
    return lambda *a, **k: instance


# ---- convert_documents -------------------------------------------------


def test_empty_list_returns_empty_without_touching_markitdown():
    # Must short-circuit before instantiating MarkItDown (no patch supplied).
    assert convert_documents([]) == ""


def test_single_document_wraps_with_header_and_markers():
    fake = _fake_markitdown({"/x/agenda.pdf": "Повестка: бюджет на Q3"})
    with patch("tasks.doc_context.MarkItDown", fake):
        out = convert_documents(["/x/agenda.pdf"])
    assert out.startswith("=== ПРИЛОЖЕННЫЕ ДОКУМЕНТЫ ===")
    assert out.rstrip().endswith("=== КОНЕЦ ДОКУМЕНТОВ ===")
    assert "### agenda.pdf" in out          # basename only, not full path
    assert "/x/" not in out
    assert "Повестка: бюджет на Q3" in out


def test_multiple_documents_concatenated():
    fake = _fake_markitdown({"/a.pdf": "AAA", "/b.docx": "BBB"})
    with patch("tasks.doc_context.MarkItDown", fake):
        out = convert_documents(["/a.pdf", "/b.docx"])
    assert "### a.pdf" in out and "AAA" in out
    assert "### b.docx" in out and "BBB" in out


def test_one_bad_file_is_skipped_others_survive():
    fake = _fake_markitdown(
        {"/good.pdf": "GOOD CONTENT", "/bad.pptx": RuntimeError("corrupt")}
    )
    with patch("tasks.doc_context.MarkItDown", fake):
        out = convert_documents(["/bad.pptx", "/good.pdf"])
    assert "GOOD CONTENT" in out
    assert "bad.pptx" not in out  # no header emitted for the failed file


def test_all_files_fail_returns_empty():
    fake = _fake_markitdown({"/bad.pptx": OSError("nope")})
    with patch("tasks.doc_context.MarkItDown", fake):
        assert convert_documents(["/bad.pptx"]) == ""


def test_empty_text_content_is_skipped():
    fake = _fake_markitdown({"/blank.pdf": "   "})
    with patch("tasks.doc_context.MarkItDown", fake):
        assert convert_documents(["/blank.pdf"]) == ""


def test_oversized_content_is_truncated_with_marker():
    big = "x" * (MAX_DOC_CONTEXT_CHARS + 5000)
    fake = _fake_markitdown({"/big.pdf": big})
    with patch("tasks.doc_context.MarkItDown", fake):
        out = convert_documents(["/big.pdf"], max_chars=1000)
    assert "…[документы обрезаны]" in out
    assert len(out) <= 1000 + 200  # body capped near max_chars + wrapper/marker


# ---- combine_context ---------------------------------------------------


def test_combine_both_empty_returns_none():
    assert combine_context(None, "") is None


def test_combine_only_meeting_context():
    assert combine_context("MEET", "") == "MEET"


def test_combine_only_doc_context():
    assert combine_context(None, "DOCS") == "DOCS"


def test_combine_both_joined_by_blank_line():
    assert combine_context("MEET", "DOCS") == "MEET\n\nDOCS"
