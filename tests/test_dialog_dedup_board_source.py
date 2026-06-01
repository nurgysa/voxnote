# tests/test_dialog_dedup_board_source.py
import pathlib

_SRC = pathlib.Path("ui/dialogs/extract_tasks/__init__.py").read_text(encoding="utf-8")


def test_run_dedup_uses_board_registry_not_local_history():
    # Slice the _run_dedup method so we only assert on its body.
    start = _SRC.index("def _run_dedup(")
    end = _SRC.index("def _on_extract_success(")
    body = _SRC[start:end]
    assert "build_board_registry(backend, container_id)" in body
    assert "build_sent_registry(" not in body          # old source retired
    assert "list_history_entries" not in body          # no history scan
    # backend errors widen the swallow
    assert "LinearError" in body and "TrelloError" in body
