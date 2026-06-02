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
        transcript=StageStatus.DONE,
        protocol=StageStatus.RUNNING,
        tasks=StageStatus.AWAITING_REVIEW,
        error_stage="protocol",
        error_message="boom",
    )
    restored = QueueItem.from_dict(item.to_dict())
    assert restored == item


def test_from_dict_tolerates_missing_and_bad_values():
    restored = QueueItem.from_dict({"id": "z", "transcript": "bogus"})
    assert restored.id == "z"
    assert restored.transcript is StageStatus.PENDING
    assert restored.auto is False
    assert restored.options == {}
    assert restored.project_id is None


def test_stage_status_serializes_to_plain_strings():
    d = QueueItem(id="i", audio_path="", title="", created_at="").to_dict()
    assert d["transcript"] == "pending"
    assert isinstance(d["transcript"], str)
