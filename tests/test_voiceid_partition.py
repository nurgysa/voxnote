from processing.voiceid import partition_speakers


def _seg(speaker, text, start):
    return {"speaker": speaker, "text": text, "start": start, "end": start + 1}


def test_identified_go_to_participants_unknown_go_to_pending():
    segments = [
        _seg("Айбек Нурланов", "привет", 0.0),
        _seg("SPEAKER_1", "кто это", 2.0),
        _seg("SPEAKER_1", "ещё", 3.0),
    ]
    speaker_identifiers = {
        "Айбек Нурланов": ["known-id"],   # raw label == name (identified)
        "S1": ["new-id"],                  # raw anonymous label
    }
    participants, pending = partition_speakers(
        segments, speaker_identifiers, known_names={"Айбек Нурланов"},
    )
    assert participants == ["Айбек Нурланов"]
    assert pending == [{
        "label": "SPEAKER_1", "identifier": "new-id",
        "sample_text": "кто это", "first_start": 2.0,
    }]


def test_participants_sorted_and_unique():
    segments = [_seg("Данияр", "a", 0.0), _seg("Алмас", "b", 1.0),
                _seg("Данияр", "c", 2.0)]
    participants, pending = partition_speakers(segments, {}, known_names=set())
    assert participants == ["Алмас", "Данияр"]
    assert pending == []


def test_pending_skipped_when_no_identifier():
    segments = [_seg("SPEAKER_1", "x", 0.0)]
    participants, pending = partition_speakers(
        segments, {"S1": []}, known_names=set(),
    )
    assert participants == []
    assert pending == []  # no identifier → cannot enroll → not surfaced


def test_pending_sorted_by_first_start():
    segments = [_seg("SPEAKER_2", "later", 5.0), _seg("SPEAKER_1", "early", 1.0)]
    _, pending = partition_speakers(
        segments, {"S2": ["id2"], "S1": ["id1"]}, known_names=set(),
    )
    assert [p["label"] for p in pending] == ["SPEAKER_1", "SPEAKER_2"]


def test_uu_label_treated_as_unknown():
    # Speechmatics may emit "UU" (unattributable); _to_segments normalised it to
    # "SPEAKER_UU". It carries an identifier → surfaces as a pending voice.
    segments = [_seg("SPEAKER_UU", "mumble", 0.0)]
    _, pending = partition_speakers(
        segments, {"UU": ["uu-id"]}, known_names=set(),
    )
    assert pending == [{
        "label": "SPEAKER_UU", "identifier": "uu-id",
        "sample_text": "mumble", "first_start": 0.0,
    }]
