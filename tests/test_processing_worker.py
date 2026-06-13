import json

from processing.model import StageStatus
from processing.worker import ProcessingQueue


def _queue(tmp_path, **over):
    kwargs = dict(
        meetings_dir=str(tmp_path / "meetings"),
        config_loader=lambda: {},
        resolve_project=lambda pid: None,
        queue_path=str(tmp_path / "queue.json"),
        on_change=None,
    )
    kwargs.update(over)
    return ProcessingQueue(**kwargs)


def test_enqueue_appends_and_persists(tmp_path):
    q = _queue(tmp_path)
    item_id = q.enqueue("/audio/a.m4a", {"provider": "AssemblyAI", "project_id": "p1"})

    snap = q.snapshot()
    assert len(snap) == 1
    assert snap[0].id == item_id
    assert snap[0].audio_path == "/audio/a.m4a"
    assert snap[0].auto is True
    assert snap[0].project_id == "p1"
    assert snap[0].transcript == StageStatus.PENDING

    with open(tmp_path / "queue.json", encoding="utf-8") as f:
        data = json.load(f)
    assert data["items"][0]["id"] == item_id


def test_snapshot_is_a_deep_copy(tmp_path):
    q = _queue(tmp_path)
    q.enqueue("/audio/a.m4a", {})
    snap = q.snapshot()
    snap[0].transcript = StageStatus.DONE
    assert q.snapshot()[0].transcript == StageStatus.PENDING


def test_on_change_fires_on_enqueue(tmp_path):
    calls = []
    q = _queue(tmp_path, on_change=lambda: calls.append(1))
    q.enqueue("/audio/a.m4a", {})
    assert calls == [1]


def test_loads_existing_active_items(tmp_path):
    q1 = _queue(tmp_path)
    q1.enqueue("/audio/a.m4a", {})
    q2 = _queue(tmp_path)
    assert len(q2.snapshot()) == 1


def _fake_transcribe_output(text="hello", language="ru", segments=None):
    class _Out:
        pass
    o = _Out()
    o.text = text
    o.language = language
    o.segments = segments if segments is not None else [{"speaker": "A", "text": "hi"}]
    return o


def test_transcribe_stage_creates_folder_and_marks_done(tmp_path, monkeypatch):
    import os

    from processing.model import StageStatus

    meetings = tmp_path / "meetings"
    meetings.mkdir()
    monkeypatch.setattr("utils.get_meetings_dir", lambda: str(meetings))
    monkeypatch.setattr(
        "cli.core.run_transcribe",
        lambda *a, **k: _fake_transcribe_output(),
    )
    audio = tmp_path / "rec.m4a"
    audio.write_bytes(b"\x00\x00")

    q = _queue(
        tmp_path,
        meetings_dir=str(meetings),
        config_loader=lambda: {"cloud_api_keys": {"AssemblyAI": "k"}},
    )
    q.enqueue(str(audio), {"provider": "AssemblyAI", "language": "ru"})
    ok = q._stage_transcribe(q._items[0])

    assert ok is True
    live = q.snapshot()[0]
    assert live.transcript == StageStatus.DONE
    assert live.meeting_folder and os.path.isdir(live.meeting_folder)
    assert os.path.isfile(os.path.join(live.meeting_folder, "transcript.md"))
    assert os.path.isfile(os.path.join(live.meeting_folder, "segments.json"))


def test_transcribe_stage_missing_key_errors_and_halts(tmp_path, monkeypatch):
    from processing.model import StageStatus

    meetings = tmp_path / "meetings"
    meetings.mkdir()
    monkeypatch.setattr("utils.get_meetings_dir", lambda: str(meetings))
    audio = tmp_path / "rec.m4a"
    audio.write_bytes(b"\x00")
    q = _queue(tmp_path, meetings_dir=str(meetings), config_loader=lambda: {})
    q.enqueue(str(audio), {"provider": "AssemblyAI"})
    ok = q._stage_transcribe(q._items[0])
    assert ok is False
    live = q.snapshot()[0]
    assert live.transcript == StageStatus.ERROR
    assert live.error_stage == "transcript"
    assert live.error_message


def _done_meeting(q, tmp_path, folder_name="2026-06-13_12-00-00_m"):
    """Create a transcript-DONE item with a real folder + transcript.md."""
    from processing.model import StageStatus

    folder = tmp_path / "meetings" / folder_name
    folder.mkdir(parents=True)
    (folder / "transcript.md").write_text("the transcript", encoding="utf-8")
    q.enqueue("/audio/x.m4a", {"language": "ru"})
    it = q._items[0]
    it.meeting_folder = str(folder)
    it.transcript = StageStatus.DONE
    return it


def test_protocol_stage_writes_protocol_md(tmp_path, monkeypatch):
    from processing.model import StageStatus

    class _Proto:
        markdown = "# Протокол\n\n- пункт"
    monkeypatch.setattr("cli.core.run_protocol", lambda *a, **k: _Proto())
    q = _queue(tmp_path, config_loader=lambda: {"openrouter_api_key": "or-key"})
    it = _done_meeting(q, tmp_path)
    ok = q._stage_protocol(it)
    assert ok is True
    assert q.snapshot()[0].protocol == StageStatus.DONE
    assert (tmp_path / "meetings" / "2026-06-13_12-00-00_m" / "protocol.md").read_text(
        encoding="utf-8"
    ) == "# Протокол\n\n- пункт"


def test_protocol_stage_llm_error_halts(tmp_path, monkeypatch):
    from processing.model import StageStatus

    def _boom(*a, **k):
        raise RuntimeError("OpenRouter вернул 500")
    monkeypatch.setattr("cli.core.run_protocol", _boom)
    q = _queue(tmp_path, config_loader=lambda: {"openrouter_api_key": "or-key"})
    it = _done_meeting(q, tmp_path)
    ok = q._stage_protocol(it)
    assert ok is False
    live = q.snapshot()[0]
    assert live.protocol == StageStatus.ERROR
    assert live.error_stage == "protocol"


def test_tasks_stage_writes_raw_and_awaits_review(tmp_path, monkeypatch):
    import json

    from processing.model import StageStatus
    from tasks.schema import Task

    fake = {"tasks": [Task(title="Do X")], "corrections": 1, "model": "m"}
    monkeypatch.setattr("cli.core.run_extract_tasks", lambda *a, **k: fake)
    q = _queue(tmp_path, config_loader=lambda: {"openrouter_api_key": "or-key"})
    it = _done_meeting(q, tmp_path)
    ok = q._stage_tasks(it)
    assert ok is True
    assert q.snapshot()[0].tasks == StageStatus.AWAITING_REVIEW
    raw_path = tmp_path / "meetings" / "2026-06-13_12-00-00_m" / "tasks_raw.json"
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    assert data["tasks"][0]["title"] == "Do X"
    assert data["corrections"] == 1
    assert data["model"] == "m"


def test_tasks_stage_error_halts(tmp_path, monkeypatch):
    from processing.model import StageStatus

    def _boom(*a, **k):
        raise RuntimeError("OpenRouter не вернул валидных задач")
    monkeypatch.setattr("cli.core.run_extract_tasks", _boom)
    q = _queue(tmp_path, config_loader=lambda: {"openrouter_api_key": "or-key"})
    it = _done_meeting(q, tmp_path)
    ok = q._stage_tasks(it)
    assert ok is False
    assert q.snapshot()[0].tasks == StageStatus.ERROR
    assert q.snapshot()[0].error_stage == "tasks"
