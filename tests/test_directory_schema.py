from directory.schema import Person, Project, Voiceprint


def test_person_roundtrip():
    p = Person(full_name="Айбек Нурланов", role="тимлид", project_ids=["pr1"])
    p2 = Person.from_dict(p.to_dict())
    assert p2.full_name == "Айбек Нурланов"
    assert p2.role == "тимлид"
    assert p2.project_ids == ["pr1"]
    assert p2.id == p.id


def test_person_from_dict_tolerates_missing_optional():
    p = Person.from_dict({"full_name": "Дана"})
    assert p.role == ""
    assert p.project_ids == []
    assert p.voiceprints == []
    assert p.tracker_member_id is None
    assert p.id


def test_person_autogenerates_distinct_ids():
    assert Person(full_name="A").id != Person(full_name="B").id


def test_voiceprint_roundtrip():
    vp = Voiceprint(
        identifier="sp-id-1", model="m-x", source_meeting="2026-05-30_x",
    )
    vp2 = Voiceprint.from_dict(vp.to_dict())
    assert vp2.identifier == "sp-id-1"
    assert vp2.model == "m-x"
    assert vp2.provider == "speechmatics"
    assert vp2.source_meeting == "2026-05-30_x"


def test_person_roundtrip_with_voiceprints():
    p = Person(full_name="A", voiceprints=[Voiceprint(identifier="id1", model="m")])
    p2 = Person.from_dict(p.to_dict())
    assert len(p2.voiceprints) == 1
    assert p2.voiceprints[0].identifier == "id1"


def test_voiceprint_from_dict_ignores_legacy_vector():
    # Pre-Phase-B records held {"vector": [...]} and no identifier; they must
    # load without error (identifier/model fall back to "").
    vp = Voiceprint.from_dict({"vector": [0.1, 0.2], "source_meeting": "old"})
    assert vp.identifier == ""
    assert vp.model == ""
    assert vp.source_meeting == "old"
    assert not hasattr(vp, "vector")


def test_project_roundtrip():
    pr = Project(name="Миграция", description="Перенос на Stripe")
    pr2 = Project.from_dict(pr.to_dict())
    assert pr2.name == "Миграция"
    assert pr2.description == "Перенос на Stripe"
    assert pr2.id == pr.id
