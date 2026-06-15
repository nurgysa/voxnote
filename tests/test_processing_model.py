from processing.model import QueueItem, StageStatus


def test_queue_item_round_trips():
    item = QueueItem(
        id="abc",
        audio_path="/a/x.wav",
        title="x",
        created_at="2026-06-02T10:00:00",
        meeting_folder="/m/x",
        options={"language": "ru", "project_id": "p1"},
        auto=True,
        project_id="p1",
        source="record",
        source_path="G:/sources/x.wav",
        status=StageStatus.DONE,
        nudge_delivered=True,
        error_message=None,
        has_protocol=True,
        has_tasks=False,
    )
    restored = QueueItem.from_dict(item.to_dict())
    assert restored == item


def test_from_dict_tolerates_missing_and_bad_values():
    restored = QueueItem.from_dict({"id": "z", "status": "bogus"})
    assert restored.id == "z"
    assert restored.status is StageStatus.PENDING
    assert restored.auto is False
    assert restored.options == {}
    assert restored.project_id is None
    assert restored.source == "pick"
    assert restored.source_path is None
    assert restored.nudge_delivered is False
    assert restored.has_protocol is False
    assert restored.has_tasks is False


def test_status_serializes_to_plain_string():
    d = QueueItem(id="i", audio_path="", title="", created_at="").to_dict()
    assert d["status"] == "pending"
    assert isinstance(d["status"], str)
