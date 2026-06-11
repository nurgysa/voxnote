from pathlib import Path

SRC = Path(__file__).parent.parent / "ui/dialogs/extract_tasks/__init__.py"
BUILDER = Path(__file__).parent.parent / "ui/dialogs/extract_tasks/builder.py"


def test_dialog_loads_directory_store():
    src = SRC.read_text(encoding="utf-8")
    assert "from directory.store import" in src
    assert "DirectoryStore()" in src


def test_dialog_builds_context_section():
    src = SRC.read_text(encoding="utf-8")
    bld = BUILDER.read_text(encoding="utf-8")
    # "Контекст встречи" lives in the builder comment after the widget-tree move.
    assert "Контекст встречи" in bld
    # State vars are initialised in ExtractTasksDialog.__init__ (still __init__.py).
    assert "_context_project_var" in src
    assert "_context_person_vars" in src


def test_dialog_uses_default_participants():
    src = SRC.read_text(encoding="utf-8")
    assert "default_participants" in src


def test_dialog_restores_selection_from_speakers_json():
    src = SRC.read_text(encoding="utf-8")
    assert "load_speakers" in src


def test_run_extraction_passes_context_to_both_calls():
    src = SRC.read_text(encoding="utf-8")
    # render once, thread into extract() and generate()
    assert "render_meeting_context(" in src
    assert src.count("context=meeting_context") >= 2


def test_protocol_speakers_uses_real_names():
    src = SRC.read_text(encoding="utf-8")
    assert "speakers=[p.full_name for p in people]" in src
    assert "speakers=[],  # cloud-only build has no voice library" not in src


def test_run_extraction_persists_speakers_json():
    src = SRC.read_text(encoding="utf-8")
    assert "save_speakers(" in src


def test_dialog_builds_speaker_rows():
    src = SRC.read_text(encoding="utf-8")
    bld = BUILDER.read_text(encoding="utf-8")
    # "Кто говорит" label lives in build_ui (builder.py) after the widget-tree move.
    assert "Кто говорит" in bld
    assert "build_speaker_rows" in bld
    assert "_speaker_row_vars" in src
    assert "load_segments" in bld
    assert "_build_speaker_map" in bld


def test_dialog_speaker_autosync_to_participants():
    src = SRC.read_text(encoding="utf-8")
    assert "_on_speaker_bound" in src
    assert "_person_by_name" in src


def test_run_extraction_rewrites_transcript_with_names():
    src = SRC.read_text(encoding="utf-8")
    assert "apply_speaker_names(" in src
    # rewritten transcript flows into BOTH extract() and generate()
    assert src.count("transcript=transcript_for_llm") >= 2


def test_run_extraction_persists_speaker_map():
    src = SRC.read_text(encoding="utf-8")
    assert "speaker_map=speaker_map" in src
    assert "_selected_speaker_maps()" in src
