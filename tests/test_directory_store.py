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
        s.add_voiceprint(p.id, Voiceprint(vector=[float(i)]))
    vps = s.get_person(p.id).voiceprints
    assert len(vps) == 5
    assert vps[0].vector == [1.0]   # oldest (0.0) evicted
    assert vps[-1].vector == [5.0]


def test_add_voiceprint_unknown_person_raises(tmp_path):
    s = _fresh(tmp_path)
    with pytest.raises(DirectoryError):
        s.add_voiceprint("nope", Voiceprint(vector=[1.0]))


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
