import pytest

from directory.schema import Person, Project, Voiceprint
from directory.store import DirectoryError, DirectoryStore


def _fresh(tmp_path) -> DirectoryStore:
    s = DirectoryStore(path=tmp_path / "directory.json")
    s.load()
    return s


def test_load_missing_file_is_empty(tmp_path):
    s = _fresh(tmp_path)
    assert s.people() == []
    assert s.projects() == []


def test_upsert_person_persists_across_reload(tmp_path):
    path = tmp_path / "directory.json"
    s = DirectoryStore(path=path)
    s.load()
    s.upsert_person(Person(full_name="Айбек"))
    s2 = DirectoryStore(path=path)
    s2.load()
    assert [p.full_name for p in s2.people()] == ["Айбек"]


def test_delete_project_strips_refs_from_people(tmp_path):
    s = _fresh(tmp_path)
    pr = Project(name="Alpha")
    s.upsert_project(pr)
    p = Person(full_name="A", project_ids=[pr.id])
    s.upsert_person(p)
    s.delete_project(pr.id)
    assert s.projects() == []
    assert s.get_person(p.id).project_ids == []


def test_add_voiceprint_caps_at_five_dropping_oldest(tmp_path):
    s = _fresh(tmp_path)
    p = Person(full_name="A")
    s.upsert_person(p)
    for i in range(6):
        s.add_voiceprint(p.id, Voiceprint(identifier=f"id{i}", model="m"))
    vps = s.get_person(p.id).voiceprints
    assert len(vps) == 5
    assert vps[0].identifier == "id1"   # oldest (id0) evicted
    assert vps[-1].identifier == "id5"


def test_add_voiceprint_unknown_person_raises(tmp_path):
    s = _fresh(tmp_path)
    with pytest.raises(DirectoryError):
        s.add_voiceprint("nope", Voiceprint(identifier="id1", model="m"))


def test_malformed_file_raises_on_load(tmp_path):
    path = tmp_path / "directory.json"
    path.write_text("{ not json", encoding="utf-8")
    s = DirectoryStore(path=path)
    with pytest.raises(DirectoryError):
        s.load()


def _boom(*_a, **_k):
    raise ValueError("boom")


def test_save_failure_leaves_previous_file_intact(tmp_path, monkeypatch):
    import directory.store as store_mod

    path = tmp_path / "directory.json"
    s = DirectoryStore(path=path)
    s.load()
    s.upsert_person(Person(full_name="Good"))   # valid file on disk

    monkeypatch.setattr(store_mod.json, "dumps", _boom)
    with pytest.raises(ValueError):
        s.upsert_person(Person(full_name="Bad"))
    monkeypatch.undo()

    s2 = DirectoryStore(path=path)
    s2.load()
    assert [p.full_name for p in s2.people()] == ["Good"]
    assert not (tmp_path / ".directory.json.tmp").exists()


def test_people_for_project_returns_sorted_members(tmp_path):
    s = _fresh(tmp_path)
    pr = Project(name="Alpha")
    s.upsert_project(pr)
    s.upsert_person(Person(full_name="Данияр", project_ids=[pr.id]))
    s.upsert_person(Person(full_name="Алмас", project_ids=[pr.id]))
    s.upsert_person(Person(full_name="Чужой", project_ids=[]))
    names = [p.full_name for p in s.people_for_project(pr.id)]
    assert names == ["Алмас", "Данияр"]  # sorted by full_name; non-member excluded


def test_people_for_project_empty_for_falsy_or_unknown(tmp_path):
    s = _fresh(tmp_path)
    s.upsert_person(Person(full_name="A", project_ids=["p1"]))
    assert s.people_for_project(None) == []
    assert s.people_for_project("") == []
    assert s.people_for_project("nope") == []


def test_identifiers_for_model_groups_by_person_filtering_model(tmp_path):
    s = _fresh(tmp_path)
    a = Person(full_name="Алмас")
    b = Person(full_name="Данияр")
    c = Person(full_name="Чужой")
    s.upsert_person(a)
    s.upsert_person(b)
    s.upsert_person(c)
    s.add_voiceprint(a.id, Voiceprint(identifier="a1", model="m-x"))
    s.add_voiceprint(a.id, Voiceprint(identifier="a2", model="m-x"))
    s.add_voiceprint(b.id, Voiceprint(identifier="b1", model="m-x"))
    s.add_voiceprint(c.id, Voiceprint(identifier="c1", model="OTHER"))  # wrong model
    assert s.identifiers_for_model("m-x") == [
        ("Алмас", ["a1", "a2"]),
        ("Данияр", ["b1"]),
    ]  # sorted by full_name; Чужой omitted (no m-x voiceprint)


def test_identifiers_for_model_empty_when_none_match(tmp_path):
    s = _fresh(tmp_path)
    p = Person(full_name="A")
    s.upsert_person(p)
    s.add_voiceprint(p.id, Voiceprint(identifier="i", model="m-x"))
    assert s.identifiers_for_model("OTHER") == []
    assert s.identifiers_for_model("m-x") == [("A", ["i"])]


def test_latest_voiceprint_model_returns_newest(tmp_path):
    s = _fresh(tmp_path)
    p = Person(full_name="A")
    s.upsert_person(p)
    s.add_voiceprint(p.id, Voiceprint(
        identifier="i1", model="old", enrolled_at="2026-01-01T00:00:00"))
    s.add_voiceprint(p.id, Voiceprint(
        identifier="i2", model="new", enrolled_at="2026-06-01T00:00:00"))
    assert s.latest_voiceprint_model() == "new"


def test_latest_voiceprint_model_none_when_empty(tmp_path):
    s = _fresh(tmp_path)
    s.upsert_person(Person(full_name="A"))  # no voiceprints
    assert s.latest_voiceprint_model() is None
