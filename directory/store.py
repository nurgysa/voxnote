"""On-disk store for the people/projects directory.

One combined file ~/.voxnote/directory.json holding
{"people": [...], "projects": [...]}. Atomic write (tmp + os.replace),
mirroring tasks/persistence.py. Lives under ~/.voxnote (outside the vault) so
voiceprint biometrics stay local — backup/restore is Hermes Desktop's job.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from directory.schema import Person, Project, Voiceprint

FILENAME = "directory.json"
VOICEPRINT_CAP = 5


class DirectoryError(Exception):
    """Disk read/write or lookup failures bubble up as this."""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_directory_path() -> Path:
    """~/.voxnote/directory.json — in the app-data home.

    USERPROFILE/HOME env lookup stays test-friendly under monkeypatch.
    """
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    return home / ".voxnote" / FILENAME


class DirectoryStore:
    """In-memory people/projects keyed by id; every mutation writes the file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else _default_directory_path()
        self._people: dict[str, Person] = {}
        self._projects: dict[str, Project] = {}

    def load(self) -> None:
        if not self.path.is_file():
            self._people, self._projects = {}, {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise DirectoryError(f"{FILENAME} malformed: {e}") from e
        self._people = {
            d["id"]: Person.from_dict(d) for d in data.get("people", [])
        }
        self._projects = {
            d["id"]: Project.from_dict(d) for d in data.get("projects", [])
        }

    # ── reads ──
    def people(self) -> list[Person]:
        return list(self._people.values())

    def projects(self) -> list[Project]:
        return list(self._projects.values())

    def get_person(self, person_id: str) -> Person | None:
        return self._people.get(person_id)

    def get_project(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def people_for_project(self, project_id: str | None) -> list[Person]:
        """People whose project_ids include project_id, sorted by full_name for a
        stable note. Empty list for a falsy or unknown id."""
        if not project_id:
            return []
        return sorted(
            (p for p in self._people.values() if project_id in p.project_ids),
            key=lambda p: p.full_name,
        )

    # ── writes ──
    def upsert_person(self, person: Person) -> None:
        person.updated_at = _now_iso()
        self._people[person.id] = person
        self._save()

    def upsert_project(self, project: Project) -> None:
        project.updated_at = _now_iso()
        self._projects[project.id] = project
        self._save()

    def delete_person(self, person_id: str) -> None:
        self._people.pop(person_id, None)
        self._save()

    def delete_project(self, project_id: str) -> None:
        self._projects.pop(project_id, None)
        for person in self._people.values():
            if project_id in person.project_ids:
                person.project_ids = [
                    pid for pid in person.project_ids if pid != project_id
                ]
        self._save()

    def add_voiceprint(self, person_id: str, vp: Voiceprint) -> None:
        person = self._people.get(person_id)
        if person is None:
            raise DirectoryError(f"add_voiceprint: unknown person {person_id!r}")
        person.voiceprints.append(vp)
        if len(person.voiceprints) > VOICEPRINT_CAP:
            person.voiceprints = person.voiceprints[-VOICEPRINT_CAP:]
        person.updated_at = _now_iso()
        self._save()

    def identifiers_for_model(self, model: str) -> list[tuple[str, list[str]]]:
        """(full_name, [identifier, ...]) for every person holding >=1 voiceprint
        of `model`, sorted by full_name. This is the payload the queue worker
        passes as speaker_diarization_config.speakers. People without a
        matching-model voiceprint are omitted (their ids would be ignored
        server-side anyway); identifier order within a person is preserved."""
        result: list[tuple[str, list[str]]] = []
        for person in sorted(self._people.values(), key=lambda p: p.full_name):
            ids = [
                vp.identifier
                for vp in person.voiceprints
                if vp.model == model and vp.identifier
            ]
            if ids:
                result.append((person.full_name, ids))
        return result

    def latest_voiceprint_model(self) -> str | None:
        """The model of the most-recently-enrolled voiceprint across all people,
        or None when nobody has a model-bearing voiceprint. The worker filters
        known speakers by this (enroll + identify share the provider's stable
        default model; if it ever changes, the newest voiceprint reflects it)."""
        best_at = ""
        best_model: str | None = None
        for person in self._people.values():
            for vp in person.voiceprints:
                if vp.model and vp.enrolled_at >= best_at:
                    best_at = vp.enrolled_at
                    best_model = vp.model
        return best_model

    # ── persistence ──
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "people": [p.to_dict() for p in self._people.values()],
            "projects": [pr.to_dict() for pr in self._projects.values()],
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = self.path.parent / f".{self.path.name}.tmp"
        try:
            tmp.write_text(encoded, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise DirectoryError(f"Не удалось записать {FILENAME}: {e}") from e
