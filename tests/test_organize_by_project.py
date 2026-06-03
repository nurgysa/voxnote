import importlib.util
import json
import os
import pathlib

from directory.schema import Project
from directory.store import DirectoryStore

_PATH = pathlib.Path("scripts/organize_by_project.py")
_spec = importlib.util.spec_from_file_location("organize_by_project", _PATH)
organize_by_project = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(organize_by_project)


def _meeting(folder, *, project_id=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "transcript.md").write_text("hi", encoding="utf-8")
    if project_id is not None:
        (folder / "speakers.json").write_text(
            json.dumps({"project_id": project_id, "participants": [], "speakers": {}}),
            encoding="utf-8",
        )


def _store(tmp_path):
    store = DirectoryStore(path=tmp_path / "directory.json")
    store.load()
    store.upsert_project(Project(name="Kitng", id="p1"))
    return store


def test_plan_selects_only_resolvable_project_meetings(tmp_path):
    meetings = tmp_path / "meetings"
    _meeting(meetings / "2026-06-02_kitng", project_id="p1")
    _meeting(meetings / "2026-06-01_noproject")            # stays in root
    _meeting(meetings / "2026-05-30_ghost", project_id="gone")  # unknown project
    (meetings / "recordings").mkdir(parents=True)

    plan = organize_by_project._plan(str(meetings), _store(tmp_path))
    assert len(plan) == 1
    folder, dest, name = plan[0]
    assert os.path.basename(folder) == "2026-06-02_kitng"
    assert dest == os.path.join(str(meetings), "Kitng")
    assert name == "Kitng"


def test_plan_apply_moves_folder(tmp_path):
    meetings = tmp_path / "meetings"
    _meeting(meetings / "2026-06-02_kitng", project_id="p1")
    plan = organize_by_project._plan(str(meetings), _store(tmp_path))
    folder, dest, _name = plan[0]
    new = organize_by_project.move_into(folder, dest)
    assert os.path.isfile(os.path.join(new, "transcript.md"))
    assert new == os.path.join(str(meetings), "Kitng", "2026-06-02_kitng")
